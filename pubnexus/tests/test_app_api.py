import unittest
from unittest.mock import Mock, patch
import test_app_export as support


class AppApiTests(support.AppExportTests):
    # Reuse TEMP setup only; discovery excludes inherited test methods below.
    def test_selected_inputs_use_indexes_without_confusing_equal_names(self):
        one = self.source("one.pdf")
        two = self.source("two.pdf")
        self.app.pdfs = [one, two]
        self.app.start = Mock()
        api = support.app_module.Api(self.app)
        result = api.extract_selected([1, 1])
        self.assertTrue(result["ok"])
        self.app.start.assert_called_once_with([two], force=False)

    def test_invalid_selection_never_starts_a_job(self):
        self.app.pdfs = [self.source()]
        self.app.start = Mock()
        result = support.app_module.Api(self.app).extract_selected([-1, 200, "0"])
        self.assertFalse(result["ok"])
        self.app.start.assert_not_called()

    def test_external_open_uses_only_a_selected_source_or_its_export(self):
        source = self.source()
        self.app.pdfs = [source]
        api = support.app_module.Api(self.app)
        with patch.object(support.app_module.os, "startfile") as opened:
            self.assertTrue(api.open_file(0, "source")["ok"])
            opened.assert_called_once_with(str(source))
            self.assertFalse(api.open_file(0, "markdown")["ok"])
            self.assertFalse(api.open_file(0, "arbitrary.exe")["ok"])
            self.assertFalse(api.open_file(-1, "source")["ok"])

    def test_csv_uses_native_adapter_and_saves_without_paper_engine(self):
        source = self.root / 'measurements.csv'
        source.write_text('Name,Value\nSample,3.14\n', encoding='utf-8')
        self.app.add_files([source])
        self.assertEqual(self.app.pdfs, [source])
        with patch('pubnexus.single.extract_one', side_effect=AssertionError('Not PDF')), \
             patch.object(self.app.grobid, 'wait', side_effect=AssertionError('No paper engine')):
            self.app._work([source])
        self.assertTrue(self.app.file_rows()[0]['done'])
        self.assertIn('| Sample | 3.14 |', (self.root / 'Markdown' / 'measurements.md').read_text(encoding='utf-8'))

    def test_reextract_refreshes_coordinates_when_markdown_bytes_do_not_change(self):
        source = self.source()
        self.app.pdfs = [source]
        doc = self.extracted(source)
        paragraph = doc['body_text'][0]['paragraphs'][0]
        paragraph['source'] = {'page': 1, 'bbox': [72, 60, 200, 80]}
        api = support.app_module.Api(self.app)
        with patch('pubnexus.single.extract_one', return_value=doc):
            self.app._work([source], force=True)
            self.assertEqual(api.location(0, 'p-1')['bbox'][0], 72)
            paragraph['source']['bbox'][0] = 80
            self.app._work([source], force=True)
        self.assertEqual(api.location(0, 'p-1')['bbox'][0], 80)

    def test_external_rtf_is_automatically_exported_without_paper_parser(self):
        from pubnexus.external_documents import find_engine
        if find_engine('pandoc') is None:
            self.skipTest('Optional local Pandoc is unavailable')
        source = self.root / 'external.rtf'
        source.write_bytes(br'{\rtf1\ansi Original RTF content 9137.}')
        original = source.read_bytes()
        self.app.add_files([source])
        self.assertIn(source, self.app.pdfs)
        with patch('pubnexus.single.extract_one', side_effect=AssertionError('Not PDF')):
            self.app._work([source])
        self.assertTrue(self.app.file_rows()[0]['done'])
        self.assertIn('Original RTF content 9137.', (self.root / 'Markdown' / 'external.md').read_text(encoding='utf-8'))
        self.assertEqual(source.read_bytes(), original)

    def test_cached_pdf_receives_table_enhancement_before_automatic_export(self):
        import copy
        from pubnexus import store
        source = self.source()
        self.app.pdfs = [source]
        original = self.extracted(source)
        store.save(original['sha1'], original, source)
        enhanced = copy.deepcopy(original)
        enhanced['tables'] = [{'id': 't1', 'caption': 'Table 1', 'markdown': '| Name | Value |\n| --- | --- |\n| Sample | 9173 |'}]
        enhanced['extraction_report'] = {'status': 'partial', 'warnings': ['Synthetic table review warning']}
        with patch('pubnexus.general_pdf.ensure_fulltext', return_value=(original, {})), \
             patch('pubnexus.docling_tables.enhance_document', return_value=enhanced) as enhance, \
             patch('pubnexus.single.extract_one', side_effect=AssertionError('Cached PDF')):
            self.app._work([source])
        enhance.assert_called_once()
        self.assertIn('| Sample | 9173 |', (self.root / 'Markdown' / 'book.md').read_text(encoding='utf-8'))
        row = self.app.file_rows()[0]
        self.assertTrue(row['done'])
        self.assertEqual(row['status'], 'partial')
        self.assertIn('Synthetic table review warning', row['warnings'])

    def test_failed_table_supplement_cannot_be_exported_as_success(self):
        source = self.source()
        self.app.pdfs = [source]
        original = self.extracted(source)
        failed = dict(original, extraction_report={'status': 'failed', 'warnings': ['Source changed']})
        with patch('pubnexus.single.extract_one', return_value=original), \
             patch('pubnexus.docling_tables.enhance_document', return_value=failed):
            self.app._work([source], force=True)
        self.assertFalse((self.root / 'Markdown' / 'book.md').exists())
        final = [payload for kind, payload in self.events if kind == 'run'][-1]
        self.assertEqual(final['nfail'], 1)

    def test_ocr_child_command_does_not_create_a_gui(self):
        with patch.object(support.app_module.sys, 'argv', ['extractor.exe', '--ocr-worker', 'request.json']), \
             patch('pubnexus.local_ocr.worker_cli', return_value=0) as worker, \
             patch.object(support.app_module, 'build', side_effect=AssertionError('No GUI for OCR child')):
            with self.assertRaises(SystemExit) as stopped:
                support.app_module.main()
        self.assertEqual(stopped.exception.code, 0)
        worker.assert_called_once_with('request.json')

    def test_remote_debugging_requires_explicit_hidden_test_launch(self):
        import webview
        import os
        for hidden, expected in ((False, None), (True, 54321)):
            app = Mock()
            observed = []
            argv = ['extractor.exe', '--hidden'] if hidden else ['extractor.exe']
            with patch.object(support.app_module.sys, 'argv', argv), \
                 patch.dict(os.environ, {'PNX_TEST_DEBUG_PORT': '54321'}), \
                 patch.object(webview, 'settings', {'REMOTE_DEBUGGING_PORT': None}), \
                 patch.object(support.app_module, 'build', return_value=(app, Mock(), Mock())), \
                 patch.object(webview, 'start', side_effect=lambda *a, **k: observed.append(webview.settings['REMOTE_DEBUGGING_PORT'])):
                support.app_module.main()
            self.assertEqual(observed, [expected])

    def test_output_folder_can_be_opened_without_a_selected_document(self):
        api = support.app_module.Api(self.app)
        self.assertEqual(api.state()['output_dir'], str(self.app.output_dir))
        with patch.object(support.app_module.os, 'startfile') as opened:
            result = api.reveal_output()
        self.assertTrue(result['ok'])
        opened.assert_called_once_with(str(self.app.output_dir))
        self.assertTrue(self.app.output_dir.is_dir())

    def test_page_cancel_is_stopped_without_file_failure_or_export(self):
        from pubnexus.general_pdf import ExtractionCancelled
        source = self.source()
        self.app.pdfs = [source]
        with patch('pubnexus.single.extract_one', side_effect=ExtractionCancelled('Stopped')):
            self.app._work([source], force=True)
        final = [payload for kind, payload in self.events if kind == 'run'][-1]
        self.assertTrue(final['stopped'])
        self.assertEqual(final['nfail'], 0)
        self.assertFalse((self.root / 'Markdown' / 'book.md').exists())

    def test_changed_preview_engine_invalidates_display_coordinate_cache(self):
        import fitz
        from pubnexus.preview import Preview
        source = self.source()
        rotated = self.root / 'rotated-preview.pdf'
        with fitz.open(source) as pdf:
            pdf[0].set_rotation(90)
            pdf.save(rotated)
        self.app.pdfs = [source]
        with patch('pubnexus.single.extract_one', return_value=self.extracted(source)):
            self.app._work([source], force=True)
        state = {'revision': 'first', 'path': source}
        with patch('pubnexus.preview.renderer_identity', side_effect=lambda path: state['revision']), \
             patch('pubnexus.preview.preview_path', side_effect=lambda path: Preview(state['path'], True, 'test', '')):
            self.assertEqual(self.app.document_map(0)['pages'][0]['width'], 595)
            state.update(revision='second', path=rotated)
            self.assertEqual(self.app.document_map(0)['pages'][0]['width'], 842)


# Keep this module's run limited to its API cases instead of repeating the base suite.
def load_tests(loader, tests, pattern):
    names = [name for name in AppApiTests.__dict__ if name.startswith("test_")]
    return unittest.TestSuite(AppApiTests(name) for name in names)


if __name__ == "__main__":
    unittest.main()
