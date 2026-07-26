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

from . import metadata, utils
from .schema import (Document, Meta, Section, Paragraph, Figure, Table,
                     Reference, BACK_MATTER, classify_section, classify_path,
                     normalize_title)
from .textfix import (clean_heading, clean_paragraph, strip_running_header,
                      _dehyphenate)
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
# 양끝의 구분자(쉼표·세미콜론)까지 허용해야 한다. GROBID 는 '[2,3]' 을 <ref>[2,</ref>
# <ref>3]</ref> 두 개로 쪼개 내보내는 일이 잦은데, 앞쪽 '[2,' 이 이 패턴에 걸리지
# 않으면 원문 그대로 남아 '[2,[3]' 같은 깨진 마커가 된다(실측 103건/15편).
_CITE_NUM_RE = re.compile(r"^[\s\[\(,;]*([\d]{1,3}(?:\s*[,;\-–]\s*\d{1,3})*)[\s\]\),;]*$")
_PUNCT_ONLY_RE = re.compile(r"^[\s\[\]\(\),;.\-–]*$")


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
    if _PUNCT_ONLY_RE.match(t):
        return ""                                  # 쪼개진 마커의 구두점 조각(']' ',')
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


def _marker_numbers(root, keys: set[str]) -> dict[str, int]:
    """본문 인용 마커에서 '목록 항목 ↔ 지면 번호' 대응을 거둔다.

    GROBID 는 <ref type="bibr" target="#b14">15</ref> 처럼 **인쇄된 번호와 목록
    항목을 함께** 내보낸다. 이 대응이 지면 번호의 1차 근거다 — listBibl 의 나열
    순서보다 강하다.

    실측(TEI 캐시 134편, 마커 3,879개):
      · 나열 순서(index+1)와 이 대응이 어긋난 논문이 **22편**. 그중
        10.25259/ijdvl_558_2021 은 listBibl 첫 항목 b0 이 지면 **21번**이었다
        (PDF 13쪽 확인: 지면 1번은 Forman D 'Global burden of human papillomavirus
        and related diseases' = b20). 나열 순서를 믿었으면 72개 전부 틀렸다.
      · 이 대응은 130편 **전부 1:1**이었다 — 한 항목이 두 번호를 갖거나 한 번호에
        두 항목이 붙은 사례 0건. 그래서 다대일이 나오면 데이터가 이상한 것이므로
        그 항목을 버린다(추정으로 메우지 않는다).
    """
    votes: dict[str, dict[int, int]] = {}
    for ref in root.iter("{*}ref"):
        if ref.get("type") != "bibr":
            continue
        tgt = (ref.get("target") or "").lstrip("#")
        if tgt not in keys:
            continue
        nums = _display_nums(ref.text)
        if len(nums) != 1:                 # '[12-14]' 처럼 여러 개를 가리키는 마커는
            continue                       # 어느 항목이 몇 번인지 말해주지 않는다
        n = int(nums[0])
        if 0 < n <= 999:
            votes.setdefault(tgt, {}).setdefault(n, 0)
            votes[tgt][n] += 1

    best = {k: max(v.items(), key=lambda kv: (kv[1], -kv[0]))[0] for k, v in votes.items()}
    # 한 번호를 두 항목이 주장하면 둘 다 버린다(어느 쪽이 맞는지 근거가 없다).
    owners: dict[int, list[str]] = {}
    for k, n in best.items():
        owners.setdefault(n, []).append(k)
    return {k: n for k, n in best.items() if len(owners[n]) == 1}


