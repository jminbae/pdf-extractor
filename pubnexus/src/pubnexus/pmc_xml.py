"""2단계a — PMC JATS XML → 정본 Document.

inEPMC=Y 논문은 출판사 정본 XML이 있으므로 파싱 오류가 원리적으로 0.
섹션 구조·인용 마커·표·그림·참고문헌이 전부 태그로 분리돼 있다(설계서 1단계).
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from . import utils, jats
from .schema import (Document, Meta, Section, Paragraph, Figure, Table,
                     Reference, classify_section)
from .utils import HttpClient, norm_text, log


def fetch_xml(http: HttpClient, base: str, pmcid: str, cache_dir: Path) -> bytes | None:
    cache = cache_dir / f"{pmcid}.xml"
    if cache.exists():
        return cache.read_bytes()
    r = http.get(f"{base}/{pmcid}/fullTextXML", accept="application/xml")
    if r is None or not r.content:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    return r.content


def _build_ref_map(root) -> tuple[dict[str, Reference], dict[str, str]]:
    """ref-list → {key: Reference}, {rid: doi_or_key}."""
    refs: dict[str, Reference] = {}
    rid_to_ref: dict[str, str] = {}
    for ref in root.iter("{*}ref"):
        key = ref.get("id") or ""
        if not key:
            continue
        doi = pmid = title = None
        year = None
        cit = ref.find("{*}element-citation")
        if cit is None:
            cit = ref.find("{*}mixed-citation")
        scope = cit if cit is not None else ref
        for pid in scope.iter("{*}pub-id"):
            t = pid.get("pub-id-type")
            if t == "doi":
                doi = utils.clean_doi(pid.text)
            elif t == "pmid":
                pmid = (pid.text or "").strip()
        at = scope.find("{*}article-title")
        if at is not None:
            title = norm_text("".join(at.itertext()))
        yr = scope.find("{*}year")
        if yr is not None and yr.text:
            try:
                year = int(yr.text[:4])
            except ValueError:
                pass
        raw = norm_text("".join(scope.itertext()))[:400]
        refs[key] = Reference(key=key, doi=doi, pmid=pmid,
                              title=title or "", year=year, raw=raw)
        rid_to_ref[key] = doi or key
    return refs, rid_to_ref


def _walk_sections(sec_elem, path, rid_to_ref, figures, tables,
                   pcount) -> list[Section]:
    """<sec> 재귀 → Section 리스트(중첩 평탄화, path 보존)."""
    title_el = sec_elem.find("{*}title")
    title = norm_text("".join(title_el.itertext())) if title_el is not None else ""
    cur_path = path + ([title] if title else [])

    out: list[Section] = []
    section = Section(path=cur_path or ["Body"],
                      section_type=classify_section(title or (path[-1] if path else "")))

    for child in sec_elem:
        tag = jats._local(child.tag)
        if tag == "p":
            info = jats.paragraph_text(child)
            if not info["text"]:
                continue
            pcount[0] += 1
            cited = [rid_to_ref.get(k, k) for k in info["cited_keys"]]
            section.paragraphs.append(Paragraph(
                id=f"p{pcount[0]}",
                text=info["text"],
                cited_refs=cited,
                cited_keys=info["cited_keys"],
                refs_figure=info["fig_ids"],
                refs_table=info["table_ids"],
            ))
        elif tag == "sec":
            # 자식 섹션은 재귀 (현재 섹션 먼저 닫고 이어붙임)
            if section.paragraphs:
                out.append(section)
                section = Section(path=cur_path or ["Body"],
                                  section_type=classify_section(title))
            out.extend(_walk_sections(child, cur_path, rid_to_ref,
                                      figures, tables, pcount))
        # 표/그림은 parse()의 단일 문서 패스에서 일괄 추출(여기선 처리하지 않음)

    if section.paragraphs:
        out.append(section)
    return out


def parse(xml_bytes: bytes, meta: dict, source_file: str = "") -> Document:
    root = etree.fromstring(xml_bytes)
    refs, rid_to_ref = _build_ref_map(root)

    figures: list[Figure] = []
    tables: list[Table] = []
    pcount = [0]
    sections: list[Section] = []

    body = root.find(".//{*}body")
    if body is not None:
        for sec in body.findall("{*}sec"):
            sections.extend(_walk_sections(sec, [], rid_to_ref,
                                           figures, tables, pcount))
        # 섹션 없이 <body> 직속 <p> 만 있는 경우
        loose = [c for c in body if jats._local(c.tag) == "p"]
        if loose:
            sec = Section(path=["Body"], section_type="other")
            for p in loose:
                info = jats.paragraph_text(p)
                if info["text"]:
                    pcount[0] += 1
                    sec.paragraphs.append(Paragraph(
                        id=f"p{pcount[0]}", text=info["text"],
                        cited_refs=[rid_to_ref.get(k, k) for k in info["cited_keys"]],
                        cited_keys=info["cited_keys"],
                        refs_figure=info["fig_ids"], refs_table=info["table_ids"]))
            if sec.paragraphs:
                sections.append(sec)

    # 표/그림 단일 패스: body + floats-group + back 을 훑어 중복 없이 추출.
    # id 없는 것도 생성 id 부여(누락 방지). sub-article(동료심사 등)은 제외.
    subarticle_els = {id(e) for sa in root.findall(".//{*}sub-article")
                      for e in sa.iter()}
    scopes = [body] + root.findall(".//{*}floats-group") + root.findall(".//{*}back")
    seen_t: set[int] = set()
    seen_f: set[int] = set()
    for scope in scopes:
        if scope is None:
            continue
        for tw in scope.iter("{*}table-wrap"):
            if id(tw) in seen_t or id(tw) in subarticle_els:
                continue
            seen_t.add(id(tw))
            tables.append(Table(id=tw.get("id") or f"tab{len(tables)+1}",
                                caption=jats.caption_text(tw),
                                markdown=jats.table_to_markdown(tw)))
        for fg in scope.iter("{*}fig"):
            if id(fg) in seen_f or id(fg) in subarticle_els:
                continue
            seen_f.add(id(fg))
            figures.append(Figure(id=fg.get("id") or f"fig{len(figures)+1}",
                                  caption=jats.caption_text(fg)))

    # 초록은 '문서 자체'에서 추출(QC 초록대조를 실제 검증신호로 만들기 위함)
    extracted_abstract = ""
    front = root.find(".//{*}front")
    if front is not None:
        ab = front.find(".//{*}abstract")
        if ab is not None:
            extracted_abstract = norm_text(" ".join(
                "".join(p.itertext()) for p in ab.iter("{*}p"))) or \
                norm_text("".join(ab.itertext()))

    api_abstract = meta.get("abstract_pubmed") or meta.get("abstract") or ""
    if extracted_abstract:
        abstract, abstract_source = extracted_abstract, "extracted"
    elif api_abstract:
        abstract, abstract_source = api_abstract, "api"
    else:
        abstract, abstract_source = "", "none"

    m = _meta_from_dict(meta)
    doc = Document(
        paper_id=meta.get("doi") or meta.get("pmid") or "unknown",
        source="pmc_xml",
        source_file=source_file,
        meta=m,
        abstract=abstract,
        abstract_source=abstract_source,
        sections=sections,
        figures=figures,
        tables=tables,
        references=list(refs.values()),
    )
    return doc


def _meta_from_dict(meta: dict) -> Meta:
    return Meta(
        doi=meta.get("doi"), pmid=meta.get("pmid"), pmcid=meta.get("pmcid"),
        title=meta.get("title", ""), authors=meta.get("authors", []),
        journal=meta.get("journal", ""), year=meta.get("year"),
        mesh=meta.get("mesh", []), pub_types=meta.get("pub_types", []),
        rcr=meta.get("rcr"), citation_count=meta.get("citation_count"),
        is_open_access=bool(meta.get("is_open_access")),
    )


def run(config: dict | None = None) -> list[Document]:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    xml_dir = work / "xml"
    norm_dir = work / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)

    manifest = {r["doi"]: r for r in utils.read_jsonl(work / "manifest.jsonl")
                if r.get("doi")}
    metas = [utils.read_json(p) for p in sorted((work / "meta").glob("*.json"))]
    xml_metas = [m for m in metas if m.get("in_epmc") and m.get("pmcid")]
    log(f"[2단계a] PMC XML: {len(xml_metas)}편")

    http = HttpClient(email=cfg["metadata"]["email"],
                      delay=cfg["metadata"]["request_delay_sec"],
                      timeout=cfg["metadata"]["timeout_sec"])
    base = cfg["fulltext"]["europepmc_base"]

    docs, failed = [], 0
    for i, meta in enumerate(xml_metas, 1):
        pmcid = meta["pmcid"]
        try:
            xml = fetch_xml(http, base, pmcid, xml_dir)
            if not xml:
                log(f"  [{i}/{len(xml_metas)}] XML 없음: {pmcid}"); failed += 1; continue
            src = manifest.get(meta["doi"], {}).get("file", "")
            doc = parse(xml, meta, source_file=src)
            dest = norm_dir / f"{utils.slug(doc.paper_id)}.json"
            utils.write_json(dest, doc.to_dict())
            npar = sum(len(s.paragraphs) for s in doc.sections)
            ncite = sum(len(p.cited_refs) for s in doc.sections for p in s.paragraphs)
            log(f"  [{i}/{len(xml_metas)}] {pmcid}: 섹션 {len(doc.sections)} · "
                f"문단 {npar} · 인용링크 {ncite} · 표 {len(doc.tables)} · "
                f"그림 {len(doc.figures)} · 참고문헌 {len(doc.references)}")
            docs.append(doc)
        except Exception as e:  # noqa: BLE001 — 한 편 실패가 배치를 멈추지 않도록 격리
            failed += 1
            log(f"  [{i}/{len(xml_metas)}] 파싱 실패({pmcid}): {type(e).__name__}: {e}")
    log(f"[2단계a] 완료 → {norm_dir} (성공 {len(docs)}, 실패 {failed})")
    return docs


if __name__ == "__main__":
    run()
