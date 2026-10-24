import os
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from pubnexus import preview, store


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'PUBNEXUS_STORE': str(self.root / 'cache')})
        env.start()
        self.addCleanup(env.stop)
        self.source = self.root / 'source.docx'
        self.source.write_bytes(b'synthetic document input')
        self.engine = self.root / 'soffice.exe'
        self.engine.write_bytes(b'synthetic engine')

    def test_plain_source_decodes_existing_encodings_without_extracting_or_rendering(self):
        method = getattr(preview, 'text_source', None)
        self.assertTrue(callable(method), 'Text originals need a direct read-only preview entry')
        text = '한글 원문\r\n<script>literal source</script>\t값\n😀 끝'
        for encoding, expected in (('utf-8-sig', 'utf-8-sig'), ('utf-16', 'utf-16'),
                                   ('cp949', 'cp949')):
            with self.subTest(encoding=encoding):
                value = text if encoding != 'cp949' else text.replace('😀 ', '')
                path = self.root / ('plain-' + encoding + '.txt')
                payload = value.encode(encoding)
                path.write_bytes(payload)
                before = path.stat()
                with patch.object(preview, 'find_engine', side_effect=AssertionError('No renderer for text')), \
                        patch('pubnexus.documents.extract_document', side_effect=AssertionError('No extraction for source preview')):
                    result = method(path)
                self.assertEqual(result['text'], value)
                self.assertEqual(result['encoding'], expected)
                self.assertEqual(result['source_kind'], 'text')
                self.assertEqual(result['pages'], [])
                self.assertEqual(result['source_label'], '원본')
                self.assertEqual(result['source_revision'], hashlib.sha256(payload).hexdigest())
                self.assertEqual(result['revision'], result['source_revision'])
                self.assertEqual(result['line_starts'], [0, value.index('<script>'), value.index('끝') - (2 if encoding != 'cp949' else 0)])
                self.assertEqual(path.read_bytes(), payload)
                self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
                self.assertFalse((store.root() / 'docs').exists())

    def test_plain_source_csv_tsv_markdown_are_raw_text_and_other_formats_use_existing_preview(self):
        method = getattr(preview, 'text_source', None)
        self.assertTrue(callable(method), 'Text source preview must be available before conversion')
        for suffix in ('.csv', '.tsv', '.md'):
            path = self.root / ('source' + suffix)
            path.write_bytes(b'# source\r\na,b\r\n1,2\r\n')
            result = method(path)
            self.assertEqual(result['text'], '# source\r\na,b\r\n1,2\r\n')
        self.assertIsNone(method(self.root / 'not-read.pdf'))

    def test_plain_source_binary_or_oversized_input_fails_without_replacement_text(self):
        from pubnexus import documents
        method = getattr(preview, 'text_source', None)
        self.assertTrue(callable(method), 'Text source preview must reject invalid inputs explicitly')
        path = self.root / 'binary.txt'
        path.write_bytes(b'secret\0binary')
        with self.assertRaises(preview.PreviewUnavailable):
            method(path)
        path.write_bytes(b'A' * 17)
        with patch.object(documents, 'MAX_TEXT_BYTES', 16):
            with self.assertRaises(preview.PreviewUnavailable):
                method(path)
        self.assertEqual(path.read_bytes(), b'A' * 17)

    def test_missing_engine_reports_dependency_without_touching_original(self):
        with patch.object(preview, 'find_engine', return_value=None):
            with self.assertRaisesRegex(preview.PreviewUnavailable, 'LibreOffice'):
                preview.preview_path(self.source)
        self.assertEqual(self.source.read_bytes(), b'synthetic document input')

    def test_failed_conversion_is_not_reused_as_a_valid_preview(self):
        with patch.object(preview, 'find_engine', return_value=self.engine), \
             patch.object(preview, '_run', side_effect=preview.PreviewUnavailable('변환 실패')) as run:
            for _ in range(2):
                with self.assertRaises(preview.PreviewUnavailable):
                    preview.preview_path(self.source)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(list((store.root() / 'previews').rglob('preview.pdf')), [])

    def test_completed_preview_cache_does_not_reconvert(self):
        def convert(args, **kwargs):
            from urllib.parse import urlparse, unquote
            profile_arg = next(str(x).split('=', 1)[1] for x in args if str(x).startswith('-env:UserInstallation='))
            profile_path = Path(unquote(urlparse(profile_arg).path).lstrip('/') if os.name == 'nt' else unquote(urlparse(profile_arg).path))
            self.assertFalse(profile_path.is_relative_to(kwargs['work']), 'Windows LO profile must use a short separate TEMP path')
            output = Path(args[args.index('--outdir') + 1]) / (Path(args[-1]).stem + '.pdf')
            with fitz.open() as pdf:
                pdf.new_page().insert_text((72, 72), 'Source preview')
                pdf.save(output)
        with patch.object(preview, 'find_engine', return_value=self.engine), \
             patch.object(preview, '_run', side_effect=convert) as run:
            one = preview.preview_path(self.source)
            two = preview.preview_path(self.source)
        self.assertEqual(one.path, two.path)
        self.assertTrue(one.converted)
        self.assertEqual(run.call_count, 1)

    def test_cancelled_conversion_publishes_no_preview(self):
        with patch.object(preview, 'find_engine', return_value=self.engine):
            with self.assertRaises(preview.PreviewUnavailable):
                preview.preview_path(self.source, cancel=lambda: True)
        self.assertEqual(list((store.root() / 'previews').rglob('preview.pdf')), [])

    def test_private_profile_disables_scripts_and_automatic_external_link_updates(self):
        from lxml import etree
        profile = self.root / 'profile'
        preview._profile(profile)
        root = etree.parse(str(profile / 'user' / 'registrymodifications.xcu'))
        ns = {'oor': 'http://openoffice.org/2001/registry'}
        values = {item.get('{'+ns['oor']+'}path') + '/' + prop.get('{'+ns['oor']+'}name'): prop.findtext('value')
                  for item in root.getroot() for prop in item}
        self.assertEqual(values['/org.openoffice.Office.Common/Security/Scripting/DisableMacrosExecution'], 'true')
        self.assertEqual(values['/org.openoffice.Office.Calc/Content/Update/Link'], '1')  # Calc schema: never
        self.assertEqual(values['/org.openoffice.Office.Writer/Content/Update/Link'], '2')  # Writer schema: never

    def test_presentation_export_includes_hidden_slides_in_original_order(self):
        source = self.root / 'slides.pptx'
        source.write_bytes(b'synthetic presentation input')
        before = source.read_bytes()

        def convert(args, **kwargs):
            filter_value = args[args.index('--convert-to') + 1]
            self.assertTrue(filter_value.startswith('pdf:impress_pdf_Export:'), filter_value)
            options = json.loads(filter_value.split(':', 2)[2])
            self.assertEqual(options['ExportHiddenSlides'], {'type': 'boolean', 'value': 'true'})
            output = Path(args[args.index('--outdir') + 1]) / 'input.pdf'
            with fitz.open() as pdf:
                for number in (1, 2, 3):
                    pdf.new_page().insert_text((72, 72), f'Slide {number}')
                pdf.save(output)

        with patch.object(preview, 'find_engine', return_value=self.engine), \
             patch.object(preview, '_run', side_effect=convert):
            result = preview.preview_path(source)
        with fitz.open(result.path) as pdf:
            self.assertEqual([page.get_text().strip() for page in pdf], ['Slide 1', 'Slide 2', 'Slide 3'])
        self.assertEqual(source.read_bytes(), before)

    def test_hidden_slide_policy_invalidates_only_presentation_preview_cache(self):
        with patch.object(preview, 'find_engine', return_value=self.engine):
            self.assertTrue(preview.renderer_identity(self.root / 'slides.pptx').endswith(':preview-v4'))
            self.assertTrue(preview.renderer_identity(self.root / 'slides.odp').endswith(':preview-v4'))
            self.assertTrue(preview.renderer_identity(self.root / 'slides.ppt').endswith(':preview-v4'))
            self.assertTrue(preview.renderer_identity(self.source).endswith(':preview-v3'))

    def test_cancellation_does_not_wait_for_another_preview_to_finish(self):
        cancelled = threading.Event()
        entered = threading.Event()
        errors = []
        with patch.object(preview, 'find_engine', return_value=self.engine):
            sha = store.file_sha1(self.source)
            identity = preview.renderer_identity(self.source)
            key = sha + '-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
            held = threading.Lock()
            held.acquire()
            with preview._locks_guard:
                previous = preview._locks.get(key)
                preview._locks[key] = held

            def wait_for_preview():
                entered.set()
                try:
                    preview.preview_path(self.source, cancel=cancelled.is_set)
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=wait_for_preview, daemon=True)
            try:
                worker.start()
                self.assertTrue(entered.wait(1))
                cancelled.set()
                worker.join(1)
                self.assertFalse(worker.is_alive(), 'Cancellation must not wait for the active renderer lock')
            finally:
                held.release()
                worker.join(2)
                with preview._locks_guard:
                    if previous is None:
                        preview._locks.pop(key, None)
                    else:
                        preview._locks[key] = previous
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], preview.PreviewUnavailable)
        self.assertIn('중지', str(errors[0]))
