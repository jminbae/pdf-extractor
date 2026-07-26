"""논문 PDF 구조화 검수 도구 — pywebview + HTML 판.

    [폴더 열기]  경로            GROBID ●        [이 논문만] [전부 추출] [중지]
    ┌────────┬──────────────────────┬──────────────────────────┐
    │ 파일   │ PDF                   │ 추출 결과                 │
    │ 목록   │ (모든 쪽 연속 스크롤)  │ (논문처럼 읽히는 화면)     │
    └────────┴──────────────────────┴──────────────────────────┘

app_gui.py(tkinter)의 후속이다. 기존 파일은 지우지 않는다 — 이쪽이 실패해도
돌아갈 자리가 있어야 한다.

설계 메모
  · 화면은 WebView2 가 그린다. 파이썬은 (1) 창을 띄우고 (2) 127.0.0.1 에
    작은 HTTP 서버를 하나 열어 페이지 이미지·문서 HTML 을 흘려보낸다.
    이미지가 <img src> 로 나가야 브라우저가 알아서 지연 로딩·캐시·취소를
    해준다 — 큰 PDF 에서 멈추지 않는 유일한 방법이다.
  · **바깥 인터넷을 타는 자원이 하나도 없다.** CSS·JS·글꼴 전부 이 파일 안에
    문자열로 들어 있다(PyInstaller onefile 로 그대로 묶인다).
  · 무거운 일(폴더 스캔·추출·PDF 렌더)은 전부 워커 스레드다. pywebview 는
    js_api 호출마다 스레드를 새로 파므로 화면이 얼지 않는다.
  · 추출 결과는 **PDF 와 같은 폴더에 DOI 이름(utils.slug)** 으로 저장한다.
    확정 설계다 — 제목이 교정돼도 이름이 안 바뀌고, 같은 논문이 파일명만
    다르게 두 벌 있어도 한 JSON 으로 모인다.
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent / "src"))


def _setup_console() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_console()

from pubnexus import utils  # noqa: E402

GROBID_WARMUP_SEC = 180          # 콜드 기동 40~50초. 느린 디스크를 감안해 넉넉히.

# single.py 가 올려보내는 단계 이름 → 사람 말. 0%·100% 에 멈춰 보이지 않게 한다.
_STAGE = {
    "probe": "PDF 읽는 중", "doi": "DOI 확인", "metadata": "서지정보 조회",
    "fulltext": "본문 추출", "assemble": "정리·수리", "done": "저장",
}


def _norm(p: str | Path) -> str:
    """경로 비교용 정규화. 대소문자·상대경로 차이로 짝을 놓치지 않게."""
    try:
        return os.path.normcase(os.path.abspath(str(p)))
    except Exception:  # noqa: BLE001
        return str(p).lower()


# ══════════════════════════════════════════════════════════════════════
#  Markdown(뷰) → HTML
#
#  render.to_markdown() 이 내놓는 좁은 방언만 다룬다. 범용 마크다운 파서를
#  들이면 의존성이 늘고, 정작 필요한 것(표를 진짜 <table> 로, 인용을 옅게,
#  pub_types 를 회색 라벨로)은 어차피 직접 해야 한다.
# ══════════════════════════════════════════════════════════════════════
_H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_ONLY_CODE_RE = re.compile(r"^(?:`[^`]+`\s*)+$")
_ITALIC_LINE_RE = re.compile(r"^\*([^*].*[^*])\*$")
_BOLD_LINE_RE = re.compile(r"^\*\*(.+)\*\*$")
_SUB_RE = re.compile(r"^<sub>(.*)</sub>$")
_CITE_RE = re.compile(r"\[\s*\d{1,3}(?:\s*[–—,-]\s*\d{1,3})*\s*\]")
#  [[15]](#ref-15) — 대괄호가 한 겹 더 있다(인용 마커 자체가 [15] 라서).
#  안쪽 대괄호 한 겹까지 받아들인다.
_LINK_RE = re.compile(r"\[((?:[^\[\]\n]|\[[^\[\]\n]*\])*?)\]\((#[-\w.:]{1,60})\)")
#  렌더러가 원시 앵커를 넣는 경로가 남아 있을 수 있다 — 글자로 찍히면 안 된다.
_RAW_A_RE = re.compile(r'<a\s+(?:id|name)\s*=\s*"([-\w.:]{1,60})"\s*>\s*</a>', re.I)
_REFHEAD_RE = re.compile(r"references|참고\s*문헌|bibliography|works cited", re.I)
_REFNUM_RE = re.compile(r"^\s*\[?(\d{1,3})[\].]\s")
_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)")


def _inline(text: str) -> str:
    """평문 한 줄 → HTML. 이스케이프가 먼저, 서식이 나중.

    앵커·링크는 이스케이프 전후로 떼어 보관한다 — 안 그러면 화면에
    `<a id="ref-2"></a>2. Oliver ID…` 가 글자로 그대로 찍힌다(실측).
    """
    kept: list[str] = []

    def _keep(frag: str) -> str:
        kept.append(frag)
        return f"\x00{len(kept) - 1}\x00"

    # 0) 원시 앵커는 **이스케이프 전에** 빼둔다
    text = _RAW_A_RE.sub(lambda m: _keep(f'<a id="{m.group(1)}"></a>'), text)
    esc = _html.escape(text, quote=False)

    def _link(m: re.Match) -> str:
        label = m.group(1)
        if not (label.startswith("[") and label.endswith("]")):
            label = f"[{label}]"          # [[15]](#ref-15) 는 이미 대괄호가 있다
        return _keep(f'<a class="cite" href="{m.group(2)}">{label}</a>')

    esc = _LINK_RE.sub(_link, esc)
    out: list[str] = []
    pos = 0
    for m in _INLINE_RE.finditer(esc):
        out.append(_cites(esc[pos:m.start()]))
        s = m.group(0)
        if s.startswith("**"):
            out.append("<strong>" + _cites(s[2:-2]) + "</strong>")
        elif s.startswith("`"):
            out.append("<code>" + s[1:-1] + "</code>")
        else:
            out.append("<em>" + _cites(s[1:-1]) + "</em>")
        pos = m.end()
    out.append(_cites(esc[pos:]))
    html_out = "".join(out)
    if kept:
        html_out = re.sub(r"\x00(\d+)\x00",
                          lambda m: kept[int(m.group(1))], html_out)
    return html_out


def _cites(s: str) -> str:
    """본문 안 [15] 는 흐름을 끊지 않게 옅은 위첨자로."""
    return _CITE_RE.sub(lambda m: f'<span class="cite">{m.group(0)}</span>', s)


def _table_html(rows: list[str]) -> str:
    """마크다운 파이프 표 → 진짜 <table>.

    구분행(| --- |)이 있으면 그 앞을 머리행으로 본다. 없으면 전부 본문행.
    셀 수가 행마다 달라도(추출 결함) 깨지지 않게 최대 열수에 맞춰 채운다.
    """
    def cells(line: str) -> list[str]:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    head: list[list[str]] = []
    body: list[list[str]] = []
    sep_at = next((i for i, r in enumerate(rows) if _SEP_RE.match(r)), -1)
    for i, r in enumerate(rows):
        if _SEP_RE.match(r):
            continue
        (head if (sep_at > 0 and i < sep_at) else body).append(cells(r))

    ncol = max((len(r) for r in head + body), default=0)
    if not ncol:
        return ""

    def tr(cs: list[str], tag: str) -> str:
        cs = cs + [""] * (ncol - len(cs))
        return "<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cs) + "</tr>"

    out = ['<div class="twrap"><table>']
    if head:
        out.append("<thead>" + "".join(tr(r, "th") for r in head) + "</thead>")
    if body:
        out.append("<tbody>" + "".join(tr(r, "td") for r in body) + "</tbody>")
    out.append("</table></div>")
    return "".join(out)


def chips_html(meta: dict) -> str:
    """제목 아래 칩 = **키워드**. MeSH(색인자 부여)가 먼저, 저자 키워드가 뒤.

    'Journal Article' 같은 pub_types 는 여기 넣지 않는다 — 원장이 키워드 자리로
    쓴다고 못박았다(데이터에는 그대로 남는다). 두 무리는 색만 은은하게 다르고
    모양·크기는 같다. 어느 쪽인지는 마우스를 올리면 알 수 있으면 충분하다.
    많으면 앞의 것만 두고 접는다 — 화면을 잡아먹으면 안 된다.
    """
    def clean(xs) -> list[str]:
        out, seen = [], set()
        for x in xs or []:
            t = re.sub(r"\s+", " ", str(x)).strip()
            k = t.lower()
            if t and k not in seen:
                seen.add(k)
                out.append(t)
        return out

    mesh = clean(meta.get("mesh"))
    kws = [k for k in clean(meta.get("keywords")) if k.lower() not in
           {m.lower() for m in mesh}]
    items = [("mesh", "MeSH 용어", m) for m in mesh] + \
            [("kw", "저자 키워드", k) for k in kws]
    if not items:
        return ""                                   # 빈 자리를 만들지 않는다
    show = 12
    out = ['<p class="chips">']
    for n, (cls, tip, txt) in enumerate(items):
        hid = " hid" if n >= show else ""
        out.append(f'<span class="chip {cls}{hid}" title="{tip}">'
                   f'{_html.escape(txt)}</span>')
    if len(items) > show:
        out.append(f'<button class="chipmore">+{len(items) - show}개 더</button>')
    out.append("</p>")
    return "".join(out)


def figures_html(doc: dict, sha1: str) -> str:
    """그림을 실제 이미지로. 파일이 아직 없으면 **빈 자리를 만들지 않는다.**

    `figures[].image` 는 store.figs_dir(sha1) 기준 상대경로다(절대경로도 받는다).
    한 장도 실제로 없으면 빈 문자열을 돌려 마크다운의 캡션 목록을 그대로 쓰게 한다.
    """
    figs = doc.get("figures") or []
    if not figs:
        return ""
    from pubnexus import store
    base = store.figs_dir(sha1)
    items: list[tuple[str, str]] = []
    for f in figs:
        cap = str((f or {}).get("caption") or "").strip()
        img = str((f or {}).get("image") or "").strip()
        url = ""
        if img:
            p = Path(img)
            fp = p if p.is_absolute() else (base / img)
            if utils.path_exists(fp):
                url = ("/fig?s=" + urllib.parse.quote(sha1) +
                       "&amp;f=" + urllib.parse.quote(img))
        if url or cap:
            items.append((url, cap))
    if not any(u for u, _ in items):
        return ""                       # 아직 그림 파일이 없다 → 자리를 만들지 않는다
    out = ["<h2>Figures</h2>"]
    for url, cap in items:
        out.append('<figure class="fig">')
        if url:
            out.append(f'<img src="{url}" loading="lazy" alt="">')
        if cap:
            out.append(f"<figcaption>{_inline(cap)}</figcaption>")
        out.append("</figure>")
    return "".join(out)


def md_to_html(md: str, chips: str = "", figs: str = "") -> str:
    """to_markdown() 결과를 읽기 화면용 HTML 로.

    앞머리(제목·서지·저자)는 본문과 다른 대접을 한다. pub_types 줄은 **버린다** —
    그 자리는 키워드(chips)가 쓴다.
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    seen_h2 = False          # 첫 ## 전까지가 앞머리
    put_chips = False        # 키워드 칩을 앞머리에 한 번만
    drop_figs = False        # 그림 절을 이미지판으로 갈아끼우는 중
    in_refs = False          # 참고문헌 절: 항목마다 #ref-N 앵커를 붙인다
    ul: list[str] = []

    def _li(x: str) -> str:
        """참고문헌 항목이면 '15.' 을 읽어 id=ref-15 를 달아준다.

        본문 [15] 링크가 이 항목으로 내려오게 하는 착지점이다. 렌더러가
        앵커를 직접 넣어주면 그쪽이 우선이고, 없을 때 여기서 보완한다.
        """
        if not in_refs or 'id="ref-' in x:
            return f"<li>{x}</li>"
        from pubnexus import render as _r
        m = _r.REF_LINE_RE.match(re.sub(r"<[^>]+>", "", x))
        return (f'<li id="{_r.ref_anchor_id(m.group(1))}">{x}</li>'
                if m else f"<li>{x}</li>")

    def flush_ul() -> None:
        if ul:
            out.append("<ul>" + "".join(_li(x) for x in ul) + "</ul>")
            ul.clear()

    while i < n:
        raw = lines[i]
        line = raw.strip()
        i += 1

        if not line:
            flush_ul()
            continue
        if set(line) <= {"-", "─", "—"} and len(line) >= 3:
            continue                                   # 문서 맨 위 --- 찌꺼기

        if _ROW_RE.match(raw):                          # 표
            flush_ul()
            rows = [raw]
            while i < n and _ROW_RE.match(lines[i]):
                rows.append(lines[i])
                i += 1
            out.append(_table_html(rows))
            continue

        h = _H_RE.match(line)
        if h:
            flush_ul()
            lv = len(h.group(1))
            drop_figs = False
            if figs and h.group(2).strip().lower() == "figures":
                out.append(figs)     # 캡션 목록 대신 진짜 그림
                drop_figs = True
                continue
            if lv >= 2 and not seen_h2 and chips and not put_chips:
                out.append(chips)
                put_chips = True
            seen_h2 = seen_h2 or lv >= 2
            in_refs = bool(_REFHEAD_RE.search(h.group(2)))
            txt = _inline(h.group(2)).replace(
                "›", '<span class="crumb">›</span>')
            out.append(f"<h{min(lv, 5)}>{txt}</h{min(lv, 5)}>")
            continue

        if line.startswith("- "):
            if not drop_figs:
                ul.append(_inline(line[2:]))
            continue
        flush_ul()

        if line.startswith(">"):
            out.append(f'<blockquote>{_inline(line.lstrip("> "))}</blockquote>')
            continue

        sub = _SUB_RE.match(line)
        if sub:
            out.append(f'<p class="aside">{_inline(sub.group(1))}</p>')
            continue

        if not seen_h2:                                 # ── 앞머리 ──
            m = _ITALIC_LINE_RE.match(line)
            if m:
                out.append(f'<p class="bib">{_inline(m.group(1))}</p>')
                continue
            if _ONLY_CODE_RE.match(line):
                # pub_types 줄 — 화면에서는 버리고 그 자리에 키워드를 놓는다
                if chips and not put_chips:
                    out.append(chips)
                    put_chips = True
                continue
            out.append(f'<p class="byline">{_inline(line)}</p>')
            continue

        m = _BOLD_LINE_RE.match(line)
        if m:                                           # 표 캡션 등 단독 굵은 줄
            out.append(f'<p class="cap">{_inline(m.group(1))}</p>')
            continue
        m = _ITALIC_LINE_RE.match(line)
        if m:
            out.append(f'<p class="aside">{_inline(m.group(1))}</p>')
            continue

        if in_refs:
            # 참고문헌은 `15. Kim …` 처럼 문단으로 온다. 본문 [15] 가 내려앉을
            # 착지점을 여기서 만드는 것이 **화면 쪽 몫**이다(확정 계약).
            # 번호 → 앵커 id 규칙은 render 것을 그대로 쓴다 — 어긋나면 안 된다.
            from pubnexus import render as _r
            m = _r.REF_LINE_RE.match(line)
            body = _inline(line)
            if m and 'id="ref-' not in body:
                aid = _r.ref_anchor_id(m.group(1))
                out.append(f'<p class="ref" id="{aid}">{body}</p>')
                continue
            out.append(f'<p class="ref">{body}</p>')
            continue

        out.append(f"<p>{_inline(line)}</p>")

    flush_ul()
    if chips and not put_chips:      # 절이 하나도 없는 짧은 문서
        out.append(chips)
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════
#  PDF 페이지 렌더러 — 열린 문서와 그린 이미지를 둘 다 LRU 로 붙잡는다
# ══════════════════════════════════════════════════════════════════════
class PageRenderer:
    MAX_DOCS = 4
    MAX_IMGS = 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._docs: OrderedDict[int, tuple[Path, object]] = OrderedDict()
        self._imgs: OrderedDict[tuple, bytes] = OrderedDict()
        self._seq = 0

    def open(self, path: Path) -> dict:
        """문서를 열고 {key, pages:[[w,h],…]} 를 돌려준다."""
        import fitz
        with self._lock:
            for k, (p, d) in self._docs.items():
                if p == path:
                    self._docs.move_to_end(k)
                    return {"key": k, "pages": self._sizes(d)}
            try:
                doc = fitz.open(str(path))
            except Exception:
                # 260자 초과 경로 — 스트림으로 우회
                with open(utils.long_path(path), "rb") as f:
                    doc = fitz.open(stream=f.read(), filetype="pdf")
            self._seq += 1
            k = self._seq
            self._docs[k] = (path, doc)
            while len(self._docs) > self.MAX_DOCS:
                _, (_, old) = self._docs.popitem(last=False)
                try:
                    old.close()
                except Exception:
                    pass
            return {"key": k, "pages": self._sizes(doc)}

    @staticmethod
    def _sizes(doc) -> list[list[float]]:
        out = []
        for pg in doc:
            r = pg.rect
            out.append([round(r.width, 1) or 612.0, round(r.height, 1) or 792.0])
        return out

    def png(self, key: int, idx: int, width: int) -> bytes | None:
        ck = (key, idx, width)
        with self._lock:
            hit = self._imgs.get(ck)
            if hit is not None:
                self._imgs.move_to_end(ck)
                return hit
            ent = self._docs.get(key)
            if ent is None:
                return None
            self._docs.move_to_end(key)
            doc = ent[1]
            if idx < 0 or idx >= doc.page_count:
                return None
            import fitz
            page = doc[idx]
            s = width / (page.rect.width or 612.0)
            pix = page.get_pixmap(matrix=fitz.Matrix(s, s), alpha=False)
            data = pix.tobytes("png")
            self._imgs[ck] = data
            while len(self._imgs) > self.MAX_IMGS:
                self._imgs.popitem(last=False)
            return data

    def close_all(self) -> None:
        with self._lock:
            for _, (_, d) in self._docs.items():
                try:
                    d.close()
                except Exception:
                    pass
            self._docs.clear()
            self._imgs.clear()


