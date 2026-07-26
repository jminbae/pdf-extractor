"""이웃 논문 경계 — 한 PDF 지면에 여러 편이 이어 실린 경우의 소속 판정.

JAAD·JACI·JID·BJD·JEADV·CED·J Dermatol·EJD·Ann Dermatol 의 research letter 는
한 PDF 지면에 여러 편이 연달아 조판된다. GROBID 도 PyMuPDF 폴백도 PDF 를
'논문 한 편'으로 보기 때문에 앞/뒤 편의 초록·문단·표·그림·참고문헌이 이 논문의
레코드로 통째 흘러든다. 개수 검사는 오히려 풍성해 보여 전부 통과한다.

이 모듈이 하는 일은 하나다 — **PDF 안에서 논문 경계를 찾아, 어떤 텍스트가 이
논문 것이고 어떤 텍스트가 이웃 것인지 판정한다.**

경계 신호(실물 PDF 에서 확인한 것만 쓴다)
  M1 단독 DOI 줄. 이 코퍼스의 모든 저널이 편마다 정확히 한 줄을 찍는다.
     · Elsevier(JAAD/JACI/JID) `http://dx.doi.org/10.1016/j.jaad.2016.04.002`
       — 참고문헌 뒤, 즉 **그 편의 끝**.
     · Wiley/JEADV `DOI: 10.1111/jdv.16524` — 역시 앞 편의 끝(제목 앞).
     · Wiley/BJD  `DOI: 10.1111/bjd.18340` — 제목 **바로 아래**, 즉 그 편의 머리.
     · EJD `doi:10.1684/ejd.2018.3344` — 그 편의 끝.
     같은 모양의 줄이 저널마다 머리도 되고 꼬리도 되므로 **줄 자체로는 판정할 수
     없다.** 제목 런이 앞에 붙었는지 뒤에 붙었는지로 가른다.
  M2 편지 서두("To the Editor", "DEAR EDITOR", "Dear Editor", "SIR,", "Editor").
     research letter 는 예외 없이 이 줄로 본문을 연다.
  M3 제목 런. 본문 폰트와 **다른 글꼴**이거나 더 큰 크기의 줄이 서두 바로 앞에
     연달아 나온다. JAAD 는 제목과 본문의 크기가 10.0pt 로 같고 글꼴만
     AdvPS9B31 / AdvPS9B2B 로 갈리므로 크기만 봐서는 못 잡는다.

경계는 **M2 를 닻으로 M3 를 거슬러 올라가** 잡는다. M1 은 구간에 DOI 라는
이름표를 붙이는 데 쓴다(그래야 '이 구간이 이 논문'이라고 말할 수 있다).

확신이 없으면 아무것도 자르지 않는다
  잘못 자르면 본문이 사라진다. 오염보다 소실이 나쁘다. 그래서
  · 이 논문 구간을 **DOI 일치 또는 제목 일치**로 지목하지 못하면 → 자르지 않는다.
  · 구간이 하나뿐이면 → 자르지 않는다.
  · 메타(제목/DOI)가 가리키는 구간과 정본 본문이 실제로 놓인 구간이 **다르면**
    → 자르지 않는다. 이건 경계 문제가 아니라 신원(paper_id) 문제이므로
    `identity_conflict` 로 보고만 한다(10.1111/jdv.16524 유형).
  · 개별 항목도 마찬가지다. PDF 에서 위치를 못 찾으면 `unknown` 이고,
    unknown 은 **남긴다**. 이웃 구간에서 확실히 찾았을 때만 버린다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


# 2단 판정은 폴백 파서와 규칙이 같지만 **전폭 줄 허용치가 다르다**. 폴백은 본문
# 재구성이 목적이라 전폭 줄이 30%를 넘으면 2단 판정을 포기하는데, 그러면 이 모듈이
# 필요로 하는 읽기순서가 무너진다 — 실측 10.1111/bjd.17850 1쪽은 전폭 그림 캡션
# 12줄 때문에 30% 문턱(11.7)을 0.3줄 차이로 넘겨 2단 판정이 무산됐고, 좌·우 단이
# y 순으로 교차해 제목 3줄 사이에 옆 단 본문이 끼어들었다. 경계 탐지는 문단 복원과
# 달리 전폭 띠를 '구분선'으로만 쓰므로 허용치를 넉넉히 둔다.
COL_MIN_LINES = 4
COL_MIN_SHARE = 0.20
COL_MAX_SPAN = 0.60


# ── 신호 정규식 ──────────────────────────────────────────────────────
# 줄 전체가 DOI 인 것만 인정한다. 참고문헌 항목 끝의 'doi: 10.x' 는 앞에 서지가
# 붙어 있으므로 걸리지 않는다.
DOI_LINE_RE = re.compile(
    r'^\s*(?:article\s+)?(?:https?://)?(?:dx\.)?(?:doi\.org/|doi\s*:\s*)\s*'
    r'(10\.\d{4,9}/\S+?)\s*[.,;]?'
    # IJDVL 은 같은 줄에 PMID 를 붙여 찍는다: 'DOI: 10.25259/IJDVL_1369_20  PMID: 35962514'
    r'(?:\s*PMID\s*:?\s*\d+)?\s*$', re.I)

OPENER_RE = re.compile(
    r'^\s*(?:to\s+the\s+editors?|dear\s+editors?|editor|sir|madam)\s*[,:.]?\s*$'
    r'|^\s*(?:to\s+the\s+editors?|dear\s+editors?)\s*[,:]\s*\S',
    re.I)
# 'SIR,' 는 옛 BJD 서두. 다만 'Sir' 하나로는 약해서 대문자 또는 콤마를 요구한다.
SIR_RE = re.compile(r'^\s*(?:SIR|Sir)\s*[,:]')

# 제목으로 볼 수 없는 상투어(저널 표제·구역 머리)
NOT_TITLE_RE = re.compile(
    r'^(?:letters?|research\s+letters?|correspondence|references?|acknowledge?ments?|'
    r'conflicts?\s+of\s+interest|funding\s+sources?|disclosure|author\s+contributions?|'
    r'supporting\s+information|abstract|summary|key\s?words?|see\s+related|'
    r'linked\s+(?:article|comment)|editor\'?s?\s+note|table\s+\d|fig(?:ure)?\.?\s*\d|'
    r'appendix|erratum|correction|short\s+report|brief\s+report|original\s+article|'
    r'review\s+article|images?\s+in|journal\s+of\b|clinical\s+letter|'
    r'available\s+online|additional\s+information|supplement(?:al|ary)\s+information|'
    r'materials?\s+and\s+methods|patients?\s+and\s+methods|statistical\s+analys|'
    r'study\s+design|data\s+(?:collection|availability|sharing)|randomi[sz]ation|'
    r'funding|disclosures?|declaration|received:|accepted:|revised:|'
    # 절 제목 — 이것들이 제목 런으로 승격되면 논문 한가운데가 경계가 된다
    r'(?:introduction|background|methods?|materials?|results?|discussion|conclusions?|'
    r'limitations?|patients?\s+and\s+methods|case\s+report|statistical\s+analysis|'
    r'data\s+availability|ethics?|consent|orcid|competing\s+interests?|'
    r'author\s+information|to\s+the\s+editor|dear\s+editor)\s*[:.]?\s*$)', re.I)

AUTHORLINE_RE = re.compile(
    r'^[A-Z][A-Za-z\'`’.\- ]+,?\s*(?:M\.?D\.?|Ph\.?D\.?|MSc|MBBS|MPH|iD\b)',
    re.I)

# 제목이 아니라 저자·소속·교신 줄인지. 저자 블록을 제목으로 오인하면 논문
# 한가운데(저자 서명 자리)가 새 논문의 시작으로 잡힌다(실측 10.1111/jdv.16226
# 은 저자 블록 세 줄이 각각 구간이 되어 5개로 쪼개졌다).
_FUNC_WORDS = {"the", "of", "and", "in", "with", "for", "a", "an", "to", "on",
               "after", "by", "from", "is", "are", "as", "at", "using", "during",
               "into", "versus", "vs", "case", "report"}


_DATE_RE = re.compile(
    r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|'
    r'aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s*\d{4}',
    re.I)


def _same_journal(a: str, b: str) -> bool:
    """두 DOI 가 같은 저널의 것인가(등록기관 접두 + 저널 토큰).

    paper_id 오배정을 보고할 때 이 조건을 요구한다 — 실제 실패 양상은 '옆에 실린
    형제 논문의 DOI 로 파일링' 이므로 반드시 같은 저널이다. 이 조건이 없으면
    참고문헌·자료저장소 DOI 가 '올바른 DOI' 로 둔갑한다(실측
    10.1001/jamadermatol.2026.0294 → 10.1111/1346-8138.12099 같은 오보).
    """
    def key(d: str):
        d = (d or "").strip().lower()
        if "/" not in d:
            return None
        pre, suf = d.split("/", 1)
        toks = re.split(r'[./_\-]', suf)
        toks = [t for t in toks if t]
        if not toks:
            return None
        tok = toks[0]
        if tok == "j" and len(toks) > 1:      # Elsevier 'j.jaad.…'
            tok = toks[1]
        return pre, tok
    ka, kb = key(a), key(b)
    return bool(ka and kb and ka == kb)


def _looks_author(text: str) -> bool:
    t = text.strip()
    if "@" in t or re.search(r'\b(?:e-?mail|correspondence|department|university|'
                             r'hospital|college of medicine)\b', t, re.I):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", t)
    if len(words) < 2:
        return False
    if any(w.lower() in _FUNC_WORDS for w in words):
        return False
    caps = sum(1 for w in words if w[:1].isupper())
    if caps / len(words) < 0.8:
        return False
    return bool(t.count(",") >= 1
                or re.search(r'\b(?:MD|PhD|MSc|MBBS|MPH|iD)\b', t)
                or re.search(r'\d\s*[,*]', t))


def _norm(s: str) -> str:
    """대조용 정규화 — NFKC 후 영숫자만 남기고 소문자."""
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _norm_title(s: str) -> str:
    return _norm(s)


def _col_split(page_lines: list[dict]) -> float | None:
    """2단 조판의 경계 x. 2단이 아니면 None."""
    n = len(page_lines)
    if n < 8:
        return None
    lo = min(l["x0"] for l in page_lines)
    hi = max(l["x1"] for l in page_lines)
    width = hi - lo
    if width < 100:
        return None
    floor = max(COL_MIN_LINES, n * 0.06)
    bins: dict[int, list[float]] = {}
    for l in page_lines:
        bins.setdefault(round(l["x0"] / 3) * 3, []).append(l["x0"])
    strong = sorted(k for k, v in bins.items() if len(v) >= floor)
    if len(strong) < 2:
        return None
    left = strong[0]
    best = None
    for k in strong[1:]:
        r = min(bins[k])
        if r < left + width * 0.25:
            continue
        nl = sum(1 for l in page_lines if l["x1"] <= r - 1)
        nr = sum(1 for l in page_lines if l["x0"] >= r - 1)
        span = n - nl - nr
        if nl < COL_MIN_LINES or nr < COL_MIN_LINES:
            continue
        if min(nl, nr) < (nl + nr) * COL_MIN_SHARE:
            continue
        if span > (nl + nr) * COL_MAX_SPAN:
            continue
        score = min(nl, nr) - 0.5 * span
        if best is None or score > best[0]:
            best = (score, float(r))
    return best[1] if best else None


# ── 라인 수집 ────────────────────────────────────────────────────────
def _lines_of(doc) -> tuple[list[dict], float, str]:
    """PDF 전체 라인을 읽기순서로 돌려준다.  (lines, body_size, body_font)"""
    from collections import Counter

    pages: dict[int, list[dict]] = {}
    size_c: Counter = Counter()
    font_c: Counter = Counter()
    for pno in range(doc.page_count):
        pg = doc[pno]
        pw, ph = pg.rect.width, pg.rect.height
        rows: list[dict] = []
        for bi, b in enumerate(pg.get_text("dict")["blocks"]):
            if b.get("type") != 0:
                continue
            for l in b["lines"]:
                spans = l.get("spans") or []
                text = "".join(s["text"] for s in spans)
                if not text.strip():
                    continue
                # 회전(세로 조판) 줄은 흐름에서 뺀다. 세로 표가 한 단을 통째로
                # 차지하면 좌표만으로는 읽기순서를 세울 수 없어 옆 단 제목이 표
                # 셀 사이에 끼어든다(실측 10.1016/j.jaad.2016.04.002 2쪽: 세로
                # Table I 때문에 옆 단의 빈대 레터 제목을 놓쳤다). 빠진 줄의
                # 텍스트는 어느 구간에서도 찾을 수 없게 되므로 판정은 unknown 이
                # 되고, unknown 은 남긴다 — 소실보다 오염을 택하지 않는다.
                dx, dy = l.get("dir") or (1.0, 0.0)
                if abs(dx) < abs(dy) or dx < 0:
                    continue
                text = unicodedata.normalize("NFKC", text)
                size = round(max(s["size"] for s in spans), 1)
                # 글꼴은 '글자 수가 가장 많은' span 의 것으로 대표시킨다.
                fc: Counter = Counter()
                flag_or = 0
                for s in spans:
                    fc[s.get("font", "")] += len(s["text"])
                    flag_or |= s.get("flags", 0)
                font = fc.most_common(1)[0][0] if fc else ""
                x0, y0, x1, y1 = l["bbox"]
                rows.append(dict(text=text, size=size, font=font,
                                 bold=bool(flag_or & 16), page=pno, blk=bi,
                                 x0=x0, y0=y0, x1=x1, y1=y1,
                                 page_w=pw, page_h=ph, col=0))
                size_c[size] += len(text)
                font_c[(size, font)] += len(text)
        pages[pno] = rows

    body_size = size_c.most_common(1)[0][0] if size_c else 10.0
    body_font = ""
    for (sz, fn), _ in font_c.most_common():
        if sz == body_size:
            body_font = fn
            break

    out: list[dict] = []
    for pno in sorted(pages):
        pl = pages[pno]
        bnd = _col_split(pl) if (_col_split and pl) else None
        if bnd is None:
            for l in pl:
                l["col"] = -1
        else:
            for l in pl:
                l["col"] = 0 if l["x1"] <= bnd - 1 else (1 if l["x0"] >= bnd - 1 else -1)
        pl.sort(key=lambda d: (round(d["y0"], 1), d["x0"]))
        band: list[dict] = []
        for ln in pl:
            if ln["col"] == -1:
                out.extend(sorted(band, key=lambda d: (d["col"], round(d["y0"], 1), d["x0"])))
                band = []
                out.append(ln)
            else:
                band.append(ln)
        out.extend(sorted(band, key=lambda d: (d["col"], round(d["y0"], 1), d["x0"])))
    for i, ln in enumerate(out):
        ln["i"] = i
    return out, body_size, body_font


# ── 신호 판정 ────────────────────────────────────────────────────────
def _is_doi_line(ln: dict) -> str | None:
    m = DOI_LINE_RE.match(ln["text"])
    return m.group(1).rstrip(".,;") if m else None


def _is_opener(ln: dict) -> bool:
    t = ln["text"].strip()
    if len(t) > 200:
        return False
    return bool(OPENER_RE.match(t) or SIR_RE.match(t))


def _titleish(ln: dict, body_size: float, body_font: str) -> bool:
    """제목 런의 구성원이 될 수 있는 줄인가."""
    t = ln["text"].strip()
    if len(t) < 2 or len(t) > 160:
        return False
    if NOT_TITLE_RE.match(t):
        return False
    if _is_doi_line(ln) or _is_opener(ln):
        return False
    if ln["size"] < body_size - 0.15:      # 참고문헌·각주 크기는 제목이 아니다
        return False
    bigger = ln["size"] >= body_size + 0.6
    other_font = bool(body_font) and ln["font"] != body_font
    return bool(bigger or ln["bold"] or other_font)


def _doi_markers(lines: list[dict]) -> dict[int, str]:
    """**이음매에 홀로 선** DOI 줄만 경계 표식으로 인정한다.

    참고문헌 항목의 DOI 는 서지 다음 줄로 넘어가면서 줄 하나를 통째로 차지하는
    일이 흔하다(Wiley). 그것까지 표식으로 세면 참고문헌 목록 한가운데가 경계가
    되어 본문이 두 동강 난다(실측 10.1111/dth.13157: 참고문헌의
    `10.1097/IOP.0b013e3182141c37` 가 구간 이름표로 뽑혔다).
    가르는 잣대는 **아래쪽 간격**이다(실측 7편의 DOI 줄 12개를 재 보니
    참고문헌 안에 딸려 온 것은 아래 간격이 줄높이의 0.40배로 붙어 있고, 진짜
    이음매 표식은 0.95~7.2배로 떨어져 있었다). 위쪽 간격은 0.33~1.83배로 흩어져
    구분에 쓸 수 없다 — 표식 DOI 는 앞 편 참고문헌 마지막 줄 바로 아래에 붙는다.
    """
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ln in lines:
        groups[(ln["page"], ln["col"])].append(ln)
    iso: set[int] = set()
    for g in groups.values():
        g = sorted(g, key=lambda d: d["y0"])
        for k, ln in enumerate(g):
            h = max(4.0, ln["y1"] - ln["y0"])
            gap_dn = g[k + 1]["y0"] - ln["y1"] if k + 1 < len(g) else 1e9
            if gap_dn >= h * 0.85:
                iso.add(ln["i"])
    out = {}
    for ln in lines:
        d = _is_doi_line(ln)
        if not d:
            continue
        # PLOS 는 표·그림마다 하위 DOI 를 따로 찍는다(…0179088.t002). 이건 논문의
        # 신원이 아니다. 데이터 저장소 DOI(Mendeley·Zenodo·figshare·Dataverse)도
        # 논문 DOI 가 아니다 — 실측 10.1016/j.jaad.2022.11.005 는 자료 DOI
        # 10.17632/ctb8brksnm.1 을 논문 이름표로 달았다.
        if re.search(r'\.(?:[tgs]\d{3})$', d, re.I):
            continue
        if d.split("/", 1)[0] in {"10.17632", "10.5281", "10.6084", "10.7910",
                                  "10.5061", "10.24433"}:
            continue
        if ln["i"] in iso:
            out[ln["i"]] = d
    return out


# 별지(온라인 보조자료) 캡션 — E1·S1 번호가 붙은 표·그림
SUPP_CAP_RE = re.compile(
    r'^\s*(?:table|tab|fig(?:ure)?)\.?\s*[ES]-?\d{1,3}\b'
    r'|^\s*supplement(?:al|ary)?[\s.]', re.I)


def _running(lines: list[dict], npages: int) -> set[int]:
    """여러 페이지의 **같은 자리**에 반복되는 러닝헤드/푸터 줄.

    '여러 번 나온 문자열' 만으로 판정하면 표의 반복 셀('complete'·'diffuse')이
    전부 러닝헤드가 된다(실측 10.1016/j.jaad.2016.04.002 2쪽에서 84줄이 걸렸다).
    페이지 상·하단 12% 라는 **위치 증거**를 함께 요구한다.
    """
    if npages < 2:
        return set()
    from collections import defaultdict
    seen: dict[str, list[dict]] = defaultdict(list)
    for ln in lines:
        h = ln.get("page_h") or 0
        if h and not (ln["y0"] <= h * 0.12 or ln["y1"] >= h * 0.88):
            continue
        seen[_norm(ln["text"])[:60]].append(ln)
    out: set[int] = set()
    for k, group in seen.items():
        if len(k) < 4:
            continue
        if len({g["page"] for g in group}) >= max(2, round(npages * 0.5)):
            out.update(g["i"] for g in group)
    return out


# ── 자료구조 ─────────────────────────────────────────────────────────
@dataclass
class Segment:
    index: int
    start: int                       # 라인 인덱스(포함)
    end: int                         # 라인 인덱스(배타)
    doi: str | None = None
    title: str = ""
    evidence: list[str] = field(default_factory=list)
    doi_alt: str | None = None           # 경계에 놓여 양쪽 후보인 DOI

    def has_doi(self, doi: str) -> bool:
        doi = doi.strip().lower().rstrip(".")
        return doi in {(d or "").strip().lower().rstrip(".")
                       for d in (self.doi, self.doi_alt) if d}

    def to_dict(self) -> dict:
        return dict(index=self.index, start=self.start, end=self.end,
                    doi=self.doi, doi_alt=self.doi_alt, title=self.title,
                    evidence=list(self.evidence))


@dataclass
class BoundaryMap:
    """한 PDF 의 논문 경계와 소속 판정기."""
    path: Path
    segments: list[Segment]
    own: int | None
    confident: bool
    reason: str
    identity_conflict: dict | None = None
    _stream: str = ""
    _pos2line: list[int] = field(default_factory=list)
    _line_seg: list[int] = field(default_factory=list)
    _lines: list[dict] = field(default_factory=list)

    # 소속 판정 ------------------------------------------------------
    def locate_stats(self, text: str, probes: int = 12) -> tuple[list[int], int]:
        """텍스트를 PDF 에서 찾아 (조각이 놓인 구간 번호들, 시도 횟수)."""
        q = _norm(text)
        if len(q) < 24 or not self._stream:
            return ([], 0)
        step = max(24, len(q) // max(1, probes))
        hits: list[int] = []
        tried = 0
        i = 0
        while i + 24 <= len(q) and tried < probes * 2:
            probe = q[i:i + 40]
            if len(probe) >= 24:
                tried += 1
                p = self._stream.find(probe)
                if p >= 0:
                    hits.append(self._line_seg[self._pos2line[p]])
            i += step
        return (hits, tried)

    def locate(self, text: str, probes: int = 12) -> list[int]:
        return self.locate_stats(text, probes)[0]

    def owner(self, text: str) -> tuple[str, int, int]:
        """('own'|'other'|'unknown', 내 구간 표 수, 이웃 구간 표 수)

        **이웃 것이라고 말하려면 내 구간에서 단 한 조각도 나오지 않아야 한다.**
        비율로 판정하면 GROBID 가 경계를 넘겨 이어붙인 문단(내 문장 + 이웃 문장)
        에서 내 문장까지 함께 사라진다. 오염 한 문장을 남기는 편이 낫다.

        그리고 **조각의 절반 이상을 PDF 에서 실제로 찾았을 때만** 판정한다.
        2단 판정이 실패한 페이지에서는 좌·우 단이 교차해 문장이 이어지지 않으므로
        내 문장이 스트림에 없는 것처럼 보인다 — 그 상태에서 '내 구간 0회' 를
        근거로 삼으면 내 결론 문단이 이웃 것으로 지워진다(실측
        10.1016/j.jid.2018.09.024 p7: 내 결론 + 옆 letter 서두가 한 문단으로
        붙은 것인데 12조각 중 3조각만 찾혀 통째로 버려졌다).
        """
        if not self.confident or self.own is None:
            return ("unknown", 0, 0)
        raw, tried = self.locate_stats(text)
        hits = [h for h in raw if h >= 0]                   # -1 = 별지, 판정 보류
        if not hits:
            return ("unknown", 0, 0)
        if tried >= 3 and len(raw) < tried * 0.5:
            return ("unknown", 0, 0)
        mine = sum(1 for h in hits if h == self.own)
        others = len(hits) - mine
        if mine == 0 and others >= 2:
            return ("other", mine, others)
        return ("own", mine, others)

    def to_dict(self) -> dict:
        return dict(pdf=str(self.path), confident=self.confident, own=self.own,
                    reason=self.reason, identity_conflict=self.identity_conflict,
                    segments=[s.to_dict() for s in self.segments])


# ── 본체 ─────────────────────────────────────────────────────────────
def analyze(pdf_path: str | Path, meta: dict | None = None,
            body_probe: str = "") -> BoundaryMap:
    """PDF 를 구간으로 나누고 이 논문의 구간을 지목한다.

    meta: {'doi'|'paper_id', 'title'} 를 본다.
    body_probe: 정본 본문(이미 추출된 것). 메타가 가리키는 구간과 본문이 실제로
        놓인 구간이 어긋나는지(=paper_id 오배정) 교차 검증하는 데만 쓴다.
    """
    import fitz

    path = Path(pdf_path)
    meta = meta or {}
    want_doi = (meta.get("doi") or meta.get("paper_id") or "").strip().lower()
    want_title = _norm_title(meta.get("title") or "")

    doc = fitz.open(path)
    try:
        lines, body_size, body_font = _lines_of(doc)
        npages = doc.page_count
    finally:
        doc.close()

    if not lines:
        return BoundaryMap(path, [], None, False, "빈 PDF")

    run = _running(lines, npages)

    # 1) 마커 수집
    openers = [ln["i"] for ln in lines
               if _is_opener(ln) and ln["i"] not in run]
    doi_lines = _doi_markers(lines)

    # 2) 제목 런을 찾고, 그 옆의 증거(서두 / 머리DOI / 앞 편 꼬리DOI)로 절단점을
    #    인정한다. 서두만 닻으로 삼으면 서두가 아예 없는 저널을 통째로 놓친다 —
    #    CED 는 '제목 → doi: 10.1111/ced.13226 → 본문' 이고(실측 10.1111/ced.13226
    #    은 3편이 실린 지면인데 구간 1개로 잡혔다), EJD 는 '앞 편 doi → 제목 →
    #    본문' 이다(10.1684/ejd.2018.3344).
    idx_run = set(run)
    live = [ln for ln in lines if ln["i"] not in idx_run]

    def _next_live(i: int, k: int) -> list[dict]:
        out = []
        for ln in lines[i + 1:]:
            if ln["i"] in idx_run:
                continue
            out.append(ln)
            if len(out) >= k:
                break
        return out

    title_runs: list[list[dict]] = []
    cur: list[dict] = []
    for ln in live:
        if _titleish(ln, body_size, body_font):
            if cur:
                p = cur[-1]
                broken = (ln["page"] != p["page"] or ln["col"] != p["col"]
                          or ln["y0"] - p["y1"] > max(6.0, (p["y1"] - p["y0"]) * 1.6))
                if broken:
                    title_runs.append(cur)
                    cur = []
            cur.append(ln)
        elif cur:
            title_runs.append(cur)
            cur = []
    if cur:
        title_runs.append(cur)

    def _prose_run(after: list[dict], need: int = 3) -> bool:
        """이어지는 본문 산문 줄이 need 개 이상인가(= 여기서 논문이 시작한다)."""
        n = 0
        for ln in after:
            if (abs(ln["size"] - body_size) < 0.35 and ln["font"] == body_font
                    and len(ln["text"].strip()) >= 25):
                n += 1
                if n >= need:
                    return True
            elif n:
                return False
        return False

    cuts: dict[int, dict] = {}
    for tr in title_runs:
        while tr and (AUTHORLINE_RE.match(tr[-1]["text"].strip())
                      or _looks_author(tr[-1]["text"])):
            tr.pop()
        if not tr or len(tr) > 12:
            continue
        if any(_looks_author(l["text"]) for l in tr):
            continue
        title = " ".join(l["text"].strip() for l in tr).strip()
        # 짧은 줄은 제목으로 인정하지 않는다 — 'Young-Min Park' 같은 저자 한 줄이
        # 제목으로 승격되면 논문 한가운데가 경계가 된다(실측 10.5021/ad.2018.30.5.630).
        if len(title) < 20 or len(title) > 300 or len(title.split()) < 3:
            continue
        if _DATE_RE.search(title):         # 'Available online August 28, 2013.'
            continue
        after = _next_live(tr[-1]["i"], 10)
        head_doi_at = next((k for k, ln in enumerate(after[:2])
                            if ln["i"] in doi_lines), None)
        head_doi = doi_lines[after[head_doi_at]["i"]] if head_doi_at is not None else None
        opener_at = next((ln["i"] for ln in after[:3] if _is_opener(ln)), None)
        # 앞쪽 3줄 안의 단독 DOI = 앞 편의 꼬리
        prev_tail = None
        seen = 0
        for ln in reversed(lines[:tr[0]["i"]]):
            if ln["i"] in idx_run:
                continue
            seen += 1
            if seen > 3:
                break
            if ln["i"] in doi_lines:
                prev_tail = ln["i"]
                break

        # 구조 증거: 제목 바로 뒤에서 논문이 실제로 시작하는가
        body_after = after[head_doi_at + 1:] if head_doi_at is not None else after
        starts_here = opener_at is not None or _prose_run(body_after)
        ev = []
        if opener_at is not None:
            ev.append("제목런+서두")
        if head_doi and starts_here:
            ev.append("제목런+머리DOI")
        if prev_tail is not None and starts_here:
            ev.append("앞편 꼬리DOI+제목런")
        # 서두도 인접 DOI 도 없으면 경계로 인정하지 않는다. 구조만으로 자르면
        # 절 제목·그림 캡션이 전부 새 논문의 시작이 된다.
        if not ev:
            continue
        cuts[tr[0]["i"]] = dict(title=title, doi=head_doi, ev=ev, opener=opener_at)

    # 제목 런이 없는데 앞 편 꼬리 DOI 바로 뒤에 서두가 오는 경우(제목 조판이
    # 본문과 구분되지 않는 PDF)도 경계로 인정한다.
    for oi in openers:
        if any(s <= oi < s + 20 for s in cuts):
            continue
        prev = [k for k in doi_lines if k < oi and oi - k <= 4]
        if prev and oi not in cuts:
            cuts[oi] = dict(title="", doi=None, ev=["앞편 꼬리DOI+서두"], opener=oi)

    # 한 제목이 여러 런으로 쪼개지면 절단점이 연달아 생긴다(2단 판정이 제목의
    # 첫 줄만 전폭으로 보는 경우 등 — 실측 10.1111/phpp.12784·10.1111/pai.13931·
    # 10.1016/j.jid.2018.09.024·10.5021/ad.2017.29.6.817 에서 제목 한 개가 구간
    # 두 개가 됐다). 가까운 절단점은 하나로 합치고 제목도 이어 붙인다.
    merged: dict[int, dict] = {}
    for s in sorted(cuts):
        if merged:
            last = max(merged)
            gap = sum(1 for ln in lines[last:s] if ln["i"] not in idx_run)
            if gap <= 6:
                m = merged[last]
                m["title"] = (m["title"] + " " + cuts[s]["title"]).strip()
                m["doi"] = m["doi"] or cuts[s]["doi"]
                m["opener"] = m["opener"] if m["opener"] is not None else cuts[s]["opener"]
                for e in cuts[s]["ev"]:
                    if e not in m["ev"]:
                        m["ev"].append(e)
                continue
        merged[s] = cuts[s]
    cuts = merged

    # 3) 구간 만들기 — 첫 절단점 앞의 머리 구역(prefix) 처리
    #    Wiley 계열은 1쪽 맨 위에 'Received … | DOI: 10.1111/…' 와 저널 표제를
    #    찍는다. 이건 앞 편의 잔재가 아니라 **이 편의 머리**다. 앞 편의 잔재라면
    #    반드시 참고문헌 목록이나 상당한 분량의 본문을 달고 있다. 그 둘을
    #    가르지 않으면 머리 몇 줄이 '앞 논문 구간'이 되고, 거기 박힌 이 논문의
    #    DOI 때문에 '메타는 0번, 본문은 1번' 이라는 가짜 신원충돌이 난다
    #    (실측 10.1111/dth.13157 · 10.1111/pcmr.12814 · 10.1111/bjd.17850).
    starts = sorted(cuts)
    if not starts:
        starts = [0]
        cuts[0] = dict(title="", doi=None, ev=["문서 시작"], opener=None)
    elif starts[0] != 0:
        head = lines[:starts[0]]
        has_refhead = any(re.match(r'^\s*references?\s*$', l["text"], re.I) for l in head)
        if len(head) >= 25 or has_refhead:
            cuts[0] = dict(title="", doi=None, ev=["문서 시작(앞 편 잔재)"], opener=None)
            starts = [0] + starts
        else:
            # 머리 구역을 첫 구간에 붙인다
            c = cuts.pop(starts[0])
            c["ev"] = list(c["ev"]) + ["머리 구역 병합"]
            cuts[0] = c
            starts = [0] + starts[1:]
    segments: list[Segment] = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        c = cuts[s]
        segments.append(Segment(k, s, e, c["doi"], c["title"], list(c["ev"])))

    # 4) DOI 줄을 구간에 배정한다. **이음매에 놓인 DOI 를 먼저 처리해야 한다** —
    #    앞 구간 안에 있다고 무조건 그 구간의 꼬리로 삼으면, 뒤 편의 머리 DOI 를
    #    앞 편이 가져가 이름표가 통째로 어긋난다(실측 10.5021/ad.2018.30.5.630 은
    #    다음 편 Steatocystoma 의 DOI ...633 을 자기 이름표로 달았다).
    seam: dict[int, str] = {}
    for k in range(len(segments) - 1):
        b = segments[k + 1]
        near = [i for i in doi_lines if b.start - 4 <= i < b.start]
        for i in near:
            seam[i] = "?"

    # 이음매 DOI 가 앞 편의 꼬리인지 뒤 편의 머리인지는 **조판만으로 갈리지 않는다.**
    #   · JEADV  `DOI: 10.1111/jdv.16524`  → 위쪽 GvHD 편지의 것(꼬리)
    #   · Elsevier `http://dx.doi.org/10.1016/j.jaad.2016.04.002` → 꼬리
    #   · Ann Dermatol `https://doi.org/10.5021/ad.2018.30.5.630` → 아래 LGR4 논문의 것(머리)
    # 세 경우의 좌표·글꼴 배치가 사실상 같다. 그래서 기본값은 다수 관행인 '꼬리'로
    # 두되, **그 DOI 가 이 레코드의 paper_id 이고 아래 구간의 제목이 이 레코드의
    # 제목과 일치하면** 그때만 '머리'로 뒤집는다(그 편이 이 논문이라는 독립 증거가
    # 두 개 겹친 경우다).
    def _title_hit(seg: Segment) -> bool:
        if not want_title or len(want_title) < 20:
            return False
        t = _norm_title(seg.title)
        if len(t) < 20:
            return False
        import difflib
        return (t == want_title or t in want_title or want_title in t
                or difflib.SequenceMatcher(None, t, want_title).ratio() >= 0.82)

    for i in list(seam):
        nxt = next((s for s in segments if s.start - 4 <= i < s.start), None)
        if (want_doi and doi_lines[i].strip().lower().rstrip(".") == want_doi
                and nxt is not None and _title_hit(nxt)):
            seam[i] = "next"
        else:
            seam[i] = "prev"

    for seg in segments:
        if seg.doi:
            continue
        owned = []
        for i, d in doi_lines.items():
            if not (seg.start <= i < seg.end):
                continue
            if seam.get(i) == "next":       # 뒤 편의 머리다 — 내 것이 아니다
                continue
            owned.append(d)
        # 내 구간이 시작하기 직전의 이음매 DOI 가 '뒤 편(=나)의 머리' 로 판정됐다면
        for i, side in seam.items():
            if side == "next" and seg.start - 4 <= i < seg.start:
                owned.append(doi_lines[i])
        if len(owned) == 1:
            seg.doi = owned[0]
            seg.evidence.append("DOI 표식")
        elif len(owned) > 1:
            seg.doi = owned[-1]
            seg.evidence.append(f"DOI 표식(후보 {len(owned)})")

    # 4b) 구간 **사이**에 홀로 놓인 DOI 줄은 어느 쪽 것인지 줄만 봐서는 모른다.
    #     JEADV 는 앞 편의 꼬리로 찍고(10.1111/jdv.16524 = 앞의 GvHD 편지),
    #     Ann Dermatol 은 뒤 편의 머리로 찍는다(10.5021/ad.2018.30.5.630 = 뒤의
    #     LGR4 논문). 조판이 똑같아 위치로는 갈리지 않으므로 **양쪽 후보**로 둔다.
    #     어느 쪽에 더 가까이 붙어 조판됐는지(세로 간격)로 먼저 가르고, 두 간격이
    #     엇비슷할 때만 양쪽 후보로 남긴다. 실측: Ann Dermatol 은 DOI→제목 27pt /
    #     앞 참고문헌→DOI 85pt(뒤 편 머리), JAAD 는 22pt/31pt·JEADV 는 20pt/57pt
    #     (둘 다 앞 편 꼬리).
    for k in range(len(segments) - 1):
        a, b = segments[k], segments[k + 1]
        near = [i for i in doi_lines if b.start - 4 <= i < b.start]
        if not near:
            continue
        i = near[-1]
        d = doi_lines[i]
        if seam.get(i) != "both":
            continue
        if a.doi == d and not b.doi:
            b.doi_alt = d
            b.evidence.append("경계 DOI(양쪽 후보)")
        elif b.doi == d and not a.doi:
            a.doi_alt = d
            a.evidence.append("경계 DOI(양쪽 후보)")

    # 5) 문자 스트림 + 인덱스
    line_seg = [0] * len(lines)
    for seg in segments:
        for i in range(seg.start, min(seg.end, len(lines))):
            line_seg[i] = seg.index
    buf: list[str] = []
    pos2line: list[int] = []
    for ln in lines:
        t = _norm(ln["text"])
        buf.append(t)
        pos2line.extend([ln["i"]] * len(t))
    stream = "".join(buf)

    bm = BoundaryMap(path, segments, None, False, "", None,
                     stream, pos2line, line_seg, lines)

    # 6) 이 논문의 구간 지목
    #    구간이 하나뿐이어도 여기를 지난다 — 자를 것은 없지만 **paper_id 가 그
    #    논문의 것이 맞는지**는 확인해야 한다(실측 10.1111/jdv.17073 은 합본이
    #    아니라 연계 논평의 DOI 로 잘못 파일링된 경우다).
    #    제목 일치가 DOI 일치보다 강한 증거다 — DOI 줄은 앞뒤 어느 편 것인지
    #    조판만으로 갈리지 않는 자리에 놓이는 일이 있지만(4b 참조), PDF 에 찍힌
    #    제목이 PubMed 제목과 같으면 그 구간이 이 논문이라는 뜻이다.
    by_doi = [s.index for s in segments if want_doi and s.has_doi(want_doi)]
    by_title = []
    if want_title and len(want_title) >= 20:
        import difflib
        for s in segments:
            t = _norm_title(s.title)
            if len(t) < 20:
                continue
            # 포함 관계만 보면 쪽번호가 앞에 붙은 제목('979 Decreased risk…')이
            # 어긋난다(실측 10.1016/j.jaad.2020.09.016). 유사도도 함께 본다.
            if (t == want_title or t in want_title or want_title in t
                    or difflib.SequenceMatcher(None, t, want_title).ratio() >= 0.82):
                by_title.append(s.index)

    meta_pick = None
    how = ""
    if len(by_title) == 1:
        meta_pick, how = by_title[0], "제목 일치"
        if by_doi == by_title:
            how = "제목+DOI 일치"
    elif len(by_doi) == 1:
        meta_pick, how = by_doi[0], "DOI 일치"

    body_pick = None
    if body_probe:
        hits = [h for h in bm.locate(body_probe, probes=40) if h >= 0]
        if hits:
            from collections import Counter
            c = Counter(hits).most_common()
            top, n = c[0]
            second = c[1][1] if len(c) > 1 else 0
            if n >= len(hits) * 0.55 or (n >= len(hits) * 0.40 and n >= second * 2):
                body_pick = top

    # 6b) paper_id 검증 — 이 논문이라고 지목한 구간이 PDF 에 자기 DOI 를 찍어
    #     두었는데 그것이 paper_id 와 다르면, 기본키가 남의 논문 것이다.
    #     경계로 고칠 수 있는 문제가 아니므로 **보고만** 한다(metadata 담당 몫).
    #     단, 구간 자체는 옳게 지목했으므로 걸러내기는 그대로 진행한다.
    if meta_pick is not None and want_doi:
        mseg = segments[meta_pick]
        if mseg.doi and not mseg.has_doi(want_doi) and _same_journal(mseg.doi, want_doi):
            bm.identity_conflict = dict(
                paper_id=want_doi, correct_doi=mseg.doi,
                own_segment=meta_pick, own_title=mseg.title,
                how=how, evidence=list(mseg.evidence),
                note="구간은 확실히 지목됐고 그 구간의 DOI 가 paper_id 와 다름")

    if len(segments) < 2:
        s0 = segments[0]
        if (not bm.identity_conflict and want_doi and s0.doi
                and not s0.has_doi(want_doi) and _same_journal(s0.doi, want_doi)):
            bm.identity_conflict = dict(
                paper_id=want_doi, correct_doi=s0.doi, own_segment=0,
                own_title=s0.title, how="단일 논문 PDF", evidence=list(s0.evidence),
                note="PDF 에 찍힌 이 논문의 DOI 가 paper_id 와 다름")
        bm.reason = "구간 1개 — 합본 지면이 아님"
        if bm.identity_conflict:
            bm.reason += " (다만 paper_id 가 PDF 의 DOI 와 다르다)"
        return bm

    if meta_pick is None:
        # PDF 안 어느 구간의 DOI 도 paper_id 와 같지 않고 제목도 안 맞는다면
        # 기본키가 이 PDF 의 어느 논문도 가리키지 않는다는 뜻이다(보고만 한다).
        known = [s.doi for s in segments if s.doi]
        if len(known) >= 2 and not bm.identity_conflict and want_doi:
            bm.identity_conflict = dict(
                paper_id=want_doi, correct_doi=None, candidates=known,
                body_segment=body_pick, meta_segment=None,
                body_title=(segments[body_pick].title if body_pick is not None else ""),
                meta_title=meta.get("title") or "",
                note="paper_id 가 PDF 안 어느 논문의 DOI 와도 일치하지 않고 제목도 안 맞음")
        bm.reason = "이 논문의 구간을 DOI·제목 어느 쪽으로도 지목하지 못함 → 자르지 않음"
        return bm
    # 메타가 가리킨 구간이 **제목도 없는 잔재**(앞 편의 꼬리)인데 정본 본문은
    # 제목이 뚜렷한 다른 구간에 놓여 있다면, paper_id 가 이웃 편 것이다.
    # 메타 구간에 제목이 있으면 지목이 맞은 것이므로 오염이 심한 경우로 본다.
    if (body_pick is not None and body_pick != meta_pick
            and how != "제목+DOI 일치" and len(by_title) != 1
            and not segments[meta_pick].title
            and segments[body_pick].title
            and _same_journal(segments[body_pick].doi or "", want_doi)):
        # 메타는 A 구간을, 정본 본문은 B 구간을 가리킨다 → 지목 자체가 불확실하다.
        # 여기서 자르면 본문 전체가 날아갈 수 있으므로 아무것도 하지 않는다.
        bm.identity_conflict = dict(
            paper_id=want_doi, correct_doi=segments[body_pick].doi,
            body_segment=body_pick, body_title=segments[body_pick].title,
            meta_segment=meta_pick, meta_title=segments[meta_pick].title,
            evidence=list(segments[body_pick].evidence),
            note="메타가 가리키는 구간과 정본 본문이 놓인 구간이 다름")
        bm.reason = ("메타가 가리키는 구간과 정본 본문이 놓인 구간이 다름 "
                     "→ paper_id 오배정 의심, 자르지 않음")
        return bm

    # 6c) 지목한 구간이 **제목 없는 잔재**면 그건 논문이 아니라 앞 편의 꼬리다.
    #     여기를 '이 논문' 으로 삼고 자르면 진짜 본문이 통째로 사라진다
    #     (실측 10.1684/ejd.2018.3344: paper_id 가 앞 레터의 DOI 라서 잔재 구간이
    #      지목됐다. 실제 논문은 다음 구간 'Contact vitiligo…', DOI ...3350).
    if not segments[meta_pick].title:
        nxt = next((s for s in segments[meta_pick + 1:] if s.title), None)
        if (nxt is not None and not bm.identity_conflict
                and _same_journal(nxt.doi or "", want_doi)):
            bm.identity_conflict = dict(
                paper_id=want_doi, correct_doi=nxt.doi,
                body_segment=nxt.index, body_title=nxt.title,
                meta_segment=meta_pick, meta_title="",
                evidence=list(nxt.evidence),
                note="paper_id 가 가리킨 구간이 제목 없는 잔재(앞 편의 꼬리)다")
        bm.reason = ("paper_id 가 가리킨 구간이 제목 없는 잔재라 이 논문으로 볼 수 없음 "
                     "→ 자르지 않음")
        return bm

    # 6d) 정본 본문이 이 구간에 실제로 놓여 있는지 확인한다. 본문이 여러 구간에
    #     흩어져 다수가 없거나 다른 구간을 가리키면, 지목이 맞다는 보장이 없으므로
    #     자르지 않는다. 단 제목과 DOI 가 함께 맞으면 지목은 확실하고 본문 쪽이
    #     오염된 것이므로 진행한다(10.1111/bjd.21054).
    if body_probe and how != "제목+DOI 일치" and body_pick != meta_pick:
        bm.reason = ("정본 본문의 다수가 이 구간에 있지 않음 "
                     f"(본문 구간={body_pick}) → 자르지 않음")
        return bm

    bm.own = meta_pick
    bm.confident = True
    bm.reason = f"{how} (구간 {len(segments)}개 중 {meta_pick}번)"
    return bm


# ── 정본 걸러내기 ────────────────────────────────────────────────────
def filter_document(doc: dict, bmap: BoundaryMap, *, apply: bool = True,
                    references: bool = False) -> dict:
    """정본 dict 에서 이웃 논문 소속 항목을 제거하고 보고서를 돌려준다.

    doc 은 제자리에서 수정된다(apply=True 일 때). 확신이 없으면 아무것도 건드리지
    않는다. 판정이 'unknown' 인 항목도 남긴다 — 소실이 오염보다 나쁘다.

    references: 참고문헌 목록까지 걸러낼지. **기본값 False** — 참고문헌은 이제
        iCite(NIH) 에서 받아오므로 PDF 파싱 결과를 손댈 이유가 없다. 판정 자체는
        해서 보고서(`detail`)에는 남긴다(iCite 도입 전 데이터 점검용).
    """
    rep = {"pdf": str(bmap.path), "confident": bmap.confident,
           "reason": bmap.reason, "segments": len(bmap.segments),
           "identity_conflict": bmap.identity_conflict,
           "dropped": {"abstract": 0, "paragraphs": 0, "sections": 0,
                       "tables": 0, "figures": 0, "references": 0},
           "detail": []}
    if not bmap.confident or bmap.own is None:
        return rep

    def judge(text: str, kind: str, ident: str):
        lab, mine, others = bmap.owner(text or "")
        if lab == "other":
            rep["detail"].append(
                {"kind": kind, "id": ident, "mine": mine, "other": others,
                 "text": (text or "")[:180]})
        return lab

    # 초록 ---------------------------------------------------------
    ab = doc.get("abstract") or ""
    if ab and judge(ab, "abstract", "abstract") == "other":
        rep["dropped"]["abstract"] = 1
        if apply:
            doc["abstract"] = ""
            doc["abstract_source"] = "none"

    # 본문 ---------------------------------------------------------
    secs = doc.get("body_text") or []
    keep_secs = []
    for si, sec in enumerate(secs):
        paras = sec.get("paragraphs") or []
        keep_p = []
        for p in paras:
            if judge(p.get("text") or "", "paragraph", p.get("id") or f"s{si}") == "other":
                rep["dropped"]["paragraphs"] += 1
            else:
                keep_p.append(p)
        if not keep_p and paras:
            rep["dropped"]["sections"] += 1
            continue
        if apply:
            sec["paragraphs"] = keep_p
        keep_secs.append(sec)
    if apply:
        doc["body_text"] = keep_secs

    # 표·그림 ------------------------------------------------------
    # 별지(E1·S1) 캡션은 판정하지 않는다. Elsevier 는 여러 편의 온라인 보조자료를
    # 본문 뒤에 한꺼번에 붙이는데, 그 구역은 마지막 구간 안에 들어가 버려서
    # **이 논문 자신의 E-표·E-그림이 이웃 것으로 판정된다**(실측
    # 10.1016/j.jaci.2014.02.038 의 TABLE E1·E3·FIG E1, 10.1016/j.jaci.2018.05.015
    # 의 TABLE E1·FIG E1 — 다섯 개 전부 이 논문 것이었다). 반대로 이웃의 별지가
    # 섞여 들어온 것은 못 걸러내지만, 소실보다는 오염을 택한다.
    for key, kind in (("tables", "table"), ("figures", "figure")):
        items = doc.get(key) or []
        keep = []
        for it in items:
            cap = str(it.get("caption") or "")
            if SUPP_CAP_RE.match(cap):
                rep["detail"].append({"kind": kind, "id": it.get("id") or "",
                                      "mine": -1, "other": -1,
                                      "text": "별지 캡션 → 판정 보류: " + cap[:120]})
                keep.append(it)
                continue
            probe = " ".join(str(it.get(k) or "") for k in ("caption", "markdown"))
            if judge(probe, kind, it.get("id") or "") == "other":
                rep["dropped"][key] += 1
            else:
                keep.append(it)
        if apply:
            doc[key] = keep

    # 참고문헌 -----------------------------------------------------
    refs = doc.get("references") or []
    keep_r, dropped_keys = [], set()
    for r in refs:
        probe = r.get("raw") or r.get("title") or ""
        if judge(probe, "reference", r.get("key") or "") == "other":
            rep["dropped"]["references"] += 1
            dropped_keys.add(r.get("key"))
        else:
            keep_r.append(r)
    if apply and references:
        doc["references"] = keep_r
        if dropped_keys:
            _prune_citations(doc, dropped_keys)
    return rep


def _prune_citations(doc: dict, dropped_keys: set) -> None:
    """버린 참고문헌을 가리키던 인용 링크를 끊는다(끊긴 그래프를 남기지 않는다)."""
    for sec in doc.get("body_text") or []:
        for p in sec.get("paragraphs") or []:
            keys = p.get("cited_keys") or []
            if not keys:
                continue
            idx = [i for i, k in enumerate(keys) if k in dropped_keys]
            if not idx:
                continue
            refs = p.get("cited_refs") or []
            p["cited_keys"] = [k for i, k in enumerate(keys) if i not in set(idx)]
            if len(refs) == len(keys):
                p["cited_refs"] = [r for i, r in enumerate(refs) if i not in set(idx)]


def apply_to_parsed(document, pdf_path: str | Path, meta: dict | None = None,
                    *, references: bool = False) -> dict:
    """schema.Document(데이터클래스)에 그대로 적용한다 — pdf_fallback 연결용."""
    meta = dict(meta or {})
    meta.setdefault("doi", getattr(document, "paper_id", None))
    if not meta.get("title"):
        meta["title"] = getattr(getattr(document, "meta", None), "title", "") or ""
    probe = " ".join(p.text or "" for s in (document.body_text or [])
                     for p in (s.paragraphs or []))
    bm = analyze(pdf_path, meta, body_probe=probe)
    rep = {"pdf": str(bm.path), "confident": bm.confident, "reason": bm.reason,
           "segments": len(bm.segments), "identity_conflict": bm.identity_conflict,
           "dropped": {"abstract": 0, "paragraphs": 0, "sections": 0,
                       "tables": 0, "figures": 0, "references": 0}}
    if not bm.confident:
        return rep

    if document.abstract and bm.owner(document.abstract)[0] == "other":
        rep["dropped"]["abstract"] = 1
        document.abstract = ""
        document.abstract_source = "none"

    keep_secs = []
    for sec in document.body_text or []:
        keep_p = [p for p in (sec.paragraphs or [])
                  if bm.owner(p.text or "")[0] != "other"]
        rep["dropped"]["paragraphs"] += len(sec.paragraphs or []) - len(keep_p)
        if not keep_p and sec.paragraphs:
            rep["dropped"]["sections"] += 1
            continue
        sec.paragraphs = keep_p
        keep_secs.append(sec)
    document.body_text = keep_secs

    for attr, key in (("tables", "tables"), ("figures", "figures")):
        items = getattr(document, attr, None) or []
        keep = []
        for it in items:
            cap = getattr(it, "caption", "") or ""
            if SUPP_CAP_RE.match(cap):       # 별지 캡션은 판정 보류
                keep.append(it)
                continue
            probe_t = cap + " " + (getattr(it, "markdown", "") or "")
            if bm.owner(probe_t)[0] == "other":
                rep["dropped"][key] += 1
            else:
                keep.append(it)
        setattr(document, attr, keep)

    if references:
        refs = getattr(document, "references", None) or []
        keep_r = [r for r in refs
                  if bm.owner(getattr(r, "raw", "") or getattr(r, "title", "") or "")[0]
                  != "other"]
        rep["dropped"]["references"] = len(refs) - len(keep_r)
        document.references = keep_r
    return rep


def apply_to_document(doc: dict, pdf_path: str | Path | None = None) -> dict:
    """정본 dict 하나를 받아 경계 분석 → 필터까지 한 번에 한다."""
    path = pdf_path or doc.get("source_file")
    if not path or not Path(path).exists():
        return {"confident": False, "reason": "PDF 없음"}
    meta = dict(doc.get("meta") or {})
    meta.setdefault("doi", doc.get("paper_id"))
    probe = " ".join(p.get("text") or ""
                     for s in (doc.get("body_text") or [])
                     for p in (s.get("paragraphs") or []))
    bm = analyze(path, meta, body_probe=probe)
    return filter_document(doc, bm)
