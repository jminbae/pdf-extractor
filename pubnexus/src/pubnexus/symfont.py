"""기호 전용 글꼴의 글자를 진짜 유니코드로 되돌린다.

증상: `10.1002/jso.23438` Table III(Newcastle–Ottawa Scale)의 별점이 전부
`$` 로 나온다. PDF 텍스트층이 실제로 `$` 를 담고 있기 때문이다 —
그 자리 글꼴은 `AdvPi3`, Elsevier 의 **기호 전용** 글꼴이고, 기호 글꼴에서는
ASCII 코드값에 아무 의미가 없다. 그 코드에 어떤 **그림**을 그리도록 글꼴이
만들어졌는지가 진짜 글자다. (교차검증: 별 8개인 행의 Total 열이 8이다.)

이 표는 **글자를 실제로 렌더해 눈으로 판정**해 만들었다. 규칙이나 추측이
아니다. 각 항목의 근거(어느 PDF 몇 쪽에서 확인했는지, 렌더한 이미지)를
아래 표에 그대로 적었다. 확인하지 못한 것은 **넣지 않았다**.

세 가지 안전 원칙
  1. **글꼴이 그 글꼴일 때만 바꾼다.** `$` 는 비용 연구에서 진짜 달러 기호로
     쓰인다. 글자만 보고 바꾸면 안 된다. 이 모듈의 모든 함수는 글꼴 이름을
     반드시 받는다.
  2. **이미 맞게 나오는 것은 건드리지 않는다.** `EuclidSymbol` 의 `<`·`>`·`≥`,
     `Symbol` 의 `β`·`γ`, `SymbolMT` 의 `≤`·`μ`, `CMSY10` 의 `≤`·`≥`,
     `AdvminionSymbols` 의 `·`, `AMsam10A` 의 `■`, `TeX_CM_Maths_Italic` 의 `<`
     는 렌더해 보니 **글자 그대로**였다. 이 글꼴들은 제대로 된 ToUnicode CMap
     을 갖고 있다. 표에 아예 넣지 않아 손대지 않는다(아래 '건드리지 않는 것').
  3. **1:1 치환만 한다.** `tablefill._visible_words` 는 글자 bbox 로 낱말을
     끊으므로 글자 수가 바뀌면 좌표 대응이 깨진다.

── 확정 매핑과 시각 확인 근거 ────────────────────────────────────────
렌더 이미지는 작업 시 스크래치에 남겼다(`sheets/<글꼴>_U<코드>__ALL_<개수>.png`
= 그 쌍의 **모든** 출현을 한 장에 모은 것, `glyphs/<글꼴>_U<코드>__ctx.png`
= 문맥). 아래 '전수'는 205편 코퍼스 안 출현 횟수다.

AdvPi3 (Elsevier 기호 글꼴)
  '$' U+0024 → '★' U+2605   전수 67, 1편. 67개 전부 채워진 5각별.
      2013-12-O-JSO Laparoscopic gastrectomy for advanced gastric cancer.pdf p4
      (= 10.1002/jso.23438, Table III NOS 별점. 별 8개 행의 Total 이 8)
  '#' U+0023 → '©' U+00A9   전수 4, **4편**. 문맥 `© ISS 2010`.
      2010-06-C-SCI(Skeletal Radiol) Primary cutaneous Ewing's sarcoma… p1 외 3편
  '1' U+0031 → '®' U+00AE   전수 1. 문맥 `(®Dr. VAE, Suwon, Korea)`.
      2019-03-O-LSM Comparison of 311-nm TitaniumSapphire laser… p3

AdvPi1 (Elsevier 기호 글꼴 — AdvPi3 와 **배치가 다르다**)
  '5' U+0035 → '<' U+003C   전수 40, 1편. 40개 전부 부등호 `<`.
      2017-05-O Association of inflammatory bowel disease with ankylosing… p3
      문맥 `p<0.0001`. ← 이것을 안 고치면 `p 5 0.0001` 로 남아 의미가 뒤집힌다
  '4' U+0034 → '>' U+003E   전수 1. 문맥 `IOP > 21 mm Hg`.
      2019-04-O Asymmetry of the macular structure… p2
  '8' U+0038 → '°' U+00B0   전수 6, 1편. 문맥 `25°C and 60% relative`.
      2015-07-O-LSM Hair regrowth through wound healing process… p2
  'b' U+0062 → 'β' U+03B2   전수 2. 문맥 `β-1,3-glucan (curdlan)`.
      2017-05-O Association of inflammatory bowel disease with ankylosing… p4
      ※ '5'(<) 와 같은 논문에서 확인 — 한 문서 안에서 배치가 일관된다

MathematicalPi-One (제어문자로 나온다 — 그대로 두면 통째로 쓰레기)
  '\x02' → '≥' U+2265   전수 22, **7편**. 문맥 `(≥25%)`.
      2017-07-O-JAMAd Phototherapy for Vitiligo… p1 외 6편
  '\x03' → '≤' U+2264   전수 2. 문맥 `(aged ≤18 years)`.
      2026-XX-O-JAMAD Definition of Severity and Relapse for Vitiligo… p3
  '\x04' → 'α' U+03B1   전수 1. 문맥 `TNF-α inhibitors reduce`.
      2018-10-L-EJD Contact vitiligo induced by rubber earloops… p1

TeX_CM_Maths_Symbols
  '\x15' → '≥' U+2265   전수 4. 문맥 `40–59, ≥60 years`.
      2017-06-O-PONE Increased risk of thyroid diseases… p3
  '\x14' → '≤' U+2264   전수 1. 문맥 `age (≤19, 20–39,`. 같은 쪽·같은 문장.
      2017-06-O-PONE Increased risk of thyroid diseases… p3

SymbolMT
  '\x1b' → 'ε' U+03B5   전수 1. 문맥이 `Epsilon ε` — 그림이 글자 이름을 적어 뒀다.
      2016 코스메티컬과 두피모발_8(2).pdf p89
      ※ SymbolMT 의 '≤'(U+2264)·'μ'(U+03BC) 는 이미 맞다 — 넣지 않았다

Wingdings3
  '' → '▼' U+25BC   전수 2, 2편. 문맥 `▼This medicine is subject to
      additional monitoring`(EMA 흑색 역삼각형 표시).
      2021-04-L-BJD Classification of facial and truncal segmental vitiligo… p5

Wingdings-Regular
  '*' U+002A → '✉' U+2709   전수 1. 문맥 `✉ Nicoline F. Post` (교신저자 봉투).
      2023-10-O-ADR Expert opinion about laser and intense pulsed light… p1
  'z' U+007A → '●' U+25CF   전수 1. 문맥 highlights 상자의 주황 원형 불릿.
      2025-12-R-NRDP Vitiligo2.pdf p1

── 2차 발견: Elsevier/JAAD 계열 ──────────────────────────────────────
이름으로 기호 글꼴을 찾으면 놓친다. **내용**(제어문자·사설영역이 섞였는가,
알파벳 비율이 낮은가)으로 다시 훑어 아래를 찾았다. 앞의 것들보다 양이 많다.

AdvPSMP4 (JAAD 본문의 비교기호 — 임상적으로 가장 위험했다)
  '\' U+005C → '<'   전수 227, **24편**. `P \ .05` → `P < .05`,
      `age \18 y` → `age <18 y`, `(\5%)` → `(<5%)`
      2013-04-O-JAAD Mohs micrographic surgery… p2 외 23편
  '[' U+005B → '>'   전수 61, **17편**. `P [ .05` → `P > .05`,
      `BT [2 mm` → `BT >2 mm`, `Age [ 50 y` → `Age > 50 y`
  ※ 세로 조판 표머리에서는 글자가 90° 돌아 '∨'·'∧' 로 보이지만 같은 글자다.

AdvP7DA6  ※ AdvPi3 와 **정반대다** — 여기 '$' 는 별이 아니라 '≥' 다
  '$' U+0024 → '≥'   전수 82, **22편**. `age $ 18 y` → `age ≥ 18 y`,
      `($75% repigmentation)` → `(≥75% …)`, `$ 2 LNs` → `≥ 2 LNs`
  '#' U+0023 → '≤'   전수 19, 6편. `Age # 60 y` → `≤ 60 y`,
      `BT #2 mm` → `BT ≤2 mm`, `(#200 kU/L)` → `(≤200 kU/L)`
  'b' U+0062 → 'β'   전수 22, 2편. `b-catenin` → `β-catenin`, `TGF-b1` → `TGF-β1`

AdvPSMPi6
  'd' U+0064 → '•'   전수 42, **16편**. JAAD CAPSULE SUMMARY 의 글머리표.
      `d Extramammary Paget disease is` → `• Extramammary …`
      ※ 같은 자리를 바르게 뽑는 논문에서 이 글머리표가 U+2022 로 나온다 —
        그래서 '●'(U+25CF) 가 아니라 '•'(U+2022) 로 맞춘다.
  'j' U+006A → '■'   전수 12, 1편. 미배정 권·호 자리표시.
      `VOLUME jj, NUMBER j` → `VOLUME ■■, NUMBER ■`

AdvPSSym
  'ª' U+00AA → '©'   전수 14, **11편**. 전부 저작권 줄.
      `ª 2015 by the American Academy of Dermatology, Inc.` → `© 2015 …`

── 건드리지 않는 것(렌더해서 '이미 맞다'고 확인한 글꼴) ──────────────
  EuclidSymbol       '<' '>' '≥' '±' '=' 'α' 'β' '+'  ← 전부 글자 그대로
                     (`< 0%, 1–49%`, `CD8⁺ T cells`, `≥50%, ≥75%`)
  EuclidSymbol-Bold  '±' '='   (`3 months ± 2 weeks`)
  Symbol             'β' 'γ' '≥' '®'   (`TGFβ`, `≥40 years`)
  SymbolMT           '≤' 'μ'   (`CRM ≤1mm`, `μg/dl`)
  SymbolGreekU       'χ'       (`χ² test`)
  CMSY10             '≤' '≥'   (`≤20`)
  AdvminionSymbols   '·'       (키워드 구분 `Immunomodulation · Mechanism`)
  AMsam10A           '■'       (기사 끝 사각형)
  TeX_CM_Maths_Italic '<'      (`P < 0.001`)
  SegoeUISymbol      '®'
이들은 CHAR_MAP 에 없으므로 이 모듈을 통과해도 원문 그대로 나온다.

── 적용 지점 ────────────────────────────────────────────────────────
글꼴을 알 수 있는 곳, 즉 PyMuPDF span 을 직접 다루는 곳에서만 쓴다.
  · pdf_fallback._line_text_and_cites  (span 단위)
  · tablefill._Page.__init__           (span 단위)
  · tablefill._visible_words           (글자 단위 — bbox 를 유지해야 해서 1:1)
GROBID/TEI 경로에는 글꼴 정보가 없어 여기서 손댈 수 없다(모듈 최하단 주석).
"""

