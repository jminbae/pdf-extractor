"""Isolated official PyMuPDF 2.0 worker; no application imports or layout repair."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading


class WorkerError(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _event(path, page, total, stage="sdk_layout"):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"page": page, "total": total, "stage": stage}) + "\n")


class _SDKProgress:
    """Observe the pinned SDK's public show_progress output, without altering it."""
    def __init__(self, path, total):
        self.path, self.total, self.buffer, self.last = path, total, "", -1

    def write(self, text):
        self.buffer = (self.buffer + text)[-512:]
        for match in re.finditer(r"\(\s*(\d+)/(\d+)\)", self.buffer):
            current, total = map(int, match.groups())
            # SDK prints before processing the current page; only previous pages are done.
            completed = max(0, current - 1)
            if total == self.total and completed > self.last:
                self.last = completed
                _event(self.path, completed, total)
        return len(text)

    def flush(self):
        pass


def _convert(request):
    # Also covers import-time Python networking; native SDK conversion is local.
    def audit(event, arguments):
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen",
                     "os.system", "os.posix_spawn"}:
            raise PermissionError("PDF SDK worker forbids network and child processes")
    sys.addaudithook(audit)
    models = request.get("ocr_models") or []
    if models:
        directory = Path(request["source"]).parent / "models"
        for model in models:
            if model["language"] not in ("kor", "eng") or _sha(directory / (model["language"] + ".traineddata")) != model["sha256"]:
                raise WorkerError("OCR 모델의 무결성 확인에 실패했습니다.", "unavailable")
        os.environ["TESSDATA_PREFIX"] = str(directory)
    import pymupdf
    if pymupdf.VersionBind != "2.0":
        raise WorkerError("공식 PyMuPDF 2.0 전용 엔진이 필요합니다.", "unavailable")
    source = Path(request["source"])
    if _sha(source) != request["sha256"]:
        raise WorkerError("격리한 PDF의 무결성 확인에 실패했습니다.", "source_changed")
    work = source.parent
    image_path = "images/" + request["sha256"][:16]
    (work / image_path).mkdir(parents=True, exist_ok=True)
    progress = Path(request["progress"])
    with pymupdf.open(source) as pdf:
        if pdf.needs_pass:
            raise WorkerError("암호가 필요한 PDF입니다.", "encrypted")
        if not 0 < len(pdf) <= request["max_pages"]:
            raise WorkerError("PDF 페이지 예산을 초과했습니다.", "limit")
        pages, scan_pages = [], []
        _event(progress, 0, len(pdf), "sdk_layout")
        for page in pdf:
            if (max(page.rect.width, page.rect.height) > 20000
                    or page.rect.width * page.rect.height * (150 / 72) ** 2 > 32_000_000):
                raise WorkerError("PDF 페이지 크기 예산을 초과했습니다.", "limit")
            has_text = bool(page.get_text().strip())
            blank = False
            if not has_text:
                # A white raster is evidence of a blank page even if a white image is embedded.
                scale = min(.5, 256 / max(page.rect.width, page.rect.height, 1))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                      colorspace=pymupdf.csRGB, alpha=False)
                blank = bool(pix.samples) and min(pix.samples) >= 254
                if not blank:
                    scan_pages.append(page.number + 1)
            pages.append({"page": page.number + 1, "width": page.cropbox.width,
                          "height": page.cropbox.height, "rotation": page.rotation,
                          "sdk_to_source": list(page.derotation_matrix),
                          "status": "text_extracted" if has_text else "blank" if blank else "ocr_required",
                          "ocr_performed": False, "blank_verified": blank})
        if scan_pages and not models:
            raise WorkerError("문자가 없는 비백지 페이지는 기존 OCR 경로가 필요합니다: "
                              + ", ".join(map(str, scan_pages[:30])), "ocr_required")
        options = dict(header=False, footer=False, page_separators=False, write_images=True,
                       image_path=image_path, use_ocr=bool(models), page_chunks=True, show_progress=True)
        if models:
            from pymupdf4llm.ocr.tesseract_api import exec_ocr
            language = "+".join(model["language"] for model in models)
            def tracked_ocr(page, **kwargs):
                info = pages[page.number]
                if info["blank_verified"]:
                    return
                _event(progress, page.number, len(pdf), "sdk_ocr")
                # Published SDK callback and built-in Tesseract only; no extra OCR framework.
                kwargs["dpi"] = min(kwargs.get("dpi", 150), 150,
                                    int(72 * (8_000_000 / max(page.rect.get_area(), 1)) ** .5))
                exec_ocr(page, **kwargs)
                info["ocr_performed"] = True
                info["ocr_method"] = "tesseract"
                info["ocr_language"] = language
                if info["status"] == "ocr_required":
                    info["status"] = "ocr_extracted" if page.get_text().strip() else "ocr_empty"
            options.update(ocr_function=tracked_ocr, ocr_language=language, ocr_dpi=150)
        original_stdout = sys.stdout
        sys.stdout = _SDKProgress(progress, len(pdf))
        try:
            chunks = pdf.to_markdown(**options)
        finally:
            sys.stdout = original_stdout
        # These are already serialized SDK Markdown page strings. No text reconstruction.
        markdown = "".join(chunk["text"] for chunk in chunks)
        if len(markdown.encode("utf-8")) > request["max_text_bytes"]:
            raise WorkerError("SDK Markdown 출력 예산을 초과했습니다.", "limit")
        for info, chunk in zip(pages, chunks):
            if info["status"] in ("text_extracted", "ocr_extracted") and not chunk["text"].strip():
                info["recognized_status_before_sdk_filter"] = info["status"]
                info["status"] = "sdk_empty"
        _event(progress, len(pdf), len(pdf), "sdk_complete")
        if models:
            options["ocr_function"] = "pymupdf4llm.ocr.tesseract_api.exec_ocr (tracked)"
        return {"ok": True, "version": pymupdf.VersionBind, "markdown": markdown,
                "chunks": chunks, "pages": pages, "metadata": pdf.metadata,
                "options": options, "network": "Python socket/DNS/process audit blocked"}


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return 2
    request_path = Path(arguments[0]).resolve()
    output = request_path.parent / "result.json"
    try:
        if request_path.stat().st_size > 16384:
            raise WorkerError("잘못된 SDK 요청입니다.", "limit")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        for key in ("source", "progress"):
            if Path(request[key]).resolve().parent != request_path.parent:
                raise WorkerError("SDK 작업 경로가 격리 폴더 밖에 있습니다.", "invalid_output")
        os.chdir(request_path.parent)
        # This PID is the actual Python interpreter, never the venv launcher.
        import psutil
        stop = threading.Event()
        def memory_guard():
            process = psutil.Process(os.getpid())
            while not stop.wait(.05):
                if process.memory_info().rss > request["max_memory_bytes"]:
                    temporary = output.with_suffix(".limit.json")
                    temporary.write_text(json.dumps({"ok": False, "code": "limit",
                        "message": "PDF SDK 전용 프로세스 메모리 예산 초과"}), encoding="utf-8")
                    os.replace(temporary, output)
                    os._exit(65)
        threading.Thread(target=memory_guard, daemon=True).start()
        try:
            result = _convert(request)
        finally:
            stop.set()
    except Exception as error:
        result = {"ok": False, "code": getattr(error, "code", "unavailable" if isinstance(
            error, (ImportError, ModuleNotFoundError)) else "conversion"),
                  "message": f"{type(error).__name__}: {error}"[:2000]}
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 128 * 1024 * 1024:
        encoded = json.dumps({"ok": False, "code": "limit", "message": "SDK JSON 예산 초과"}).encode()
    output.write_bytes(encoded)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
