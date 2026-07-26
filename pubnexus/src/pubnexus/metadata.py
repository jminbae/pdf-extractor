"""1단계 — 메타데이터를 API로 수집(파싱하지 않는다, 설계서 4단계).

소스별로 독립 수집하고 실패는 격리한다(한 소스가 죽어도 나머지는 채운다).
재개 가능: paper별 data/meta/{slug}.json 이 있으면 건너뛴다.

  Europe PMC : pmid, pmcid, inEPMC(원문XML 여부), OA, 제목/저자/저널/연도, 초록
  PubMed     : MeSH terms, publication types, 정본 초록
  iCite(NIH) : RCR, 인용수, 임상 인용 여부
  Crossref   : reference 개수(QC 대조용), 라이선스
  OpenAlex   : concepts(주제 태깅/시각화)

대규모(3만 편) 대비 — 이 단계가 rate-limit 때문에 가장 오래 걸리고 가장 자주 끊긴다:
  · 원장 = meta/{slug}.json (논문 1편 끝날 때마다 즉시 기록) → 재개 단위가 1편
  · **원자적 쓰기**(임시파일 → os.replace): 쓰다 죽어도 반쪽 JSON 이 남지 않는다.
    (후속 단계들이 meta/*.json 을 glob 해서 read_json 하므로 반쪽 파일 1개가
     파이프라인 전체를 죽인다 — 그 사고 경로를 막는다)
  · 손상된 캐시는 '완료'로 보지 않고 재수집하며, 논문 1편의 예외가 전체를 멈추지 않는다
  · 실패는 failures.jsonl 로 표면화하고 마지막에 요약 로그

호스트 앱(ResearchMap) 연동용 선택 인자 — 기존 호출부는 그대로 동작한다:
  collect_all(cfg, on_progress=cb, should_cancel=fn)

  on_progress(payload: dict)  — 논문마다 호출. payload 키(안정 계약):
      stage    "metadata"
      phase    "start" | "item" | "cached" | "failed" | "done" | "cancelled"
      done/total/ok/failed/cached : int
      current  현재 DOI
      message  사람이 읽는 한 줄
    콜백 예외는 무시한다(파이프라인을 멈추지 않는다).

  should_cancel() -> bool  — True 면 이미 수집한 편은 그대로 두고 즉시 멈춘다.
    meta/*.json 이 편별로 확정 저장돼 있으므로 다시 실행하면 남은 편만 수집한다.

설정(모두 선택, 기본값으로 기존 동작 유지):
  metadata.checkpoint_every : 진행 요약 로그 주기(편). 기본 25.
      (수집 결과 자체는 편마다 즉시 저장되므로 이 값과 무관하게 재개는 항상 안전하다)
  metadata.retry_failed     : True 면 '모든 소스 실패' 캐시도 다시 시도. 기본 True
"""
from __future__ import annotations

import collections
import json
import os
import re
import time
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

    # 이 DOI 가 실제로 존재하는가 — 기본키 게이트의 근거를 원장에 남긴다.
    # 세 곳 중 어디도 모르는 DOI 는 정규식을 통과했을 뿐 **해소되지 않는다**
    # ('10.1200/jco.18' 처럼 조판으로 잘린 접두부). 네트워크 장애로 전부
    # 실패한 경우와 구분하려고 '조회는 됐는데 없더라'만 False 로 기록한다.
    tried = set(meta["sources_ok"]) | set(meta["sources_fail"])
    if {"crossref", "europepmc", "openalex"} <= tried:
        meta["doi_resolved"] = bool(
            meta.get("crossref_title") or meta.get("pmid")
            or meta.get("openalex_id"))
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


