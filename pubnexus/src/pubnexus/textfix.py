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
import unicodedata
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


# ── D8 인코딩 깨짐 수리 ─────────────────────────────────────────────
# 근본 원인: Elsevier(3B2)·Wiley 조판이 = ± ≥ ≤ < > × · − ° * + 같은 기호를
# 서브셋 폰트의 **남는 슬롯**에 밀어 넣으면서 ToUnicode/글리프 이름을 갱신하지
# 않았다. 사람 눈에는 '≥75%' 로 보이지만 추출기는 슬롯 코드를 그대로 읽어
# '$75%' 를 내놓는다(폰트가 신고하는 글리프 이름도 'onequarter'/'thorn' 처럼
# 거짓이라 폰트 표로는 복원할 수 없다 — 렌더링만이 근거가 된다).
#
# 아래 매핑은 전부 **원본 PDF 를 렌더링해 육안으로 확인한 것만** 담았다.
#   문서                              PDF 실제 표기            → 추출 결과
#   10.1016/j.jaad.2018.06.016        ≥75% repigmentation       $75%
#   10.1016/j.jaad.2018.06.016        <50% as insufficient      \50%
#   10.1016/j.jaad.2018.06.004        BT >2 mm … BT ≤2 mm       BT [2 mm … BT #2 mm
#   10.1016/j.jaad.2020.03.009        86.1% ± 23.9%             86.1% 6 23.9%
#   10.1016/j.jid.2017.11.012         CD8⁺ … ratio = 1.99       CD8 þ … ratio ¼ 1.99
#   10.1016/j.jid.2023.02.031         affecting 0.5−1%          0.5e1%
#   10.1016/j.jcjo.2018.04.020        p < 0.001                 p o 0.001
#   10.1002/lsm.23048                 55.4 ± 28.7% … −2.862%    55.4 AE 28.7% … À2.862%
#   10.1002/lsm.22358                 ×200 … 94°C               Â200 … 948C
#   10.1080/09546634.2020.1817298     delta L*                  delta L Ã
#   10.1111/bjd.15560                 0·5–2% … 49·8 years       0Á5-2% … 49Á8
#   10.1016/S2468-2667(24)00026-4     24·7 (95% CI 24·3–25·2)   24•7 … 24•3-25•2
#   10.1016/j.jid.2023.07.007         aged <40 … or ≥40 years   <40 … !40
#   10.1111/j.1365-2133.2008.08937.x  LLD ≥ 10 mm … 62·5%       LLD ‡ 10 mm … 62AE5%
#   10.1111/jocd.12551                Antera 3D® system         Antera 3D â
#   10.1111/jocd.12338                Köln, Germany             K€ oln, Germany
#   10.1111/bjd.21054                 Behçet disease            Behc ßet disease
#   10.1016/j.jid.2023.02.031         Sjögren's syndrome        Sjo ̈gren's syndrome
#
# 설계 원칙은 기존 함수들과 같다: **오탐 0 우선.** 치환은 두 겹으로 막는다.
#   (1) 문서 게이트 — 정상 텍스트에 사실상 나올 수 없는 '조판 사고 서명'이
#       하나도 없는 문서에는 ASCII 계열 치환($ \ # [ 6 e o AE …)을 아예 열지
#       않는다. 그래야 진짜 통화 '$75', 진짜 인용 '[19]', 진짜 번호 '#1' 이
#       멀쩡한 문서에서 훼손되지 않는다.
#   (2) 자리 문맥 — 게이트를 통과해도 숫자·단위가 붙는 자리에서만 바꾼다.

# 아래 정규식의 '공백'은 전부 `[ \t]` 다(`\s` 가 아니다). `\s` 는 줄바꿈을 먹으므로
# 치환하면서 표 markdown 의 **행이 병합**되어 열 정렬이 무너진다(기존 표 잡음 제거가
# 줄 단위로 도는 것과 같은 이유다).
_SP = r"[ \t]"

