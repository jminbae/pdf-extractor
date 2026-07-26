"""논문 PDF 구조화 검수 도구 — pywebview + HTML 판.

    [폴더 열기]  경로            GROBID ●        [이 논문만] [전부 추출] [중지]
    ┌────────┬──────────────────────┬──────────────────────────┐
    │ 파일   │ PDF 원본              │ 추출 결과                 │
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
_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)")


def _inline(text: str) -> str:
    """평문 한 줄 → HTML. 이스케이프가 먼저, 서식이 나중."""
    esc = _html.escape(text, quote=False)
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
    return "".join(out)


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


def md_to_html(md: str) -> str:
    """to_markdown() 결과를 읽기 화면용 HTML 로.

    앞머리(제목·서지·저자·pub_types)는 본문과 다른 대접을 한다 — 사용자가
    빨간 코드칩으로 나오던 pub_types 를 보기 싫다고 했다. 회색 라벨로 눕힌다.
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    seen_h2 = False          # 첫 ## 전까지가 앞머리
    ul: list[str] = []

    def flush_ul() -> None:
        if ul:
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in ul) + "</ul>")
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
            seen_h2 = seen_h2 or lv >= 2
            txt = _inline(h.group(2)).replace(
                "›", '<span class="crumb">›</span>')
            out.append(f"<h{min(lv, 5)}>{txt}</h{min(lv, 5)}>")
            continue

        if line.startswith("- "):
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
            if _ONLY_CODE_RE.match(line):               # pub_types
                tags = re.findall(r"`([^`]+)`", line)
                out.append('<p class="tags">' + "".join(
                    f'<span class="tag">{_html.escape(t)}</span>'
                    for t in tags) + "</p>")
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

        out.append(f"<p>{_inline(line)}</p>")

    flush_ul()
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
        self.cfg = utils.load_config()
        self.folder: Path | None = None
        self.pdfs: list[Path] = []
        self.jmap: dict[str, Path] = {}          # PDF 파일명 → JSON 경로
        self.jcache: dict[str, tuple[float, int, str]] = {}
        self.renderer = PageRenderer()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
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

    # ── 폴더 ────────────────────────────────────────────────────────
    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.status(f"{self.folder.name} 훑는 중…", busy=True, force=True)
        self.pdfs = sorted((p for p in self.folder.rglob("*.pdf") if p.is_file()),
                           key=lambda p: p.name.lower())
        self.scan_jsons()
        self.push("folder", {"path": str(self.folder), "files": self.file_rows()})
        self.status("", busy=False, force=True)

    def scan_jsons(self) -> None:
        """폴더 안 정본 JSON 을 훑어 {PDF 파일명 → JSON 경로}.

        JSON 이름은 DOI(slug)라 파일명만으로는 짝을 못 찾는다 — 안의 source_file
        로 맺는다. 246편을 통째로 파싱하면 느리니 앞부분만 읽는다(스키마상
        source_file 은 문서 머리에 있다). 실패하면 그때만 전체를 읽는다.
        """
        if not self.folder:
            return
        idx: dict[str, Path] = {}
        pat = re.compile(r'"source_file"\s*:\s*"((?:[^"\\]|\\.)*)"')
        for jp in self.folder.rglob("*.json"):
            try:
                st = jp.stat()
            except OSError:
                continue
            ck = str(jp)
            cached = self.jcache.get(ck)
            if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                src = cached[2]
            else:
                src = ""
                try:
                    with open(utils.long_path(jp), "r", encoding="utf-8",
                              errors="replace") as f:
                        head = f.read(8192)
                    m = pat.search(head)
                    if m:
                        src = json.loads('"' + m.group(1) + '"')
                    elif '"paper_id"' in head:
                        d = utils.read_json(jp)
                        src = str(d.get("source_file") or "") if isinstance(d, dict) else ""
                except Exception:
                    src = ""
                self.jcache[ck] = (st.st_mtime, st.st_size, src)
            if src:
                idx[Path(src).name] = jp
        self.jmap = idx

    def json_for(self, pdf: Path) -> Path | None:
        hit = self.jmap.get(pdf.name)
        if hit is not None:
            return hit
        side = pdf.with_suffix(".json")          # 옛 규칙(PDF 이름) 호환
        return side if utils.path_exists(side) else None

    def file_rows(self) -> list[dict]:
        return [{"name": p.name, "done": self.json_for(p) is not None}
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
        return res

    def doc_view(self, pdf: Path) -> dict:
        jp = self.json_for(pdf)
        if jp is None:
            return {"extracted": False}
        try:
            d = utils.read_json(jp)
        except Exception as e:  # noqa: BLE001
            return {"extracted": False, "error": f"{type(e).__name__}: {e}"}
        # 옛 산출물은 본문 키가 sections 다(정본 스키마는 body_text).
        # render 는 body_text 만 보므로 여기서만 맞춰 끼운다 — 파일은 안 건드린다.
        if not d.get("body_text") and d.get("sections"):
            d = dict(d, body_text=d["sections"])
        from pubnexus import render
        md = render.to_markdown(d)

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
            "extracted": True, "html": md_to_html(md),
            "info": " · ".join(bits), "notes": notes,
            "paper_id": str(d.get("paper_id") or ""), "json": str(jp),
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
        self.push("run", {"on": True})
        fails: list[tuple[str, str]] = []
        try:
            if self.grobid.state in ("starting", "unknown"):
                self.status("논문 분석기 준비 중 — 준비되면 시작합니다",
                            pct=0, busy=True, force=True)
                self.grobid.wait(GROBID_WARMUP_SEC)
            cfg = utils.load_config()
            for i, p in enumerate(targets, 1):
                if self.cancel.is_set():
                    self.status(f"중지됨 — {i - 1}/{total}편까지 저장",
                                pct=100.0 * (i - 1) / max(total, 1), force=True)
                    break
                head = f"[{i}/{total}] {p.name}"
                self.status(head, pct=100.0 * (i - 1) / max(total, 1),
                            busy=True, force=True)

                def prog(ev: dict, _h=head, _i=i, _t=total) -> None:
                    frac = (_i - 1 + (ev.get("done") or 0) / max(ev.get("total") or 5, 1))
                    self.status(f"{_h} — {ev.get('message') or ev.get('stage') or ''}",
                                pct=100.0 * frac / max(_t, 1), busy=True)

                try:
                    # DOI 는 추출이 끝나야 안다 → 문서를 받아 여기서 이름을 짓는다.
                    doc = single.extract_one(p, cfg, on_progress=prog)
                    pid = str(doc.get("paper_id") or p.stem)
                    dest = p.parent / f"{utils.slug(pid)}.json"
                    utils.write_json(dest, doc)
                    self.jcache.pop(str(dest), None)
                except Exception as e:  # noqa: BLE001 — 파일별 격리
                    fails.append((p.name, f"{type(e).__name__}: {e}"))
                    utils.log(f"[app] 실패 {p.name}: {type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            fails.append(("(전체)", f"{type(e).__name__}: {e}"))
            utils.log(traceback.format_exc())
        finally:
            self.scan_jsons()
            self.push("run", {"on": False, "files": self.file_rows(),
                              "fails": [{"name": n, "why": w} for n, w in fails[:20]],
                              "nfail": len(fails)})
            done = total - len(fails)
            self.status(f"완료 {done}편" + (f" · 실패 {len(fails)}편" if fails else ""),
                        pct=100.0, busy=False, force=True)


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
            targets = [p for p in a.pdfs if a.json_for(p) is None]
            if not targets:
                targets = list(a.pdfs)
        if not targets:
            return {"ok": False, "why": "처리할 PDF 가 없습니다"}
        a.start(targets)
        return {"ok": True, "n": len(targets)}

    def count_todo(self) -> dict:
        a = self._app
        todo = [p for p in a.pdfs if a.json_for(p) is None]
        return {"todo": len(todo), "total": len(a.pdfs)}

    def cancel(self) -> dict:
        self._app.cancel.set()
        self._app.status("중지 요청됨 — 현재 논문이 끝나면 멈춥니다", force=True)
        return {"ok": True}

    def state(self) -> dict:
        a = self._app
        return {"path": str(a.folder or ""), "grobid": a.grobid.state,
                "sec": a.grobid.secs, "files": a.file_rows()}

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
<title>논문 추출 검수</title>
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
  font-size:13px;overflow:hidden;-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-thumb{background:#cfd4db;border-radius:8px;
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:#b3bac3;background-clip:content-box;
  border:3px solid transparent}
::-webkit-scrollbar-track{background:transparent}

/* ── 위쪽 막대 ───────────────────────────────────────────── */
#top{display:flex;align-items:center;gap:10px;padding:9px 14px;
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
#path{flex:1;min-width:0;color:var(--mut);font-size:12px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;direction:rtl;
  text-align:left}
#gro{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--mut);
  padding:3px 9px;border-radius:20px;background:#f4f5f7;white-space:nowrap}
#gro i{width:7px;height:7px;border-radius:50%;background:var(--mut2);
  display:inline-block}
