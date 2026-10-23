"""Markdown 내보내기의 파일 시스템 경계. 시험 자료는 OS TEMP에만 만든다."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pubnexus.export import ExportError, export_document


class ExportDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pubnexus-export-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "원본 문서.pdf"
        self.source.write_bytes(b"synthetic source, never parsed")
        self.images = self.base / "internal-cache"
        self.images.mkdir()
        (self.images / "figure.png").write_bytes(b"synthetic image bytes")
        self.doc = {
            "paper_id": "fixture", "source": "fixture", "schema_version": "1.0",
            "meta": {"title": "내보내기 시험"},
            "body_text": [{"path": ["본문"], "paragraphs": [
                {"id": "p1", "text": "그림을 확인합니다.", "refs_figure": ["fig1"]}]}],
            "figures": [{"id": "fig1", "caption": "시험 그림", "image": "figure.png"}],
            "tables": [], "references": [],
        }

    def test_saves_markdown_and_relative_assets_without_mutating_canonical_document(self):
        original = copy.deepcopy(self.doc)
        source_bytes = self.source.read_bytes()

        result = export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(result.markdown_path.parent, self.base / "Markdown")
        self.assertEqual(result.markdown_path.name, "원본 문서.md")
        text = result.markdown_path.read_text(encoding="utf-8")
        self.assertIn("# 내보내기 시험", text)
        self.assertIn("그림을 확인합니다.", text)
        self.assertIn("![시험 그림](assets/", text)
        self.assertNotIn(str(self.images), text)
        copied = list(result.assets_dir.glob("*.png"))
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].read_bytes(), b"synthetic image bytes")
        self.assertEqual(self.doc, original)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertTrue(result.manifest_path.is_file())

    def test_table_fallback_image_is_copied_and_linked_relatively(self):
        self.doc['figures'] = []
        self.doc['tables'] = [{'id': 'table1', 'caption': 'Uncertain cells',
                               'image': 'figure.png', 'markdown': '![표 원문](figure.png)'}]
        result = export_document(self.doc, self.source, asset_base=self.images)
        text = result.markdown_path.read_text(encoding='utf-8')
        self.assertIn('![표 원문](assets/', text)
        self.assertEqual(len(list(result.assets_dir.glob('*.png'))), 1)

    def test_retry_keeps_the_same_files_and_reports_unchanged(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        paths = [first.markdown_path, first.manifest_path, *first.assets_dir.glob("*.png")]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}

        again = export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(again.markdown_path, first.markdown_path)
        self.assertTrue(again.unchanged)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}, before)

    def test_external_markdown_edit_blocks_export_and_preserves_its_assets(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        edited = b"# user edited markdown\n"
        first.markdown_path.write_bytes(edited)
        before = {p.name: p.read_bytes() for p in first.assets_dir.iterdir()}
        (self.images / "figure.png").write_bytes(b"new image after extraction")
        self.doc["body_text"][0]["paragraphs"][0]["text"] = "새로운 추출 결과"

        with self.assertRaisesRegex(RuntimeError, "편집|변경"):
            export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(first.markdown_path.read_bytes(), edited)
        self.assertEqual({p.name: p.read_bytes() for p in first.assets_dir.iterdir()}, before)

    def test_same_stem_sources_have_distinct_stable_results(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        first_bytes = first.markdown_path.read_bytes()
        other = self.source.with_suffix(".docx")
        other.write_bytes(b"second synthetic source")
        self.doc["meta"]["title"] = "두 번째 문서"

        second = export_document(self.doc, other, asset_base=self.images)
        retry = export_document(self.doc, other, asset_base=self.images)

        self.assertNotEqual(first.markdown_path, second.markdown_path)
        self.assertIn("--", second.markdown_path.stem)
        self.assertEqual(retry.markdown_path, second.markdown_path)
        self.assertNotEqual(first.assets_dir, second.assets_dir)
        self.assertEqual(first.markdown_path.read_bytes(), first_bytes)

    def test_long_utf16_source_name_exports_to_a_short_valid_name(self):
        source = self.base / ("문서📄" * 55 + ".pdf")
        raw_path = "\\\\?\\" + str(source) if os.name == "nt" else str(source)
        with open(raw_path, "wb") as stream:
            stream.write(b"synthetic long filename source")
        self.addCleanup(lambda: os.unlink(raw_path))

        result = export_document(self.doc, source, asset_base=self.images)

        self.assertLessEqual(len(result.markdown_path.name.encode("utf-16-le")) // 2, 180)
        self.assertIn("--", result.markdown_path.stem)
        self.assertTrue(result.markdown_path.is_file())

    def test_asset_copy_failure_is_failure_and_preserves_previous_result(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        old_markdown = first.markdown_path.read_bytes()
        old_files = {p: p.read_bytes() for p in first.assets_dir.iterdir()}
        (self.images / "figure.png").write_bytes(b"a new image requiring a copy")

        with patch("shutil.copyfile", side_effect=OSError("simulated disk full")):
            with self.assertRaisesRegex(ExportError, "그림"):
                export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(first.markdown_path.read_bytes(), old_markdown)
        self.assertEqual({p: p.read_bytes() for p in old_files}, old_files)

    def test_markdown_write_failure_preserves_previous_bytes_and_allows_retry(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        old_markdown = first.markdown_path.read_bytes()
        self.doc["body_text"][0]["paragraphs"][0]["text"] = "새로 추출한 본문"
        real_replace = os.replace

        def fail_markdown_replace(src, dst):
            if str(dst).endswith(".md"):
                raise OSError("simulated Markdown write failure")
            return real_replace(src, dst)

        with patch("os.replace", side_effect=fail_markdown_replace):
            with self.assertRaisesRegex(ExportError, "Markdown"):
                export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(first.markdown_path.read_bytes(), old_markdown)
        retry = export_document(self.doc, self.source, asset_base=self.images)
        self.assertIn("새로 추출한 본문", retry.markdown_path.read_text(encoding="utf-8"))

    def test_manifest_commit_failure_is_reported_and_retry_recovers_same_markdown(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        self.doc["body_text"][0]["paragraphs"][0]["text"] = "관리 정보 재시도 본문"
        real_replace = os.replace

        def fail_manifest_replace(src, dst):
            if str(dst).endswith("manifest.json"):
                raise OSError("simulated manifest commit failure")
            return real_replace(src, dst)

        with patch("os.replace", side_effect=fail_manifest_replace):
            with self.assertRaisesRegex(ExportError, "관리 정보"):
                export_document(self.doc, self.source, asset_base=self.images)

        retry = export_document(self.doc, self.source, asset_base=self.images)
        self.assertEqual(retry.markdown_path, first.markdown_path)
        self.assertIn("관리 정보 재시도 본문", retry.markdown_path.read_text(encoding="utf-8"))
        self.assertEqual(len(list(retry.markdown_path.parent.glob("*.md"))), 1)
        self.assertFalse((retry.assets_dir / "pending.json").exists())

    def test_edit_during_asset_copy_is_not_accepted_as_the_expected_markdown(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        (self.images / "figure.png").write_bytes(b"changed image to trigger copy")
        real_copy = shutil.copyfile

        def edit_while_copying(src, dst):
            first.markdown_path.write_bytes(b"user edited while copying assets\n")
            return real_copy(src, dst)

        with patch("shutil.copyfile", side_effect=edit_while_copying):
            with self.assertRaisesRegex(ExportError, "편집|변경"):
                export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(first.markdown_path.read_bytes(), b"user edited while copying assets\n")

    def test_image_cannot_escape_the_explicit_asset_base(self):
        (self.base / "outside.png").write_bytes(b"not part of this document assets")
        self.doc["figures"][0]["image"] = "../outside.png"

        with self.assertRaisesRegex(ExportError, "그림|폴더"):
            export_document(self.doc, self.source, asset_base=self.images)

        self.assertFalse(list((self.base / "Markdown").glob("*.md")))

    def test_manifest_preserves_existing_source_coordinates_and_block_ids(self):
        self.doc["source_map"] = {
            "coordinate_space": "pymupdf_unrotated_points",
            "pages": [{"page": 1, "width": 595, "height": 842}],
        }
        self.doc["body_text"][0]["paragraphs"][0]["source"] = {
            "page": 1, "block_id": "pdf-p000001-b000003", "bbox": [10, 20, 100, 60],
        }

        result = export_document(self.doc, self.source, asset_base=self.images)

        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_map"]["document"], self.doc["source_map"])
        self.assertEqual(manifest["source_map"]["paragraphs"], [{
            "id": "p1", "source": {
                "page": 1, "block_id": "pdf-p000001-b000003", "bbox": [10, 20, 100, 60]},
        }])

    def test_unowned_existing_markdown_is_kept_and_new_export_gets_a_suffix(self):
        folder = self.base / "Markdown"
        folder.mkdir()
        manual = folder / "원본 문서.md"
        manual.write_bytes(b"manual notes, not managed by the exporter")

        result = export_document(self.doc, self.source, asset_base=self.images)

        self.assertNotEqual(result.markdown_path, manual)
        self.assertIn("--", result.markdown_path.stem)
        self.assertEqual(manual.read_bytes(), b"manual notes, not managed by the exporter")

    def test_modified_exported_asset_is_not_overwritten(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        image = next(first.assets_dir.glob("*.png"))
        image.write_bytes(b"external image edit")
        previous = first.markdown_path.read_bytes()

        with self.assertRaisesRegex(ExportError, "그림"):
            export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(image.read_bytes(), b"external image edit")
        self.assertEqual(first.markdown_path.read_bytes(), previous)

    def test_missing_declared_image_is_a_failure_with_no_completed_markdown(self):
        self.doc["figures"][0]["image"] = "missing.png"

        with self.assertRaisesRegex(ExportError, "그림"):
            export_document(self.doc, self.source, asset_base=self.images)

        self.assertFalse(list((self.base / "Markdown").glob("*.md")))

    def test_corrupted_manifest_does_not_allow_overwriting_existing_markdown(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        previous = first.markdown_path.read_bytes()
        first.manifest_path.write_bytes(b"{corrupted metadata")

        with self.assertRaisesRegex(ExportError, "관리 정보"):
            export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(first.markdown_path.read_bytes(), previous)

    def test_wrongly_typed_manifest_hash_is_a_safe_conflict(self):
        first = export_document(self.doc, self.source, asset_base=self.images)
        previous = first.markdown_path.read_bytes()
        manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
        manifest["markdown_sha256"] = 42
        first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ExportError, "관리 정보"):
            export_document(self.doc, self.source, asset_base=self.images)

        self.assertEqual(first.markdown_path.read_bytes(), previous)

    def test_sha1_document_resolves_images_from_the_existing_app_store(self):
        sha1 = "1" * 40
        cache = self.base / "app-cache"
        figures = cache / "figs" / "11" / sha1
        figures.mkdir(parents=True)
        (figures / "figure.png").write_bytes(b"image in existing app store")
        self.doc["sha1"] = sha1

        with patch.dict(os.environ, {"PUBNEXUS_STORE": str(cache)}):
            result = export_document(self.doc, self.source)

        image = next(result.assets_dir.glob("*.png"))
        self.assertEqual(image.read_bytes(), b"image in existing app store")

    @unittest.skipUnless(os.name == "nt", "Windows NT 파일명 fixture")
    def test_windows_reserved_stem_is_not_used_for_the_export_name(self):
        source = Path("\\\\?\\" + str(self.base / "CON.pdf"))
        with open(source, "wb") as stream:
            stream.write(b"synthetic NT filename source")
        self.addCleanup(lambda: os.unlink(source))

        result = export_document(self.doc, source, asset_base=self.images)

        self.assertEqual(result.markdown_path.name, "_CON.md")
        self.assertTrue(result.markdown_path.is_file())

    def test_shortening_does_not_split_a_zwj_emoji_sequence(self):
        family = "👨‍👩‍👧‍👦"
        source = self.base / ("가" * 151 + family * 6 + ".pdf")
        raw = "\\\\?\\" + str(source) if os.name == "nt" else str(source)
        with open(raw, "wb") as stream:
            stream.write(b"synthetic grapheme filename source")
        self.addCleanup(lambda: os.unlink(raw))

        result = export_document(self.doc, source, asset_base=self.images)

        self.assertEqual(result.markdown_path.stem.split("--")[0], "가" * 151)

    def test_unicode_equivalent_unowned_name_is_treated_as_a_collision(self):
        source = self.base / "café.pdf"
        source.write_bytes(b"unicode filename source")
        folder = self.base / "Markdown"
        folder.mkdir()
        manual = folder / "café.md"
        manual.write_bytes(b"decomposed Unicode manual file")

        result = export_document(self.doc, source, asset_base=self.images)

        self.assertIn("--", result.markdown_path.stem)
        self.assertEqual(manual.read_bytes(), b"decomposed Unicode manual file")

    def test_total_path_longer_than_260_characters_can_be_exported(self):
        folder = self.base.joinpath(*("긴경로" * 10 for _ in range(7)))
        raw_folder = "\\\\?\\" + str(folder) if os.name == "nt" else str(folder)
        os.makedirs(raw_folder)
        source = folder / "book.pdf"
        raw_source = "\\\\?\\" + str(source) if os.name == "nt" else str(source)
        with open(raw_source, "wb") as stream:
            stream.write(b"synthetic deep path source")

        result = export_document(self.doc, source, asset_base=self.images)

        self.assertGreater(len(str(result.markdown_path)), 260)
        self.assertIn("# 내보내기 시험", result.markdown_path.read_text(encoding="utf-8"))
        self.assertTrue(result.manifest_path.is_file())


if __name__ == "__main__":
    unittest.main()
