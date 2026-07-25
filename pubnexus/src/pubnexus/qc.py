"""4단계 — QC 게이트(설계서 8단계).

3만 편은 눈으로 못 본다. 자동 품질 신호로 실패 건만 걸러낸다.
  · 텍스트층/페이지당 글자수
  · 섹션 헤딩 수(0이면 실패)
  · 초록 일치도(추출 vs PubMed) — 강력한 검증 신호
  · 참고문헌 수 vs Crossref 대조
  · 문자 깨짐 비율(CID/치환문자)
"""
from __future__ import annotations

import re
from pathlib import Path

from rapidfuzz import fuzz

from . import utils
from .utils import log

# 치환문자/제어문자 = OCR·폰트 깨짐 신호
_BROKEN_RE = re.compile(r'[�\x00-\x08\x0b\x0c\x0e-\x1f]')


def score_doc(doc: dict, manifest_rec: dict, meta: dict) -> dict:
    body_text = " ".join(p["text"] for s in doc["sections"] for p in s["paragraphs"])
    n_sec = len(doc["sections"])
    n_par = sum(len(s["paragraphs"]) for s in doc["sections"])
    n_chars = len(body_text)
    source = doc.get("source", "")
    # 초록 대조는 '문서에서 실제 추출된 초록 vs API 초록'일 때만 유효.
    # 소스 종류가 아니라 실제 추출 성공 여부(abstract_source)로 판정 → 순환검증 차단.
    extracted_abstract = doc.get("abstract_source") == "extracted"

    flags: list[str] = []
    signals: dict = {}

    # 1) 텍스트층 / 페이지당 글자수
    pages = manifest_rec.get("pages", 0) or 1
    cpp = manifest_rec.get("chars_per_page", 0)
    signals["chars_per_page"] = cpp
    if manifest_rec.get("is_scanned_candidate"):
        flags.append("scanned_no_text_layer")

    # 2) 섹션 헤딩 수
    signals["n_sections"] = n_sec
    signals["n_paragraphs"] = n_par
    if n_sec == 0 or n_par == 0:
        flags.append("no_sections")

    # 3) 초록 일치도 (추출 초록 vs PubMed 초록) — 추출 소스일 때만 유효
    api_abs = meta.get("abstract_pubmed") or meta.get("abstract") or ""
    doc_abs = doc.get("abstract") or ""
    if extracted_abstract and api_abs and doc_abs:
        ratio = fuzz.token_set_ratio(doc_abs, api_abs) / 100.0
        signals["abstract_match"] = round(ratio, 3)
        if ratio < 0.5:
            flags.append("abstract_mismatch")
    else:
        signals["abstract_match"] = None   # 폴백 등 순환검증은 신호 없음

    # 4) 참고문헌 수 vs Crossref
    n_ref = len(doc.get("references", []))
    cref = meta.get("crossref_ref_count")
    signals["n_references"] = n_ref
    signals["crossref_ref_count"] = cref
    if cref:
        if n_ref == 0:
            flags.append("no_references_extracted")   # 참조 미추출(폴백 한계)
        else:
            rel = abs(n_ref - cref) / max(cref, 1)
            signals["ref_count_delta"] = round(rel, 2)
            if rel > 0.5:
                flags.append("ref_count_off")

    # 5) 문자 깨짐 비율
    broken = len(_BROKEN_RE.findall(body_text))
    broken_ratio = broken / max(n_chars, 1)
    signals["broken_char_ratio"] = round(broken_ratio, 5)
    if broken_ratio > 0.005:
        flags.append("char_corruption")

    # 종합 점수 (신호별 가중, 0~1)
    score = 1.0
    if "scanned_no_text_layer" in flags: score -= 0.5
    if "no_sections" in flags: score -= 0.4
    if "abstract_mismatch" in flags: score -= 0.2
    if "ref_count_off" in flags: score -= 0.1
    if "no_references_extracted" in flags: score -= 0.15
    if "char_corruption" in flags: score -= 0.2
    # 추출 소스는 초록대조로 상한 조정, 폴백은 구조적 감점(참조링크 없음·섹션 약함)
    if extracted_abstract and signals.get("abstract_match") is not None:
        score = min(score, 0.6 + 0.4 * signals["abstract_match"])
    if source == "pdf_fallback":
        flags.append("fallback_needs_grobid")
        score = min(score, 0.7) - 0.05
    score = max(0.0, round(score, 3))

    return {"quality_score": score, "flags": flags, "signals": signals,
            "source": source,
            "pass": score >= 0.6 and "no_sections" not in flags}


def run(config: dict | None = None) -> list[dict]:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"

    manifest = {r["doi"]: r for r in utils.read_jsonl(work / "manifest.jsonl")
                if r.get("doi")}
    metas = {m["doi"]: m for m in
             (utils.read_json(p) for p in (work / "meta").glob("*.json"))}

    reports = []
    docs = sorted(norm_dir.glob("*.json"))
    log(f"[4단계] QC 게이트: {len(docs)}편")
    for p in docs:
        doc = utils.read_json(p)
        doi = doc.get("paper_id")
        rec = manifest.get(doi, {})
        meta = metas.get(doi, {})
        qc = score_doc(doc, rec, meta)
        # 점수를 정규화 JSON 에도 반영
        doc["quality_score"] = qc["quality_score"]
        doc["qc"] = qc
        utils.write_json(p, doc)
        reports.append({"paper_id": doi, "source": doc.get("source"), **qc})
        mark = "PASS" if qc["pass"] else "FAIL"
        log(f"  {mark} q={qc['quality_score']:.2f} "
            f"absΔ={qc['signals'].get('abstract_match')} "
            f"ref={qc['signals'].get('n_references')}/{qc['signals'].get('crossref_ref_count')} "
            f"flags={qc['flags']}  {doi}")

    utils.write_jsonl(work / "qc_report.jsonl", reports)
    n_pass = sum(r["pass"] for r in reports)
    log(f"[4단계] 완료: PASS {n_pass}/{len(reports)} → {work/'qc_report.jsonl'}")
    return reports


if __name__ == "__main__":
    run()
