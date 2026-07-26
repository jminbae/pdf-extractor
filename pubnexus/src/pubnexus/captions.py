"""4.7단계 — 그림·표 캡션을 **PDF 좌표·글꼴 근거**로 뽑고 경계를 확정한다.

figtab.py 는 PyMuPDF 의 블록 텍스트(문자열)만 보고 캡션을 찾는다. 그래서
  · 캡션이 어디서 **끝나는지** 모른다 → 뒤따르는 본문을 통째로 삼킨다
  · 캡션이 단 경계에서 두 블록으로 쪼개지면 앞토막만 잡는다
  · 같은 지면에 실린 **이웃 논문**의 캡션을 구분할 수 없다
세 가지를 못 고친다. 이 모듈은 글꼴 크기·굵기·bbox·읽기순서를 근거로 세 가지를
모두 다룬다.

── 캡션 판별 근거(실측) ────────────────────────────────────────────────
조사한 조판은 모두 **캡션 글자 크기가 본문보다 작다**. 라벨은 굵은 글꼴이다.

  Springer Lasers Med Sci (10.1007/s10103-019-02890-6) 3쪽
    본문  'Although the mechanism by which TSL induces hair re-'  size 10.0
    캡션  'Fig. 2'  size 8.5 font GwhlysJwmxhxAdvTT577c760(볼드)
          + ' Alopecia totalis treated'  size 8.5 (일반)
  Elsevier JAAD (10.1016/j.jaad.2020.09.088) 3쪽
    본문 size 10.0 · 캡션 blk65 'Fig 1. A, WTP cure: …' size 9.0 볼드 라벨
  Wiley JEADV (10.1111/jdv.16524) 2쪽
    본문 size 9.0 · 캡션 blk18 'Figure 1 Seasonality of chronic skin diseases. (a) …' size 8.0
  JAMA Pediatrics (10.1001/jamapediatrics.2017.5203) 1·2쪽
    본문 size 8.5 · 캡션 'Figure 1. Well-defined hyperkeratotic desquamation …' size 7.0

그래서 캡션의 **끝**은 "라벨 줄과 같은 크기 등급(±0.4pt)이 유지되는 동안"이다.
크기가 본문 등급으로 돌아오면 그 줄부터는 캡션이 아니다. 이 한 가지 규칙이
'캡션이 본문 400자를 삼킴'(감사 21편)의 직접 원인을 없앤다.

── 단 경계를 넘는 캡션 ─────────────────────────────────────────────────
Springer 는 두 단을 가로지르는 캡션을 두 블록으로 나눠 낸다(실측
10.1007/s10103-018-2559-9 4쪽: blk58 (51,416)-(289,455) + blk59 (306,416)-(544,455)).
같은 크기 등급이고 **y 띠가 겹치며** 오른쪽에 붙어 있으면 이어짐으로 본다.
같은 단 안에서는 줄간 1.8배 이내로 바로 아래에 붙어 있을 때 이어짐으로 본다.

── 이웃 논문 오염 차단(가장 중요) ──────────────────────────────────────
합본 지면(레터 여러 편이 한 PDF)에서는 이웃 논문 캡션이 같은 번호를 주장한다.
figtab.py 가 이것을 못 막아 파이프라인에 연결되지 못했다(실증:
10.1016/j.jaad.2016.05.022 에 이웃 레터 10.1016/j.jaad.2016.05.014 의
'Table II. Final diagnoses made by the consulting …' 이 들어왔다).

두 겹의 문지기를 둔다.

  (1) **DOI 도장** — Elsevier 레터는 각 편 끝에 'http://dx.doi.org/<그 편의 DOI>'
      한 줄을 찍는다. 실측 10.1016/j.jaad.2016.05.022 PDF:
        1쪽 우단 y540  'http://dx.doi.org/10.1016/j.jaad.2016.05.014'  ← 이웃 편 끝
        1쪽 우단 y602  'To the Editor: Vasculitis is histologically defined by 2'  ← 이 편 시작
        3쪽 우단 y331  'http://dx.doi.org/10.1016/j.jaad.2016.05.022'  ← 이 편 끝
      이 도장으로 읽기순서 스트림을 토막 내면 이웃 편의 Table I·II(1쪽 좌단
      y76·y373)는 이 편 구간 **밖**이 되어 후보에서 아예 빠진다.
  (2) **본문 정박** — 정본 body_text 의 6-gram 이 실제로 찍힌 줄만 '이 논문 줄'로
      본다. 그 줄들의 최소~최대 읽기순서가 정박 구간이다. 도장이 없는 조판은
      이것만으로 판정한다.

둘 다 실패하면(정박 줄이 5줄 미만) **아무것도 뽑지 않는다**. 그림 몇 개보다
오염이 해롭다는 것이 이 모듈의 기본 방침이다.

── 이 모듈이 하는 일 ───────────────────────────────────────────────────
  extract_captions(pdf)          좌표·글꼴 근거 캡션 목록(경계 확정)
  owned_captions(pdf, doc)       위 목록에서 이 논문 것만
  normalize_label / dedupe_label 라벨 표기 통일·중복 접두 제거
  strip_captions_from_body(doc)  본문에 새어 든 캡션 제거(끊긴 문장 재봉합)
  repair_document(doc, pdf)      위를 묶어 정본 한 편을 고친다
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ── 상수 ─────────────────────────────────────────────────────────────
SIZE_TOL = 0.45           # 같은 '크기 등급' 으로 볼 허용 오차(pt)
BODY_MIN_CHARS = 45       # 본문 크기 추정에 쓸 줄의 최소 길이
MIN_DESC_CHARS = 20       # 라벨 뒤 설명이 이보다 짧으면 캡션으로 안 본다
MAX_CAPTION_CHARS = 2000  # 안전 상한(실측 정상 캡션 최장 1,531자)
LINE_GAP_FACTOR = 1.9     # 같은 단 안 이어짐 허용 줄간(줄높이 배수)
BAND_OVERLAP = 0.60       # 옆 단 이어짐: 두 토막의 y 띠가 이만큼 겹쳐야 한다
COL_GAP = 36              # 단 구분 최소 가로 간격(pt)
ANCHOR_NGRAM = 6          # 본문 정박에 쓰는 낱말 n-gram 크기
ANCHOR_MIN_WORDS = 8      # 정박 판정 대상 줄의 최소 낱말 수
ANCHOR_MIN_LINES = 5      # 이보다 정박 줄이 적으면 소유 판정 포기
STRIP_MIN_CHARS = 60      # 본문에서 캡션을 지울 최소 일치 길이(정규화 기준)

_FIGW = r"(?:FIGURES?|FIGS?|Figures?|Figs?|Fig|FIG)"
_TABW = r"(?:TABLES?|Tables?|TABLE|Table|Tbl)"
_NUM = r"(?:[0-9]{1,2}|[IVX]{1,5}|[A-Z]{1,2}[0-9]{1,2})"
_SUPW = (r"(?:Supplementary|Supplemental|Supporting|Online|Appendix|"
         r"SUPPLEMENTARY|SUPPLEMENTAL)")

# 줄 **맨 앞**에서만 문다. '(Fig 2)' 같은 본문 중 참조는 구조적으로 탈락한다.
LABEL_RE = re.compile(
    r"^(?P<sup>" + _SUPW + r"\s+|e(?=Table|Figure))?"
    r"(?P<kind>" + _FIGW + r"|" + _TABW + r")"
    r"(?P<dot>\s*\.)?\s*"
    r"(?:(?P<num>" + _NUM + r")(?P<panel>[a-h](?![a-zA-Z]))?"
    r"(?P<sep>\s*[.:|—–‒]\s*|\s+)"
    r"|(?P<nonum>[.:]\s+)(?=[A-Z]))"
    r"(?P<rest>.*)", re.S)

# 설명 첫 낱말이 이것이면 캡션이 아니라 본문('Table 1 shows …')
_STOPWORD_RE = re.compile(
    r"^(?:shows?|showed|shown|displays?|demonstrat\w*|presents?|lists?|summar\w*|"
    r"depict\w*|illustrat\w*|reports?|describ\w*|provid\w*|indicat\w*|contain\w*|"
    r"gives?|and|or|of|in|on|to|for|the|an|were|was|is|are|also|see|from|but|"
    r"which|that|this|these|those|continue[sd]?|cont|above|below)\b", re.I)
_PANEL_HEAD_RE = re.compile(r"^([a-h])\s+([A-Z(])")
# 그림 위 패널문자가 캡션 앞에 새어 든 경우: 'b c Fig. 6 | …'
_LEAD_JUNK_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[.)])?\s+){1,6}(?=(?:" + _FIGW + r"|" + _TABW + r")\b)")
# 자간 조판 'F I G U R E 1' · 'TA B L E 4'
_SPACED_RE = re.compile(
    r"^(?P<pre>\s*)"
    r"(?P<kw>[Tt]\s*[Aa]\s*[Bb]\s*[Ll]\s*[Ee]|[Ff]\s*[Ii]\s*[Gg](?:\s*[Uu]\s*[Rr]\s*[Ee])?)"
    r"(?=\s*\.?\s*[0-9IVX])")
_WS_RE = re.compile(r"[ \t     ]+")

# 논문 경계 도장. 줄 대부분이 그 URL 이어야 한다(참고문헌 꼬리 DOI 는 탈락).
_STAMP_RE = re.compile(
    r"(?:https?://)?(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s\"'<>)\],;]+)", re.I)
_STAMP_ALT_RE = re.compile(
    r"^\s*(?:DOI|doi)\s*[:：]?\s*(10\.\d{4,9}/[^\s\"'<>)\],;]+)\s*$")

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
          "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
          "XIV": 14, "XV": 15}

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏⁠﻿]")
_CTRL_DIGITS_RE = re.compile(r"(?<=\d)[\x00-\x08\x0b\x0c\x0e-\x1f](?=\d)")
_SOFT_HYPHEN_RE = re.compile(r"­")
_ALNUM_RE = re.compile(r"[a-z0-9]")


# ── 줄 모델 ──────────────────────────────────────────────────────────
@dataclass
class Line:
    page: int
    block: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float           # 문자수 가중 최빈 크기
    lead_size: float      # 첫 span 크기(라벨 크기)
    lead_font: str
    bold: bool            # 첫 span 이 굵은 글꼴인가
    bbox_block: tuple[float, float, float, float] = (0, 0, 0, 0)
    first_of_block: bool = False
    page_width: float = 595.0
    col: int = 0
    order: int = 0

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class Caption:
    kind: str             # 'fig' | 'tab'
    label: str            # 원문 라벨어('Fig.', 'FIGURE', 'Table')
    raw: str | None       # 원문 번호 토큰('II', '1', 'S2')
    num: int | None       # 정수 번호(로마자 정규화). 보조자료는 None
    supp: bool
    head: str             # 'Fig. 2' — 정규화 전 표기
    desc: str             # 라벨을 뗀 설명
    text: str             # 완성 캡션
    page: int             # 0-base
    bbox: tuple[float, float, float, float]
    start: int            # 읽기순서 인덱스(시작)
    end: int              # 읽기순서 인덱스(끝, 포함)
    size: float
    evidence: str = ""    # 경계를 그렇게 잡은 근거(사람이 읽는 문장)

    def key(self) -> str:
        n = self.raw if self.supp else (str(self.num) if self.num else "?")
        return f"{self.kind}:{n}"


# ── 문자 유틸 ────────────────────────────────────────────────────────
def _flatten(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\n", " ")).strip()


def _unspace_label(s: str) -> str:
    m = _SPACED_RE.match(s)
    return m.group("pre") + re.sub(r"\s+", "", m.group("kw")) + s[m.end():] if m else s


def _strip_lead_junk(s: str) -> str:
    m = _LEAD_JUNK_RE.match(s)
    return s[m.end():] if m else s


def prep(s: str) -> str:
    """캡션 판별 전 표준 전처리(자간 라벨 붙이기 · 패널문자 부스러기 제거)."""
    return _strip_lead_junk(_unspace_label(_flatten(s)))


def roman_to_int(tok: str) -> int | None:
    return _ROMAN.get((tok or "").upper())


def caption_number(tok: str | None) -> int | None:
    if tok is None:
        return None
    if tok.isdigit():
        return int(tok)
    return roman_to_int(tok)


def is_figure_word(kind: str) -> bool:
    return (kind or "").lower().startswith("fig")


def _norm_key(s: str) -> str:
    """비교용 정규화 — 소문자 영숫자만 남긴다."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def clean_text(text: str, *, typeset: bool = False) -> str:
    """PDF 원문을 정본 본문과 같은 문자 규약으로 맞춘다(figtab.clean_caption 과 동일)."""
    from . import textfix

    s = _CTRL_DIGITS_RE.sub("·", text or "")
    s = _SOFT_HYPHEN_RE.sub("", _CTRL_RE.sub("", s))
    s = textfix.clean_paragraph(s)
    return textfix.fix_encoding(s, typeset=typeset)


