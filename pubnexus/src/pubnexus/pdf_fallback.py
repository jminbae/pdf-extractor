"""2단계b-fallback — GROBID 미가동 시 PyMuPDF 폰트/레이아웃 기반 구조 추출.

GROBID(Docker/Linux)를 못 쓰는 환경을 위한 '차선책'. GROBID를 붙이면 stage 2b가
이 산출물을 덮어써 고품질로 승격된다.

개선 사항(범용 변환기 대비):
  · 2단 컬럼 읽기순서 정렬(좌단 전체 → 우단)
  · front matter(저자·소속·수신일·교신·펀딩·키워드) 제거 — 제목/저자/초록은 API 보유
  · 헤딩은 '번호 매김' 또는 '섹션 키워드' + 폰트 강조일 때만 인정(저자명 오탐 방지)
  · 세로 간격 기반 문단 재구성 + 줄바꿈 하이픈 분철 복원
  · 위첨자 인용번호를 폰트 플래그로 감지해 본문에서 제거
한계(GROBID만 가능): 인용→참고문헌 링크, 정밀 컬럼/표 복원.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import fitz

from . import utils
from .jats import _dedup, _tidy_punct
from .schema import Document, Meta, Section, Paragraph, classify_section
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
    r'grant number|conflict of interest|orcid|keywords?\b|key words|'
    r'how to cite|doi:|department of|division of|institute of|'
    r'college of medicine|©\s*\d{4})', re.I)
# 저자명 나열 라인: "Bo Ri Kim | Kun Hee Lee | ..." 또는 콤마 구분 다수 이름
AUTHORLIST_RE = re.compile(r'^([A-Z][a-z]+(?:\s[A-Z][a-z.]+){0,3})(\s*[|,]\s*[A-Z][a-z]+(?:\s[A-Z][a-z.]+){0,3}){1,}$')


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


def _collect_lines(doc, body: float) -> list[dict]:
    """모든 페이지의 라인을 (2단 컬럼 인식) 읽기순서로 수집."""
    out = []
    for pno in range(doc.page_count):
        pg = doc[pno]
        mid = pg.rect.width / 2
        page_lines = []
        for b in pg.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for l in b["lines"]:
                if not l["spans"]:
                    continue
                text, cites = _line_text_and_cites(l, body)
                text = text.strip()
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
                col = 0 if (x0 + x1) / 2 < mid else 1
                page_lines.append({"text": text, "cites": cites, "size": size,
                                   "bold": bold, "italic": italic, "page": pno,
                                   "col": col, "x0": x0, "y0": y0, "y1": y1})
        # 컬럼 → y 순으로 정렬(좌단 전체 후 우단)
        page_lines.sort(key=lambda d: (d["col"], round(d["y0"])))
        out.extend(page_lines)
    return out


def _is_heading(ln: dict, body: float) -> bool:
    t = ln["text"].strip()
    if not (3 <= len(t) <= 90):
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
    if (emphasized and words <= 8 and t[0:1].isupper()
            and not t.rstrip().endswith((".", ",", ";", ":"))
            and not _is_frontmatter(t) and not AUTHORLIST_RE.match(t)
            and sum(c.isdigit() for c in t) <= 4):
        return True
    return False


def _is_frontmatter(text: str) -> bool:
    t = text.strip()
    if FRONT_RE.match(t):
        return True
    if AUTHORLIST_RE.match(t) and len(t) < 160:
        return True
    return False


def _para_break(prev: dict, ln: dict, body: float) -> bool:
    """이전 줄과 현재 줄 사이가 새 문단 경계인가."""
    if ln["page"] != prev["page"] or ln["col"] != prev["col"]:
        return True
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


def _body_start(lines: list[dict], body: float) -> int:
    """본문 시작 인덱스: Introduction/1번 섹션 우선(제목·저자·초록 스킵)."""
    headings = [(i, ln) for i, ln in enumerate(lines) if _is_heading(ln, body)]
    for i, ln in headings:
        core = re.sub(r'^\d+[\s.|)]*', '', ln["text"]).strip().lower().rstrip(":")
        if core.startswith(("introduction", "background")) or re.match(r'^1[\s.|)]\s', ln["text"]):
            return i
    for k, (i, ln) in enumerate(headings):   # 'abstract' 다음 헤딩
        if re.sub(r'^\d+[\s.|)]*', '', ln["text"]).strip().lower().rstrip(":") == "abstract":
            return headings[k + 1][0] if k + 1 < len(headings) else i
    return headings[0][0] if headings else 0


def _reconstruct(lines: list[dict], body: float) -> list[Section]:
    """헤딩·문단 재구성. front matter 제거, body 시작 이후만."""
    lines = lines[_body_start(lines, body):]

    sections: list[Section] = []
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
                sections.append(cur)
            title = clean_heading(ln["text"])   # 자간 아티팩트 복원(textfix)
            cur = Section(path=[title], section_type=classify_section(
                re.sub(r'^\d+[\s.|)]*', '', title)))
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
        sections.append(cur)
    return sections


def parse_pdf(path: Path, meta: dict) -> Document:
    doc = fitz.open(path)
    try:
        return _parse_open_doc(doc, path, meta)
    finally:
        doc.close()   # 예외 발생 시에도 핸들 누수 방지


def _parse_open_doc(doc, path: Path, meta: dict) -> Document:
    # 본문 폰트 크기
    spans_sizes = [(s["size"], s["text"]) for pno in range(doc.page_count)
                   for b in doc[pno].get_text("dict")["blocks"] if b.get("type") == 0
                   for l in b["lines"] for s in l["spans"]]
    body = _body_size(spans_sizes)

    lines = _collect_lines(doc, body)

    # 반복 머리말/꼬리말 제거(여러 페이지에 동일 텍스트)
    freq = Counter(ln["text"][:60] for ln in lines)
    npages = doc.page_count
    repeated = {k for k, n in freq.items() if n >= max(3, npages * 0.4)}
    lines = [ln for ln in lines if ln["text"][:60] not in repeated]

    sections = _reconstruct(lines, body)

    m = Meta(
        doi=meta.get("doi"), pmid=meta.get("pmid"), pmcid=meta.get("pmcid"),
        title=meta.get("title", ""), authors=meta.get("authors", []),
        journal=meta.get("journal", ""), year=meta.get("year"),
        mesh=meta.get("mesh", []), pub_types=meta.get("pub_types", []),
        rcr=meta.get("rcr"), citation_count=meta.get("citation_count"),
        is_open_access=bool(meta.get("is_open_access")),
    )
    api_abstract = meta.get("abstract_pubmed") or meta.get("abstract") or ""
    return Document(
        paper_id=meta.get("doi") or meta.get("pmid") or "unknown",
        source="pdf_fallback", source_file=str(path), meta=m,
        abstract=api_abstract, abstract_source="api" if api_abstract else "none",
        sections=sections, figures=[], tables=[], references=[],
    )


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
            npar = sum(len(s.paragraphs) for s in doc.sections)
            ncite = sum(len(p.cited_keys) for s in doc.sections for p in s.paragraphs)
            log(f"  [{i}/{len(targets)}] {r['doi']}: 섹션 {len(doc.sections)} · "
                f"문단 {npar} · 인용마커 {ncite} (참조링크 없음)")
            docs.append(doc)
        except Exception as e:  # noqa: BLE001 — 파일 단위 격리
            failed += 1
            log(f"  [{i}/{len(targets)}] 폴백 실패({r['doi']}): {type(e).__name__}: {e}")
    log(f"[2단계b-fallback] 완료 → {norm_dir} (성공 {len(docs)}, 실패 {failed})")
    return docs


if __name__ == "__main__":
    run()