# 게이트 서명. 정상 영문 의학 텍스트에서는 사실상 만들어질 수 없는 형태만 골랐다.
_MOJI_SIGNS: tuple[tuple[str, re.Pattern], ...] = (
    # 'P ¼ 0.22' — 분수 ¼ 가 변수와 숫자 사이에 오는 문장은 존재하지 않는다
    ("eq_quarter", re.compile(r"[\w)\]]\s*(?:1⁄4|¼)\s*[\d.,]")),
    # 본문 속 역슬래시+수('\50%', 'P \.001') — 정상 산문에는 역슬래시가 없다
    ("lt_backslash", re.compile(r"\\\s?\.?\d")),
    ("dot_aacute", re.compile(r"\d\s?Á\s?\d")),   # 49Á8
    ("minus_agrave", re.compile(r"À\s?[\d.]|\d\s?À")),
    ("pm_ae", re.compile(r"\d\s?AE\s?\d")),
    ("times_acirc", re.compile(r"Â\s?\d")),
    ("plus_thorn", re.compile(r"\d\s?þ|[a-z]þ")),
    ("star_atilde", re.compile(r"[A-Za-z]\s?Ã(?![\w])")),
    # '$75%' / '$ 60 years' — 통화로는 읽을 수 없는 자리의 달러 기호
    ("ge_dollar", re.compile(
        r"\$\s?\d+(?:\.\d+)?\s?(?:%|y\b|yr\b|years?\b|mo\b|months?\b|days?\b|"
        r"cm\b|mm\b|g\b|LNs?\b|colou?rs?\b|times\b|physician\b)", re.I)),
    ("pm_six", re.compile(r"\b(?:mean|age)\s6\s(?:SD|SEM)\b", re.I)),
    ("deg_eight", re.compile(r"\d8C\b")),
    # '!40 years' — 느낌표 바로 뒤에 숫자가 오는 영문 문장은 없다
    ("ge_excl", re.compile(r"!\d")),
    # '‡ 10 mm' — 각주 기호 뒤에 '수+단위'가 오는 조판은 없다
    ("ge_ddagger", re.compile(rf"‡{_SP}?\d+{_SP}?(?:%|mm\b|cm\b|y\b|years?\b)")),
    # 괄호 안 신뢰구간이 'e' 로 이어진 형태('(1.683e3.863)', '0.5e1%')
    ("dash_e", re.compile(r"\d\.\d+e\d+\.\d|\de\d+\s?%")),
)

# ── (1) 문맥만으로 확정되는 수리 — 게이트 없이 적용 ──────────────────
# 숫자 사이의 Á/• 는 영국식 소수점(middle dot)이 깨진 것 외의 해석이 없다.
_MJ_DOT_AACUTE = re.compile(rf"(?<=\d){_SP}?Á{_SP}?(?=\d)")   # 49Á8  → 49·8
_MJ_DOT_BULLET = re.compile(r"(?<=\d)•(?=\d)")                # 24•7  → 24·7
# U+2010 HYPHEN / U+2011 NB-HYPHEN: 유니코드상 '정상'이지만 ASCII '-' 로 찾으면
# 걸리지 않아 검색이 조용히 실패한다('large‐scale'). 표기를 통일한다.
_MJ_HYPHENS = re.compile(r"[‐‑]")
# 결합 기호가 앞 글자에서 떨어져 나온 형태: 'Sjo ̈gren' → 'Sjögren',
# 'Behc ̧et' → 'Behçet', 'Atas ‚' → 'Ataş'(U+201A 가 세디유 자리에 왔다).
# 조판이 í 를 **점 없는 i(U+0131) + 액센트**로 쌓는 경우도 같은 사고다
# ('Dı ́az Angulo' → 'Díaz Angulo', PDF 렌더링 확인). ı 는 NFC 로 합쳐지지
# 않으므로 밑글자를 보통 i 로 되돌린 뒤 합성한다.
_MJ_COMBINING = re.compile(rf"([A-Za-zıİ]){_SP}+([̀-ͯ])")
_DOTLESS = {"ı": "i", "İ": "I"}
_MJ_CEDILLA_LOW = re.compile(rf"([A-Za-z]){_SP}+‚")
# 같은 사고의 다른 슬롯: '€' 는 **뒤따르는 모음** 위의 움라우트,
# 'ß' 는 **앞 글자** 밑의 세디유가 떨어져 나온 것이다.
#   'Sj€ ogren' → 'Sjögren' · 'K€oln' → 'Köln' · 'Behc ßet' → 'Behçet'
# (실측: € 17건 전부 Sjögren/Köln, ß 9건 전부 Behçet — 진짜 유로·에스체트는 없다)
_MJ_UMLAUT_EURO = re.compile(rf"€{_SP}?([aeiouAEIOU])")
_MJ_CEDILLA_SS = re.compile(rf"([Cc]){_SP}?ß")
# 'â' 는 등록상표 ®. 제품명 뒤에 홀로 선 자리에서만 바꾼다 — 같은 코드가
# 10.1002/lsm.22358 에서는 'â-catenin'(= β-catenin)이라 하이픈이 붙으면 손대지
# 않는다(둘 다 PDF 렌더링으로 확인). 정상 단어 속 â('château')는 구조상 안 걸린다.
_MJ_REG_ACIRC = re.compile(rf"(?<=[A-Za-z0-9]){_SP}?â(?![-\w])")

