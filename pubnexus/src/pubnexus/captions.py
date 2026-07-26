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

**논문 경계 판정은 boundary.py 가 한다. 여기서 다시 하지 않는다.**
boundary 는 제목 런·단독 DOI 줄·'To the Editor' 서두로 지면을 구간으로 나누고
이 논문의 구간을 지목한다(grobid_client·pdf_fallback 과 같은 판정기).
캡션은 본문 문단과 달리 **한 덩어리로 조판된 물리적 블록**이라 걸친 곳이 곧 그
논문이다. 그래서 boundary.owner()(문단용 — 내 구간에서 한 조각도 안 나와야
'other')보다 엄격하게, **찾힌 조각이 전부 내 구간 밖이면 other** 로 본다
(caption_owner). boundary 가 구간을 확신하지 못하면 자르지 않는다.

boundary.analyze 가 실패하면 **캡션을 하나도 쓰지 않는다**. 소유를 모르는 채로
보충하면 이웃 논문 글이 정본에 들어간다 — 그림 몇 개보다 오염이 해롭다.

── 이 모듈이 하는 일 ───────────────────────────────────────────────────
  extract_captions(pdf)          좌표·글꼴 근거 캡션 목록(경계 확정)
  caption_owner(bmap, text)      boundary 구간으로 캡션 한 개의 소속 판정
  owned_captions(pdf, doc)       위 둘을 묶어 이 논문 캡션만
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
STRIP_MIN_CHARS = 60      # 본문에서 캡션을 지울 최소 일치 길이(정규화 기준)
DESC_ONLY_MIN_CHARS = 110  # 라벨 없이 설명만으로 지울 때의 최소 일치 길이

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
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
          "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
          "XIV": 14, "XV": 15}

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏⁠﻿]")
_CTRL_DIGITS_RE = re.compile(r"(?<=\d)[\x00-\x08\x0b\x0c\x0e-\x1f](?=\d)")
_SOFT_HYPHEN_RE = re.compile(r"­")
_ALNUM_RE = re.compile(r"[a-z0-9]")
# 캡션을 지운 자리가 기능어에서 끊기면 그건 본문 문장이었다(되돌리기 신호)
_DANGLING_RE = re.compile(
    r"\b(?:the|a|an|of|and|in|on|to|for|with|that|is|are|was|were|by|from|"
    r"between|among|shows?|summar\w*|lists?|presents?)\s*[.,;:)\]]*\s*$", re.I)


def _bold_name(font: str) -> bool:
    """서브셋 글꼴 이름에서 굵기를 읽는다('AdvTTfac587ca.B', 'Helvetica-Bold', '…-Semibold')."""
    f = (font or "")
    low = f.lower()
    return ("bold" in low or "black" in low or "heavy" in low
            or f.endswith((".B", "-B", ",B", ".BI", "-BI"))
            or low.endswith("bd") or low.endswith("-md"))


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
    lead_distinct: bool = False   # 라벨 글꼴이 그 줄의 나머지와 다른가
    frags: int = 1                # 같은 밑줄에서 합친 조각 수(표 행이면 크다)
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
    words = desc.split()
    if words:                      # min_desc=0 이면 설명이 빈 줄('Fig. 1')도 온다
        first = words[0]
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
def _merge_fragments(frags: list[Line]) -> list[Line]:
    """같은 블록·같은 밑줄에 놓인 조각들을 한 줄로 합친다.

    PyMuPDF 는 가로 간격이 크면 한 시각적 줄을 여러 line 으로 쪼갠다. Wiley 는
    라벨과 설명 사이를 넓게 띄우므로(실측 10.1111/jocd.12338 blk25:
    'FIGURE 1'(x398,y626) + 'Clinical photographs with'(x449,y626)) 합치지 않으면
    라벨만 있는 줄이 되어 캡션으로 인식되지 못한다.
    """
    from collections import Counter

    out: list[Line] = []
    i = 0
    frags = sorted(frags, key=lambda f: (round(f.y0, 1), f.x0))
    while i < len(frags):
        grp = [frags[i]]
        j = i + 1
        while j < len(frags):
            g0 = min(f.y0 for f in grp)
            g1 = max(f.y1 for f in grp)
            c = frags[j]
            ov = min(g1, c.y1) - max(g0, c.y0)
            if ov <= 0 or ov < 0.55 * min(g1 - g0, c.y1 - c.y0):
                break
            grp.append(c)
            j += 1
        if len(grp) == 1:
            out.append(grp[0])
        else:
            grp.sort(key=lambda f: f.x0)
            sizes: Counter = Counter()
            for f in grp:
                sizes[f.size] += max(1, len(f.text))
            head = grp[0]
            out.append(Line(
                page=head.page, block=head.block,
                text=" ".join(f.text.strip() for f in grp),
                x0=min(f.x0 for f in grp), y0=min(f.y0 for f in grp),
                x1=max(f.x1 for f in grp), y1=max(f.y1 for f in grp),
                size=sizes.most_common(1)[0][0],
                lead_size=head.lead_size, lead_font=head.lead_font, bold=head.bold,
                lead_distinct=(head.lead_distinct
                               or any(f.lead_font != head.lead_font for f in grp[1:])),
                frags=len(grp),
                bbox_block=head.bbox_block, first_of_block=head.first_of_block,
                page_width=head.page_width))
        i = j
    return out


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
            fonts: Counter = Counter()
            for s in spans:
                sizes[round(float(s["size"]), 1)] += max(1, len(s["text"]))
                fonts[s.get("font", "")] += max(1, len(s["text"]))
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
                or _bold_name(lead.get("font", "")),
                lead_distinct=(lead.get("font", "") != fonts.most_common(1)[0][0]),
                bbox_block=bb, first_of_block=first,
                page_width=float(page.rect.width or 595.0)))
            first = False
    # 블록별로 같은 밑줄 조각을 합치고 first_of_block 을 다시 매긴다
    merged: list[Line] = []
    for bi in sorted({l.block for l in out}):
        grp = _merge_fragments([l for l in out if l.block == bi])
        for k, l in enumerate(grp):
            l.first_of_block = (k == 0)
        merged.extend(grp)
    merged.sort(key=lambda l: (round(l.y0, 1), l.x0))
    return merged


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


