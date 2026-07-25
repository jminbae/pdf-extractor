"""전수조사 감사 — 정규화 산출물의 추출 품질을 내용 수준까지 자동 점검.

QC 게이트(구조 신호)보다 깊게, "3만 편은 눈으로 못 본다"를 위한 자동 이상 탐지.
각 문서를 여러 신호로 검사해 문제 문서만 순위화해 뽑아낸다.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import utils
from .utils import log

LIGATURE = re.compile(r'[ﬁﬂﬀﬃﬄﬆ]')                     # 미해제 합자
BROKEN = re.compile(r'[�\x00-\x08\x0b\x0c\x0e-\x1f]')  # 치환/제어문자
CID = re.compile(r'\(cid:\d+\)')                          # 폰트 CID 미해석
FRONT_LEAK = re.compile(
    r'^\s*(received:|accepted:|correspondence\b|©|copyright|downloaded from|'
    r'e-?mail|how to cite|this article|funding information|conflict of interest|'
    r'\bdoi:\s*10\.)', re.I)
# 인용번호가 본문에 남음: "families.2–5" 처럼 문장부호 뒤 다중 인용이 붙은 경우
# (마침표 필수 → "mellitus480,764" 같은 큰 숫자 오탐 방지)
CITE_CONTAM = re.compile(r'\b[a-z]{4,}\.\d{1,3}[,\-–]\d{1,3}\b')
# 참고문헌 항목이 본문에 섞임: "Author AB, Cd EF. ... . Journal. 2019;..."
REF_LIKE = re.compile(r'^[A-Z][a-zA-Z\-]+ [A-Z]{1,3}(,| et al).{0,120}\.\s.{5,}\.\s.{0,60}\d{4}')
URL_LEAK = re.compile(r'https?://|www\.')


def _is_letter(doc: dict, mrec: dict, meta: dict) -> bool:
    """Letter/Comment/Editorial 여부 — 이런 글은 섹션이 없는 게 정상."""
    pt = " ".join((doc.get("meta") or {}).get("pub_types", []) + meta.get("pub_types", []))
    if re.search(r'letter|comment|editorial|reply|correspondence', pt, re.I):
        return True
    fn = mrec.get("filename", "")
    return "-L-" in fn or "-C-" in fn  # 파일명 규칙(letter/correspondence)


def audit_doc(doc: dict, mrec: dict, meta: dict) -> dict:
    paras = [p for s in doc["sections"] for p in s["paragraphs"]]
    body = " ".join(p["text"] for p in paras)
    nchars = len(body)
    flags: list[str] = []
    sig: dict = {}
    pages = mrec.get("pages", 0)
    is_letter = _is_letter(doc, mrec, meta)
    sig["is_letter"] = is_letter

    # 1) 추출 완성도(본문/원본 글자수). 참고문헌 제외라 낮은 건 정상, 극단만 플래그
    raw = mrec.get("total_chars", 0)
    sig["extract_ratio"] = round(nchars / raw, 2) if raw else 0
    if raw > 4000 and sig["extract_ratio"] < 0.12:
        flags.append("under_extracted")

    # 2) 섹션/문단
    sig["n_sections"] = len(doc["sections"])
    sig["n_paragraphs"] = len(paras)
    # Letter/편지류는 섹션·짧은 본문이 정상 → 제외
    if not is_letter and pages >= 3 and len(doc["sections"]) <= 1:
        flags.append("no_sections")
    if not is_letter and pages >= 4 and nchars < 1500:
        flags.append("too_little_body")

    # 3) 깨진 글자/합자/CID
    sig["ligatures"] = len(LIGATURE.findall(body))
    sig["broken"] = len(BROKEN.findall(body))
    sig["cid"] = len(CID.findall(body))
    if sig["ligatures"] > 8:
        flags.append("unresolved_ligatures")
    if sig["broken"] > 3:
        flags.append("broken_chars")
    if sig["cid"] > 0:
        flags.append("cid_artifacts")

    # 4) front matter 누수(본문 문단이 수신일/교신/저작권 등으로 시작)
    sig["frontmatter_leak"] = sum(1 for p in paras if FRONT_LEAK.search(p["text"]))
    if sig["frontmatter_leak"] >= 2:
        flags.append("frontmatter_leak")

    # 5) 인용번호 오염(GROBID/XML이 제거했어야 함)
    sig["cite_contam"] = len(CITE_CONTAM.findall(body))
    if sig["cite_contam"] >= 4:
        flags.append("cite_contamination")

    # 6) 참고문헌 항목이 본문에 섞임
    sig["ref_leak"] = sum(1 for p in paras if REF_LIKE.match(p["text"].strip()))
    if sig["ref_leak"] >= 3:
        flags.append("references_in_body")

    # 7) 이상 헤딩(숫자만/한 글자/문단이 통째로 헤딩된 과길이 >150)
    #    긴 소제목은 정상이므로 임계를 크게 잡음
    bad = 0
    for s in doc["sections"]:
        h = s["path"][-1] if s["path"] else ""
        if h and (re.fullmatch(r'[\d\s.|)]+', h) or len(h) == 1 or len(h) > 150):
            bad += 1
    sig["bad_headings"] = bad
    if bad >= 2:
        flags.append("suspicious_headings")

    # 8) 표: 비어있는 표
    tabs = doc.get("tables", [])
    sig["n_tables"] = len(tabs)
    sig["empty_tables"] = sum(1 for t in tabs if not (t.get("markdown") or "").strip())

    # 9) 중복 문단
    texts = [p["text"] for p in paras]
    sig["dup_paragraphs"] = len(texts) - len(set(texts))
    if sig["dup_paragraphs"] >= 3:
        flags.append("duplicate_paragraphs")

    # 10) 메타 제목 유무
    if not (doc.get("meta") or {}).get("title"):
        flags.append("no_title")

    # 11) 참고문헌 '누락'만 플래그: 추출이 Crossref의 절반 미만일 때(과다추출은 OK).
    #     Letter는 Crossref 카운트가 부정확해 제외.
    nref = len(doc.get("references", []))
    cref = meta.get("crossref_ref_count")
    sig["n_references"] = nref
    if not is_letter and cref and cref >= 8 and nref < cref * 0.5:
        flags.append("references_missing")

    return {"paper_id": doc.get("paper_id"), "source": doc.get("source"),
            "filename": Path(mrec.get("file", "")).name,
            "quality_score": doc.get("quality_score"),
            "flags": flags, "signals": sig}


def run(config: dict | None = None) -> list[dict]:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    manifest = {r["doi"]: r for r in utils.read_jsonl(work / "manifest.jsonl") if r.get("doi")}
    metas = {m["doi"]: m for m in
             (utils.read_json(p) for p in (work / "meta").glob("*.json"))}

    reports = []
    for p in sorted((work / "normalized").glob("*.json")):
        doc = utils.read_json(p)
        doi = doc.get("paper_id")
        reports.append(audit_doc(doc, manifest.get(doi, {}), metas.get(doi, {})))

    utils.write_jsonl(work / "audit_report.jsonl", reports)

    # 집계
    from collections import Counter
    flag_counts = Counter(f for r in reports for f in r["flags"])
    clean = [r for r in reports if not r["flags"]]
    flagged = [r for r in reports if r["flags"]]
    log(f"[감사] 전수조사 {len(reports)}편: 무결점 {len(clean)} · 플래그 {len(flagged)}")
    log("  플래그 분포:")
    for f, n in flag_counts.most_common():
        log(f"    {f:<24} {n}편")
    log("\n  가장 문제 많은 문서(플래그 수 순):")
    for r in sorted(flagged, key=lambda r: -len(r["flags"]))[:20]:
        log(f"    [{r['source'][:6]:<6}] {','.join(r['flags'])}  | {r['filename'][:42]}")
    log(f"\n[감사] → {work / 'audit_report.jsonl'}")
    return reports


if __name__ == "__main__":
    run()
