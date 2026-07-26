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

# 본문 글자에서 그림·표를 부르는 말. refs_figure 가 비어 있는 문서(전체의 40%)를
# 위해 글자로도 찾는다. 'Supplementary Figure 1' 은 본문 그림이 아니다.
_FIG_MENTION_RE = re.compile(
    r"(?<!supp)(?<!supplementary\s)\bfigs?\.?\s*(\d{1,2})\b|\bfigures?\s*(\d{1,2})\b",
    re.I)
_TAB_MENTION_RE = re.compile(r"\btables?\.?\s*(\d{1,2})\b", re.I)


def _float_number(item: dict, kind: str) -> int | None:
    """그림·표의 지면 번호. 캡션에서 읽고, 없으면 id 꼬리 숫자로."""
    cap = (item.get("caption") or "").strip()
    if cap:
        try:
            from .captions import parse_caption
            got = parse_caption(cap, min_desc=0)
        except Exception:                       # noqa: BLE001
            got = None
        if got and not got["supp"] and got["num"] is not None:
            want = "fig" if kind == "fig" else "tab"
            if got["kind"] == want:
                return int(got["num"])
    m = re.search(r"(\d{1,3})\s*$", str(item.get("id") or ""))
    return int(m.group(1)) if m else None


def _table_block(t: dict) -> list[str]:
    return ["", f"**{t.get('caption') or t['id']}**", "",
            t.get("markdown") or "*(표 본문을 뽑지 못했습니다)*"]


def _figure_block(f: dict) -> list[str]:
    """그림 한 장. 이미지가 있으면 **마크다운 그림**으로 낸다.

    `![캡션](fig1.png)` — 경로는 `figures[].image` 그대로(상대경로)다. 화면 쪽은
    이것을 받아 자기 주소로 바꿔 진짜 이미지를 건다. 마크다운 파일로 그냥 열어도
    그림 폴더 옆에서는 보인다. 이미지가 없으면 캡션만 남긴다.
    """
    cap = (f.get("caption") or "").strip()
    img = (f.get("image") or "").strip()
    if img:
        return ["", f"![{cap}]({img})"]
    return ["", f"**{cap}**"] if cap else []


class _FloatPlacer:
    """그림·표를 **처음 인용된 문단 뒤**에 놓는다. 남은 것은 끝으로.

    두 가지 단서를 쓴다.
      · `paragraphs[].refs_figure`·`refs_table` — 파서가 붙여 준 것(60%·33%)
      · 문단 글자의 'Figure 2'·'Table 1' — 위가 비어 있는 문서를 위한 보완
    """

    def __init__(self, doc: dict) -> None:
        self._figs = list(doc.get("figures") or [])
        self._tabs = list(doc.get("tables") or [])
        self._left_f = {id(f): f for f in self._figs}
        self._left_t = {id(t): t for t in self._tabs}
        self._fig_by_id = {f.get("id"): f for f in self._figs if f.get("id")}
        self._tab_by_id = {t.get("id"): t for t in self._tabs if t.get("id")}
        self._fig_by_num: dict[int, dict] = {}
        self._tab_by_num: dict[int, dict] = {}
        for f in self._figs:
            k = _float_number(f, "fig")
            if k is not None:
                self._fig_by_num.setdefault(k, f)
        for t in self._tabs:
            k = _float_number(t, "tab")
            if k is not None:
                self._tab_by_num.setdefault(k, t)

    def after(self, para: dict, text: str) -> list[str]:
        """이 문단이 처음 부른 그림·표의 마크다운. 없으면 빈 목록."""
        out: list[str] = []
        for t in self._pick(para.get("refs_table"), text,
                            _TAB_MENTION_RE, self._tab_by_id,
                            self._tab_by_num, self._left_t, "tab"):
            out += _table_block(t)
        for f in self._pick(para.get("refs_figure"), text,
                            _FIG_MENTION_RE, self._fig_by_id,
                            self._fig_by_num, self._left_f, "fig"):
            out += _figure_block(f)
        return out

    @staticmethod
    def _pick(refs, text, mention_re, by_id, by_num, left, kind) -> list[dict]:
        found: list[dict] = []

        def take(item) -> None:
            # 캡션뿐이어도 본문 제자리에 놓는다 — 있다는 사실 자체가 정보다.
            if item is not None and id(item) in left:
                del left[id(item)]
                found.append(item)

        for rid in (refs or ()):
            take(by_id.get(rid))
        for m in mention_re.finditer(text or ""):
            num = next((g for g in m.groups() if g), None)
            if num:
                take(by_num.get(int(num)))
        return found

    def leftovers(self) -> tuple[list[dict], list[dict]]:
        """아직 놓지 못한 것들. 본문 순서를 지켜 돌려준다."""
        return ([t for t in self._tabs if id(t) in self._left_t],
                [f for f in self._figs if id(f) in self._left_f])

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


def _is_debris(ref: dict) -> bool:
    """번호 없는 항목 중 **잘린 파편**인가.

    지면 목록을 항목 단위로 끊다가 한 항목을 중간에서 자르면, 앞 조각은 번호를
    갖고 남고 뒷조각이 '번호 없는 새 항목'이 된다. 실측한 뒷조각들:

        'o C. Exp Appl Acarol 2013;60:117-126.'          ← 저자명 꼬리부터
        'JAMA Dermatol. 2017;153(7):666-674. doi:…'      ← 저널 정보만
        'Phototherapy for Vitiligo … jamadermatology.com' ← 러닝헤더(문헌도 아니다)

    뒷조각에 DOI 가 실려 있으면 iCite 가 짝을 찾아 그럴듯한 제목까지 붙여 주므로
    **화면에서는 진짜 문헌처럼 보인다.** 원장이 "이건 중복 아니냐"고 본 것이 이것이다.

    가르는 잣대는 **원문(raw)의 유무**다. 번호를 확정한 항목도 원문은 번호를 떼고
    저자명부터 시작한다(실측 4,681건). 그러니 원문이 있는데 번호가 없다는 것은
    '지면에서 항목 하나를 온전히 읽었는데 번호만 못 셌다'는 뜻이 되어야 하는데,
    실측 295건이 전부 중간부터 시작하는 조각이었다 — 그런 항목은 없다.
    원문이 없는 것(iCite 목록에만 있는 것)은 온전하므로 남긴다.
    """
    return bool((ref.get("raw") or "").strip())


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
    rest = [r for r in refs if not r.get("number") and not _is_debris(r)]
    out = ["", "## References"]
    for r in sorted(numbered, key=lambda x: x["number"]):
        out += ["", f'{r["number"]}. {format_reference(r)}']
    if rest:
        # 번호를 확정하지 못한 항목 — 버리지 않고 보여주되 링크는 걸지 않는다.
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

    # 그림·표를 **처음 인용된 문단 바로 뒤**에 놓는다(실제 논문 조판과 같다).
    # 맨 아래로 몰면 'Figure 2 에서 보듯' 을 읽을 때마다 끝으로 갔다 돌아와야 한다.
    placer = _FloatPlacer(doc)

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
            out += placer.after(p, text)        # 이 문단이 처음 부른 그림·표

    # 본문에서 한 번도 부르지 않은 것은 갈 자리가 없다 → 끝에 모은다.
    left_t, left_f = placer.leftovers()
    if left_t:
        out += ["", "## Tables"]
        for t in left_t:
            out += _table_block(t)
    if left_f:
        blocks = [b for f in left_f for b in _figure_block(f)]
        if blocks:
            out += ["", "## Figures"] + blocks

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
