"""논문 PDF 구조화 검수 도구 — 원본과 추출물을 나란히 놓고 확인하는 3분할 화면.

    [폴더] [추출하기]                          진행률
    ┌──────────┬──────────────────┬──────────────────┐
    │ 파일 목록 │ PDF 원본          │ 추출 결과         │
    │  ✓ 추출됨 │ (페이지 이미지)    │ (Markdown)       │
    └──────────┴──────────────────┴──────────────────┘

목적은 UI 가 아니라 **추출 품질 검수**다 — 표가 표로 나왔는지, 그림 캡션이 따로 있는지,
섹션 제목이 논문 구조와 맞는지, 인용이 [15] 로 제자리에 박혔는지를 원본과 대조한다.
추출 결과(JSON)는 PDF 옆에 같은 이름으로 저장한다.

ResearchMap 에 얹을 때는 그쪽 화면(pywebview)이 이 자리에 들어간다 — 이 UI 는 검수용이다.
"""
from __future__ import annotations

import json
import os
import re
import queue
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent / "src"))

from pubnexus import utils


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


# ── Markdown → tkinter Text 렌더링 ──────────────────────────────────
# 최종 목적지(ResearchMap)는 pywebview+HTML 이지만, 검수 화면에서 마크다운 기호를
# 날것으로 보여주면 추출 품질을 눈으로 판단할 수 없다. 태그로 서식을 입힌다.
_MD_H = re.compile(r"^(#{1,4})\s+(.*)$")
_MD_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_MD_SUB = re.compile(r"^<sub>(.*)</sub>$")
_MD_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[\d{1,3}\])")


def setup_md_tags(t: tk.Text) -> None:
    base = ("Malgun Gothic", 11)
    t.configure(background="#ffffff", foreground="#1a1a1a", font=base,
                spacing1=3, spacing2=2, spacing3=8, padx=28, pady=20,
                borderwidth=0, highlightthickness=0, cursor="arrow",
                selectbackground="#cfe3f7", selectforeground="#000000")
    t.tag_configure("h1", font=("Malgun Gothic", 17, "bold"), foreground="#12355b",
                    spacing1=16, spacing3=10)
    t.tag_configure("h2", font=("Malgun Gothic", 14, "bold"), foreground="#1d4e79",
                    spacing1=18, spacing3=6)
    t.tag_configure("h3", font=("Malgun Gothic", 12, "bold"), foreground="#2e6da4",
                    spacing1=14, spacing3=4)
    t.tag_configure("h4", font=("Malgun Gothic", 11, "bold"), foreground="#4a7fb5",
                    spacing1=12, spacing3=3)
    t.tag_configure("meta", font=("Malgun Gothic", 9), foreground="#7a7a7a")
    t.tag_configure("quote", font=("Malgun Gothic", 10), foreground="#5a5a5a",
                    lmargin1=18, lmargin2=18, background="#f6f8fa")
    t.tag_configure("bold", font=("Malgun Gothic", 11, "bold"))
    t.tag_configure("italic", font=("Malgun Gothic", 11, "italic"))
    t.tag_configure("code", font=("Consolas", 10), background="#f1f3f5",
                    foreground="#a03030")
    t.tag_configure("cite", font=("Malgun Gothic", 10), foreground="#1a73c8")
    t.tag_configure("table", font=("Consolas", 9), background="#f8f9fa",
                    lmargin1=14, lmargin2=14, spacing1=0, spacing3=0)
    t.tag_configure("bullet", lmargin1=18, lmargin2=32)
    t.tag_configure("warn", font=("Malgun Gothic", 11, "bold"), foreground="#b33")


def _insert_inline(t: tk.Text, line: str, base_tag: str | None = None) -> None:
    """굵게/기울임/코드/인용번호만 태그로 입히고 나머지는 평문."""
    pos = 0
    for m in _MD_INLINE.finditer(line):
        if m.start() > pos:
            t.insert("end", line[pos:m.start()], base_tag or "")
        s = m.group(0)
        if s.startswith("**"):
            t.insert("end", s[2:-2], ("bold",) + ((base_tag,) if base_tag else ()))
        elif s.startswith("`"):
            t.insert("end", s[1:-1], ("code",) + ((base_tag,) if base_tag else ()))
        elif s.startswith("["):
            t.insert("end", s, ("cite",) + ((base_tag,) if base_tag else ()))
        else:
            t.insert("end", s[1:-1], ("italic",) + ((base_tag,) if base_tag else ()))
        pos = m.end()
    t.insert("end", line[pos:] + "\n", base_tag or "")


