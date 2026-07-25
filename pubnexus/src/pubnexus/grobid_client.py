"""2단계b — GROBID(TEI XML) → 정본 Document.

비-PMC born-digital PDF의 유일한 정답(설계서 2단계). GROBID는 인라인 인용을
<ref type="bibr" target="#b14">15</ref> 로 '태그'하므로, 본문에서 인용번호를
제거하되 cited_refs 로 옮길 수 있다 — 범용 변환기가 못 하는 부분.

엔드포인트는 설정(grobid.url)으로 주입. 프로덕션=로컬 Docker, 파일럿=공개서버.
"""
from __future__ import annotations

import re
from pathlib import Path

import requests
from lxml import etree

from . import utils
from .schema import (Document, Meta, Section, Paragraph, Figure, Table,
                     Reference, classify_section)
from .textfix import clean_heading, clean_paragraph
from .utils import norm_text, log

XMLID = "{http://www.w3.org/XML/1998/namespace}id"


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


# ── GROBID 서비스 호출 ───────────────────────────────────────────────
def is_alive(url: str, timeout: int = 20) -> bool:
    try:
        r = requests.get(url.rstrip("/") + "/api/isalive", timeout=timeout)
        return r.status_code == 200 and "true" in r.text.lower()
    except requests.RequestException:
        return False


def process_pdf(url: str, pdf_path: Path, cfg_grobid: dict) -> bytes | None:
    """PDF → TEI XML(bytes). 실패 시 None."""
    endpoint = url.rstrip("/") + "/api/processFulltextDocument"
    data = {
        "consolidateHeader": str(cfg_grobid.get("consolidate_header", 1)),
        "consolidateCitations": "0",
        "includeRawCitations": str(cfg_grobid.get("include_raw_citations", 1)),
        "segmentSentences": str(cfg_grobid.get("segment_sentences", 0)),
    }
    with open(pdf_path, "rb") as f:
        files = {"input": (pdf_path.name, f, "application/pdf")}
        try:
            r = requests.post(endpoint, files=files, data=data,
                              timeout=cfg_grobid.get("timeout_sec", 300))
        except requests.RequestException as e:
            log(f"      ! GROBID 호출 실패: {e}")
            return None
    if r.status_code != 200 or not r.content:
        log(f"      ! GROBID {r.status_code}")
        return None
    return r.content


# ── TEI 파싱 ─────────────────────────────────────────────────────────
def _tei_paragraph(p_elem) -> dict:
    """TEI <p> → {text, cited_keys, fig_ids, table_ids}. bibr 인용 숫자는 제거."""
    cited, figs, tables, parts = [], [], [], []

    def walk(node):
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = _local(child.tag)
            if tag == "ref":
                rtype = child.get("type", "")
                target = (child.get("target") or "").lstrip("#")
                if rtype == "bibr":
                    if target:
                        cited.append(target)
                    # 인용 숫자 텍스트 제거
                elif rtype == "figure":
                    if target:
                        figs.append(target)
                    if child.text:
                        parts.append(child.text)
                elif rtype == "table":
                    if target:
                        tables.append(target)
                    if child.text:
                        parts.append(child.text)
                else:
                    if child.text:
                        parts.append(child.text)
            elif tag == "formula":
                pass  # 수식은 본문에서 제외 (tail 은 살림)
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(p_elem)
    from .jats import _tidy_punct, _dedup
    # 러닝헤더·자간 아티팩트 등 추출 결함 수리(textfix, 결함 발생 지점에서 차단)
    text = clean_paragraph(_tidy_punct(norm_text("".join(parts))))
    return {"text": text, "cited_keys": _dedup(cited),
            "fig_ids": _dedup(figs), "table_ids": _dedup(tables)}


def _build_refs(root) -> tuple[dict[str, Reference], dict[str, str]]:
    refs, rid_to_ref = {}, {}
    back = root.find(".//{*}back")
    if back is None:
        return refs, rid_to_ref
    for bs in back.iter("{*}biblStruct"):
        key = bs.get(XMLID)
        if not key:
            continue
        doi = pmid = None
        for idno in bs.iter("{*}idno"):
            t = (idno.get("type") or "").lower()
            if t == "doi":
                doi = utils.clean_doi(idno.text)
            elif t == "pmid":
                pmid = (idno.text or "").strip()
        title_el = bs.find(".//{*}title[@type='main']")
        if title_el is None:
            title_el = bs.find(".//{*}title")
        title = norm_text("".join(title_el.itertext())) if title_el is not None else ""
        year = None
        date = bs.find(".//{*}date")
        if date is not None:
            w = date.get("when") or (date.text or "")
            if w[:4].isdigit():
                year = int(w[:4])
        raw = norm_text("".join(bs.itertext()))[:400]
        refs[key] = Reference(key=key, doi=doi, pmid=pmid, title=title,
                              year=year, raw=raw)
        rid_to_ref[key] = doi or key
    return refs, rid_to_ref


def _tei_table_markdown(figure) -> str:
    tbl = figure.find(".//{*}table")
    if tbl is None:
        return ""
    rows = []
    for row in tbl.iter("{*}row"):
        cells = [norm_text("".join(c.itertext()))
                 for c in row if _local(c.tag) == "cell"]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |",
          "| " + " | ".join(["---"] * ncol) + " |"]
    for r in rows[1:]:
        md.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
    return "\n".join(md)