# ══════════════════════════════════════════════════════════════════════
#  분석 엔진(GROBID) — 꺼져 있으면 **창 없이** 켜고 기다린다
#
#  콘솔창을 띄우지 않는 기동은 grobid_service 가 맡는다(javaw + CREATE_NO_WINDOW
#  + DETACHED_PROCESS). 여기서는 그것을 워커 스레드에서 부르고 경과초를 화면에
#  흘려보내는 일만 한다 — 40~50초 동안 화면이 얼면 안 된다.
#  못 켜면 그냥 없이 간다(PyMuPDF 폴백). 겁주는 경고 상자는 띄우지 않는다.
# ══════════════════════════════════════════════════════════════════════
class Grobid:
    def __init__(self, url: str, notify=None) -> None:
        self.url = (url or "http://localhost:8070").rstrip("/")
        self.notify = notify
        self.state = "unknown"          # unknown | starting | ok | off
        self.secs = 0
        self.ready = threading.Event()
        self._t: threading.Thread | None = None

    def _set(self, st: str, secs: int = 0) -> None:
        self.state, self.secs = st, secs
        if st == "ok":
            self.ready.set()
        if self.notify:
            self.notify(st, secs)

    def ensure_async(self) -> None:
        if self._t and self._t.is_alive():
            return
        self._t = threading.Thread(target=self._ensure, daemon=True)
        self._t.start()

    def _ensure(self) -> None:
        from pubnexus import grobid_service as gs
        if gs.is_alive(self.url, timeout=3.0):
            self._set("ok")
            return
        self._set("starting", 0)
        t0 = time.time()
        ok = gs.ensure(self.url, timeout=GROBID_WARMUP_SEC,
                       on_progress=lambda s: self._set("starting", int(s)))
        if ok:
            utils.log(f"[app] 분석 엔진 준비됨 ({time.time() - t0:.0f}초)")
        else:
            utils.log("[app] 분석 엔진 없이 진행 — PyMuPDF 폴백")
        self._set("ok" if ok else "off")

    def wait(self, seconds: float) -> bool:
        if self.state == "ok":
            return True
        return self.ready.wait(seconds)


