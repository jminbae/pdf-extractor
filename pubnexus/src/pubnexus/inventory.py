"""0단계 — 인벤토리 원장(manifest).

모든 라우팅의 단일 진실. 각 PDF에 대해:
  파일경로 · sha1 · 쪽수 · 총글자수 · 텍스트층 여부 · DOI · 중복그룹
을 판정해 manifest.jsonl 로 기록한다. 이후 단계는 전부 이 원장을 읽는다.

대규모(3만 편) 대비:
  · 파일 단위로 partial 원장에 증분 저장 → 중단 후 재개 가능(이미 스캔한 파일 건너뜀)
  · DOI 미추출 시 Crossref 제목 매칭으로 보강, 그래도 실패면 unidentified.jsonl 로
    '무음 탈락' 대신 명시적으로 분리(설계서 식별 파이프라인 2단계)
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import fitz  # PyMuPDF
from rapidfuzz import fuzz

from . import utils
from .utils import HttpClient, log


def sha1_of(path: Path, buf_size: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(buf_size):
            h.update(chunk)
    return h.hexdigest()


def probe_pdf(path: Path, scan_pages: int, scanned_threshold: int,
              doi_re: re.Pattern) -> dict:
    """단일 PDF 프로빙: 쪽수, 글자수, 텍스트층, DOI."""
    rec: dict = {
        "file": str(path), "filename": path.name, "sha1": sha1_of(path),
        "pages": 0, "total_chars": 0, "chars_per_page": 0,
        "has_text_layer": False, "is_scanned_candidate": False,
        "doi": None, "doi_source": None, "title_guess": "", "error": None,
    }
    try:
        doc = fitz.open(path)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"open_fail: {e}"
        return rec
    try:
        rec["pages"] = doc.page_count
        page_texts = [doc[i].get_text() for i in range(doc.page_count)]
        total = sum(len(t) for t in page_texts)
        rec["total_chars"] = total
        rec["chars_per_page"] = int(total / doc.page_count) if doc.page_count else 0
        rec["has_text_layer"] = total >= scanned_threshold
        rec["is_scanned_candidate"] = not rec["has_text_layer"]

        # DOI: PDF 메타 → 앞쪽 페이지 → 전체 텍스트(일부 저널은 뒤쪽/각주에 표기)
        head = "".join(page_texts[:scan_pages])
        meta_doi = _find_doi(doc.metadata.get("subject", "") or "", doi_re)
        doi = meta_doi or _find_doi(head, doi_re) or _find_doi("".join(page_texts), doi_re)
        rec["doi"] = doi
        rec["doi_source"] = "pdf" if doi else None

        # 제목: 폰트 기반(1페이지 최대 폰트 텍스트) — 한국어/영어 모두 안정적
        rec["title_guess"] = _extract_title(doc[0]) if doc.page_count else ""
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"probe_fail: {e}"
    finally:
        doc.close()
    return rec


def _find_doi(text: str, doi_re: re.Pattern) -> str | None:
    m = doi_re.search(text or "")
    return utils.clean_doi(m.group(0)) if m else None


def _extract_title(page) -> str:
    """1페이지에서 가장 큰 폰트의 텍스트(=제목)를 추출. 인접 동일크기 줄 병합.

    '가장 긴 줄' 휴리스틱보다 안정적 — 본문/소속/캡션 조각에 속지 않고,
    한국어·영어 제목을 모두 잡는다. DOI 미표기 논문의 Crossref 회수에 핵심.
    """
    lines = []  # (size, y, text)
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            txt = utils.norm_text("".join(s["text"] for s in l["spans"]))
            sz = max((s["size"] for s in l["spans"]), default=0.0)
            if len(txt) >= 4:
                lines.append((round(sz, 1), l["bbox"][1], txt))
    if not lines:
        return ""
    max_sz = max(s for s, _, _ in lines)
    # 최대 폰트 줄들(제목은 1~2줄에 걸치기도) — 위→아래 순서로 병합
    title_lines = sorted((y, t) for s, y, t in lines if s >= max_sz - 0.1)
    title = utils.norm_text(" ".join(t for _, t in title_lines))
    # 저널 머리말류(전부 대문자 & 매우 짧음)만 걸러냄
    return title[:300]


def assign_dup_groups(records: list[dict]) -> None:
    """중복 그룹 부여(in-place).

    DOI가 있으면 DOI 키로, 없으면 sha1(바이트 동일 파일) 키로 그룹핑한다.
    각 그룹의 첫 등장 레코드를 is_primary=True 로.
    """
    for r in records:
        r["dup_group"] = f"doi:{r['doi']}" if r.get("doi") else f"sha1:{r['sha1']}"
    seen: set[str] = set()
    for r in records:
        g = r["dup_group"]
        r["is_primary"] = g not in seen
        seen.add(g)


def resolve_missing_dois(records: list[dict], cfg: dict) -> int:
    """DOI 미추출 레코드를 Crossref 제목 퍼지매칭으로 보강. 보강 건수 반환."""
    missing = [r for r in records if not r.get("doi") and r.get("title_guess")]
    if not missing:
        return 0
    md = cfg["metadata"]
    thr = cfg["identify"].get("title_match_threshold", 90)
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"],
                      timeout=md["timeout_sec"])
    n = 0
    log(f"[0단계] DOI 미추출 {len(missing)}편 → Crossref 제목 매칭 시도")
    for r in missing:
        title = r["title_guess"]
        try:
            data = http.get_json("https://api.crossref.org/works",
                                 params={"query.bibliographic": title, "rows": 1,
                                         "mailto": md["email"]})
            items = (data or {}).get("message", {}).get("items", [])
            if not items:
                continue
            cand = items[0]
            cand_title = (cand.get("title") or [""])[0]
            score = fuzz.token_set_ratio(title, cand_title)
            if score >= thr and cand.get("DOI"):
                r["doi"] = utils.clean_doi(cand["DOI"])
                r["doi_source"] = f"crossref_title({score})"
                n += 1
        except Exception as e:  # noqa: BLE001
            log(f"      ! Crossref 매칭 실패: {e}")
    log(f"[0단계] Crossref 보강 성공 {n}/{len(missing)}")
    return n


def build_manifest(config: dict | None = None, resume: bool = True) -> list[dict]:
    cfg = config or utils.load_config()
    input_dir = utils.resolve(cfg["project"]["input_dir"])
    work = utils.resolve(cfg["project"]["work_dir"])
    scan_pages = cfg["identify"]["scan_pages"]
    threshold = cfg["identify"]["scanned_char_threshold"]
    doi_re = re.compile(cfg["identify"]["doi_regex"], re.I)

    pdfs = sorted(input_dir.rglob("*.pdf"))
    partial = work / "manifest.partial.jsonl"

    # 재개: 이미 스캔된 파일은 건너뜀
    done = {}
    if resume and partial.exists():
        for rec in utils.read_jsonl(partial):
            done[rec["file"]] = rec
    log(f"[0단계] 인벤토리: PDF {len(pdfs)}개 @ {input_dir}"
        + (f" (재개: 기존 {len(done)}건)" if done else ""))

    records = []
    partial.parent.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(pdfs, 1):
        key = str(p)
        if key in done:
            records.append(done[key]); continue
        rec = probe_pdf(p, scan_pages, threshold, doi_re)
        records.append(rec)
        _append_jsonl(partial, rec)   # 증분 저장(중단돼도 여기까진 보존)
        flag = "ERR" if rec["error"] else ("scan?" if rec["is_scanned_candidate"] else "ok")
        log(f"  [{i:>3}/{len(pdfs)}] {flag:<5} {rec['pages']:>3}p "
            f"doi={rec['doi'] or '-'}  {rec['filename'][:46]}")

    if cfg["identify"].get("resolve_missing_doi"):
        resolve_missing_dois(records, cfg)

    assign_dup_groups(records)

    out = work / "manifest.jsonl"
    utils.write_jsonl(out, records)

    # 미식별(DOI 없음) 논문은 '무음 탈락' 대신 별도 큐로 표면화
    unidentified = [r for r in records if not r.get("doi")]
    if unidentified:
        utils.write_jsonl(work / "unidentified.jsonl", unidentified)

    n_text = sum(r["has_text_layer"] for r in records)
    n_doi = sum(bool(r["doi"]) for r in records)
    n_primary = sum(r["is_primary"] for r in records)
    log(f"[0단계] 완료 → {out}")
    log(f"        born-digital {n_text}/{len(records)} · DOI {n_doi}/{len(records)} · "
        f"고유논문 {n_primary} (중복 {len(records) - n_primary})")
    if unidentified:
        log(f"        ⚠ 미식별(DOI 없음) {len(unidentified)}편 → unidentified.jsonl "
            f"(후속 단계 자동 제외)")
    return records


def _append_jsonl(path: Path, row: dict):
    import json
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    build_manifest()
