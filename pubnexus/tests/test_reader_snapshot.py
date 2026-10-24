"""A reader snapshot cannot mix a saved MD with a newer canonical document."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_app_export as support
from pubnexus import comparison, export, store


class ReaderSnapshotTests(unittest.TestCase):
    setUp = support.AppExportTests.setUp
    source = support.AppExportTests.source
    extracted = support.AppExportTests.extracted

    def published(self):
        source = self.source()
        doc = self.extracted(source)
        doc['body_text'][0]['paragraphs'][0]['source'] = {'page': 1, 'bbox': [72, 60, 200, 80]}
        doc = comparison.attach(doc, source)
        result = export.export_document(doc, source, output_dir=self.app.output_dir)
        store.save(doc['sha1'], doc, source)
        self.app.pdfs = [source]
        self.app.exported[support.app_module._norm(source)] = {
            'markdown': str(result.markdown_path), 'assets': str(result.assets_dir),
            'markdown_sha256': hashlib.sha256(result.markdown_path.read_bytes()).hexdigest(),
            'source_sha1': doc['sha1'], 'source_stat': (source.stat().st_size, source.stat().st_mtime_ns)}
        self.addCleanup(self.app.renderer.close_all)
        return source, doc, result

    def test_open_and_map_share_saved_revision(self):
        source, doc, result = self.published()
        opened = self.app.paper(0)
        mapping = self.app.document_map(0)
        self.assertTrue(opened['revision'])
        self.assertEqual(opened['revision'], mapping['revision'])
        self.assertTrue(opened['doc']['mapping_valid'])

    def test_new_canonical_before_export_cannot_replace_saved_html_or_map(self):
        source, doc, result = self.published()
        previous_md = result.markdown_path.read_bytes()
        replacement = copy.deepcopy(doc)
        replacement['body_text'][0]['paragraphs'][0]['text'] = 'NEW CANONICAL MUST NOT LEAK'
        replacement['body_text'][0]['paragraphs'][0]['source']['bbox'] = [200, 200, 300, 230]
        store.save(doc['sha1'], replacement, source)
        view = self.app.doc_view(source)
        self.assertIn('offline document body', view['html'])
        self.assertNotIn('NEW CANONICAL', view['html'])
        self.assertFalse(view['mapping_valid'])
        self.assertFalse(self.app.document_map(0)['blocks'])
        self.assertEqual(result.markdown_path.read_bytes(), previous_md)

    def test_same_markdown_new_coordinates_invalidates_old_revision_requests(self):
        source, doc, result = self.published()
        old_revision = self.app.paper(0)['revision']
        old_bytes = result.markdown_path.read_bytes()
        replacement = copy.deepcopy(doc)
        replacement['body_text'][0]['paragraphs'][0]['source']['bbox'] = [180, 100, 300, 130]
        store.save(doc['sha1'], replacement, source)
        export.export_document(replacement, source, output_dir=self.app.output_dir)
        current = self.app.paper(0)
        self.assertEqual(result.markdown_path.read_bytes(), old_bytes)
        self.assertNotEqual(current['revision'], old_revision)
        api = support.app_module.Api(self.app)
        for response in (api.search(0, 'offline', old_revision), api.location(0, 'p-1', old_revision)):
            self.assertFalse(response['ok'])
            self.assertEqual(response['code'], 'stale_revision')
        self.assertTrue(api.search_source(0, 'offline')['results'])

    def test_map_uses_revision_of_the_same_validated_comparison_snapshot(self):
        source, doc, result = self.published()
        replacement = copy.deepcopy(doc)
        replacement['body_text'][0]['paragraphs'][0]['source']['bbox'] = [180, 100, 300, 130]
        original_comparison = self.app.comparison_doc
        def publish_then_compare(index):
            store.save(doc['sha1'], replacement, source)
            export.export_document(replacement, source, output_dir=self.app.output_dir)
            return original_comparison(index)
        with patch.object(self.app, 'comparison_doc', side_effect=publish_then_compare):
            mapping = self.app.document_map(0)
        self.assertEqual(mapping['revision'], self.app.paper(0)['revision'])
        self.assertEqual(mapping['blocks'][0]['locations'][0]['bbox'], [180, 100, 300, 130])

    def test_view_uses_captured_markdown_when_export_changes_during_render(self):
        source, doc, result = self.published()
        old = self.app.paper(0)['doc']
        replacement = copy.deepcopy(doc)
        replacement['body_text'][0]['paragraphs'][0]['text'] = 'NEW BODY PUBLISHED DURING RENDER'
        original_snapshot = self.app._saved_snapshot
        def capture_then_publish(path, canonical=None):
            snapshot = original_snapshot(path, canonical)
            store.save(doc['sha1'], replacement, source)
            updated = export.export_document(replacement, source, output_dir=self.app.output_dir)
            self.app.exported[support.app_module._norm(source)]['markdown_sha256'] = hashlib.sha256(updated.markdown_path.read_bytes()).hexdigest()
            return snapshot
        with patch.object(self.app, '_saved_snapshot', side_effect=capture_then_publish):
            view = self.app.doc_view(source)
        self.assertEqual(view['md_text'], old['md_text'])
        self.assertEqual(view['revision'], old['revision'])
        self.assertNotIn('NEW BODY', view['html'])

    def test_identical_content_at_two_paths_has_valid_saved_snapshots(self):
        one, two = self.root / 'one.csv', self.root / 'two.csv'
        one.write_text('Name,Count\nAlpha,7391\n', encoding='utf-8')
        two.write_bytes(one.read_bytes())
        self.app.add_files([one, two])
        self.app._work([one, two])
        self.addCleanup(self.app.renderer.close_all)
        self.assertEqual(store.file_sha1(one), store.file_sha1(two))
        for path in (one, two):
            with self.subTest(path=path.name):
                view = self.app.doc_view(path)
                self.assertTrue(view['mapping_valid'])
                self.assertIn('7391', view['md_text'])

    def test_legacy_manifest_keeps_full_canonical_validation(self):
        source, doc, result = self.published()
        manifest = json.loads(result.manifest_path.read_text(encoding='utf-8'))
        manifest.pop('canonical_content_revision', None)
        result.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assertTrue(self.app.doc_view(source)['mapping_valid'])
        replacement = copy.deepcopy(doc)
        replacement['source_file'] = str(self.root / 'moved.pdf')
        store.save(doc['sha1'], replacement)
        self.assertFalse(self.app.doc_view(source)['mapping_valid'])


if __name__ == '__main__':
    unittest.main()
