"""2단계b — GROBID(TEI XML) → 정본 Document.

비-PMC born-digital PDF의 유일한 정답(설계서 2단계). GROBID는 인라인 인용을
<ref type="bibr" target="#b14">15</ref> 로 '태그'하므로, 본문에서 인용번호를
제거하되 cited_refs 로 옮길 수 있다 — 범용 변환기가 못 하는 부분.

엔드포인트는 설정(grobid.url)으로 주입. 프로덕션=로컬 Docker, 파일럿=공개서버.

TEI 실측(캐시 134편)에 기반한 파서 방침:
  · <body> 아래 <div> 는 **평면**으로 나온다(중첩 div 0개/1,140개). 계층은
    head 자체의 신호(@n 점번호·정규 IMRaD 제목·대문자 조판 관례)로만 보수적으로
    추론한다. 신호가 없으면 계층을 만들지 않는다.
  · <figure> 에는 진짜 그림뿐 아니라 약어상자·capsule summary·검색전략 같은
    고립 텍스트 박스도 섞여 나온다 → <graphic> 유무·라벨 형태로 게이트.
  · 캡션은 <head>(라벨) + <figDesc>(본문)로 쪼개져 나오므로 둘을 합치되
    라벨 중복('Fig. 1. Fig. 1. …')을 만들지 않는다.
  · <body> 가 비어 있는 TEI 를 조용히 통과시키지 않는다(예외 → 상위 폴백).
"""
from __future__ import annotations

import re
from pathlib import Path

import requests
from lxml import etree

from . import utils
from .schema import (Document, Meta, Section, Paragraph, Figure, Table,
                     Reference, BACK_MATTER, classify_section, classify_path,
                     normalize_title)
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
_CITE_NUM_RE = re.compile(r"^[\s\[\(]*([\d]{1,3}(?:\s*[,\-–]\s*\d{1,3})*)[\s\]\)]*$")


def _cite_marker(raw: str | None) -> str:
    """인용 마커를 본문 그 자리에 남길 문자열로 만든다.

    논문에 인쇄된 번호를 그대로 살려 [15] 로 감싼다. 대괄호가 핵심이다 —
    위첨자 인용은 텍스트로 뽑으면 '효과가 있었다15,16' 처럼 본문 수치와 섞여
    구분이 불가능해진다. '15,16' 처럼 여러 개가 한 마커에 묶여 있으면 [15][16] 로 편다.
    번호가 아닌 저자-연도식 인용(예: 'Smith et al., 2019')은 이미 읽을 수 있으므로
    원문 그대로 둔다.
    """
    t = (raw or "").strip()
    if not t:
        return ""
    m = _CITE_NUM_RE.match(t)
    if not m:
        return t                                   # 저자-연도식 → 손대지 않음
    body = m.group(1)
    out: list[str] = []
    for chunk in re.split(r"\s*,\s*", body):
        rng = re.match(r"^(\d{1,3})\s*[-–]\s*(\d{1,3})$", chunk.strip())
        if rng:                                    # '12-14' 는 12,13,14 를 뜻한다
            a, b = int(rng.group(1)), int(rng.group(2))
            if 0 < b - a <= 40:                    # 비정상적으로 넓으면 범위가 아니다
                out += [str(n) for n in range(a, b + 1)]
                continue
        if chunk.strip().isdigit():
            out.append(chunk.strip())
    return "".join(f"[{n}]" for n in out) if out else f"[{body}]"


def _display_nums(raw: str | None) -> list[str]:
    """인용 마커 원문에서 논문에 인쇄된 번호들을 뽑는다('12-14' → 12,13,14)."""
    return re.findall(r"\d{1,3}", _cite_marker(raw))