# ── (2) 게이트가 열려야만 적용되는 수리 ─────────────────────────────
_MJ_EQ_QUARTER = re.compile(rf"{_SP}*(?:1⁄4|¼){_SP}*")  # P ¼ 0.22 → P = 0.22
# ≥ 는 두 슬롯으로 깨진다: JAAD 계열은 '$', JID/LSM 계열은 '!'.
_MJ_GE_DOLLAR = re.compile(rf"\${_SP}?(?=\d)")               # $75%     → ≥75%
_MJ_GE_EXCL = re.compile(rf"!{_SP}?(?=\d)")                  # !40 y    → ≥40 y
_MJ_LT_BACKSLASH = re.compile(rf"\\{_SP}?(?=[\d.])")         # \50%     → <50%
_MJ_PLUS_THORN = re.compile(rf"(?<=[0-9A-Za-z]){_SP}?þ")     # CD8 þ    → CD8+
_MJ_STAR_ATILDE = re.compile(rf"(?<=[A-Za-z]){_SP}?Ã(?![\w])")  # L Ã   → L*
_MJ_TIMES_ACIRC = re.compile(rf"Â(?={_SP}?\d)")              # Â200     → ×200
_MJ_DEG_EIGHT = re.compile(r"(?<=\d)8(?=C\b)")               # 948C     → 94°C
_MJ_LT_LETTER_O = re.compile(rf"\b([Pp]){_SP}+o{_SP}+(?=[.,]?\d)")  # p o .001
# ± : Wiley 계열은 'AE'(Æ 슬롯), Elsevier 계열은 '6' 으로 깨진다.
# 단, **같은 Æ 슬롯이 BJD 구권에서는 영국식 소수점(·)** 이다(둘 다 렌더링 확인):
#   10.1111/jocd.12338            '35.2±4.8'  → '35.2AE4.8'
#   10.1111/j.1365-2133.2008.08937.x '62·5%'  → '62AE5%'  · 'P = 0·006' → '0AE006'
# 가르는 근거: ± 는 이미 소수점이 찍힌 두 수 사이에 오고, · 는 소수점 자리 자체다.
_MJ_PM_AE_NUM = re.compile(rf"(?<=\d){_SP}*AE{_SP}*(?=\d)")
_MJ_AE_LEFT = re.compile(r"[\d.,·]+\Z")
_MJ_AE_RIGHT = re.compile(r"[\d.,·]+")
_MJ_PM_AE_SD = re.compile(rf"\bAE(?={_SP}+(?:SD|SEM)\b)")
_MJ_PM_AE_CELL = re.compile(rf"(?<=\|{_SP})AE(?={_SP}+\d)")
_MJ_PM_SIX_NUM = re.compile(rf"(?<=\d){_SP}6{_SP}(?=\d)")
_MJ_PM_SIX_SD = re.compile(
    rf"(?<=[A-Za-z(])({_SP}?)6({_SP})(?=SD\b|SEM\b|standard\b)")