def _is_seed(line: Line, body: float, *, min_desc: int = 0) -> dict | None:
    """이 줄이 캡션의 시작인가. 아니면 None.

    기본 min_desc=0 이다 — 캡션 첫 **줄**은 짧을 수 있다(실측
    10.1007/s00256-009-0872-x 3쪽 'Fig. 2 a Axial T1-weighted' 는 설명이 17자뿐이라
    20자 문턱에 걸려 캡션으로 안 잡혔고, 그래서 Fig. 2 가 통째로 누락됐다).
    길이 검사는 이어붙이기가 끝난 **전체 캡션**에 대해 extract_captions 가 한다.
    """
    got = parse_caption(line.text, min_desc=min_desc)
    if not got:
        return None
    # 캡션은 본문과 **조판이 다르다**. 넷 중 하나라도 있으면 캡션으로 본다.
    #   · 글자 크기가 본문과 다르다(Springer 8.5 vs 10.0 등)
    #   · 라벨이 굵다
    #   · 라벨 글꼴이 그 줄의 나머지와 다르다
    #     (실측 10.1111/jocd.12338 'FIGURE 1'(AdvTTfac587ca.B) +
    #      'Clinical photographs with'(AdvTTa9c1b374) — 크기는 둘 다 본문과 같은 8.0)
    #   · 번호 뒤에 구두점이 있다('Table 1. …')
    if (_same_size(line.size, body) and not line.bold and not line.lead_distinct):
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
        # (a) 같은 단 바로 아래.
        # 먼저 **같은 블록의 다음 줄**을 본다. 그림 위 패널문자('a','b' 12.0pt)가
        # 읽기순서상 캡션 줄 사이에 끼어들면 바로 다음 줄만 보고는 캡션이 한 줄에서
        # 끊긴다(실측 10.1007/s00256-009-0872-x 3쪽 'Fig. 2 a Axial T1-weighted'
        # 한 줄만 잡히고 Fig. 2 가 통째로 누락됐다).
        j = taken[-1] + 1
        for k in range(taken[-1] + 1, page_hi):
            if lines[k].block == cur.block:
                j = k
                break
            if lines[k].block in jumped:
                continue
        if j < page_hi:
            n = lines[j]
            # 줄상자는 서로 조금 겹치기도 한다(촘촘한 행간) → 아래쪽이기만 하면 된다
            below = n.y0 > cur.y0 + 0.3 * cur.height
            near = (n.y0 - cur.y1) <= LINE_GAP_FACTOR * cur.height
            # 한 밑줄에서 조각이 셋 이상 합쳐졌으면 그건 산문이 아니라 **표 행**이다.
            # (실측 10.1111/bjd.21054 2쪽 Table 1 은 캐션과 셀이 둘 다 8.0pt 라
            #  크기로는 갈리지 않고, 캐션이 'Levodopa use Vitiligo group Control
            #  group OR (95% CI) P-value Ever use 185 (0·33%) …' 까지 삼켰다)
            if n.frags >= 3:
                reason = f"표 행(한 밑줄 조각 {n.frags}개)"
                nxt_idx = None
                break
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
        parsed = parse_caption(raw_text)
        if parsed is None:
            # 이어붙여도 설명이 20자에 못 미치면 캡션이 아니다(축 라벨·패널문자 등)
            continue
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


