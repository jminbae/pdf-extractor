"""4.5단계 — 정규화 텍스트 결함 수리(자간 아티팩트·러닝헤더·캡션 누수·섹션 타입).

파일럿 167편 전수조사에서 드러난 추출 결함을 5~7단계(청킹·임베딩·검색)에
들어가기 전에 정본 JSON 수준에서 복구한다. 근본 원인은 PDF 조판이라 파서를
고쳐도 이미 만들어진 산출물은 낫지 않으므로, (a) 여기서 사후 수리하고
(b) 같은 함수를 jats/grobid_client/pdf_fallback 이 호출해 새로 처리되는
문서에는 애초에 결함이 생기지 않게 한다.

수리 대상
  D1 섹션 타입 붕괴  — 제목 자간 복원 → 재분류 → 상위경로 상속 → 직전 타입 캐리포워드
  D2 자간 아티팩트    — 'M ATER I A L S A N D M ETHODS' → 'MATERIALS AND METHODS'
  D3 러닝헤더 잔재    — 본문에 끼어든 'J AM ACAD DERMATOL', '0123456789();:',
                        'Downloaded from https://…', 저작권/이용약관 문구
  D4 캡션 누수        — 본문·초록 끝에 붙은 'Figure 4. …' / 'Table III. …'
  D5 중복 문단        — 문서 내 완전 동일 문단
  D6 빈 표            — 데이터행도 캡션도 없는 표
  D7 줄바꿈 분철      — 'do- mains' → 'domains' (접미 결합형은 보존)

설계 원칙: **오탐 0 우선.** 판정이 조금이라도 애매하면 원문을 그대로 둔다.
자간 복원은 "붙였을 때 어휘 사전으로 완전히 분해되고, 실제로 토큰 수가
줄어들 때"만 채택하므로 정상 제목('Materials and Methods', 'NB-UVB',
'J AM ACAD DERMATOL')은 구조적으로 훼손될 수 없다.
"""
from __future__ import annotations

import copy
import re
import shutil
from typing import Any

from . import schema, utils
from .schema import BACK_MATTER, SECTION_TYPE_MAP, classify_section
from .utils import log, norm_text

# ── 자간 복원용 어휘 사전 ────────────────────────────────────────────
# 사전은 (1) schema 의 섹션 타입 맵/백매터 어휘를 자동 수확하고
# (2) 실측 코퍼스에서 자간이 깨진 제목에 실제로 등장한 단어를 더해 만든다.
# 사전에 없는 단어가 섞인 제목은 분해에 실패해 '수리하지 않음'으로 떨어진다
# (= 안전 실패). 사전을 늘리면 복구율이 오르고 오탐 위험은 늘지 않는다.
_EXTRA_VOCAB = """
ABBREVIATION ABBREVIATIONS ACKNOWLEDGEMENT ACKNOWLEDGEMENTS ACKNOWLEDGMENT
ACKNOWLEDGMENTS ACTIVITY ADDITIONAL AFFILIATION AFFILIATIONS ANALYSES ANALYSIS
APPENDIX APPROVAL ASSESSMENT ASSOCIATED AUTHOR AUTHORS AUTHORSHIP AVAILABILITY
BACKGROUND BOARD BURDEN CASE CHARACTERISTICS CLINICAL COMMENT COMMITTEE
COMPETING CONCLUSION CONCLUSIONS CONFLICT CONFLICTS CONSENT CONTRIBUTION
CONTRIBUTIONS CONTRIBUTORS CRITERIA DATA DECISION DECLARATION DECLARATIONS
DESIGN DIAGNOSIS DISCLAIMER DISCLOSURE DISCLOSURES DISCUSSION DISEASE EDITOR
ETHICAL ETHICS EVENTS EXPECTATIONS EXTRACTION FIGURE FIGURES FILE FILES
FINDINGS FOLLOW FRAMING FUNDING FUTURE GOALS GRANT GUIDELINE GUIDELINES
IMPLICATIONS INFORMATION INFORMED INITIAL INSTITUTIONAL INTEREST INTERESTS
INTERVENTION INTRODUCTION IRB KEY KEYWORD KEYWORDS LEGEND LEGENDS LETTER
LIMITATION LIMITATIONS MAKING MANAGEMENT MATERIAL MATERIALS MEASURES METHOD
METHODS NOMENCLATURE NOTE NOTES OBJECTIVE OBJECTIVES OUTCOME OUTCOMES PATIENT
PATIENTS POPULATION PROCEDURE PROCEDURES PROTOCOL PURPOSE RATIONALE
RECOMMENDATION RECOMMENDATIONS REFERENCE REFERENCES REGISTRATION REPORT REVIEW
RESULT RESULTS SAFETY SAMPLE SCORING SEARCH SELECTION SERIES SEVERITY SHARED
SHARING SIGNIFICANCE SOURCE SOURCES STATEMENT STATISTICAL STATISTICS STATUS
STRATEGY STUDY SUBJECT SUBJECTS SUMMARY SUPPLEMENT SUPPLEMENTAL SUPPLEMENTARY
SUPPORT SUPPORTING TABLE TABLES TERMINOLOGY TRIAL TREATMENT
AND FOR FROM NOT OF ON OR THE TO WITH IN AS AT BY ALL ITS VS
"""


