import unittest
import test_app_export as support


class ResultViewTests(unittest.TestCase):
    def test_internal_block_id_is_escaped_and_code_view_never_activates_markers(self):
        key = '\" onclick=\"evil()'.encode().hex()
        marked = f'<!--pnx-block:{key}-->\n\n## Heading\n\nWords\n\n<!--pnx-end-->'
        shown = support.app_module.md_to_html(marked, source_markers=True)
        self.assertIn('data-block-id="&quot; onclick=&quot;evil()"', shown)
        plain = support.app_module.md_to_html(marked)
        self.assertNotIn('data-block-id=', plain)

    def test_merged_table_renders_spans_without_executable_html(self):
        md = '# Doc\n\n## Table\n\n<table><tr><td rowspan="2" onclick="evil()">Merged</td><td>A</td></tr><tr><td><script>alert(1)</script>B</td></tr></table>'
        shown = support.app_module.md_to_html(md)
        self.assertIn('<td rowspan="2">Merged</td>', shown)
        self.assertNotIn('<script', shown)
        self.assertNotIn('onclick', shown)


class SavedResultTests(support.AppExportTests):
    def test_saved_state_survives_app_restart_and_external_md_edit_disables_links(self):
        from unittest.mock import patch
        source = self.source()
        self.app.pdfs = [source]
        with patch('pubnexus.single.extract_one', return_value=self.extracted(source)):
            self.app._work([source])
        other = support.app_module.App()
        other.pdfs = [source]
        other.scan_store()
        self.addCleanup(other.renderer.close_all)
        self.assertTrue(other.file_rows()[0]['done'])
        from pathlib import Path
        md = Path(other.file_rows()[0]['markdown'])
        md.write_text('# Externally edited\n\nUser text', encoding='utf-8')
        self.assertIn('User text', other.doc_view(source)['md_text'])
        self.assertEqual(other.document_map(0)['blocks'], [])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([loader.loadTestsFromTestCase(ResultViewTests),
                              SavedResultTests('test_saved_state_survives_app_restart_and_external_md_edit_disables_links')])