def _fill_numbers(keys: list[str], anchors: dict[str, int]) -> dict[str, int]:
    """마커로 확정한 번호(anchors) 사이의 빈칸을 **셀 수 있을 때만** 메운다.

    한 번도 인용되지 않은 항목에는 마커가 없다(실측: 3,375개 중 993개). 그래도
    앞뒤 항목의 번호가 확정돼 있고 **자리 수와 번호 수가 정확히 맞으면** 그 사이는
    한 가지로만 셀 수 있다(예: 54번·56번 사이의 한 자리는 55번). 수가 맞지 않으면
    GROBID 가 항목을 합치거나 흘린 구간이므로 **비워 둔다** — 번호 없는 항목은
    본문에서 링크되지 않을 뿐이지만, 틀린 번호는 [15] 를 엉뚱한 논문으로 보낸다.
    """
    if not anchors:
        return {}
    idx = {k: i for i, k in enumerate(keys)}
    known = sorted(((idx[k], n) for k, n in anchors.items() if k in idx))
    out = dict(anchors)
    used = set(anchors.values())

    def claim(pos: int, num: int) -> None:
        if num >= 1 and num not in used:
            out[keys[pos]] = num
            used.add(num)

    # 사이 구간 — 자리 수 == 번호 수일 때만
    for (i, ni), (j, nj) in zip(known, known[1:]):
        if j - i > 1 and nj - ni == j - i:
            for step in range(1, j - i):
                claim(i + step, ni + step)
    # 앞뒤 꼬리 — 가장 가까운 확정점의 간격을 그대로 이어 본다
    i0, n0 = known[0]
    for step in range(1, i0 + 1):
        claim(i0 - step, n0 - step)
    i1, n1 = known[-1]
    for step in range(1, len(keys) - i1):
        claim(i1 + step, n1 + step)
    return out


def _build_refs(root) -> tuple[list[Reference], dict[str, int]]:
    """참고문헌을 **지면 순서·지면 번호**로 만든다.

    반환: (번호순 Reference 목록, 로컬키 → 지면번호). 두 번째 값으로 본문
    cited_refs 를 번호에 다시 잇는다.
    """
    back = root.find(".//{*}back")
    if back is None:
        return [], {}

    parsed: list[Reference] = []
    order: list[str] = []
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
        journal = ""
        j_el = bs.find(".//{*}title[@level='j']")
        if j_el is not None:
            journal = norm_text("".join(j_el.itertext()))
        authors = []
        for pers in bs.iter("{*}author"):
            sur = pers.find(".//{*}surname")
            fore = pers.find(".//{*}forename")
            nm = " ".join(x for x in ((sur.text if sur is not None else ""),
                                      (fore.text if fore is not None else "")) if x)
            if nm.strip():
                authors.append(norm_text(nm))
        # 지면 원문. <note type="raw_reference"> 가 있으면 그걸 쓴다 — itertext 는
        # 구조 요소를 붙여 이어 'HChoi HRKim CHNa' 처럼 읽을 수 없는 문자열이 된다
        # (includeRawCitations=1 로 켜 두고 있고, 캐시 134편에서 거의 전부 존재).
        raw_el = bs.find(".//{*}note[@type='raw_reference']")
        raw = norm_text("".join(raw_el.itertext())) if raw_el is not None else ""
        raw = _dehyphenate(raw or norm_text("".join(bs.itertext())))[:400]
        order.append(key)
        parsed.append(Reference(key=key, doi=doi, pmid=pmid, title=title,
                                year=year, journal=journal, authors=authors,
                                raw=raw, source="parsed"))

    numbers = _fill_numbers(order, _marker_numbers(root, set(order)))
    if not numbers:
        # 번호 인용이 아예 없는 논문(저자-연도식). listBibl 나열 순서가 곧 지면
        # 순서이므로 자리번호를 준다 — 화면에 목록을 번호와 함께 보여주기 위해서다.
        numbers = {k: i + 1 for i, k in enumerate(order)}
    for r in parsed:
        r.number = numbers.get(r.key)

    # 번호가 있는 것은 번호순, 없는 것은 나열 순서 그대로 뒤에 붙인다.
    # (없는 것을 중간에 끼워 넣으면 '몇 번쯤' 이라는 없는 주장을 하게 된다)
    numbered = sorted((r for r in parsed if r.number), key=lambda r: r.number)
    rest = [r for r in parsed if not r.number]
    return numbered + rest, numbers


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