# ── 소유 판정 ── boundary.py 에 전적으로 의존한다 ───────────
# 합본 지면의 논문 경계는 boundary.analyze() 가 이미 판정한다(제목 런 · 단독 DOI 줄 ·
# 'To the Editor' 서두). 같은 일을 여기서 다시 하면 두 판정이 엇갈려 어느 쪽도 믿을 수
# 없게 된다. 이 모듈은 **캐션을 뽑고 경계를 잡는 일**만 하고, '이 캡션이 누구 것이냐'는
# boundary 에게 묻는다.
#
# 캐션은 본문 문단과 다르다 — **한 덩어리로 조판된 물리적 블록**이라 걸친 곳이
# 곷 그 논문이다. 그래서 boundary.owner() (문단용 — 내 구간에서 한 조각도 안 나와야
# 'other') 보다 엄격하게, **찾힌 조각이 전부 내 구간 밖이면 other** 로 본다.
CAPTION_PROBES = 8         # boundary.locate 에 넘길 조각 수


def caption_owner(bmap, text: str) -> str:
    """boundary 구간으로 캐션 한 개의 소속을 읽는다 — 'own' | 'other' | 'unknown'.

    boundary 가 구간을 확신하지 못하면(confident=False) 나누지 않는다 — 그 경우
    한 편짜리 지면이면 전부 내 것이고, 여러 편이면 모른다고 답한다.
    위치를 못 찾은 캐션은 unknown 이고, unknown 은 **남긴다**(boundary 의 원칙과 같다 —
    실측 10.1016/j.jaad.2018.10.010 의 'Table I. Interventions and clinical outcomes
    described in all included studies' 는 세로 조판 쪽이라 boundary 스트림에 없지만
    이 논문 것이다).
    """
    if bmap is None or not getattr(bmap, "segments", None):
        return "unknown"
    if not bmap.confident or bmap.own is None:
        return "own" if len(bmap.segments) <= 1 else "unknown"
    hits = [h for h in bmap.locate(text, probes=CAPTION_PROBES) if h >= 0]
    if not hits:
        return "unknown"
    return "own" if bmap.own in hits else "other"


def boundary_map(pdf_path: str | Path, doc: dict):
    """이 PDF 의 boundary.BoundaryMap. 실패하면 None."""
    from . import boundary

    meta = doc.get("meta") or {}
    probe = " ".join(p.get("text") or ""
                     for s in (doc.get("body_text") or [])
                     for p in (s.get("paragraphs") or []))[:4000]
    return boundary.analyze(pdf_path,
                            {"doi": doc.get("paper_id") or meta.get("doi"),
                             "title": meta.get("title") or ""},
                            body_probe=probe)


