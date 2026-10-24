"""Actual native-process concurrency, admission and independent row state."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_app_export as support
from pubnexus import extraction_process
from pubnexus import resource_budget
import test_extraction_process as process_support


class ParallelTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp
    source = support.AppExportTests.source
    extracted = support.AppExportTests.extracted

    @staticmethod
    def abundant():
        return {'total_bytes': 32 * 1024 ** 3, 'available_bytes': 24 * 1024 ** 3,
                'rss_bytes': 128 * 1024 ** 2, 'main_bytes': 128 * 1024 ** 2,
                'system_cpu_percent': 0, 'jobs': {}}

    def test_parallel_limit_and_same_content_share_one_reservation(self):
        sources = [self.root / name for name in ('one.csv', 'copy.csv', 'other.csv', 'last.csv')]
        for index, source in enumerate(sources):
            source.write_text('Name,Value\nItem,' + str(index) + '\n', encoding='utf-8')
        sources[1].write_bytes(sources[0].read_bytes())
        self.app.pdfs = sources
        policy = resource_budget.Policy(physical_cpus=4, logical_cpus=8, max_workers=2)
        release = threading.Event()
        self.addCleanup(release.set)
        entered = []
        real_run = extraction_process.run

        def run(paths, output_dir, **kwargs):
            entered.append(paths[0])
            release.wait(5)
            return real_run(paths, output_dir, **kwargs)

        with patch('pubnexus.resource_budget.Policy', return_value=policy), \
             patch('pubnexus.resource_budget.sample', side_effect=lambda _: self.abundant()), \
             patch.object(extraction_process, 'run', side_effect=run):
            self.app.start(sources, True)
            try:
                deadline = time.monotonic() + 3
                while len(entered) < 2 and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(entered, [sources[0], sources[2]])
                self.assertEqual(len(self.app._active_paths), 2)
                self.assertEqual([row['status'] for row in self.app.file_rows()],
                                 ['running', 'queued', 'running', 'queued'])
            finally:
                release.set()
                self.app.worker.join(timeout=20)
        self.assertFalse(self.app.worker.is_alive())
        self.assertEqual(len(entered), 4)
        self.assertTrue(all(row['done'] for row in self.app.file_rows()))

    def test_memory_pressure_queues_work_and_it_resumes_when_memory_is_available(self):
        source = self.root / 'waiting.csv'
        source.write_text('Name,Value\nMemory,713\n', encoding='utf-8')
        self.app.pdfs = [source]
        memory = self.abundant()
        memory['available_bytes'] = 1024 ** 3
        with patch('pubnexus.resource_budget.sample', side_effect=lambda _: dict(memory)):
            self.app.start([source])
            try:
                deadline = time.monotonic() + 3
                while not self.app._resource_state and time.monotonic() < deadline:
                    time.sleep(.02)
                time.sleep(.15)
                self.assertFalse(any(kind == 'worker_started' for kind, _ in self.events))
                self.assertEqual(self.app.file_rows()[0]['status'], 'queued')
                memory['available_bytes'] = 24 * 1024 ** 3
                self.app.worker.join(timeout=10)
                self.assertFalse(self.app.worker.is_alive())
                self.assertTrue(self.app.file_rows()[0]['done'])
            finally:
                self.app.cancel.set()
                self.app.worker.join(timeout=5)

    def test_failed_coordinator_releases_backpressure_before_waiting_for_monitors(self):
        script = r'''
import os,queue,sys,time
from pathlib import Path
from unittest.mock import patch
import test_app_export as support
root=Path(sys.argv[1]);source=root/'flood.csv';source.write_text('Name,Value\nFlood,713\n')
os.environ.update(PUBNEXUS_STORE=str(root/'store'),PUBNEXUS_OUTPUT=str(root/'output'))
app=support.app_module.App();app.pdfs=[source];app.push=lambda *_:None
original=queue.Queue;calls=0
def sample(_):
 global calls
 calls+=1
 if calls>1:raise RuntimeError('Synthetic coordinator failure')
 return {'total_bytes':32*1024**3,'available_bytes':24*1024**3,'main_bytes':0,'rss_bytes':0,'jobs':{}}
def flood(paths,output,**kwargs):
 while not kwargs['cancel']():
  kwargs['on_event']('prog',{'path':str(paths[0]),'name':paths[0].name,'stage':'Flood','file_pct':3})
with patch('queue.Queue',side_effect=lambda *a,**k:original(1)),patch('pubnexus.resource_budget.sample',side_effect=sample),patch('pubnexus.extraction_process.run',side_effect=flood):
 app.start([source]);app.worker.join(4)
 alive=app.worker.is_alive()
 print('COORDINATOR_REAPED='+str(not alive),flush=True)
 os._exit(2 if alive else 0)
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(self.root)],
                                capture_output=True, text=True, timeout=8,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('COORDINATOR_REAPED=True', result.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows native worker test')
    def test_two_actual_native_workers_progress_independently_while_gui_works(self):
        sources = [self.source('one.pdf'), self.source('two.pdf')]
        with sources[1].open('ab') as stream:
            stream.write(b'\n% distinct parallel fixture\n')
        self.app.pdfs = sources
        native = self.root / 'native.py'
        native.write_text('''import ctypes,json,sys
from pathlib import Path
request=Path(sys.argv[-1]);data=json.loads(request.read_text(encoding='utf-8'))
source=data['paths'][0]
with (request.parent/'events.jsonl').open('a',encoding='utf-8') as f:
 f.write(json.dumps({'kind':'prog','payload':{'path':source,'name':Path(source).name,'stage':'Native work','file_pct':23}})+'\\n');f.flush()
ctypes.PyDLL('kernel32').Sleep(60000)
''', encoding='utf-8')
        self.addCleanup(self.app.renderer.close_all)
        with patch.object(extraction_process, '_command', return_value=[sys.executable, '-B', str(native)]):
            self.app.start(sources, True)
            try:
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    paths = {p['path'] for k, p in self.events if k == 'prog' and p.get('stage') == 'Native work'}
                    if len(paths) == 2:
                        break
                    time.sleep(.02)
                self.assertEqual(paths, {str(p) for p in sources}, 'Both independent native workers must run together')
                rows = self.app.file_rows()
                self.assertTrue(all(row['status'] == 'running' and row['progress_pct'] == 23 for row in rows))
                began = time.monotonic()
                self.assertIn('key', self.app.source_view(0))
                hits = support.app_module.Api(self.app).search_source(1, 'offline')
                self.assertTrue(hits['ok'] and hits['results'])
                self.assertLess(time.monotonic() - began, 2)
            finally:
                self.app.cancel.set()
                self.app.worker.join(timeout=5)
            self.assertFalse(self.app.worker.is_alive())
            workers = [p for k, p in self.events if k == 'worker_started']
            self.assertEqual(len(workers), 2)
            self.assertEqual(len({p['pid'] for p in workers}), 2)
            self.assertTrue(all(not process_support.BackgroundProcessTests._alive(p['pid']) for p in workers))
            self.assertTrue(all(row['status'] == 'paused' for row in self.app.file_rows()))


if __name__ == '__main__':
    unittest.main()