# ── 라벨 정규화 ──────────────────────────────────────────────────────
def normalize_label(kind: str, raw: str | None, *, style: str = "canonical",
                    label: str = "") -> str:
    """라벨 표기를 통일한다.

    'Fig. 1.' / 'FIGURE 1' / 'Figure 1:' / 'F I G U R E 1' → 'Figure 1'
    'TABLE II' / 'Table 2.' → 'Table 2'
    style='pdf' 면 원문 라벨어를 살리고 구두점만 통일한다.
    """
    n = caption_number(raw)
    shown = str(n) if n is not None else (raw or "")
    if style == "pdf" and label:
        word = label.rstrip(".")
    else:
        word = "Figure" if is_figure_word(kind) else "Table"
    return f"{word} {shown}".strip()


def dedupe_label(caption: str) -> tuple[str, int]:
    """캡션 앞에 같은 라벨이 두 번 이상 찍힌 것을 지운다.

    'Fig 1. Fig 1. Repigmentation …' → ('Fig 1. Repigmentation …', 1)
    'Fig. 2 aFig. 3 a. …'            → ('Fig. 2 a. …', 1)   ← 라벨 오염(감사 유형 4)
    돌려주는 둘째 값은 지운 횟수.
    """
    s = prep(caption)
    removed = 0
    first = LABEL_RE.match(s)
    if not first:
        return s, 0
    kind = "fig" if is_figure_word(first.group("kind")) else "tab"
    head_end = first.start("rest")
    head = s[:head_end]
    rest = s[head_end:]
    while True:
        m = LABEL_RE.match(rest)
        if not m:
            # 'Fig. 2 aFig. 3 a.' — 라벨이 낱말에 붙어 나온 오염
            m2 = re.match(r"^\s*[a-h]?(" + _FIGW + r"|" + _TABW + r")\s*\.?\s*"
                          + _NUM + r"[a-h]?\s*[.:]?\s*", rest)
            if m2 and (("fig" if is_figure_word(m2.group(1)) else "tab") == kind):
                rest = rest[m2.end():]
                removed += 1
                continue
            break
        if ("fig" if is_figure_word(m.group("kind")) else "tab") != kind:
            break
        rest = rest[m.start("rest"):]
        removed += 1
    out = (head + rest).strip() if removed else s
    return _flatten(out), removed


