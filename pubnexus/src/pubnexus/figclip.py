"""4.8단계 — 논문의 **그림을 실제 이미지로** 잘라 PNG 로 낸다.

지금까지 파이프라인에는 그림을 잘라내는 단계가 아예 없었다. `figures[].image`
는 전 코퍼스에서 전부 None 이고 캡션만 담겼다. 앱은 캡션만 띄우고 그림은 못
띄운다. 이 모듈이 그 한 칸을 채운다.

── 두 갈래 ─────────────────────────────────────────────────────────
그림이 PDF 에 들어 있는 방식은 둘뿐이다.

  (a) **내장 이미지** — 사진·현미경 사진·임상 사진. `page.get_images(full=True)`
      로 xref 를, `page.get_image_rects(xref)` 로 지면 좌표를 정확히 얻는다.
      실측: 이 코퍼스 205편에 내장 이미지 827개.
  (b) **벡터 도형** — forest plot·flow diagram·Kaplan-Meier 곡선. 파일로 박혀
      있지 않고 선·도형으로 그려져 있어 **영역을 잘라내야** 한다.
      `page.get_drawings()` 의 도형 bbox 합집합이 실제로 그려진 범위를 준다.

두 갈래를 따로 다루지 않는다. 이미지 사각형과 도형 사각형을 **같은 조각(piece)**
으로 보고 한 절차에 태운다. 한 그림이 여러 조각(패널 a·b·c)으로 박혀 있으면
합친 bbox 로 한 장을 만든다.

── 영역을 잡는 근거 ────────────────────────────────────────────────
가장 강한 단서는 **캡션의 좌표**다. captions.py 가 캡션의 bbox 를 글꼴·크기
근거로 이미 확정해 둔다. 그림은 캡션 바로 위(드물게 아래)에 있다.

  1. **세로**: 캡션에서 위로 올라가며 **본문 글줄 덩어리(블록)** 가 나오면 거기서
     멈춘다. 다른 캡션·절 제목·러닝헤드·표 영역(tablefill 의 `pdf_span`)도 같은
     장벽이다. 장벽이 없으면 지면 윗 여백까지.
  2. **가로**: 그 높이에 **옆 단 본문 글이 있느냐**로 정한다(`_side_limits`).
     글이 있으면 그 앞에서 끊고, 없으면(그림이 전폭으로 자리를 차지했다는 뜻)
     지면 끝까지 연다. 옆 단과의 중간점으로 못박으면 캡션은 한 단인데 그림은
     전폭인 조판에서 그림이 반쪽 난다.
  3. 그 구간 안의 조각들의 **합집합 bbox** 로 범위를 좁힌다. 조각이 하나도 없으면
     **자르지 않는다** — 그림이 없는 것이 엉뚱하게 잘린 그림보다 낫다.
  4. 캡션을 그림 **옆**에 좁고 길게 세워 싣는 조판(Springer)도 있다. 캡션 덩어리가
     좁고 길면(높이 ≥ 폭의 0.5배) 그 옆을 먼저 본다.

'본문 글줄'을 블록 단위로 보는 이유: 문단의 **마지막 줄은 짧다**. 줄 단위로
'긴 줄'만 장벽으로 삼으면 그 짧은 꼬리 한 줄이 그림 영역 안에 남는다. 블록
전체를 장벽으로 삼으면 그런 일이 없다.

반대로 **그림 안의 글자는 본문이 아니다** — 축 라벨·패널 문자·범례·flow diagram
상자 안의 글. 이것들은 (ㄱ) 본문보다 작거나 (ㄴ) 단 폭의 절반이 안 되거나
(ㄷ) 큰 조각 안에 들어 있다. 셋 중 하나면 본문 블록으로 세지 않는다.

── 이웃 논문 오염 차단 ─────────────────────────────────────────────
합본 지면(레터 여러 편)에서 옆 논문의 그림을 가져오면 안 된다. 판정은
**boundary.py 가 한다**(captions.py 와 같은 규약). boundary 가 구간을 확신하지
못하면 캡션 소유가 unknown 이고, unknown 캡션은 남기되 그 그림 영역이 이웃
구간의 글을 물면 버린다(`_contaminated`). boundary.analyze 가 아예 실패하면
**한 장도 만들지 않는다.**

── 지면 장식 ───────────────────────────────────────────────────────
로고·저널 마크·아이콘·QR 은 그림이 아니다. 신호 세 가지로 버린다.
  · 아주 작다(짧은 변 26pt 미만 또는 넓이 1,300pt² 미만)
  · **두 쪽 이상의 같은 자리에 반복**된다(같은 xref, 좌상단 ±6pt)
  · 지면 폭을 가로지르는 얇은 장식선, 지면의 55% 이상을 덮는 배경 사각형
지면 위·아래 5% 띠(러닝헤드·꼬리말 자리)도 아예 보지 않는다.

── 저장 ────────────────────────────────────────────────────────────
JSON 옆 `<paper_id slug>_figs/` 폴더에 넣고, `figures[].image` 에는 **JSON 기준
상대경로**를 담는다(절대경로 금지 — 폴더를 옮기면 깨진다). 폴더는 한 편에
하나이고, 다시 돌리면 그 폴더를 이번 산출물로 맞춘다(멱등).

기본 형식은 PNG(170dpi)다. 한 장이 MAX_BYTES 를 넘으면 DPI 를 96까지 낮추고,
그래도 넘으면 그때만 JPEG 로 바꾼다 — 임상 사진은 PNG 로 담으면 96dpi 에서도
800KB 를 넘는다(실측). exe 로 묶어 배포하므로 **PyMuPDF 말고 아무것도 쓰지
않는다.**

── 실측(정본 사본 52편 · 그림 130개) ───────────────────────────────
  · 119개(92%) 추출 — 내장 이미지 57 · 벡터 20 · 섞임 42
  · 이웃 논문 오염 0건 · 총 25.1MB · 한 장 평균 206KB · 최대 457KB
  · 119장 전부를 대조표로 눈으로 확인했다(본문 문장이 섞인 것 0장).
  · 못 뽑은 11개: 캡션이 PDF 에 없음 4 · 조각 없음 2(지면 전체를 덮는 배경) ·
    눕힌 캡션 1 · 번호 모호 1 · 그림 캡션을 못 찾은 문서 1편(3개)
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import captions as _cap

# ── 튜닝 상수(pt 단위. 이 코퍼스 실측으로 정했다) ────────────────────
MIN_PIECE_SIDE = 26.0       # 조각의 짧은 변이 이보다 작으면 장식(불릿·아이콘)
MIN_PIECE_AREA = 1300.0     # 조각 넓이 하한
MIN_FIG_W = 54.0            # 그림으로 인정할 최소 가로
MIN_FIG_H = 40.0            # 그림으로 인정할 최소 세로
MIN_FIG_AREA = 4200.0       # 그림으로 인정할 최소 넓이
EDGE_ZONE = 0.05            # 지면 위·아래 이 비율은 러닝헤드/꼬리말 자리
SIDE_MARGIN = 22.0          # 옆 단이 없을 때 좌우로 넓혀 볼 여유
PIECE_IN_BAND = 0.60        # 조각이 이 비율 이상 단 안에 있어야 채택
PIECE_IN_REGION = 0.65      # 조각이 이 비율 이상 구간 안에 있어야 채택
PAD = 2.5                   # 잘라낼 때 사방 여유
CAP_GAP = 3.0               # 캡션과의 최소 간격(캡션 글자가 그림에 남지 않게)
PROSE_MIN_CHARS = 48        # 본문 블록으로 볼 최소 글자 수
PROSE_MIN_WIDTH = 0.52      # 본문 블록으로 볼 최소 폭(단 폭 대비)
PROSE_SIZE_TOL = 0.8        # 본문 크기로 볼 허용 오차(pt)
BIG_PIECE_AREA = 2600.0     # 이보다 큰 조각 안의 글자는 '그림 속 글자'
GAP_MAX = 96.0              # 조각 무리가 이보다 벌어지면 다른 그림이다
LABEL_GAP = 30.0            # 도형에서 이 안쪽에 있는 글자만 그림 라벨로 붙인다
CAPTION_SLACK = 70.0        # 옆 단 그림의 캡션은 그림 아래 이만큼까지 내려간다
DPI_DEFAULT = 170           # 기본 해상도
DPI_MIN = 96                # 더 낮추지 않는다(읽을 수 없어진다)
MAX_BYTES = 480_000         # PNG 한 장의 상한
MAX_PIXELS = 2200           # 긴 변 픽셀 상한

_HEADING_RE = re.compile(
    r"^\s*(?:abstract|introduction|background|materials?\s+and\s+methods?|"
    r"patients?\s+and\s+methods?|methods?|results?|discussion|conclusions?|"
    r"references?|acknowledge?ments?|conflicts?\s+of\s+interest|funding|"
    r"supplementary|case\s+report|limitations?)\b\s*[:.]?\s*$", re.I)


# ── 기하 헬퍼 ────────────────────────────────────────────────────────
def _area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _inter(a, b) -> float:
    return (max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            * max(0.0, min(a[3], b[3]) - max(a[1], b[1])))


def _union(boxes) -> tuple[float, float, float, float]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _covered(inner, outer) -> float:
    """inner 가 outer 안에 들어 있는 넓이 비율(0~1)."""
    a = _area(inner)
    return _inter(inner, outer) / a if a > 0 else 0.0


def _xshare(box, x0: float, x1: float) -> float:
    """box 의 가로 폭 중 [x0,x1] 에 들어오는 비율."""
    w = box[2] - box[0]
    if w <= 0:
        return 0.0
    return max(0.0, min(box[2], x1) - max(box[0], x0)) / w


# ── 조각(piece) 모으기 ───────────────────────────────────────────────
@dataclass
class _Piece:
    kind: str                                   # 'image' | 'draw'
    rect: tuple[float, float, float, float]
    xref: int = 0


@dataclass
class _FigPage:
    """한 쪽의 그림 재료 — 조각·글줄 블록·단 경계."""
    pno: int
    rect: tuple[float, float, float, float]
    pieces: list[_Piece] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)   # 글줄 블록
    cols: list[tuple[float, float]] = field(default_factory=list)
    tables: list[tuple[float, float, float, float]] = field(default_factory=list)

    @property
    def top(self) -> float:
        return self.rect[1] + (self.rect[3] - self.rect[1]) * EDGE_ZONE

    @property
    def bottom(self) -> float:
        return self.rect[3] - (self.rect[3] - self.rect[1]) * EDGE_ZONE

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]


def _image_pieces(page, decor_xrefs: set[int]) -> list[_Piece]:
    """내장 이미지 → 조각. 장식 xref 와 너무 작은 것은 버린다."""
    out: list[_Piece] = []
    try:
        imgs = page.get_images(full=True)
    except Exception:                            # noqa: BLE001
        return out
    for item in imgs:
        xref = int(item[0])
        if xref in decor_xrefs:
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:                        # noqa: BLE001
            continue
        for r in rects:
            box = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
            if min(box[2] - box[0], box[3] - box[1]) < MIN_PIECE_SIDE:
                continue
            if _area(box) < MIN_PIECE_AREA:
                continue
            out.append(_Piece("image", box, xref))
    return out


def _draw_pieces(page) -> list[_Piece]:
    """벡터 도형 → 조각.

    지면 전체를 덮는 배경 사각형과 머리·꼬리 장식선은 버린다. 그것들이 남으면
    합집합 bbox 가 지면 전체가 되어 좁히기가 아무 일도 못 한다.
    """
    out: list[_Piece] = []
    pr = page.rect
    pw, ph = float(pr.width or 1.0), float(pr.height or 1.0)
    page_area = pw * ph
    try:
        drawings = page.get_drawings()
    except Exception:                            # noqa: BLE001 — 그래픽 파싱 실패는 치명적이지 않다
        return out
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        box = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
        w, h = box[2] - box[0], box[3] - box[1]
        if w <= 0 or h <= 0:
            continue
        if _area(box) >= 0.55 * page_area:       # 지면 배경
            continue
        if h <= 3.0 and w >= 0.70 * pw:          # 머리·꼬리 장식선
            continue
        out.append(_Piece("draw", box))
    return out


def _decoration_xrefs(doc) -> set[int]:
    """여러 쪽의 **같은 자리**에 반복되는 작은 이미지 = 로고·저널 마크·워터마크."""
    from collections import defaultdict

    n = doc.page_count
    if n < 2:
        return set()
    seen: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    size: dict[int, float] = {}
    for pno in range(n):
        page = doc[pno]
        try:
            imgs = page.get_images(full=True)
        except Exception:                        # noqa: BLE001
            continue
        for item in imgs:
            xref = int(item[0])
            try:
                rects = page.get_image_rects(xref)
            except Exception:                    # noqa: BLE001
                continue
            for r in rects:
                seen[xref].append((pno, round(float(r.x0)), round(float(r.y0))))
                size[xref] = max(size.get(xref, 0.0),
                                 float(r.width) * float(r.height))
    out: set[int] = set()
    for xref, hits in seen.items():
        pages = {p for p, _, _ in hits}
        if len(pages) < 2:
            continue
        # **같은 자리에 두 쪽 이상** 반복되면 지면 장식이다(저널 로고·마크·워터마크).
        # 진짜 그림이 서로 다른 쪽의 같은 좌표에 실리는 일은 없다. 처음에는
        # '절반 이상의 쪽'을 요구했는데, 홀수 쪽에만 찍는 로고를 놓쳤다
        # (실측 10.5021/ad.23.151 의 ANNALS of DERMATOLOGY 마크가 그림에 들어왔다).
        xs = [x for _, x, _ in hits]
        ys = [y for _, _, y in hits]
        same_spot = (max(xs) - min(xs) <= 6 and max(ys) - min(ys) <= 6)
        if same_spot and size.get(xref, 0.0) < 0.25 * _PAGE_AREA_REF:
            out.add(xref)
        elif (len(pages) >= max(2, math.ceil(n * 0.5))
                and size.get(xref, 0.0) < 6000.0):
            out.add(xref)                        # 작고 여러 쪽에 흩어져 있으면 장식
    return out


_PAGE_AREA_REF = 595.0 * 842.0                   # A4(pt²) — 크기 기준용


def _running_heads(doc, npages: int) -> set[str]:
    """여러 쪽의 위·아래 12% 띠에 **반복되는 줄** = 러닝헤드/꼬리말.

    러닝헤드는 짧아서(실측 'Lipid Metabolism in Atopic Dermatitis' 37자) 산문
    문턱(48자)에 걸리지 않는다. 그러면 지면 맨 위 그림의 장벽이 없어져 저널
    표제와 로고가 그림 안에 들어온다. boundary.py 의 `_running` 과 같은 근거
    (**반복 + 위치**)로 따로 잡는다.
    """
    from collections import defaultdict

    if npages < 2:
        return set()
    seen: dict[str, set[int]] = defaultdict(set)
    for pno in range(npages):
        page = doc[pno]
        h = float(page.rect.height or 1.0)
        try:
            blocks = page.get_text("blocks")
        except Exception:                        # noqa: BLE001
            continue
        for b in blocks:
            if b[1] > h * 0.12 and b[3] < h * 0.88:
                continue
            key = _norm_head(b[4] or "")
            if len(key) >= 6:
                seen[key].add(pno)
    return {k for k, ps in seen.items() if len(ps) >= 2}


def _norm_head(s: str) -> str:
    """러닝헤드 비교용 키 — **숫자를 버린다**.

    러닝헤드에는 쪽번호가 붙는다('132 Choi et al.' · '436 BAE ET AL'). 숫자를
    남기면 쪽마다 다른 문자열이 되어 '반복'으로 잡히지 않고, 그러면 지면 맨 위
    그림의 장벽이 사라져 저자 러닝헤드가 그림 안에 들어온다(실측
    10.1002/jso.23618 · 10.1002/lsm.22358).
    """
    return re.sub(r"[^a-z가-힣]+", "", (s or "").lower())[:60]


def _page_blocks(lines: list[_cap.Line], body: float, pieces: list[_Piece],
                 cols: list[tuple[float, float]], heads: set[str]) -> list[dict]:
    """한 쪽의 글줄을 PyMuPDF 블록 단위로 묶고 '본문 블록'인지 표시한다.

    문단의 **마지막 줄은 짧다**. 줄 단위로 '긴 줄'만 장벽으로 삼으면 그 꼬리가
    그림 영역에 남는다. 그래서 블록을 통째로 장벽으로 쓴다.

    폭 기준은 **그 블록이 놓인 단**으로 잰다. 캡션이 두 단을 가로지르면 기준
    폭이 지면 전체가 되어 한 단짜리 문단이 산문으로 인정되지 않는다(실측
    10.1080/14397595.2016.1211229 3쪽·10.5021/ad.23.151 3쪽 — 오른쪽 단 본문
    문단이 통째로 그림 안에 들어왔다).
    """
    from collections import defaultdict

    big = [p.rect for p in pieces if _area(p.rect) >= BIG_PIECE_AREA]
    grp: dict[int, list[_cap.Line]] = defaultdict(list)
    for ln in lines:
        grp[ln.block].append(ln)
    out: list[dict] = []
    for bid, ls in grp.items():
        box = (min(l.x0 for l in ls), min(l.y0 for l in ls),
               max(l.x1 for l in ls), max(l.y1 for l in ls))
        chars = sum(len(l.text.strip()) for l in ls)
        sizes = sorted(l.size for l in ls)
        med = sizes[len(sizes) // 2]
        # 그림 속 글자인가 — 큰 조각 안에 들어 있으면 본문이 아니다
        in_piece = any(_covered(box, r) >= 0.60 for r in big)
        # 이 블록이 놓인 단의 폭(가장 많이 덮는 단). 못 찾으면 블록 폭 그대로.
        colw = box[2] - box[0]
        best = 0.0
        for c in cols:
            share = _xshare(box, c[0], c[1])
            if share > best and share >= 0.7:
                best, colw = share, max(1.0, c[1] - c[0])
        txt = " ".join(l.text.strip() for l in ls)
        # 눕힌 조판(세로 글) — 줄 상자가 저마다 가로보다 세로로 길다.
        # 세로로 눕힌 표가 그림 옆에 실리는 일이 있다(실측 10.1016/j.jaad.2016.04.002
        # 의 세로 표가 사진 세 장과 함께 한 장으로 잘렸다).
        tall = sum(1 for l in ls if (l.y1 - l.y0) > (l.x1 - l.x0))
        out.append({"id": bid, "bbox": box, "chars": chars, "size": med,
                    "lines": ls, "in_piece": in_piece, "colw": colw,
                    "rotated": len(ls) >= 4 and tall >= 0.8 * len(ls),
                    "running": _norm_head(txt) in heads,
                    "heading": bool(len(ls) == 1
                                    and _HEADING_RE.match(ls[0].text.strip())),
                    "body_like": abs(med - body) <= PROSE_SIZE_TOL})
    out.sort(key=lambda b: b["bbox"][1])
    return out


def _col_split(lines: list[_cap.Line]) -> float | None:
    """2단 조판의 경계 x. boundary.py 의 판정기를 그대로 빌려 쓴다."""
    from . import boundary

    if not lines:
        return None
    try:
        return boundary._col_split([{"x0": l.x0, "x1": l.x1} for l in lines])
    except Exception:                            # noqa: BLE001
        return None


def _columns(lines: list[_cap.Line], fallback: float | None = None
             ) -> list[tuple[float, float]]:
    """이 쪽의 단(段) 가로 범위.

    captions.Line.col 을 그대로 쓰면 안 된다 — 그 col 은 **왼쪽 끝 군집**이라
    전폭 줄(표제·러닝헤드·전폭 캡션)이 0번 단에 섞이면서 0번 단의 범위가 지면
    전체가 된다(실측 10.3346/jkms.2010.25.6.924 2쪽: 단이 (59,535)·(123,527)·
    (301,538) 로 나왔고, 그 바람에 오른쪽 단 캡션의 '단'이 지면 전체가 됐다).

    그림이 지면을 거의 다 채운 쪽은 본문 줄이 몇 줄 없어 단 판정이 아예 실패한다
    (실측 같은 논문 3쪽: 그림 두 개 때문에 단이 (60,536) 한 개로 나왔고, 그래서
    왼쪽 단 그림의 '단'이 지면 전체가 되어 오른쪽 단 본문이 그림에 들어왔다).
    그럴 때는 **문서 전체로 잰 단 경계**(fallback)를 쓴다 — 학술지는 한 논문
    안에서 판형을 바꾸지 않는다.
    """
    if not lines:
        return []
    split = _col_split(lines)
    if split is None:
        split = fallback
    if split is None:
        return [(min(l.x0 for l in lines), max(l.x1 for l in lines))]
    left = [l for l in lines if l.x1 <= split - 1]
    right = [l for l in lines if l.x0 >= split - 1]
    out = []
    for grp in (left, right):
        if grp:
            out.append((min(l.x0 for l in grp), max(l.x1 for l in grp)))
    return sorted(out, key=lambda c: c[0]) or [
        (min(l.x0 for l in lines), max(l.x1 for l in lines))]


def _read_page(doc, pno: int, lines: list[_cap.Line], body: float,
               decor: set[int], heads: set[str],
               spans: list[tuple[float, float, float, float]],
               split: float | None = None) -> _FigPage:
    page = doc[pno]
    pr = page.rect
    pieces = _image_pieces(page, decor) + _draw_pieces(page)
    pg = _FigPage(pno, (float(pr.x0), float(pr.y0), float(pr.x1), float(pr.y1)))
    # 러닝헤드/꼬리말 띠의 조각은 아예 보지 않는다
    pg.pieces = [p for p in pieces
                 if p.rect[3] > pg.top and p.rect[1] < pg.bottom]
    pg.cols = _columns(lines, split)
    pg.blocks = _page_blocks(lines, body, pg.pieces, pg.cols, heads)
    pg.tables = list(spans)
    return pg


# ── 캡션이 걸친 단(段) → 가로 한계 ───────────────────────────────────
BAND_FILL = 0.75            # 캡션이 단의 이만큼을 채워야 그 단을 '걸쳤다'고 본다


def _band(pg: _FigPage, cap: _cap.Caption) -> tuple[float, float, float, float]:
    """(band_x0, band_x1, safe_x0, safe_x1).

    band 는 캡션이 실제로 걸친 단. safe 는 **옆 단과의 중간점**까지 넓힌 한계다
    — 그림이 본문 단보다 조금 넓게 조판되는 일이 흔하지만, 옆 단의 글을 물어서는
    안 된다.

    단을 '걸쳤다'고 보려면 캡션이 그 단의 **4분의 3 이상**을 채워야 한다. 문턱을
    낮게 잡으면(예전 0.35) 단 판정이 실패해 지면 전체가 한 단으로 나온 쪽에서
    한 단짜리 캡션이 지면 전체를 자기 단이라고 주장한다. 못 미치면 **캡션 폭
    그대로**가 band 다 — 캡션은 그림 아래에 그림 폭으로 조판되므로 이것이 가장
    보수적이면서 정확한 근사다.
    """
    cx0, _cy0, cx1, _cy1 = cap.bbox
    covered = [c for c in pg.cols
               if (c[1] - c[0]) > 0
               and (min(c[1], cx1) - max(c[0], cx0)) >= BAND_FILL * (c[1] - c[0])]
    if covered:
        bx0 = min(min(c[0] for c in covered), cx0)
        bx1 = max(max(c[1] for c in covered), cx1)
    else:
        bx0, bx1 = cx0, cx1
    # 그림은 캡션보다 넓을 수 있다 — 캡션은 그림 아래에 짧게 조판되기도 한다
    # (실측 10.1002/jso.23438 의 forest plot 은 지면 폭인데 캡션이 좁아 왼쪽
    #  연구명 칸이 잘려 나갔다). 그래서 **band 를 품은 단의 폭**까지는 허용한다.
    span = max(1.0, bx1 - bx0)
    host = [c for c in pg.cols
            if (min(c[1], bx1) - max(c[0], bx0)) >= 0.60 * span]
    hx0 = min([c[0] for c in host] + [bx0])
    hx1 = max([c[1] for c in host] + [bx1])
    # 좌우 한계는 일단 지면 끝. 실제 한계는 **그 높이에 옆 단 글이 있느냐**로
    # 정한다(_side_limits). 옆 단과의 중간점으로 못박으면, 캡션은 한 단인데
    # 그림은 전폭인 조판에서 그림이 잘린다(실측 10.5021/ad.2015.27.5.578 Fig 2
    # 는 오른쪽 절반이 통째로 날아갔다).
    return bx0, bx1, pg.rect[0], pg.rect[2]


def _side_limits(pg: _FigPage, cap: _cap.Caption, others: list[_cap.Caption],
                 bx0: float, bx1: float, sx0: float, sx1: float,
                 y0: float, y1: float) -> tuple[float, float]:
    """그림이 놓인 높이 구간 [y0,y1] 에서의 좌·우 한계.

    옆 단의 **본문 글**과 **다른 그림의 캡션**이 한계다. 그 높이에 옆 단 글이
    없으면(그림이 전폭으로 자리를 차지했다는 뜻) 지면 끝까지 열어 둔다.
    """
    band_w = max(1.0, bx1 - bx0)
    lo, hi = sx0, sx1

    def bump(b, slack: float = 0.0) -> None:
        nonlocal lo, hi
        if min(b[3], y1 + slack) - max(b[1], y0) <= 2.0:
            return                               # 이 높이에 걸치지 않는다
        if b[2] <= bx0 + 1:
            lo = max(lo, b[2] + 3.0)
        elif b[0] >= bx1 - 1:
            hi = min(hi, b[0] - 3.0)

    for blk in pg.blocks:
        if _is_barrier(blk, band_w):
            bump(blk["bbox"])
    # 옆 단에 **나란히 실린 다른 그림**. 그 그림의 캡션은 그림 **아래**에 있어
    # 구간과 세로로 안 겹치는 일이 많으므로 아래쪽으로 여유를 준다.
    for o in others:
        if o is not cap and o.page == cap.page:
            bump(o.bbox, CAPTION_SLACK)
    bump(cap.bbox)
    for t in pg.tables:
        bump(t)
    return (lo, hi) if hi - lo >= MIN_FIG_W else (sx0, sx1)


# ── 세로 장벽 ────────────────────────────────────────────────────────
def _is_barrier(blk: dict, band_w: float) -> bool:
    """이 블록이 그림 영역을 끊는 '본문'인가."""
    if blk["heading"] or blk["running"]:
        return True
    # 눕힌 표·세로 조판 글덩어리. 축 라벨도 눕지만 그건 4줄이 안 되고 짧다.
    if blk["rotated"] and blk["chars"] >= PROSE_MIN_CHARS:
        return True
    if blk["in_piece"]:
        return False                             # 그림 속 글자
    if not blk["body_like"]:
        return False                             # 축 라벨·범례는 본문보다 작다
    if blk["chars"] < PROSE_MIN_CHARS:
        return False
    w = blk["bbox"][2] - blk["bbox"][0]
    return w >= PROSE_MIN_WIDTH * min(band_w, blk["colw"])


def _hits_band(box, bx0: float, bx1: float) -> float:
    """box 와 단이 겹치는 정도 — **둘 중 큰 비율**.

    한쪽만 보면 안 된다. 전폭 문단 대 한쪽 단이면 문단 기준 비율이 0.5 언저리라
    문단을 장벽에서 놓치고, 좁은 축 라벨 대 전폭 단이면 단 기준 비율이 0.05 라
    라벨을 놓친다.
    """
    ov = max(0.0, min(box[2], bx1) - max(box[0], bx0))
    return max(ov / max(1.0, box[2] - box[0]), ov / max(1.0, bx1 - bx0))


def _limits(pg: _FigPage, cap: _cap.Caption, others: list[_cap.Caption],
            bx0: float, bx1: float) -> tuple[float, float]:
    """캡션 위·아래로 그림이 놓일 수 있는 y 한계 (top, bottom)."""
    band_w = max(1.0, bx1 - bx0)
    cy0, cy1 = cap.bbox[1], cap.bbox[3]
    top, bottom = pg.top, pg.bottom
    for blk in pg.blocks:
        b = blk["bbox"]
        # 러닝헤드는 **지면 폭 전체**의 장벽이다 — 옆 단에 찍혀 있어도 그 아래가
        # 판면의 시작이다. 이걸 단 안에서만 보면 지면 맨 위 그림에 저널 로고가
        # 딸려 온다(실측 10.3346/jkms.2017.32.5.873 1쪽 JKMS 마크).
        if not blk["running"] and _hits_band(b, bx0, bx1) < 0.45:
            continue                             # 옆 단 글은 장벽이 아니다
        if not _is_barrier(blk, band_w):
            continue
        if b[3] <= cy0 + 0.5:
            top = max(top, b[3])
        elif b[1] >= cy1 - 0.5:
            bottom = min(bottom, b[1])
    # 다른 캡션(이웃 논문 것 포함)도 장벽이다 — 그림 둘이 캡션 하나를 사이에 두고
    # 세로로 붙어 조판되는 일이 흔하다
    for o in others:
        if o is cap or o.page != cap.page:
            continue
        b = o.bbox
        if _hits_band(b, bx0, bx1) < 0.30:
            continue
        if b[3] <= cy0 + 0.5:
            top = max(top, b[3])
        elif b[1] >= cy1 - 0.5:
            bottom = min(bottom, b[1])
    # tablefill 이 좌표까지 확정해 둔 **표 영역**도 장벽이다. 표의 괘선은
    # get_drawings() 에 도형으로 잡히므로, 막지 않으면 그림 바로 위의 표가
    # 통째로 그림 안에 들어온다.
    for b in pg.tables:
        if _hits_band(b, bx0, bx1) < 0.30:
            continue
        if b[3] <= cy0 + 0.5:
            top = max(top, b[3])
        elif b[1] >= cy1 - 0.5:
            bottom = min(bottom, b[1])
    return top, bottom


# ── 그림 영역 ────────────────────────────────────────────────────────
def _grow(pieces: list[_Piece], anchor_far: bool
          ) -> list[_Piece]:
    """캡션에 가까운 쪽부터 조각을 잇는다. GAP_MAX 이상 벌어지면 끊는다.

    한 지면에 그림이 둘 이상 붙어 있고 그 사이에 글줄이 없을 때(캡션도 옆 단에
    있을 때) 위 그림까지 통째로 삼키는 것을 막는다.
    """
    if not pieces:
        return []
    # anchor_far=True → 캡션이 아래에 있다(위로 올라가며 잇는다)
    ordered = sorted(pieces, key=lambda p: p.rect[3], reverse=anchor_far)
    taken = [ordered[0]]
    lo, hi = ordered[0].rect[1], ordered[0].rect[3]
    for p in ordered[1:]:
        gap = (lo - p.rect[3]) if anchor_far else (p.rect[1] - hi)
        if gap > GAP_MAX:
            break
        taken.append(p)
        lo, hi = min(lo, p.rect[1]), max(hi, p.rect[3])
    return taken


def _caption_block(pg: _FigPage, cap: _cap.Caption
                   ) -> tuple[float, float, float, float]:
    """캡션이 실린 **글줄 덩어리 전체**의 bbox.

    captions.py 의 Caption.bbox 는 '캡션으로 확정한 줄들'만 감싼다. 조판 덩어리는
    그보다 길 수 있다. 그림에서 캡션을 빼낼 때는 덩어리 전체를 알아야 한다.
    """
    best, cover = cap.bbox, 0.0
    for blk in pg.blocks:
        b = blk["bbox"]
        ov = _inter(b, cap.bbox)
        if ov > cover:
            best, cover = b, ov
    return best if cover > 0.5 * _area(cap.bbox) else cap.bbox


def _cut_out(box, cut) -> tuple[float, float, float, float]:
    """box 에서 cut 을 도려낸다 — 네 방향으로 잘라 **가장 넓게 남는** 쪽.

    잘라낸 그림 안에 그 그림의 캡션이 통째로 들어오는 조판이 있다(실측
    10.1111/1346-8138.13053 1쪽: 패널 (d)~(i) 오른쪽에 캡션 939자가 세로로
    세워져 있어 그림과 같은 높이를 차지한다). 캡션은 그림이 아니므로 잘라 낸다.
    """
    if _inter(box, cut) <= 0.02 * _area(box):
        return box
    cands = [(box[0], box[1], min(box[2], cut[0] - 2.0), box[3]),
             (max(box[0], cut[2] + 2.0), box[1], box[2], box[3]),
             (box[0], box[1], box[2], min(box[3], cut[1] - 2.0)),
             (box[0], max(box[1], cut[3] + 2.0), box[2], box[3])]
    return max(cands, key=_area)


def _region(pg: _FigPage, cap: _cap.Caption, others: list[_cap.Caption]
            ) -> tuple[tuple[float, float, float, float], str, str] | None:
    """(잘라낼 사각형, 갈래('image'|'draw'|'mixed'), 근거 문장). 없으면 None."""
    bx0, bx1, sx0, sx1 = _band(pg, cap)
    if sx1 - sx0 < MIN_FIG_W:
        return None
    top, bottom = _limits(pg, cap, others, bx0, bx1)
    cbox = _caption_block(pg, cap)
    cy0, cy1 = cap.bbox[1], cap.bbox[3]

    def try_side(y0: float, y1: float, above: bool,
                 wx0: float | None = None, wx1: float | None = None):
        if y1 - y0 < MIN_FIG_H:
            return None
        a0 = bx0 if wx0 is None else wx0
        a1 = bx1 if wx1 is None else wx1
        lo, hi = _side_limits(pg, cap, others, a0, a1,
                              sx0 if wx0 is None else wx0,
                              sx1 if wx1 is None else wx1, y0, y1)
        zone = (lo, y0, hi, y1)
        cands = [p for p in pg.pieces
                 if _xshare(p.rect, lo, hi) >= PIECE_IN_BAND
                 and _covered(p.rect, zone) >= PIECE_IN_REGION]
        if not cands:
            return None
        cands = _grow(cands, above)
        box = _union([p.rect for p in cands])
        # 구간 안으로 자른다(조각이 살짝 삐져나온 경우)
        box = (max(box[0], lo), max(box[1], y0),
               min(box[2], hi), min(box[3], y1))
        # 그림 속 글자(축 라벨·패널 문자·범례)를 되붙인다 — 장벽이 아니고,
        # 도형 덩어리와 **실제로 세로로 맞물리는** 블록만. 맞물리지 않는 것은
        # 위아래 10pt 안의 짧은 조각(패널 문자 'a'·'b')일 때만 받는다.
        band_w = max(1.0, bx1 - bx0)
        for blk in pg.blocks:
            b = blk["bbox"]
            if _covered(b, zone) < 0.95 or _is_barrier(blk, band_w):
                continue
            if _xshare(b, lo, hi) < 0.6:
                continue
            ov = min(b[3], box[3]) - max(b[1], box[1])
            if ov <= 0 and not (ov > -10.0 and blk["chars"] <= 30):
                continue
            # 가로로도 붙어 있어야 한다 — 범례·축 라벨은 도형 바로 옆이다
            if max(box[0] - b[2], b[0] - box[2]) <= LABEL_GAP:
                box = _union([box, b])
        # 여유를 준 **뒤에** 한계 안으로 다시 자른다. 순서를 뒤집으면 캡션
        # 첫 줄의 머리가 그림 아래에 남는다(실측 JKMS Fig. 1).
        box = (box[0] - PAD, box[1] - PAD, box[2] + PAD, box[3] + PAD)
        box = (max(box[0], lo), max(box[1], y0),
               min(box[2], hi), min(box[3], y1))
        box = _cut_out(box, cbox)                # 자기 캡션은 그림이 아니다
        w, h = box[2] - box[0], box[3] - box[1]
        if w < MIN_FIG_W or h < MIN_FIG_H or w * h < MIN_FIG_AREA:
            return None
        kinds = {p.kind for p in cands}
        kind = kinds.pop() if len(kinds) == 1 else "mixed"
        why = (f"{cap.page + 1}쪽 캡션 {'아래' if above else '위'}"
               f" 조각 {len(cands)}개 · 구간 y[{y0:.0f},{y1:.0f}]")
        return box, kind, why

    # 캡션을 그림 **옆**에 세워 싣는 조판이 있다(Springer: 좁은 캡션 칸 + 넓은
    # 그림 칸). 실측 10.1007/s11695-012-0674-4 2쪽의 PRISMA 흐름도는 캡션이
    # 왼쪽에 세로로 서 있어 '캡션 위/아래'로만 찾으면 위쪽 절반이 잘려 나간다.
    # 캡션 덩어리와 **같은 높이에** 조각이 옆으로 놓여 있으면 그쪽을 먼저 본다.
    ch = max(1.0, cbox[3] - cbox[1])
    cw = max(1.0, cbox[2] - cbox[0])
    # 옆에 세워 실은 캡션은 **좁고 길다**. 이 조건이 없으면, 아래쪽 옆 단
    # 그림이 마침 캡션과 같은 높이까지 내려온 것만으로 옆 단 그림을 가져온다
    # (실측 10.1111/dsu.12239 2쪽: Fig 1 이 Figure 2 의 원그래프를 물었다).
    for lo2, hi2 in (((cbox[2] + CAP_GAP, sx1), (sx0, cbox[0] - CAP_GAP))
                     if ch >= 0.5 * cw else ()):
        if hi2 - lo2 < MIN_FIG_W:
            continue
        beside = [p for p in pg.pieces
                  if p.rect[0] >= lo2 - 1 and p.rect[2] <= hi2 + 1
                  and p.rect[1] >= top - 1 and p.rect[3] <= bottom + 1
                  and (min(p.rect[3], cbox[3]) - max(p.rect[1], cbox[1])) > 0.6 * ch]
        if sum(_area(p.rect) for p in beside) < MIN_FIG_AREA:
            continue
        # 세로 한계도 **그 칸 기준**으로 다시 잰다. 캡션 칸 기준으로 재면 옆
        # 칸의 다른 캡션·본문이 장벽으로 안 잡혀 아래 그림까지 삼킨다(실측
        # 10.1111/dsu.12239 2쪽: Fig 1 이 Figure 2 캡션을 물었다).
        t2, b2 = _limits(pg, cap, others, lo2, hi2)
        got = try_side(t2, b2, True, lo2, hi2)
        if got is not None:
            return got

    # 그림은 캡션 **위**가 원칙(figure). 위에서 못 찾으면 아래를 본다.
    got = try_side(top, cy0 - CAP_GAP, True)
    if got is None:
        got = try_side(cy1 + CAP_GAP, bottom, False)
    return got


# ── 이웃 논문 오염 검사 ──────────────────────────────────────────────
def _contaminated(page, box, bmap, own: int | None) -> bool:
    """잘라낼 영역 안의 글이 **이웃 논문 구간**에 놓여 있는가."""
    if bmap is None or own is None or not getattr(bmap, "confident", False):
        return False
    import fitz

    try:
        txt = page.get_text("text", clip=fitz.Rect(*box)) or ""
    except Exception:                            # noqa: BLE001
        return False
    txt = " ".join(txt.split())
    if len(txt) < 60:
        return False
    hits = [h for h in bmap.locate(txt, probes=10) if h >= 0]
    if not hits:
        return False
    return own not in hits


# ── 렌더 ─────────────────────────────────────────────────────────────
def render_clip(page, box, *, dpi: int = DPI_DEFAULT,
                max_bytes: int = MAX_BYTES) -> tuple[bytes, str, int]:
    """영역을 이미지로. (bytes, 확장자, 실제 dpi)

    기본은 PNG 다 — 도표·선그림은 PNG 가 옳고 글자가 뭉개지지 않는다. 다만
    **임상 사진**은 PNG 로 담으면 한 장이 800KB 를 넘는다(실측: 96dpi 로 낮춰도
    803KB). 사진은 PNG 가 애초에 맞지 않는 형식이므로, DPI 를 최저(96)까지
    낮추고도 상한을 못 지키면 그때만 JPEG 로 바꾼다. 앱은 `figures[].image` 의
    경로를 그대로 읽으므로 확장자가 섞여도 문제되지 않는다.
    """
    import fitz

    rect = fitz.Rect(*box) & page.rect
    # 픽셀 상한을 먼저 지킨다(메모리 폭주 방지)
    long_pt = max(rect.width, rect.height) or 1.0
    dpi = max(DPI_MIN, int(min(dpi, MAX_PIXELS * 72.0 / long_pt)))
    data = b""
    pix = None
    for _ in range(4):
        pix = page.get_pixmap(clip=rect, dpi=dpi)
        data = pix.tobytes("png")
        if len(data) <= max_bytes or dpi <= DPI_MIN:
            break
        nxt = int(dpi * math.sqrt(max_bytes / float(len(data))) * 0.95)
        dpi = max(DPI_MIN, min(dpi - 8, nxt))
    if len(data) > max_bytes and pix is not None:
        try:
            jpg = pix.tobytes("jpeg", jpg_quality=82)
            if jpg and len(jpg) < len(data):
                return jpg, "jpg", dpi
        except Exception:                        # noqa: BLE001 — JPEG 미지원이면 PNG 그대로
            pass
    return data, "png", dpi


# ── 그림 항목 ↔ PDF 캡션 짝짓기 ──────────────────────────────────────
def _fig_key(item: dict) -> tuple[str | None, bool]:
    """정본 그림 항목에서 (번호키, 보조자료 여부). 못 읽으면 (None, False)."""
    cur = (item.get("caption") or "").strip()
    got = _cap.parse_caption(cur, min_desc=0) if cur else None
    if got and got["kind"] == "fig":
        if got["supp"]:
            return (got["raw"], True)
        if got["num"] is not None:
            return (str(got["num"]), False)
    m = re.search(r"(\d{1,2})\s*$", str(item.get("id") or ""))
    return (m.group(1), False) if m else (None, False)


def _table_spans(doc: dict) -> dict[int, list[tuple[float, float, float, float]]]:
    """tablefill 이 남긴 `tables[].pdf_span` → 쪽별 표 영역(0-base 쪽번호).

    회전(눕힌) 표의 좌표는 회전 좌표계라 지면 좌표와 섞을 수 없으므로 뺀다.
    """
    out: dict[int, list[tuple[float, float, float, float]]] = {}
    for t in doc.get("tables") or []:
        sp = t.get("pdf_span")
        if not isinstance(sp, dict) or sp.get("rotated"):
            continue
        try:
            pno = int(sp["page"]) - 1
            box = (float(sp["x0"]), float(sp["y0"]),
                   float(sp["x1"]), float(sp["y1"]))
        except (KeyError, TypeError, ValueError):
            continue
        if box[2] > box[0] and box[3] > box[1]:
            out.setdefault(pno, []).append(box)
    return out


def _folder_name(doc: dict, pdf_path: Path) -> str:
    """`<paper_id slug>_figs`.

    폴더는 **한 편에 하나**여야 한다. 폴더 정리(멱등성)가 그 폴더 안의 그림을
    지우기 때문에, 두 편이 같은 폴더를 쓰면 서로의 그림을 지운다. paper_id 가
    비면 PDF 파일 이름으로 대신한다(single.py 는 DOI→sha1→파일명 순으로 항상
    고유한 paper_id 를 넣지만, 이 모듈만 따로 부를 수도 있다).
    """
    from . import utils

    pid = str(doc.get("paper_id") or "").strip()
    if not pid or pid.lower() == "unknown":
        pid = f"file:{Path(pdf_path).stem}"
    return f"{utils.slug(pid)[:80]}_figs"


def _file_stem(item: dict, key: str | None, supp: bool, used: set[str]) -> str:
    """`fig1` · `figS2` — **그림 번호**로 짓는다.

    정본 항목의 id 는 출처마다 제각각이라(fig_0·F3·zoi230742f2·pone.0179088.g001)
    사람이 폴더를 열어 보고 어느 그림인지 알 수 없다. 번호를 못 읽으면 그때만
    id 를 쓴다.
    """
    from . import utils

    if key:
        stem = f"fig{'S' if supp else ''}{utils.slug(key)}"
    else:
        stem = utils.slug(str(item.get("id") or "fig"))[:60] or "fig"
    out, k = stem, 2
    while out in used:
        out = f"{stem}_{k}"
        k += 1
    used.add(out)
    return out


# ── 본체 ─────────────────────────────────────────────────────────────
def fill_document(doc: dict, pdf_path: str | Path, *,
                  json_path: str | Path | None = None,
                  dpi: int = DPI_DEFAULT, max_bytes: int = MAX_BYTES,
                  write: bool = True
                  ) -> tuple[dict, dict]:
    """정본의 `figures[].image` 를 실제 PNG 로 채운다. (수정된 doc, 통계)

    입력 doc 은 건드리지 않는다(깊은 복사본을 돌려준다).

    json_path: 정본 JSON 이 저장될 경로. PNG 폴더를 그 **옆**에 만들고
        `figures[].image` 에는 그 JSON 기준 **상대경로**를 담는다.
        주지 않으면 PDF 옆(같은 이름의 .json)을 가정한다.
    write=False 면 파일을 쓰지 않고 좌표만 통계에 담는다(검증용).
    """
    import fitz

    out = copy.deepcopy(doc)
    figs = out.get("figures") or []
    pdf_path = Path(pdf_path)
    base = Path(json_path) if json_path else pdf_path.with_suffix(".json")
    folder = _folder_name(out, pdf_path)
    dest_dir = base.parent / folder

    stats: dict[str, Any] = {
        "paper_id": out.get("paper_id"), "pdf": str(pdf_path),
        "figures_total": len(figs), "clipped": 0, "from_image": 0,
        "from_draw": 0, "from_mixed": 0, "skipped": 0, "contaminated": 0,
        "bytes": 0, "dir": str(dest_dir), "reasons": {}, "items": [],
    }

    def bump(k: str) -> None:
        stats["reasons"][k] = stats["reasons"].get(k, 0) + 1

    if not figs:
        return out, stats

    try:
        lines = _cap.document_lines(pdf_path)
    except Exception as e:                       # noqa: BLE001
        stats["reason"] = f"PDF 읽기 실패: {type(e).__name__}: {e}"
        return out, stats
    if not lines:
        stats["reason"] = "텍스트층 없음 — 캡션 좌표를 얻을 수 없다"
        return out, stats

    body = _cap.body_size(lines)
    all_caps = _cap.extract_captions(pdf_path, lines=lines)

    # 소유 판정 — boundary.py 에 전적으로 맡긴다. 실패하면 **한 장도 만들지 않는다**.
    try:
        bmap = _cap.boundary_map(pdf_path, out)
    except Exception as e:                       # noqa: BLE001
        stats["reason"] = f"boundary.analyze 실패({type(e).__name__}) — 전량 보류"
        return out, stats
    own = getattr(bmap, "own", None) if bmap is not None else None
    mine = [c for c in all_caps
            if c.kind == "fig" and _cap.caption_owner(bmap, c.text) != "other"]
    stats["captions_pdf"] = len(mine)
    stats["captions_other"] = sum(1 for c in all_caps if c.kind == "fig") - len(mine)
    if not mine:
        stats["reason"] = "이 논문 소유의 그림 캡션을 PDF 에서 찾지 못함"
        return out, stats

    ambig = _cap.ambiguous_numbers(mine, "fig")
    by_num: dict[str, _cap.Caption] = {}
    by_supp: dict[str, _cap.Caption] = {}
    for c in mine:
        tgt, key = ((by_supp, c.raw) if c.supp
                    else (by_num, None if c.num is None else str(c.num)))
        if not key:
            continue
        if key not in tgt or len(c.desc) > len(tgt[key].desc):
            tgt[key] = c

    fdoc = fitz.open(str(pdf_path))
    try:
        decor = _decoration_xrefs(fdoc)
        heads = _running_heads(fdoc, fdoc.page_count)
        tspans = _table_spans(out)
        by_page: dict[int, list[_cap.Line]] = {}
        for ln in lines:
            by_page.setdefault(ln.page, []).append(ln)
        # 문서 전체로 잰 단 경계 — 그림이 지면을 채운 쪽에서 쪽 단위 판정이
        # 실패했을 때 쓸 대체값(학술지는 한 논문 안에서 판형을 바꾸지 않는다)
        doc_split = _col_split(lines)
        pages: dict[int, _FigPage] = {}

        def page_of(pno: int) -> _FigPage:
            if pno not in pages:
                pages[pno] = _read_page(fdoc, pno, by_page.get(pno, []),
                                        body, decor, heads,
                                        tspans.get(pno, []), doc_split)
            return pages[pno]

        used_names: set[str] = set()
        plan: list[dict] = []
        for it in figs:
            key, supp = _fig_key(it)
            if key is None:
                bump("no_number")
                stats["skipped"] += 1
                continue
            cap = by_supp.get(key) if supp else by_num.get(key)
            if cap is None:
                bump("caption_not_in_pdf")
                stats["skipped"] += 1
                continue
            if not supp and cap.num in ambig:
                bump("ambiguous_number")
                stats["skipped"] += 1
                continue
            # 세로(눕힌) 조판 캡션은 손대지 않는다 — 좌표계가 다르다
            cw = cap.bbox[2] - cap.bbox[0]
            ch = cap.bbox[3] - cap.bbox[1]
            if cw < ch:
                bump("rotated_caption")
                stats["skipped"] += 1
                continue
            pg = page_of(cap.page)
            got = _region(pg, cap, all_caps)
            if got is None:
                bump("no_graphics")
                stats["skipped"] += 1
                continue
            box, kind, why = got
            if _contaminated(fdoc[cap.page], box, bmap, own):
                bump("neighbour_paper")
                stats["contaminated"] += 1
                stats["skipped"] += 1
                continue
            # 같은 그림을 정본이 두 항목으로 담고 있을 수 있다(파서가 중복
            # 생성). 같은 쪽·같은 영역이면 **파일 하나를 함께 가리키게** 한다.
            spot = (cap.page, tuple(round(v) for v in box))
            twin = next((q for q in plan if q["spot"] == spot), None)
            if twin is not None:
                plan.append(dict(twin, item=it, num=key, supp=supp,
                                 dup=True))
                continue
            plan.append({"item": it,
                         "stem": _file_stem(it, key, supp, used_names),
                         "box": box, "kind": kind, "why": why, "spot": spot,
                         "page": cap.page, "num": key, "supp": supp,
                         "dup": False})

        if write and plan:
            dest_dir.mkdir(parents=True, exist_ok=True)
        keep: set[str] = set()
        made: dict[str, tuple[str, int, int]] = {}   # stem → (파일명, 크기, dpi)
        for p in plan:
            page = fdoc[p["page"]]
            nbytes = 0
            real_dpi = dpi
            name = f"{p['stem']}.png"
            if p["stem"] in made:                # 중복 항목 — 같은 파일을 가리킨다
                name, nbytes, real_dpi = made[p["stem"]]
                nbytes = 0                       # 용량은 한 번만 센다
            elif write:
                try:
                    data, ext, real_dpi = render_clip(page, p["box"], dpi=dpi,
                                                      max_bytes=max_bytes)
                except Exception as e:           # noqa: BLE001 — 한 장 실패가 전체를 막지 않는다
                    bump(f"render_fail:{type(e).__name__}")
                    stats["skipped"] += 1
                    continue
                name = f"{p['stem']}.{ext}"
                (dest_dir / name).write_bytes(data)
                nbytes = len(data)
                keep.add(name)
                made[p["stem"]] = (name, nbytes, real_dpi)
            rel = f"{folder}/{name}"
            p["item"]["image"] = rel
            stats["clipped"] += 1
            stats["bytes"] += nbytes
            stats[{"image": "from_image", "draw": "from_draw"}
                  .get(p["kind"], "from_mixed")] += 1
            stats["items"].append({
                "id": p["item"].get("id"), "num": p["num"], "supp": p["supp"],
                "page": p["page"] + 1, "kind": p["kind"], "image": rel,
                "bbox": [round(v, 1) for v in p["box"]], "dpi": real_dpi,
                "bytes": nbytes, "why": p["why"]})

        # 지난 실행이 남긴 그림 정리 — 폴더를 옮기지 않는 한 멱등하게 유지한다
        if write and dest_dir.is_dir():
            for old in list(dest_dir.glob("*.png")) + list(dest_dir.glob("*.jpg")):
                if old.name not in keep:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            try:
                next(dest_dir.iterdir())
            except StopIteration:
                try:
                    dest_dir.rmdir()
                except OSError:
                    pass
    finally:
        fdoc.close()
    return out, stats


def apply_to_parsed(document, pdf_path: str | Path, *,
                    json_path: str | Path | None = None, **kw) -> dict:
    """schema.Document(데이터클래스)에 그대로 적용한다 — 파서 경로 연결용."""
    figs = list(getattr(document, "figures", None) or [])
    view = {"paper_id": getattr(document, "paper_id", ""),
            "meta": {"title": getattr(getattr(document, "meta", None),
                                      "title", "") or ""},
            "figures": [{"id": f.id, "caption": f.caption or "", "image": f.image}
                        for f in figs],
            "body_text": [{"paragraphs": [{"text": p.text} for p in s.paragraphs]}
                          for s in (getattr(document, "body_text", None) or [])]}
    new, stats = fill_document(view, pdf_path, json_path=json_path, **kw)
    by_id = {f.id: f for f in figs}
    for row in new.get("figures") or []:
        o = by_id.get(row["id"])
        if o is not None:
            o.image = row.get("image")
    return stats


__all__ = ["fill_document", "apply_to_parsed", "render_clip",
           "DPI_DEFAULT", "MAX_BYTES"]