# ══════════════════════════════════════════════════════════════════════
#  앱 상태
# ══════════════════════════════════════════════════════════════════════
class App:
    def __init__(self) -> None:
        self.window = None
        try:
            self.cfg = utils.load_config()
        except Exception as e:  # noqa: BLE001 — 설정이 없어도 화면은 떠야 한다
            utils.log(f"[app] config.yaml 을 읽지 못했다({e}) — 기본값으로 시작")
            self.cfg = {}
        self.folder: Path | None = None
        self.pdfs: list[Path] = []
        # 정본은 PDF 옆이 아니라 앱 저장소에 있다(store.py). 짝은 내용 지문(sha1).
        self.done: dict[str, str] = {}           # 정규화한 PDF 경로 → sha1
        self.found: dict[str, str] = {}          # 열어 보고 확인한 것(경로가 바뀐 PDF)
        self.renderer = PageRenderer()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.queue = None            # 배치 처리 순서(WorkQueue) — 클릭하면 당긴다
        self.grobid = Grobid(
            (self.cfg.get("grobid") or {}).get("url", ""),
            notify=lambda st, sec: self.push("grobid", {"state": st, "sec": sec}))
        self._last_push = 0.0

    # ── JS 쪽으로 밀어넣기 ──────────────────────────────────────────
    def push(self, kind: str, payload: dict) -> None:
        w = self.window
        if w is None:
            return
        try:
            msg = json.dumps({"kind": kind, **payload}, ensure_ascii=False)
            w.evaluate_js(f"window.pnx && window.pnx.on({msg})")
        except Exception:
            pass

    def status(self, text: str, *, pct: float | None = None,
               busy: bool | None = None, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_push < 0.08:
            return
        self._last_push = now
        self.push("status", {"text": text, "pct": pct, "busy": busy})

    # ── 지난번에 보던 자리 ──────────────────────────────────────────
    def ui_path(self) -> Path:
        from pubnexus import store
        return store.root() / "ui.json"

    def ui_load(self) -> dict:
        try:
            d = utils.read_json(self.ui_path())
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001 — 없거나 깨졌으면 그냥 처음처럼
            return {}

    def ui_save(self, **kw) -> None:
        try:
            d = self.ui_load()
            d.update(kw)
            self.ui_path().parent.mkdir(parents=True, exist_ok=True)
            utils.write_json(self.ui_path(), d)
        except Exception:  # noqa: BLE001 — 기억 못 해도 앱은 돌아간다
            pass

    # ── 폴더 ────────────────────────────────────────────────────────
    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.status(f"{self.folder.name} 훑는 중…", busy=True, force=True)
        self.pdfs = sorted((p for p in self.folder.rglob("*.pdf") if p.is_file()),
                           key=lambda p: p.name.lower())
        self.scan_store()
        ui = self.ui_load()
        last = ui.get("paper") if _norm(ui.get("folder") or "") == _norm(self.folder) else None
        self.ui_save(folder=str(self.folder))
        self.push("folder", {"path": str(self.folder), "files": self.file_rows(),
                             "last": last})
        self.status("", busy=False, force=True)
        # 요약에 적힌 경로로 못 찾은 것들은 **내용**으로 맞춰 본다. PDF 를 옮겼거나
        # 다른 폴더에서 뽑았으면 경로가 다르기 때문이다. 목록은 이미 떴고, 확인되는
        # 대로 점이 하나씩 초록으로 바뀐다 — 화면을 붙잡지 않는다.
        threading.Thread(target=self._reconcile, args=(self.folder,),
                         daemon=True).start()

    def _reconcile(self, folder: Path) -> None:
        for p in list(self.pdfs):
            if self.folder != folder:        # 그 사이 다른 폴더를 열었다
                return
            if _norm(p) in self.done:
                continue
            try:
                self.sha_for(p, deep=True)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.004)                # 디스크·화면에 숨 쉴 틈을 준다

    def scan_store(self) -> None:
        """저장소 요약을 읽어 {PDF 경로 → sha1} 지도를 만든다.

        목록을 그릴 때 205편의 sha1 을 일일이 계산하면 폴더 열기가 몇 초씩
        걸린다. 요약(index.json)에 적힌 경로로 먼저 맞춰 보고, **실제로 열 때만**
        내용 지문을 계산해 확인한다(그 사이 파일이 옮겨졌을 수 있으므로).
        """
        from pubnexus import store
        done: dict[str, str] = {}
        try:
            for sha, row in (store.index() or {}).items():
                pdf = str((row or {}).get("pdf") or "")
                if pdf:
                    done[_norm(pdf)] = sha
        except Exception as e:  # noqa: BLE001 — 요약이 깨져도 화면은 떠야 한다
            utils.log(f"[app] 저장소 요약을 읽지 못했다: {e}")
        done.update(self.found)          # 열어 보고 확인한 것은 계속 유지
        self.done = done

    def sha_for(self, pdf: Path, deep: bool = False) -> str | None:
        """이 PDF 의 정본 지문. deep=True 면 내용까지 읽어 확인한다."""
        from pubnexus import store
        hit = self.done.get(_norm(pdf))
        if hit:
            return hit
        if not deep:
            return None
        try:
            sha = store.file_sha1(pdf)
        except OSError:
            return None
        if store.has(sha):               # 옮겼거나 이름이 바뀐 PDF — 내용으로 찾았다
            self.found[_norm(pdf)] = sha
            self.done[_norm(pdf)] = sha
            self.push("mark", {"name": pdf.name})
            return sha
        return None

    def file_rows(self) -> list[dict]:
        return [{"name": p.name, "done": _norm(p) in self.done}
                for p in self.pdfs]

    # ── 문서 한 편 ──────────────────────────────────────────────────
    def paper(self, i: int) -> dict:
        if not (0 <= i < len(self.pdfs)):
            return {"error": "없는 항목"}
        pdf = self.pdfs[i]
        res: dict = {"name": pdf.name, "path": str(pdf)}
        try:
            res.update(self.renderer.open(pdf))
        except Exception as e:  # noqa: BLE001
            res["pdf_error"] = f"{type(e).__name__}: {e}"
            res["pages"] = []
        res["doc"] = self.doc_view(pdf)
        if not res["doc"].get("extracted"):
            res["doc"]["queued"] = self.bump(pdf)
        return res

    def bump(self, pdf: Path) -> str | None:
        """지금 보는 논문을 배치 맨 앞으로.

        이미 일꾼이 잡고 있으면 당기지 않는다('running') — 같은 논문을 두 번
        처리하면 같은 파일에 동시에 쓰게 되어 반쪽 산출물이 나온다.
        """
        q = self.queue
        if q is None:
            return None
        try:
            st = q.bump(pdf)
        except Exception:  # noqa: BLE001
            return None
        return st if st in ("queued", "running") else None

    def doc_view(self, pdf: Path) -> dict:
        from pubnexus import store
        sha = self.sha_for(pdf, deep=True)
        if not sha:
            return {"extracted": False}
        d = store.load(sha)
        if d is None:
            return {"extracted": False}
        # 옛 산출물은 본문 키가 sections 다(정본 스키마는 body_text).
        # render 는 body_text 만 보므로 여기서만 맞춰 끼운다 — 파일은 안 건드린다.
        if not d.get("body_text") and d.get("sections"):
            d = dict(d, body_text=d["sections"])
        from pubnexus import render
        md = render.to_markdown(d)
        html = md_to_html(md, chips_html(d.get("meta") or {}),
                          figures_html(d, sha))

        secs = d.get("body_text") or []
        npar = sum(len(s.get("paragraphs") or []) for s in secs)
        nchar = sum(len(p.get("text") or "")
                    for s in secs for p in s.get("paragraphs", []))
        q = d.get("quality_score")
        bits = [str(d.get("source") or "?")]
        if isinstance(q, (int, float)):
            bits.append(f"품질 {q:.2f}")
        bits += [f"섹션 {len(secs)}", f"문단 {npar}",
                 f"표 {len(d.get('tables') or [])}",
                 f"그림 {len(d.get('figures') or [])}",
                 f"참고문헌 {len(d.get('references') or [])}"]
        notes = list((d.get("qc") or {}).get("notes") or [])
        if nchar < 500:
            notes.insert(0, f"본문이 {nchar}자뿐 — 스캔본이거나 추출 실패일 수 있다")
        return {
            "extracted": True, "html": html,
            "info": " · ".join(bits), "notes": notes,
            "paper_id": str(d.get("paper_id") or ""),
            "json": str(store.doc_path(sha)), "sha1": sha,
            "thin": nchar < 500,
        }

    # ── 추출 ────────────────────────────────────────────────────────
    def start(self, targets: list[Path]) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.cancel.clear()
        self.worker = threading.Thread(target=self._work, args=(targets,),
                                       daemon=True)
        self.worker.start()

    def _work(self, targets: list[Path]) -> None:
        from pubnexus import single
        total = len(targets)
        self.push("run", {"on": True, "total": total})
        fails: list[tuple[str, str]] = []
        times: list[float] = []
        i = 0
        try:
            if self.grobid.state in ("starting", "unknown"):
                self.prog(1, total, targets[0].name, "논문 분석기 준비 중", 0.0)
                self.grobid.wait(GROBID_WARMUP_SEC)
            cfg = utils.load_config()
            # 처리 순서를 통에 담아 둔다. 원장이 목록 뒤쪽 논문을 클릭하면
            # paper() 가 그 논문을 맨 앞으로 당긴다(WorkQueue.bump).
            q = single.WorkQueue()
            q.reset(targets)
            self.queue = q
            while not self.cancel.is_set():
                item = q.take()
                if item is None:
                    break
                p = item[1]
                i += 1
                t0 = time.time()
                self.prog(i, total, p.name, "준비", 0.0, times)

                def prog(ev: dict, _i=i, _n=p.name) -> None:
                    steps = max(ev.get("total") or 5, 1)
                    self.prog(_i, total, _n, _STAGE.get(ev.get("stage") or "",
                                                        ev.get("message") or ""),
                              (ev.get("done") or 0) / steps, times)

                try:
                    # 정본은 앱 저장소에 들어간다(PDF 옆이 아니다). 자리와 목록
                    # 요약 갱신은 파이프라인이 맡는다 — 여기서 경로를 짓지 않는다.
                    dest = single.default_json_path(p)
                    doc = single.extract_one(p, cfg, out_json=dest,
                                             on_progress=prog)
                    sha = str(doc.get("sha1") or "")
                    if sha:
                        self.found[_norm(p)] = sha
                        self.done[_norm(p)] = sha
                    self.push("mark", {"name": p.name})   # 목록에 즉시 ✓
                except Exception as e:  # noqa: BLE001 — 파일별 격리
                    fails.append((p.name, f"{type(e).__name__}: {e}"))
                    utils.log(f"[app] 실패 {p.name}: {type(e).__name__}: {e}")
                finally:
                    q.finish(p)
                times.append(time.time() - t0)
        except Exception as e:  # noqa: BLE001
            fails.append(("(전체)", f"{type(e).__name__}: {e}"))
            utils.log(traceback.format_exc())
        finally:
            self.queue = None
            stopped = self.cancel.is_set()
            self.scan_store()
            self.push("run", {
                "on": False, "files": self.file_rows(), "stopped": stopped,
                "done": max(i - (1 if stopped else 0), 0) - len(fails),
                "total": total, "nfail": len(fails),
                "fails": [{"name": n, "why": w} for n, w in fails[:30]]})

    def prog(self, i: int, total: int, name: str, stage: str, sub: float,
             times: list[float] | None = None) -> None:
        """진행 표시 한 번. 남은 시간은 **믿을 만할 때만** 보낸다.

        틀린 예측은 없느니만 못하다 — 다섯 편 넘게 처리해 실측이 쌓인 뒤에,
        그것도 중앙값으로만 낸다(어쩌다 오래 걸린 한 편에 끌려가지 않게).
        """
        now = time.time()
        if now - self._last_push < 0.12:
            return
        self._last_push = now
        eta = None
        if times and len(times) >= 5:
            s = sorted(times[-12:])
            med = s[len(s) // 2]
            eta = int(med * (total - i + 1))
        self.push("prog", {"i": i, "total": total, "name": name, "stage": stage,
                           "pct": 100.0 * (i - 1 + min(max(sub, 0.0), 1.0)) / max(total, 1),
                           "eta": eta})


# ══════════════════════════════════════════════════════════════════════
#  로컬 HTTP — 화면·페이지 이미지·문서 HTML
# ══════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    app: App = None            # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:      # 콘솔을 더럽히지 않는다
        pass

    def _send(self, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _json(self, obj) -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    _IMG = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}

    def _fig(self, sha1: str, rel: str) -> None:
        """저장소 그림 폴더의 이미지 한 장. 폴더 밖으로는 절대 나가지 않는다."""
        from pubnexus import store
        if not re.fullmatch(r"[0-9a-fA-F]{6,64}", sha1 or ""):
            self.send_error(404)
            return
        base = store.figs_dir(sha1).resolve()
        try:
            fp = (base / rel).resolve()
        except Exception:  # noqa: BLE001
            self.send_error(404)
            return
        if base not in fp.parents and fp.parent != base:
            self.send_error(403)         # ../ 로 저장소 밖을 훔쳐보려는 시도
            return
        ctype = self._IMG.get(fp.suffix.lower())
        if not ctype or not fp.exists():
            self.send_error(404)
            return
        self._send(fp.read_bytes(), ctype, cache="max-age=600")

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif u.path == "/files":
                self._json({"path": str(self.app.folder or ""),
                            "files": self.app.file_rows()})
            elif u.path == "/open":
                self._json(self.app.paper(int(q.get("p", ["-1"])[0])))
            elif u.path == "/page":
                data = self.app.renderer.png(int(q["k"][0]), int(q["i"][0]),
                                             int(q["w"][0]))
                if data is None:
                    self.send_error(404)
                else:
                    self._send(data, "image/png", cache="max-age=600")
            elif u.path == "/fig":
                self._fig(q.get("s", [""])[0], q.get("f", [""])[0])
            else:
                self.send_error(404)
        except Exception:  # noqa: BLE001
            utils.log(traceback.format_exc())
            try:
                self.send_error(500)
            except Exception:
                pass


def serve(app: App) -> int:
    Handler.app = app
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port


# ══════════════════════════════════════════════════════════════════════
#  JS ↔ 파이썬 다리 (창을 띄우고 파일 대화상자를 여는 일만)
# ══════════════════════════════════════════════════════════════════════
class Api:
    def __init__(self, app: App) -> None:
        self._app = app

    def pick_folder(self) -> dict:
        import webview
        w = self._app.window
        res = w.create_file_dialog(webview.FOLDER_DIALOG,
                                   directory=str(self._app.folder or ""))
        if not res:
            return {"ok": False}
        self._app.set_folder(Path(res[0]))
        return {"ok": True, "path": str(self._app.folder)}

    def extract(self, which: str, index: int = -1) -> dict:
        a = self._app
        if a.worker and a.worker.is_alive():
            return {"ok": False, "why": "이미 처리 중입니다"}
        if which == "one":
            if not (0 <= index < len(a.pdfs)):
                return {"ok": False, "why": "왼쪽에서 논문을 먼저 고르세요"}
            targets = [a.pdfs[index]]
        else:
            # 여기서만 내용 지문까지 확인한다. 옮겨오거나 이름만 바꾼 PDF 를
            # 다시 뽑느라 몇 분을 버리느니, 몇 초 더 읽는 편이 낫다.
            a.status("이미 처리된 것이 있는지 확인하는 중…", busy=True, force=True)
            targets = [p for p in a.pdfs if a.sha_for(p, deep=True) is None]
            a.status("", busy=False, force=True)
            if not targets:
                targets = list(a.pdfs)
        if not targets:
            return {"ok": False, "why": "처리할 PDF 가 없습니다"}
        a.start(targets)
        return {"ok": True, "n": len(targets)}

    def count_todo(self) -> dict:
        a = self._app
        todo = [p for p in a.pdfs if a.sha_for(p) is None]
        return {"todo": len(todo), "total": len(a.pdfs)}

    def cancel(self) -> dict:
        self._app.cancel.set()
        return {"ok": True}

    def state(self) -> dict:
        a = self._app
        ui = a.ui_load()
        last = ui.get("paper") if _norm(ui.get("folder") or "") == _norm(a.folder or "") else None
        return {"path": str(a.folder or ""), "grobid": a.grobid.state,
                "sec": a.grobid.secs, "files": a.file_rows(), "last": last}

    def remember(self, name: str) -> dict:
        """마지막에 보던 논문. 다음에 켜면 그 자리에서 이어 본다."""
        self._app.ui_save(paper=str(name or ""))
        return {"ok": True}

    def reveal(self, path: str) -> dict:
        try:
            os.startfile(str(Path(path).parent))     # noqa: S606 — 탐색기 열기
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "why": str(e)}
        return {"ok": True}


