"""Fast PPTX headers, immutable bytes and logical positions without rendering."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

from pptx import Presentation
from pptx.util import Inches
from pubnexus import presentation_preview as preview


class PresentationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'slides.pptx'
        deck = Presentation()
        deck.slide_width, deck.slide_height = Inches(10), Inches(5.625)
        for i in range(3):
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = 'Actual slide ' + str(i + 1)
            if i == 1:
                slide._element.set('show', '0')
        deck.save(self.source)

    def test_header_reads_no_slide_xml_or_media_and_preserves_hidden_order(self):
        reads, real = [], zipfile.ZipFile.read
        def read(archive, name, *args, **kwargs):
            reads.append(name)
            return real(archive, name, *args, **kwargs)
        before = self.source.read_bytes()
        with patch.object(zipfile.ZipFile, 'read', read):
            info = preview.describe(self.source, 4)
        self.assertEqual(info['pages'], [[960, 540]] * 3)
        self.assertEqual(info['slide_parts'], [f'ppt/slides/slide{i}.xml' for i in range(1, 4)])
        self.assertEqual(set(reads), {'ppt/presentation.xml', 'ppt/_rels/presentation.xml.rels'})
        self.assertEqual(info['source_revision'], hashlib.sha256(before).hexdigest())
        self.assertIn('?p=4&r=', info['source_data_url'])
        self.assertEqual(self.source.read_bytes(), before)

    def test_raw_data_requires_the_current_exact_revision(self):
        info = preview.describe(self.source, 0)
        self.assertEqual(preview.read_data(self.source, info['source_revision']), self.source.read_bytes())
        self.source.write_bytes(self.source.read_bytes() + b'Changed after metadata')
        with self.assertRaisesRegex(ValueError, '변경'):
            preview.read_data(self.source, info['source_revision'])
        with self.assertRaises(ValueError):
            preview.read_data(self.source, 'anything')

    def test_budget_rejects_before_unbounded_bytes_read(self):
        with patch.object(preview, 'MAX_BYTES', 64), patch.object(zipfile, 'ZipFile', side_effect=AssertionError('Reject size first')):
            with self.assertRaisesRegex(ValueError, '크기'):
                preview.describe(self.source, 0)

    def test_unsafe_package_paths_are_rejected_by_shared_guard(self):
        with zipfile.ZipFile(self.source, 'a') as archive:
            archive.writestr('../outside.xml', '<root/>')
        with self.assertRaisesRegex(ValueError, '경로'):
            preview.describe(self.source, 0)

    def test_native_shapes_remain_logical_until_the_active_renderer_supplies_display_bounds(self):
        origin = {'slide': 2, 'shape': '3', 'shape_bbox_emu': [914400, 1828800, 2743200, 2743200]}
        doc = {'body_text': [{'paragraphs': [{'id': 'normal', 'text': 'text', 'source': origin},
                                             {'id': 'group', 'text': 'group child', 'source': dict(origin, shape='3/1')}]}]}
        mapping = preview.document_map(doc, preview.describe(self.source, 0))
        self.assertEqual(mapping['blocks'][0]['locations'], [])
        self.assertEqual(mapping['blocks'][0]['slide'], 1)
        self.assertEqual(mapping['blocks'][0]['mapping'], 'unmapped')
        self.assertEqual(mapping['blocks'][1]['locations'], [])
        self.assertEqual(mapping['blocks'][1]['mapping'], 'unmapped')
        self.assertEqual(mapping['source_kind'], 'presentation')

    def test_asset_paths_are_allowlisted_and_vendor_hash_must_match(self):
        web, vendor = self.root / 'web', self.root / 'vendor'
        web.mkdir(); vendor.mkdir()
        for name in ('presentation-frame.html', 'presentation-frame.mjs'):
            (web / name).write_text('fixture', encoding='utf-8')
        bundle = vendor / 'renderer.mjs'
        bundle.write_bytes(b'Pinned original bundle')
        (vendor / 'manifest.json').write_text(json.dumps({'version': preview.VERSION,
            'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()}), encoding='utf-8')
        with patch.object(preview, '_web_root', return_value=web), patch.object(preview, '_vendor_root', return_value=vendor):
            self.assertTrue(preview.available())
            self.assertEqual(preview.asset('vendor'), b'Pinned original bundle')
            with self.assertRaises(ValueError):
                preview.asset('../settings.json')
            bundle.write_bytes(b'Tampered bundle')
            self.assertFalse(preview.available())


if __name__ == '__main__':
    unittest.main()
