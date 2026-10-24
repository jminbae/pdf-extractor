import unittest
from pathlib import Path
from unittest.mock import patch

import test_app_export as support
from pubnexus import pdf_markdown, pdf_pipeline
from pubnexus.general_pdf import ExtractionCancelled


class PDFPipelineTests(support.AppExportTests):
    def test_common_extract_checks_displayed_table_text_not_markdown_tags(self):
        import fitz
        text = ('Alpha observations retain every original measurement and the complete '
                'clinical narrative across multiple physical lines in the table cell.')
        source = self.root / 'multiline-table.pdf'
        with fitz.open() as pdf:
            pdf.new_page().insert_textbox((50, 60, 500, 240), text)
            pdf.save(source)
        cell = '<br>'.join(text.split())
        variants = ('| Observation |\n| --- |\n| ' + cell + ' |\n',
                    '<table><tr><td colspan="2">' + cell + '</td></tr></table>\n')
        before = source.read_bytes()
        for markdown in variants:
            with self.subTest(markdown=markdown[:30]), \
                 patch.object(pdf_markdown, 'convert', return_value={
                     'source': 'pdf_sdk', 'sdk_markdown': markdown,
                     'extraction_report': {'status': 'complete'}}), \
                 patch('pubnexus.general_pdf.ensure_fulltext',
                       side_effect=AssertionError('Structured table text is present')):
                result = pdf_pipeline.extract(source)
                self.assertEqual(result['source'], 'pdf_sdk')
                self.assertEqual(result['sdk_markdown'], markdown)
                self.assertEqual(result['extraction_report']['native_coverage']['wholly_unrepresented_pages'], [])
                self.assertEqual(result['extraction_report']['native_coverage']['sampled_text_coverage'], 1.0)
        self.assertEqual(source.read_bytes(), before)

    def test_common_extract_table_plaintext_does_not_double_occurrence_budget(self):
        import fitz
        text = ('Repeated observations contain all original measurement details and '
                'remain a separate complete native page that must never disappear.')
        source = self.root / 'repeated-table-pages.pdf'
        with fitz.open() as pdf:
            for _ in range(2):
                pdf.new_page().insert_textbox((50, 60, 500, 240), text)
            pdf.save(source)
        markdown = '| Observation |\n| --- |\n| ' + '<br>'.join(text.split()) + ' |\n'
        with patch.object(pdf_markdown, 'convert', return_value={
                'source': 'pdf_sdk', 'sdk_markdown': markdown}), \
             patch('pubnexus.local_ocr.discover', return_value=None), \
             patch('pubnexus.general_pdf.shutil.which', return_value=None):
            result = pdf_pipeline.extract(source)
        self.assertEqual(result['source'], 'pdf_general')
        self.assertEqual(len(result['body_text']), 2)
        self.assertIn('SDK', '\n'.join(result['extraction_report']['warnings']))

    def test_common_extract_starts_with_sdk_without_legacy_paper_work(self):
        from pubnexus import store
        source = self.source()
        before = source.read_bytes()
        published = {'source': 'pdf_sdk', 'sdk_markdown': '# Official common PDF\n\nAn offline document body for export verification.\n',
                     'extraction_report': {'status': 'complete'}}
        stages = []
        with patch.object(pdf_markdown, 'convert', return_value=published) as convert, \
             patch('pubnexus.single.extract_one', side_effect=AssertionError('No paper pipeline')), \
             patch('pubnexus.metadata.collect_one', side_effect=AssertionError('No network metadata')), \
             patch('pubnexus.captions.apply_to_parsed', side_effect=AssertionError('No paper captions')), \
             patch('pubnexus.general_pdf.ensure_fulltext', side_effect=AssertionError('SDK must run first')):
            result = pdf_pipeline.extract(source, progress=lambda *event: stages.append(event))
        convert.assert_called_once()
        self.assertEqual(convert.call_args.args[0], source)
        self.assertEqual(convert.call_args.args[1], store.figs_dir(store.file_sha1(source)))
        self.assertEqual(stages[0], (0, 1, 'pdf_sdk'))
        self.assertEqual(result['sha1'], store.file_sha1(source))
        self.assertEqual(result['source_file'], str(source))
        self.assertTrue(pdf_pipeline.current(result))
        self.assertEqual(source.read_bytes(), before)

    def test_common_extract_sdk_failure_preserves_actual_native_body_once(self):
        source = self.source()
        before = source.read_bytes()
        stages = []
        with patch.object(pdf_markdown, 'convert', side_effect=pdf_markdown.PDFMarkdownError('Offline SDK', 'unavailable')) as convert, \
             patch('pubnexus.single.extract_one', side_effect=AssertionError('No paper fallback')), \
             patch('pubnexus.local_ocr.discover', return_value=None), \
             patch('pubnexus.general_pdf.shutil.which', return_value=None), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
            result = pdf_pipeline.extract(source, progress=lambda *event: stages.append(event))
            self.assertIs(pdf_pipeline.upgrade_general(result, source), result)
        self.assertEqual(convert.call_count, 1, 'A fallback must not immediately retry the same SDK')
        self.assertEqual(result['source'], 'pdf_general')
        text = '\n'.join(p['text'] for s in result['body_text'] for p in s['paragraphs'])
        self.assertIn('An offline document body for export verification.', text)
        self.assertEqual(result['extraction_report']['status'], 'partial')
        self.assertTrue(result['extraction_report']['used_fallback'])
        self.assertIn('Offline SDK', '\n'.join(result['extraction_report']['warnings']))
        self.assertEqual(result['pdf_conversion_attempt'], pdf_markdown.engine_id())
        self.assertTrue(any(event[2] == 'native' for event in stages))
        self.assertFalse(pdf_pipeline.current(result))
        self.assertEqual(source.read_bytes(), before)

    def test_common_extract_fatal_errors_and_cancel_never_enter_fallback(self):
        source = self.source()
        errors = [ExtractionCancelled('stopped'),
                  pdf_markdown.PDFMarkdownError('Changed source', 'source_changed'),
                  pdf_markdown.PDFMarkdownError('Conflicting asset', 'asset_conflict')]
        for error in errors:
            with self.subTest(error=str(error)), \
                 patch.object(pdf_markdown, 'convert', side_effect=error), \
                 patch('pubnexus.general_pdf.ensure_fulltext', side_effect=AssertionError('Fatal is not a fallback')):
                with self.assertRaises(type(error)):
                    pdf_pipeline.extract(source)

    def test_common_extract_checks_cancel_before_and_after_sdk(self):
        source = self.source()
        with patch.object(pdf_markdown, 'convert', side_effect=AssertionError('Already cancelled')) as convert:
            with self.assertRaises(ExtractionCancelled):
                pdf_pipeline.extract(source, cancel=lambda: True)
            convert.assert_not_called()
        stopped = False

        def converted(*args, **kwargs):
            nonlocal stopped
            stopped = True
            return {'source': 'pdf_sdk', 'sdk_markdown': 'Never save cancelled body'}

        with patch.object(pdf_markdown, 'convert', side_effect=converted), \
             patch('pubnexus.general_pdf.ensure_fulltext', side_effect=AssertionError('Cancel is not fallback')):
            with self.assertRaises(ExtractionCancelled):
                pdf_pipeline.extract(source, cancel=lambda: stopped)

    def test_common_extract_native_failure_stays_failed(self):
        source = self.source()
        failed = {'source': 'pdf_general', 'body_text': [],
                  'extraction_report': {'status': 'failed', 'warnings': ['Unreadable source']}}
        with patch.object(pdf_markdown, 'convert', side_effect=RuntimeError('SDK failure')), \
             patch('pubnexus.general_pdf.ensure_fulltext', return_value=(failed, failed['extraction_report'])):
            result = pdf_pipeline.extract(source)
        self.assertEqual(result['extraction_report']['status'], 'failed')
        self.assertFalse(pdf_pipeline.current(result))
        self.assertEqual(result['pdf_conversion_attempt'], pdf_markdown.engine_id())

    def test_common_extract_detects_source_mutation_before_native_fallback(self):
        source = self.source()

        def changed(*args, **kwargs):
            source.write_bytes(source.read_bytes() + b'\nSynthetic source change')
            raise RuntimeError('SDK stopped before its final source verification')

        with patch.object(pdf_markdown, 'convert', side_effect=changed), \
             patch('pubnexus.general_pdf.ensure_fulltext', side_effect=AssertionError('Do not recover changed input')):
            with self.assertRaises(pdf_markdown.PDFMarkdownError) as raised:
                pdf_pipeline.extract(source)
        self.assertEqual(raised.exception.code, 'source_changed')

    def test_common_extract_preserves_a_rotated_body_page_missing_from_sdk(self):
        import fitz
        source = self.root / 'rotated-source.pdf'
        first = 'First original page body has substantial numeric evidence 7391 and a complete sentence.'
        second = 'Rotated second original page body must survive SDK omission with numeric evidence 8426.'
        with fitz.open() as pdf:
            pdf.new_page().insert_text((50, 100), first, fontsize=9)
            page = pdf.new_page()
            page.insert_text((50, 100), second, fontsize=9)
            page.set_rotation(90)
            pdf.save(source)
        published = {'source': 'pdf_sdk', 'sdk_markdown': first,
                     'extraction_report': {'status': 'partial'}}
        with patch.object(pdf_markdown, 'convert', return_value=published), \
             patch('pubnexus.local_ocr.discover', return_value=None), \
             patch('pubnexus.general_pdf.shutil.which', return_value=None):
            result = pdf_pipeline.extract(source)
        self.assertEqual(result['source'], 'pdf_general')
        body = '\n'.join(p['text'] for s in result['body_text'] for p in s['paragraphs'])
        self.assertIn(first, body)
        self.assertIn(second, body)
        self.assertIn('누락', '\n'.join(result['extraction_report']['warnings']))

    def test_common_extract_keeps_sdk_blank_without_a_native_text_baseline(self):
        import fitz
        source = self.root / 'actually-blank.pdf'
        with fitz.open() as pdf:
            pdf.new_page()
            pdf.save(source)
        published = {'source': 'pdf_sdk', 'sdk_markdown': '',
                     'extraction_report': {'status': 'complete'}}
        with patch.object(pdf_markdown, 'convert', return_value=published), \
             patch('pubnexus.general_pdf.ensure_fulltext', side_effect=AssertionError('Blank is not lost text')):
            result = pdf_pipeline.extract(source)
        self.assertTrue(pdf_pipeline.current(result))
        self.assertFalse(result['extraction_report']['native_coverage']['baseline_available'])

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
        with patch.object(pdf_pipeline, 'extract', return_value=sdk) as converted, \
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
