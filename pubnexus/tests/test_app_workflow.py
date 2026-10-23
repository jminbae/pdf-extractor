import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_app_export as support
from pubnexus import store


class WorkflowTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp
    source = support.AppExportTests.source
    extracted = support.AppExportTests.extracted

    def test_open_original_before_extraction_never_reads_or_queues_markdown(self):
        source = self.source()
        self.app.pdfs = [source]
        self.addCleanup(self.app.renderer.close_all)
        with patch.object(self.app, 'doc_view', side_effect=AssertionError('No Markdown on original open')), \
             patch.object(self.app, 'sha_for', side_effect=AssertionError('No extraction cache on original open')), \
             patch.object(self.app, 'bump', side_effect=AssertionError('Reading never changes queue')):
            result = self.app.source_view(0)
        self.assertEqual(len(result['pages']), 1)
        self.assertIn('key', result)
        self.assertNotIn('doc', result)
        self.assertFalse(self.app.done)
        self.assertFalse(self.app.exported)
        self.assertFalse(self.app.file_rows()[0]['md_ready'])

    def test_original_search_works_before_markdown_exists(self):
        source = self.source()
        self.app.pdfs = [source]
        with patch.object(self.app, 'comparison_doc', side_effect=AssertionError('No extracted result required')):
            result = support.app_module.Api(self.app).search_source(0, 'offline')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['results'])
        self.assertTrue(all(hit['side'] == 'source' for hit in result['results']))
        self.assertEqual(result['results'][0]['page'], 0)
        self.assertFalse(self.app.exported)

    def test_reopened_original_uses_current_bytes_and_retires_previous_images(self):
        import fitz
        source = self.source()
        self.app.pdfs = [source]
        self.addCleanup(self.app.renderer.close_all)
        first = self.app.source_view(0)
        self.assertTrue(self.app.renderer.png(first['key'], 0, 300))
        with fitz.open() as replacement:
            replacement.new_page(width=500, height=600)
            replacement.new_page(width=500, height=600)
            source.write_bytes(replacement.tobytes())
        second = self.app.source_view(0)
        self.assertEqual(second['pages'], [[500.0, 600.0], [500.0, 600.0]])
        self.assertNotEqual(first['key'], second['key'])
        self.assertIsNone(self.app.renderer.png(first['key'], 0, 300))

    def test_row_progress_identifies_path_and_md_is_ready_only_after_save(self):
        source = self.source()
        other = self.root / 'other' / source.name
        other.parent.mkdir()
        other.write_bytes(source.read_bytes())
        self.app.pdfs = [source, other]
        self.app._active_path = source
        self.app.prog(1, 2, source.name, '원문 확인', 0, path=source,
                      file_pct=37.5, page=3, pages=8)
        rows = self.app.file_rows()
        self.assertEqual(rows[0]['status'], 'running')
        self.assertEqual(rows[0]['progress_pct'], 37.5)
        self.assertEqual(rows[0]['progress_page'], 3)
        self.assertEqual(rows[1]['status'], 'ready')
        self.assertFalse(rows[0]['md_ready'])
        event = [p for kind, p in self.events if kind == 'prog'][-1]
        self.assertEqual(event['path'], str(source))
        self.assertEqual(event['file_pct'], 37.5)
        self.app._active_path = None
        doc = self.extracted(source)
        store.save(doc['sha1'], doc, source)
        self.app._work([source])
        self.assertTrue(self.app.file_rows()[0]['md_ready'])
        self.assertEqual(self.app.file_rows()[0]['progress_pct'], 100)

    def test_previous_source_markdown_is_not_mixed_with_current_cached_html(self):
        source = self.source()
        old_doc = self.extracted(source)
        store.save(old_doc['sha1'], old_doc, source)
        self.app.pdfs = [source]
        self.app._work([source])
        markdown = Path(self.app.file_rows()[0]['markdown'])
        old_bytes = markdown.read_bytes()
        source.write_bytes(source.read_bytes() + b'\nChanged revision\n')
        new_doc = self.extracted(source)
        new_doc['body_text'][0]['paragraphs'][0]['text'] = 'Current canonical revision content.'
        store.save(new_doc['sha1'], new_doc, source)
        view = self.app.doc_view(source)
        self.assertIn('Current canonical revision content.', view['html'])
        self.assertIn('Current canonical revision content.', view['md_text'])
        self.assertEqual(markdown.read_bytes(), old_bytes)
        self.assertFalse(self.app.file_rows()[0]['md_ready'])

    def test_legacy_general_pdf_needs_refresh_without_deleting_previous_output(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc['sha1'], doc, source)
        self.app.pdfs = [source]
        self.app._work([source])
        record = self.app.exported[support.app_module._norm(source)]
        markdown = Path(record['markdown'])
        before = markdown.read_bytes()
        from pubnexus import pdf_markdown
        record.pop('pdf_conversion_engine', None)
        with patch.object(pdf_markdown, 'available', return_value=True):
            row = self.app.file_rows()[0]
        self.assertTrue(row['needs_refresh'])
        self.assertFalse(row['md_ready'])
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(markdown.read_bytes(), before)
        self.app._work([source])
        self.assertTrue(self.app.file_rows()[0]['md_ready'])


if __name__ == '__main__':
    unittest.main()