def parse_tei(tei_bytes: bytes, meta: dict, source_file: str = "") -> Document:
    root = etree.fromstring(tei_bytes)
    refs, rid_to_ref = _build_refs(root)

    figures: list[Figure] = []
    tables: list[Table] = []
    sections: list[Section] = []
    pcount = [0]

    body = root.find(".//{*}text/{*}body")
    if body is not None:
        for div in body.findall("{*}div"):
            head_el = div.find("{*}head")
            title = norm_text("".join(head_el.itertext())) if head_el is not None else ""
            title = re.sub(r'^[\s|.)]+', '', title).strip()  # Wiley "3.2 |" 등 잔재 정리
            title = clean_heading(title)   # 자간 아티팩트 복원(textfix)
            sec = Section(path=[title] if title else ["Body"],
                          section_type=classify_section(title))
            for p in div.findall("{*}p"):
                info = _tei_paragraph(p)
                if not info["text"]:
                    continue
                pcount[0] += 1
                sec.paragraphs.append(Paragraph(
                    id=f"p{pcount[0]}", text=info["text"],
                    cited_refs=[rid_to_ref.get(k, k) for k in info["cited_keys"]],
                    cited_keys=info["cited_keys"],
                    refs_figure=info["fig_ids"], refs_table=info["table_ids"]))
            if sec.paragraphs:
                sections.append(sec)

        # figure / table (GROBID 는 body 하위에 <figure> 로 둠)
        for fig in body.iter("{*}figure"):
            fid = fig.get(XMLID) or ""
            if fig.get("type") == "table":
                cap_el = fig.find("{*}figDesc")
                if cap_el is None:
                    cap_el = fig.find("{*}head")
                caption = norm_text("".join(cap_el.itertext())) if cap_el is not None else ""
                tables.append(Table(id=fid or f"tab{len(tables)+1}",
                                    caption=clean_paragraph(caption),
                                    markdown=_tei_table_markdown(fig)))
            else:
                head_el = fig.find("{*}head")
                desc_el = fig.find("{*}figDesc")
                cap = clean_paragraph(" ".join(norm_text("".join(e.itertext()))
                                               for e in (head_el, desc_el) if e is not None))
                figures.append(Figure(id=fid or f"fig{len(figures)+1}", caption=cap))

    m = Meta(
        doi=meta.get("doi"), pmid=meta.get("pmid"), pmcid=meta.get("pmcid"),
        title=meta.get("title", ""), authors=meta.get("authors", []),
        journal=meta.get("journal", ""), year=meta.get("year"),
        mesh=meta.get("mesh", []), pub_types=meta.get("pub_types", []),
        rcr=meta.get("rcr"), citation_count=meta.get("citation_count"),
        is_open_access=bool(meta.get("is_open_access")),
    )
    # 초록은 TEI 헤더에서 추출(QC 초록대조용 실제 검증신호)
    extracted_abstract = ""
    ab = root.find(".//{*}profileDesc/{*}abstract")
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

    return Document(
        paper_id=meta.get("doi") or meta.get("pmid") or "unknown",
        source="grobid", source_file=source_file, meta=m,
        abstract=abstract, abstract_source=abstract_source,
        sections=sections, figures=figures, tables=tables,
        references=list(refs.values()),
    )


def run(config: dict | None = None) -> list[Document]:
    cfg = config or utils.load_config()
    gcfg = cfg["grobid"]
    url = gcfg["url"]
    work = utils.resolve(cfg["project"]["work_dir"])
    tei_dir = work / "tei"
    norm_dir = work / "normalized"
    tei_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)

    if not is_alive(url):
        log(f"[2단계b] GROBID 서버 응답 없음: {url}")
        log("        → 로컬 Docker 구동 필요: "
            "docker run --rm -p 8070:8070 lfoppiano/grobid:latest-full")
        return []

    manifest = utils.read_jsonl(work / "manifest.jsonl")
    metas = {m["doi"]: m for m in
             (utils.read_json(p) for p in (work / "meta").glob("*.json"))}
    # 비-PMC(원문XML 없음) + primary 만 GROBID 경로
    targets = [r for r in manifest if r.get("is_primary") and r.get("doi")
               and not metas.get(r["doi"], {}).get("in_epmc")]
    log(f"[2단계b] GROBID: {len(targets)}편 @ {url}")

    docs, failed = [], 0
    for i, r in enumerate(targets, 1):
        doi = r["doi"]
        try:
            pdf = Path(r["file"])
            tei_cache = tei_dir / f"{utils.slug(doi)}.tei.xml"
            if tei_cache.exists():
                tei = tei_cache.read_bytes()
            else:
                tei = process_pdf(url, pdf, gcfg)
                if tei:
                    tei_cache.write_bytes(tei)
            if not tei:
                log(f"  [{i}/{len(targets)}] 변환 실패: {doi}"); failed += 1; continue
            doc = parse_tei(tei, metas.get(doi, {"doi": doi}), source_file=str(pdf))
            dest = norm_dir / f"{utils.slug(doc.paper_id)}.json"
            utils.write_json(dest, doc.to_dict())
            npar = sum(len(s.paragraphs) for s in doc.sections)
            ncite = sum(len(p.cited_refs) for s in doc.sections for p in s.paragraphs)
            log(f"  [{i}/{len(targets)}] {doi}: 섹션 {len(doc.sections)} · 문단 {npar} · "
                f"인용링크 {ncite} · 표 {len(doc.tables)} · 참고문헌 {len(doc.references)}")
            docs.append(doc)
        except Exception as e:  # noqa: BLE001 — 파일 단위 격리
            failed += 1
            log(f"  [{i}/{len(targets)}] 파싱 실패({doi}): {type(e).__name__}: {e}")
    log(f"[2단계b] 완료 → {norm_dir} (성공 {len(docs)}, 실패 {failed})")
    return docs


if __name__ == "__main__":
    run()
