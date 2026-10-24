"""Safe native content windows reuse HTML and MIME adapters, with no fetch."""
import base64
import hashlib
import io
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pubnexus import native_preview, store
from pubnexus.preview import PreviewUnavailable


class NativeHtmlMailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-html-mail-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        image = io.BytesIO()
        Image.new('RGB', (12, 8), 'white').save(image, format='PNG')
        self.png = image.getvalue()

    def html(self, suffix='.html'):
        path = self.base / ('source' + suffix)
        path.write_text('''<html><head><title>한글 제목</title></head><body>
            <h1>첫 제목</h1><p>&lt;script&gt;literal&lt;/script&gt; 한글 본문</p>
            <table><tr><td>표 숫자 3.14</td></tr></table>
            <img src="data:image/png;base64,'''+base64.b64encode(self.png).decode()+'''">
            <img src="neighbor.png"><img src="../outside.png"><img src="https://invalid.test/x.png">
            <script>active_script_not_content()</script><iframe src="https://invalid.test"></iframe>
            <p>마지막 본문</p></body></html>''', encoding='utf-8')
        (self.base / 'neighbor.png').write_bytes(self.png)
        return path

    def message(self, subject='원문 제목', body='한글 메일 본문', image=True):
        mail = EmailMessage()
        mail['From'], mail['To'], mail['Subject'] = 'sender@example.test', 'reader@example.test', subject
        mail.set_content(body)
        if image:
            mail.add_alternative(f'<html><body><p>{body}</p><img src="cid:embedded">'
                                 '<img src="neighbor.png"><img src="https://invalid.test/x.png"></body></html>', subtype='html')
            mail.get_payload()[-1].add_related(self.png, maintype='image', subtype='png', cid='<embedded>')
        mail.add_attachment(b'never execute', maintype='application', subtype='octet-stream', filename='original.bin')
        return mail.as_bytes()

    def test_html_inline_only_literal_order_pagination_search_and_original_bytes(self):
        for suffix in ('.html', '.htm'):
            with self.subTest(suffix=suffix):
                path = self.html(suffix)
                before = path.read_bytes()
                real_open = open
                def guarded(file, *args, **kwargs):
                    if Path(file).name in ('neighbor.png', 'outside.png'):
                        raise AssertionError('no adjacent image read')
                    return real_open(file, *args, **kwargs)
                with (patch('builtins.open', side_effect=guarded),
                      patch.object(store, 'root', side_effect=AssertionError('no user store')),
                      patch('requests.sessions.Session.request', side_effect=AssertionError('no network'))):
                    first = native_preview.view(path, count=1)
                    result = native_preview.view(path)
                    self.assertEqual(first['blocks'][0]['text'], '첫 제목')
                    self.assertEqual(first['next_start'], 1)
                    self.assertEqual(result['title'], '한글 제목')
                    texts = [block.get('text', '') for block in result['blocks']]
                    self.assertIn('<script>literal</script> 한글 본문', texts)
                    self.assertFalse(any('active_script' in text for text in texts))
                    image = [block for block in result['blocks'] if block['kind'] == 'image']
                    self.assertEqual(len(image), 1)
                    self.assertEqual(native_preview.read_asset(path, image[0]['asset_id'], result['revision'])[0], self.png)
                    hit = native_preview.search(path, '한글 본문')['results'][0]
                    self.assertEqual(hit['locations'][0]['kind'], 'content')
                    self.assertEqual(hit['block_id'], result['blocks'][1]['id'])
                self.assertTrue(any('그림' in warning for warning in result['warnings']))
                self.assertTrue(result['partial'])
                self.assertEqual(path.read_bytes(), before)

    def test_eml_subject_headers_body_and_cid_image_use_existing_mime_parser(self):
        path = self.base / 'source.eml'
        path.write_bytes(self.message())
        before = path.read_bytes()
        with (patch.object(store, 'root', side_effect=AssertionError('no store')),
              patch('requests.sessions.Session.request', side_effect=AssertionError('no network'))):
            result = native_preview.view(path)
            text = '\n'.join(block.get('text', '') for block in result['blocks'])
            self.assertIn('원문 제목', text)
            self.assertIn('sender@example.test', text)
            self.assertIn('한글 메일 본문', text)
            self.assertIn('첨부 파일: original.bin', text)
            self.assertEqual(result['title'], '원문 제목')
            images = [block for block in result['blocks'] if block['kind'] == 'image']
            self.assertEqual(len(images), 1)
            self.assertEqual(native_preview.read_asset(path, images[0]['asset_id'], result['revision'])[0], self.png)
            hit = native_preview.search(path, '메일 본문')['results'][0]
            self.assertEqual(hit['locations'][0]['kind'], 'content')
        self.assertTrue(any('첨부' in warning for warning in result['warnings']))
        self.assertEqual(path.read_bytes(), before)

    def test_mbox_subjects_and_late_message_are_visible_and_searchable_without_conversion(self):
        path = self.base / 'source.mbox'
        path.write_bytes(b'From first@example.test Thu Jan  1 00:00:00 2026\n' + self.message('첫 제목', '첫 본문', False)
                         + b'\nFrom second@example.test Thu Jan  1 00:00:00 2026\n' + self.message('둘째 제목', '뒤쪽 본문', False))
        revision = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch('pubnexus.preview.preview_path', side_effect=AssertionError('no conversion')):
            result = native_preview.view(path)
            self.assertEqual(result['revision'], revision)
            text = '\n'.join(block.get('text', '') for block in result['blocks'])
            self.assertIn('첫 제목', text)
            self.assertIn('둘째 제목', text)
            first = native_preview.view(path, count=1)
            self.assertTrue(first['truncated'])
            hit = native_preview.search(path, '뒤쪽 본문')['results'][0]
            self.assertTrue(any(block['id'] == hit['block_id'] for block in result['blocks']))
        self.assertTrue(any('mboxrd' in warning for warning in result['warnings']))

    def test_same_native_bounds_stale_source_cancel_and_unsupported_format_contract_remain(self):
        path = self.html()
        revision = native_preview.view(path)['revision']
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(PreviewUnavailable, '변경'):
            native_preview.view(path, revision=revision)
        with self.assertRaisesRegex(PreviewUnavailable, '취소'):
            native_preview.view(path, cancel=lambda: True)
        with patch.object(native_preview, 'MAX_WINDOW_BYTES', 10), self.assertRaisesRegex(PreviewUnavailable, '내용 창'):
            native_preview.view(path)
        for suffix in ('.ipynb', '.xls', '.msg'):
            self.assertFalse(native_preview.supports('not-expanded' + suffix))


if __name__ == '__main__':
    unittest.main()
