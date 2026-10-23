"""Conservative offline TableFormer supplement for selected PDF table pages.

Native/paper extraction remains authoritative. Only captioned missing tables are
attempted; every accepted cell must agree with text at its native PDF position.
Models are never downloaded here, and the optional SDK runs in a bounded child.
"""
from __future__ import annotations

from collections import Counter
from bisect import bisect_left, bisect_right
import copy
from functools import lru_cache
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import fitz

from . import store, utils
from .docling_table_child import CHILD_CODE
from .external_documents import _stop_owned_process
from .general_pdf import ExtractionCancelled

_CAPTION = re.compile(r"^\s*(?:table\s+(?:\d+|[ivxlcdm]+)|표\s*\d+)\b", re.I)
_MODEL_FILES = (
    "docling-project--docling-layout-heron/config.json",
    "docling-project--docling-layout-heron/preprocessor_config.json",
    "docling-project--docling-layout-heron/model.safetensors",
    "docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json",
    "docling-project--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors",
)
_MAX_PAGES = 8
_MAX_OUTPUT = 16 * 1024 * 1024
_VERSION = "pdfium-accurate-cellmatch-validated-v1"


def _check(cancel):
    if cancel and cancel():
        raise ExtractionCancelled("표 보완을 중지했습니다.")


def _notify(progress, page, total, cancel):
    _check(cancel)
    if progress:
        progress(page, total, "docling_tables")
    _check(cancel)


def _compact(text):
    return "".join(str(text).split())


def _fold(text):
    return "".join(c for c in str(text).casefold() if c.isalnum())


def _box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("missing PDF bounds")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in value):
        raise ValueError("invalid PDF bounds")
    box = fitz.Rect(value)
    if box.is_empty or box.is_infinite:
        raise ValueError("empty PDF bounds")
    return box


def _source(table):
    origin = table.get("source") or {}
    if origin.get("page") and origin.get("bbox"):
        return int(origin["page"]), _box(origin["bbox"])
    span = table.get("pdf_span") or {}
    if span.get("page") and all(k in span for k in ("x0", "y0", "x1", "y1")):
        return int(span["page"]), _box([span[k] for k in ("x0", "y0", "x1", "y1")])
    return None


def _candidates(doc, pdf, cancel, warnings):
    candidates = []
    if doc.get("source") == "pdf_general":
        for section in doc.get("body_text") or []:
            for paragraph in section.get("paragraphs") or []:
                text, origin = paragraph.get("text") or "", paragraph.get("source") or {}
                if origin.get("method") or not _CAPTION.match(text) or not origin.get("page") or not origin.get("bbox"):
                    continue
                page, cap = int(origin["page"]), _box(origin["bbox"])
                covered = False
                for table in doc.get("tables") or []:
                    located = _source(table)
                    if located and located[0] == page and 0 <= located[1].y0-cap.y1 <= 100:
                        covered = True
                if not covered and 1 <= page <= len(pdf):
                    candidates.append({"page": page, "caption": text, "caption_bbox": list(cap), "index": None})
    else:
        empty = [(i, t) for i, t in enumerate(doc.get("tables") or [])
                 if not str(t.get("markdown") or "").strip()]
        for index, table in empty:
            key, hits = _fold(table.get("caption") or ""), []
            if len(key) < 30:
                warnings.append("빈 표의 고유 캡션/위치를 확인하지 못해 원문을 유지했습니다.")
                continue
            for page_index in range(len(pdf)):
                _check(cancel)
                for block in pdf[page_index].get_text("blocks"):
                    if len(block) > 6 and block[6] == 0 and _fold(block[4]).startswith(key):
                        hits.append((page_index+1, list(block[:4])))
            if len(hits) == 1:
                candidates.append({"page": hits[0][0], "caption": table["caption"],
                                   "caption_bbox": hits[0][1], "index": index})
            else:
                warnings.append("빈 표의 캡션 위치가 없거나 여러 곳이어서 원문을 유지했습니다.")
    return candidates


