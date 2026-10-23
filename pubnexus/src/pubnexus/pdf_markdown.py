"""Thin canonical adapter for an isolated official PyMuPDF 2.0 runtime."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .external_documents import _stop_owned_process
from .general_pdf import ExtractionCancelled
from .schema import Document, Meta

MAX_INPUT = 1024 * 1024 * 1024
MAX_OUTPUT = 128 * 1024 * 1024
MAX_ASSETS = 1024 * 1024 * 1024
MAX_PAGES = 3000
MAX_MEMORY = 2 * 1024 * 1024 * 1024
TIMEOUT = 1800.0
STALL_TIMEOUT = 120.0


class PDFMarkdownError(RuntimeError):
    def __init__(self, message, code="conversion"):
        super().__init__(message)
        self.code = code


def engine_id():
    return "pymupdf-2.0-adapter-1"


def _command():
    if os.environ.get("PUBNEXUS_PDF_ENGINE", "").lower() == "off":
        return None
    worker = os.environ.get("PUBNEXUS_PDF_SDK_WORKER")
    if worker:
        return [str(Path(worker))] if Path(worker).is_file() else None
    roots = [Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "engines",
             Path(sys.executable).parent / "engines"]
    for root in roots:
        candidate = root / "pdf-markdown" / "pdf-markdown-worker.exe"
        if candidate.is_file():
            return [str(candidate)]
    interpreter = os.environ.get("PUBNEXUS_PDF_SDK_PYTHON")
    if not interpreter:
        settings = Path.home() / ".ai-workspace" / "paths.md"
        if settings.is_file():
            match = re.search(r"^cache_root:\s*(.+)$", settings.read_text(encoding="utf-8-sig"), re.M)
            if match:
                candidate = Path(match.group(1).strip()) / "260725 PDF 추출" / "engines" / "pymupdf4llm-runtime" / "Scripts" / "python.exe"
                if candidate.is_file():
                    interpreter = str(candidate)
    if interpreter and Path(interpreter).is_file():
        return [interpreter, "-I", "-B", str(Path(__file__).with_name("pdf_markdown_child.py"))]
    return None


def available():
    return _command() is not None


def _check(cancel):
    if cancel and cancel():
        raise ExtractionCancelled("PDF SDK 추출을 중지했습니다.")


def _sha(path, cancel=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            _check(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _run(command, request, work, progress, cancel):
    output, events = work / "result.json", work / "progress.jsonl"
    environment = os.environ.copy()
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", HF_HUB_OFFLINE="1",
                       TRANSFORMERS_OFFLINE="1", DO_NOT_TRACK="1", SCARF_NO_ANALYTICS="1",
                       HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
                       ALL_PROXY="http://127.0.0.1:9", NO_PROXY="")
    started = last_activity = time.monotonic()
    seen, event_size = 0, 0
    with (work / "stdout.log").open("wb") as stdout, (work / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen([*command, str(request)], stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, shell=False, cwd=str(work),
                                   env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while True:
                _check(cancel)
                now = time.monotonic()
                if now - started > TIMEOUT or now - last_activity > STALL_TIMEOUT:
                    raise PDFMarkdownError("PDF SDK 작업 시간이 초과되었습니다.", "timeout")
                for log in (work / "stdout.log", work / "stderr.log", events):
                    if log.exists() and log.stat().st_size > 2 * 1024 * 1024:
                        raise PDFMarkdownError("PDF SDK 진단 출력 예산을 초과했습니다.", "limit")
                if output.exists() and output.stat().st_size > MAX_OUTPUT:
                    raise PDFMarkdownError("PDF SDK JSON 예산을 초과했습니다.", "limit")
                if events.exists() and events.stat().st_size != event_size:
                    event_size = events.stat().st_size
                    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
                    for line in lines[seen:]:
                        if not line.endswith("\n"):
                            break
                        event = json.loads(line)
                        if progress:
                            progress(event["page"], event["total"], event["stage"])
                        seen += 1
                    last_activity = now
                if sum(p.stat().st_size for p in (work / "images").rglob("*") if p.is_file()) > MAX_ASSETS:
                    raise PDFMarkdownError("PDF SDK 그림 예산을 초과했습니다.", "limit")
                if process.poll() is not None:
                    break
                time.sleep(.05)
        finally:
            _stop_owned_process(process)
    if not output.is_file() or output.stat().st_size > MAX_OUTPUT:
        diagnostic = (work / "stderr.log").read_text(encoding="utf-8", errors="replace")[-2000:]
        raise PDFMarkdownError("PDF SDK 결과를 만들지 못했습니다: " + diagnostic, "conversion")
    record = json.loads(output.read_text(encoding="utf-8"))
    if not record.get("ok"):
        raise PDFMarkdownError(record.get("message", "PDF SDK 변환 실패"), record.get("code", "conversion"))
    if process.returncode:
        raise PDFMarkdownError("PDF SDK 프로세스가 정상 종료하지 않았습니다.", "conversion")
    return record


def _bbox(box, matrix):
    a, b, c, d, e, f = matrix
    points = [(a*x+c*y+e, b*x+d*y+f) for x in (box[0], box[2]) for y in (box[1], box[3])]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _canonical(record, path, digest, asset_dir, work, cancel):
    markdown, chunks, pages = record["markdown"], record["chunks"], record["pages"]
    if (record.get("version") != "2.0" or not isinstance(markdown, str)
            or not isinstance(chunks, list) or not isinstance(pages, list)
            or not 0 < len(pages) <= MAX_PAGES or len(chunks) != len(pages)
            or markdown != "".join(chunk["text"] for chunk in chunks)):
        raise PDFMarkdownError("SDK 문자열/페이지 계약이 일치하지 않습니다.", "invalid_output")
    document = Document(paper_id=digest[:16], source="pdf_sdk", source_file=str(path),
                        meta=Meta(title=record.get("metadata", {}).get("title") or path.stem)).to_dict()
    document.update(scope="general_pdf", sdk_engine="pymupdf-2.0", sdk_markdown=markdown,
                    sdk_blocks=[], asset_base=str(asset_dir))
    offset, figures, tables, blocks, sections, assets, asset_spans = 0, [], [], [], [], {}, []
    for page_number, (chunk, page) in enumerate(zip(chunks, pages), 1):
        _check(cancel)
        if page["page"] != page_number or chunk["metadata"]["page_number"] != page_number:
            raise PDFMarkdownError("SDK 페이지 순서가 올바르지 않습니다.", "invalid_output")
        paragraphs, page_ids = [], []
        for box in chunk["page_boxes"]:
            start, end = box["pos"]
            bounds = box["bbox"]
            if (not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(chunk["text"])
                    or len(bounds) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds)):
                raise PDFMarkdownError("SDK 블록 좌표/문자 범위가 잘못되었습니다.", "invalid_output")
            if start == end:
                continue  # SDK excluded header/footer boxes have empty ranges.
            identifier = f"sdk-p{page_number:06d}-b{box['index']:06d}"
            source = {"page": page_number, "block_id": identifier,
                      "bbox": _bbox(bounds, page["sdk_to_source"]), "method": "pymupdf-2.0"}
            text = chunk["text"][start:end]
            block = {"id": identifier, "start": offset + start, "end": offset + end,
                     "kind": box["class"], "text": text, "source": source,
                     "sdk_bbox": bounds, "sdk_pos": [start, end]}
            blocks.append(block)
            page_ids.append(identifier)
            paragraphs.append({"id": identifier, "text": text, "source": source,
                               "cited_refs": [], "cited_keys": [], "refs_figure": [], "refs_table": []})
            if box["class"] == "table":
                tables.append({"id": identifier, "caption": "", "markdown": text, "source": source})
            if box["class"] not in ("picture", "formula"):
                continue
            generated = f"images/{digest[:16]}/document.pdf-{page_number:04d}-{box['index']:02d}.png"
            match = re.match(r"\n!\[\]\(([^)]+)\)\n", text)
            if (work / generated).is_file():
                if not match or match.group(1) != generated:
                    raise PDFMarkdownError("SDK 그림 주소의 정확한 문자 범위를 확인하지 못했습니다.", "invalid_output")
            if match:
                relative = match.group(1)
                candidate = (work / relative).resolve()
                expected = work / "images" / digest[:16]
                if (Path(relative).is_absolute() or not candidate.is_relative_to(expected.resolve())
                        or not candidate.is_file() or candidate.is_symlink()):
                    raise PDFMarkdownError("SDK 그림이 격리 자산 범위를 벗어났거나 없습니다.", "invalid_output")
                assets[relative] = candidate
                asset_spans.append({"image": relative, "start": offset + start + match.start(1),
                                    "end": offset + start + match.end(1)})
                figures.append({"id": identifier + f"-image-{len(figures):04d}", "caption": "",
                                "image": relative, "source": source})
        page["block_ids"] = page_ids
        sections.append({"path": [f"Page {page_number}"], "section_type": "other", "paragraphs": paragraphs})
        offset += len(chunk["text"])
    for relative, original in assets.items():
        _check(cancel)
        target = (asset_dir / relative).resolve()
        if not target.is_relative_to(asset_dir.resolve()):
            raise PDFMarkdownError("SDK 저장 자산 경계 오류입니다.", "invalid_output")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _sha(target, cancel) != _sha(original, cancel):
                raise PDFMarkdownError("기존 SDK 그림과 새 결과가 충돌합니다.", "asset_conflict")
        else:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".sdk-", delete=False) as staged:
                temporary = Path(staged.name)
                try:
                    with original.open("rb") as stream:
                        shutil.copyfileobj(stream, staged)
                    staged.flush()
                    os.fsync(staged.fileno())
                except BaseException:
                    staged.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                # Content-derived directory; never silently overwrite an existing asset.
                os.link(temporary, target)
            except FileExistsError:
                if _sha(target, cancel) != _sha(original, cancel):
                    raise PDFMarkdownError("SDK 그림 저장 중 충돌했습니다.", "asset_conflict")
            finally:
                temporary.unlink(missing_ok=True)
    document.update(body_text=sections, tables=tables, figures=figures, sdk_blocks=blocks, sdk_assets=asset_spans)
    document["source_map"] = {"schema_version": "1.0", "coordinate_space": "pymupdf_unrotated_points",
                              "offset_unit": "unicode_codepoint", "engine": "PyMuPDF", "engine_version": "2.0",
                              "source_sha256": digest, "pages": pages,
                              "blocks": [{"id": b["id"], **b["source"], "start": b["start"], "end": b["end"]} for b in blocks]}
    warnings = [f"{p['page']}쪽 원문 문자가 SDK의 머리말/꼬리말 제외 후 남지 않았습니다 — 원본 확인 필요"
                for p in pages if p["status"] == "sdk_empty"]
    warnings.extend(f"{p['page']}쪽 SDK OCR에서 문자를 인식하지 못했습니다 — 그림/스캔 원본 확인 필요"
                    for p in pages if p["status"] == "ocr_empty")
    warnings.extend(f"{p['page']}쪽은 SDK가 OCR 대상으로 선택하지 않아 문자가 없습니다 — 그림/스캔 원본 확인 필요"
                    for p in pages if p["status"] == "ocr_required")
    ocr_performed = any(p["ocr_performed"] for p in pages)
    if ocr_performed:
        warnings.append("SDK에서 로컬 Tesseract OCR을 실행했습니다 — 원문 대조 필요")
    document["extraction_report"] = {"version": 1, "status": "partial" if warnings else "complete", "pages": pages,
        "warnings": warnings, "used_fallback": False, "asset_base": str(asset_dir), "ocr_performed": ocr_performed,
        "engine": {"name": "PyMuPDF Document.to_markdown", "version": "2.0", "identity": engine_id(),
                   "options": record["options"], "network": record.get("network")},
        "source_map_status": "sdk_page_boxes", "source_sha256": digest}
    return document


def convert(path, asset_dir, progress=None, cancel=None):
    """Preserve SDK Markdown verbatim; offsets count Unicode code points, not bytes/UTF16.

    Without verified local models, textless nonblank pages raise code='ocr_required'.
    With models, the SDK's published Tesseract callback is used and OCR stays partial.
    No old references/body are accepted or merged by this adapter.
    """
    _check(cancel)
    command = _command()
    if command is None:
        raise PDFMarkdownError("공식 PDF SDK 엔진이 없습니다. 별도 PDF 엔진 설치를 확인해 주세요.", "unavailable")
    path, asset_dir = Path(path).resolve(), Path(asset_dir).resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise PDFMarkdownError("읽을 수 있는 PDF 파일이 필요합니다.", "unsupported")
    if path.stat().st_size > MAX_INPUT:
        raise PDFMarkdownError("PDF 입력 크기 예산을 초과했습니다.", "limit")
    before = path.stat()
    digest = _sha(path, cancel)
    with tempfile.TemporaryDirectory(prefix="pubnexus-pdf-sdk-") as temporary:
        work = Path(temporary)
        private = work / "document.pdf"
        shutil.copyfile(path, private)
        from .local_ocr import discover
        provider = discover()
        models = []
        if provider:
            (work / "models").mkdir()
            for model in provider["models"]:
                _check(cancel)
                target = work / "models" / (model["language"] + ".traineddata")
                shutil.copyfile(model["path"], target)
                if _sha(target, cancel) != model["sha256"]:
                    raise PDFMarkdownError("OCR 모델 복사 무결성 확인에 실패했습니다.", "unavailable")
                models.append({"language": model["language"], "sha256": model["sha256"]})
        request = work / "request.json"
        request.write_text(json.dumps({"source": str(private), "progress": str(work / "progress.jsonl"),
            "sha256": digest, "max_pages": MAX_PAGES, "max_text_bytes": MAX_OUTPUT // 4,
            "max_memory_bytes": MAX_MEMORY, "ocr_models": models}), encoding="utf-8")
        record = _run(command, request, work, progress, cancel)
        if path.stat().st_mtime_ns != before.st_mtime_ns or _sha(path, cancel) != digest:
            raise PDFMarkdownError("변환 중 원본 PDF가 변경되었습니다.", "source_changed")
        result = _canonical(record, path, digest, asset_dir, work, cancel)
    _check(cancel)
    return result