def _build_vocab() -> frozenset[str]:
    words: set[str] = set()
    for phrase in list(SECTION_TYPE_MAP) + list(BACK_MATTER):
        for w in re.split(r"[^A-Za-z]+", phrase):
            if len(w) >= 2:
                words.add(w.upper())
    for w in _EXTRA_VOCAB.split():
        if len(w) >= 2:
            words.add(w)
    return frozenset(words)


VOCAB = _build_vocab()
_MAXW = max(len(w) for w in VOCAB)

# ── 러닝헤더/저작권 잔재 (실측 코퍼스에서 확인된 고정밀 패턴만) ──────
_JUNK_PATTERNS = [
    # Elsevier(JAAD) 러닝헤더가 문장 중간에 삽입된 경우
    re.compile(r"\s*\bJ\s*AM\s*ACAD\s*DERMATOL\b\s*"),
    # Nature Reviews 러닝헤더('0123456789();:' / '0123456789;: Primer')
    re.compile(r"\s*0123456789\s*[();:.]{0,4}\s*(?:Primer\b)?\s*"),
    # OUP/Wiley 다운로드 워터마크 (끝에 남는 ']' 와 마침표까지 함께 걷는다 —
    # 남기면 '… clear.]. See' 처럼 문장부호 잔해가 본문에 박힌다)
    re.compile(r"\s*Downloaded\s+(?:from|for)\s+\S+[^|\n]{0,140}?\b\d{4}\b\]?[.;]?\s*", re.I),
    re.compile(r"\s*See\s+the\s+Terms\s+and\s+Conditions\s*\([^)]*\)[^|\n]{0,160}?"
               r"(?:governed\s+by[^|\n]{0,60}?|rules\s+of\s+use;?)\s*", re.I),
    re.compile(r"\s*(?:https?://)?onlinelibrary\.wiley\.com/terms-and-conditions\)?\s*", re.I),
    re.compile(r"\s*This\s+article\s+is\s+protected\s+by\s+copyright\.?\s*"
               r"(?:All\s+rights\s+reserved\.?)?\s*", re.I),
    # 페이지 꼬리말형 저작권 줄(문단 앞에 붙어 들어온 경우)
    re.compile(r"^\s*©\s*\d{4}[^.]{0,160}?pp\.?\s*\d+\s*[-–]\s*\d+\s*", re.I),
    # 법인 접미(Inc/Ltd/…)에는 \b 를 반드시 붙인다. 없으면 'Incorporated' 의 'Inc',
    # 'obvious' 의 'bv' 같은 **단어 중간**에 걸려 본문을 잘라먹는다(실측 확인).
    re.compile(r"^\s*©\s*\d{4}\s+(?:The\s+Authors?\.?\s*)?"
               r"(?:Published\s+by\s+)?[A-Z][\w&.,'’\- ]{0,60}?"
               r"(?:Ltd|Inc|B\.?V|LLC|GmbH)\b\.?\s*", re.I),
]

# 캡션 누수: 문장 끝(또는 문자열 시작) 뒤에 오는 'Figure 4. …' 형태로 끝나는 꼬리.
# 번호 뒤 구두점(. :)을 **필수**로 요구해 "Table 1 shows the …"(정상 본문)와
# "Table III. Summary of …"(캡션)를 가른다 — 실측에서 이 조건만이 둘을 갈랐다.
# 길이 상한 400자: 캡션 뒤에 본문이 다시 이어지는 혼합 문단에서 본문까지
# 통째로 떼어내는 사고를 막는다(상한을 넘으면 아예 손대지 않는다).
_CAPTION_TAIL = re.compile(
    r"(?:\A|(?<=[.?!])\s+)"
    r"((?:Fig(?:ure)?s?|Tab(?:le)?s?|Supplementary\s+(?:Fig(?:ure)?|Table)|"
    r"Appendix|Box|Chart)\s*\.?\s*(?:\d{1,2}|[IVX]{1,4})[A-Za-z]?\s*[.:]\s+"
    r"[A-Z][^\n]{15,400})\Z"
)