#gro.ok i{background:var(--ok)} #gro.starting i{background:var(--warn);
  animation:pulse 1.1s ease-in-out infinite} #gro.off i{background:#d05a5a}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}

/* ── 진행 막대 ───────────────────────────────────────────── */
#prog{height:0;overflow:hidden;background:var(--panel);
  border-bottom:1px solid transparent;transition:height .16s ease}
#prog.on{height:34px;border-bottom-color:var(--line)}
#prog .in{display:flex;align-items:center;gap:12px;padding:0 14px;height:34px}
#bar{flex:1;height:4px;border-radius:3px;background:#e9ecf1;overflow:hidden}
#bar span{display:block;height:100%;width:0;background:var(--accent);
  border-radius:3px;transition:width .2s ease}
#ptext{font-size:11.5px;color:var(--mut);white-space:nowrap;max-width:52%;
  overflow:hidden;text-overflow:ellipsis}

/* ── 3분할 ──────────────────────────────────────────────── */
#cols{display:grid;height:calc(100% - 47px);grid-template-columns:250px 1px 1.02fr 1px 1fr}
#cols.busy{height:calc(100% - 81px)}
.split{background:var(--line);cursor:col-resize;position:relative}
.split::after{content:"";position:absolute;inset:0 -4px;z-index:5}
.split:hover{background:var(--accent)}
.pane{min-width:0;display:flex;flex-direction:column;background:var(--panel);
  overflow:hidden}