# '6' 은 진짜 숫자이기도 하다. 표에서 공백으로 갈린 **수치 칸**('… 3.1 6 416,460 4.1',
# '1 2 3 4 5 6 7')이 '±' 로 둔갑하지 않도록 오른쪽 피연산자를 검사한다:
# 천단위 콤마가 있으면 개수 칸, 한 자리 숫자면 나열이다(실측 오탐 15건 전부 이 둘).
_MJ_SIX_RIGHT = re.compile(r"[\d,]+(?:\.\d+)?")
# − : Elsevier 조판에서 같은 대시 글리프가 'À' 또는 'e' 로 떨어진다.
#     범위('0.5−1%')와 음수('−2.862%') 모두 같은 글리프라 대상도 하나로 둔다.
_MJ_MINUS_AGRAVE = re.compile(
    rf"À(?={_SP}?[\d.])"           # À0.013 / À 6.531
    r"|(?<=[a-z])À(?=[A-Za-z])"    # factorÀa / gammaÀCXCL10
    r"|(?<=\()À(?=\))"             # (À)
    rf"|(?<=\d{_SP})À(?={_SP})")   # 1 À coefficient
_MJ_MINUS_E = re.compile(r"(?<=\d)e(?=\d)")
_MJ_MINUS_E_LEAD = re.compile(rf"(?<=[=<>≤≥]{_SP})e(?=[\d.])")
# ≤/≥ : '#' 와 '‡' 는 진짜 번호·각주 기호('#1 OR #2', 'analysis #1', '‡1,4-6',
#       'P value ‡')로도 쓰이므로 **뒤에 수 + 단위·백분율·표 칸 경계가 오는
#       자리**에서만 비교기호로 본다.
_MJ_LE_HASH = re.compile(rf"#{_SP}?(?=\d)")
_MJ_GE_DDAGGER = re.compile(rf"‡{_SP}?(?=\d)")   # LLD ‡ 10 mm → LLD ≥10 mm
_MJ_CMP_UNIT = re.compile(
    rf"[#‡]{_SP}?\d+(?:\.\d+)?{_SP}*"
    r"(?:%|\||\)|y\b|yr\b|years?\b|mo\b|months?\b|d\b|days?\b|"
    r"wk\b|weeks?\b|h\b|hours?\b|cm\b|mm\b|m\b|g\b|kg\b|mg\b|mL\b|L\b|LNs?\b|"
    r"points?\b|times\b|colou?rs?\b|cases?\b|lesions?\b)", re.I)
# > : '[' 는 인용 '[19]'·통계 '[95% confidence interval 1.10-3.17]' 로도 쓰인다.
#     **같은 줄 안에서 닫는 ']' 가 끝내 나오지 않을 때만** 비교기호로 본다.
#     창을 20자로 잡았더니 '[95% confidence interval …]'(닫는 괄호가 35자 뒤)이
#     전부 오탐으로 걸렸다 → 창을 60자로 넓히고 통계 괄호는 따로 제외한다.
#     '[.99'(= '>.99', P값)도 대상이라 소수점으로 시작하는 수까지 받는다.
_MJ_GT_BRACKET = re.compile(
    rf"\[{_SP}?(?=\.?\d)(?![^\]\n]{{0,60}}\])"
    rf"(?!{_SP}?\d+{_SP}?%?{_SP}*(?:confidence|CI\b|IQR\b))")

