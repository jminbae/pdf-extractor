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

    def test_text_and_markdown_are_viewable_but_never_extraction_targets(self):
        text = self.file('notes.txt')
        markdown = self.file('notes.MD')
        document = self.file('book.pdf')
        self.app.pdfs = [text, markdown, document]
        api = support.app_module.Api(self.app)
        with patch.object(self.app, 'start', return_value=[document]) as start:
            result = api.extract_selected([0, 1, 2], force=True,
                                          expected_paths=[str(p) for p in self.app.pdfs])
        self.assertTrue(result['ok'], result)
        start.assert_called_once_with([document], force=True)
        self.assertFalse(self.app.file_rows()[0]['extractable'])
        self.assertFalse(self.app.file_rows()[1]['extractable'])
        self.assertTrue(self.app.file_rows()[2]['extractable'])
        with patch.object(self.app, 'start', side_effect=AssertionError('Already textual')):
            self.assertFalse(api.extract('one', 0)['ok'])
            self.assertFalse(api.extract_selected([0, 1], force=True)['ok'])

    def test_direct_admission_does_not_launch_workers_for_text_files(self):
        text = self.file('notes.txt')
        with patch('threading.Thread', side_effect=AssertionError('Text needs no worker')):
            self.assertEqual(self.app.start([text]), [])
        self.assertFalse(self.app._pending_paths)

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

    def test_opening_one_file_navigates_to_its_folder_and_returns_the_source_to_open(self):
        old = self.file('old/old.pdf')
        picked = self.file('new/picked.pdf')
        neighbour = self.file('new/neighbour.pdf')
        self.app.folder, self.app.pdfs = old.parent, [old]
        self.app.window = Mock()
        self.app.window.create_file_dialog.return_value = [str(picked)]
        result = support.app_module.Api(self.app).pick_files()
        self.assertTrue(result['ok'])
        self.assertEqual(self.app.folder, picked.parent)
        self.assertEqual(set(self.app.pdfs), {picked, neighbour})
        self.assertEqual(result.get('open_path'), str(picked))
        self.assertEqual(result.get('selected_paths'), [str(picked)])
        self.assertEqual(result['files'], self.app.file_rows())

    def test_opening_multiple_files_selects_them_without_opening_an_arbitrary_reader(self):
        files = [self.file('documents/one.pdf'), self.file('documents/two.pdf')]
        self.file('documents/other.pdf')
        self.app.window = Mock()
        self.app.window.create_file_dialog.return_value = list(map(str, files))
        result = support.app_module.Api(self.app).pick_files()
        self.assertEqual(result.get('selected_paths'), list(map(str, files)))
        self.assertFalse(result.get('open_path'))
        self.assertEqual(len(result['files']), 3)

    def test_folder_navigation_exposes_shortcuts_without_scanning_drives(self):
        from pubnexus import output_location
        self.assertTrue(callable(getattr(output_location, 'known_directory', None)))
        with patch.object(output_location, 'known_directory', side_effect=lambda kind: self.root / kind), \
             patch.object(support.app_module.os, 'listdrives', return_value=['C:\\', 'Z:\\'], create=True), \
             patch.object(Path, 'is_dir', side_effect=AssertionError('Navigation roots must not probe drives')):
            navigation = self.app.folder_state()['navigation']
        self.assertEqual([row['path'] for row in navigation['shortcuts']],
                         [str(self.root / name) for name in ('documents', 'desktop', 'downloads')])
        self.assertEqual([row['path'] for row in navigation['drives']], ['C:\\', 'Z:\\'])

    def test_expanding_a_tree_folder_does_not_navigate_or_change_background_work(self):
        source = self.file('current/active.csv')
        child = self.file('elsewhere/child/readme.txt')
        self.file('elsewhere/second/nested/readme.txt')
        self.file('elsewhere/not-a-document.exe')
        self.app.folder, self.app.pdfs = source.parent, [source]
        self.app._folders = [{'name': 'kept', 'path': str(source.parent)}]
        self.app._pending_paths = {support.app_module._norm(source)}
        self.app._background_queue = [(source, True)]
        before = (self.app.folder, list(self.app.pdfs), list(self.app._folders), self.app.output_dir)
        api = support.app_module.Api(self.app)
        self.assertTrue(callable(getattr(api, 'list_folders', None)), 'Tree expansion must be read-only')
        self.app.ui_save = Mock(side_effect=AssertionError('Expanding a tree must not navigate'))
        result = api.list_folders(str(child.parent.parent))
        self.assertTrue(result['ok'])
        self.assertEqual([row['name'] for row in result['folders']], ['child', 'second'])
        self.assertEqual((self.app.folder, self.app.pdfs, self.app._folders, self.app.output_dir), before)
        self.assertEqual(self.app._pending_paths, {support.app_module._norm(source)})
        self.assertEqual(self.app._background_queue, [(source, True)])

    def test_invalid_tree_expansion_does_not_replace_the_current_folder(self):
        source = self.file('current/report.pdf')
        self.app.folder, self.app.pdfs = source.parent, [source]
        api = support.app_module.Api(self.app)
        self.assertTrue(callable(getattr(api, 'list_folders', None)))
        for path in ('', 'relative', str(source), str(self.root / 'missing')):
            self.assertFalse(api.list_folders(path)['ok'])
        self.assertEqual(self.app.pdfs, [source])


if __name__ == "__main__":
    unittest.main()
