"""1단계 — 메타데이터를 API로 수집(파싱하지 않는다, 설계서 4단계).

소스별로 독립 수집하고 실패는 격리한다(한 소스가 죽어도 나머지는 채운다).
재개 가능: paper별 data/meta/{slug}.json 이 있으면 건너뛴다.

  Europe PMC : pmid, pmcid, inEPMC(원문XML 여부), OA, 제목/저자/저널/연도, 초록
  PubMed     : MeSH terms, publication types, 정본 초록
  iCite(NIH) : RCR, 인용수, 임상 인용 여부
  Crossref   : reference 개수(QC 대조용), 라이선스
  OpenAlex   : concepts(주제 태깅/시각화)
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from . import utils
from .utils import HttpClient, log

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ── Europe PMC ──────────────────────────────────────────────────────
def fetch_europepmc(http: HttpClient, base: str, doi: str) -> dict:
    q = {"query": f'DOI:"{doi}"', "format": "json",
         "resultType": "core", "pageSize": 1}
    data = http.get_json(base + "/search", params=q)
    res = (data or {}).get("resultList", {}).get("result", [])
    if not res:
        return {}
    r = res[0]
    return {
        "pmid": r.get("pmid"),
        "pmcid": r.get("pmcid"),
        "in_epmc": r.get("inEPMC") == "Y",
        "is_open_access": r.get("isOpenAccess") == "Y",
        "title": r.get("title", "").rstrip("."),
        "journal": (r.get("journalInfo", {}) or {}).get("journal", {}).get("title", ""),
        "year": _to_int(r.get("pubYear")),
        "authors": _split_authors(r.get("authorString", "")),
        "abstract": r.get("abstractText", "") or "",
    }


# ── PubMed E-utilities (efetch XML) ─────────────────────────────────
def fetch_pubmed(http: HttpClient, pmid: str, email: str, api_key: str) -> dict:
    if not pmid:
        return {}
    params = {"db": "pubmed", "id": pmid, "retmode": "xml",
              "tool": "PubNexus", "email": email}
    if api_key:
        params["api_key"] = api_key
    r = http.get(EUTILS + "/efetch.fcgi", params=params, accept="application/xml")
    if r is None:
        return {}
    try:
        root = etree.fromstring(r.content)
    except etree.XMLSyntaxError:
        return {}
    art = root.find(".//PubmedArticle")
    if art is None:
        return {}
    mesh = [d.text for d in art.findall(".//MeshHeading/DescriptorName") if d.text]
    ptypes = [p.text for p in art.findall(".//PublicationType") if p.text]
    # 초록(구조화된 경우 라벨별 연결)
    abstract_parts = []
    for ab in art.findall(".//Abstract/AbstractText"):
        label = ab.get("Label")
        txt = "".join(ab.itertext()).strip()
        abstract_parts.append(f"{label}: {txt}" if label else txt)
    return {
        "mesh": mesh,
        "pub_types": ptypes,
        "abstract_pubmed": "\n".join(abstract_parts),
    }


# ── iCite (NIH) ─────────────────────────────────────────────────────
def fetch_icite(http: HttpClient, pmid: str) -> dict:
    if not pmid:
        return {}
    data = http.get_json(f"https://icite.od.nih.gov/api/pubs/{pmid}")
    if not data:
        return {}
    return {
        "rcr": data.get("relative_citation_ratio"),
        "citation_count": data.get("citation_count"),
        "nih_percentile": data.get("nih_percentile"),
        "is_clinical": bool(data.get("is_clinical")),
    }


# ── Crossref (reference 개수 = QC 대조 신호) ──────────────────────────
def fetch_crossref(http: HttpClient, doi: str, email: str) -> dict:
    data = http.get_json(f"https://api.crossref.org/works/{doi}",
                         params={"mailto": email})
    msg = (data or {}).get("message", {})
    if not msg:
        return {}
    lic = [l.get("URL") for l in msg.get("license", [])] if msg.get("license") else []
    authors = []
    for a in msg.get("author", []) or []:
        fam = a.get("family", ""); giv = a.get("given", "")
        nm = (fam + " " + "".join(w[0] for w in giv.split() if w)) if fam else giv
        if nm.strip():
            authors.append(nm.strip())
    dp = (msg.get("published", {}) or msg.get("issued", {})).get("date-parts") or [[None]]
    return {
        "crossref_ref_count": msg.get("references-count"),
        "crossref_cited_by": msg.get("is-referenced-by-count"),
        "license": lic,
        "crossref_title": (msg.get("title") or [""])[0],
        "crossref_journal": (msg.get("container-title") or [""])[0],
        "crossref_year": dp[0][0] if dp and dp[0] else None,
        "crossref_authors": authors,
    }


# ── OpenAlex (concepts) ─────────────────────────────────────────────
def fetch_openalex(http: HttpClient, doi: str, email: str) -> dict:
    data = http.get_json(f"https://api.openalex.org/works/https://doi.org/{doi}",
                         params={"mailto": email})
    if not data:
        return {}
    concepts = [c.get("display_name") for c in data.get("concepts", [])
                if c.get("score", 0) >= 0.3]
    oa_authors = [a.get("author", {}).get("display_name", "")
                  for a in data.get("authorships", [])]
    return {
        "openalex_id": data.get("id"),
        "concepts": concepts[:12],
        "cited_by_count": data.get("cited_by_count"),
        "openalex_title": data.get("title") or data.get("display_name"),
        "openalex_year": data.get("publication_year"),
        "openalex_journal": ((data.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "openalex_authors": [a for a in oa_authors if a],
    }


# ── 오케스트레이션 ───────────────────────────────────────────────────
def collect_one(http: HttpClient, doi: str, email: str, api_key: str,
                epmc_base: str) -> dict:
    meta: dict = {"doi": doi, "sources_ok": [], "sources_fail": []}

    for name, fn in [
        ("europepmc", lambda: fetch_europepmc(http, epmc_base, doi)),
        ("crossref", lambda: fetch_crossref(http, doi, email)),
        ("openalex", lambda: fetch_openalex(http, doi, email)),
    ]:
        try:
            got = fn()
            meta.update(got)
            meta["sources_ok"].append(name) if got else meta["sources_fail"].append(name)
        except Exception as e:  # noqa: BLE001
            meta["sources_fail"].append(name)
            log(f"      ! {name} 실패: {e}")

    pmid = meta.get("pmid")
    for name, fn in [
        ("pubmed", lambda: fetch_pubmed(http, pmid, email, api_key)),
        ("icite", lambda: fetch_icite(http, pmid)),
    ]:
        try:
            got = fn()
            meta.update(got)
            meta["sources_ok"].append(name) if got else meta["sources_fail"].append(name)
        except Exception as e:  # noqa: BLE001
            meta["sources_fail"].append(name)
            log(f"      ! {name} 실패: {e}")

    # 제목/저널/연도/저자가 비면 Crossref → OpenAlex 순으로 채움
    # (Europe PMC 미등재 논문, 특히 한국 저널에서 제목 누락 방지)
    for key in ("title", "journal", "year", "authors"):
        if not meta.get(key):
            meta[key] = meta.get(f"crossref_{key}") or meta.get(f"openalex_{key}") or meta.get(key)
    return meta


def collect_all(config: dict | None = None, force: bool = False) -> list[dict]:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    meta_dir = work / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    records = utils.read_jsonl(work / "manifest.jsonl")
    primaries = [r for r in records if r.get("is_primary") and r.get("doi")]
    log(f"[1단계] 메타데이터: 고유 논문 {len(primaries)}편")

    md = cfg["metadata"]
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"],
                      timeout=md["timeout_sec"])
    epmc_base = cfg["fulltext"]["europepmc_base"]

    out = []
    for i, r in enumerate(primaries, 1):
        doi = r["doi"]
        dest = meta_dir / f"{utils.slug(doi)}.json"
        cached = utils.read_json(dest) if (dest.exists() and not force) else None
        # 모든 소스가 실패한 캐시(네트워크 장애 등)는 '완료'로 보지 않고 재수집.
        if cached is not None and cached.get("sources_ok"):
            meta = cached
            log(f"  [{i:>3}/{len(primaries)}] (캐시) {doi}")
        else:
            meta = collect_one(http, doi, md["email"], md.get("ncbi_api_key", ""),
                               epmc_base)
            utils.write_json(dest, meta)
            xml = "XML" if meta.get("in_epmc") else "PDF"
            log(f"  [{i:>3}/{len(primaries)}] {xml} pmid={meta.get('pmid') or '-':<9} "
                f"rcr={meta.get('rcr')} ok={','.join(meta['sources_ok'])}  {doi}")
        out.append(meta)

    n_xml = sum(bool(m.get("in_epmc")) for m in out)
    log(f"[1단계] 완료 → {meta_dir}  (원문XML {n_xml}/{len(out)})")
    return out


# ── 헬퍼 ────────────────────────────────────────────────────────────
def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _split_authors(s: str) -> list[str]:
    if not s:
        return []
    return [a.strip().rstrip(".") for a in s.split(",") if a.strip()]


if __name__ == "__main__":
    collect_all()
