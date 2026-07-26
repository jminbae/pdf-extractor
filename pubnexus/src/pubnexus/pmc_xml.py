"""2단계a — PMC JATS XML → 정본 Document.

inEPMC=Y 논문은 출판사 정본 XML이 있으므로 파싱 오류가 원리적으로 0.
섹션 구조·인용 마커·표·그림·참고문헌이 전부 태그로 분리돼 있다(설계서 1단계).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from . import utils, jats, metadata
from .schema import (Document, Meta, Section, Paragraph, Figure, Table,
                     Reference, classify_section)
from .utils import HttpClient, norm_text, log


@dataclass
class ImageTable(Table):
    """<table> 없이 <graphic> 이미지 한 장으로만 실린 표.

    일부 저널의 PMC 변환본은 표를 스캔 이미지로 넣는다. 이때 markdown 이 비는 것은
    '추출 실패'가 아니라 '이미지 표'다 — 구분되지 않으면 회수 가능한 결함과
    회수 불가능한 원본 한계를 같은 통계에 섞게 된다. Table 의 하위형이라 기존
    소비자는 그대로 동작하고, asdict() 는 추가 필드까지 직렬화한다.
    (본문 회수는 pdf_fallback 담당 — 여기서는 표시만 남긴다.)
    """
    source: str = "graphic"        # 표 본문의 출처
    graphic: str = ""              # <graphic xlink:href> 이미지 파일명


def float_caption(elem) -> str:
    """<fig>/<table-wrap> 의 캡션을 만든다 — 라벨·제목·설명 사이 경계를 살린다.

    jats.caption_text 는 요소 전체를 itertext 로 이어 붙이므로 <title> 과 <p> 가
    맞붙어 문장이 뭉개진다(실측 10.1001/jamadermatol.2024.4534 doi240051f1:
    'Figure 1. Estimates of T-VASI Percentage Score Change From Baseline to Week
    24Abbreviations: …' — '24' 와 'Abbreviations' 사이 경계가 없다).
    여기서는 <label> · <caption>/<title> · <caption>/<p> 를 각각 뽑아 문장부호로 잇고,
    captions.dedupe_label 로 라벨이 두 번 찍힌 것을 지운다.
    """
    from . import captions as _cap

    def _txt(el) -> str:
        return norm_text("".join(el.itertext())) if el is not None else ""

    label = _txt(elem.find("{*}label"))
    cap = elem.find("{*}caption")
    bits: list[str] = []
    if cap is not None:
        title = cap.find("{*}title")
        if title is not None:
            bits.append(_txt(title))
        for p in cap.findall("{*}p"):
            t = _txt(p)
            if t:
                bits.append(t)
        if not bits:
            bits.append(_txt(cap))
    parts = [b for b in bits if b]
    out = label
    for b in parts:
        if not out:
            out = b
        else:
            out = out + ("" if out.endswith((".", ":", "?", "!")) else ".") + " " + b
    text, _n = _cap.dedupe_label(jats.clean_paragraph(out))
    return text


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
                   pcount, ref_ids=None) -> list[Section]:
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
            info = jats.paragraph_text(child, ref_ids)
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
                                      figures, tables, pcount, ref_ids))
        # 표/그림은 parse()의 단일 문서 패스에서 일괄 추출(여기선 처리하지 않음)

    if section.paragraphs:
        out.append(section)
    return out


# 이 논문이 아닌 '딸린 글' — 동료심사 보고서·편집자 논평·저자 답변.
# 여기 실린 표·그림은 본 논문의 것이 아니므로 정본에 넣으면 안 된다.
_FOREIGN_ARTICLE = frozenset({"sub-article", "response"})


def _in_sub_article(el) -> bool:
    """sub-article/response(동료심사·편집자 논평 등) 안의 요소인가."""
    for anc in el.iterancestors():
        if jats._local(anc.tag) in _FOREIGN_ARTICLE:
            return True
    return False


def _collect_floats(root, body, tag: str) -> list:
    """문서 전체를 훑어 tag 요소를 문서 순서대로 중복 없이 모은다(딸린 글 제외).

    **id() 로 중복을 판정하면 안 된다.** lxml 요소는 접근할 때마다 파이썬 프록시가
    새로 생성됐다 참조가 끊기는 즉시 GC 되므로, 해제된 주소가 다른 원소의 프록시에
    재사용되면 그 원소를 '이미 봤다'고 오판해 통째로 건너뛴다. 결과가 실행할 때마다
    달라지는 비결정적 소실이었다(수리 전 실측: 33편 중 8편에서 표·그림 개수가 요동).
    그래서 안정 식별자인 tree.getpath() 로 판정하고, 모은 요소는 반환 리스트가
    끝까지 붙들어 둬 프록시 수명 자체를 보장한다.

    범위를 body/floats-group/back 으로 좁히면 <front> 에 실린 그래픽 초록처럼
    바깥에 놓인 float 를 소리 없이 잃는다. 판정 기준을 '딸린 글 안인가' 하나로
    통일해 XPath 진값(//table-wrap[not(ancestor::sub-article)])과 정의를 맞춘다.
    body 인자는 공개 시그니처 유지를 위해 남겨둔다(범위 계산에는 쓰지 않는다).
    """
    tree = root.getroottree()
    out: list = []
    seen: set[str] = set()
    for el in root.iter(tag):
        key = tree.getpath(el)
        if key in seen or _in_sub_article(el):
            continue
        seen.add(key)
        out.append(el)
    return out


# 초록이 아닌 '초록 자리' 요소들 — 본문 초록보다 먼저 나와도 이것을 초록으로 삼으면 안 된다.
# (JAMA 계열은 <abstract abstract-type="teaser"> 한 문장이 맨 앞에 온다.)
_ABSTRACT_ASIDES = frozenset({"teaser", "graphical", "toc", "video", "web",
                              "précis", "precis", "editor-summary"})


def _pick_abstract(front):
    """front 안 여러 <abstract> 중 본문 초록을 고른다.

    JAMA 계열은 teaser·key-points·본초록이 나란히 있어서 '첫 번째'를 집으면
    한 문장짜리 티저가 초록으로 확정된다(수리 전 실측 2편). 타입 없는 초록이
    본초록이며, 여럿이면 가장 긴 것을 쓴다.
    """
    cands = front.findall(".//{*}abstract")
    if not cands:
        return None
    plain = [a for a in cands if not (a.get("abstract-type") or "").strip()]
    pool = plain or [a for a in cands
                     if (a.get("abstract-type") or "").strip().lower()
                     not in _ABSTRACT_ASIDES] or cands
    return max(pool, key=lambda a: len("".join(a.itertext())))


def parse(xml_bytes: bytes, meta: dict, source_file: str = "") -> Document:
    root = etree.fromstring(xml_bytes)
    refs, rid_to_ref = _build_ref_map(root)
    ref_ids = set(refs)          # 인용 rid 판정용(ref-type='ref' 등도 포착)

    figures: list[Figure] = []
    tables: list[Table] = []
    pcount = [0]
    body_text: list[Section] = []

    body = root.find(".//{*}body")
    if body is not None:
        for sec in body.findall("{*}sec"):
            body_text.extend(_walk_sections(sec, [], rid_to_ref,
                                            figures, tables, pcount, ref_ids))
        # 섹션 없이 <body> 직속 <p> 만 있는 경우
        loose = [c for c in body if jats._local(c.tag) == "p"]
        if loose:
            sec = Section(path=["Body"], section_type="other")
            for p in loose:
                info = jats.paragraph_text(p, ref_ids)
                if info["text"]:
                    pcount[0] += 1
                    sec.paragraphs.append(Paragraph(
                        id=f"p{pcount[0]}", text=info["text"],
                        cited_refs=[rid_to_ref.get(k, k) for k in info["cited_keys"]],
                        cited_keys=info["cited_keys"],
                        refs_figure=info["fig_ids"], refs_table=info["table_ids"]))
            if sec.paragraphs:
                body_text.append(sec)

    # 표/그림 단일 패스: body + floats-group + back 을 훑어 중복 없이 추출.
    # id 없는 것도 생성 id 부여(누락 방지). sub-article(동료심사 등)은 제외.
    for tw in _collect_floats(root, body, "{*}table-wrap"):
        tid = tw.get("id") or f"tab{len(tables)+1}"
        cap = float_caption(tw)
        md = jats.table_to_markdown(tw)
        href = jats.graphic_href(tw) if not md.strip() else ""
        if href:
            # <table> 없이 이미지로만 실린 표 — 빈 markdown 을 '추출 실패'와 구분한다.
            tables.append(ImageTable(id=tid, caption=cap, markdown="", graphic=href))
        else:
            tables.append(Table(id=tid, caption=cap, markdown=md))
    for fg in _collect_floats(root, body, "{*}fig"):
        figures.append(Figure(id=fg.get("id") or f"fig{len(figures)+1}",
                              caption=float_caption(fg)))

    # 초록은 '문서 자체'에서 추출(QC 초록대조를 실제 검증신호로 만들기 위함)
    extracted_abstract = ""
    front = root.find(".//{*}front")
    if front is not None:
        ab = _pick_abstract(front)
        if ab is not None:
            extracted_abstract = jats.abstract_text(ab, ref_ids)

    # 뽑았다고 해서 '이 논문의 초록'인 것은 아니다 — 표제 뒤 텍스트·API 정본과
    # 대조해 확정한다(metadata.choose_abstract). 검증 결과는 qc.abstract 로 남긴다.
    _first = ""
    for _s in body_text:
        for _p in _s.paragraphs:
            if _p.text:
                _first = _p.text
                break
        if _first:
            break
    abstract, abstract_source, _abs_info = metadata.choose_abstract(
        extracted_abstract, meta, source_file or None,
        body_first=_first, title=meta.get("title") or "")

    m = _meta_from_dict(meta)
    doc = Document(
        paper_id=meta.get("doi") or meta.get("pmid") or "unknown",
        source="pmc_xml",
        source_file=source_file,
        meta=m,
        abstract=abstract,
        abstract_source=abstract_source,
        body_text=body_text,
        figures=figures,
        tables=tables,
        references=list(refs.values()),
    )
    if _abs_info:
        doc.qc["abstract"] = _abs_info
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
            npar = sum(len(s.paragraphs) for s in doc.body_text)
            ncite = sum(len(p.cited_refs) for s in doc.body_text for p in s.paragraphs)
            log(f"  [{i}/{len(xml_metas)}] {pmcid}: 섹션 {len(doc.body_text)} · "
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
