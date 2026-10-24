"""An in-flight viewer request must never borrow another row's document."""
import io
import json
import urllib.parse
import unittest
from unittest.mock import Mock, patch

import test_app_export as support


class SelectionIdentityTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp

    def inputs(self):
        original = self.root / 'original.txt'
        inserted = self.root / 'inserted.txt'
        original.write_text('Original contents 7142', encoding='utf-8')
        inserted.write_text('Wrong document 9538', encoding='utf-8')
        self.app.pdfs = [inserted, original]
        return original, inserted

    def test_source_result_and_map_reject_replaced_row_before_reading(self):
        original, _ = self.inputs()
        with patch.object(self.app, 'doc_view', side_effect=AssertionError('Wrong result read')):
            for method in (self.app.source_view, self.app.result_view, self.app.document_map):
                with self.subTest(method=method.__name__):
                    result = method(0, expected_path=str(original))
                    self.assertEqual(result.get('code'), 'stale_selection', result)

    def test_search_and_location_bridge_accept_identity_and_reject_replaced_row(self):
        original, _ = self.inputs()
        api = support.app_module.Api(self.app)
        for method, args in ((api.search_source, (0, 'Original', str(original))),
                             (api.search, (0, 'Original', None, '', str(original))),
                             (api.location, (0, 'block', None, '', str(original)))):
            with self.subTest(method=method.__name__):
                result = method(*args)
                self.assertFalse(result['ok'])
                self.assertEqual(result.get('code'), 'stale_selection', result)
        result = api.search_source(1, 'Original', str(original))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['path'], str(original))

    def test_search_keeps_captured_path_when_list_changes_mid_read(self):
        original, inserted = self.inputs()
        self.app.pdfs = [original]
        from pubnexus import preview
        real = preview.text_source

        def replace_list(path):
            self.app.pdfs = [inserted]
            return real(path)

        with patch.object(preview, 'text_source', side_effect=replace_list):
            result = support.app_module.Api(self.app).search_source(0, 'Original', str(original))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['path'], str(original))
        self.assertIn('Original', result['results'][0]['text'])

    def test_http_replaced_selection_returns_409_json(self):
        original, _ = self.inputs()
        for route in ('/open?p=0&mode=source', '/open?p=0&mode=result', '/document-map?p=0'):
            handler = object.__new__(support.app_module.Handler)
            handler.app = self.app
            handler.path = route + '&expected_path=' + urllib.parse.quote(str(original))
            handler.wfile = io.BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler.send_error = Mock()
            handler.do_GET()
            with self.subTest(route=route):
                handler.send_response.assert_called_once_with(409)
                self.assertEqual(json.loads(handler.wfile.getvalue())['code'], 'stale_selection')


if __name__ == '__main__':
    unittest.main()