@lru_cache(maxsize=4)
def _fingerprint(files):
    digest = hashlib.sha256()
    for filename, size, mtime in files:
        digest.update(str((filename, size, mtime)).encode("utf-8"))
        with open(utils.long_path(filename), "rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


def _engine():
    from .external_documents import find_engine
    override = os.environ.get("PUBNEXUS_DOCLING_PYTHON")
    python = Path(override) if override else find_engine("markitdown_python")
    if python is None or not python.is_file():
        raise RuntimeError("별도 Docling Python이 없습니다(PUBNEXUS_DOCLING_PYTHON).")
    models = Path(os.environ.get("PUBNEXUS_DOCLING_MODELS") or python.parent.parent.parent / "docling-models")
    files = []
    for name in _MODEL_FILES:
        file = models / name
        if not file.is_file():
            raise RuntimeError("Docling 로컬 layout/TableFormer 모델이 없습니다. 앱은 자동 다운로드하지 않습니다.")
        stat = file.stat()
        files.append((str(file), stat.st_size, stat.st_mtime_ns))
    packages = python.parent.parent / "Lib" / "site-packages"
    version_files = list(packages.glob("docling*-*.dist-info/METADATA"))
    if not version_files:
        raise RuntimeError("선택한 Python에 Docling이 설치되지 않았습니다.")
    for pattern in ("pypdfium2-*.dist-info/METADATA", "torch-*.dist-info/METADATA", "transformers-*.dist-info/METADATA"):
        version_files.extend(packages.glob(pattern))
    for file in sorted(version_files):
        stat = file.stat()
        files.append((str(file), stat.st_size, stat.st_mtime_ns))
    return python, models, _fingerprint(tuple(files))


def _atomic(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".docling-", dir=utils.long_path(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(utils.long_path(name), utils.long_path(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _record(path, page):
    if not path.is_file() or path.stat().st_size > _MAX_OUTPUT:
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("page") == page and record.get("status") == "success"
                and not record.get("errors") and isinstance(record.get("tables"), list)
                and isinstance(record.get("pages"), dict) and record.get("version")):
            return record
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _run(python, models, source, pages, total, cache, cancel, progress, report):
    completed = set()
    with tempfile.TemporaryDirectory(prefix="pubnexus-docling-") as temporary:
        work = Path(temporary)
        request = work / "request.json"
        request.write_text(json.dumps({"source": str(source), "models": str(models),
                                       "pages": pages, "output": str(work)}), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
                           TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false",
                           HF_HOME=str(models.parent / "hf-cache"), TORCH_HOME=str(models / "torch-cache"),
                           HF_HUB_CACHE=str(models.parent / "hf-cache" / "hub"),
                           HUGGINGFACE_HUB_CACHE=str(models.parent / "hf-cache" / "hub"),
                           XDG_CACHE_HOME=str(models / "runtime-cache"), DOCLING_CACHE_DIR=str(models / "docling-cache"),
                           OMP_NUM_THREADS="4")
        timeout = min(600., max(.001, float(os.environ.get("PUBNEXUS_DOCLING_TIMEOUT", "120"))))
        started = time.monotonic()
        log = work / "diagnostic.log"
        with open(log, "wb") as stream:
            process = subprocess.Popen([str(python), "-I", "-B", "-c", CHILD_CODE, str(request)],
                                       stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                       cwd=str(work), env=environment, shell=False,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                while True:
                    _check(cancel)
                    for page in pages:
                        if page in completed:
                            continue
                        record = _record(work / f"page-{page:06d}.json", page)
                        if record:
                            _atomic(cache / f"page-{page:06d}.json", record)
                            completed.add(page)
                            _notify(progress, page, total, cancel)
                    if process.poll() is not None:
                        break
                    if time.monotonic()-started > timeout:
                        raise RuntimeError("Docling 표 보완 시간 제한 — 완료한 쪽은 캐시하고 본문을 보존했습니다.")
                    if log.stat().st_size > 1024*1024:
                        raise RuntimeError("Docling 표 보완 진단 출력 예산을 초과했습니다.")
                    if any((work / f"page-{p:06d}.json").is_file() and (work / f"page-{p:06d}.json").stat().st_size > _MAX_OUTPUT for p in pages):
                        raise RuntimeError("Docling 표 결과 출력 예산을 초과했습니다.")
                    time.sleep(.05)
            except ExtractionCancelled:
                raise
            except RuntimeError as error:
                report["warnings"].append(str(error))
            finally:
                _stop_owned_process(process)
                process.wait(timeout=5)
        if process.returncode or len(completed) != len(pages):
            detail = log.read_bytes()[:2000].decode("utf-8", "replace")
            report["warnings"].append("Docling 일부 쪽을 보완하지 못했습니다: " + detail)
    return completed


def _canonical(raw, size, page):
    if abs(size["width"]-page.rect.width) > .1 or abs(size["height"]-page.rect.height) > .1:
        raise ValueError("PDF backend page geometry mismatch")
    if raw["coord_origin"] == "BOTTOMLEFT":
        box = _box([raw["l"], size["height"]-raw["t"], raw["r"], size["height"]-raw["b"]])
    elif raw["coord_origin"] == "TOPLEFT":
        box = _box([raw["l"], raw["t"], raw["r"], raw["b"]])
    else:
        raise ValueError("unknown coordinate origin")
    return box * page.derotation_matrix


def _validated(table, record, page, cancel=None):
    prov = table["prov"]
    if len(prov) != 1 or prov[0]["page_no"] != page.number+1:
        raise ValueError("ambiguous page provenance")
    size = record["pages"][str(page.number+1)]
    region = _canonical(prov[0]["bbox"], size, page)
    words = [word for word in page.get_text("words") if (fitz.Rect(word[:4]).tl+fitz.Rect(word[:4]).br)/2 in region]
    words = sorted(words, key=lambda w: (w[1]+w[3])/2)
    centers_y = [(word[1]+word[3])/2 for word in words]
    rows, cols = table["rows"], table["cols"]
    if not isinstance(rows, int) or not isinstance(cols, int) or min(rows, cols) < 2 or rows*cols > 100000:
        raise ValueError("uncertain table dimensions")
    cells, occupied, checks = [], {}, 0
    for cell in table["cells"]:
        _check(cancel)
        row, col, rs, cs = (cell[k] for k in ("start_row_offset_idx", "start_col_offset_idx", "row_span", "col_span"))
        if not all(isinstance(x, int) for x in (row, col, rs, cs)) or min(row, col) < 0 or min(rs, cs) < 1 or row+rs > rows or col+cs > cols:
            raise ValueError("invalid cell span")
        indices = {(r, c) for r in range(row, row+rs) for c in range(col, col+cs)}
        if occupied.keys() & indices:
            raise ValueError("overlapping logical cells")
        text = str(cell["text"]).strip()
        if rs != 1 or cs != 1 or not text:
            raise ValueError("complex merged/sparse table left unchanged")
        box = _canonical(cell["bbox"], size, page) if cell.get("bbox") else None
        if text:
            if box is None:
                raise ValueError("cell text has no native position")
            expanded = box + (-3, -3, 3, 3)
            nearby = words[bisect_left(centers_y, expanded.y0):bisect_right(centers_y, expanded.y1)]
            checks += len(nearby)
            if checks > 1000000:
                raise ValueError("native cell verification budget exceeded")
            at_position = " ".join(word[4] for word in nearby if (word[0]+word[2])/2 >= expanded.x0 and (word[0]+word[2])/2 <= expanded.x1)
            if _compact(at_position) != _compact(text):
                raise ValueError("cell text differs from original PDF at its position")
        entry = {"row": row, "col": col, "row_span": rs, "col_span": cs,
                 "text": text, "bbox": list(box) if box else None}
        cells.append(entry)
        occupied.update((index, entry) for index in indices)
    if len(occupied) != rows*cols or not any(c["text"] for c in cells):
        raise ValueError("incomplete table grid")
    if Counter(_compact("".join(word[4] for word in words))) != Counter(_compact("".join(c["text"] for c in cells))):
        raise ValueError("table would lose or invent original characters/numbers")
    # Check geometric order along both logical axes in O(rows*cols), including
    # spans and empty cells. A bag of correct numbers does not prove row identity.
    for vertical in (False, True):
        outer, inner = (cols, rows) if vertical else (rows, cols)
        axis = 1 if vertical else 0
        for start in range(outer):
            _check(cancel)
            previous = None
            for step in range(inner):
                entry = occupied[(step, start) if vertical else (start, step)]
                if not entry["text"] or entry is previous:
                    continue
                if previous and sum(previous["bbox"][axis::2]) >= sum(entry["bbox"][axis::2]):
                    raise ValueError("native cell positions disagree with logical rows/columns")
                previous = entry
    cells.sort(key=lambda c: (c["row"], c["col"]))
    by_row, by_col = [[] for _ in range(rows)], [[] for _ in range(cols)]
    for cell in cells:
        by_row[cell["row"]].append(cell)
        by_col[cell["col"]].append(cell)
    for groups, axis in ((by_row, 1), (by_col, 0)):
        for group in groups:
            _check(cancel)
            if len(group) < 2 or max(c["bbox"][axis] for c in group) > min(c["bbox"][axis+2] for c in group)+2:
                raise ValueError("glyph intervals disagree within a logical row/column")
    parts = ["<table><tbody>"]
    for row in by_row:
        _check(cancel)
        parts.append("<tr>" + "".join('<td>' + html.escape(c["text"]).replace("\n", "<br>") + '</td>' for c in row) + "</tr>")
    markup = "".join(parts) + "</tbody></table>"
    return region, cells, markup


def _remove_prose(doc, page, region, table_id, cancel):
    """Edit only overlapping native paragraphs, never regenerate the page body."""
    replacements = []
    for section in doc.get("body_text") or []:
        for index, paragraph in enumerate(section.get("paragraphs") or []):
            _check(cancel)
            origin = paragraph.get("source") or {}
            if origin.get("page") != page.number+1 or not origin.get("bbox"):
                continue
            box = _box(origin["bbox"])
            if not box.intersects(region):
                continue
            if origin.get("method"):
                raise ValueError("OCR paragraph cannot be safely reassigned")
            raw = page.get_text("rawdict", clip=box+(-.01, -.01, .01, .01), flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
            full, kept, bounds = [], [], []
            for block in raw["blocks"]:
                for line in block.get("lines", []):
                    line_kept = []
                    for span in line.get("spans", []):
                        for char in span.get("chars", []):
                            charbox = fitz.Rect(char["bbox"])
                            full.append(char["c"])
                            if (charbox.tl+charbox.br)/2 not in region:
                                line_kept.append(char["c"])
                                bounds.append(charbox)
                    if line_kept:
                        kept.append("".join(line_kept))
            if _compact("".join(full)) != _compact(paragraph.get("text") or ""):
                raise ValueError("overlapping paragraph is not an unchanged native block")
            replacement = copy.deepcopy(paragraph)
            replacement["text"] = "\n".join(kept)
            if bounds:
                newbox = fitz.Rect(bounds[0])
                for item in bounds[1:]:
                    newbox |= item
                replacement["source"]["bbox"] = list(newbox)
            replacements.append((section, index, replacement if bounds else None))
    changed = {}
    for section, index, replacement in reversed(replacements):
        old = section["paragraphs"][index]
        changed[old["id"]] = replacement
        if replacement:
            section["paragraphs"][index] = replacement
        else:
            section["paragraphs"].pop(index)
    mapping = doc.get("source_map") or {}
    mapping["blocks"] = [{"id": block["id"], **changed[block["id"]]["source"]} if block["id"] in changed else block
                         for block in mapping.get("blocks", []) if block["id"] not in changed or changed[block["id"]]]
    for info in mapping.get("pages", []):
        if info.get("page") == page.number+1:
            info["block_ids"] = [block["id"] for block in mapping["blocks"] if block["page"] == page.number+1]
    anchors = [paragraph for section in doc.get("body_text") or [] for paragraph in section.get("paragraphs") or []
               if (paragraph.get("source") or {}).get("page") == page.number+1
               and _box(paragraph["source"]["bbox"]).y1 <= region.y0]
    if anchors:
        anchor = max(anchors, key=lambda p: p["source"]["bbox"][3])
        anchor.setdefault("refs_table", []).append(table_id)


def enhance_document(doc, path, *, cancel=None, progress=None):
    """Return a copy with only verified missing tables enhanced; never download."""
    _check(cancel)
    out, source = copy.deepcopy(doc), Path(path).resolve()
    report = {"status": "complete", "attempted_pages": [], "added": 0, "filled": 0,
              "warnings": [], "cache_reused": 0, "engine": "Docling/PDFium/TableFormer accurate", "network": False, "llm": False}
    with fitz.open(utils.long_path(source)) as pdf:
        candidates = _candidates(out, pdf, cancel, report["warnings"])
        if not candidates:
            if report["warnings"]:
                report["status"] = "partial"
                out.setdefault("extraction_report", {})["docling_tables"] = report
                if out["extraction_report"].get("status") != "failed":
                    out["extraction_report"]["status"] = "partial"
                out["extraction_report"].setdefault("warnings", []).extend(report["warnings"])
            return out
        out.setdefault("extraction_report", {})["docling_tables"] = report
        try:
            python, models, fingerprint = _engine()
            sha = store.file_sha1(source)
            key = hashlib.sha256((_VERSION+fitz.VersionBind+fingerprint+hashlib.sha256(CHILD_CODE.encode()).hexdigest()).encode()).hexdigest()
            cache = store.root() / "docling-tables" / sha[:2] / sha / key
            report["model_fingerprint"] = fingerprint
            pages = sorted({c["page"] for c in candidates})
            records = set()
            for page in pages:
                _check(cancel)
                record = _record(cache / f"page-{page:06d}.json", page)
                if record:
                    records.add(page)
                    report["cache_reused"] += 1
            missing = [page for page in pages if page not in records]
            report["attempted_pages"] = pages
            if missing:
                for start in range(0, len(missing), _MAX_PAGES):
                    batch = missing[start:start+_MAX_PAGES]
                    _notify(progress, batch[0], len(pdf), cancel)
                    records.update(_run(python, models, source, batch, len(pdf), cache, cancel, progress, report))
            loaded_page, record = None, None
            for candidate in candidates:
                _check(cancel)
                if loaded_page != candidate["page"]:
                    loaded_page = candidate["page"]
                    record = _record(cache / f"page-{loaded_page:06d}.json", loaded_page) if loaded_page in records else None
                if record is None:
                    report["warnings"].append(f'{candidate["page"]}쪽 표 결과가 없어 원문을 유지했습니다.')
                    continue
                page, choices, declined = pdf[candidate["page"]-1], [], []
                cap = _box(candidate["caption_bbox"])
                for number, table in enumerate(record["tables"], 1):
                    try:
                        region, cells, markup = _validated(table, record, page, cancel)
                        if region.y0 < cap.y1-3 or region.y0-cap.y1 > 220:
                            continue
                        if min(region.x1, cap.x1)-max(region.x0, cap.x0) < min(region.width, cap.width)*.25:
                            continue
                        if any(located and located[0] == candidate["page"] and located[1].intersects(region)
                               for i, t in enumerate(out.get("tables") or []) if i != candidate["index"]
                               for located in [_source(t)]):
                            continue
                        choices.append((region.y0-cap.y1, number, region, cells, markup))
                    except (ValueError, KeyError, TypeError, IndexError) as error:
                        declined.append(str(error))
                        continue
                if len(choices) != 1:
                    report["warnings"].append(f'{candidate["page"]}쪽 표 구조/원문 셀 검증이 불확실하여 원문을 유지했습니다. ' + "; ".join(declined[:3]))
                    continue
                _, number, region, cells, markup = choices[0]
                block_id = f'pdf-p{candidate["page"]:06d}-doclingtable{number:04d}'
                origin = {"page": candidate["page"], "block_id": block_id, "bbox": list(region),
                          "engine": "docling_pdfium", "coordinate_space": "pymupdf_unrotated_points"}
                table = {"id": block_id, "caption": candidate["caption"], "markdown": markup,
                         "markdown_source": "docling_tableformer", "cells": cells, "source": origin}
                if candidate["index"] is None:
                    _remove_prose(out, page, region, block_id, cancel)
                    out.setdefault("tables", []).append(table)
                    report["added"] += 1
                else:
                    old = out["tables"][candidate["index"]]
                    table["id"] = old.get("id") or block_id
                    old.update(table)
                    report["filled"] += 1
                out.setdefault("source_map", {}).setdefault("floats", []).append({"id": table["id"], "kind": "table", **origin})
                report["engine_version"] = record["version"]
            if store.file_sha1(source) != sha:
                out = copy.deepcopy(doc)
                report.update(status="failed", added=0, filled=0)
                out.setdefault("extraction_report", {}).update(status="failed", docling_tables=report)
                report["warnings"].append("보완 도중 원본이 변경되어 표 결과를 확정하지 않았습니다.")
        except ExtractionCancelled:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            report["warnings"].append(str(error))
    if report["warnings"]:
        if report["status"] != "failed":
            report["status"] = "partial"
        if out["extraction_report"].get("status") != "failed":
            out["extraction_report"]["status"] = "partial"
        out["extraction_report"].setdefault("warnings", []).extend(report["warnings"])
    return out
