"""Offline regressions: generated PDFs live only in TemporaryDirectory."""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pubnexus import single
from pubnexus.schema import Document, Meta, Paragraph, Reference, Section


def body_text(doc):
    return "\n".join(p["text"] for section in doc["body_text"]
                     for p in section["paragraphs"])


class GeneralPdfRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pubnexus-general-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.net = self.enterContext(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("tests must never use the network")))
        self.enterContext(patch('pubnexus.local_ocr.discover', return_value=None))
        self.enterContext(patch.dict(os.environ, {
            "PUBNEXUS_STORE": str(self.root / "store")}))
        self.enterContext(patch.object(single, "_scratch_dir", return_value=self.root / "scratch"))

    def tearDown(self):
        self.net.assert_not_called()

    def pdf(self, pages, korean=False):
        path = self.root / "synthetic.pdf"
        with fitz.open() as pdf:
            for text in pages:
                page = pdf.new_page()
                if text:
                    page.insert_text((45, 60), text, fontsize=10,
                                     fontname="korea" if korean else "helv")
            pdf.save(path)
        return path

    def candidate(self, text, source="grobid"):
        return Document(
            paper_id="synthetic", source=source, meta=Meta(title="Synthetic"),
            body_text=[Section(path=["Body"], paragraphs=[Paragraph("p1", text)])]
            if text else [],
            references=[Reference(key="b1", number=1, title="Synthetic reference")])

    def extract(self, path, candidate=None):
        cfg = single._prepare_cfg({
            "identify": {"resolve_missing_doi": False, "verify_truncated_doi": False},
            "textfix": {"enabled": False}})
        ctx = SimpleNamespace(http=SimpleNamespace(offline=True))
        if candidate is not None:
            self.enterContext(patch.object(single, "_fulltext", return_value=candidate))
        # Native paper postprocessors are kept; the external engine is the only candidate stub.
        return single._extract(path, cfg, ctx, out_json=None, on_progress=None,
                               use_grobid=False)

    def test_korean_body_is_retained_after_paper_scope_filter(self):
        text = "한글 책의 본문을 버리지 않고 보존합니다.\n반복 문단도 원문 그대로 남겨야 합니다."
        path = self.pdf([text, text], korean=True)
        result = self.extract(path, self.candidate(text))
        self.assertIn("한글 책의 본문", body_text(result))
        self.assertEqual(result["source"], "pdf_general")
        self.assertNotEqual(result.get("scope"), "non_english")
        self.assertEqual(body_text(result).count("한글 책의 본문"), 2)

    def test_reference_only_candidate_recovers_all_native_pages(self):
        pages = ["Chapter one contains the original book body and its observations.",
                 "Chapter two contains additional observations and unabridged details.",
                 "References\n1. Example author. A synthetic reference for this book."]
        result = self.extract(self.pdf(pages), self.candidate(""))
        text = body_text(result)
        for original in pages:
            self.assertIn(original.splitlines()[0], text)
        self.assertEqual(result["references"][0]["title"], "Synthetic reference")
        self.assertEqual(result["extraction_report"]["status"], "complete")

    def test_one_paragraph_is_not_a_complete_book(self):
        pages = ["Chapter %d. " % n + ("Distinct page %d has original native text. " % n) * 8
                 for n in range(1, 5)]
        result = self.extract(self.pdf(pages), self.candidate(pages[-1]))
        self.assertIn("Distinct page 1", body_text(result))
        self.assertEqual(result["source"], "pdf_general")

    def test_general_fallback_preserves_short_blocks_and_native_coordinates(self):
        path = self.pdf(["A\nB", "A\nB"])
        result = self.extract(path, self.candidate(""))
        paragraphs = [p for s in result["body_text"] for p in s["paragraphs"]]
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual([p["source"]["page"] for p in paragraphs], [1, 2])
        self.assertEqual(len({p["id"] for p in paragraphs}), 2)
        self.assertTrue(all(len(p["source"]["bbox"]) == 4 for p in paragraphs))
        self.assertEqual(result["source_map"]["coordinate_space"], "pymupdf_unrotated_points")

    def test_sufficient_paper_result_is_not_replaced(self):
        text = "The original paper body contains enough native text to cover this entire page."
        candidate = self.candidate(text)
        before = copy.deepcopy(candidate.to_dict()["body_text"])
        result = self.extract(self.pdf([text]), candidate)
        self.assertEqual(result["source"], "grobid")
        self.assertEqual(result["body_text"], before)

    def test_textless_page_is_partial_even_if_references_have_text(self):
        result = self.extract(self.pdf(["", "References\nSynthetic reference text."]),
                              self.candidate(""))
        self.assertIn("extraction_report", result)
        report = result["extraction_report"]
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["pages"][0]["status"], "blank")
        self.assertFalse(any("OCR" in warning for warning in report["warnings"]))

    def test_verified_white_page_is_blank_and_does_not_run_available_ocr(self):
        from pubnexus import general_pdf
        with patch.object(general_pdf.shutil, "which", return_value="synthetic-tesseract"), \
                patch.object(general_pdf, "_local_command", side_effect=AssertionError("No OCR on blank pages")) as command:
            _, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), self.pdf(["", "Native body."]))
        self.assertEqual(report["pages"][0]["status"], "blank")
        self.assertFalse(report["pages"][0].get("ocr_performed", False))
        self.assertFalse(report["warnings"])
        command.assert_not_called()

    def test_attempted_empty_ocr_is_not_reported_as_unexecuted(self):
        from pubnexus import general_pdf
        path, _ = self.image_pdf()
        empty_tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        with patch.object(general_pdf.shutil, "which", return_value="synthetic-tesseract"), \
                patch.object(general_pdf, "_local_command", side_effect=["List of languages (1):\neng\n", empty_tsv]):
            _, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertEqual(report["pages"][0]["status"], "ocr_empty")
        self.assertTrue(report["ocr_performed"])
        self.assertTrue(any("인식하지 못" in text for text in report["warnings"]))
        self.assertFalse(any("미실행" in text for text in report["warnings"]))

    def test_native_only_pdf_needs_no_doi_or_abstract(self):
        text = "No DOI\nNo abstract\nNative text from a general document must still be retained."
        result = self.extract(self.pdf([text]))
        self.assertIn("No DOI", body_text(result))
        self.assertEqual(result["source"], "pdf_general")

    def test_paper_parser_exception_can_recover_native_text(self):
        path = self.pdf(["Native text is retained even when the paper parser fails."])
        with patch.object(single, "_fulltext", side_effect=ValueError("paper parse failed")):
            try:
                result = self.extract(path)
            except ValueError as error:
                self.fail(f"native general fallback was not attempted: {error}")
        self.assertIn("Native text is retained", body_text(result))
        self.assertEqual(result["source"], "pdf_general")

    def test_missing_one_of_four_pages_is_not_complete(self):
        from pubnexus import general_pdf
        pages = [f"Page {number} has unique original book content. " * 4
                 for number in range(1, 5)]
        result, report = general_pdf.ensure_fulltext(
            self.candidate("\n".join(pages[1:])).to_dict(), self.pdf(pages))
        self.assertIn("Page 1 has unique", body_text(result))
        self.assertTrue(report["used_fallback"])

    def test_repeated_native_body_occurrences_are_not_collapsed(self):
        from pubnexus import general_pdf
        text = "This repeated body is a complete original paragraph, not a running header."
        result, report = general_pdf.ensure_fulltext(self.candidate(text).to_dict(),
                                                   self.pdf([text, text]))
        self.assertEqual(body_text(result).count("This repeated body"), 2)
        self.assertTrue(report["used_fallback"])

    def test_confident_paper_boundary_does_not_reintroduce_neighbour(self):
        from pubnexus import general_pdf
        own = "The selected article has native original body text on its own page."
        other = "The neighbouring article must not be merged into the selected article."
        candidate = self.candidate(own).to_dict()
        candidate["qc"] = {"boundary": {"confident": True}}
        result, report = general_pdf.ensure_fulltext(candidate, self.pdf([own, other]))
        self.assertEqual(result["body_text"], candidate["body_text"])
        self.assertEqual(report["status"], "partial")

    def test_failed_page_retries_once_and_keeps_other_pages(self):
        from pubnexus import general_pdf
        path = self.pdf(["Original first page.", "Original middle page.", "Original last page."])
        read = general_pdf._page_blocks
        attempts = {}

        def intermittent(page):
            attempts[page.number] = attempts.get(page.number, 0) + 1
            if page.number == 1 and attempts[page.number] == 1:
                raise RuntimeError("synthetic temporary page failure")
            return read(page)

        with patch.object(general_pdf, "_page_blocks", side_effect=intermittent):
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertIn("Original middle page", body_text(result))
        self.assertEqual(attempts[1], 2)
        self.assertEqual(report["pages"][1]["attempts"], 2)
        self.assertEqual(report["status"], "complete")

    def test_permanent_page_failure_is_partial_and_keeps_surrounding_pages(self):
        from pubnexus import general_pdf
        path = self.pdf(["Original first page.", "Original middle page.", "Original last page."])
        read = general_pdf._page_blocks

        def broken(page):
            if page.number == 1:
                raise RuntimeError("synthetic permanent page failure")
            return read(page)

        with patch.object(general_pdf, "_page_blocks", side_effect=broken):
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertIn("Original first page", body_text(result))
        self.assertIn("Original last page", body_text(result))
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["pages"][1]["status"], "failed")
        self.assertIn("attempts", report["pages"][1])
        self.assertEqual(report["pages"][1]["attempts"], 2)

    def test_paper_header_is_not_evidence_of_missing_body(self):
        from pubnexus import general_pdf
        header = "Journal of Synthetic Research - Volume 12 - page 123"
        text = "The original article body is complete and retains all meaningful study observations. " * 12
        path = self.root / "header.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((45, 30), header, fontsize=10)
            self.assertGreaterEqual(page.insert_textbox((45, 80, 550, 300), text, fontsize=10), 0)
            pdf.save(path)
        candidate = self.candidate(text).to_dict()
        result, report = general_pdf.ensure_fulltext(candidate, path)
        self.assertEqual(result["source"], "grobid")
        self.assertEqual(result["body_text"], candidate["body_text"])
        self.assertFalse(report["used_fallback"])

    def test_general_native_output_still_retains_paper_header(self):
        from pubnexus import general_pdf
        header = "Journal of Synthetic Research - Volume 12 - page 123"
        text = "The general PDF body remains alongside its original publication header. " * 6
        path = self.root / "header.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((45, 30), header, fontsize=10)
            self.assertGreaterEqual(page.insert_textbox((45, 80, 550, 300), text, fontsize=10), 0)
            pdf.save(path)
        result, _ = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertIn(header, body_text(result))
        self.assertIn("general PDF body", body_text(result))

    def test_meaningful_marginal_paragraph_is_coverage_evidence(self):
        from pubnexus import general_pdf
        lead = "The journal of synthetic research volume 12 is analysed as a meaningful study sentence."
        text = "The remaining original study body is preserved in the paper candidate. " * 6
        path = self.root / "marginal-body.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((45, 30), lead, fontsize=10)
            self.assertGreaterEqual(page.insert_textbox((45, 80, 550, 300), text, fontsize=10), 0)
            pdf.save(path)
        result, report = general_pdf.ensure_fulltext(self.candidate(text).to_dict(), path)
        self.assertEqual(result["source"], "grobid")
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["used_fallback"])
        preserved = result.get("native_unrepresented_blocks") or []
        self.assertTrue(any(block["text"] == lead for block in preserved))

    def test_structured_paper_metadata_and_section_title_are_represented(self):
        from pubnexus import general_pdf
        title = "A randomized study of synthetic observations in clinical research"
        author = "Synthetic Author"
        heading = "Study design and original observations"
        text = "The full original article body remains preserved with its structured metadata. " * 10
        path = self.root / "structured-paper.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((45, 60), title, fontsize=10)
            page.insert_text((45, 80), author, fontsize=10)
            page.insert_text((45, 100), heading, fontsize=10)
            self.assertGreaterEqual(page.insert_textbox((45, 120, 550, 400), text, fontsize=10), 0)
            pdf.save(path)
        candidate = self.candidate(text).to_dict()
        candidate["meta"].update(title=title, authors=[author])
        candidate["body_text"][0]["path"] = [heading]
        result, report = general_pdf.ensure_fulltext(candidate, path)
        self.assertEqual(result["source"], "grobid")
        self.assertEqual(result["body_text"], candidate["body_text"])
        self.assertFalse(report["used_fallback"])

    def ruled_table_pdf(self, merged=False):
        path = self.root / "table.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 60), "The original body remains outside the numeric table.")
            for y in (120, 150, 180, 210):
                page.draw_line((50, y), (380, y))
            for x in (50, 160, 270, 380):
                page.draw_line((x, 150 if merged and x == 160 else 120), (x, 210))
            rows = [["Combined", "", "C"], ["111", "222", "333"], ["444", "555", "666"]]
            if not merged:
                rows[0] = ["A", "B", "C"]
            for row, values in enumerate(rows):
                for col, value in enumerate(values):
                    if value:
                        page.insert_text((60 + col * 110, 140 + row * 30), value, fontsize=10)
            pdf.save(path)
        return path

    def image_pdf(self, text=""):
        path = self.root / "image.pdf"
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 12), 0)
        image.clear_with(120)
        original = image.tobytes("png")
        with fitz.open() as pdf:
            page = pdf.new_page()
            if text:
                page.insert_text((45, 60), text)
            page.insert_image(fitz.Rect(50, 120, 250, 240), stream=original)
            pdf.save(path)
        return path, image

    def test_ruled_table_is_structured_and_numbers_are_not_duplicated_in_prose(self):
        from pubnexus import general_pdf, render
        result, _ = general_pdf.ensure_fulltext(self.candidate("").to_dict(), self.ruled_table_pdf())
        self.assertEqual(len(result["tables"]), 1)
        table = result["tables"][0]
        self.assertIn("111", table["markdown"])
        self.assertEqual(len(table["cells"]), 9)
        self.assertEqual(table["source"]["page"], 1)
        self.assertEqual(len(table["source"]["bbox"]), 4)
        self.assertNotIn("111", body_text(result))
        self.assertIn("original body", body_text(result))
        self.assertEqual(render.to_markdown(result).count("111"), 1)

    def test_merged_table_preserves_span_instead_of_filling_fake_pipe_cells(self):
        from pubnexus import general_pdf
        result, _ = general_pdf.ensure_fulltext(self.candidate("").to_dict(),
                                               self.ruled_table_pdf(merged=True))
        self.assertEqual(len(result["tables"]), 1)
        table = result["tables"][0]
        self.assertIn('colspan="2"', table["markdown"])
        self.assertEqual(table["markdown"].count("Combined"), 1)
        self.assertNotIn("111", body_text(result))

    def test_embedded_image_is_saved_in_isolated_store_with_coordinates(self):
        from pubnexus import general_pdf, store
        path, original = self.image_pdf("The original body and embedded figure are both retained.")
        result, _ = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertEqual(len(result["figures"]), 1)
        figure = result["figures"][0]
        asset = store.figs_dir(store.file_sha1(path)) / figure["image"]
        self.assertTrue(asset.is_file())
        self.assertEqual(Path(figure["image"]).name, figure["image"])
        restored = fitz.Pixmap(str(asset))
        self.assertEqual((restored.width, restored.height), (original.width, original.height))
        self.assertEqual(restored.samples, original.samples)
        self.assertEqual(figure["source"]["page"], 1)
        self.assertEqual(len(figure["source"]["bbox"]), 4)

    def test_scan_without_tesseract_has_explicit_dependency_warning(self):
        from pubnexus import general_pdf
        path, _ = self.image_pdf()
        with patch.object(general_pdf.shutil, "which", return_value=None):
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["ocr_performed"])
        self.assertFalse(report["ocr_available"])
        self.assertTrue(any("Tesseract" in warning for warning in report["warnings"]))
        self.assertFalse(body_text(result).strip())

    def test_cancelled_general_extraction_raises_without_success_or_assets(self):
        from pubnexus import general_pdf, store
        path = self.pdf(["Original first page.", "Original second page."])
        self.assertTrue(hasattr(general_pdf, "ExtractionCancelled"))
        stopped = False
        observed = []

        def progress(page, total, stage):
            nonlocal stopped
            observed.append((page, total, stage))
            if page == 1 and stage == "native":
                stopped = True

        with self.assertRaises(general_pdf.ExtractionCancelled):
            general_pdf.ensure_fulltext(self.candidate("").to_dict(), path,
                                        cancel=lambda: stopped, progress=progress)
        self.assertTrue(observed)
        self.assertFalse(any(page == 2 for page, _, _ in observed))
        self.assertFalse(store.figs_dir(store.file_sha1(path)).exists())

    def test_local_ocr_tsv_is_retained_with_confidence_and_source_coordinates(self):
        from pubnexus import general_pdf
        path, _ = self.image_pdf()
        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t1\t1\t100\t200\t80\t20\t96\tRecovered\n"
               "5\t1\t1\t1\t1\t2\t190\t200\t70\t20\t94\tscanned\n"
               "5\t1\t1\t1\t1\t3\t270\t200\t40\t20\t95\tbody.\n")
        with patch.object(general_pdf.shutil, "which", return_value="synthetic-tesseract"), \
                patch.object(general_pdf, "_local_command", side_effect=["List of languages (2):\neng\nkor\n", tsv]) as command:
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertIn("Recovered scanned body.", body_text(result))
        self.assertTrue(report["ocr_performed"])
        self.assertEqual(report["status"], "partial")
        origin = result["body_text"][0]["paragraphs"][0]["source"]
        self.assertEqual(origin["method"], "tesseract")
        self.assertEqual(origin["confidence"], 95)
        self.assertEqual(len(origin["bbox"]), 4)
        self.assertEqual(command.call_args_list[-1].args[0][0], "synthetic-tesseract")

    def test_retry_reuses_native_pages_completed_before_cancellation(self):
        from pubnexus import general_pdf
        path = self.pdf(["Native first page is fully preserved.", "Native second page is fully preserved."])
        cancelled = False

        def progress(page, total, stage):
            nonlocal cancelled
            if page == 1 and stage == "native":
                cancelled = True

        with self.assertRaises(general_pdf.ExtractionCancelled):
            general_pdf.ensure_fulltext(self.candidate("").to_dict(), path,
                                        cancel=lambda: cancelled, progress=progress)
        read = general_pdf._read_native_page
        with patch.object(general_pdf, "_read_native_page", wraps=read) as reader:
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertIn("Native second page", body_text(result))
        self.assertEqual([call.args[1] for call in reader.call_args_list], [1])
        self.assertEqual(report["page_cache"]["native_reused"], 1)

    def test_retry_reuses_completed_media_page_and_assets(self):
        from pubnexus import general_pdf, store
        path, _ = self.image_pdf("A complete original body remains next to its embedded image.")
        cancelled = False

        def progress(page, total, stage):
            nonlocal cancelled
            if page == 1 and stage == "done":
                cancelled = True

        with self.assertRaises(general_pdf.ExtractionCancelled):
            general_pdf.ensure_fulltext(self.candidate("").to_dict(), path,
                                        cancel=lambda: cancelled, progress=progress)
        with patch.object(fitz.Page, "find_tables", side_effect=AssertionError("completed media must be reused")) as finder:
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        finder.assert_not_called()
        self.assertEqual(report["page_cache"]["media_reused"], 1)
        self.assertTrue((store.figs_dir(store.file_sha1(path)) / result["figures"][0]["image"]).is_file())

    def test_uncertain_table_keeps_native_text_next_to_retained_image(self):
        from pubnexus import general_pdf, render
        with patch.object(general_pdf, "_table_markup", side_effect=ValueError("synthetic uncertain grid")):
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), self.ruled_table_pdf())
        self.assertEqual(report["status"], "partial")
        self.assertIn("111", result["tables"][0]["markdown"])
        self.assertEqual(render.to_markdown(result).count("111"), 1)

    def test_table_removal_splits_mixed_span_without_dropping_body_characters(self):
        from pubnexus import general_pdf, render
        path = self.root / "mixed-span.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            for y in (120, 150, 180):
                page.draw_line((150, y), (380, y))
            for x in (150, 270, 380):
                page.draw_line((x, 120), (x, 180))
            page.insert_text((160, 140), "A")
            page.insert_text((280, 140), "B")
            page.insert_text((45, 170), "KEEP                777", fontname="cour", fontsize=10)
            page.insert_text((280, 170), "888")
            pdf.save(path)
        result, _ = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertIn("KEEP", body_text(result))
        self.assertNotIn("777", body_text(result))
        self.assertEqual(render.to_markdown(result).count("777"), 1)

    def test_asset_publish_failure_does_not_discard_table_native_text(self):
        from pubnexus import general_pdf
        path = self.ruled_table_pdf()
        combined = self.root / "table-and-image.pdf"
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 12, 12), 0)
        image.clear_with(100)
        with fitz.open(path) as pdf:
            pdf[0].insert_image(fitz.Rect(50, 300, 150, 400), stream=image.tobytes("png"))
            pdf.save(combined)
        with patch.object(general_pdf, "_copy_asset", side_effect=PermissionError("synthetic asset lock")):
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), combined)
        self.assertEqual(report["status"], "partial")
        available = body_text(result) + "\n".join(table["markdown"] for table in result["tables"])
        self.assertIn("111", available)

    def test_ocr_cache_key_changes_when_language_model_is_installed(self):
        from pubnexus import general_pdf
        path = self.pdf(["Synthetic native source"])
        models = self.root / "tessdata"
        models.mkdir()
        (models / "eng.traineddata").write_bytes(b"synthetic eng model")
        executable = self.root / "synthetic-tesseract.exe"
        executable.write_bytes(b"synthetic executable")
        with patch.dict(os.environ, {"TESSDATA_PREFIX": str(models)}):
            _, before = general_pdf._cache_location(path, str(executable))
            (models / "kor.traineddata").write_bytes(b"synthetic kor model")
            _, after = general_pdf._cache_location(path, str(executable))
        self.assertNotEqual(before, after)

    def test_ocr_body_outside_vector_grid_survives_table_preservation(self):
        from pubnexus import general_pdf
        path = self.root / "ocr-grid.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            for y in (120, 150, 180):
                page.draw_line((50, y), (270, y))
            for x in (50, 160, 270):
                page.draw_line((x, 120), (x, 180))
            pdf.save(path)
        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t1\t1\t100\t100\t100\t20\t95\tOUTSIDE\n"
               "5\t1\t1\t1\t2\t1\t200\t350\t40\t20\t95\t111\n")
        # Some detector versions reject pure grids; exercise an accepted empty grid explicitly.
        native = SimpleNamespace(bbox=(50, 120, 270, 180), header=SimpleNamespace(external=False),
                                 extract=lambda: [["", ""], ["", ""]])
        with patch.object(general_pdf.shutil, "which", return_value="synthetic-tesseract"), \
                patch.object(general_pdf, "_local_command", side_effect=["List of languages (1):\neng\n", tsv]), \
                patch.object(fitz.Page, "find_tables", return_value=SimpleNamespace(tables=[native])):
            result, report = general_pdf.ensure_fulltext(self.candidate("").to_dict(), path)
        self.assertIn("OUTSIDE", body_text(result))
        self.assertNotIn("111", body_text(result))
        self.assertIn("111", result["tables"][0]["markdown"])
        self.assertEqual(report["status"], "partial")


if __name__ == "__main__":
    unittest.main()
