"""The full PDF guard must use bundled OCR without an external executable."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from pubnexus import general_pdf, local_ocr, store


class BuiltinOCRIntegrationTests(unittest.TestCase):
    def test_image_only_pdf_recovers_text_and_reuses_page_checkpoint(self):
        provider = local_ocr.discover()
        if provider is None:
            self.skipTest('Optional pinned local OCR data is unavailable')
        with tempfile.TemporaryDirectory(prefix='pdf-ocr-integration-') as temporary:
            root = Path(temporary)
            source = root / '원본 스캔.pdf'
            with fitz.open() as text:
                page = text.new_page(width=400, height=120)
                page.insert_text((25, 55), 'OFFLINE OCR CHECK 7391', fontsize=20)
                image = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes('png')
            with fitz.open() as scanned:
                scanned.new_page(width=400, height=120).insert_image(fitz.Rect(0, 0, 400, 120), stream=image)
                scanned.save(source)
            original = source.read_bytes()
            with patch.dict(os.environ, {'PUBNEXUS_STORE': str(root / 'store')}), \
                 patch.object(general_pdf.shutil, 'which', return_value=None):
                doc, report = general_pdf.ensure_fulltext({'meta': {'title': 'Keep title'}}, source)
                self.assertTrue(report['ocr_available'])
                self.assertTrue(report['ocr_performed'])
                paragraphs = [p for section in doc['body_text'] for p in section['paragraphs']]
                self.assertTrue(any('7391' in p['text'] for p in paragraphs))
                self.assertTrue(all(p['source']['page'] == 1 for p in paragraphs))
                with patch.object(local_ocr, 'extract_page', side_effect=AssertionError('Cached OCR')):
                    _, reused = general_pdf.ensure_fulltext({'meta': {'title': 'Keep title'}}, source)
                self.assertEqual(reused['page_cache']['native_reused'], 1)
            self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