from __future__ import annotations

import re
from collections import Counter

__all__ = ["normalize_font", "is_mapped_font", "remap_char", "remap_text",
           "span_text", "CHAR_MAP", "reset_stats", "get_stats",
           "TYPESET_EVIDENCE", "saw_typeset_evidence", "damaged_spans"]


# ── 확정 매핑 ────────────────────────────────────────────────────────
# {정규화한 글꼴 이름: {뽑힌 글자: 진짜 글자}}
# **눈으로 확인한 것만** 넣는다. 위 표의 근거와 1:1 로 대응한다.
# 값은 반드시 한 글자여야 한다(글자 bbox 대응을 유지하기 위해).
CHAR_MAP: dict[str, dict[str, str]] = {
    "AdvPi3": {
        "$": "★",      # ★ BLACK STAR
        "#": "©",      # ©
        "1": "®",      # ®
    },
    "AdvPi1": {
        "5": "<",
        "4": ">",
        "8": "°",      # °
        "b": "β",      # β
    },
    "MathematicalPi-One": {
        "\x02": "≥",   # ≥
        "\x03": "≤",   # ≤
        "\x04": "α",   # α
    },
    "TeX_CM_Maths_Symbols": {
        "\x14": "≤",   # ≤
        "\x15": "≥",   # ≥
    },
    "SymbolMT": {
        "\x1b": "ε",   # ε
    },
    "Wingdings3": {
        "": "▼",  # ▼
    },
    "Wingdings-Regular": {
        "*": "✉",      # ✉
        "z": "●",      # ●
    },
    # ── Elsevier/JAAD 계열 (아래 '2차 발견' 주석 참고) ──
    "AdvPSMP4": {
        "\\": "<",
        "[": ">",
    },
    "AdvP7DA6": {
        "$": "≥",      # ≥  ※ AdvPi3 의 '$' 는 ★ 다. 글꼴마다 다르다
        "#": "≤",      # ≤
        "b": "β",      # β
    },
    "AdvPSMPi6": {
        "d": "•",      # •
        "j": "■",      # ■
    },
    "AdvPSSym": {
        "ª": "©",      # ©
        # ※ AdvPSSym 의 U+0001 · U+0002 는 **논문마다 © 이기도 ® 이기도 하다**.
        #    글꼴+코드만으로는 가를 수 없어 넣지 않는다(보고서의 '미상' 항목).
    },
}

