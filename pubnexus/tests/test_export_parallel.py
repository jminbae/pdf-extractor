"""Actual independent-process output commits; synthetic inputs only in TEMP."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubnexus import export
from pubnexus.external_documents import _stop_owned_process


CHILD = '''import json,sys,time
from pathlib import Path
from pubnexus import export
base=Path(sys.argv[1]); number=sys.argv[2]
source=base/number/'same.csv'
original=export._write_markdown
def delayed(path,data,expected):
    (base/('commit-'+number)).touch()
    deadline=time.monotonic()+1
    while not all((base/('commit-'+n)).exists() for n in ('one','two')) and time.monotonic()<deadline:
        time.sleep(.01)
    return original(path,data,expected)
export._write_markdown=delayed
(base/('ready-'+number)).touch()
while not (base/'go').exists():time.sleep(.01)
doc={'source':'pdf_general','meta':{'title':'Independent '+number},
 'body_text':[{'paragraphs':[{'text':'Unique original content '+number}]}],
 'figures':[],'tables':[],'references':[]}
try:
 result=export.export_document(doc,source,output_dir=base/'out')
 receipt={'ok':True,'markdown':str(result.markdown_path)}
except Exception as error:
 receipt={'ok':False,'type':type(error).__name__,'message':str(error)}
(base/('result-'+number+'.json')).write_text(json.dumps(receipt),encoding='utf8')
'''


class ParallelExportTests(unittest.TestCase):
    def test_lock_timeout_is_closed_and_killed_owner_releases_os_lock(self):
        with tempfile.TemporaryDirectory(prefix='pubnexus-export-lock-') as temporary:
            base = Path(temporary)
            source = base / 'source.csv'
            source.write_bytes(b'untouched source')
            script = base / 'hold.py'
            script.write_text('''import sys,time
from pathlib import Path
from pubnexus import export
base=Path(sys.argv[1])
with export._output_lock(base/'out'):
    (base/'locked').touch()
    time.sleep(60)
''', encoding='utf-8')
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                               PYTHONPATH=str(Path(export.__file__).resolve().parent.parent))
            child = subprocess.Popen([sys.executable, '-B', str(script), str(base)], env=environment,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 15
                while not (base / 'locked').exists():
                    self.assertLess(time.monotonic(), deadline, 'Owned child must acquire the real OS lock')
                    time.sleep(.02)
                with self.assertRaisesRegex(export.ExportError, '대기'):
                    with export._output_lock(base / 'out', timeout=.15):
                        self.fail('A busy output must never proceed without a lock')
                self.assertFalse(list((base / 'out').glob('*.md')))
                _stop_owned_process(child)
                result = export.export_document({'meta': {'title': 'After owned child exit'},
                    'body_text': [{'paragraphs': [{'text': 'Recovered commit'}]}],
                    'figures': [], 'tables': [], 'references': []}, source, output_dir=base / 'out')
                self.assertIn('Recovered commit', result.markdown_path.read_text(encoding='utf-8'))
                self.assertEqual(source.read_bytes(), b'untouched source')
            finally:
                _stop_owned_process(child)
                if child.stderr:
                    child.stderr.close()

    def test_lock_file_cannot_follow_a_link_outside_output(self):
        with tempfile.TemporaryDirectory(prefix='pubnexus-export-lock-path-') as temporary:
            base = Path(temporary)
            source = base / 'source.csv'
            source.write_bytes(b'untouched source')
            outside = base / 'outside.lock'
            outside.write_bytes(b'untouched outside')
            dest = base / 'out'
            dest.mkdir()
            try:
                (dest / '.pubnexus-export.lock').symlink_to(outside)
            except OSError as error:
                self.skipTest('This environment does not allow owned synthetic symlinks: ' + str(error))
            with self.assertRaisesRegex(export.ExportError, '잠금'):
                export.export_document({}, source, output_dir=dest)
            self.assertEqual(outside.read_bytes(), b'untouched outside')
            self.assertFalse(list(dest.glob('*.md')))

    def test_two_processes_same_stem_preserve_both_distinct_results(self):
        with tempfile.TemporaryDirectory(prefix='pubnexus-export-parallel-') as temporary:
            base = Path(temporary)
            script = base / 'child.py'
            script.write_text(CHILD, encoding='utf-8')
            inputs = []
            for name in ('one', 'two'):
                source = base / name / 'same.csv'
                source.parent.mkdir()
                source.write_bytes(('untouched synthetic ' + name).encode())
                inputs.append((source, source.read_bytes()))
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                               PYTHONPATH=str(Path(export.__file__).resolve().parent.parent))
            children = []
            try:
                for name in ('one', 'two'):
                    children.append(subprocess.Popen(
                        [sys.executable, '-B', str(script), str(base), name],
                        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
                deadline = time.monotonic() + 15
                while not all((base / ('ready-' + name)).exists() for name in ('one', 'two')):
                    self.assertLess(time.monotonic(), deadline, 'Both owned children must reach the barrier')
                    time.sleep(.02)
                (base / 'go').touch()
                for child in children:
                    _, stderr = child.communicate(timeout=15)
                    self.assertEqual(child.returncode, 0, stderr.decode('utf-8', errors='replace'))
                results = [json.loads((base / ('result-' + name + '.json')).read_text(encoding='utf-8'))
                           for name in ('one', 'two')]
                self.assertTrue(all(result['ok'] for result in results), results)
                self.assertEqual(len({result['markdown'] for result in results}), 2)
                for name, result in zip(('one', 'two'), results):
                    self.assertIn('Unique original content ' + name,
                                  Path(result['markdown']).read_text(encoding='utf-8'))
                self.assertEqual(len(list((base / 'out').glob('*.md'))), 2)
                for source, original in inputs:
                    self.assertEqual(source.read_bytes(), original)
            finally:
                for child in children:
                    _stop_owned_process(child)
                    if child.stderr:
                        child.stderr.close()


if __name__ == '__main__':
    unittest.main()