# ══════════════════════════════════════════════════════════════════════
#  화면 — CSS·JS 전부 인라인(바깥 자원 0)
# ══════════════════════════════════════════════════════════════════════
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>PDF Extractor</title>
<style>
:root{
  --bg:#f4f5f7; --panel:#fff; --line:#e7e9ee; --line2:#f0f1f4;
  --ink:#1e2227; --ink2:#3d444d; --mut:#7c848e; --mut2:#a2a9b3;
  --accent:#2f6bd8; --accent-s:#eaf0fd; --ok:#16a34a; --warn:#c2870b;
  --ui:"Segoe UI Variable Text","Segoe UI","Pretendard Variable",Pretendard,
      "Malgun Gothic",system-ui,sans-serif;
  --serif:Charter,Georgia,"Iowan Old Style","Times New Roman","Malgun Gothic",serif;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font-family:var(--ui);
  font-size:13px;overflow:hidden;-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;height:100%}   /* 높이를 숫자로 빼지 않는다 */
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-thumb{background:#cfd4db;border-radius:8px;
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:#b3bac3;background-clip:content-box;
  border:3px solid transparent}
::-webkit-scrollbar-track{background:transparent}

/* ── 위쪽 막대 ───────────────────────────────────────────── */
#top{flex:none;display:flex;align-items:center;gap:10px;padding:9px 14px;
  background:var(--panel);border-bottom:1px solid var(--line)}
.btn{padding:6px 13px;border-radius:7px;border:1px solid var(--line);
  background:#fff;color:var(--ink2);font-size:12.5px;line-height:1.4;
  transition:background .12s,border-color .12s}
.btn:hover{background:#f7f8fa;border-color:#d8dce3}
.btn:active{background:#eef0f4}
.btn[disabled]{opacity:.42;cursor:default;background:#fff}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{background:#2a60c4}
.btn.primary[disabled]{background:var(--accent);opacity:.35}
.btn.ghost{border-color:transparent;color:var(--mut)}
.btn.ghost:hover{background:#f2f4f7;color:var(--ink2)}
#path{flex:1;min-width:0;color:var(--ink2);font-size:12.5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#path b{font-weight:600}
/* 앞쪽(상위 경로)은 옅게, 끝의 폴더 이름만 진하게 — 순서는 실제 경로 그대로다. */
#path span{color:var(--mut2)}
#gro{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--mut);
  padding:3px 9px;border-radius:20px;background:#f4f5f7;white-space:nowrap}
#gro i{width:7px;height:7px;border-radius:50%;background:var(--mut2);
  display:inline-block}
#gro.ok i{background:var(--ok)} #gro.starting i{background:var(--warn);
  animation:pulse 1.1s ease-in-out infinite} #gro.off i{background:#d05a5a}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

/* ── 진행 표시 — 일이 없으면 자리를 차지하지 않는다 ────────── */
#prog{flex:none;height:0;overflow:hidden;background:var(--panel);
  border-bottom:1px solid transparent;transition:height .18s ease}
#prog.idle{height:27px;border-bottom-color:var(--line)}
#prog.on{height:41px;border-bottom-color:var(--line)}
#prog .in{display:flex;align-items:center;gap:12px;padding:0 15px;height:27px}
#prog.on .in{height:38px}
#prog:not(.on) #bar{display:none}
#prog:not(.on) .sep{display:none}
#pcount{font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums;
  white-space:nowrap}
#pstage{font-size:11.5px;color:var(--mut);white-space:nowrap}
#pname{flex:1;min-width:0;font-size:11.5px;color:var(--mut);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
#peta{font-size:11.5px;color:var(--mut2);white-space:nowrap}
#bar{height:3px;background:#eef0f4}
#bar span{display:block;height:100%;width:0;background:var(--accent);
  transition:width .35s cubic-bezier(.4,0,.2,1)}
.sep{width:1px;height:12px;background:var(--line)}

/* ── 3분할 ──────────────────────────────────────────────── */
#cols{flex:1;min-height:0;display:grid;
  grid-template-columns:minmax(150px,1fr) 1px minmax(220px,2fr) 1px minmax(320px,3fr)}
.split{background:var(--line);cursor:col-resize;position:relative}
.split::after{content:"";position:absolute;inset:0 -4px;z-index:5}
.split:hover{background:var(--accent)}
.pane{min-width:0;display:flex;flex-direction:column;background:var(--panel);
  overflow:hidden}

/* ── 왼쪽: 목록 ─────────────────────────────────────────── */
#find{padding:10px 10px 9px}
#q{width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:7px;
  font:inherit;font-size:12px;background:#fafbfc;outline:none}
