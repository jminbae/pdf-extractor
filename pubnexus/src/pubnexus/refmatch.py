"""참고문헌 — **번호·순서는 지면에서, 내용은 iCite(NIH) 에서.**

두 소스를 짝지어 합친다. 어느 한쪽만으로는 원장이 요구한 동작
("본문에서 참고문헌 클릭하면 그 참고문헌으로 가야지")이 나오지 않는다.

  · 지면(GROBID/JATS 파싱)은 **순서와 원문이 맞다.** 서지 내용은 못 믿는다 —
    DOI 가 순열로 뒤섞이고(한 편에서 125개 중 98개), 이웃 논문 참고문헌이 섞이고,
    러닝헤더가 항목으로 등록된다(실측 10.25259/ijdvl_558_2021 의 b40 = 이 논문
    자신의 제목).
  · iCite 는 **내용이 정확하다.** 순서는 지면 번호가 아니다 — 실측
    10.1002/iid3.316 의 지면은 1.Kim 2.Oh 3.Won 4.Kramer 인데 iCite 는
    Kim·Kramer·**자기자신**·Won·Oh 순으로 준다. 집합은 맞고 순서만 다르다.

그래서 지면 항목 ↔ iCite 항목을 DOI·제목·저자연도로 짝지어 한 레코드로 합친다.
  · 짝을 찾으면  source="parsed+icite"  (번호도 내용도 맞다)
  · 못 찾으면    source="parsed"        (지면 원문만 남긴다. 지어내지 않는다)
  · iCite 에만   source="icite"         (number=None 으로 뒤에 붙인다. 버리지 않는다)

**틀린 링크는 없는 링크보다 해롭다.** 근거가 애매하면 잇지 않는다.

지면 번호(number)를 만드는 근거는 grobid_client._marker_numbers 참고 —
GROBID 가 <ref target="#b14">15</ref> 로 내보내는 '항목↔인쇄번호' 대응이다.

iCite: https://icite.od.nih.gov/api/pubs?pmids=<pmid>[,<pmid>...]
  인증 불필요·무료·배치조회. 응답은 data/icite/ 에 캐시해 오프라인 재현이 되게 한다.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import utils
from .utils import HttpClient, log, norm_text

try:                                     # 없으면 제목 짝짓기를 포기하고 DOI 만 쓴다
    from rapidfuzz import fuzz
except Exception:                        # noqa: BLE001
    fuzz = None

ICITE = "https://icite.od.nih.gov/api/pubs"
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

_BATCH = 100          # iCite 는 쉼표로 여러 PMID 를 받는다

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_NONWORD_RE = re.compile(r"[^a-z0-9 ]+")
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

# 짝짓기 임계 — 실측(정본 163편 · 지면 3,400여 항목)으로 정한 값이다.
_T_TITLE = 88         # 제목 대 제목
_T_RAW = 92           # 제목 대 지면 원문(부분일치라 더 엄하게)
_T_DOI_VETO = 55      # DOI 가 같다는데 제목이 이만큼도 안 닮으면 그 DOI 를 의심한다


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


# ── iCite 레코드 → 비교하기 좋은 형태 ────────────────────────────────
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


def icite_items(own_pmid: str, icite_rec: dict,
                detail: dict[str, dict]) -> list[dict]:
    """iCite 의 references(PMID 목록)를 짝짓기용 항목으로 편다.

    자기 자신은 뺀다 — iCite 가 자기 PMID 를 자기 참조목록에 넣어 주는 경우가
    있다(실측 1/158, 인용 그래프에 자기루프를 만든다).
    """
    out: list[dict] = []
    for pm in (icite_rec or {}).get("references") or []:
        pm = str(pm)
        if not pm or pm == str(own_pmid):
            continue
        d = detail.get(pm) or {}
        out.append({
            "pmid": pm,
            "doi": (d.get("doi") or "").lower() or None,
            "title": norm_text(d.get("title") or ""),
            "year": d.get("year"),
            "journal": d.get("journal") or "",
            "authors": _authors(d),
        })
    return out


# ── 짝짓기 ───────────────────────────────────────────────────────────
def _ascii(s: str) -> str:
    """발음기호를 벗긴다: 'Böhm'→'Bohm', 'Krämer'→'Kramer', 'Rodríguez'→'Rodriguez'.

    **빼먹으면 안 되는 단계다.** 지면은 원어 철자로, iCite 는 그때그때 다르게
    적는다. 이걸 안 하면 유럽·중남미 저자 이름이 통째로 다른 문자열이 된다
    (초기 실측: 짝지은 3,420건 중 '저자 성이 지면 원문에 없다'로 잘못 잡힌
    548건의 대부분이 이 문제였다).
    """
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def _nt(s: str) -> str:
    """제목 비교용 정규화 — 발음기호 제거·소문자·기호 제거·공백 압축."""
    return re.sub(r"\s+", " ",
                  _NONWORD_RE.sub(" ", _ascii(s).lower())).strip()


def _norm_doi(d: str) -> str:
    """DOI 비교용 정규화. iCite 는 옛 표기 '10.1037//0033-…' 를 그대로 준다."""
    d = (d or "").strip().lower().rstrip(".,;)]")
    return re.sub(r"(?<!:)//+", "/", d)


def _dois_of(ref: dict, ban: set[str] = frozenset()) -> set[str]:
    """지면 항목이 주장하는 DOI 들(파싱 필드 + 원문에 인쇄된 것).

    ban 에 든 것은 뺀다 — 출판사 조판이 **논문 자신의 DOI 를 참고문헌 줄마다**
    붙여 인쇄하는 일이 흔하다(실측: JAAD 계열에서 참고문헌 raw 273개 중 36개가
    그 논문 자신의 DOI). 그대로 두면 '이 참조의 DOI' 로 오인된다.
    """
    out = set()
    d = _norm_doi(ref.get("doi") or "")
    if d:
        out.add(d)
    for m in _DOI_RE.findall(ref.get("raw") or ""):
        out.add(_norm_doi(m))
    return {d for d in out if d and d not in ban}


def _banned_dois(printed: list[dict], host_doi: str | None) -> set[str]:
    """참조의 DOI 로 볼 수 없는 것들 — 논문 자신의 DOI, 여러 항목에 반복된 DOI."""
    ban = {_norm_doi(host_doi)} if host_doi else set()
    seen: dict[str, int] = {}
    for p in printed:
        for d in {_norm_doi(m) for m in _DOI_RE.findall(p.get("raw") or "")}:
            seen[d] = seen.get(d, 0) + 1
    ban |= {d for d, n in seen.items() if n >= 3}   # 같은 DOI 가 세 항목에 = 조판 잔재
    ban.discard("")
    return ban


def _distrust_corrupt(printed: list[dict]) -> int:
    """파싱한 서지 필드가 **지면 원문과 다른 항목**은 그 필드를 버린다.

    GROBID 는 참고문헌을 외부 서지와 맞춰 보정하다가 통째로 **다른 논문의
    레코드를 갖다 붙이는** 일이 있다. 실측 3,309개 중 1개(10.1111/jdv.19451 의
    b30): 지면 원문은 'Ezzedine K, … Revised classification/nomenclature of
    vitiligo …' 인데 파싱된 title 은 'Standardizing serial photography …',
    doi 는 10.1016/j.jaad.2019.10.055 로 **제목도 DOI 도 다른 논문 것**이었다.
    이런 항목은 DOI 가 진짜로 존재하므로 형식검사를 다 통과한다 — 원문과
    대조하는 것 말고는 잡을 방법이 없다.

    빈도는 0.03% 지만 결과가 '15번을 눌렀더니 딴 논문' 이므로 반드시 막는다.
    지면 원문(raw)만 남기면 제목 대조로 제 짝을 다시 찾는다.
    """
    if fuzz is None:
        return 0
    n = 0
    for p in printed:
        t, raw = _nt(p.get("title") or ""), _nt(p.get("raw") or "")
        if len(t) < 12 or len(raw) < 20:
            continue
        if fuzz.partial_ratio(t, raw) < 80:
            p["title"] = ""
            p["doi"] = None
            p["pmid"] = None
            p["year"] = None
            p["journal"] = ""
            p["authors"] = []
            n += 1
    return n


def _surname(name: str) -> str:
    """iCite 저자표기('Kim, Hyunjin' / 'Hyunjin Kim')에서 성만."""
    n = _ascii(name).strip()
    if "," in n:
        n = n.split(",")[0]
    else:
        parts = n.split()
        n = parts[-1] if parts else ""
    return _NONWORD_RE.sub("", n.lower())


def _year_of(ref: dict) -> int | None:
    y = ref.get("year")
    try:
        return int(y) if y else None
    except (TypeError, ValueError):
        return None


def _title_score(pref: dict, item: dict) -> float:
    """지면 항목과 iCite 항목의 제목 닮은 정도(0~100).

    지면 제목이 있으면 제목끼리, 없으면(JATS mixed-citation 등) 지면 **원문**
    안에서 iCite 제목을 찾는다. 후자는 부분일치라 임계를 더 높게 둔다.
    """
    if fuzz is None:
        return 0.0
    it = _nt(item.get("title") or "")
    if len(it) < 12:                      # 너무 짧은 제목은 아무 데나 붙는다
        return 0.0
    pt = _nt(pref.get("title") or "")
    if len(pt) >= 12:
        return float(fuzz.token_sort_ratio(pt, it))
    raw = _nt(pref.get("raw") or "")
    if len(raw) < 20:
        return 0.0
    # partial_ratio 는 짧은 제목이 긴 원문 어딘가에 걸치기만 해도 높게 나온다.
    # 제목의 실질 낱말이 원문에 실제로 있는지로 한 번 더 막는다.
    toks = {w for w in it.split() if len(w) >= 4}
    if toks and sum(1 for w in toks if w in raw) / len(toks) < 0.6:
        return 0.0
    return float(fuzz.partial_ratio(it, raw)) - 4.0   # 원문 대조는 한 단계 깎는다


def _year_ok(pref: dict, item: dict) -> bool:
    """연도가 서로를 부정하지 않는가(둘 다 있고 2년 넘게 벌어지면 아니다)."""
    py, iy = _year_of(pref), _year_of(item)
    if py and iy:
        return abs(py - iy) <= 1
    raw_years = {int(y) for y in _YEAR_RE.findall(pref.get("raw") or "")}
    if iy and raw_years:
        return any(abs(iy - y) <= 1 for y in raw_years)
    return True                            # 근거가 없으면 부정하지 않는다


def _author_year_score(pref: dict, item: dict) -> float:
    """제1저자 성 + 연도 + 저널이 지면 원문 안에 다 있는가(3순위 근거)."""
    raw = _nt(pref.get("raw") or "")
    if len(raw) < 20:
        return 0.0
    sur = _surname((item.get("authors") or [""])[0])
    iy = _year_of(item)
    if len(sur) < 3 or not iy or f" {sur} " not in f" {raw} ":
        return 0.0
    if str(iy) not in (pref.get("raw") or ""):
        return 0.0
    jt = [w for w in _nt(item.get("journal") or "").split() if len(w) > 2]
    if not jt or not any(w in raw for w in jt):
        return 0.0
    return 80.0


def pair(printed: list[dict], items: list[dict],
         host_doi: str | None = None) -> list[tuple[int, int, str]]:
    """지면 항목 ↔ iCite 항목 짝짓기. (지면 index, iCite index, 근거) 목록.

    후보쌍을 전부 채점해 **점수 높은 순으로 확정**한다(양쪽 다 아직 안 쓰인 것만).
    한 지면 항목이 여러 iCite 항목에 비슷하게 닮았을 때 앞쪽부터 집어 먹는
    탐욕적 순회의 오배정을 막기 위해서다.
    """
    ban = _banned_dois(printed, host_doi)
    cand: list[tuple[float, int, int, str]] = []
    for pi, p in enumerate(printed):
        pdois = _dois_of(p, ban)
        for ii, it in enumerate(items):
            ts = _title_score(p, it)
            # 1순위 — DOI. 단, 제목이 명백히 다르면 그 DOI 는 뒤섞인 것이다.
            if it.get("doi") and _norm_doi(it["doi"]) in pdois:
                if ts and ts < _T_DOI_VETO:
                    continue
                cand.append((200.0 + ts, pi, ii, "doi"))
                continue
            # 2순위 — 제목 + 연도
            if ts >= (_T_TITLE if _nt(p.get("title") or "") else _T_RAW) \
                    and _year_ok(p, it):
                cand.append((100.0 + ts, pi, ii, "title"))
                continue
            # 3순위 — 제1저자 성 + 연도 + 저널
            a = _author_year_score(p, it)
            if a:
                cand.append((a, pi, ii, "author-year"))

    cand.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_p: set[int] = set()
    used_i: set[int] = set()
    out: list[tuple[int, int, str]] = []
    for _score, pi, ii, how in cand:
        if pi in used_p or ii in used_i:
            continue
        used_p.add(pi)
        used_i.add(ii)
        out.append((pi, ii, how))
    return out


# ── 정본 반영 ────────────────────────────────────────────────────────
def ensure_numbers(doc: dict) -> dict[str, int]:
    """지면 번호가 없는 정본에 자리번호를 준다. key → number.

    JATS <ref-list> 는 **지면 순서 그대로**이므로 자리번호 = 지면 번호다(출판사
    정본 XML). GROBID 경로는 grobid_client 가 인용 마커로 이미 번호를 매겨 놓았다.

    GROBID 정본인데 번호가 없다면 **옛 파서로 만든 정본**이다. 그때는 자리번호를
    주지 않는다 — GROBID 의 나열 순서는 지면 순서가 아닐 수 있고(실측 104편 중
    22편이 어긋났다. 10.25259/ijdvl_558_2021 은 첫 항목이 지면 21번), 자리번호를
    믿고 본문을 이으면 [15] 가 딴 논문으로 간다. TEI 를 다시 읽어야 한다
    (배치 경로는 renumber_from_tei 로 캐시에서 되살린다).
    """
    refs = doc.get("references") or []
    if any(r.get("number") for r in refs):
        return {r["key"]: r["number"] for r in refs if r.get("key") and r.get("number")}
    if doc.get("source") == "grobid":
        doc["references_numbering"] = "unknown"
        return {}
    for i, r in enumerate(refs, 1):
        r["number"] = i
    return {r["key"]: r["number"] for r in refs if r.get("key")}


def renumber_from_tei(doc: dict, tei_path: Path) -> int:
    """옛 정본의 참고문헌을 TEI 캐시에서 다시 읽어 번호·원문을 되살린다.

    GROBID 를 다시 부르지 않고(캐시가 있으므로) 번호·지면 순서·읽을 수 있는
    원문(raw_reference)을 복구한다. 키가 서로 어긋나면 손대지 않는다.
    """
    refs = doc.get("references") or []
    if not refs or any(r.get("number") for r in refs) or not tei_path.exists():
        return 0
    try:
        from lxml import etree
        from .grobid_client import _build_refs
        fresh, key_num = _build_refs(etree.parse(str(tei_path)).getroot())
    except Exception as e:  # noqa: BLE001 — 캐시가 깨졌으면 그냥 두고 넘어간다
        log(f"      ! TEI 재번호 실패({tei_path.name}): {type(e).__name__}: {e}")
        return 0
    have = {r.get("key") for r in refs}
    if not fresh or len(have & {r.key for r in fresh}) < 0.8 * len(have):
        return 0                              # 다른 문서의 TEI — 건드리지 않는다
    doc["references"] = [r.__dict__.copy() for r in fresh]
    doc.pop("references_numbering", None)
    return sum(1 for v in key_num.values() if v)


def relink_cited_refs(doc: dict, key_num: dict[str, int]) -> int:
    """본문 인용을 **지면 번호**로 다시 잇는다. 이어진 인용 수를 돌려준다.

    근거는 두 가지. (a) 파서가 남긴 로컬키(b14 / iid3316-bib-0001)를 번호로
    바꾸고, (b) 파서가 목록과 잇지 못해 표시번호만 남은 것('num:15')은 인쇄된
    번호가 마커에 그대로 있으므로 그걸 쓴다. 어느 쪽으로도 번호를 못 얻으면
    **잇지 않는다**.
    """
    known = {int(n) for n in key_num.values() if n}
    n_linked = 0
    for s in doc.get("body_text") or []:
        for p in s.get("paragraphs") or []:
            nums: list[str] = []
            for k in p.get("cited_keys") or []:
                k = str(k)
                if k.startswith("num:"):
                    v = k[4:]
                    n = int(v) if v.isdigit() and int(v) in known else None
                else:
                    n = key_num.get(k)
                if n and str(n) not in nums:
                    nums.append(str(n))
            p["cited_refs"] = nums
            n_linked += len(nums)
    return n_linked


def _positional_numbering(doc: dict, printed: list[dict]) -> bool:
    """번호가 '나열 순서'로만 매겨졌는가 — 인용 마커의 뒷받침이 없는가.

    GROBID 가 인용을 목록 항목에 이어 준 논문은 번호가 마커에서 왔으므로 믿는다.
    하나도 못 이어 준 논문만 나열 순서로 번호를 매겼고, 그때만 아래의 보정을
    적용한다(멀쩡한 논문을 건드리지 않기 위한 자물쇠).
    """
    numbered = {r.get("key") for r in printed if r.get("number")}
    for s in doc.get("body_text") or []:
        for p in s.get("paragraphs") or []:
            for k in p.get("cited_keys") or []:
                if not str(k).startswith("num:") and k in numbered:
                    return False
    return True


def _max_marker(doc: dict) -> int:
    """본문에 인쇄된 인용 번호의 최대값(합본 지면 판정용)."""
    hi = 0
    for s in doc.get("body_text") or []:
        for p in s.get("paragraphs") or []:
            for k in p.get("cited_keys") or []:
                k = str(k)
                if k.startswith("num:") and k[4:].isdigit():
                    hi = max(hi, int(k[4:]))
    return hi


def _split_foreign(printed: list[dict], paired: set[int], n_icite: int,
                   max_marker: int) -> tuple[list[int], list[int]] | None:
    """합본 지면에서 **옆 논문의 참고문헌 목록**이 통째로 섞인 경우를 가려낸다.

    실측(정본 97편 중 5편, 전부 JAAD/JACI research letter): 한 지면에 두 편이
    이어 실려 GROBID 가 두 목록을 하나로 이어 붙인다. 예) 10.1016/j.jaad.2013.05.012
    ('Green foot syndrome') 은 PDF 에 REFERENCES 표제가 **두 번** 나오고 앞의 것은
    앞 편(Elston DM, Patient safety…)의 것이다. 나열 순서로 번호를 매기면 본문
    [1] 이 앞 편의 1번(Elston)으로 간다 — PDF 지면의 1번은 Hall JH 다.

    가려내는 근거 세 가지가 **모두** 맞을 때만 손댄다:
      · 짝지어진 항목이 한쪽에 뭉쳐 있고 반대쪽에는 하나도 없다(두 목록의 경계)
      · 본문이 쓰는 최대 인용번호가 그 뭉치 크기 안에 든다(목록이 본문보다 길다)
      · 뭉치 크기가 iCite 가 말하는 참조 개수와 비슷하다
    한국 저널처럼 iCite 재현율이 낮아 뒤쪽이 통째로 안 짝지어지는 경우와
    구별되는 지점은 두 번째 근거다 — 그런 논문은 본문이 끝번호까지 인용한다.

    반환: (남의 것 index 목록, 이 논문 것 index 목록) 또는 None.
    """
    L = len(printed)
    if L < 5 or len(paired) < 3 or max_marker < 2 or n_icite < 3 or L <= max_marker:
        return None
    lo, hi = min(paired), max(paired)
    # 짝지어진 항목이 놓인 구간을 이 논문 것으로 본다. 앞뒤 어느 쪽을 남의 것으로
    # 볼지는 세 가지 모양을 순서대로 시험한다(앞뒤 둘 다 / 앞만 / 뒤만).
    for span in ((lo, hi + 1), (lo, L), (0, hi + 1)):
        own = list(range(*span))
        if not own:
            continue
        foreign = [i for i in range(L) if i not in set(own)]
        hit = sum(1 for i in own if i in paired)
        if len(foreign) < 2 or hit < 3 or hit < 0.7 * len(own):
            continue
        if max_marker > len(own):
            continue
        if abs(len(own) - n_icite) > max(2, 0.4 * n_icite):
            continue
        return foreign, own
    return None


def merge(doc: dict, items: list[dict], host_doi: str | None = None) -> dict:
    """지면 목록과 iCite 목록을 합쳐 doc['references'] 를 확정한다.

    돌려주는 값은 집계(감사용). doc 는 제자리에서 고친다.
    """
    printed = list(doc.get("references") or [])
    for p in printed:                      # 옛 스키마로 만든 정본도 받아들인다
        p.setdefault("number", None)
        p.setdefault("journal", "")
        p.setdefault("authors", [])
        p.setdefault("raw", "")
        p.setdefault("source", "parsed")
        p.setdefault("match", "")
    n_corrupt = _distrust_corrupt(printed)
    stat = {"printed": len(printed), "icite": len(items), "corrupt": n_corrupt,
            "doi": 0, "title": 0, "author-year": 0,
            "unpaired_printed": 0, "icite_only": 0}

    matched_i: dict[int, int] = {}         # iCite index → printed index
    if printed and items:
        for pi, ii, how in pair(printed, items, host_doi):
            matched_i[ii] = pi
            stat[how] += 1
            p, it = printed[pi], items[ii]
            p["doi"] = it.get("doi") or None
            p["pmid"] = it.get("pmid")
            p["title"] = it.get("title") or p.get("title") or ""
            p["year"] = it.get("year") or p.get("year")
            p["journal"] = it.get("journal") or p.get("journal") or ""
            p["authors"] = it.get("authors") or p.get("authors") or []
            p["source"] = "parsed+icite"
            p["match"] = how

    # 합본 지면에서 옆 논문 목록이 통째로 섞였는지 — 번호가 나열 순서로만 매겨진
    # 논문에 한해 확인하고, 확실할 때만 남의 목록을 빼고 다시 1번부터 매긴다.
    paired_idx = {pi for pi in matched_i.values()}
    if _positional_numbering(doc, printed):
        split = _split_foreign(printed, paired_idx, len(items), _max_marker(doc))
        if split:
            foreign, own = split
            for i in foreign:
                printed[i]["number"] = None
                printed[i]["source"] = "foreign"
            for n, i in enumerate(own, 1):
                printed[i]["number"] = n
            stat["foreign_block"] = len(foreign)

    for p in printed:
        if p.get("source") not in ("parsed+icite", "foreign"):
            # 짝을 못 찾았다 → 파싱한 서지값은 **믿을 수 없으니 지운다**.
            # 지면 원문(raw)만 남긴다. 번호는 지면에서 온 것이라 그대로 둔다.
            p["source"] = "parsed"
            p["match"] = ""
            p["doi"] = None
            p["pmid"] = None
            stat["unpaired_printed"] += 1
        elif p.get("source") == "foreign":
            p["match"] = ""
            p["doi"] = None
            p["pmid"] = None
            p["title"] = ""

    extra: list[dict] = []
    for ii, it in enumerate(items):
        if ii in matched_i:
            continue
        extra.append({"key": f"pmid{it['pmid']}", "number": None,
                      "doi": it.get("doi"), "pmid": it.get("pmid"),
                      "title": it.get("title", ""), "year": it.get("year"),
                      "journal": it.get("journal", ""),
                      "authors": it.get("authors") or [], "raw": "",
                      "source": "icite", "match": ""})
    stat["icite_only"] = len(extra)

    numbered = sorted((r for r in printed if r.get("number")),
                      key=lambda r: r["number"])
    unnumbered = [r for r in printed if not r.get("number")]
    doc["references"] = numbered + unnumbered + extra
    stat["numbered"] = len(numbered)
    stat.setdefault("foreign_block", 0)
    return stat


def reconcile(doc: dict, icite_rec: dict | None,
              detail: dict[str, dict] | None = None) -> dict:
    """정본 한 편의 참고문헌을 확정한다(번호·순서·내용·본문 링크).

    icite_rec 이 없으면(PMID 없음·오프라인·iCite 미수록) **지면 목록만으로**
    채운다. 비우지 않는다 — 원장이 목록을 보고 싶어 한다. 그 상태는
    references_source="parsed" 로 표시된다.
    """
    ensure_numbers(doc)
    items = icite_items((doc.get("meta") or {}).get("pmid") or "",
                        icite_rec or {}, detail or {})
    host = (doc.get("meta") or {}).get("doi") or doc.get("paper_id")
    stat = merge(doc, items, host if str(host).startswith("10.") else None)
    # **합친 뒤의** 번호로 잇는다 — 합본 지면 보정이 번호를 바꿀 수 있다.
    key_num = {r["key"]: r["number"] for r in doc.get("references") or []
               if r.get("key") and r.get("number")}
    stat["linked_citations"] = relink_cited_refs(doc, key_num)

    kinds = {r.get("source") for r in doc.get("references") or []}
    if not kinds:
        src = "none"
    elif "parsed+icite" in kinds:
        src = "parsed+icite"
    elif kinds == {"icite"}:
        src = "icite"
    else:
        src = "parsed"
    doc["references_source"] = src
    doc["references_match"] = stat
    return stat


# ── 오케스트레이션(배치) ─────────────────────────────────────────────
def run(config: dict | None = None) -> None:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    cache_dir = work / "icite"
    md = cfg["metadata"]
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"],
                      timeout=md["timeout_sec"])

    paths = sorted((work / "normalized").glob("*.json"))
    docs = {p: utils.read_json(p) for p in paths}
    # 옛 파서로 만들어 번호를 잃은 정본은 TEI 캐시에서 번호를 되살린다
    # (GROBID 를 다시 부르지 않는다 — 캐시가 곧 그때의 TEI 다).
    n_renum = sum(1 for p, d in docs.items()
                  if renumber_from_tei(d, work / "tei" / f"{p.stem}.tei.xml"))
    if n_renum:
        log(f"  TEI 캐시에서 지면 번호 복구: {n_renum}편")
    metas = {m.get("doi"): m for m in
             (utils.read_json(p) for p in (work / "meta").glob("*.json"))}
    log(f"[참조] 지면 번호 + iCite 내용으로 참고문헌 확정: {len(docs)}편")

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

    # 2) 논문 본체 iCite 조회 → 3) 참조 PMID 상세 조회
    arts = fetch_icite(pmid_of.values(), cache_dir, http)
    ref_pmids: set[str] = set()
    for pm in pmid_of.values():
        for r in (arts.get(pm) or {}).get("references") or []:
            ref_pmids.add(str(r))
    log(f"  참조 PMID {len(ref_pmids)}건 상세 조회")
    detail = fetch_icite(ref_pmids, cache_dir, http)

    # 4) 정본에 반영
    agg = {"paired": 0, "printed": 0, "icite_only": 0, "unpaired": 0,
           "numbered": 0, "linked": 0}
    by_src: dict[str, int] = {}
    for p, d in docs.items():
        pm = pmid_of.get(p)
        if pm and not (d.get("meta") or {}).get("pmid"):
            d.setdefault("meta", {})["pmid"] = pm
        st = reconcile(d, arts.get(pm) if pm else None, detail)
        agg["printed"] += st["printed"]
        agg["paired"] += st["doi"] + st["title"] + st["author-year"]
        agg["unpaired"] += st["unpaired_printed"]
        agg["icite_only"] += st["icite_only"]
        agg["numbered"] += st["numbered"]
        agg["linked"] += st["linked_citations"]
        by_src[d["references_source"]] = by_src.get(d["references_source"], 0) + 1
        utils.write_json(p, d)

    log(f"[참조] 완료: 지면 {agg['printed']}개 중 번호 {agg['numbered']} · "
        f"iCite 짝 {agg['paired']} · 미짝 {agg['unpaired']} · "
        f"iCite 전용 추가 {agg['icite_only']} · 본문 인용 링크 {agg['linked']}건")
    log(f"        소스별 편수: {by_src}")


if __name__ == "__main__":
    run()
