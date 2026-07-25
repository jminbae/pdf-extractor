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

from .utils import norm_text


def _local(tag) -> str:
    """네임스페이스 제거한 로컬 태그명."""
    if not isinstance(tag, str):
        return ""
    return tag.split("}")[-1]


def paragraph_text(p_elem) -> dict:
    """<p> 요소 → {text, cited_keys, fig_ids, table_ids}.

    본문에서 bibr 인용 숫자는 지우고(깨끗한 임베딩용), 링크만 메타로 남긴다.
    figure/table 참조는 표현 텍스트를 유지한다("Figure 2" 등은 의미 있는 문맥).
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
                if rtype == "bibr":
                    for k in rid.split():
                        cited.append(k)
                    # 인용 숫자 텍스트는 본문에서 제거 (child.tail 만 이어붙임)
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


def caption_text(elem) -> str:
    """<label> + <caption> 을 하나의 캡션 문자열로."""
    label = elem.find("{*}label")
    cap = elem.find("{*}caption")
    bits = []
    if label is not None:
        bits.append(norm_text("".join(label.itertext())))
    if cap is not None:
        bits.append(norm_text("".join(cap.itertext())))
    return " ".join(b for b in bits if b).strip()


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


def _tidy_punct(s: str) -> str:
    """인용 마커 제거 후 남는 구분자 잔재(",," ".,,," "( , )" 등) 정리."""
    import re
    s = _strip_residual_citations(s)              # 파서가 놓친 잔여 인용 제거
    s = re.sub(r'\s+([,.;:])', r'\1', s)          # " ," → ","
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
