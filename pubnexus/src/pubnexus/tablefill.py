"""4.7단계 — 정본의 **표**를 원본 PDF 텍스트층에서 좌표로 바로잡는다.

이 모듈이 하는 일은 네 가지다(자세한 것은 `fill_document` 참고).
  1. **관문** `fake_table_reason` — 표가 아닌 것을 `tables[]` 에서 뺀다.
     참고문헌 목록·CAPSULE SUMMARY/약어 상자·워터마크·1열 산문·그림.
     캡션이 'Table N' 이거나 PDF 안의 진짜 표 캡션에 그 문구가 들어 있으면
     **절대 지우지 않는다** — 망가진 진짜 표를 지우는 것이 최악이다.
  2. **채움** — markdown 이 빈 표를 복원한다(원래 이 모듈의 유일한 일이었다).
  3. **수리** `better_reason` — 이미 markdown 이 있는 표도 전부 재추출해 보고,
     **기존 값을 하나도 잃지 않을 때만** 갈아 끼운다. 구조만 봐서는 멀쩡해
     보이는 결함(열 밀림·셀 병합·전치)이 있어 '구조 검사 실패'를 조건으로
     삼을 수 없기 때문이다. 대신 받아들이는 조건을 좁게 잡았다.
  4. **발굴** `discover_tables` — GROBID 가 **아예 못 찾은** 표를 PDF 에서
     새로 만든다. 윈도우 GROBID 는 번들 pdfalto 가 0.1 이라 표 검출이
     리눅스보다 60% 나쁘다(같은 134편에서 TEI 표 231 → 93). 실측 최악은
     10.1002/jso.23438 — PDF 에 TABLE I·II·III 가 있는데 GROBID 표 0개다.
     후보는 두 곳에서 모은다: 이 모듈의 캡션 훑기와 `pdf_fallback` 의 표 찾기.
     **소속을 확인할 수 없으면 하나도 만들지 않는다**(아래 오염 항목).

표마다 `footnote`(표 각주)와 `pdf_span`(지면 좌표)도 담는다. 각주는 지금까지
어디에도 담기지 않아 본문 꼬리로 새거나 통째로 사라졌다.

**이웃 논문 오염**이 이 모듈의 가장 큰 위험이다. 레터·단신은 한 지면에 여러
편이 실려 옆 논문 표를 가져오기 쉽다(실증: figtab.py 가
10.1016/j.jaad.2016.05.022 에 이웃 레터의 Table II 를 붙였고 그래서 연결되지
못했다). 발굴 경로는 `boundary.py` 로 구간을 받아
  · PDF 에 논문이 하나뿐이거나(가져올 이웃이 없다)
  · boundary 가 내 구간을 확정했을 때만 열고, 캡션마다 `owner()` 로 거른다.
판정기를 못 얻으면 발굴을 **켜지 않는다**(reason="no_boundary").

── 아래는 채움 경로(2)의 원래 설명이다 ──────────────────────────────

증상: 정본에 캡션만 있고 markdown 이 빈 표가 있다. 실측 167편 · 표 299개 중
37개(10편)가 여기 해당한다. 대부분(34개) source="graphic" 인 pmc_xml 문서로,
PMC 변환본이 `<table-wrap>` 안에 `<table>` 마크업 없이 `<graphic href=…>` 만
담기 때문이다(표가 **이미지**다). 원 데이터는 PDF 텍스트층에 그대로 있다.

`page.find_tables()` 는 이 코퍼스에서 못 쓴다(pdf_fallback.py 상단 주석의 실측).
그래서 좌표로 직접 재구성한다. 핵심은 **표의 경계를 글자가 아니라 괘선으로
잡는다**는 것이다 — 이 학술지들(Ann Dermatol·JKMS)은 표의 위/머리행 아래/아래를
가로 괘선으로 긋고, 그 선은 벡터 그래픽으로 PDF 에 남아 있다. 선이 경계를
주면 표 각주·다음 문단·옆단 본문이 애초에 들어올 수 없다.

절차
  1. 캡션 문자열로 위치를 찾는다. 공백·구두점·리가처·발음기호를 모두 버린
     영숫자 키로 비교하므로 'Table 1. Level of…' 와 'Table 1 Level of…' 가 같다.
     **블록 머리에서 시작하는 매치만** 인정한다(본문 중 'as shown in Table 1' 배제).
  2. 캡션 아래 첫 괘선(46pt 이내)부터 같은 x 범위의 괘선들을 모아 표 영역을 만든다.
     다음 캡션 줄이 나오면 거기서 끊는다(한 지면에 표가 둘 이상인 경우).
     괘선이 path 하나로 묶여 나오기도 해 **선분 단위**까지 내려가 모은다.
  3. 낱말은 **보이는 글자**로만 만든다 — 이 학술지들은 소수점 정렬을 흰 글자
     '0'·'.' 로 하므로 색을 보지 않으면 '8 (3.9)' 가 '08 (3.9)0.0..' 이 된다.
  4. 낱말을 y 로 묶어 행을, **본문 행들의 세로 빈 띠**로 열 경계를 만든다.
     머리행은 열을 걸치는 일이 잦아(가운데 정렬·병합) 열 추정에서 뺀다.
  5. 셀은 **열 경계가 낱말 사이에 놓일 때만** 끊는다(간격 임계값은 조판마다
     달라 못 쓴다). 조판상 다른 줄의 낱말은 절대 한 셀로 묶지 않는다.
  6. 머리행은 조판상의 줄 단위로 처리해 열별로 세로 병합한다('Level of' +
     'evidence' → 'Level of evidence'). 여러 열을 덮는 머리글은 덮은 열 전부에
     붙인다. 줄 끝 분철('Recommenda-' + 'tion')은 textfix 의 정지목록으로 되붙인다.

**오염이 누락보다 나쁘다.** 다음 관문을 모두 통과해야 표로 인정한다.
  · 캡션이 **블록 머리**에서 30자(정규화 키 기준) 이상 일치 — 같은 지면 다른
    논문의 표를 끌어오지 못한다(레터는 한 페이지에 여러 편이 실린다).
    실측 근거: 이 코퍼스의 빈 표 37개 중 PDF 에서 찾히는 35개는 전부 블록
    첫 줄에서 시작했고, 블록 중간에서 걸린 1개는 GROBID 가 캡션으로 잘못
    잡아 온 **본문 문단의 꼬리**였다.
  · **괘선이 없으면 아예 시도하지 않는다**(reason="no_rules"). 캡션 아래를
    글자만으로 잘라 내는 안전망을 뒀다가 뺐다 — 실측 34개 복원에 한 건도
    기여하지 못하면서 문단을 표로 만들 통로만 열어 두기 때문이다.
  · 열 2 이상 · 행 2 이상 · 2줄 이상이 2칸 이상 채워짐 · 채움률 0.25 이상
  · 짧은 셀(60자 이하) 비율 0.5 이상, 긴 문장형 셀 비율 0.5 미만
  · 어느 열의 셀들이 한 문단처럼 이어지지 않을 것(_flows_as_prose)
  하나라도 실패하면 **빈 채로 두고** 사유를 통계에 남긴다.

이미 markdown 이 있는 표는 손대지 않는다. 회전 조판(세로 표)은 좌표를 90° 돌려
같은 절차를 태운다(가로 괘선↔세로 괘선도 함께 뒤집는다) — 이 코퍼스에는
회전된 표가 없어 이 경로는 실측되지 않았다.

실측(정본 167편 · 표 299개 · 빈 표 37개)
  · 복원 34 / 실패 3 — caption_not_found 2(둘 다 GROBID 가 본문을 캡션으로
    잘못 잡은 것), no_words 1(PDF 에서도 그 표가 이미지다).
  · 복원 34개 중 10개를 PDF 렌더와 눈으로 대조 — 행·열 수와 셀 값 모두 일치.
  · 오탐 검사: 그림 캡션 239개 + 본문 문단 909개를 캡션으로 먹여도 **문단이
    표로 둔갑한 건 0건**(표가 아닌 캡션 아래에 진짜 표가 있어 그 표를 가져온
    경우가 5건 — 표 캡션에는 해당하지 않는 경로다).
  · 원래 내용이 있던 표 262개 전부 무변경. 두 번 돌려도 결과 동일(멱등).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from . import utils
from .utils import log

# ── 튜닝 상수(pt). 이 코퍼스 실측으로 정했다 ────────────────────────
CAP_TO_RULE_MAX = 46.0     # 캡션 아래 첫 괘선까지 허용 거리
RULE_THICK_MAX = 2.5       # 이보다 두꺼우면 선이 아니라 상자
RULE_MIN_LEN = 12.0        # 이보다 짧으면 괘선으로 보지 않는다
RULE_Y_TOL = 1.2           # 같은 y 로 볼 오차
RULE_X_JOIN = 4.0          # 토막난 선분을 이을 x 간격(JKMS 는 열마다 끊어 그린다)
RULE_DEDUP_Y = 3.0         # 겹줄(이중선)을 한 줄로 본다
RULE_X0_TOL = 6.0          # 같은 표의 괘선으로 볼 x0 오차
RULE_X1_TOL = 16.0         # 같은 표의 괘선으로 볼 x1 오차
RULE_CAP_X0_TOL = 26.0     # 괘선 x0 가 캡션 x0 보다 이만큼 오른쪽까지는 같은 표
RULE_CAP_OVERLAP = 25.0    # 괘선이 캡션 칸과 가로로 겹쳐야 할 최소 길이
MIN_COL_GAP = 4.0          # 열 경계로 인정할 빈 세로 띠의 최소 폭
CHAR_BREAK = 1.2           # 공백 글자 없이 이만큼 벌어지면 낱말을 끊는다
CAP_MIN_KEY = 30           # 캡션 대조에 쓸 정규화 키 최소 길이
FOOTER_ZONE = 26.0         # 페이지 아래 이만큼은 꼬리말 영역(괘선 후보에서 제외).
                           #   넉넉히(55pt) 잡았더니 페이지 밑까지 내려오는 표의
                           #   마지막 괘선이 잘려 본문 5행을 잃었다(실측: 표 5).

# 캡션 줄머리 판정(표 영역을 어디서 끊을지 결정)
_CAP_START = re.compile(
    r"^\s*\(?\s*(?:table|tab\.|fig(?:ure)?\.?|chart|scheme|appendix|"
    r"supplementary|supplemental)\s*(?:[ivxlIVXL]+|\d+|[A-Z]\d?)\s*[.:)\-–—]?\s",
    re.I)
# 표 영역을 끊는 절 제목 — 표 아래 본문이 다시 시작하면 그 아래 괘선은 남의 것이다
_HEADING = re.compile(
    r"^\s*(?:abstract|introduction|materials?|methods?|results?|discussion|"
    r"conclusions?|references?|acknowledg|conflict|funding|supplementary)\b",
    re.I)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}
# 눈에 보이지 않는 자릿수 맞춤 채움('94 (45.6)....' 의 '....' 는 흰 글자다).
# 실측: Ann Dermatol 표는 소수점 정렬을 흰색 '0'·'.' 로 한다 — 그대로 뽑으면
# '8 (3.9)' 가 '08 (3.9)0.0..' 가 된다. 색이 흰색이면서 채움 문자뿐일 때만 버린다.
_FILLER = re.compile(r"^[0.\s\-–—]+$")
_WHITE = 0xFFFFFF
# 글꼴 인코딩이 깨져 **읽지 못한 한 글자** 자리. 지우면 양옆 숫자가 붙어
# 거짓 값이 되므로(위 _cell_text 주석) 이 표식을 남긴다.
UNREADABLE = "�"


# ── 문자열 정규화 ───────────────────────────────────────────────────
def _key(s: str) -> str:
    """공백·구두점·리가처·발음기호·자간을 모두 버린 대조 키(영숫자 소문자)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def _cell_text(s: str) -> str:
    """셀 문자열 정리 — markdown 표를 깨뜨리는 문자와 제어문자를 막는다.

    제어문자가 나오는 이유: ToUnicode 표가 없는 서브셋 글꼴은 기호를 슬롯
    코드 그대로 내놓는다(실측: 표 5 의 '1998∼2004' 가 U+0002).

    **그냥 지우면 안 된다.** 지우는 순간 양옆이 들러붙어 *없던 수*가 만들어진다.
    실측(눈으로 확인): 10.1111/bjd.15560 3쪽 Table 1 은 렌더링상 '292 (26·0)'
    인데 텍스트층은 '292 (26\\x010)' 이다. 제어문자를 지우면 '292 (260)' —
    26.0% 가 260 이 된다. 임상 표에서 이건 빈칸보다 훨씬 해롭다.
    슬롯의 뜻은 문서마다 다르므로(같은 U+0001 이 JAAD 에선 '©',
    10.1016/j.jaad.2013.05.012 에선 'μ', BJD 에선 '·') 추측해서 채우지 않는다.
    양옆이 영숫자일 때만 U+FFFD 를 남겨 **읽지 못했다는 사실**을 보존한다.
    그 표는 info['unreadable'] 로 세어, 수리 판정에서 기존 값을 이기지 못하게 한다.
    """
    s = utils.norm_text(s)
    out: list[str] = []
    for i, c in enumerate(s):
        if unicodedata.category(c) in ("Cc", "Cf"):
            prev = out[-1] if out else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if prev.isalnum() and nxt.isalnum():
                out.append(UNREADABLE)
            continue
        out.append(c)
    s = re.sub(r"\s{2,}", " ", "".join(out))
    return s.replace("|", r"\|").strip()