# ── 캡션 파싱 ────────────────────────────────────────────────────────
def parse_caption(text: str, *, min_desc: int = MIN_DESC_CHARS) -> dict | None:
    """문자열 머리에서 캡션을 읽는다. 캡션이 아니면 None."""
    t = prep(text)
    m = LABEL_RE.match(t)
    if not m:
        return None
    desc = (m.group("rest") or "").strip()
    pm = _PANEL_HEAD_RE.match(desc)
    if pm and not (m.group("sep") or " ").strip():
        desc = desc[pm.start(2):]
    if len(desc) < min_desc:
        return None
    first = desc.split()[0]
    if _STOPWORD_RE.match(first) and not first[:1].isupper():
        return None
    raw = m.group("num")
    supp = bool(m.group("sup")) or (raw is not None and caption_number(raw) is None)
    label = m.group("kind")
    num = caption_number(raw)
    if raw is None:
        num = 1
    head = label + ("." if m.group("dot") else "")
    if raw:
        head += " " + raw + (m.group("panel") or "")
    return {"kind": "fig" if is_figure_word(label) else "tab",
            "label": label, "raw": raw, "num": num, "supp": supp,
            "head": head, "desc": desc}


# ── PDF → 줄 ─────────────────────────────────────────────────────────
def _page_lines(page, pno: int) -> list[Line]:
    from collections import Counter

    out: list[Line] = []
    d = page.get_text("dict")
    for bi, blk in enumerate(d.get("blocks", ())):
        if blk.get("type") != 0:
            continue
        bb = tuple(float(v) for v in (blk.get("bbox") or (0, 0, 0, 0)))
        first = True
        for ln in blk.get("lines", ()):
            spans = [s for s in ln.get("spans", ()) if s.get("text")]
            if not spans:
                continue
            txt = "".join(s["text"] for s in spans)
            if not txt.strip():
                continue
            sizes: Counter = Counter()
            for s in spans:
                sizes[round(float(s["size"]), 1)] += max(1, len(s["text"]))
            b = ln["bbox"]
            lead = spans[0]
            out.append(Line(
                page=pno, block=bi, text=txt,
                x0=b[0], y0=b[1], x1=b[2], y1=b[3],
                size=sizes.most_common(1)[0][0],
                lead_size=round(float(lead["size"]), 1),
                lead_font=lead.get("font", ""),
                bold=bool(int(lead.get("flags", 0)) & 2 ** 4)
                or "bold" in lead.get("font", "").lower()
                or "semibold" in lead.get("font", "").lower(),
                bbox_block=bb, first_of_block=first,
                page_width=float(page.rect.width or 595.0)))
            first = False
    return out


