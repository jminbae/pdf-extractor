"""Isolated official PyMuPDF 2.0 worker; no application imports or layout repair."""
from __future__ import annotations

import hashlib
from functools import lru_cache
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
    def __init__(self, path, total, stream=None):
        self.path, self.total, self.buffer, self.last = path, total, "", -1
        self.stream = stream

    def write(self, text):
        if self.stream is not None:
            self.stream.write(text)
        self.buffer = (self.buffer + text)[-512:]
        counts = []
        for match in re.finditer(r"\(\s*(\d+)/(\d+)\)", self.buffer):
            current, total = map(int, match.groups())
            # The SDK fallback prints before processing the current page.
            counts.append((match.start(), max(0, current - 1), total))
        for match in re.finditer(r"(?:^|[\r\n])\s*\d+%\|[^\r\n]*?\|\s*(\d+)/(\d+)\s*\[", self.buffer):
            completed, total = map(int, match.groups())
            # tqdm's counter advances after the yielded page has been processed.
            counts.append((match.start(), completed, total))
        for _, completed, total in sorted(counts):
            if total == self.total and 0 <= completed <= total and completed > self.last:
                self.last = completed
                _event(self.path, completed, total)
        return len(text)

    def flush(self):
        if self.stream is not None:
            self.stream.flush()


def _background_evidence(page, pymupdf):
    """Prove a vector-only uniform background; never infer blankness from failed OCR."""
    if page.get_image_info():
        return None
    interior = page.rect + (.01, .01, -.01, -.01)
    covers, border_shapes = 0, 0
    drawings = page.get_drawings()
    for drawing in drawings:
        pad = max(0, drawing.get("width", 0) or 0) / 2 if "s" in drawing["type"] else 0
        for item in drawing["items"]:
            if item[0] == "re":
                bounds = item[1]
                if bounds.contains(interior):
                    covers += drawing.get("fill") is not None
                    border_shapes += "s" in drawing["type"]
                    continue
            elif item[0] in ("l", "c"):
                points = item[1:]
                bounds = pymupdf.Rect(min(p.x for p in points), min(p.y for p in points),
                                      max(p.x for p in points), max(p.y for p in points))
            else:
                return None
            if not ((bounds + (-pad, -pad, pad, pad)) & interior).is_empty:
                return None
    if min(page.rect.width, page.rect.height) <= 2:
        return None
    pix = page.get_pixmap(dpi=150, clip=page.rect + (1, 1, -1, -1),
                          colorspace=pymupdf.csRGB, alpha=False)
    colors = pix.color_count()
    if colors != 1:
        return None
    _, rgb = pix.color_topusage()
    kind = "blank" if min(rgb) >= 254 and not border_shapes else "background_only"
    return {"kind": kind, "native_characters": 0, "embedded_images": 0,
            "interior_color_count": colors, "interior_rgb": rgb.hex(), "dpi": 150,
            "raster_inset_points": 1, "geometry_inset_points": .01,
            "fullpage_fills": covers, "border_shapes": border_shapes,
            "drawing_paths": len(drawings), "other_visible_vector_items": 0}


def _drawing_touches(drawing, region, pymupdf):
    pad = max(0, drawing.get("width", 0) or 0) / 2
    for item in drawing["items"]:
        if item[0] == "re":
            bounds = item[1]
        elif item[0] in ("l", "c"):
            points = item[1:]
            bounds = pymupdf.Rect(min(p.x for p in points), min(p.y for p in points),
                                  max(p.x for p in points), max(p.y for p in points))
        else:
            return True  # Unknown geometry cannot prove a clean original photo.
        if not ((bounds + (-pad, -pad, pad, pad)) & region).is_empty:
            return True
    return False


def _icc_rgb_profile(pdf, image_xref):
    kind, value = pdf.xref_get_key(image_xref, "ColorSpace")
    if kind == "xref":
        value = pdf.xref_object(int(value.split()[0]))
    match = re.fullmatch(r"\[\s*/ICCBased\s+(\d+)\s+0\s+R\s*\]", value)
    if match and pdf.xref_get_key(int(match[1]), "N")[1] == "3":
        return pdf.xref_stream(int(match[1]))
    return None