def _tei_paragraph(p_elem) -> dict:
    """TEI <p> → {text, cited_keys, cited_targets, fig_ids, table_ids}.

    bibr 인용은 본문에 [15] 로 남긴다. GROBID 가 target 을 못 붙인 bibr(실측
    4,350개 중 250개)도 **표시번호를 'num:15' 형태로 cited_keys 에 보존**한다 —
    나중에 참고문헌 목록과 번호로 대조할 수 있어야 인용이 증발하지 않는다.
    cited_targets 는 target 이 실제로 있는 것만 담아 cited_refs 해소에 쓴다
    (추정으로 참고문헌을 연결하면 잘못된 출처를 만들어내므로 하지 않는다).
    """
    cited, targets, figs, tables, parts = [], [], [], [], []

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
                        targets.append(target)
                    else:
                        # target 이 없어도 인쇄된 번호는 남긴다(대조용 미해소 키).
                        cited.extend(f"num:{n}" for n in _display_nums(child.text))
                    # 인용 마커를 '그 자리에' [15] 형태로 남긴다.
                    #   위첨자 인용은 텍스트로 뽑히면 '치료했다15,16' 처럼 본문 수치와
                    #   구분이 안 된다. 대괄호로 감싸면 텍스트만 가져가도 인용임이 명확하다.
                    #   번호는 논문에 인쇄된 것을 그대로 쓴다(참고문헌 목록과 대조 가능).
                    parts.append(_cite_marker(child.text))
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
            "cited_targets": _dedup(targets),
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


# ── 섹션 계층 복원 ───────────────────────────────────────────────────
#
# GROBID 는 <body> 아래에 <div> 를 평면으로 내보낸다 — 캐시 134편 실측에서
# 중첩 div 는 0개, 1,140개가 전부 body 직속이었다. 그래서 재귀 순회만으로는
# 계층이 살아나지 않는다. 재귀는 그대로 두되(다른 GROBID 버전·설정은 중첩을
# 내보내므로), 평면 출력에는 head 자체의 신호로만 보수적으로 계층을 매긴다.
#
# **최상위**로 올리는 신호는 셋뿐이고 전부 실측으로 확인했다:
#   1) head/@n 의 점 번호("3.2.1") — 조판 번호 그대로. 16편에 존재.
#   2) 구조어만으로 된 최상위 제목(어순 무관, _TOPLEVEL_RE) + 부속 제목 목록.
#   3) 한 문서 안에 대문자 제목과 혼합대소문자 제목이 공존하면
#      대문자 = 최상위라는 출판사 조판 관례. 47편에서 관측.
# 신호가 없는 제목을 **하위절로 내리는 것**은 전제가 확인될 때만 한다:
# 문서가 IMRaD 골격을 갖췄고(_is_imrad) 현재 루트가 구조 절일 때. 이 전제를
# 빼면 IMRaD 가 아닌 리뷰에서 첫 제목이 문서 전체를 삼킨다(실측 확인, _is_imrad 참고).
#
# 부속 제목(References·Funding 등)은 반드시 최상위로 둔다 — classify_path 가
# 경로의 root 를 먼저 보므로, 'Funding' 을 Discussion 밑에 넣으면 back 이어야 할
# 섹션이 discussion 으로 잘못 분류된다.
_ROOT_TITLES = frozenset({
    "abstract", "summary",
    "introduction", "background", "objective", "objectives",
    "method", "methods", "methodology",
    "material and methods", "materials and methods",
    "material & methods", "materials & methods",
    "patients and methods", "subjects and methods",
    "patients, materials and methods",
    # 'study design'·'statistical analysis' 류는 Methods 의 **하위** 절이므로
    # 최상위 목록에 넣지 않는다(넣으면 계층이 다시 평평해진다).
    "result", "results", "findings",
    "discussion", "discussions", "comment", "comments",
    "conclusion", "conclusions",
    "case report", "case presentation", "case description", "case series",
})

# 위 목록은 어순 변형을 못 잡는다 — 'Materials and Methods' 만 있고
# 'Methods and Materials' 가 없어서, 실측 1편(10.1016/j.ijrobp.2017.03.008)의
# Methods 절 전체가 Introduction 밑으로 들어가 intro 로 오분류됐다. 그래서
# **구조어만으로 이루어진 제목**은 어순과 무관하게 최상위로 본다 — 구조어 밖의
# 낱말이 하나라도 섞이면(예: 'Statistical analysis') 걸리지 않으므로
# 하위절을 최상위로 승격시키는 반대 방향 오탐은 생기지 않는다.
_CORE = (r"(?:materials?|patients?|subjects?|methods?|methodology|results?|"
         r"findings?|discussions?|conclusions?|introduction|background|"
         r"comments?|summary|abstract|objectives?|"
         r"case\s+(?:report|series|presentation|description))")
_TOPLEVEL_RE = re.compile(rf"^{_CORE}(?:\s*(?:and|&|,|/)\s*{_CORE})*$", re.I)

