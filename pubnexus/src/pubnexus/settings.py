"""Durable user preferences, separate from extraction caches and recent files."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading

from . import store, utils
from .output_location import default_output_dir


class Preferences:
    def __init__(self):
        self.path = store.root() / 'settings.json'
        self._lock = threading.RLock()
        try:
            data = utils.read_json(self.path) if self.path.stat().st_size <= 65536 else {}
        except (OSError, ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        directory = data.get('output_dir')
        self._data = {
            'output_dir': directory if isinstance(directory, str) and directory
                and '\x00' not in directory and Path(directory).is_absolute() else None,
            'auto_update': data.get('auto_update') if type(data.get('auto_update')) is bool else True,
        }

    @property
    def output_dir(self) -> Path:
        # Explicit environment overrides isolate automated tests and portable runs.
        with self._lock:
            if os.environ.get('PUBNEXUS_OUTPUT', '').strip():
                return default_output_dir()
            return Path(self._data['output_dir']).resolve() if self._data['output_dir'] else default_output_dir()

    @property
    def auto_update(self) -> bool:
        with self._lock:
            return self._data['auto_update']

    def _save(self, **changes):
        with self._lock:
            data = {**self._data, **changes}
            utils.write_json(self.path, data)
            self._data = data

    def set_output_dir(self, directory: Path | None) -> Path:
        if directory is None:
            chosen = default_output_dir()
        else:
            chosen = Path(directory).expanduser()
            if not chosen.is_absolute():
                raise ValueError('저장 폴더는 전체 경로로 지정하세요.')
        chosen = chosen.resolve()
        chosen.mkdir(parents=True, exist_ok=True)
        # Confirm the destination is usable before persisting a broken setting.
        with tempfile.TemporaryFile(dir=utils.long_path(chosen), prefix='.document-write-') as stream:
            stream.write(b'1')
            stream.flush()
        self._save(output_dir=str(chosen) if directory is not None else None)
        return chosen

    def set_auto_update(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError('자동 업데이트 설정을 확인하세요.')
        self._save(auto_update=enabled)