# 넣자마자 깨지는 실수를 막는다 — 값은 한 글자, 키도 한 글자.
for _f, _m in CHAR_MAP.items():
    for _k, _v in _m.items():
        assert len(_k) == 1 and len(_v) == 1, f"1:1 아님: {_f} {_k!r}->{_v!r}"


# ── 글꼴 이름 정규화 ─────────────────────────────────────────────────
# PDF 마다 서브셋 접두가 붙는다. 이 코퍼스에서 실제로 관측한 형태는 세 가지다.
#   (a) 표준 서브셋 접두 : 'ABCDEF+AdvPi3' — PDF 규격상 **대문자 정확히 6글자 + '+'**
#   (b) Elsevier 무작위 접두(플러스 없음) : 'WmprvxAdvPi3',
#       'GkkjkjNrnrxtAdvPi3'(접두가 **두 번** 붙기도 한다)
#   (c) '+' 가 **접미**로 붙는 이름 : 'AdvOT3c2d9f11+20', 'AdvTTec369687+22'
#       (Elsevier 인코딩 변종 번호). 여기서 '+' 앞을 버리면 이름이 '20' 이 된다.
# 그래서 '+' 를 무조건 자르면 안 되고 (a) 의 형태일 때만 잘라야 한다.
# 마찬가지로 '앞 6글자를 자른다' 같은 규칙도 쓰면 안 된다 — 실제로 그렇게 했다가
# 'Symbol'→'' , 'SymbolMT'→'MT' , 'Wingdings3'→'ngs3' 로 이름이 부서졌다.
# 아는 이름으로 **끝나는지**만 본다. 접두 길이에 의존하지 않는다.
_MAPPED = tuple(sorted(CHAR_MAP, key=len, reverse=True))   # 긴 이름 우선
_SUBSET = re.compile(r"^[A-Z]{6}\+")