def owned_captions(pdf_path: str | Path, doc: dict, *,
                   lines: list[Line] | None = None,
                   typeset: bool = False,
                   bmap=None) -> tuple[list[Caption], list[Caption], str]:
    """(이 논문 캡션, 이웃 논문으로 판정해 버린 캡션, 근거).

    boundary.analyze 가 터지면 **아무것도 돌려주지 않는다**. 소유를 모르는 채로
    캡션을 보충하면 이웃 논문 글이 정본에 들어간다 — 그림 몇 개보다 오염이 해롭다.
    """
    lines = lines if lines is not None else document_lines(pdf_path)
    caps = extract_captions(pdf_path, lines=lines, typeset=typeset)
    if bmap is None:
        try:
            bmap = boundary_map(pdf_path, doc)
        except Exception as e:                       # noqa: BLE001
            return [], caps, f"boundary.analyze 실패({type(e).__name__}: {e}) — 전량 보류"
    mine, other = [], []
    for c in caps:
        (other if caption_owner(bmap, c.text) == "other" else mine).append(c)
    why = (f"boundary: 구간 {len(bmap.segments)}개 · 내 구간 {bmap.own} · "
           f"확신 {bmap.confident} · {bmap.reason[:70]}")
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
    cut_head: set[int] = set()      # 머리에서 캡션을 뗀 문단
    cut_tail: set[int] = set()      # 꼬리에서 캡션을 뗀 문단
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
                        # 라벨 없는 설명만으로 지우는 것은 더 길게 일치할 때만 허용한다.
                        # 'Table 1 summarizes the demographics and baseline clinical
                        #  characteristics of the study population.' 같은 **본문 문장**이
                        # 캡션 설명과 같은 말이라서 통째로 지워지는 사고를 막는다
                        # (실측 10.1016/j.jcjo.2018.04.020 p8).
                        floor = min_chars if needle is nfull else DESC_ONLY_MIN_CHARS
                        if len(needle) < floor:
                            continue
                        pos, ln = _longest_prefix_at(hay, needle, floor)
                        if pos < 0 or ln < floor:
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
                        # 지운 결과가 기능어에서 끊기면 그것은 캡션 누수가 아니라
                        # 본문 문장이었다는 뜻이다 → 되돌린다.
                        if _DANGLING_RE.search(new):
                            continue
                        # 남은 쪽이 구두점뿐이면 사실상 머리/꼬리에서 뗀 것이다
                        lsig = left.strip(" .,;:)]·\t\n")
                        rsig = right.strip(" .,;:([·\t\n")
                        where = ("가운데" if lsig and rsig
                                 else ("앞" if not lsig else "뒤"))
                        reports.append({
                            "paragraph": p.get("id"), "caption": cap.key(),
                            "removed": len(text) - len(new), "where": where,
                            "sample": text[max(0, a - 40):a]
                                      + " ⟦" + text[a:a + 60] + "…⟧ "
                                      + text[b:b + 40]})
                        cut_head.add(id(p)) if where == "앞" else None
                        cut_tail.add(id(p)) if where == "뒤" else None
                        text = new
                        changed = True
                        break
                    if changed:
                        break
            p["text"] = text

    # 문단 경계에서 잘린 문장 재봉합 — 앞 문단 꼬리에서 캡션을 떼고 뒤 문단 머리에서도
    # 떼었다면 원래 **한 문장**이 캡션 때문에 두 문단으로 갈린 것이다.
    # (실측 10.1016/j.jid.2023.07.007: p12 '…whereas we found a lower' + Figure 3 캡션,
    #  p13 Figure 4 캡션 + 'risk of cardiovascular mortality in Korean patients…')
    for sec in (doc.get("body_text") or []):
        ps = sec.get("paragraphs") or []
        merged: list[dict] = []
        for p in ps:
            if (merged and id(merged[-1]) in cut_tail and id(p) in cut_head):
                lend = re.sub(r"[\s.]+$", "", merged[-1].get("text") or "")
                rstart = re.sub(r"^[\s.]+", "", p.get("text") or "")
                # 뒤쪽이 소문자로 시작할 때만 잇는다 — 그것이 '한 문장이 갈렸다'는
                # 증거다. 대문자로 시작하면 원래 다른 문단이었을 수 있다.
                if lend and rstart and rstart[:1].islower():
                    merged[-1]["text"] = f"{lend} {rstart}"
                    for k in ("cited_refs", "cited_keys", "refs_figure", "refs_table"):
                        merged[-1][k] = list(dict.fromkeys(
                            (merged[-1].get(k) or []) + (p.get(k) or [])))
                    reports.append({"paragraph": merged[-1].get("id"),
                                    "caption": "-", "removed": 0, "where": "문단 병합",
                                    "sample": f"{lend[-50:]} ⟦+⟧ {rstart[:50]}"})
                    continue
            merged.append(p)
        sec["paragraphs"] = merged

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

    desc_by_kind = {"fig": {}, "tab": {}}
    for c in mine:
        if c.supp or c.num is None:
            continue
        k = str(c.num)
        if k not in desc_by_kind[c.kind] or len(c.desc) > len(desc_by_kind[c.kind][k]):
            desc_by_kind[c.kind][k] = c.desc

    for kind, field_name, prefix in (("fig", "figures", "fig"), ("tab", "tables", "tab")):
        items = doc.get(field_name) or []
        pdfcaps = by_kind[kind]
        descs = desc_by_kind[kind]
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
                    # 본문 삼킴·앞머리 오염: PDF 캡션이 정본 캡션 **안에 들어 있고**
                    # 정본 쪽이 더 길면 PDF 것으로 바꾼다. 앞머리가 오염된 경우
                    # (실측 10.1007/s00256-009-0872-x fig_1 = 'Fig. 2 aFig. 3 a.
                    #  Fig. 2 a Axial T1-weighted image shows …' — 앞에 다른 그림의
                    #  라벨이 붙어 startswith 로는 안 걸린다)도 여기서 잡힌다.
                    # 비교는 **설명 본문**으로 한다 — 라벨·패널문자 표기가 파서마다
                    # 달라('Fig. 2 a Axial …' vs 'Fig. 2. Axial …') 머리로 맞추면
                    # 어긋난다.
                    a, b = _norm_key(cur), _norm_key(ref)
                    dsc = _norm_key(descs.get(key, ""))
                    head = dsc[:max(40, min(60, len(dsc)))]
                    # 바꾸는 조건 둘: (1) 정본이 15% 이상 길다(본문 삼킴)
                    #                 (2) 설명이 시작되는 위치가 PDF 보다 뒤다
                    #                     (앞머리에 다른 그림 라벨이 붙었다)
                    swallowed = len(a) > len(b) * 1.15
                    polluted = head and a.find(head) > b.find(head) + 3
                    if len(b) >= 40 and head and head in a and (swallowed or polluted):
                        rep["trimmed"].append({
                            "id": it.get("id"), "was": len(cur), "now": len(ref),
                            "cut": cur[:120]})
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


