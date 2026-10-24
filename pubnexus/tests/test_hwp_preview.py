"""Original HWP pages use the shipped renderer, never semantic extraction."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pubnexus import hwp_preview
from pubnexus.preview import PreviewUnavailable


class HwpPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hwp-preview-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / '원본.hwp'
        self.source.write_bytes(b'private original HWP fixture')
        self.engine = self.base / 'rhwp.exe'
        self.engine.write_bytes(b'pinned renderer')
        self.calls = []
        self.addCleanup(hwp_preview._clear_cache)
        self.engine_patch = patch.object(hwp_preview, 'find_engine', return_value=self.engine)
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)

    def renderer(self, read, args, work):
        self.calls.append(list(args))
        command = args[1]
        if command == 'info':
            return json.dumps({'schemaVersion': '1.0', 'pageCount': 3, 'title': '<literal>'}).encode()
        if command == 'export-text':
            return json.dumps({'pageCount': 3, 'pages': [
                {'page': 0, 'text': '원문 😀 한글'}, {'page': 1, 'text': 'Next page'},
                {'page': 2, 'text': '마지막 한글 원문'}], 'truncated': False, 'omittedCount': 0}).encode()
        folder = Path(args[args.index('-o') + 1])
        folder.mkdir(parents=True, exist_ok=True)
        if command == 'export-render-tree':
            for page in range(3):
                (folder / f'render_tree_{page + 1:03}.json').write_text(json.dumps({
                    'type': 'Page', 'bbox': {'x': 0, 'y': 0, 'w': 800 + page, 'h': 1100 - page},
                    'children': [{'type': 'TextRun', 'text': '원문'}]}), encoding='utf-8')
        elif command == 'export-png':
            page = int(args[args.index('-p') + 1])
            Image.new('RGB', (800 + page, 1100 - page), 'white').save(folder / 'source.png')
        else:
            raise AssertionError(command)
        return b''

    def test_describe_exact_count_and_all_sizes_without_page_raster_or_extraction(self):
        before = self.source.read_bytes()
        with (patch.object(hwp_preview, '_run', side_effect=self.renderer),
              patch('pubnexus.documents.extract_document', side_effect=AssertionError('no extraction')),
              patch('pubnexus.preview.preview_path', side_effect=AssertionError('no PDF'))):
            result = hwp_preview.describe(self.source)
        self.assertEqual(result['source_kind'], 'hwp_native')
        self.assertEqual(result['page_count'], 3)
        self.assertEqual(result['pages'], [[800, 1100], [801, 1099], [802, 1098]])
        self.assertEqual(result['source_revision'], hashlib.sha256(before).hexdigest())
        self.assertEqual(result['coordinate_space'], 'page_px')
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual([a[1] for a in self.calls], ['info', 'export-render-tree'])

    def test_page_only_requested_png_and_warm_cache_no_engine_or_source_reparse(self):
        with patch.object(hwp_preview, '_run', side_effect=self.renderer):
            result = hwp_preview.describe(self.source)
            png, mime = hwp_preview.read_page(self.source, result['revision'], 2)
            count = len(self.calls)
            again = hwp_preview.read_page(self.source, result['revision'], 2)
        self.assertEqual(mime, 'image/png')
        self.assertEqual(again, (png, mime))
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.calls[-1][self.calls[-1].index('-p') + 1], '2')
        self.assertIn('--scale', self.calls[-1])
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (802, 1098))

    def test_revision_page_and_cancel_are_checked_before_using_cached_pixels(self):
        with patch.object(hwp_preview, '_run', side_effect=self.renderer):
            result = hwp_preview.describe(self.source)
            hwp_preview.read_page(self.source, result['revision'], 0)
            for page in (-1, 3, True):
                with self.assertRaises(PreviewUnavailable):
                    hwp_preview.read_page(self.source, result['revision'], page)
            with self.assertRaises(PreviewUnavailable):
                hwp_preview.read_page(self.source, '0' * 64, 0)
            with self.assertRaises(PreviewUnavailable):
                hwp_preview.read_page(self.source, result['revision'], 0, cancel=lambda: True)
            self.source.write_bytes(b'changed source')
            with self.assertRaises(PreviewUnavailable):
                hwp_preview.read_page(self.source, result['revision'], 0)

    def test_missing_or_invalid_geometry_is_rejected_not_assumed_from_first_page(self):
        def bad(read, args, work):
            value = self.renderer(read, args, work)
            if args[1] == 'export-render-tree':
                folder = Path(args[args.index('-o') + 1])
                (folder / 'render_tree_003.json').unlink()
            return value
        with patch.object(hwp_preview, '_run', side_effect=bad), self.assertRaises(PreviewUnavailable):
            hwp_preview.describe(self.source)

    def test_malformed_geometry_and_engine_page_count_bounds(self):
        for width in (0, float('nan'), 100_000):
            def bad(read, args, work):
                value = self.renderer(read, args, work)
                if args[1] == 'export-render-tree':
                    folder = Path(args[args.index('-o') + 1])
                    (folder / 'render_tree_001.json').write_text(json.dumps(
                        {'type': 'Page', 'bbox': {'x': 0, 'y': 0, 'w': width, 'h': 1100}}))
                return value
            with patch.object(hwp_preview, '_run', side_effect=bad), self.assertRaises(PreviewUnavailable):
                hwp_preview.describe(self.source)
        with patch.object(hwp_preview, '_run', return_value=b'{"pageCount":999999}'), self.assertRaises(PreviewUnavailable):
            hwp_preview.describe(self.source)

    def test_source_changed_during_render_and_invalid_png_are_never_published(self):
        with patch.object(hwp_preview, '_run', side_effect=self.renderer):
            revision = hwp_preview.describe(self.source)['revision']
        def mutate(read, args, work):
            result = self.renderer(read, args, work)
            self.source.write_bytes(b'different original')
            return result
        with patch.object(hwp_preview, '_run', side_effect=mutate), self.assertRaises(PreviewUnavailable):
            hwp_preview.read_page(self.source, revision, 0)

    def test_other_formats_return_none_and_missing_engine_is_typed(self):
        self.assertIsNone(hwp_preview.describe(self.base / 'other.docx'))
        with patch.object(hwp_preview, 'find_engine', return_value=None), self.assertRaises(PreviewUnavailable):
            hwp_preview.describe(self.source)

    def test_search_uses_engine_exact_page_and_codepoint_offsets_with_bounded_cache(self):
        with patch.object(hwp_preview, '_run', side_effect=self.renderer):
            result = hwp_preview.search(self.source, '한글')
            count = len(self.calls)
            again = hwp_preview.search(self.source, '원문', revision=result['source_revision'], limit=1)
        self.assertEqual([hit['page'] for hit in result['results']], [0, 2])
        self.assertEqual(result['results'][0]['locations'],
                         [{'kind': 'hwp_native', 'page': 0, 'offset': 5, 'length': 2}])
        self.assertEqual(len(self.calls), count)
        self.assertTrue(again['truncated'])
        self.assertEqual(self.calls[-1][1], 'export-text')

    def test_search_rejects_unverified_page_numbers_and_preserves_engine_truncation(self):
        def invalid(read, args, work):
            if args[1] == 'export-text':
                return b'{"pageCount":3,"pages":[{"page":7,"text":"original"}],"truncated":false,"omittedCount":0}'
            return self.renderer(read, args, work)
        with patch.object(hwp_preview, '_run', side_effect=invalid), self.assertRaises(PreviewUnavailable):
            hwp_preview.search(self.source, 'original')
        def partial(read, args, work):
            if args[1] == 'export-text':
                return b'{"pageCount":3,"pages":[{"page":0,"text":"original"}],"truncated":true,"omittedCount":2}'
            return self.renderer(read, args, work)
        with patch.object(hwp_preview, '_run', side_effect=partial):
            result = hwp_preview.search(self.source, 'original')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['omitted_count'], 2)


if __name__ == '__main__':
    unittest.main()
