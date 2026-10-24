"""Run the existing headless extraction pipeline outside the GUI interpreter.

Only progress and committed output metadata cross this boundary. Documents and
MuPDF objects stay in the child; cancellation owns the entire child process tree.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .external_documents import _stop_owned_process
from .general_pdf import ExtractionCancelled

MAX_EVENTS = 64 * 1024 * 1024
MAX_LINE = 16 * 1024 * 1024
MAX_LOG = 8 * 1024 * 1024


class _OwnedJob:
    """Windows' standard kill-on-close job; never attach unrelated processes."""

    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes as c
        from ctypes import wintypes as w

        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong),
                        ('flags', w.DWORD), ('minimum_working_set', c.c_size_t),
                        ('maximum_working_set', c.c_size_t), ('active_processes', w.DWORD),
                        ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class IO(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t),
                        ('job_memory', c.c_size_t), ('peak_process_memory', c.c_size_t),
                        ('peak_job_memory', c.c_size_t)]

        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.kernel.SetInformationJobObject.restype = w.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        try:
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
                raise c.WinError(c.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, w.HANDLE(process._handle)):
                raise c.WinError(c.get_last_error())
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _command():
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--extract-worker']
    app = Path(__file__).resolve().parents[2] / 'app.py'
    return [sys.executable, '-B', str(app), '--extract-worker']


def run(paths, output_dir, *, force=False, cancel=None, on_event=None, timeout=86400):
    """Start one owned batch, forward complete JSONL events, and reap on every exit."""
    from . import store

    def emit(kind, payload):
        if on_event:
            on_event(kind, payload)

    with tempfile.TemporaryDirectory(prefix='pubnexus-extraction-') as temporary:
        work = Path(temporary)
        request = work / 'request.json'
        request.write_text(json.dumps({'version': 1, 'paths': [str(Path(p).resolve()) for p in paths],
                                       'output_dir': str(Path(output_dir).resolve()), 'force': bool(force),
                                       'store': str(store.root().resolve())}, ensure_ascii=False), encoding='utf-8')
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        scratch = work / 'scratch'
        scratch.mkdir()
        env.update(TEMP=str(scratch), TMP=str(scratch))
        # A source interpreter also needs the repository's package import path.
        if not getattr(sys, 'frozen', False):
            env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
        env.pop('PNX_TEST_DEBUG_PORT', None)
        env.pop('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', None)
        events = work / 'events.jsonl'
        events.touch()
        started = last_heartbeat = time.monotonic()
        latest = None
        finished = False
        exit_observed = False
        cancel_deadline = None
        cancel_started = None
        process = job = None
        try:
            with (work / 'stdout.log').open('wb') as stdout, (work / 'stderr.log').open('wb') as stderr:
                process = subprocess.Popen([*_command(), str(request)], stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, cwd=str(work), env=env, shell=False,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                job = _OwnedJob(process)
                emit('worker_started', {'pid': process.pid})
                with events.open('rb') as stream:
                    while True:
                        now = time.monotonic()
                        if now - started > timeout:
                            raise RuntimeError('추출 작업의 제한 시간을 초과했습니다.')
                        if events.stat().st_size > MAX_EVENTS or any(
                            (work / name).stat().st_size > MAX_LOG for name in ('stdout.log', 'stderr.log')):
                            raise RuntimeError('추출 작업의 진단 출력 예산을 초과했습니다.')
                        while True:
                            position = stream.tell()
                            line = stream.readline(MAX_LINE + 1)
                            if len(line) > MAX_LINE:
                                raise RuntimeError('추출 상태 메시지가 너무 큽니다.')
                            if not line.endswith(b'\n'):
                                stream.seek(position)
                                break
                            event = json.loads(line)
                            kind, payload = event['kind'], event['payload']
                            if not isinstance(kind, str) or not isinstance(payload, dict):
                                raise RuntimeError('추출 상태 메시지 형식이 올바르지 않습니다.')
                            if kind == 'prog':
                                latest = dict(payload)
                            elif kind in ('mark', 'file_error'):
                                latest = None
                            if kind == 'run' and not payload.get('on'):
                                finished = True
                            emit(kind, payload)
                        if cancel and cancel():
                            committing = latest and latest.get('stage') == 'MD와 이미지 저장'
                            if cancel_deadline is None:
                                (work / 'cancel').touch()
                                cancel_started = now
                                cancel_deadline = now + (8 if committing else 1)
                            elif committing:
                                cancel_deadline = max(cancel_deadline, cancel_started + 8)
                            if now >= cancel_deadline:
                                raise ExtractionCancelled('추출을 중지했습니다. 저장된 결과는 보존했습니다.')
                        if latest and not finished and now - last_heartbeat >= 1:
                            emit('prog', {**latest, 'elapsed_sec': int(now - started), 'worker_pid': process.pid})
                            last_heartbeat = now
                        if process.poll() is not None:
                            # The file was drained after the child's final flush.
                            if stream.tell() != events.stat().st_size:
                                if exit_observed:
                                    raise RuntimeError('추출 상태 메시지가 완성되지 않았습니다.')
                                exit_observed = True
                                continue  # Drain the child's final flush after observing exit.
                            break
                        time.sleep(.05)
            if process.returncode or not finished:
                diagnostic = (work / 'stderr.log').read_text(encoding='utf-8', errors='replace')[-1500:]
                raise RuntimeError('추출 작업이 정상 완료되지 않았습니다. ' + diagnostic)
        finally:
            if job:
                job.close()
            if process:
                _stop_owned_process(process)


def worker_cli(request_json, app_factory):
    """Invoked before WebView initialization, in both source and frozen builds."""
    from . import store
    request = Path(request_json).resolve(strict=True)
    if request.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('추출 요청이 너무 큽니다.')
    record = json.loads(request.read_text(encoding='utf-8'))
    paths = record.get('paths')
    if (record.get('version') != 1 or not isinstance(paths, list) or not 0 < len(paths) <= 10000
            or not all(isinstance(p, str) and Path(p).is_absolute() and Path(p).is_file() for p in paths)
            or type(record.get('force')) is not bool or not Path(record['output_dir']).is_absolute()
            or Path(record['store']).resolve() != store.root().resolve()):
        raise ValueError('추출 요청 형식이 올바르지 않습니다.')
    app = app_factory()
    app._worker_mode = True
    is_set = app.cancel.is_set
    app.cancel.is_set = lambda: is_set() or (request.parent / 'cancel').exists()
    app.output_dir = Path(record['output_dir'])
    app.pdfs = [Path(p) for p in paths]
    with (request.parent / 'events.jsonl').open('a', encoding='utf-8', buffering=1) as stream:
        def push(kind, payload):
            stream.write(json.dumps({'kind': kind, 'payload': payload}, ensure_ascii=False) + '\n')
            stream.flush()
        app.push = push
        try:
            app._work(app.pdfs, force=record['force'])
        finally:
            app.renderer.close_all()
    return 0