# ── 정본 초록 판정 ───────────────────────────────────────────────────
# 파서(GROBID TEI / JATS)가 뽑은 초록은 '그 자리에 있던 글자'일 뿐 **이 논문의
# 초록이라는 보장이 없다.** 2단 조판 레터에서는 앞 논문의 꼬리·서론 첫 문단·
# 키워드 줄이 그대로 초록 자리에 들어오며, 셋 다 문법적으로 멀쩡한 과학
# 문장이라 길이·null·문자율 검사를 전부 통과한다.
#
# 그래서 초록은 두 개의 독립 증인으로 검증한다:
#   1) PDF 의 'Abstract' 표제 뒤 텍스트  (pdf_abstract)      — 문서 자체의 증언
#   2) PubMed/EuropePMC 의 정본 초록      (meta.abstract_*)   — 외부 권위
# 둘 중 하나라도 확보되면 추출 초록을 대조할 수 있다.

_WORD_RE = re.compile(r"[a-z0-9]+")

# 'Abstract' 표제. 저널마다 'ABSTRACT' · 'A B S T R A C T'(자간분리) ·
# 'Abstract |'(JAMA) 로 조판이 갈린다. 줄 시작에서만 인정한다 —
# 본문 중 'in the abstract' 같은 산문에 걸리지 않게.
_ABS_HEAD_RE = re.compile(
    r"^[ \t]*(?:A\s?B\s?S\s?T\s?R\s?A\s?C\s?T|Abstract|SUMMARY|Summary)"
    r"[ \t]*[|:.]?[ \t]*$|^[ \t]*Abstract[ \t]*[|:][ \t]*(?=\S)",
    re.M)

# 초록의 끝. 키워드 줄·서론 표제·저작권 상용구 중 가장 먼저 오는 것.
_ABS_STOP_RE = re.compile(
    r"^[ \t]*(?:K\s?E\s?Y\s?\s?W\s?O\s?R\s?D\s?S?|Key[ \t]?words?|KEYWORDS?)\b"
    r"|^[ \t]*(?:\d[.\s|]*)?(?:INTRODUCTION|Introduction|BACKGROUND\s*$)"
    r"|^[ \t]*(?:©|Copyright\b|This is an open access)"
    r"|^[ \t]*(?:What'?s already known|What is already known)",
    re.M)


