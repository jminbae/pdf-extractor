"""공식 PDF SDK의 문자열·좌표·격리 프로세스 공개 계약."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class PDFMarkdownTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pubnexus-pdf-sdk-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def fixture(self, name="original.pdf"):
        path = self.base / name
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((36, 65), "Native SDK marker 7391", fontsize=17)
            page.insert_text((36, 95), "First paragraph. Exact punctuation preserved.")
            page.draw_rect(fitz.Rect(40, 170, 210, 270), color=(0, 0, 0), fill=(.2, .6, .9))
            page = pdf.new_page(width=400, height=500)
            page.insert_text((36, 210), "Second page marker 4278. This is another body paragraph.")
            page.insert_text((36, 230), "Additional body content with ordinary punctuation.")
            pdf.save(path)
        return path

    def test_actual_sdk_preserves_markdown_and_exact_page_box_offsets(self):
        from pubnexus.pdf_markdown import available, convert, engine_id
        if not available():
            self.skipTest("별도 설치한 공식 PyMuPDF 2.0 엔진이 없습니다.")
        path = self.fixture()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        progress = []
        result = convert(path, self.base / "assets", progress=lambda *args: progress.append(args))
        self.assertEqual(result["source"], "pdf_sdk")
        self.assertEqual(result["sdk_engine"], "pymupdf-2.0")
        self.assertIn("2.0", engine_id())
        markdown = result["sdk_markdown"]
        self.assertIn("Native SDK marker 7391", markdown)
        self.assertIn("Second page marker 4278", markdown)
        self.assertEqual(result["references"], [])
        self.assertEqual(result["extraction_report"]["engine"]["version"], "2.0")
        self.assertEqual(len(result["source_map"]["pages"]), 2)
        self.assertTrue(result["sdk_blocks"])
        for block in result["sdk_blocks"]:
            self.assertEqual(markdown[block["start"]:block["end"]], block["text"])
            self.assertIn(block["source"]["page"], (1, 2))
            self.assertEqual(len(block["source"]["bbox"]), 4)
        self.assertTrue(progress)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_sdk_generated_image_destination_ranges_are_exact_and_assets_are_relative(self):
        from pubnexus.pdf_markdown import available, convert
        if not available():
            self.skipTest("별도 SDK가 없습니다.")
        result = convert(self.fixture(), self.base / "assets")
        self.assertTrue(result["figures"])
        self.assertTrue(result["sdk_assets"])
        for asset in result["sdk_assets"]:
            self.assertEqual(result["sdk_markdown"][asset["start"]:asset["end"]], asset["image"])
            self.assertFalse(Path(asset["image"]).is_absolute())
            self.assertTrue((self.base / "assets" / asset["image"]).is_file())
            self.assertTrue(any(figure["image"] == asset["image"] for figure in result["figures"]))

    def test_page_chunk_aggregation_is_identical_to_the_public_sdk_combined_markdown(self):
        from pubnexus.pdf_markdown import available, convert
        runtime = os.environ.get("PUBNEXUS_PDF_SDK_PYTHON")
        if not runtime:
            settings = (Path.home() / ".ai-workspace" / "paths.md").read_text(encoding="utf-8-sig")
            cache = next(line.split(":", 1)[1].strip() for line in settings.splitlines() if line.startswith("cache_root:"))
            runtime = str(Path(cache) / "260725 PDF 추출" / "engines" / "pymupdf4llm-runtime" / "Scripts" / "python.exe")
        if not available() or not Path(runtime).is_file():
            self.skipTest("공개 API 대조용 별도 Python이 없습니다.")
        path = self.fixture()
        result = convert(path, self.base / "assets")
        reference = self.base / "reference"
        reference.mkdir()
        shutil.copyfile(path, reference / "document.pdf")
        prefix = "images/" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        (reference / prefix).mkdir(parents=True)
        # Independent public SDK combined mode, deliberately no application adapter.
        script = ("import pymupdf,sys;from pathlib import Path;"
                  "doc=pymupdf.open('document.pdf');"
                  "md=doc.to_markdown(header=False,footer=False,page_separators=False,"
                  "write_images=True,image_path=sys.argv[1],use_ocr=False,page_chunks=False);"
                  "Path('expected.md').write_text(md,encoding='utf-8')")
        subprocess.run([runtime, "-I", "-B", "-c", script, prefix], cwd=reference, check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result["sdk_markdown"], (reference / "expected.md").read_text(encoding="utf-8"))

    def test_rotated_page_sdk_boxes_point_to_original_unrotated_text(self):
        from pubnexus.pdf_markdown import available, convert
        if not available():
            self.skipTest("별도 SDK가 없습니다.")
        path = self.base / "rotated.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            # Upright displayed text in a rotated page coordinate system.
            page.insert_text((210, 460), "Second page marker 4278. Ordinary body content.", rotate=90)
            page.insert_text((230, 460), "Another ordinary paragraph on this page.", rotate=90)
            page.set_rotation(90)
            target = page.search_for("Second page marker 4278")[0]
            pdf.save(path)
        result = convert(path, self.base / "assets")
        block = next(b for b in result["sdk_blocks"] if "Second page marker 4278" in b["text"])
        self.assertEqual(block["source"]["page"], 1)
        self.assertGreater((fitz.Rect(block["source"]["bbox"]) & target).get_area(), target.get_area() * .5)
        with fitz.open(path) as pdf:
            self.assertEqual(pdf[0].rotation, 90)

    def test_sideways_sdk_limitation_is_rejected_by_native_body_loss_guard(self):
        from pubnexus.pdf_markdown import available
        from pubnexus.pdf_pipeline import upgrade_general
        if not available():
            self.skipTest("별도 SDK가 없습니다.")
        source = self.fixture()
        path = self.base / "sideways.pdf"
        with fitz.open(source) as pdf:
            pdf.select([1])
            pdf[0].set_rotation(90)
            native = pdf[0].get_text()
            pdf.save(path)
        baseline = {"source": "pdf_general", "body_text": [{"path": ["Page 1"], "paragraphs": [{
            "id": "native-1", "text": native, "source": {"page": 1, "bbox": [0, 0, 400, 500]}}]}],
            "source_map": {"coordinate_space": "pymupdf_unrotated_points"},
            "extraction_report": {"status": "complete", "pages": [{"page": 1, "height": 500}], "warnings": []}}
        with patch.dict(os.environ, {"PUBNEXUS_STORE": str(self.base / "store")}):
            result = upgrade_general(baseline, path)
        self.assertEqual(result["source"], "pdf_general")
        self.assertEqual(result["body_text"], baseline["body_text"])
        self.assertEqual(result["extraction_report"]["status"], "partial")
        self.assertTrue(result["extraction_report"]["warnings"])

    def test_actual_sdk_ocr_recognizes_an_image_only_page_and_keeps_blank_page_honest(self):
        from pubnexus.pdf_markdown import available, convert
        from pubnexus.local_ocr import discover
        if not available() or discover() is None:
            self.skipTest("별도 SDK와 검증된 로컬 OCR 모델이 필요합니다.")
        image = Image.new("RGB", (1600, 800), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 72)
        draw.text((90, 100), "SDK OCR marker 7391", font=font, fill="black")
        draw.text((90, 240), "An ordinary scanned body sentence.", font=font, fill="black")
        draw.text((90, 380), "Another body paragraph for the scan.", font=font, fill="black")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        path = self.base / "scan.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=500, height=600)
            page.insert_image(fitz.Rect(30, 180, 470, 400), stream=stream.getvalue())
            pdf.new_page(width=500, height=600)
            pdf.save(path)
        with fitz.open(path) as pdf:
            self.assertEqual(pdf[0].get_text(), "")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = convert(path, self.base / "assets")
        self.assertIn("7391", result["sdk_markdown"])
        report = result["extraction_report"]
        self.assertTrue(report["ocr_performed"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["pages"][0]["status"], "ocr_extracted")
        self.assertEqual(report["pages"][1]["status"], "blank")
        self.assertFalse(report["pages"][1]["ocr_performed"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_missing_engine_encrypted_pdf_and_oversized_render_have_explicit_errors(self):
        from pubnexus.pdf_markdown import PDFMarkdownError, available, convert
        path = self.fixture()
        with patch.dict(os.environ, {"PUBNEXUS_PDF_ENGINE": "off"}):
            self.assertFalse(available())
            with self.assertRaises(PDFMarkdownError) as raised:
                convert(path, self.base / "assets")
            self.assertEqual(raised.exception.code, "unavailable")
        if not available():
            return
        for mode in ("encrypted", "limit"):
            path = self.base / (mode + ".pdf")
            with fitz.open() as pdf:
                page = pdf.new_page(width=10000 if mode == "limit" else 400, height=10000 if mode == "limit" else 500)
                page.insert_text((50, 100), "Private marker")
                options = dict(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password") if mode == "encrypted" else {}
                pdf.save(path, **options)
            with self.subTest(mode=mode), self.assertRaises(PDFMarkdownError) as raised:
                convert(path, self.base / "assets")
            self.assertEqual(raised.exception.code, mode)

    def test_real_child_cancel_timeout_and_memory_limits_leave_no_temporary_handles(self):
        from pubnexus import pdf_markdown as sdk
        from pubnexus.general_pdf import ExtractionCancelled
        if not sdk.available():
            self.skipTest("별도 SDK가 없습니다.")
        path = self.fixture()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        real_temporary = tempfile.TemporaryDirectory
        work_paths = []
        def isolated_temporary(*args, **kwargs):
            temporary = real_temporary(*args, dir=self.base, **kwargs)
            work_paths.append(Path(temporary.name))
            return temporary
        for mode in ("cancel", "timeout", "memory"):
            started = time.monotonic()
            with self.subTest(mode=mode), patch("pubnexus.pdf_markdown.tempfile.TemporaryDirectory", isolated_temporary), patch.object(
                    sdk, "TIMEOUT", .3 if mode == "timeout" else 60), patch.object(
                    sdk, "MAX_MEMORY", 1_000_000 if mode == "memory" else sdk.MAX_MEMORY):
                expected = ExtractionCancelled if mode == "cancel" else sdk.PDFMarkdownError
                with self.assertRaises(expected) as raised:
                    sdk.convert(path, self.base / "assets", cancel=(lambda: time.monotonic()-started > .3) if mode == "cancel" else None)
                if mode != "cancel":
                    self.assertEqual(raised.exception.code, "timeout" if mode == "timeout" else "limit")
            self.assertTrue(all(not work.exists() for work in work_paths))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
