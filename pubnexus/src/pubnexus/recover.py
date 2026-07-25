"""4.6단계 — GROBID 가 버린 본문 앞부분을 원본 PDF 에서 되살린다.

증상: TEI 에 문단 **앞부분이 통째로 없다**. 실측 167편에서 68건·49편(29%)이
해당하고 전부 grobid 경로다(pmc_xml 0건). 원인은 드롭캡(첫 글자 장식 조판)과
컬럼/박스 조판이며, 우리 파서가 아니라 GROBID 가 이미 버린 것이라 TEI 를
다시 읽어도 나오지 않는다. **그러나 원문 PDF 에는 그대로 남아 있다.**

전략: 잘린 문단의 앞부분 문자열을 PDF 텍스트에서 찾고, 그 **앞쪽**으로
거슬러 올라가 사라진 문장들을 되살린다.

다만 실측해 보면 '소문자로 시작하는 문단'은 두 부류였고, 둘을 먼저 갈라야 한다.
  · 본문 유실   — PDF 에는 있는데 TEI 에 없다 → PDF 에서 되살린다.
  · 문단 쪼개짐 — 직전 문단이 PDF 에서 곧바로 이어진다. 사라진 글자가 없으므로
                  되살릴 것이 없고 **합쳐야** 원상복구된다. 되살리려 들면
                  반드시 중복이 된다. (실측 68건 중 33건이 이쪽이었다.)

오염 방지가 최우선이라 다음 순서로 방어한다.
  (1) PDF 라인을 세 갈래로 분류한다.
      · keep    — 본문
      · drop    — 러닝헤더/페이지번호/저작권/소속/워터마크/캡션/키워드 목록 등
                  **본문 흐름의 끼어들기**. 지우되 흔적을 남기지 않는다
                  (지우고 이어붙여야 페이지를 넘어가는 문단이 다시 연결된다).
      · barrier — 섹션 헤딩·참고문헌처럼 **본문의 경계**. 여기서 거슬러
                  올라가기를 멈춘다(\\x00 로 표시).
  (2) 되살릴 범위는 (직전 문단의 끝 / 최근 barrier / max_chars) 중 가장 늦은
      지점까지로 자른다. '직전 문단'은 문서 순서가 아니라 **PDF 에서 실제로
      바로 앞에 오는 문단**을 찾아 쓴다(표 각주가 문단으로 끼어든 문서가 많다).
  (3) 문장 단위로 뒤에서부터 채우다가 중복(문서에 이미 있는 문장)·잡음
      (저널명·페이지·소속·캡션·표 셀)을 만나면 **거기서 멈춘다**.
  (4) 마지막 불변식: 잘린 문단은 정의상 문장 중간에서 시작하므로,
      되살린 텍스트는 **문장 종결부호로 끝나면 안 된다**. 끝나면 이어붙일
      자리가 아니라는 뜻이라 통째로 버린다(안전 실패).

되살린 문단에는 recovered=true / recovered_chars=N 을, 합친 문단에는
merged_split=true / merged_ids=[…] 를 남겨 추적·되돌리기가 가능하게 한다.
기존 공개 API 는 건드리지 않고 이 모듈만 추가한다.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from . import utils
from .utils import log, norm_text

# 본문 흐름의 경계 표시(정규화 비교에서 자동으로 무시되도록 비문자 1글자).
BARRIER = "\x00"

# ── 잘린 문단 탐지 ──────────────────────────────────────────────────
# 소문자로 시작 = 앞 문장이 통째로 사라진 것. 정상 문단은 대문자·숫자·인용부호로 시작한다.
_BAD_START = re.compile(r"^\s*[a-z]")
# 통계·단위 표기는 정상적으로 소문자로 시작할 수 있다(오탐 제외).
_ALLOW_START = re.compile(
    r"^\s*(?:p\s*[<=>]|n\s*=|vs\.?|et al|mg|ml|cm|mm|µ|α|β|γ)", re.I)

# ── PDF 라인 분류 ───────────────────────────────────────────────────
# 본문 흐름에 끼어드는 조판 부산물. **지우되 경계로 삼지 않는다**
# (경계로 삼으면 페이지 꼬리말 뒤로 이어지는 문단을 영영 잇지 못한다).
_DROP_LINE = re.compile(
    r"^(?:"
    r"downloaded\s+(?:from|by|for)\b"
    r"|©|\(c\)\s*\d{4}\b"
    r"|copyright\b"
    r"|all\s+rights\s+reserved\b"
    r"|https?://|www\."
    r"|doi:\s*10\.|https?://doi\.org"
    r"|e-?mail\s*:"
    r"|correspondence\b|corresponding\s+author\b"
    r"|reprints?\b"
    r"|(?:received|accepted|revised|submitted)\b[^.]{0,30}\d{4}\b"
    r"|received\s*:|accepted\s+for\s+publication\b|accepted\s*:|revised\s*:"
    r"|first\s+published\b|available\s+online\b|published\s+online\b"
    r"|conflicts?\s+of\s+interest\b|competing\s+interests?\b"
    r"|funding\s+(?:sources?|information|statement)\b"
    r"|grant\s*/\s*award|grant\s+number\b"
    r"|from\s+the\s+(?:department|division|institute|center|centre|college|school|unit)\b"
    r"|k\s?e\s?y\s?\s?w\s?o\s?r\s?d\s?s?\b"   # 'Key words:' / 자간 조판 'K E Y W O R D S'
    r"|abbreviations?\s+used\b|abbreviations?\s*:"
    r"|capsule\s+summary\b"
    r"|orcid\b|issn\b"
    r"|this\s+article\s+is\s+protected\s+by\s+copyright"
    r"|see\s+the\s+terms\s+and\s+conditions\b"
    r"|creative\s+commons\b|open\s+access\b|licen[sc]e\b"
    r"|video\s+available\s+at\b"
    r"|how\s+to\s+cite\b"
    r"|\d{4}-\d{3}[\dx]\s*/\s*\$"          # 0190-9622/$36.00
    r")", re.I)

# 줄 어디에 있든 잡아야 하는 워터마크(회전 삽입되어 본문 줄 끝에 붙는다)
_DROP_ANY = re.compile(
    r"\bdownloaded\s+(?:from|by|for)\b|\bprotected\s+by\s+copyright\b|"
    r"\bfirst\s+published\s+as\b|\bby\s+guest\b|\bwiley\s+online\s+library\b|"
    r"\bcreativecommons\.org\b|\bonlinelibrary\.wiley\.com\b", re.I)

# 페이지 번호만 있는 줄 / 'e123' 같은 전자페이지 / '12 of 20'
_PAGENUM_LINE = re.compile(
    r"^(?:[ivxlcdm]{1,7}|e?\d{1,5}|\d{1,4}\s*(?:of|/|\|)\s*\d{1,4})$", re.I)
# 그림 패널 라벨만 있는 줄: '(a)' / '(a) (b) (c)' (그림 위에 흩어져 조판된다)
_PANEL_LINE = re.compile(r"^(?:\(?[a-z]\)[\s,]*)+$")

# 그림/표 캡션의 머리. 자간 조판('FI G U R E 3')까지 잡는다.
_CAPTION_HEAD = re.compile(
    r"^(?:f\s?i\s?g(?:\s?u\s?r\s?e)?s?|t\s?a\s?b(?:\s?l\s?e)?s?|c\s?h\s?a\s?r\s?t|"
    r"s\s?c\s?h\s?e\s?m\s?e|b\s?o\s?x|a\s?p\s?p\s?e\s?n\s?d\s?i\s?x|"
    r"e\s?x\s?h\s?i\s?b\s?i\s?t|supplement(?:ary|al)?\s+(?:fig(?:ure)?|table|data))"
    r"\s*\.?\s*(?:\d{1,3}[a-z]?|[ivxlc]{1,5})\b", re.I)


def _is_caption_line(t: str) -> bool:
    """그림/표 캡션의 첫 줄인가.

    'Table 1 shows the characteristics …'(정상 본문)와 'Table 1. Clinical …',
    'FI G U R E 3 Network graph …'(캡션)를 가른다 — 번호 뒤가 구두점이거나
    대문자로 시작하거나 줄이 거기서 끝나면 캡션으로 본다.
    """
    m = _CAPTION_HEAD.match(t or "")
    if not m:
        return False
    rest = t[m.end():]
    if not rest.strip():
        return True
    if rest[:1] in ".:|)":
        return True
    return bool(re.match(r"\s+[A-Z(]", rest))

# 참고문헌 목록. **여기부터 문서 끝까지 버리면 안 된다** — 한 PDF 에 레터가
# 두 편 실린 경우(앞 편의 REFERENCES 뒤에 목표 논문이 온다) 본문을 통째로
# 잃는다(실측 확인). 그래서 헤딩만 경계로 삼고 '참고문헌처럼 생긴 줄'만 버린다.
_REFS_HEAD = re.compile(
    r"^(?:references?|bibliography|literature\s+cited|reference\s+list)\s*:?\s*$", re.I)
_REF_ENTRY = re.compile(r"^(?:\d{1,3}[.)]\s+\S|\[\d{1,3}\]\s*\S)")
_REF_ISH = re.compile(r"\d{4}\s*[;:]\s*\d+|\bet al\b|\bdoi\b|\bPubMed\b", re.I)
# JAAD 'CAPSULE SUMMARY' 박스 글머리(심볼폰트 'd' 가 네모로 조판된다)
_BULLET_LINE = re.compile(r"^d\s+[A-Z]")
# 라벨 다음 줄에 목록이 이어지는 부속(키워드·약어·박스) — 꼬리까지 함께 지운다
_LABEL_LINE = re.compile(
    r"^(?:k\s?e\s?y\s?\s?w\s?o\s?r\s?d\s?s?\b|abbreviations?\b|"
    r"capsule\s+summary\b|highlights?\s*$|what[’']?s\s+already\s+known\b)", re.I)

# ── 되살린 조각에서 걸러낼 잡음 문장 ────────────────────────────────
_JUNK_SENT = [
    re.compile(r"\d{4}\s*;\s*\d+"),                       # 'Dermatol 2018;79:720-7'
    re.compile(r"\bdoi\b|https?://|www\.", re.I),
    re.compile(r"\bdownloaded\s+(?:from|by|for)\b", re.I),
    re.compile(r"©|\bcopyright\b|all\s+rights\s+reserved", re.I),
    re.compile(r"\be-?mail\b|[\w.]+@[\w.]+\.\w", re.I),
    re.compile(r"^\s*(?:from\s+the|correspondence|corresponding\s+author|reprints?|"
               r"accepted\s+for\s+publication|received|revised|funding|"
               r"conflicts?\s+of\s+interest|competing\s+interests?|key\s*words?|"
               r"abbreviations?|acknowledg|orcid|capsule\s+summary)\b", re.I),
    re.compile(r"\b(?:department|division|institute|college\s+of\s+medicine|"
               r"school\s+of\s+medicine|university|hospital)\b[^.]{0,60},\s*"
               r"[A-Z][a-z]+\s*$"),                        # 소속 줄
    re.compile(r"^\s*(?:fig(?:ure)?s?|tab(?:le)?s?|chart|scheme|box|appendix)\s*\.?\s*"
               r"(?:\d{1,3}[a-z]?|[ivxlc]{1,5})\s*[.:]", re.I),
    re.compile(r"\b\d{4}-\d{3}[\dx]\b|\bissn\b", re.I),
    re.compile(r"^\s*[A-Z][A-Za-z.'’\- ]{1,30}\bet\s+al\.?\s*\d*\s*$"),  # 러닝 푸터
    re.compile(r"(?:\(?[a-z]\)[\s,]*){3,}"),                # 그림 패널 라벨 '(a) (b) (c)'
]

# 문장 종결 판정에서 제외할 약어(문장 중간에 마침표가 오는 것들)
_ABBR = re.compile(
    r"^(?:e\.g|i\.e|vs|etc|cf|fig|figs|tab|tabs|dr|drs|mr|mrs|ms|prof|no|nos|"
    r"al|approx|st|inc|ltd|co|corp|ca|resp|viz|ref|refs|eq|eqs|suppl|vol|"
    r"jr|sr|ph|dept|univ|min|max|sec|hr|hrs|wk|wks|mo|mos|yr|yrs)$", re.I)

_SENT_BREAK = re.compile(r"[.!?][\"'’”\)\]]*\s+")
_LAST_WORD = re.compile(r"([A-Za-z]+|\d+)\s*$")
_TERMINAL_END = re.compile(r"[.!?][\"'’”\)\]]*\s*$")

# 하이픈으로 끝나는 조각을 이을 때: 줄바꿈 분철인가, 진짜 합성어인가.
#   'charac-' + 'terized' → 분철이므로 하이픈을 지운다.
#   'HA-'     + 'fabricated' → 'HA-fabricated' 는 합성어다. 지우면
#                              'HAfabricated' 라는 없는 낱말이 생긴다(실측 확인).
# 하이픈 앞이 두 글자 이상 전부 대문자/숫자인 토큰이면 합성어로 본다
# (본문에서 약어가 줄바꿈으로 분철되는 일은 사실상 없다).
_ACRONYM_TAIL = re.compile(r"(?:^|[\s(\[/-])[A-Z0-9]{2,}-$")


def _ends_sentence(s: str) -> bool:
    """문단이 **완결된 문장**으로 끝나는가. 'et al.' 같은 약어 마침표는 제외.

    병합 가부의 핵심 불변식이다. 잘린 문단은 정의상 문장 중간에서 시작하므로
    진짜 '쪼개짐'이라면 직전 문단은 문장 중간에서 끝나 있어야 한다.
    """
    t = (s or "").rstrip()
    if not _TERMINAL_END.search(t):
        return False
    core = t.rstrip("\"'’”)]").rstrip()
    if not core.endswith((".", "!", "?")):
        return False
    wm = _LAST_WORD.search(core[:-1])
    return not (wm and _ABBR.match(wm.group(1)))


def _join_hyphen(a: str, b: str) -> str:
    """하이픈으로 끝나는 a 에 b 를 잇는다(분철이면 하이픈 제거, 합성어면 유지)."""
    return (a + b) if _ACRONYM_TAIL.search(a) else (a[:-1] + b)
# 위첨자 인용번호가 폰트 플래그로 잡히지 않고 본문에 남은 경우의 보수적 제거.
# 'melanocytes.1 It' → 'melanocytes. It'. 앞 낱말이 4글자 이상일 때만 손대
# 'Fig.1'·'Vol.2' 를 지키고, 마침표 앞이 숫자면(0.8) 애초에 걸리지 않는다.
_INLINE_CITE = re.compile(r"([A-Za-z]+)([.,;:])\d{1,3}(?:[,–−-]\d{1,3})*(?=\s|\Z)")


def _strip_inline_cite(s: str) -> str:
    def repl(m: re.Match) -> str:
        return m.group(0) if len(m.group(1)) < 4 else m.group(1) + m.group(2)
    return _INLINE_CITE.sub(repl, s)


_CITE_TOKEN = re.compile(r'^[0-9]{1,3}(?:[,\-–][0-9]{1,3})*$')

# 드롭캡 텍스트 보정에서 붙이면 안 되는 정상 낱말(‘A stainless’, ‘T he’ 방지)
_DROPCAP_STOP = frozenset("""
he his her him hers is in it its of on or to the and as at by be we us an no
not for with that this was were are all had has have but she they them their
""".split())
_DROPCAP_TEXT = re.compile(r"^([B-HJ-Z])\s+([a-z]{2,})\b")


# ── 잘린 문단 찾기 ──────────────────────────────────────────────────
def find_truncated(doc: dict) -> list[tuple[int, int]]:
    """앞부분이 잘린 문단의 (섹션idx, 문단idx) 목록.

    총괄이 전수 측정에 쓴 규칙과 동일하다(소문자 시작, 통계표기 제외).
    이미 복원한 문단은 대문자로 시작하므로 자연히 재검출되지 않는다.
    """
    out: list[tuple[int, int]] = []
    for si, sec in enumerate(doc.get("sections") or []):
        for pi, para in enumerate(sec.get("paragraphs") or []):
            t = (para.get("text") or "").strip()
            if not t or not _BAD_START.match(t) or _ALLOW_START.match(t):
                continue
            out.append((si, pi))
    return out


# ── 정규화 비교(공백·하이픈·줄바꿈·위첨자 무시) ─────────────────────
def _letters(s: str) -> str:
    """비교용: 알파벳 소문자만 남긴다(공백·하이픈·숫자·기호 전부 무시)."""
    return "".join(c for c in (s or "").lower() if "a" <= c <= "z")


def _letters_map(s: str) -> tuple[str, list[int]]:
    """알파벳만 남긴 문자열과 원문 오프셋 대응표."""
    keep: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        c = ch.lower()
        if "a" <= c <= "z":
            keep.append(c)
            idx.append(i)
    return "".join(keep), idx


# ── PDF 라인 수집·분류 ──────────────────────────────────────────────
def _line_parts(line: dict, body: float) -> tuple[str, str | None]:
    """한 줄의 span → (본문 텍스트, 드롭캡 글자).

    위첨자 인용번호는 폰트 플래그/크기로 감지해 지우고(GROBID 본문과 표기를
    맞춘다), 본문보다 훨씬 큰 한 글자 span 은 드롭캡으로 떼어 돌려준다.
    """
    parts: list[str] = []
    dropcap: str | None = None
    for sp in line.get("spans") or []:
        t = sp.get("text", "")
        st = t.strip()
        if not st:
            parts.append(t)
            continue
        size = float(sp.get("size", body))
        if (dropcap is None and not "".join(parts).strip()
                and len(st) == 1 and st.isalpha() and st.isupper()
                and size >= body * 1.6):
            dropcap = st                       # 장식 첫 글자(드롭캡)
            continue
        is_super = bool(int(sp.get("flags", 0)) & 1) or size < body * 0.72
        if is_super and _CITE_TOKEN.match(st):
            continue                            # 위첨자 인용번호
        parts.append(t)
    return norm_text("".join(parts)), dropcap


def _running_keys(rows: list[dict]) -> set[str]:
    """여러 페이지에서 반복되는 줄 = 러닝헤더/꼬리말.

    여백(상/하단 12%)에 있으면 2쪽만 반복돼도 러닝헤더로 본다. 여백 밖이면
    3쪽 이상·60자 이하일 때만 인정한다('Review Article' 처럼 본문 단 안쪽에
    조판된 러닝헤더가 실제로 있다 — 남기면 본문에 섞여 들어간다).
    """
    marg: dict[str, set[int]] = {}
    fixed_y: dict[tuple[str, int], set[int]] = {}
    for r in rows:
        key = re.sub(r"\d+", "#", r["text"].lower()).strip()
        if not key or len(key) > 90:
            continue
        if r["margin"]:
            marg.setdefault(key, set()).add(r["page"])
        # 여백 밖 러닝헤더는 **같은 세로 위치에** 반복된다는 조건을 반드시 건다.
        # 조건 없이 '여러 쪽에 반복'만 보면 양끝맞춤 단의 한 낱말 줄('of', 'a',
        # 'vitiligo')이 통째로 지워져 본문이 소리 없이 훼손된다(실측 확인).
        if 12 <= len(key) <= 60 and len(key.split()) >= 2:
            fixed_y.setdefault((key, int(r["y0"] // 6)), set()).add(r["page"])
    out = {k for k, pages in marg.items() if len(pages) >= 2}
    out |= {k for (k, _y), pages in fixed_y.items() if len(pages) >= 3}
    return out


def _pdf_lines(pdf_path: str | Path) -> list[dict]:
    """PDF 전체 라인을 콘텐츠 스트림 순서(= 출판사 PDF 의 읽기순서)로 수집·분류."""
    import fitz                                   # 무거운 import 는 함수 안에서
    from collections import Counter

    doc = fitz.open(str(pdf_path))
    try:
        pages = [p.get_text("dict") for p in doc]
        heights = [doc[i].rect.height for i in range(doc.page_count)]
    finally:
        doc.close()

    # 본문 폰트 크기: '산문처럼 보이는 줄'(30자 이상)의 글자수 최빈값.
    # 표·참고문헌이 많은 문서에서 기준이 흔들리지 않게 한다.
    cnt: Counter = Counter()
    for pd in pages:
        for b in pd["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b.get("lines") or []:
                txt = "".join(sp.get("text", "") for sp in ln.get("spans") or [])
                if len(txt.strip()) < 30:
                    continue
                for sp in ln.get("spans") or []:
                    cnt[round(float(sp.get("size", 10.0)), 1)] += len(sp.get("text", ""))
    if not cnt:                                   # 산문 줄이 없으면 전체로 재집계
        for pd in pages:
            for b in pd["blocks"]:
                if b.get("type") != 0:
                    continue
                for ln in b.get("lines") or []:
                    for sp in ln.get("spans") or []:
                        cnt[round(float(sp.get("size", 10.0)), 1)] += len(sp.get("text", ""))
    # 최빈값을 그대로 쓰면 안 된다. 같은 본문이 8.2/8.3 처럼 0.1 씩 갈려 담기면
    # 표·박스의 7.0 이 최빈이 되어 **본문이 작은 활자로 오인돼 통째로 버려진다**
    # (Nature Reviews Primer 에서 실측 확인). ±0.3 이웃을 합산해 고른다.
    body = 10.0
    if cnt:
        body = max(cnt, key=lambda s: sum(c for z, c in cnt.items() if abs(z - s) <= 0.3))

    rows: list[dict] = []
    for pno, pd in enumerate(pages):
        h = heights[pno] or 1.0
        for bi, b in enumerate(pd["blocks"]):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines") or []:
                if not ln.get("spans"):
                    continue
                text, dropcap = _line_parts(ln, body)
                if not text and not dropcap:
                    continue
                x0, y0, x1, y1 = ln["bbox"]
                size = round(max((float(sp.get("size", body))
                                  for sp in ln["spans"]), default=body), 1)
                names = " ".join(sp.get("font", "") for sp in ln["spans"])
                flag_or = 0
                for sp in ln["spans"]:
                    flag_or |= int(sp.get("flags", 0))
                rows.append({
                    "text": text, "dropcap": dropcap, "size": size,
                    "bold": bool(flag_or & 16) or bool(re.search(
                        r"(bold|black|semibold|heavy)", names, re.I)),
                    "page": pno, "block": bi, "x0": x0, "y0": y0, "y1": y1,
                    "margin": (y1 / h) <= 0.12 or (y0 / h) >= 0.88,
                    "body": body,
                })
    return _classify(rows, body)


def _is_heading_line(r: dict, body: float) -> bool:
    """섹션 헤딩처럼 보이는가(경계 판정용, 넉넉하게 잡는다 = 안전한 방향)."""
    t = r["text"].strip()
    if not (3 <= len(t) <= 90) or len(t.split()) > 8:
        return False
    if t.rstrip().endswith((".", ",", ";", "?")):
        return False
    if sum(c.isdigit() for c in t) > 4:
        return False
    emphasized = r["size"] >= body * 1.05 or r["bold"] or t.isupper()
    return bool(emphasized and t[:1].isupper())


def _continues(prev: dict, cur: dict, body: float) -> bool:
    """두 줄이 같은 덩어리(캡션·키워드 목록 등)로 이어지는가 — 기하 판정.

    출판사에 따라 PyMuPDF 블록이 '한 줄=한 블록'으로 쪼개져(Wiley) 블록 단위
    처리가 무력해진다. 그래서 세로 간격·좌측 정렬로 이어짐을 본다.
    """
    if prev["page"] != cur["page"]:
        return False
    gap = cur["y0"] - prev["y1"]
    return -body <= gap <= body * 1.8 and abs(cur["x0"] - prev["x0"]) <= body * 4


def _classify(rows: list[dict], body: float) -> list[dict]:
    """각 줄에 kind = keep | drop | barrier 를 매긴다.

    판정 순서가 중요하다.
      · **반복 러닝헤더를 헤딩보다 먼저** 본다 — 'J AM ACAD DERMATOL' 같은
        굵은 러닝헤더를 헤딩(경계)으로 오인하면 페이지를 넘어가는 문단이
        영영 이어지지 않는다.
      · 캡션·키워드 목록·박스는 **경계가 아니라 삭제**다. 본문 흐름에
        끼어든 것이므로 지우고 앞뒤를 이어야 잘린 문단이 복원된다.
        대신 첫 줄만 지우면 꼬리가 본문에 남으므로 **덩어리째** 지운다.
    """
    running = _running_keys(rows)
    blocks: list[list[dict]] = []
    for r in rows:
        key = (r["page"], r["block"])
        if blocks and (blocks[-1][0]["page"], blocks[-1][0]["block"]) == key:
            blocks[-1].append(r)
        else:
            blocks.append([r])

    in_refs = False
    for blk in blocks:
        texts = [r["text"] for r in blk if r["text"]]
        joined = " ".join(texts)
        head = texts[0] if texts else ""
        maxsize = max(r["size"] for r in blk)
        small = maxsize < body - 0.75

        has_cap = any(r["dropcap"] for r in blk)  # 드롭캡 = 본문 문단의 시작. 절대 버리지 않는다
        if in_refs:                              # 참고문헌 목록이 이어지는 중
            if small or _REF_ENTRY.match(head) or _REF_ISH.search(joined[:400]):
                for r in blk:
                    r["kind"] = "drop"
                continue
            in_refs = False
        if head and _REFS_HEAD.match(head):
            for r in blk:
                r["kind"] = "barrier"
            in_refs = True
            continue
        if not has_cap and head and (_DROP_LINE.match(head) or _is_caption_line(head)):
            for r in blk:
                r["kind"] = "drop"               # 라벨·캡션은 블록째
            continue
        if small and not has_cap:
            for r in blk:
                r["kind"] = "drop"
            continue
        for r in blk:                            # 블록 안 라인 단위 판정
            t = r["text"]
            rkey = re.sub(r"\d+", "#", t.lower()).strip()
            if r["dropcap"]:
                r["kind"] = "keep"
            elif not t:
                r["kind"] = "drop"
            elif _DROP_LINE.match(t) or _BULLET_LINE.match(t) or _DROP_ANY.search(t):
                r["kind"] = "drop"
            elif rkey in running or (r["margin"] and _PAGENUM_LINE.match(t)):
                r["kind"] = "drop"
            elif r["size"] < body - 0.75:
                r["kind"] = "drop"
            elif _is_caption_line(t) or _PANEL_LINE.match(t):
                r["kind"] = "drop"
            elif _is_heading_line(r, body):
                r["kind"] = "barrier"
            else:
                r["kind"] = "keep"

    # 삭제한 덩어리의 '꼬리'(라벨 다음 줄에 이어지는 키워드 목록, 두 줄짜리
    # 캡션 등)를 기하적으로 따라가 함께 지운다. 남기면 본문에 섞여 들어간다.
    for i, r in enumerate(rows):
        t = r["text"]
        if not t:
            continue
        if not (_is_caption_line(t) or _BULLET_LINE.match(t) or _LABEL_LINE.match(t)):
            continue
        prev = r
        for nxt in rows[i + 1:i + 8]:
            if nxt["kind"] == "barrier" or nxt["dropcap"] or not nxt["text"]:
                break                            # 드롭캡을 만나면 거기가 본문 시작
            if _is_heading_line(nxt, body) or not _continues(prev, nxt, body):
                break
            nxt["kind"] = "drop"
            prev = nxt
    return rows


def _join_stream(rows: list[dict]) -> str:
    """분류된 라인을 하나의 본문 스트림으로. 경계는 BARRIER 로 남긴다."""
    out: list[str] = []
    pending: str | None = None

    def add(t: str) -> None:
        if not out:
            out.append(t)
            return
        last = out[-1]
        if last.endswith(BARRIER):
            out.append(t)
        elif (last.endswith("-") and not last.endswith((" -", "--"))
              and t[:1].islower()):
            # 줄바꿈 분철('charac-'+'terized')이면 하이픈을 지우고,
            # 약어 합성어('UV-'+'induced')면 하이픈을 남긴 채 붙인다.
            if not _ACRONYM_TAIL.search(last):
                out[-1] = last[:-1]
            out.append(t)
        else:
            out.append(" " + t)

    for r in rows:
        kind = r.get("kind", "keep")
        if kind == "barrier":
            if out and not out[-1].endswith(BARRIER):
                out.append(BARRIER)
            pending = None
            continue
        if kind == "drop":
            continue
        t = r["text"]
        if r.get("dropcap"):
            if t:
                t = r["dropcap"] + t
            else:
                pending = r["dropcap"]
                continue
        if pending:
            t = pending + t
            pending = None
        if t:
            add(t)
    return "".join(out)


def extract_pdf_text(pdf_path: str | Path) -> str:
    """복원용 PDF 본문 스트림(잡음 제거·경계 표시·분철 복원 완료)."""
    return _join_stream(_pdf_lines(pdf_path))


# ── 문장 분리 ───────────────────────────────────────────────────────
def _split_sentences(text: str) -> list[str]:
    """약어·소수점·이니셜에서 끊기지 않는 보수적 문장 분리."""
    starts = [0]
    for m in _SENT_BREAK.finditer(text):
        left = text[:m.start()]
        wm = _LAST_WORD.search(left)
        w = wm.group(1) if wm else ""
        if _ABBR.match(w):
            continue
        if len(w) == 1 and w.isalpha():          # 이니셜 'J.' / 'e.g.'
            continue
        # 소수점('0.8')·서수('79:720-7.')는 구두점 뒤 공백을 요구하는 _SENT_BREAK
        # 자체가 걸러내므로 숫자라는 이유만으로 문장 경계를 포기하지 않는다.
        nxt = text[m.end():m.end() + 1]
        if not (nxt.isupper() or nxt.isdigit() or nxt in "\"'“‘(["):
            continue
        starts.append(m.end())
    starts.append(len(text))
    return [text[a:b].strip() for a, b in zip(starts, starts[1:]) if text[a:b].strip()]


def _is_junk_sentence(s: str) -> bool:
    for rx in _JUNK_SENT:
        if rx.search(s):
            return True
    n = len(s)
    if n >= 20:
        digits = sum(c.isdigit() for c in s)
        alpha = sum(c.isalpha() for c in s)
        if digits / n > 0.30 and alpha / n < 0.55:
            return True                          # 표 셀이 이어붙은 조각
    return False


def _fix_dropcap_text(s: str) -> str:
    """폰트로 못 잡은 드롭캡의 보수적 보정: 'V itiligo' → 'Vitiligo'.

    첫 낱말에만 적용하고 'A'/'I'(정상 관사·대명사)와 뒤가 흔한 낱말인
    경우('T he')는 손대지 않는다 — 정상 문장을 훼손하지 않는 쪽을 택한다.
    """
    m = _DROPCAP_TEXT.match(s)
    if not m:
        return s
    if m.group(2).lower() in _DROPCAP_STOP:
        return s
    return m.group(1) + s[m.end(1):].lstrip()


# ── 문단 복원 ───────────────────────────────────────────────────────
def _locate(letters: str, needle: str) -> int:
    """정규화 문자열에서 유일하게 걸리는 위치. 없거나 모호하면 -1.

    짧은 접두로 찾고, 여러 곳에 걸리면 **접두를 늘려** 하나로 좁힌다.
    (반대로 짧히면 더 모호해질 뿐이라 접두를 줄이는 재시도는 하지 않는다.)
    """
    if len(needle) < 40:
        return -1
    for take in (70, 110, 160, 240, 400):
        probe = needle[:take]
        first = letters.find(probe)
        if first < 0:
            return -1                            # 더 늘려도 못 찾는다
        if letters.find(probe, first + 1) < 0:
            return first
        if take >= len(needle):
            return -1                            # 문단 전체가 중복 등장 → 모호
    return -1


def _final_clean(s: str) -> str:
    """되살린 조각의 마지막 손질 — textfix.clean_paragraph 를 쓰되 없으면 자체 처리.

    textfix 는 같은 결함군을 다루는 형제 모듈이라 있으면 그대로 쓰는 것이 맞다.
    다만 그 모듈이 깨져 있어도 복원이 통째로 죽지는 않게 감싼다(단계 격리).
    """
    try:
        from .textfix import clean_paragraph
        return clean_paragraph(s)
    except Exception:                            # noqa: BLE001 — 모듈 격리
        s = norm_text(s)
        s = re.sub(r"\b([a-z]{2,})-\s+([a-z]{2,7})\b", r"\1\2", s)   # 분철
        s = re.sub(r"\s+([,;)\]])", r"\1", s)
        s = re.sub(r"\s+\.(?!\d)", ".", s)
        return re.sub(r"\s+", " ", s).strip()


def _prev_end(letters: str, prev_text: str, before: int) -> int:
    """직전 문단이 PDF 스트림에서 끝나는 위치(정규화 인덱스). 못 찾으면 -1.

    꼬리 60자로 찾는 것이 정확하지만, 직전 문단 **끝에 캡션·표 각주가 새어
    들어간** 문서가 실제로 많다('… CI, confidence interval; HR, hazard ratio.').
    그런 꼬리는 우리 스트림에 없으므로 못 찾는다 → 문단 시작을 찾아 **이어지는
    만큼만** 인정하는 방법을 함께 쓴다(둘 중 더 뒤를 채택).
    """
    plet = _letters(prev_text or "")
    if len(plet) < 40:
        return -1
    best = -1
    win, j, steps = 40, len(plet), 0
    while j - win >= 0 and steps < 16:           # 꼬리 ~340자를 40자 창으로 훑는다
        pos = letters.rfind(plet[j - win:j], 0, before)
        if pos >= 0:
            best = max(best, pos + win)
        j -= 20
        steps += 1
    ps = _locate(letters, plet)                  # 문단 시작부터 '이어지는 만큼'
    if 0 <= ps < before:
        n, lim = 0, min(len(plet), len(letters) - ps)
        while n < lim and letters[ps + n] == plet[n]:
            n += 1
        best = max(best, ps + n)
    return best if 0 <= best <= before else -1


def _recover_detail(pdf_text: str, para_text: str, max_chars: int = 600, *,
                    seen: str = "", prev_text: str | None = None
                    ) -> tuple[str | None, str]:
    """recover_paragraph 의 내부 구현. (복원문자열|None, 사유) 를 돌려준다."""
    para = norm_text(para_text or "")
    if not para:
        return None, "empty_paragraph"
    # 잘리지 않은 문단에 부르면 앞 헤딩을 본문으로 끌어오는 사고가 난다.
    # 이 함수는 '앞이 잘린 문단' 전용이다(대상 판정은 find_truncated 와 동일).
    if not _BAD_START.match(para) or _ALLOW_START.match(para):
        return None, "not_truncated"
    letters, omap = _letters_map(pdf_text)
    pneedle = _letters(para)
    if len(pneedle) < 40:
        return None, "paragraph_too_short"
    at = _locate(letters, pneedle)
    if at < 0:
        return None, "prefix_not_found"
    start = omap[at]
    if start == 0:
        return None, "at_stream_start"

    # ── 되살릴 범위의 하한 ──────────────────────────────────────────
    window = max(0, start - max_chars * 3)
    low = window
    seg = pdf_text[:start]
    b = seg.rfind(BARRIER)                       # 헤딩·캡션·참고문헌 경계
    if b >= 0:
        low = max(low, b + 1)
    if prev_text:                                # 직전 문단의 끝
        pend = _prev_end(letters, prev_text, at)
        if pend >= 0:
            low = max(low, omap[pend - 1] + 1)
    # 섹션 헤딩은 스트림에서 이미 barrier 로 표시되므로 따로 하한을 두지 않는다.
    # (헤딩 문자열을 본문에서 rfind 하면 'Response to conventional combination
    #  therapy' 처럼 본문 문장 안의 같은 표현에 걸려 하한이 엉뚱해진다.)
    region = pdf_text[low:start].replace(BARRIER, " ").strip()
    if len(region) < 12:
        return None, "nothing_before"

    # ── 뒤에서부터 문장 단위로 채운다 ───────────────────────────────
    sents = _split_sentences(region)
    if not sents:
        return None, "no_sentence"
    picked: list[str] = []
    total = 0
    stop = ""
    for s in reversed(sents):
        if _is_junk_sentence(s):
            stop = stop or "junk"
            break
        core = _letters(s)
        if len(core) >= 25 and seen and core in seen:
            stop = stop or "duplicate"
            break                                # 문서에 이미 있는 문장 = 남의 것
        if total + len(s) > max_chars and picked:
            stop = stop or "max_chars"
            break
        picked.append(s)
        total += len(s) + 1
    if not picked:
        return None, f"first_sentence_{stop or 'rejected'}"

    rec = " ".join(reversed(picked)).strip()
    # 첫 조각이 문장 중간에서 시작하면(범위 하한에서 잘린 조각) 버린다.
    if picked and len(picked) > 1 and not rec[:1].isupper():
        head = _split_sentences(rec)
        if len(head) > 1:
            rec = " ".join(head[1:]).strip()
    rec = _fix_dropcap_text(rec)
    if not rec[:1].isupper() and not rec[:1].isdigit():
        return None, "starts_midsentence"

    # 불변식: 잘린 문단은 문장 중간에서 시작한다 → 앞 조각은 종결부호로 끝날 수 없다.
    if re.search(r"[.!?][\"'’”\)\]]*$", rec):
        wm = _LAST_WORD.search(rec[:-1])
        if not (wm and _ABBR.match(wm.group(1))):
            return None, "ends_with_terminal"

    rec = _strip_inline_cite(rec)
    rec = _final_clean(rec)
    if len(rec) < 12:
        return None, "too_short_after_clean"
    if _letters(rec) and seen and _letters(rec) in seen:
        return None, "duplicate"
    return rec, "ok"


def recover_paragraph(pdf_text: str, para_text: str,
                      max_chars: int = 600) -> str | None:
    """문단 앞에 붙어야 할 텍스트를 PDF 에서 찾아 돌려준다. 못 찾으면 None.

    pdf_text 는 extract_pdf_text() 가 만든 스트림이어야 한다(경계 표시 포함).
    문서 문맥(중복 검사·직전 문단·헤딩)을 주면 정확도가 오른다 —
    recover_document() 가 그렇게 호출한다.
    """
    return _recover_detail(pdf_text, para_text, max_chars)[0]


def _seen_index(doc: dict) -> str:
    """문서에 이미 있는 모든 텍스트(초록·문단·캡션·참고문헌·섹션 제목)의 정규화 색인.

    섹션 제목을 넣는 이유: GROBID 가 본문 한 문장을 통째로 '헤딩'으로 오인해
    path 에 넣어버린 문서가 있다. 그 문장을 다시 본문에 되살리면 중복이 된다.
    """
    buf: list[str] = [doc.get("abstract") or ""]
    for sec in doc.get("sections") or []:
        buf.extend(sec.get("path") or [])
        for para in sec.get("paragraphs") or []:
            buf.append(para.get("text") or "")
    for fig in doc.get("figures") or []:
        buf.append(fig.get("caption") or "")
    for tbl in doc.get("tables") or []:
        buf.append(tbl.get("caption") or "")
        buf.append(tbl.get("markdown") or "")
    for ref in doc.get("references") or []:
        buf.append(ref.get("raw") or ref.get("title") or "")
    return _letters("  ".join(buf))


def _prev_text(doc: dict, si: int, pi: int) -> str | None:
    """직전 문단(같은 섹션 → 없으면 이전 섹션의 마지막 문단)."""
    secs = doc.get("sections") or []
    if pi > 0:
        return (secs[si]["paragraphs"][pi - 1].get("text") or "") or None
    for k in range(si - 1, -1, -1):
        ps = secs[k].get("paragraphs") or []
        if ps:
            return (ps[-1].get("text") or "") or None
    return None


def _prev_candidates(doc: dict, si: int, pi: int,
                     limit: int = 4) -> list[tuple[int, int, dict]]:
    """뒤로 최대 limit 개의 선행 문단(문서 순서)."""
    secs = doc.get("sections") or []
    out: list[tuple[int, int, dict]] = []
    for j in range(pi - 1, -1, -1):
        out.append((si, j, secs[si]["paragraphs"][j]))
        if len(out) >= limit:
            return out
    for k in range(si - 1, -1, -1):
        ps = secs[k].get("paragraphs") or []
        for j in range(len(ps) - 1, -1, -1):
            out.append((k, j, ps[j]))
            if len(out) >= limit:
                return out
    return out


def _anchor_prev(letters: str, doc: dict, si: int, pi: int,
                 at: int) -> tuple[int, int, dict, int] | None:
    """PDF 스트림에서 **실제로** 이 문단 바로 앞에 오는 문단을 찾는다.

    바로 앞 문단이 표 각주·캡션이 통째로 새어 들어온 가짜 문단인 경우가
    실측에서 흔하다('CI, confidence interval; HR, hazard ratio.'). 그런 문단은
    우리 스트림에 없으므로, 뒤로 몇 개 더 훑어 **끝 위치가 가장 늦은** 것을
    고른다. 그것이 진짜 선행 문단이다.
    """
    best: tuple[int, int, dict, int] | None = None
    for sj, pj, para in _prev_candidates(doc, si, pi):
        end = _prev_end(letters, para.get("text") or "", at)
        if end < 0:
            continue
        if best is None or end > best[3]:
            best = (sj, pj, para, end)
    return best




# ── 문단 쪼개짐(복원할 텍스트가 아예 없는 경우) ─────────────────────
def _merge_paragraphs(head: dict, tail: dict) -> dict:
    """쪼개진 두 문단을 하나로. 되돌릴 수 있게 merged_split 표시를 남긴다."""
    a = (head.get("text") or "").rstrip()
    b = (tail.get("text") or "").lstrip()
    if a.endswith("-") and b[:1].islower():
        head["text"] = norm_text(_join_hyphen(a, b))
    else:
        head["text"] = norm_text(a + " " + b)
    for k in ("cited_refs", "cited_keys", "refs_figure", "refs_table"):
        merged = list(head.get(k) or [])
        for v in (tail.get(k) or []):
            if v not in merged:
                merged.append(v)
        if merged:
            head[k] = merged
    head["merged_split"] = True
    head["merged_ids"] = list(head.get("merged_ids") or []) + [tail.get("id")]
    return head


def recover_document(doc: dict, pdf_path: str | Path, max_chars: int = 600, *,
                     merge_splits: bool = True,
                     merge_cross_section: bool = True) -> tuple[dict, dict]:
    """한 편의 잘린 문단을 전부 복원한다. (수리본, 통계) 를 돌려준다.

    잘린 문단은 두 부류다.
      · **본문 유실** — PDF 에는 있는데 TEI 에 없다 → PDF 에서 되살린다.
      · **문단 쪼개짐** — 직전 문단이 PDF 에서 곧바로 이어진다. 사라진 글자가
        없으므로 되살릴 것이 없고, 둘을 **합쳐야** 원상복구된다.
    후자를 되살리려 하면 반드시 중복이 되므로 인접성으로 먼저 가른다.
    다만 **PDF 에서 붙어 있다는 것만으로는 부족하다** — 직전 문단이 완결
    문장으로 끝나면 이어질 자리가 아니므로(캡션·표 각주·연구비 문구가 본문
    조각 앞에 조판된 것일 뿐) 병합하지 않고 split_head_complete 로 보고한다.

    merge_splits=True 는 같은 섹션 안의 쪼개짐을 합친다(문단만 줄고 구조는 그대로).
    merge_cross_section=True 는 섹션 경계를 넘는 쪼개짐도 합친다. 이때 tail 이
    그 섹션의 유일한 문단이면 **섹션이 통째로 사라진다** — 실측 2편이 그랬고
    둘 다 러닝헤더가 헤딩으로 오인된 가짜 섹션('J AM ACAD DERMATOL',
    'Review Article')이라 지워지는 편이 옳았다. 그래도 구조 변경이므로
    지워진 섹션 path 를 stats['sections_removed'] 에 남겨 감사 가능하게 한다.
    끄고 싶으면 merge_cross_section=False 로 호출하면 된다.

    원본 doc 은 건드리지 않는다(깊은 복사본을 수리해 돌려준다).
    """
    out = copy.deepcopy(doc)
    stats: dict[str, Any] = {
        "paper_id": doc.get("paper_id"), "source": doc.get("source"),
        "pdf": Path(pdf_path).name, "truncated": 0, "recovered": 0,
        "merged": 0, "failed": 0, "chars": 0, "reasons": {}, "items": [],
        "sections_removed": [],
    }
    targets = find_truncated(out)
    stats["truncated"] = len(targets)
    if not targets:
        return out, stats

    try:
        pdf_text = extract_pdf_text(pdf_path)
    except Exception as e:                        # noqa: BLE001 — 문서 단위 격리
        stats["failed"] = len(targets)
        stats["reasons"] = {f"pdf_error:{type(e).__name__}": len(targets)}
        return out, stats
    if len(pdf_text) < 400:
        stats["failed"] = len(targets)
        stats["reasons"] = {"pdf_no_text": len(targets)}
        return out, stats

    seen = _seen_index(out)
    letters, _omap = _letters_map(pdf_text)
    to_merge: list[tuple[int, int, dict]] = []
    for si, pi in targets:
        para = out["sections"][si]["paragraphs"][pi]
        ptext = para.get("text") or ""
        at = _locate(letters, _letters(ptext))
        anchor = _anchor_prev(letters, out, si, pi, at) if at >= 0 else None
        prev = (anchor[2].get("text") if anchor else None) or _prev_text(out, si, pi)
        rec, why = _recover_detail(pdf_text, ptext, max_chars, seen=seen,
                                   prev_text=prev)
        if not rec:
            # 사이에 경계(헤딩·캡션·참고문헌)가 있으면 이어진 문단이 아니다.
            # 경계 표시는 비문자라 정규화 인덱스에 안 잡히므로 원문에서 확인한다.
            adjacent = bool(anchor) and 0 <= at - anchor[3] <= 2 and (
                BARRIER not in pdf_text[_omap[anchor[3] - 1] + 1:_omap[at]])
            # PDF 에서 붙어 있다고 해서 '쪼개진 한 문단'인 것은 아니다.
            # 직전 문단이 **완결 문장으로 끝나면** 이어질 자리가 아니다 —
            # 캡션·표 각주·연구비·저자기여 같은 조판 부속이 본문 조각 바로
            # 앞에 놓였을 뿐이다. 실측 33건 중 14건이 이 경우였고, 병합하면
            # 그림 캡션이 본문 문장 한가운데로 끌려 들어온다(오염). 게다가
            # 병합된 문단은 대문자로 시작해 find_truncated 에 다시 안 잡히므로
            # **결함이 조용히 숨는다**. 그래서 병합하지 않고 사유만 남긴다.
            if adjacent and _ends_sentence(anchor[2].get("text") or ""):
                why = "split_head_complete"
            elif adjacent:
                same = anchor[0] == si
                why = "split" if same else "split_cross_section"
                if (same and merge_splits) or (not same and merge_cross_section):
                    to_merge.append((si, pi, anchor[2]))
            stats["reasons"][why] = stats["reasons"].get(why, 0) + 1
            stats["failed"] += 1
            stats["items"].append({"section": si, "para": pi, "ok": False,
                                   "reason": why, "head": ptext[:70]})
            continue
        stats["reasons"][why] = stats["reasons"].get(why, 0) + 1
        if rec.endswith("-") and ptext[:1].islower():
            para["text"] = norm_text(_join_hyphen(rec, ptext))
        else:
            para["text"] = norm_text(rec + " " + ptext)
        para["recovered"] = True
        para["recovered_chars"] = len(rec)
        stats["recovered"] += 1
        stats["chars"] += len(rec)
        stats["items"].append({"section": si, "para": pi, "ok": True,
                               "chars": len(rec), "text": rec})
        seen = seen + _letters(rec)

    # 병합은 인덱스가 밀리므로 **뒤에서부터**. 대상 문단은 객체 참조로 잡아
    # 두었으므로(인덱스가 아니라) 연쇄 병합에도 안전하다. 섹션이 비면 섹션째 뺀다.
    for si, pi, target in sorted(to_merge, key=lambda x: (x[0], x[1]), reverse=True):
        secs = out["sections"]
        tail = secs[si]["paragraphs"][pi]
        _merge_paragraphs(target, tail)
        del secs[si]["paragraphs"][pi]
        if not secs[si]["paragraphs"]:
            stats["sections_removed"].append(list(secs[si].get("path") or []))
            del secs[si]
        stats["merged"] += 1
        for it in stats["items"]:
            if it["section"] == si and it["para"] == pi:
                it["merged"] = True
    return out, stats


# ── 진입점 ──────────────────────────────────────────────────────────
def _find_pdf(doc: dict, pdf_dirs: list[Path]) -> Path | None:
    """source_file 은 다른 PC 경로일 수 있다 → **파일명만** 떼어 찾는다."""
    raw = (doc.get("source_file") or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].strip()
    if not name:
        return None
    for d in pdf_dirs:
        p = d / name
        if p.exists():
            return p
    stem = name[:-4].lower() if name.lower().endswith(".pdf") else name.lower()
    for d in pdf_dirs:
        if not d.is_dir():
            continue
        for p in d.glob("*.pdf"):
            if p.stem.lower() == stem:
                return p
    return None


def run(config: dict | None = None, *, dry_run: bool = True) -> None:
    """normalized/*.json 의 잘린 문단을 원본 PDF 로 복원한다.

    기본이 dry_run=True 다 — 정본을 말없이 고치지 않는다. 보고서
    (work_dir/recover_report.jsonl)는 항상 쓰고, dry_run=False 일 때만
    출력 디렉터리에 문서를 쓴다. 출력 위치는 config['recover']['output_dir']
    (work_dir 기준 상대, 기본 'normalized_recovered')이라 정본과 분리된다.
    """
    cfg = config or utils.load_config()
    opts = (cfg.get("recover") or {}) if isinstance(cfg, dict) else {}
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / (opts.get("input_dir") or "normalized")
    out_dir = work / (opts.get("output_dir") or "normalized_recovered")
    report = work / (opts.get("report") or "recover_report.jsonl")
    max_chars = int(opts.get("max_chars", 600))

    pdf_dirs = [utils.resolve(cfg["project"]["input_dir"])]
    for extra in (opts.get("pdf_dirs") or []):
        pdf_dirs.append(utils.resolve(extra))

    files = sorted(norm_dir.glob("*.json"))
    log(f"[복원] GROBID 결손 본문 복구: {len(files)}편 @ {norm_dir}"
        + (" (DRY-RUN: 문서를 쓰지 않는다)" if dry_run else f" → {out_dir}"))
    if not files:
        log(f"        → 정본 문서가 없다. 0~4단계를 먼저 실행할 것: {norm_dir}")
        return
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    n_trunc = n_rec = n_merge = n_fail = n_chars = n_nopdf = n_wrote = 0
    for i, src in enumerate(files, 1):
        try:
            doc = utils.read_json(src)
        except Exception as e:                    # noqa: BLE001 — 파일 단위 격리
            log(f"  [{i}/{len(files)}] 읽기 실패({src.name}): {type(e).__name__}: {e}")
            continue
        targets = find_truncated(doc)
        if not targets:
            continue
        pdf = _find_pdf(doc, pdf_dirs)
        if not pdf:
            n_nopdf += 1
            n_trunc += len(targets)
            n_fail += len(targets)
            rows.append({"paper_id": doc.get("paper_id"), "truncated": len(targets),
                         "recovered": 0, "failed": len(targets),
                         "reasons": {"pdf_not_found": len(targets)}})
            log(f"  [{i}/{len(files)}] PDF 없음: {doc.get('paper_id')}")
            continue
        fixed, st = recover_document(doc, pdf, max_chars)
        n_trunc += st["truncated"]
        n_rec += st["recovered"]
        n_merge += st["merged"]
        n_fail += st["failed"]
        n_chars += st["chars"]
        rows.append({k: v for k, v in st.items() if k != "items"} | {"items": st["items"]})
        # **복원과 병합 둘 다** 문서를 바꾼다. 복원만 보고 쓰면 병합분이 통째로 날아간다.
        if st["recovered"] or st["merged"]:
            n_wrote += 1
            log(f"  [{i}/{len(files)}] {st['paper_id']}: 복원 {st['recovered']}"
                f" · 병합 {st['merged']} / 잘림 {st['truncated']}건 · {st['chars']}자")
            if not dry_run:
                utils.write_json(out_dir / src.name, fixed)
    utils.write_jsonl(report, rows)
    log(f"[복원] 잘린 문단 {n_trunc}건 → PDF 복원 {n_rec}건 · 쪼개짐 병합 {n_merge}건 · "
        f"남음 {n_fail - n_merge}건 · 복원 {n_chars:,}자"
        f"(평균 {n_chars // max(1, n_rec)}자) · PDF 없음 {n_nopdf}편")
    log(f"        수리된 문서 {n_wrote}편"
        + ("(DRY-RUN: 쓰지 않았다)" if dry_run else f" → {out_dir}"))
    log(f"        보고서: {report}")