# 하위절을 거느릴 수 있는 '구조적' 최상위 타입. 이 밖(other·back·잡음 제목)이
# 루트일 때 그 밑에 본문을 넣으면 classify_path 가 루트를 보고 오분류한다.
_STRUCTURAL = frozenset({"abstract", "intro", "methods", "results", "discussion"})


def _is_caps(title: str) -> bool:
    """대문자 전용 제목인가(자간 아티팩트 대비 알파벳 3자 이상일 때만)."""
    letters = [c for c in title if c.isalpha()]
    return len(letters) >= 3 and not any(c.islower() for c in letters)


def _is_root_title(title: str) -> bool:
    t = normalize_title(title).lower().strip(" :.")
    if not t:
        return False
    return (t in _ROOT_TITLES or t in BACK_MATTER
            or bool(_TOPLEVEL_RE.match(t)))


def _n_depth(n: str | None) -> int:
    """head/@n 의 점 번호를 깊이로. '3.2.1' → 3. 번호가 아니면 0."""
    s = (n or "").strip().rstrip(".")
    if not s or not re.fullmatch(r"\d+(?:\.\d+)*", s):
        return 0
    return min(len(s.split(".")), 4)


def _div_head(div) -> tuple[str, str]:
    """<div> 의 제목과 @n. 제목은 Wiley '3.2 |' 잔재와 자간 아티팩트를 정리한다."""
    head_el = div.find("{*}head")
    if head_el is None:
        return "", ""
    title = norm_text("".join(head_el.itertext()))
    title = re.sub(r"^[\s|.)]+", "", title).strip()
    return clean_heading(title), (head_el.get("n") or "").strip()


def _iter_divs(parent, depth: int = 1):
    """<div> 를 문서 순서로 재귀 순회한다. (div, 중첩깊이) 를 낸다."""
    for child in parent:
        if _local(child.tag) != "div":
            continue
        yield child, depth
        yield from _iter_divs(child, depth + 1)


def _is_imrad(entries: list[dict], caps_contrast: bool) -> bool:
    """이 문서가 IMRaD 골격을 갖췄는가(최상위로 확인된 구조 타입 2종 이상).

    이 판정이 '신호 없는 제목을 하위절로 볼지'의 전제다. IMRaD 논문에서
    'Statistical analysis' 가 METHODS 의 하위절인 것은 거의 확실하지만,
    리뷰·프라이머처럼 IMRaD 가 아닌 글에서는 'Management'·'Diagnosis'·'Outlook'
    이 전부 **대등한 최상위 절**이다. 전제를 확인하지 않고 하위로 밀어 넣으면
    첫 제목이 문서 전체를 삼켜 classify_path 가 본문 전량을 그 타입으로
    오분류한다(실측: 65,591자짜리 리뷰 44개 절이 전부 'intro' 가 됐다).

    Methods/Results 중 하나는 반드시 있어야 한다 — Introduction·Conclusion 은
    서술형 리뷰에도 거의 항상 있어서 IMRaD 신호가 못 된다(실측: 그 둘만으로
    통과시키면 21개 절짜리 치료 리뷰가 통째로 Introduction 밑에 들어갔다).
    """
    types = set()
    for e in entries:
        t = e["title"]
        if not t:
            continue
        if _is_root_title(t) or (caps_contrast and _is_caps(t)):
            st = classify_section(t)
            if st in _STRUCTURAL:
                types.add(st)
    return len(types) >= 2 and bool(types & {"methods", "results"})