def normalize_font(name: str | None) -> str:
    """'ABCDEF+AdvPi3' · 'WmprvxAdvPi3' · 'GkkjkjNrnrxtAdvPi3' → 'AdvPi3'.

    'AdvOT3c2d9f11+20' 처럼 '+' 가 접미인 이름은 그대로 둔다.
    표에 있는 글꼴로 해석되지 않으면 (접두만 떼고) 원래 이름을 돌려준다.
    """
    if not name:
        return ""
    name = _SUBSET.sub("", name, count=1)
    for k in _MAPPED:
        if name == k or name.endswith(k):
            return k
    return name


def is_mapped_font(name: str | None) -> bool:
    """이 글꼴에 손댈 것이 있는가."""
    return normalize_font(name) in CHAR_MAP


# ── 치환 ─────────────────────────────────────────────────────────────
_stats: Counter = Counter()      # (글꼴, 원글자, 새글자) → 횟수. QC 용.


def reset_stats() -> None:
    _stats.clear()


def get_stats() -> dict[tuple[str, str, str], int]:
    return dict(_stats)


# ── textfix 게이트와의 관계 (중요) ───────────────────────────────────
# textfix 는 '조판 사고 서명'(_MOJI_SIGNS: '\50%'·'$75%'·'948C'…)이 보일 때만
# ASCII 계열 치환을 연다. 그런데 이 모듈이 PDF 를 읽는 단계에서 그 글자들을
# 미리 고쳐 버리면 **서명이 사라져 게이트가 닫히고**, 같은 문서의 다른 손상
# (¼→=, AE→±, e→−)이 수리되지 않는다. 실측: 205편 중 18편에서 게이트가 닫혔고
# '[95%'→'>95%', '#12'→'≤12' 수리 5건을 잃었다.
#
# 그래서 아래 집합을 둔다. 이 (글꼴, 원글자) 를 고쳤다는 것은 그 문서가
# **ASCII 조판 사고를 겪었다는 글꼴 수준의 증거**다(문자열 어림보다 강하다).
# 게이트 판정에 OR 로 넣으면 손실이 사라진다(실측: 나빠진 자리 0).
#
# 기호(★·©·▼·•·ε)만 고친 문서는 **넣으면 안 된다** — 그런 문서에서 게이트를
# 열면 진짜 달러가 망가진다(실측: '0190-9622/$36.00' → '≥36.00',
# '3.3 oz, $26' → '≥26'). 조판 사고 글자만 증거로 삼는 이유다.
TYPESET_EVIDENCE: frozenset[tuple[str, str]] = frozenset({
    ("AdvPSMP4", "\\"), ("AdvPSMP4", "["),
    ("AdvP7DA6", "$"), ("AdvP7DA6", "#"),
    ("AdvPi1", "5"), ("AdvPi1", "4"), ("AdvPi1", "8"),
})


