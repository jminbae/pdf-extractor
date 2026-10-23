import unittest
from pubnexus import render


class GeneralRenderingTests(unittest.TestCase):
    def test_native_table_stays_at_its_document_anchor_after_a_prior_mention(self):
        doc = {'source': 'document_docx', 'meta': {'title': 'Document'},
               'body_text': [{'path': [], 'paragraphs': [
                   {'id': 'p1', 'text': 'See Table 1 below.'},
                   {'id': 'p2', 'text': 'This intervening paragraph must remain before the table.'},
                   {'id': 'p3', 'text': '', 'refs_table': ['table1']}]}],
               'tables': [{'id': 'table1', 'caption': '', 'markdown': '| Actual table |\n| --- |\n| Value |'}],
               'figures': [], 'references': []}
        result = render.to_markdown(doc)
        self.assertLess(result.index('This intervening paragraph'), result.index('| Actual table |'))
        self.assertNotIn('**table1**', result)

    def test_general_document_keeps_literal_letter_opener(self):
        text = "DEAR EDITOR This is a literal passage quoted in a book."
        doc = {"source": "pdf_general", "meta": {"title": "Book"},
               "body_text": [{"path": ["Page 1"], "paragraphs": [{"id": "p1", "text": text}]}]}
        self.assertIn(text, render.to_markdown(doc))


if __name__ == "__main__":
    unittest.main()
