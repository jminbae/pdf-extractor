"""MuPDF 내장 Tesseract OCR의 합성 스캔/격리 프로세스 공개 계약."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class LocalOCRTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pubnexus-ocr-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_missing_or_changed_models_are_not_discovered_and_worker_reports_dependency(self):
        from pubnexus.local_ocr import discover, worker_cli
        (self.base / "eng.traineddata").write_bytes(b"changed or invalid model")
        with patch.dict(os.environ, {"PUBNEXUS_TESSDATA": str(self.base)}):
            self.assertIsNone(discover())
        request = self.base / "request.json"
        request.write_text(json.dumps({"language": "eng", "width": 1, "height": 1}))
        self.assertEqual(worker_cli(request), 2)
        result = json.loads((self.base / "result.json").read_text(encoding="utf-8"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "dependency")
        self.assertEqual((self.base / "eng.traineddata").read_bytes(), b"changed or invalid model")

    def test_worker_checks_png_dimensions_before_decoding_the_image(self):
        from pubnexus.local_ocr import discover, worker_cli
        provider = discover()
        if provider is None:
            self.skipTest("검증된 로컬 tessdata 모델이 없습니다.")
        models = self.base / "models"
        models.mkdir()
        for model in provider["models"]:
            shutil.copyfile(model["path"], models / Path(model["path"]).name)
        request = self.base / "request.json"
        request.write_text(json.dumps({"language": provider["language"], "width": 1, "height": 1}))
        # Oversized PNG IHDR, deliberately no payload: reject before a decoder allocates pixels.
        (self.base / "page.png").write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 50000, 50000))
        self.assertEqual(worker_cli(request), 2)
        result = json.loads((self.base / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["code"], "limit")

    def test_cancel_and_timeout_stop_the_owned_ocr_worker(self):
        from pubnexus.local_ocr import LocalOCRError, discover, extract_page
        from pubnexus.general_pdf import ExtractionCancelled
        provider = discover()
        if provider is None:
            self.skipTest("검증된 로컬 tessdata 모델이 없습니다.")
        with fitz.open() as document:
            page = document.new_page(width=100, height=100)
            for mode in ("cancel", "timeout"):
                with self.subTest(mode=mode):
                    processes = []
                    class Process:
                        pid, returncode = 424242, None
                        def __init__(self, *args, **kwargs):
                            self.killed = self.waited = False
                            self.assertions = kwargs
                            processes.append(self)
                        def poll(self):
                            return self.returncode
                        def kill(self):
                            self.killed, self.returncode = True, -9
                        def wait(self, timeout):
                            self.waited = True
                            return self.returncode
                    with patch("pubnexus.local_ocr.subprocess.Popen", Process), patch(
                            "pubnexus.local_ocr.subprocess.run", side_effect=lambda *a, **kw: processes[-1].kill()):
                        expected = ExtractionCancelled if mode == "cancel" else LocalOCRError
                        with self.assertRaises(expected) as raised:
                            extract_page(page, provider, timeout=0.2,
                                         cancel=(lambda: bool(processes)) if mode == "cancel" else None)
                    self.assertEqual(len(processes), 1)
                    self.assertTrue(processes[0].killed and processes[0].waited)
                    self.assertEqual(processes[0].assertions["env"]["PATH"], "")
                    if mode == "timeout":
                        self.assertEqual(raised.exception.code, "timeout")

    def test_actual_builtin_ocr_has_bounded_unrotated_word_locations_without_an_executable(self):
        from pubnexus.local_ocr import discover, extract_page
        provider = discover()
        if provider is None:
            self.skipTest("검증된 로컬 tessdata 모델이 없습니다(자동 다운로드하지 않음).")
        image = Image.new("RGB", (1200, 300), "white")
        drawing = ImageDraw.Draw(image)
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 65)
        drawing.text((45, 90), "LOCAL OCR CHECK 7391", font=font, fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        path = self.base / "scan.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=576, height=144)
            page.insert_image(page.rect, stream=buffer.getvalue())
            page.set_rotation(90)
            pdf.save(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with fitz.open(path) as pdf:
            page = pdf[0]
            self.assertFalse(page.get_text().strip())
            with patch.dict(os.environ, {"PATH": ""}):
                paragraphs, mapped, warnings = extract_page(page, provider)
            self.assertEqual(page.rotation, 90)

        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertIn("7391", " ".join(p["text"] for p in paragraphs))
        self.assertEqual([p["id"] for p in paragraphs], [b["id"] for b in mapped])
        self.assertTrue(warnings, "OCR accuracy limitations must remain visible")
        for paragraph in paragraphs:
            source = paragraph["source"]
            self.assertEqual(source["page"], 1)
            self.assertEqual(source["method"], "tesseract")
            self.assertIsNone(source["confidence"])
            self.assertTrue(source["words"])
            for word in source["words"]:
                x0, y0, x1, y1 = word["bbox"]
                self.assertTrue(0 <= x0 < x1 <= 576 and 0 <= y0 < y1 <= 144)
                self.assertIsNone(word["confidence"])
        json.dumps(provider)


if __name__ == "__main__":
    unittest.main()
