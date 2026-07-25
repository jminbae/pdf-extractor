"""JATS(PMC) XML 파서 헬퍼 — 인라인 요소 처리 및 표 변환.

lxml 트리에서:
  - 문단 텍스트를 추출하되 인용 마커(<xref ref-type="bibr">)의 숫자는 본문에서 제거,
    대신 참조 로컬 키로 기록(설계서의 cited_refs 원리).
  - figure/table 상호참조는 본문 텍스트를 보존하되 링크로도 기록.
  - <table> 을 markdown 으로 변환.
"""
from __future__ import annotations

import re

from lxml import etree

from .textfix import clean_paragraph
from .utils import norm_text


def _local(tag) -> str:
    """네임스페이스 제거한 로컬 태그명."""
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1]


# xref 의 ref-type 중 '참고문헌 인용일 수 없는' 구조 참조.
# rid 가 ref-list 안의 id 로 해소되더라도 이 타입들은 인용으로 보지 않는다(오탐 차단).
_NON_CITE_REF_TYPES = frozenset({
    "fig", "table", "table-fn", "fn", "aff", "corresp", "author-notes",
    "supplementary-material", "sec", "app", "boxed-text", "list",
    "disp-formula", "chem", "kwd", "award", "contrib",
})


# <p> 안에 둥지를 튼 부유(float) 블록 — 본문 산문이 아니다.
#
# 일부 출판사 변환본(JAAD·Springer 계열)은 <fig>/<table-wrap> 을 <sec> 의 형제가
# 아니라 **<p> 내부**에 넣는다. 이때 문단을 그냥 재귀 순회하면 표 셀 전체와
# 그림 캡션이 산문 한가운데로 쏟아진다 — 실측: "…covariates are listed in Table
# I.Table IDemographic and health characteristics…PreweightingPostweighting…"
# 이 블록들은 _collect_floats() 가 이미 Figure/Table 로 따로 회수하므로
# 문단에서 빼도 잃는 것이 없고, 빼지 않으면 같은 내용이 두 번 실린다.
_FLOAT_BLOCKS = frozenset({
    "fig", "fig-group", "table-wrap", "table-wrap-group",
    "supplementary-material",      # "Supplementary file1 (DOCX 165 kb)" 첨부 목록
})


def _is_citation(rtype: str, rid: str, ref_ids: set[str] | None) -> bool:
    """이 xref 가 참고문헌 인용인가.

    표준은 ref-type="bibr" 이지만 일부 저널 변환본은 ref-type="ref" 로 내보내고,
    아예 ref-type 이 빠진 경우도 있다. 그래서 **rid 가 ref-list 안의 id 로
    해소되는지**로 판정 범위를 넓힌다. 다만 fig/table/aff 처럼 인용일 수 없는
    구조 참조는 rid 가 우연히 겹쳐도 인용으로 보지 않는다.
    """
    if rtype == "bibr":
        return True
    if not ref_ids or rtype in _NON_CITE_REF_TYPES:
        return False
    return any(k in ref_ids for k in rid.split())


def paragraph_text(p_elem, ref_ids: set[str] | None = None) -> dict:
    """<p> 요소 → {text, cited_keys, fig_ids, table_ids}.

    본문에서 bibr 인용 숫자는 지우고(깨끗한 임베딩용), 링크만 메타로 남긴다.
    figure/table 참조는 표현 텍스트를 유지한다("Figure 2" 등은 의미 있는 문맥).
    ref_ids 를 주면 ref-type 이 'bibr' 이 아닌 인용(ref 등)도 rid 로 판정한다.
    """
    cited: list[str] = []
    figs: list[str] = []
    tables: list[str] = []
    parts: list[str] = []

    def walk(node):
        # node 자체의 선행 텍스트
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = _local(child.tag)
            if tag == "xref":
                rtype = child.get("ref-type", "")
                rid = child.get("rid", "")
                if _is_citation(rtype, rid, ref_ids):
                    for k in rid.split():
                        cited.append(k)
                    # 인용 마커를 그 자리에 [15] 로 남긴다(grobid 경로와 동일 규칙).
                    #   위첨자로 뽑히면 본문 수치와 구분이 안 되므로 대괄호로 감싼다.
                    from .grobid_client import _cite_marker
                    parts.append(_cite_marker("".join(child.itertext())))
                elif rtype in ("fig",):
                    figs.extend(rid.split())
                    if child.text:
                        parts.append(child.text)
                elif rtype in ("table",):
                    tables.extend(rid.split())
                    if child.text:
                        parts.append(child.text)
                else:
                    if child.text:
                        parts.append(child.text)
            elif tag in ("sup", "sub", "italic", "bold", "sc", "underline",
                         "named-content", "styled-content"):
                walk(child)  # 인라인 서식은 텍스트만 이어받음
            elif tag in _FLOAT_BLOCKS:
                # 표·그림·첨부는 별도 객체로 이미 회수됨 — 본문에 흘리지 않는다.
                # (tail 은 아래 공통 처리로 살아남아 문장이 끊기지 않는다.)
                if tag in ("fig", "fig-group"):
                    figs.extend((child.get("id") or "").split())
                elif tag in ("table-wrap", "table-wrap-group"):
                    tables.extend((child.get("id") or "").split())
            else:
                # 수식·그래픽 등은 본문에서 제외하되 tail 은 살림
                if tag in ("inline-formula", "disp-formula", "graphic",
                           "inline-graphic", "ext-link"):
                    if tag == "ext-link" and child.text:
                        parts.append(child.text)
                else:
                    walk(child)
            # 공통: 자식 뒤의 tail 텍스트
            if child.tail:
                parts.append(child.tail)

    walk(p_elem)
    text = norm_text("".join(parts))
    # 인용 제거 후 남는 공백/문장부호 정리: " ," "( )" 등
    text = _tidy_punct(text)
    # 러닝헤더·자간 아티팩트 등 추출 결함 수리(textfix, 결함 발생 지점에서 차단)
    text = clean_paragraph(text)
    # 순서 보존 dedup
    return {
        "text": text,
        "cited_keys": _dedup(cited),
        "fig_ids": _dedup(figs),
        "table_ids": _dedup(tables),
    }


