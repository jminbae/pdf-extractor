"""Read-only native source windows; no PDF/MD/extraction prerequisite."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

from docx import Document
from openpyxl import Workbook
from PIL import Image

from pubnexus import native_preview
from pubnexus.preview import PreviewUnavailable


class NativePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-preview-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def docx(self):
        path = self.base / 'source.docx'
        doc = Document()
        doc.add_paragraph('<script>literal 한글 😀</script>')
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = 'A1'
        table.cell(1, 1).text = 'B2'
        image = io.BytesIO()
        Image.new('RGB', (12, 8), 'white').save(image, format='PNG')
        doc.add_picture(io.BytesIO(image.getvalue()))
        doc.add_paragraph('Last paragraph')
        doc.save(path)
        return path, image.getvalue()

    def xlsx(self):
        path = self.base / 'source.xlsx'
        book = Workbook()
        sheet = book.active
        sheet.title = 'First 한글'
        for row in range(1, 301):
            sheet.cell(row, 1, row)
            sheet.cell(row, 2, '<img onerror=alert(1)>')
        sheet.cell(2, 3, '=SUM(A1:A2)')
        book.create_sheet('Second').cell(1, 1, 'Second value')
        book.create_sheet('Hidden').sheet_state = 'hidden'
        book.save(path)
        book.close()
        return path

    def test_docx_native_order_window_and_original_image_are_read_without_extraction(self):
        path, png = self.docx()
        before = path.read_bytes()
        with (patch('pubnexus.documents.extract_document', side_effect=AssertionError('no extraction')),
              patch('pubnexus.preview.preview_path', side_effect=AssertionError('no conversion'))):
            first = native_preview.view(path, count=1)
            self.assertEqual(first['source_kind'], 'content')
            self.assertEqual(first['blocks'][0]['text'], '<script>literal 한글 😀</script>')
            self.assertEqual(first['next_start'], 1)
            self.assertTrue(first['truncated'])
            table = native_preview.view(path, start=1, count=1, revision=first['source_revision'])
            self.assertEqual(table['blocks'][0]['rows'], [['A1', ''], ['', 'B2']])
            image = native_preview.view(path, start=2, count=1)['blocks'][-1]
            self.assertEqual(image['kind'], 'image')
            self.assertNotIn('path', image)
            self.assertNotIn('member', image)
            raw, mime = native_preview.read_asset(path, image['asset_id'], first['source_revision'])
            self.assertEqual(raw, png)
            self.assertEqual(mime, 'image/png')
            self.assertEqual(image['image_sha256'], hashlib.sha256(png).hexdigest())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(first['source_revision'], hashlib.sha256(before).hexdigest())
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), [path.name])

    def test_xlsx_uses_read_only_visible_window_and_preserves_formula_and_literal_text(self):
        path = self.xlsx()
        import openpyxl
        calls = []
        real = openpyxl.load_workbook
        def load(*args, **kwargs):
            calls.append(kwargs)
            return real(*args, **kwargs)
        with patch.object(openpyxl, 'load_workbook', side_effect=load):
            first = native_preview.view(path, row_count=2, col_count=3)
        self.assertEqual(first['source_kind'], 'sheet')
        self.assertEqual([s['name'] for s in first['sheets']], ['First 한글', 'Second', 'Hidden'])
        self.assertEqual(first['sheets'][-1]['state'], 'hidden')
        self.assertEqual(first['rows'][1], ['2', '<img onerror=alert(1)>', '=SUM(A1:A2)'])
        self.assertEqual(len(first['rows']), 2)
        self.assertTrue(first['has_more'])
        self.assertTrue(all(c['read_only'] and not c['data_only'] and not c['keep_links'] for c in calls))
        second = native_preview.view(path, sheet_index=1, start_row=1, row_count=1, col_count=1)
        self.assertEqual(second['rows'], [['Second value']])
        later = native_preview.view(path, start_row=201, row_count=3, col_count=1)
        self.assertEqual(later['rows'], [['201'], ['202'], ['203']])

    def test_changed_revision_cancel_and_bad_windows_stop_before_parser(self):
        path, _ = self.docx()
        current = native_preview.view(path)['source_revision']
        path.write_bytes(path.read_bytes() + b'changed')
        with patch('docx.Document', side_effect=AssertionError('reject before parse')):
            with self.assertRaisesRegex(PreviewUnavailable, '변경'):
                native_preview.view(path, revision=current)
            with self.assertRaisesRegex(PreviewUnavailable, '취소'):
                native_preview.view(path, cancel=lambda: True)
            for params in ({'count': 0}, {'count': 201}, {'start': -1}, {'row_count': 1000}, {'col_count': 1000}):
                with self.subTest(params=params), self.assertRaises(PreviewUnavailable):
                    native_preview.view(path, **params)
        self.assertIsNone(native_preview.view(self.base / 'unsupported.pdf'))

    def test_zip_path_asset_identifier_and_byte_budgets_are_guarded(self):
        path, _ = self.docx()
        revision = native_preview.view(path)['source_revision']
        with self.assertRaises(PreviewUnavailable):
            native_preview.read_asset(path, '../word/document.xml', revision)
        with patch.object(native_preview, 'MAX_SOURCE_BYTES', 64), patch('docx.Document', side_effect=AssertionError()):
            with self.assertRaisesRegex(PreviewUnavailable, '크기'):
                native_preview.view(path)
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('../outside', 'bad')
        with self.assertRaisesRegex(PreviewUnavailable, '경로'):
            native_preview.view(path)

    def test_asset_reuses_revision_bound_bytes_without_reparsing_or_rehashing_each_picture(self):
        path, png = self.docx()
        view = native_preview.view(path)
        image = next(block for block in view['blocks'] if block['kind'] == 'image')
        with (patch('docx.Document', side_effect=AssertionError('cached image must not reparse')),
              patch.object(native_preview, '_Read', side_effect=AssertionError('cached image must not rehash'))):
            for _ in range(3):
                self.assertEqual(native_preview.read_asset(path, image['asset_id'], view['source_revision'])[0], png)
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(PreviewUnavailable, '변경'):
            native_preview.read_asset(path, image['asset_id'], view['source_revision'])

    def test_docx_and_xlsx_search_return_exact_native_addresses_without_pdf(self):
        path, _ = self.docx()
        result = native_preview.search(path, '한글')
        self.assertEqual(result['results'][0]['locations'][0],
                         {'kind': 'content', 'block_id': 'docx-b000001', 'offset': 16, 'length': 2})
        sheet = self.xlsx()
        result = native_preview.search(sheet, 'Second value')
        loc = result['results'][0]['locations'][0]
        self.assertEqual({k: loc[k] for k in ('kind', 'sheet_index', 'sheet', 'cell', 'row', 'col')},
                         {'kind': 'sheet', 'sheet_index': 1, 'sheet': 'Second', 'cell': 'A1', 'row': 1, 'col': 1})

    def test_formula_only_docx_explicitly_reports_partial_content_and_original_layout_limit(self):
        from docx.oxml import parse_xml
        path = self.base / 'formula.docx'
        native = Document()
        paragraph = native.add_paragraph()
        paragraph._p.append(parse_xml('<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x=1</m:t></m:r></m:oMath>'))
        native.save(path)
        result = native_preview.view(path)
        self.assertTrue(result['partial'])
        self.assertTrue(any('수식' in warning for warning in result['warnings']))
        self.assertEqual(result['blocks'][0]['text'], '')

    def test_image_metadata_and_cache_have_independent_byte_and_count_budgets(self):
        path, png = self.docx()
        doc = Document(path)
        doc.add_picture(io.BytesIO(png))
        doc.save(path)
        with patch.object(native_preview, 'MAX_WINDOW_IMAGES', 1):
            result = native_preview.view(path)
        self.assertEqual(sum(b['kind'] == 'image' for b in result['blocks']), 1)
        self.assertTrue(any('그림' in warning and '제한' in warning for warning in result['warnings']))
        with patch.object(native_preview, 'MAX_ASSET_CACHE_BYTES', 1):
            native_preview.view(path)
        with native_preview._assets_lock:
            self.assertEqual(len(native_preview._assets), 0)

    def test_nonfinite_timeout_and_empty_search_are_bounded_before_native_parser(self):
        path, _ = self.docx()
        with patch('docx.Document', side_effect=AssertionError('no native parse')):
            self.assertEqual(native_preview.search(path, '')['results'], [])
            for timeout in (float('nan'), float('inf'), -1):
                with self.assertRaises(PreviewUnavailable):
                    native_preview.search(path, 'query', timeout=timeout)

    def test_xlsx_without_dimension_metadata_still_offers_next_real_row_window(self):
        path = self.base / 'streamed.xlsx'
        book = Workbook(write_only=True)
        sheet = book.create_sheet('Streamed')
        for row in range(5):
            sheet.append([row])
        book.save(path)
        book.close()
        first = native_preview.view(path, row_count=2, col_count=1)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['rows'], [['0'], ['1']])
        final = native_preview.view(path, start_row=5, row_count=2, col_count=1)
        self.assertFalse(final['has_more'])
        self.assertEqual(final['rows'], [['4']])


if __name__ == '__main__':
    unittest.main()