def _assign_columns(lines: list[Line], page_width: float) -> None:
    """단을 나눈다. 폭이 넓은 줄들의 왼쪽 끝을 모아 군집한다."""
    wide = [l for l in lines if (l.x1 - l.x0) >= 0.30 * page_width]
    if not wide:
        for l in lines:
            l.col = 0
        return
    xs = sorted(round(l.x0) for l in wide)
    edges = [xs[0]]
    for x in xs[1:]:
        if x - edges[-1] > COL_GAP:
            edges.append(x)
    # 군집 대표값을 실제 왼쪽 끝으로 다듬는다
    for l in lines:
        col = 0
        for i, e in enumerate(edges):
            if l.x0 >= e - COL_GAP / 2:
                col = i
        l.col = col


def document_lines(pdf_path: str | Path) -> list[Line]:
    """PDF 전체를 읽기순서(쪽 → 단 → y)로 정렬된 줄 목록으로."""
    import fitz

    out: list[Line] = []
    with fitz.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc):
            pls = _page_lines(page, pno)
            _assign_columns(pls, page.rect.width or 595.0)
            pls.sort(key=lambda l: (l.col, round(l.y0, 1), l.x0))
            out.extend(pls)
    for i, l in enumerate(out):
        l.order = i
    return out


def body_size(lines: Iterable[Line]) -> float:
    """본문 글자 크기 — 긴 줄(≥45자)의 문자수 가중 최빈 크기."""
    from collections import Counter

    c: Counter = Counter()
    for l in lines:
        t = l.text.strip()
        if len(t) >= BODY_MIN_CHARS:
            c[l.size] += len(t)
    if not c:
        c = Counter()
        for l in lines:
            c[l.size] += len(l.text)
    return c.most_common(1)[0][0] if c else 10.0


# ── 캡션 경계 ────────────────────────────────────────────────────────
def _same_size(a: float, b: float) -> bool:
    return abs(a - b) <= SIZE_TOL


def _band_overlap(a: Line, b: Line) -> float:
    lo, hi = max(a.y0, b.y0), min(a.y1, b.y1)
    if hi <= lo:
        return 0.0
    return (hi - lo) / max(1.0, min(a.y1 - a.y0, b.y1 - b.y0))


def _is_seed(line: Line, body: float) -> dict | None:
    """이 줄이 캡션의 시작인가. 아니면 None."""
    got = parse_caption(line.text)
    if not got:
        return None
    # 본문 크기와 같고 굵지도 않으면 본문 문장일 수 있다 → 라벨 뒤 구두점을 요구
    if _same_size(line.size, body) and not line.bold:
        t = prep(line.text)
        if not re.match(r"^(?:" + _FIGW + r"|" + _TABW + r")\s*\.?\s*"
                        + _NUM + r"[a-h]?\s*[.:|]", t):
            return None
    return got