#q:focus{border-color:#c3cfe6;background:#fff;box-shadow:0 0 0 3px var(--accent-s)}
#list{flex:1;overflow-y:auto;overflow-x:hidden;padding:0 6px 10px;outline:none}
.row{display:flex;gap:7px;align-items:flex-start;padding:6px 7px;border-radius:6px;
  cursor:default;font-size:11.5px;line-height:1.45;color:var(--ink2);
  overflow-wrap:anywhere;word-break:normal;hyphens:none}
.row:hover{background:#f5f6f8}
.row.sel{background:var(--accent-s);color:#1c3f80}
.row .dot{flex:none;width:6px;height:6px;border-radius:50%;margin-top:5px;
  background:#dfe3e9}
.row.done .dot{background:var(--ok)}
.row .nm{flex:1;min-width:0}

/* ── 가운데: PDF ────────────────────────────────────────── */
.head{display:flex;align-items:center;gap:8px;padding:7px 12px;
  border-bottom:1px solid var(--line2);min-height:36px}
.head .t{font-size:11.5px;color:var(--mut);letter-spacing:.02em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.head .sp{flex:1}
.zbtn{width:26px;height:24px;border-radius:6px;color:var(--mut);font-size:14px;
  line-height:1}
.zbtn:hover{background:#f2f4f7;color:var(--ink)}
.zbtn[disabled]{opacity:.28;cursor:default;background:none}
#pgno{font-size:11.5px;color:var(--mut);min-width:56px;text-align:center;
  font-variant-numeric:tabular-nums}
#scroll{flex:1;overflow:auto;background:#f1f2f4;padding:16px 0 30px;
  scroll-behavior:auto}
.pg{margin:0 auto 14px;background:#fff;position:relative;
  box-shadow:0 1px 2px rgba(20,28,40,.10),0 3px 12px rgba(20,28,40,.06)}
.pg img{width:100%;height:100%;display:block}
.pg::before{content:attr(data-n);position:absolute;right:-2px;bottom:-17px;
  font-size:10px;color:var(--mut2)}
.empty{display:flex;height:100%;align-items:center;justify-content:center;
  color:var(--mut2);font-size:12.5px;flex-direction:column;gap:8px;padding:20px;
  text-align:center}

/* ── 오른쪽: 읽기 화면 ──────────────────────────────────── */
#doc{flex:1;overflow:auto;background:#fff}
#art{padding:24px 28px 70px}
#art h1{font-family:var(--ui);font-size:23px;line-height:1.3;font-weight:650;
  letter-spacing:-.012em;margin:0 0 11px;color:#151a21}
#art h2{font-family:var(--ui);font-size:15.5px;font-weight:650;color:#182029;
  margin:26px 0 9px;padding-bottom:5px;border-bottom:1px solid var(--line2);
  letter-spacing:-.005em}
#art h3{font-family:var(--ui);font-size:14px;font-weight:640;color:#28313c;
  margin:19px 0 6px}
#art h4,#art h5{font-family:var(--ui);font-size:13px;font-weight:640;
  color:#455060;margin:15px 0 5px}
#art .crumb{color:var(--mut2);font-weight:400;margin:0 3px}
#art p{font-family:var(--serif);font-size:15.2px;line-height:1.72;
  color:#242a31;margin:0 0 11px;text-align:left;
  overflow-wrap:break-word;hyphens:auto}
#art p.bib{font-family:var(--ui);font-size:12px;color:var(--mut);margin:0 0 6px}
#art p.byline{font-family:var(--ui);font-size:12.5px;color:var(--ink2);
  margin:0 0 12px;line-height:1.6}
/* 키워드 칩 — MeSH 와 저자 키워드를 같은 모양, 색만 은은하게 다르게 */
#art p.chips{margin:2px 0 10px;display:flex;flex-wrap:wrap;gap:4px;
  align-items:center}
#art .chip{font-family:var(--ui);font-size:10.5px;border-radius:4px;
  padding:2.5px 7px;line-height:1.45;letter-spacing:.01em;
  white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
#art .chip.mesh{background:#eef2f8;color:#5a6675}
#art .chip.kw{background:#f4f5f6;color:#71767e}
#art .chip.hid{display:none}
#art .chipmore{font-family:var(--ui);font-size:10.5px;color:var(--mut2);
  padding:2.5px 6px;border-radius:4px}
#art .chipmore:hover{background:#f4f5f7;color:var(--ink2)}
#art p.aside{font-family:var(--ui);font-size:11.5px;color:var(--mut)}
/* 참고문헌 — 번호가 왼쪽으로 걸리는 매달린 들여쓰기, 본문보다 조금 작게 */
#art p.ref{font-family:var(--serif);font-size:13.3px;line-height:1.62;
  color:#3a424b;margin:0 0 7px;padding-left:1.5em;text-indent:-1.5em}
#art p.cap{font-family:var(--ui);font-size:12.5px;font-weight:620;color:#2b333d;
  margin:20px 0 7px}
#art blockquote{margin:0 0 15px;padding:2px 0 2px 14px;
  border-left:2px solid var(--line);color:var(--ink2)}
#art ul{font-family:var(--serif);font-size:14.6px;line-height:1.65;
  color:#2b323a;margin:0 0 11px;padding-left:20px}
#art li{margin:0 0 6px}
#art code{font-family:Consolas,"Cascadia Mono",monospace;font-size:.88em;
  background:#f4f5f7;border-radius:3px;padding:1px 4px;color:#48505a}
#art .cite{color:#a8b0ba;font-size:.8em;vertical-align:.28em;
  letter-spacing:-.02em;margin:0 .5px}
#art a.cite{text-decoration:none;cursor:pointer}
#art a.cite:hover{color:var(--accent)}
#art .hit{background:#fff6d8;border-radius:3px;
  transition:background .5s ease}                /* 눌러서 찾아간 항목 잠깐 표시 */
#art .fig{margin:0 0 18px;padding:0}
#art .fig img{max-width:100%;height:auto;display:block;border:1px solid var(--line);
  border-radius:6px;background:#fff}
#art .fig figcaption{font-family:var(--ui);font-size:11.5px;color:var(--mut);
  margin-top:6px;line-height:1.55}
#art .twrap{overflow-x:auto;overscroll-behavior-x:contain;margin:0 0 18px;
  border:1px solid var(--line);border-radius:8px;background:#fff}
#art table{border-collapse:collapse;width:100%;font-family:var(--ui);
  font-size:12.4px;line-height:1.5;table-layout:auto}
#art th,#art td{padding:8px 12px;text-align:left;vertical-align:top;
  border-bottom:1px solid var(--line2);white-space:normal;min-width:74px;
  overflow-wrap:break-word;word-break:keep-all}   /* 한 글자씩 세로로 쌓이면 실패 */
#art thead th{background:#fafbfc;font-weight:640;color:#333c46;
  border-bottom:1px solid #dfe3e9;position:sticky;top:0}
#art tbody tr:last-child td{border-bottom:0}
#art tbody tr:hover{background:#fcfcfd}
#foot{border-top:1px solid var(--line2);padding:6px 14px;font-size:10.5px;
  color:var(--mut2);display:flex;gap:8px;align-items:center;min-height:28px}
#foot .more{color:var(--mut);text-decoration:underline;text-underline-offset:2px}
#notes{padding:0 14px 10px;font-size:11px;color:var(--mut);display:none;
  border-top:1px solid var(--line2)}
#notes.on{display:block}
#notes li{margin:5px 0}

/* ── 겹침(확인 상자·안내) ───────────────────────────────── */
#veil{position:fixed;inset:0;background:rgba(24,29,36,.30);display:none;
  align-items:center;justify-content:center;z-index:50}
#veil.on{display:flex}
.card{background:#fff;border-radius:12px;padding:22px 24px 18px;width:460px;
  max-width:82vw;box-shadow:0 12px 40px rgba(18,24,33,.22)}
.card h3{margin:0 0 8px;font-size:15px;font-weight:640}
.card p{margin:0 0 18px;font-size:12.5px;color:var(--ink2);line-height:1.65;
  white-space:pre-line;max-height:46vh;overflow:auto}
.card .r{display:flex;justify-content:flex-end;gap:8px}
#toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,14px);
  background:#232a33;color:#fff;padding:9px 16px;border-radius:8px;font-size:12px;
  opacity:0;pointer-events:none;transition:.2s;z-index:60;max-width:70%}
#toast.on{opacity:1;transform:translate(-50%,0)}
</style></head><body>

<div id="top">
  <button class="btn" id="bopen">폴더 열기</button>
  <div id="path">폴더를 고르세요</div>
  <div id="gro"><i></i><span>분석기 확인 중</span></div>
  <button class="btn" id="bone" disabled>이 논문만</button>
  <button class="btn primary" id="ball" disabled>전부 추출</button>
  <button class="btn ghost" id="bstop" style="display:none">중지</button>
</div>

<div id="prog">
  <div class="in">
    <span id="pcount"></span><span class="sep"></span>
    <span id="pname"></span>
    <span id="pstage"></span>
    <span id="peta"></span>
  </div>
  <div id="bar"><span></span></div>
</div>

<div id="cols">
  <div class="pane">
    <div id="find"><input id="q" placeholder="파일 이름 검색" spellcheck="false"></div>
    <div id="list" tabindex="0"></div>
  </div>
  <div class="split" data-s="0"></div>
  <div class="pane">
    <div class="head"><span class="t">PDF</span><span class="sp"></span>
      <button class="zbtn" id="zo" title="축소">−</button>
      <span id="pgno">—</span>
      <button class="zbtn" id="zi" title="확대">＋</button>
      <button class="btn ghost" id="zf" style="padding:3px 9px;font-size:11.5px">폭 맞춤</button>
    </div>
    <div id="scroll"><div class="empty">왼쪽에서 논문을 고르세요</div></div>
  </div>
  <div class="split" data-s="1"></div>
  <div class="pane">
    <div class="head">
      <button class="zbtn" id="bback" title="이전 위치 (Alt+←, 마우스 옆 버튼)" disabled>‹</button>
      <button class="zbtn" id="bfwd" title="다음 위치 (Alt+→, 마우스 옆 버튼)" disabled>›</button>
      <span class="t" id="dtitle">추출 결과</span>
    </div>
    <div id="doc"><div class="empty">왼쪽에서 논문을 고르세요</div></div>
    <div id="notes"></div>
    <div id="foot"></div>
  </div>
</div>

<div id="veil"><div class="card">
  <h3 id="mt"></h3><p id="mm"></p>
  <div class="r"><button class="btn" id="mc">취소</button>
  <button class="btn primary" id="mo">계속</button></div>
</div></div>
<div id="toast"></div>

