"""참고문헌 목록 — **파싱하지 않는다. iCite(NIH) 에서 가져온다.**

설계 변경(2026-07-26, 원장 지시). PDF/GROBID 에서 뽑은 references[] 는 구조적으로
못 믿는다. 실측된 참고문헌 결함이 전부 여기서 나왔다:
  · DOI 순열 뒤섞임 — 한 편에서 125개 중 98개가 '같은 목록 안 다른 참조'의 DOI
  · 이웃 논문의 참고문헌 혼입, 각주 doi.org 줄을 흡수한 자기인용
  · 펀딩 문구가 참고문헌 항목으로 등록, 목록 0개
이 결함들은 **형식·해소가능성 검사를 전부 통과한다**(DOI 가 진짜로 존재하니까).
그래서 규칙 탐지로는 영원히 못 잡는다 → 소스를 바꾼다.

iCite: https://icite.od.nih.gov/api/pubs?pmids=<pmid>[,<pmid>...]
  인증 불필요·무료·배치조회. `references` 가 PMID 목록으로 온다.
  참조 PMID 를 다시 배치조회하면 제목·저자·연도·저널·DOI 를 채울 수 있다.

지키는 원칙:
  · PMID 가 없거나 iCite 에 없으면 **references[] 를 비운다.** 파싱으로 돌아가지
    않는다 — 비어 있는 것이 틀린 것보다 낫다.
  · 본문 인용 마커 [15] 는 그대로 둔다(그건 본문 정보다).
  · **cited_refs 는 연결하지 않는다.** iCite 의 references 순서는 논문의 번호순이
    아니다(실측: 10.1002/iid3.316 은 지면 1,2,3,4 = Kim·Oh·Won·Kramer 인데
    iCite 는 Kim·Kramer·자기자신·Won·Oh 순). 표시번호와 API 순서를 맞출 근거가
    없으므로 링크하지 않는다 — 틀린 출처 링크는 없는 링크보다 해롭다.
  · 응답은 data/icite/ 에 캐시해 오프라인 재현이 되게 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import utils
from .utils import HttpClient, log, norm_text

ICITE = "https://icite.od.nih.gov/api/pubs"
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

_BATCH = 100          # iCite 는 쉼표로 여러 PMID 를 받는다


# ── iCite 수집(캐시 우선) ────────────────────────────────────────────
def fetch_icite(pmids, cache_dir: Path, http: HttpClient) -> dict[str, dict]:
    """PMID → iCite 레코드. 캐시에 있으면 네트워크를 타지 않는다.

    '조회했으나 iCite 에 없음' 도 캐시한다(빈 dict) — 3만 편 규모에서 없는
    PMID 를 매번 다시 묻지 않기 위해서다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    todo: list[str] = []
    for pm in {str(p) for p in pmids if p}:
        f = cache_dir / f"{pm}.json"
        if f.exists():
            try:
                out[pm] = utils.read_json(f) or {}
                continue
            except Exception:  # noqa: BLE001 — 깨진 캐시는 다시 받는다
                pass
        todo.append(pm)

    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        try:
            data = http.get_json(ICITE, params={"pmids": ",".join(chunk)})
        except Exception as e:  # noqa: BLE001 — 배치 실패가 전체를 멈추지 않는다
            log(f"      ! iCite 배치 실패({len(chunk)}건): {e}")
            continue
        got = {str(r.get("pmid")): r for r in (data or {}).get("data", []) if r}
        for pm in chunk:
            rec = got.get(pm) or {}
            utils.write_json(cache_dir / f"{pm}.json", rec)
            out[pm] = rec
    return out


def doi_to_pmid(dois, cache_path: Path, http: HttpClient) -> dict[str, str]:
    """DOI → PMID (NCBI ID Converter). meta.pmid 가 비었을 때만 쓴다."""
    cache = utils.read_json(cache_path) if cache_path.exists() else {}
    todo = [d for d in dois if d and d not in cache]
    for i in range(0, len(todo), 50):       # idconv 는 200개까지 받지만 보수적으로
        chunk = todo[i:i + 50]
        try:
            data = http.get_json(IDCONV, params={"ids": ",".join(chunk),
                                                 "format": "json"})
        except Exception as e:  # noqa: BLE001
            log(f"      ! idconv 실패: {e}")
            continue
        for rec in (data or {}).get("records", []) or []:
            d = (rec.get("doi") or "").lower()
            if d:
                cache[d] = rec.get("pmid") or ""
        for d in chunk:                     # 응답에 없으면 '없음'으로 확정
            cache.setdefault(d, "")
    utils.write_json(cache_path, cache)
    return {k: v for k, v in cache.items() if v}


# ── 참조 레코드 구성 ─────────────────────────────────────────────────
def _authors(rec: dict) -> list[str]:
    out = []
    for a in rec.get("authors") or []:
        nm = (a.get("fullName") or "").strip()
        if not nm:
            fam, giv = a.get("lastName") or "", a.get("firstName") or ""
            nm = f"{fam}, {giv}".strip(", ")
        if nm:
            out.append(nm)
    return out