def table_number(caption: str) -> str | None:
    """'Table IV. …' → '4'. 이어짐(Continued) 표를 찾을 때 쓴다."""
    m = re.match(r"\s*\(?\s*tab(?:le|\.)?\s*([0-9]{1,3}|[ivxlIVXL]{1,5})\b",
                 caption or "", re.I)
    if not m:
        return None
    tok = m.group(1).lower()
    return tok if tok.isdigit() else (str(_ROMAN[tok]) if tok in _ROMAN else None)


# ── 좌표 변환(회전 조판) ────────────────────────────────────────────
def _rot(box: tuple[float, float, float, float], mode: int
         ) -> tuple[float, float, float, float]:
    """mode 0=그대로, 1=(x,y)->(-y,x) [dir=(0,-1)], 2=(x,y)->(y,-x) [dir=(0,1)]."""
    x0, y0, x1, y1 = box
    if mode == 0:
        return (x0, y0, x1, y1)
    pts = ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
    if mode == 1:
        q = [(-y, x) for x, y in pts]
    else:
        q = [(y, -x) for x, y in pts]
    xs = [p[0] for p in q]
    ys = [p[1] for p in q]
    return (min(xs), min(ys), max(xs), max(ys))


# ── 페이지 캐시: 줄·낱말·괘선을 한 번만 뽑는다 ───────────────────────
class _Page:
    """한 페이지의 줄/낱말/괘선. 문서에 표가 13개면 같은 페이지를 여러 번 본다."""

    def __init__(self, page):
        self.page = page
        self.rect = page.rect
        self.blocks: list[list[dict]] = []      # 블록 → 줄 목록
        self.lines: list[dict] = []
        raw = page.get_text("dict")
        for b in raw.get("blocks", ()):
            if b.get("type") != 0:
                continue
            bl: list[dict] = []
            for ln in b.get("lines", ()):
                txt = "".join(sp.get("text", "") for sp in ln.get("spans", ()))
                if not txt.strip():
                    continue
                d = tuple(ln.get("dir") or (1.0, 0.0))
                # 줄 bbox 높이를 글자 크기로 쓰면 안 된다 — 위첨자 하나가
                # bbox 를 부풀린다(실측: '1Mean with its range.' 는 본문 7.5pt
                # 인데 위첨자 때문에 bbox 높이가 9.2pt 다). 글자 수가 가장
                # 많은 span 의 size 를 그 줄의 크기로 삼는다.
                spans = [sp for sp in ln.get("spans", ()) if sp.get("text")]
                size = 0.0
                if spans:
                    size = max(spans, key=lambda sp: len(sp.get("text") or ""))\
                        .get("size", 0.0)
                rec = {"text": txt, "bbox": tuple(ln["bbox"]), "dir": d,
                       "size": float(size or 0.0), "key": _key(txt)}
                bl.append(rec)
                self.lines.append(rec)
            if bl:
                self.blocks.append(bl)
        self.words = _visible_words(page)
        self._segs = self._segments()

    # 얇고 긴 그래픽 = 괘선. 가로/세로를 모두 모아 둔다(회전 조판 대비).
    def _segments(self) -> list[tuple[str, float, float, float]]:
        """path 의 **개별 선분**까지 내려가 괘선을 모은다.

        바깥 rect 만 보면 안 된다 — 표의 위·머리·아래 괘선이 path 하나로 묶여
        나오는 조판이 있고(실측: JKMS 2010), 그 rect 는 높이 72pt 라 '선'으로
        보이지 않아 표를 통째로 놓친다.
        """
        segs: list[tuple[str, float, float, float]] = []
        try:
            drawings = self.page.get_drawings()
        except Exception:                      # noqa: BLE001 — 그래픽 파싱 실패는 치명적이지 않다
            return segs
        for d in drawings:
            items = d.get("items") or ()
            for it in items:
                op = it[0]
                if op == "l":
                    p1, p2 = it[1], it[2]
                    if abs(p1.y - p2.y) <= 0.8 and abs(p1.x - p2.x) >= RULE_MIN_LEN:
                        segs.append(("h", (p1.y + p2.y) / 2.0,
                                     min(p1.x, p2.x), max(p1.x, p2.x)))
                    elif abs(p1.x - p2.x) <= 0.8 and abs(p1.y - p2.y) >= RULE_MIN_LEN:
                        segs.append(("v", (p1.x + p2.x) / 2.0,
                                     min(p1.y, p2.y), max(p1.y, p2.y)))
                elif op in ("re", "qu"):
                    obj = it[1]
                    r = getattr(obj, "rect", obj)
                    if r.height <= RULE_THICK_MAX and r.width >= RULE_MIN_LEN:
                        segs.append(("h", (r.y0 + r.y1) / 2.0, r.x0, r.x1))
                    elif r.width <= RULE_THICK_MAX and r.height >= RULE_MIN_LEN:
                        segs.append(("v", (r.x0 + r.x1) / 2.0, r.y0, r.y1))
        return segs

    def rules(self, mode: int) -> list[tuple[float, float, float]]:
        """변환 후 좌표계에서 '가로'인 괘선 (y, x0, x1). 토막난 선분은 잇는다."""
        want = "h" if mode == 0 else "v"
        raw: list[tuple[float, float, float]] = []
        for kind, pos, a, b in self._segs:
            if kind != want:
                continue
            box = (a, pos, b, pos) if kind == "h" else (pos, a, pos, b)
            x0, y0, x1, y1 = _rot(box, mode)
            raw.append(((y0 + y1) / 2.0, x0, x1))
        return _merge_rules(raw)

    def words_rot(self, mode: int) -> list[tuple]:
        if mode == 0:
            return self.words
        out = []
        for w in self.words:
            x0, y0, x1, y1 = _rot((w[0], w[1], w[2], w[3]), mode)
            out.append((x0, y0, x1, y1, w[4], w[5]))
        return out


def _visible_words(page) -> list[tuple]:
    """**보이는** 글자만으로 낱말 목록을 만든다 (x0, y0, x1, y1, text, 줄id).

    page.get_text("words") 를 쓰지 않는 이유는 하나다 — 그 함수는 색을 모른다.
    이 코퍼스의 표는 소수점 자리를 흰 글자로 맞추므로(위 _FILLER 주석), 색을
    보지 않으면 보이지도 않는 '0' 과 '.' 이 셀 값에 섞여 들어온다.
    글자 단위(rawdict)로 내려가 공백·큰 간격에서 낱말을 끊는다.
    """
    out: list[tuple] = []
    lid = 0

    def flush(buf):
        if not buf:
            return
        txt = "".join(c for c, _ in buf)
        if not txt.strip():
            return
        xs0 = min(b[0] for _, b in buf)
        ys0 = min(b[1] for _, b in buf)
        xs1 = max(b[2] for _, b in buf)
        ys1 = max(b[3] for _, b in buf)
        out.append((xs0, ys0, xs1, ys1, txt, lid))

    raw = page.get_text("rawdict")
    for b in raw.get("blocks", ()):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", ()):
            lid += 1
            buf: list[tuple[str, tuple]] = []
            prev_x1 = None
            for sp in ln.get("spans", ()):
                chars = sp.get("chars") or ()
                text = "".join(ch.get("c", "") for ch in chars)
                if (sp.get("color") == _WHITE and _FILLER.match(text or "x")) \
                        or sp.get("alpha") == 0:
                    flush(buf)                 # 보이지 않는 조판용 채움 — 버린다
                    buf, prev_x1 = [], None
                    continue
                for ch in chars:
                    c = ch.get("c", "")
                    bb = ch.get("bbox")
                    if bb is None:
                        continue
                    if not c.strip():
                        flush(buf)
                        buf, prev_x1 = [], None
                        continue
                    if prev_x1 is not None and bb[0] - prev_x1 > CHAR_BREAK:
                        flush(buf)
                        buf = []
                    buf.append((c, bb))
                    prev_x1 = bb[2]
            flush(buf)
    return out


def _merge_rules(raw: list[tuple[float, float, float]]
                 ) -> list[tuple[float, float, float]]:
    """같은 y 의 토막 선분을 하나로 잇고, 겹줄(이중선)을 합친다."""
    if not raw:
        return []
    raw = sorted(raw, key=lambda r: (r[0], r[1]))
    buckets: list[list[tuple[float, float, float]]] = []
    for r in raw:
        if buckets and abs(r[0] - buckets[-1][0][0]) <= RULE_Y_TOL:
            buckets[-1].append(r)
        else:
            buckets.append([r])
    out: list[tuple[float, float, float]] = []
    for bk in buckets:
        y = sum(r[0] for r in bk) / len(bk)
        bk = sorted(bk, key=lambda r: r[1])   # x 순서로 봐야 이어붙일 수 있다
        cur_a, cur_b = bk[0][1], bk[0][2]
        for _, a, b in bk[1:]:
            if a - cur_b <= RULE_X_JOIN:
                cur_a, cur_b = min(cur_a, a), max(cur_b, b)
            else:
                out.append((y, cur_a, cur_b))
                cur_a, cur_b = a, b
        out.append((y, cur_a, cur_b))
    # 이중선(1pt 간격 두 줄)은 한 줄로
    merged: list[tuple[float, float, float]] = []
    for r in sorted(out, key=lambda t: t[0]):
        if merged and abs(r[0] - merged[-1][0]) <= RULE_DEDUP_Y \
                and not (r[2] < merged[-1][1] - 2 or r[1] > merged[-1][2] + 2):
            p = merged[-1]
            merged[-1] = (p[0], min(p[1], r[1]), max(p[2], r[2]))
        else:
            merged.append(r)
    return merged


# ── 1. 캡션 위치 찾기 ───────────────────────────────────────────────
def _match_in_block(block: list[dict], needle: str) -> tuple[int, int] | None:
    """블록 **머리**에서 needle 이 시작하는지 본다 → (첫 줄, 끝 줄).

    블록 중간에서 시작하는 매치는 받지 않는다. 이 한 줄이 'GROBID 가 본문
    문단을 캡션으로 잘못 잡아 온' 표를 걸러 준다 — 실측: 이 코퍼스의 빈 표
    37개 중 PDF 에서 찾히는 35개는 **전부** 블록 첫 줄에서 시작했고, 블록
    중간(4번째 줄)에서 걸린 1개는 본문 문단의 꼬리였다(그 문단 아래 표를
    가져올 뻔했다). pdf_fallback 의 캡션 규칙 C1 과 같은 근거다.
    """
    if not block:
        return None
    text = "".join(ln["key"] for ln in block)
    if not text.startswith(needle):
        return None
    j = 0
    off = 0
    for i, ln in enumerate(block):
        off += len(ln["key"])
        j = i
        if off >= len(needle):
            break
    return (0, j)


def find_caption(pages: list[_Page], caption: str
                 ) -> tuple[int, tuple[float, float, float, float], int, str] | None:
    """캡션 문자열의 PDF 위치. → (페이지번호, 캡션 bbox, 회전모드, 매치설명)

    전체 키가 안 맞으면 접두사를 줄여 가며 다시 본다(PMC 캡션과 조판 캡션의
    문구가 조금 다른 일이 있다: 'strength of the recommendations' vs
    'strength of recommendation2'). 최소 CAP_MIN_KEY 자는 맞아야 한다 — 그
    아래로 내려가면 같은 지면의 다른 표를 물어 온다.
    """
    full = _key(caption)
    if len(full) < CAP_MIN_KEY:
        return None
    lens = []
    n = len(full)
    for frac in (1.0, 0.8, 0.6, 0.45):
        v = max(CAP_MIN_KEY, int(n * frac))
        if v <= n and v not in lens:
            lens.append(v)
    for ln in lens:
        needle = full[:ln]
        for pno, pg in enumerate(pages):
            for block in pg.blocks:
                hit = _match_in_block(block, needle)
                if hit is None:
                    continue
                i, j = hit
                lines = block[i:j + 1]
                mode = _rot_mode(lines)
                # **회전 좌표계로 돌려서** 돌려준다. 부르는 쪽(_next_stop_y·
                # _rule_region)은 줄·괘선을 _rot 한 좌표로 보므로 캡션만 원좌표로
                # 남기면 세로 조판 표에서 x 비교가 통째로 어긋난다.
                # 실측: 10.1016/j.jaad.2016.12.034 Table II 는 가로로 눕혀 조판된
                # 표인데(PDF 3쪽 하단), 캡션 x=[325,336] 과 괘선 x=[-729,-77] 을
                # 비교하게 돼 늘 no_rules 로 떨어졌다. 그 결과 GROBID 의 전치된
                # 표(행 이름이 마지막 행에 있는)가 그대로 남아 있었다.
                xs0 = min(l["bbox"][0] for l in lines)
                ys0 = min(l["bbox"][1] for l in lines)
                xs1 = max(l["bbox"][2] for l in lines)
                ys1 = max(l["bbox"][3] for l in lines)
                box = _rot((xs0, ys0, xs1, ys1), mode)
                how = "exact" if ln == n else f"prefix{ln}/{n}"
                return (pno, box, mode, how)
    return None


def _rot_mode(lines: list[dict]) -> int:
    d = lines[0].get("dir") or (1.0, 0.0)
    if abs(d[0]) >= abs(d[1]):
        return 0
    return 1 if d[1] < 0 else 2


