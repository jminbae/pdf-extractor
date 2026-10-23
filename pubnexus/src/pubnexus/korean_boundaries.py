"""Conservative physical-line repairs using the published Kiwi APIs.

SDK layout, native text and their original offsets are retained. This module
does not create paragraphs or infer a document layout.
"""
from __future__ import annotations

import copy
from collections import Counter
from bisect import bisect_right
import hashlib
from importlib.metadata import version
import re

VERSION = 1
_HANGUL = re.compile(r"[가-힣]")


class KoreanCancelled(RuntimeError):
    pass


def make_kiwi():
    """Use the already installed model; never fetch a model or another SDK."""
    if version("kiwipiepy") != "0.24.1" or version("kiwipiepy_model") != "0.24.0":
        raise RuntimeError("Verified Kiwi 0.24.1/model 0.24.0 is required")
    from kiwipiepy import Kiwi
    return Kiwi(model_type="cong", num_workers=1, typos=None, load_typo_dict=False)


def _compact(text):
    return "".join(c for c in text if not c.isspace())


def _source_bounds(bounds, matrix):
    a, b, c, d, e, f = matrix
    x0, y0, x1, y1 = bounds
    points = [(a*x+c*y+e, b*x+d*y+f) for x, y in ((x0,y0),(x1,y0),(x0,y1),(x1,y1))]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _lines(native, bounds):
    x0, y0, x1, y1 = bounds
    result = []
    for block in native.get("blocks", []):
        for line in block.get("lines", []):
            a, b, c, d = line["bbox"]
            area = max(0, c-a) * max(0, d-b)
            overlap = max(0, min(x1, c)-max(x0, a)) * max(0, min(y1, d)-max(y0, b))
            if not area or overlap < area * .8:
                continue
            spans = line.get("spans", [])
            result.append({"text": "".join(s["text"] for s in spans), "bbox": list(line["bbox"]),
                           "fonts": sorted({s.get("font", "") for s in spans}),
                           "sizes": sorted({round(s.get("size", 0), 2) for s in spans})})
    return result


def _witnesses(native_pages):
    return {word for native in native_pages.values() for block in native.get("blocks", [])
            for line in block.get("lines", []) for span in line.get("spans", [])
            for word in re.findall(r"[가-힣]+", span["text"])}


def _join_evidence(left, right, kiwi, witnesses=()):
    joined = left + right
    glued, spaces = kiwi.glue([left, right], return_space_insertions=True)
    if spaces != [False] or glued != joined:
        return None
    spaced = kiwi.space(joined)
    if _compact(spaced) != _compact(joined):
        return None
    boundary = len(_compact(left))
    characters = [i for i, c in enumerate(spaced) if not c.isspace()]
    if spaced[characters[boundary-1]+1:characters[boundary]]:
        return None
    tokens = kiwi.analyze(joined, top_n=1)[0][0]
    crossing = [t for t in tokens if t.start < len(left) < t.end]
    nouns = [t for t in crossing if t.tag.startswith("NN") and joined[t.start:t.end] == t.form]
    tail, head = re.search(r"[가-힣]+$", left), re.match(r"[가-힣]+", right)
    witness = tail[0]+head[0] if tail and head and tail[0]+head[0] in witnesses else None
    # Morphemes inside one attested word need not cross its physical line break.
    # Let public spacing plus an exact native word witness handle such endings.
    if not nouns and not (witness and tokens):
        return None
    evidence = {"provider": "Kiwi.glue+space+analyze", "crossing_tokens": [t.form for t in crossing]}
    if witness:
        evidence["source_witness"] = witness
        evidence["boundary_tokens"] = [{"form": t.form, "tag": t.tag} for t in tokens
                                       if t.start <= len(left) <= t.end]
    return evidence


def _position_mapper(edits):
    ends, shifts = [], [0]
    for edit in edits:
        ends.append(edit["end"])
        shifts.append(shifts[-1] + len(edit["after"]) - (edit["end"]-edit["start"]))
    def mapped(position):
        i = bisect_right(ends, position)
        if i < len(edits) and edits[i]["start"] < position:
            return edits[i]["start"] + shifts[i]
        return position + shifts[i]
    return mapped


def _reference_scope(chunks):
    protected, active = set(), False
    for n, chunk in enumerate(chunks):
        for box in chunk["page_boxes"]:
            start, end = box["pos"]
            text = chunk["text"][start:end].strip().strip("#*_ ")
            if re.fullmatch(r"(?:참고\s*문헌|references|bibliography)\s*[:：]?", text, re.I):
                active = True
            if active:
                protected.add((n, box["index"]))
    return protected


