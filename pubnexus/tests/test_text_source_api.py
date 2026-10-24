"""Original text previews and search must work without generating Markdown or PDF."""
from pathlib import Path
import unittest
from unittest.mock import patch

import test_app_export as support


class TextSourceApiTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp

    def source(self, text, suffix='.txt', encoding='utf-8'):
        path = self.root / ('original' + suffix)
        path.write_bytes(text.encode(encoding))
        self.app.pdfs = [path]
        self.addCleanup(self.app.renderer.close_all)
        return path

    def test_original_text_preserves_source_without_pdf_conversion_or_extraction(self):
        text = '한글\r\n<script>literal</script>\t42\r\n'
        path = self.source(text)
        with patch('pubnexus.preview.preview_path', side_effect=AssertionError('Text must not become PDF')), \
             patch.object(self.app, 'doc_view', side_effect=AssertionError('Preview must not extract')):
            view = self.app.source_view(0)
        self.assertEqual(view.get('source_kind'), 'text')
        self.assertEqual(view.get('text'), text)
        self.assertEqual(view['pages'], [])
        self.assertTrue(view['source_ready'])
        self.assertEqual(path.read_bytes(), text.encode())
        self.assertEqual(self.app.exported, {})

    def test_cp949_source_is_displayed_without_replacement_characters(self):
        self.source('한국어 원본\r\n두 번째 줄', encoding='cp949')
        view = self.app.source_view(0)
        self.assertEqual(view.get('text'), '한국어 원본\r\n두 번째 줄')
        self.assertEqual(view.get('encoding'), 'cp949')

    def test_source_search_returns_exact_codepoint_offsets_and_source_revision(self):
        text = '🙂 first\r\n찾기 text 찾기\r\nlast'
        self.source(text)
        view = self.app.source_view(0)
        result = support.app_module.Api(self.app).search_source(0, '찾기')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result.get('source_revision'), view.get('source_revision'))
        self.assertEqual([hit['locations'][0]['line'] for hit in result['results']], [2, 2])
        for hit in result['results']:
            location = hit['locations'][0]
            self.assertEqual(location['kind'], 'text')
            self.assertEqual(text[location['offset']:location['offset'] + location['length']], '찾기')
            self.assertEqual(hit['side'], 'source')

    def test_saved_text_click_maps_its_exact_original_span(self):
        self.source('First paragraph.\n\n둘째 문단 찾기.\n')
        self.app._work(self.app.pdfs)
        mapping = self.app.document_map(0)
        paragraph = next(block for block in mapping['blocks'] if '둘째' in block['text'])
        self.assertTrue(paragraph['locations'], mapping)
        self.assertEqual(paragraph['locations'][0]['kind'], 'text')
        self.assertEqual(paragraph['locations'][0]['line'], 1)
        self.assertEqual(paragraph['locations'][0]['offset'], 0)
        result = support.app_module.Api(self.app).location(0, paragraph['id'], mapping['revision'])
        self.assertTrue(result['ok'], result)

    def test_duplicate_text_is_not_linked_to_an_arbitrary_first_occurrence(self):
        from pubnexus import text_source
        self.source('Repeated paragraph.\n\nRepeated paragraph.\n')
        doc = {'body_text': [{'paragraphs': [{'id': 'repeated', 'text': 'Repeated paragraph.'}]}]}
        from pubnexus.preview import text_source as read_source
        mapping = text_source.document_map(doc, read_source(self.app.pdfs[0]))
        self.assertTrue(mapping['blocks'])
        self.assertTrue(all(not block['locations'] for block in mapping['blocks']))


if __name__ == '__main__':
    unittest.main()