# ── 2. 표 영역 ──────────────────────────────────────────────────────
def _next_stop_y(pg: _Page, mode: int, cap_box, cap_lines_keys: set[str]) -> float:
    """캡션 아래에서 표를 끊어야 하는 y — 다음 캡션 / 절 제목 / 꼬리말."""
    cx0, _cy0, _cx1, cy1 = cap_box
    pr = _rot((pg.rect.x0, pg.rect.y0, pg.rect.x1, pg.rect.y1), mode)
    stop = pr[3] - FOOTER_ZONE
    for ln in pg.lines:
        b = _rot(ln["bbox"], mode)
        if b[1] <= cy1 + 1.0:
            continue
        # 같은 단(段)의 줄만 본다 — 옆단 캡션이 표를 잘라 먹지 않게
        if not (cx0 - 8.0 <= b[0] <= cx0 + 40.0):
            continue
        if ln["key"] in cap_lines_keys:
            continue
        if _CAP_START.match(ln["text"]) or _HEADING.match(ln["text"]):
            stop = min(stop, b[1])
    return stop


def _rule_region(pg: _Page, mode: int, cap_box, stop_y: float
                 ) -> tuple[list[float], tuple[float, float]] | None:
    """캡션 아래 괘선 무리 → (괘선 y 목록, (x0, x1)). 없으면 None.

    후보 조건이 예전에는 `괘선 x0 ≤ 캡션 x0 + 8pt` 였다. 이건 캡션이 표보다
    **왼쪽에서 시작한다**는 가정인데, 캡션을 표 폭 안쪽으로 들여 짜지 않고
    바깥으로 내어 짜는 조판에서 1pt 차이로 표를 통째로 놓쳤다.
    실측: 10.1111/bjd.15560 Table 1 은 캡션 x0=308.4, 괘선 x0=317.3 이라
    317.3 ≤ 316.4 가 거짓이 되어 no_rules 로 떨어졌다(같은 논문 표 3개 전부).
    그래서 '캡션 칸과 **겹치는가**'로 바꾼다 — 옆단·다른 논문의 괘선은
    가로로 겹치지 않으므로 오염 방어력은 그대로다.
    """
    cx0, _cy0, cx1, cy1 = cap_box

    def overlaps(r) -> bool:
        ov = min(r[2], cx1) - max(r[1], cx0)
        return (r[1] <= cx0 + RULE_CAP_X0_TOL) and ov >= RULE_CAP_OVERLAP

    rules = pg.rules(mode)
    cands = [r for r in rules
             if cy1 + 0.5 < r[0] < stop_y - 0.5 and overlaps(r)]
    if not cands:
        return None
    # 캡션 바로 아래 괘선부터 순서대로 '표의 윗선'으로 세워 본다. 느슨해진
    # 후보 조건 때문에 첫 후보가 표와 무관한 짧은 선일 수 있어서다.
    for k, top in enumerate(cands):
        if top[0] - cy1 > CAP_TO_RULE_MAX:
            break
        ys = [top[0]]
        xs = [(top[1], top[2])]
        for r in cands[k + 1:]:
            if abs(r[1] - top[1]) > RULE_X0_TOL or abs(r[2] - top[2]) > RULE_X1_TOL:
                continue
            ys.append(r[0])
            xs.append((r[1], r[2]))
        ys, xs = _cut_at_gap(ys, xs)
        if len(ys) >= 2 and ys[-1] - ys[0] >= 10.0:
            return ys, (min(a for a, _ in xs), max(b for _, b in xs))
    return None


RULE_GAP_FACTOR = 3.5      # 이웃 괘선 간격이 중앙값의 이 배를 넘으면 남의 선이다
RULE_GAP_FLOOR = 60.0      # 다만 이만큼은 무조건 봐준다(윗선~머리선 간격)