<script>
"use strict";
const $=s=>document.querySelector(s), DPR=Math.min(window.devicePixelRatio||1,2);
const S={files:[],view:[],sel:-1,key:null,pages:[],zoom:1,fitW:0,bucket:1200,
         els:[],io:null,busy:false,ready:false,
         hist:[],hidx:-1,mem:{},    /* 이동 이력 · 논문별로 읽던 자리 */
         cursor:-1,last:null,waiting:null};  /* 목록 커서 · 지난번 논문 · 기다리는 논문 */

/* ── 도우미 ─────────────────────────────────────────────── */
let tmr=null;
function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("on");
  clearTimeout(tmr);tmr=setTimeout(()=>t.classList.remove("on"),2600);}
function ask(title,msg,ok,alone){return new Promise(res=>{
  $("#mt").textContent=title;$("#mm").textContent=msg;$("#mo").textContent=ok||"계속";
  $("#mc").style.display=alone?"none":"";
  $("#veil").classList.add("on");
  const done=v=>{$("#veil").classList.remove("on");
    $("#mo").onclick=null;$("#mc").onclick=null;res(v);};
  $("#mo").onclick=()=>done(true);$("#mc").onclick=()=>done(false);});}
const api=()=>window.pywebview&&window.pywebview.api;
/* 경로는 **읽는 순서 그대로** 보여 준다 — 상위 경로를 옅게 앞에, 폴더 이름을 진하게 뒤에.
   예전에는 폴더 이름을 앞에 두고 상위 경로를 뒤에 붙였는데("예시  C:\\…\\전달용"),
   실제 경로와 순서가 뒤집혀 읽는 사람이 한 번 더 생각해야 했다. 좁으면 앞이 잘리도록
   direction:rtl 대신 그냥 ellipsis 를 쓰고, 전체 경로는 툴팁에 남긴다. */
function setPath(p){
  const el=$("#path");el.title=p||"";el.dataset.full=p||"";
  if(!p){el.textContent="폴더를 고르세요";return;}
  renderPath();}
/* 경로는 읽는 순서 그대로. 넘치면 **앞쪽**을 …로 줄인다 —
   뒤를 자르면 정작 어느 폴더인지가 사라진다. */
function renderPath(){
  const el=$("#path"),full=el.dataset.full||"";
  if(!full)return;
  const parts=full.replace(/[\\/]+$/,"").split(/[\\/]/);
  const name=parts.pop()||full;
  let segs=parts,cut=false;
  const draw=()=>{
    el.textContent="";
    const head=(cut?"…\\":"")+(segs.length?segs.join("\\")+"\\":"");
    if(head){const s=document.createElement("span");s.textContent=head;
      el.appendChild(s);}
    const b=document.createElement("b");b.textContent=name;el.appendChild(b);};
  draw();
  let guard=0;
  while(el.scrollWidth>el.clientWidth+1&&segs.length&&guard++<40){
    segs=segs.slice(1);cut=true;draw();}}

/* ── 목록 ───────────────────────────────────────────────── */
function setFiles(files){S.files=files||[];draw();}
function draw(){
  const q=$("#q").value.trim().toLowerCase();
  S.view=[];const L=$("#list");L.textContent="";
  const frag=document.createDocumentFragment();
  S.files.forEach((f,i)=>{
    if(q&&f.name.toLowerCase().indexOf(q)<0)return;
    S.view.push(i);
    const d=document.createElement("div");
    d.className="row"+(f.done?" done":"")+(i===S.cursor?" sel":"");
    d.dataset.i=i;
    d.title=f.name;
    d.innerHTML='<span class="dot"></span><span class="nm"></span>';
    d.lastChild.textContent=f.name;
    frag.appendChild(d);});
  L.appendChild(frag);
  updateCount();
  $("#ball").disabled=!S.ready||!S.files.length||S.busy;
  $("#bone").disabled=!S.ready||S.sel<0||S.busy;
}
/* 편수는 늘 목록에서 다시 세어 쓴다 — 더하기로 굴리면 어긋난다(실측) */
/* 숫자는 **한 곳에 하나만.** 배치 진행 순번과 누적 추출 수가 따로 놀면
   왜 60 과 77 이 다른지 알 수 없다(원장 지적). 기준은 하나 — 전체 중 끝난 편수. */
function updateCount(){
  const done=S.files.reduce((n,f)=>n+(f.done?1:0),0);
  $("#pcount").textContent=S.files.length?`${done} / ${S.files.length} 추출됨`:"";
  if(!S.busy)showProg(false);}
$("#list").addEventListener("click",e=>{
  const r=e.target.closest(".row");if(!r)return;
  $("#list").focus({preventScroll:true});openPaper(+r.dataset.i);});
$("#q").addEventListener("input",()=>{draw();autoPick();});

/* 목록을 화살표로 오르내린다. 연타할 때 매번 열면 버벅이므로 커서만 먼저
   움직이고, 손을 멈춘 뒤에 연다. */
let pickTimer=null;
function markCursor(i){
  S.cursor=i;
  const L=$("#list");
  L.querySelectorAll(".row.sel").forEach(r=>r.classList.remove("sel"));
  const r=L.querySelector('.row[data-i="'+i+'"]');
  if(r){r.classList.add("sel");r.scrollIntoView({block:"nearest"});}}
function moveSel(d){
  if(!S.view.length)return;
  let k=S.view.indexOf(S.cursor);
  k=(k<0)?(d>0?0:S.view.length-1):Math.min(S.view.length-1,Math.max(0,k+d));
  const i=S.view[k];
  if(i===S.cursor)return;
  markCursor(i);
  clearTimeout(pickTimer);
  pickTimer=setTimeout(()=>{if(S.cursor===i&&S.sel!==i)openPaper(i);},170);}
/* 폴더를 열면 빈 화면을 보여주지 않는다 — 지난번에 보던 논문, 없으면 첫 항목 */
function autoPick(){
  if(!S.view.length)return;
  if(S.view.indexOf(S.sel)>=0){markCursor(S.sel);return;}
  let i=S.view[0];
  if(S.last){const k=S.files.findIndex(f=>f.name===S.last);
    if(k>=0&&S.view.indexOf(k)>=0)i=k;}
  markCursor(i);openPaper(i);}
window.addEventListener("keydown",e=>{
  if(e.key!=="ArrowDown"&&e.key!=="ArrowUp")return;
  const t=e.target,tag=(t&&t.tagName||"").toLowerCase();
  if(tag==="input"||tag==="textarea"||(t&&t.isContentEditable))return;
  /* 본문·PDF 를 스크롤하는 중이면 그쪽이 우선 — Ctrl 을 누르면 언제나 목록 */
  const inPane=t&&t.closest&&(t.closest("#doc")||t.closest("#scroll"));
  if(inPane&&!e.ctrlKey)return;
  e.preventDefault();
  moveSel(e.key==="ArrowDown"?1:-1);});

/* ── 이동 이력 ───────────────────────────────────────────
   원장은 목록을 오르내리며 원본과 대조한다. 인용을 눌러 참고문헌으로 뛰었으면
   **읽던 자리로 돌아올 길**이 있어야 한다. 크게 튀는 이동만 쌓는다 —
   단순 스크롤까지 쌓으면 뒤로가기가 쓸모없어진다. */
const dbox=()=>$("#doc");
const curPos=()=>({p:S.sel,top:dbox().scrollTop});
function navBtns(){
  $("#bback").disabled=S.hidx<=0;
  $("#bfwd").disabled=S.hidx<0||S.hidx>=S.hist.length-1;}
function histPush(pos,from){
  /* from 을 받는 이유: 논문을 바꾼 뒤에 curPos() 를 읽으면 이미 새 논문이라
     떠나온 자리가 통째로 덮여 뒤로가기가 제자리를 맴돈다(실제로 겪었다). */
  if(S.hidx>=0)S.hist[S.hidx]=from||curPos();
  S.hist=S.hist.slice(0,S.hidx+1);
  S.hist.push(pos);S.hidx=S.hist.length-1;
  if(S.hist.length>80){S.hist.shift();S.hidx--;}
  navBtns();}
async function histGo(d){
  const n=S.hidx+d;
  if(S.hidx<0||n<0||n>=S.hist.length)return;
  S.hist[S.hidx]=curPos();
  S.hidx=n;navBtns();
  const e=S.hist[n];
  if(e.p!==S.sel)await openPaper(e.p,{noHist:true,top:e.top});
  else scrollDocTo(e.top);}
function scrollDocTo(top){
  dbox().scrollTo({top:top,behavior:"smooth"});
  setTimeout(()=>flashAt(top),340);}         /* 어디로 왔는지 눈이 따라가게 */
function flashAt(top){
  const art=$("#art");if(!art)return;
  let best=null,bd=1e9;
  art.querySelectorAll("p,li,h1,h2,h3,h4,h5,.twrap").forEach(el=>{
    const d=Math.abs(el.offsetTop-top-30);
    if(d<bd){bd=d;best=el;}});
  if(best&&bd<400){best.classList.add("hit");
    setTimeout(()=>best.classList.remove("hit"),1300);}}
$("#bback").onclick=()=>histGo(-1);
$("#bfwd").onclick=()=>histGo(1);
/* 마우스 옆 버튼(뒤로 3 / 앞으로 4).
   한 번 누르면 pointerdown·mousedown·mouseup·auxclick 이 줄줄이 오는데, 어느
   것이 오고 어느 것이 안 오는지가 WebView2 에서 일정하지 않았다(앞으로가 두
   번에 한 번 씹혔다). 그래서 **오는 것 아무거나** 받아 움직이고, 같은 한 번의
   누름이 두 번 세지 않게 짧은 잠금으로 막는다. */
let navGuard=0;
function sideNav(btn){
  const t=Date.now();
  if(t-navGuard<320)return;
  navGuard=t;
  histGo(btn===3?-1:1);}
for(const ev of ["pointerdown","mousedown","mouseup","auxclick","pointerup"])
  window.addEventListener(ev,e=>{
    if(e.button!==3&&e.button!==4)return;
    e.preventDefault();e.stopPropagation();
    sideNav(e.button);},{capture:true});
window.addEventListener("keydown",e=>{
  if(!e.altKey)return;
  if(e.key==="ArrowLeft"){e.preventDefault();histGo(-1);}
  else if(e.key==="ArrowRight"){e.preventDefault();histGo(1);}});

/* ── 논문 열기 ──────────────────────────────────────────── */
let openSeq=0, memTimer=null;
function rememberSoon(){
  clearTimeout(memTimer);
  memTimer=setTimeout(()=>{if(S.last&&api())api().remember(S.last);},900);}