# 캐리포워드 정지어: 여기부터는 본문이 아니라 후행 부속(백매터)이다.
_BACKMATTER_STOP = re.compile(
    r"^(acknowledg|conflict|competing|disclos|funding|orcid|author\s*contrib|"
    r"authorship|contributor|data\s+availab|data\s+sharing|ethic|irb|consent|"
    r"supplement|supporting\s+information|appendix|abbreviat|keywords?$|"
    r"key\s*words?$|affiliation|declaration|permission|references?$|"
    r"bibliography$|disclaimer|financial\s+support|competing\s+interest)", re.I)

# 줄바꿈 분철 복원에서 **건드리면 안 되는** 접미/접두(정상 하이픈 합성어)
_HYPHEN_TAIL_STOP = frozenset("""
and or nor to but with without versus vs than then plus related associated
based induced free like dependent independent specific negative positive
matched mediated controlled resistant naive term type level dose sparing
old linked blind only wide rich poor driven derived guided treated exposed
""".split())
_HYPHEN_HEAD_STOP = frozenset("""
self non pre post anti co mid sub well long short high low single double
open case cross follow first second third one two three multi semi intra
inter trans re de over under new old full half whole time site dose
sex gender age race year week month day drug skin sun body risk
""".split())
_HYPHEN_SPLIT = re.compile(r"\b([a-z]{2,})-\s+([a-z]{2,})\b")
# 줄바꿈 분철의 뒷조각은 대개 짧다('do- mains', 'character- ized'). 뒷조각이 길면
# 'sex- stratified' / 'subse- adjustment' 처럼 **온전한 단어**일 확률이 높고, 붙이면
# 검색 불가능한 쓰레기 토큰이 된다 → 길면 손대지 않는다(안전 실패).
_HYPHEN_MAX_TAIL = 7

_SEP_SPLIT = re.compile(r"([^A-Z0-9]+)")     # 대문자·숫자 이외는 구분자로 보존
_DIGITS = re.compile(r"^\d+$")


# ── 자간(글자 사이 공백) 아티팩트 복원 ──────────────────────────────
def _dp_segment(s: str, allow_unknown: bool = False) -> list[tuple[str, bool]] | None:
    """공백 없는 대문자 문자열을 어휘 사전으로 분해(조각 수 최소화 DP).

    반환: [(조각, 미지어 여부), ...] 또는 None(분해 실패).
    """
    n = len(s)
    if n == 0:
        return []
    inf = float("inf")
    cost = [inf] * (n + 1)
    back: list[tuple[int, str, bool] | None] = [None] * (n + 1)
    cost[0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(0, i - _MAXW), i):
            if cost[j] == inf:
                continue
            piece = s[j:i]
            if piece in VOCAB or _DIGITS.match(piece):
                c: float = 1.0
                unk = False
            elif allow_unknown and len(piece) >= 3:
                c = 10.0 + len(piece)      # 미지어는 강한 벌점(최후 수단)
                unk = True
            else:
                continue
            if cost[j] + c < cost[i]:
                cost[i] = cost[j] + c
                back[i] = (j, piece, unk)
    if cost[n] == inf:
        return None
    out: list[tuple[str, bool]] = []
    i = n
    while i > 0:
        j, piece, unk = back[i]           # type: ignore[misc]
        out.append((piece, unk))
        i = j
    out.reverse()
    return out


def _segment_with_seps(concat: str, allow_unknown: bool,
                       max_unknown: int = 0) -> str | None:
    """구분자(-, /, :, . 등)를 제자리에 두고 각 조각을 분해해 재조립."""
    parts = _SEP_SPLIT.split(concat)
    out: list[str] = []
    unknown = 0
    for k, part in enumerate(parts):
        if k % 2 == 1 or not part:        # 구분자 위치는 그대로
            out.append(part)
            continue
        seg = _dp_segment(part, allow_unknown=allow_unknown)
        if seg is None:
            return None
        unknown += sum(1 for _, unk in seg if unk)
        if unknown > max_unknown:
            return None
        out.append(" ".join(p for p, _ in seg))
    return "".join(out)


def _is_cap_token(t: str) -> bool:
    """자간 복원 후보 토큰: 소문자가 없고 영문자를 하나 이상 가진 토큰."""
    return (bool(re.search(r"[A-Z]", t))
            and not any(c.islower() for c in t)
            and bool(re.fullmatch(r"[A-Z0-9][A-Z0-9\-/&:.'’]*", t)))


