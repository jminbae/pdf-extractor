"""참고문헌 DOI 보강 — 학술 DB의 '참조목록'으로 GROBID 참고문헌의 DOI를 채운다.

PDF가 참고문헌에 DOI를 안 찍는 저널이 많아, PDF/GROBID만으로는 인용→DOI 해소율이 낮다.
학술 DB는 '논문 ID로 완전한 참조목록(각 참조의 DOI 포함)'을 주므로 PDF 파싱에 의존하지 않는다.

소스 우선순위(커버리지 실측 기준):
  1) Semantic Scholar  /paper/DOI/references  — DOI+제목, 커버리지 최상(Crossref 미기탁분도 보유)
  2) Crossref          /works/DOI.reference   — 출판사 기탁분(불완전)
  3) Crossref 제목검색  query.bibliographic    — 개별 참조 직접 조회(최종 폴백)
매칭: (a) 제목 퍼지매칭, (b) 개수 정렬 시 위치매칭(제목 미추출 참조 보강).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from rapidfuzz import fuzz

from . import utils
from .utils import HttpClient, norm_text, log


# ── 소스별 참조목록 수집 ─────────────────────────────────────────────
def fetch_s2_refs(doi: str, cache_dir: Path, email: str) -> list[dict] | None:
    """Semantic Scholar 참조목록: [{doi,title,year}] (논문 순서 보존). 실패 시 None."""
    cache = cache_dir / f"s2_{utils.slug(doi)}.json"
    if cache.exists():
        return utils.read_json(cache)
    url = (f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}/references"
           "?fields=externalIds,title,year&limit=500")
    out, ok = [], False
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"pubnexus (mailto:{email})"})
            data = json.load(urllib.request.urlopen(req, timeout=30))
            for it in data.get("data", []):
                cp = it.get("citedPaper") or {}
                ext = cp.get("externalIds") or {}
                out.append({"doi": (ext.get("DOI") or "").lower() or None,
                            "title": norm_text(cp.get("title") or ""),
                            "year": cp.get("year")})
            ok = True
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3 * (attempt + 1)); continue
            ok = (e.code == 404)  # 404 = 논문 없음(정상), 캐시해도 됨
            break
        except Exception:
            break
    if ok:
        cache.parent.mkdir(parents=True, exist_ok=True)
        utils.write_json(cache, out)
        return out
    return None   # 레이트리밋 소진 등 → 캐시 안 함(다음에 재시도)


def fetch_crossref_refs(http: HttpClient, doi: str, cache_dir: Path) -> list[dict]:
    cache = cache_dir / f"{utils.slug(doi)}.json"
    if cache.exists():
        return utils.read_json(cache)
    data = http.get_json(f"https://api.crossref.org/works/{doi}", params={"mailto": http.email})
    refs = ((data or {}).get("message", {}) or {}).get("reference", []) or []
    out = []
    for r in refs:
        title = r.get("article-title") or r.get("volume-title") or r.get("unstructured") or ""
        yr = r.get("year")
        try:
            yr = int(str(yr)[:4]) if yr else None
        except ValueError:
            yr = None
        out.append({"doi": (r.get("DOI") or "").lower() or None,
                    "title": norm_text(title), "year": yr})
    cache.parent.mkdir(parents=True, exist_ok=True)
    utils.write_json(cache, out)
    return out


def resolve_by_title(http: HttpClient, title: str, year, cache: dict, threshold: int = 90) -> str | None:
    """참조 제목으로 Crossref를 직접 검색해 DOI를 찾는다(제목→DOI 캐시)."""
    t = norm_text(title)
    if len(t) < 25 or len(t.split()) < 3:
        return None
    if t in cache:
        return cache[t]
    doi = None
    try:
        data = http.get_json("https://api.crossref.org/works",
                             params={"query.bibliographic": t, "rows": 3,
                                     "select": "DOI,title,issued", "mailto": http.email})
        for it in ((data or {}).get("message", {}) or {}).get("items", []):
            ct = norm_text((it.get("title") or [""])[0])
            if ct and fuzz.token_set_ratio(t, ct) >= threshold:
                cy = ((it.get("issued", {}) or {}).get("date-parts") or [[None]])[0][0]
                if year and cy and abs(int(year) - int(cy)) > 1:
                    continue
                doi = (it.get("DOI") or "").lower() or None
                break
    except Exception:  # noqa: BLE001
        doi = None
    cache[t] = doi
    return doi


# ── 매칭 ─────────────────────────────────────────────────────────────
def match_by_title(doc: dict, candidates: list[dict], threshold: int = 88) -> int:
    """DOI 없는 참고문헌을 후보 참조목록과 제목 퍼지매칭해 채움."""
    cand = [c for c in candidates if c.get("doi") and c.get("title")]
    filled = 0
    for ref in doc.get("references", []):
        if ref.get("doi") or not ref.get("title"):
            continue
        rt = ref["title"]
        best, best_c = 0, None
        for c in cand:
            sc = fuzz.token_set_ratio(rt, c["title"])
            if sc > best:
                best, best_c = sc, c
        if best >= threshold and best_c:
            if ref.get("year") and best_c["year"] and abs(ref["year"] - int(best_c["year"] or 0)) > 1:
                continue
            ref["doi"] = best_c["doi"]; filled += 1
    return filled


def match_by_position(doc: dict, ordered: list[dict], min_sim: int = 55) -> int:
    """개수가 정렬되면 위치매칭 — 제목이 안 뽑힌 참조도 순서로 DOI를 채움."""
    grefs = doc.get("references", [])
    if not ordered or not grefs:
        return 0
    if abs(len(grefs) - len(ordered)) > max(3, int(0.25 * len(ordered))):
        return 0   # 개수 차이 크면 순서가 안 맞을 위험 → 스킵
    filled = 0
    for i, g in enumerate(grefs):
        if g.get("doi") or i >= len(ordered):
            continue
        s = ordered[i]
        if not s.get("doi"):
            continue
        gt, st = g.get("title", ""), s.get("title", "")
        if gt and st and fuzz.token_set_ratio(gt, st) < min_sim:
            continue   # 제목이 둘 다 있는데 너무 다르면 위치 어긋남 → 스킵
        g["doi"] = s["doi"]; filled += 1
    return filled


def reresolve_cited(doc: dict) -> None:
    """참고문헌 DOI 가 보강된 뒤 문단의 cited_refs 를 다시 매긴다.

    **참고문헌 목록에 실제로 있는 키만** 옮긴다. GROBID 가 연결에 실패한 인용은
    'num:15' 같은 표시번호로 cited_keys 에 남아 있는데, 이것까지 cited_refs 로
    옮기면 '존재하지 않는 참조를 가리키는 링크'가 된다(실측 271건/47편).
    표시번호는 본문에 이미 [15] 로 박혀 있으므로 정보가 사라지지도 않는다.
    """
    keymap = {r["key"]: (r.get("doi") or r["key"]) for r in doc.get("references", [])}
    for s in doc["body_text"]:
        for p in s["paragraphs"]:
            if p.get("cited_keys"):
                p["cited_refs"] = [keymap[k] for k in p["cited_keys"] if k in keymap]


# ── 오케스트레이션 ───────────────────────────────────────────────────
def run(config: dict | None = None) -> None:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    xr_dir = work / "crossref_refs"
    s2_dir = work / "s2_refs"
    md = cfg["metadata"]
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"], timeout=md["timeout_sec"])

    title_cache_path = work / "title_doi_cache.json"
    title_cache = utils.read_json(title_cache_path) if title_cache_path.exists() else {}

    docs = sorted((work / "normalized").glob("*.json"))
    log(f"[참조보강] S2+Crossref 대조: {len(docs)}편")
    before_tot = after_tot = 0
    n_s2 = n_pos = n_title = 0
    for i, p in enumerate(docs, 1):
        doc = utils.read_json(p)
        doi = doc.get("paper_id")
        refs = doc.get("references", [])
        if not refs or not doi or not doi.startswith("10."):
            continue
        before = sum(1 for r in refs if r.get("doi"))

        # 1) Semantic Scholar 참조목록(주 소스): 제목매칭 + 위치매칭
        s2 = fetch_s2_refs(doi, s2_dir, md["email"])
        if s2:
            n_s2 += match_by_title(doc, s2)
            n_pos += match_by_position(doc, s2)
        # 2) Crossref 기탁 참조목록(보조): 제목매칭
        try:
            n_s2 += match_by_title(doc, fetch_crossref_refs(http, doi, xr_dir))
        except Exception:  # noqa: BLE001
            pass
        # 3) 남은 제목-보유 참조 → Crossref 제목검색(최종 폴백)
        for r in refs:
            if not r.get("doi") and r.get("title"):
                d = resolve_by_title(http, r["title"], r.get("year"), title_cache)
                if d:
                    r["doi"] = d; n_title += 1

        reresolve_cited(doc)
        utils.write_json(p, doc)
        after = sum(1 for r in refs if r.get("doi"))
        before_tot += before; after_tot += after
        if after > before:
            log(f"  [{i}/{len(docs)}] {doi}: DOI {before}→{after}")
        if i % 15 == 0:
            utils.write_json(title_cache_path, title_cache)

    utils.write_json(title_cache_path, title_cache)
    tot = sum(len(utils.read_json(p).get("references", [])) for p in docs)
    log(f"[참조보강] 완료: DOI 해소 {before_tot}→{after_tot} / 전체 {tot} "
        f"({100*after_tot//max(tot,1)}%)  [S2/Crossref제목 +{n_s2}, 위치 +{n_pos}, 제목검색 +{n_title}]")


if __name__ == "__main__":
    run()