@lru_cache(maxsize=1)
def _sdk_rgb_profile():
    # Obtain this SDK's standard RGB profile through its public PDF/image API.
    # A profile name alone is not evidence of equivalent colour rendering.
    import pymupdf
    with pymupdf.open() as reference:
        page = reference.new_page()
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), False)
        pixmap.clear_with(255)
        xref = page.insert_image(pymupdf.Rect(0, 0, 1, 1), pixmap=pixmap)
        return _icc_rgb_profile(reference, xref)


def _native_image(pdf, page, box, pymupdf):
    region = pymupdf.Rect(box["bbox"])
    images = page.get_image_info(xrefs=True)
    matches = [image for image in images if image.get("xref") and
               max(abs(a-b) for a, b in zip(image["bbox"], region)) <= 1.05]
    if len(matches) != 1:
        return None
    image = matches[0]
    if image["width"] * image["height"] > 32_000_000 or image.get("has-mask"):
        return None
    a, b, c, d, _, _ = image["transform"]
    if a <= 0 or d <= 0 or abs(b) > .00001 or abs(c) > .00001:
        return None
    if any(other is not image and not (pymupdf.Rect(other["bbox"]) & region).is_empty for other in images):
        return None
    xref = image["xref"]
    if any(pdf.xref_get_key(xref, key)[0] != "null" for key in ("SMask", "Mask", "Decode")):
        return None
    if pdf.xref_get_key(xref, "ColorSpace")[1] not in ("/DeviceRGB", "/DeviceGray"):
        profile = _icc_rgb_profile(pdf, xref)
        if not profile or profile != _sdk_rgb_profile():
            return None
    # Normal drawing lists omit clipping paths. Do
    # not reveal pixels hidden by a PDF graphics state in a standalone image.
    for drawing in page.get_drawings(extended=True):
        if drawing["type"] == "clip":
            bounds = drawing.get("scissor") or drawing.get("rect")
            if bounds is None or not (pymupdf.Rect(bounds) & region).is_empty:
                return None
    positions = [index for index, entry in enumerate(page.get_bboxlog())
                 if entry[0] == "fill-image" and max(abs(a-b) for a, b in zip(entry[1], image["bbox"])) <= .05]
    if len(positions) != 1:
        return None
    sequence = positions[0]
    if any(drawing.get("seqno", sequence+1) > sequence and _drawing_touches(drawing, region, pymupdf)
           for drawing in page.get_drawings()):
        return None
    if any(span.get("type") != 3 and span.get("opacity", 1) > 0 and
           not (pymupdf.Rect(span["bbox"]) & region).is_empty for span in page.get_texttrace()):
        return None
    extracted = pdf.extract_image(xref)
    if (not extracted or extracted.get("smask") or extracted["ext"] not in ("jpeg", "png")
            or len(extracted["image"]) > 64*1024*1024):
        return None
    # Preserve only untagged originals. EXIF orientation / embedded colour
    # profiles could display differently outside their PDF graphics state.
    # The pinned SDK's image_profile(bytes) is broken, so uncertain metadata
    # conservatively retains the SDK's colour-managed 300dpi crop.
    if any(marker in extracted["image"] for marker in (b"Exif\x00\x00", b"ICC_PROFILE\x00", b"eXIf", b"iCCP")):
        return None
    if extracted["ext"] == "jpeg" and pdf.xref_stream_raw(xref) != extracted["image"]:
        return None
    # Public SDK rendering validates the complete PDF graphics state (blend,
    # opacity, clipping and colour management). Only byte-identical RGB pixels
    # at the native sampling size permit independent original-byte reuse.
    native_pixmap = pymupdf.Pixmap(extracted["image"])
    if native_pixmap.colorspace.n != 3:
        native_pixmap = pymupdf.Pixmap(pymupdf.csRGB, native_pixmap)
    rendered = page.get_pixmap(matrix=pymupdf.Matrix(native_pixmap.width / region.width,
        native_pixmap.height / region.height), clip=region, colorspace=pymupdf.csRGB, alpha=False)
    if ((native_pixmap.width, native_pixmap.height) != (rendered.width, rendered.height)
            or native_pixmap.samples != rendered.samples):
        return None
    return {"bytes": extracted["image"], "format": extracted["ext"], "xref": xref,
            "method": "embedded_original" if extracted["ext"] == "jpeg" else "embedded_native_pixels",
            "width": extracted["width"], "height": extracted["height"], "native_bbox": list(image["bbox"]),
            "sha256": hashlib.sha256(extracted["image"]).hexdigest()}


