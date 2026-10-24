"""Explorer tiles use a small original thumbnail, never a whole conversion."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import fitz
from PIL import Image

from pubnexus import thumbnails


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thumb-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_pdf_first_page_is_small_and_does_not_prepare_document_preview(self):
        source = self.base / 'source.pdf'
        with fitz.open() as pdf:
            pdf.new_page(width=600, height=800).insert_text((72, 72), 'A fast thumbnail')
            pdf.save(source)
        with patch('pubnexus.preview.preview_path', side_effect=AssertionError('no conversion')):
            data, mime = thumbnails.read(source)
        self.assertEqual(mime, 'image/png')
        with Image.open(io.BytesIO(data)) as image:
            self.assertLessEqual(max(image.size), 240)
            self.assertGreater(min(image.size), 20)

    def test_office_uses_embedded_image_only_and_unknown_is_no_thumbnail(self):
        image = io.BytesIO()
        Image.new('RGB', (800, 450), 'blue').save(image, format='PNG')
        for suffix in ('.docx', '.pptx', '.xlsx'):
            source = self.base / ('source' + suffix)
            with zipfile.ZipFile(source, 'w') as package:
                package.writestr('docProps/thumbnail.png', image.getvalue())
                package.writestr('unrelated.xml', b'<opaque/>')
            with patch('pubnexus.preview.preview_path', side_effect=AssertionError('no conversion')):
                data, mime = thumbnails.read(source)
            with Image.open(io.BytesIO(data)) as result:
                self.assertLessEqual(max(result.size), 240)
        text = self.base / 'notes.txt'
        text.write_text('Text is represented with its file icon')
        self.assertIsNone(thumbnails.read(text))

    def test_missing_or_damaged_office_thumbnail_falls_back_to_file_icon(self):
        source = self.base / 'source.pptx'
        with zipfile.ZipFile(source, 'w') as package:
            package.writestr('docProps/thumbnail.png', b'not an image')
        self.assertIsNone(thumbnails.read(source))
        source = self.base / 'empty.docx'
        with zipfile.ZipFile(source, 'w') as package:
            package.writestr('word/document.xml', b'<opaque/>')
        self.assertIsNone(thumbnails.read(source))


if __name__ == '__main__':
    unittest.main()
