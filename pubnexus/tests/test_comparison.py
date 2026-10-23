"""Location links must keep repeated text distinct and reject ambiguous matches."""
import tempfile
import unittest
from pathlib import Path

import fitz

from pubnexus import comparison, render


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.pdf'
        with fitz.open() as pdf:
            for text in ('Repeated paragraph.', 'Unique second chapter.', 'Repeated paragraph.'):
                pdf.new_page().insert_text((72, 72), text)
            pdf.save(self.source)

    def doc(self, texts):
        return {'source': 'grobid', 'meta': {'title': 'Example'}, 'body_text': [
            {'path': ['Body'], 'paragraphs': [{'id': f'p{i}', 'text': t} for i, t in enumerate(texts)]}],
            'tables': [], 'figures': [], 'references': []}

    def test_unique_normalized_match_maps_without_first_hit_guess(self):
        doc = comparison.attach(self.doc(['Unique second chapter.']), self.source)
        block = comparison.document_map(doc, self.source)['blocks'][0]
        self.assertEqual(block['locations'][0]['page'], 1)
        self.assertEqual(block['mapping'], 'normalized')

    def test_repeated_unlocated_text_stays_unmapped(self):
        doc = comparison.attach(self.doc(['Repeated paragraph.']), self.source)
        block = comparison.document_map(doc, self.source)['blocks'][0]
        self.assertEqual(block['locations'], [])
        self.assertEqual(block['mapping'], 'unmapped')

    def test_partial_mapping_does_not_overwrite_explicit_multiple_locations(self):
        doc = self.doc(['Repeated paragraph.Unique second chapter.', 'No such text in this source.'])
        origin = {'locations': [{'page': 1, 'bbox': [74, 61, 180, 77]},
                                {'page': 2, 'bbox': [74, 61, 190, 77]}], 'mapping': 'exact'}
        doc['body_text'][0]['paragraphs'][0]['source'] = origin
        mapped = comparison.attach(doc, self.source)
        self.assertEqual(mapped['body_text'][0]['paragraphs'][0]['source'], origin)

    def test_merged_html_table_matches_visible_cell_text_in_preview(self):
        doc = self.doc([])
        doc['tables'] = [{'id': 'merged', 'caption': '', 'markdown':
                          '<table><tr><td colspan="2">Unique second</td></tr><tr><td>chapter.</td></tr></table>',
                          'cells': [{'row': 1, 'col': 0, 'text': 'chapter.'},
                                    {'row': 0, 'col': 0, 'col_span': 2, 'text': 'Unique second'}]}]
        data = comparison.document_map(comparison.attach(doc, self.source), self.source)
        block = data['blocks'][0]
        self.assertEqual(block['locations'][0]['page'], 1)
        self.assertEqual(block['mapping'], 'normalized')
        self.assertNotIn('<td', block['text'])
        hits = comparison.search(comparison.attach(doc, self.source), self.source, 'chapter.')
        self.assertTrue(any(h['side'] == 'source' and h['block_id'] == 'merged' for h in hits['results']))

    def test_existing_native_address_distinguishes_same_words(self):
        doc = self.doc(['Repeated paragraph.'])
        doc['body_text'][0]['paragraphs'][0]['source'] = {'page': 3, 'bbox': [72, 60, 190, 78]}
        doc = comparison.attach(doc, self.source)
        result = comparison.search(doc, self.source, 'Repeated')
        hits = [x for x in result['results'] if x['side'] == 'source']
        self.assertEqual([x['page'] for x in hits], [0, 2])
        self.assertIsNone(hits[0]['block_id'])
        self.assertEqual(hits[1]['block_id'], 'p0')

    def test_rotation_matches_display_coordinates(self):
        with fitz.open(self.source) as pdf:
            pdf[0].set_rotation(90)
            other = Path(self.temp.name) / 'rotated.pdf'
            pdf.save(other)
        doc = self.doc(['Repeated paragraph.'])
        doc['body_text'][0]['paragraphs'][0]['source'] = {'page': 1, 'bbox': [72, 60, 190, 78]}
        data = comparison.document_map(comparison.attach(doc, other), other)
        location = data['blocks'][0]['locations'][0]
        self.assertEqual(location['width'], 842.0)
        self.assertEqual(location['bbox'], [764.0, 72.0, 782.0, 190.0])

    def test_markers_are_internal_and_preserve_explicit_ids(self):
        doc = self.doc(['First paragraph.', 'Second paragraph.'])
        clean = render.to_markdown(doc)
        marked = render.to_markdown(doc, source_markers=True)
        self.assertNotIn('pnx-block', clean)
        self.assertIn('<!--pnx-block:7030-->', marked)
        self.assertIn('<!--pnx-end-->', marked)

    def test_invalid_nonfinite_and_outside_boxes_are_not_mapped(self):
        for box in ([0, 0, float('nan'), 50], [0, 0, float('inf'), 50],
                    [False, 0, 50, 50], [-500, -500, -400, -400]):
            with self.subTest(box=box):
                doc = self.doc(['Text'])
                doc['body_text'][0]['paragraphs'][0]['source'] = {'page': 1, 'bbox': box}
                self.assertEqual(comparison.document_map(doc, self.source)['blocks'][0]['locations'], [])

    def test_all_view_ids_are_unique_even_for_conflicting_generated_ids(self):
        import hashlib
        doc = self.doc(['A', 'B', 'C'])
        items = doc['body_text'][0]['paragraphs']
        items[0]['id'] = 'same'
        items[1]['id'] = 'paragraph-0-2-' + hashlib.sha256(b'same').hexdigest()[:8]
        items[2]['id'] = 'same'
        mapped = comparison.attach(doc, self.source)
        ids = [p['_view_id'] for p in mapped['body_text'][0]['paragraphs']]
        self.assertEqual(len(set(ids)), 3)


if __name__ == '__main__':
    unittest.main()
