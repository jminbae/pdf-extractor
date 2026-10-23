import unittest
from pathlib import Path
from unittest.mock import patch

import test_app_export as support
from pubnexus import pdf_markdown, pdf_pipeline
from pubnexus.general_pdf import ExtractionCancelled


class PDFPipelineTests(support.AppExportTests):
    def test_native_recovery_removes_stale_sdk_text_assets_and_identity(self):
        from pubnexus.general_pdf import ensure_fulltext
        from pubnexus import render
        source = self.source()
        old = dict(self.extracted(source), source='pdf_sdk', sdk_markdown='STALE SDK BODY',
                   sdk_blocks=[{'id': 'obsolete'}], sdk_assets=[{'image': 'obsolete.png'}],
                   sdk_engine='old-sdk', pdf_conversion_engine='old-adapter')
        recovered, report = ensure_fulltext(old, source)
        self.assertEqual(recovered['source'], 'pdf_general')
        self.assertNotIn('sdk_markdown', recovered)
        self.assertNotIn('sdk_assets', recovered)
        self.assertNotIn('pdf_conversion_engine', recovered)
        self.assertNotIn('STALE SDK BODY', render.to_markdown(recovered))
        self.assertEqual(old['sdk_markdown'], 'STALE SDK BODY')

    def test_healthy_paper_never_enters_general_sdk(self):
        doc = dict(self.extracted(self.source()), source='grobid')
        with patch.object(pdf_markdown, 'available', side_effect=AssertionError('Paper owns its body')):
            self.assertIs(pdf_pipeline.upgrade_general(doc, self.root / 'book.pdf'), doc)

    def test_sdk_result_owns_body_and_references_without_legacy_merging(self):
        source = self.source()
        doc = self.extracted(source)
        doc['references'] = [{'title': 'Spurious paper reference'}]
        published = dict(source='pdf_sdk', sdk_engine='pymupdf-2.0', sdk_markdown='# Official body\n',
                         extraction_report={'status': 'complete', 'engine': {'identity': pdf_markdown.engine_id()}})
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', return_value=published):
            result = pdf_pipeline.upgrade_general(doc, source)
        self.assertNotIn('references', result)
        self.assertTrue(pdf_pipeline.current(result))
        self.assertEqual(result['sha1'], doc['sha1'])
        self.assertEqual(result['sdk_markdown'], '# Official body\n')

    def test_unavailable_sdk_retains_native_ocr_without_claiming_sdk_success(self):
        source = self.source()
        doc = self.extracted(source)
        with patch.object(pdf_markdown, 'available', return_value=False):
            self.assertIs(pdf_pipeline.upgrade_general(doc, source), doc)
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', side_effect=RuntimeError('Scan requires native OCR')):
            result = pdf_pipeline.upgrade_general(doc, source)
        self.assertEqual(result['source'], 'pdf_general')
        self.assertEqual(result['body_text'], doc['body_text'])
        self.assertEqual(result['extraction_report']['status'], 'partial')
        self.assertEqual(result['pdf_conversion_attempt'], pdf_markdown.engine_id())

    def test_cancel_never_exports_a_fallback_as_success(self):
        source = self.source()
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', side_effect=ExtractionCancelled('stopped')):
            with self.assertRaises(ExtractionCancelled):
                pdf_pipeline.upgrade_general(self.extracted(source), source)

    def test_failure_does_not_promote_an_old_sdk_cache_to_current(self):
        source = self.source()
        doc = dict(self.extracted(source), source='pdf_sdk', sdk_markdown='Old SDK text',
                   pdf_conversion_engine='older-sdk')
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', side_effect=RuntimeError('Temporary failure')):
            result = pdf_pipeline.upgrade_general(doc, source)
        self.assertFalse(pdf_pipeline.current(result))
        self.assertEqual(result['pdf_conversion_engine'], 'older-sdk')

    def test_detected_source_change_is_fatal_even_when_stat_is_unchanged(self):
        source = self.source()
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', side_effect=pdf_markdown.PDFMarkdownError('Changed hash', 'source_changed')):
            with self.assertRaises(pdf_markdown.PDFMarkdownError):
                pdf_pipeline.upgrade_general(self.extracted(source), source)

    def test_sdk_that_drops_a_whole_body_page_cannot_replace_native_text(self):
        source = self.source()
        doc = self.extracted(source)
        paragraph = doc['body_text'][0]['paragraphs'][0]
        paragraph['text'] = 'Substantial original document body with important sentences and numeric evidence 9173. ' * 4
        paragraph['source'] = {'page': 1, 'bbox': [70, 100, 400, 300]}
        doc['source_map'] = {'coordinate_space': 'pymupdf_unrotated_points'}
        doc['extraction_report'] = {'status': 'complete', 'pages': [{'page': 1, 'height': 800}]}
        published = {'source': 'pdf_sdk', 'sdk_markdown': '# A wrongly empty extraction\n',
                     'extraction_report': {'status': 'complete'}}
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', return_value=published):
            result = pdf_pipeline.upgrade_general(doc, source)
        self.assertEqual(result['source'], 'pdf_general')
        self.assertEqual(result['body_text'], doc['body_text'])
        self.assertIn('누락', result['extraction_report']['warnings'][0])

    def test_loss_guard_accepts_preserved_body_despite_markdown_and_spaces(self):
        source = self.source()
        doc = self.extracted(source)
        text = 'Substantial Mixed CASE original body with important sentences and numeric evidence 9173. ' * 3
        paragraph = doc['body_text'][0]['paragraphs'][0]
        paragraph.update(text=text, source={'page': 1, 'bbox': [70, 100, 400, 300]})
        doc['source_map'] = {'coordinate_space': 'pymupdf_unrotated_points'}
        doc['extraction_report'] = {'status': 'complete', 'pages': [{'page': 1, 'height': 800}]}
        published = {'source': 'pdf_sdk', 'sdk_markdown': '# Heading\n\n' + text,
                     'extraction_report': {'status': 'complete'}}
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', return_value=published):
            result = pdf_pipeline.upgrade_general(doc, source)
        self.assertEqual(result['source'], 'pdf_sdk')
        self.assertEqual(result['extraction_report']['native_coverage']['sampled_text_coverage'], 1)

    def test_no_native_prose_is_not_evidence_that_the_sdk_dropped_body(self):
        source = self.source()
        doc = self.extracted(source)
        doc['body_text'] = []
        doc['source_map'] = {'coordinate_space': 'pymupdf_unrotated_points'}
        doc['extraction_report'] = {'status': 'complete', 'pages': [{'page': 1, 'height': 800, 'status': 'blank'}]}
        published = {'source': 'pdf_sdk', 'sdk_markdown': '', 'extraction_report': {'status': 'complete'}}
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', return_value=published):
            result = pdf_pipeline.upgrade_general(doc, source)
        self.assertEqual(result['source'], 'pdf_sdk')
        self.assertFalse(result['extraction_report']['native_coverage']['baseline_available'])

    def test_cached_current_sdk_is_not_reconverted(self):
        doc = {'source': 'pdf_sdk', 'sdk_markdown': 'Original SDK text',
               'pdf_conversion_engine': pdf_markdown.engine_id()}
        with patch.object(pdf_markdown, 'convert', side_effect=AssertionError('No conversion for current cache')):
            self.assertIs(pdf_pipeline.upgrade_general(doc, self.root / 'unused.pdf'), doc)

    def test_app_saves_sdk_markdown_and_reopens_cache_without_native_reassembly(self):
        from pubnexus import store
        source = self.source()
        self.app.pdfs = [source]
        sdk = dict(self.extracted(source), source='pdf_sdk', sdk_engine='pymupdf-2.0',
                   sdk_markdown='# Official document\n\n- One\n- Two\n', sdk_blocks=[], sdk_assets=[],
                   extraction_report={'status': 'complete', 'warnings': [], 'pages': [],
                                      'engine': {'identity': pdf_markdown.engine_id()}})
        with patch.object(pdf_markdown, 'available', return_value=True), \
             patch.object(pdf_markdown, 'convert', return_value=sdk) as converted, \
             patch('pubnexus.single.extract_one', return_value=self.extracted(source)), \
             patch('pubnexus.docling_tables.enhance_document', side_effect=AssertionError('SDK owns its tables')):
            self.app._work([source])
            self.assertEqual(converted.call_count, 1)
            row = self.app.file_rows()[0]
            self.assertTrue(row['md_ready'], row)
            self.assertEqual(row['progress_pct'], 100)
            self.assertEqual(Path(row['markdown']).read_text(encoding='utf-8'), sdk['sdk_markdown'])
            with patch('pubnexus.general_pdf.ensure_fulltext', side_effect=AssertionError('Current SDK cache')):
                self.app._work([source])
            self.assertEqual(converted.call_count, 1)
        shown = self.app.doc_view(source)
        self.assertIn('<ul>', shown['html'])
        self.assertEqual(shown['md_text'], sdk['sdk_markdown'])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name, PDFPipelineTests)
                              for name in dir(PDFPipelineTests) if name.startswith('test_')
                              and name in PDFPipelineTests.__dict__)