def pdf_abstract(pdf_path, scan_pages: int = 2, limit: int = 6000) -> str:
    """PDF 의 'Abstract' 표제 뒤 텍스트를 그대로 돌려준다(없으면 "").

    **이것이 초록 검증의 1차 증인이다.** 표제 자체가 없으면 "" 를 돌려주는데,
    그것은 '초록이 없는 letter/correspondence' 라는 뜻이지 결함이 아니다.
    호출부는 ""(표제 없음)과 '표제는 있는데 내용이 다름'을 반드시 구분해야 한다.

    2단 조판에서 읽기 순서가 섞일 수 있으므로 이 반환값은 **어휘 대조용**이며
    그대로 정본 초록으로 삼지 않는다(대체는 API 초록으로만 한다).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:  # noqa: BLE001 — PDF 없음/손상은 '판정 불가'
        return ""
    try:
        text = "".join(doc[i].get_text()
                       for i in range(min(scan_pages, doc.page_count)))
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass

    m = _ABS_HEAD_RE.search(text)
    if not m:
        return ""
    body = text[m.end():m.end() + limit]
    stop = _ABS_STOP_RE.search(body)
    if stop and stop.start() > 80:      # 표제 직후 오탐 방지(최소 길이)
        body = body[:stop.start()]
    return utils.norm_text(body).strip()


# 초록 끝에 눌어붙는 조판 부속물. 어휘 대조로는 못 잡는다 — 키워드 한 줄은
# 초록 어휘의 4% 남짓이라 정밀도 0.96 으로 게이트를 통과한다(실측
# 10.1002/iid3.316). 종류가 정해져 있으므로 **끝에서만** 결정적으로 잘라낸다.
_ABS_TAIL_RES = (
    # 키워드 줄. **대문자 표제이거나 콜론이 붙은 것만** 인정한다.
    #   'Keywords: rosacea, ...'      → 키워드 줄 (자름)
    #   'KEYWORDS ...' / 'K E Y W O R D S' → 키워드 줄 (자름)
    #   '...using common keywords related to gastric cancer...' → **본문 문장** (안 자름)
    # 소문자 'keywords' 를 무조건 자르면 Methods 문장 한가운데를 잘라
    # 초록의 792~1112자를 통째로 날린다(실측 3편: jso.23438·jso.23618·s11695).
    re.compile(r"\s*(?:K\s?E\s?Y\s?\s?W\s?O\s?R\s?D\s?S?\b|Key\s?[Ww]ords?\s*:)\s*.*$",
               re.S),
    # 저널 자기인용 꼬리 '(J Am Acad Dermatol 2018;79:836-42.)'
    re.compile(r"\s*\([A-Z][A-Za-z .]{4,40}\s+\d{4};\d+[:;].*$"),
    # 위 꼬리가 잘려 남은 고아 여는 괄호
    re.compile(r"\s*\(\s*$"),
    # 전자보충자료 상용구
    re.compile(r"\s*(?:The online version of this article|Electronic supplementary"
               r"|Supplementary information).*$", re.I | re.S),
)


def strip_abstract_tail(abstract: str) -> tuple[str, list[str]]:
    """초록 끝의 키워드 줄·자기인용 꼬리를 제거한다 → (정리된 초록, 제거항목).

    본문 내용은 절대 건드리지 않는다(끝에서만, 정해진 패턴만).
    """
    s = (abstract or "").strip()
    removed: list[str] = []
    for rx in _ABS_TAIL_RES:
        m = rx.search(s)
        if m and m.start() > 120:      # 초록 본체를 통째로 날리지 않게
            removed.append(s[m.start():m.end()].strip()[:80])
            s = s[:m.start()].rstrip()
    return s, removed


def _tokens(s: str) -> "collections.Counter":
    return collections.Counter(_WORD_RE.findall((s or "").lower()))


def lexical_agreement(a: str, b: str) -> tuple[float, float]:
    """(정밀도, 재현율) = a 가 b 에 얼마나 담겼나 / b 가 a 에 얼마나 담겼나.

    rapidfuzz 의 token_set_ratio 를 쓰면 안 된다 — 집합 기반이라
    297자 초록 vs 1498자 정본이 96 점을 받는다(실측). 잘림(재현율 하락)과
    이물질 혼입(정밀도 하락)은 **방향이 다른 결함**이므로 따로 재야 한다.
    """
    A, B = _tokens(a), _tokens(b)
    if not A or not B:
        return (0.0, 0.0)
    inter = sum((A & B).values())
    return (inter / sum(A.values()), inter / sum(B.values()))


# PubMed 가 '초록 없음'이라고 말해도 믿을 수 있는 문헌 종류.
# 원저(Journal Article)는 초록이 있는 게 정상이므로 여기 넣지 않는다 —
# PubMed 수집 실패를 '초록 없는 논문'으로 오판하면 멀쩡한 초록을 지운다.
_NO_ABSTRACT_TYPES = frozenset({
    "letter", "comment", "editorial", "case reports",
    "published erratum", "news", "biography", "historical article",
})


def has_no_abstract(meta: dict, pdf_path=None) -> bool:
    """이 문헌은 애초에 초록이 없다 — 를 **증거로** 판정한다.

    세 조건이 모두 참일 때만 True:
      1) PubMed 레코드를 실제로 받아왔다(pmid + sources_ok 에 'pubmed')
         → '수집 실패로 초록이 비었다'와 '원래 초록이 없다'를 구분한다
      2) 그 레코드에 초록이 없다
      3) PDF 에 'Abstract' 표제가 없다
      4) 문헌 종류가 letter/comment/editorial/case report 류다

    실측 근거: abstract 가 빈 49편은 전부 PDF 에 Abstract 표제가 없는
    letter/correspondence 였고(오탐 0/49), 이 판정과 정확히 일치한다.
    """
    if not meta.get("pmid") or "pubmed" not in (meta.get("sources_ok") or []):
        return False
    if (meta.get("abstract_pubmed") or meta.get("abstract") or "").strip():
        return False
    types = {str(t).strip().lower() for t in (meta.get("pub_types") or [])}
    if not types & _NO_ABSTRACT_TYPES:
        return False
    return not (pdf_abstract(pdf_path) if pdf_path else "")


def choose_abstract(extracted: str, meta: dict, pdf_path=None,
                    body_first: str = "", title: str = "") -> tuple[str, str, dict]:
    """정본 초록과 그 출처를 정한다 → (abstract, abstract_source, info).

    abstract_source 값:
      "extracted"        문서에서 뽑은 초록이 검증을 통과함
      "api"              추출 초록이 검증에 실패해 PubMed/EPMC 정본으로 대체
      "extracted_unver"  대조할 증인이 없어 검증하지 못한 추출 초록(그대로 둠)
      "none"             초록이 없음 — 표제도 API 초록도 없는 letter. 결함 아님
      "extracted_bad"    검증 실패했으나 대체할 정본이 없다(표면화만, 내용은 보존)

    수리 정책 — **손실 없는 수리만 한다**:
      R1 API 정본이 있으면 그것으로 대체한다(권위 있는 출처, 본문은 무관).
      R2 '이 문헌엔 초록이 없다'가 증거로 확정되면 비운다. 이때 지워지는 글자는
         info['abstract_removed'] 로 반드시 넘겨준다 — 그 글자는 대개 본문 경계
         오판으로 초록 자리에 온 **본문**이므로, 본문 담당이 되살릴 수 있어야 한다.
      R3 그 밖의 실패는 표면화만 하고 내용을 건드리지 않는다.

    PDF 에서 뽑은 초록은 2단 조판에서 읽기 순서가 섞이므로 **검증에만** 쓰고
    정본으로 승격하지 않는다.
    """
    api = (meta.get("abstract_pubmed") or meta.get("abstract") or "").strip()
    extracted = (extracted or "").strip()
    info: dict = {}
    if not extracted:
        return (api, "api", info) if api else ("", "none", info)

    # 조판 부속물(키워드 줄 등)은 검증 전에 떼어낸다 — 그래야 남은 어휘 대조가
    # '초록 내용' 자체를 보게 된다.
    extracted, tail = strip_abstract_tail(extracted)
    if tail:
        info["abstract_tail_removed"] = tail

    verdict = verify_abstract(extracted, meta, pdf_path,
                              body_first=body_first, title=title)
    info["abstract_check"] = verdict

    # R2 — 초록이 없는 문헌인데 무언가 들어와 있다(증인 없음 포함)
    if not api and has_no_abstract(meta, pdf_path):
        info["abstract_removed"] = extracted
        info["abstract_check"] = {
            "ok": False,
            "reasons": (verdict["reasons"] or []) + ["paper_has_no_abstract"],
            "signals": verdict["signals"]}
        return "", "none", info

    if verdict["ok"] is None:            # 증인 없음 → 판정 불가, 손대지 않는다
        return extracted, "extracted_unver", info
    if verdict["ok"]:
        return extracted, "extracted", info
    if api:                              # R1
        return api, "api", info
    return extracted, "extracted_bad", info   # R3 — 표면화만


# 임계값 — 실측으로 정했다(정상 86편의 최저 재현율 0.89, 결함 11편의 최고 0.78).
_ABS_MIN_RECALL = 0.85       # 이보다 낮으면 잘림/딴 논문
_ABS_MIN_PRECISION = 0.80    # 이보다 낮으면 이물질 혼입(키워드·이웃논문)


def verify_abstract(abstract: str, meta: dict, pdf_path=None,
                    body_first: str = "", title: str = "") -> dict:
    """추출 초록이 '이 논문의 초록'인지 판정. 결과 dict:

        ok       True(통과) / False(실패) / None(증인 없음 = 판정 불가)
        reasons  실패 사유 목록
        signals  측정값(감사 추적용)

    증인 우선순위: API 정본 초록 > PDF 'Abstract' 표제 뒤 텍스트.
    증인이 하나도 없으면 None 을 돌려준다 — '통과'로 위장하지 않는다.
    """
    abstract = (abstract or "").strip()
    reasons: list[str] = []
    sig: dict = {}
    if not abstract:
        return {"ok": None, "reasons": [], "signals": sig}

    # (a) 본문 첫 문단과 동일 → 서론이 초록 자리에 들어온 사고
    if body_first:
        p, r = lexical_agreement(abstract, body_first)
        sig["abs_vs_body1"] = round(min(p, r), 3)
        if min(p, r) >= 0.90:
            reasons.append("abstract_is_body_first_paragraph")

    # (b) 제목과 어휘가 하나도 안 겹침 → 다른 논문의 초록
    if title:
        t = {w for w in _WORD_RE.findall(title.lower()) if len(w) > 3}
        a = set(_WORD_RE.findall(abstract.lower()))
        if t:
            ov = len(t & a) / len(t)
            sig["abs_vs_title"] = round(ov, 3)
            if ov == 0.0:
                reasons.append("abstract_title_zero_overlap")

    # (c) 정본 대조 — API 초록이 1순위 증인
    api = (meta.get("abstract_pubmed") or meta.get("abstract") or "").strip()
    ref, witness = (api, "api") if api else ("", "")
    if not ref and pdf_path:
        pa = pdf_abstract(pdf_path)
        if pa:
            ref, witness = pa, "pdf_heading"
    if ref:
        p, r = lexical_agreement(abstract, ref)
        sig["abs_precision"] = round(p, 3)
        sig["abs_recall"] = round(r, 3)
        sig["abs_witness"] = witness
        if r < _ABS_MIN_RECALL:
            reasons.append("abstract_truncated_or_foreign")
        if p < _ABS_MIN_PRECISION:
            reasons.append("abstract_polluted")

    ok = None if (not ref and not reasons) else (not reasons)
    return {"ok": ok, "reasons": reasons, "signals": sig}


# ── 신원(기본키) 판정 ────────────────────────────────────────────────
# 2단 조판 저널에서는 한 PDF 에 앞 논문의 꼬리가 같이 실린다. 그 꼬리에 박힌
# DOI 로 파일링되면 **DOI 는 멀쩡히 해소되는데 제목·저자·PMID 가 전부 남의
# 것**이 된다(10.1111/jdv.16524 실측). DOI 해소 검사로는 절대 못 잡는다.
#
# 폰트 기반 PDF 제목추출은 러닝헤드에 속아 이 판정의 증인이 되지 못했다
# (실측 오탐 26/167). 대신 **수집 당시 파일명**을 증인으로 쓴다 — 다만
# 파일명이 제목 구실을 할 때만. 아니면 판정을 포기한다(모르면 모른다고 둔다).

_FNAME_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-[A-Za-z]*-?\S*\s+")


def _filename_title(source_file: str) -> str:
    """파일명에서 제목 구실을 하는 부분을 떼어낸다. 못 믿으면 ""."""
    stem = Path(str(source_file or "")).stem
    stem = _FNAME_PREFIX_RE.sub("", stem).strip()
    if len(stem) < 20:
        return ""                      # 'Reply' 같은 무정보 파일명
    letters = [c for c in stem if c.isalpha()]
    if not letters:
        return ""
    # 라틴 문자 비중이 낮으면 서지 제목과 언어가 달라 대조가 무의미하다
    # (한국어 파일명 vs 영문 record 제목 — 불일치가 아니라 번역이다)
    if sum(c.isascii() for c in letters) / len(letters) < 0.8:
        return ""
    return stem


_IDENTITY_MIN = 70        # 실측: 알려진 신원오류 10편 전부 70 미만, 정상 최저 84.9


def verify_identity(record_title: str, source_file: str) -> dict:
    """레코드의 제목이 이 PDF 의 논문 제목과 같은가.

    ok=None 은 '증인이 없어 판정 불가' — 통과로 위장하지 않는다.
    """
    from rapidfuzz import fuzz
    fname = _filename_title(source_file)
    rec = re.sub(r"<[^>]+>", "", record_title or "").strip()
    if not fname or len(rec) < 15:
        return {"ok": None, "score": None, "witness": None}
    score = fuzz.token_set_ratio(rec.lower(), fname.lower())
    return {"ok": score >= _IDENTITY_MIN, "score": round(score, 1),
            "witness": "filename"}


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
