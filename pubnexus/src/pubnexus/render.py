"""정본 JSON → Markdown 뷰(설계서 5단계).

JSON이 정본, Markdown은 여기서 렌더링해 '보기용'으로만 쓴다.
참고문헌 목록은 본문 뷰에서 제외(API 메타로 보유), 인용은 각주 링크로 표기 가능.
"""
from __future__ import annotations

from pathlib import Path

from . import utils


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
    q = doc.get("quality_score")
    if q is not None:
        out.append("")
        out.append(f"> QC 품질점수 **{q}** · source `{doc.get('source')}`")

    if doc.get("abstract"):
        out += ["", "## Abstract", "", doc["abstract"]]

    # 표/그림 id → 캡션 조회용
    tab_by_id = {t["id"]: t for t in doc.get("tables", [])}
    fig_by_id = {f["id"]: f for f in doc.get("figures", [])}

    last_path = []
    for sec in doc.get("sections", []):
        path = sec.get("path", [])
        # 섹션 헤딩(깊이만큼 #)
        depth = min(len(path), 4)
        if path != last_path:
            out += ["", "#" * (depth + 1) + " " + " › ".join(path)]
            last_path = path
        for p in sec.get("paragraphs", []):
            text = p["text"]
            out += ["", text]
            tags = []
            if show_citations and p.get("cited_refs"):
                tags.append(f"↳ 인용: {', '.join(p['cited_refs'][:8])}"
                            + (" …" if len(p["cited_refs"]) > 8 else ""))
            if p.get("refs_table"):
                tags.append("표: " + ", ".join(p["refs_table"]))
            if p.get("refs_figure"):
                tags.append("그림: " + ", ".join(p["refs_figure"]))
            if tags:
                out.append("<sub>" + " | ".join(tags) + "</sub>")

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


def run(config: dict | None = None) -> None:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"
    md_dir = work / "markdown"
    md_dir.mkdir(parents=True, exist_ok=True)
    docs = sorted(norm_dir.glob("*.json"))
    for p in docs:
        doc = utils.read_json(p)
        md = to_markdown(doc)
        (md_dir / (p.stem + ".md")).write_text(md, encoding="utf-8")
    utils.log(f"[렌더] {len(docs)}편 → {md_dir}")


if __name__ == "__main__":
    run()
