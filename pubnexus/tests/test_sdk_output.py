"""The published converter owns Markdown; the app only presents and saves it."""
import hashlib
import unittest
from pathlib import Path

import test_app_export as support
from pubnexus import comparison, export, render, store


class SDKOutputTests(support.AppExportTests):
    def sdk_doc(self, source):
        doc = self.extracted(source)
        md = '# SDK title\n\n- First\n- Second\n\n```python\nx = 42\n```\n\n![Picture](images/figure.png)\n\n| A | B |\n| --- | --- |\n| 71 | 82 |\n'
        start = md.index('- First')
        doc.update(source='pdf_sdk', sdk_engine='pymupdf-2.0-adapter-1', sdk_markdown=md,
                   sdk_blocks=[{'id': 'sdk-list', 'start': start, 'end': md.index('```'),
                                'kind': 'paragraph', 'text': '- First\n- Second',
                                'source': {'page': 1, 'bbox': [70, 60, 300, 100]}}],
                   references=[{'number': 1, 'title': 'Must not append legacy references'}])
        return doc

    def test_sdk_markdown_is_not_reassembled_by_paper_renderer(self):
        doc = self.sdk_doc(self.source())
        self.assertEqual(render.to_markdown(doc), doc['sdk_markdown'])
        self.assertEqual(render.to_markdown(doc, source_markers=True), doc['sdk_markdown'])

    def test_standard_reader_keeps_lists_code_and_official_source_offsets(self):
        from pubnexus.markdown_view import to_html
        doc = self.sdk_doc(self.source())
        shown = to_html(doc['sdk_markdown'], doc['sdk_blocks'])
        self.assertIn('<ul>', shown)
        self.assertIn('>First</li>', shown)
        self.assertIn('<code class="language-python">', shown)
        self.assertIn('<table>', shown)
        self.assertIn('data-block-id="sdk-list"', shown)
        mapped = comparison.document_map(comparison.attach(doc, Path(doc['source_file'])),
                                         Path(doc['source_file']))
        self.assertEqual([b['id'] for b in mapped['blocks']], ['sdk-list'])
        self.assertEqual(mapped['blocks'][0]['locations'][0]['page'], 0)

    def test_standard_reader_does_not_load_document_html_or_remote_images(self):
        from pubnexus.markdown_view import to_html
        md = '<script>alert(9)</script>\n\n![remote](https://example.com/x.png)\n\n[bad](javascript:alert(1))\n\n<table><tr><td colspan="2" onclick="bad()">Kept</td></tr></table>'
        shown = to_html(md, safe_table=support.app_module._safe_table)
        self.assertNotIn('<script', shown)
        self.assertNotIn('<img', shown)
        self.assertNotIn('onclick', shown)
        self.assertNotIn('href="javascript:', shown)
        self.assertIn('<td colspan="2">Kept</td>', shown)

    def test_export_changes_only_local_asset_destinations(self):
        source = self.source()
        doc = self.sdk_doc(source)
        asset_base = self.root / 'sdk-assets'
        (asset_base / 'images').mkdir(parents=True)
        image = asset_base / 'images' / 'figure.png'
        image.write_bytes(b'PNG fixture content')
        doc['figures'] = [{'id': 'sdk-picture', 'image': 'images/figure.png'}]
        start = doc['sdk_markdown'].index('images/figure.png')
        doc['sdk_assets'] = [{'image': 'images/figure.png', 'start': start, 'end': start + len('images/figure.png')}]
        result = export.export_document(doc, source, output_dir=self.root / 'output', asset_base=asset_base)
        expected_image = 'assets/' + result.assets_dir.name + '/' + hashlib.sha256(image.read_bytes()).hexdigest() + '.png'
        self.assertEqual(result.markdown_path.read_text(encoding='utf-8'),
                         doc['sdk_markdown'].replace('images/figure.png', expected_image))
        self.assertEqual(doc['figures'][0]['image'], 'images/figure.png')
        self.assertTrue((result.markdown_path.parent / expected_image).is_file())

    def test_export_preserves_code_examples_of_the_same_image_destination(self):
        source = self.source()
        doc = self.sdk_doc(source)
        literal = '```markdown\n![literal](images/figure.png)\n```\n\n`![literal](images/figure.png)`\n\n'
        doc['sdk_markdown'] = literal + '![actual](images/figure.png)\n'
        start = doc['sdk_markdown'].rindex('images/figure.png')
        doc['sdk_assets'] = [{'image': 'images/figure.png', 'start': start, 'end': start + len('images/figure.png')}]
        doc['figures'] = [{'id': 'sdk-picture', 'image': 'images/figure.png'}]
        assets = self.root / 'pictures'
        (assets / 'images').mkdir(parents=True)
        (assets / 'images' / 'figure.png').write_bytes(b'picture')
        result = export.export_document(doc, source, asset_base=assets)
        saved = result.markdown_path.read_text(encoding='utf-8')
        self.assertTrue(saved.startswith(literal))
        self.assertNotIn('![actual](images/figure.png)', saved)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name, SDKOutputTests)
                              for name in dir(SDKOutputTests) if name.startswith('test_')
                              and name in SDKOutputTests.__dict__)
