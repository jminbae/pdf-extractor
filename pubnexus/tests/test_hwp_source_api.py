"""Native Hancom pages reuse rHWP, not whole-document PDF or MD conversion."""
import urllib.parse
import unittest
from unittest.mock import Mock, patch

import test_app_export as support


class HwpSourceApiTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp

    def source(self):
        path = self.root / 'original.hwpx'
        path.write_bytes(b'Opaque source for API dispatch only')
        self.app.pdfs = [path]
        header = {'source_kind': 'hwp_native', 'source_ready': True,
                  'source_revision': 'a' * 64, 'revision': 'a' * 64,
                  'pages': [[600, 800], [800, 600]], 'page_count': 2}
        return path, header

    def test_native_hwp_header_uses_exact_engine_page_sizes(self):
        from pubnexus import hwp_preview
        path, header = self.source()
        with patch.object(hwp_preview, 'describe', return_value=header), \
             patch('pubnexus.native_preview.view', side_effect=AssertionError('No semantic re-extraction')), \
             patch('pubnexus.preview.preview_path', side_effect=AssertionError('No whole PDF conversion')):
            result = self.app.source_view(0, expected_path=str(path))
        self.assertEqual(result['source_kind'], 'hwp_native', result)
        self.assertEqual(result['pages'], header['pages'])
        self.assertTrue(result['source_page_url'].startswith('/hwp-page?'))
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(result['source_page_url']).query)['expected_path'], [str(path)])

    def test_page_route_renders_only_requested_page_and_uses_actual_aspect_ratio(self):
        from pubnexus import hwp_preview
        path, header = self.source()
        handler = object.__new__(support.app_module.Handler)
        handler.app = self.app
        handler.path = '/hwp-page?' + urllib.parse.urlencode({'p': 0, 'expected_path': str(path),
            'r': header['source_revision'], 'i': 0, 'w': 800, 'viewer': 'reader', 'generation': 1})
        handler._send = Mock()
        handler._json = Mock()
        handler.send_error = Mock()
        with patch.object(hwp_preview, 'describe', return_value=header), \
             patch.object(hwp_preview, 'read_page', return_value=(b'png-bytes', 'image/png')) as render:
            handler.do_GET()
        handler._send.assert_called_once_with(b'png-bytes', 'image/png')
        self.assertEqual(render.call_args.args, (path, header['source_revision'], 0))
        self.assertEqual(render.call_args.kwargs['max_dimension'], 1067)
        support.app_module.Api(self.app).cancel_preview('reader', 2)
        with patch.object(hwp_preview, 'read_page', side_effect=AssertionError('Obsolete page must not render')):
            handler.do_GET()
        self.assertEqual(handler._json.call_args.args[0]['code'], 'cancelled')

    def test_hwp_search_uses_native_page_numbers_without_pdf_conversion(self):
        from pubnexus import hwp_preview
        path, header = self.source()
        expected = {'ok': True, 'source_revision': header['source_revision'], 'results': [
            {'side': 'source', 'page': 1, 'text': 'literal match',
             'locations': [{'kind': 'hwp_native', 'page': 1}], 'mapping': 'exact'}],
            'truncated': False}
        with patch.object(hwp_preview, 'search', return_value=expected) as search, \
             patch('pubnexus.native_preview.search', side_effect=AssertionError('No semantic search')), \
             patch('pubnexus.preview.preview_path', side_effect=AssertionError('No PDF conversion')):
            result = support.app_module.Api(self.app).search_source(0, 'match', str(path))
        self.assertEqual(result, dict(expected, path=str(path)))
        search.assert_called_once_with(path, 'match')


if __name__ == '__main__':
    unittest.main()