def _extend(lines: list[Line], i: int, body: float,
            page_lo: int, page_hi: int) -> tuple[int, list[int], str]:
    """i 번째 줄에서 시작한 캡션이 어디서 끝나는지 판정한다.

    두 가지 이어짐만 허용한다.
      (a) **같은 단 바로 아래** — 줄간이 줄높이의 1.9배 이내
      (b) **옆 단 같은 y 띠** — Springer 가 두 단을 가로지르는 캡션을 두 블록으로
          쪼개 내보내는 조판(실측 10.1007/s10103-018-2559-9 4쪽
          blk58 (51,416)-(289,455) + blk59 (306,416)-(544,455)).
          캡션 크기가 본문 크기와 같을 때는 (b) 를 쓰지 않는다 — 본문을 물 위험.

    캡션 크기가 본문 크기와 같으면 **같은 PyMuPDF 블록 안**으로만 이어붙인다.
    돌려주는 값: (마지막 줄 인덱스, 채택한 줄 인덱스들, 종료 근거)
    """
    seed = lines[i]
    size = seed.size
    body_like = _same_size(size, body) and not seed.bold
    taken = [i]
    cur = seed
    band_y0, band_y1 = seed.bbox_block[1], seed.bbox_block[3]
    total = len(seed.text)
    reason = "블록 끝"
    jumped: set[int] = {seed.block}
    while True:
        nxt_idx = None
        # (a) 같은 단 바로 아래
        j = taken[-1] + 1
        if j < page_hi:
            n = lines[j]
            # 줄상자는 서로 조금 겹치기도 한다(촘촘한 행간) → 아래쪽이기만 하면 된다
            below = n.y0 > cur.y0 + 0.3 * cur.height
            near = (n.y0 - cur.y1) <= LINE_GAP_FACTOR * cur.height
            cross_ok = (n.block == cur.block
                        or ((n.bbox_block[2] - n.bbox_block[0])
                            >= 0.25 * n.page_width
                            and abs(n.bbox_block[0] - cur.bbox_block[0]) <= 12))
            if (n.col == cur.col and _same_size(n.size, size)
                    and not _is_seed(n, body)
                    and (not body_like or n.block == cur.block)
                    and cross_ok and below and near):
                nxt_idx = j
            elif not _same_size(n.size, size):
                reason = (f"글자 크기 {n.size} ≠ 캡션 {size}"
                          + (" (본문 등급)" if _same_size(n.size, body) else ""))
            elif _is_seed(n, body):
                reason = "다음 캡션 라벨 시작"
            elif body_like and n.block != cur.block:
                reason = "본문과 같은 크기 → 블록 밖으로 안 나감"
            elif not cross_ok:
                reason = "다음 블록이 산문 폭·왼쪽 정렬이 아님(표 셀·라벨)"
            elif n.col != cur.col:
                reason = "단 끝"
            else:
                reason = f"줄간 {n.y0 - cur.y1:.0f}pt 초과"
        else:
            reason = "쪽 끝"
        # (b) 옆 단 같은 y 띠 — **블록 통째로** 판정한다(줄 하나씩 주우면 본문을 문다)
        if nxt_idx is None and not body_like:
            best = None
            for k in range(taken[-1] + 1, page_hi):
                c = lines[k]
                if not c.first_of_block or c.block in jumped:
                    continue
                if c.col <= cur.col or not _same_size(c.size, size):
                    continue
                if _is_seed(c, body) or c.x0 < cur.x1 - 1:
                    continue
                bx0, by0, bx1, by1 = c.bbox_block
                # 산문 폭이어야 한다 — 표 셀·축 라벨 같은 좁은 덩어리는 제외
                if (bx1 - bx0) < 0.25 * c.page_width:
                    continue
                # 캡션이 단을 가로지를 때 두 토막은 **같은 y 에서 시작**한다
                if abs(by0 - band_y0) > 1.6 * cur.height:
                    continue
                band = max(0.0, min(band_y1, by1) - max(band_y0, by0))
                if band < BAND_OVERLAP * max(band_y1 - band_y0, by1 - by0):
                    continue
                if best is None or (c.col, c.y0) < (lines[best].col, lines[best].y0):
                    best = k
            if best is not None:
                nxt_idx = best
                jumped.add(lines[best].block)
                reason = "옆 단 이어짐"
        if nxt_idx is None:
            break
        n = lines[nxt_idx]
        taken.append(nxt_idx)
        band_y0, band_y1 = min(band_y0, n.y0), max(band_y1, n.y1)
        cur = n
        total += len(n.text)
        if total > MAX_CAPTION_CHARS:
            reason = f"상한 {MAX_CAPTION_CHARS}자"
            break
    return taken[-1], taken, reason


def extract_captions(pdf_path: str | Path, *, lines: list[Line] | None = None,
                     typeset: bool = False) -> list[Caption]:
    """PDF 의 모든 그림·표 캡션을 좌표·글꼴 근거로 뽑는다(소유 판정 전)."""
    lines = lines if lines is not None else document_lines(pdf_path)
    body = body_size(lines)
    # 쪽 경계(읽기순서 슬라이스) — 이어짐 탐색은 같은 쪽 안에서만 한다
    bounds: dict[int, tuple[int, int]] = {}
    for i, l in enumerate(lines):
        lo, hi = bounds.get(l.page, (i, i))
        bounds[l.page] = (min(lo, i), i + 1)
    out: list[Caption] = []
    used: set[int] = set()
    for i, l in enumerate(lines):
        if i in used:
            continue
        got = _is_seed(l, body)
        if not got:
            continue
        p_lo, p_hi = bounds[l.page]
        last, taken, reason = _extend(lines, i, body, p_lo, p_hi)
        used.update(taken)
        parts = [lines[k].text for k in taken]
        raw_text = _flatten(" ".join(parts))
        parsed = parse_caption(raw_text) or got
        desc = clean_text(parsed["desc"], typeset=typeset).strip()
        if len(desc) > MAX_CAPTION_CHARS:
            cut = desc.rfind(". ", 0, MAX_CAPTION_CHARS)
            desc = (desc[:cut + 1] if cut > MAX_CAPTION_CHARS // 2
                    else desc[:MAX_CAPTION_CHARS]).strip()
        head = clean_text(parsed["head"], typeset=typeset).strip()
        text, _ = dedupe_label(f"{head}. {desc}" if desc else head)
        span = [lines[k] for k in taken]
        out.append(Caption(
            kind=parsed["kind"], label=parsed["label"], raw=parsed["raw"],
            num=parsed["num"], supp=parsed["supp"], head=head, desc=desc,
            text=text, page=l.page,
            bbox=(min(s.x0 for s in span), min(s.y0 for s in span),
                  max(s.x1 for s in span), max(s.y1 for s in span)),
            start=i, end=last, size=l.size,
            evidence=(f"{l.page+1}쪽 ({l.x0:.0f},{l.y0:.0f}) 크기 {l.size}"
                      f"{'/볼드' if l.bold else ''} 본문 {body} · "
                      f"{len(taken)}줄 · 종료근거: {reason}")))
        consumed = last
    return out