def _repair_run(tokens: list[str]) -> str | None:
    """대문자 토큰 런 하나를 자간 복원. 확신이 없으면 None(원문 유지)."""
    if len(tokens) < 2:
        return None
    alpha = [t for t in tokens if any(c.isalpha() for c in t)]
    if len(alpha) < 2:
        return None
    # 이미 전부 사전에 있는 정상 단어라면 손대지 않는다
    # ('MATERIALS AND METHODS', 'CASE REPORT', 'PATIENTS AND METHODS' 보호)
    if all(re.sub(r"[^A-Z0-9]", "", t) in VOCAB for t in alpha):
        return None
    singles = sum(1 for t in alpha if len(t) == 1)
    strong = singles >= 3 and singles / len(alpha) >= 0.4
    concat = "".join(tokens)
    fixed = _segment_with_seps(concat, allow_unknown=False)
    if fixed is None and strong:
        # 단독 문자가 3개 이상 흩어진 '확실한 아티팩트'에 한해 미지어 1개 허용
        fixed = _segment_with_seps(concat, allow_unknown=True, max_unknown=1)
    if fixed is None:
        return None
    if len(fixed.split()) >= len(tokens):
        return None                        # 실제로 합쳐진 게 없으면 취소
    return fixed


def _despace_runs(s: str) -> str:
    """문자열 안의 모든 '대문자 토큰 런'을 찾아 자간 복원을 시도한다."""
    if not s:
        return s
    out: list[str] = []
    run: list[str] = []

    def flush():
        if run:
            fixed = _repair_run(run)
            out.append(fixed if fixed is not None else " ".join(run))
            run.clear()

    for t in s.split():
        if _is_cap_token(t):
            run.append(t)
        else:
            flush()
            out.append(t)
    flush()
    return " ".join(out)


def clean_heading(s: str) -> str:
    """섹션 제목의 자간(글자 사이 공백) 아티팩트를 복원한다.

    'I N TRODUC TION' → 'INTRODUCTION', 'CO N FLI C T O F I NTE R E S T' →
    'CONFLICT OF INTEREST', 'TA B L E 1 Baseline …' → 'TABLE 1 Baseline …'.

    판정 규칙(오탐 방지가 최우선)
      1. 소문자가 없는 토큰들이 **연속으로 2개 이상** 이어진 구간(런)만 손댄다.
         소문자가 섞인 토큰은 런을 끊으므로 정상 제목의 본문부는 절대 변형되지 않는다.
      2. 런의 모든 토큰이 이미 어휘 사전의 단어면 그대로 둔다
         ('MATERIALS AND METHODS', 'CASE REPORT' 보호).
      3. 런을 공백 없이 붙인 뒤 어휘 사전으로 **완전히** 분해될 때만 채택한다.
         분해 실패 = 수리 포기('J AM ACAD DERMATOL', 'THE 308-NM EXCIMER LASER',
         'ABLATIVE FRACTIONAL LASER' 보호).
      4. 단독 알파벳이 3개 이상이고 40% 이상인 '확실한 아티팩트'에 한해
         분해되지 않는 조각 1개를 허용한다(부분 복구).
      5. 복원 결과의 토큰 수가 원래보다 줄지 않으면 취소한다(무의미 재배열 차단).

    한계
      · 소문자까지 자간이 벌어진 본문형 아티팩트('A r e c e n t l y …')는
        단어 경계 정보가 소실돼 복원하지 않는다(정규화 단계에서 이미 공백이
        1칸으로 압축되어 원 정보가 없다).
      · 사전에 없는 전문어로만 이루어진 제목은 복원되지 않는다(안전 실패).
    """
    s = norm_text(s or "")
    if not s:
        return ""
    s = _despace_runs(s)
    return _tidy_spaces(s)