def _same_sdk_content(before, after):
    if len(before["page_boxes"]) != len(after["page_boxes"]):
        return False
    for old, new in zip(before["page_boxes"], after["page_boxes"]):
        if any(old[key] != new[key] for key in ("index", "class", "bbox")):
            return False
        old_text, new_text = before["text"][slice(*old["pos"])], after["text"][slice(*new["pos"])]
        if old["class"] in ("picture", "formula"):
            # Comparison only; adopted Markdown is always the intact official SDK chunk.
            old_text = re.sub(r"^\n!\[\]\([^)]+\)\n", "", old_text, count=1)
            new_text = re.sub(r"^\n!\[\]\([^)]+\)\n", "", new_text, count=1)
        if old_text != new_text:
            return False
    return True


def _quality_assets(pdf, chunks, options, page_options, progress, pymupdf, original_pdf):
    formats, assets, audit = ["png"] * len(chunks), {}, []
    for index, chunk in enumerate(chunks):
        boxes = [box for box in chunk["page_boxes"] if box["class"] in ("picture", "formula")
                 and re.match(r"\n!\[\]\([^)]+\)\n", chunk["text"][slice(*box["pos"])])]
        if not boxes:
            continue
        # OCR's official callback may wrap the copied page in new clips and
        # re-encode its image streams. Verify and extract the original PDF's
        # graphics and bytes, never that intermediate OCR representation.
        page = original_pdf[index]
        candidates = {box["index"]: _native_image(original_pdf, page, box, pymupdf) for box in boxes}
        if all(value and value["format"] == "jpeg" for value in candidates.values()):
            _event(progress, index+1, len(pdf), "sdk_assets")
            retry_options = {**options, **page_options.get(index, {}), "pages": [index],
                "image_format": "jpeg", "use_ocr": False, "show_progress": False}
            retry_options.pop("ocr_function", None)
            retried = pdf.to_markdown(**retry_options)[0]
            if _same_sdk_content(chunk, retried):
                audit.append({"page": index+1, "image_format": "jpeg", "use_ocr": False,
                    "before_sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(),
                    "after_sha256": hashlib.sha256(retried["text"].encode("utf-8")).hexdigest()})
                chunk = chunks[index] = retried
                formats[index] = "jpeg"
        for box in chunk["page_boxes"]:
            if box["index"] not in candidates:
                continue
            match = re.match(r"\n!\[\]\(([^)]+)\)\n", chunk["text"][slice(*box["pos"])])
            if not match:
                raise WorkerError("공식 SDK 그림 주소가 변경됐습니다.", "invalid_output")
            native = candidates[box["index"]]
            metadata = {"method": "sdk_crop", "dpi": 300}
            if native and native["format"] == formats[index]:
                destination = Path(match.group(1))
                if not destination.resolve().is_relative_to((Path.cwd() / options["image_path"]).resolve()):
                    raise WorkerError("공식 SDK 자산 경계 오류입니다.", "invalid_output")
                destination.write_bytes(native["bytes"])
                metadata = {key: value for key, value in native.items() if key != "bytes"}
            assets[match.group(1)] = metadata
    return formats, assets, audit