def apply_to_parsed(document, pdf_path: str | Path, *,
                    fill_missing: bool = True, strip_body: bool = True) -> dict:
    """schema.Document(데이터클래스)에 그대로 적용한다 — grobid_client·pdf_fallback 연결용.

    boundary.apply_to_parsed 와 같은 모양으로 둔다(같은 자리에서 나란히 부른다).
    기존 Figure/Table 객체는 **caption 만 제자리에서 고치고**, 새로 보충되는 것만
    객체로 만들어 덧붙인다. ImageTable 같은 하위형의 추가 필드를 잃지 않기 위함이다.
    """
    from .schema import Figure, Table

    figs = list(getattr(document, "figures", None) or [])
    tabs = list(getattr(document, "tables", None) or [])
    view: dict[str, Any] = {
        "paper_id": getattr(document, "paper_id", ""),
        "abstract": getattr(document, "abstract", "") or "",
        "meta": {"title": getattr(getattr(document, "meta", None), "title", "") or ""},
        "figures": [{"id": f.id, "caption": f.caption or "", "image": f.image}
                    for f in figs],
        "tables": [{"id": t.id, "caption": t.caption or "",
                    "markdown": t.markdown or ""} for t in tabs],
        "body_text": [{"path": list(s.path), "section_type": s.section_type,
                       "paragraphs": [{"id": p.id, "text": p.text,
                                       "cited_refs": list(p.cited_refs),
                                       "cited_keys": list(p.cited_keys),
                                       "refs_figure": list(p.refs_figure),
                                       "refs_table": list(p.refs_table)}
                                      for p in s.paragraphs]}
                      for s in (getattr(document, "body_text", None) or [])],
    }
    rep = repair_document(view, pdf_path, fill_missing=fill_missing,
                          strip_body=strip_body)

    # 캡션 반영 — 기존 객체는 제자리 수정, 신규만 추가
    for src, objs, cls in (("figures", figs, Figure), ("tables", tabs, Table)):
        by_id = {o.id: o for o in objs}
        out = []
        for row in view[src]:
            o = by_id.get(row["id"])
            if o is not None:
                o.caption = row["caption"]
                out.append(o)
            else:
                out.append(cls(id=row["id"], caption=row["caption"])
                           if cls is Figure else
                           cls(id=row["id"], caption=row["caption"],
                               markdown=row.get("markdown") or ""))
        setattr(document, src, out)

    # 본문 반영 — 문단 텍스트·병합 결과를 되돌려 쓴다
    if strip_body:
        secs = getattr(document, "body_text", None) or []
        keep = []
        for sec, row in zip(secs, view["body_text"]):
            wanted = {p["id"]: p for p in row["paragraphs"]}
            ps = []
            for p in sec.paragraphs:
                w = wanted.get(p.id)
                if w is None:
                    continue                     # 지워졌거나 앞 문단에 합쳐졌다
                p.text = w["text"]
                p.refs_figure = list(w.get("refs_figure") or [])
                p.refs_table = list(w.get("refs_table") or [])
                ps.append(p)
            if ps:
                sec.paragraphs = ps
                keep.append(sec)
        if len(view["body_text"]) == len(secs):
            document.body_text = keep
    return rep


__all__ = [
    "Line", "Caption", "SIZE_TOL", "MAX_CAPTION_CHARS",
    "prep", "parse_caption", "normalize_label", "dedupe_label", "clean_text",
    "document_lines", "body_size", "extract_captions", "boundary_map",
    "caption_owner",
    "owned_captions", "caption_map", "ambiguous_numbers",
    "strip_captions_from_body", "repair_document", "apply_to_parsed",
]