def render_markdown(t: tk.Text, md: str) -> None:
    t.configure(state="normal")
    t.delete("1.0", "end")
    in_table = False
    for line in md.splitlines():
        if _MD_TABLE.match(line):
            # 구분행(| --- | --- |)은 표시하지 않는다
            if not re.fullmatch(r"\s*\|[\s:|-]+\|\s*", line):
                t.insert("end", line.strip() + "\n", "table")
            in_table = True
            continue
        if in_table:
            t.insert("end", "\n")
            in_table = False
        h = _MD_H.match(line)
        if h:
            t.insert("end", h.group(2) + "\n", f"h{len(h.group(1))}")
            continue
        sub = _MD_SUB.match(line.strip())
        if sub:
            t.insert("end", sub.group(1) + "\n", "meta")
            continue
        if line.startswith(">"):
            _insert_inline(t, line.lstrip("> ").strip(), "quote")
            continue
        if line.startswith("- "):
            _insert_inline(t, "• " + line[2:], "bullet")
            continue
        if line.startswith("*") and line.endswith("*") and len(line) > 2 and "**" not in line:
            t.insert("end", line[1:-1] + "\n", "meta")
            continue
        if line.startswith("⚠"):
            t.insert("end", line + "\n", "warn")
            continue
        _insert_inline(t, line)
    t.configure(state="disabled")


def scan_jsons(folder: Path) -> dict[str, Path]:
    """폴더의 정본 JSON 을 훑어 {원본 PDF 파일명 → JSON 경로} 를 만든다.

    JSON 파일명은 **DOI**(slug)다 — 제목이 나중에 교정돼도 이름이 안 바뀌고,
    같은 논문이 파일명만 다르게 두 벌 있어도 한 JSON 으로 모이기 때문이다.
    그래서 파일명만으로는 짝을 찾을 수 없고, JSON 안의 source_file 로 맺는다.
    """
    idx: dict[str, Path] = {}
    for jp in folder.rglob("*.json"):
        try:
            with open(utils.long_path(jp), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict) or "paper_id" not in d:
            continue                                  # 우리 산출물이 아닌 json
        src = Path(str(d.get("source_file") or "")).name
        if src:
            idx[src] = jp
    return idx


