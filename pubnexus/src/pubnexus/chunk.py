"""5단계 — 정본 Document → 검색 단위 청크(설계서 6단계 전처리).

RAG 품질의 8할은 청크 경계에서 갈린다. 원칙은 셋이다.
  1) 섹션 경계를 절대 넘지 않는다. Methods 의 통계 기법과 Results 의 수치가
     한 청크에 섞이면 "이 논문의 방법은?" 질의가 결과값을 근거로 답한다.
  2) 청크는 자기 위치를 안다. 임베딩 입력 앞에 "[논문제목] > Results > ..."
     컨텍스트 헤더를 붙여, 대명사·약어만 남은 문단도 소속을 잃지 않게 한다.
  3) 표/그림은 쪼개지 않는다. 표는 캡션+마크다운 통째로 한 청크(상한 초과 시에만
     헤더행을 반복하며 행 단위 분할), 그림은 캡션 한 청크.

참고문헌 목록(doc["references"])은 청킹하지 않는다 — 서지정보는 메타로 이미
구조화돼 있고, 본문 인덱스에 넣으면 제목 문자열이 검색 결과를 오염시킨다.

파일럿 167편 실측 결함 두 가지를 청킹 시점에 방어한다.
  · 섹션 제목의 자간 아티팩트("I N TRODUC TION") → 제목 정규화 후 재분류
  · 정본 JSON 에 굳어버린 section_type="other"(1,019/1,271) → schema.classify_path 재적용
정식 수리는 textfix 모듈 담당이라 있으면 위임하고, 없으면 자체 최소 정규화로 버틴다.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from . import schema
from . import utils
from .utils import approx_tokens, log

# textfix 는 별도 담당 모듈. 아직 없어도 5단계는 돌아가야 한다(자체 폴백 사용).
try:
    from . import textfix as _textfix   # type: ignore[attr-defined]
except ImportError:
    _textfix = None                     # noqa: N816 — 폴백 신호용 모듈 핸들

# 문장 경계: 종결부호 + (닫는 따옴표/괄호) + 공백 + 대문자/여는 괄호로 시작
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?])["\')\]]?\s+(?=[A-Z(\[])')
# 문장 경계로 오인하기 쉬운 약어(의학 본문에 흔하다)
_ABBREV_RE = re.compile(
    r'(?:\b(?:e\.g|i\.e|vs|cf|ca|approx|no|fig|figs|tab|tabs|eq|ref|refs|'
    r'et\s+al|dr|prof|mr|mrs|st|vol|pp|ca|min|max|sd|se|ci)\.|'
    r'\b[A-Z]\.)\s*$', re.I)
# 마크다운 표의 구분선(| --- | --- |)
_TABLE_SEP_RE = re.compile(r'^[\s|:\-]+$')
# 캡션 앞머리의 표/그림 번호 라벨
_ASSET_LABEL_RE = re.compile(
    r'^\s*((?:table|tab\.?|figure|fig\.?)\s*[0-9IVXivx]+[a-zA-Z]?)', re.I)

_warned: set[str] = set()               # 같은 경고를 167번 찍지 않도록


@dataclass
class Chunk:
    """검색·임베딩의 최소 단위. 1행 = chunks.jsonl 의 1레코드."""
    chunk_id: str                       # f"{slug(paper_id)}#c{seq:04d}"
    paper_id: str
    seq: int
    kind: str                           # abstract | text | table | figure
    section_path: list[str]
    section_type: str                   # abstract|intro|methods|results|discussion|other
    context_header: str                 # "[논문제목] > Results > Repigmentation rate"
    text: str                           # 청크 본문(컨텍스트 헤더 미포함)
    embed_text: str                     # 임베딩 입력 = 헤더 + "\n\n" + 본문
    n_tokens: int                       # utils.approx_tokens(embed_text)
    para_ids: list[str] = field(default_factory=list)
    cited_refs: list[str] = field(default_factory=list)
    refs_figure: list[str] = field(default_factory=list)
    refs_table: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Piece:
    """패킹 이전의 최소 조각(문단 하나 또는 긴 문단의 분할 조각)."""
    text: str
    para_id: str = ""
    cited_refs: list[str] = field(default_factory=list)
    refs_figure: list[str] = field(default_factory=list)
    refs_table: list[str] = field(default_factory=list)


# ── textfix 위임 + 자체 폴백 ────────────────────────────────────────────
def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log(msg)


def _repair_doc(doc: dict) -> dict:
    """textfix.repair_sections 가 있으면 섹션 수리를 위임한다(없으면 원본 그대로).

    repair_sections 는 '제자리 수정'이라 호출자의 dict 를 건드린다. 청킹은
    읽기 전용 연산이어야 하므로(정본 JSON 을 들고 있는 호출자가 그대로 다시
    쓰는 경로가 생기면 곧바로 데이터 손상이다) sections 만 복제해서 넘긴다.
    """
    fn = getattr(_textfix, "repair_sections", None) if _textfix else None
    if fn is None:
        return doc
    try:
        doc = dict(doc)
        doc["body_text"] = copy.deepcopy(doc.get("body_text") or [])
        return fn(doc) or doc
    except Exception as e:   # noqa: BLE001 — 수리 실패가 청킹을 멈추지 않도록 격리
        _warn_once("repair", f"      ! textfix.repair_sections 실패(원본 사용): "
                             f"{type(e).__name__}: {e}")
        return doc


def _clean_heading(s: str) -> str:
    """섹션 제목 정규화: textfix 우선, 없으면 공백압축+자간복원+번호접두 제거."""
    fn = getattr(_textfix, "clean_heading", None) if _textfix else None
    if fn is not None:
        try:
            s = fn(s)
        except Exception as e:   # noqa: BLE001 — 항목 단위 격리
            _warn_once("heading", f"      ! textfix.clean_heading 실패(폴백 사용): "
                                  f"{type(e).__name__}: {e}")
    return schema.normalize_title(utils.norm_text(s))


def _clean_paragraph(s: str) -> str:
    """본문 정규화: textfix 우선, 없으면 유니코드 정규화+공백 정리."""
    fn = getattr(_textfix, "clean_paragraph", None) if _textfix else None
    if fn is not None:
        try:
            s = fn(s)
        except Exception as e:   # noqa: BLE001 — 항목 단위 격리
            _warn_once("para", f"      ! textfix.clean_paragraph 실패(폴백 사용): "
                               f"{type(e).__name__}: {e}")
    return utils.norm_text(s)


# ── 텍스트 분할 헬퍼 ────────────────────────────────────────────────────
def _split_sentences(text: str) -> list[str]:
    """문장 분할. 약어(e.g. / Fig. / et al.) 뒤는 다시 이어 붙인다."""
    raw = _SENT_SPLIT_RE.split(text)
    out: list[str] = []
    for s in raw:
        s = s.strip()
        if not s:
            continue
        if out and _ABBREV_RE.search(out[-1]):
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
    return out or ([text.strip()] if text.strip() else [])


def _hard_wrap(text: str, budget: int) -> list[str]:
    """최후 수단: 구두점이 없는 덩어리를 공백(없으면 글자) 단위로 강제 절단."""
    words = text.split()
    if not words:
        return []
    out, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and approx_tokens(cand) > budget:
            out.append(cur)
            cur = w
        else:
            cur = cand
        while approx_tokens(cur) > budget:      # 단어 하나가 예산을 넘는 병리적 경우
            cut = max(1, budget * 4)
            out.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        out.append(cur)
    return out


def _split_long_text(text: str, target: int, hard_max: int,
                     overlap_sents: int) -> list[str]:
    """상한을 넘는 텍스트를 문장 경계로 분할. 마지막 1~2문장을 다음 조각에 겹친다."""
    if approx_tokens(text) <= hard_max:
        return [text]
    sents = _split_sentences(text)
    pieces: list[str] = []
    cur: list[str] = []
    for s in sents:
        if approx_tokens(s) > hard_max:         # 문장 하나가 상한 초과 → 강제 절단
            if cur:
                pieces.append(" ".join(cur))
                cur = []
            pieces.extend(_hard_wrap(s, hard_max))
            continue
        if cur and approx_tokens(" ".join(cur + [s])) > target:
            pieces.append(" ".join(cur))
            # 문맥 유지용 겹침: 직전 조각의 꼬리 문장들(예산의 1/3 이내로 제한)
            tail = cur[-overlap_sents:] if overlap_sents > 0 else []
            while tail and approx_tokens(" ".join(tail + [s])) > target:
                tail = tail[1:]
            cur = list(tail)
        cur.append(s)
    if cur:
        pieces.append(" ".join(cur))
    # 겹침 때문에 상한을 넘긴 조각이 남을 수 있으므로 최종 강제 준수
    out: list[str] = []
    for p in pieces:
        out.extend([p] if approx_tokens(p) <= hard_max else _hard_wrap(p, hard_max))
    return [p for p in out if p.strip()]


def _split_table(caption: str, markdown: str, hard_max: int) -> list[str]:
    """표 1개 → 청크 텍스트 목록.

    상한 초과 시에만 캡션+헤더행을 반복하며 행 단위로 나눈다. 단, 반복될
    prefix 가 예산의 절반을 넘으면 헤더행 반복을 포기한다(아래 주석 참조).
    """
    body = "\n\n".join(x for x in (caption, markdown) if x)
    if approx_tokens(body) <= hard_max:
        return [body] if body.strip() else []

    lines = [ln for ln in markdown.splitlines() if ln.strip()]
    head: list[str] = []
    if lines:
        head = [lines[0]]
        if len(lines) > 1 and _TABLE_SEP_RE.match(lines[1]):
            head.append(lines[1])
    prefix = "\n".join(x for x in ([caption] if caption else []) + head if x)

    # 반복되는 prefix(캡션+헤더행)는 예산의 절반을 넘어선 안 된다.
    # PDF 표 추출이 실패해 헤더행 하나에 표 전체가 뭉개져 들어온 문서가 있는데
    # (실측: bjd/ljac074 tab_2 는 헤더행만 2,806자), 그걸 조각마다 반복하면
    # 같은 내용이 7번 복제돼 인덱스가 6배로 부풀고 동일 벡터가 검색을 지배한다.
    # 그런 경우 헤더행 반복을 포기하고 캡션만 이어붙인다(헤더행은 일반 행으로 강등).
    prefix_budget = max(hard_max // 2, 8)
    if approx_tokens(prefix) > prefix_budget:
        head = []
        prefix = caption or ""
        if approx_tokens(prefix) > prefix_budget:
            prefix = prefix[:prefix_budget * 4].rstrip()
    rows = lines[len(head):]

    out: list[str] = []
    cur: list[str] = []
    for r in rows:
        cand = "\n".join([prefix] + cur + [r]) if prefix else "\n".join(cur + [r])
        if cur and approx_tokens(cand) > hard_max:
            out.append("\n".join([prefix] + cur) if prefix else "\n".join(cur))
            cur = [r]
        else:
            cur.append(r)
        if approx_tokens("\n".join([prefix] + cur) if prefix else "\n".join(cur)) > hard_max:
            # 행 하나가 상한을 넘는 초장문 셀 → 강제 절단
            out.extend(_hard_wrap("\n".join([prefix] + cur) if prefix else "\n".join(cur),
                                  hard_max))
            cur = []
    if cur:
        out.append("\n".join([prefix] + cur) if prefix else "\n".join(cur))
    return [p for p in out if p.strip()]


# ── 컨텍스트 헤더 ───────────────────────────────────────────────────────
def _context_header(title: str, path: list[str]) -> str:
    """'[논문제목] > Results > Repigmentation rate' — 제목 없으면 paper_id."""
    parts = [f"[{title}]"] + [p for p in path if p]
    return " > ".join(parts)


def _asset_label(item: dict, fallback_word: str) -> str:
    """캡션 앞머리의 'Table 2' / 'Figure 1' 라벨을 뽑는다(없으면 id)."""
    m = _ASSET_LABEL_RE.match(item.get("caption") or "")
    if m:
        return utils.norm_text(m.group(1))
    return item.get("id") or fallback_word


# ── 패킹(문단 → 청크 후보) ─────────────────────────────────────────────
def _join(pieces: list[_Piece]) -> str:
    return "\n\n".join(p.text for p in pieces)


def _pack(pieces: list[_Piece], target: int, hard_max: int,
          min_tokens: int) -> list[list[_Piece]]:
    """target 을 목표로 모으고 hard_max 를 절대 넘기지 않는다. 이후 짧은 조각 병합."""
    groups: list[list[_Piece]] = []
    cur: list[_Piece] = []
    for pc in pieces:
        if cur:
            cand = approx_tokens(_join(cur + [pc]))
            if cand > target and (approx_tokens(_join(cur)) >= min_tokens
                                  or cand > hard_max):
                groups.append(cur)
                cur = [pc]
                continue
        cur.append(pc)
    if cur:
        groups.append(cur)

    # min_tokens 미만은 같은 섹션의 이웃과 병합(상한을 넘지 않는 선에서).
    # 병합해도 미달이거나 이웃이 없으면(단독 섹션) 그대로 둔다.
    i = 0
    while i < len(groups):
        if approx_tokens(_join(groups[i])) >= min_tokens or len(groups) == 1:
            i += 1
            continue
        if (i + 1 < len(groups)
                and approx_tokens(_join(groups[i] + groups[i + 1])) <= hard_max):
            groups[i:i + 2] = [groups[i] + groups[i + 1]]
            continue
        if (i > 0
                and approx_tokens(_join(groups[i - 1] + groups[i])) <= hard_max):
            groups[i - 1:i + 1] = [groups[i - 1] + groups[i]]
            i -= 1
            continue
        i += 1
    return groups


def _uniq(seq: list[str]) -> list[str]:
    """순서 보존 중복 제거."""
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ── 섹션 전처리 ────────────────────────────────────────────────────────
def _prep_sections(doc: dict) -> list[dict]:
    """제목 정규화 + section_type 재분류. other 는 schema.classify_path 로 보강."""
    out: list[dict] = []
    # .get(k, []) 가 아니라 `or []` 다 — 키가 있는데 값이 None 인 문서
    # (수작업 편집·부분 실패 산출물)에서 TypeError 로 한 편을 통째로 잃지 않도록.
    for sec in (doc.get("body_text") or []):
        path = [h for h in (_clean_heading(x) for x in (sec.get("path") or [])) if h]
        st = sec.get("section_type") or "other"
        if st == "other":
            # 정본 JSON 이 굳은 뒤 SECTION_TYPE_MAP 이 확장됐다. 재적용해 구제한다.
            st = schema.classify_path(path)
        paras = []
        for p in (sec.get("paragraphs") or []):
            text = _clean_paragraph(p.get("text") or "")
            if not text:
                continue
            paras.append({
                "id": p.get("id") or "",
                "text": text,
                "cited_refs": list(p.get("cited_refs") or []),
                "refs_figure": list(p.get("refs_figure") or []),
                "refs_table": list(p.get("refs_table") or []),
            })
        out.append({"path": path, "section_type": st, "paragraphs": paras})
    return out


def _group_sections(sections: list[dict], respect_boundary: bool) -> list[dict]:
    """청킹 단위 묶음. 경계 준수(기본)면 섹션 1개 = 묶음 1개."""
    if respect_boundary:
        return sections
    merged: list[dict] = []
    for sec in body_text:
        if merged and merged[-1]["section_type"] == sec["section_type"]:
            merged[-1]["paragraphs"] = merged[-1]["paragraphs"] + sec["paragraphs"]
        else:
            merged.append({"path": list(sec["path"]),
                           "section_type": sec["section_type"],
                           "paragraphs": list(sec["paragraphs"])})
    return merged


# ── 본체 ───────────────────────────────────────────────────────────────
def chunk_document(doc: dict, cfg: dict) -> list[dict]:
    """정본 Document(dict) 1편 → 청크 dict 목록."""
    c = cfg.get("chunk", {})
    target = int(c.get("target_tokens", 550))
    max_tokens = int(c.get("max_tokens", 700))
    min_tokens = int(c.get("min_tokens", 80))
    respect_boundary = bool(c.get("respect_section_boundary", True))
    use_header = bool(c.get("context_header", True))
    include_abstract = bool(c.get("include_abstract", True))
    include_tables = bool(c.get("include_tables", True))
    include_figures = bool(c.get("include_figures", True))
    # exclude_references 는 참고문헌 목록뿐 아니라 감사의 글·이해충돌 등
    # 후행 부속(section_type="back")에도 같이 적용한다.
    drop_back = bool(c.get("exclude_back_matter", c.get("exclude_references", True)))
    min_caption_chars = int(c.get("min_caption_chars", 15))
    overlap_sents = int(c.get("overlap_sentences", 2))

    doc = _repair_doc(doc)
    paper_id = doc.get("paper_id") or ""
    m = doc.get("meta") or {}
    title = utils.norm_text(m.get("title") or "") or paper_id
    base_meta = {
        "title": m.get("title") or "",
        "journal": m.get("journal") or "",
        "year": m.get("year"),
        "doi": m.get("doi"),
        "pmid": m.get("pmid"),
        "pmcid": m.get("pmcid"),
        "mesh": list(m.get("mesh") or []),
        "pub_types": list(m.get("pub_types") or []),
        "source": doc.get("source") or "",
        "quality_score": doc.get("quality_score"),
    }

    chunks: list[Chunk] = []
    sid = utils.slug(paper_id)

    def header_for(path: list[str]) -> str:
        if not use_header:
            return ""
        h = _context_header(title, path)
        # 비정상적으로 긴 제목/경로가 본문 자리를 잠식하지 않도록 상한의 절반으로 제한
        limit = max(max_tokens // 2, 16)
        if approx_tokens(h) > limit:
            h = h[:limit * 4 - 1].rstrip() + "…"
        return h

    def emit(kind: str, path: list[str], stype: str, text: str,
             para_ids: list[str], cited: list[str],
             r_fig: list[str], r_tab: list[str]) -> None:
        text = text.strip()
        if not text:
            return
        header = header_for(path)
        embed = f"{header}\n\n{text}" if header else text
        seq = len(chunks) + 1
        chunks.append(Chunk(
            chunk_id=f"{sid}#c{seq:04d}",
            paper_id=paper_id,
            seq=seq,
            kind=kind,
            section_path=list(path),
            section_type=stype,
            context_header=header,
            text=text,
            embed_text=embed,
            n_tokens=approx_tokens(embed),
            para_ids=_uniq(para_ids),
            cited_refs=_uniq(cited),
            refs_figure=_uniq(r_fig),
            refs_table=_uniq(r_tab),
            meta=dict(base_meta),
        ))

    def budgets(path: list[str]) -> tuple[int, int]:
        """컨텍스트 헤더도 임베딩 입력에 들어가므로 헤더 몫을 미리 뺀다."""
        if not use_header:
            return target, max_tokens
        # +1 은 헤더와 본문을 이어붙일 때 생기는 토큰 근사(floor) 오차 여유
        hb = approx_tokens(header_for(path) + "\n\n") + 1
        hard = max(max_tokens - hb, 8)
        return max(min(target - hb, hard), 8), hard

    # 1) 초록 — 검색 진입점으로 가치가 커 단독 청크로 둔다
    if include_abstract:
        abs_text = _clean_paragraph(doc.get("abstract") or "")
        if abs_text:
            path = ["Abstract"]
            tb, hb = budgets(path)
            for piece in _split_long_text(abs_text, tb, hb, overlap_sents):
                emit("abstract", path, "abstract", piece, [], [], [], [])

    # 2) 본문 섹션 — 경계를 넘지 않고 패킹
    sections = _prep_sections(doc)
    # 표/그림이 어느 섹션에서 인용됐는지(캡션 청크의 소속 추정에 쓴다)
    asset_home: dict[str, tuple[list[str], str]] = {}
    for sec in sections:
        for p in sec["paragraphs"]:
            for rid in list(p["refs_table"]) + list(p["refs_figure"]):
                asset_home.setdefault(rid, (sec["path"], sec["section_type"]))

    for sec in _group_sections(sections, respect_boundary):
        if drop_back and sec["section_type"] == "back":
            continue
        if not sec["paragraphs"]:
            continue
        tb, hb = budgets(sec["path"])
        pieces: list[_Piece] = []
        for p in sec["paragraphs"]:
            for frag in _split_long_text(p["text"], tb, hb, overlap_sents):
                pieces.append(_Piece(frag, p["id"], p["cited_refs"],
                                     p["refs_figure"], p["refs_table"]))
        for grp in _pack(pieces, tb, hb, min_tokens):
            emit("text", sec["path"], sec["section_type"], _join(grp),
                 [g.para_id for g in grp],
                 [r for g in grp for r in g.cited_refs],
                 [r for g in grp for r in g.refs_figure],
                 [r for g in grp for r in g.refs_table])

    # 3) 표 — 캡션+마크다운 통째로 1청크(상한 초과 시에만 행 분할)
    if include_tables:
        for t in (doc.get("tables") or []):
            cap = _clean_paragraph(t.get("caption") or "")
            md = (t.get("markdown") or "").strip()
            if not md and len(cap) < min_caption_chars:
                continue                       # 빈 표 + 무의미 캡션은 색인 잡음
            home_path, home_type = asset_home.get(t.get("id") or "", ([], "other"))
            path = list(home_path) + [_asset_label(t, "Table")]
            _tb, hb = budgets(path)
            for piece in _split_table(cap, md, hb):
                emit("table", path, home_type, piece, [], [], [], [t.get("id") or ""])

    # 4) 그림 — 캡션 1청크
    if include_figures:
        for f in (doc.get("figures") or []):
            cap = _clean_paragraph(f.get("caption") or "")
            if len(cap) < min_caption_chars:
                continue                       # "Figure 1." 뿐인 캡션은 제외
            home_path, home_type = asset_home.get(f.get("id") or "", ([], "other"))
            path = list(home_path) + [_asset_label(f, "Figure")]
            tb, hb = budgets(path)
            for piece in _split_long_text(cap, tb, hb, overlap_sents):
                emit("figure", path, home_type, piece, [], [],
                     [f.get("id") or ""], [])

    return [ch.to_dict() for ch in chunks]


# ── 통계 ───────────────────────────────────────────────────────────────
def _pct(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def _dist(rows: list[dict], key: str) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[key]] = counts.get(r[key], 0) + 1
    return " · ".join(f"{k} {v}" for k, v in
                      sorted(counts.items(), key=lambda kv: -kv[1]))


def run(config: dict | None = None) -> None:
    cfg = config or utils.load_config()
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"
    out_path = utils.resolve(cfg["chunk"]["output"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    max_tokens = int(cfg.get("chunk", {}).get("max_tokens", 700))

    docs = sorted(norm_dir.glob("*.json"))
    log(f"[5단계] 청킹: {len(docs)}편 @ {norm_dir}")
    if _textfix is None:
        log("        textfix 미설치 → 자체 최소 정규화(공백 압축·자간 복원)로 진행")

    rows: list[dict] = []
    empty = 0
    for i, p in enumerate(docs, 1):
        try:
            doc = utils.read_json(p)
            cs = chunk_document(doc, cfg)
        except Exception as e:   # noqa: BLE001 — 파일 단위 격리
            log(f"  [{i:>3}/{len(docs)}] 청킹 실패({p.stem}): {type(e).__name__}: {e}")
            continue
        rows.extend(cs)
        if not cs:
            empty += 1
            log(f"      ! 청크 0개: {doc.get('paper_id')}")
            continue
        n_tab = sum(1 for x in cs if x["kind"] == "table")
        n_fig = sum(1 for x in cs if x["kind"] == "figure")
        log(f"  [{i:>3}/{len(docs)}] {doc.get('paper_id')}: 청크 {len(cs)} · "
            f"토큰 {sum(x['n_tokens'] for x in cs)} · 표 {n_tab} · 그림 {n_fig}")

    utils.write_jsonl(out_path, rows)

    toks = sorted(x["n_tokens"] for x in rows)
    over = sum(1 for t in toks if t > max_tokens)
    n_papers = len({x["paper_id"] for x in rows})
    log(f"[5단계] 완료 → {out_path} (문서 {n_papers}/{len(docs)} · 청크 {len(rows)}"
        + (f" · 청크 0개 문서 {empty}" if empty else "") + ")")
    log(f"        kind: {_dist(rows, 'kind')}")
    log(f"        section_type: {_dist(rows, 'section_type')}")
    log(f"        토큰 p50 {_pct(toks, 0.5)} · p90 {_pct(toks, 0.9)} · "
        f"max {toks[-1] if toks else 0} · 평균 {sum(toks)//max(len(toks),1)}")
    log(f"        상한({max_tokens}) 초과 청크: {over}")


if __name__ == "__main__":
    run(utils.load_config())