def _tidy_spaces(s: str) -> str:
    """공백·구두점 주변 정리(자간 복원/잡음 제거 후 남는 자국).

    제목에도 쓰이므로 '내용을 지우는' 규칙은 두지 않는다(공백만 다룬다).
    '%'·'!' 는 PDF 추출에서 '≥' 등이 깨져 들어오는 자리라 일반 규칙에서 빼고,
    숫자 바로 뒤의 '%'(95 % CI → 95% CI)만 붙인다.

    의학 논문 표기를 깨지 않기 위한 예외 둘(실측에서 훼손 확인):
      · '. ' 앞 공백 제거는 뒤가 숫자면 하지 않는다 — 'P = .001' 이 'P =.001' 이 된다.
      · ': ' 앞 공백 제거는 앞이 숫자면 하지 않는다 — 비율 '1 : 1.6' 이 '1: 1.6' 이 된다.
    """
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,;)\]])", r"\1", s)
    s = re.sub(r"\s+\.(?!\d)", ".", s)
    s = re.sub(r"(?<!\d)\s+:", ":", s)
    s = re.sub(r"(?<=\d)\s+%", "%", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    return s.strip()


def _dehyphenate(s: str) -> str:
    """줄바꿈 분철 복원: 'do- mains' → 'domains'.

    'inter- and intraobserver', 'self- reported' 같은 정상 하이픈 합성어는
    앞/뒤 단어 정지목록과 뒷조각 길이 상한으로 보호한다.
    """
    def repl(m: re.Match) -> str:
        head, tail = m.group(1), m.group(2)
        if (len(tail) > _HYPHEN_MAX_TAIL
                or tail in _HYPHEN_TAIL_STOP or head in _HYPHEN_HEAD_STOP):
            return m.group(0)
        return head + tail
    return _HYPHEN_SPLIT.sub(repl, s)


def clean_paragraph(s: str) -> str:
    """본문 문단에서 러닝헤더·다운로드 워터마크·저작권 잔재를 제거하고 정리한다.

    제거 대상은 실측 코퍼스에서 확인된 고정밀 패턴뿐이다(잡히지 않는 잡음은
    남긴다 — 본문 유실보다 잡음 잔존이 낫다). 자간 복원과 줄바꿈 분철 복원도
    함께 적용한다. 멱등(여러 번 적용해도 같은 결과)이다.
    """
    s = norm_text(s or "")
    if not s:
        return ""
    for rx in _JUNK_PATTERNS:
        s = rx.sub(" ", s)
    s = _despace_runs(s)
    s = _dehyphenate(s)
    s = re.sub(r"\(\s*\)", "", s)          # 잡음을 걷어낸 자리에 남은 빈 괄호
    return _tidy_spaces(s)


def strip_caption_leak(s: str) -> tuple[str, list[str]]:
    """본문/초록 끝에 붙어 들어온 figure·table 캡션을 떼어낸다.

    반환: (캡션을 제거한 본문, 떼어낸 캡션 리스트).
    문단 전체가 캡션이면 본문은 ""가 되고 캡션 1개가 반환된다.
    "Table 1 shows the characteristics …" 같은 **정상 본문 문장**은 번호 뒤
    구두점이 없으므로 걸리지 않는다.
    """
    text = (s or "").strip()
    caps: list[str] = []
    while True:
        m = _CAPTION_TAIL.search(text)
        if not m:
            break
        caps.append(_tidy_spaces(m.group(1)))
        text = text[:m.start()].rstrip()
        if not text:
            break
    caps.reverse()
    return text, caps


# ── 섹션 타입 복구 ──────────────────────────────────────────────────
def _classify_path(path: list[str]) -> str:
    """경로 전체로 섹션 타입 판정(schema.classify_path 우선, 없으면 자체 구현)."""
    fn = getattr(schema, "classify_path", None)
    if fn is not None:
        return fn(path)
    parts = [p for p in (path or []) if p and p.strip()]
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


def _is_backmatter_title(title: str) -> bool:
    t = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[IVXLC]+)[.)|]?\s*", "", title or "").strip()
    return bool(_BACKMATTER_STOP.match(t))


def repair_sections(doc: dict, carry_forward: bool = True) -> dict:
    """섹션 제목을 수리하고 section_type 을 다시 매긴다(제자리 수정).

    1) 제목 자간 복원 → 2) 경로 전체 재분류(상위 경로 상속) →
    3) 후행 부속(감사의 글·이해충돌 등)은 'back' 으로 고정 →
    4) 그래도 'other' 면 직전 섹션의 타입을 이어받는다(캐리포워드).

    GROBID TEI 가 계층 없는 평면 경로를 내보내는 탓에 하위 절이 부모를 잃고
    80%가 'other' 로 떨어지는 문제(D1)의 실질적 해법이다. 캐리포워드는
    백매터 정지어를 만나면 멈추므로 감사의 글이 Results 로 오염되지 않는다.
    """
    prev_type = "other"
    for sec in doc.get("sections") or []:
        path = [clean_heading(p) for p in (sec.get("path") or [])]
        sec["path"] = path
        st = _classify_path(path)
        leaf = path[-1] if path else ""
        root = path[0] if path else ""
        if _is_backmatter_title(leaf) or _is_backmatter_title(root):
            st = "back"
        # 머리말/꼬리말 잔재('CAPSULE SUMMARY d', 'J AM ACAD DERMATOL', '0123456789();:')가
        # 섹션으로 승격된 경우는 캐리포워드 대상에서 제외한다. 이어받게 두면 쓰레기 텍스트가
        # Results/Methods 로 둔갑해 필터 검색 결과를 오염시킨다(실측 15개 중 10개가 승격됐다).
        elif st == "other" and schema.is_junk_title(leaf):
            pass                                    # other 로 고정, prev_type 도 갱신하지 않음
        elif st == "other" and carry_forward and prev_type not in ("other", "back", "abstract"):
            st = prev_type
        sec["section_type"] = st
        if st != "other":
            prev_type = st
    return doc


