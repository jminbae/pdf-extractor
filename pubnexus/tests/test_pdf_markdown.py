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
        self.assertIn("korean_refinement", result["extraction_report"])
        self.assertEqual(result["sdk_markdown_raw"], markdown)
        self.assertEqual(len(result["source_map"]["pages"]), 2)
        self.assertTrue(result["sdk_blocks"])
        for block in result["sdk_blocks"]:
            self.assertEqual(markdown[block["start"]:block["end"]], block["text"])
            self.assertIn(block["source"]["page"], (1, 2))
            self.assertEqual(len(block["source"]["bbox"]), 4)
        self.assertTrue(progress)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_refinement_raw_basis_and_cross_page_source_locations_survive_canonical(self):
        from pubnexus.pdf_markdown import _canonical
        pages = [{"page": n, "status": "text_extracted", "ocr_performed": False,
                  "sdk_to_source": [1, 0, 0, 1, 0, 0] if n == 1 else [0, -1, 1, 0, 0, 400]}
                 for n in (1, 2)]
        locations = [{"page": n, "sdk_bbox": [10, 20, 30, 40], "raw_pos": [0, 4],
                      "block_index": 0, "corrected_local_pos": [0, 4] if n == 1 else [4, 8]} for n in (1, 2)]
        record = {"version": "2.0", "markdown": "joined\n\n", "markdown_raw": "join ed\n\n",
            "chunks_raw": [{"text": "join ed\n\n"}], "korean_input_markdown": "join ed\n\n",
            "korean_input_chunks": [{"text": "join ed\n\n"}], "metadata": {}, "pages": pages,
            "chunks": [{"metadata": {"page_number": 1}, "text": "joined\n\n", "page_boxes": [
                {"index": 0, "class": "text", "bbox": [10, 20, 30, 40], "pos": [0, 8],
                 "pos_raw": [0, 4], "korean_source_locations": locations}]},
                {"metadata": {"page_number": 2}, "text": "", "page_boxes": []}],
            "korean_refinement": {"status": "partial", "warnings": ["검증 필요"], "edits": [],
                                   "cross_page_joins": [{"left": locations[0], "right": locations[1]}]},
            "table_refinements": [{"page": 1, "method": "PyMuPDF.Table.to_html"}],
            "options": {}, "image_formats": ["png", "png"], "image_assets": {}}
        result = _canonical(record, self.base / "original.pdf", "a"*64, self.base / "assets", self.base, None)
        self.assertEqual(result["sdk_markdown_raw"], record["markdown_raw"])
        self.assertEqual(result["sdk_chunks_raw"], record["chunks_raw"])
        self.assertEqual(result["sdk_korean_input_markdown"], record["korean_input_markdown"])
        self.assertEqual(result["extraction_report"]["table_refinements"], record["table_refinements"])
        block = result["sdk_blocks"][0]
        self.assertEqual(block["source"]["locations"][0]["bbox"], [10, 20, 30, 40])
        self.assertEqual(block["source"]["locations"][1]["bbox"], [20, 370, 40, 390])
        self.assertEqual([p["page"] for p in block["source"]["locations"]], [1, 2])
        self.assertEqual(block["source"]["locations"][1]["corrected_local_pos"], [4, 8])
        self.assertEqual(block["source"]["locations"][1]["corrected_offset_basis"], "block_text")
        self.assertEqual(result["sdk_markdown"][block["start"]:block["end"]], block["text"])
        self.assertEqual(result["source_map"]["offset_basis"], "sdk_markdown_refined")

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

    def test_sdk_empty_text_pages_retry_public_options_and_numeric_page_stays_excluded(self):
        from pubnexus.pdf_markdown import available, convert, engine_id
        if not available():
            self.skipTest("별도 SDK가 없습니다.")
        source = self.base / "short-titles.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((45, 450), "Chapter title marker 9284")
            page = pdf.new_page(width=400, height=500)
            page.insert_text((230, 50), "Author title marker 6157", fontsize=8)
            page.insert_text((195, 475), "2")
            page = pdf.new_page(width=400, height=500)
            page.insert_text((195, 475), "3")
            page = pdf.new_page(width=400, height=500)
            page.insert_text((180, 450), "2026")
            pdf.save(source)
        result = convert(source, self.base / "assets")
        self.assertIn("Chapter title marker 9284", result["sdk_markdown"])
        self.assertIn("Author title marker 6157", result["sdk_markdown"])
        self.assertIn("2026", result["sdk_markdown"])
        self.assertNotIn("**3**", result["sdk_markdown"])
        self.assertNotIn("**2**", result["sdk_markdown"])
        self.assertEqual(engine_id(), "pymupdf-2.0-adapter-3")
        pages = result["extraction_report"]["pages"]
        self.assertEqual(pages[2]["status"], "pagination_only")
        self.assertFalse(pages[2]["blank_verified"])
        self.assertFalse(pages[2]["ocr_performed"])
        recovery = result["extraction_report"]["recovery_per_page"]
        self.assertEqual([page["page"] for page in recovery], [1, 2, 4])
        for page in recovery:
            self.assertEqual(page["before_sha256"], hashlib.sha256(b"").hexdigest())
            self.assertNotEqual(page["after_sha256"], page["before_sha256"])
            self.assertTrue(page["selected_options"]["header"])
        self.assertTrue(recovery[0]["selected_options"]["footer"])
        self.assertFalse(recovery[1]["selected_options"]["footer"])
        for block in result["sdk_blocks"]:
            self.assertEqual(result["sdk_markdown"][block["start"]:block["end"]], block["text"])

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
        prefix = result["extraction_report"]["engine"]["options"]["image_path"]
        (reference / prefix).mkdir(parents=True)
        # Independent public SDK combined mode, deliberately no application adapter.
        script = ("import pymupdf,sys;from pathlib import Path;"
                  "doc=pymupdf.open('document.pdf');"
                  "md=doc.to_markdown(header=False,footer=False,page_separators=False,"
                  "write_images=True,image_path=sys.argv[1],dpi=300,use_ocr=False,page_chunks=False);"
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

    def test_uniform_vector_background_and_off_trim_marks_are_verified_without_fake_ocr(self):
        from pubnexus.pdf_markdown import available, convert
        if not available():
            self.skipTest("별도 SDK가 없습니다.")
        source = self.base / "backgrounds.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.draw_rect(page.rect, color=None, fill=(.88, .93, .92))
            # Printer marks can have a combined bbox enclosing the page while every segment is outside it.
            shape = page.new_shape()
            shape.draw_line((-10, -10), (410, -10))
            shape.draw_line((-10, 510), (410, 510))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            page = pdf.new_page(width=400, height=500)
            page.draw_rect(fitz.Rect(399.9995, -2, 405, 502), color=None, fill=(.3, .6, .5))
            pdf.save(source)
        result = convert(source, self.base / "assets")
        pages = result["extraction_report"]["pages"]
        self.assertEqual([p["status"] for p in pages], ["background_only", "blank"])
        self.assertTrue(all(p["background_verification"]["interior_color_count"] == 1 for p in pages))
        self.assertTrue(all(not p["ocr_performed"] for p in pages))
        self.assertEqual(result["extraction_report"]["status"], "complete")

    def test_unselected_rectangle_outline_text_gets_official_ocr_and_single_line_is_kept(self):
        from pubnexus.pdf_markdown import available, convert
        from pubnexus.local_ocr import discover
        if not available() or discover() is None:
            self.skipTest("별도 SDK와 검증된 모델이 필요합니다.")
        bitmap = Image.new("1", (470, 90), 1)
        ImageDraw.Draw(bitmap).text((8, 12), "FORCE OCR 7391", fill=0,
            font=ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 48))
        source = self.base / "rectangle-outline-scan.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=600, height=600)
            shape = page.new_shape()
            for y in range(bitmap.height):
                start = None
                for x in range(bitmap.width + 1):
                    black = x < bitmap.width and bitmap.getpixel((x, y)) == 0
                    if black and start is None:
                        start = x
                    elif not black and start is not None:
                        shape.draw_rect(fitz.Rect(60+start, 200+y, 60+x, 201+y))
                        start = None
            shape.finish(color=None, fill=(0, 0, 0))
            shape.commit()
            self.assertEqual(page.get_text(), "")
            self.assertEqual(page.get_image_info(), [])
            pdf.save(source)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = convert(source, self.base / "assets")
        self.assertIn("7391", result["sdk_markdown"])
        page = result["extraction_report"]["pages"][0]
        self.assertTrue(page["ocr_performed"])
        self.assertEqual(page["ocr_selection"], "forced_after_sdk_skip")
        self.assertEqual(page["status"], "ocr_extracted")
        self.assertFalse(page["background_verified"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_exact_embedded_jpeg_is_reused_and_vector_crop_uses_300dpi_without_old_asset_overwrite(self):
        from pubnexus.pdf_markdown import available, convert
        if not available():
            self.skipTest("별도 SDK가 없습니다.")
        gradient = Image.linear_gradient("L").resize((1600, 1200))
        image = Image.merge("RGB", (gradient, gradient.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                                    Image.new("L", gradient.size, 140)))
        ImageDraw.Draw(image).ellipse((250, 200, 1050, 1000), fill=(190, 80, 45))
        encoded = io.BytesIO()
        image.save(encoded, "JPEG", quality=97, subsampling=0)
        original = encoded.getvalue()
        source = self.base / "native-photo.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((35, 80), "An ordinary body paragraph before the calibration image.")
            page.insert_image(fitz.Rect(40, 150, 360, 390), stream=original)
            pdf.save(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        old = self.base / "assets" / "images" / digest / "document.pdf-0001-01.png"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"previous-150dpi-cache-must-stay")
        result = convert(source, self.base / "assets")
        preserved = [figure for figure in result["figures"] if figure.get("asset_info", {}).get("method") == "embedded_original"]
        self.assertEqual(len(preserved), 1)
        self.assertEqual((self.base / "assets" / preserved[0]["image"]).read_bytes(), original)
        self.assertTrue(preserved[0]["image"].endswith(".jpeg"))
        self.assertIn("/sdk-v3/", preserved[0]["image"])
        if result["extraction_report"]["engine"]["options"]["use_ocr"]:
            self.assertTrue(result["extraction_report"]["pages"][0]["ocr_performed"],
                            "Original bytes must also survive a real mixed-page OCR callback")
        self.assertEqual(old.read_bytes(), b"previous-150dpi-cache-must-stay")
        vector = convert(self.fixture("vector.pdf"), self.base / "vector-assets")
        self.assertTrue(vector["figures"])
        for figure in vector["figures"]:
            info = figure["asset_info"]
            self.assertEqual(info["method"], "sdk_crop")
            self.assertEqual(info["dpi"], 300)
            with Image.open(self.base / "vector-assets" / figure["image"]) as rendered:
                self.assertGreater(rendered.width, 650)

    def test_pdf_opacity_is_not_lost_when_considering_embedded_original_image(self):
        from pubnexus.pdf_markdown_child import _native_image
        encoded = io.BytesIO()
        photo = Image.linear_gradient("L").resize((800, 600)).convert("RGB")
        ImageDraw.Draw(photo).ellipse((100, 100, 600, 500), fill=(190, 80, 45))
        photo.save(encoded, "JPEG", quality=95)
        source = self.base / "transparent-photo.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((35, 80), "An ordinary body paragraph before the transparent photo.")
            page.insert_image(fitz.Rect(40, 150, 360, 390), stream=encoded.getvalue())
            alpha = pdf.get_new_xref()
            pdf.update_object(alpha, "<< /ca 0.35 /CA 0.35 >>")
            resources = int(pdf.xref_get_key(page.xref, "Resources")[1].split()[0])
            pdf.xref_set_key(resources, "ExtGState", f"<< /Faded {alpha} 0 R >>")
            stream = page.get_contents()[-1]
            pdf.update_stream(stream, b"q\n/Faded gs\n" + pdf.xref_stream(stream) + b"\nQ")
            pdf.save(source)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with fitz.open(source) as pdf:
            page = pdf[0]
            box = {"bbox": [40, 150, 360, 390]}
            self.assertIsNone(_native_image(pdf, page, box, fitz))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_clipped_photo_keeps_sdk_crop_and_does_not_reveal_hidden_original_pixels(self):
        from pubnexus.pdf_markdown import available, convert
        if not available():
            self.skipTest("별도 SDK가 없습니다.")
        photo = Image.linear_gradient("L").resize((1600, 1200)).convert("RGB")
        ImageDraw.Draw(photo).ellipse((250, 200, 1050, 1000), fill=(190, 80, 45))
        encoded = io.BytesIO()
        photo.save(encoded, "JPEG", quality=97, subsampling=0)
        source = self.base / "clipped-photo.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=500)
            page.insert_text((35, 80), "An ordinary body paragraph before a clipped calibration image.")
            page.insert_image(fitz.Rect(40, 150, 360, 390), stream=encoded.getvalue())
            stream = page.get_contents()[-1]
            pdf.update_stream(stream, b"q\n40 110 320 240 re\n160 180 80 80 re\nW* n\n"
                              + pdf.xref_stream(stream) + b"\nQ")
            pdf.save(source)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = convert(source, self.base / "assets")
        self.assertTrue(result["figures"])
        self.assertTrue(all(f["asset_info"]["method"] == "sdk_crop" for f in result["figures"]))
        self.assertTrue(all(f["asset_info"]["dpi"] == 300 for f in result["figures"]))
        with Image.open(self.base / "assets" / result["figures"][0]["image"]) as crop:
            pixel = crop.convert("RGB").getpixel((crop.width // 2, crop.height // 2))
            self.assertGreaterEqual(min(pixel), 250, "The clipped centre must remain hidden/white")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

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