# ── (2-a) 문서 게이트를 통과해도 남는 오탐 두 가지 ────────────────────
# 게이트는 **문서 단위**라, 한 문서 안에 '깨진 ≥'와 '진짜 달러'가 함께 있으면
# 막지 못한다. 실측으로 확인된 사고 두 건을 자리 문맥으로 따로 잘라낸다.
#
#  (i) 10.1016/j.jaad.2020.09.088 (지불의사액 연구) — 같은 문서 안에
#      본문 '($2000 for vitiligo, $1000 for psoriasis)' = **진짜 미국 달러**,
#      표 '| $60 | 103 (19.4) |' = 깨진 '≥60'. 둘 다 PDF 렌더링으로 확인했다.
#      가르는 근거: 비교기호 '≥N' 뒤에는 세는 대상(단위·명사·표 칸)이 오고
#      전치사가 오지 않는다. 금액 '$N' 은 'for/per …' 로 이어진다.
#      보조로 ±90자 안의 금액 어휘(WTP·cost·price…)도 본다.
#      실측: 코퍼스의 '$숫자' 85자리 중 이 두 조건에 걸리는 것은 그 4자리뿐이다.
#      ('to'·'in' 은 '≥5 to 10 y' 같은 정상 표현과 겹칠 수 있어 넣지 않는다 —
#       금액 4자리는 'for' 만으로도 전부 걸리고, ±90자 어휘 그물이 이중으로 막는다.)
_MJ_MONEY_TAIL = re.compile(rf"\$[ \t]?[\d,]+(?:\.\d+)?{_SP}+(?:for|per)\b")
_MJ_MONEY_SEP = re.compile(r"\$[ \t]?\d{1,3}(?:,\d{3})+")   # $1,200 = 금액
_MJ_MONEY_WORD = re.compile(
    r"\b(?:WTP|willing(?:ness)?|cost|costs|costed|price|prices|priced|pay|paid|"
    r"payment|USD|dollars?|expenditure|reimburse\w*|out-of-pocket)\b", re.I)
#
#  (ii) 10.1111/jdv.16976 — 표 각주 '‡43.9% (n=189/431), and 43.4% … achieved'
#       를 '≥43.9%' 로 바꿔 **원문에 없는 주장**을 만들었다(PDF 확인: 위첨자
#       각주 기호다). 비교기호는 같은 절 안에 **왼쪽 피연산자**가 있어야 한다.
#       문장 끝('.', ')', ']') 직후나 필드 맨 앞에 선 '#'·'‡' 는 각주로 본다.
#       (표 칸 첫머리 '| ‡10 mm |' 는 왼쪽이 '|' 라 그대로 통과한다.)
_MJ_FOOTNOTE_LEFT = ".)]\n"


def _money_here(s: str, i: int) -> bool:
    """s[i] 의 '$' 가 통화로 읽히는 자리인지."""
    if _MJ_MONEY_TAIL.match(s, i) or _MJ_MONEY_SEP.match(s, i):
        return True
    return bool(_MJ_MONEY_WORD.search(s[max(0, i - 90):i + 90]))


def _footnote_here(s: str, i: int) -> bool:
    """s[i] 의 '#'·'‡' 가 왼쪽 피연산자 없는 각주 기호 자리인지."""
    j = i - 1
    while j >= 0 and s[j] in " \t":
        j -= 1
    return j < 0 or s[j] in _MJ_FOOTNOTE_LEFT


def encoding_profile(doc: dict) -> frozenset[str]:
    """문서 전체 본문을 훑어 '조판 사고 서명'을 찾는다(게이트 판정).

    반환된 집합이 비어 있지 않으면 그 문서는 서브셋 폰트 오매핑을 겪은 것이라
    보고 ASCII 계열 치환을 연다. 비어 있으면 문맥만으로 확정되는 수리
    (숫자 사이 Á/•, U+2010 하이픈, 떨어진 결합기호)만 적용한다.
    """
    found: set[str] = set()
    for text in _iter_doc_text(doc):
        if not text:
            continue
        for name, rx in _MOJI_SIGNS:
            if name not in found and rx.search(text):
                found.add(name)
        if len(found) == len(_MOJI_SIGNS):
            break
    return frozenset(found)


def _iter_doc_text(doc: dict):
    """수리 대상 문자열을 흘려보낸다(초록·제목·문단·캡션·표 본문)."""
    yield doc.get("abstract") or ""
    for sec in doc.get("sections") or []:
        for p in sec.get("path") or []:
            yield p or ""
        for para in sec.get("paragraphs") or []:
            yield para.get("text") or ""
    for fig in doc.get("figures") or []:
        yield fig.get("caption") or ""
    for tbl in doc.get("tables") or []:
        yield tbl.get("caption") or ""
        yield tbl.get("markdown") or ""


