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
    r'^\s*(?:https?://)?(?:dx\.)?(?:doi\.org/|doi\s*:\s*)\s*(10\.\d{4,9}/\S+?)\s*[.,;]?\s*$',
    re.I)

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
    r'appendix|erratum|correction)\b', re.I)

AUTHORLINE_RE = re.compile(
    r'^[A-Z][A-Za-z\'`’.\- ]+,?\s*(?:M\.?D\.?|Ph\.?D\.?|MSc|MBBS|MPH|iD\b)',
    re.I)


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
    진짜 표식은 위아래로 빈 줄만큼 떨어져 있으므로 **세로 간격**으로 가른다.
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
            gap_up = ln["y0"] - g[k - 1]["y1"] if k else 99.0
            gap_dn = g[k + 1]["y0"] - ln["y1"] if k + 1 < len(g) else 99.0
            if gap_up >= h * 0.9 and gap_dn >= h * 0.9:
                iso.add(ln["i"])
    out = {}
    for ln in lines:
        d = _is_doi_line(ln)
        if d and ln["i"] in iso:
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

    def to_dict(self) -> dict:
        return dict(index=self.index, start=self.start, end=self.end,
                    doi=self.doi, title=self.title, evidence=list(self.evidence))


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
    def locate(self, text: str, probes: int = 12) -> list[int]:
        """텍스트를 PDF 에서 찾아 각 조각이 놓인 구간 번호들을 돌려준다."""
        q = _norm(text)
        if len(q) < 24 or not self._stream:
            return []
        step = max(24, len(q) // max(1, probes))
        hits: list[int] = []
        i = 0
        while i + 24 <= len(q) and len(hits) < probes * 2:
            probe = q[i:i + 40]
            if len(probe) >= 24:
                p = self._stream.find(probe)
                if p >= 0:
                    hits.append(self._line_seg[self._pos2line[p]])
            i += step
        return hits

    def owner(self, text: str) -> tuple[str, int, int]:
        """('own'|'other'|'unknown', 내 구간 표 수, 이웃 구간 표 수)

        **이웃 것이라고 말하려면 내 구간에서 단 한 조각도 나오지 않아야 한다.**
        비율로 판정하면 GROBID 가 경계를 넘겨 이어붙인 문단(내 문장 + 이웃 문장)
        에서 내 문장까지 함께 사라진다. 오염 한 문장을 남기는 편이 낫다.
        """
        if not self.confident or self.own is None:
            return ("unknown", 0, 0)
        hits = [h for h in self.locate(text) if h >= 0]     # -1 = 별지, 판정 보류
        if not hits:
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

    # 2) 서두를 닻으로 제목 런을 거슬러 올라가 절단점을 만든다
    cuts: dict[int, dict] = {}
    for oi in openers:
        j = oi - 1
        head_doi = None
        # 서두 바로 앞의 단독 DOI 줄(BJD 유형)은 이 편의 머리다
        while j >= 0 and (lines[j]["i"] in run or not lines[j]["text"].strip()):
            j -= 1
        if j >= 0 and j in doi_lines:
            head_doi = doi_lines[j]
            j -= 1
        title_lines: list[dict] = []
        guard = 0
        while j >= 0 and guard < 14:
            ln = lines[j]
            if ln["i"] in run:
                j -= 1
                guard += 1
                continue
            if not _titleish(ln, body_size, body_font):
                break
            # 제목 런은 같은 페이지·같은 단 안에서 이어져야 한다
            if title_lines and (ln["page"] != title_lines[-1]["page"]
                                or ln["col"] != title_lines[-1]["col"]):
                break
            title_lines.append(ln)
            j -= 1
            guard += 1
        title_lines.reverse()
        # 저자 블록만 잡힌 경우(제목 없음)는 제목으로 인정하지 않는다
        while title_lines and AUTHORLINE_RE.match(title_lines[-1]["text"].strip()):
            title_lines.pop()

        if title_lines:
            start = title_lines[0]["i"]
            ev = ["제목런+서두"]
        elif head_doi:
            start = oi
            ev = ["머리DOI+서두"]
        else:
            # 앞 편의 꼬리 DOI 가 바로 앞에 있으면 그것으로 경계를 인정한다
            prev_doi = [k for k in doi_lines if k < oi and oi - k <= 4]
            if prev_doi:
                start = oi
                ev = ["앞편 꼬리DOI+서두"]
            else:
                continue                       # 근거 부족 → 절단점으로 쓰지 않는다
        title = " ".join(l["text"].strip() for l in title_lines).strip()
        prev = cuts.get(start)
        if prev is None or (not prev["title"] and title):
            cuts[start] = dict(title=title, doi=head_doi, ev=ev, opener=oi)

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

    # 4) 꼬리 DOI 로 이름표 보강(Elsevier/JEADV/EJD 유형)
    for seg in segments:
        if seg.doi:
            continue
        tail = [d for i, d in doi_lines.items() if seg.start <= i < seg.end]
        if len(tail) == 1:
            seg.doi = tail[0]
            seg.evidence.append("꼬리DOI")
        elif len(tail) > 1:
            seg.doi = tail[-1]
            seg.evidence.append(f"꼬리DOI(후보 {len(tail)})")

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

    if len(segments) < 2:
        bm.reason = "구간 1개 — 합본 지면이 아님"
        return bm

    # 6) 이 논문의 구간 지목
    by_doi = [s.index for s in segments
              if s.doi and want_doi and s.doi.lower().rstrip(".") == want_doi]
    by_title = []
    if want_title and len(want_title) >= 20:
        for s in segments:
            t = _norm_title(s.title)
            if not t:
                continue
            if t == want_title or (len(t) >= 20 and (t in want_title or want_title in t)):
                by_title.append(s.index)

    meta_pick = None
    how = ""
    if len(by_doi) == 1:
        meta_pick, how = by_doi[0], "DOI 일치"
    elif len(by_title) == 1:
        meta_pick, how = by_title[0], "제목 일치"

    body_pick = None
    if body_probe:
        hits = [h for h in bm.locate(body_probe, probes=40) if h >= 0]
        if hits:
            from collections import Counter
            c = Counter(hits)
            top, n = c.most_common(1)[0]
            if n >= len(hits) * 0.55:
                body_pick = top

    if meta_pick is None:
        bm.reason = "이 논문의 구간을 DOI·제목 어느 쪽으로도 지목하지 못함 → 자르지 않음"
        return bm
    if body_pick is not None and body_pick != meta_pick:
        bm.identity_conflict = dict(
            meta_segment=meta_pick, body_segment=body_pick,
            meta_doi=segments[meta_pick].doi, meta_title=segments[meta_pick].title,
            body_doi=segments[body_pick].doi, body_title=segments[body_pick].title)
        bm.reason = ("메타가 가리키는 구간과 정본 본문이 놓인 구간이 다름 "
                     "→ paper_id 오배정 의심, 자르지 않음")
        return bm

    bm.own = meta_pick
    bm.confident = True
    bm.reason = f"{how} (구간 {len(segments)}개 중 {meta_pick}번)"
    return bm


# ── 정본 걸러내기 ────────────────────────────────────────────────────
def filter_document(doc: dict, bmap: BoundaryMap, *, apply: bool = True) -> dict:
    """정본 dict 에서 이웃 논문 소속 항목을 제거하고 보고서를 돌려준다.

    doc 은 제자리에서 수정된다(apply=True 일 때). 확신이 없으면 아무것도 건드리지
    않는다. 판정이 'unknown' 인 항목도 남긴다 — 소실이 오염보다 나쁘다.
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
    if apply:
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