# ── 표/그림 ─────────────────────────────────────────────────────────
def _table_data_rows(markdown: str) -> int:
    lines = [l for l in (markdown or "").splitlines() if l.strip()]
    return max(0, len(lines) - 2)          # 헤더행 + 구분행 제외


def _is_empty_table(tbl: dict) -> bool:
    """데이터행이 하나도 없고 캡션도 사실상 없는 표 = 추출 실패 잔해."""
    return (_table_data_rows(tbl.get("markdown") or "") == 0
            and len((tbl.get("caption") or "").strip()) < 15)


def _caption_exists(items: list[dict], cap: str) -> bool:
    key = re.sub(r"\W+", "", cap.lower())[:40]
    for it in items:
        other = re.sub(r"\W+", "", (it.get("caption") or "").lower())
        if key and key in other:
            return True
    return False


def _attach_caption(doc: dict, cap: str, seq: int) -> str | None:
    """떼어낸 캡션을 figures/tables 로 옮긴다. 이미 있으면 버린다."""
    is_table = bool(re.match(r"\s*(?:Tab(?:le)?s?|Supplementary\s+Table)\b", cap, re.I))
    bucket = doc.setdefault("tables" if is_table else "figures", [])
    if _caption_exists(bucket, cap):
        return None
    item: dict[str, Any] = {"id": f"{'tab' if is_table else 'fig'}_leak{seq}",
                            "caption": cap}
    if is_table:
        item["markdown"] = ""
    else:
        item["image"] = None
    bucket.append(item)
    return item["id"]


