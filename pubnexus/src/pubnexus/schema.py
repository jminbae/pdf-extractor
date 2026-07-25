"""정본(canonical) 문서 스키마.

JSON을 정본으로 두고 Markdown은 여기서 렌더링한다(설계서 5단계).
Markdown -> 구조 복원은 불가능하므로 이 스키마가 단일 진실이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any


SCHEMA_VERSION = "1.0"


@dataclass
class Paragraph:
    id: str
    text: str
    cited_refs: list[str] = field(default_factory=list)   # 해소된 DOI 또는 로컬 ref key
    cited_keys: list[str] = field(default_factory=list)    # GROBID 로컬 키(#b14 등) 원본
    refs_figure: list[str] = field(default_factory=list)
    refs_table: list[str] = field(default_factory=list)


@dataclass
class Section:
    path: list[str]                    # ["Results", "Repigmentation rate"]
    # abstract|intro|methods|results|discussion|back|other
    #   back = 감사의 글·이해충돌·연구비 등 후행 부속(청킹 제외 대상)
    section_type: str = "other"
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class Figure:
    id: str
    caption: str = ""
    image: str | None = None           # 저장 경로(옵션)


@dataclass
class Table:
    id: str
    caption: str = ""
    markdown: str = ""                 # 표 본문(가능한 경우)


@dataclass
class Reference:
    key: str                           # 로컬 키 (b14 등)
    doi: str | None = None
    pmid: str | None = None
    title: str = ""
    year: int | None = None
    raw: str = ""                      # 원문 문자열


@dataclass
class Meta:
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    title: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    year: int | None = None
    mesh: list[str] = field(default_factory=list)
    pub_types: list[str] = field(default_factory=list)
    rcr: float | None = None           # iCite Relative Citation Ratio
    citation_count: int | None = None
    is_open_access: bool = False


@dataclass
class Document:
    paper_id: str                      # DOI 우선, 없으면 PMID/파일해시
    source: str                        # "pmc_xml" | "grobid"
    schema_version: str = SCHEMA_VERSION
    quality_score: float | None = None
    source_file: str = ""
    meta: Meta = field(default_factory=Meta)
    abstract: str = ""
    abstract_source: str = "none"      # extracted | api | none — QC 순환검증 방지
    sections: list[Section] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    qc: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 섹션 제목 → 표준 타입 매핑 (검색 필터용) ────────────────────────────
#
# 파일럿 167편 실측: 소제목까지 감안하지 않으면 섹션의 80.2%가 'other' 로 떨어져
# section_type 필터가 사실상 무용지물이 된다. 실제 코퍼스에 나타난 소제목을
# 넣어 확장한 결과 43.4% 까지 내려갔다(구제 467개 섹션).
SECTION_TYPE_MAP = {
    "abstract": "abstract", "summary of the article": "abstract",

    # 'Primer' 는 서론이 아니라 Nature Reviews 의 **논문 종류** 이름이다.
    #   매핑에 넣었더니 10.1038/s41572-025-00670-x 의 43개 절 중 26개가
    #   intro 로 오분류됐다(Management·Diagnosis·Quality of life 까지 전부).
    "background": "intro", "introduction": "intro",
    "epidemiology": "intro", "rationale": "intro", "objective": "intro",
    "objectives": "intro", "aim": "intro", "aims": "intro", "purpose": "intro",

    "method": "methods", "methods": "methods", "materials": "methods",
    "material and methods": "methods", "materials and methods": "methods",
    "patients and methods": "methods", "subjects and methods": "methods",
    "study design": "methods", "study population": "methods",
    "study sample": "methods", "study subjects": "methods",
    "study selection": "methods", "subjects": "methods", "patients": "methods",
    "participants": "methods", "animals": "methods",
    "statistical analysis": "methods", "statistical analyses": "methods",
    "statistical methods": "methods", "statistics": "methods",
    "data source": "methods", "data sources": "methods",
    "data collection": "methods", "data extraction": "methods",
    "data analysis": "methods", "data availability": "methods",
    "search strategy": "methods", "search methods": "methods",
    "literature search": "methods", "literature review": "methods",
    "eligibility criteria": "methods", "inclusion criteria": "methods",
    "exclusion criteria": "methods", "covariates": "methods",
    "confounding variables": "methods", "outcome of interest": "methods",
    "outcome measures": "methods", "measures": "methods",
    "assessment": "methods", "clinical assessment": "methods",
    "quality assessment": "methods", "risk of bias": "methods",
    "ethics": "methods", "ethics statement": "methods",
    "ethical approval": "methods", "irb approval status": "methods",
    "sample size": "methods", "randomization": "methods",
    "intervention": "methods", "interventions": "methods",
    "procedure": "methods", "procedures": "methods", "protocol": "methods",
    "treatment protocol": "methods", "definitions": "methods",
    "consensus process": "methods",

    "result": "results", "results": "results", "findings": "results",
    "outcomes": "results", "adverse events": "results", "safety": "results",
    "efficacy": "results", "treatment response": "results",
    "subgroup analysis": "results", "subgroup analyses": "results",
    "sensitivity analysis": "results", "sensitivity analyses": "results",
    "search results": "results", "study characteristics": "results",
    "baseline characteristics": "results", "patient characteristics": "results",
    "characteristics of the study population": "results",
    "description of included studies": "results", "included studies": "results",
    "data synthesis": "results", "effect of interventions": "results",
    "case": "results", "case report": "results", "case presentation": "results",
    "case description": "results", "case series": "results",
    "case summary": "results", "clinical course": "results",
    "clinical findings": "results", "clinical studies": "results",
    "histopathology": "results", "pathology": "results",
    "animal study": "results", "animal studies": "results",

    "discussion": "discussion", "conclusion": "discussion",
    "conclusions": "discussion", "limitations": "discussion",
    "strengths and limitations": "discussion", "comment": "discussion",
    "clinical implications": "discussion", "implications": "discussion",
    "future directions": "discussion", "interpretation": "discussion",
    "significance": "discussion", "mechanism of action": "discussion",
    "pathogenesis": "discussion", "concluding remarks": "discussion",
    "outlook": "discussion", "management": "discussion",
}

# 본문이 아닌 후행 부속. 검색 대상에서 빼야 잡음이 줄어든다.
BACK_MATTER = frozenset({
    "acknowledgment", "acknowledgments", "acknowledgement", "acknowledgements",
    "conflict of interest", "conflicts of interest", "competing interests",
    "disclosure", "disclosures", "funding", "funding sources",
    "financial support", "author contributions", "authorship", "contributors",
    "data availability statement", "supporting information",
    "supplementary material", "supplementary materials", "abbreviations",
    "orcid", "references", "bibliography", "appendix", "associated data",
    "ethics approval and consent to participate",
})

# 머리말/꼬리말 잔재가 섹션 제목으로 승격된 쓰레기 (검색 품질을 갉아먹는다)
_JUNK_RE = re.compile(
    r"^(?:0123456789|capsule summary|j am acad dermatol|downloaded (?:from|for)|"
    r"see related|this article is protected|copyright|licen[sc]e)", re.I)

# "3.", "3.1.", "IV.", "a)" 같은 번호 접두
_NUM_PREFIX_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+|[a-z])[.)]\s+", re.I)

_ALL_CAPS_WORDS = (
    "MATERIALS", "METHODS", "INTRODUCTION", "RESULTS", "DISCUSSION",
    "CONCLUSION", "CONCLUSIONS", "BACKGROUND", "ACKNOWLEDGEMENTS",
    "ACKNOWLEDGEMENT", "ACKNOWLEDGMENTS", "ACKNOWLEDGMENT", "CONFLICT",
    "INTEREST", "AUTHORSHIP", "REFERENCES", "ABSTRACT", "FUNDING",
    "DISCLOSURE", "PATIENTS", "CASE", "REPORT", "SUMMARY", "OBJECTIVE",
    "OBJECTIVES", "LIMITATIONS", "FIGURE", "TABLE", "AND", "OF", "THE",
)


def despace_heading(title: str) -> str:
    """'M ATER I A L S A N D M ETHODS' → 'MATERIALS AND METHODS'.

    PDF 자간 조판 때문에 글자 사이에 공백이 끼는 제목을 복원한다.
    오탐 방지 장치는 **완전 분해**다 — 공백을 모두 없앤 문자열이 알려진 단어들로
    빈틈없이 쪼개질 때만 채택하고, 한 글자라도 남으면 원본을 그대로 돌려준다.
    'VITILIGO TREATMENT' 처럼 사전에 없는 단어가 섞이면 손대지 않으므로 안전하다.
    소문자가 섞인 제목('Materials and Methods')은 애초에 대상이 아니다.

    'DISCUS SION'(두 토막)처럼 짧은 사례도 잡아야 하므로 토막 수·길이 비율로는
    거르지 않는다. 실측: 정상 제목 13종 오탐 0, 아티팩트 제목 7종 전부 복원.
    """
    s = re.sub(r"\s+", " ", title or "").strip()
    if not s or any(c.islower() for c in s):
        return s
    toks = s.split(" ")
    if len(toks) < 2:
        return s
    joined = "".join(toks)
    out, i = [], 0
    while i < len(joined):
        for w in sorted(_ALL_CAPS_WORDS, key=len, reverse=True):
            if joined.startswith(w, i):
                out.append(w)
                i += len(w)
                break
        else:
            return s          # 완전 분해 실패 → 손대지 않는다
    return " ".join(out)


def normalize_title(title: str) -> str:
    """분류·표시 공용 제목 정규화: 공백 압축 → 자간 복원 → 번호 접두 제거."""
    s = despace_heading(title)
    s = _NUM_PREFIX_RE.sub("", s)
    return s.strip(" :.-—–\t")


# 제목 끝머리에 오는 IMRaD 어간. GROBID 가 헤딩을 병합하면서
# 'SEVERITY SCORING METHODS' 처럼 구조어가 뒤로 밀리는 사례가 흔하다.
_SUFFIX_STEMS = {
    "methods": "methods", "method": "methods", "methodology": "methods",
    "results": "results", "result": "results", "findings": "results",
    "discussion": "discussion", "conclusion": "discussion",
    "conclusions": "discussion", "introduction": "intro",
    "background": "intro", "abstract": "abstract",
}


def is_junk_title(title: str) -> bool:
    """머리말/꼬리말 잔재가 섹션 제목으로 승격된 쓰레기인가."""
    t = normalize_title(title).lower()
    return bool(t) and bool(_JUNK_RE.match(t))


def classify_section(title: str) -> str:
    """섹션 제목 문자열을 표준 타입으로 분류."""
    t = normalize_title(title).lower()
    if not t or _JUNK_RE.match(t):
        return "other"
    if t in BACK_MATTER:
        return "back"
    if t in SECTION_TYPE_MAP:
        return SECTION_TYPE_MAP[t]
    for key, val in SECTION_TYPE_MAP.items():
        if t.startswith(key):
            return val
    for key in BACK_MATTER:
        if t.startswith(key):
            return "back"
    # 끝단어가 구조어면 그것을 따른다 ('SEVERITY SCORING METHODS' → methods).
    # 접두 검사보다 뒤에 두어, 앞머리가 명확한 제목의 판정을 뒤집지 않는다.
    last = re.split(r"[^\w-]+", t)[-1] if t else ""
    if last in _SUFFIX_STEMS:
        return _SUFFIX_STEMS[last]
    return "other"


def classify_path(path: list[str]) -> str:
    """섹션 경로 전체로 분류한다.

    IMRaD 최상위 제목이 구조의 정본이므로 root 를 먼저 본다
    ('Results > Study Population' 은 방법이 아니라 결과다). root 가 분류되지
    않을 때만 leaf → 조상 순으로 내려가며 시도한다.
    파일럿 실측에서 root 우선/leaf 우선이 갈린 9건 모두 root 우선이 옳았다.
    """
    # 러닝헤더·페이지꼬리말이 제목으로 승격된 것(예: '0123456789();:')은
    # 경로에서 건너뛴다. 남겨 두면 그 잡음이 root 로 앉아 문서 전체의 분류를 끌고 간다.
    parts = [p for p in (path or []) if p and p.strip() and not is_junk_title(p)]
    if not parts:
        return "other"
    st = classify_section(parts[0])
    if st != "other":
        return st
    for part in reversed(parts):
        st = classify_section(part)
        if st != "other":
            return st
    return "other"