/* ── 왼쪽: 목록 ─────────────────────────────────────────── */
#find{padding:10px 10px 8px}
#q{width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:7px;
  font:inherit;font-size:12px;background:#fafbfc;outline:none}
#q:focus{border-color:#c3cfe6;background:#fff;box-shadow:0 0 0 3px var(--accent-s)}
#count{padding:0 12px 7px;font-size:11px;color:var(--mut2)}
#list{flex:1;overflow-y:auto;overflow-x:hidden;padding:0 6px 10px}
.row{display:flex;gap:7px;align-items:flex-start;padding:6px 8px;border-radius:6px;
  cursor:default;font-size:11.5px;line-height:1.45;color:var(--ink2);
  word-break:break-all}
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
#pgno{font-size:11.5px;color:var(--mut);min-width:56px;text-align:center;
  font-variant-numeric:tabular-nums}
#scroll{flex:1;overflow:auto;background:#eceef1;padding:16px 0 30px;
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
#art{max-width:74ch;margin:0 auto;padding:34px 40px 90px}
#art h1{font-family:var(--ui);font-size:24px;line-height:1.32;font-weight:650;
  letter-spacing:-.012em;margin:0 0 14px;color:#151a21}
#art h2{font-family:var(--ui);font-size:15.5px;font-weight:650;color:#182029;
  margin:38px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line2);
  letter-spacing:-.005em}
#art h3{font-family:var(--ui);font-size:14px;font-weight:640;color:#28313c;
  margin:26px 0 7px}
#art h4,#art h5{font-family:var(--ui);font-size:13px;font-weight:640;
  color:#455060;margin:20px 0 6px}
#art .crumb{color:var(--mut2);font-weight:400;margin:0 3px}
#art p{font-family:var(--serif);font-size:15.3px;line-height:1.78;
  color:#242a31;margin:0 0 15px;text-align:left;
  overflow-wrap:break-word;hyphens:auto}
#art p.bib{font-family:var(--ui);font-size:12px;color:var(--mut);margin:0 0 6px}
#art p.byline{font-family:var(--ui);font-size:12.5px;color:var(--ink2);
  margin:0 0 12px;line-height:1.6}
#art p.tags{margin:0 0 6px;display:flex;flex-wrap:wrap;gap:5px}
#art .tag{font-family:var(--ui);font-size:10.5px;color:var(--mut);
  background:#f4f5f7;border-radius:4px;padding:2px 7px;line-height:1.5;
  letter-spacing:.01em}
#art p.aside{font-family:var(--ui);font-size:11.5px;color:var(--mut)}
#art p.cap{font-family:var(--ui);font-size:12.5px;font-weight:620;color:#2b333d;
  margin:26px 0 8px}
#art blockquote{margin:0 0 15px;padding:2px 0 2px 14px;
  border-left:2px solid var(--line);color:var(--ink2)}
