"""Native PDF preservation guard; never applies paper-specific deletion rules.

The existing paper result remains authoritative when its sampled source text is
present. Otherwise preserve native page blocks, including short/repeated text.
Native ruled tables and embedded images are preserved only on the general path.
Optional local OCR is explicit and never a reading-order or accuracy proof.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import fitz

from . import store, utils

COORDINATE_SPACE = "pymupdf_unrotated_points"
_TABLE_LOCK = threading.Lock()  # PyMuPDF table.extract uses module-global CHARS.
_CACHE_VERSION = 2


class ExtractionCancelled(RuntimeError):
    """The caller must not save/export a cancelled extraction as success."""


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel():
        raise ExtractionCancelled("PDF 추출이 취소되었습니다")


def _notify(progress, page: int, total: int, stage: str, cancel) -> None:
    _check_cancel(cancel)
    if progress is not None:
        progress(page, total, stage)
    _check_cancel(cancel)


def _cache_location(path, tesseract):
    identity = store.file_sha1(path)
    if isinstance(tesseract, dict):
        settings = {"cache_version": _CACHE_VERSION, "engine": fitz.VersionBind,
                    "table_strategy": "lines_strict", "local_ocr": tesseract,
                    "render_pixels": 8_000_000, "render_max_side": 4096}
        key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode('utf-8')).hexdigest()[:16]
        return store.root() / 'pdf-pages' / identity[:2] / identity / key, key
    try:
        executable_mtime = Path(tesseract).stat().st_mtime_ns if tesseract else None
    except OSError:
        executable_mtime = None
    model_roots = []
    if os.environ.get("TESSDATA_PREFIX"):
        prefix = Path(os.environ["TESSDATA_PREFIX"])
        model_roots.extend([prefix, prefix / "tessdata"])
    if tesseract:
        executable_folder = Path(tesseract).resolve().parent
        model_roots.extend([executable_folder / "tessdata", executable_folder.parent / "share" / "tessdata"])
    models = []
    for folder in set(model_roots):
        for model in sorted(folder.glob("*.traineddata")):
            try:
                stat = model.stat()
                models.append((str(model.resolve()), stat.st_size, stat.st_mtime_ns))
            except OSError:
                continue
    settings = {"cache_version": _CACHE_VERSION, "engine": fitz.VersionBind,
                "table_strategy": "lines_strict", "ocr": tesseract,
                "ocr_mtime": executable_mtime, "tessdata": os.environ.get("TESSDATA_PREFIX"),
                "language_models": sorted(models), "language_preference": "kor+eng-or-installed",
                "render_pixels": 8_000_000, "render_max_side": 4096, "ocr_psm": 3}
    key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return store.root() / "pdf-pages" / identity[:2] / identity / key, key


def _read_checkpoint(directory, key, page, kind):
    path = directory / f"{kind}-p{page:06d}.json"
    try:
        if path.stat().st_size > 16_000_000:
            return None
        data = utils.read_json(path)
        if (data.get("key") != key or data.get("page") != page
                or data.get("version") != _CACHE_VERSION or data.get("kind") != kind):
            return None
        for paragraph in data["paragraphs"]:
            if (not isinstance(paragraph["text"], str) or paragraph["source"]["page"] != page
                    or len(paragraph["source"]["bbox"]) != 4):
                return None
        if kind == "native" and data["info"]["status"] not in ("text_extracted", "ocr_extracted", "blank", "ocr_empty"):
            return None
        if kind == "media":
            for asset in data["assets"]:
                if Path(asset["name"]).name != asset["name"]:
                    return None
                cached = directory / f"assets-p{page:06d}" / asset["name"]
                if store.file_sha1(cached) != asset["sha1"]:
                    return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _checkpoint(directory, key, page, kind, payload):
    try:
        utils.write_json(directory / f"{kind}-p{page:06d}.json",
                         {"version": _CACHE_VERSION, "key": key, "page": page,
                          "kind": kind, **payload})
        return True
    except OSError:
        return False


def _copy_asset(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".asset-", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _local_command(args, cancel, timeout=45.0) -> str:
    """Run only the explicitly located local OCR executable, with bounded wait."""
    _check_cancel(cancel)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + timeout
    try:
        while True:
            _check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise TimeoutError("로컬 OCR 제한시간 초과")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        _check_cancel(cancel)
        if process.returncode:
            raise RuntimeError((stderr or f"Tesseract exit {process.returncode}")[:500])
        return stdout
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def _pixmap(page, clip=None):
    rect = fitz.Rect(clip) * page.rotation_matrix if clip is not None else page.rect
    scale = min(150 / 72, math.sqrt(8_000_000 / max(rect.get_area(), 1)),
                4096 / max(rect.width, rect.height, 1))
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect if clip is not None else None,
                           colorspace=fitz.csRGB, alpha=False)


def _ocr_page(page, executable, cancel, config):
    if isinstance(executable, dict):
        from .local_ocr import extract_page
        return extract_page(page, executable, cancel=cancel)
    warnings = []
    if "language" not in config:
        listing = _local_command([executable, "--list-langs"], cancel, timeout=10)
        installed = {line.strip() for line in listing.splitlines()[1:] if line.strip()}
        preferred = [language for language in ("kor", "eng") if language in installed]
        if not preferred:
            preferred = sorted(installed - {"osd"})[:1]
        if not preferred:
            raise RuntimeError("Tesseract 언어 모델 없음")
        config["language"] = "+".join(preferred)
        if "kor" not in installed:
            warnings.append("한국어 Tesseract 모델 없음 — 설치된 언어만 OCR")
    with tempfile.TemporaryDirectory(prefix="pubnexus-ocr-") as scratch:
        pix = _pixmap(page)
        image = Path(scratch) / "page.png"
        pix.save(str(image))
        output = _local_command([executable, str(image), "stdout", "-l", config["language"],
                                 "--psm", "3", "tsv"], cancel)
        groups = {}
        for word in csv.DictReader(io.StringIO(output), delimiter="\t"):
            if word.get("level") != "5" or not (word.get("text") or "").strip():
                continue
            key = tuple(word.get(name) for name in ("block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append(word)
        paragraphs, mapped = [], []
        for index, words in enumerate(groups.values(), 1):
            left = min(float(word["left"]) for word in words)
            top = min(float(word["top"]) for word in words)
            right = max(float(word["left"]) + float(word["width"]) for word in words)
            bottom = max(float(word["top"]) + float(word["height"]) for word in words)
            rect = fitz.Rect(left * page.rect.width / pix.width, top * page.rect.height / pix.height,
                             right * page.rect.width / pix.width, bottom * page.rect.height / pix.height)
            rect = rect * page.derotation_matrix
            block_id = f"pdf-p{page.number + 1:06d}-ocr{index:06d}"
            origin = {"page": page.number + 1, "block_id": block_id, "bbox": list(rect),
                      "method": "tesseract", "language": config["language"],
                      "confidence": round(sum(float(word["conf"]) for word in words) / len(words), 2)}
            origin["words"] = []
            for word in words:
                word_rect = fitz.Rect(float(word["left"]) * page.rect.width / pix.width,
                                      float(word["top"]) * page.rect.height / pix.height,
                                      (float(word["left"]) + float(word["width"])) * page.rect.width / pix.width,
                                      (float(word["top"]) + float(word["height"])) * page.rect.height / pix.height)
                origin["words"].append({"text": word["text"], "bbox": list(word_rect * page.derotation_matrix),
                                        "confidence": float(word["conf"])})
            paragraphs.append({"id": block_id, "text": " ".join(word["text"] for word in words),
                               "source": origin, "cited_refs": [], "cited_keys": [],
                               "refs_figure": [], "refs_table": []})
            mapped.append({"id": block_id, **origin})
    return paragraphs, mapped, warnings


def _fold(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def _body_text(doc: dict) -> str:
    return "\n".join(p.get("text") or "" for section in doc.get("body_text") or []
                     for p in section.get("paragraphs") or [])


def _represented_text(doc: dict) -> str:
    parts = [_body_text(doc), doc.get("abstract") or ""]
    meta = doc.get("meta") or {}
    parts.extend(str(meta.get(key) or "") for key in ("title", "journal", "year", "doi"))
    parts.extend(str(author) for author in meta.get("authors") or [])
    for section in doc.get("body_text") or []:
        parts.extend(section.get("path") or [])
    for table in doc.get("tables") or []:
        parts.extend([table.get("caption") or "", table.get("markdown") or ""])
    parts.extend(figure.get("caption") or "" for figure in doc.get("figures") or [])
    for reference in doc.get("references") or []:
        parts.extend([reference.get("raw") or "", reference.get("title") or ""])
    return _fold("\n".join(parts))


def _page_blocks(page) -> list[dict]:
    # Exclude embedded image bytes from the temporary dict, not native text.
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    return [block for block in page.get_text("dict", flags=flags)["blocks"]
            if block.get("type") == 0]


def _visually_blank(page) -> bool:
    """Conservative bounded raster proof for textless, image-free white pages."""
    try:
        if page.get_image_info():
            return False  # An unreadable scan is not evidence of a blank page.
        scale = min(.5, 256/max(page.rect.width, page.rect.height, 1))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
        return bool(pix.samples) and min(pix.samples) >= 254
    except Exception:
        return False


def _read_native_page(pdf, page_index: int) -> tuple[dict, list[dict], list[dict]]:
    page = pdf.load_page(page_index)
    page_number = page_index + 1
    info = {"page": page_number, "width": float(page.cropbox.width),
            "height": float(page.cropbox.height), "rotation": int(page.rotation), "block_ids": []}
    paragraphs, mapped_blocks = [], []
    for block_index, block in enumerate(_page_blocks(page)):
        # Retain original line and span text; no whitespace cleanup or deduplication.
        text = "\n".join("".join(span.get("text", "") for span in line.get("spans", []))
                         for line in block.get("lines", []))
        if not text.strip():
            continue
        block_id = f"pdf-p{page_number:06d}-b{block_index:06d}"
        origin = {"page": page_number, "block_id": block_id,
                  "bbox": [float(value) for value in block["bbox"]]}
        paragraphs.append({"id": block_id, "text": text, "source": origin,
                           "cited_refs": [], "cited_keys": [], "refs_figure": [], "refs_table": []})
        info["block_ids"].append(block_id)
        mapped_blocks.append({"id": block_id, **origin})
    info["status"] = "text_extracted" if paragraphs else "no_text"
    return info, paragraphs, mapped_blocks


def _native_pages(path: Path, *, cancel=None, progress=None, tesseract=None,
                  cache_dir=None, cache_key=None) -> tuple[list[dict], dict, list[dict], list[str]]:
    sections, mapped_blocks, pages, warnings = [], [], [], []
    ocr_config = {}
    _check_cancel(cancel)
    with fitz.open(utils.long_path(path)) as pdf:
        if pdf.needs_pass:
            raise ValueError("암호가 필요한 PDF입니다")
        for page_index in range(pdf.page_count):
            _check_cancel(cancel)
            page_number = page_index + 1
            cached = _read_checkpoint(cache_dir, cache_key, page_number, "native") if cache_dir else None
            if cached:
                info = cached["info"]
                info["cache_hit"] = True
                pages.append(info)
                mapped_blocks.extend(cached["blocks"])
                warnings.extend(cached.get("warnings", []))
                if cached["paragraphs"]:
                    sections.append({"path": [f"Page {page_number}"], "section_type": "other",
                                     "paragraphs": cached["paragraphs"]})
                _notify(progress, page_number, pdf.page_count, "native", cancel)
                continue
            for attempt in (1, 2):
                try:
                    warning_start = len(warnings)
                    if attempt == 1:
                        info, paragraphs, blocks = _read_native_page(pdf, page_index)
                    else:
                        # Retry only the failed page with a fresh native handle.
                        with fitz.open(utils.long_path(path)) as retry_pdf:
                            info, paragraphs, blocks = _read_native_page(retry_pdf, page_index)
                    info["attempts"] = attempt
                    if not paragraphs and _visually_blank(pdf.load_page(page_index)):
                        info.update(status="blank", blank_verified=True, ocr_performed=False, ocr_outcome="not_needed")
                    if (paragraphs or info["status"] == "blank") and cache_dir:
                        _checkpoint(cache_dir, cache_key, page_number, "native",
                                    {"info": info, "paragraphs": paragraphs, "blocks": blocks, "warnings": []})
                    _notify(progress, page_number, pdf.page_count, "native", cancel)
                    if not paragraphs and info["status"] != "blank":
                        if tesseract:
                            _notify(progress, page_number, pdf.page_count, "ocr", cancel)
                            info["ocr_performed"] = True
                            try:
                                paragraphs, blocks, ocr_warnings = _ocr_page(
                                    pdf.load_page(page_index), tesseract, cancel, ocr_config)
                                warnings.extend(ocr_warnings)
                                if paragraphs:
                                    info["status"] = "ocr_extracted"
                                    info["ocr_outcome"] = "text"
                                    info["block_ids"] = [paragraph["id"] for paragraph in paragraphs]
                                else:
                                    info.update(status="ocr_empty", ocr_outcome="empty")
                                    warnings.append(f"{page_number}쪽 OCR 실행했으나 문자를 인식하지 못했습니다 — 원본 확인 필요")
                            except ExtractionCancelled:
                                raise
                            except Exception as error:
                                info.update(status="ocr_failed", ocr_outcome="failed")
                                warnings.append(f"{page_number}쪽 Tesseract OCR 실패 — {type(error).__name__}: {error}")
                        else:
                            info.update(ocr_performed=False, ocr_outcome="not_run")
                            warnings.append(f"{page_number}쪽 Tesseract 미설치/검색불가 — OCR 미실행")
                    if info["status"] in ("ocr_extracted", "ocr_empty") and cache_dir:
                        _checkpoint(cache_dir, cache_key, page_number, "native",
                                    {"info": info, "paragraphs": paragraphs, "blocks": blocks,
                                     "warnings": warnings[warning_start:]})
                    pages.append(info)
                    mapped_blocks.extend(blocks)
                    if paragraphs:
                        sections.append({"path": [f"Page {page_number}"], "section_type": "other",
                                         "paragraphs": paragraphs})
                    break
                except ExtractionCancelled:
                    raise
                except Exception as error:
                    if attempt == 2:
                        message = f"{type(error).__name__}: {error}"
                        pages.append({"page": page_number, "status": "failed", "attempts": attempt,
                                      "block_ids": [], "error": message})
                        warnings.append(f"{page_number}쪽 추출 실패 — {message}")
    source_map = {"schema_version": "1.0", "coordinate_space": COORDINATE_SPACE,
                  "engine": "PyMuPDF", "engine_version": fitz.VersionBind,
                  "pages": pages, "blocks": mapped_blocks}
    return sections, source_map, pages, warnings


def _table_markup(table) -> tuple[str, list[dict]]:
    """Copy geometry/text while the table's Page and global CHARS are valid."""
    values = table.extract()
    if not any(str(value or "").strip() for row in values for value in row):
        raise ValueError("표 셀의 native 문자가 없습니다 — 빈 격자 또는 스캔표")
    text_by_box = {tuple(round(float(value), 3) for value in box): values[r][c] or ""
                   for r, row in enumerate(table.rows) for c, box in enumerate(row.cells)
                   if box is not None}
    boxes = [tuple(round(float(value), 3) for value in box) for box in table.cells if box is not None]
    xs = sorted({value for box in boxes for value in (box[0], box[2])})
    ys = sorted({value for box in boxes for value in (box[1], box[3])})
    if len(xs) < 2 or len(ys) < 2 or (len(xs) - 1) * (len(ys) - 1) > 10_000:
        raise ValueError("표 격자가 너무 크거나 불명확합니다")
    cells, occupied = [], set()
    for box in boxes:
        row, col = ys.index(box[1]), xs.index(box[0])
        rowspan, colspan = ys.index(box[3]) - row, xs.index(box[2]) - col
        area = {(r, c) for r in range(row, row + rowspan) for c in range(col, col + colspan)}
        if not area or area & occupied:
            raise ValueError("표 셀 겹침/빈 영역")
        occupied.update(area)
        cells.append({"row": row, "col": col, "row_span": rowspan, "col_span": colspan,
                      "bbox": list(box), "text": text_by_box.get(box, "")})
    rows, cols = len(ys) - 1, len(xs) - 1
    if len(occupied) != rows * cols:
        raise ValueError("표 셀 경계를 확정하지 못했습니다")
    cells.sort(key=lambda cell: (cell["row"], cell["col"]))
    merged = any(cell["row_span"] != 1 or cell["col_span"] != 1 for cell in cells)
    external = bool(table.header.external)
    escaped = lambda text: html.escape(str(text)).replace("\n", "<br>")
    if not merged and not external:
        lines = []
        for row in range(rows):
            line = [escaped(cell["text"]).replace("|", "&#124;").replace("\\", "&#92;")
                    for cell in cells if cell["row"] == row]
            lines.append("| " + " | ".join(line) + " |")
            if row == 0:
                lines.append("| " + " | ".join("---" for _ in range(cols)) + " |")
        return "\n".join(lines), cells
    lines = ["<table>"]
    if external:
        lines.append("<thead><tr>" + "".join(f"<th>{escaped(name)}</th>" for name in table.header.names)
                     + "</tr></thead>")
    lines.append("<tbody>")
    for row in range(rows):
        lines.append("<tr>" + "".join(
            f'<td rowspan="{cell["row_span"]}" colspan="{cell["col_span"]}">{escaped(cell["text"])}</td>'
            for cell in cells if cell["row"] == row) + "</tr>")
    return "\n".join(lines + ["</tbody></table>"]), cells