# ── 소유 판정 ────────────────────────────────────────────────────────
def _doc_ngrams(doc: dict, n: int = ANCHOR_NGRAM) -> set[str]:
    words: list[str] = []
    for sec in (doc.get("body_text") or []):
        for p in (sec.get("paragraphs") or []):
            words.extend(re.findall(r"[a-z0-9]+", (p.get("text") or "").lower()))
    for key in ("abstract",):
        words.extend(re.findall(r"[a-z0-9]+", (doc.get(key) or "").lower()))
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def _anchored(lines: list[Line], grams: set[str], n: int = ANCHOR_NGRAM) -> list[int]:
    hits = []
    for i, l in enumerate(lines):
        w = re.findall(r"[a-z0-9]+", l.text.lower())
        if len(w) < ANCHOR_MIN_WORDS:
            continue
        if any(" ".join(w[k:k + n]) in grams for k in range(len(w) - n + 1)):
            hits.append(i)
    return hits


def _stamps(lines: list[Line]) -> list[tuple[int, str]]:
    """논문 경계 도장 — (읽기순서 인덱스, DOI)."""
    out = []
    for i, l in enumerate(lines):
        t = l.text.strip()
        if len(t) > 130:
            continue
        m = _STAMP_RE.search(t)
        if m and len(m.group(0)) >= 0.5 * len(t):
            out.append((i, m.group(1).rstrip(".").lower()))
            continue
        m = _STAMP_ALT_RE.match(t)
        if m:
            out.append((i, m.group(1).rstrip(".").lower()))
    return out


def article_span(lines: list[Line], doc: dict) -> tuple[int, int, str]:
    """이 논문이 차지하는 읽기순서 구간 [lo, hi] 와 그 근거.

    판정 불가면 (-1, -1, 사유) 를 돌려준다 — 그 경우 캡션을 하나도 쓰지 않는다.
    """
    grams = _doc_ngrams(doc)
    hits = _anchored(lines, grams)
    if len(hits) < ANCHOR_MIN_LINES:
        return -1, -1, f"본문 정박 줄 {len(hits)}개 < {ANCHOR_MIN_LINES} — 소유 판정 포기"
    lo, hi = hits[0], hits[-1]

    stamps = _stamps(lines)
    distinct = {d for _, d in stamps}
    if len(distinct) < 2:
        # 도장이 한 종류뿐 = 이 PDF 는 한 편짜리다 → 지면 전체가 이 논문 것이다.
        # (실측 10.1001/jamapediatrics.2017.5203 은 1쪽 본문·2쪽 그림+참고문헌 구성이라
        #  정박 구간만 쓰면 2쪽 'Figure 2. After treatment with oral, low-dose
        #  isotretinoin …' 을 놓친다)
        return 0, len(lines) - 1, f"단일 편(DOI 도장 {len(distinct)}종) — 지면 전체"

    # 합본 지면: 도장 줄에서 스트림을 토막 내고 **정박 줄이 가장 많은 토막**을 고른다.
    # 도장이 편 머리에 찍히는 조판(Annals of Dermatology)과 편 끝에 찍히는 조판
    # (Elsevier 레터)이 섞여 있어 도장의 역할을 미리 가정하지 않는다.
    cuts = sorted({i for i, _ in stamps})
    segs: list[tuple[int, int]] = []
    a = 0
    for c in cuts:
        if c >= a:
            segs.append((a, c))
        a = c + 1
    segs.append((a, len(lines) - 1))
    counts = [sum(1 for h in hits if x <= h <= y) for x, y in segs]
    best = max(range(len(segs)), key=lambda k: counts[k])
    lo2, hi2 = segs[best]
    # 한 편 안에 DOI 줄이 더 있을 수 있다 → 정박이 충분한 이웃 토막은 이어 붙인다
    thr = max(ANCHOR_MIN_LINES, 0.35 * counts[best])
    k = best
    while k - 1 >= 0 and counts[k - 1] >= thr:
        k -= 1
        lo2 = segs[k][0]
    k = best
    while k + 1 < len(segs) and counts[k + 1] >= thr:
        k += 1
        hi2 = segs[k][1]
    return (lo2, hi2,
            f"합본 {len(distinct)}편 · 도장 {len(cuts)}줄로 {len(segs)}토막 · "
            f"정박 최다 토막 채택(정박 {counts[best]}/{len(hits)}줄, 읽기순서 {lo2}~{hi2})")