def build_references(own_pmid: str, icite_rec: dict,
                     detail: dict[str, dict]) -> list[dict]:
    """iCite 의 references(PMID 목록)로 참고문헌을 만든다.

    key 는 **PMID 기반 안정 키**로 준다(b0/b1 같은 위치 키는 순서를 신뢰한다는
    뜻이 되어버린다). 자기 자신은 뺀다 — iCite 가 자기 PMID 를 자기 참조목록에
    넣어 주는 경우가 있다(실측 1/158, 인용 그래프에 자기루프를 만든다).
    """
    refs = [str(p) for p in (icite_rec.get("references") or []) if p]
    out: list[dict] = []
    for pm in refs:
        if pm == str(own_pmid):
            continue
        d = detail.get(pm) or {}
        out.append({
            "key": f"pmid{pm}",
            "doi": (d.get("doi") or "").lower() or None,
            "pmid": pm,
            "title": norm_text(d.get("title") or ""),
            "year": d.get("year"),
            "journal": d.get("journal") or "",
            "authors": _authors(d),
            "raw": "",                       # 지면 원문은 쓰지 않는다(파싱 폐기)
            "source": "icite",
        })
    return out


def unlink_cited_refs(doc: dict) -> int:
    """cited_refs 를 끊는다. 표시번호(cited_keys)와 본문 마커는 그대로 둔다.

    iCite 목록 순서와 지면 번호를 맞출 근거가 없다. 근거 없이 이어 붙이면
    '[15] 를 눌렀더니 엉뚱한 논문' 이 된다 — 그게 지금까지의 오염이었다.
    """
    n = 0
    for s in doc.get("body_text") or []:
        for p in s.get("paragraphs") or []:
            if p.get("cited_refs"):
                n += len(p["cited_refs"])
            p["cited_refs"] = []
    return n


# ── 오케스트레이션 ───────────────────────────────────────────────────
def run(config: dict | None = None) -> None:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    cache_dir = work / "icite"
    md = cfg["metadata"]
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"],
                      timeout=md["timeout_sec"])

    paths = sorted((work / "normalized").glob("*.json"))
    docs = {p: utils.read_json(p) for p in paths}
    metas = {m.get("doi"): m for m in
             (utils.read_json(p) for p in (work / "meta").glob("*.json"))}
    log(f"[참조] iCite 로 참고문헌 재구성: {len(docs)}편")

    # 1) 논문별 PMID 확보 (meta.pmid → 없으면 DOI 변환)
    pmid_of: dict[Path, str] = {}
    need_conv = []
    for p, d in docs.items():
        pid = d.get("paper_id")
        pm = (metas.get(pid) or {}).get("pmid") or (d.get("meta") or {}).get("pmid")
        if pm:
            pmid_of[p] = str(pm)
        elif pid and str(pid).startswith("10."):
            need_conv.append(str(pid).lower())
    if need_conv:
        conv = doi_to_pmid(need_conv, work / "doi_pmid_cache.json", http)
        for p, d in docs.items():
            if p in pmid_of:
                continue
            pm = conv.get(str(d.get("paper_id", "")).lower())
            if pm:
                pmid_of[p] = str(pm)
        log(f"  DOI→PMID 변환으로 {sum(1 for p in docs if p in pmid_of) - (len(docs) - len(need_conv))}편 보강")

    # 2) 논문 본체 iCite 조회
    arts = fetch_icite(pmid_of.values(), cache_dir, http)

    # 3) 참조 PMID 를 모아 한 번에 상세 조회(제목·저자·연도·저널·DOI)
    ref_pmids: set[str] = set()
    for pm in pmid_of.values():
        for r in (arts.get(pm) or {}).get("references") or []:
            ref_pmids.add(str(r))
    log(f"  참조 PMID {len(ref_pmids)}건 상세 조회")
    detail = fetch_icite(ref_pmids, cache_dir, http)

    # 4) 정본에 반영
    n_filled = n_empty = n_unlinked = 0
    before_tot = after_tot = 0
    for p, d in docs.items():
        before_tot += len(d.get("references") or [])
        pm = pmid_of.get(p)
        rec = arts.get(pm) if pm else None
        refs = build_references(pm, rec, detail) if rec else []
        d["references"] = refs
        d["references_source"] = "icite" if refs else "none"
        after_tot += len(refs)
        n_filled += bool(refs)
        n_empty += (not refs)
        n_unlinked += unlink_cited_refs(d)
        utils.write_json(p, d)

    n_doi = sum(1 for p in docs for r in docs[p].get("references") or []
                if r.get("doi"))
    log(f"[참조] 완료: 참조 보유 {n_filled}편 · 비움 {n_empty}편 · "
        f"항목 {before_tot}→{after_tot}(DOI {n_doi}) · cited_refs 해제 {n_unlinked}건")


if __name__ == "__main__":
    run()