def _inside(box, regions):
    rectangle = fitz.Rect(box)
    return any((rectangle.tl + rectangle.br) / 2 in region for region in regions)


def _table_raw_text(page, region, sections):
    parts = [page.get_text("text", clip=region).strip()]
    for section in sections:
        for paragraph in section["paragraphs"]:
            source = paragraph["source"]
            if source["page"] != page.number + 1 or source.get("method") != "tesseract":
                continue
            if source.get("words"):
                parts.append(" ".join(word["text"] for word in source["words"]
                                      if _inside(word["bbox"], [region])))
            elif _inside(source["bbox"], [region]):
                parts.append(paragraph["text"])
    return "\n".join(part for part in parts if part)


def _without_table_text(page, sections, regions):
    """Remove table characters/words, retaining the rest of mixed native/OCR blocks."""
    paragraphs, mapped = [], []
    flags = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES
    blocks = [block for block in page.get_text("rawdict", flags=flags)["blocks"] if block.get("type") == 0]
    for index, block in enumerate(blocks):
        lines, kept_boxes = [], []
        for line in block.get("lines", []):
            spans = []
            for span in line.get("spans", []):
                for character in span.get("chars", []):
                    box = fitz.Rect(character["bbox"])
                    if not _inside(box, regions):
                        spans.append(character.get("c", ""))
                        kept_boxes.append(box)
            if spans:
                lines.append("".join(spans))
        text = "\n".join(lines)
        if not text.strip():
            continue
        box = fitz.Rect(kept_boxes[0])
        for kept in kept_boxes[1:]:
            box |= kept
        block_id = f"pdf-p{page.number + 1:06d}-b{index:06d}"
        origin = {"page": page.number + 1, "block_id": block_id, "bbox": list(box)}
        paragraphs.append({"id": block_id, "text": text, "source": origin,
                           "cited_refs": [], "cited_keys": [], "refs_figure": [], "refs_table": []})
        mapped.append({"id": block_id, **origin})
    for section in sections:
        if section["paragraphs"] and section["paragraphs"][0]["source"]["page"] == page.number + 1:
            for paragraph in section["paragraphs"]:
                source = paragraph["source"]
                if source.get("method") != "tesseract":
                    continue
                words = [word for word in source.get("words", []) if not _inside(word["bbox"], regions)]
                if words:
                    kept = copy.deepcopy(paragraph)
                    kept["text"] = " ".join(word["text"] for word in words)
                    box = fitz.Rect(words[0]["bbox"])
                    for word in words[1:]:
                        box |= fitz.Rect(word["bbox"])
                    kept["source"].update(words=words, bbox=list(box))
                elif not source.get("words") and not _inside(source["bbox"], regions):
                    kept = copy.deepcopy(paragraph)
                else:
                    continue
                paragraphs.append(kept)
                mapped.append({"id": kept["id"], **kept["source"]})
            section["paragraphs"] = paragraphs
            break
    return mapped