def _cross_page_joins(output, native_pages, kiwi, cancel, excluded):
    """Join only an attested word split across adjacent SDK text blocks."""
    chunks, pages = output["chunks"], output["pages"]
    report = output["korean_refinement"]
    report["cross_page_joins"] = []
    witnesses = _witnesses(native_pages)
    replacements, owners, lookup = {}, {}, {}
    before_body, before_protected = [], []
    entries = []
    for n, chunk in enumerate(chunks):
        row = []
        for box in chunk["page_boxes"]:
            start, end = box["pos"]
            item = {"page": n, "box": box, "text": chunk["text"][start:end]}
            lookup[(n, box["index"])] = item
            row.append(item)
            (before_body if box["class"] == "text" else before_protected).append(_compact(item["text"]))
        entries.append(row)
    for n in range(len(chunks)-1):
        if cancel and cancel():
            raise KoreanCancelled("Korean boundary refinement cancelled")
        if n not in native_pages or n+1 not in native_pages:
            continue
        lefts = [i for i, item in enumerate(entries[n]) if item["box"]["class"] == "text" and item["text"].strip()]
        rights = [i for i, item in enumerate(entries[n+1]) if item["box"]["class"] == "text" and item["text"].strip()]
        if not lefts or not rights:
            continue
        li, ri = lefts[-1], rights[0]
        left, right = entries[n][li], entries[n+1][ri]
        if (n, left["box"]["index"]) in excluded or (n+1, right["box"]["index"]) in excluded:
            continue
        floats = [item for item in entries[n][li+1:]+entries[n+1][:ri] if item["text"].strip()]
        if any(item["box"]["class"] not in ("picture", "caption") for item in floats):
            continue
        bounds, lines = [], []
        for item in (left, right):
            p = item["page"]
            bounds.append(_source_bounds(item["box"]["bbox"], pages[p].get("sdk_to_source", [1,0,0,1,0,0])))
            lines.append(_lines(native_pages[p], bounds[-1]))
        if any(not row or _compact(item["text"]) != _compact("".join(l["text"] for l in row))
               for item, row in zip((left, right), lines)):
            continue
        a, b = lines[0][-1], lines[1][0]
        if (a["fonts"] != b["fonts"] or a["sizes"] != b["sizes"] or len(a["sizes"]) != 1
                or a["sizes"][0] <= 0 or a["text"].endswith(" ") or b["text"].startswith(" ")):
            continue
        tolerance = max(1, a["sizes"][0] * .6)
        widths = [box[2]-box[0] for box in bounds]
        if (abs(a["bbox"][2]-bounds[0][2]) > tolerance or abs(b["bbox"][0]-bounds[1][0]) > tolerance
                or min(widths) <= 0 or not .75 <= widths[0]/widths[1] <= 1.33):
            continue
        tail, head = re.search(r"[가-힣]+$", a["text"]), re.match(r"[가-힣]+", b["text"])
        if not tail or not head or tail[0]+head[0] not in witnesses:
            continue
        if kiwi is None:
            try:
                kiwi = make_kiwi()
            except (ImportError, RuntimeError, OSError) as error:
                report.update(status="partial", warnings=["Kiwi boundary correction unavailable: " + str(error)])
                break
        evidence = _join_evidence(a["text"].strip(), b["text"].strip(), kiwi, witnesses)
        if not evidence:
            continue
        key = (n, left["box"]["index"])
        master = owners.get(key, key)
        master_box = lookup[master]["box"]
        base = replacements.get(master, left["text"]).rstrip()
        replacements[master] = base+right["text"].lstrip()
        right_key = (n+1, right["box"]["index"])
        replacements[right_key] = ""
        owners[right_key] = master
        locations = [{"page": item["page"]+1, "sdk_bbox": list(item["box"]["bbox"]),
                      "raw_pos": list(item["box"].get("pos_raw", item["box"]["pos"])),
                      "block_index": item["box"]["index"]} for item in (left, right)]
        existing = master_box.get("korean_source_locations")
        if existing:
            existing[-1]["corrected_local_pos"][1] = min(existing[-1]["corrected_local_pos"][1], len(base))
            locations[1]["corrected_local_pos"] = [len(base), len(replacements[master])]
            existing.append(locations[1])
        else:
            locations[0]["corrected_local_pos"] = [0, len(base)]
            locations[1]["corrected_local_pos"] = [len(base), len(replacements[master])]
            master_box["korean_source_locations"] = copy.deepcopy(locations)
        right["box"]["korean_merged_into"] = {"page": master[0]+1, "block_index": master[1]}
        report["cross_page_joins"].append({"left": locations[0], "right": locations[1],
            "witness": tail[0]+head[0], "evidence": evidence,
            "floats_after_paragraph": [{"page": item["page"]+1, "block_index": item["box"]["index"]} for item in floats]})
    after_body, after_protected = [], []
    for n, chunk in enumerate(chunks):
        text, position, length, parts = chunk["text"], 0, 0, []
        for item in entries[n]:
            box = item["box"]
            start, end = box["pos"]
            gap = text[position:start]
            parts.append(gap)
            length += len(gap)
            updated = replacements.get((n, box["index"]), item["text"])
            new_start = length
            parts.append(updated)
            length += len(updated)
            box["pos"] = [new_start, new_start+len(updated)]
            position = end
            (after_body if box["class"] == "text" else after_protected).append(_compact(updated))
        parts.append(text[position:])
        chunk["text"] = "".join(parts)
    if "".join(before_body) != "".join(after_body) or before_protected != after_protected:
        raise ValueError("Korean page join changed body order or protected blocks")