#art ul{font-family:var(--serif);font-size:14.6px;line-height:1.7;
  color:#2b323a;margin:0 0 15px;padding-left:20px}
#art li{margin:0 0 6px}
#art code{font-family:Consolas,"Cascadia Mono",monospace;font-size:.88em;
  background:#f4f5f7;border-radius:3px;padding:1px 4px;color:#48505a}
#art .cite{color:#a8b0ba;font-size:.8em;vertical-align:.28em;
  letter-spacing:-.02em;margin:0 .5px}
#art .twrap{overflow-x:auto;margin:0 0 22px;border:1px solid var(--line);
  border-radius:8px;background:#fff}
#art table{border-collapse:collapse;width:100%;font-family:var(--ui);
  font-size:12.2px;line-height:1.5}
#art th,#art td{padding:7px 11px;text-align:left;vertical-align:top;
  border-bottom:1px solid var(--line2);white-space:normal;min-width:44px}
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
.card{background:#fff;border-radius:12px;padding:22px 24px 18px;width:400px;
  box-shadow:0 12px 40px rgba(18,24,33,.22)}
.card h3{margin:0 0 8px;font-size:15px;font-weight:640}
.card p{margin:0 0 18px;font-size:12.5px;color:var(--ink2);line-height:1.65;
  white-space:pre-line}
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

<div id="prog"><div class="in"><div id="bar"><span></span></div>
  <div id="ptext"></div></div></div>

<div id="cols">
  <div class="pane">
    <div id="find"><input id="q" placeholder="파일 이름으로 걸러내기" spellcheck="false"></div>
    <div id="count"></div>
    <div id="list"></div>
  </div>
  <div class="split" data-s="0"></div>
  <div class="pane">
    <div class="head"><span class="t">PDF 원본</span><span class="sp"></span>
      <button class="zbtn" id="zo" title="축소">−</button>
      <span id="pgno">—</span>
      <button class="zbtn" id="zi" title="확대">＋</button>
      <button class="btn ghost" id="zf" style="padding:3px 9px;font-size:11.5px">폭 맞춤</button>
    </div>
    <div id="scroll"><div class="empty">왼쪽에서 논문을 고르세요</div></div>
  </div>
  <div class="split" data-s="1"></div>
  <div class="pane">
    <div class="head"><span class="t" id="dtitle">추출 결과</span></div>
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
         els:[],io:null,busy:false,ready:false};

/* ── 도우미 ─────────────────────────────────────────────── */
let tmr=null;
function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("on");
  clearTimeout(tmr);tmr=setTimeout(()=>t.classList.remove("on"),2600);}
function ask(title,msg,ok){return new Promise(res=>{
  $("#mt").textContent=title;$("#mm").textContent=msg;$("#mo").textContent=ok||"계속";
  $("#veil").classList.add("on");
  const done=v=>{$("#veil").classList.remove("on");
    $("#mo").onclick=null;$("#mc").onclick=null;res(v);};
  $("#mo").onclick=()=>done(true);$("#mc").onclick=()=>done(false);});}
const api=()=>window.pywebview&&window.pywebview.api;

/* ── 목록 ───────────────────────────────────────────────── */
function setFiles(files){S.files=files||[];draw();}
function draw(){
  const q=$("#q").value.trim().toLowerCase();
  S.view=[];const L=$("#list");L.textContent="";
  const frag=document.createDocumentFragment();let done=0;
  S.files.forEach((f,i)=>{
    if(q&&f.name.toLowerCase().indexOf(q)<0)return;
    S.view.push(i);if(f.done)done++;
    const d=document.createElement("div");
    d.className="row"+(f.done?" done":"")+(i===S.sel?" sel":"");
    d.dataset.i=i;
    d.innerHTML='<span class="dot"></span><span class="nm"></span>';
    d.lastChild.textContent=f.name;
    frag.appendChild(d);});
  L.appendChild(frag);
  $("#count").textContent=S.files.length
    ? `추출됨 ${done} / ${S.view.length}편`+(S.view.length!==S.files.length?` (전체 ${S.files.length})`:"")
    : "";
  $("#ball").disabled=!S.ready||!S.files.length||S.busy;
  $("#bone").disabled=!S.ready||S.sel<0||S.busy;
}
$("#list").addEventListener("click",e=>{
  const r=e.target.closest(".row");if(!r)return;openPaper(+r.dataset.i);});
$("#q").addEventListener("input",draw);