def table_to_markdown(table_wrap) -> str:
    """<table-wrap> 안의 <table>(XHTML형) → markdown 표.

    colspan/rowspan 을 그리드로 펼쳐 열 정렬을 보존한다(임상 표 정보밀도 유지).
    병합 셀 값은 덮이는 모든 칸에 반복 기입해 각 열이 라벨을 갖게 한다.
    """
    tbl = table_wrap.find(".//{*}table")
    if tbl is None:
        return ""

    # (text, colspan, rowspan) 원본 행 수집 — thead/tbody 구분 없이 순서대로
    raw_rows: list[list[tuple[str, int, int]]] = []
    for tr in tbl.iter("{*}tr"):
        cells = []
        for cell in tr:
            if _local(cell.tag) in ("td", "th"):
                text = norm_text("".join(cell.itertext()))
                cs = _int(cell.get("colspan"), 1)
                rs = _int(cell.get("rowspan"), 1)
                cells.append((text, cs, rs))
        if cells:
            raw_rows.append(cells)
    if not raw_rows:
        return ""

    # HTML 표 그리드 알고리즘: rowspan/colspan 을 좌표에 실제 배치
    grid: dict[tuple[int, int], str] = {}
    maxcol = 0
    for r, cells in enumerate(raw_rows):
        c = 0
        for text, cs, rs in cells:
            while (r, c) in grid:      # rowspan 으로 이미 점유된 칸 건너뜀
                c += 1
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = text
            c += cs
            maxcol = max(maxcol, c)

    nrows = max(k[0] for k in grid) + 1
    rows = [[grid.get((r, c), "") for c in range(maxcol)] for r in range(nrows)]

    header = rows[0]
    md = ["| " + " | ".join(header) + " |",
          "| " + " | ".join(["---"] * maxcol) + " |"]
    for r in rows[1:]:
        md.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
    return "\n".join(md)


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def graphic_href(elem) -> str:
    """<graphic>/<media> 의 이미지 파일명(xlink:href). 없으면 빈 문자열.

    표가 <table> 없이 이미지 한 장으로만 실린 변환본을 식별하는 데 쓴다.
    """
    for tag in ("{*}graphic", "{*}inline-graphic", "{*}media"):
        for g in elem.iter(tag):
            href = g.get(_XLINK_HREF) or g.get("href") or ""
            if href.strip():
                return href.strip()
    return ""


