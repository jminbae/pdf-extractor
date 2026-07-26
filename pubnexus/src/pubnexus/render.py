"""정본 JSON → Markdown 뷰(설계서 5단계).

JSON이 정본, Markdown은 여기서 렌더링해 '보기용'으로만 쓴다.
참고문헌 목록은 본문 뷰에서 제외(API 메타로 보유), 인용은 각주 링크로 표기 가능.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import utils

# GROBID 내부 참조키(b0, b1 …). 사람이 볼 화면에 그대로 나오면 안 된다.
_LOCAL_KEY_RE = re.compile(r"^b\d+$", re.I)

# 본문 첫머리에 남은 머리말 조각: 'DOI: 10.1111/bjd.15779 DEAR EDITOR, ...'
_LEAD_DOI_RE = re.compile(
    r"^\s*(?:DOI:?\s*)?(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/[-._;()/:\w]+\s*", re.I)

# letter 의 실질적 시작 표지 — 'Body' 라는 가짜 제목보다 이게 진짜 제목이다
_OPENER_RE = re.compile(r"^\s*(DEAR\s+EDITOR|TO\s+THE\s+EDITOR|Dear\s+Editor|"
                        r"To\s+the\s+Editor)\s*[,:—-]?\s*", re.I)

# GROBID 가 제목 없는 덩어리에 붙이는 자리표시자
_PLACEHOLDER_TITLES = {"body", "text", "unknown", "untitled", ""}


def _ref_label(ref: dict, num: int) -> str:
    """참고문헌 한 건을 사람이 읽는 짧은 표기로. '3. Kim (2019)' 형태."""
    who = ""
    raw = (ref.get("raw") or "").strip()
    if ref.get("title"):
        who = ref["title"][:60]
    elif raw:
        who = raw[:60]
    year = ref.get("year")
    tail = f" ({year})" if year else ""
    return f"{num}. {who}{tail}" if who else f"{num}. {ref.get('doi') or ref.get('key')}"


def _citation_index(doc: dict) -> tuple[dict, list[dict]]:
    """인용값(DOI 또는 내부키) → 번호. 본문에는 번호만 쓰고 뒤에 목록을 붙인다."""
    refs = doc.get("references") or []
    idx: dict[str, int] = {}
    for i, r in enumerate(refs, 1):
        if r.get("key"):
            idx[str(r["key"])] = i
        if r.get("doi"):
            idx[str(r["doi"]).lower()] = i
    return idx, refs


def _cite_marks(cited: list[str], idx: dict) -> list[str]:
    """인용값들을 번호로 바꾼다. 번호를 못 찾으면 **버린다**(내부키 노출 금지)."""
    nums = []
    for c in cited or []:
        n = idx.get(str(c).lower()) or idx.get(str(c))
        if n and n not in nums:
            nums.append(n)
        elif not n and not _LOCAL_KEY_RE.match(str(c)):
            # 목록에 없는 DOI 는 그대로 보여준다(추적 가치가 있다)
            nums.append(str(c))
    return [str(n) for n in nums]


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

    cite_idx, refs = _citation_index(doc)
    used_refs: set[int] = set()

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

        if path and path != last_path:
            depth = min(len(path), 4)
            out += ["", "#" * (depth + 1) + " " + " › ".join(path)]
            last_path = path

        for p in paras:
            text = p.get("text", "")
            if not text.strip():
                continue
            out += ["", text]
            # 인용은 본문 안에 [15] 로 이미 박혀 있다 → 문단 아래에 따로 붙이지 않는다.
            # (텍스트만 가져가도 인용 위치가 정확히 남는 것이 목적)

    # 표
    if doc.get("tables"):
        out += ["", "## Tables"]
        for t in doc["tables"]:
            out += ["", f"**{t.get('caption') or t['id']}**", ""]
            out.append(t.get("markdown") or "*(표 본문 추출 실패 — MinerU 보강 대상)*")

    # 그림 (캡션만)
    if doc.get("figures"):
        out += ["", "## Figures"]
        for f in doc["figures"]:
            out.append(f"- **{f['id']}**: {f.get('caption') or '(캡션 없음)'}")

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
