"""Process boundary, final-flush race and native-GIL cancellation regressions."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_app_export as support
from pubnexus import extraction_process


class BackgroundProcessTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp
    source = support.AppExportTests.source
    extracted = support.AppExportTests.extracted

    def test_final_complete_line_written_at_process_exit_is_drained(self):
        source = self.source()
        received = []

        class ExitingProcess:
            pid = 123
            returncode = None

            def __init__(self, args, **kwargs):
                self.work = Path(args[-1]).parent

            def poll(self):
                if self.returncode is None:
                    with (self.work / 'events.jsonl').open('a', encoding='utf-8') as f:
                        f.write(json.dumps({'kind': 'run', 'payload': {'on': False, 'done': 1}}) + '\n')
                    self.returncode = 0
                return self.returncode

        error = None
        with patch.object(extraction_process.subprocess, 'Popen', ExitingProcess), \
             patch.object(extraction_process, '_OwnedJob'):
            try:
                extraction_process.run([source], self.root / 'Markdown',
                    on_event=lambda kind, payload: received.append((kind, payload)))
            except RuntimeError as exc:
                error = str(exc)
        self.assertIsNone(error, 'A complete final event must not be lost at exit')
        self.assertTrue(any(k == 'run' and not p['on'] for k, p in received))

    def test_cancellation_drains_a_commit_already_finishing_before_reaping(self):
        source = self.source()
        child = self.root / 'finishing.py'
        child.write_text('''
import json,sys,time
from pathlib import Path
request=Path(sys.argv[-1]); data=json.loads(request.read_text(encoding='utf-8'))
with (request.parent/'events.jsonl').open('a',encoding='utf-8',buffering=1) as f:
 def event(kind,payload):f.write(json.dumps({'kind':kind,'payload':payload})+'\\n');f.flush()
 event('prog',{'path':data['paths'][0],'stage':'MD와 이미지 저장','file_pct':98})
 while not (request.parent/'cancel').exists():time.sleep(.01)
 event('mark',{'path':data['paths'][0],'md_ready':True})
 event('run',{'on':False,'done':1,'stopped':True})
''', encoding='utf-8')
        cancel = threading.Event()
        received = []
        def event(kind, payload):
            received.append((kind, payload))
            if kind == 'prog':
                cancel.set()
        with patch.object(extraction_process, '_command', return_value=[sys.executable, '-B', str(child)]):
            try:
                extraction_process.run([source], self.root / 'Markdown',
                                       on_event=event, cancel=cancel.is_set, timeout=10)
            except extraction_process.ExtractionCancelled:
                pass
        self.assertTrue(any(kind == 'mark' for kind, _ in received),
                        'A completed commit must reach the GUI before cancellation reaps the child')

    def test_cancellation_extends_bounded_grace_if_commit_starts_just_after_cancel(self):
        source = self.source()
        child = self.root / 'commit_race.py'
        child.write_text('''
import json,sys,time
from pathlib import Path
request=Path(sys.argv[-1]); data=json.loads(request.read_text(encoding='utf-8'))
with (request.parent/'events.jsonl').open('a',encoding='utf-8',buffering=1) as f:
 def event(kind,payload):f.write(json.dumps({'kind':kind,'payload':payload})+'\\n');f.flush()
 event('prog',{'path':data['paths'][0],'stage':'본문 보존 확인','file_pct':97})
 while not (request.parent/'cancel').exists():time.sleep(.01)
 event('prog',{'path':data['paths'][0],'stage':'MD와 이미지 저장','file_pct':98})
 time.sleep(1.2)
 event('mark',{'path':data['paths'][0],'md_ready':True})
 event('run',{'on':False,'done':1,'stopped':True})
''', encoding='utf-8')
        cancel = threading.Event()
        received = []
        def event(kind, payload):
            received.append((kind, payload))
            if kind == 'prog':
                cancel.set()
        with patch.object(extraction_process, '_command', return_value=[sys.executable, '-B', str(child)]):
            try:
                extraction_process.run([source], self.root / 'Markdown',
                                       on_event=event, cancel=cancel.is_set, timeout=12)
            except extraction_process.ExtractionCancelled:
                pass
        self.assertTrue(any(kind == 'mark' for kind, _ in received),
                        'A commit entering just after cancellation needs bounded finishing time')

    @unittest.skipUnless(os.name == 'nt', 'Windows native GIL/owned-job regression')
    def test_gui_reads_searches_and_adds_files_while_child_holds_gil_and_cancels_tree(self):
        first, second = self.source('working.pdf'), self.source('reading.pdf')
        self.app.pdfs = [first, second]
        self.app._work([second])
        markdown = Path(self.app.file_rows()[1]['markdown'])
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (first, second, markdown)}
        native = self.root / 'native_work.py'
        native.write_text('''
import ctypes,json,os,subprocess,sys
from pathlib import Path
request=Path(sys.argv[-1]); data=json.loads(request.read_text(encoding='utf-8'))
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
event={'kind':'prog','payload':{'path':data['paths'][0],'name':Path(data['paths'][0]).name,
    'stage':'Native analysis','file_pct':4,'i':1,'total':1,'pct':4,'descendant_pid':child.pid,
    'scratch':os.environ['TEMP']}}
with (request.parent/'events.jsonl').open('a',encoding='utf-8') as f:
 f.write(json.dumps(event)+'\\n');f.flush()
ctypes.PyDLL('kernel32').Sleep(60000)
''', encoding='utf-8')
        self.addCleanup(self.app.renderer.close_all)
        with patch.object(extraction_process, '_command', return_value=[sys.executable, '-B', str(native)]):
            self.app.start([first], force=True)
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    progress = [p for k, p in self.events if k == 'prog' and p.get('descendant_pid')]
                    if progress:
                        break
                    time.sleep(.02)
                self.assertTrue(progress, 'The actual child must enter the native GIL-holding operation')
                self.assertTrue(self.app.worker.is_alive())
                started = [p for k, p in self.events if k == 'worker_started'][0]
                self.assertNotEqual(started['pid'], os.getpid())
                began = time.monotonic()
                self.assertEqual(len(self.app.file_rows()), 2)
                self.assertIn('key', self.app.source_view(1))
                hits = support.app_module.Api(self.app).search_source(1, 'offline')
                self.assertTrue(hits['ok'] and hits['results'])
                self.assertTrue(self.app.doc_view(second)['extracted'])
                third = self.root / 'added.md'
                third.write_text('Added without stopping the running extraction.', encoding='utf-8')
                self.app.add_files([third])
                self.assertEqual(len(self.app.file_rows()), 3)
                self.assertLess(time.monotonic() - began, 2)
                began = time.monotonic()
                self.assertTrue(support.app_module.Api(self.app).cancel()['ok'])
                self.app.worker.join(timeout=4)
                self.assertFalse(self.app.worker.is_alive(), 'Native GIL work must be cancellable from the GUI')
                self.assertLess(time.monotonic() - began, 4)
                self.assertFalse(self._alive(started['pid']))
                self.assertFalse(self._alive(progress[0]['descendant_pid']))
                self.assertFalse(Path(progress[0]['scratch']).exists(), 'Nested engine scratch must be reaped too')
                self.assertEqual(self.app.file_rows()[0]['status'], 'paused')
                self.assertTrue(self.app.file_rows()[1]['md_ready'])
                for path, checksum in before.items():
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
            finally:
                self.app.cancel.set()
                self.app.worker.join(timeout=6)

    @staticmethod
    def _alive(pid):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            kernel.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