def _general_media(out, path, sections, source_map, report, cancel, progress, cache_dir, cache_key):
    """Only a selected general fallback may replace prose with native tables."""
    sha = str(out.get("sha1") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        sha = store.file_sha1(path)
    out["sha1"] = sha
    destination = store.figs_dir(sha)
    tables, figures, floats, pending = [], [], [], []
    with tempfile.TemporaryDirectory(prefix="pubnexus-pdf-assets-") as scratch:
        scratch = Path(scratch)

        def asset(name, data):
            target = scratch / name
            target.write_bytes(data)
            pending.append((target, name))
            return name

        with fitz.open(utils.long_path(path)) as pdf:
            for page_index in range(pdf.page_count):
                _check_cancel(cancel)
                info = source_map["pages"][page_index]
                if info["status"] == "failed":
                    continue
                cached = _read_checkpoint(cache_dir, cache_key, page_index + 1, "media")
                if cached:
                    tables.extend(cached["tables"])
                    figures.extend(cached["figures"])
                    floats.extend(cached["floats"])
                    warnings = cached.get("warnings", [])
                    report["warnings"].extend(warnings)
                    if cached.get("partial"):
                        report["status"] = "partial"
                    source_map["blocks"] = [block for block in source_map["blocks"]
                                            if block["page"] != page_index + 1] + cached["blocks"]
                    info["block_ids"] = [block["id"] for block in cached["blocks"]]
                    for section in sections:
                        if section["paragraphs"] and section["paragraphs"][0]["source"]["page"] == page_index + 1:
                            section["paragraphs"] = cached["paragraphs"]
                            break
                    pending.extend((cache_dir / f"assets-p{page_index + 1:06d}" / item["name"], item["name"])
                                   for item in cached["assets"])
                    report["page_cache"]["media_reused"] += 1
                    _notify(progress, page_index + 1, pdf.page_count, "done", cancel)
                    continue
                first_table, first_figure, first_float = len(tables), len(figures), len(floats)
                first_asset, first_warning = len(pending), len(report["warnings"])
                media_failed = False
                page, regions = pdf.load_page(page_index), []
                _notify(progress, page_index + 1, pdf.page_count, "table", cancel)
                try:
                    while not _TABLE_LOCK.acquire(timeout=0.1):
                        _check_cancel(cancel)
                    try:
                        finder = page.find_tables(strategy="lines_strict")
                        for number, native in enumerate(finder.tables, 1):
                            _check_cancel(cancel)
                            block_id = f"pdf-p{page_index + 1:06d}-table{number:04d}"
                            source = {"page": page_index + 1, "block_id": block_id,
                                      "bbox": [float(value) for value in native.bbox]}
                            table = {"id": block_id, "caption": "", "source": source}
                            try:
                                table["markdown"], table["cells"] = _table_markup(native)
                            except Exception as error:
                                name = asset(f"native-p{page_index + 1:06d}-table{number:04d}.png",
                                             _pixmap(page, clip=fitz.Rect(native.bbox)).tobytes("png"))
                                raw = _table_raw_text(page, fitz.Rect(native.bbox), sections)
                                markup = f"![표 원문]({name})"
                                if raw:
                                    markup += "\n\n<pre>" + html.escape(raw) + "</pre>"
                                table.update(image=name, markdown=markup, raw_text=raw, cells=[])
                                report["warnings"].append(f"{page_index + 1}쪽 표 구조 미확정 — 원문 이미지 보존: {error}")
                                report["status"] = "partial"
                            tables.append(table)
                            regions.append(fitz.Rect(native.bbox))
                            if native.header.external:
                                regions.append(fitz.Rect(native.header.bbox))
                            floats.append({"id": block_id, "kind": "table", **source})
                    finally:
                        _TABLE_LOCK.release()
                    if regions:
                        mapped = _without_table_text(page, sections, regions)
                        source_map["blocks"] = [block for block in source_map["blocks"]
                                                if block["page"] != page_index + 1] + mapped
                        info["block_ids"] = [block["id"] for block in mapped]
                except ExtractionCancelled:
                    raise
                except Exception as error:
                    media_failed = True
                    report["warnings"].append(f"{page_index + 1}쪽 표 검출 실패 — 본문 원문 유지: {error}")
                    report["status"] = "partial"
                _notify(progress, page_index + 1, pdf.page_count, "images", cancel)
                try:
                    for number, image in enumerate(page.get_image_info(xrefs=True), 1):
                        _check_cancel(cancel)
                        box = fitz.Rect(image["bbox"])
                        if box.is_empty or box.is_infinite:
                            continue
                        extracted = pdf.extract_image(image["xref"]) if image.get("xref") else None
                        if (extracted and not extracted.get("smask")
                                and extracted.get("ext") in ("png", "jpeg", "jpg", "webp")):
                            data, extension = extracted["image"], extracted["ext"]
                        else:
                            data, extension = _pixmap(page, clip=box).tobytes("png"), "png"
                        name = asset(f"native-p{page_index + 1:06d}-img{number:04d}.{extension}", data)
                        block_id = f"pdf-p{page_index + 1:06d}-img{number:04d}"
                        source = {"page": page_index + 1, "block_id": block_id,
                                  "bbox": list(box), "xref": image.get("xref", 0)}
                        figures.append({"id": block_id, "caption": "", "image": name, "source": source})
                        floats.append({"id": block_id, "kind": "figure", **source})
                except ExtractionCancelled:
                    raise
                except Exception as error:
                    media_failed = True
                    report["warnings"].append(f"{page_index + 1}쪽 그림 보존 실패 — {error}")
                    report["status"] = "partial"
                if not media_failed:
                    try:
                        cache_assets = []
                        replacements = []
                        for temporary, name in pending[first_asset:]:
                            cached_asset = cache_dir / f"assets-p{page_index + 1:06d}" / name
                            _copy_asset(temporary, cached_asset)
                            cache_assets.append({"name": name, "sha1": store.file_sha1(cached_asset)})
                            replacements.append((cached_asset, name))
                        paragraphs = [paragraph for section in sections for paragraph in section["paragraphs"]
                                      if paragraph["source"]["page"] == page_index + 1]
                        payload = {"paragraphs": paragraphs,
                                   "blocks": [block for block in source_map["blocks"] if block["page"] == page_index + 1],
                                   "tables": tables[first_table:], "figures": figures[first_figure:],
                                   "floats": floats[first_float:], "assets": cache_assets,
                                   "warnings": report["warnings"][first_warning:],
                                   "partial": len(report["warnings"]) > first_warning}
                        if _checkpoint(cache_dir, cache_key, page_index + 1, "media", payload):
                            pending[first_asset:] = replacements
                    except OSError:
                        report["warnings"].append(f"{page_index + 1}쪽 자산 cache 저장 실패 — 이번 결과는 계속 보존")
                _notify(progress, page_index + 1, pdf.page_count, "done", cancel)
        _check_cancel(cancel)
        if pending:
            destination.mkdir(parents=True, exist_ok=True)
            for temporary, name in pending:
                _copy_asset(temporary, destination / name)
    sections[:] = [section for section in sections if section["paragraphs"]]
    source_map["floats"] = floats
    if tables:
        out["tables"] = tables
    if figures:
        out["figures"] = figures
    report["native_tables"] = len(tables)
    report["native_images"] = len(figures)
    _check_cancel(cancel)


def _sampled_coverage(sections: list[dict], represented: str) -> dict:
    """Page probes with occurrence budgets; still a heuristic, not completeness proof."""
    covered_chars = total_chars = 0
    remaining = Counter()
    missing_pages, missing_blocks, wholly_missing_pages, page_coverage = [], [], [], []
    for section in sections:
        page_covered = page_chars = 0
        page = section["paragraphs"][0]["source"]["page"]
        for paragraph in section["paragraphs"]:
            text = _fold(paragraph["text"])
            if not text:
                continue
            hits = total = 0
            width = min(48, len(text))
            starts = sorted({0, max(0, (len(text) - width) // 2), len(text) - width})
            probes = dict.fromkeys(text[start:start + width] for start in starts)
            for probe in probes:
                if probe not in remaining:
                    remaining[probe] = represented.count(probe)
                expected = text.count(probe)
                found = min(expected, remaining[probe])
                remaining[probe] -= found
                total += expected
                hits += found
            page_chars += len(text)
            page_covered += len(text) * hits / total
            if hits < total:
                missing_blocks.append(paragraph["id"])
        ratio = page_covered / page_chars if page_chars else 0.0
        page_coverage.append({"page": page, "native_chars": page_chars,
                              "sampled_text_coverage": round(ratio, 3)})
        if page_covered < page_chars:
            missing_pages.append(page)
        if page_chars >= 48 and ratio < 0.20:
            wholly_missing_pages.append(page)
        covered_chars += page_covered
        total_chars += page_chars
    return {"sampled_text_coverage": round(covered_chars / total_chars if total_chars else 0.0, 3),
            "unrepresented_pages": missing_pages, "unrepresented_blocks": missing_blocks,
            "wholly_unrepresented_pages": wholly_missing_pages, "page_coverage": page_coverage}


def _coverage_sections(sections: list[dict], pages: list[dict]) -> tuple[list[dict], list[str]]:
    """Exclude only identifiable page furniture from evidence, never from output.

    A short line at a margin is insufficient: require an explicit publication
    label/page number plus a separate substantial body block on the same page.
    """
    heights = {page["page"]: page.get("height", 0) for page in pages}
    filtered, excluded = [], []
    page_number = re.compile(r"(?:(?:page|p\.)\s*)?(?:\d{1,5}|[ivxlcdm]{1,8})", re.I)
    publication = re.compile(
        r"^(?:journal\b.*\b(?:vol(?:ume)?\.?|issue|page)\b|"
        r"(?:vol(?:ume)?\.?|issue)\s*\d.*\b(?:page|pp?\.)|"
        r"(?:issn|e-issn)\b|©|copyright\b)", re.I)
    for section in sections:
        paragraphs = section["paragraphs"]
        kept = []
        for paragraph in paragraphs:
            origin = paragraph["source"]
            height = heights.get(origin["page"], 0)
            bbox = origin["bbox"]
            text = paragraph["text"].strip()
            marginal = height > 0 and (bbox[3] <= height * 0.10 or bbox[1] >= height * 0.90)
            substantial_other = any(other is not paragraph and len(_fold(other["text"])) >= 120
                                    for other in paragraphs)
            identifiable = bool(page_number.fullmatch(text) or publication.match(text))
            if (marginal and substantial_other and len(text) <= 160
                    and len(text.splitlines()) <= 3 and identifiable):
                excluded.append(paragraph["id"])
            else:
                kept.append(paragraph)
        if kept:
            filtered.append({**section, "paragraphs": kept})
    return filtered, excluded


def ensure_fulltext(doc_dict: dict, pdf_path: str | Path, *, cancel=None, progress=None) -> tuple[dict, dict]:
    """Return (document, report), preserving paper metadata/references on fallback.

    Native coordinates use unrotated PyMuPDF points, top-left origin, 1-based
    pages. Missing native text is explicit; OCR is never silently assumed.
    """
    _check_cancel(cancel)
    out = copy.deepcopy(doc_dict)
    tesseract = shutil.which("tesseract")
    if not tesseract:
        from .local_ocr import discover
        tesseract = discover()
    report = {"version": 1, "status": "failed", "used_fallback": False,
              "reasons": [], "pages": [], "warnings": [],
              "ocr_performed": False, "ocr_available": bool(tesseract),
              "source_map_status": "unmapped"}
    try:
        cache_dir, cache_key = _cache_location(Path(pdf_path), tesseract)
        sections, source_map, pages, warnings = _native_pages(
            Path(pdf_path), cancel=cancel, progress=progress, tesseract=tesseract,
            cache_dir=cache_dir, cache_key=cache_key)
    except ExtractionCancelled:
        raise
    except Exception as error:
        report["warnings"].append(f"원문 확인 실패 — {type(error).__name__}: {error}")
        out["extraction_report"] = report
        return out, report
    if any(page["status"] == "ocr_extracted" for page in pages):
        warnings.append("로컬 OCR 인식 결과가 포함되어 있습니다 — 원문 대조 필요")
    report.update(pages=pages, warnings=list(dict.fromkeys(warnings)))
    report["page_cache"] = {"key": cache_key, "native_reused": sum(bool(page.get("cache_hit")) for page in pages),
                            "media_reused": 0}
    report["ocr_performed"] = any(page.get("ocr_performed") for page in pages)
    report["status"] = ("complete" if pages and all(
        page["status"] in ("text_extracted", "blank") for page in pages) else "partial")
    if not pages or all(page["status"] == "failed" for page in pages):
        report["status"] = "failed"
    coverage_sections, excluded = _coverage_sections(sections, pages)
    coverage = _sampled_coverage(coverage_sections, _represented_text(out))
    report.update(coverage)
    missing_pages = coverage["unrepresented_pages"]
    report["coverage_excluded_blocks"] = excluded
    if not _body_text(out).strip():
        report["reasons"].append("missing_body")
    if out.get("scope") == "non_english":
        report["reasons"].append("paper_language_filter")
    if out.get("source") not in ("grobid", "pmc_xml"):
        report["reasons"].append("native_general_path")
    if missing_pages:
        report["reasons"].append("unrepresented_pages")
    fallback_required = (any(reason in report["reasons"] for reason in
                             ("missing_body", "paper_language_filter", "native_general_path"))
                         or coverage["sampled_text_coverage"] < 0.50
                         or bool(coverage["wholly_unrepresented_pages"]))
    boundary = (out.get("qc") or {}).get("boundary") or {}
    protected_boundary = bool(boundary.get("confident") or
                              (out.get("extraction_report") or {}).get("paper_boundary_preserved"))
    if missing_pages and protected_boundary and _body_text(out).strip():
        # A confident paper boundary may intentionally exclude neighbouring articles.
        # Never undo that safety decision by merging the full PDF into its body.
        report["status"] = "partial" if report["status"] != "failed" else "failed"
        report["paper_boundary_preserved"] = True
        report["warnings"].append("논문 경계 밖 원문은 합치지 않았습니다 — 미대표 페이지 검수 필요")
    elif fallback_required:
        if sections or not _body_text(out).strip():
            out["body_text"] = sections
            out["previous_source"] = out.get("previous_source") or out.get("source")
            out["source"] = "pdf_general"
            out["scope"] = "general_pdf"
            out["source_map"] = source_map
            report["used_fallback"] = True
            report["source_map_status"] = "native_blocks"
            out.pop("native_unrepresented_blocks", None)
            original_sections, original_map = copy.deepcopy(sections), copy.deepcopy(source_map)
            try:
                _general_media(out, Path(pdf_path), sections, source_map, report, cancel, progress,
                               cache_dir, cache_key)
            except ExtractionCancelled:
                raise
            except Exception as error:
                out["body_text"], out["source_map"] = original_sections, original_map
                report["status"] = "partial" if report["status"] != "failed" else "failed"
                report["warnings"].append(f"표/그림 자산 보완 실패 — 본문 보존: {type(error).__name__}: {error}")
    elif missing_pages:
        # A publisher line or small wording mismatch must not destroy a healthy
        # structured paper. Preserve the unmatched native blocks separately.
        report["status"] = "partial" if report["status"] != "failed" else "failed"
        report["warnings"].append("일부 원문 블록 미대표 — 논문 구조 유지, 별도 원문 블록 검수 필요")
        missing_ids = set(coverage["unrepresented_blocks"])
        out["native_unrepresented_blocks"] = [paragraph for section in sections
                                             for paragraph in section["paragraphs"]
                                             if paragraph["id"] in missing_ids]
    out["extraction_report"] = report
    report["warnings"] = list(dict.fromkeys(report["warnings"]))
    _check_cancel(cancel)
    return out, report