def _compose_marks(s: str) -> str:
    """앞 글자에서 떨어진 결합 기호를 다시 붙인다('Sjo ̈gren' → 'Sjögren')."""
    def repl(m: re.Match) -> str:
        raw, mark = m.group(1), m.group(2)
        base = _DOTLESS.get(raw)
        if base is not None:
            # 점 없는 i 는 조합형 글자가 있을 때만 i 로 되돌린다. 안 그러면
            # 진짜 터키어 'ı' 를 'i' 로 바꿔 놓고 결합기호만 붙이게 된다.
            out = unicodedata.normalize("NFC", base + mark)
            return out if len(out) == 1 else raw + mark
        return unicodedata.normalize("NFC", raw + mark)
    s = _MJ_COMBINING.sub(repl, s)
    s = _MJ_CEDILLA_LOW.sub(
        lambda m: unicodedata.normalize("NFC", m.group(1) + "̧"), s)
    s = _MJ_UMLAUT_EURO.sub(
        lambda m: unicodedata.normalize("NFC", m.group(1) + "̈"), s)
    return _MJ_CEDILLA_SS.sub(
        lambda m: unicodedata.normalize("NFC", m.group(1) + "̧"), s)


def _pm_ae(m: re.Match) -> str:
    """'AE'(Æ 슬롯)가 ± 인지 ·(영국식 소수점)인지 양쪽 피연산자로 가른다.

    '35.2AE4.8' 처럼 양쪽이 이미 소수인 경우는 ±(평균±표준편차)이고,
    '62AE5%'·'0AE006' 처럼 소수점이 하나도 없으면 그 자리가 소수점이다.
    """
    s = m.string
    lm = _MJ_AE_LEFT.search(s[:m.start()])
    rm = _MJ_AE_RIGHT.match(s, m.end())
    both = (lm.group(0) if lm else "") + (rm.group(0) if rm else "")
    return " ± " if ("." in both or "·" in both) else "·"


def _pm_six(m: re.Match) -> str:
    """'6' 을 ± 로 볼지 판정. 오른쪽이 개수 칸/나열이면 원문을 유지한다."""
    tok = _MJ_SIX_RIGHT.match(m.string, m.end())
    right = tok.group(0) if tok else ""
    if "." in right:
        return " ± "                       # '15.4' — 표준편차 표기
    if "," in right or len(right) <= 1:
        return m.group(0)                  # '416,460' / '7' — 수치 칸·나열
    return " ± "                           # '1831'(IgE 평균±SD 처럼 정수 SD)


def _minus_e(m: re.Match) -> str:
    """숫자 사이 'e' 를 대시로 볼지 판정. 해시·식별자 안이면 손대지 않는다.

    '0.5e1%'/'(1.683e3.863)' 는 대시지만, 'dbc21e882f7a'(첨부파일 해시)의 e 는
    글자다. 숫자런 바깥에 영문자가 붙어 있으면 후자로 보고 원문을 유지한다.
    """
    s, i = m.string, m.start()
    j = i - 1
    while j >= 0 and (s[j].isdigit() or s[j] == "."):
        j -= 1
    k = i + 1
    while k < len(s) and (s[k].isdigit() or s[k] == "."):
        k += 1
    if (j >= 0 and s[j].isalpha()) or (k < len(s) and s[k].isalpha()):
        return "e"
    return "−"


