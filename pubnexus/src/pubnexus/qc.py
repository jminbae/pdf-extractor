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

from . import metadata, utils
from .utils import log

# 치환문자/제어문자 = OCR·폰트 깨짐 신호
_BROKEN_RE = re.compile(r'[�\x00-\x08\x0b\x0c\x0e-\x1f]')

# 초록 검증에 쓸 원본 PDF 를 찾는다. 정본의 source_file 은 수집 당시 PC 의
# 경로라 그대로는 열리지 않는다 → 파일명만 떼어 현재 PDF 폴더에서 찾는다.
_PDF_DIRS: list[Path] = []


def _pdf_for(doc: dict) -> Path | None:
    src = str(doc.get("source_file") or "")
    if not src:
        return None
    p = Path(src)
    if p.exists():
        return p
    for d in _PDF_DIRS:
        cand = d / p.name
        if cand.exists():
            return cand
    return None


def score_doc(doc: dict, manifest_rec: dict, meta: dict) -> dict:
    body_text = " ".join(p["text"] for s in doc["body_text"] for p in s["paragraphs"])
    n_sec = len(doc["body_text"])
    n_par = sum(len(s["paragraphs"]) for s in doc["body_text"])
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

    # 3) 초록 검증 — '있는가'가 아니라 '이 논문의 초록인가'를 본다.
    #
    # 예전 게이트는 token_set_ratio < 0.5 하나였고 실측 결과 **0편**을 잡았다.
    # 집합 기반 비율이라 297자로 잘린 초록이 1498자 정본과 96점을 받는다
    # (10.1016/j.jaad.2019.10.055 실측). 잘림과 이물질 혼입은 방향이 다른
    # 결함이므로 정밀도·재현율을 따로 재고, 증인을 두 개(PDF 표제·API)로 늘린다.
    doc_abs = doc.get("abstract") or ""
    body_first = ""
    for s in doc["body_text"]:
        for p in s["paragraphs"]:
            if p.get("text"):
                body_first = p["text"]
                break
        if body_first:
            break
    pdf_path = _pdf_for(doc)
    verdict = metadata.verify_abstract(
        doc_abs, meta, pdf_path, body_first=body_first,
        title=(doc.get("meta") or {}).get("title") or meta.get("title") or "")
    signals.update(verdict["signals"])
    # 하위호환: 예전 신호 이름을 유지하되 의미는 '재현율'로 바꾼다
    signals["abstract_match"] = verdict["signals"].get("abs_recall")
    for r in verdict["reasons"]:
        flags.append(r)
    if verdict["ok"] is None and doc_abs:
        flags.append("abstract_unverified")   # 증인 없음 — 통과로 위장하지 않는다

    # 3b) 기본키 게이트 — meta.doi 가 실제로 해소되는가.
    # 정규식은 통과하지만 어디에도 존재하지 않는 DOI('10.1200/jco.18')는
    # **기본키가 깨진 레코드**다. 조용히 두면 중복·인용그래프가 전부 어긋난다.
    doi = doc.get("paper_id") or ""
    signals["doi_resolved"] = meta.get("doi_resolved")
    if not meta.get("sources_ok"):
        # 어떤 API 도 이 DOI 를 모른다 = 해소 실패(네트워크 실패와 구분하려면
        # meta 수집이 성공했는지를 본다. sources_ok 가 비면 그 자체가 신호다)
        flags.append("doi_unresolved")
    elif meta.get("doi_resolved") is False:
        flags.append("doi_unresolved")

    # 3c) 신원 게이트 — 이 레코드의 제목이 이 PDF 의 논문 제목과 같은가.
    # 2단 조판에서 앞 레터의 DOI 로 파일링되면 제목·저자·PMID 가 통째로
    # 남의 것이 된다(10.1111/jdv.16524). 그때도 DOI 자체는 멀쩡히 해소된다.
    rec_title = (doc.get("meta") or {}).get("title") or ""
    ident = metadata.verify_identity(rec_title, doc.get("source_file") or "")
    signals["identity_score"] = ident["score"]
    if ident["ok"] is False:
        flags.append("identity_mismatch")

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
    if "ref_count_off" in flags: score -= 0.1
    if "no_references_extracted" in flags: score -= 0.15
    if "char_corruption" in flags: score -= 0.2
    # 초록 결함은 종류별로 감점한다(예전엔 abstract_mismatch 하나였다)
    if "abstract_truncated_or_foreign" in flags: score -= 0.25
    if "abstract_polluted" in flags: score -= 0.15
    if "abstract_is_body_first_paragraph" in flags: score -= 0.3
    if "abstract_title_zero_overlap" in flags: score -= 0.3
    # 기본키·신원은 치명적이다 — 이 레코드는 다른 논문과 섞인다
    if "doi_unresolved" in flags: score -= 0.5
    if "identity_mismatch" in flags: score -= 0.5
    # 추출 소스는 초록대조로 상한 조정, 폴백은 구조적 감점(참조링크 없음·섹션 약함)
    if extracted_abstract and signals.get("abstract_match") is not None:
        score = min(score, 0.6 + 0.4 * signals["abstract_match"])
    if source == "pdf_fallback":
        flags.append("fallback_needs_grobid")
        score = min(score, 0.7) - 0.05
    score = max(0.0, round(score, 3))

    # 기본키가 깨졌거나 신원이 다르면 점수와 무관하게 통과시키지 않는다.
    # 이 두 결함은 '품질이 낮은 레코드'가 아니라 **틀린 레코드**다.
    fatal = {"no_sections", "doi_unresolved", "identity_mismatch"}
    return {"quality_score": score, "flags": flags, "signals": signals,
            "source": source,
            "pass": score >= 0.6 and not (fatal & set(flags))}


def run(config: dict | None = None) -> list[dict]:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"
    # 초록 검증의 1차 증인이 원본 PDF 이므로 그 폴더를 알려준다
    _PDF_DIRS.clear()
    _PDF_DIRS.append(utils.resolve(cfg["project"]["input_dir"]))

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