async function openPaper(i,opt){
  opt=opt||{};
  const from=(S.sel>=0)?curPos():null;                    /* 떠나는 자리 */
  if(from&&S.sel!==i)S.mem[S.sel]=from.top;               /* 읽던 자리 기억 */
  S.sel=i;S.cursor=i;draw();
  S.last=S.files[i]?S.files[i].name:null;
  rememberSoon();                       /* 훑는 동안 파일을 매번 쓰지 않게 */
  const seq=++openSeq;
  $("#dtitle").textContent=S.files[i]?S.files[i].name:"";
  $("#scroll").innerHTML='<div class="empty">여는 중…</div>';
  $("#doc").innerHTML="";$("#foot").textContent="";
  $("#notes").className="";$("#notes").textContent="";
  let r;
  try{r=await (await fetch("/open?p="+i)).json();}
  catch(e){$("#scroll").innerHTML='<div class="empty">PDF 를 열지 못했습니다</div>';return;}
  if(seq!==openSeq)return;
  buildPdf(r);renderDoc(r.doc||{});
  const top=(opt.top!==undefined)?opt.top:(S.mem[i]||0);
  if(top)requestAnimationFrame(()=>{dbox().scrollTop=top;flashAt(top);});
  if(!opt.noHist)histPush({p:i,top:top},from);
}
function renderDoc(d){
  const box=$("#doc");
  if(!d.extracted){
    S.waiting=d.queued?($("#dtitle").textContent||""):null;
    const msg=d.queued==="running"
      ?["추출 중…","끝나면 바로 보여 드립니다"]
      :d.queued==="queued"
      ?["곧 추출됩니다","이 논문을 맨 앞으로 당겼습니다"]
      :["추출 안 됨","위쪽 <b>이 논문만</b> 을 누르면 이 한 편을 처리합니다"];
    box.innerHTML='<div class="empty">'+msg[0]
      +'<br><span style="font-size:11.5px;color:#b3b9c2">'+msg[1]+'</span></div>';
    $("#foot").textContent=d.error||"";return;}
  S.waiting=null;
  box.innerHTML='<article id="art"></article>';
  const art=$("#art");
  art.innerHTML=d.html||"";
  linkCites(art);
  box.scrollTop=0;
  const f=$("#foot");f.textContent="";
  const s=document.createElement("span");s.textContent=d.info||"";f.appendChild(s);
  if(d.notes&&d.notes.length){
    const b=document.createElement("button");b.className="more";
    b.textContent="자세히 "+d.notes.length;
    b.onclick=()=>{const n=$("#notes");n.classList.toggle("on");
      if(n.classList.contains("on")){n.innerHTML="<ul>"+d.notes.map(x=>
        "<li>"+x.replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))
        +"</li>").join("")+"</ul>";}};
    f.appendChild(b);}
  if(d.json){const b=document.createElement("button");b.className="more";
    b.textContent="저장된 곳 열기";
    b.onclick=()=>api()&&api().reveal(d.json);f.appendChild(b);}
}

/* 칩이 많으면 접어 두었다가 눌러서 펼친다 */
$("#doc").addEventListener("click",e=>{
  const b=e.target.closest(".chipmore");if(!b)return;
  b.parentNode.querySelectorAll(".chip.hid").forEach(c=>c.classList.remove("hid"));
  b.remove();});

/* 참고문헌 목록이 있으면 본문 [15] 를 그 항목으로 가는 링크로 바꾼다.
   렌더러가 링크를 직접 넣어주면 그대로 쓰고, 없으면 여기서 이어준다. */
function linkCites(art){
  if(!art.querySelector('[id^="ref-"]'))return;
  art.querySelectorAll("span.cite").forEach(s=>{
    const m=s.textContent.match(/^\[\s*(\d{1,3})\s*\]$/);
    if(!m||!art.querySelector('#ref-'+m[1]))return;
    const a=document.createElement("a");
    a.className="cite";a.href="#ref-"+m[1];a.textContent=s.textContent;
    s.replaceWith(a);});
}

/* ── PDF: 모든 쪽을 이어 붙이고, 보이는 곳만 그린다 ──────── */
function buildPdf(r){
  const sc=$("#scroll");sc.innerHTML="";
  if(S.io){S.io.disconnect();S.io=null;}
  S.key=r.key;S.pages=r.pages||[];S.els=[];S.zoom=1;
  if(!S.pages.length){
    sc.innerHTML='<div class="empty">'+(r.pdf_error?"PDF 를 열지 못했습니다":"빈 PDF")+'</div>';
    $("#pgno").textContent="—";return;}
  const frag=document.createDocumentFragment();
  S.pages.forEach((wh,i)=>{const d=document.createElement("div");
    d.className="pg";d.dataset.i=i;d.dataset.n=(i+1)+" / "+S.pages.length;
    frag.appendChild(d);S.els.push(d);});
  sc.appendChild(frag);
  layout();
  S.io=new IntersectionObserver(ents=>{
    for(const e of ents){ if(e.isIntersecting) load(e.target); else drop(e.target); }},
    {root:sc,rootMargin:"1400px 0px"});
  S.els.forEach(d=>S.io.observe(d));
  sc.scrollTop=0;$("#pgno").textContent="1 / "+S.pages.length;
}
function layout(keep){
  if(!S.pages.length)return;
  const sc=$("#scroll");
  const anchor=keep?(sc.scrollTop+sc.clientHeight/2)/Math.max(sc.scrollHeight,1):0;
  const avail=Math.max(160,sc.clientWidth-34);
  S.fitW=avail;
  const W=Math.round(S.fitW*S.zoom);
  S.bucket=Math.min(2200,Math.max(700,Math.ceil(W*DPR/200)*200));
  S.els.forEach((d,i)=>{const p=S.pages[i];
    d.style.width=W+"px";d.style.height=Math.round(W*p[1]/p[0])+"px";});
  if(keep)sc.scrollTop=anchor*sc.scrollHeight-sc.clientHeight/2;
  S.els.forEach(d=>{if(d.firstChild)load(d);});
}
function load(d){
  const url="/page?k="+S.key+"&i="+d.dataset.i+"&w="+S.bucket;
  let img=d.firstChild;
  if(img&&img.getAttribute("src")===url)return;
  if(!img){img=new Image();img.decoding="async";d.appendChild(img);}
  img.setAttribute("src",url);
}
function drop(d){ if(d.firstChild)d.removeChild(d.firstChild); }
function zoom(f){S.zoom=Math.max(.3,Math.min(4,S.zoom*f));layout(true);}
$("#zi").onclick=()=>zoom(1.2); $("#zo").onclick=()=>zoom(1/1.2);
$("#zf").onclick=()=>{S.zoom=1;layout(true);};
$("#scroll").addEventListener("wheel",e=>{
  if(e.ctrlKey){e.preventDefault();zoom(e.deltaY<0?1.12:1/1.12);}},{passive:false});
let rafOn=false;
$("#scroll").addEventListener("scroll",()=>{
  if(rafOn||!S.pages.length)return;rafOn=true;
  requestAnimationFrame(()=>{rafOn=false;
    const sc=$("#scroll"),y=sc.scrollTop+50;let n=1;
    for(let i=0;i<S.els.length;i++){if(S.els[i].offsetTop<=y)n=i+1;else break;}
    $("#pgno").textContent=n+" / "+S.pages.length;});});
let rt=null;
window.addEventListener("resize",()=>{clearTimeout(rt);
  rt=setTimeout(()=>{layout(true);renderPath();},120);});

/* ── 분할선 ───────────────────────────────────────────────
   폭을 px 로 굳히지 않고 **비율(fr)** 로 유지한다 — 창 크기를 바꿔도
   1 : 1 : 2 로 잡아둔 배분이 그대로 따라간다. 분할선을 두 번 누르면 기본값. */
const DEF_COLS="minmax(150px,1fr) 1px minmax(220px,2fr) 1px minmax(320px,3fr)";
(function(){
  let cur=null,x0=0,a0=0,b0=0,c0=0;
  const cols=$("#cols"), MIN=150;
  const put=(a,b,c)=>{cols.style.gridTemplateColumns=
    a.toFixed(3)+"fr 1px "+b.toFixed(3)+"fr 1px "+c.toFixed(3)+"fr";};
  document.querySelectorAll(".split").forEach(s=>{
    s.title="끌어서 폭 조절 · 두 번 누르면 기본 비율(1 : 2 : 3)";
    s.addEventListener("dblclick",()=>{cols.style.gridTemplateColumns=DEF_COLS;layout(true);});
    s.addEventListener("mousedown",e=>{
      cur=+s.dataset.s;x0=e.clientX;
      const st=getComputedStyle(cols).gridTemplateColumns.split(" ").map(parseFloat);
      a0=st[0];b0=st[2];c0=st[4];
      document.body.style.cursor="col-resize";e.preventDefault();});});
  window.addEventListener("mousemove",e=>{
    if(cur===null)return;
    const dx=e.clientX-x0;
    if(cur===0){const a=Math.max(MIN,Math.min(a0+b0-MIN,a0+dx));put(a,a0+b0-a,c0);}
    else{const b=Math.max(MIN,Math.min(b0+c0-MIN,b0+dx));put(a0,b,b0+c0-b);}});
  window.addEventListener("mouseup",()=>{if(cur===null)return;
    cur=null;document.body.style.cursor="";layout(true);});
})();

/* ── 본문 안 링크: [15] → 아래 참고문헌 15번으로 부드럽게 ──────────
   참고문헌 절 규격이 확정되기 전이라도, 앵커(#ref-15)가 오면 바로 동작한다. */
$("#doc").addEventListener("click",e=>{
  const a=e.target.closest('a[href^="#"]');
  if(!a)return;
  e.preventDefault();
  const t=document.getElementById(a.getAttribute("href").slice(1));
  if(!t)return;
  /* 뛰기 전에 지금 자리를 이력에 남긴다 — 뒤로 눌러 돌아올 수 있게 */
  histPush({p:S.sel,top:Math.max(0,t.offsetTop-dbox().clientHeight/2)});
  t.scrollIntoView({behavior:"smooth",block:"center"});
  /* 착지점이 빈 앵커면 감싼 문단을 물들인다 — 빈 태그는 강조해도 안 보인다 */
  const box=(t.textContent||"").trim()?t:(t.parentElement||t);
  box.classList.add("hit");setTimeout(()=>box.classList.remove("hit"),1400);
});