def fix_encoding(s: str, typeset: bool = False) -> str:
    """한 문자열의 인코딩 깨짐을 복구한다. 멱등(여러 번 적용해도 같은 결과).

    typeset=False 면 문맥만으로 확정되는 수리만 한다. typeset=True 는
    `encoding_profile()` 이 조판 사고 서명을 찾은 문서에서만 넘겨야 한다 —
    그 문서 밖에서 켜면 진짜 통화 '$75'·인용 '[19]'·번호 '#1' 을 훼손한다.
    게이트가 열린 문서 안에서도 진짜 금액('$2000 for vitiligo')과 각주
    기호('… data). ‡43.9%')는 자리 문맥으로 따로 걸러낸다.
    """
    if not s:
        return s or ""
    # (1) 게이트 없이
    s = _MJ_DOT_AACUTE.sub("·", s)
    s = _MJ_DOT_BULLET.sub("·", s)
    s = _MJ_HYPHENS.sub("-", s)
    s = _MJ_REG_ACIRC.sub("®", s)
    s = _compose_marks(s)
    if not typeset:
        return s
    # (2) 게이트 통과 문서만 — '=' 부터 복원해야 'X = e0.254'(선행 마이너스)가 산다
    s = _MJ_EQ_QUARTER.sub(" = ", s)
    s = _MJ_GE_DOLLAR.sub(
        lambda m: m.group(0) if _money_here(m.string, m.start()) else "≥", s)
    s = _MJ_GE_EXCL.sub("≥", s)
    s = _MJ_LT_BACKSLASH.sub("<", s)
    s = _MJ_LE_HASH.sub(
        lambda m: "≤" if (_MJ_CMP_UNIT.match(m.string, m.start())
                          and not _footnote_here(m.string, m.start()))
        else m.group(0), s)
    s = _MJ_GE_DDAGGER.sub(
        lambda m: "≥" if (_MJ_CMP_UNIT.match(m.string, m.start())
                          and not _footnote_here(m.string, m.start()))
        else m.group(0), s)
    s = _MJ_GT_BRACKET.sub(">", s)
    s = _MJ_PM_AE_NUM.sub(_pm_ae, s)
    s = _MJ_PM_AE_SD.sub("±", s)
    s = _MJ_PM_AE_CELL.sub("±", s)
    s = _MJ_PM_SIX_NUM.sub(_pm_six, s)
    s = _MJ_PM_SIX_SD.sub(r"\1±\2", s)
    s = _MJ_PLUS_THORN.sub("+", s)
    s = _MJ_STAR_ATILDE.sub("*", s)
    s = _MJ_TIMES_ACIRC.sub("×", s)
    s = _MJ_DEG_EIGHT.sub("°", s)
    s = _MJ_MINUS_AGRAVE.sub("−", s)
    s = _MJ_MINUS_E.sub(_minus_e, s)
    s = _MJ_MINUS_E_LEAD.sub("−", s)
    s = _MJ_LT_LETTER_O.sub(r"\1 < ", s)
    return s


def repair_encoding(doc: dict) -> tuple[int, list[str]]:
    """문서 dict 의 본문 문자열을 제자리에서 인코딩 수리한다.

    반환: (바뀐 문자 수, 감사용 before→after 표본 리스트).
    문자 수는 '수리로 사라진/생긴 글자'가 아니라 **치환된 자리 수**의 근사로,
    길이 변화와 무관하게 세도록 원문/결과를 자리별로 비교하지 않고
    치환 규칙이 실제로 문자열을 바꾼 횟수를 세어 보고한다.
    """
    profile = encoding_profile(doc)
    typeset = bool(profile)
    n = 0
    samples: list[str] = []

    def fix(text: str) -> str:
        nonlocal n
        if not text:
            return text
        out = fix_encoding(text, typeset=typeset)
        if out != text:
            n += _count_diff(text, out)
            if len(samples) < 8:
                samples.append(_diff_sample(text, out))
        return out

    doc["abstract"] = fix(doc.get("abstract") or "")
    for sec in doc.get("sections") or []:
        if sec.get("path"):
            sec["path"] = [fix(p or "") for p in sec["path"]]
        for para in sec.get("paragraphs") or []:
            para["text"] = fix(para.get("text") or "")
    for fig in doc.get("figures") or []:
        fig["caption"] = fix(fig.get("caption") or "")
    for tbl in doc.get("tables") or []:
        tbl["caption"] = fix(tbl.get("caption") or "")
        tbl["markdown"] = fix(tbl.get("markdown") or "")
    return n, samples


# 보고용 잔존 계측기 — 수리 대상으로 확인된 자리만 센다(미확인 문자는 세지 않는다).
_MOJI_ALL = re.compile(
    r"[ÀÁÂÃþ⁄¼‐‑€]"
    rf"|(?<=\d)•(?=\d)|\${_SP}?\d|!\d|\\{_SP}?[\d.]|[A-Za-z]{_SP}+[̀-ͯ]"
    rf"|[Cc]{_SP}?ß|(?<=[A-Za-z0-9]){_SP}?â(?![-\w])")


