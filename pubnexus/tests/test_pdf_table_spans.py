import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubnexus.pdf_table_spans import refine_record_tables


class Table:
    bbox = [10, 20, 210, 120]

    def to_html(self):
        return '<table><tr><th colspan="2">Group</th></tr><tr><td>A</td><td>12.50</td></tr></table>'


class Page:
    def find_tables(self, **options):
        assert options == {'refine': True}
        return type('Found', (), {'tables': [Table()]})()


def fixture():
    table = '|Group||\n|---|---|\n|A|12.50|\n\n'
    text = 'Before\n\n' + table + 'After\n\n'
    return {'markdown': text, 'chunks': [{'text': text, 'page_boxes': [
        {'class': 'text', 'index': 0, 'bbox': [10, 1, 210, 19], 'pos': [0, 8]},
        {'class': 'table', 'index': 1, 'bbox': [10, 20, 210, 120], 'pos': [8, 8 + len(table)]},
        {'class': 'text', 'index': 2, 'bbox': [10, 130, 210, 150], 'pos': [8 + len(table), len(text)]}]}]}


class TableSpanTests(unittest.TestCase):
    def test_published_merged_html_replaces_only_table_and_updates_offsets(self):
        record = fixture()
        raw = copy.deepcopy(record)
        refine_record_tables(record, [Page()])
        self.assertIn('colspan="2"', record['markdown'])
        self.assertEqual(record['markdown_raw'], raw['markdown'])
        chunk = record['chunks'][0]
        self.assertEqual(chunk['text'][slice(*chunk['page_boxes'][-1]['pos'])], 'After\n\n')
        self.assertTrue(record['markdown'].startswith('Before\n\n'))
        self.assertEqual(record['table_refinements'][0]['method'], 'PyMuPDF.Table.to_html')

    def test_numeric_change_rejects_candidate(self):
        record = fixture()
        record['chunks'][0]['text'] = record['chunks'][0]['text'].replace('12.50', '99.50')
        record['markdown'] = record['chunks'][0]['text']
        original = copy.deepcopy(record)
        refine_record_tables(record, [Page()])
        self.assertEqual(record['markdown'], original['markdown'])
        self.assertEqual(record.get('table_refinements'), [])

    def test_ambiguous_two_tables_leave_original(self):
        class Ambiguous(Page):
            def find_tables(self, **options):
                return type('Found', (), {'tables': [Table(), Table()]})()
        record = fixture()
        old = record['markdown']
        refine_record_tables(record, [Ambiguous()])
        self.assertEqual(record['markdown'], old)

    def test_text_tight_sdk_box_can_match_larger_native_ruled_grid(self):
        record = fixture()
        record['chunks'][0]['page_boxes'][1]['bbox'] = [10, 20, 150, 120]
        refine_record_tables(record, [Page()])
        self.assertIn('colspan="2"', record['markdown'])

    def test_existing_raw_is_not_overwritten_by_later_refinement(self):
        record = fixture()
        record['markdown_raw'] = 'First SDK bytes'
        refine_record_tables(record, [Page()])
        self.assertEqual(record['markdown_raw'], 'First SDK bytes')

    def test_cell_boundaries_are_preserved_even_when_flat_text_agrees(self):
        class ChangedTable(Table):
            def to_html(self):
                return '<table><tr><th colspan="2">Group</th></tr><tr><td></td><td>A12.50</td></tr></table>'
        class ChangedPage(Page):
            def find_tables(self, **options):
                return type('Found', (), {'tables': [ChangedTable()]})()
        record = fixture()
        original = record['markdown']
        refine_record_tables(record, [ChangedPage()])
        self.assertEqual(record['markdown'], original)

    def test_clinical_symbols_cannot_change_inside_a_cell(self):
        for before, after in [('HER2+', 'HER2-'), ('>10', '<10'), ('12.50%', '12.50'),
                              ('10<br>20', '1020')]:
            with self.subTest(before=before):
                class ChangedTable(Table):
                    def to_html(self):
                        import html
                        return ('<table><tr><th colspan="2">Group</th></tr>'
                                '<tr><td>A</td><td>' + html.escape(after) + '</td></tr></table>')
                class ChangedPage(Page):
                    def find_tables(self, **options):
                        return type('Found', (), {'tables': [ChangedTable()]})()
                record = fixture()
                record['chunks'][0]['text'] = record['markdown'] = record['markdown'].replace('12.50', before)
                # The fixture value has a different length; keep exact box positions.
                delta = len(before) - len('12.50')
                record['chunks'][0]['page_boxes'][1]['pos'][1] += delta
                record['chunks'][0]['page_boxes'][2]['pos'] = [v + delta for v in record['chunks'][0]['page_boxes'][2]['pos']]
                original = record['markdown']
                refine_record_tables(record, [ChangedPage()])
                self.assertEqual(record['markdown'], original)


if __name__ == '__main__':
    unittest.main()
