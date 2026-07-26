"""4.6단계 — 그림·표 누락/과잉과 캡션 접두 중복을 원본 PDF 대조로 수리.

정본 JSON 의 figures/tables 는 PDF 조판을 그대로 못 따라간다. 167편 실측 결함은 넷이다.

  F1 캡션 접두 중복  'Fig 1. Fig 1. Repigmentation after …'
                     GROBID TEI 의 <head>(라벨)와 <figDesc>(설명)을 이어 붙일 때
                     설명 앞머리에 이미 라벨이 들어 있어 두 번 찍힌다.
  F2 누락            PDF 에는 Fig 1~4 인데 정본에는 3개. 조판이 특이한 캡션
                     (자간 조판 'TA B L E 4', 앞에 패널문자가 새어 든 'b c Fig. 6 |')
                     을 파서가 캡션으로 못 알아본다.
  F3 캡션 결손       표 본문(markdown)은 살아 있는데 캡션이 빈 항목. 그림 쪽은 캡션도
                     이미지도 없는 껍데기(figN)만 남는다.
  F4 과잉            PDF 표 2개인데 정본 표 4개. 본문 조각이 표로 잘못 잡히거나,
                     한 표가 페이지 경계에서 'Continued' 로 쪼개진다.

여기서는 **PDF 를 진실의 기준**으로 삼는다. PyMuPDF 블록 텍스트에서 캡션을 뽑고
(pdf_figure_captions/pdf_table_captions), 정본 항목을 번호에 정렬한 뒤(align_items)
빠진 번호를 채우고 중복 접두를 지운다.

캡션 판별 규칙(오탐 0 우선)
  (a) 블록(또는 줄)의 **맨 앞**에서 시작한다. '(Fig 2)' 처럼 여는 괄호가 앞서면 탈락 →
      본문 중 참조를 구조적으로 배제한다.
  (b) 'Figure|Fig|Fig.|FIGURE|Table|TABLE' + 번호(아라비아/로마/S1·E1 형).
  (c) 번호 뒤 설명이 20자 이상 → 'Table 1. Continued' 같은 이어짐 머리글이 탈락한다.
  (d) 설명 첫 낱말이 소문자 서술동사(shows/lists/…)나 기능어면 탈락 →
      'Table 1 shows the characteristics…' 같은 본문 첫 문장을 배제한다.
      단 'Fig. 1 a The patient…'(Springer 패널문자)는 패널문자를 흡수해 살린다.

정본 항목 ↔ PDF 번호 정렬은 3단계다(앞 단계가 이기고, 배정된 번호는 재사용 안 함).
  1) 캡션이 스스로 번호를 갖고 있다 → 그 번호.
  2) 캡션 설명 퍼지 일치(길이에 강건한 접두/부분 일치, 기본 임계 80). 167편 실측에서
     번호를 가진 284건 argmax 정확도 98%, 번호 없는 항목은 80 위/아래로 깨끗이 갈린다.
  3) **페이지 결속** — 캡션이 빈 표에 한해, 표 셀 값이 실제로 찍힌 PDF 쪽을 찾아
     그 쪽의 미배정 캡션이 정확히 하나면 그것으로 본다.

수리는 보수적이다. 보충(정본에 없는 항목을 PDF 캡션으로 채우는 일)에는 가드가 넷이다.
  · **문자 규약** — 넣기 전 clean_caption 으로 정본과 같은 세탁을 거친다. 안 하면
    합자(ﬁ)·연성하이픈·제어문자가 캡션에서만 살아남는다(가드 전 실측 28/138건).
  · **주제 일치** — 캡션 내용어가 그 문서 어휘에 하나도 없으면 넣지 않는다.
  · **번호 모호** — 한 PDF 에서 서로 다른 캡션이 같은 번호를 주장하면(레터 합본
    지면) 어느 쪽이 이 논문 것인지 알 수 없으므로 보충하지 않는다.
  · **캡션 중복** — 같은 글이 이미 문서에 있으면(GROBID 가 표 캡션을 그림에
    붙여 놓은 경우) 넣지 않는다. 가드 전에는 완전중복 3쌍이 만들어졌다.
  과잉은 **지우지 않는다**. 'Continued' 로 쪼개진 표만 앞 표에 합친다.

파일럿 167편 실측(수리 전 → 후. 사본에서 전수 재측정)
  캡션 접두 중복   그림 150편분 → 0 · 표 0(표에는 애초에 없다)
  누락 없는 편수   그림 113 → 164 / 167 · 표 134 → 165 / 167
  PDF 와 개수 일치 그림 105 → 120 / 167 · 표 113 → 126 / 167
  보충            그림 69개 · 표 66개(신규 54 + 빈 캡션 채움 12)
  병합            이어짐 표 6건
  보류            주제 불일치 3건 · 캡션 중복 3건 · 번호 모호 0건
  보충 캡션 135건은 전부 PDF 원문에 있는 글이다(정규화 대조 불일치 0건).
  잔여 불일치는 거의 전부 GROBID 과잉 추출(본문 조각이 그림/표로 잡힘)이고,
  여기서는 손대지 않고 리포트에만 남긴다.

2026-07-26 수정 — 캡션 출처를 captions.py 로 바꿨다(geometry=True 가 기본)
  위 '알려진 한계' 첫 항목(합본 지면 이웃 논문 캡션 유입)이 이 모듈을 파이프라인에
  연결하지 못한 이유였다. 이제 캡션 목록을 geometry_caption_index() → captions.py 에서
  받는다. captions 는 PDF 를 읽기순서 스트림으로 펴고 이웃 편 DOI 도장·'To the Editor'
  표지로 이 논문 구간을 끊어, 구간 밖 캡션을 후보에서 뺀다.
  167편 전수 A/B 실측(같은 코드·같은 PDF, geometry 만 바꿈):
      geometry=False  보충 118건 · **새로 들어간 이웃 논문 캡션 2건**
                      (10.1016/j.jaad.2016.05.022 → 이웃 레터 …2016.05.014 의
                       'Table II. Final diagnoses made by the consulting inpatient
                       dermatology team' / 10.1111/bjd.21054 → tab_pdf1)
      geometry=True   보충 111건 · **새로 들어간 이웃 논문 캡션 0건**
  캡션 종료 경계도 글자 크기 등급으로 잡으므로 '캡션이 본문을 삼킴'이 함께 줄어든다.

남은 한계(리포트로만 남긴다)
  · 정본에 **이미 들어 있는** 이웃 논문 캡션은 지우지 않는다(과잉 삭제 안 함 원칙).
    167편에 14건 남아 있으며 근본 수리는 상류(GROBID 분할)에서 해야 한다.
  · captions 의 구간 판정이 이 논문 캡션을 이웃 것으로 잘못 버리는 일이 있다
    (실측 24건 중 7건 — 정본 본문 자체가 이웃 글로 오염돼 정박이 엉뚱한 토막을
    가리키는 문서들. 예: 10.1111/bjd.21054 의 'Table 1. Prior use of levodopa …').
    오염 0 을 지키려고 재현율을 내준 결과다.
  · 보충한 캡션과 **같은 글이 본문 문단에도 남아 있는** 문서가 있었다(GROBID 캡션
    누수). 이제 captions.strip_captions_from_body 가 그 본문 쪽을 지운다(실측 18편 → 0).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from . import utils
from .utils import log

# ── 캡션 문법 ────────────────────────────────────────────────────────
_FIGW = r"(?:FIGURES?|FIGS?|Figures?|Figs?|Fig|FIG)"
_TABW = r"(?:TABLES?|Tables?|Tbl|TABLE|Table)"
# 아라비아(1~99) · 로마(I~XV) · 보조자료형(S1·E2·A1)
_NUM = r"(?:[0-9]{1,2}|[IVX]{1,5}|[A-Z]{1,2}[0-9]{1,2})"
# 번호와 설명 사이 구분자. Nature 계열은 '|', Springer 계열은 공백만 쓴다.
_SEP = r"(?:\s*[.:|—–‒]\s*|\s+)"
_SUPW = (r"(?:Supplementary|Supplemental|Supporting|Online|Appendix|"
         r"SUPPLEMENTARY|SUPPLEMENTAL)")

CAPTION_RE = re.compile(
    r"^(?P<sup>" + _SUPW + r"\s+|e(?=Table|Figure))?"
    r"(?P<kind>" + _FIGW + r"|" + _TABW + r")(?P<dot>\s*\.)?\s*"
    r"(?:(?P<num>" + _NUM + r")(?P<panel>[a-h](?![a-zA-Z]))?(?P<sep>" + _SEP + r")"
    # 번호 없는 단일 그림/표('Figure. Vitiligo Severity Algorithm'). 설명이 반드시
    # 대문자로 시작해야 한다 — 'Fig. 1). Demographic data …' 같은 본문 중 참조가
    # 줄바꿈으로 끊겼을 때 무번호 캡션으로 오인되는 것을 막는다.
    r"|(?P<nonum>[.:]\s+)(?=[A-Z]))"
    r"(?P<rest>.*)", re.S)

# 설명 첫 낱말이 이것이면 캡션이 아니라 본문(= 'Table 1 shows …').
_STOPWORD_RE = re.compile(
    r"^(?:shows?|showed|shown|displays?|demonstrat\w*|presents?|lists?|summar\w*|"
    r"depict\w*|illustrat\w*|reports?|describ\w*|provid\w*|indicat\w*|contain\w*|"
    r"gives?|and|or|of|in|on|to|for|the|an|were|was|is|are|also|see|from|but|"
    r"which|that|this|these|those|continue[sd]?|cont|above|below)\b", re.I)
# Springer 'Fig. 1 a The patient had …' — 번호 뒤 홑문자 패널 라벨
_PANEL_HEAD_RE = re.compile(r"^([a-h])\s+([A-Z(])")
# 조판 자간으로 흩어진 라벨: 'TA B L E 4', 'FI G U R E 1', 'F IG U R E 1'
_SPACED_LABEL_RE = re.compile(
    r"^(?P<pre>\s*)"
    r"(?P<kw>[Tt]\s*[Aa]\s*[Bb]\s*[Ll]\s*[Ee]|[Ff]\s*[Ii]\s*[Gg](?:\s*[Uu]\s*[Rr]\s*[Ee])?)"
    r"(?=\s*\.?\s*[0-9IVX])")
# 그림 위 패널문자가 캡션 블록 앞에 새어 든 경우: 'b c Fig. 6 | …', '0 Fig. 1. …'
_LEAD_JUNK_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[.)])?\s+){1,6}(?=(?:" + _FIGW + r"|" + _TABW + r")\b)")
_WS_RE = re.compile(r"[ \t     ]+")
# 이어짐 표 머리글: 'Continued', 'Continued…', '(continued)', "Cont'd"
_CONT_ONLY_RE = re.compile(
    r"^\W*(?:cont(?:inued|inues|inuation)?|cont[’'`]d)\W*$", re.I)
_CONT_TAIL_RE = re.compile(
    r"[\s(]*\b(?:cont(?:inued|inues|inuation)?|cont[’'`]d)\b[.…\s)]*$", re.I)

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
          "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
          "XIV": 14, "XV": 15}

MIN_DESC_CHARS = 20        # 캡션 판별: 번호 뒤 설명 최소 길이
MAX_CAPTION_CHARS = 1500   # 블록이 본문까지 물고 온 경우의 상한
EMPTY_CAPTION_CHARS = 15   # 이보다 짧은 캡션은 '없는 것'으로 본다(chunk.py 와 같은 기준)
FUZZY_CUTOFF = 80          # 캡션 퍼지 정렬 임계(실측 근거는 모듈 docstring 참고)
TOPICAL_CUTOFF = 0.25      # 보충 캡션 주제 일치 임계(0 인 경우가 이웃 논문 지면)
PAGE_VOTE_MIN = 3          # 페이지 결속에 필요한 최소 셀 일치 수
PAGE_SEP = "\f"            # pdf_blocks_text 가 넣는 페이지 구분자
AMBIG_CUTOFF = 70          # 같은 번호를 주장하는 두 캡션이 이보다 안 닮으면 '모호'
                           # (패널 변형 'Figure 1a'/'1b' 는 95 로 붙어 모호가 아니다)

# PDF 원문에만 있는 제어문자·폭 없는 문자(정본 본문에는 0건이다 — 실측)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2060\ufeff]")
_SOFT_HYPHEN_RE = re.compile(r"\u00ad")
_CTRL_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)[\x00-\x08\x0b\x0c\x0e-\x1f](?=\d)")


# ── 텍스트 전처리 ────────────────────────────────────────────────────
def _flatten(block: str) -> str:
    """블록 안 줄바꿈을 공백으로 접고 공백류를 정규화한다."""
    return _WS_RE.sub(" ", (block or "").replace("\n", " ")).strip()


def _unspace_label(s: str) -> str:
    """자간 조판된 라벨을 붙인다. 뒤에 번호가 올 때만 손대므로 본문은 안전하다."""
    m = _SPACED_LABEL_RE.match(s)
    if not m:
        return s
    return m.group("pre") + re.sub(r"\s+", "", m.group("kw")) + s[m.end():]


def _strip_lead_junk(s: str) -> str:
    """캡션 앞에 붙은 패널문자/축 눈금 부스러기를 뗀다('b c Fig. 6 |' → 'Fig. 6 |')."""
    m = _LEAD_JUNK_RE.match(s)
    return s[m.end():] if m else s


def _prep(block: str) -> str:
    return _strip_lead_junk(_unspace_label(_flatten(block)))


def clean_caption(text: str, *, typeset: bool = False) -> str:
    """PDF 에서 뽑은 캡션을 **정본 본문과 같은 문자 규약**으로 맞춘다.

    정본의 문단·캡션은 0~4단계에서 textfix 를 거쳐 합자(ﬁ)·연성하이픈·제어문자가
    한 건도 남아 있지 않다(167편 실측 0/2742 문단·0/615 캡션). PDF 원문을 그대로
    넣으면 그 규약이 캡션에서만 깨지므로 같은 세탁을 통과시킨다.

      1) 제어문자 — 숫자 사이의 것은 '·'(BJD 계열 소수점)로, 나머지는 제거
      2) 연성하이픈(U+00AD) 제거 — 'epi\\u00addermal' → 'epidermal'
      3) textfix.clean_paragraph — NFKC(합자 분해)·러닝헤더 제거·줄바꿈 분철 복원
      4) textfix.fix_encoding — 조판 사고 복원. typeset 은 그 문서가
         `textfix.encoding_profile()` 서명을 가질 때만 True 로 넘겨야 한다.

    멱등이다(여러 번 적용해도 같은 결과).
    """
    from . import textfix               # 무거운 import 는 함수 안에서

    s = _CTRL_BETWEEN_DIGITS_RE.sub("·", text or "")
    s = _SOFT_HYPHEN_RE.sub("", _CTRL_RE.sub("", s))
    s = textfix.clean_paragraph(s)
    return textfix.fix_encoding(s, typeset=typeset)


def _typeset_flag(doc: dict) -> bool:
    """이 문서가 조판 사고(서브셋 폰트 오매핑) 서명을 갖는가 — clean_caption 게이트."""
    from . import textfix

    try:
        return bool(textfix.encoding_profile(doc or {}))
    except Exception:                    # noqa: BLE001 — 게이트 실패는 보수적으로 닫는다
        return False


def roman_to_int(tok: str) -> int | None:
    """'III' → 3. 로마자가 아니면 None."""
    return _ROMAN.get((tok or "").upper())


def caption_number(tok: str | None) -> int | None:
    """캡션 번호 토큰을 정수로. 보조자료형(S1·E2)·해석불가는 None."""
    if tok is None:
        return None
    if tok.isdigit():
        return int(tok)
    return roman_to_int(tok)


def is_figure_word(kind: str) -> bool:
    return (kind or "").lower().startswith("fig")


# ── 캡션 한 건 파싱 ──────────────────────────────────────────────────
def parse_caption(text: str, *, min_desc: int = MIN_DESC_CHARS,
                  prepped: bool = False) -> dict[str, Any] | None:
    """문자열 머리에서 캡션을 읽는다. 캡션이 아니면 None.

    반환 dict: kind('fig'|'tab') · label('Fig.') · raw('II') · num(2|None) ·
               supp(bool) · desc(설명) · caption(라벨까지 붙인 완성 캡션)
    """
    t = text if prepped else _prep(text)
    m = CAPTION_RE.match(t)
    if not m:
        return None
    desc = m.group("rest").strip()
    # Springer 패널문자 흡수: 'Fig. 1 a The patient …' → 설명은 'The patient …'
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
    if raw is None:                      # 'Table. Demographic …' — 번호 없는 단일 표
        num = 1
    if len(desc) > MAX_CAPTION_CHARS:
        desc = desc[:MAX_CAPTION_CHARS].rstrip() + "…"
    # 라벨 표기(Fig. / Fig / FIGURE)와 패널문자(2a)는 원문대로 살린다.
    head = label + ("." if m.group("dot") else "")
    if raw:
        head += " " + raw + (m.group("panel") or "")
    return {"kind": "fig" if is_figure_word(label) else "tab",
            "label": label, "raw": raw, "num": num, "supp": supp,
            "unnumbered": raw is None, "panel": m.group("panel"),
            "head": head, "desc": desc, "caption": f"{head}. {desc}"}


# ── PDF → 캡션 사전 ──────────────────────────────────────────────────
def pdf_blocks_text(pdf_path: str | Path) -> str:
    """PDF 를 '블록=빈 줄, 페이지=폼피드(\\f)' 텍스트로 만든다.

    pdf_*_captions 의 입력 규약이다. 블록 단위로 뽑아야 캡션이 통째로 잡힌다
    (줄 단위로 뽑으면 두 줄짜리 캡션이 잘린다).
    """
    import fitz                      # 무거운 import 는 함수 안에서
    pages: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            blocks = [(b[4] or "").strip() for b in page.get_text("blocks")]
            pages.append("\n\n".join(b for b in blocks if b))
    return PAGE_SEP.join(pages)


def _page_texts(pdf_text: str) -> list[str]:
    return (pdf_text or "").split(PAGE_SEP)


def _iter_blocks(pdf_text: str) -> Iterator[tuple[int, str]]:
    """(페이지 번호, 블록 텍스트). 빈 줄이 없는 입력이면 줄을 블록으로 본다."""
    for pno, page in enumerate(_page_texts(pdf_text)):
        blocks = [b for b in re.split(r"\n[ \t]*\n", page) if b.strip()]
        if len(blocks) <= 1 and page.count("\n") > 4:
            blocks = [ln for ln in page.splitlines() if ln.strip()]
        for b in blocks:
            yield pno, b


def pdf_caption_index(pdf_text: str) -> list[dict[str, Any]]:
    """PDF 의 모든 캡션을 쪽 번호와 함께 순서대로 낸다(감사·페이지 결속용)."""
    out: list[dict[str, Any]] = []
    for pno, blk in _iter_blocks(pdf_text):
        got = parse_caption(blk)
        if got:
            out.append({**got, "page": pno})
    return out


def _collect(index: list[dict], kind: str, *, supplementary: bool,
             typeset: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for cap in index:
        if cap["kind"] != kind or cap["supp"] != supplementary:
            continue
        key = cap["raw"] if supplementary else (
            None if cap["num"] is None else str(cap["num"]))
        if not key:
            continue
        # 같은 번호가 여러 번 잡히면(이어짐 쪽 등) 설명이 가장 긴 것을 남긴다.
        if key not in out or len(cap["caption"]) > len(out[key]):
            out[key] = cap["caption"]
    return {k: clean_caption(v, typeset=typeset) for k, v in out.items()}


def ambiguous_numbers(index: list[dict], kind: str) -> set[int]:
    """한 PDF 안에서 **서로 다른 캡션이 같은 번호를 주장**하는 번호들.

    레터 합본 지면(한 PDF 에 논문 여럿)에서는 이웃 논문의 'Fig 1' 이 같은 자리를
    다툰다. 167편 실측에서 13건이 이 상태였고 그중 4건은 최장 캡션이 실제로
    이웃 논문 것이었다(예: 10.1016/j.jaad.2016.04.036 의 'Fig 1. Wait times in
    Parkland dermatology clinic'). 어느 쪽이 이 논문 것인지 PDF 만으로는 알 수
    없으므로 **보충에는 쓰지 않는다**(정렬에는 그대로 쓴다 — 정렬은 정본 캡션과의
    일치로 검증되기 때문이다).
    """
    seen: dict[int, list[str]] = {}
    for cap in index:
        if cap["kind"] != kind or cap["supp"] or cap["num"] is None:
            continue
        seen.setdefault(cap["num"], []).append(cap["caption"])
    out: set[int] = set()
    for num, caps in seen.items():
        if len(caps) < 2:
            continue
        base = caps[0]
        if any(caption_similarity(_desc_of(base), _desc_of(c)) < AMBIG_CUTOFF
               for c in caps[1:]):
            out.add(num)
    return out


def pdf_figure_captions(pdf_text: str) -> dict[str, str]:
    """PDF 텍스트 → {'1': 'Fig 1. Repigmentation after …', '2': …}.

    키는 그림 번호(로마자는 아라비아로 정규화). 보조자료(Supplementary Fig. S1)는
    빼고 본문 그림만 담는다 — 보조자료는 pdf_supplementary_captions 로.
    """
    return _collect(pdf_caption_index(pdf_text), "fig", supplementary=False)


def pdf_table_captions(pdf_text: str) -> dict[str, str]:
    """PDF 텍스트 → {'1': 'Table I. Clinical characteristics …', …}. 규약은 그림과 같다."""
    return _collect(pdf_caption_index(pdf_text), "tab", supplementary=False)


def geometry_caption_index(pdf_path: str | Path, doc: dict | None = None, *,
                           typeset: bool = False,
                           lines: list | None = None) -> list[dict[str, Any]]:
    """captions.py 로 **좌표·글꼴 근거** 캡션 목록을 만들어 이 모듈 규약으로 낸다.

    pdf_caption_index() 를 대체한다. 셋이 다르다.
      · 캡션 **종료 경계**를 글자 크기 등급으로 잡는다 → 뒤따르는 본문을 안 삼킨다
      · 단 경계를 넘어가는 캡션을 이어 붙인다
      · doc 을 주면 **이웃 논문 캡션을 걸러낸다**(합본 지면 오염 차단)

    doc 을 주지 않으면 소유 판정 없이 지면 전체를 낸다 — 합본 지면에서는
    이웃 논문 캡션이 섞이므로 **수리에는 반드시 doc 을 넘겨라**.
    """
    from . import captions as _cap

    ls = lines if lines is not None else _cap.document_lines(pdf_path)
    if doc is not None:
        caps, _other, _why = _cap.owned_captions(pdf_path, doc, lines=ls,
                                                 typeset=typeset)
    else:
        caps = _cap.extract_captions(pdf_path, lines=ls, typeset=typeset)
    return [{"kind": c.kind, "label": c.label, "raw": c.raw, "num": c.num,
             "supp": c.supp, "unnumbered": c.raw is None, "panel": None,
             "head": c.head, "desc": c.desc, "caption": c.text,
             "page": c.page, "bbox": c.bbox, "evidence": c.evidence}
            for c in caps]


def pdf_supplementary_captions(pdf_text: str) -> dict[str, dict[str, str]]:
    """보조자료 캡션(Supplementary Figure S1 / FIG E1 / eTable 1)만 따로 모은다."""
    idx = pdf_caption_index(pdf_text)
    return {"figures": _collect(idx, "fig", supplementary=True),
            "tables": _collect(idx, "tab", supplementary=True)}


# ── 캡션 접두 중복 ───────────────────────────────────────────────────
def strip_duplicate_prefix(caption: str) -> tuple[str, int]:
    """'Fig 1. Fig 1. Repigmentation …' → ('Fig 1. Repigmentation …', 1).

    같은 종류(fig/tab)·같은 번호의 라벨이 연달아 두 번 이상 찍힌 경우만 지운다.
    'Table 2. Comparison with Table 1' 처럼 설명 **안쪽**의 라벨은 검사 대상이
    아니므로 구조적으로 안전하다.
    """
    cap = (caption or "").strip()
    if not cap:
        return cap, 0
    first = parse_caption(cap, min_desc=1)
    if not first:
        return cap, 0
    dropped = 0
    desc = first["desc"]
    while True:
        nxt = parse_caption(desc, min_desc=1)
        if not nxt:
            break
        same_kind = nxt["kind"] == first["kind"]
        same_num = (nxt["raw"] or "").upper() == (first["raw"] or "").upper()
        if not (same_kind and same_num):
            break
        desc = nxt["desc"]
        dropped += 1
    if not dropped:
        return cap, 0
    return f'{first["head"]}. {desc}'.strip(), dropped


# ── 유사도(길이에 강건) ──────────────────────────────────────────────
def _key(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


def caption_similarity(a: str, b: str) -> float:
    """캡션 설명끼리의 유사도(0~100).

    한쪽이 본문까지 물고 와 훨씬 길어지는 일이 잦아 token_set/token_sort 는
    못 쓴다. (1) 짧은 쪽 길이에 맞춘 접두 비교와 (2) 부분 문자열 비교 중
    높은 쪽을 쓴다 — 앞머리에 잡음이 붙은 캡션도 (2)가 건진다.
    """
    from rapidfuzz import fuzz         # 무거운 import 는 함수 안에서

    ka, kb = _key(a), _key(b)
    k = min(len(ka), len(kb), 160)
    prefix = fuzz.ratio(ka[:k], kb[:k]) if k >= 20 else 0.0
    short, long = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    partial = fuzz.partial_ratio(short[:300], long[:600]) if len(short) >= 20 else 0.0
    return float(max(prefix, partial))


def _desc_of(caption: str) -> str:
    p = parse_caption(caption or "", min_desc=1)
    return p["desc"] if p else (caption or "")


def _num_of(caption: str) -> int | None:
    p = parse_caption(caption or "", min_desc=1)
    if not p or p["supp"] or p["unnumbered"]:
        return None
    return p["num"]


# ── 페이지 결속(캡션이 빈 표 전용) ───────────────────────────────────
def _table_cells(markdown: str, limit: int = 14) -> list[str]:
    """표에서 페이지 탐색에 쓸 만한 셀 값(글자가 든 5자 이상)을 앞에서부터 모은다."""
    out: list[str] = []
    for line in (markdown or "").splitlines():
        if not line.strip() or set(line.strip()) <= set("|- "):
            continue
        for cell in line.split("|"):
            cell = cell.strip()
            if len(cell) >= 5 and re.search(r"[A-Za-z]", cell):
                out.append(cell)
        if len(out) >= limit:
            break
    return out[:limit]


def locate_item_page(markdown: str, pages: list[str]) -> int | None:
    """표 셀 값이 가장 많이 찍힌 PDF 쪽. 여러 쪽에 걸치면 **시작 쪽**을 준다."""
    cells = _table_cells(markdown)
    if len(cells) < 5:
        return None
    flat = [_WS_RE.sub(" ", p.replace("\n", " ")) for p in pages]
    needles = [_WS_RE.sub(" ", c) for c in cells]
    votes = [sum(1 for nd in needles if nd in pg) for pg in flat]
    top = max(votes) if votes else 0
    if top < PAGE_VOTE_MIN:
        return None
    return votes.index(top)               # 동률이면 가장 앞 쪽 = 표가 시작한 쪽


# ── 정본 항목 ↔ PDF 번호 정렬 ────────────────────────────────────────
def _is_empty_caption(item: dict) -> bool:
    return len((item.get("caption") or "").strip()) < EMPTY_CAPTION_CHARS


def ensure_item_ids(items: list) -> list[dict]:
    """id 가 없거나 dict 가 아닌 항목이 섞여 와도 죽지 않게 다듬는다(제자리 보정).

    정본 스키마는 id 를 요구하지만 새 코퍼스에서 파서가 빠뜨릴 수 있다. 없으면
    자리 번호로 임시 id 를 주고, dict 가 아닌 항목은 버린다(감사·수리 대상이 아니다).
    """
    out: list[dict] = []
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        if not it.get("id"):
            it["id"] = f"item_{i}"
        out.append(it)
    return out


def align_items(items: list[dict], pdf_caps: dict[str, str],
                *, pdf_text: str | None = None, kind: str = "tab",
                fuzzy_cutoff: float = FUZZY_CUTOFF) -> dict[str, int | None]:
    """정본 figures/tables 각 항목을 PDF 캡션 번호에 정렬한다(3단계).

    1) 항목 캡션이 스스로 번호를 갖고 그 번호가 PDF 에도 있으면 그 번호.
    2) 캡션 설명 퍼지 일치의 argmax 가 fuzzy_cutoff 이상이면 그 번호.
    3) pdf_text 가 주어지고 kind=='tab' 이면, **캡션이 빈 표**에 한해 셀 값이
       찍힌 쪽을 찾아 그 쪽의 미배정 캡션이 정확히 하나일 때 그 번호.
    이미 배정된 번호는 재사용하지 않는다. 못 붙으면 None(= 과잉 후보).
    """
    assigned: dict[str, int | None] = {}
    taken: set[int] = set()
    pool = {int(k): _desc_of(v) for k, v in pdf_caps.items() if str(k).isdigit()}
    items = ensure_item_ids(items)

    for it in items:                                     # 1) 번호 직접 보유
        n = _num_of(it.get("caption") or "")
        if n is not None and n in pool and n not in taken:
            assigned[it["id"]] = n
            taken.add(n)
        else:
            assigned[it["id"]] = None

    for it in items:                                     # 2) 캡션 퍼지 일치
        if assigned[it["id"]] is not None:
            continue
        desc = _desc_of(it.get("caption") or "")
        if len(desc) < 12:
            continue
        best, best_n = 0.0, None
        for n, pdesc in pool.items():
            if n in taken:
                continue
            sc = caption_similarity(desc, pdesc)
            if sc > best:
                best, best_n = sc, n
        if best_n is not None and best >= fuzzy_cutoff:
            assigned[it["id"]] = best_n
            taken.add(best_n)

    if pdf_text is None or kind != "tab":                # 3) 페이지 결속
        return assigned
    index = [c for c in pdf_caption_index(pdf_text)
             if c["kind"] == "tab" and not c["supp"] and c["num"] is not None]
    pages = _page_texts(pdf_text)
    for it in items:
        if assigned[it["id"]] is not None or not _is_empty_caption(it):
            continue
        pno = locate_item_page(it.get("markdown") or "", pages)
        if pno is None:
            continue
        here = {c["num"] for c in index if c["page"] == pno and c["num"] not in taken}
        if len(here) == 1:
            n = here.pop()
            assigned[it["id"]] = n
            taken.add(n)
    return assigned


# ── 주제 일치 가드 ───────────────────────────────────────────────────
_STOP = frozenset("""the and for with from that this those these was were are is be been
being not but which who whom whose their there here into over under after before during
between among also than then when where while about above below such each other others any
all both more most some few many much very can may might will would should could has have
had did does do per via versus based using used use shown show showed according
respectively total number group groups patient patients study studies data results result
analysis analyses table tables figure figures fig figs mean median standard deviation error
confidence interval ratio rate rates value values score scores level levels time times year
years month months week weeks day days case cases control controls baseline follow first
second third""".split())


def document_vocabulary(doc: dict) -> frozenset[str]:
    """문서가 실제로 쓰는 낱말 집합(제목·초록·본문·표·그림·참고문헌 제목)."""
    parts: list[str] = [(doc.get("meta") or {}).get("title") or "",
                        doc.get("abstract") or ""]
    for sec in doc.get("body_text") or []:
        parts.append(" ".join(x for x in (sec.get("path") or []) if x))
        for p in sec.get("paragraphs") or []:
            parts.append(p.get("text") or "")
    for t in doc.get("tables") or []:
        parts.append((t.get("caption") or "") + " " + (t.get("markdown") or ""))
    for f in doc.get("figures") or []:
        parts.append(f.get("caption") or "")
    for r in doc.get("references") or []:
        parts.append(r.get("title") or "")
    return frozenset(re.findall(r"[a-z]{3,}", " ".join(parts).lower()))


def topical_coverage(caption: str, vocab: frozenset[str]) -> float:
    """캡션 내용어 중 문서가 실제로 쓰는 낱말의 비율(0~1).

    레터·짧은 논문 PDF 에는 같은 지면에 실린 **이웃 논문**의 캡션이 섞여 든다.
    167편 실측에서 그런 캡션은 일치율 0.00, 진짜 캡션은 최저 0.41 로 깨끗이 갈렸다.
    """
    words = {w for w in re.findall(r"[a-z]{4,}", (caption or "").lower())
             if w not in _STOP}
    if not words:
        return 1.0                      # 판정할 근거가 없으면 막지 않는다
    return sum(1 for w in words if w in vocab) / len(words)


# ── PDF 찾기 ─────────────────────────────────────────────────────────
def find_pdf(doc: dict, pdf_dir: str | Path) -> Path | None:
    """정본의 source_file 이 다른 PC 경로여도 **파일명만** 떼어 pdf_dir 에서 찾는다."""
    import glob as _glob

    raw = (doc.get("source_file") or (doc.get("meta") or {}).get("source_file") or "")
    name = re.split(r"[\\/]", str(raw))[-1].strip()
    if not name:
        return None
    base = Path(pdf_dir)
    cand = base / name
    if cand.exists():
        return cand
    # 파일명에 * ? [ 가 들어 있으면 rglob 이 **패턴**으로 읽어 엉뚱한 PDF 를 물어온다
    # ('*.pdf' → 폴더의 첫 PDF). 그 문서에 남의 논문 캡션이 통째로 들어가므로 막는다.
    for p in base.rglob(_glob.escape(name)):
        return p
    return None


# ── 문서 단위 감사 ───────────────────────────────────────────────────
def audit_document(doc: dict, pdf_path: str | Path,
                   pdf_text: str | None = None, *,
                   geometry: bool = True) -> dict[str, Any]:
    """PDF 대비 그림·표의 누락/과잉/캡션 중복을 진단한다(문서를 바꾸지 않는다).

    geometry=True(기본)면 캡션 목록을 captions.py 의 좌표·글꼴 근거 추출로 만든다.
    이때 **이 논문 구간 밖(이웃 논문)의 캡션은 후보에서 빠진다**.
    """
    text = pdf_text if pdf_text is not None else pdf_blocks_text(pdf_path)
    ts = _typeset_flag(doc)
    if geometry:
        try:
            index = geometry_caption_index(pdf_path, doc, typeset=ts)
        except Exception as e:                      # noqa: BLE001
            log(f"  captions.py 실패 → 블록 텍스트로 되돌림: {type(e).__name__}: {e}")
            index = pdf_caption_index(text)
    else:
        index = pdf_caption_index(text)
    caps = {"figures": _collect(index, "fig", supplementary=False, typeset=ts),
            "tables": _collect(index, "tab", supplementary=False, typeset=ts)}
    supp = {"figures": _collect(index, "fig", supplementary=True, typeset=ts),
            "tables": _collect(index, "tab", supplementary=True, typeset=ts)}
    ambig = {"figures": ambiguous_numbers(index, "fig"),
             "tables": ambiguous_numbers(index, "tab")}
    vocab = document_vocabulary(doc)

    rep: dict[str, Any] = {"paper_id": doc.get("paper_id"),
                           "source": doc.get("source"),
                           "pdf": Path(pdf_path).name}
    for key, kind in (("figures", "fig"), ("tables", "tab")):
        items = ensure_item_ids(doc.get(key) or [])
        aligned = align_items(items, caps[key], pdf_text=text, kind=kind)
        pdf_nums = sorted(int(k) for k in caps[key])
        matched = sorted({n for n in aligned.values() if n is not None})
        missing = [n for n in pdf_nums if n not in matched]
        unmatched = [i for i, n in aligned.items() if n is None]
        # 과잉 후보 중 보조자료(Appendix Table A1 등)로 설명되는 것은 따로 센다
        by_id = {it["id"]: it for it in items}
        supp_like = [i for i in unmatched
                     if any(caption_similarity(_desc_of(by_id[i].get("caption") or ""),
                                               _desc_of(v)) >= FUZZY_CUTOFF
                            for v in supp[key].values())]
        low_topical = {str(n): round(topical_coverage(caps[key][str(n)], vocab), 2)
                       for n in missing
                       if topical_coverage(caps[key][str(n)], vocab) < TOPICAL_CUTOFF}
        rep[key] = {
            "pdf_nums": pdf_nums, "n_pdf": len(pdf_nums), "n_doc": len(items),
            "matched": matched, "missing": missing,
            "unmatched_ids": unmatched, "supplementary_ids": supp_like,
            "empty_caption_ids": [it["id"] for it in items if _is_empty_caption(it)],
            "n_missing": len(missing),
            "n_extra": len(unmatched) - len(supp_like),
            "dup_prefix": sum(1 for it in items
                              if strip_duplicate_prefix(it.get("caption") or "")[1]),
            "n_supp_pdf": len(supp[key]),
            "missing_captions": {str(n): caps[key][str(n)] for n in missing},
            "missing_low_topical": low_topical,
            "ambiguous_nums": sorted(ambig[key]),
        }
    rep["ok"] = all(not rep[k][f] for k in ("figures", "tables")
                    for f in ("missing", "unmatched_ids", "dup_prefix"))
    return rep


# ── 문서 단위 수리 ───────────────────────────────────────────────────
def _new_item(kind: str, num: int, caption: str) -> dict[str, Any]:
    item: dict[str, Any] = {"id": f"{kind}_pdf{num}", "caption": caption}
    if kind == "tab":
        item["markdown"] = ""
    else:
        item["image"] = None
    item["origin"] = "figtab_pdf"      # 사후 보충 표시(감사 추적용)
    return item


def _merge_continued(items: list[dict]) -> tuple[list[dict], int]:
    """'Continued' 로 쪼개진 표를 앞 표에 합친다.

    합치는 조건은 둘 중 하나뿐이다 — (a) 캡션이 이어짐 표시 하나뿐이거나
    (b) 캡션에서 이어짐 꼬리를 떼면 바로 앞 표의 캡션과 사실상 같을 것.
    그 밖의 과잉은 손대지 않는다(과잉은 누락보다 덜 해롭다).
    """
    out: list[dict] = []
    merged = 0
    for it in items:
        cap = (it.get("caption") or "").strip()
        prev = out[-1] if out else None
        joinable = False
        if prev is not None and cap:
            if _CONT_ONLY_RE.match(cap):
                joinable = True
            else:
                head = _CONT_TAIL_RE.sub("", cap).strip()
                if head and head != cap:
                    joinable = (caption_similarity(
                        _desc_of(head), _desc_of(prev.get("caption") or "")) >= 90)
        elif prev is not None and not cap:
            joinable = False
        if joinable:
            body = "\n".join(x for x in ((prev.get("markdown") or "").rstrip(),
                                         (it.get("markdown") or "").lstrip()) if x)
            prev["markdown"] = body
            merged += 1
            continue
        out.append(it)
    return out, merged


def repair_document(doc: dict, pdf_path: str | Path,
                    pdf_text: str | None = None, *,
                    geometry: bool = True) -> tuple[dict, dict]:
    """캡션 접두 중복 제거 + PDF 에만 있는 그림/표 보충. (새 문서, 통계) 를 낸다.

    수리 순서
      1. 캡션 접두 중복 제거
      2. 'Continued' 로 쪼개진 표 병합
      3. PDF 번호에 정렬 → 캡션이 빈 표에는 페이지 결속으로 찾은 캡션을 채운다
      4. 남은 누락 번호를 채운다. 캡션이 없는 그림 껍데기(figN)가 있으면 **그 자리에**
         채우고(정보 손실 0), 모자라면 새 항목(fig_pdfN/tab_pdfN)을 덧붙인다.
    과잉(정본에만 있는 항목)은 지우지 않는다 — 보고만 한다.

    geometry=True(기본)면 캡션 목록을 captions.py 에서 받는다. 이 모듈이 파이프라인에
    연결되지 못했던 이유(합본 지면에서 **이웃 논문 표를 가져다 붙임** — 실증
    10.1016/j.jaad.2016.05.022 에 이웃 레터 …2016.05.014 의 'Table II. Final diagnoses
    made by the consulting inpatient dermatology team')가 여기서 막힌다.
    captions.owned_captions 가 그 캡션을 이 논문 구간 밖으로 판정해 후보에서 뺀다.
    """
    import copy

    out = copy.deepcopy(doc)
    text = pdf_text if pdf_text is not None else pdf_blocks_text(pdf_path)
    ts = _typeset_flag(doc)
    if geometry:
        try:
            index = geometry_caption_index(pdf_path, doc, typeset=ts)
        except Exception as e:                      # noqa: BLE001
            log(f"  captions.py 실패 → 블록 텍스트로 되돌림: {type(e).__name__}: {e}")
            index = pdf_caption_index(text)
    else:
        index = pdf_caption_index(text)
    caps = {"figures": _collect(index, "fig", supplementary=False, typeset=ts),
            "tables": _collect(index, "tab", supplementary=False, typeset=ts)}
    ambig = {"figures": ambiguous_numbers(index, "fig"),
             "tables": ambiguous_numbers(index, "tab")}
    vocab = document_vocabulary(doc)
    order_before = {k: [it.get("id") for it in (out.get(k) or [])
                        if isinstance(it, dict)] for k in ("figures", "tables")}

    st: dict[str, Any] = {"paper_id": doc.get("paper_id"), "source": doc.get("source"),
                          "pdf": Path(pdf_path).name, "dup_prefix_fixed": 0,
                          "figures_added": [], "tables_added": [],
                          "figures_filled": [], "tables_filled": [],
                          "tables_merged": 0, "skipped_low_topical": [],
                          "skipped_duplicate": [], "skipped_ambiguous": [],
                          "reordered": [],
                          "figures_extra": 0, "tables_extra": 0, "changed": False}

    for key, kind in (("figures", "fig"), ("tables", "tab")):
        items = ensure_item_ids(out.get(key) or [])
        for it in items:                                        # 1. 중복 접두
            fixed, n = strip_duplicate_prefix(it.get("caption") or "")
            if n:
                it["caption"] = fixed
                st["dup_prefix_fixed"] += n
        if kind == "tab":                                       # 2. 이어짐 병합
            items, merged = _merge_continued(items)
            st["tables_merged"] = merged
        aligned = align_items(items, caps[key], pdf_text=text, kind=kind)
        by_id = {it["id"]: it for it in items}
        for iid, n in aligned.items():                          # 3. 빈 캡션 채우기
            if n is None or not _is_empty_caption(by_id[iid]):
                continue
            by_id[iid]["caption"] = caps[key][str(n)]
            by_id[iid]["origin"] = "figtab_pdf_caption"
            st[f"{key}_filled"].append(n)
        taken = {n for n in aligned.values() if n is not None}
        # 4. 남은 누락 보충 — 빈 껍데기(캡션도 이미지/본문도 없는 항목) 우선 재사용
        shells = [it for it in items
                  if aligned.get(it["id"]) is None and _is_empty_caption(it)
                  and not (it.get("image") or (it.get("markdown") or "").strip())]
        added: list[int] = []
        # 이미 문서 어딘가(그림/표 어느 쪽이든)에 같은 캡션이 있으면 보충하지 않는다.
        # GROBID 가 표 캡션을 그림 항목에 붙여 놓은 문서에서 같은 글이 두 번
        # 들어가는 것을 막는다(167편 실측 3건).
        # 판정은 **정규화 후 완전일치**로만 한다. 퍼지로 하면 'Expert consensus
        # recommendations for TCSs/WWT/TCIs' 처럼 틀만 같은 다른 표까지 막힌다(실측 15건).
        existing = {_key(_desc_of(it.get("caption") or ""))
                    for k2 in ("figures", "tables") for it in (out.get(k2) or [])
                    if isinstance(it, dict) and len((it.get("caption") or "").strip()) >= 20}
        existing |= {_key(_desc_of(it.get("caption") or "")) for it in items
                     if len((it.get("caption") or "").strip()) >= 20}
        existing.discard("")
        for skey in sorted(caps[key], key=lambda k: int(k)):
            n = int(skey)
            if n in taken:
                continue
            if n in ambig[key]:                    # 같은 번호를 두 캡션이 다툰다
                st["skipped_ambiguous"].append(
                    {"kind": key, "num": n, "caption": caps[key][skey][:200]})
                continue
            cov = topical_coverage(caps[key][skey], vocab)
            if cov < TOPICAL_CUTOFF:
                st["skipped_low_topical"].append(
                    {"kind": key, "num": n, "coverage": round(cov, 2),
                     "caption": caps[key][skey][:200]})
                continue
            cand = _key(_desc_of(caps[key][skey]))
            if cand and cand in existing:
                st["skipped_duplicate"].append(
                    {"kind": key, "num": n, "caption": caps[key][skey][:200]})
                continue
            if shells:
                shell = shells.pop(0)
                shell["caption"] = caps[key][skey]
                shell["origin"] = "figtab_pdf_caption"
                aligned[shell["id"]] = n
            else:
                item = _new_item(kind, n, caps[key][skey])
                items.append(item)
                aligned[item["id"]] = n
            taken.add(n)
            added.append(n)
            existing.add(cand)
        st[f"{key}_added"] = added
        st[f"{key}_extra"] = sum(1 for n in aligned.values() if n is None)
        items.sort(key=lambda it: (0, aligned[it["id"]]) if aligned.get(it["id"])
                   is not None else (1, 0))
        out[key] = items
        # 정렬만으로도 문서는 바뀐다 — changed 에 반영하지 않으면 run() 이 그 변경을
        # 저장하지 않아 '메모리 결과 ≠ 디스크 결과' 가 된다(167편 실측 2편).
        if [it.get("id") for it in items] != order_before[key]:
            st["reordered"].append(key)

    st["changed"] = bool(st["dup_prefix_fixed"] or st["figures_added"]
                         or st["tables_added"] or st["figures_filled"]
                         or st["tables_filled"] or st["tables_merged"]
                         or st["reordered"])
    return out, st


# ── 진입점 ───────────────────────────────────────────────────────────
def run(config: dict | None = None, *, dry_run: bool = True) -> None:
    """정본 전편을 PDF 와 대조해 감사하고(기본) 수리한다.

    **기본값이 dry_run=True 다.** data/normalized/ 는 총괄이 검토한 뒤에만 덮어쓴다.
    dry_run 이면 리포트(figtab_report.jsonl)만 쓰고 정본은 건드리지 않는다.
    """
    cfg = config or utils.load_config()
    opts = (cfg.get("figtab") or {}) if isinstance(cfg, dict) else {}
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"
    pdf_dir = utils.resolve(opts.get("pdf_dir") or cfg["project"]["input_dir"])
    report_path = work / (opts.get("report") or "figtab_report.jsonl")

    files = sorted(norm_dir.glob("*.json"))
    log(f"[그림·표] PDF 대조 감사: {len(files)}편 @ {norm_dir}"
        + ("  (DRY-RUN: 정본을 쓰지 않는다)" if dry_run else ""))
    if not files:
        log(f"        → 정본 문서가 없다. 0~4단계를 먼저 실행할 것: {norm_dir}")
        return

    reports: list[dict] = []
    n_changed = n_nopdf = failed = 0
    tot = {"dup": 0, "fig_add": 0, "tab_add": 0, "fig_fill": 0, "tab_fill": 0,
           "merged": 0, "skipped": 0, "fig_extra": 0, "tab_extra": 0}
    for i, src in enumerate(files, 1):
        try:
            doc = utils.read_json(src)
            pdf = find_pdf(doc, pdf_dir)
            if pdf is None:
                n_nopdf += 1
                reports.append({"paper_id": doc.get("paper_id"),
                                "skipped": "pdf_not_found"})
                continue
            text = pdf_blocks_text(pdf)
            audit = audit_document(doc, pdf, pdf_text=text)
            fixed, st = repair_document(doc, pdf, pdf_text=text)
            reports.append({**st, "audit": audit})
            tot["dup"] += st["dup_prefix_fixed"]
            tot["fig_add"] += len(st["figures_added"])
            tot["tab_add"] += len(st["tables_added"])
            tot["fig_fill"] += len(st["figures_filled"])
            tot["tab_fill"] += len(st["tables_filled"])
            tot["merged"] += st["tables_merged"]
            tot["skipped"] += len(st["skipped_low_topical"])
            tot["fig_extra"] += st["figures_extra"]
            tot["tab_extra"] += st["tables_extra"]
            if st["changed"]:
                n_changed += 1
                if not dry_run:
                    utils.write_json(src, fixed)
                log(f"  [{i}/{len(files)}] {st['paper_id']}: 중복접두 {st['dup_prefix_fixed']} · "
                    f"그림보충 {st['figures_added']} · 표보충 {st['tables_added']} · "
                    f"캡션채움 {st['figures_filled'] + st['tables_filled']} · "
                    f"표병합 {st['tables_merged']}")
        except Exception as e:                    # noqa: BLE001 — 파일 단위 격리
            failed += 1
            log(f"  [{i}/{len(files)}] 감사 실패({src.name}): {type(e).__name__}: {e}")

    utils.write_jsonl(report_path, reports)
    log(f"[그림·표] 완료 (수리대상 {n_changed}/{len(files)}, PDF 없음 {n_nopdf}, 실패 {failed})  "
        f"[중복접두 {tot['dup']} · 그림보충 {tot['fig_add']} · 표보충 {tot['tab_add']} · "
        f"캡션채움 {tot['fig_fill'] + tot['tab_fill']} · 표병합 {tot['merged']} · "
        f"주제불일치로 보류 {tot['skipped']} · 과잉 그림 {tot['fig_extra']}/표 {tot['tab_extra']}]")
    log(f"[그림·표] 리포트 {report_path}"
        + ("  ※ DRY-RUN 이라 정본은 그대로다" if dry_run else ""))


if __name__ == "__main__":
    run()
