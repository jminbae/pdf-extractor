"""정본 JSON → Markdown 뷰(설계서 5단계).

JSON이 정본, Markdown은 여기서 렌더링해 '보기용'으로만 쓴다.

참고문헌은 맨 아래 `## References` 절에 **지면 번호 순서로** 싣는다.
본문의 인용 마커 `[15]` 는 `[[15]](#ref-15)` 링크로 바꿔 내보낸다.
번호를 확정하지 못한 항목에는 링크를 걸지 않는다 — 틀린 링크는 없는 링크보다
해롭다는 것이 이 프로젝트의 원칙이다.

**화면(HTML) 쪽과의 계약**
  · 본문 링크의 목적지는 `#ref-<지면번호>` 다(REF_ANCHOR_FMT).
  · 목록 항목은 `## References` 아래에서 `15. …` 처럼 **번호로 시작하는 줄**로
    나온다. 여기에 `id="ref-15"` 를 붙이는 것은 **보여주는 쪽의 몫**이다 —
    마크다운에 원시 HTML 을 섞으면 뷰어가 그것을 글자로 찍어버리기 때문이다.
    번호 → 앵커 id 는 ref_anchor_id() 로 얻는다(양쪽이 같은 규칙을 쓰도록).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import utils

# 본문에 박힌 인용 마커. grobid_client._cite_marker 가 [15] 형태로 남긴다.
_CITE_MARK_RE = re.compile(r"\[(\d{1,3})\]")

# 본문 링크와 목록 앵커가 만나는 지점. 화면(HTML) 쪽도 이 규칙을 써야 한다.
REF_ANCHOR_FMT = "ref-{}"

# `## References` 아래에서 한 항목이 시작하는 줄 — 화면 쪽이 앵커를 붙일 자리.
REF_LINE_RE = re.compile(r"^(\d{1,3})\.\s")


def ref_anchor_id(number: int | str) -> str:
    """지면 번호 → 앵커 id. 본문 링크(#ref-15)와 목록 항목이 같은 값을 쓴다."""
    return REF_ANCHOR_FMT.format(number)

# 본문 첫머리에 남은 머리말 조각: 'DOI: 10.1111/bjd.15779 DEAR EDITOR, ...'
_LEAD_DOI_RE = re.compile(
    r"^\s*(?:DOI:?\s*)?(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/[-._;()/:\w]+\s*", re.I)

# letter 의 실질적 시작 표지 — 'Body' 라는 가짜 제목보다 이게 진짜 제목이다
_OPENER_RE = re.compile(r"^\s*(DEAR\s+EDITOR|TO\s+THE\s+EDITOR|Dear\s+Editor|"
                        r"To\s+the\s+Editor)\s*[,:—-]?\s*", re.I)

# GROBID 가 제목 없는 덩어리에 붙이는 자리표시자
_PLACEHOLDER_TITLES = {"body", "text", "unknown", "untitled", ""}


def _authors_line(authors: list[str], limit: int = 6) -> str:
    """'Kim, Hyunjin' 표기를 'Kim H' 로 줄여 학술지 관례대로 잇는다."""
    out = []
    for a in authors[:limit]:
        a = (a or "").strip()
        if "," in a:
            fam, giv = a.split(",", 1)
            ini = "".join(w[0] for w in giv.split() if w[:1].isalpha())
            out.append(f"{fam.strip()} {ini}".strip())
        else:
            out.append(a)
    s = ", ".join(x for x in out if x)
    return s + (", et al" if len(authors) > limit else "")


def format_reference(ref: dict) -> str:
    """참고문헌 한 건을 사람이 읽는 한 줄로.

    지면 원문(raw)이 있으면 그것을 쓴다 — 권·쪽까지 인쇄돼 있어 가장 완전하다.
    원문이 없는 항목(지면에서 못 찾고 iCite 에만 있는 것)만 서지 필드로 조립한다.
    확인된 식별자(doi·PMID)는 원문에 없을 때만 뒤에 덧붙인다.
    """
    body = (ref.get("raw") or "").strip()
    if not body:
        bits = []
        if ref.get("authors"):
            bits.append(_authors_line(ref["authors"]) + ".")
        if ref.get("title"):
            bits.append(ref["title"].rstrip(". ") + ".")
        tail = " ".join(x for x in (ref.get("journal") or "",
                                    str(ref.get("year") or "")) if x)
        if tail:
            bits.append(tail + ".")
        body = " ".join(bits).strip()
    if not body:
        body = ref.get("doi") or ref.get("key") or ""

    extra = []
    doi = (ref.get("doi") or "").strip()
    if doi and doi.lower() not in body.lower():
        extra.append(f"doi:{doi}")
    pmid = str(ref.get("pmid") or "").strip()
    if pmid and f"pmid {pmid}".lower() not in body.lower() and pmid not in body:
        extra.append(f"PMID {pmid}")
    return (body + (" " + " · ".join(extra) if extra else "")).strip()


def _link_citations(text: str, known: set[int]) -> str:
    """본문의 [15] 를 [[15]](#ref-15) 로 바꾼다. 목록에 없는 번호는 그대로 둔다."""
    if not known:
        return text

    def repl(m):
        n = int(m.group(1))
        return f"[[{n}]](#{ref_anchor_id(n)})" if n in known else m.group(0)

    return _CITE_MARK_RE.sub(repl, text or "")


def _references_block(doc: dict) -> list[str]:
    """맨 아래 참고문헌 절. **번호로 시작하는 평범한 줄**로 낸다.

    앵커(`<a id="ref-15">`)를 여기서 직접 쓰지 않는다. 마크다운을 보여주는 쪽이
    원시 HTML 을 글자로 취급하면 화면에 `<a id="ref-2"></a>2. Oliver ID…` 가
    그대로 찍힌다(실측). 앵커는 **보여주는 쪽**이 `15.` 로 시작하는 항목에
    붙이는 것이 맞다 — 마크다운은 마크다운으로만 두고, HTML 은 HTML 을 아는
    곳에서 만든다.
    """
    refs = doc.get("references") or []
    if not refs:
        return []
    numbered = [r for r in refs if r.get("number")]
    rest = [r for r in refs if not r.get("number")]
    out = ["", "## References"]
    for r in sorted(numbered, key=lambda x: x["number"]):
        out += ["", f'{r["number"]}. {format_reference(r)}']
    if rest:
        # 번호를 확정하지 못한 항목 — 버리지 않고 보여주되 링크는 걸지 않는다.
        #   parsed  : 지면에 있으나 몇 번인지 셀 수 없었던 항목
        #   icite   : API 에는 있는데 지면 목록에서 못 찾은 항목
        #   foreign : 같은 지면에 실린 **옆 논문**의 참고문헌
        out += ["", "### 번호 미확정 (본문 링크 없음)"]
        if doc.get("references_warning") == "icite_no_overlap":
            out += ["", "*API 목록이 지면 목록과 한 건도 겹치지 않는다 — "
                        "이 논문의 참고문헌이 아닐 수 있다(PMID 오배정 의심).*"]
        for r in rest:
            tag = {"icite": "API에만 있음", "foreign": "옆 논문 목록",
                   }.get(r.get("source"), "지면 번호 미확정")
            out += ["", f"- *({tag})* {format_reference(r)}"]
    return out


def _clean_lead(text: str) -> tuple[str, str | None]:
    """문단 첫머리의 머리말 DOI 를 떼고, letter 시작 표지를 찾아 돌려준다."""
    t = _LEAD_DOI_RE.sub("", text or "", count=1)
    opener = None
    m = _OPENER_RE.match(t)
    if m:
        opener = re.sub(r"\s+", " ", m.group(1)).upper()
        t = t[m.end():]
    return t.lstrip(), opener


def to_markdown(doc: dict, show_citations: bool = True) -> str:
    m = doc.get("meta", {})
    out: list[str] = []
    out.append(f"# {m.get('title') or doc.get('paper_id')}")
    out.append("")
    # 메타 라인
    bits = []
    if m.get("journal"): bits.append(m["journal"])
    if m.get("year"): bits.append(str(m["year"]))
    if m.get("pmid"): bits.append(f"PMID {m['pmid']}")
    if m.get("doi"): bits.append(f"doi:{m['doi']}")
    out.append("*" + " · ".join(bits) + "*" if bits else "")
    if m.get("authors"):
        out.append("")
        out.append(", ".join(m["authors"][:12]) + (" et al." if len(m["authors"]) > 12 else ""))
    if m.get("pub_types"):
        out.append("")
        out.append("`" + "` `".join(m["pub_types"]) + "`")
    # QC 점수·source 는 우리 내부 지표지 논문 내용이 아니다 → 본문 뷰에 넣지 않는다.
    # (앱은 정보줄에 따로 표시한다.)

    if doc.get("abstract"):
        out += ["", "## Abstract", "", doc["abstract"]]

    # 표/그림 id → 캡션 조회용
    tab_by_id = {t["id"]: t for t in doc.get("tables", [])}
    fig_by_id = {f["id"]: f for f in doc.get("figures", [])}

    # 목록에 실제로 있는 지면 번호만 링크 대상으로 삼는다.
    known_nums = {int(r["number"]) for r in (doc.get("references") or [])
                  if r.get("number")}

    last_path = []
    first_para = True
    for sec in doc.get("body_text", []):
        path = list(sec.get("path", []))
        paras = sec.get("paragraphs", [])

        # 첫 문단 머리말 정리 + letter 시작 표지 확보
        opener = None
        if paras:
            cleaned, opener = _clean_lead(paras[0].get("text", "")) if first_para else \
                (paras[0].get("text", ""), None)
            if first_para:
                paras = [dict(paras[0], text=cleaned)] + list(paras[1:])
                first_para = False

        # 'Body' 같은 자리표시자 제목은 헤딩으로 쓰지 않는다.
        # letter 면 실제 시작 표지("DEAR EDITOR")를 제목으로 올린다.
        leaf = (path[-1] if path else "").strip()
        if leaf.lower() in _PLACEHOLDER_TITLES:
            path = path[:-1] + ([opener] if opener else [])
        elif opener:
            path = [opener] + path

        # 실제 논문처럼 **상위 절 제목을 한 번만** 쓰고 그 아래에 소제목을 둔다.
        #   JSON 은 절마다 전체 경로(['RESULTS','Subgroup analyses'])를 들고 있지만,
        #   화면에 'RESULTS › 소제목' 을 소절마다 반복하면 읽기 불편하다.
        #   직전 경로와 겹치는 앞부분은 건너뛰고 **새로 시작하는 단계만** 제목으로 낸다.
        # 제목이 아닌 것(인용번호 조각·표 본문·초록 라벨)은 제목으로 내지 않는다.
        # 정본 JSON 에는 남겨 두고 **화면에서만** 뺀다 — 구조 판단은 뒤에 고칠 수 있고,
        # 그 사이에 원장이 보는 화면이 파편 목록처럼 보이면 안 된다.
        from .schema import is_not_a_heading
        path = [h for h in path if not is_not_a_heading(h)]

        if path and path != last_path:
            same = 0
            while (same < len(path) and same < len(last_path)
                   and path[same] == last_path[same]):
                same += 1
            for lvl in range(same, len(path)):
                out += ["", "#" * min(lvl + 2, 5) + " " + path[lvl]]
            last_path = path

        for p in paras:
            text = p.get("text", "")
            if not text.strip():
                continue
            # 인용은 본문 안에 [15] 로 이미 박혀 있다 → 문단 아래에 따로 붙이지
            # 않고, 그 자리에서 눌러 목록으로 뛸 수 있게 링크로 바꾼다.
            out += ["", _link_citations(text, known_nums)]

    # 표
    if doc.get("tables"):
        out += ["", "## Tables"]
        for t in doc["tables"]:
            out += ["", f"**{t.get('caption') or t['id']}**", ""]
            out.append(t.get("markdown") or "*(표 본문 추출 실패 — MinerU 보강 대상)*")

    # 그림 (캡션만). 내부 id(fig_1)는 화면에 내보내지 않는다 — 캡션에 이미
    # 'Fig 1.' 이 인쇄돼 있어 'fig_1: Fig 1.' 처럼 두 번 나온다.
    if doc.get("figures"):
        caps = [(f.get("caption") or "").strip() for f in doc["figures"]]
        caps = [c for c in caps if c]
        if caps:
            out += ["", "## Figures"]
            out += [f"- {c}" for c in caps]

    # 참고문헌 — 지면 번호 순서로 맨 아래. 본문 [15] 가 여기 15번으로 뛴다.
    out += _references_block(doc)

    return "\n".join(out).strip() + "\n"


# ── 사람이 읽는 파일명 ────────────────────────────────────────────────
_BAD_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_STOP = {"of", "the", "and", "for", "in", "on", "a", "an", "de", "et", "la"}


def journal_abbrev(journal: str) -> str:
    """저널 약어. PubMed 표기 'Full name : ABBREV' 면 뒤쪽을, 이미 짧으면 그대로,
    그것도 아니면 주요 단어 첫 글자를 모아 만든다(예: JEADV)."""
    j = (journal or "").strip()
    if not j:
        return ""
    if " : " in j:
        return j.split(" : ")[-1].strip()
    if len(j) <= 24:
        return j
    words = [w for w in re.split(r"[\s\-]+", j) if w.lower() not in _STOP and w[:1].isalpha()]
    return "".join(w[0].upper() for w in words)[:8] or j[:24]


def human_name(doc: dict, max_len: int = 110) -> str:
    """'2023 JEADV Pigmented contact dermatitis and hair dyes' 형태.

    윈도우 경로 260자 제한 때문에 제목을 자른다. 같은 이름이 겹칠 수 있으므로
    호출부(export)가 DOI 끝자리를 덧붙여 유일성을 보장한다.
    """
    m = doc.get("meta", {})
    year = str(m.get("year") or "")
    ab = journal_abbrev(m.get("journal") or "")
    title = re.sub(r"\s+", " ", (m.get("title") or "").strip())
    name = " ".join(x for x in (year, ab, title) if x) or str(doc.get("paper_id") or "unknown")
    name = _BAD_FS.sub("", name).strip(" .")
    return name[:max_len].rstrip(" .,-")


def export(config: dict | None = None, out_dir: str | Path | None = None,
           human: bool = True) -> int:
    """정본 JSON → Markdown 파일로 **내보낸다**(요청할 때만).

    Markdown 은 정본이 아니라 뷰라서 평소에는 저장하지 않는다. 저장해 두면
    JSON 을 수리했을 때 조용히 낡아 두 번째(틀린) 정본이 되기 때문이다.
    외부 뷰어로 훑어보고 싶을 때만 이 함수로 만들어 쓴다.
    """
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    dest = Path(out_dir) if out_dir else (work / "export_md")
    dest.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    n = 0
    for p in sorted((work / "normalized").glob("*.json")):
        doc = utils.read_json(p)
        stem = human_name(doc) if human else p.stem
        if stem.lower() in used:                       # 동명이인 방지: DOI 끝자리 덧붙임
            tail = str(doc.get("paper_id") or "").rsplit("/", 1)[-1][-12:]
            stem = f"{stem} ({utils.slug(tail)})"
        used.add(stem.lower())
        (dest / f"{stem}.md").write_text(to_markdown(doc), encoding="utf-8")
        n += 1
    utils.log(f"[내보내기] {n}편 → {dest}")
    return n


def run(config: dict | None = None) -> None:
    """옛 호출부 호환용 — data/markdown 에 DOI 이름으로 내보낸다.

    ※ 기본 파이프라인에서는 더 이상 호출하지 않는다(JSON 하나만 정본으로 둔다).
    """
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    export(cfg, out_dir=work / "markdown", human=False)


if __name__ == "__main__":
    export()
