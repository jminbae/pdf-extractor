"""2단계b-fallback — PyMuPDF 단독 구조 추출 + 상위 단계의 2차 안전망.

두 가지 역할을 한다.
  (1) GROBID 를 못 쓰는 환경의 '차선책' 파서(parse_pdf → Document).
  (2) GROBID/PMC 가 놓친 그림·표·본문을 PDF 에서 되찾아 주는 **안전망**
      (pdf_figures_tables / missing_prose). 상위 단계가 보완에 쓴다.

설계 원칙은 하나다 — **오염이 누락보다 나쁘다.** 머리말·꼬리말·저자소속·
교신·저작권·참고문헌 목록·표 본문은 본문(sections)에 들어오면 안 된다.
그래서 규칙은 모두 '구조적 증거'(블록 머리 / 좌표 / 반복 / 폰트)를 요구하고,
증거가 애매하면 **버리는 쪽이 아니라 본문에 넣지 않는 쪽**으로 판단한다.

구조 추출
  · 읽기순서: 페이지가 실제로 2단인지 판정한 뒤에만 좌단→우단으로 정렬한다.
    (전폭 줄이 섞인 1단 조판을 무조건 2단으로 보면 문단이 뒤섞인다 — 실측 결함)
  · 회전(90°) 텍스트는 본문 흐름에서 빼고 표 경로로만 쓴다(세로 조판 표).
  · front matter(저자·소속·수신일·교신·펀딩·키워드) 제거 — 제목/저자/초록은 API 보유
  · 머리말/꼬리말은 '페이지 상·하단 8% + 여러 페이지 반복' 이라는 위치 증거로 제거
  · 참고문헌 구역(헤딩 또는 번호목록 런) 이후는 본문에서 통째로 제외
  · 헤딩은 '번호 매김' 또는 '섹션 키워드' + 폰트 강조일 때만 인정(저자명 오탐 방지)
  · 세로 간격 기반 문단 재구성 + 줄바꿈 하이픈 분철 복원
  · 위첨자 인용번호를 폰트 플래그로 감지해 본문에서 제거

그림·표 캡션 판별 규칙(본문 중 참조 'as shown in Fig 2' 와 가르는 근거)
  C1 **블록 머리**에서 시작한다. 본문 중 참조는 블록 첫 글자에 오지 않는다.
     여는 괄호가 앞서면 그 시점에서 이미 탈락한다.
  C2 라벨(Figure|Fig|Table|…) + 번호(아라비아/로마/S1·E1) + 구분자.
  C3 번호 뒤 설명이 20자 이상. 'Table 1. Continued' 같은 이어짐 머리글이 빠진다.
     라벨만 있는 블록은 바로 아래 인접 블록을 한 번만 이어붙여 재판정한다.
  C4 설명 첫 낱말이 소문자 서술동사·기능어(shows/lists/and/of…)면 탈락 →
     'Table 1 shows the characteristics…' 같은 본문 첫 문장을 배제한다.
     '(a) Clinical pictures…' 처럼 한 글자 패널 라벨은 검사에서 뺀다.
  ※ '참고문헌 구역 안이면 탈락' 규칙은 실측 후 뺐다 — JAAD 는 그림 캡션 쪽이
     참고문헌 **뒤**에 오고(7개 유실), Wiley 표 캡션은 러닝헤드 띠에 있어
     'TABLE 2'/'TABLE 3' 이 반복 머리말로 오인됐다(4개 유실). 막아 준 오탐은 0.

실측(정본 167편 대응 PDF)
  · 캡션 615개 전수 육안 검토 — 캡션 아닌 것 0개.
  · 본문이 부른 그림 21·표 12번호를 못 찾았으나, 그중 PDF 블록 머리에 캡션
    문자열이 실제로 있던 것은 표 1건('Table 1 (continued)') 뿐이다. 나머지는
    캡션 없는 그림(이미지만) 또는 별지 보조자료다.
  · 위 615개는 보조자료 키 충돌을 고치기 전 수치다. 고친 뒤 9개(그림 5·표 4)가
    더 살아나 624개가 됐다(아래 '보조자료' 항목).

표 본문
  PyMuPDF `page.find_tables()` 는 이 코퍼스에서 쓸 수 없다(실측: 정본 표 59개
  25편에서 lines 전략 16개 회수 · 오탐 포함, text 전략은 페이지 전체를 표 하나로
  잡는다). 그래서 캡션 아래 **표 영역**을 좌표로 잘라내 단어를 y(회전이면 x)로
  묶어 행을 복원하고, 행 안의 가로 공백으로 열을 나눈다. 열이 서지 않으면
  격자를 포기하고 행 텍스트만 그대로 남긴다(누락보다 낫고, 오정렬은 표 안에 갇힌다).

수리 전/후 실측(정본 167편에 대응하는 PDF 전수, 같은 스크립트)
              그림   표  표본문   본문글자   정본회수(중앙/평균)  오염 front/refs/caption/running
  수리 전       0     0     0   3,348,858    0.883 / 0.829     253 / 722 / 370 / 89
  수리 후     328   296   245   2,322,385    0.865 / 0.840      76 /  95 /   4 / 21
  ※ '오염' = 본문 문단이 소속·펀딩·판권 패턴이거나 참고문헌 항목이거나 캡션이거나
    러닝헤드인 건수. **판정 규칙은 이 모듈 바깥에 따로 둔 독립 패턴으로 잰다** —
    이 모듈의 _is_frontmatter/parse_caption 으로 자기 산출물을 채점하면 정의상
    걸릴 수 없는 것만 남아 수치가 낙관적으로 나온다(같은 코퍼스에서 front 55 vs
    253, caption 4 vs 370 으로 갈렸다).
  ※ 수리 전의 본문 글자수가 큰 것은 참고문헌·머리말을 통째로 담고 있었기 때문이다.
    글자수가 줄어도 **정본 본문의 회수는 늘었다** — 수리 전이 살렸는데 수리 후가
    잃은 정본 6-gram 11,252개, 반대로 새로 회수한 것 12,243개(순증 +991).
  ※ 남은 회수 손실의 최대 항목(41.6%)은 캡션 구간인데, 이는 손실이 아니라 이동이다
    — 폴백은 캡션을 sections 가 아니라 figures/tables 에 담는다. 정본(GROBID)이
    캡션을 본문 문단에 섞어 둔 탓에 회수율 지표에서만 손실로 보인다.

캡션 문법은 4.6단계 figtab 과 규칙이 겹치지만 여기서는 좌표·폰트 증거를 같이
쓰므로 별도로 둔다(안전망이 다른 단계에 의존하지 않게 한다). 두 구현을 167편
전수 대조한 결과 불일치는 1건뿐이었다.

적대적 검증에서 추가로 잡은 결함(각 항목의 근거는 해당 함수 docstring에 있다)
  · _body_start 가 참고문헌 첫 항목 '1. Richard MA, …' 를 '1번 섹션'으로 오인
    (10.1001/jamadermatol.2026.0294: 974줄 중 964줄을 버려 회수율 0.000 → 0.605).
  · _table_region 이 단 사이 여백을 넘어 옆 단 본문을 표로 흡수
    (10.1111/jdv.15936: 본문 21줄 → 회수율 0.791 → 0.895).
  · 보조자료 키 충돌 — 'Figure 1' 과 'Supplementary Fig 1' 이 같은 키가 되어
    보조자료 9개(그림 5·표 4)가 통째로 버려졌다.
  · 참고문헌 쪽의 읽기순서가 뒤섞여 항목이 헤딩보다 앞에 오면 지워지지 않던 문제
    (_mark_refs_geometric 로 좌표 기준 보완 — 오염 문단 105 → 95).
  · missing_prose 가 참고문헌 항목·게재이력을 회수 구간으로 내보내던 문제
    (_injectable 관문 — 표본 오염 3/75 → 0/72).

한계(GROBID만 가능): 인용→참고문헌 링크, 참고문헌 목록 파싱.
남은 위험: 다단 참고문헌 쪽에서 한 항목이 단 경계를 넘으면(전폭 줄) 밴드가
끊겨 읽기순서가 뒤섞인다. _mark_refs_geometric 이 같은 쪽은 막아 주지만 근본
수리는 _order_lines 의 밴드 규칙을 손대야 한다(오염 잔량 95문단 · 32편).
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from pathlib import Path

import fitz

from . import utils
from .jats import _dedup, _tidy_punct
from .schema import Document, Meta, Section, Paragraph, Figure, Table, classify_section
from .textfix import clean_heading, clean_paragraph
from .utils import norm_text, log

CITE_RE = re.compile(r'^[0-9]{1,3}(?:[,\-–][0-9]{1,3})*$')
SUPERSCRIPT = 1   # span flags 비트0
BOLD = 16         # span flags 비트4
ITALIC = 2        # span flags 비트1
# 굵기/이탤릭은 flags가 아니라 폰트 이름 접미로 인코딩되는 경우가 많다(출판사별 상이)
BOLD_FONT = re.compile(r'(bold|black|semibold|heavy|\.b$|-b$|bd$|-bd)', re.I)
ITALIC_FONT = re.compile(r'(italic|oblique|\.i$|-i$|-it$)', re.I)

# 번호 매김 섹션 헤딩: "1 | INTRODUCTION", "2. Methods", "3.2.2 | Neoplasms"
# 구분자(| . ))를 필수로 요구 → "384 914 patients" 같은 본문 숫자 오탐 방지
NUM_HEAD = re.compile(r'^\d{1,2}(?:\.\d{1,2}){0,3}\s*[|.)]\s+\S')
SECTION_KEYS = {
    "introduction", "background", "methods", "method", "materials",
    "materials and methods", "patients and methods", "study design",
    "results", "result", "findings", "discussion", "conclusion",
    "conclusions", "limitations", "references", "acknowledgment",
    "acknowledgments", "acknowledgements", "abstract",
}
# front matter(본문에서 제거) — 고정밀 패턴만
FRONT_RE = re.compile(
    r'^(received:|accepted:|published|revised:|correspondence|corresponding author|'
    r'e-?mail|©|copyright|all rights reserved|funding information|grant/award|'
    # 'keywords' 는 뒤에 콜론이나 대문자 항목이 올 때만 front matter 로 본다 —
    # 'keywords associated with the search strategy …' 같은 본문 문장을 지우면 안 된다.
    r'grant number|conflict of interest|orcid|keywords?\b(?=\s*[:：]|\s+[A-Z])|'
    r'key words\b(?=\s*[:：]|\s+[A-Z])|'
    r'how to cite|doi:|department of|division of|institute of|'
    r'college of medicine|©\s*\d{4})', re.I)
# 저자명 나열 라인: "Bo Ri Kim | Kun Hee Lee | ..." 또는 콤마 구분 다수 이름
AUTHORLIST_RE = re.compile(r'^([A-Z][a-z]+(?:\s[A-Z][a-z.]+){0,3})(\s*[|,]\s*[A-Z][a-z]+(?:\s[A-Z][a-z.]+){0,3}){1,}$')

# front matter 보강 — 첫 페이지 각주 블록(Elsevier/JAAD 계열)과 판권 표기.
# 전부 '줄 머리' 고정 패턴이라 본문 문장에 걸릴 여지가 없다.
FRONT_EXTRA_RE = re.compile(
    r'^(from a?the (?:department|division|institute|center|centre|unit|school|'
    r'faculty|hospital|clinic|laborator)|reprint requests|reprints not available|'
    r'accepted for publication|available online|first published|'
    r'issn\b|0190-9622|this article is protected|presented in part at|'
    r'j am acad dermatol \d{4}|conflicts? of interest|competing interests|'
    r'data availability\s*(?:statement|[:：])|data sharing statement|'
    r'author contributions|additional contributions|role of the funder|'
    r'open access\s*[:：]|funding\s*/?\s*support|funding statement|'
    r'this (?:study|work|research) was (?:sponsored|funded|supported) by|'
    r'medical writing (?:and editorial )?support|'
    # 'informed consent'·'ethical approval' 은 콜론이 붙은 표제일 때만.
    # 'Informed consent was waived by the boards …' 는 방법 본문이다(실측 오탐).
    r'ethical approval\s*[:：]|informed consent\s*[:：]|'
    r'https?://(?:dx\.)?doi\.org/)', re.I)
# 소속 나열: 한 줄에 기관 토큰이 둘 이상이면 본문 문장이 아니다
AFFIL_TOKEN_RE = re.compile(
    r'\b(?:Department of|Division of|College of|School of|Institute of|'
    r'University|Hospital|Medical Center|Medical Centre|Clinic)\b')
# 저자 학위 나열: "Joo Hee Lee, MD,a Ji Hae Lee, MD,a … and Jung Min Bae, MDa"
# 학위 토큰이 2개 이상 있고 쉼표가 섞인 줄만 잡는다(본문 문장에는 나오지 않는다).
DEGREE_RE = re.compile(r'\b(?:MD|PhD|MSc|MPH|MBBS|MBChB|DO|RN|BSc|BA|MS|DMD|DDS|PharmD)\b\.?,?')

# 참고문헌 구역 시작(헤딩형)
REF_HEAD_RE = re.compile(
    r'^(references?|bibliography|literature cited|references cited|'
    r'reference list)\s*[:.]?$', re.I)
# 참고문헌 항목형: "12. Smith J, Lee K. Title... 2019;12:34-40."
REF_ITEM_RE = re.compile(r'^\[?\d{1,3}[\].)]\s+[A-Z]')
# 참고문헌 목록임을 뒷받침하는 서명(연도;권:쪽 · et al · DOI · 번호항목)
REF_SIGN_RES = (
    re.compile(r'\b(?:19|20)\d{2}\s*[;:]\s*\d', re.I),
    re.compile(r'\bet al\b', re.I),
    re.compile(r'\bdoi\b|doi\.org/', re.I),
    REF_ITEM_RE,
    re.compile(r'\b[A-Z][a-z]+\s+[A-Z]{1,3}[,.]\s'),      # 'Smith JA, ' 저자 표기
)


# 구역이 '아직 참고문헌인가' 판정용(본문에도 흔한 'et al' 은 뺀 엄격 집합)
REF_CONT_RES = tuple(rx for rx in REF_SIGN_RES if rx.pattern != r'\bet al\b')
# 한 줄이 참고문헌 '항목'인가 판정용. 번호 패턴(REF_ITEM_RE)은 뺀다 — 번호는
# 진짜 섹션 헤딩('1. Introduction')에도 있어서 그것만으로는 가를 수 없다.
REF_ENTRY_RES = tuple(rx for rx in REF_SIGN_RES if rx is not REF_ITEM_RE)


def _looks_ref_entry(text: str) -> bool:
    """'1. Richard MA, Saint Aroman M, et al.' 같은 참고문헌 항목인가.

    번호 뒤에 저자 표기·et al·연도;권:쪽·DOI 중 하나라도 있으면 참고문헌이다.
    '1. Introduction' · '1 | INTRODUCTION' 같은 섹션 헤딩에는 하나도 없다.
    """
    return any(rx.search(text) for rx in REF_ENTRY_RES)


def _ref_signatures(lines: list[dict], start: int, window: int = 12,
                    rules=REF_SIGN_RES) -> int:
    """start 이후 window 줄 중 참고문헌 서명을 가진 줄 수."""
    n = 0
    for ln in lines[start:start + window]:
        t = ln["text"]
        if any(rx.search(t) for rx in rules):
            n += 1
    return n


def _body_size(spans_sizes) -> float:
    c = Counter()
    for sz, txt in spans_sizes:
        c[round(sz, 1)] += len(txt)
    return c.most_common(1)[0][0] if c else 10.0


def _line_text_and_cites(line, body: float):
    """한 줄의 span → (텍스트, 인용번호들). 위첨자 숫자는 본문에서 제거."""
    parts, cites = [], []
    for s in line["spans"]:
        t = s["text"]
        st = t.strip()
        is_super = bool(s.get("flags", 0) & SUPERSCRIPT) or s["size"] < body * 0.72
        if st and is_super and CITE_RE.match(st):
            for n in re.split(r'[,\-–]', st):
                if n:
                    cites.append(n)
            continue
        parts.append(t)
    return "".join(parts), cites


def _rotation(direction) -> int:
    """line['dir'] → 0(정방향) · 90(아래→위 세로) · 270(위→아래 세로) · 180."""
    dx, dy = direction if direction else (1.0, 0.0)
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 180
    return 90 if dy < 0 else 270


def _all_lines(doc, body: float) -> list[dict]:
    """모든 페이지의 라인을 원본(블록) 순서 그대로 수집한다(회전 라인 포함).

    라인 dict 에 페이지 기하(page_w/page_h)와 블록 번호(blk), 블록 첫 줄 여부(head),
    회전(rot)을 담아 두어 뒤 단계가 좌표 증거를 쓸 수 있게 한다.
    """
    out: list[dict] = []
    for pno in range(doc.page_count):
        pg = doc[pno]
        pw, ph = pg.rect.width, pg.rect.height
        for bi, b in enumerate(pg.get_text("dict")["blocks"]):
            if b.get("type") != 0:
                continue
            first = True
            for l in b["lines"]:
                if not l["spans"]:
                    continue
                text, cites = _line_text_and_cites(l, body)
                # 여기서 유니코드 정규화까지 끝낸다. 합자(ﬁ·ﬂ)를 그대로 두면
                # 'Conﬂicts of interest' 가 front matter 패턴에 걸리지 않아
                # 이해충돌·펀딩 문단이 본문으로 샌다(실측 결함).
                text = norm_text(text)
                if not text:
                    continue
                x0, y0, x1, y1 = l["bbox"]
                size = round(max((s["size"] for s in l["spans"]), default=body), 1)
                # 굵기/이탤릭: flags 비트 + 폰트 이름 접미(양쪽 다 확인)
                names = " ".join(s.get("font", "") for s in l["spans"])
                flag_or = 0
                for s in l["spans"]:
                    flag_or |= s.get("flags", 0)
                bold = bool(flag_or & BOLD) or bool(BOLD_FONT.search(names))
                italic = bool(flag_or & ITALIC) or bool(ITALIC_FONT.search(names))
                out.append({"text": text, "cites": cites, "size": size,
                            "bold": bold, "italic": italic, "page": pno,
                            "blk": bi, "head": first, "rot": _rotation(l.get("dir")),
                            "col": 0, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                            "page_w": pw, "page_h": ph, "skip": ""})
                first = False
    return out


COL_MIN_LINES = 4          # 한 단으로 인정할 최소 줄 수
COL_MIN_SHARE = 0.25       # 두 단 중 작은 쪽이 차지해야 할 최소 비율
COL_MAX_SPAN = 0.30        # 전폭 줄이 이보다 많으면 2단으로 보지 않는다


def _column_split(page_lines: list[dict]) -> float | None:
    """2단 조판의 경계 x 를 찾는다. 2단이 아니면 None.

    페이지 폭의 절반을 경계로 쓰면 안 된다 — 실측에서 왼쪽 단이 중앙선을
    0.3pt 넘겨 전폭으로 오판되고, 두 단이 y 순으로 뒤섞여 문장이 교차했다
    (10.1159/000537810). '가운데 빈 띠' 만 보는 방법도 약하다: 1쪽은 제목·
    저자·초록이 전폭이라 띠가 메워져 2단을 놓친다(10.5021/ad.2016.28.6.796).

    그래서 **줄 시작 x 의 봉우리들**을 경계 후보로 만들고, 각 후보를 실제로
    적용해 좌·우·전폭 줄 수를 세어 검증한다. 한쪽이 지나치게 작거나(표의 한
    열을 단으로 오인) 전폭 줄이 많으면 후보를 버린다.
    """
    n = len(page_lines)
    if n < 8:
        return None
    lo = min(l["x0"] for l in page_lines)
    hi = max(l["x1"] for l in page_lines)
    width = hi - lo
    if width < 100:
        return None
    floor = max(COL_MIN_LINES, n * 0.08)
    bins: dict[int, list[float]] = {}
    for l in page_lines:
        bins.setdefault(round(l["x0"] / 3) * 3, []).append(l["x0"])
    strong = sorted(k for k, v in bins.items() if len(v) >= floor)
    if len(strong) < 2:
        return None
    left = strong[0]
    best = None
    for k in strong[1:]:
        # 봉우리의 대표값은 구간 중심이 아니라 **실제 최소 x0** 여야 한다.
        # 중심을 쓰면 1pt 어긋난 것만으로 오른쪽 단 전체가 '전폭'으로 분류돼
        # 2단 판정이 무산된다(실측 10.1111/bjd.17850: 34줄이 전폭으로 샜다).
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
        score = min(nl, nr) - span
        if best is None or score > best[0]:
            best = (score, float(r))
    return best[1] if best else None





def _assign_columns(page_lines: list[dict]) -> bool:
    """페이지가 실제 2단인지 판정하고 각 줄의 col(0·1·-1=전폭)을 채운다.

    무조건 x중심으로 2단을 가정하면 1단 조판의 전폭 줄이 폭에 따라 좌/우로
    갈려 문단 순서가 깨진다(실측: 서한형 논문에서 본문 문단이 조각남).
    그래서 실제 여백(거터)을 찾고, '왼쪽 전용·오른쪽 전용 줄이 각각 충분히
    많고 전폭 줄이 적을 때'만 2단으로 본다.
    """
    if not page_lines:
        return False
    boundary = _column_split(page_lines)
    if boundary is None:
        for l in page_lines:
            l["col"] = -1          # 전부 전폭 취급 → 순수 y 순
        return False
    for l in page_lines:
        if l["x1"] <= boundary - 1:
            l["col"] = 0
        elif l["x0"] >= boundary - 1:
            l["col"] = 1
        else:
            l["col"] = -1
    return True


def _mark_alone(page_lines: list[dict]) -> None:
    """같은 단(col)에서 같은 행을 나눠 쓰는 줄인지 표시한다(alone=False).

    표의 셀은 언제나 같은 행에 형제가 있고, 진짜 섹션 제목은 자기 행을 혼자
    쓴다. 이 구분이 없으면 'Controls'·'Psoriasis' 같은 표 머리 셀이 헤딩으로
    승격돼 섹션이 폭발하고 본문 시작점이 표 한가운데로 밀린다(실측 결함).
    """
    for ln in page_lines:
        ln["alone"] = True
    bycol: dict[int, list[dict]] = {}
    for ln in page_lines:
        bycol.setdefault(ln["col"], []).append(ln)
    for group in bycol.values():
        group = sorted(group, key=lambda d: d["y0"])
        for i, a in enumerate(group):
            ha = max(1.0, a["y1"] - a["y0"])
            for b in group[i + 1:]:
                if b["y0"] >= a["y1"] - ha * 0.5:
                    break
                if b["x0"] > a["x1"] + 1 or b["x1"] < a["x0"] - 1:
                    a["alone"] = False
                    b["alone"] = False


def _order_lines(lines: list[dict]) -> list[dict]:
    """읽기순서 정렬. 전폭 줄을 경계로 밴드를 끊고, 밴드 안에서만 좌단→우단."""
    out: list[dict] = []
    for pno in sorted({l["page"] for l in lines}):
        page_lines = [l for l in lines if l["page"] == pno]
        _assign_columns(page_lines)
        _mark_alone(page_lines)
        page_lines.sort(key=lambda d: (round(d["y0"], 1), d["x0"]))
        band: list[dict] = []
        for ln in page_lines:
            if ln["col"] == -1:
                out.extend(sorted(band, key=lambda d: (d["col"], round(d["y0"], 1), d["x0"])))
                band = []
                out.append(ln)
            else:
                band.append(ln)
        out.extend(sorted(band, key=lambda d: (d["col"], round(d["y0"], 1), d["x0"])))
    return out


# ── 머리말/꼬리말 ────────────────────────────────────────────────────
_PAGENUM_RE = re.compile(r'^(?:[ivxlcdm]{1,7}|e?\d{1,4}(?:[-–]\d{1,4})?)$', re.I)


def _mark_running(lines: list[dict], npages: int) -> int:
    """반복 머리말/꼬리말·페이지번호를 skip 표시한다. 표시한 줄 수를 낸다.

    '여러 페이지에 같은 텍스트'만으로는 2~3쪽짜리 서한을 못 거른다. 그래서
    **위치 증거**를 같이 본다 — 페이지 상·하단 8% 안에 있는 짧은 줄만 후보로
    삼고, 그 안에서 (a) 2쪽 이상 반복이거나 (b) 페이지 번호꼴이면 지운다.
    본문은 이 띠에 거의 오지 않으므로 오탐이 구조적으로 막힌다.
    """
    edge: list[dict] = []
    for ln in lines:
        h = ln["page_h"] or 1.0
        if len(ln["text"]) <= 110 and (ln["y1"] <= h * 0.08 or ln["y0"] >= h * 0.92):
            edge.append(ln)
    pages_of: dict[str, set[int]] = {}
    for ln in edge:
        key = re.sub(r'\d+', "#", ln["text"][:60]).strip().lower()
        pages_of.setdefault(key, set()).add(ln["page"])
    n = 0
    for ln in edge:
        key = re.sub(r'\d+', "#", ln["text"][:60]).strip().lower()
        repeated = npages >= 2 and len(pages_of.get(key, ())) >= 2
        if repeated or _PAGENUM_RE.match(ln["text"].strip()):
            ln["skip"] = "running"
            n += 1
    return n


def _mark_first_page_footnote(lines: list[dict], body: float) -> int:
    """첫 페이지 하단의 '본문보다 작은 글씨' 각주 블록을 skip 표시한다.

    Elsevier/Wiley 계열은 소속·교신·펀딩·판권을 1쪽 아래쪽에 본문보다 작은
    글자로 싣는다. 문구가 제각각이라 패턴만으로는 다 못 걸러진다(실측: 주소
    뒷줄 'of Korea, 93, Jungbu-daero…' 가 본문으로 샜다).
    본문 글자 크기는 정의상 문서에서 가장 많이 쓰인 크기이므로, **그보다 작은**
    글씨가 1쪽 하단 25% 에 있으면 본문일 수 없다 — 이 두 조건을 동시에 만족할
    때만 지운다.
    """
    n = 0
    for ln in lines:
        if ln["page"] != 0 or ln["skip"]:
            continue
        if ln["size"] <= body - 0.6 and ln["y0"] >= (ln["page_h"] or 1.0) * 0.75:
            ln["skip"] = "front"
            n += 1
    return n


# ── front matter ────────────────────────────────────────────────────
def _is_author_list(text: str) -> bool:
    """저자 나열 줄인가. 구분자·조각 모양까지 봐서 제목 오탐을 막는다.

    'International, Multidisciplinary Electronic Delphi Survey' 처럼 쉼표가 든
    섹션 제목이 저자 나열로 잡히면, 블록 전파 때문에 그 아래 방법 본문까지
    통째로 사라진다(실측 10.1001/jamadermatol.2026.0294, 6줄).
    """
    t = (text or "").strip()
    if len(t) >= 160 or not AUTHORLIST_RE.match(t):
        return False
    parts = [p.strip() for p in re.split(r'\s*[|,]\s*', t) if p.strip()]
    if len(parts) < 2:
        return False
    if "|" in t or len(parts) >= 3:
        return True
    return all(len(p.split()) >= 2 for p in parts)   # 이름 두 토막 이상씩


def _is_frontmatter(text: str) -> bool:
    t = text.strip()
    if FRONT_RE.match(t) or FRONT_EXTRA_RE.match(t):
        return True
    if _is_author_list(t):
        return True
    # 학위 토큰 2개 이상 + 쉼표 = 저자 나열 줄
    if len(t) < 240 and "," in t and len(DEGREE_RE.findall(t)) >= 2:
        return True
    # 기관 토큰 2개 이상 = 소속 나열 줄('… Korea (Ju, Lee, Bae); Department of …')
    if len(t) < 300 and len(AFFIL_TOKEN_RE.findall(t)) >= 2:
        return True
    return False


FRONT_BLOCK_LINES = 12       # 전파 허용 블록 크기(줄)
FRONT_BLOCK_CHARS = 700      # 전파 허용 블록 크기(글자)


def _mark_frontmatter(lines: list[dict]) -> int:
    """front matter 를 skip 표시한다. 작은 블록 안에서는 뒤로 전파한다.

    소속·교신·펀딩·판권은 한 블록 안에서 여러 줄로 이어지므로 첫 매치 이후를
    같이 빼야 주소 뒷줄이 본문으로 새지 않는다. 다만 전파는 **작은 블록**
    (≤12줄·≤700자)에서만 한다 — 초록 끝에 'Keywords:' 가 붙은 큰 블록을 통째로
    지워 본문이 사라지는 실측 결함이 있었다(10.5124/jkma.2020.63.12.748).
    """
    per_block: dict[tuple[int, int], list[dict]] = {}
    for ln in lines:
        per_block.setdefault((ln["page"], ln["blk"]), []).append(ln)
    n = 0
    for blk in per_block.values():
        small = (len(blk) <= FRONT_BLOCK_LINES
                 and sum(len(l["text"]) for l in blk) <= FRONT_BLOCK_CHARS)
        first = next((i for i, l in enumerate(blk) if _is_frontmatter(l["text"])), None)
        if first is None:
            continue
        targets = blk[first:] if small else [l for l in blk if _is_frontmatter(l["text"])]
        for ln in targets:
            if not ln["skip"]:
                ln["skip"] = "front"
                n += 1
    return n


# ── 참고문헌 구역 ────────────────────────────────────────────────────
def _mark_references(lines: list[dict]) -> int:
    """참고문헌 헤딩(또는 번호목록 런) 이후를 skip 표시한다.

    헤딩형을 먼저 찾고, 없으면 '번호. 대문자로 시작' 항목이 5줄 이상 연속되는
    지점을 시작으로 본다. 문서 앞쪽(35% 이전)의 매치는 무시한다.

    **후보를 반드시 검증한다.** 통계표의 기준범주 셀 'Reference'/'reference' 가
    헤딩으로 오인돼 결과·고찰이 통째로 잘려 나가는 실측 결함이 있었다(1825줄).
    그래서 (a) 대문자로 시작하고 (b) 바로 다음 12줄에 참고문헌 서명(연도;권:쪽 ·
    et al · DOI · 번호항목 · 저자표기)이 4줄 이상일 때만 인정한다.

    **끝도 찾는다.** 별쇄본 PDF 는 앞 논문의 참고문헌 뒤에 이 논문 본문이
    이어지기도 한다(10.1111/bjd.18427). 참고문헌 서명이 끊기면 거기서 멈춘다.
    """
    n_total = len(lines)
    if n_total < 20:
        return 0
    floor = int(n_total * 0.35)
    start = -1
    for i, ln in enumerate(lines):
        if i < floor or ln["skip"]:
            continue
        raw = ln["text"].strip()
        core = re.sub(r'^\d+[\s.|)]*', '', clean_heading(raw).strip().lower()).strip()
        if (raw[:1].isupper() and REF_HEAD_RE.match(core) and len(raw) <= 40
                and _ref_signatures(lines, i + 1) >= 4):
            start = i
            break
    if start < 0:
        # '연속 5줄'로 세면 안 된다 — 참고문헌 항목의 둘째 줄부터는 번호가 없어
        # 연속이 끊긴다. 창(25줄) 안에 번호 항목이 몇 개인지로 센다.
        for i in range(floor, max(floor, len(lines) - 8)):
            if not REF_ITEM_RE.match(lines[i]["text"]):
                continue
            items = sum(1 for l in lines[i:i + 25] if REF_ITEM_RE.match(l["text"]))
            if items >= 4 and _ref_signatures(lines, i, 25) >= 6:
                start = i
                break
    if start < 0:
        return 0
    end = n_total
    i = start + 1
    while i < n_total:
        if n_total - i < 25:                       # 꼬리는 그대로 참고문헌으로 본다
            break
        if _ref_signatures(lines, i, 20, REF_CONT_RES) < 2:
            end = i
            break
        i += 10
    n = 0
    for ln in lines[start:end]:
        if not ln["skip"]:
            ln["skip"] = "refs"
            n += 1
    n += _mark_refs_geometric(lines, start, end)
    return n


def _mark_refs_geometric(lines: list[dict], start: int, end: int) -> int:
    """참고문헌 시작 줄과 **같은 쪽**에서 읽기순서상 앞으로 밀려난 항목을 마저 지운다.

    참고문헌 구역은 flow(읽기순서) 위에서 연속이라고 가정하지만, 참고문헌 쪽은
    한 항목이 단 경계를 넘나들어(전폭 줄) 밴드가 끊기는 일이 잦고 그러면 좌·우
    단이 뒤섞여 항목 일부가 헤딩보다 **앞**에 온다(실측 10.1007/s00256-009-0872-x:
    'References' 헤딩이 459줄 중 448번째로 잡혀 앞선 항목 40여 줄이 본문에 남았다).

    좌표는 뒤섞이지 않으므로 같은 쪽 안에서 '헤딩보다 아래(또는 오른쪽 단)'인
    줄만 추가로 지운다. **참고문헌 서명이 있는 줄로 한정**해 본문을 건드리지
    않는다 — 별쇄본에서 참고문헌 뒤에 오는 다음 논문 본문은 서명이 없다.
    """
    head = lines[start]
    page = head["page"]
    n = 0
    for i, ln in enumerate(lines):
        if i >= start or ln["skip"] or ln["page"] != page:
            continue
        after = (ln["col"] > head["col"]
                 or (ln["col"] == head["col"] and ln["y0"] >= head["y0"] - 1))
        if not after or start >= end:
            continue
        if any(rx.search(ln["text"]) for rx in REF_CONT_RES):
            ln["skip"] = "refs"
            n += 1
    return n


# ── 그림·표 캡션 문법 ────────────────────────────────────────────────
_FIGW = r"(?:FIGURES?|FIGS?|Figures?|Figs?|FIG|Fig)"
_TABW = r"(?:TABLES?|Tables?|TABLE|Table|Tbl)"
_CAPNUM = r"(?:\d{1,2}|[IVX]{1,5}|[SE]\d{1,2})"
# 구분자: Nature 계열 '|', Elsevier 계열 '—'(붙여 씀), Springer 계열 공백만.
# ASCII 하이픈은 양쪽 공백이 있을 때만 인정한다('Fig 1-3' 같은 범위 표기 보호).
_CAPSEP = r"(?:\s*[.:|—–‒]\s*|\s+-\s+|\s+)"
_SUPPW = (r"(?:Supplementary|Supplemental|Supporting|Online|Appendix|"
          r"SUPPLEMENTARY|SUPPLEMENTAL)")
CAPTION_RE = re.compile(
    r"^(?P<supp>" + _SUPPW + r"\s+|e(?=Table|Figure))?"
    r"(?P<kind>" + _FIGW + r"|" + _TABW + r")\s*\.?\s*"
    r"(?:(?P<num>" + _CAPNUM + r")(?P<panel>[a-h](?![A-Za-z]))?(?P<sep>" + _CAPSEP + r")"
    r"|(?P<nonum>[.:]\s+))"           # 'Figure. …' — 표·그림이 하나뿐인 논문
    r"(?P<rest>.+)$", re.S)
# 설명 첫 낱말이 이것이면 캡션이 아니라 본문('Table 1 shows …')
CAP_STOP_RE = re.compile(
    r"^(?:shows?|showed|shown|displays?|demonstrat\w*|presents?|lists?|summar\w*|"
    r"depict\w*|illustrat\w*|reports?|describ\w*|provid\w*|indicat\w*|contain\w*|"
    r"gives?|and|or|of|in|on|to|for|the|an|a|were|was|is|are|also|see|from|but|"
    r"which|that|this|these|those|continued?|cont|above|below|legend)\b", re.I)
# 조판 자간으로 흩어진 라벨: 'TA B L E 4', 'FI G U R E 1'
_SPACED_LABEL_RE = re.compile(
    r"^(?P<pre>\s*)"
    r"(?P<kw>[Tt]\s*[Aa]\s*[Bb]\s*[Ll]\s*[Ee]|[Ff]\s*[Ii]\s*[Gg](?:\s*[Uu]\s*[Rr]\s*[Ee])?)"
    r"(?=\s*\.?\s*[0-9IVX])")
# 그림 위 패널문자가 캡션 블록 앞에 새어 든 경우: 'b c Fig. 6 | …'
_LEAD_JUNK_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[.)])?\s+){1,6}(?=(?:" + _FIGW + r"|" + _TABW + r")\b)")
_PANEL_HEAD_RE = re.compile(r"^([a-h])\s+([A-Z(])")
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
          "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
          "XIV": 14, "XV": 15}
MIN_CAPTION_DESC = 20      # C3: 번호 뒤 설명 최소 길이
MAX_CAPTION_CHARS = 1500


def _cap_prep(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()
    m = _SPACED_LABEL_RE.match(s)
    if m:
        s = m.group("pre") + re.sub(r"\s+", "", m.group("kw")) + s[m.end():]
    j = _LEAD_JUNK_RE.match(s)
    return s[j.end():] if j else s


def parse_caption(text: str, *, min_desc: int = MIN_CAPTION_DESC) -> dict | None:
    """문자열 **머리**에서 그림/표 캡션을 읽는다. 캡션이 아니면 None.

    반환: kind('fig'|'tab') · label · raw(번호 원문) · num(정수|None) ·
          supp(보조자료 여부) · desc(설명) · caption(완성 캡션)
    """
    t = _cap_prep(text)
    m = CAPTION_RE.match(t)
    if not m:
        return None
    desc = m.group("rest").strip()
    # Springer 패널문자 흡수: 'Fig. 1 a The patient …' → 설명은 'The patient …'
    if not (m.group("sep") or "").strip():
        pm = _PANEL_HEAD_RE.match(desc)
        if pm:
            desc = desc[pm.start(2):]
    if len(desc) < min_desc:
        return None
    # C4 — 설명 첫 낱말 판정. 앞머리 괄호·기호를 떼고 보되(‘(continued) …’ 차단),
    # 한 글자짜리는 패널 라벨('(a) Clinical pictures …')이므로 검사에서 뺀다.
    head_word = re.match(r"[A-Za-z]+", desc.split()[0].lstrip("([{'\"*†‡§"))
    core = head_word.group(0) if head_word else ""
    if len(core) >= 2 and CAP_STOP_RE.match(core) and not core[:1].isupper():
        return None
    raw = m.group("num")
    if raw is None:                       # 'Figure. …' — 번호 없는 단일 그림/표
        num, supp = 1, bool(m.group("supp"))
    else:
        num = int(raw) if raw.isdigit() else _ROMAN.get(raw.upper())
        supp = bool(m.group("supp")) or num is None
    if len(desc) > MAX_CAPTION_CHARS:
        desc = desc[:MAX_CAPTION_CHARS].rstrip() + "…"
    label = m.group("kind")
    prefix = (m.group("supp") or "").strip()      # 'Supplementary' 표시는 살린다
    head = f"{label} {raw}" if raw else label
    if prefix:
        head = f"{prefix} {head}"
    return {"kind": "fig" if label.lower().startswith("fig") else "tab",
            "label": label, "raw": raw, "num": num, "supp": supp,
            "desc": desc, "caption": clean_paragraph(f"{head}. {desc}")}


# ── 캡션·표 영역 추출 ────────────────────────────────────────────────
def _blocks_of(lines: list[dict]) -> list[dict]:
    """라인들을 (page, blk) 블록으로 묶는다. 블록 텍스트·bbox·회전을 함께 낸다."""
    order: list[tuple[int, int]] = []
    grp: dict[tuple[int, int], list[dict]] = {}
    for ln in lines:
        key = (ln["page"], ln["blk"])
        if key not in grp:
            grp[key] = []
            order.append(key)
        grp[key].append(ln)
    out = []
    for key in order:
        ls = grp[key]
        rot = Counter(l["rot"] for l in ls).most_common(1)[0][0]
        if rot == 90:                      # 아래→위 세로 조판: x 증가 = 다음 줄
            ls = sorted(ls, key=lambda l: l["x0"])
        elif rot == 270:
            ls = sorted(ls, key=lambda l: -l["x0"])
        out.append({
            "page": key[0], "blk": key[1], "lines": ls, "rot": rot,
            "text": " ".join(l["text"] for l in ls),
            "x0": min(l["x0"] for l in ls), "x1": max(l["x1"] for l in ls),
            "y0": min(l["y0"] for l in ls), "y1": max(l["y1"] for l in ls),
            "page_w": ls[0]["page_w"], "page_h": ls[0]["page_h"],
        })
    return out


def _caption_blocks(blocks: list[dict]) -> list[dict]:
    """캡션인 블록만 골라 낸다(C1~C4). 라벨만 있는 블록은 다음 블록을 한 번 잇는다."""
    caps = []
    for i, b in enumerate(blocks):
        # 참고문헌/머리말 구역이라는 이유로 캡션 후보를 버리지 않는다(실측 결과):
        #   · JAAD 계열은 그림 캡션 페이지가 참고문헌 **뒤**에 온다 → 7개 유실
        #   · Wiley 계열 표 캡션은 러닝헤드 띠 안에 있고, 'TABLE 2'/'TABLE 3' 이
        #     숫자 정규화 후 같은 키가 되어 반복 머리말로 오인됐다 → 4개 유실
        # 캡션 문법(C1~C4) 자체가 이미 참고문헌 항목('12. Smith J …')을 배제한다.
        got = parse_caption(b["text"])
        joined = False
        if got is None and i + 1 < len(blocks):
            nb = blocks[i + 1]
            near = (nb["page"] == b["page"] and nb["rot"] == b["rot"]
                    and 0 <= nb["y0"] - b["y1"] < 30
                    and not (nb["x1"] < b["x0"] or nb["x0"] > b["x1"]))
            if near and parse_caption(nb["text"]) is None:
                got = parse_caption(b["text"] + " " + nb["text"])
                joined = got is not None
        if got is None:
            continue
        got = dict(got)
        got.update({"block": b, "joined_next": joined})
        caps.append(got)
    return caps


def _rows_from_words(words: list[tuple], rot: int, ytol: float) -> list[list[tuple]]:
    """단어들을 '한 줄'로 묶는다. 회전 조판이면 진행축이 x 다."""
    if rot == 90:
        adv, read = (lambda w: w[0]), (lambda w: -w[3])
    elif rot == 270:
        adv, read = (lambda w: -w[2]), (lambda w: w[1])
    else:
        adv, read = (lambda w: w[1]), (lambda w: w[0])
    ws = sorted(words, key=lambda w: (round(adv(w), 1), read(w)))
    rows, cur = [], []
    for w in ws:
        if cur and (adv(w) - adv(cur[-1])) > ytol:
            rows.append(sorted(cur, key=read))
            cur = []
        cur.append(w)
    if cur:
        rows.append(sorted(cur, key=read))
    return rows


def _split_cells(row: list[tuple], rot: int, gap: float) -> list[str]:
    """한 행을 가로 공백으로 셀 분할."""
    lo = (lambda w: -w[3]) if rot == 90 else ((lambda w: w[1]) if rot == 270 else (lambda w: w[0]))
    hi = (lambda w: -w[1]) if rot == 90 else ((lambda w: w[3]) if rot == 270 else (lambda w: w[2]))
    cells, cur, prev = [], [], None
    for w in row:
        if prev is not None and (lo(w) - hi(prev)) > gap:
            cells.append(" ".join(x[4] for x in cur))
            cur = []
        cur.append(w)
        prev = w
    if cur:
        cells.append(" ".join(x[4] for x in cur))
    return [norm_text(c) for c in cells if c.strip()]


def _table_body(page, rect: tuple[float, float, float, float], rot: int,
                body: float) -> str:
    """표 영역의 단어를 행/열로 복원한다. 열이 안 서면 행 텍스트만 남긴다.

    find_tables() 가 이 코퍼스에서 쓸 수 없어(모듈 docstring 실측) 좌표로 직접
    복원한다. 격자는 '행의 40% 이상이 2열 이상으로 갈릴 때'만 채택한다 —
    억지로 세운 격자는 셀이 어긋나 오히려 읽기 어렵다.
    """
    x0, y0, x1, y1 = rect
    words = [w for w in page.get_text("words")
             if w[0] >= x0 - 2 and w[2] <= x1 + 2 and w[1] >= y0 - 2 and w[3] <= y1 + 2]
    if len(words) < 4:
        return ""
    rows = _rows_from_words(words, rot, max(2.5, body * 0.45))
    if len(rows) < 2:
        return ""
    gaps = []
    lo = (lambda w: -w[3]) if rot == 90 else ((lambda w: w[1]) if rot == 270 else (lambda w: w[0]))
    hi = (lambda w: -w[1]) if rot == 90 else ((lambda w: w[3]) if rot == 270 else (lambda w: w[2]))
    for r in rows:
        for a, b in zip(r, r[1:]):
            g = lo(b) - hi(a)
            if g > 0:
                gaps.append(g)
    med = statistics.median(gaps) if gaps else body * 0.3
    thr = min(max(med * 2.5, body * 0.45), body * 1.8)
    grid = [_split_cells(r, rot, thr) for r in rows]
    grid = [g for g in grid if g]
    if not grid:
        return ""
    ncol = max(len(g) for g in grid)
    multi = sum(1 for g in grid if len(g) >= 2)
    if ncol < 2 or multi < len(grid) * 0.4:
        return "\n".join(" ".join(g) for g in grid)      # 격자 포기 → 행 텍스트
    grid = [g + [""] * (ncol - len(g)) for g in grid]
    esc = lambda c: c.replace("|", "\\|")                # noqa: E731
    md = ["| " + " | ".join(esc(c) for c in grid[0]) + " |",
          "| " + " | ".join(["---"] * ncol) + " |"]
    for g in grid[1:]:
        md.append("| " + " | ".join(esc(c) for c in g) + " |")
    return "\n".join(md)


_PROSE_TAIL_RE = re.compile(r'[.?!][")\']?$')
# 표 영역의 가로 띠 허용 오차(첫 블록에만 적용). 캡션 띠와 이만큼까지 떨어져
# 있어도 같은 표의 머리행으로 본다. 실측 근거 두 개 사이에 들어가야 한다 —
#   허용해야 함: 10.1111/jdv.19395 캡션↔머리행 6.1pt (body 10.0 → 8.0)
#   막아야 함  : 10.1111/jdv.15936 단 사이 여백 11.3pt (body 9.0 → 7.2)
TABLE_BAND_SLACK_EM = 0.8      # 본문 글자 크기 배수
TABLE_BAND_SLACK_MAX = 10.0    # 절대 상한(pt)


def _looks_prose(text: str) -> bool:
    """표 영역 종료 판정용 — '표 셀'이 아니라 '이어지는 산문'인가."""
    t = text.strip()
    if len(t) < 60:
        return False
    words = t.split()
    if len(words) < 10:
        return False
    digits = sum(c.isdigit() for c in t)
    return digits <= len(t) * 0.12


def _table_region(cap: dict, blocks: list[dict], body: float,
                  cap_keys: set[tuple[int, int]]) -> tuple | None:
    """표 캡션 아래(회전이면 옆) 표 영역의 bbox 를 정한다. 없으면 None.

    멈추는 조건(오염 차단):
      · 다른 캡션 블록을 만나면
      · 진행 방향 간격이 본문 글자 2.2배를 넘으면(표와 본문 사이의 큰 공백)
      · 산문으로 보이는 블록이 연달아 2개 나오면(본문으로 되돌아온 것)

    가로(띠) 방향은 두 단계로 본다.
      · **첫 블록**(띠를 세우는 행)만 작은 슬랙을 허용한다. 캡션이 표보다 좁아
        표 머리행이 캡션 오른쪽에서 시작하는 조판이 있다(실측 10.1111/jdv.19395:
        캡션 x[45.7,261.8], 머리행 x[267.8,542.1] — 6.1pt 떨어져 있다).
      · **띠가 선 뒤에는 실제 교집합을 요구한다.** 예전에는 ±12pt 슬랙을 끝까지
        허용해서, 단 사이 여백이 11.3pt 인 2단 조판에서 옆 단이 0.7pt 차이로
        통과했고 한 번 들어오자 띠가 그 단까지 넓어져 오른쪽 단 본문 21줄이
        통째로 표에 빨려 들어갔다(실측 10.1111/jdv.15936). 이 조판에서 옆 단
        블록은 표 머리행보다 아래에 있어 첫 블록이 될 수 없으므로 두 규칙에
        모두 걸린다.
    """
    b = cap["block"]
    rot = b["rot"]
    same = [x for x in blocks
            if x["page"] == b["page"] and x["rot"] == rot and x is not b]
    if rot == 90:
        after = sorted([x for x in same if x["x0"] >= b["x1"] - 1], key=lambda x: x["x0"])
        adv0, adv1 = (lambda x: x["x0"]), (lambda x: x["x1"])
        band0, band1 = (lambda x: x["y0"]), (lambda x: x["y1"])
    elif rot == 270:
        after = sorted([x for x in same if x["x1"] <= b["x0"] + 1], key=lambda x: -x["x1"])
        adv0, adv1 = (lambda x: -x["x1"]), (lambda x: -x["x0"])
        band0, band1 = (lambda x: x["y0"]), (lambda x: x["y1"])
    else:
        after = sorted([x for x in same if x["y0"] >= b["y1"] - 1], key=lambda x: x["y0"])
        adv0, adv1 = (lambda x: x["y0"]), (lambda x: x["y1"])
        band0, band1 = (lambda x: x["x0"]), (lambda x: x["x1"])

    # 캡션이 조판 단을 가로지르면 표도 전폭으로 본다
    lo, hi = band0(b), band1(b)
    wide = (hi - lo) > (b["page_w"] if rot == 0 else b["page_h"]) * 0.55
    if wide:
        lo, hi = -1e9, 1e9
    picked: list[dict] = []
    cursor = adv1(b)
    prose_run = 0
    slack = min(TABLE_BAND_SLACK_MAX, body * TABLE_BAND_SLACK_EM)
    for x in after:
        if (x["page"], x["blk"]) in cap_keys:
            break
        tol = slack if not picked else 0.0    # 슬랙은 띠를 세울 때만
        if min(band1(x), hi) - max(band0(x), lo) < -tol:
            continue                                   # 다른 단의 블록
        if adv0(x) - cursor > body * 2.2:
            break
        if _looks_prose(x["text"]):
            prose_run += 1
            if prose_run >= 2:
                break
            continue
        prose_run = 0
        picked.append(x)
        cursor = max(cursor, adv1(x))
        lo, hi = min(lo, band0(x)), max(hi, band1(x))
    picked = [x for x in picked if not _looks_prose(x["text"])]
    if not picked:
        return None
    return (min(x["x0"] for x in picked), min(x["y0"] for x in picked),
            max(x["x1"] for x in picked), max(x["y1"] for x in picked)), picked


def _figures_tables(doc, lines: list[dict], body: float
                    ) -> tuple[list[Figure], list[Table]]:
    """캡션 블록에서 figures/tables 를 만들고, 쓰인 줄은 skip 표시한다."""
    blocks = _blocks_of(lines)
    caps = _caption_blocks(blocks)
    cap_keys = {(c["block"]["page"], c["block"]["blk"]) for c in caps}
    bykey = {(b["page"], b["blk"]): b for b in blocks}

    figures: list[Figure] = []
    tables: list[Table] = []
    seen: set[tuple[str, str]] = set()
    for c in caps:
        b = c["block"]
        num_tag = c["raw"] or str(c["num"])
        # 보조자료 여부를 키에 넣는다. 넣지 않으면 'Figure 1' 과 'Supplementary
        # Fig 1' 이 같은 키가 되어 뒤에 온 쪽이 통째로 버려진다(실측 9건:
        # 10.5021/ad.23.151 은 Supplementary Fig 1~4 를 전부 잃었다).
        key = (c["kind"], bool(c["supp"]), num_tag.upper())
        for ln in b["lines"]:
            if not ln["skip"]:
                ln["skip"] = "caption"
        if c.get("joined_next"):
            nb = bykey.get((b["page"], b["blk"] + 1))
            for ln in (nb or {}).get("lines", []):
                if not ln["skip"]:
                    ln["skip"] = "caption"
        if key in seen:                    # 같은 번호가 여러 쪽에 이어짐 → 첫 것만
            continue
        seen.add(key)
        tag = "S" if c["supp"] else ""
        if c["kind"] == "fig":
            figures.append(Figure(id=f"pdffig{tag}{num_tag}", caption=c["caption"]))
            continue
        md = ""
        got = _table_region(c, blocks, body, cap_keys)
        if got:
            rect, picked = got
            md = _table_body(doc[b["page"]], rect, b["rot"], body)
            for x in picked:
                for ln in x["lines"]:
                    if not ln["skip"]:
                        ln["skip"] = "tablebody"
        tables.append(Table(id=f"pdftab{tag}{num_tag}", caption=c["caption"],
                            markdown=md))
    return figures, tables


def _prepare(doc) -> tuple[float, list[dict], list[dict]]:
    """공통 전처리 — 본문 글자 크기 추정 → 라인 수집 → 잡음 구간 표시.

    반환: (본문 글자 크기, 전체 라인[회전 포함], 본문 흐름 라인[읽기순서]).
    잡음 표시는 라인 dict 의 skip 필드에 남으므로 세 진입점이 **같은 규칙**을 쓴다.
    """
    body = _body_size([(s["size"], s["text"]) for pno in range(doc.page_count)
                       for b in doc[pno].get_text("dict")["blocks"]
                       if b.get("type") == 0
                       for l in b["lines"] for s in l["spans"]])
    lines = _all_lines(doc, body)
    _mark_running(lines, doc.page_count)            # 머리말/꼬리말·페이지번호
    _mark_frontmatter(lines)                        # 저자·소속·교신·판권
    _mark_first_page_footnote(lines, body)          # 1쪽 하단 각주 블록
    flow = _order_lines([l for l in lines if l["rot"] == 0])
    _mark_references(flow)                          # 참고문헌 구역
    return body, lines, flow


def pdf_figures_tables(path: str | Path) -> tuple[list[Figure], list[Table]]:
    """PDF 한 편에서 그림·표를 회수한다(상위 단계 보완용 공개 진입점).

    GROBID/PMC 산출물이 그림·표를 놓쳤을 때 이 결과로 채워 넣을 수 있다.
    """
    with fitz.open(str(path)) as doc:
        body, lines, _flow = _prepare(doc)
        return _figures_tables(doc, lines, body)


# ── 헤딩·문단 ───────────────────────────────────────────────────────
def _is_heading(ln: dict, body: float) -> bool:
    t = ln["text"].strip()
    if not (3 <= len(t) <= 90):
        return False
    if parse_caption(t, min_desc=1):        # 'Table 1. …' 은 헤딩이 아니다
        return False
    words = len(t.split())
    emphasized = ln["size"] >= body * 1.05 or ln["bold"] or ln.get("italic")
    # 자간 아티팩트('I N TRODUC TION')도 섹션 키워드로 인식되도록 복원 후 판정
    core = re.sub(r'^\d+[\s.|)]*', '', clean_heading(t)).strip().lower().rstrip(":")
    first_word = core.split(" ")[0] if core else ""
    # 번호 매김 섹션 ("1 | INTRODUCTION", "3.2.2 Neoplasms")
    if NUM_HEAD.match(t) and (emphasized or t.isupper()):
        return True
    # 섹션 키워드
    if (core in SECTION_KEYS or first_word in SECTION_KEYS) and emphasized:
        return True
    # 폰트 강조 + 짧은 제목형 라인(굵기/이탤릭 전용 폰트로 표기된 섹션 제목)
    # alone: 같은 행에 형제가 있으면 표 셀이다 → 헤딩으로 승격하지 않는다
    if (emphasized and words <= 8 and t[0:1].isupper() and ln.get("alone", True)
            and not t.rstrip().endswith((".", ",", ";", ":"))
            and not _is_frontmatter(t) and not _is_author_list(t)
            and sum(c.isdigit() for c in t) <= 4):
        return True
    return False


def _para_break(prev: dict, ln: dict, body: float) -> bool:
    """이전 줄과 현재 줄 사이가 새 문단 경계인가."""
    if ln["page"] != prev["page"] or ln["col"] != prev["col"]:
        return True
    # 같은 baseline 이 조각난 것(목록 라벨 'A.' + 항목명, 셀 분할)은 경계가 아니다.
    # 이 예외가 없으면 아래 들여쓰기 규칙이 한 줄을 문단 여럿으로 쪼개 버린다.
    if ln["y0"] < prev["y1"] - 1.0:
        return False
    gap = ln["y0"] - prev["y1"]
    if gap > body * 0.7:          # 줄 간격보다 큰 세로 공백 = 문단 경계
        return True
    # 들여쓰기 시작(이전 줄이 문장 종료) → 새 문단
    if ln["x0"] - prev["x0"] > body * 0.8 and prev["text"].rstrip().endswith((".", ":", "?")):
        return True
    return False


def _join_lines(lines: list[dict]) -> str:
    """문단 라인 병합 + 줄바꿈 하이픈 분철 복원."""
    out = ""
    for ln in lines:
        t = ln["text"].strip()
        if not t:
            continue
        if out.endswith("-") and not out.endswith((" -", "--")) and t[:1].islower():
            out = out[:-1] + t          # 분철 복원: "dis-" + "order" → "disorder"
        elif out:
            out += " " + t
        else:
            out = t
    # 러닝헤더·자간 아티팩트 등 추출 결함 수리(textfix, 결함 발생 지점에서 차단)
    return clean_paragraph(_tidy_punct(norm_text(out)))


PROSE_RUN_LINES = 4        # 본문 시작 판정: 이만큼 연속돼야 산문으로 본다
PROSE_RUN_WORDS = 8        # 한 줄이 '산문 조각'으로 인정되는 최소 단어 수
BODY_START_MAX_FRAC = 0.5  # '1번 섹션' 헤딩을 본문 시작으로 인정할 최대 위치


def _first_prose_index(lines: list[dict], body: float) -> int:
    """헤딩이 없는 조판(서한·증례)에서 본문이 시작되는 줄.

    '단어 8개 이상인 줄이 4줄 연속'되는 첫 지점을 본문 시작으로 본다 —
    제목·저자·소속은 그렇게 이어지지 않으므로 구조적으로 건너뛴다.
    블록 단위로 재면 2단 조판에서 PyMuPDF 가 한 줄씩 블록을 끊는 탓에
    기준을 못 넘겨 본문 시작이 한참 뒤로 밀린다(실측 10.1111/cup.13358).
    못 찾으면 0(현행 동작 유지).
    """
    run = 0
    for i, ln in enumerate(lines):
        t = ln["text"]
        if len(t.split()) >= PROSE_RUN_WORDS and not _is_frontmatter(t):
            run += 1
            if run >= PROSE_RUN_LINES:
                return i - run + 1
        else:
            run = 0
    return 0


# 서한(letter) 본문 시작 상투구 — 별쇄본에 여러 편이 섞여 있을 때의 기준점
LETTER_OPEN_RE = re.compile(
    r'^(to the editor|dear editor|dear sir|sir,|madam,|we read with (?:great )?interest|'
    r'we have read with)', re.I)


def _title_index(lines: list[dict], title: str, span: int = 6) -> int:
    """PDF 안에서 이 논문 제목이 끝나는 줄 다음 인덱스. 못 찾으면 -1.

    별쇄본 PDF 는 앞 논문의 꼬리(참고문헌·저자정보)가 1쪽 앞머리에 그대로
    붙어 있는 일이 흔하다(실측 3편). 제목을 찾으면 '어느 논문이 우리 것인지'가
    확정되므로, 제목 앞은 모두 남의 글로 보고 버린다.
    """
    key = re.sub(r'[^a-z0-9]', "", (title or "").lower())[:40]
    if len(key) < 20:
        return -1
    for i in range(len(lines)):
        acc = ""
        for j in range(i, min(i + span, len(lines))):
            acc += re.sub(r'[^a-z0-9]', "", lines[j]["text"].lower())
            if key in acc:
                return j + 1
            if len(acc) > len(key) + 60:
                break
    return -1


def _body_start(lines: list[dict], body: float, title: str = "") -> int:
    """본문 시작 인덱스: Introduction/1번 섹션 우선(제목·저자·초록 스킵).

    헤딩이 문서 한참 뒤에서야 처음 잡히는 조판(별쇄본·표가 앞선 서한)에서
    headings[0] 을 그대로 쓰면 앞쪽 본문을 통째로 버린다. 그래서 서한 상투구·
    첫 산문 줄과 함께 **가장 이른 지점**을 고르고, 헤딩은 문서 앞 25% 안에
    있을 때만 후보로 쓴다.

    제목을 찾았으면(별쇄본) 그 지점이 곧 이 논문의 시작이므로 더 뒤로 밀지
    않는다 — 저자·소속은 front matter 규칙이 이미 걸러낸다. 밀었다가 짧은
    본문이 통째로 잘리는 실측 결함이 있었다(10.3904/kjim.2018.200).

    **'1번 섹션' 후보는 검증한다.** 참고문헌 첫 항목 '1. Richard MA, …' 이
    `^1[\\s.|)]\\s` 에 걸려 1번 섹션으로 오인되면 본문 시작이 문서 끝으로 밀려
    본문이 통째로 사라진다(실측 10.1001/jamadermatol.2026.0294: 974줄 중
    964줄을 버려 회수율 0.000). 그래서 (a) 참고문헌 서명이 있는 줄은 후보에서
    빼고 (b) 후보는 문서 앞 절반 안에 있어야 한다 — 진짜 서론이 문서 후반부에
    처음 나오는 조판은 없다.
    """
    floor = _title_index(lines, title)          # 제목보다 앞은 남의 논문
    rest = lines[floor:] if floor > 0 else lines
    base = floor if floor > 0 else 0
    headings = [(i, ln) for i, ln in enumerate(rest) if _is_heading(ln, body)]
    limit = len(rest) * BODY_START_MAX_FRAC
    for i, ln in headings:
        if i > limit:
            break
        if _looks_ref_entry(ln["text"]):        # 참고문헌 항목은 섹션이 아니다
            continue
        core = re.sub(r'^\d+[\s.|)]*', '', ln["text"]).strip().lower().rstrip(":")
        if core.startswith(("introduction", "background")) or re.match(r'^1[\s.|)]\s', ln["text"]):
            return base + i
    for k, (i, ln) in enumerate(headings):   # 'abstract' 다음 헤딩
        if i > limit:
            break
        if re.sub(r'^\d+[\s.|)]*', '', ln["text"]).strip().lower().rstrip(":") == "abstract":
            return base + (headings[k + 1][0] if k + 1 < len(headings) else i)
    if base:
        return base
    cands = []
    for i, ln in enumerate(rest):
        if LETTER_OPEN_RE.match(ln["text"].strip()):
            cands.append(i)
            break
    prose = _first_prose_index(rest, body)
    if prose:
        cands.append(prose)
    if headings and headings[0][0] <= len(rest) * 0.25:
        cands.append(headings[0][0])
    return base + (min(cands) if cands else 0)


def _reconstruct(lines: list[dict], body: float, title: str = "") -> list[Section]:
    """헤딩·문단 재구성. front matter 제거, body 시작 이후만."""
    lines = lines[_body_start(lines, body, title):]

    body_text: list[Section] = []
    cur = Section(path=["Body"], section_type="other")
    pcount = [0]
    para: list[dict] = []
    prev = None

    def flush():
        if not para:
            return
        text = _join_lines(para)
        cites = _dedup([c for ln in para for c in ln["cites"]])
        if len(text) >= 40:
            pcount[0] += 1
            cur.paragraphs.append(Paragraph(id=f"p{pcount[0]}", text=text,
                                            cited_refs=[], cited_keys=cites))
        para.clear()

    for ln in lines:
        if _is_heading(ln, body):
            flush()
            if cur.paragraphs:
                body_text.append(cur)
            # 논문 제목 파라미터(title)를 덮어쓰지 않는다 — 지금은 _body_start 가
            # 먼저 끝나 결과가 같지만, 순서를 바꾸는 순간 조용히 깨지는 자리다.
            head = clean_heading(ln["text"])    # 자간 아티팩트 복원(textfix)
            cur = Section(path=[head], section_type=classify_section(
                re.sub(r'^\d+[\s.|)]*', '', head)))
            prev = None
            continue
        if _is_frontmatter(ln["text"]):
            flush(); prev = None
            continue
        if prev and _para_break(prev, ln, body):
            flush()
        para.append(ln)
        prev = ln

    flush()
    if cur.paragraphs:
        body_text.append(cur)
    return body_text


# ── 본문 회수(안전망) ────────────────────────────────────────────────
_WORD_RE = re.compile(r"[a-z0-9]+")
SHINGLE_N = 6


def document_prose(doc: dict | Document) -> str:
    """정본(dict 또는 Document)의 본문 산문을 한 덩어리 문자열로."""
    if isinstance(doc, Document):
        return " ".join(p.text for s in doc.body_text for p in s.paragraphs)
    return " ".join(p.get("text", "") for s in (doc.get("body_text") or [])
                    for p in (s.get("paragraphs") or []))


def _shingles(text: str, n: int = SHINGLE_N) -> set[str]:
    w = _WORD_RE.findall((text or "").lower())
    if len(w) < n:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def _coverage(cand: str, ref: set[str]) -> float:
    """후보 문단의 n-gram 중 정본에 이미 있는 비율(0~1)."""
    sh = _shingles(cand)
    if not sh:
        return 1.0
    return sum(1 for s in sh if s in ref) / len(sh)


# missing_prose 전용 최종 관문. 이 함수의 결과는 상위가 정본에 **넣는** 데 쓰므로
# 본문 경로보다 한 겹 더 엄격하게 막는다(모듈 원칙: 오염이 누락보다 나쁘다).
# 콜론 없는 게재이력 줄('Received July 4, 2013, Revised …')은 FRONT_RE 가 콜론을
# 요구해 비껴간다. 날짜 꼴까지 요구하므로 본문 문장에 걸릴 여지가 없다.
_PUBHIST_RE = re.compile(
    r'^(received|revised|accepted|submitted)\s+'
    r'(?:\w+\s+\d{1,2},?\s*\d{4}|\d{1,2}\s+\w+\s+\d{4})', re.I)


def _injectable(text: str) -> bool:
    """이 구간을 정본에 넣어도 되는가(참고문헌 항목·게재이력·front matter 배제)."""
    t = text.strip()
    if _PUBHIST_RE.match(t) or _is_frontmatter(t):
        return False
    if REF_ITEM_RE.match(t) and _looks_ref_entry(t):     # '1. Bae JM, … et al.'
        return False
    return True


def missing_prose(pdf_path: str | Path, canonical_text: str, *,
                  title: str = "", min_words: int = 25,
                  cover_cutoff: float = 0.5) -> list[dict]:
    """PDF 본문 산문 중 **정본에 없는** 구간을 돌려준다(상위가 보완에 쓴다).

    제외 규칙(오염 차단 — 각 규칙은 이 모듈 안에서 같은 코드로 본문에도 적용된다)
      · 머리말/꼬리말·페이지번호(위치 + 반복 증거)
      · front matter(저자·소속·교신·펀딩·판권·키워드)
      · 참고문헌 구역 전체
      · 그림/표 캡션과 표 본문 영역
      · 회전(세로) 조판 텍스트
      · 산문이 아닌 조각: min_words 미만이거나 문장부호로 끝나지 않는 토막
    남은 문단 중 정본과의 n-gram 겹침이 cover_cutoff 미만인 것만 낸다.
    title 을 주면 별쇄본에 섞인 다른 논문을 제목 기준으로 잘라낸다(권장).

    반환: [{"text", "words", "coverage", "section"} …] (읽기순서)
      · words   : 단어 수
      · coverage: 정본과의 n-gram 겹침 비율(0=정본에 전혀 없음)
      · section : 이 구간이 속한 섹션 제목(폴백이 인식한 것)
    """
    with fitz.open(str(pdf_path)) as doc:
        body, lines, flow = _prepare(doc)
        _figures_tables(doc, lines, body)      # 캡션·표 본문 구간도 skip 표시
        body_text = _reconstruct([l for l in flow if not l["skip"]], body, title)

    ref = _shingles(canonical_text)
    out: list[dict] = []
    for sec in body_text:
        for p in sec.paragraphs:
            t = p.text.strip()
            nw = len(t.split())
            if nw < min_words or not _PROSE_TAIL_RE.search(t):
                continue
            if parse_caption(t, min_desc=1) or not _injectable(t):
                continue
            cov = _coverage(t, ref)
            if cov < cover_cutoff:
                out.append({"text": t, "words": nw, "coverage": round(cov, 3),
                            "section": sec.path[0] if sec.path else ""})
    return out


# ── 진입점 ───────────────────────────────────────────────────────────
def parse_pdf(path: Path, meta: dict) -> Document:
    doc = fitz.open(path)
    try:
        return _parse_open_doc(doc, path, meta)
    finally:
        doc.close()   # 예외 발생 시에도 핸들 누수 방지


def _parse_open_doc(doc, path: Path, meta: dict) -> Document:
    body, lines, flow = _prepare(doc)
    figures, tables = _figures_tables(doc, lines, body)   # 캡션·표 본문 skip 표시
    body_text = _reconstruct([l for l in flow if not l["skip"]], body,
                            meta.get("title", ""))

    m = Meta(
        doi=meta.get("doi"), pmid=meta.get("pmid"), pmcid=meta.get("pmcid"),
        title=meta.get("title", ""), authors=meta.get("authors", []),
        journal=meta.get("journal", ""), year=meta.get("year"),
        mesh=meta.get("mesh", []), keywords=meta.get("keywords", []),
        pub_types=meta.get("pub_types", []),
        rcr=meta.get("rcr"), citation_count=meta.get("citation_count"),
        is_open_access=bool(meta.get("is_open_access")),
    )
    api_abstract = meta.get("abstract_pubmed") or meta.get("abstract") or ""
    if api_abstract:                 # 초록이 본문으로 한 번 더 들어오는 중복 제거
        body_text = _drop_abstract_echo(body_text, api_abstract)
    out = Document(
        paper_id=meta.get("doi") or meta.get("pmid") or "unknown",
        source="pdf_fallback", source_file=str(path), meta=m,
        abstract=api_abstract, abstract_source="api" if api_abstract else "none",
        body_text=body_text, figures=figures, tables=tables, references=[],
    )
    # 이웃 논문 혼입 제거 — 한 PDF 지면에 여러 편이 실린 경우(research letter).
    # 경계를 확신할 수 없으면 boundary 가 아무것도 건드리지 않는다.
    try:
        from . import boundary
        rep = boundary.apply_to_parsed(out, path, meta)
        if rep.get("confident") or rep.get("identity_conflict"):
            out.qc = dict(out.qc or {})
            out.qc["boundary"] = rep
    except Exception as e:                       # 안전망은 절대 파이프라인을 죽이지 않는다
        utils.log(f"  경계 판정 생략({path.name}): {type(e).__name__}: {e}")
    return out


def _drop_abstract_echo(body_text: list[Section], abstract: str) -> list[Section]:
    """API 초록과 사실상 같은 본문 문단을 뺀다(초록은 abstract 필드에 이미 있다)."""
    ref = _shingles(abstract)
    if not ref:
        return body_text
    out = []
    for sec in body_text:
        keep = [p for p in sec.paragraphs if _coverage(p.text, ref) < 0.8]
        if keep:
            sec.paragraphs = keep
            out.append(sec)
    return out


def run(config: dict | None = None) -> list[Document]:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)

    manifest = utils.read_jsonl(work / "manifest.jsonl")
    metas = {m["doi"]: m for m in
             (utils.read_json(p) for p in (work / "meta").glob("*.json"))}
    targets = []
    for r in manifest:
        if not (r.get("is_primary") and r.get("doi")):
            continue
        if metas.get(r["doi"], {}).get("in_epmc"):
            continue
        dest = norm_dir / f"{utils.slug(r['doi'])}.json"
        if dest.exists() and utils.read_json(dest).get("source") == "grobid":
            continue
        targets.append(r)

    log(f"[2단계b-fallback] PyMuPDF 구조추출: {len(targets)}편 (GROBID 미가동 대체)")
    docs, failed = [], 0
    for i, r in enumerate(targets, 1):
        try:
            doc = parse_pdf(Path(r["file"]), metas.get(r["doi"], {"doi": r["doi"]}))
            dest = norm_dir / f"{utils.slug(doc.paper_id)}.json"
            utils.write_json(dest, doc.to_dict())
            npar = sum(len(s.paragraphs) for s in doc.body_text)
            ncite = sum(len(p.cited_keys) for s in doc.body_text for p in s.paragraphs)
            log(f"  [{i}/{len(targets)}] {r['doi']}: 섹션 {len(doc.body_text)} · "
                f"문단 {npar} · 그림 {len(doc.figures)} · 표 {len(doc.tables)} · "
                f"인용마커 {ncite} (참조링크 없음)")
            docs.append(doc)
        except Exception as e:  # noqa: BLE001 — 파일 단위 격리
            failed += 1
            log(f"  [{i}/{len(targets)}] 폴백 실패({r['doi']}): {type(e).__name__}: {e}")
    log(f"[2단계b-fallback] 완료 → {norm_dir} (성공 {len(docs)}, 실패 {failed})")
    return docs


if __name__ == "__main__":
    run()