# ── GROBID 가 <figure> 상자에 가둔 본문 되살리기 ─────────────────────
#
# GROBID 는 박스·컬럼 조판을 만나면 본문 문단을 <figure> 로 잘못 담는다.
# 그 상자는 _is_real_figure/표 게이트에서 **통째로 버려졌다** — 캐시 130편
# 실측 76개 상자·38,780자가 그렇게 사라졌고, 그 안에는
#   · 10.1002/jso.23438  PubMed 검색어 (3)~(14) 12개 항목
#   · 10.1002/jso.23618  'APPENDIX. PubMed Search Strategy' 검색식 5~25번
#   · 10.1111/pcmr.12699 Results 4,242자(가장 큰 한 덩어리)
# 가 들어 있다. TEI 에는 살아 있으니 GROBID 가 아니라 **우리 파서의 손실**이다.
#
# 되살릴지는 규칙이 아니라 **원문 PDF 의 본문 흐름에 그 글이 실제로 있는가**
# 로 판단한다. recover.extract_pdf_text() 는 러닝헤더·저작권·소속·캡션·
# 키워드/약어 상자를 이미 걷어낸 본문 스트림을 만든다. 거기서 유일하게
# 찾히면 본문이고, 안 찾히면 조판 부속이라 그대로 버린다(지금 동작 유지).
# 넣을 위치도 같은 자리에서 얻는다 — PDF 에서 그 지점 **직전에 끝나는 문단**
# 뒤에 넣는다.
#
# 절대 기존 문단에 **이어붙이지 않는다**. 언제나 별도 문단으로만 넣는다.
# 이어붙이면 문장이 엉뚱하게 연결돼 비문이 되고(recover 병합 실측 33건 중
# 14건이 그랬다), 게다가 대문자로 시작하게 되어 탐지기에 다시 안 잡힌다 —
# 결함이 조용히 숨는다. 별도 문단이면 최악의 경우도 '위치가 어색한 문단'
# 이지 문장 파손이 아니다.

# 상자 안에서 본문과 부속을 가르는 경계.
#   · 자간 조판된 캡션 라벨('F I G U R E 2' · 'TA B L E 1')과 전부 대문자 라벨.
#     본문 참조('as shown in Table 3')는 혼합 대소문자라 걸리지 않는다.
#   · CAPSULE SUMMARY·ABBREVIATIONS USED 같은 박스 머리(번호가 없다).
# 번호 뒤에 \b 를 쓰면 안 된다 — 캡션 본문이 번호에 붙어 나오는 일이 흔해서
# ('F I G U R E 1Representative cases…') 경계가 성립하지 않아 통째로 놓친다.
_BOX_CAPTION_CUT = re.compile(
    r"(?:(?:F\s*I\s*G\s*U\s*R\s*E|T\s*A\s*B\s*L\s*E|F\s*I\s*G|S\s*C\s*H\s*E\s*M\s*E)"
    r"\s*\.?\s*(?:\d{1,3}|[IVXLC]{1,5})(?![0-9])"
    r"|C\s*A\s*P\s*S\s*U\s*L\s*E\s+S\s*U\s*M\s*M\s*A\s*R\s*Y"
    r"|A\s*B\s*B\s*R\s*E\s*V\s*I\s*A\s*T\s*I\s*O\s*N\s*S?\s+U\s*S\s*E\s*D)")

# 저자 명단('… Bae, MD, PhD, a Jung Eun Kim, MD, PhD, b …')은 본문이 아니다.
# 러닝헤더가 절 제목으로 승격된 문서에서는 이 명단이 본문 스트림에까지
# 남아 위치 확인만으로는 걸러지지 않는다(실측 10.1016/j.jaad.2020.09.088).
# 학위 표기가 3개 이상 나오는 본문 문단은 사실상 없다.
_DEGREE_RE = re.compile(r"\b(?:MD|PhD|MSc|MPH|MBBS|BSc|DO|RN|DrPH)\b")


def _is_byline(seg: str) -> bool:
    return len(_DEGREE_RE.findall(seg or "")) >= 3


def _box_segments(text: str) -> list[str]:
    """텍스트 상자를 캡션 라벨 경계로 토막낸다.

    한 상자에 본문과 캡션이 함께 담기는 일이 잦다(예: 10.1111/jocd.12551
    fig_0 은 Methods 문단 뒤에 'F I G U R E 1 Retinoid metabolism…' 캡션이
    이어 붙어 있다). 토막마다 따로 PDF 본문 스트림에서 찾으므로, 본문 토막만
    살아남고 캡션 토막은 스트림에 없어 자동으로 걸러진다.

    경계를 잘못 잡아도 손해가 없다 — 두 토막이 각각 제자리에서 찾히면 둘 다
    들어가고, 문단 경계 하나가 더 생길 뿐 글자가 사라지거나 섞이지 않는다.
    """
    cuts = [m.start() for m in _BOX_CAPTION_CUT.finditer(text or "")]
    if not cuts:
        return [text] if text else []
    bounds = [0] + [c for c in cuts if c > 0] + [len(text)]
    return [text[a:b].strip() for a, b in zip(bounds, bounds[1:])
            if text[a:b].strip()]