def abstract_text(ab_elem, ref_ids: set[str] | None = None) -> str:
    """<abstract> → 한 덩어리 텍스트.

    본문과 **같은 규칙**으로 처리한다 — 인용 xref 를 [15] 로 정규화하고
    구두점 잔재를 정리한다(구조화 초록의 <xref ref-type="bibr"> 가 그대로 남아
    '[5][6][7][8]' 같은 마커가 초록에 섞여 나오는 것을 막는다).
    구조화 초록의 소제목은 'Background: ...' 형태로 살린다 —
    PubMed 초록도 같은 형식이라 QC 초록대조가 정확해진다.
    """
    def walk(node, depth: int) -> str:
        title_el = node.find("{*}title")
        label = ""
        if title_el is not None and depth > 0:
            label = norm_text("".join(title_el.itertext())).strip(" :")
        bits: list[str] = []
        for child in node:
            tag = _local(child.tag)
            if tag == "title":
                continue                       # 소제목은 label 로 이미 처리
            if tag == "p":
                t = paragraph_text(child, ref_ids)["text"]
                if t:
                    bits.append(t)
            elif tag == "sec":
                t = walk(child, depth + 1)
                if t:
                    bits.append(t)
            else:
                # <list>·<disp-quote> 등 블록 안에 묻힌 <p> 도 빠짐없이 회수한다.
                nested = list(child.iter("{*}p"))
                if nested:
                    bits.extend(t for t in
                                (paragraph_text(x, ref_ids)["text"] for x in nested) if t)
                else:
                    t = clean_paragraph(_tidy_punct(norm_text("".join(child.itertext()))))
                    if t:
                        bits.append(t)
        body = " ".join(bits)
        if label and body:
            return f"{label}: {body}"
        return body

    text = norm_text(walk(ab_elem, 0))
    if not text:            # <p>/<sec> 밖에 텍스트가 있는 변칙 구조 — 통째로 회수
        text = clean_paragraph(_tidy_punct(norm_text("".join(ab_elem.itertext()))))
    return text


def caption_text(elem) -> str:
    """<label> + <caption> 을 하나의 캡션 문자열로."""
    label = elem.find("{*}label")
    cap = elem.find("{*}caption")
    bits = []
    if label is not None:
        bits.append(norm_text("".join(label.itertext())))
    if cap is not None:
        bits.append(norm_text("".join(cap.itertext())))
    return clean_paragraph(" ".join(b for b in bits if b))


# ── 헬퍼 ────────────────────────────────────────────────────────────
def _dedup(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# 파서가 놓친 잔여 인용마커: "worldwide.1,2" "steady.7-9" (마침표/괄호 + 다중숫자).
# 구분자(,-–) 필수 → "area1,703,487"(큰 숫자) 같은 것은 건드리지 않음.
_RESID_CITE = re.compile(r'([A-Za-z]{3,}[.)\]])\d{1,3}(?:[,\-–]\d{1,3})+(?=[\s,.;:)]|$)')


def _strip_residual_citations(s: str) -> str:
    return _RESID_CITE.sub(r'\1', s)


# 잇달아 붙은 인용 마커 사이의 구분자: "[1], [2], [3]" → "[1][2][3]".
# JATS 는 인용 하나하나를 <xref> 로 쪼개 사이 쉼표를 tail 로 흘리므로 그대로 두면
# TEI 경로('15,16' 한 마커 → [15][16])와 표기가 갈린다. 같은 코퍼스 안에서 인용
# 표기가 두 가지면 눈에 띄고 청킹·검색의 정규화도 어긋난다.
# 붙임표는 건드리지 않는다 — "[1]-[3]" 의 범위 의미를 지우면 안 되기 때문이다.
_MARKER_JOIN = re.compile(r"(?<=\d\])\s*[,;]\s*(?=\[\d)")


def _tidy_punct(s: str) -> str:
    """인용 마커 제거 후 남는 구분자 잔재(",," ".,,," "( , )" 등) 정리."""
    import re
    s = _strip_residual_citations(s)              # 파서가 놓친 잔여 인용 제거
    s = _MARKER_JOIN.sub("", s)                   # "[1], [2]" → "[1][2]"
    # " ," → ","  단, 통계 표기는 보존한다: "P = .001" 의 앞 공백,
    # "1 : 1.6" 의 비율 콜론을 지우면 수치의 의미가 바뀐다(textfix._tidy_spaces 와 동일 규칙).
    s = re.sub(r'\s+([,;])', r'\1', s)
    s = re.sub(r'\s+\.(?!\d)', '.', s)
    s = re.sub(r'(?<!\d)\s+:', ':', s)
    # 연속 인용을 지운 자리의 쉼표/세미콜론 런 축약
    s = re.sub(r'([.;:])[\s,;]*[,;][\s,;]*', r'\1 ', s)  # ".,,," → ". "
    s = re.sub(r',[\s,]*,', ",", s)                # ",," / ", ," → ","
    s = re.sub(r',\s*\.', ".", s)                  # ", ." → "."
    s = re.sub(r'\(\s*[,;]\s*', "(", s)            # "( ," → "("
    s = re.sub(r'[,;]\s*\)', ")", s)               # ", )" → ")"
    s = re.sub(r'\[\s*[-,–;\s]*\]', "", s)         # 빈 "[ ]" 제거
    s = re.sub(r'\(\s*[-,–;\s]*\)', "", s)         # 빈 "( )" 제거
    s = re.sub(r'\s+([,.;:)])', r'\1', s)          # 재차 공백+문장부호
    s = re.sub(r'\s{2,}', " ", s)
    return s.strip()