/* ── 추출 ───────────────────────────────────────────────── */
$("#bone").onclick=async()=>{
  if(S.sel<0){toast("왼쪽에서 논문을 먼저 고르세요");return;}
  const r=await api().extract("one",S.sel); if(!r.ok)toast(r.why||"시작하지 못했습니다");};
$("#ball").onclick=async()=>{
  const c=await api().count_todo();
  const n=c.todo||c.total;
  const msg=(c.todo?`아직 추출되지 않은 ${c.todo}편을 처리합니다.`
                   :`모두 추출되어 있습니다. ${c.total}편을 다시 처리합니다.`)
    +"\n분량에 비례해 오래 걸리고, 중간에 멈춰도 다시 하면 이어집니다.";
  if(!await ask("전부 추출",msg,"시작"))return;
  const r=await api().extract("all"); if(!r.ok)toast(r.why||"시작하지 못했습니다");};
$("#bstop").onclick=()=>{                       /* 눌린 티가 바로 나야 한다 */
  $("#bstop").textContent="중지 중…";$("#bstop").disabled=true;
  $("#pstage").textContent="이 논문을 마치면 멈춥니다";api().cancel();};
$("#bopen").onclick=async()=>{const r=await api().pick_folder();
  if(r&&r.ok)toast("폴더를 읽었습니다");};

/* ── 파이썬이 밀어넣는 신호 ─────────────────────────────── */
window.pnx={on(m){
  if(m.kind==="status"){          /* 폴더 훑기처럼 편수가 없는 일 */
    if(m.text!==undefined){$("#pstage").textContent=m.text;
      $("#pname").textContent="";$("#peta").textContent="";}
    if(m.pct!==null&&m.pct!==undefined)$("#bar").firstChild.style.width=m.pct+"%";
    if(m.busy!==undefined&&m.busy!==null)showProg(m.busy||S.busy);
  }else if(m.kind==="prog"){
    $("#pname").textContent=m.name?("지금: "+m.name):"";
    $("#pstage").textContent=m.stage||"";
    $("#peta").textContent=m.eta?etaText(m.eta):"";   /* 못 믿을 값은 아예 안 쓴다 */
    $("#bar").firstChild.style.width=(m.pct||0)+"%";
  }else if(m.kind==="mark"){
    const f=S.files.find(x=>x.name===m.name);
    if(f&&!f.done){f.done=true;
      const r=$("#list").querySelector('.row[data-i="'+S.files.indexOf(f)+'"]');
      if(r)r.classList.add("done");
      updateCount();
      /* 기다리던 논문이 끝났으면 말없이 다시 열어 결과를 보여준다 */
      if(S.waiting&&S.waiting===m.name&&S.sel>=0&&S.files[S.sel]&&
         S.files[S.sel].name===m.name){S.waiting=null;
        openPaper(S.sel,{noHist:true,top:0});}}
  }else if(m.kind==="grobid"){
    /* 원장은 GROBID 가 뭔지 알 필요가 없다. 준비 상태만 조용히 보이면 된다. */
    const g=$("#gro");g.className=m.state==="ok"?"ok":m.state==="starting"?"starting":
      m.state==="off"?"off":"";
    g.lastChild.textContent=m.state==="ok"?"논문 분석기 준비됨":
      m.state==="starting"?("논문 분석기 준비 중… "+(m.sec||0)+"초"):
      m.state==="off"?"간이 분석으로 진행":"분석기 확인 중";
    g.title=m.state==="off"
      ?"논문 분석기를 켜지 못해 PDF 자체 텍스트만 씁니다 — 추출은 계속됩니다":"";
  }else if(m.kind==="folder"){
    S.hist=[];S.hidx=-1;S.mem={};S.sel=-1;S.cursor=-1;navBtns();
    setPath(m.path);setFiles(m.files);
    if(m.last!==undefined)S.last=m.last;
    autoPick();
  }else if(m.kind==="run"){
    S.busy=!!m.on;showProg(S.busy);
    $("#bstop").style.display=S.busy?"":"none";
    $("#bstop").textContent="중지";$("#bstop").disabled=false;
    if(m.on){$("#pstage").textContent="시작";
      $("#pname").textContent="";$("#peta").textContent="";
      $("#bar").firstChild.style.width="0%";}
    draw();
    if(!m.on){
      if(m.files)setFiles(m.files);
      if(S.sel>=0)openPaper(S.sel);
      report(m);
    }
  }}};
function etaText(s){
  if(s<75)return "1분 이내";
  if(s<3600)return "약 "+Math.round(s/60)+"분 남음";
  return "약 "+(s/3600).toFixed(1)+"시간 남음";}
function showProg(on){
  const e=$("#prog");e.classList.toggle("on",!!on);
  /* 놀 때는 편수 한 줄만 남긴다(자리를 거의 안 먹는다) */
  e.classList.toggle("idle",!on&&S.files.length>0);
  if(!on){$("#pname").textContent="";$("#pstage").textContent="";
    $("#peta").textContent="";}}
/* 끝난 뒤 보고 — 실패를 조용히 넘기지 않는다 */
function report(m){
  const ok=m.done||0, nf=m.nfail||0;
  if(!nf){toast(m.stopped?("중지했습니다 — "+ok+"편까지 저장됨")
                         :(ok+"편 추출을 마쳤습니다"));return;}
  const list=(m.fails||[]).map(f=>"· "+f.name+"\n   "+f.why).join("\n");
  ask((m.stopped?"중지됨 — ":"")+ok+"편 완료 · "+nf+"편 실패",
      "다음 논문은 처리하지 못했습니다. 나머지는 정상 저장됐습니다.\n\n"+list,
      "확인",true);
}

/* ── 시작 ───────────────────────────────────────────────── */
window.addEventListener("pywebviewready",async()=>{
  S.ready=true;
  const st=await api().state();
  setPath(st.path);
  if(st.last)S.last=st.last;
  if(st.files&&st.files.length){setFiles(st.files);autoPick();}else draw();
  window.pnx.on({kind:"grobid",state:st.grobid,sec:st.sec});
});
fetch("/files").then(r=>r.json()).then(d=>{
  if(d.path)setPath(d.path);
  if(d.files&&d.files.length&&!S.files.length)setFiles(d.files);}).catch(()=>{});
</script></body></html>
"""


# ══════════════════════════════════════════════════════════════════════
def _screen() -> tuple[int, int]:
    """작업 화면 크기. 창이 화면보다 커지면 분할선이 밖으로 나간다."""
    try:
        import ctypes
        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 1600, 960


def _own_hwnd() -> int:
    """이 프로세스가 만든, 보이는 가장 큰 창 = 앱 창.

    window.native.Handle 은 UI 스레드가 아니면 COM 예외가 나기 쉬워 쓰지 않는다.
    """
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        best, area, me = 0, 0, os.getpid()

        def cb(h, _l):
            nonlocal best, area
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == me and u.IsWindowVisible(h):
                r = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                a = (r.right - r.left) * (r.bottom - r.top)
                if a > area:
                    best, area = h, a
            return True

        u.EnumWindows(ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        return int(best)
    except Exception:  # noqa: BLE001
        return 0


def paint_caption(timeout: float = 8.0) -> bool:
    r"""제목표시줄을 화면과 같은 흰색으로 칠한다(윈도우 11).

    검정 제목표시줄 밑에 흰 화면이 붙으면 창이 두 동강 나 보인다. DWM 속성으로
    캡션·글자·테두리 색을 지정하면 제목표시줄부터 본문까지 색이 이어진다.
    COLORREF 는 0x00BBGGRR — RGB 순서가 아니다.
    윈도우 10 구버전은 이 속성을 모른다 → 조용히 실패하고 기본 창틀로 간다.
    """
    import ctypes
    from ctypes import wintypes
    t0 = time.time()
    hwnd = 0
    while time.time() - t0 < timeout:
        hwnd = _own_hwnd()
        if hwnd:
            break
        time.sleep(0.15)
    if not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi

        def put(attr: int, colorref: int) -> None:
            v = ctypes.c_uint(colorref)
            dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), ctypes.c_uint(attr),
                                      ctypes.byref(v), ctypes.sizeof(v))

        put(35, 0x00FFFFFF)      # DWMWA_CAPTION_COLOR — 본문과 같은 흰색
        put(36, 0x0028231F)      # DWMWA_TEXT_COLOR    — #1f2328
        put(34, 0x00EEE9E7)      # DWMWA_BORDER_COLOR  — #e7e9ee
        return True
    except Exception:  # noqa: BLE001 — 구버전 윈도우: 그냥 기본 창틀로 둔다
        return False


def build(folder: str | Path | None = None):
    """창을 만들어 돌려준다(테스트에서 직접 몰아보기 위해 분리)."""
    import webview

    app = App()
    port = serve(app)
    api = Api(app)
    # 창을 **최대화해서** 띄운다. 세 칸을 나란히 보는 화면이라 넓을수록 좋고,
    # 무엇보다 고해상도(175% 배율) 화면에서 요청한 크기가 배율만큼 부풀어
    # 창 오른쪽이 화면 밖으로 나가는 일을 원천적으로 막는다.
    w, h = _screen()
    win = webview.create_window(
        "PDF Extractor", url=f"http://127.0.0.1:{port}/", js_api=api,
        width=min(1360, max(1120, w - 120)), height=min(860, max(660, h - 120)),
        min_size=(1120, 660), maximized=True,
        background_color="#ffffff", text_select=True)
    app.window = win

    # 시작 폴더: 지난번에 열었던 곳 → 설정 → 없음.
    # 빈 값으로 resolve 하면 프로젝트 루트가 나와 리포 전체를 훑게 된다.
    start = Path(folder) if folder else None
    if start is None:
        prev = (app.ui_load().get("folder") or "").strip()
        if prev and Path(prev).is_dir():
            start = Path(prev)
    if start is None:
        _cfg_dir = (app.cfg.get("project") or {}).get("input_dir") or ""
        start = utils.resolve(_cfg_dir) if _cfg_dir else None

    def boot() -> None:
        paint_caption()          # 제목표시줄을 본문과 같은 흰색으로(창이 뜬 뒤에)
        app.grobid.ensure_async()
        try:
            if start and start.is_dir():
                app.set_folder(start)
        except Exception:  # noqa: BLE001
            utils.log(traceback.format_exc())

    return app, win, boot


def main() -> None:
    import webview
    app, win, boot = build()
    try:
        webview.start(lambda: threading.Thread(target=boot, daemon=True).start(),
                      debug=bool(os.environ.get("PNX_DEBUG")), private_mode=True)
    finally:
        app.renderer.close_all()


if __name__ == "__main__":
    main()