def _refine_record(record, pdf):
    if __package__:
        from .pdf_table_spans import refine_record_tables
        from .korean_boundaries import refine_record
    else:
        # Direct isolated Python (-I) does not import the main application.
        from importlib.util import module_from_spec, spec_from_file_location
        def load(name):
            spec = spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        refine_record_tables = load("pdf_table_spans").refine_record_tables
        refine_record = load("korean_boundaries").refine_record
    return refine_record(refine_record_tables(record, pdf), pdf)


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
    image_path = "images/" + request["sha256"][:16] + "/sdk-v3"
    (work / image_path).mkdir(parents=True, exist_ok=True)
    progress = Path(request["progress"])
    with pymupdf.open(source) as pdf:
        if pdf.needs_pass:
            raise WorkerError("암호가 필요한 PDF입니다.", "encrypted")
        if not 0 < len(pdf) <= request["max_pages"]:
            raise WorkerError("PDF 페이지 예산을 초과했습니다.", "limit")
        pages, scan_pages, native_texts = [], [], []
        _event(progress, 0, len(pdf), "sdk_layout")
        for page in pdf:
            if (max(page.rect.width, page.rect.height) > 20000
                    or page.rect.width * page.rect.height * (300 / 72) ** 2 > 32_000_000):
                raise WorkerError("PDF 페이지 크기 예산을 초과했습니다.", "limit")
            native_text = page.get_text().strip()
            native_texts.append(native_text)
            has_text = bool(native_text)
            blank, background = False, None
            if not has_text:
                background = _background_evidence(page, pymupdf)
                blank = bool(background and background["kind"] == "blank")
                if not background:
                    scan_pages.append(page.number + 1)
            pages.append({"page": page.number + 1, "width": page.cropbox.width,
                          "height": page.cropbox.height, "rotation": page.rotation,
                          "page_label": page.get_label(),
                          "sdk_to_source": list(page.derotation_matrix),
                          "status": "text_extracted" if has_text else background["kind"] if background else "ocr_required",
                          "ocr_performed": False, "blank_verified": blank,
                          "background_verified": bool(background), "background_verification": background})
        if scan_pages and not models:
            raise WorkerError("문자가 없는 비백지 페이지는 기존 OCR 경로가 필요합니다: "
                              + ", ".join(map(str, scan_pages[:30])), "ocr_required")
        options = dict(header=False, footer=False, page_separators=False, write_images=True,
                       image_path=image_path, dpi=300, use_ocr=bool(models), page_chunks=True, show_progress=True)
        if models:
            from pymupdf4llm.ocr.tesseract_api import exec_ocr
            language = "+".join(model["language"] for model in models)
            def tracked_ocr(page, **kwargs):
                info = pages[page.number]
                if info["background_verified"]:
                    return
                info.setdefault("ocr_selection", "sdk_selected")
                _event(progress, page.number + 1, len(pdf), "sdk_ocr")
                # Published SDK callback and built-in Tesseract only; no extra OCR framework.
                kwargs["dpi"] = min(kwargs.get("dpi", 300), 300,
                                    int(72 * (8_000_000 / max(page.rect.get_area(), 1)) ** .5))
                exec_ocr(page, **kwargs)
                info["ocr_performed"] = True
                info["ocr_method"] = "tesseract"
                info["ocr_language"] = language
                if info["status"] == "ocr_required":
                    info["status"] = "ocr_extracted" if page.get_text().strip() else "ocr_empty"
            options.update(ocr_function=tracked_ocr, ocr_language=language, ocr_dpi=150)
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = _SDKProgress(progress, len(pdf))
        sys.stderr = _SDKProgress(progress, len(pdf), original_stderr)
        try:
            chunks = pdf.to_markdown(**options)
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr
        ocr_recovery = []
        if models:
            for index, info in enumerate(pages):
                forced = info["status"] == "ocr_required"
                if forced:
                    info["ocr_selection"] = "forced_after_sdk_skip"
                    tracked_ocr(pdf[index], dpi=300, language=language, keep_ocr_text=False)
                if not forced and not (info["status"] == "ocr_extracted" and not chunks[index]["text"].strip()):
                    continue
                before_md = chunks[index]["text"]
                retry_options = {**options, "pages": [index], "header": True, "footer": True,
                                 "use_ocr": False, "show_progress": False}
                retry_options.pop("ocr_function", None)
                chunks[index] = pdf.to_markdown(**retry_options)[0]
                ocr_recovery.append({"page": index + 1, "selection": info["ocr_selection"],
                    "header": True, "footer": True, "use_ocr": False,
                    "before_sha256": hashlib.sha256(before_md.encode("utf-8")).hexdigest(),
                    "after_sha256": hashlib.sha256(chunks[index]["text"].encode("utf-8")).hexdigest()})
        recovery, candidates = {}, []
        for index, (info, chunk, native_text) in enumerate(zip(pages, chunks, native_texts)):
            if chunk["text"].strip() or not native_text:
                continue
            footer = any(box["class"] == "page-footer" for box in chunk["page_boxes"])
            number_matches = native_text == str(info["page"]) or (
                bool(info["page_label"]) and native_text == info["page_label"])
            if native_text.isdigit() and footer and number_matches:
                info.update(status="pagination_only", pagination_verified=True)
                continue
            candidates.append(index)
            recovery[index] = {"page": info["page"],
                "before_sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(),
                "attempts": [], "selected_options": None}
        # Retry only otherwise empty native pages using published SDK flags.
        # Keep each returned chunk intact: SDK text, box classes, positions and assets.
        for keep_footer in (False, True):
            if not candidates:
                break
            _event(progress, pages[candidates[0]]["page"], len(pdf), "sdk_recovery")
            retry_options = {**options, "header": True, "footer": keep_footer,
                             "pages": candidates, "use_ocr": False, "show_progress": False}
            retry_options.pop("ocr_function", None)
            retried = pdf.to_markdown(**retry_options)
            by_page = {chunk["metadata"]["page_number"]: chunk for chunk in retried}
            still_empty = []
            for index in candidates:
                chunk = by_page[pages[index]["page"]]
                digest = hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()
                attempt = {"header": True, "footer": keep_footer, "use_ocr": False,
                           "sha256": digest, "nonempty": bool(chunk["text"].strip())}
                recovery[index]["attempts"].append(attempt)
                if attempt["nonempty"]:
                    chunks[index] = chunk
                    recovery[index]["selected_options"] = {"header": True, "footer": keep_footer, "use_ocr": False}
                    pages[index]["sdk_recovered"] = True
                else:
                    still_empty.append(index)
                _event(progress, pages[index]["page"], len(pdf), "sdk_recovery")
            candidates = still_empty
        for index, info in recovery.items():
            info["after_sha256"] = hashlib.sha256(chunks[index]["text"].encode("utf-8")).hexdigest()
        page_options = {index: info["selected_options"] for index, info in recovery.items() if info["selected_options"]}
        page_options.update({info["page"]-1: {"header": True, "footer": True} for info in ocr_recovery})
        with pymupdf.open(source) as original_pdf:
            image_formats, image_assets, image_audit = _quality_assets(
                pdf, chunks, options, page_options, progress, pymupdf, original_pdf)
        # These are already serialized SDK Markdown page strings. No text reconstruction.
        markdown = "".join(chunk["text"] for chunk in chunks)
        if len(markdown.encode("utf-8")) > request["max_text_bytes"]:
            raise WorkerError("SDK Markdown 출력 예산을 초과했습니다.", "limit")
        for info, chunk in zip(pages, chunks):
            if info["status"] in ("text_extracted", "ocr_extracted") and not chunk["text"].strip():
                info["recognized_status_before_sdk_filter"] = info["status"]
                info["status"] = "sdk_empty"
        if models:
            options["ocr_function"] = "pymupdf4llm.ocr.tesseract_api.exec_ocr (tracked)"
        record = {"ok": True, "version": pymupdf.VersionBind, "markdown": markdown,
                "chunks": chunks, "pages": pages, "metadata": pdf.metadata,
                "recovery_per_page": [recovery[index] for index in sorted(recovery)],
                "ocr_recovery_per_page": ocr_recovery,
                "image_formats": image_formats, "image_assets": image_assets, "image_format_retries": image_audit,
                "options": options, "network": "Python socket/DNS/process audit blocked"}
        record = _refine_record(record, pdf)
        if len(record["markdown"].encode("utf-8")) > request["max_text_bytes"]:
            raise WorkerError("SDK 보정 Markdown 출력 예산을 초과했습니다.", "limit")
        _event(progress, len(pdf), len(pdf), "sdk_complete")
        return record


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