def refine_record(record, pdf, *, kiwi=None, cancel=None):
    """Return a new SDK record with only proven native line-boundary edits."""
    digest = hashlib.sha256(record["markdown"].encode("utf-8")).hexdigest()
    prior = record.get("korean_refinement", {})
    if prior.get("version") == VERSION and prior.get("output_sha256") == digest:
        return copy.deepcopy(record)
    output = copy.deepcopy(record)
    output.setdefault("markdown_raw", record["markdown"])
    output.setdefault("chunks_raw", copy.deepcopy(record["chunks"]))
    output["korean_input_markdown"] = record["markdown"]
    output["korean_input_chunks"] = copy.deepcopy(record["chunks"])
    report = {"version": VERSION, "status": "complete", "edits": [], "warnings": [], "checked_boundaries": 0}
    output["korean_refinement"] = report
    report["input_sha256"] = digest
    excluded = _reference_scope(record["chunks"])
    report["protected_reference_blocks"] = len(excluded)
    native_pages = {}
    for number, page in enumerate(record["pages"]):
        if cancel and cancel():
            raise KoreanCancelled("Korean boundary refinement cancelled")
        if not page.get("ocr_performed"):
            # Preserve native whitespace/ligatures, without embedding image bytes.
            native_pages[number] = pdf[number].get_text("dict", flags=3)
    witnesses = _witnesses(native_pages)
    for number, chunk in enumerate(output["chunks"]):
        if cancel and cancel():
            raise KoreanCancelled("Korean boundary refinement cancelled")
        if record["pages"][number].get("ocr_performed"):
            continue
        native = native_pages[number]
        text, edits = chunk["text"], []
        for box in chunk["page_boxes"]:
            if box["class"] != "text" or (number, box["index"]) in excluded:
                continue
            start, end = box["pos"]
            content = text[start:end]
            bounds = _source_bounds(box["bbox"], record["pages"][number].get("sdk_to_source", [1,0,0,1,0,0]))
            lines = _lines(native, bounds)
            if len(lines) < 2 or _compact(content) != _compact("".join(l["text"] for l in lines)):
                continue
            positions = [i for i, c in enumerate(content) if not c.isspace()]
            consumed = 0
            for left, right in zip(lines, lines[1:]):
                if cancel and cancel():
                    raise KoreanCancelled("Korean boundary refinement cancelled")
                consumed += len(_compact(left["text"]))
                if not 0 < consumed < len(positions):
                    continue
                a, b = positions[consumed-1]+1, positions[consumed]
                gap = content[a:b]
                if not gap or not gap.isspace() or not _HANGUL.fullmatch(content[a-1]) or not _HANGUL.fullmatch(content[b]):
                    continue
                # A source-encoded word boundary is stronger than a model suggestion.
                if left["text"].endswith(" ") or right["text"].startswith(" "):
                    continue
                report["checked_boundaries"] += 1
                try:
                    if kiwi is None:
                        kiwi = make_kiwi()
                    evidence = _join_evidence(left["text"].strip(), right["text"].strip(), kiwi, witnesses)
                except (ImportError, RuntimeError, OSError) as error:
                    report.update(status="partial", warnings=["Kiwi boundary correction unavailable: " + str(error)])
                    evidence = None
                if evidence:
                    edits.append({"start": start+a, "end": start+b, "before": gap, "after": "",
                                  "page": number+1, "block_index": box["index"],
                                  "native_lines": [left["bbox"], right["bbox"]], "evidence": evidence})
        edits.sort(key=lambda e: e["start"])
        for edit in reversed(edits):
            text = text[:edit["start"]] + edit["after"] + text[edit["end"]:]
        mapped = _position_mapper(edits)
        for box in chunk["page_boxes"]:
            box["pos_raw"] = list(box["pos"])
            box["pos"] = [mapped(p) for p in box["pos"]]
        chunk["text"] = text
        report["edits"].extend(edits)
    before_join = "".join(c["text"] for c in output["chunks"])
    if _compact(before_join) != _compact(record["markdown"]):
        raise ValueError("Korean boundary correction changed non-whitespace text")
    _cross_page_joins(output, native_pages, kiwi, cancel, excluded)
    output["markdown"] = "".join(c["text"] for c in output["chunks"])
    if Counter(_compact(output["markdown"])) != Counter(_compact(record["markdown"])):
        raise ValueError("Korean page join changed non-whitespace characters")
    report["verification_required"] = bool(report["edits"] or report["cross_page_joins"])
    if report["verification_required"]:
        report["status"] = "partial"
        report["warnings"].append("Kiwi로 물리줄 공백 및 증거가 일치한 인접쪽 문단을 교정했습니다 — 원문 대조 필요")
    report["output_sha256"] = hashlib.sha256(output["markdown"].encode("utf-8")).hexdigest()
    return output