def _place_boxes(body_text: list[Section], boxes: list[str],
                 pdf_file: str, pcount: list[int]) -> int:
    """버려질 뻔한 텍스트 상자를 PDF 본문 흐름에서 확인하고 제자리에 넣는다.

    돌려주는 값은 실제로 되살린 문단 수. PDF 를 못 읽거나 한 토막도 못 찾으면
    0 이고, 그때 동작은 기존과 완전히 같다(전부 버림).
    """
    if not boxes or not body_text or not pdf_file:
        return 0
    try:
        from .recover import (extract_pdf_text, _letters, _letters_map, _locate)
        stream = extract_pdf_text(pdf_file)
    except Exception as e:                    # noqa: BLE001 — 상자 복원 실패가
        log(f"      · 텍스트 상자 복원 건너뜀(PDF 읽기 실패): "  # 파싱을 막지 않게
            f"{type(e).__name__}: {e}")
        return 0
    if len(stream) < 400:
        return 0
    letters, _ = _letters_map(stream)

    # 이미 정본에 있는 글자(중복 삽입 방지) + 기존 문단의 PDF 위치(삽입 지점)
    doc_letters = _letters(" ".join(p.text for s in body_text
                                    for p in s.paragraphs))
    def _at(text: str) -> int:
        """문단이 PDF 스트림에서 시작하는 위치. 모호하면 -1.

        _locate 는 40자 미만을 거부한다(모호해서). 그런데 '(1) Advanced stomach
        neoplasm.' 같은 **짧은 목록 문단**이 기준점이 되어야 상자를 그 뒤에
        넣을 수 있다. 그래서 짧은 것은 유일성을 직접 확인해 받아들인다.
        """
        L = _letters(text)
        at = _locate(letters, L)
        if at >= 0 or len(L) < 18:
            return at
        first = letters.find(L)
        if first < 0 or letters.find(L, first + 1) >= 0:
            return -1                         # 없거나 여러 곳 → 기준점으로 못 쓴다
        return first

    anchors: list[tuple[int, int, int]] = []
    for si, sec in enumerate(body_text):
        for pi, p in enumerate(sec.paragraphs):
            at = _at(p.text)
            if at >= 0:
                anchors.append((at, si, pi))
    if not anchors:
        return 0

    # (섹션, 문단뒤) → 넣을 문단들. 인덱스가 밀리지 않게 뒤에서부터 반영한다.
    pending: list[tuple[int, int, str]] = []
    for raw in boxes:
        for seg in _box_segments(raw):
            L = _letters(seg)
            if len(L) < 60:
                continue                      # 너무 짧으면 위치가 모호하다
            if _is_byline(seg):
                continue                      # 저자 명단 — 본문이 아니다
            if L[:100] in doc_letters:
                continue                      # 이미 본문에 있다
            at = _locate(letters, L)
            if at < 0:
                continue                      # PDF 본문 흐름에 없다 → 조판 부속
            prev = [a for a in anchors if a[0] < at]
            if not prev:
                continue                      # 앞에 기준 문단이 없다 → 넣지 않는다
            _, si, pi = max(prev, key=lambda a: a[0])
            pending.append((si, pi, seg))
            doc_letters += L

    for si, pi, seg in sorted(pending, key=lambda x: (x[0], x[1]), reverse=True):
        pcount[0] += 1
        para = Paragraph(id=f"p{pcount[0]}", text=clean_paragraph(norm_text(seg)))
        body_text[si].paragraphs.insert(pi + 1, para)
    return len(pending)