def owned_captions(pdf_path: str | Path, doc: dict, *,
                   lines: list[Line] | None = None,
                   typeset: bool = False) -> tuple[list[Caption], list[Caption], str]:
    """(이 논문 캡션, 이웃 논문으로 판정해 버린 캡션, 근거)."""
    lines = lines if lines is not None else document_lines(pdf_path)
    caps = extract_captions(pdf_path, lines=lines, typeset=typeset)
    lo, hi, why = article_span(lines, doc)
    if lo < 0:
        return [], caps, why
    mine = [c for c in caps if lo <= c.start <= hi]
    other = [c for c in caps if not (lo <= c.start <= hi)]
    return mine, other, why


# ── 번호별 사전 ──────────────────────────────────────────────────────
def caption_map(caps: Iterable[Caption], kind: str, *,
                supplementary: bool = False) -> dict[str, str]:
    """{'1': 'Fig 1. …'} — 같은 번호가 여럿이면 설명이 가장 긴 것."""
    out: dict[str, str] = {}
    for c in caps:
        if c.kind != kind or c.supp != supplementary:
            continue
        key = c.raw if supplementary else (None if c.num is None else str(c.num))
        if not key:
            continue
        if key not in out or len(c.text) > len(out[key]):
            out[key] = c.text
    return out


def ambiguous_numbers(caps: Iterable[Caption], kind: str) -> set[int]:
    """같은 번호를 서로 다른 캡션이 주장하는 번호(소유 판정 뒤에도 남은 경우)."""
    from rapidfuzz import fuzz

    seen: dict[int, list[str]] = {}
    for c in caps:
        if c.kind != kind or c.supp or c.num is None:
            continue
        seen.setdefault(c.num, []).append(c.desc)
    bad = set()
    for num, descs in seen.items():
        if len(descs) < 2:
            continue
        base = descs[0]
        if any(fuzz.partial_ratio(_norm_key(base), _norm_key(d)) < 70
               for d in descs[1:]):
            bad.add(num)
    return bad


# ── 본문에 새어 든 캡션 제거 ─────────────────────────────────────────
def _norm_with_map(s: str) -> tuple[str, list[int]]:
    buf, idx = [], []
    for i, ch in enumerate(s):
        c = ch.lower()
        if c.isalnum():
            buf.append(c)
            idx.append(i)
    return "".join(buf), idx


def _longest_prefix_at(hay: str, needle: str, min_len: int) -> tuple[int, int]:
    """hay 안에서 needle 의 가장 긴 앞토막이 나타나는 (시작, 길이). 없으면 (-1, 0)."""
    lo, hi = min_len, len(needle)
    best = (-1, 0)
    while lo <= hi:
        mid = (lo + hi) // 2
        pos = hay.find(needle[:mid])
        if pos >= 0:
            best = (pos, mid)
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def strip_captions_from_body(doc: dict, caps: Iterable[Caption], *,
                             min_chars: int = STRIP_MIN_CHARS) -> list[dict]:
    """본문 문단에 통째로 새어 든 캡션을 지우고 끊긴 문장을 다시 잇는다.

    감사 유형 3(캡션이 본문 문단 한가운데 삽입 → 문장이 두 동강)의 수리다.
    지운 뒤 좌우를 붙일 때, 왼쪽이 문장부호로 끝나지 않고 오른쪽이 소문자로
    시작하면 **공백 하나로** 잇는다 — 그것이 원문 문장이다.
    """
    reports: list[dict] = []
    targets = [(c, _norm_key(c.text), _norm_key(c.desc)) for c in caps]
    for sec in (doc.get("body_text") or []):
        for p in (sec.get("paragraphs") or []):
            text = p.get("text") or ""
            changed = True
            while changed:
                changed = False
                hay, idx = _norm_with_map(text)
                if not hay:
                    break
                for cap, nfull, ndesc in targets:
                    for needle in (nfull, ndesc):
                        if len(needle) < min_chars:
                            continue
                        pos, ln = _longest_prefix_at(hay, needle, min_chars)
                        if pos < 0 or ln < min_chars:
                            continue
                        # 문단 전체가 캡션이면 문단째로 비운다
                        a, b = idx[pos], idx[pos + ln - 1] + 1
                        left, right = text[:a], text[b:]
                        if not left.strip() and not right.strip():
                            new = ""
                        elif left.strip() and right.strip():
                            lend = left.rstrip()
                            rstart = right.lstrip()
                            joiner = " "
                            if lend and lend[-1] not in ".?!:;" and rstart[:1].islower():
                                joiner = " "       # 끊긴 문장 재봉합
                            new = lend + joiner + rstart
                        else:
                            new = (left + right).strip()
                        reports.append({
                            "paragraph": p.get("id"), "caption": cap.key(),
                            "removed": len(text) - len(new),
                            "where": ("가운데" if left.strip() and right.strip()
                                      else ("앞" if not left.strip() else "뒤")),
                            "sample": text[max(0, a - 40):a]
                                      + " ⟦" + text[a:a + 60] + "…⟧ "
                                      + text[b:b + 40]})
                        text = new
                        changed = True
                        break
                    if changed:
                        break
            p["text"] = text
    # 빈 문단·빈 절 정리
    for sec in (doc.get("body_text") or []):
        sec["paragraphs"] = [p for p in (sec.get("paragraphs") or [])
                             if (p.get("text") or "").strip()]
    doc["body_text"] = [s for s in (doc.get("body_text") or []) if s.get("paragraphs")]
    return reports