def _assign_paths(entries: list[dict]) -> list[list[str]]:
    """각 div 에 섹션 경로를 매긴다. 제목 없는 div 는 현재 경로를 이어받는다."""
    titled = [e["title"] for e in entries if e["title"]]
    ncaps = sum(1 for t in titled if _is_caps(t))
    # 대문자/혼합대소문자가 함께 있어야 조판 관례가 신호가 된다.
    caps_contrast = ncaps >= 2 and (len(titled) - ncaps) >= 1
    imrad = _is_imrad(entries, caps_contrast)

    paths: list[list[str]] = []
    stack: list[str] = []
    for e in entries:
        title = e["title"]
        if not title:
            paths.append(list(stack))       # 제목 없는 덩어리는 앞 섹션의 연속
            continue
        depth = e["native_depth"] if e["native_depth"] > 1 else 0
        if not depth:
            depth = _n_depth(e["n"])
        if not depth:
            if _is_root_title(title) or (caps_contrast and _is_caps(title)):
                depth = 1
            else:
                # 신호가 없는 제목은 **전제가 확인될 때만** 하위절로 본다:
                #   (a) 문서가 IMRaD 골격을 갖췄고,
                #   (b) 현재 루트가 하위절을 거느릴 수 있는 구조 절이다.
                # 부속(Disclosure·References…)이나 잡음 제목이 루트일 때 그 밑에
                # 붙이면 classify_path 가 루트를 보고 본문을 back/other 로 분류해
                # 청킹에서 통째로 빠진다(실측 2건·6,024자가 그랬다).
                nestable = (imrad and stack
                            and classify_section(stack[0]) in _STRUCTURAL)
                depth = 2 if nestable else 1
        depth = max(1, min(depth, len(stack) + 1, 4))
        stack = stack[:depth - 1] + [title]
        paths.append(list(stack))
    return paths


def _lead_title(entries: list[dict]) -> str:
    """제목 없는 선두 덩어리의 이름.

    첫 제목이 Methods 계열이면 그 앞 덩어리는 제목이 안 붙은 서론이다(실측:
    선두 무제목 div 57편 전부가 문서 첫 div, 그중 상당수가 'METHODS' 앞의 서론).
    그 밖에는 추정하지 않고 'Body' 를 유지한다.
    """
    for e in entries:
        if e["title"]:
            return "Introduction" if classify_section(e["title"]) == "methods" else "Body"
    return "Body"


# ── 캡션·그림 판정 ───────────────────────────────────────────────────
_FIG_LABEL_RE = re.compile(
    r"^\W*(?:fig(?:ure)?|scheme|chart|plate|photo|image|panel|graph)s?\.?\s*\d", re.I)
# 라벨 번호는 '3' · 'IV' · 보충자료 'E1'/'S2' 를 모두 받는다.
_LABEL_RE = re.compile(
    r"^\W*((?:fig(?:ure)?|scheme|chart|plate|photo|image|panel|graph|table|tab)s?"
    r"\.?\s*(?:[a-z]?\d{1,3}|[ivxlcdm]{1,6}))\b", re.I)


_TAB_NUM_RE = re.compile(
    r"\b(?:table|tab)s?\b\.?\s*(?:[a-z]?\d{1,3}|[ivxlcdm]{1,6})\b", re.I)
# 캡션 한복판에 다시 나타나는 라벨 = 다음 캡션이 흘러들어온 지점
_SECOND_LABEL_RE = re.compile(
    r"(?<=[\s.;:)])((?:Fig(?:ure)?|Table|TABLE|FIGURE)\.?\s*"
    r"(?:\d{1,3}|[IVX]{1,5})\s*[.:|]\s+[A-Z(])")


def _despaced(s: str) -> str:
    """자간 조판('F I G U R E 2')을 라벨 판정용으로 공백 없이 편다."""
    return re.sub(r"\s+", "", s or "")