# ── GROBID 가 지워 버린 '진짜 하이픈' 되살리기 ───────────────────────
#
# GROBID 는 줄 끝에서 끊긴 하이픈을 **무조건 지우고** 붙인다. 조판 하이픈
# ('popu-\nlation' → 'population')은 그게 맞지만, 원래 하이픈이 있는 합성어까지
# 붙여 버린다 — 10.1002/jso.23438 PDF 의 'overall survival (OS) or disease-\nfree
# survival (DFS).' 이 TEI·정본에서 'diseasefree survival' 이 됐다. 같은 PDF
# 초록에는 줄바꿈 없이 'disease-free' 가 인쇄돼 있다.
#
# 그래서 규칙이 아니라 **그 PDF 자신의 조판**을 증거로 쓴다.
#   · real   — 줄바꿈이 아닌 자리에 'a-b' 로 인쇄된 쌍. 진짜 하이픈이다.
#   · solid  — 하이픈 없이 통짜로 인쇄된 낱말.
# 붙어 있는 낱말 w 가 solid 에 있으면 손대지 않는다. 없을 때만 쪼개서
# (left,right) 가 real 에 있으면 하이픈을 되살린다.
# solid 검사가 오탐 방지의 핵심이다 — 'consensus' 는 통짜로 인쇄돼 있으므로
# 'con-sensus' 로 되돌아가지 않는다. 조판 하이픈을 살려 'popu-lation' 을
# 본문에 남기는 것이 원래 결함보다 나쁘다.
_PDF_HYPHEN = "[-‐‑]"
_HYPH_PAIR_RE = re.compile(rf"([A-Za-z]{{2,}}){_PDF_HYPHEN}([A-Za-z]{{2,}})")
_SOLID_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_JOINED_RE = re.compile(r"[A-Za-z]{6,}")


def _hyphen_evidence(pdf_file: str) -> tuple[set[tuple[str, str]], set[str]]:
    """PDF 원문에서 (진짜 하이픈 쌍, 통짜 낱말) 을 모은다."""
    import fitz
    real: set[tuple[str, str]] = set()
    solid: set[str] = set()
    with fitz.open(pdf_file) as doc:
        for page in doc:
            for line in page.get_text("text").splitlines():
                # 줄 안에서만 본다 — 줄 끝 하이픈은 조판 분철이라 증거가 아니다.
                for m in _HYPH_PAIR_RE.finditer(line):
                    real.add((m.group(1).lower(), m.group(2).lower()))
                for w in _SOLID_WORD_RE.findall(_HYPH_PAIR_RE.sub(" ", line)):
                    solid.add(w.lower())
    return real, solid


def _restore_hyphens(text: str, real: set[tuple[str, str]],
                     solid: set[str]) -> str:
    """붙어 버린 합성어에 하이픈을 되살린다(증거가 있을 때만)."""
    def repl(m: re.Match) -> str:
        w = m.group(0)
        lw = w.lower()
        if lw in solid:
            return w                          # 통짜로 인쇄된 낱말 — 손대지 않는다
        for k in range(2, len(w) - 1):
            if (lw[:k], lw[k:]) in real:
                return w[:k] + "-" + w[k:]
        return w
    return _JOINED_RE.sub(repl, text)


def _cited_numbers(cited_keys, key_num: dict[str, int]) -> list[str]:
    """문단의 인용을 **지면 번호**로 바꾼다.

    두 경로를 다 쓴다. (a) GROBID 가 target 을 붙인 인용은 로컬키로 번호를 찾고,
    (b) target 을 못 붙인 인용('num:15')은 인쇄된 번호가 마커에 그대로 있으므로
    그걸 쓴다 — 실측 4,350개 중 250개가 (b)라서, 버리면 인용이 통째로 증발한다.
    번호를 못 찾으면 **넣지 않는다**.
    """
    out: list[str] = []
    for k in cited_keys or []:
        n = k[4:] if str(k).startswith("num:") else key_num.get(k)
        if n and str(n) not in out:
            out.append(str(n))
    return out