/* ── 논문 열기 ──────────────────────────────────────────── */
let openSeq=0;
async function openPaper(i){
  S.sel=i;draw();
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
}
function renderDoc(d){
  const box=$("#doc");
  if(!d.extracted){
    box.innerHTML='<div class="empty">추출 안 됨<br><span style="font-size:11.5px;color:#b3b9c2">'
      +'위쪽 <b>이 논문만</b> 을 누르면 이 한 편을 처리합니다</span></div>';
    $("#foot").textContent=d.error||"";return;}
  box.innerHTML='<article id="art"></article>';
  $("#art").innerHTML=d.html||"";
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
    b.textContent="폴더 열기";
    b.onclick=()=>api()&&api().reveal(d.json);f.appendChild(b);}
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
window.addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(()=>layout(true),120);});

/* ── 분할선 ─────────────────────────────────────────────── */
(function(){
  let cur=null,x0=0,l0=0,m0=0,r0=0,tw=0;
  const cols=$("#cols");
  document.querySelectorAll(".split").forEach(s=>s.addEventListener("mousedown",e=>{
    cur=+s.dataset.s;x0=e.clientX;
    const st=getComputedStyle(cols).gridTemplateColumns.split(" ").map(parseFloat);
    l0=st[0];m0=st[2];r0=st[4];tw=m0+r0;
    document.body.style.cursor="col-resize";e.preventDefault();}));
  window.addEventListener("mousemove",e=>{
    if(cur===null)return;const dx=e.clientX-x0;
    if(cur===0){const w=Math.max(170,Math.min(460,l0+dx));
      cols.style.gridTemplateColumns=w+"px 1px "+m0+"fr 1px "+r0+"fr";}
    else{const m=Math.max(180,Math.min(tw-180,m0+dx));
      cols.style.gridTemplateColumns=l0+"px 1px "+m+"fr 1px "+(tw-m)+"fr";}});
  window.addEventListener("mouseup",()=>{if(cur===null)return;
    cur=null;document.body.style.cursor="";layout(true);});
})();

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
$("#bstop").onclick=()=>api().cancel();
$("#bopen").onclick=async()=>{const r=await api().pick_folder();
  if(r&&r.ok)toast("폴더를 읽었습니다");};

/* ── 파이썬이 밀어넣는 신호 ─────────────────────────────── */
window.pnx={on(m){
  if(m.kind==="status"){
    if(m.text!==undefined)$("#ptext").textContent=m.text;
    if(m.pct!==null&&m.pct!==undefined)$("#bar").firstChild.style.width=m.pct+"%";
    if(m.busy!==undefined&&m.busy!==null)showProg(m.busy||S.busy);
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
    $("#path").textContent=m.path||"";setFiles(m.files);
  }else if(m.kind==="run"){
    S.busy=!!m.on;showProg(S.busy);
    $("#bstop").style.display=S.busy?"":"none";
    draw();
    if(!m.on){
      if(m.files)setFiles(m.files);
      if(S.sel>=0)openPaper(S.sel);
      if(m.nfail)toast(m.nfail+"편이 실패했습니다 — "+(m.fails[0]?m.fails[0].name:""));
    }
  }}};
function showProg(on){$("#prog").classList.toggle("on",!!on);
  $("#cols").classList.toggle("busy",!!on);}

/* ── 시작 ───────────────────────────────────────────────── */
window.addEventListener("pywebviewready",async()=>{
  S.ready=true;
  const st=await api().state();
  if(st.path)$("#path").textContent=st.path;
  if(st.files&&st.files.length)setFiles(st.files);else draw();
  window.pnx.on({kind:"grobid",state:st.grobid,sec:st.sec});
});
fetch("/files").then(r=>r.json()).then(d=>{
  if(d.path)$("#path").textContent=d.path;
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


def build(folder: str | Path | None = None):
    """창을 만들어 돌려준다(테스트에서 직접 몰아보기 위해 분리)."""
    import webview

    app = App()
    port = serve(app)
    api = Api(app)
    w, h = _screen()
    win = webview.create_window(
        "논문 추출 검수", url=f"http://127.0.0.1:{port}/", js_api=api,
        width=min(1720, w - 40), height=min(1020, h - 70),
        min_size=(1120, 660), background_color="#f4f5f7", text_select=True)
    app.window = win

    start = Path(folder) if folder else utils.resolve(
        (app.cfg.get("project") or {}).get("input_dir") or "")

    def boot() -> None:
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
