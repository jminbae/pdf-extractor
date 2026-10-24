import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import test_app_export as support


class AppInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"PUBNEXUS_STORE": str(self.root / "cache"),
                                     "PUBNEXUS_OUTPUT": str(self.root / "Markdown")})
        env.start()
        self.addCleanup(env.stop)
        config = patch.object(support.app_module.utils, "load_config", return_value={})
        config.start()
        self.addCleanup(config.stop)
        self.app = support.app_module.App()
        self.app.push = lambda *args: None
        self.app._reconcile = lambda folder: None

    def file(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"input")
        return p.resolve()

    def test_individual_files_keep_same_names_from_different_folders(self):
        first = self.file("one/report.pdf")
        second = self.file("two/report.PDF")
        ignored = self.file("program.exe")
        self.app.add_files([first, second, first, ignored])
        self.assertEqual(set(self.app.pdfs), {first, second})
        self.assertEqual(len({row["path"] for row in self.app.file_rows()}), 2)

    def test_folder_scan_handles_case_and_ignores_generated_markdown(self):
        source = self.file("documents/book.PDF")
        self.file("documents/sub/Markdown/preview.pdf")
        self.app.set_folder(self.root / "documents")
        self.assertEqual(self.app.pdfs, [source])

    def test_folder_scan_excludes_the_central_output_folder(self):
        source = self.file('documents/book.pdf')
        self.app.output_dir = self.root / 'documents' / 'gathered-output'
        self.file('documents/gathered-output/book.md')
        self.file('documents/gathered-output/assets/extracted.txt')
        self.app.set_folder(self.root / 'documents')
        self.assertEqual(self.app.pdfs, [source])

    def test_inputs_can_be_appended_without_changing_a_running_job(self):
        self.app.worker = Mock()
        self.app.worker.is_alive.return_value = True
        active = self.file('active.pdf')
        extra = self.file('new.pdf')
        self.app.pdfs = [active]
        self.app._pending_paths = {support.app_module._norm(active)}
        self.app.add_files([extra])
        self.assertEqual(self.app.pdfs, [active, extra])
        self.assertEqual(self.app._pending_paths, {support.app_module._norm(active)})

    def test_drop_uses_native_full_paths_and_folder_filter(self):
        source = self.file("drop/nested/book.pdf")
        self.file("drop/Markdown/generated.md")
        event = {"dataTransfer": {"files": [
            {"name": "drop", "pywebviewFullPath": str(self.root / "drop")},
            {"name": "fake.pdf", "path": str(self.file("fake.pdf"))}]}}
        result = self.app.add_dropped_files(event)
        self.assertTrue(result["ok"])
        self.assertEqual(self.app.pdfs, [source])

    def test_empty_drop_does_not_change_list_during_extraction(self):
        self.app.worker = Mock()
        self.app.worker.is_alive.return_value = True
        result = self.app.add_dropped_files({"dataTransfer": {"files": []}})
        self.assertFalse(result["ok"])
        self.assertFalse(self.app.pdfs)

    def test_browse_lists_immediate_folders_and_documents_without_recursing(self):
        direct = self.file('documents/direct.PDF')
        self.file('documents/sub/nested.pdf')
        self.file('documents/Markdown/generated.md')
        self.file('documents/program.exe')
        api = support.app_module.Api(self.app)
        self.assertTrue(callable(getattr(api, 'browse_folder', None)), 'Folder navigation is missing')
        result = api.browse_folder(str(direct.parent))
        self.assertTrue(result['ok'])
        self.assertEqual(self.app.pdfs, [direct])
        self.assertEqual(result['parent'], str(self.root))
        self.assertEqual(result['folders'], [{'name': 'sub', 'path': str(direct.parent / 'sub')}])
        self.assertEqual(result['files'][0]['path'], str(direct))
        self.assertEqual(api.state()['folders'], result['folders'])

    def test_browse_during_extraction_keeps_the_job_paths_and_output(self):
        active = self.file('old/active.csv')
        new = self.file('new/new.csv')
        self.app.pdfs = [active]
        self.app.worker = Mock()
        self.app.worker.is_alive.return_value = True
        key = support.app_module._norm(active)
        self.app._pending_paths = {key}
        self.app._active_paths = {key}
        self.app._background_queue = [(active, True)]
        self.app._worker_pids = {key: 12345}
        output = self.app.output_dir
        api = support.app_module.Api(self.app)
        self.assertTrue(callable(getattr(api, 'browse_folder', None)), 'Folder navigation is missing')
        self.assertTrue(api.browse_folder(str(new.parent))['ok'])
        self.assertEqual(self.app.pdfs, [new])
        self.assertEqual(self.app._pending_paths, {key})
        self.assertEqual(self.app._active_paths, {key})
        self.assertEqual(self.app._background_queue, [(active, True)])
        self.assertEqual(self.app._worker_pids, {key: 12345})
        self.assertEqual(self.app.output_dir, output)
        self.assertFalse(self.app.cancel.is_set())

    def test_invalid_browse_does_not_replace_the_current_selection(self):
        source = self.file('documents/report.csv')
        self.app.pdfs = [source]
        api = support.app_module.Api(self.app)
        self.assertTrue(callable(getattr(api, 'browse_folder', None)), 'Folder navigation is missing')
        for value in ('', 'relative/path', str(self.root / 'missing'), str(source), None, 13):
            self.assertFalse(api.browse_folder(value)['ok'])
            self.assertEqual(self.app.pdfs, [source])

    def test_stale_indexes_after_folder_navigation_cannot_extract_a_different_file(self):
        before = self.file('old/report.csv')
        now = self.file('new/report.csv')
        self.app.pdfs = [now]
        self.app.start = Mock()
        api = support.app_module.Api(self.app)
        self.assertIn('expected_paths', __import__('inspect').signature(api.extract_selected).parameters,
                      'Extraction needs the selected paths, not only mutable row indexes')
        result = api.extract_selected([0], True, [str(before)])
        self.assertFalse(result['ok'])
        self.app.start.assert_not_called()
        self.app.start.return_value = [now]
        result = api.extract_selected([0], True, [str(now)])
        self.assertTrue(result['ok'])
        self.app.start.assert_called_once_with([now], force=True)


if __name__ == "__main__":
    unittest.main()