class PdfPane(ttk.Frame):
    """PDF 를 페이지 이미지로 그려 보여준다(PyMuPDF 렌더 → tk 이미지, 외부 의존 없음)."""

    def __init__(self, master):
        super().__init__(master)
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.btn_prev = ttk.Button(bar, text="◀", width=3, command=self.prev)
        self.btn_prev.pack(side="left")
        self.var_page = tk.StringVar(value="—")
        ttk.Label(bar, textvariable=self.var_page, width=10,
                  anchor="center").pack(side="left")
        self.btn_next = ttk.Button(bar, text="▶", width=3, command=self.next)
        self.btn_next.pack(side="left")
        ttk.Button(bar, text="－", width=3, command=lambda: self.zoom(-0.2)).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="＋", width=3, command=lambda: self.zoom(0.2)).pack(side="left")
        ttk.Button(bar, text="폭 맞춤", command=self.fit).pack(side="left", padx=6)

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg="#3a3a3a", highlightthickness=0)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        hs.pack(fill="x")
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Configure>", lambda e: None)

        self.doc = None
        self.page = 0
        self.scale = 1.25
        self._img = None

    def open(self, path: Path) -> None:
        import fitz
        self.close()
        try:
            self.doc = fitz.open(path)
        except Exception as e:
            self.doc = None
            self.canvas.delete("all")
            self.canvas.create_text(20, 20, anchor="nw", fill="white",
                                    text=f"PDF 를 열지 못했습니다:\n{type(e).__name__}: {e}")
            return
        self.page = 0
        self.fit()

    def close(self) -> None:
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self._img = None
        self.canvas.delete("all")
        self.var_page.set("—")

    def fit(self) -> None:
        if not self.doc:
            return
        try:
            w = self.doc[self.page].rect.width or 612
            avail = max(self.canvas.winfo_width() - 4, 200)
            self.scale = max(0.3, min(4.0, avail / w))
        except Exception:
            self.scale = 1.25
        self.draw()

    def zoom(self, delta: float) -> None:
        self.scale = max(0.3, min(4.0, self.scale + delta))
        self.draw()

    def prev(self) -> None:
        if self.doc and self.page > 0:
            self.page -= 1
            self.draw()

    def next(self) -> None:
        if self.doc and self.page < self.doc.page_count - 1:
            self.page += 1
            self.draw()

    def _wheel(self, evt) -> None:
        if evt.state & 0x0004:                       # Ctrl+휠 = 확대/축소
            self.zoom(0.2 if evt.delta > 0 else -0.2)
            return
        top, _ = self.canvas.yview()
        if evt.delta < 0 and top >= 0.999:           # 아래 끝 → 다음 쪽
            self.next()
        elif evt.delta > 0 and top <= 0.001:         # 위 끝 → 이전 쪽
            self.prev()
        else:
            self.canvas.yview_scroll(-evt.delta // 60, "units")

    def draw(self) -> None:
        if not self.doc:
            return
        import fitz
        page = self.doc[self.page]
        pix = page.get_pixmap(matrix=fitz.Matrix(self.scale, self.scale))
        # tkinter 는 PPM 을 그대로 읽는다 → PIL 없이 표시 가능
        self._img = tk.PhotoImage(data=pix.tobytes("ppm"))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._img)
        self.canvas.configure(scrollregion=(0, 0, pix.width, pix.height))
        self.canvas.yview_moveto(0)
        self.var_page.set(f"{self.page + 1} / {self.doc.page_count}")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("논문 PDF 구조화 검수 도구")
        self.geometry("1680x980")
        self.minsize(1100, 640)
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.pdfs: list[Path] = []
        self.cfg = utils.load_config()

        self._build()
        self.after(80, self._pump)
        start = utils.resolve(self.cfg["project"]["input_dir"])
        if start.is_dir():
            self.load_folder(start)

    # ── 화면 ────────────────────────────────────────────────────────
    def _style(self) -> None:
        s = ttk.Style(self)
        try:
            s.theme_use("vista")
        except tk.TclError:
            pass
        self.configure(background="#eef1f5")
        s.configure(".", background="#eef1f5", font=("Malgun Gothic", 9))
        s.configure("TFrame", background="#eef1f5")
        s.configure("Card.TFrame", background="#ffffff", relief="flat",
                    borderwidth=1)
        s.configure("Pane.TLabel", font=("Malgun Gothic", 10, "bold"),
                    foreground="#2a4a6b", background="#eef1f5")
        s.configure("Meta.TLabel", font=("Malgun Gothic", 8),
                    foreground="#6b7684", background="#eef1f5")
        s.configure("Dir.TLabel", font=("Malgun Gothic", 9),
                    foreground="#44546a", background="#eef1f5")
        s.configure("TButton", font=("Malgun Gothic", 9), padding=(10, 5))
        s.configure("Go.TButton", font=("Malgun Gothic", 9, "bold"), padding=(12, 5))

    def _build(self) -> None:
        self._style()
        top = ttk.Frame(self)
        top.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Button(top, text="폴더 열기", command=self.pick).pack(side="left")
        self.var_dir = tk.StringVar(value="(폴더를 고르세요)")
        ttk.Label(top, textvariable=self.var_dir, style="Dir.TLabel").pack(
            side="left", padx=12)
        self.btn_all = ttk.Button(top, text="전부 추출", command=self.extract_all,
                                  style="Go.TButton")
        self.btn_all.pack(side="right")
        self.btn_one = ttk.Button(top, text="이 논문만", command=self.extract_one)
        self.btn_one.pack(side="right", padx=6)
        self.btn_stop = ttk.Button(top, text="중지", command=self.stop, state="disabled")
        self.btn_stop.pack(side="right")

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=14, pady=(0, 6))
        self.var_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.var_status, style="Meta.TLabel").pack(side="left")
        self.prog = ttk.Progressbar(bar, mode="determinate", length=200)
        self.prog.pack(side="right", padx=(12, 0))

        pan = ttk.PanedWindow(self, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # 1) 파일 목록 — 좁게. 원본·추출물 대조가 주인공이므로 폭을 아낀다.
        left = ttk.Frame(pan, width=260)
        left.pack_propagate(False)
        ttk.Label(left, text="논문", style="Pane.TLabel").pack(anchor="w", pady=(0, 4))
        self.var_q = tk.StringVar()
        e = ttk.Entry(left, textvariable=self.var_q, font=("Malgun Gothic", 9))
        e.pack(fill="x", pady=(0, 6))
        self.var_q.trace_add("write", lambda *_: self.refresh_list())
        lbox = ttk.Frame(left, style="Card.TFrame")
        lbox.pack(fill="both", expand=True)
        self.lb = tk.Listbox(lbox, activestyle="none", exportselection=False,
                             font=("Malgun Gothic", 8), borderwidth=0,
                             highlightthickness=0, background="#ffffff",
                             selectbackground="#2a6db5", selectforeground="#ffffff")
        sb = ttk.Scrollbar(lbox, command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(fill="both", expand=True, padx=2, pady=2)
        self.lb.bind("<<ListboxSelect>>", self.on_select)
        pan.add(left, weight=0)

        # 2) PDF 원본
        mid = ttk.Frame(pan)
        ttk.Label(mid, text="PDF 원본", style="Pane.TLabel").pack(anchor="w", pady=(0, 4))
        pbox = ttk.Frame(mid, style="Card.TFrame")
        pbox.pack(fill="both", expand=True)
        self.pdf = PdfPane(pbox)
        self.pdf.pack(fill="both", expand=True, padx=2, pady=2)
        pan.add(mid, weight=3)

        # 3) 추출 결과 — 마크다운을 서식대로 그린다
        right = ttk.Frame(pan)
        head = ttk.Frame(right)
        head.pack(fill="x", pady=(0, 4))
        ttk.Label(head, text="추출 결과", style="Pane.TLabel").pack(side="left")
        self.var_meta = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.var_meta, wraplength=640,
                  justify="left", style="Meta.TLabel").pack(anchor="w", pady=(0, 6))
        box = ttk.Frame(right, style="Card.TFrame")
        box.pack(fill="both", expand=True)
        self.txt = tk.Text(box, wrap="word")
        setup_md_tags(self.txt)
        ts = ttk.Scrollbar(box, command=self.txt.yview)
        self.txt.configure(yscrollcommand=ts.set, state="disabled")
        ts.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)
        pan.add(right, weight=3)

    # ── 폴더·목록 ───────────────────────────────────────────────────
    def pick(self) -> None:
        d = filedialog.askdirectory(title="논문 PDF 가 있는 폴더")
        if d:
            self.load_folder(Path(d))

    def load_folder(self, folder: Path) -> None:
        self.folder = folder
        self.pdfs = sorted(folder.rglob("*.pdf"))
        self.jsons = scan_jsons(folder)
        self.var_dir.set(f"{folder}   (PDF {len(self.pdfs)}개)")
        self.refresh_list()

    def json_of(self, pdf: Path) -> Path | None:
        return self.jsons.get(pdf.name)

    def refresh_list(self) -> None:
        self.jsons = scan_jsons(self.folder)
        q = self.var_q.get().strip().lower()
        self.view = [p for p in self.pdfs if not q or q in p.name.lower()]
        self.lb.delete(0, "end")
        done = 0
        for p in self.view:
            ok = self.json_of(p) is not None
            done += ok
            self.lb.insert("end", ("✓ " if ok else "   ") + p.name)
        self.var_status.set(f"추출됨 {done} / {len(self.view)}편")

    def current(self) -> Path | None:
        sel = self.lb.curselection()
        return self.view[sel[0]] if sel else None

    # ── 선택 시 원본 + 추출물 동시 표시 ──────────────────────────────
    def on_select(self, _evt=None) -> None:
        p = self.current()
        if not p:
            return
        self.pdf.open(p)
        self.show_extract(p)

    def show_extract(self, pdf: Path) -> None:
        from pubnexus import render
        jp = self.json_of(pdf)
        if jp is None:
            self.var_meta.set("아직 추출되지 않았습니다")
            render_markdown(self.txt,
                            "## 아직 추출하지 않은 논문입니다\n\n"
                            "위쪽 **이 논문만** 을 누르면 이 논문 하나를 처리합니다.\n"
                            "**전부 추출** 은 폴더 전체를 처리합니다.\n\n"
                            "추출 결과는 PDF 와 같은 폴더에 DOI 이름의 .json 으로 저장됩니다.")
            return
        try:
            doc = utils.read_json(jp)
            md = render.to_markdown(doc)
        except Exception as e:
            self.var_meta.set(f"읽기 실패: {type(e).__name__}: {e}")
            return
        m = doc.get("meta", {})
        n_par = sum(len(s.get("paragraphs") or []) for s in doc.get("sections", []))
        body = sum(len(p.get("text") or "")
                   for s in doc.get("sections", []) for p in s.get("paragraphs", []))
        self.var_meta.set(
            f"{doc.get('paper_id')} · {m.get('journal') or '?'} {m.get('year') or ''} · "
            f"섹션 {len(doc.get('sections', []))} · 문단 {n_par} · "
            f"그림 {len(doc.get('figures', []))} · 표 {len(doc.get('tables', []))} · "
            f"참고문헌 {len(doc.get('references', []))} · source {doc.get('source')}")

        # 본문이 사실상 비었는데 조용히 넘어가면 안 된다 — 왜 실패했는지 화면에 알린다.
        if body < 500:
            md = ("⚠ 본문을 제대로 뽑지 못했습니다\n\n"
                  f"추출된 본문 {body}자 · 문단 {n_par}개\n\n"
                  "**흔한 원인**\n"
                  "- 스캔본 PDF (텍스트층 없음) — 이 도구의 범위 밖입니다\n"
                  "- 영어가 아닌 논문 — 현재 영어 논문만 지원합니다\n"
                  f"- GROBID 미가동 — 켜면 품질이 크게 올라갑니다 (현재 `{doc.get('source')}`)\n\n"
                  "---\n\n") + md
        render_markdown(self.txt, md)

    # ── 추출 ────────────────────────────────────────────────────────
    def extract_one(self) -> None:
        p = self.current()
        if not p:
            messagebox.showinfo("선택 없음", "왼쪽에서 논문을 먼저 고르세요.")
            return
        self._start([p])

    def extract_all(self) -> None:
        if not self.pdfs:
            messagebox.showinfo("폴더 없음", "먼저 폴더를 여세요.")
            return
        todo = [p for p in self.pdfs if self.json_of(p) is None]
        if not todo:
            if not messagebox.askyesno("이미 완료", "모두 추출되어 있습니다. 다시 할까요?"):
                return
            todo = list(self.pdfs)
        if not messagebox.askyesno(
                "전부 추출", f"{len(todo)}편을 처리합니다.\n"
                             "분량에 비례해 오래 걸리고, 중간에 멈춰도 다시 하면 이어집니다.\n\n"
                             "계속할까요?"):
            return
        self._start(todo)

    def _start(self, targets: list[Path]) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.cancel.clear()
        self.prog.configure(maximum=len(targets), value=0)
        self.btn_all["state"] = self.btn_one["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        self.worker = threading.Thread(target=self._work, args=(targets,), daemon=True)
        self.worker.start()

    def _work(self, targets: list[Path]) -> None:
        try:
            from pubnexus import single
        except Exception as e:
            self.q.put(("done", f"추출 모듈을 불러오지 못했습니다: {type(e).__name__}: {e}"))
            return
        cfg = utils.load_config()
        fails = []
        for i, p in enumerate(targets, 1):
            if self.cancel.is_set():
                self.q.put(("log", "중지했습니다. 여기까지는 저장됐습니다."))
                break
            self.q.put(("step", (i, len(targets), p.name)))
            try:
                # DOI 는 추출이 끝나야 알 수 있으므로 문서를 받아 여기서 이름을 짓는다.
                #   파일명 = DOI(slug), 위치 = PDF 와 같은 폴더.
                #   DOI 를 못 뽑은 논문은 extract_one 이 sha1 기반 paper_id 를 준다.
                doc = single.extract_one(p, cfg)
                pid = str(doc.get("paper_id") or p.stem)
                utils.write_json(p.parent / f"{utils.slug(pid)}.json", doc)
            except Exception as e:
                fails.append((p.name, f"{type(e).__name__}: {e}"))
                self.q.put(("log", f"실패 {p.name}: {type(e).__name__}: {e}"))
        self.q.put(("done", fails))

    def stop(self) -> None:
        self.cancel.set()
        self.var_status.set("중지 요청됨 — 현재 논문이 끝나면 멈춥니다")

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "step":
                    i, total, name = payload
                    self.prog["value"] = i
                    self.var_status.set(f"[{i}/{total}] {name[:60]}")
                elif kind == "log":
                    self.var_status.set(str(payload)[:110])
                elif kind == "done":
                    self.btn_all["state"] = self.btn_one["state"] = "normal"
                    self.btn_stop["state"] = "disabled"
                    self.refresh_list()
                    cur = self.current()
                    if cur:
                        self.show_extract(cur)
                    if isinstance(payload, str):
                        messagebox.showerror("오류", payload)
                    elif payload:
                        msg = "\n".join(f"· {n}: {e}" for n, e in payload[:12])
                        messagebox.showwarning(
                            "일부 실패", f"{len(payload)}편이 실패했습니다:\n\n{msg}")
                    else:
                        self.var_status.set(self.var_status.get() + "  · 완료")
        except queue.Empty:
            pass
        self.after(80, self._pump)


def main() -> None:
    try:
        App().mainloop()
    except Exception:
        try:
            messagebox.showerror("오류", traceback.format_exc())
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
