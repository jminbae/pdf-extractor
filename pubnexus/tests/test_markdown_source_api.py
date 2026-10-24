"""Existing MD files are readable documents without a second extraction."""
import io
import re
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import test_app_export as support


class MarkdownSourceApiTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp

    def source(self, text):
        path = self.root / 'notes.md'
        path.write_bytes(text.encode('utf-8'))
        self.app.pdfs = [path]
        return path

    def test_markdown_is_rendered_read_only_with_original_line_locations(self):
        text = '# Heading\n\nBody **text**.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n<script>literal</script>'
        path = self.source(text)
        with patch('pubnexus.documents.extract_document', side_effect=AssertionError('No MD extraction')):
            result = self.app.source_view(0)
        self.assertEqual(result.get('format'), 'md')
        self.assertIn('<h1', result['markdown_html'])
        self.assertIn('<table', result['markdown_html'])
        self.assertIn('data-source-line="3"', result['markdown_html'])
        self.assertNotIn('<script>', result['markdown_html'])
        self.assertEqual(result['text'], text)
        self.assertEqual(path.read_text(encoding='utf-8'), text)
        self.assertFalse(self.app.exported)

    def test_only_confined_local_images_are_resolved_and_bound_to_revision(self):
        image = self.root / 'assets' / 'image.png'
        image.parent.mkdir()
        blob = io.BytesIO()
        Image.new('RGB', (8, 5), 'red').save(blob, format='PNG')
        image.write_bytes(blob.getvalue())
        path = self.source('![local](assets/image.png)\n\n![remote](https://example.com/private.png)\n\n![outside](../outside.png)')
        result = self.app.source_view(0)
        match = re.search(r'src="([^\"]+)"', result['markdown_html'])
        self.assertIsNotNone(match)
        import html
        route = html.unescape(match[1])
        self.assertTrue(route.startswith('/source-asset?'))
        self.assertNotIn('src="https:', result['markdown_html'])
        self.assertEqual(result['markdown_html'].count('<img '), 1)
        handler = object.__new__(support.app_module.Handler)
        handler.app = self.app
        handler.path = route
        handler._send = Mock()
        handler._json = Mock()
        handler.send_error = Mock()
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[:2], (blob.getvalue(), 'image/png'))
        path.write_text('Changed Markdown', encoding='utf-8')
        handler._send.reset_mock()
        handler.do_GET()
        handler._send.assert_not_called()
        self.assertEqual(handler._json.call_args.args[0]['code'], 'stale_source')

    def test_markdown_image_dimensions_are_bounded_before_browser_decode(self):
        import struct
        import zlib
        image = self.root / 'image.png'
        payload = io.BytesIO()
        Image.new('RGB', (8, 5), 'red').save(payload, format='PNG')
        data = bytearray(payload.getvalue())
        data[16:24] = struct.pack('>II', 9000, 8000)
        data[29:33] = struct.pack('>I', zlib.crc32(data[12:29]) & 0xffffffff)
        image.write_bytes(data)
        path = self.source('![large](image.png)')
        self.assertIsNone(support.app_module.source_image_path(path, 'image.png'))

    def test_every_allowed_raster_format_has_header_dimensions(self):
        path = self.source('Document with local images')
        for extension, image_format in (('png', 'PNG'), ('jpg', 'JPEG'), ('gif', 'GIF'), ('webp', 'WEBP'), ('bmp', 'BMP')):
            with self.subTest(format=image_format):
                image = self.root / ('small.' + extension)
                Image.new('RGB', (8, 5), 'red').save(image, format=image_format)
                self.assertEqual(support.app_module.source_image_path(path, image.name), image)
                self.assertEqual(support.app_module._img_size(image), (8, 5))


if __name__ == '__main__':
    unittest.main()
