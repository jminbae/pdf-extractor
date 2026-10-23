"""Offline app integration: export success is distinct from cached extraction."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP_FILE = Path(__file__).resolve().parents[1] / "app.py"
spec = importlib.util.spec_from_file_location("document_app_test", APP_FILE)
app_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_module)

from pubnexus import store


class AppExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"PUBNEXUS_STORE": str(self.root / "cache"),
                                         "PUBNEXUS_PDF_ENGINE": "off",
                                         "PUBNEXUS_OUTPUT": str(self.root / "Markdown")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(app_module.utils, "load_config", return_value={})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.app = app_module.App()
        self.app.grobid.state = "off"
        self.events = []
        self.app.push = lambda kind, payload: self.events.append((kind, payload))

    def source(self, name="book.pdf"):
        import fitz
        path = self.root / name
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "An offline document body for export verification.")
            pdf.save(path)
        return path

    def extracted(self, source):
        sha = store.file_sha1(source)
        return {"paper_id": sha, "sha1": sha, "source": "pdf_general",
                "source_file": str(source), "meta": {"title": "Book"},
                "body_text": [{"path": ["Page 1"], "section_type": "other",
                               "paragraphs": [{"id": "p-1", "text": "An offline document body for export verification."}]}],
                "figures": [], "tables": [], "references": [], "qc": {"notes": []}}

    def test_success_creates_portable_markdown_in_configured_output(self):
        source = self.source()
        doc = self.extracted(source)

        def extract(path, cfg, **kwargs):
            store.save(doc["sha1"], doc, path)
            return doc

        with patch("pubnexus.single.extract_one", side_effect=extract):
            self.app._work([source])

        markdown = source.parent / "Markdown" / "book.md"
        self.assertTrue(markdown.is_file(), "Successful app extraction must automatically save Markdown")
        self.assertIn("offline document body", markdown.read_text(encoding="utf-8"))

    def test_mixed_source_folders_export_to_one_output_folder(self):
        destination = self.root / 'gathered'
        self.app.output_dir = destination
        one = self.root / 'one'
        two = self.root / 'two'
        one.mkdir()
        two.mkdir()
        sources = [one / 'report.csv', two / 'report.csv']
        sources[0].write_text('Label,Value\nFirst,713\n', encoding='utf-8')
        sources[1].write_text('Label,Value\nSecond,842\n', encoding='utf-8')
        self.app.add_files(sources)
        self.app._work(sources)
        rows = self.app.file_rows()
        paths = [Path(row['markdown']) for row in rows]
        self.assertTrue(all(path.parent == destination for path in paths))
        self.assertEqual(len(set(paths)), 2)
        self.assertFalse((one / 'Markdown').exists())
        self.assertFalse((two / 'Markdown').exists())

    def test_cache_is_exported_without_running_paper_pipeline_again(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc["sha1"], doc, source)
        with patch("pubnexus.single.extract_one", side_effect=AssertionError("Do not re-extract cached input")):
            self.app._work([source])
        self.assertTrue((self.root / "Markdown" / "book.md").is_file())

    def test_export_failure_is_not_a_success_and_batch_continues(self):
        from pubnexus import export
        sources = [self.source("first.pdf"), self.source("second.pdf")]
        real_export = export.export_document

        def save_result(doc, source, **kwargs):
            if source.name == "first.pdf":
                raise OSError("Synthetic disk failure")
            return real_export(doc, source, **kwargs)

        with patch("pubnexus.single.extract_one", side_effect=lambda p, cfg, **kw: self.extracted(p)), \
                patch.object(export, "export_document", side_effect=save_result):
            self.app._work(sources)

        completed = [payload for kind, payload in self.events if kind == "run" and not payload["on"]][-1]
        self.assertEqual(completed["nfail"], 1)
        self.assertEqual(completed["done"], 1)
        self.assertTrue((self.root / "Markdown" / "second.md").is_file())
        self.assertFalse(any(kind == "mark" and payload.get("name") == "first.pdf" for kind, payload in self.events))

    def test_cached_extraction_alone_is_not_reported_as_saved_markdown(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc["sha1"], doc, source)
        self.app.pdfs = [source]
        self.app.scan_store()
        self.assertFalse(self.app.file_rows()[0]["done"])

    def test_cache_discovery_does_not_emit_saved_event(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc["sha1"], doc, source)
        self.app.sha_for(source, deep=True)
        self.assertFalse(any(kind == "mark" for kind, payload in self.events))

    def test_unreadable_source_report_is_not_promoted_to_complete(self):
        source = self.source()
        doc = self.extracted(source)
        doc["extraction_report"] = {"status": "failed", "warnings": ["Source unreadable"]}
        with patch("pubnexus.single.extract_one", return_value=doc):
            self.app._work([source])
        completed = [payload for kind, payload in self.events if kind == "run" and not payload["on"]][-1]
        self.assertEqual(completed["nfail"], 1)
        self.assertEqual(completed["done"], 0)
        self.assertFalse((self.root / "Markdown" / "book.md").exists())

    def test_changed_source_invalidates_saved_state_and_cached_identity(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc["sha1"], doc, source)
        self.app.pdfs = [source]
        self.app._work([source])
        self.assertTrue(self.app.file_rows()[0]["done"])
        source.write_bytes(source.read_bytes() + b"\nchanged revision\n")
        self.assertFalse(self.app.file_rows()[0]["done"])
        self.assertIsNone(self.app.sha_for(source, deep=True))

    def test_cached_export_does_not_wait_for_paper_engine(self):
        source = self.source()
        doc = self.extracted(source)
        store.save(doc["sha1"], doc, source)
        self.app.grobid.state = "starting"
        self.app.grobid.wait = unittest.mock.Mock()
        self.app._work([source])
        self.app.grobid.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