# ── 문서 단위 수리 ──────────────────────────────────────────────────
def fix_document(doc: dict, carry_forward: bool = True) -> tuple[dict, dict]:
    """정본 문서 dict 하나를 수리한다. 반환: (수리된 dict, 변경 통계 dict).

    입력 dict 는 변경하지 않는다(깊은 복사 후 작업). 통계에는 감사용으로
    실제 변경된 제목 쌍·재분류 내역을 (개수 제한과 함께) 담는다.
    """
    doc = copy.deepcopy(doc)
    st: dict[str, Any] = {
        "paper_id": doc.get("paper_id", ""),
        "source": doc.get("source", ""),
        "headings_fixed": 0, "sections_reclassified": 0,
        "paragraphs_cleaned": 0, "paragraphs_dropped_dup": 0,
        "paragraphs_dropped_caption": 0, "paragraphs_dropped_empty": 0,
        "captions_extracted": 0, "captions_attached": 0,
        "abstract_cleaned": False, "abstract_caption_stripped": 0,
        "tables_dropped": 0, "tables_cleaned": 0, "figures_cleaned": 0,
        "sections_dropped": 0,
        "heading_samples": [], "reclass_samples": [], "caption_samples": [],
    }
    leaked: list[str] = []

    # 1) 초록 — 캡션 꼬리는 본문이 남을 때만 떼어낸다(초록 전체가 캡션인
    #    문서는 그대로 두는 편이 검색 진입점 손실보다 낫다).
    ab_before = doc.get("abstract") or ""
    ab = clean_paragraph(ab_before)
    body, caps = strip_caption_leak(ab)
    if caps and body.strip():
        ab = body
        leaked.extend(caps)
        st["abstract_caption_stripped"] = len(caps)
    if ab != ab_before:
        doc["abstract"] = ab
        st["abstract_cleaned"] = True

    # 2) 섹션 제목 수리 + 재분류
    #    path 안에 None 이 섞여 들어와도(파서 사고) 감사 로그 조립에서 죽지 않게 한다.
    before_titles = [[str(p) if p else "" for p in (s.get("path") or [])]
                     for s in (doc.get("sections") or [])]
    before_types = [s.get("section_type", "other") for s in (doc.get("sections") or [])]
    repair_sections(doc, carry_forward=carry_forward)
    for i, sec in enumerate(doc.get("sections") or []):
        if list(sec.get("path") or []) != before_titles[i]:
            st["headings_fixed"] += 1
            if len(st["heading_samples"]) < 10:
                st["heading_samples"].append([" > ".join(before_titles[i]),
                                              " > ".join(sec.get("path") or [])])
        if sec.get("section_type") != before_types[i]:
            st["sections_reclassified"] += 1
            if len(st["reclass_samples"]) < 10:
                st["reclass_samples"].append([" > ".join(sec.get("path") or []),
                                              before_types[i], sec["section_type"]])

    # 3) 문단 수리 — 잡음 제거 → 캡션 분리 → 중복/빈 문단 제거
    seen: set[str] = set()
    for sec in doc.get("sections") or []:
        kept = []
        for para in sec.get("paragraphs") or []:
            raw = para.get("text") or ""
            text = clean_paragraph(raw)
            body, caps = strip_caption_leak(text)
            if caps:
                st["captions_extracted"] += len(caps)
                leaked.extend(caps)
                if len(st["caption_samples"]) < 5:
                    st["caption_samples"].append(caps[0][:120])
                text = body
            if text != raw:
                st["paragraphs_cleaned"] += 1
            if not text:
                if caps:
                    st["paragraphs_dropped_caption"] += 1
                else:
                    st["paragraphs_dropped_empty"] += 1
                continue
            key = text.casefold()
            if key in seen:
                st["paragraphs_dropped_dup"] += 1
                continue
            seen.add(key)
            para["text"] = text
            kept.append(para)
        sec["paragraphs"] = kept
    before_secs = len(doc.get("sections") or [])
    doc["sections"] = [s for s in (doc.get("sections") or []) if s.get("paragraphs")]
    st["sections_dropped"] = before_secs - len(doc["sections"])

    # 4) 그림·표 캡션 정리, 빈 표 제거
    for fig in doc.get("figures") or []:
        cap = clean_paragraph(fig.get("caption") or "")
        if cap != (fig.get("caption") or ""):
            fig["caption"] = cap
            st["figures_cleaned"] += 1
    kept_tables = []
    for tbl in doc.get("tables") or []:
        cap = clean_paragraph(tbl.get("caption") or "")
        # 표 본문은 **줄 단위**로 잡음을 걷는다(패턴의 \s* 가 줄바꿈을 먹어
        # 행이 병합되면 열 정렬이 깨지므로 행 구조를 절대 건드리지 않는다).
        md_lines = []
        for line in (tbl.get("markdown") or "").splitlines():
            fixed_line = line
            for rx in _JUNK_PATTERNS:
                fixed_line = rx.sub(" ", fixed_line)
            if fixed_line != line:            # 손댄 행만 공백 정리(불필요한 diff 방지)
                fixed_line = re.sub(r"[ \t]{2,}", " ", fixed_line).rstrip()
            md_lines.append(fixed_line)
        md = "\n".join(md_lines)
        if cap != (tbl.get("caption") or "") or md != (tbl.get("markdown") or ""):
            st["tables_cleaned"] += 1
        tbl["caption"], tbl["markdown"] = cap, md
        if _is_empty_table(tbl):
            st["tables_dropped"] += 1
            continue
        kept_tables.append(tbl)
    doc["tables"] = kept_tables

    # 5) 떼어낸 캡션을 그림/표로 편입(중복이면 버린다)
    for i, cap in enumerate(leaked, 1):
        if _attach_caption(doc, cap, i):
            st["captions_attached"] += 1

    st["changed"] = bool(
        st["headings_fixed"] or st["sections_reclassified"] or st["paragraphs_cleaned"]
        or st["paragraphs_dropped_dup"] or st["paragraphs_dropped_caption"]
        or st["paragraphs_dropped_empty"] or st["tables_dropped"]
        or st["tables_cleaned"] or st["figures_cleaned"] or st["abstract_cleaned"]
        or st["sections_dropped"])
    return doc, st