def saw_typeset_evidence() -> bool:
    """마지막 `reset_stats()` 이후 ASCII 조판 사고 글자를 고친 적이 있는가.

    문서 하나를 읽기 전에 `reset_stats()` 를 부르고, 다 읽은 뒤 이 함수를
    게이트 판정에 OR 로 넣어 쓴다(모듈 상단 '적용 지점' 및 위 주석 참고).
    """
    return any((f, old) in TYPESET_EVIDENCE for (f, old, _new) in _stats)


def remap_char(font: str | None, ch: str) -> str:
    """글자 하나. 글꼴이 표에 없거나 그 코드가 표에 없으면 **그대로** 돌려준다.

    `tablefill._visible_words` 처럼 글자 bbox 를 함께 다루는 곳에서 쓴다.
    """
    m = CHAR_MAP.get(normalize_font(font))
    if not m:
        return ch
    new = m.get(ch)
    if new is None:
        return ch
    _stats[(normalize_font(font), ch, new)] += 1
    return new


def remap_text(font: str | None, text: str) -> str:
    """span 텍스트 전체. 글자 수는 변하지 않는다(1:1)."""
    if not text:
        return text
    f = normalize_font(font)
    m = CHAR_MAP.get(f)
    if not m:
        return text
    if not any(c in m for c in text):
        return text
    out = []
    for c in text:
        new = m.get(c)
        if new is None:
            out.append(c)
        else:
            out.append(new)
            _stats[(f, c, new)] += 1
    return "".join(out)


def span_text(span: dict) -> str:
    """PyMuPDF span dict → 바로잡은 텍스트. 호출부를 짧게 쓰려고 둔다."""
    return remap_text(span.get("font"), span.get("text", ""))


# ── GROBID/TEI 경로 ──────────────────────────────────────────────────
# TEI 에는 글꼴 정보가 없다. GROBID 는 pdfalto 가 뽑은 글자를 그대로 싣고,
# pdfalto 도 같은 텍스트층을 읽으므로 **같은 손상이 그대로 들어온다**
# (실측: TEI 에서도 별 자리가 '$' 다). 그래서 TEI 문자열만 보고는 고칠 수 없다.
# 글자만 보고 고치면 비용 연구의 진짜 '$' 를 별로 바꾼다 — 절대 안 된다.
#
# 쓸 수 있는 방법은 하나다: **같은 PDF 를 좌표로 다시 읽어 대조한다.**
# 이 모듈은 그 대조에 필요한 재료(글꼴별 확정 매핑)를 이미 갖고 있고,
# `damaged_spans()` 가 '이 PDF 에서 손상된 글자가 어느 자리에 몇 개 있는지'를
# 돌려준다. 호출부는 TEI 쪽 텍스트에서 같은 이웃 문맥을 찾아 바꾸면 된다.
# 자세한 판단은 보고서에 적었다(적용은 담당 밖이라 여기서 재료만 제공한다).
def damaged_spans(doc) -> list[dict]:
    """열린 PyMuPDF 문서에서 **바로잡을 글자가 있는 자리**를 모두 찾는다.

    돌려주는 항목: page, bbox(글자), font, old, new, 그리고 그 글자가 들어 있는
    줄의 원문(line_before)과 바로잡은 줄(line_after).
    TEI 처럼 글꼴을 모르는 텍스트를 대조·수리할 때 쓰라고 만든 것이다.
    """
    out: list[dict] = []
    for pno in range(doc.page_count):
        try:
            raw = doc[pno].get_text("rawdict")
        except Exception:                      # noqa: BLE001
            continue
        for b in raw.get("blocks", ()):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", ()):
                spans = ln.get("spans", ())
                before = "".join(
                    "".join(c.get("c", "") for c in sp.get("chars", ()))
                    for sp in spans)
                after = "".join(
                    remap_text(sp.get("font"),
                               "".join(c.get("c", "") for c in sp.get("chars", ())))
                    for sp in spans)
                if before == after:
                    continue
                for sp in spans:
                    f = normalize_font(sp.get("font"))
                    m = CHAR_MAP.get(f)
                    if not m:
                        continue
                    for c in sp.get("chars", ()):
                        ch = c.get("c", "")
                        if ch in m:
                            out.append({"page": pno, "bbox": tuple(c["bbox"]),
                                        "font": f, "old": ch, "new": m[ch],
                                        "line_before": before, "line_after": after})
    return out
