"""Non-blocking accounting for owned workers and admission of new work only.

Callers provide live PIDs that they spawned. No general process list, command
line, executable name or user information is queried. Estimates are reservations,
not hard limits or a promise that a document cannot use more memory.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import threading
import time

import psutil

MIB = 1024 ** 2
GIB = 1024 ** 3
_lock = threading.RLock()
_processes: dict[int, psutil.Process] = {}


def _process(pid: int):
    process = _processes.get(pid)
    if process is None or not process.is_running():
        process = psutil.Process(pid)
        _processes[pid] = process
    return process


def sample(pids: dict[str, int]) -> dict:
    """Sample this app plus the supplied owned trees, deduplicating each PID.

    Repeated calls retain Process objects for meaningful cpu_percent(None)
    deltas. CPU percentages use the capacity of all logical CPUs; first samples
    may be zero. Private bytes are null when this OS does not expose them.
    """
    with _lock:
        logical = psutil.cpu_count() or os.cpu_count() or 1
        memory = psutil.virtual_memory()
        result = {'total_bytes': int(memory.total), 'available_bytes': int(memory.available),
                  'rss_bytes': 0, 'private_bytes': None, 'main_bytes': 0,
                  'cpu_percent': 0.0, 'system_cpu_percent': float(psutil.cpu_percent(interval=None)),
                  'logical_cpus': logical, 'processes': [], 'jobs': {},
                  'sampled_at': time.time(), 'errors': []}
        found = {}
        trees = {}
        for key, pid in pids.items():
            if type(pid) is not int or pid <= 0:
                continue
            tree = set()
            try:
                root = _process(pid)
                tree.add(pid)
                found[pid] = root
                for child in root.children(recursive=True):
                    tree.add(child.pid)
                    found[child.pid] = _process(child.pid)
            except (psutil.Error, OSError) as error:
                result['errors'].append({'pid': pid, 'kind': type(error).__name__})
            trees[str(key)] = tree
        try:
            found[os.getpid()] = _process(os.getpid())
        except (psutil.Error, OSError) as error:
            result['errors'].append({'pid': os.getpid(), 'kind': type(error).__name__})
        measured = {}
        for pid, process in found.items():
            try:
                info = process.memory_info()
                private = getattr(info, 'private', None)
                raw_cpu = max(0.0, float(process.cpu_percent(interval=None)))
                item = {'pid': pid, 'rss_bytes': int(info.rss),
                        'private_bytes': int(private) if private is not None else None,
                        'cpu_percent': raw_cpu / logical,
                        'peak_bytes': max(int(info.rss), int(private or 0),
                                          int(getattr(info, 'peak_wset', 0)),
                                          int(getattr(info, 'peak_pagefile', 0)))}
                measured[pid] = item
            except (psutil.Error, OSError) as error:
                result['errors'].append({'pid': pid, 'kind': type(error).__name__})
        # A PID appearing as both a launcher descendant and a root counts once.
        result['processes'] = list(measured.values())
        result['rss_bytes'] = sum(item['rss_bytes'] for item in measured.values())
        if measured and all(item['private_bytes'] is not None for item in measured.values()):
            result['private_bytes'] = sum(item['private_bytes'] for item in measured.values())
        result['cpu_percent'] = min(100.0, sum(item['cpu_percent'] for item in measured.values()))
        own = measured.get(os.getpid()) or {}
        result['main_bytes'] = max(own.get('rss_bytes', 0), own.get('private_bytes') or 0)
        for key, tree in trees.items():
            items = [measured[pid] for pid in sorted(tree) if pid in measured]
            result['jobs'][key] = {
                'pids': [item['pid'] for item in items],
                'rss_bytes': sum(item['rss_bytes'] for item in items),
                'private_bytes': (sum(item['private_bytes'] for item in items)
                                  if items and all(item['private_bytes'] is not None for item in items) else None),
                'peak_bytes': sum(item['peak_bytes'] for item in items),
                'cpu_percent': sum(item['cpu_percent'] for item in items)}
        for pid in set(_processes) - set(measured):
            _processes.pop(pid, None)
        return result


def _kind(source) -> str:
    suffix = Path(source).suffix.lower()
    if suffix == '.pdf':
        return 'pdf'
    if suffix in {'.txt', '.md', '.markdown', '.csv', '.tsv', '.html', '.htm', '.eml', '.mbox'}:
        return 'text'
    return 'office'


class Policy:
    """Conservative per-session reservations; never terminates running work."""
    memory_fraction = .5
    reserve_fraction = .15
    minimum_reserve_bytes = 2 * GIB
    emergency_free_bytes = 512 * MIB
    cpu_threshold = 90.0

    def __init__(self, *, physical_cpus=None, logical_cpus=None, max_workers=10):
        logical = logical_cpus if logical_cpus is not None else psutil.cpu_count()
        physical = physical_cpus if physical_cpus is not None else psutil.cpu_count(logical=False)
        capacity = physical or max(1, (logical or os.cpu_count() or 1) // 2)
        self.max_workers = min(10, max(1, int(max_workers)), max(1, int(capacity) // 2))
        self._estimates = {'pdf': 2 * GIB, 'office': 512 * MIB, 'text': 128 * MIB}

    def estimate(self, source) -> int:
        return self._estimates[_kind(source)]

    def observe(self, source, peak_bytes: int) -> None:
        if isinstance(peak_bytes, (int, float)) and math.isfinite(peak_bytes) and peak_bytes > 0:
            kind = _kind(source)
            self._estimates[kind] = max(self._estimates[kind], math.ceil(peak_bytes * 1.2))

    def can_start(self, source, active_estimates: list[int], snapshot: dict) -> tuple[bool, str, int]:
        estimate = self.estimate(source)
        total, available = snapshot.get('total_bytes', 0), snapshot.get('available_bytes', 0)
        if not isinstance(total, (int, float)) or total <= 0 or not isinstance(available, (int, float)):
            return False, '시스템 메모리 정보를 확인하는 중입니다.', estimate
        if available < self.emergency_free_bytes:
            return False, '시스템 여유 메모리가 512MB 미만이라 기다립니다.', estimate
        if len(active_estimates) >= self.max_workers:
            return False, '동시에 처리할 수 있는 문서 수에 도달했습니다.', estimate
        if (snapshot.get('system_cpu_percent') or 0) >= self.cpu_threshold:
            return False, '시스템 CPU 사용량이 높아 새 문서를 기다립니다.', estimate
        main = max(0, snapshot.get('main_bytes') or 0)
        measured = max(0, snapshot.get('rss_bytes') or 0, snapshot.get('private_bytes') or 0)
        reserved = main + sum(max(0, int(value)) for value in active_estimates)
        projected = max(measured, reserved) + estimate
        budget = int(total * self.memory_fraction)
        reserve = max(int(total * self.reserve_fraction), self.minimum_reserve_bytes)
        remaining = available - max(0, reserved - measured) - estimate
        if projected <= budget and remaining >= reserve:
            return True, '', estimate
        # Only inherently small machines get the one-worker escape. A large
        # machine temporarily occupied by other apps must retain its reserve.
        if (not active_estimates and remaining >= self.emergency_free_bytes
                and (main + estimate > budget or main + estimate + reserve > total)):
            return True, '메모리가 작은 PC에서 한 개 문서만 처리합니다.', estimate
        return False, '메모리 여유를 확보한 뒤 새 문서를 시작합니다.', estimate