def _cut_at_gap(ys: list[float], xs: list[tuple[float, float]]
                ) -> tuple[list[float], list[tuple[float, float]]]:
    """괘선 줄기를 **간격이 갑자기 벌어지는 곳**에서 끊는다.

    x 정렬만으로 괘선을 모으면 같은 폭으로 그어진 **페이지 아래 장식선**까지
    표에 딸려 들어온다. 실측: 10.1371/journal.pone.0179088 4쪽 Table 1 은
    괘선이 y=88.4…286.0 인데 페이지 바닥선 y=744.2 도 x=[36,576] 으로 같아
    영역이 페이지 끝까지 늘어났고, 그 사이 Discussion 문단 15개가 통째로
    표의 셀이 됐다(수리 후 검증에서 잡았다).
    """
    if len(ys) < 3:
        return ys, xs
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    med = sorted(gaps)[len(gaps) // 2]
    limit = max(RULE_GAP_FACTOR * med, RULE_GAP_FLOOR)
    for i, g in enumerate(gaps):
        if g > limit:
            return ys[:i + 1], xs[:i + 1]
    return ys, xs


# ── 3~4. 행·열 복원 ─────────────────────────────────────────────────
def _rows_of(words) -> list[list]:
    """낱말을 y 중심으로 묶어 행을 만든다(위첨자는 같은 행으로 흡수).

    **누적 평균으로 이어붙이면 안 된다** — 머리행이 열마다 반 줄씩 어긋나게
    조판된 표(실측: Ann Dermatol Part I)에서는 중심 간격이 임계값 언저리라
    평균이 조금씩 밀리며 머리행 전체가 한 줄로 뭉친다. 이웃한 중심 사이의
    **간격**만 보고 끊는다.
    """
    if not words:
        return []
    hs = sorted(w[3] - w[1] for w in words)
    mh = hs[len(hs) // 2] or 8.0
    tol = max(2.5, 0.45 * mh)
    ordered = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
    rows: list[list] = []
    cur: list = [ordered[0]]
    prev = (ordered[0][1] + ordered[0][3]) / 2.0
    for w in ordered[1:]:
        c = (w[1] + w[3]) / 2.0
        if c - prev <= tol:
            cur.append(w)
        else:
            rows.append(sorted(cur, key=lambda x: x[0]))
            cur = [w]
        prev = c
    rows.append(sorted(cur, key=lambda x: x[0]))
    return rows


SLACK_MIN_ROWS = 5         # 이보다 행이 적으면 '한 행 눈감기'를 쓰지 않는다
SLACK_MIN_BAND = 20.0      # 한 행을 눈감고 얻은 띠는 이만큼 넓어야 인정


def _bands(rows: list[list], x0: float, x1: float, min_gap: float,
           slack: int) -> list[tuple[float, float]]:
    """세로로 비어 있는 띠 목록 (시작x, 끝x). slack = 침범을 눈감아 줄 행 수."""
    step = 0.5
    n = int((x1 - x0) / step) + 3
    hits = [0] * n
    for r in rows:
        seen = bytearray(n)
        for w in r:
            a = max(0, int((w[0] - x0) / step))
            b = min(n - 1, int((w[2] - x0) / step) + 1)
            for k in range(a, b + 1):
                seen[k] = 1
        for k in range(n):
            if seen[k]:
                hits[k] += 1
    free = [h <= slack for h in hits]
    out: list[tuple[float, float]] = []
    k = 0
    while k < n and free[k]:      # 표 왼쪽 여백은 열 사이의 띠가 아니다
        k += 1
    start = None
    for j in range(k, n):
        if free[j]:
            if start is None:
                start = j
        else:
            if start is not None:
                if (j - start) * step >= min_gap:
                    out.append((x0 + start * step, x0 + j * step))
                start = None
    return out


def _col_bounds(rows: list[list], x0: float, x1: float, min_gap: float
                ) -> list[float]:
    """본문 행들의 세로 빈 띠 → 열 경계.

    기본은 **모든 행에서 비어 있는** 띠다(엄격). 여기에만 의존하면 표를
    가로지르는 한 줄이 경계를 통째로 지운다 — 실측: 'Rank of sensitivity |
    Allergens with a high frequency of positivity (%)' 한 줄 때문에 4열 표가
    3열로 뭉쳤다. 그래서 '한 행만 침범하는 띠'도 보되, **아주 넓을 때만**
    (20pt 이상) 그리고 엄격 띠와 겹치지 않을 때만 경계로 받아들인다.
    폭 조건이 없으면 넓은 글자 열 안의 우연한 5pt 틈이 열로 승격돼(실측:
    Ann Dermatol 표 3개) 멀쩡한 표가 쪼개진다.
    """
    strict = _bands(rows, x0, x1, min_gap, 0)
    bounds = [(a + b) / 2.0 for a, b in strict]
    if len(rows) >= SLACK_MIN_ROWS:
        wide = max(SLACK_MIN_BAND, 3.0 * min_gap)
        for a, b in _bands(rows, x0, x1, min_gap, 1):
            if b - a < wide:
                continue
            if any(not (b <= sa or a >= sb) for sa, sb in strict):
                continue                       # 이미 엄격 띠가 잡은 자리
            bounds.append((a + b) / 2.0)
    return sorted(bounds)


SPAN_COVER = 0.6           # 머리글이 이 비율 이상 덮은 열은 '걸친' 것으로 본다


def _assign(row: list, bounds: list[float], lo: float, hi: float,
            spread: bool = False) -> list[str]:
    """한 행의 낱말을 열에 배치.

    셀은 **열 경계가 낱말 사이에 놓일 때만** 끊는다. 낱말 간격에 임계값을 두면
    조판마다 공백 폭이 달라 터진다 — 실측: Ann Dermatol Part I 의 머리글
    '% of respondents' 는 낱말 간격이 4.2pt 라 2.6pt 기준으로는 쪼개졌고,
    같은 표 본문 셀 '2, 4, 9, 10' 의 간격도 4.2pt 라 기준을 올리면 이번엔
    다른 표의 인접 셀이 붙는다. 경계 기준은 이 상충을 아예 없앤다.
    """
    ncols = len(bounds) + 1
    edges = [lo - 1e4] + list(bounds) + [hi + 1e4]
    cells: list[list[str]] = [[] for _ in range(ncols)]
    chunks: list[list] = []
    cur = [row[0]]
    for w in row[1:]:
        prev_x1 = max(x[2] for x in cur)
        # 조판상의 다른 줄(line)에 속한 낱말은 절대 한 셀로 묶지 않는다
        if w[5] != cur[-1][5] or any(prev_x1 <= b <= w[0] for b in bounds):
            chunks.append(cur)
            cur = [w]
        else:
            cur.append(w)
    chunks.append(cur)
    # 열 폭 계산용 경계는 표 상자 안으로 자른다(양끝이 ±1e4 면 폭이 무한대다)
    span_edges = [lo] + list(bounds) + [hi]
    for ch in chunks:
        a = min(x[0] for x in ch)
        b = max(x[2] for x in ch)
        text = " ".join(w[4] for w in ch)
        covered: list[int] = []
        if spread:
            for ci in range(ncols):
                w = span_edges[ci + 1] - span_edges[ci]
                ov = min(b, span_edges[ci + 1]) - max(a, span_edges[ci])
                if w > 0 and ov / w >= SPAN_COVER:
                    covered.append(ci)
        if len(covered) >= 2:
            # 여러 열을 덮는 머리글(병합 셀)은 덮은 열 **모두**에 붙인다.
            # 한 열에만 넣으면 'No. (%) of patients' 가 1990s 칸의 이름이 돼 버린다.
            for ci in covered:
                cells[ci].append(text)
            continue
        best, best_ov = 0, -1e9
        for ci in range(ncols):
            ov = min(b, edges[ci + 1]) - max(a, edges[ci])
            if ov > best_ov:
                best_ov, best = ov, ci
        cells[best].append(text)
    return [_cell_text(" ".join(c)) for c in cells]


_HYPHENS = "-‐‑­"
_STOPS: tuple[frozenset, frozenset] | None = None


def _hyphen_stops() -> tuple[frozenset, frozenset]:
    """줄바꿈 분철을 되붙일 때 건드리면 안 되는 앞/뒤 낱말 목록.

    textfix 가 이미 실측으로 다듬어 둔 목록을 그대로 쓴다(정책 이원화 방지).
    import 가 실패해도 표 복원이 멈추면 안 되므로 빈 목록으로 물러선다.
    """
    global _STOPS
    if _STOPS is None:
        try:
            from .textfix import _HYPHEN_HEAD_STOP, _HYPHEN_TAIL_STOP
            _STOPS = (_HYPHEN_HEAD_STOP, _HYPHEN_TAIL_STOP)
        except Exception:                      # noqa: BLE001
            _STOPS = (frozenset(), frozenset())
    return _STOPS


def _join_frag(a: str, b: str) -> str:
    """같은 셀의 두 줄을 잇는다. 'Recommenda-' + 'tion' → 'Recommendation'.

    여기서 잇는 두 조각은 **한 셀 안의 줄바꿈**이 확실하므로, 자유 텍스트보다
    강한 근거가 있다. 다만 'non-soap' 처럼 원래 하이픈이 있는 합성어가 하필
    그 자리에서 줄바꿈된 경우가 있어 정지목록으로 막는다.
    """
    a, b = a.strip(), b.strip()
    if not a:
        return b
    if not b:
        return a
    if a[-1] in _HYPHENS and b[:1].islower():
        head = re.split(r"[\s]", a[:-1])[-1].lower()
        tail = re.split(r"[\s]", b)[0].lower()
        head_stop, tail_stop = _hyphen_stops()
        if head in head_stop or tail in tail_stop:
            return a + b                       # 하이픈은 살리고 공백만 없앤다
        return a[:-1] + b
    return a + " " + b


def _merge_wrapped(rows: list[list[str]], pitches: list[float]) -> list[list[str]]:
    """줄바꿈으로 이어지는 셀을 앞 행에 붙인다(칸이 하나만 찬 행에 한해).

    조건을 좁게 잡는다 — 앞 행의 같은 칸이 20자 이상이고 마침표로 끝나지 않으며,
    이어붙일 조각이 소문자로 시작할 때만. 'Male / (빈칸) 20 8' 같은 정상 행을
    앞 행에 흡수해 버리는 사고를 막는다.
    """
    out: list[list[str]] = []
    med = sorted(p for p in pitches if p > 0)
    pitch = med[len(med) // 2] if med else 0.0
    for idx, r in enumerate(rows):
        nz = [i for i, c in enumerate(r) if c.strip()]
        gap = pitches[idx] if idx < len(pitches) else 0.0
        if (out and len(nz) == 1 and len(r) > 1):
            c = nz[0]
            prev = out[-1][c]
            frag = r[c].strip()
            tight = (pitch <= 0) or (gap <= pitch * 1.6)
            if (prev and len(prev) >= 20
                    and not prev.rstrip().endswith(".")
                    and (frag[:1].islower() or prev.rstrip()[-1:] in _HYPHENS)
                    and tight):
                out[-1][c] = _join_frag(prev, frag)
                continue
        out.append(list(r))
    return out


_URL_ONLY = re.compile(r"^(?:https?://|doi:|www\.)\S+$", re.I)


def _split_trailing_note(body: list[list[str]]) -> tuple[list[list[str]], str]:
    """표 **마지막 행들**이 각주면 떼어 낸다 → (본문 행, 각주 문자열).

    아래 괘선을 각주 밑에 긋는 조판이 있어(실측: PLOS ONE 은 각주와 DOI 줄
    아래에 선을 긋는다) 각주가 표의 마지막 행으로 들어온다. 각주는 표의
    일부지만 **셀이 아니다** — 떼어서 footnote 로 담는다.
    """
    notes: list[str] = []
    while body:
        nz = [c for c in body[-1] if c.strip()]
        if len(nz) != 1:
            break
        txt = nz[0].strip()
        if _URL_ONLY.match(txt) or _FOOT_MARK.match(txt) or _ABBR_TOKEN.search(txt):
            notes.insert(0, txt)
            body = body[:-1]
            continue
        break
    return body, " ".join(notes).strip()


# 표 **안에서** 행을 떼어낼 때는 각주 판정을 더 좁게 한다. 표 아래 각주는
# 기하(밑선 바로 아래·같거나 작은 글씨)가 이미 걸러 주지만, 여기서는 그 근거가
# 없어 진짜 셀을 떼어낼 위험이 있다. 실측: 10.5021/ad.2015.27.5.578 표 12 의
# 'However, the cost-effectiveness should be seriously considered.' 는 앞 칸
# 권고문이 줄바꿈된 것인데 느슨한 규칙이 각주로 떼어 갔다.
_FOOT_MARK = re.compile(r"^\s*[*†‡§¶#]|^\s*(?:Note|Notes|Abbreviations?|Source)\b")
# 약어 풀이의 머리 토큰은 '약어처럼' 생겨야 한다 — 대문자 2개 이상이거나
# 3자 이하이거나 숫자를 품는다(CI, OR, Tm, SD, VEGFa, GAPDH, NB-UVB, ICD-10).
# 'However,' · 'Values,' 같은 평범한 낱말은 걸리지 않는다.
_ABBR_TOKEN = re.compile(
    r"(?:^|[;.]\s|\s)(?=[A-Za-z0-9/+-]{1,8}[,:]\s)"
    r"(?:[A-Za-z0-9/+-]*[A-Z][A-Za-z0-9/+-]*[A-Z][A-Za-z0-9/+-]*"
    r"|[A-Za-z][A-Za-z0-9/+-]{0,2}"
    r"|[A-Za-z][A-Za-z0-9/+-]*\d[A-Za-z0-9/+-]*)[,:]\s+[A-Za-z]")


# ── 5. 판정 & markdown ──────────────────────────────────────────────
# 셀이 '끝난 것처럼' 보이는 마침 문자. 줄 끝 하이픈은 여기 넣으면 안 된다 —
# 그것이야말로 다음 줄로 이어진다는 가장 강한 증거다.
_FINISHED = re.compile(r"[.,:;!?)\]%\d’”\"]$")


def _flows_as_prose(grid: list[list[str]]) -> bool:
    """어느 한 열의 셀들이 **한 문단처럼 이어지는가**.

    괘선 상자 안에 표가 아니라 글이 들어 있는 조판이 있다(실측: JAMA Pediatrics
    의 'What is your diagnosis?' 상자 — 왼쪽은 증례 서술, 오른쪽은 보기 A~D).
    셀 길이·채움률만으로는 표와 구분되지 않는다. 갈라 주는 것은 '줄이 문장
    중간에서 끊기고 다음 줄이 소문자로 이어진다'는 흐름이다. 세 줄 연속이면
    표가 아니라 문단으로 본다.
    """
    if not grid:
        return False
    ncols = max(len(r) for r in grid)
    for c in range(ncols):
        run = 0
        for i in range(len(grid) - 1):
            a = grid[i][c] if c < len(grid[i]) else ""
            b = grid[i + 1][c] if c < len(grid[i + 1]) else ""
            if (a and b and len(a) > 45 and not _FINISHED.search(a)
                    and b[:1].islower()):
                run += 1
                if run >= 2:
                    return True
            else:
                run = 0
    return False


PARA_CELL_MIN = 120        # 이보다 긴 셀이 그 행의 유일한 값이면 '문단 행'
PARA_ROWS_MAX = 1          # 문단 행이 이보다 많으면 표가 아니라 본문을 삼킨 것


def _para_rows(grid: list[list[str]]) -> int:
    """한 칸만 채워져 있고 그 값이 문장 길이인 행의 수."""
    n = 0
    for r in grid:
        nz = [c for c in r if c.strip()]
        if len(nz) == 1 and len(nz[0]) > PARA_CELL_MIN:
            n += 1
    return n


def _validate(header: list[str] | None, body: list[list[str]]) -> str | None:
    grid = ([header] if header else []) + body
    if not grid:
        return "no_rows"
    # 본문 문단을 셀로 삼킨 표를 막는 마지막 그물. _flows_as_prose 는 문단이
    # 마침표로 끝나면(즉 셀 하나가 문단 하나를 통째로 담으면) 걸리지 않는다.
    if _para_rows(grid) > PARA_ROWS_MAX:
        return "swallowed_prose"
    ncols = max(len(r) for r in grid)
    if ncols < 2:
        return "cols_lt_2"
    if len(grid) < 2:
        return "rows_lt_2"
    cells = [c for r in grid for c in r]
    filled = [c for c in cells if c.strip()]
    if not filled:
        return "no_content"
    if len(filled) / len(cells) < 0.25:
        return "sparse"
    multi = sum(1 for r in grid if sum(1 for c in r if c.strip()) >= 2)
    if multi < 2:
        return "single_column"
    short = sum(1 for c in filled if len(c) <= 60)
    if short / len(filled) < 0.5:
        return "prose_like"
    sentence = sum(1 for c in filled
                   if len(c) > 80 and c.rstrip().endswith("."))
    if sentence / len(filled) > 0.5:
        return "prose_like"
    if _flows_as_prose(grid):    # 머리행부터 본다 — 문단 상자는 첫 줄이 머리행이 된다
        return "prose_flow"
    return None


def to_markdown(header: list[str], body: list[list[str]]) -> str:
    ncols = max([len(header)] + [len(r) for r in body])

    def pad(r: list[str]) -> list[str]:
        return list(r) + [""] * (ncols - len(r))

    lines = ["| " + " | ".join(pad(header)) + " |",
             "| " + " | ".join(["---"] * ncols) + " |"]
    for r in body:
        lines.append("| " + " | ".join(pad(r)) + " |")
    return "\n".join(lines)


# ── markdown 자체 검사 ──────────────────────────────────────────────
# '표가 있다' 와 '표가 쓸 만하다' 는 다르다. 표 개수 대조는 전부 통과하는데
# markdown 이 렌더되지 않거나 데이터가 한 줄도 없는 표가 실제로 있다.
_SEP_CELL = re.compile(r"^:?-{2,}:?$")
COLLAPSE_COLS = 12         # 데이터 1행에 이만큼 열이면 표가 한 줄로 뭉갠 것
_OPEN_TAIL = re.compile(r"[(\[{]\s*$|[(\[]\s*[\d.,-]{0,4}$")


def parse_markdown(md: str) -> tuple[list[list[str]], int]:
    """markdown 표 → (행 목록, 구분선 index). 구분선이 없으면 -1."""
    rows: list[list[str]] = []
    sep = -1
    for line in (md or "").split("\n"):
        if not line.strip():
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if sep < 0 and cells and any(cells) \
                and all(_SEP_CELL.match(c) for c in cells if c):
            sep = len(rows)
        rows.append(cells)
    return rows, sep


def check_markdown(md: str) -> dict:
    """markdown 표의 **구조**만 본다(내용의 옳고 그름은 여기서 판단하지 않는다).

    반환 `problems` 에 담기는 사유와 그 근거:
      · no_separator / separator_misplaced — GFM 표는 `| --- |` 구분선이
        **머리행 바로 다음 한 줄**이어야 렌더된다. 실측: 구분선이 데이터
        뒤에 오는 표가 코퍼스에 있다(그 표는 어느 뷰어에서도 표로 안 보인다).
      · no_data_rows — 머리 조각만 남고 데이터가 0행.
      · ragged_columns — 행마다 열 수가 달라 값이 다른 머리글 밑으로 밀린다.
      · collapsed — 데이터 1행에 열 12개 이상. 표 전체가 한 행으로 뭉갰다.
      · truncated — 마지막 행이 다른 행보다 짧고 끝이 열린 괄호다
        (실측: '| Black | 2 (4) | 8 (23) | 1 (4) | 4 (' 에서 끝난 표).
      · unreadable — 글꼴 인코딩 때문에 못 읽은 글자(U+FFFD)가 남은 셀.
    """
    rows, sep = parse_markdown(md)
    out: dict[str, Any] = {"problems": [], "nrows": len(rows), "sep": sep}
    if not rows:
        out["problems"].append("empty")
        out["ok"] = False
        out["ncols"] = out["ndata"] = 0
        return out
    data = rows[sep + 1:] if sep >= 0 else rows[1:]
    widths = [len(r) for i, r in enumerate(rows) if i != sep]
    ncols = max(widths) if widths else 0
    ndata = sum(1 for r in data if any(c.strip() for c in r))
    out.update(ncols=ncols, ndata=ndata)
    p = out["problems"]
    if sep < 0:
        p.append("no_separator")
    elif sep != 1:
        p.append("separator_misplaced")
    if ndata == 0:
        p.append("no_data_rows")
    if ncols < 2:
        p.append("single_column")
    if len({len(r) for i, r in enumerate(rows) if i != sep}) > 1:
        p.append("ragged_columns")
    if ndata <= 1 and ncols >= COLLAPSE_COLS:
        p.append("collapsed")
    if data:
        last = [c for c in data[-1] if c.strip()]
        tail = last[-1] if last else ""
        # 셀 중간에서 잘린 표. 실측: '| Black | 2 (4) | 8 (23) | 1 (4) | 4 ('
        # — 행 길이는 멀쩡한데 마지막 값이 '4 (' 에서 끊겼다. 그래서 길이가
        # 아니라 **괄호가 열린 채 끝났는가**를 본다.
        if _OPEN_TAIL.search(tail) or tail.count("(") > tail.count(")"):
            p.append("truncated")
        elif len(data[-1]) < ncols:
            p.append("truncated")
    if UNREADABLE in (md or ""):
        p.append("unreadable")
    out["ok"] = not p
    return out


# ── 표가 아닌 것을 걸러내는 관문 ────────────────────────────────────
# 없는 표를 지우는 편이 망가진 표를 고치는 것보다 안전하고 효과가 크다.
# 다만 **진짜 표가 추출에 실패한 것**과 **애초에 표가 아닌 것**은 다르다.
# 캡션이 'Table N …' 이면 그 표는 PDF 에 실재하므로 절대 지우지 않는다
# (비어 있어도 '표 N 이 있다'는 사실 자체가 정보다). 지우는 것은
# **표 캡션이 아닌데 내용도 표가 아닌 것**뿐이다.
_TABLE_CAPTION = re.compile(
    r"^\s*\(?\s*(?:table|tab\.|tabla|tableau)\s*"
    r"(?:[ivxlIVXL]{1,5}|\d{1,3}|[A-Z]\d?)\s*[.:)\-–—|]?(?:\s|$)", re.I)
# 참고문헌 항목: '3. Zhong SY, Chen YX, Fang M et al. …' / '… 1999; 135: 790-793.'
# 'et al. + 연도' 는 **넣으면 안 된다** — 연구 특성표는 행마다 'Arakawa et al
# 2015' 를 담으므로 진짜 표가 통째로 참고문헌으로 오인된다(실측:
# 10.1111/phpp.12596 의 'TA B LE 1 Characteristics of studies included in this
# review' 가 이 조건 하나로 지워질 뻔했다).
_REF_ITEM = re.compile(
    r"(?:^\s*\d{1,3}[.)]\s+[A-Z][A-Za-z'’-]+\s+[A-Z]{1,3}\b)"
    r"|(?:\b(?:19|20)\d\d\s*;\s*\d+\s*(?:\(\d+\))?\s*:\s*\d+)")
# 지면 장식(워터마크·저작권·다운로드 안내). 표 셀에 있으면 표가 아니다.
_FURNITURE = re.compile(
    r"(?:Creative\s+Commons|OA\s+articles?\s+are\s+governed|Protected\s+by\s+copyright"
    r"|Downloaded\s+from|All\s+rights\s+reserved|Conflicts?\s+of\s+interest"
    r"|Wiley\s+Online\s+Library|see\s+front\s+matter|©\s*(?:19|20)\d\d)", re.I)
# 약어 상자 / 홍보 상자 — 표 자리에 실려도 표가 아니다
_SIDEBAR = re.compile(r"(?:CAPSULE\s+SUMMARY|Abbreviations?\s+used\b)", re.I)
FAKE_MIN_HITS = 0.34       # 데이터 행 중 이 비율 이상이 걸리면 그 신호를 인정


def fake_table_reason(caption: str, markdown: str) -> str | None:
    """이 표 객체가 **표가 아닌 것**이면 사유를, 표로 볼 만하면 None.

    두 조건을 **모두** 넘겨야 가짜로 판정한다.
      (1) 캡션이 'Table N' 꼴이 아니다 — 진짜 표 캡션을 달고 있으면 내용이
          망가졌을 뿐 표는 실재한다. 지우지 않고 수리 대상으로 남긴다.
      (2) 내용이 표 모양이 아니다 — 1열 / 참고문헌 목록 / 지면 장식 /
          약어·홍보 상자 / 문단 흐름.
    실측으로 정한 문턱이다. (1) 만으로 지우면 캡션 앞머리가 잘린 진짜 표
    (예: 'Incidence of psoriasiform diseases in IBD patients…' — Table 2 의
    'Table 2 ' 만 떨어져 나간 것)를 통째로 잃는다.
    """
    if _TABLE_CAPTION.match(caption or ""):
        return None
    rows, sep = parse_markdown(markdown or "")
    data = [r for r in (rows[sep + 1:] if sep >= 0 else rows[1:])
            if any(c.strip() for c in r)]
    if not rows:
        return None                       # 내용이 없으면 판단 근거가 없다
    widths = [len(r) for i, r in enumerate(rows) if i != sep]
    ncols = max(widths) if widths else 0
    blob = (caption or "") + "\n" + (markdown or "")

    def hit_frac(pat) -> float:
        if not data:
            return 0.0
        return sum(1 for r in data if pat.search(" ".join(r))) / len(data)

    # 참고문헌·지면장식 판정은 **내용(데이터 행)으로만** 한다. 캡션만 보고
    # 지우면 안 된다 — 이 코퍼스에는 캡션이 워터마크로 뒤바뀐 **진짜 표**가
    # 있다(실측: 10.1111/jdv.19451 tab_4 의 캡션은 ', 2023, 11, OA articles are
    # governed by the applicable Creative Commons License' 이지만 내용은 PDF
    # 6쪽의 'TA B L E 4 Modified assessment check list.' 그 자체다).
    if _SIDEBAR.search(blob):
        return "sidebar_box"
    if hit_frac(_REF_ITEM) >= FAKE_MIN_HITS:
        return "reference_list"
    if hit_frac(_FURNITURE) >= FAKE_MIN_HITS:
        return "page_furniture"
    if ncols < 2:
        return "single_column"
    # _flows_as_prose 는 **지우는 근거로 쓰지 않는다.** 불릿 목록을 한 열에
    # 담은 진짜 표가 문단처럼 흐르기 때문이다(실측: 10.1111/jdv.19450 의
    # 'TA B L E 1 Phototherapy pearls in vitiligo' — PDF 4쪽에 3열로 실재하는데
    # 오른쪽 열이 '• Early localized…' 불릿이라 문단으로 판정됐다).
    # 추출 단계의 _validate 에서는 계속 쓴다(거기서는 '만들지 않는' 판단이라
    # 보수적인 쪽이 안전한 반면, 여기서는 '지우는' 판단이라 반대다).
    return None


_FIG_HEAD = re.compile(r"^fig(?:ure)?(?:[ivxl]{1,5}|\d{1,3})")
FIG_TOKEN_MIN = 5          # 겹침을 따질 최소 낱말 수
FIG_TOKEN_FRAC = 0.7       # 이 비율 이상 겹치면 그림 캡션으로 본다


def _long_tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z]{4,}", (s or "").lower())}


def caption_in_pdf_figure(pages: list[_Page], caption: str) -> bool:
    """이 캡션이 PDF 의 **그림 캡션**과 같은 것인가 → 표가 아니라 그림이다.

    낱말 겹침으로 본다(부분문자열이 아니라). 조판이 'FI G U R E 1 The Vitiligo
    Extent Score for a Target Area (VESTA).' 인데 추출된 캡션은 '. Total
    repigmentation 100 VESTA Vitiligo Extent Score for a Target Area' 처럼
    어순과 앞머리가 달라져 있어 부분문자열로는 걸리지 않는다.
    실측: 10.1111/pcmr.12730 tab_0 은 Figure 1 의 채점 양식이지 표가 아니다
    (PDF 2쪽에서 확인).
    """
    toks = _long_tokens(caption)
    if len(toks) < FIG_TOKEN_MIN:
        return False
    for pg in pages:
        for block in pg.blocks:
            if not block or not _FIG_HEAD.match(_key(block[0]["text"])):
                continue
            body = " ".join(ln["text"] for ln in block[:6])
            ftoks = _long_tokens(body)
            if not ftoks:
                continue
            if len(toks & ftoks) / len(toks) >= FIG_TOKEN_FRAC:
                return True
    return False


def caption_in_pdf_table(pages: list[_Page], caption: str) -> bool:
    """이 캡션이 PDF 의 **진짜 표 캡션 안에** 들어 있는가.

    GROBID 가 캡션 앞머리 'Table 1' 을 잃는 조판이 있다(자간을 벌린
    'TA B L E 1' 은 낱말로 붙지 않는다). 그러면 캡션만 봐서는 표인지 알 수
    없다. PDF 블록 머리에서 'Table N' 으로 시작하는 캡션을 모아, 그 안에
    이 문자열이 들어 있으면 **표가 실재한다**고 보고 지우지 않는다.
    """
    key = _key(caption)
    if len(key) < CAP_MIN_KEY:
        return False
    needle = key[:max(CAP_MIN_KEY, min(len(key), 60))]
    for pg in pages:
        for block in pg.blocks:
            if not block:
                continue
            head = _key(block[0]["text"])
            if not _PDF_TABLE_HEAD.match(head):
                continue
            whole = "".join(ln["key"] for ln in block[:6])
            if needle in whole:
                return True
    return False


# 'Supplemental Table I' · 'Appendix Table 2' · 'Online Table 3' 도 표다.
# 실측: 10.1016/j.jaad.2018.06.016 의 'Supplemental Table I. Adverse events of
# micropunch grafting, by various factors (N = 230)'(PDF 9쪽)이 같은 논문
# Fig 4 캡션과 낱말이 겹쳐 '그림'으로 지워질 뻔했다.
_PDF_TABLE_HEAD = re.compile(
    r"^(?:supplement(?:al|ary)?|appendix|online|web|extended|additional|e)?"
    r"tab(?:le)?(?:[ivxl]{1,5}|\d{1,3})")


# ── 표 하나 복원 ────────────────────────────────────────────────────
def _grid_from_region(pg: _Page, mode: int, ys: list[float] | None,
                      box: tuple[float, float, float, float]
                      ) -> tuple[list[str], list[list[str]], dict] | str:
    """영역 안의 낱말 → (머리행, 본문행들, 정보). 실패하면 사유 문자열."""
    x0, y0, x1, y1 = box
    words = [w for w in pg.words_rot(mode)
             if y0 - 0.5 < (w[1] + w[3]) / 2.0 < y1 + 0.5
             and x0 - 6.0 < (w[0] + w[2]) / 2.0 < x1 + 6.0
             and w[4].strip()]
    if not words:
        return "no_words"                      # 표가 이미지다(텍스트층에 글자가 없다)
    rows = _rows_of(words)
    if not rows:
        return "no_words"

    # 머리행 구간: 위 괘선 ~ 그 다음 괘선. 이중선이면 한 칸 더 내려본다.
    head_y = None
    if ys and len(ys) >= 3:
        for y in ys[1:-1]:
            if y - ys[0] >= 6.0:
                head_y = y
                break
    head_rows: list[list] = []
    body_rows: list[list] = []
    for r in rows:
        c = sum((w[1] + w[3]) / 2.0 for w in r) / len(r)
        if head_y is not None and c < head_y:
            head_rows.append(r)
        else:
            body_rows.append(r)
    if head_y is None and len(rows) >= 2:
        head_rows, body_rows = [rows[0]], rows[1:]
    if not body_rows:
        return "rows_lt_2"

    sizes = sorted(w[3] - w[1] for r in body_rows for w in r)
    fsize = sizes[len(sizes) // 2] if sizes else 8.5
    min_gap = max(MIN_COL_GAP, 0.5 * fsize)
    bounds = _col_bounds(body_rows, x0, x1, min_gap)
    if not bounds:
        return "no_columns"

    # 머리행은 y 로 묶은 '행'이 아니라 **조판상의 줄** 단위로 처리한다.
    # 다단 머리글은 열마다 줄이 반 줄씩 어긋나 y 로는 갈라지지 않는다(실측:
    # JKMS 2010 은 좌우 머리글 기준선이 2.6pt 차이라 한 행으로 뭉쳤다).
    ncols = len(bounds) + 1
    header = [""] * ncols
    groups: dict[int, list] = {}
    for r in head_rows:
        for w in r:
            groups.setdefault(w[5], []).append(w)
    for _lid, g in sorted(groups.items(),
                          key=lambda kv: (min((w[1] + w[3]) / 2.0 for w in kv[1]),
                                          min(w[0] for w in kv[1]))):
        cells = _assign(sorted(g, key=lambda w: w[0]), bounds, x0, x1, spread=True)
        for i, v in enumerate(cells):
            if v:
                header[i] = _join_frag(header[i], v)

    body: list[list[str]] = []
    pitches: list[float] = []
    prev_c = None
    for r in body_rows:
        c = sum((w[1] + w[3]) / 2.0 for w in r) / len(r)
        pitches.append(0.0 if prev_c is None else c - prev_c)
        prev_c = c
        body.append(_assign(r, bounds, x0, x1))
    body = _merge_wrapped(body, pitches)
    body, tail_note = _split_trailing_note(body)
    info = {"ncols": ncols, "n_head_rows": len(head_rows),
            "n_body_rows": len(body)}
    if tail_note:
        info["tail_note"] = tail_note
    return header, body, info


# ── 표 각주 ────────────────────────────────────────────────────────
# 표 각주는 표의 일부다. 지금은 어디에도 담기지 않아 (a) 본문 꼬리로 새거나
# (b) 통째로 사라진다. 총괄 실측 2건:
#   10.1002/jso.23438  Table II 밑 '1Mean with its range. 2Mean with its
#       standard deviation.' 두 줄이 RESULTS 본문 마지막에 붙었다.
#   10.1002/lsm.22358  Table 1 밑 'Tm, melting temperature; VEGFa, …' 소실.
# 담는 것이 이 모듈의 몫이고, 본문에서 걷어내는 것은 본문 담당의 몫이다.
_FOOT_START = re.compile(
    r"^\s*(?:"
    r"[*†‡§¶#]"                                  # *  †  ‡ …
    r"|\(?[a-z]\)?[.)]?\s+[A-Z0-9]"              # a) …  y …
    r"|\d{1,2}\s*[A-Z][a-z]"                     # 1Mean …  2Mean …
    r"|(?:Note|Notes|Abbreviations?|Source|Data|Values?|All)\b"
    # 약어 풀이 — 쉼표꼴('Tm, melting temperature;')과 콜론꼴('CD: Crohn's
    # disease, CI: Confidence interval') 둘 다 쓰인다(실측: 후자는
    # 10.4103/ijdvl.ijdvl_875_17 Table 3 각주).
    r"|[A-Za-z][A-Za-z0-9/+-]{0,11}[,:]\s+[A-Za-z]"
    r")")
# 약어 풀이가 두 번 이상 이어지면 각주가 확실하다('CI, confidence interval;
# OR, odds ratio.'). 한 번만으로는 본문 문장과 갈라지지 않는다.
_ABBR_GLOSS = re.compile(r"\b[A-Za-z][A-Za-z0-9/+-]{0,11}[,:]\s+[A-Za-z][^;.]{2,60}[;.,]")
FOOT_MAX_GAP = 14.0        # 표 밑선에서 첫 각주 줄까지 허용 거리
FOOT_MAX_DROP = 52.0       # 각주로 훑어 내려갈 최대 높이
FOOT_SIZE_SLACK = 0.6      # 각주는 본문보다 작다 — 이만큼 넘게 크면 각주가 아니다


def _footnote_below(pg: _Page, mode: int, box: tuple[float, float, float, float],
                    stop_y: float, body_h: float) -> str:
    """표 밑선 바로 아래에 붙은 각주 줄들을 이어 붙인다. 없으면 ''.

    첫 줄이 각주 꼴이 아니면 아예 각주가 없는 것으로 본다 — 표 아래 본문
    문단을 각주로 빨아들이지 않기 위한 가장 강한 방어다.
    """
    x0, _y0, x1, y1 = box
    cands: list[tuple[float, float, float, str]] = []
    for ln in pg.lines:
        b = _rot(ln["bbox"], mode)
        if not (y1 - 0.5 < b[1] < min(stop_y, y1 + FOOT_MAX_DROP)):
            continue
        if b[2] < x0 - 8.0 or b[0] > x1 + 8.0:
            continue                       # 옆단 — 이 표의 각주가 아니다
        if _CAP_START.match(ln["text"]) or _HEADING.match(ln["text"]):
            continue
        cands.append((b[1], b[3] - b[1], ln.get("size") or 0.0, ln["text"]))
    cands.sort(key=lambda t: t[0])
    if not cands:
        return ""
    y, h, size, txt0 = cands[0]
    if y - y1 > FOOT_MAX_GAP:
        return ""
    if body_h and size and size > body_h + FOOT_SIZE_SLACK:
        return ""                          # 본문 크기 글씨 = 각주가 아니다
    txt0 = txt0.strip()
    if not (_FOOT_START.match(txt0) or _ABBR_GLOSS.search(txt0)):
        return ""
    parts = [txt0]
    prev_bottom = y + h
    for y, h, size, txt in cands[1:]:
        if y - prev_bottom > max(4.0, 0.9 * (h or 8.0)):
            break                          # 줄 사이가 벌어졌다 → 각주 끝
        if body_h and size and size > body_h + FOOT_SIZE_SLACK:
            break
        parts.append(txt.strip())
        prev_bottom = y + h
    out = _cell_text(" ".join(parts)).replace(r"\|", "|")
    return out.strip()


def _unrot(box: tuple[float, float, float, float], mode: int
           ) -> tuple[float, float, float, float]:
    """_rot 의 역변환 — 회전 좌표계의 상자를 원래 PDF 좌표로 되돌린다."""
    return _rot(box, 0 if mode == 0 else (2 if mode == 1 else 1))


def _continuation(pages: list[_Page], pno: int, num: str, mode: int
                  ) -> tuple[int, tuple] | None:
    """다음 페이지로 이어지는 'Table N (Continued …)' 머리글을 찾는다.

    번호가 같고 'contin' 이 앞머리에 있을 때만 인정한다 — 다른 표를 이어
    붙이지 않기 위한 최소 조건이다.
    """
    if not num:
        return None
    for p in range(pno + 1, min(pno + 3, len(pages))):
        for block in pages[p].blocks:
            for ln in block:
                k = ln["key"]
                if k.startswith("table" + num) and "contin" in k[:60]:
                    return (p, _rot(ln["bbox"], mode))
    return None


def extract_table(pages: list[_Page], caption: str) -> tuple[str, dict]:
    """캡션 하나에 대응하는 표를 복원한다. → (markdown 또는 '', 정보)."""
    info: dict[str, Any] = {"reason": None, "page": None, "region": None,
                            "match": None, "ncols": 0, "nrows": 0}
    hit = find_caption(pages, caption)
    if hit is None:
        info["reason"] = "caption_not_found"
        return "", info
    pno, cap_box, mode, how = hit
    info.update(page=pno + 1, match=how, rotated=bool(mode))
    pg = pages[pno]
    cap_keys = {_key(caption)}
    stop_y = _next_stop_y(pg, mode, cap_box, cap_keys)

    reg = _rule_region(pg, mode, cap_box, stop_y)
    if reg is None:
        # 괘선이 없으면 표의 경계를 좌표만으로 신뢰할 수 없다. 캡션 아래 문단을
        # 표로 만드는 사고가 실제로 가능하므로(GROBID 가 본문 문단을 캡션으로
        # 잘못 잡아 온 표가 이 코퍼스에 2개 있다) **비워 두는 쪽**을 택한다.
        info["reason"] = "no_rules"
        return "", info
    ys, (rx0, rx1) = reg
    box = (rx0, ys[0], rx1, ys[-1])
    info["region"] = "rules"

    grid = _grid_from_region(pg, mode, ys, box)
    if isinstance(grid, str):
        info["reason"] = grid
        return "", info
    header, body, ginfo = grid
    info.update(ginfo)

    # 다음 페이지로 이어지는 표
    num = table_number(caption)
    cont = _continuation(pages, pno, num or "", mode)
    if cont is not None:
        cp, cbox = cont
        cpg = pages[cp]
        cstop = _next_stop_y(cpg, mode, cbox, set())
        creg = _rule_region(cpg, mode, cbox, cstop)
        if creg is not None:
            cys, (cx0, cx1) = creg
            cgrid = _grid_from_region(cpg, mode, cys, (cx0, cys[0], cx1, cys[-1]))
            if not isinstance(cgrid, str) and len(cgrid[0]) == len(header):
                chead, cbody, _ = cgrid
                same = sum(1 for a, b in zip(chead, header) if a == b)
                if same < max(1, len(header) // 2):
                    cbody = [chead] + cbody
                body = body + cbody
                info["continued_page"] = cp + 1
                info["n_body_rows"] = len(body)

    reason = _validate(header, body)
    if reason:
        info["reason"] = reason
        return "", info
    md = to_markdown(header, body)
    info["nrows"] = len(body) + 1
    info["ncols"] = max([len(header)] + [len(r) for r in body])
    info["chars"] = len(md)
    info["unreadable"] = md.count(UNREADABLE)

    # 표가 지면에서 차지한 세로 범위. 원래 PDF 좌표로 되돌려 담는다 —
    # 본문 담당이 '표가 문장을 어디서 끊고 어디서 다시 잇는지' 판정하는 데 쓴다.
    # (실측 근거: 10.1002/jso.23438 DISCUSSION 첫 문단이 '…pivotal for both
    #  accurate LNs, but a recent systematic review…' 로 비문이 됐는데, PDF 의
    #  그 자리가 TABLE III 가 단 중간에 조판된 지점이다.)
    ux0, uy0, ux1, uy1 = _unrot((rx0, cap_box[1], rx1, ys[-1]), mode)
    info["span"] = {"page": pno + 1, "x0": round(ux0, 1), "y0": round(uy0, 1),
                    "x1": round(ux1, 1), "y1": round(uy1, 1),
                    "caption_y0": round(_unrot(cap_box, mode)[1], 1),
                    "body_y0": round(_unrot((rx0, ys[0], rx1, ys[0]), mode)[1], 1)}
    if mode:
        # 눕혀 조판된 표. x0..y1 은 PDF 좌표로 맞지만 caption_y0/body_y0 은
        # '위→아래' 순서를 뜻하지 않는다(캡션이 왼쪽에서 세로로 선다).
        info["span"]["rotated"] = True
    if info.get("continued_page"):
        info["span"]["continued_page"] = info["continued_page"]

    # 표 본문 글자 크기의 **천장**. 각주는 표 본문보다 크지 않다.
    # 중앙값을 쓰면 안 된다 — 기호 글꼴(★ 등)이 작은 크기로 잡혀 중앙값을
    # 끌어내린다(실측: 10.1002/jso.23438 Table III 은 6.0pt 67줄 · 7.5pt 52줄
    # 이라 중앙값이 6.0 이 되어 7.5pt 각주가 통째로 버려졌다).
    sizes = [ln.get("size") or 0.0 for ln in pg.lines
             if ys[0] < (_rot(ln["bbox"], mode)[1]) < ys[-1]
             and rx0 - 6 < _rot(ln["bbox"], mode)[0] < rx1 + 6]
    sizes = [s for s in sizes if s]
    body_h = max(sizes) if sizes else 0.0
    note = _footnote_below(pg, mode, (rx0, ys[0], rx1, ys[-1]), stop_y, body_h)
    note = " ".join(x for x in (info.pop("tail_note", ""), note) if x).strip()
    if note:
        info["footnote"] = note
    return md, info


# ── PDF 에서 표를 **처음부터** 찾아낸다 ──────────────────────────────
# 배경: 윈도우 GROBID 에 딸린 pdfalto 0.1 은 표 검출이 리눅스보다 60% 나쁘다
# (같은 134편에서 TEI 표 231 → 93). 실측으로 확인한 최악의 경우는
# 10.1002/jso.23438 — PDF 에 TABLE I·II·III 가 있는데 GROBID 는 표 0개를
# 내놨다. `tables[]` 가 비어 있으면 '빈 표 채우기'는 채울 대상이 없다.
# 그래서 캡션을 PDF 에서 직접 찾아 표를 새로 만든다.
#
# **오염이 이 경로의 유일한 위험이다.** 레터·단신은 한 지면에 여러 편이 실려
# 옆 논문 표를 가져오기 쉽다(실증: figtab.py 가 10.1016/j.jaad.2016.05.022 에
# 이웃 레터의 Table II 를 붙였고 그래서 파이프라인에 연결되지 못했다).
# 이제 boundary.py 가 구간을 알려 주므로 **소속이 확인된 캡션만** 받는다.
# 판정기를 못 얻으면 discovery 를 아예 켜지 않는다(reason="no_boundary").
_CAP_HEAD_KEY = re.compile(
    r"^(?:supplement(?:al|ary)?|appendix|online|web|extended|additional|e)?"
    r"tab(?:le)?(?:[ivxl]{1,5}|\d{1,3})(?!\d)")
# 'Table 2 shows the ocular factors of the two groups…' 처럼 **본문 문장**이
# 표 번호로 시작하는 일이 있다(실측: 10.1016/j.jcjo.2018.04.020 3쪽 — 이 문장이
# 캡션으로 잡혀 없는 표가 하나 생겼다). 진짜 캡션은 번호 뒤가 대문자로 시작한다
# ('Table 2 Incidence of…', 'TABLE II. The Characteristics…', 'Table. Demographic…').
# 소문자로 이어지면 문장이다.
_CAP_AFTER_NUM = re.compile(
    r"^\s*(?:supplement(?:al|ary)?|appendix|online|web|extended|additional)?\s*"
    # 'TA B L E 1' — Wiley 계열은 표 머리글의 자간을 벌린다. 글자 사이
    # 공백을 허용하지 않으면 이 저널의 표를 통째로 놓친다.
    r"t\s*a\s*b(?:\s*l\s*e)?\.?\s*(?:[IVXL]{1,5}|\d{1,3}|[A-Z]\d{1,2})?"
    r"[.:)|\s  \-–—]*(.)", re.I)


def _caption_shaped(caption: str) -> bool:
    m = _CAP_AFTER_NUM.match(caption or "")
    if not m:
        return False
    c = m.group(1)
    return c.isupper() or c.isdigit() or not c.isalpha()
CAP_MAX_LINES = 4          # 캡션으로 이어 붙일 최대 줄 수
CAP_MAX_CHARS = 260        # 캡션으로 이어 붙일 최대 길이


def find_table_captions(pages: list[_Page]) -> list[dict]:
    """PDF 안의 표 캡션 블록. → [{page, caption, number}] (읽기순서)."""
    out: list[dict] = []
    for pno, pg in enumerate(pages):
        for block in pg.blocks:
            if not block or not _CAP_HEAD_KEY.match(_key(block[0]["text"])):
                continue
            parts: list[str] = []
            for ln in block[:CAP_MAX_LINES]:
                parts.append(ln["text"].strip())
                joined = " ".join(parts)
                if len(joined) >= CAP_MAX_CHARS or joined.rstrip().endswith("."):
                    break
            caption = utils.norm_text(" ".join(parts))
            if len(_key(caption)) < CAP_MIN_KEY or not _caption_shaped(caption):
                continue
            out.append({"page": pno + 1, "caption": caption,
                        "number": table_number(caption)})
    return out


def _body_sections(doc: dict) -> list[dict]:
    """정본 본문 절 목록. 스키마 이름이 sections → body_text 로 바뀌어 둘 다 본다."""
    return doc.get("body_text") or doc.get("sections") or []


def _body_text(doc: dict) -> str:
    return " ".join((p.get("text") or "")
                    for s in _body_sections(doc)
                    for p in (s.get("paragraphs") or []))


def fallback_tables(pdf_path) -> list[dict]:
    """pdf_fallback 의 표 찾기를 빌려 후보를 얻는다. → [{caption, markdown}].

    윈도우 GROBID 는 번들 pdfalto 가 0.1 이라 표를 잘 놓친다(실측:
    10.1002/jso.23438 은 PDF 에 TABLE I·II·III 가 있는데 GROBID 표 0개).
    같은 PDF 를 pdf_fallback 의 캡션·영역 탐색으로 훑으면 3개가 나온다.
    그래서 GROBID 경로에서도 이 탐색을 **후보로만** 돌려 합친다.
    (pdf_fallback 은 다른 담당 모듈이라 읽기만 한다 — 공개 진입점
     pdf_figures_tables() 만 부른다.)
    """
    try:
        from . import pdf_fallback
        _figs, tabs = pdf_fallback.pdf_figures_tables(pdf_path)
    except Exception:                     # noqa: BLE001 — 후보가 없어도 본 경로는 돈다
        return []
    out: list[dict] = []
    for t in tabs:
        cap = (getattr(t, "caption", "") or "").strip()
        if cap:
            out.append({"caption": cap,
                        "markdown": getattr(t, "markdown", "") or "",
                        "src_id": getattr(t, "id", "")})
    return out


def _cap_core(caption: str) -> str:
    """캡션에서 'Table N' 머리를 떼어낸 대조 키.

    같은 표를 두 번 만들지 않으려면 이 키로 비교해야 한다. GROBID 는 머리를
    잃은 캡션('Strength of Recommendation Taxonomy (SORT)3')을 내놓는데,
    PDF 에서 찾은 캡션은 머리가 붙어 있다('TABLE 2. Strength of Recommendation
    Taxonomy (SORT)'). 머리를 붙인 채 비교하면 서로 다른 표로 보인다
    (실측: 10.1111/phpp.12598 에서 SORT 표가 두 벌 생겼다).
    """
    m = _CAP_AFTER_NUM.match(caption or "")
    core = caption[m.start(1):] if m else (caption or "")
    return _key(core)[:60]


def _mentioned_numbers(doc: dict) -> set[str]:
    """본문이 언급한 'Table N' 번호 집합(교차검증용)."""
    txt = [_body_text(doc)]
    nums: set[str] = set()
    for m in re.finditer(r"\bTables?\s+([IVXL]{1,5}|\d{1,3})\b",
                         "\n".join(txt), re.I):
        n = table_number("Table " + m.group(1))
        if n:
            nums.add(n)
    return nums


def discover_tables(doc: dict, pages: list[_Page], bmap: Any,
                    src_doc: dict | None = None, pdf_path=None
                    ) -> tuple[list[dict], dict]:
    """PDF 에서 아직 정본에 없는 표를 찾아낸다. → (새 표 목록, 통계).

    소속 판정기(bmap)가 없거나 자신 없으면 **아무것도 만들지 않는다.**
    이웃 논문 표를 붙이느니 표가 없는 편이 낫다.
    """
    st: dict[str, Any] = {"candidates": 0, "added": 0, "skipped": {},
                          "items": [], "mentioned": sorted(_mentioned_numbers(doc))}
    if bmap is None:
        st["reason"] = "no_boundary"
        return [], st
    # 오염을 막을 수 있는 두 경우에만 연다.
    #   (a) 한 PDF 에 논문이 하나뿐 — 가져올 이웃이 없다.
    #       (boundary 는 이때 confident=False, reason='구간 1개 — 합본 지면이
    #        아님' 을 돌려준다. '자신 없음'이 아니라 '나눌 것이 없음'이다.)
    #   (b) 합본 지면이지만 boundary 가 내 구간을 확정했다 — owner() 로 거른다.
    single = len(getattr(bmap, "segments", []) or []) <= 1
    sure = bool(getattr(bmap, "confident", False)) and getattr(bmap, "own", None) is not None
    if not (single or sure):
        st["reason"] = "boundary_unsure"
        return [], st
    st["gate"] = "single_article" if single else "boundary_confident"

    def skip(k: str) -> None:
        st["skipped"][k] = st["skipped"].get(k, 0) + 1

    have = {table_number(t.get("caption") or "")
            for t in (doc.get("tables") or [])}
    have.discard(None)
    seen: set[str] = set()
    seen_caps: set[str] = set()
    new: list[dict] = []
    # 인코딩 게이트는 **새로 뽑은 문자열**로 판정해야 열린다(정본은 이미
    # textfix 를 거쳐 서명이 지워져 있다). 그래서 먼저 전부 뽑아 둔다.
    cands = list(find_table_captions(pages))
    # 두 번째 후보원 — pdf_fallback 의 표 찾기. 캡션을 못 찾는 조판에서
    # 이쪽이 잡아 주는 표가 있다. 번호/캡션으로 합치므로 겹쳐도 안전하다.
    known = {_cap_core(c["caption"]) for c in cands}
    fb = {_cap_core(x["caption"]): x for x in fallback_tables(pdf_path)} if pdf_path else {}
    st["fallback_candidates"] = len(fb)
    for k, x in fb.items():
        if k in known or not _caption_shaped(x["caption"]):
            continue
        cands.append({"page": None, "caption": x["caption"],
                      "number": table_number(x["caption"]),
                      "fallback_md": x["markdown"]})
    raws: dict[int, tuple[str, dict]] = {}
    for i, c in enumerate(cands):
        try:
            raws[i] = extract_table(pages, c["caption"])
        except Exception as e:                # noqa: BLE001
            raws[i] = ("", {"reason": f"error:{type(e).__name__}"})
    enc, _ts = _encoder(src_doc or doc, [m for m, _ in raws.values() if m])
    for ci, cand in enumerate(cands):
        st["candidates"] += 1
        cap, num = cand["caption"], cand["number"]
        ckey = _cap_core(cap)
        if num and (num in have or num in seen):
            skip("already_have")
            continue
        if ckey in seen_caps:
            # 번호가 없는 표('Table. Demographic …')가 다음 쪽으로 이어지면
            # 같은 캡션이 두 번 잡힌다. 캡션 키로도 중복을 막는다.
            skip("duplicate_caption")
            continue
        if any(ckey and (ckey in _cap_core(t.get("caption") or "")
                         or _cap_core(t.get("caption") or "").startswith(ckey[:40]))
               for t in (doc.get("tables") or [])):
            skip("already_have")
            continue
        if re.search(r"\bcontin", cap[:80], re.I):
            skip("continuation")          # 이어짐 머리글은 _continuation 이 붙인다
            continue
        who = "own" if single else bmap.owner(cap)[0]
        if who == "other":
            skip("neighbour_article")     # 이웃 논문 표 — 여기서 막는다
            continue
        md, info = raws[ci]
        if not md and (cand.get("fallback_md") or "").strip():
            # 괘선이 없어 우리 경로가 못 잡은 표는 pdf_fallback 의 본문을 쓴다.
            # 구조 검사를 통과할 때만 받는다(값을 지어내지 않는 경로다).
            fmd = cand["fallback_md"]
            if check_markdown(fmd)["ok"]:
                md, info = fmd, {"reason": None, "region": "pdf_fallback",
                                 "nrows": check_markdown(fmd)["ndata"] + 1,
                                 "ncols": check_markdown(fmd)["ncols"]}
        if not md:
            skip(info.get("reason") or "unknown")
            st["items"].append({"caption": cap[:110], "page": cand["page"],
                                "number": num, "status": "failed",
                                "reason": info.get("reason"), "owner": who})
            continue
        md = enc(md)
        if info.get("footnote"):
            info["footnote"] = enc(info["footnote"])
        t: dict[str, Any] = {
            "id": f"tab_pdf{len(new)}",
            "caption": cap,
            "markdown": md,
            "markdown_source": ("pdf_fallback_discover"
                                if info.get("region") == "pdf_fallback"
                                else "pdf_tablefill_discover"),
            "pdf_span": info.get("span"),
        }
        if info.get("footnote"):
            t["footnote"] = info["footnote"]
            t["footnote_source"] = "pdf_tablefill"
        new.append(t)
        seen_caps.add(ckey)
        if num:
            seen.add(num)
        st["added"] += 1
        st["items"].append({"caption": cap[:110], "page": cand["page"],
                            "number": num, "status": "added", "owner": who,
                            "nrows": info.get("nrows"), "ncols": info.get("ncols")})
    st["found_numbers"] = sorted(n for n in seen if n)
    st["missing_vs_body"] = sorted(
        n for n in st["mentioned"] if n not in have and n not in seen)
    return new, st


def _boundary_map(doc: dict, pdf_path):
    """boundary.BoundaryMap 을 얻는다. 실패하면 None(=discovery 를 끈다)."""
    try:
        from . import boundary
    except Exception:                     # noqa: BLE001
        return None
    meta = doc.get("meta") or {}
    probe = _body_text(doc)
    try:
        return boundary.analyze(pdf_path,
                                {"doi": doc.get("paper_id"),
                                 "title": meta.get("title") or ""},
                                body_probe=probe)
    except Exception:                     # noqa: BLE001 — 판정기가 없으면 discovery 를 안 켠다
        return None


# ── 수리 판정: 새 추출이 기존 markdown 을 **확실히** 이길 때만 바꾼다 ──
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _num_counts(md: str) -> dict[str, int]:
    c: dict[str, int] = {}
    for tok in _NUM.findall(md or ""):
        tok = tok.rstrip(",")
        c[tok] = c.get(tok, 0) + 1
    return c


def better_reason(old_md: str, new_md: str, info: dict) -> str | None:
    """새 추출로 갈아탈 수 없는 이유. 갈아탈 만하면 None.

    임상 논문이다. **틀린 숫자가 든 표는 없는 표보다 훨씬 해롭다.** 그래서
    '더 나아 보인다'가 아니라 **잃는 것이 하나도 없다**를 조건으로 삼는다.
      · 새 표에 못 읽은 글자(U+FFFD)가 있으면 안 된다 — 글꼴 인코딩이 깨진
        조판에서 PDF 텍스트층은 '26·0' 을 '26\\x010' 으로 준다. 여기서는
        GROBID 쪽 값이 더 낫다(실측: 10.1111/bjd.15560 표 3개).
      · 기존 표에 있던 **수 토큰이 하나도 빠지지 않아야** 한다(개수까지).
        이 한 조건이 이웃 논문 표를 끌어오는 사고와 영역을 잘못 잡아 행을
        잘라먹는 사고를 동시에 막는다.
      · 구조 검사를 통과해야 한다.
      · 데이터 행이 기존보다 적으면 안 된다.
    """
    if not new_md:
        return "no_extraction"
    if info.get("unreadable"):
        return "unreadable_glyphs"
    chk_new = check_markdown(new_md)
    if not chk_new["ok"]:
        return "new_" + chk_new["problems"][0]
    chk_old = check_markdown(old_md)
    if chk_new["ndata"] < chk_old["ndata"]:
        return "fewer_rows"
    # 전치 방어. 눕혀 조판된 표(landscape)에서 영역을 잘못 잡으면 행과 열이
    # 뒤바뀐 표가 나온다 — 값은 다 있으니 '수 포함' 검사를 통과해 버린다.
    # 실측: 10.1111/jdv.15936 Table 2 는 PDF(4쪽, 90° 회전 조판)에서 질환 29개가
    # **행**인데 추출본은 8행 × 31열로 질환이 열이 됐다(렌더링으로 확인).
    # 임상 표가 행보다 열이 3배 많은 일은 사실상 없다. 기존 표가 이미 그
    # 모양이면(고칠 것이 없으므로) 따지지 않는다.
    if chk_new["ndata"] and chk_new["ncols"] >= 3 * chk_new["ndata"] \
            and not (chk_old["ndata"] and chk_old["ncols"] >= 3 * chk_old["ndata"]):
        return "looks_transposed"
    old_n, new_n = _num_counts(old_md), _num_counts(new_md)
    missing = [k for k, v in old_n.items() if new_n.get(k, 0) < v]
    if missing:
        return "loses_numbers:" + ",".join(sorted(missing)[:6])
    # 기호 되돌림 방어. textfix 가 이미 고쳐 놓은 자리를 재추출이 되돌리면
    # 안 된다 — 실측: 10.1016/j.jaad.2020.10.061 Table I 의 '42.3 ± 15.3' 이
    # 재추출로 '42.3 6 15.3' 이 됐다(JAAD 조판은 ± 를 '6' 슬롯에 넣는다).
    # '6' 은 숫자로 읽히므로 임상 표에서 특히 해롭다. textfix 의 게이트는
    # 이미 수리된 문서에서 서명이 지워져 열리지 않으므로, 여기서 막는다.
    lost = [c for c in _REPAIRED_SYMBOLS if new_md.count(c) < old_md.count(c)]
    if lost:
        return "loses_symbols:" + "".join(lost)
    return None


# textfix 가 복원해 둔 기호들. 재추출본에서 줄어들면 인코딩이 되돌아간 것이다.
_REPAIRED_SYMBOLS = "±≥≤×·−°⁺"


# ── 문서 단위 ───────────────────────────────────────────────────────
def fill_document(doc: dict, pdf_path, *, repair: bool = True,
                  drop_fake: bool = True, discover: bool = True
                  ) -> tuple[dict, dict]:
    """정본 문서의 표를 PDF 로 채우고·고치고·걸러 낸다. (수정된 doc, 통계).

    입력 doc 은 건드리지 않는다(깊은 복사본을 돌려준다). 네 갈래로 나뉜다.
      1. **관문** — 표가 아닌 것(참고문헌 목록·약어 상자·지면 장식·본문 문단·
         그림)을 `tables[]` 에서 뺀다. 캡션이 'Table N' 이거나 PDF 안의 진짜
         표 캡션에 그 문구가 들어 있으면 **절대** 빼지 않는다.
      2. **채움** — markdown 이 빈 표를 PDF 좌표로 복원한다.
      3. **수리** — markdown 이 있는 표를 전부 재추출해 보고, 새 추출이
         `better_reason() is None` 일 때만 갈아 끼운다. 아니면 그대로 둔다.
      4. **발굴** — GROBID 가 아예 못 찾은 표를 PDF 에서 새로 만든다
         (`discover_tables`). 소속을 확인할 수 없으면 하나도 만들지 않는다.
    복원에 성공한 표에는 각주(`footnote`)와 지면 좌표(`pdf_span`)도 담는다.
    """
    import copy

    import fitz

    out = copy.deepcopy(doc)
    tables = out.get("tables") or []
    stats: dict[str, Any] = {
        "paper_id": out.get("paper_id"),
        "pdf": str(pdf_path),
        "tables_total": len(tables),
        "empty": 0, "filled": 0, "failed": 0,
        "broken": 0, "repaired": 0, "repair_declined": 0,
        "dropped": 0, "footnotes": 0, "discovered": 0,
        "reasons": {}, "declined": {}, "items": [],
    }

    def bump(d: str, k: str) -> None:
        stats[d][k] = stats[d].get(k, 0) + 1

    # PDF 텍스트층에서 새로 뽑은 문자열은 **본문과 같은 인코딩 수리**를 거쳐야
    # 한다. 안 그러면 이미 고쳐져 있던 자리가 되돌아간다 — 실측:
    # 10.1016/j.jaad.2020.10.061 Table I 의 'Age (y) 42.3 ± 15.3' 이 재추출로
    # '42.3 6 15.3' 이 됐다(JAAD 조판은 ± 를 '6' 슬롯에 넣는다). '6' 은 숫자로
    # 읽히므로 임상 표에서는 특히 해롭다. 정책을 두 벌 만들지 않으려고
    # textfix 의 판정을 그대로 빌려 쓴다(멱등이라 뒤에서 또 돌아도 무해하다).
    # 게이트는 **원문 doc 이 아니라 새로 뽑은 문자열**로 열어야 한다. doc 은
    # 이미 textfix 를 거쳐 서명이 지워져 있어(그래서 표에 '±' 가 남아 있다)
    # doc 으로 판정하면 게이트가 늘 닫힌다 — 실측: 10.1016/j.jaad.2020.10.061 은
    # 본문·표가 이미 수리돼 encoding_profile(doc) 이 비었고, 그 결과 재추출한
    # '42.3 6 15.3' 이 그대로 남았다. 그래서 재추출 결과로 프로파일을 만든다.
    enc_probe: list[str] = []
    pdoc = fitz.open(str(pdf_path))
    try:
        pages = [_Page(p) for p in pdoc]
        return _fill_with_pages(out, doc, tables, pages, stats, bump,
                                repair=repair, drop_fake=drop_fake,
                                discover=discover, pdf_path=pdf_path)
    finally:
        pdoc.close()


def _encoder(src_doc: dict, probe: list[str]):
    """textfix 의 인코딩 수리를 이 문서에 맞게 켜고 끈 함수를 만든다."""
    try:
        from . import textfix
    except Exception:                           # noqa: BLE001 — 수리가 없어도 복원은 돈다
        return (lambda s: s), False
    pseudo = {"tables": [{"caption": "", "markdown": t} for t in probe]}
    typeset = bool(textfix.encoding_profile(src_doc)
                   or textfix.encoding_profile(pseudo))
    return (lambda s: textfix.fix_encoding(s, typeset=typeset) if s else s), typeset


def _fill_with_pages(out, src_doc, tables, pages, stats, bump, *,
                     repair, drop_fake, discover=False, pdf_path=None):
    # ── 1. 관문 ─────────────────────────────────────────────────────
    kept: list[dict] = []
    for t in tables:
        cap = (t.get("caption") or "").strip()
        why = fake_table_reason(cap, t.get("markdown") or "") if drop_fake else None
        if drop_fake and not why and not _TABLE_CAPTION.match(cap) \
                and caption_in_pdf_figure(pages, cap):
            why = "figure_not_table"
        if why and caption_in_pdf_table(pages, cap):
            # PDF 에 이 문구를 담은 진짜 표 캡션이 있다 → 지우지 않는다
            why = None
        if why:
            stats["dropped"] += 1
            stats["items"].append({"id": t.get("id"), "status": "dropped",
                                   "reason": why, "caption": cap[:120]})
            continue
        kept.append(t)
    out["tables"] = tables = kept

    # 수리는 **구조 검사에 걸린 표에만** 걸지 않는다. 열 밀림·셀 병합처럼
    # markdown 구조만 봐서는 멀쩡해 보이는 결함이 있기 때문이다(실측:
    # 'Scatizzi | Italy | Cohort (M) 30 (14/16) 30 (16/14) | 69 (43-86)' —
    # 세 값이 한 칸에 뭉쳤는데 행 길이는 일정하다). 대신 **받아들이는 조건**을
    # 좁게 잡는다(better_reason). 새 추출이 기존 값을 하나도 잃지 않을 때만
    # 갈아 끼우므로, 전부 시도해도 안전하다.
    todo = []
    for t in tables:
        md = (t.get("markdown") or "").strip()
        if not md:
            stats["empty"] += 1
            todo.append((t, "fill"))
            continue
        if not check_markdown(md)["ok"]:
            stats["broken"] += 1
        todo.append((t, "repair" if repair else "note"))
    if not todo and not discover:
        return out, stats

    # 1차: 원문 그대로 뽑아 둔다(인코딩 게이트를 이 결과로 판정하기 위해).
    raw: list[tuple[str, dict]] = []
    for t, _job in todo:
        caption = (t.get("caption") or "").strip()
        try:
            raw.append(extract_table(pages, caption))
        except Exception as e:                  # noqa: BLE001 — 표 하나 실패로 문서를 버리지 않는다
            raw.append(("", {"reason": f"error:{type(e).__name__}"}))
    enc, typeset = _encoder(src_doc, [m for m, _ in raw if m])
    stats["encoding_typeset"] = typeset

    for (t, job), (md, info) in zip(todo, raw):
        caption = (t.get("caption") or "").strip()
        md = enc(md)
        if info.get("footnote"):
            info["footnote"] = enc(info["footnote"])
        item = {"id": t.get("id"), "job": job, "caption": caption[:120]}
        item.update({k: v for k, v in info.items()
                     if k not in ("span",) and v not in (None, 0, False)})
        if info.get("span"):
            t["pdf_span"] = info["span"]
        if info.get("footnote") and not (t.get("footnote") or "").strip():
            t["footnote"] = info["footnote"]
            t["footnote_source"] = "pdf_tablefill"
            stats["footnotes"] += 1

        if job == "note":
            item["status"] = "note_only"
        elif job == "fill":
            if md:
                t["markdown"] = md
                t["markdown_source"] = "pdf_tablefill"
                stats["filled"] += 1
                item["status"] = "filled"
            else:
                stats["failed"] += 1
                bump("reasons", info.get("reason") or "unknown")
                item["status"] = "failed"
        else:                                   # repair
            why = better_reason(t.get("markdown") or "", md, info)
            if why is None:
                item["before"] = check_markdown(t["markdown"])["problems"]
                t["markdown_before_repair"] = t["markdown"]
                t["markdown"] = md
                t["markdown_source"] = "pdf_tablefill_repair"
                stats["repaired"] += 1
                item["status"] = "repaired"
            else:
                stats["repair_declined"] += 1
                bump("declined", why.split(":")[0])
                item["status"] = "declined"
                item["declined"] = why[:120]
        stats["items"].append(item)

    # ── 4. 발굴 — GROBID 가 아예 못 찾은 표를 PDF 에서 새로 만든다 ──
    if discover:
        bmap = _boundary_map(src_doc, pdf_path)
        found, dst = discover_tables(out, pages, bmap, src_doc, pdf_path)
        stats["discover"] = dst
        stats["discovered"] = len(found)
        if found:
            out["tables"] = list(tables) + found
    return out, stats


def _find_pdf(doc: dict, pdf_dirs: list[Path]) -> Path | None:
    """source_file 은 다른 PC 경로일 수 있다 → **파일명만** 떼어 찾는다."""
    raw = (doc.get("source_file") or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].strip()
    if not name:
        return None
    for d in pdf_dirs:
        p = d / name
        if utils.path_exists(p):
            return p
    stem = name[:-4].lower() if name.lower().endswith(".pdf") else name.lower()
    for d in pdf_dirs:
        if not d.is_dir():
            continue
        for p in d.glob("*.pdf"):
            if p.stem.lower() == stem:
                return p
    return None


def run(config: dict | None = None, *, dry_run: bool = True) -> None:
    """normalized/*.json 의 빈 표를 원본 PDF 로 채운다.

    기본이 dry_run=True 다 — 정본을 말없이 고치지 않고, **어떤 파일도 쓰지
    않는다**(보고서 포함). 무엇이 어떻게 복원됐는지는 stderr 요약으로 본다.
    dry_run=False 일 때만 config['tablefill']['output_dir'](work_dir 기준 상대,
    기본 'normalized_tablefilled')에 문서를, ['report'] 에 JSONL 보고서를 쓴다.
    출력이 정본과 분리돼 있으므로 총괄이 대조한 뒤 옮기면 된다.
    """
    cfg = config or utils.load_config()
    opts = (cfg.get("tablefill") or {}) if isinstance(cfg, dict) else {}
    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / (opts.get("input_dir") or "normalized")
    out_dir = work / (opts.get("output_dir") or "normalized_tablefilled")
    report = work / (opts.get("report") or "tablefill_report.jsonl")

    pdf_dirs = [utils.resolve(cfg["project"]["input_dir"])]
    for extra in (opts.get("pdf_dirs") or []):
        pdf_dirs.append(utils.resolve(extra))

    files = sorted(norm_dir.glob("*.json"))
    log(f"[표복원] 빈 표 채우기: {len(files)}편 @ {norm_dir}"
        + (" (DRY-RUN: 파일을 쓰지 않는다)" if dry_run else f" → {out_dir}"))
    if not files:
        log(f"        → 정본 문서가 없다. 0~4단계를 먼저 실행할 것: {norm_dir}")
        return
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    n_nopdf = n_docs = n_wrote = 0
    tot = {"empty": 0, "filled": 0, "failed": 0, "broken": 0, "repaired": 0,
           "repair_declined": 0, "dropped": 0, "footnotes": 0, "discovered": 0}
    reasons: dict[str, int] = {}
    for i, src in enumerate(files, 1):
        try:
            doc = utils.read_json(src)
        except Exception as e:                  # noqa: BLE001 — 파일 단위 격리
            log(f"  [{i}/{len(files)}] 읽기 실패({src.name}): {type(e).__name__}: {e}")
            continue
        # **빈 표가 있는 문서만 보면 안 된다.** 관문·수리·발굴은 표가 채워져
        # 있는 문서에서도 할 일이 있다(가짜 표 지우기, 열 밀림 수리, GROBID 가
        # 통째로 놓친 표 발굴). 예전에는 여기서 걸러 내 그 셋이 아예 돌지 않았다.
        n_docs += 1
        tot["empty"] += sum(1 for t in (doc.get("tables") or [])
                            if not ((t or {}).get("markdown") or "").strip())
        pdf = _find_pdf(doc, pdf_dirs)
        if not pdf:
            n_nopdf += 1
            reasons["pdf_not_found"] = reasons.get("pdf_not_found", 0) + 1
            rows.append({"paper_id": doc.get("paper_id"),
                         "reasons": {"pdf_not_found": 1}})
            log(f"  [{i}/{len(files)}] PDF 없음: {doc.get('paper_id')}")
            continue
        try:
            fixed, st = fill_document(doc, pdf)
        except Exception as e:                  # noqa: BLE001 — 문서 단위 격리
            utils.record_failure(work, "tablefill", str(src), e)
            log(f"  [{i}/{len(files)}] 실패(계속): {doc.get('paper_id')} — "
                f"{type(e).__name__}: {e}")
            continue
        for k in tot:
            if k != "empty":
                tot[k] += st.get(k, 0)
        for k, v in st["reasons"].items():
            reasons[k] = reasons.get(k, 0) + v
        rows.append(st)
        changed = (st["filled"] or st["repaired"] or st["dropped"]
                   or st["discovered"] or st["footnotes"])
        if changed:
            log(f"  [{i}/{len(files)}] {doc.get('paper_id')}: "
                f"채움 {st['filled']} · 수리 {st['repaired']} · 삭제 {st['dropped']}"
                f" · 발굴 {st['discovered']} · 각주 {st['footnotes']}")
        if not dry_run and changed:
            utils.write_json(out_dir / src.name, fixed)
            n_wrote += 1

    log(f"[표복원] 문서 {n_docs}편 · 빈 표 {tot['empty']}개 → "
        f"채움 {tot['filled']} / 실패 {tot['failed']} · "
        f"수리 {tot['repaired']}(보류 {tot['repair_declined']}) · "
        f"가짜 삭제 {tot['dropped']} · 발굴 {tot['discovered']} · "
        f"각주 {tot['footnotes']}"
        + (f" · PDF 없음 {n_nopdf}편" if n_nopdf else ""))
    if reasons:
        log("        실패 사유: "
            + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items(),
                                                      key=lambda kv: -kv[1])))
    if dry_run:
        log("        DRY-RUN 이라 아무 파일도 쓰지 않았다. 실제 적용은 "
            "run(dry_run=False).")
        return
    utils.write_jsonl(report, rows)
    log(f"        문서 {n_wrote}편 기록 → {out_dir} · 보고서 → {report}")