# ── 정본 한 편 수리 ──────────────────────────────────────────────────
def _empty(item: dict, limit: int = 15) -> bool:
    return len((item.get("caption") or "").strip()) < limit


def repair_document(doc: dict, pdf_path: str | Path, *,
                    fill_missing: bool = True,
                    strip_body: bool = True,
                    typeset: bool | None = None) -> dict:
    """정본 한 편의 캡션을 PDF 근거로 고친다. doc 은 제자리에서 바뀐다.

    · 빈 캡션 채우기          figures/tables 의 caption='' 을 PDF 캡션으로
    · 본문 삼킴 잘라내기      정본 캡션이 PDF 캡션보다 길고 앞부분이 같으면 교체
    · 라벨 중복 제거          'Fig 1. Fig 1. …' → 'Fig 1. …'
    · 누락 항목 보충          PDF 에만 있는 번호를 새 항목으로
    · 본문 누수 제거          캡션이 문단 안에 있으면 지우고 문장을 다시 잇는다
    """
    if typeset is None:
        try:
            from . import textfix
            typeset = bool(textfix.encoding_profile(doc or {}))
        except Exception:                       # noqa: BLE001
            typeset = False

    lines = document_lines(pdf_path)
    mine, other, why = owned_captions(pdf_path, doc, lines=lines, typeset=typeset)
    rep: dict[str, Any] = {"paper_id": doc.get("paper_id"), "span_reason": why,
                           "pdf_captions": len(mine), "rejected_neighbour": len(other),
                           "rejected_samples": [c.text[:90] for c in other[:6]],
                           "filled": [], "trimmed": [], "deduped": [],
                           "added": [], "body_stripped": [], "held": []}
    if not mine:
        return rep

    ambig = {"fig": ambiguous_numbers(mine, "fig"),
             "tab": ambiguous_numbers(mine, "tab")}
    by_kind = {"fig": caption_map(mine, "fig"), "tab": caption_map(mine, "tab")}

    for kind, field_name, prefix in (("fig", "figures", "fig"), ("tab", "tables", "tab")):
        items = doc.get(field_name) or []
        pdfcaps = by_kind[kind]
        used: set[str] = set()
        # 1) 번호를 가진 정본 항목 ↔ PDF 번호
        for it in items:
            cur = (it.get("caption") or "").strip()
            got = parse_caption(cur) if cur else None
            if got and got["num"] is not None and not got["supp"]:
                key = str(got["num"])
                if key in pdfcaps:
                    used.add(key)
                    ref = pdfcaps[key]
                    fixed, n = dedupe_label(cur)
                    if n:
                        rep["deduped"].append({"id": it.get("id"), "n": n})
                        cur, it["caption"] = fixed, fixed
                    # 본문 삼킴: 정본이 PDF 캡션보다 30% 이상 길고 머리가 같다
                    a, b = _norm_key(cur), _norm_key(ref)
                    if len(a) > len(b) * 1.3 and a.startswith(b[:max(40, len(b) // 2)]):
                        rep["trimmed"].append({
                            "id": it.get("id"), "was": len(cur), "now": len(ref),
                            "cut": cur[len(ref):][:120]})
                        it["caption"] = ref
        # 2) 빈 캡션 채우기 — 남은 번호가 정확히 하나면 그것
        empties = [it for it in items if _empty(it)]
        free = [k for k in sorted(pdfcaps, key=lambda x: int(x)) if k not in used]
        for it in empties:
            m = re.search(r"(\d+)$", str(it.get("id") or ""))
            key = None
            if m and m.group(1) in free:
                key = m.group(1)
            elif len(empties) == 1 and len(free) == 1:
                key = free[0]
            if key is None:
                continue
            if int(key) in ambig[kind]:
                rep["held"].append({"id": it.get("id"), "why": "번호 모호"})
                continue
            it["caption"] = pdfcaps[key]
            used.add(key)
            free.remove(key)
            rep["filled"].append({"id": it.get("id"), "num": key,
                                  "caption": pdfcaps[key][:100]})
        # 3) PDF 에만 있는 번호 보충
        if fill_missing:
            have = {_norm_key(i.get("caption") or "") for i in items}
            for key in list(free):
                if int(key) in ambig[kind]:
                    rep["held"].append({"num": key, "why": "번호 모호"})
                    continue
                cap = pdfcaps[key]
                nk = _norm_key(cap)
                if any(nk and nk in h for h in have if h):
                    rep["held"].append({"num": key, "why": "캡션 중복"})
                    continue
                new = {"id": f"{prefix}{key}", "caption": cap}
                if kind == "fig":
                    new["image"] = None
                else:
                    new["markdown"] = ""
                items.append(new)
                have.add(nk)
                rep["added"].append({"id": new["id"], "caption": cap[:100]})
        doc[field_name] = items

    if strip_body:
        rep["body_stripped"] = strip_captions_from_body(doc, mine)
    return rep


__all__ = [
    "Line", "Caption", "SIZE_TOL", "MAX_CAPTION_CHARS",
    "prep", "parse_caption", "normalize_label", "dedupe_label", "clean_text",
    "document_lines", "body_size", "extract_captions", "article_span",
    "owned_captions", "caption_map", "ambiguous_numbers",
    "strip_captions_from_body", "repair_document",
]