# ── 진입점 ──────────────────────────────────────────────────────────
def _sha1(path) -> str:
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def run(cfg: dict | None = None, dry_run: bool = False,
        config: dict | None = None) -> None:
    """normalized/*.json 전체를 수리한다(원본은 normalized_raw/ 에 백업).

    재실행 안전: 지난 실행에서 우리가 쓴 파일이면 **백업본을 입력으로** 다시
    수리하므로 반복 실행해도 결과가 누적 왜곡되지 않는다. 반대로 0~4단계가
    정본을 새로 만들었으면(해시가 원장과 다르면) 그 새 파일을 원본으로 보고
    백업을 갱신한다 — 이 판정이 없으면 재추출한 최신 본문을 낡은 백업으로
    덮어써 조용히 되돌아간다. dry_run=True 면 어떤 파일도 쓰지 않는다.

    주의: 판정 근거인 원장(normalized_raw/_textfix_state.index)을 지우면 모든
    파일이 '새 원본'으로 보이므로 백업이 현재(수리된) 내용으로 갱신된다.
    원본으로 되돌리려면 원장이 아니라 백업 디렉터리째 보존해야 한다.

    config= 는 옛 호출부 호환용 별칭이다(다른 단계 모듈과 동일하게 cfg 가 정식).
    """
    cfg = cfg or config or utils.load_config()
    opts = (cfg.get("textfix") or {}) if isinstance(cfg, dict) else {}
    carry_forward = bool(opts.get("carry_forward", True))

    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"
    raw_dir = work / (opts.get("backup_dir") or "normalized_raw")
    report_path = work / (opts.get("report") or "textfix_report.jsonl")
    # 파일명 → 우리가 쓴 결과의 sha1. 백업과 운명을 같이해야 하므로 백업 폴더에
    # 두되, 확장자를 .json 이 아니게 해 normalized_raw/*.json 글롭에 걸리지 않게 한다
    # (백업 디렉터리는 원본의 순수 사본으로 남아야 한다).
    state_path = raw_dir / "_textfix_state.index"

    files = sorted(norm_dir.glob("*.json"))
    log(f"[수리] 텍스트 결함 복구: {len(files)}편 @ {norm_dir}"
        + (" (DRY-RUN: 파일을 쓰지 않는다)" if dry_run else ""))
    if not files:
        log(f"        → 정본 문서가 없다. 0~4단계를 먼저 실행할 것: {norm_dir}")
        return
    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)

    try:
        state: dict[str, str] = utils.read_json(state_path) if state_path.exists() else {}
        if not isinstance(state, dict):
            state = {}
    except Exception:  # noqa: BLE001 — 원장이 깨졌으면 전부 '새 파일'로 취급(안전)
        state = {}
    new_state: dict[str, str] = {}

    reports: list[dict] = []
    n_changed = failed = n_refreshed = 0
    for i, src in enumerate(files, 1):
        try:
            bak = raw_dir / src.name
            if bak.exists() and state.get(src.name) == _sha1(src):
                data = utils.read_json(bak)          # 재실행: 항상 원본에서
            else:
                data = utils.read_json(src)          # 새로 생성된 정본 → 이게 원본
                if bak.exists():
                    n_refreshed += 1
                if not dry_run:
                    shutil.copy2(src, bak)           # 수리 전 원본 백업(갱신)
            fixed, st = fix_document(data, carry_forward=carry_forward)
            reports.append(st)
            if st["changed"]:
                n_changed += 1
                if not dry_run:
                    utils.write_json(src, fixed)
                log(f"  [{i}/{len(files)}] {st['paper_id']}: 제목 {st['headings_fixed']} · "
                    f"재분류 {st['sections_reclassified']} · 문단정리 {st['paragraphs_cleaned']} · "
                    f"중복 {st['paragraphs_dropped_dup']} · 캡션분리 {st['captions_extracted']} · "
                    f"빈표 {st['tables_dropped']}")
            if not dry_run:
                # 원장은 파일마다 즉시 갱신한다. 중간에 끊겨도 이미 처리한 파일이
                # '새 원본'으로 오인돼 무결한 백업이 덮여 쓰이는 일이 없다.
                new_state[src.name] = _sha1(src)
                utils.write_json(state_path, new_state)
        except Exception as e:  # noqa: BLE001 — 파일 단위 격리
            failed += 1
            if src.name in state:
                new_state[src.name] = state[src.name]   # 실패해도 원장은 유지
            log(f"  [{i}/{len(files)}] 수리 실패({src.name}): {type(e).__name__}: {e}")

    tot = {k: sum(int(r.get(k) or 0) for r in reports)
           for k in ("headings_fixed", "sections_reclassified", "paragraphs_cleaned",
                     "paragraphs_dropped_dup", "captions_extracted", "tables_dropped")}
    if not dry_run:
        utils.write_jsonl(report_path, reports)
        utils.write_json(state_path, new_state)
    if n_refreshed:
        log(f"[수리] 0~4단계가 새로 만든 정본 {n_refreshed}편 감지 → 백업 갱신")
    log(f"[수리] 완료 → {norm_dir} (수리 {n_changed}/{len(files)}, 실패 {failed})  "
        f"[제목 {tot['headings_fixed']} · 재분류 {tot['sections_reclassified']} · "
        f"문단 {tot['paragraphs_cleaned']} · 중복 {tot['paragraphs_dropped_dup']} · "
        f"캡션 {tot['captions_extracted']} · 빈표 {tot['tables_dropped']}]")
    log(f"[수리] 백업 {raw_dir} · 리포트 {report_path}")


if __name__ == "__main__":
    run()