def count_mojibake(doc: dict) -> int:
    """문서에 남아 있는 '깨진 문자' 수(수리 전후 비교 보고용).

    수리하지 않기로 **판정한** 자리(진짜 금액 '$2000 for …')는 세지 않는다.
    그러지 않으면 수리 후 계측이 실제보다 나쁘게 나와 보고를 오도한다.
    """
    n = 0
    for t in _iter_doc_text(doc):
        if not t:
            continue
        for m in _MOJI_ALL.finditer(t):
            if m.group(0).startswith("$") and _money_here(t, m.start()):
                continue
            n += 1
    return n


def _count_diff(before: str, after: str) -> int:
    """치환된 자리 수 근사 — 바뀐 구간 길이의 합.

    difflib 는 문자열 길이의 **제곱**에 비례한다. 표 markdown 은 수십만 자가
    되기도 해서(실측: 2000행 표 1.4 MB) 통째로 비교하면 계측 하나가 수 분을
    먹는다. 치환 규칙은 전부 `[ \\t]` 만 소비하고 줄바꿈은 건드리지 않으므로
    **줄 수가 보존된다** — 줄끼리 짝지어 짧은 조각만 비교하면 선형에 가깝다.
    (혹시라도 줄 수가 달라지면 통째 비교로 되돌아간다.)
    """
    import difflib

    def span(a: str, b: str) -> int:
        if a == b:
            return 0
        # 공통 접두/접미를 먼저 떼어내 difflib 입력을 최소화한다
        i, na, nb = 0, len(a), len(b)
        while i < na and i < nb and a[i] == b[i]:
            i += 1
        j = 0
        while j < na - i and j < nb - i and a[na - 1 - j] == b[nb - 1 - j]:
            j += 1
        a, b = a[i:na - j], b[i:nb - j]
        if len(a) > 4000 or len(b) > 4000:      # 최악의 경우 안전판
            return max(len(a), len(b))
        return sum(max(i2 - i1, j2 - j1)
                   for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                       None, a, b, autojunk=False).get_opcodes() if tag != "equal")

    la, lb = before.split("\n"), after.split("\n")
    if len(la) != len(lb):
        return span(before, after)
    return sum(span(x, y) for x, y in zip(la, lb))


def _diff_sample(before: str, after: str) -> str:
    """바뀐 첫 자리 주변을 'before → after' 한 줄로 요약(감사 로그용)."""
    i = 0
    while i < min(len(before), len(after)) and before[i] == after[i]:
        i += 1
    a, b = max(0, i - 30), i + 30
    return f"{before[a:b]!r} → {after[a:min(len(after), b)]!r}"


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

    **캐리포워드는 IMRaD 논문에서만 쓴다.** 원저는 소절이 상위 절에 속하므로
    이어받는 것이 옳지만, 종설(Nature Reviews Primer 등)은 Management·Diagnosis·
    Quality of life 처럼 대등한 주제 절이 나열될 뿐이라 이어받으면 문서 전량이
    앞 절의 타입으로 물든다(실측: 10.1038/s41572-025-00670-x 43개 절 중 26개가
    intro 로 오분류). 그래서 제목만으로 직접 분류된 타입이 2종 이상이고 그중
    methods 나 results 가 있을 때만 이어받는다.
    """
    direct = {_classify_path([clean_heading(p) for p in (s.get("path") or [])])
              for s in doc.get("sections") or []}
    direct.discard("other")
    direct.discard("back")
    if not ({"methods", "results"} & direct and len(direct) >= 2):
        carry_forward = False

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
        "encoding_chars_fixed": 0, "encoding_profile": [],
        "heading_samples": [], "reclass_samples": [], "caption_samples": [],
        "encoding_samples": [],
    }
    leaked: list[str] = []

    # 0) 인코딩 깨짐 복구 — 뒤 단계(자간 복원·캡션 분리·중복 판정)가 모두
    #    문자열 비교에 기대므로 **가장 먼저** 글자를 바로잡아야 한다.
    st["encoding_profile"] = sorted(encoding_profile(doc))
    n_enc, enc_samples = repair_encoding(doc)
    st["encoding_chars_fixed"] = n_enc
    st["encoding_samples"] = enc_samples

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
        or st["sections_dropped"] or st["encoding_chars_fixed"])
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
