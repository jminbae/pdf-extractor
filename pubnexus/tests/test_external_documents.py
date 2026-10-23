"""외부 로컬 엔진의 공개 계약. 모든 입력은 TEMP 합성 문서이다."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class ExternalDocumentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pubnexus-external-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        environment = patch.dict(os.environ, {"PUBNEXUS_STORE": str(self.base / "cache")})
        environment.start()
        self.addCleanup(environment.stop)

    @unittest.skipUnless(sys.platform == "win32", "Windows venv launcher regression")
    def test_actual_windows_launcher_cancel_releases_owned_descendant_log_handles(self):
        from pubnexus.external_documents import extract_document
        from pubnexus.general_pdf import ExtractionCancelled
        source = self.base / "launcher.rtf"
        source.write_bytes(br"{\rtf1\ansi original}")
        marker = self.base / "owned-worker.pid"
        real_popen = subprocess.Popen
        descendant = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(60)"
        child = ("import subprocess,sys,time;subprocess.Popen([sys.executable,'-B','-c',"
                 + repr(descendant) + ",sys.argv[1]],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));time.sleep(60)")
        def launch(args, **kwargs):
            if Path(str(args[0])).name.lower() in {"taskkill", "taskkill.exe"}:
                return real_popen(args, **kwargs)
            return real_popen([sys.executable, "-B", "-c", child, str(marker)], **kwargs)
        try:
            with patch.dict(os.environ, {"PUBNEXUS_PANDOC": sys.executable}), patch(
                    "pubnexus.external_documents.subprocess.Popen", side_effect=launch):
                with self.assertRaises(ExtractionCancelled):
                    extract_document(source, timeout=10, cancel=marker.exists)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, int(marker.read_text()))
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0,
                                     "The owned real Python descendant must be terminated before returning")
                finally:
                    kernel.CloseHandle(handle)
        finally:
            if marker.is_file():
                subprocess.run([str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                                "/PID", marker.read_text().strip(), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)

    def test_external_output_budget_is_checked_even_after_child_exit(self):
        from pubnexus.documents import DocumentExtractionError
        from pubnexus.external_documents import extract_document
        path = self.base / "large.rtf"
        original = br"{\rtf1\ansi original}"
        path.write_bytes(original)
        class CompletedProcess:
            returncode = 0
            def __init__(self, args, **kwargs):
                if "--version" in args:
                    kwargs["stdout"].write(b"pandoc 3.11\n")
                    kwargs["stdout"].flush()
                else:
                    output = Path(args[args.index("--output") + 1])
                    output.write_bytes(b"x" * 2048)
            def poll(self):
                return 0
        with patch.dict(os.environ, {"PUBNEXUS_PANDOC": sys.executable}), patch(
                "pubnexus.external_documents.subprocess.Popen", CompletedProcess), patch(
                "pubnexus.documents.MAX_TEXT_BYTES", 1024):
            with self.assertRaises(DocumentExtractionError) as raised:
                extract_document(path)
        self.assertEqual(raised.exception.code, "limit")
        self.assertEqual(path.read_bytes(), original)

    def test_external_cancel_and_timeout_stop_only_the_owned_child(self):
        from pubnexus.documents import DocumentExtractionError
        from pubnexus.external_documents import extract_document
        from pubnexus.general_pdf import ExtractionCancelled
        path = self.base / "cancel.rtf"
        path.write_bytes(br"{\rtf1\ansi original}")
        for mode in ("cancel", "timeout"):
            with self.subTest(mode=mode):
                processes = []
                class PrivateProcess:
                    returncode = None
                    pid = 424242
                    def __init__(self, *args, **kwargs):
                        self.killed = self.waited = False
                        processes.append(self)
                    def poll(self):
                        return self.returncode
                    def kill(self):
                        self.killed = True
                        self.returncode = -9
                    def wait(self, timeout):
                        self.waited = True
                        return self.returncode
                with patch.dict(os.environ, {"PUBNEXUS_PANDOC": sys.executable}), patch(
                        "pubnexus.external_documents.subprocess.Popen", PrivateProcess), patch(
                        "pubnexus.external_documents.subprocess.run", side_effect=lambda *a, **kw: processes[-1].kill()):
                    expected = ExtractionCancelled if mode == "cancel" else DocumentExtractionError
                    with self.assertRaises(expected) as raised:
                        extract_document(path, timeout=0.2,
                                         cancel=(lambda: bool(processes)) if mode == "cancel" else None)
                self.assertEqual(len(processes), 1)
                self.assertTrue(processes[0].killed)
                self.assertTrue(processes[0].waited)
                if mode == "timeout":
                    self.assertEqual(raised.exception.code, "timeout")

    def test_pandoc_sandbox_does_not_fetch_remote_or_other_local_files(self):
        from pubnexus.external_documents import extract_document, find_engine
        if find_engine("pandoc") is None:
            self.skipTest("공식 Pandoc 실행 파일이 없습니다.")
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"MUST_NOT_FETCH")
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        secret = self.base / "private.txt"
        secret.write_text("PRIVATE_FILE_MUST_NOT_EMBED", encoding="utf-8")
        path = self.base / "links.ipynb"
        notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
            {"id": "links", "cell_type": "markdown", "metadata": {}, "source": [
                f"Safe text\n\n![remote](http://127.0.0.1:{server.server_port}/a.png)\n\n![local]({secret.as_uri()})"]}]}
        path.write_text(json.dumps(notebook), encoding="utf-8")

        doc = extract_document(path)

        self.assertEqual(requests, [])
        self.assertEqual(doc["figures"], [])
        self.assertNotIn("PRIVATE_FILE_MUST_NOT_EMBED", json.dumps(doc))
        self.assertEqual(secret.read_text(encoding="utf-8"), "PRIVATE_FILE_MUST_NOT_EMBED")
        self.assertTrue(doc["extraction_report"]["warnings"])

    def test_external_missing_engine_and_child_budget_failures_are_explicit(self):
        from pubnexus.documents import DocumentExtractionError
        from pubnexus.external_documents import extract_document, find_engine
        path = self.base / "sample.rtf"
        path.write_bytes(br"{\rtf1\ansi original}")
        with patch.dict(os.environ, {"PUBNEXUS_PANDOC": str(self.base / "missing.exe")}):
            with self.assertRaises(DocumentExtractionError) as raised:
                extract_document(path)
        self.assertEqual(raised.exception.code, "dependency")
        if find_engine("markitdown_python") is None:
            return
        def record(code, data=b""):
            return struct.pack("<HH", code, len(data)) + data
        # Two occupied cells must fail a one-cell engine budget.
        header = record(0x0009, struct.pack("<HH", 2, 0x10))
        dimensions = record(0x0000, struct.pack("<HHHH", 0, 1, 0, 2))
        cells = b"".join(record(0x0004, struct.pack("<HH", 0, col) + b"\0\0\0\x01A") for col in range(2))
        path = self.base / "limited.xls"
        path.write_bytes(header + dimensions + cells + record(0x000A))
        with patch("pubnexus.documents.MAX_CELLS", 1):
            with self.assertRaises(DocumentExtractionError) as raised:
                extract_document(path)
        self.assertEqual(raised.exception.code, "limit")

    def test_actual_markitdown_msg_reads_synthetic_mapi_property_streams(self):
        from pubnexus.external_documents import extract_document, find_engine
        if find_engine("markitdown_python") is None:
            self.skipTest("별도 MarkItDown Python 런타임이 없습니다.")
        streams = {"__substg1.0_0037001F": "합성 메일", "__substg1.0_1000001F": "Synthetic MSG body",
                   "__substg1.0_0C1F001F": "sender@example.test", "__substg1.0_0E04001F": "reader@example.test"}
        streams = {name: value.encode("utf-16-le") for name, value in streams.items()}
        properties = b"\0" * 32
        for name, value in streams.items():
            properties += struct.pack("<IIII", int(name.rsplit("_", 1)[1], 16), 0, len(value), 0)
        streams["__properties_version1.0"] = properties
        original = self._synthetic_compound_file(streams)
        path = self.base / "message.msg"
        path.write_bytes(original)

        doc = extract_document(path)

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(doc["meta"]["title"], "합성 메일")
        self.assertEqual(doc["source_map"]["engine"], "MarkItDown")
        texts = "\n".join(p["text"] for s in doc["body_text"] for p in s["paragraphs"])
        self.assertIn("Synthetic MSG body", texts)
        self.assertIn("sender@example.test", texts)
        self.assertTrue(any("첨부" in w for w in doc["extraction_report"]["warnings"]))

    @staticmethod
    def _synthetic_compound_file(streams):
        # Test-only CFB v3 with a MiniFAT, root mini stream, and balanced directory.
        free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
        names = sorted(streams, key=lambda name: (len(name), name.upper()))
        mini, mini_fat, starts = bytearray(), [], {}
        for name in names:
            data = streams[name]
            starts[name] = len(mini_fat)
            count = (len(data) + 63) // 64
            mini_fat.extend([len(mini_fat) + i + 1 for i in range(count)])
            mini_fat[-1] = end
            mini.extend(data.ljust(count * 64, b"\0"))
        directory_sectors = (len(names) + 4) // 4
        mini_fat_sector = 1 + directory_sectors
        root_sector = mini_fat_sector + 1
        root_sectors = (len(mini) + 511) // 512
        entries = [None] * (len(names) + 1)
        def entry(name, kind, start, size, left=free, right=free, child=free):
            value = bytearray(128)
            encoded = (name + "\0").encode("utf-16-le")
            value[:len(encoded)] = encoded
            struct.pack_into("<HBBIII", value, 64, len(encoded), kind, 1, left, right, child)
            struct.pack_into("<IQ", value, 116, start, size)
            return value
        def tree(indices):
            if not indices:
                return free
            mid = len(indices) // 2
            index = indices[mid]
            name = names[index - 1]
            entries[index] = entry(name, 2, starts[name], len(streams[name]), tree(indices[:mid]), tree(indices[mid + 1:]))
            return index
        child = tree(list(range(1, len(names) + 1)))
        entries[0] = entry("Root Entry", 5, root_sector, len(mini), child=child)
        header = bytearray(512)
        header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
        struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
        struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, mini_fat_sector, 1, end, 0)
        struct.pack_into("<109I", header, 76, 0, *([free] * 108))
        fat = [fat_sector] + [i + 1 for i in range(1, directory_sectors + 1)]
        fat[directory_sectors] = end
        fat.extend([end] + [root_sector + i + 1 for i in range(root_sectors)])
        fat[-1] = end
        return (bytes(header) + struct.pack("<128I", *(fat + [free] * (128 - len(fat))))
                + b"".join(entries).ljust(directory_sectors * 512, b"\0")
                + struct.pack("<128I", *(mini_fat + [free] * (128 - len(mini_fat))))
                + bytes(mini).ljust(root_sectors * 512, b"\0"))

    def test_actual_markitdown_xls_reads_a_real_synthetic_biff_workbook(self):
        from pubnexus.external_documents import extract_document, find_engine
        if find_engine("markitdown_python") is None:
            self.skipTest("별도 MarkItDown Python 런타임이 없습니다.")
        def record(code, data=b""):
            return struct.pack("<HH", code, len(data)) + data
        def label(row, col, value):
            encoded = value.encode("ascii")
            return record(0x0004, struct.pack("<HH", row, col) + b"\0\0\0" + bytes([len(encoded)]) + encoded)
        original = (record(0x0009, struct.pack("<HH", 2, 0x10))
                    + record(0x0000, struct.pack("<HHHH", 0, 2, 0, 2))
                    + label(0, 0, "Name") + label(0, 1, "Value") + label(1, 0, "Synthetic")
                    + record(0x0003, struct.pack("<HH", 1, 1) + b"\0\0\0" + struct.pack("<d", 42.0))
                    + record(0x000A))
        path = self.base / "legacy.xls"
        path.write_bytes(original)

        doc = extract_document(path)

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(doc["source_map"]["engine"], "MarkItDown")
        self.assertEqual(doc["source_map"]["engine_version"], "0.1.9")
        self.assertEqual([c["text"] for c in doc["tables"][0]["cells"]], ["Name", "Value", "Synthetic", "42"])
        self.assertEqual(doc["extraction_report"]["status"], "partial")

    def test_actual_pandoc_notebook_preserves_embedded_output_without_execution(self):
        from PIL import Image
        from pubnexus.external_documents import extract_document, find_engine
        if find_engine("pandoc") is None:
            self.skipTest("공식 Pandoc 실행 파일이 없습니다.")
        image = self.base / "output.png"
        Image.new("RGB", (2, 3), "gold").save(image)
        path = self.base / "sample.ipynb"
        notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
            {"id": "intro", "cell_type": "markdown", "metadata": {}, "source": ["# Notebook title\n", "Original content"]},
            {"id": "code", "cell_type": "code", "metadata": {}, "execution_count": None,
             "source": ['raise RuntimeError("MUST_NOT_EXECUTE")'], "outputs": [
                 {"output_type": "display_data", "metadata": {}, "data": {
                     "image/png": base64.b64encode(image.read_bytes()).decode("ascii"), "text/plain": "stored output"}}]},
        ]}
        original = json.dumps(notebook).encode()
        path.write_bytes(original)

        doc = extract_document(path)

        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(doc["extraction_report"]["notebook_executed"])
        texts = "\n".join(p["text"] for s in doc["body_text"] for p in s["paragraphs"])
        self.assertIn('raise RuntimeError("MUST_NOT_EXECUTE")', texts)
        self.assertEqual(len(doc["figures"]), 1)
        figure = Path(doc["extraction_report"]["asset_base"]) / doc["figures"][0]["image"]
        self.assertEqual(figure.read_bytes(), image.read_bytes())

    def test_actual_pandoc_rtf_preserves_table_and_reports_logical_provenance(self):
        from pubnexus.external_documents import extract_document, find_engine
        if find_engine("pandoc") is None:
            self.skipTest("공식 Pandoc 실행 파일이 없습니다.")
        path = self.base / "sample.rtf"
        original = br"{\rtf1\ansi Synthetic RTF\par\trowd\cellx2000\cellx4000\intbl Left\cell Right\cell\row\pard}"
        path.write_bytes(original)

        doc = extract_document(path)

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(doc["source"], "document_rtf")
        self.assertEqual(doc["source_map"]["coordinate_space"], "logical_document")
        self.assertEqual(doc["source_map"]["engine"], "Pandoc")
        self.assertRegex(doc["source_map"]["engine_version"], r"^pandoc \d+")
        self.assertEqual([c["text"] for c in doc["tables"][0]["cells"]], ["Left", "Right"])
        self.assertTrue(all("page" not in b for b in doc["source_map"]["blocks"]))


if __name__ == "__main__":
    unittest.main()
