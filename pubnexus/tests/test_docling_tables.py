"""Offline synthetic PDF table enhancement at the public document seam."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import fitz

VALUES = (("Item", "Count", "Rate"), ("Omega", "555.10", "0.15"),
          ("Sigma", "666.20", "0.25"), ("Theta", "777.30", "0.35"), ("Lambda", "888.40", "0.45"))


@contextmanager
def fixture():
    from pubnexus.external_documents import find_engine
    from pubnexus.general_pdf import ensure_fulltext
    if find_engine("markitdown_python") is None:
        raise unittest.SkipTest("Private offline SDK is not installed")
    with tempfile.TemporaryDirectory(prefix="pubnexus-table-contract-") as temporary:
        folder = Path(temporary)
        path = folder / "원본.pdf"
        make_pdf(path)
        models = folder / "models"
        for name in ("docling-project--docling-layout-heron/config.json", "docling-project--docling-layout-heron/preprocessor_config.json",
                     "docling-project--docling-layout-heron/model.safetensors",
                     "docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json",
                     "docling-project--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors"):
            file = models / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("External SDK boundary fixture only", encoding="ascii")
        with patch.dict(os.environ, {"PUBNEXUS_STORE": str(folder / "store"), "PUBNEXUS_DOCLING_MODELS": str(models)}):
            doc, _ = ensure_fulltext({"source": "pdf_general", "meta": {}, "body_text": [], "tables": []}, path)
            with fitz.open(path) as pdf:
                cells = []
                for row, values in enumerate(VALUES):
                    for col, text in enumerate(values):
                        box = pdf[0].search_for(text)[0]
                        cells.append({"start_row_offset_idx": row, "start_col_offset_idx": col,
                                      "row_span": 1, "col_span": 1, "text": text,
                                      "bbox": {"l": box.x0, "t": box.y0, "r": box.x1, "b": box.y1, "coord_origin": "TOPLEFT"}})
            record = {"status": "success", "page": 1, "version": "external-stub",
                      "pages": {"1": {"width": 612, "height": 792}}, "errors": [],
                      "tables": [{"rows": 5, "cols": 3, "cells": cells,
                                  "prov": [{"page_no": 1, "bbox": {"l": 52, "t": 170, "r": 560, "b": 350, "coord_origin": "TOPLEFT"}}]}]}
            yield path, doc, record


def sdk_response(record, extra=""):
    """Replace only the external SDK program, retaining real child/file handling."""
    return ("import json,sys\nfrom pathlib import Path\n"
            "request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
            f"record=json.loads({json.dumps(record)!r})\n"
            "size=record['pages']['1']\n"
            "for page in request['pages']:\n"
            " record['page']=page\n"
            " record['pages']={str(page):size}\n"
            " for table in record['tables']: table['prov'][0]['page_no']=page\n"
            " (Path(request['output'])/('page-%06d.json'%page)).write_text(json.dumps(record),encoding='utf-8')\n" + extra)


def make_pdf(path):
    with fitz.open() as pdf:
        page = pdf.new_page(width=612, height=792)
        page.insert_text((52, 48), "Synthetic borderless table study", fontname="hebo", fontsize=17)
        page.insert_text((52, 80), "BODY9001: Keep the original prose and 9001 outside the table.", fontsize=11)
        page.insert_text((52, 151), "Table 1. Measured synthetic observations", fontsize=10)
        for row, cells in enumerate((("Item", "Count", "Rate"), ("Omega", "555.10", "0.15"),
                                     ("Sigma", "666.20", "0.25"), ("Theta", "777.30", "0.35"), ("Lambda", "888.40", "0.45"))):
            for col, text in enumerate(cells):
                page.insert_text(((60, 280, 420)[col], 193 + row * 36), text, fontsize=10,
                                 fontname="hebo" if row == 0 else "helv")
        page.insert_text((52, 407), "AFTER9001: Preserve this paragraph too.", fontsize=11)
        pdf.save(path)


class DoclingTableTests(unittest.TestCase):
    def test_rotated_cropped_provenance_maps_back_to_native_points(self):
        from pubnexus.docling_tables import enhance_document
        from pubnexus.general_pdf import ensure_fulltext
        with fixture() as (path, _, record):
            rotated = path.parent / "회전 원본.pdf"
            with fitz.open(path) as pdf:
                page = pdf[0]
                page.set_cropbox(fitz.Rect(30, 40, 600, 770))
                page.set_rotation(90)
                pdf.save(rotated)
                record["pages"]["1"] = {"width": page.rect.width, "height": page.rect.height}
                boxes = [record["tables"][0]["prov"][0]["bbox"]] + [c["bbox"] for c in record["tables"][0]["cells"]]
                for box in boxes:
                    native = fitz.Rect(box["l"]-30, box["t"]-40, box["r"]-30, box["b"]-40)
                    display = native * page.rotation_matrix
                    box.update(l=display.x0, t=page.rect.height-display.y0, r=display.x1,
                               b=page.rect.height-display.y1, coord_origin="BOTTOMLEFT")
            doc, _ = ensure_fulltext({"source": "pdf_general", "meta": {}, "tables": [], "body_text": []}, rotated)
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record)):
                out = enhance_document(doc, rotated)
            self.assertEqual(len(out["tables"]), 1, out.get("extraction_report"))
            for observed, expected in zip(out["tables"][0]["source"]["bbox"], (22, 130, 530, 310)):
                self.assertAlmostEqual(observed, expected, places=3)
            self.assertEqual(out["tables"][0]["source"]["coordinate_space"], "pymupdf_unrotated_points")

    def test_ocr_caption_is_not_sent_to_native_only_table_engine(self):
        from pubnexus.docling_tables import enhance_document
        with fixture() as (path, doc, _):
            for section in doc["body_text"]:
                for paragraph in section["paragraphs"]:
                    paragraph["source"]["method"] = "tesseract"
            with patch("subprocess.Popen", side_effect=AssertionError("OCR text must not launch native-only Docling")):
                self.assertEqual(enhance_document(doc, path), doc)

    def test_changed_model_invalidates_page_cache(self):
        from pubnexus.docling_tables import enhance_document
        with fixture() as (path, doc, record):
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record)):
                first = enhance_document(doc, path)
                model = path.parent / "models/docling-project--docling-layout-heron/model.safetensors"
                model.write_text("Changed external SDK fixture", encoding="ascii")
                second = enhance_document(doc, path)
            self.assertEqual(len(second["tables"]), 1)
            self.assertEqual(second["extraction_report"]["docling_tables"]["cache_reused"], 0)
            self.assertNotEqual(first["extraction_report"]["docling_tables"]["model_fingerprint"],
                                second["extraction_report"]["docling_tables"]["model_fingerprint"])

    def test_cancel_kills_child_and_retry_reuses_completed_page(self):
        from pubnexus.docling_tables import enhance_document
        from pubnexus.general_pdf import ExtractionCancelled
        with fixture() as (path, doc, record):
            event, calls = threading.Event(), []
            def progress(page, total, stage):
                calls.append((page, total, stage))
                if len(calls) == 2:
                    event.set()
            program = sdk_response(record, "import time; time.sleep(10)\n")
            before = copy.deepcopy(doc)
            started = time.monotonic()
            with patch("pubnexus.docling_tables.CHILD_CODE", program):
                with self.assertRaises(ExtractionCancelled):
                    out = enhance_document(doc, path, cancel=event.is_set, progress=progress)
                    self.fail(str((calls, out.get("extraction_report"))))
                self.assertLess(time.monotonic()-started, 4)
                with patch.dict(os.environ, {"PUBNEXUS_DOCLING_TIMEOUT": "0.001"}):
                    retry = enhance_document(doc, path)
            self.assertEqual(doc, before)
            self.assertEqual(retry["extraction_report"]["docling_tables"]["cache_reused"], 1)
            self.assertEqual(len(retry["tables"]), 1)

    def test_timeout_and_missing_models_preserve_prose_and_report_partial(self):
        from pubnexus.docling_tables import enhance_document
        with fixture() as (path, doc, _):
            for missing in (False, True):
                with self.subTest(missing_models=missing):
                    environment = {"PUBNEXUS_DOCLING_TIMEOUT": ".2"}
                    if missing:
                        environment["PUBNEXUS_DOCLING_MODELS"] = str(path.parent / "absent-models")
                    started = time.monotonic()
                    with patch.dict(os.environ, environment), patch("pubnexus.docling_tables.CHILD_CODE", "import time; time.sleep(10)"):
                        out = enhance_document(doc, path)
                    self.assertLess(time.monotonic()-started, 4)
                    self.assertEqual(out["tables"], doc["tables"])
                    self.assertEqual(out["body_text"], doc["body_text"])
                    self.assertEqual(out["extraction_report"]["status"], "partial")
                    self.assertTrue(out["extraction_report"]["warnings"])

    def test_empty_paper_table_is_filled_without_touching_paper_structure(self):
        from pubnexus.docling_tables import enhance_document
        with fixture() as (path, _, record):
            doc = {"source": "grobid", "meta": {"title": "Keep structured title"}, "abstract": "Keep abstract",
                   "body_text": [{"path": ["Introduction"], "paragraphs": [{"text": "The paper body stays exactly intact."}]}],
                   "references": [{"id": "ref1", "raw": "Keep reference"}],
                   "tables": [{"id": "paper-table1", "caption": "Table 1. Measured synthetic observations", "markdown": "", "footnote": "Keep footnote"}]}
            original = copy.deepcopy(doc)
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record)):
                out = enhance_document(doc, path)
            for key in ("source", "meta", "abstract", "body_text", "references"):
                self.assertEqual(out[key], original[key])
            self.assertEqual(doc, original)
            self.assertEqual(out["tables"][0]["id"], "paper-table1")
            self.assertEqual(out["tables"][0]["footnote"], "Keep footnote")
            self.assertEqual(out["extraction_report"]["docling_tables"]["filled"], 1)

    def test_complete_paper_table_is_not_sent_to_optional_engine(self):
        from pubnexus.docling_tables import enhance_document
        with tempfile.TemporaryDirectory(prefix="pubnexus-keep-paper-") as temporary:
            path = Path(temporary) / "source.pdf"
            make_pdf(path)
            doc = {"source": "grobid", "tables": [{"id": "good", "markdown": "| Keep | Values |\n|---|---|\n| A | 42 |"}],
                   "body_text": [{"paragraphs": [{"text": "Keep existing paper"}]}]}
            with patch("subprocess.Popen", side_effect=AssertionError("Complete paper must not start Docling")):
                self.assertEqual(enhance_document(doc, path), doc)

    def test_empty_cells_cannot_hide_a_shifted_value_column(self):
        from pubnexus.docling_tables import enhance_document
        with fixture() as (path, doc, record):
            table = record["tables"][0]
            table["rows"] = 6
            for cell in table["cells"]:
                if cell["start_col_offset_idx"] == 1:
                    cell["start_row_offset_idx"] += 1
            for row, col in ((0, 1), (5, 0), (5, 2)):
                table["cells"].append({"start_row_offset_idx": row, "start_col_offset_idx": col,
                                       "row_span": 1, "col_span": 1, "text": "", "bbox": None})
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record)):
                out = enhance_document(doc, path)
            self.assertEqual(out["tables"], doc["tables"])
            self.assertEqual(out["body_text"], doc["body_text"])
            self.assertEqual(out["extraction_report"]["status"], "partial")

    def test_all_candidate_pages_are_processed_in_bounded_batches(self):
        from pubnexus.docling_tables import enhance_document
        from pubnexus.general_pdf import ensure_fulltext
        with fixture() as (path, _, record):
            book = path.parent / "book.pdf"
            with fitz.open(path) as source, fitz.open() as pdf:
                for _ in range(9):
                    pdf.insert_pdf(source)
                pdf.save(book)
            doc, _ = ensure_fulltext({"source": "pdf_general", "meta": {}, "body_text": [], "tables": []}, book)
            extra = "with open(Path(request['source']).parent/'batches.log','a') as stream: stream.write(str(len(request['pages']))+'\\n')\n"
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record, extra)):
                out = enhance_document(doc, book)
            self.assertEqual(len(out["tables"]), 9)
            self.assertEqual((path.parent / "batches.log").read_text().splitlines(), ["8", "1"])
            self.assertEqual(out["extraction_report"]["docling_tables"]["attempted_pages"], list(range(1, 10)))

    def test_changed_source_rolls_back_all_enhancement_and_marks_failed(self):
        from pubnexus.docling_tables import enhance_document
        with fixture() as (path, doc, record):
            extra = "with open(request['source'],'ab') as stream: stream.write(b'\\n% changed synthetic source\\n')\n"
            before = path.read_bytes()
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record, extra)):
                out = enhance_document(doc, path)
            self.assertNotEqual(path.read_bytes(), before)
            self.assertEqual(out["tables"], doc["tables"])
            self.assertEqual(out["body_text"], doc["body_text"])
            self.assertEqual(out["extraction_report"]["status"], "failed")

    def test_table_is_inserted_between_before_and_after_paragraphs(self):
        from pubnexus.docling_tables import enhance_document
        from pubnexus.render import to_markdown
        with fixture() as (path, doc, record):
            before = path.read_bytes()
            record["tables"][0]["cells"].reverse()
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record)):
                out = enhance_document(doc, path)
            self.assertEqual(path.read_bytes(), before)
            md = to_markdown(out)
            self.assertLess(md.index("BODY9001"), md.index("<table>"))
            self.assertLess(md.index("<table>"), md.index("AFTER9001"))
            from lxml import etree
            rows = etree.fromstring(out["tables"][0]["markdown"].encode()).xpath(".//tr")
            self.assertEqual([[cell.text for cell in row] for row in rows], [list(values) for values in VALUES])

    def test_wrong_logical_rows_are_rejected_despite_all_original_numbers(self):
        from pubnexus.docling_tables import enhance_document
        with fixture() as (path, doc, record):
            numbers = [c for c in record["tables"][0]["cells"] if c["text"] in ("555.10", "666.20")]
            numbers[0]["start_row_offset_idx"], numbers[1]["start_row_offset_idx"] = 2, 1
            with patch("pubnexus.docling_tables.CHILD_CODE", sdk_response(record)):
                out = enhance_document(doc, path)
            self.assertEqual(out["tables"], doc["tables"])
            self.assertEqual(out["body_text"], doc["body_text"])
            self.assertEqual(out["extraction_report"]["docling_tables"]["status"], "partial")

    def test_native_borderless_table_gains_exact_cells_without_prose_duplicates(self):
        from pubnexus.docling_tables import enhance_document
        from pubnexus.external_documents import find_engine
        from pubnexus.general_pdf import ensure_fulltext
        from pubnexus.render import to_markdown
        if find_engine("markitdown_python") is None:
            self.skipTest("Private offline Docling SDK is not installed")
        with tempfile.TemporaryDirectory(prefix="pubnexus-docling-test-") as temporary:
            folder = Path(temporary)
            path = folder / "한글 무테 표.pdf"
            make_pdf(path)
            with patch.dict(os.environ, {"PUBNEXUS_STORE": str(folder / "store")}):
                original, _ = ensure_fulltext({"source": "pdf_general", "meta": {"title": "Keep title"},
                                               "body_text": [], "tables": [], "figures": [], "references": []}, path)
                before = copy.deepcopy(original)
                self.assertEqual(original["tables"], [])
                out = enhance_document(original, path)
                self.assertEqual(original, before)
                self.assertEqual(out["meta"], before["meta"])
                self.assertEqual(len(out["tables"]), 1, out.get("extraction_report"))
                cells = {(cell["row"], cell["col"]): cell["text"] for cell in out["tables"][0]["cells"]}
                self.assertEqual(cells, {(0, 0): "Item", (0, 1): "Count", (0, 2): "Rate",
                                         (1, 0): "Omega", (1, 1): "555.10", (1, 2): "0.15",
                                         (2, 0): "Sigma", (2, 1): "666.20", (2, 2): "0.25",
                                         (3, 0): "Theta", (3, 1): "777.30", (3, 2): "0.35",
                                         (4, 0): "Lambda", (4, 1): "888.40", (4, 2): "0.45"})
                prose = "\n".join(p["text"] for section in out["body_text"] for p in section["paragraphs"])
                for value in ("555.10", "666.20", "777.30", "888.40"):
                    self.assertNotIn(value, prose)
                    self.assertEqual(len(re.findall(re.escape(value), to_markdown(out))), 1)
                self.assertIn("BODY9001", prose)
                self.assertIn("AFTER9001", prose)
                self.assertEqual(out["tables"][0]["source"]["page"], 1)
                self.assertEqual(out["extraction_report"]["docling_tables"]["added"], 1)
                with patch.dict(os.environ, {"PUBNEXUS_DOCLING_TIMEOUT": "0.001"}):
                    cached = enhance_document(original, path)
                self.assertEqual(cached["tables"], out["tables"])
                self.assertEqual(cached["extraction_report"]["docling_tables"]["cache_reused"], 1)


if __name__ == "__main__":
    unittest.main()