def _squash(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def _label_of(s: str) -> str:
    """앞머리 라벨을 정규화해 돌려준다. 'Fig. 1 .' → 'fig1', 'F I G U R E 2' → 'figure2'.

    '[4]Figure 2' 처럼 인용번호가 앞에 붙어 나오는 경우가 있어 그것도 걷어낸다.
    """
    m = _LABEL_RE.match(re.sub(r"^\W*\[\d{1,3}\]", "", _despaced(s)))
    return _squash(m.group(1)) if m else ""


def _elem_text(el) -> str:
    return norm_text("".join(el.itertext())) if el is not None else ""


def _join_caption(head: str, desc: str) -> str:
    """<head>(라벨) + <figDesc>(본문) 를 합치되 라벨을 두 번 찍지 않는다.

    TEI 는 'Fig. 1 .'(head) 과 'Fig. 1. The flow diagram…'(figDesc) 를 함께
    내보낸다. 단순 연결하면 'Fig. 1. Fig. 1. …' 이 된다(정본 71편에서 발생).
    figDesc 가 같은 라벨로 시작하면 figDesc 만 쓰고, 아니면 head 를 살려
    표/그림 번호를 복원한다.
    """
    head = re.sub(r"\s+([.:,;])", r"\1", (head or "").strip())
    desc = (desc or "").strip()
    if not desc:
        return head
    if not head:
        return desc
    hl = _label_of(head)
    if hl and hl == _label_of(desc):
        return desc                      # 라벨이 이미 figDesc 안에 있다
    hs, ds = _squash(head), _squash(desc)
    if hs and ds.startswith(hs):
        return desc                      # head 가 figDesc 의 앞부분과 완전 중복
    sep = " " if head.endswith((".", ":", "|", "?", "!")) else ". "
    return head + sep + desc


def _trim_caption(cap: str, max_len: int = 2000) -> str:
    """캡션 경계 붕괴 수리 — 다음 캡션·본문까지 삼킨 figDesc 를 잘라낸다.

    GROBID 가 캡션 영역을 넓게 잡으면 한 figDesc 에 캡션 2개 또는 본문 문단이
    함께 들어온다. 라벨이 한복판에 다시 나타나는 지점을 경계로 본다.
    캡션이 라벨로 시작하지 않는데 뒤에서 라벨이 나오면 **뒤쪽이 진짜 캡션**이므로
    그쪽을 취한다(앞은 흘러들어온 본문이다).
    상한(2000자)은 실측 정상 캡션 최장 1,531자보다 넉넉히 잡아 정상 캡션을
    자르지 않는다.
    """
    s = (cap or "").strip()
    if not s:
        return ""
    m = _SECOND_LABEL_RE.search(s, 40)
    if m:
        if _label_of(s):
            s = s[:m.start()].strip()          # 라벨로 시작 → 두 번째 라벨 앞까지
        else:
            s = s[m.start():].strip()          # 앞부분은 본문 유입 → 뒤가 캡션
    if len(s) > max_len:
        cut = s.rfind(". ", 0, max_len)
        s = (s[:cut + 1] if cut > max_len // 2 else s[:max_len]).strip()
    return s


def _is_real_figure(fig, head: str, desc: str) -> bool:
    """진짜 그림인가.

    GROBID 는 약어상자·capsule summary·검색전략·각주 같은 고립 텍스트 박스를
    <figure> 로 분류한다(캐시 실측 271개 중 74개). 게이트는 일부러 느슨하게 둔다 —
    진짜 그림을 버리는 쪽이 잡음을 남기는 것보다 손해가 크다.
      · <graphic> 이 있으면 실제 이미지 영역이므로 무조건 통과
      · head 나 figDesc 가 'Fig. 3' 같은 그림 라벨로 시작하면 통과
    둘 다 없는 것만 버린다.
    """
    if fig.find("{*}graphic") is not None:
        return True
    # 자간 조판된 'F I G U R E 2' 도 그림 라벨이다 — 공백을 편 형태로도 본다.
    return any(_FIG_LABEL_RE.match(t)
               for t in (head, desc, _despaced(head), _despaced(desc)))


def parse_tei(tei_bytes: bytes, meta: dict, source_file: str = "") -> Document:
    root = etree.fromstring(tei_bytes)
    refs, rid_to_ref = _build_refs(root)

    figures: list[Figure] = []
    tables: list[Table] = []
    sections: list[Section] = []
    pcount = [0]

    body = root.find(".//{*}text/{*}body")
    if body is not None:
        # 1) div 를 재귀 순회해 제목/번호를 모으고, 2) 그로부터 경로를 매긴다.
        divs = list(_iter_divs(body))
        entries = []
        for div, native_depth in divs:
            title, n = _div_head(div)
            entries.append({"title": title, "n": n, "native_depth": native_depth})
        paths = _assign_paths(entries)
        lead = _lead_title(entries)

        for (div, _), path in zip(divs, paths):
            if not path:
                path = [lead]              # 제목 없는 선두 덩어리
            sec = Section(path=path, section_type=classify_path(path))
            for p in div.findall("{*}p"):
                info = _tei_paragraph(p)
                if not info["text"]:
                    continue
                pcount[0] += 1
                sec.paragraphs.append(Paragraph(
                    id=f"p{pcount[0]}", text=info["text"],
                    # 해소는 target 이 실제로 있는 것만. 표시번호(num:N)는
                    # cited_keys 에만 남긴다 — 추정으로 출처를 만들지 않는다.
                    cited_refs=[rid_to_ref.get(k, k) for k in info["cited_targets"]],
                    cited_keys=info["cited_keys"],
                    refs_figure=info["fig_ids"], refs_table=info["table_ids"]))
            if sec.paragraphs:
                sections.append(sec)

        # figure / table (GROBID 는 body 하위에 <figure> 로 둠)
        for fig in body.iter("{*}figure"):
            fid = fig.get(XMLID) or ""
            head = _elem_text(fig.find("{*}head"))
            desc = _elem_text(fig.find("{*}figDesc"))
            label = _elem_text(fig.find("{*}label"))
            if fig.get("type") == "table":
                markdown = _tei_table_markdown(fig)
                # 표 격자도 없고 라벨도 없으면 표가 아니라 GROBID 가 상자로 잡은
                # 본문 덩어리다(실측 231개 중 4개, 넷 다 본문에서 참조되지 않음).
                if not markdown and not (label or _label_of(head) or _label_of(desc)):
                    continue
                # head/label 을 버리면 'Table 3' 이라는 식별번호가 통째로 사라진다.
                cap = _trim_caption(_join_caption(head, desc))
                if label and not _TAB_NUM_RE.search(cap):
                    cap = f"Table {label}. {cap}".strip()
                tables.append(Table(
                    id=fid or f"tab{label or len(tables)+1}",
                    caption=clean_paragraph(cap), markdown=markdown))
            else:
                if not _is_real_figure(fig, head, desc):
                    continue           # 본문 덩어리를 그림으로 잡은 것 — 버린다
                cap = _trim_caption(_join_caption(head, desc))
                figures.append(Figure(id=fid or f"fig{label or len(figures)+1}",
                                      caption=clean_paragraph(cap)))

        # 버려진 <figure>/<table> 을 가리키는 문단 참조는 함께 지운다.
        # 남겨 두면 정본 안에 '존재하지 않는 그림 id' 를 가리키는 링크가 생겨
        # (실측 5건) 렌더러·청커가 깨진 참조를 그대로 물고 간다.
        fig_ids = {f.id for f in figures}
        tab_ids = {t.id for t in tables}
        for sec in sections:
            for p in sec.paragraphs:
                p.refs_figure = [x for x in p.refs_figure if x in fig_ids]
                p.refs_table = [x for x in p.refs_table if x in tab_ids]

    # 본문이 한 글자도 없으면 정본으로 내보내지 않는다. GROBID 가 teiHeader 만
    # 돌려주고 <text><body> 를 비워 보내는 일이 있는데(실측 134편 중 4편),
    # 그대로 통과시키면 본문 0자짜리 문서가 조용히 정본이 된다.
    # 예외를 던져 상위(run → pdf_fallback)가 다른 경로로 처리하게 한다.
    if not any(p.text for s in sections for p in s.paragraphs):
        raise ValueError(
            "TEI 본문이 비어 있음(GROBID 가 <text><body> 를 채우지 못함) — "
            "정본 생성 중단, PDF 폴백 필요")

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


def _drop_empty_stale(norm_dir: Path, doi: str) -> None:
    """앞선 실행이 남긴 **본문 0자짜리 grobid 정본**만 골라 치운다.

    pdf_fallback 은 'grobid 정본이 이미 있으면 건너뛴다'로 대상을 고르므로,
    이 껍데기를 그대로 두면 parse_tei 가 빈 본문을 거부해도 폴백이 넘겨받지
    못하고 0자 문서가 코퍼스에 남는다(실측 4편이 그 상태로 정본에 있었다).
    본문이 한 글자라도 있으면 절대 지우지 않는다 — 내용 삭제 위험을 없앤다.
    """
    dest = norm_dir / f"{utils.slug(doi)}.json"
    if not dest.exists():
        return
    try:
        d = utils.read_json(dest)
        if d.get("source") != "grobid":
            return
        if any(p.get("text") for s in d.get("sections", [])
               for p in s.get("paragraphs", [])):
            return
        dest.unlink()
        log(f"      · 본문 0자 정본 제거 → PDF 폴백으로 이관: {dest.name}")
    except Exception as e:  # noqa: BLE001 — 정리 실패가 파이프라인을 막지 않게
        log(f"      ! 빈 정본 정리 실패({dest.name}): {type(e).__name__}: {e}")


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
            _drop_empty_stale(norm_dir, doi)
    log(f"[2단계b] 완료 → {norm_dir} (성공 {len(docs)}, 실패 {failed})")
    return docs


if __name__ == "__main__":
    run()
