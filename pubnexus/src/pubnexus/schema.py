"""정본(canonical) 문서 스키마.

JSON을 정본으로 두고 Markdown은 여기서 렌더링한다(설계서 5단계).
Markdown -> 구조 복원은 불가능하므로 이 스키마가 단일 진실이다.
"""
from __future__ import annotations

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
    section_type: str = "other"        # abstract|intro|methods|results|discussion|other
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


# 섹션 제목 -> 표준 타입 매핑 (검색 필터용)
SECTION_TYPE_MAP = {
    "abstract": "abstract",
    "background": "intro", "introduction": "intro",
    "method": "methods", "methods": "methods", "materials": "methods",
    "material and methods": "methods", "materials and methods": "methods",
    "patients and methods": "methods", "study design": "methods",
    "result": "results", "results": "results", "findings": "results",
    "discussion": "discussion", "conclusion": "discussion",
    "conclusions": "discussion", "limitations": "discussion",
}


def classify_section(title: str) -> str:
    """섹션 제목 문자열을 표준 타입으로 분류."""
    t = (title or "").strip().lower()
    if not t:
        return "other"
    if t in SECTION_TYPE_MAP:
        return SECTION_TYPE_MAP[t]
    for key, val in SECTION_TYPE_MAP.items():
        if t.startswith(key):
            return val
    return "other"