def parse_tei(tei_bytes: bytes, meta: dict, source_file: str = "") -> Document:
    root = etree.fromstring(tei_bytes)
    refs, key_num = _build_refs(root)

    figures: list[Figure] = []
    tables: list[Table] = []
    body_text: list[Section] = []
    orphan_boxes: list[str] = []      # GROBID 가 <figure> 로 잘못 담은 본문 후보
    pcount = [0]

    body = root.find(".//{*}text/{*}body")
    if body is not None:
        # 1) div 를 재귀 순회해 제목/번호를 모으고, 2) 그로부터 경로를 매긴다.
        divs = list(_iter_divs(body))
        journal = (meta.get("journal") or "").strip()
        entries = []
        for div, native_depth in divs:
            title, n = _div_head(div)
            # 러닝헤더가 절 제목으로 승격되는 일이 있다(실측 10.1002/jso.23438 의
            # ['DISCUSSION','Journal of Surgical Oncology']). 저널명과 같은 제목은
            # 절 제목이 아니라 페이지 머리말이므로 제목 없는 div 로 되돌린다 —
            # 그러면 앞 섹션의 연속으로 이어붙어 본문 흐름이 복구된다.
            if journal and len(journal) >= 12 and \
                    normalize_title(title).lower() == normalize_title(journal).lower():
                title = ""
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
                    # 본문 [15] ↔ references[].number == 15 로 잇는다.
                    cited_refs=_cited_numbers(info["cited_keys"], key_num),
                    cited_keys=info["cited_keys"],
                    refs_figure=info["fig_ids"], refs_table=info["table_ids"]))
            if sec.paragraphs:
                body_text.append(sec)

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
                    # 캡션이 아니라 본문 후보다 — _join_caption 의 '. ' 이음쇠를
                    # 쓰면 'Female 1' + 'adults who…' 가 'Female 1. adults who…'
                    # 처럼 없던 마침표를 만든다. 원문 그대로 공백으로만 잇는다.
                    orphan_boxes.append(f"{head} {desc}".strip())
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
                    # 본문 덩어리를 그림으로 잡은 것. 버리지 말고 PDF 본문
                    # 흐름에서 확인해 제자리에 되살린다(_place_boxes).
                    # 캡션이 아니라 본문 후보다 — _join_caption 의 '. ' 이음쇠를
                    # 쓰면 'Female 1' + 'adults who…' 가 'Female 1. adults who…'
                    # 처럼 없던 마침표를 만든다. 원문 그대로 공백으로만 잇는다.
                    orphan_boxes.append(f"{head} {desc}".strip())
                    continue
                cap = _trim_caption(_join_caption(head, desc))
                figures.append(Figure(id=fid or f"fig{label or len(figures)+1}",
                                      caption=clean_paragraph(cap)))

        # 그림/표가 아닌 텍스트 상자는 PDF 본문 흐름에 있는 것만 되살린다.
        if orphan_boxes:
            n = _place_boxes(body_text, orphan_boxes, source_file, pcount)
            if n:
                log(f"      · 텍스트 상자에서 본문 {n}문단 되살림")

        # 문장 한복판에 박힌 러닝헤더(저널명)를 걷어낸다.
        if journal:
            nr = 0
            for sec in body_text:
                for p in sec.paragraphs:
                    fixed = strip_running_header(p.text, journal)
                    if fixed != p.text:
                        nr += 1
                        p.text = fixed
            if nr:
                log(f"      · 러닝헤더 제거: {nr}문단")

        # GROBID 가 지운 진짜 하이픈을 그 PDF 자신의 조판을 증거로 되살린다.
        if source_file and Path(source_file).exists():
            try:
                real, solid = _hyphen_evidence(source_file)
            except Exception as e:            # noqa: BLE001 — 하이픈 복원 실패가
                log(f"      · 하이픈 복원 건너뜀: {type(e).__name__}: {e}")  # 파싱을 막지 않게
            else:
                nh = 0
                for sec in body_text:
                    for p in sec.paragraphs:
                        fixed = _restore_hyphens(p.text, real, solid)
                        if fixed != p.text:
                            nh += 1
                            p.text = fixed
                if nh:
                    log(f"      · 하이픈 복원: {nh}문단")

        # 버려진 <figure>/<table> 을 가리키는 문단 참조는 함께 지운다.
        # 남겨 두면 정본 안에 '존재하지 않는 그림 id' 를 가리키는 링크가 생겨
        # (실측 5건) 렌더러·청커가 깨진 참조를 그대로 물고 간다.
        fig_ids = {f.id for f in figures}
        tab_ids = {t.id for t in tables}
        for sec in body_text:
            for p in sec.paragraphs:
                p.refs_figure = [x for x in p.refs_figure if x in fig_ids]
                p.refs_table = [x for x in p.refs_table if x in tab_ids]

    # 본문이 한 글자도 없으면 정본으로 내보내지 않는다. GROBID 가 teiHeader 만
    # 돌려주고 <text><body> 를 비워 보내는 일이 있는데(실측 134편 중 4편),
    # 그대로 통과시키면 본문 0자짜리 문서가 조용히 정본이 된다.
    # 예외를 던져 상위(run → pdf_fallback)가 다른 경로로 처리하게 한다.
    if not any(p.text for s in body_text for p in s.paragraphs):
        raise ValueError(
            "TEI 본문이 비어 있음(GROBID 가 <text><body> 를 채우지 못함) — "
            "정본 생성 중단, PDF 폴백 필요")

    m = Meta(
        doi=meta.get("doi"), pmid=meta.get("pmid"), pmcid=meta.get("pmcid"),
        title=meta.get("title", ""), authors=meta.get("authors", []),
        journal=meta.get("journal", ""), year=meta.get("year"),
        mesh=meta.get("mesh", []), keywords=meta.get("keywords", []),
        pub_types=meta.get("pub_types", []),
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

    # 뽑은 초록을 무조건 믿지 않는다 — 합본 지면에서는 옆 논문의 초록이나
    # 이 논문의 서론 첫 문단이 그 자리에 들어온다. 문법적으로 멀쩡해서
    # 길이·null 검사를 전부 통과하므로, 증인(API 정본 / PDF 의 Abstract 표제
    # 뒤 텍스트)과 대조해 고른다. pmc_xml 과 같은 판정을 쓴다.
    _first = next((p.text for s in body_text for p in s.paragraphs if p.text), "")
    abstract, abstract_source, _abs_info = metadata.choose_abstract(
        extracted_abstract, meta, source_file or None,
        body_first=_first, title=meta.get("title") or "")

    doc = Document(
        paper_id=meta.get("doi") or meta.get("pmid") or "unknown",
        source="grobid", source_file=source_file, meta=m,
        abstract=abstract, abstract_source=abstract_source,
        body_text=body_text, figures=figures, tables=tables,
        references=refs,
    )
    if _abs_info:
        doc.qc["abstract"] = _abs_info
    # 옆 논문 글 걷어내기 — research letter 는 한 지면에 여러 편이 이어 실린다.
    # 경계를 확신할 수 없으면 boundary 가 **아무것도 건드리지 않는다**(오탐 0 실측).
    if source_file and Path(source_file).exists():
        try:
            from . import boundary
            rep = boundary.apply_to_parsed(doc, source_file, meta)
            if rep.get("confident") or rep.get("identity_conflict"):
                doc.qc["boundary"] = rep
        except Exception as e:  # noqa: BLE001 — 안전망이 파이프라인을 막지 않게
            log(f"      ! 경계 판정 생략({doc.paper_id}): {type(e).__name__}: {e}")
        # 캡션 수리 — 빈 캡션 채우기·본문 삼킴 잘라내기·본문 누수 제거.
        # boundary **다음에** 부른다. 경계가 확정된 뒤라야 이웃 편 캡션을 안 물어온다
        # (A/B 실측: 경계 없이 하면 이웃 캡션이 1건 새로 들어온다).
        try:
            from . import captions
            crep = captions.apply_to_parsed(doc, source_file)
            if any(crep.get(k) for k in ("filled", "added", "trimmed", "deduped",
                                         "body_stripped")) or crep.get("rejected_neighbour"):
                doc.qc["captions"] = crep
        except Exception as e:  # noqa: BLE001
            log(f"      ! 캡션 수리 생략({doc.paper_id}): {type(e).__name__}: {e}")
    return doc


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
        if any(p.get("text") for s in d.get("body_text", [])
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
            npar = sum(len(s.paragraphs) for s in doc.body_text)
            ncite = sum(len(p.cited_refs) for s in doc.body_text for p in s.paragraphs)
            log(f"  [{i}/{len(targets)}] {doi}: 섹션 {len(doc.body_text)} · 문단 {npar} · "
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
