"""문서를 휴대 가능한 Markdown과 상대 이미지로 자동 저장하는 읽기 전용 도구.

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
  · 정본 JSON은 앱 저장소에, MD·상대 자산은 설정한 폴더에 자동 저장한다.
"""
from __future__ import annotations

import html as _html
import hashlib
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

EXTERNAL_DOCUMENT_EXTENSIONS = {'.rtf', '.ipynb', '.xls', '.msg'}
DOCUMENT_EXTENSIONS = {'.pdf', '.docx', '.pptx', '.xlsx', '.csv', '.tsv', '.txt', '.md',
                       '.html', '.htm', '.hwp', '.hwpx', '.epub', '.odt', '.ods', '.odp', '.eml', '.mbox'} | EXTERNAL_DOCUMENT_EXTENSIONS

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
#  ![캡션](fig1.png) — 렌더러가 처음 인용된 문단 뒤에 놓아 준 그림 한 장.
_IMG_LINE_RE = re.compile(r"^!\[(.*)\]\(([^)\s]+)\)$")
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


#  '그림 추출 4장(1234KB)' 처럼 **잘 된 일의 개수**만 알리는 기록. 만든 사람에게는
#  쓸모가 있어도 논문을 읽는 원장에게는 아니다. 화면에서는 뺀다.
#  문제를 알리는 기록(스캔본 의심·DOI 미식별·… 실패/생략)은 그대로 남긴다.
_STAT_NOTE_RE = re.compile(
    r"^(?:그림 추출 \d|문단 복원 \d|표 복원 \d|표 아닌 것 제거 \d|참고문헌 \d)")


def _is_stat_note(s: str) -> bool:
    return bool(_STAT_NOTE_RE.match(s.strip()))


def fig_path(sha1: str, rel: str) -> Path | None:
    """저장소 그림 폴더 안의 파일 하나. 폴더 밖이거나 없으면 None.

    화면에 그림을 흘려보내는 쪽과 '다른 이름으로 저장' 쪽이 **같은 잣대**를 쓴다.
    한쪽만 느슨하면 그쪽이 구멍이 된다.
    """
    from pubnexus import store
    if not re.fullmatch(r"[0-9a-fA-F]{6,64}", sha1 or ""):
        return None
    base = store.figs_dir(sha1).resolve()
    try:
        fp = (base / rel).resolve()
    except Exception:  # noqa: BLE001
        return None
    if base not in fp.parents and fp.parent != base:
        return None                      # ../ 로 저장소 밖을 훔쳐보려는 시도
    return fp if fp.exists() else None


def _img_size(fp: Path) -> tuple[int, int] | None:
    """이미지의 픽셀 크기를 헤더만 읽어 알아낸다(PNG·JPEG). 모르면 None.

    왜 필요한가 — 크기 없는 <img> 는 불러오기 전까지 높이가 0 이다. 그래서
    본문 [15] 를 눌러 자리를 잡아 놓아도, 그 뒤에 그림이 도착하면서 문서가
    밀려 **엉뚱한 곳에 서 있게** 된다. width/height 를 주면 브라우저가 비율을
    알고 처음부터 자리를 비워 둔다.
    """
    try:
        with open(utils.long_path(fp), "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                return (int.from_bytes(head[16:20], "big"),
                        int.from_bytes(head[20:24], "big"))
            if head[:2] != b"\xff\xd8":          # JPEG 아님
                return None
            f.seek(2)
            while True:
                b = f.read(1)
                if not b:
                    return None
                if b != b"\xff":
                    continue
                while b == b"\xff":              # 채움 바이트(0xFF) 건너뛰기
                    b = f.read(1)
                    if not b:
                        return None
                marker = b[0]
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue                     # 길이 없는 표지
                size = f.read(2)
                if len(size) < 2:
                    return None
                seg = int.from_bytes(size, "big") - 2
                # SOF0~SOF15 에 크기가 있다. DHT(C4)·JPG(C8)·DAC(CC) 는 제외.
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    body = f.read(5)
                    if len(body) < 5:
                        return None
                    w = int.from_bytes(body[3:5], "big")
                    h = int.from_bytes(body[1:3], "big")
                    # 깨진 파일에서 우연히 SOF 처럼 보이는 바이트열을 만나면 말도
                    # 안 되는 크기가 나온다. 그대로 믿으면 빈 자리만 거대해진다.
                    return (w, h) if 1 <= w <= 20000 and 1 <= h <= 20000 else None
                f.seek(seg, 1)
    except OSError:
        return None


def figure_html(cap: str, img: str, sha1: str) -> str:
    """그림 한 장을 HTML 로. 렌더러가 `![캡션](fig1.png)` 로 놓은 자리에 들어간다.

    `img` 는 store.figs_dir(sha1) 기준 상대경로다(절대경로도 받는다). 파일이
    아직 없으면 **빈 자리를 만들지 않고** 캡션만 남긴다.
    """
    from pubnexus import store
    url, wh = "", None
    if img:
        p = Path(img)
        fp = p if p.is_absolute() else (store.figs_dir(sha1) / img)
        if utils.path_exists(fp):
            url = ("/fig?s=" + urllib.parse.quote(sha1) +
                   "&amp;f=" + urllib.parse.quote(img))
            wh = _img_size(fp)
    if not url and not cap:
        return ""
    # 본문의 'Figure 1' 이 찾아올 착지점. 번호 읽기는 captions 것을 그대로 쓴다 —
    # 여기서 정규식을 새로 쓰면 'Supplementary Figure 1' 과 'Figure 1' 이 같은
    # id 를 갖는다(둘 다 있는 논문이 흔하다).
    anchor = ""
    if cap:
        from pubnexus import captions as _cap
        got = _cap.parse_caption(cap, min_desc=0)
        if got and got["kind"] == "fig" and got["num"] is not None \
                and not got["supp"]:
            anchor = f' id="fig-{got["num"]}"'
    out = [f'<figure class="fig"{anchor}>']
    if url:
        # 크기를 함께 보낸다 — 자리를 미리 잡아야 링크 착지점이 밀리지 않는다.
        dim = f' width="{wh[0]}" height="{wh[1]}"' if wh else ""
        out.append(f'<img src="{url}"{dim} loading="lazy" alt="">')
    if cap:
        out.append(f"<figcaption>{_inline(cap)}</figcaption>")
    out.append("</figure>")
    return "".join(out)


def _safe_table(fragment: str) -> str:
    """Preserve merged cells; never execute document HTML, styles, or URLs."""
    from lxml import etree, html
    if len(fragment) > 4_000_000:
        return '<p>표가 너무 커서 화면 표시를 생략했습니다. MD 파일에서 확인하세요.</p>'
    root = html.fragment_fromstring(fragment, create_parent='div')
    allowed = {'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption', 'br', 'p', 'b', 'strong', 'em', 'i', 'sub', 'sup'}
    for element in list(root.iterdescendants()):
        if not isinstance(element.tag, str) or element.tag.lower() in {'script', 'style', 'iframe', 'object', 'embed'}:
            element.drop_tree()
            continue
        if element.tag.lower() not in allowed:
            element.drop_tag()
            continue
        span_attrs = {k: v for k, v in element.attrib.items() if k in {'rowspan', 'colspan'}
                      and element.tag.lower() in {'td', 'th'} and v.isdigit() and 1 <= int(v) <= 1000}
        element.attrib.clear()
        element.attrib.update(span_attrs)
    return ''.join(etree.tostring(child, encoding='unicode', method='html') for child in root)


def md_to_html(md: str, chips: str = "", sha1: str = "", *, source_markers: bool = False,
               asset_index: int | None = None) -> str:
    """to_markdown() 결과를 읽기 화면용 HTML 로.

    앞머리(제목·서지·저자)는 본문과 다른 대접을 한다. pub_types 줄은 **버린다** —
    그 자리는 키워드(chips)가 쓴다.

    그림은 렌더러가 `![캡션](fig1.png)` 로 **처음 인용된 문단 뒤에** 놓아 준다.
    여기서 그것을 진짜 이미지로 바꾼다(`sha1` 은 그림 폴더를 찾는 열쇠).
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    seen_h2 = False          # 첫 ## 전까지가 앞머리
    put_chips = False        # 키워드 칩을 앞머리에 한 번만
    in_refs = False          # 참고문헌 절: 항목마다 #ref-N 앵커를 붙인다
    ul: list[str] = []
    in_block = False

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

        marker = re.fullmatch(r'<!--pnx-block:([0-9a-f]+)-->', line) if source_markers else None
        if marker:
            flush_ul()
            try:
                block_id = bytes.fromhex(marker.group(1)).decode('utf-8')
            except (ValueError, UnicodeError):
                block_id = ''
            if block_id:
                if in_block:
                    out.append('</div>')
                out.append(f'<div class="document-block" data-block-id="{_html.escape(block_id, quote=True)}">')
                in_block = True
                continue
        if source_markers and line == '<!--pnx-end-->':
            flush_ul()
            if in_block:
                out.append('</div>')
                in_block = False
            continue

        if not line:
            flush_ul()
            continue
        if line.lower().startswith('<table'):
            flush_ul()
            fragment = [raw]
            fragment_bytes = len(raw.encode('utf-8'))
            ended = '</table>' in raw.lower()
            while not ended and i < n:
                fragment_bytes += len(lines[i].encode('utf-8')) + 1
                if fragment_bytes > 4_000_000 or len(fragment) >= 100000:
                    out.append('<p>표가 너무 커서 화면 표시를 생략했습니다. MD 파일에서 확인하세요.</p>')
                    # Skip the rest without retaining a second large copy.
                    while i < n and '</table>' not in lines[i].lower():
                        i += 1
                    i = min(n, i + 1)
                    fragment = []
                    break
                fragment.append(lines[i])
                ended = '</table>' in lines[i].lower()
                i += 1
            if not fragment:
                continue
            try:
                out.append(_safe_table('\n'.join(fragment)))
            except (ValueError, TypeError):
                out.append('<p>' + _html.escape('\n'.join(fragment)) + '</p>')
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
            ul.append(_inline(line[2:]))
            continue
        flush_ul()

        img = _IMG_LINE_RE.match(line)          # ![캡션](fig1.png) → 진짜 그림
        if img:
            if asset_index is None:
                out.append(figure_html(img.group(1).strip(), img.group(2).strip(), sha1))
            else:
                caption = _html.escape(img.group(1), quote=True)
                url = '/asset?' + urllib.parse.urlencode({'p': asset_index, 'f': img.group(2)})
                out.append(f'<figure><img loading="lazy" src="{_html.escape(url, quote=True)}" alt="{caption}"><figcaption>{caption}</figcaption></figure>')
            continue

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
    if in_block:
        out.append('</div>')
    if chips and not put_chips:      # 절이 하나도 없는 짧은 문서
        out.append(chips)
    return "\n".join(out)


def sdk_to_html(md: str, blocks=(), sha1: str = '', *, asset_index: int | None = None) -> str:
    from pubnexus.markdown_view import to_html

    def asset_url(destination):
        if asset_index is not None:
            return ('/asset?' + urllib.parse.urlencode({'p': asset_index, 'f': destination}), None)
        path = fig_path(sha1, destination)
        if path is None:
            return None
        return ('/fig?' + urllib.parse.urlencode({'s': sha1, 'f': destination}), _img_size(path))
    return to_html(md, blocks, asset_url=asset_url, safe_table=_safe_table)


# ══════════════════════════════════════════════════════════════════════
#  PDF 페이지 렌더러 — 열린 문서와 그린 이미지를 둘 다 LRU 로 붙잡는다
# ══════════════════════════════════════════════════════════════════════
class PageRenderer:
    MAX_DOCS = 4
    MAX_IMGS = 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._docs: OrderedDict[int, tuple[Path, object, tuple[int, int]]] = OrderedDict()
        self._imgs: OrderedDict[tuple, bytes] = OrderedDict()
        self._seq = 0

    def _retire(self, key: int) -> None:
        entry = self._docs.pop(key, None)
        if entry:
            entry[1].close()
        for cached in list(self._imgs):
            if cached[0] == key:
                self._imgs.pop(cached, None)

    def open(self, path: Path) -> dict:
        """문서를 열고 {key, pages:[[w,h],…]} 를 돌려준다."""
        import fitz
        path = Path(path).resolve()
        with self._lock:
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            for k, (p, d, previous) in list(self._docs.items()):
                if p == path:
                    if previous == signature:
                        self._docs.move_to_end(k)
                        return {"key": k, "pages": self._sizes(d)}
                    self._retire(k)
            try:
                doc = fitz.open(str(path))
            except Exception:
                # 260자 초과 경로 — 스트림으로 우회
                with open(utils.long_path(path), "rb") as f:
                    doc = fitz.open(stream=f.read(), filetype="pdf")
            self._seq += 1
            k = self._seq
            self._docs[k] = (path, doc, signature)
            while len(self._docs) > self.MAX_DOCS:
                self._retire(next(iter(self._docs)))
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
            ent = self._docs.get(key)
            if ent is None:
                return None
            try:
                stat = ent[0].stat()
                current = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                current = None
            if current != ent[2]:
                self._retire(key)
                return None
            hit = self._imgs.get(ck)
            if hit is not None:
                self._imgs.move_to_end(ck)
                return hit
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
            for _, (_, d, _) in self._docs.items():
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
        self.state = "unknown"          # unknown | installing | starting | ok | off
        self.secs = 0                   # starting 은 경과초, installing 은 퍼센트
        self.ready = threading.Event()
        # 결판이 났다는 신호(켜졌든 못 켰든). 처리 쪽은 이것을 기다린다 —
        # 첫 실행에서 엔진을 내려받는 데 걸리는 시간은 회선에 달렸고, 정해진
        # 초만 기다리다 폴백으로 내려가면 **말없이 저품질 산출물**이 나온다.
        self.settled = threading.Event()
        self._t: threading.Thread | None = None

    def _set(self, st: str, secs: int = 0) -> None:
        self.state, self.secs = st, secs
        if st == "ok":
            self.ready.set()
        if st in ("ok", "off"):
            self.settled.set()
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
        # 이 PC 에 엔진이 없으면 ensure 가 내려받아 설치한다(최초 1회, 수백 MB).
        # 그동안의 진행률과, 켜지기를 기다리는 경과초는 **다른 것**이다.
        # 한 자리에 섞어 보내면 "준비 중 87초"처럼 거짓을 보여주게 된다.
        installing = gs.find_root() is None
        self._set("installing" if installing else "starting", 0)
        t0 = time.time()

        last = [-1]

        def on_progress(v: int) -> None:
            nonlocal installing
            if installing and v >= 100:
                installing = False          # 다 받았다 → 이제 기동을 기다린다
                self._set("starting", 0)
                return
            v = int(v)
            if v == last[0]:
                return                      # 1MB 마다 부른다. 값이 같으면 보내지 않는다
            last[0] = v
            self._set("installing" if installing else "starting", v)

        ok = gs.ensure(self.url, timeout=GROBID_WARMUP_SEC,
                       on_progress=on_progress)
        if ok:
            utils.log(f"[app] 분석 엔진 준비됨 ({time.time() - t0:.0f}초)")
        else:
            utils.log("[app] 분석 엔진 없이 진행 — PyMuPDF 폴백")
        self._set("ok" if ok else "off")

    def wait(self, seconds: float, cancel=None) -> bool:
        """켜졌거나 못 켰다는 **결판**이 날 때까지 기다린다.

        정해진 초만 기다리고 넘어가면, 첫 실행에서 엔진을 내려받는 중인 PC 는
        말없이 폴백으로 떨어져 저품질 산출물을 낸다(품질 0.96 → 0.65, 표 0).
        내려받는 중이면 회선 사정만큼 더 기다린다.
        """
        if self.state == "ok":
            return True
        deadline = time.time() + seconds
        while True:
            if cancel and cancel():
                return False
            if self.settled.wait(min(.2, max(0.0, deadline - time.time()))):
                return self.state == "ok"
            if time.time() < deadline:
                continue
            if self.state != "installing":
                return False                # 기동만 기다리던 것이면 약속대로 끝낸다
            deadline = time.time() + seconds  # 내려받는 중이면 더 준다


# ══════════════════════════════════════════════════════════════════════
#  앱 상태
# ══════════════════════════════════════════════════════════════════════
class App:
    def __init__(self) -> None:
        from pubnexus.settings import Preferences
        self.preferences = Preferences()
        self.output_dir = self.preferences.output_dir
        self._operation_lock = threading.RLock()
        self._updater = None
        self._update_worker = None
        self._install_requested = False
        self._closing = False
        self._hidden = False
        self._worker_mode = False
        self._update_applied = False
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
        self.exported: dict[str, dict] = {}      # 실제 MD·자산 저장을 확인한 결과
        self.export_errors: dict[str, str] = {}
        self._comparison_cache: OrderedDict = OrderedDict()
        persisted = self.ui_load().get('exports') or {}
        if isinstance(persisted, dict):
            self.exported = {str(k): v for k, v in persisted.items() if isinstance(v, dict)}
        self.renderer = PageRenderer()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.tr_worker: threading.Thread | None = None   # 한국어 번역 일꾼
        self.tr_stop = threading.Event()
        self.queue = None            # 배치 처리 순서(WorkQueue) — 클릭하면 당긴다
        self.grobid = Grobid(
            (self.cfg.get("grobid") or {}).get("url", ""),
            notify=lambda st, sec: self.push("grobid", {"state": st, "sec": sec}))
        self._last_push = 0.0
        self._active_path: Path | None = None
        self._pending_paths: set[str] = set()
        self._background_queue: list[tuple[Path, bool]] = []
        self._background_total = 0
        self._accepting_work = False
        self._active_paths: set[str] = set()
        self._worker_pids: dict[str, int] = {}
        self._resource_state: dict = {}
        self._file_progress: dict[str, dict] = {}
        self._warning_cache: dict[tuple, list[str]] = {}
        self._legacy_reports: dict[tuple, dict] = {}

    def extraction_busy(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def change_output_dir(self, directory: Path | None) -> dict:
        with self._operation_lock:
            if self.extraction_busy() or self._closing:
                raise RuntimeError('추출을 마치거나 중지한 뒤 저장 폴더를 바꾸세요.')
            self.output_dir = self.preferences.set_output_dir(directory)
            state = self.settings_state()
        self.push('settings', state)
        return state

    def settings_state(self) -> dict:
        from pubnexus.version import VERSION
        return {'ok': True, 'output_dir': str(self.output_dir),
                'auto_update': self.preferences.auto_update, 'current_version': VERSION,
                'theme': self.preferences.theme,
                'update': self._updater.snapshot() if self._updater else
                    {'status': 'idle', 'message': '업데이트를 확인할 수 있습니다.'}}

    def _get_updater(self):
        with self._operation_lock:
            if self._updater is None:
                from pubnexus.updater import UpdateManager
                self._updater = UpdateManager(notify=lambda state: self.push('update', state))
            return self._updater

    def check_update_async(self) -> dict:
        with self._operation_lock:
            if self._closing:
                return {'ok': False, 'why': '앱을 종료하는 중입니다.'}
            if self._update_worker and self._update_worker.is_alive():
                return {'ok': True, 'update': self._get_updater().snapshot()}
            manager = self._get_updater()
            def check():
                try:
                    manager.check_and_download()
                except Exception as error:
                    utils.log(f'업데이트 확인 실패: {error}')
                    self.push('update', {'status': 'error', 'message': '업데이트를 확인하지 못했습니다. 다시 시도할 수 있습니다.'})
            self._update_worker = threading.Thread(target=check, daemon=True)
            self._update_worker.start()
            return {'ok': True, 'update': manager.snapshot()}

    def start_auto_update(self, *, hidden: bool = False) -> None:
        if getattr(sys, 'frozen', False) and not hidden and self.preferences.auto_update:
            self.check_update_async()

    def request_close(self) -> bool:
        with self._operation_lock:
            busy = self.extraction_busy() or (self.tr_worker and self.tr_worker.is_alive())
            if not busy:
                self._closing = True
        if busy:
            # closing runs on the GUI thread; evaluating JS there would wait on itself.
            threading.Thread(target=self.push, args=('notice', {
                'text': '추출을 마치거나 중지한 뒤 앱을 닫으세요.'}), daemon=True).start()
        return not busy

    def request_install_update(self) -> dict:
        with self._operation_lock:
            if self.extraction_busy() or self._closing or (self.tr_worker and self.tr_worker.is_alive()):
                return {'ok': False, 'why': '추출을 마치거나 중지한 뒤 업데이트하세요.'}
            if not getattr(sys, 'frozen', False):
                return {'ok': False, 'why': '설치된 Windows 앱에서 업데이트를 설치할 수 있습니다.'}
            if self._get_updater().snapshot().get('status') != 'ready':
                return {'ok': False, 'why': '먼저 업데이트를 확인하고 다운로드하세요.'}
            self._install_requested = True
            self._closing = True
        # WinForms destroy synchronously invokes the GUI closing callback.
        # Never hold the bridge thread's lock while waiting for that callback.
        self.push('update', {'status': 'installing', 'message': '앱을 닫고 업데이트합니다. 설치 후 다시 열립니다.'})
        try:
            self.window.destroy()
        except Exception as error:
            with self._operation_lock:
                self._install_requested = False
                self._closing = False
            return {'ok': False, 'why': str(error)}
        return {'ok': True}

    def apply_update_on_exit(self) -> dict | None:
        with self._operation_lock:
            if (self._update_applied or not self._closing or self.extraction_busy()
                    or not self._updater or not (self._install_requested or self.preferences.auto_update)
                    or self._updater.snapshot().get('status') != 'ready'):
                return None
            result = self._updater.install(restart=self._install_requested)
            self._update_applied = bool(result.get('ok'))
            if not result.get('ok'):
                utils.log('업데이트 설치를 시작하지 못했습니다: ' + str(result.get('why') or result.get('message') or '다음 실행에서 다시 확인합니다.'))
            return result

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
        if self._worker_mode:
            return  # Only the GUI owner commits browsing/settings state.
        try:
            with self._operation_lock:
                d = self.ui_load()
                d.update(kw)
                self.ui_path().parent.mkdir(parents=True, exist_ok=True)
                utils.write_json(self.ui_path(), d)
        except Exception:  # noqa: BLE001 — 기억 못 해도 앱은 돌아간다
            pass

    # ── 폴더 ────────────────────────────────────────────────────────
    def folder_files(self, folder: Path) -> list[Path]:
        folder = Path(folder).resolve()
        output = self.output_dir.resolve()
        return sorted((p.resolve() for p in folder.rglob("*")
                       if p.is_file() and p.suffix.lower() in DOCUMENT_EXTENSIONS
                       and not p.resolve().is_relative_to(output)
                       and not any(part.casefold() == "markdown"
                                   for part in p.relative_to(folder).parts[:-1])),
                      key=lambda p: (p.name.casefold(), str(p).casefold()))

    def add_files(self, paths: list[Path]) -> None:
        known = {_norm(p) for p in self.pdfs}
        for raw in paths:
            p = Path(raw).resolve()
            if p.is_file() and p.suffix.lower() in DOCUMENT_EXTENSIONS and _norm(p) not in known:
                self.pdfs.append(p)
                known.add(_norm(p))
        if self.pdfs and self.folder is None:
            self.folder = self.pdfs[0].parent
        self.scan_store()
        self.push("folder", {"path": str(self.folder or ""), "files": self.file_rows(),
                             "output_dir": str(self.output_dir)})

    def add_dropped_files(self, event: dict) -> dict:
        """Use WebView2's native file-drop paths; never guess from a file name."""
        paths = []
        try:
            for item in (event.get('dataTransfer') or {}).get('files') or []:
                raw = item.get('pywebviewFullPath')
                if not isinstance(raw, str) or not Path(raw).is_absolute():
                    continue
                path = Path(raw)
                paths.extend(self.folder_files(path) if path.is_dir() else [path])
            if not paths:
                return {"ok": False, "why": "파일 경로를 받지 못했습니다. 파일 추가를 사용하세요."}
            before = len(self.pdfs)
            self.add_files(paths)
            return {"ok": True, "n": len(self.pdfs) - before}
        except (OSError, RuntimeError, ValueError) as error:
            return {"ok": False, "why": str(error)}

    def set_folder(self, folder: Path) -> None:
        if self.worker and self.worker.is_alive():
            raise RuntimeError("추출을 마치거나 중지한 뒤 폴더를 바꾸세요.")
        self.folder = Path(folder)
        self.status(f"{self.folder.name} 훑는 중…", busy=True, force=True)
        self.pdfs = self.folder_files(self.folder)
        self.scan_store()
        ui = self.ui_load()
        last = ui.get("paper") if _norm(ui.get("folder") or "") == _norm(self.folder) else None
        self.ui_save(folder=str(self.folder))
        self.push("folder", {"path": str(self.folder), "files": self.file_rows(),
                             "last": last, "output_dir": str(self.output_dir)})
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
        if hit and not deep:
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
            self.push("cached", {"name": pdf.name, "path": str(pdf)})
            return sha
        self.done.pop(_norm(pdf), None)
        self.found.pop(_norm(pdf), None)
        return None

    def file_rows(self) -> list[dict]:
        rows = []
        for p in self.pdfs:
            key = _norm(p)
            output = self.exported.get(key) or {}
            saved = bool(output.get("markdown") and Path(output["markdown"]).is_file())
            try:
                stat = p.stat()
                current = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                current = None
            changed = bool(saved and current != tuple(output.get("source_stat") or ()))
            saved = saved and not changed
            error = self.export_errors.get(key)
            needs_refresh = False
            if saved and p.suffix.lower() == '.pdf':
                from pubnexus import pdf_markdown
                source_type = output.get('source_type') or self._legacy_report(output).get('source')
                identity = output.get('pdf_conversion_engine')
                if source_type == 'pdf_general':
                    identity = output.get('pdf_conversion_attempt') or identity
                needs_refresh = (source_type in ('pdf_general', 'pdf_sdk') and pdf_markdown.available()
                                 and identity != pdf_markdown.engine_id())
            state = "failed" if error else ("changed" if changed else (output.get("status", "complete") if saved else "ready"))
            progress = self._file_progress.get(key) or {}
            active = key in self._active_paths or (self._active_path is not None and _norm(self._active_path) == key)
            waiting = key in self._pending_paths
            if active:
                state = 'running'
            elif waiting:
                state = 'queued'
            elif progress.get('status') == 'paused' and not error:
                state = 'paused'
            elif needs_refresh and not error:
                state = 'ready'
            readable = bool(saved and not needs_refresh)
            ready = bool(readable and not error and not active and not waiting and state in ('complete', 'partial'))
            warnings = self._display_warnings(output)
            if needs_refresh:
                warnings = ['본문·그림 배치 방식이 개선됐습니다. 선택 추출로 MD를 다시 저장하세요.'] + warnings
            rows.append({"name": p.name, "path": str(p), "done": ready, 'md_ready': readable,
                         'needs_refresh': needs_refresh,
                         "cached": key in self.done, "status": state,
                         "size_bytes": current[0] if current else 0,
                         "pages": progress.get('progress_pages') or output.get("pages"),
                         'progress_pct': 100 if ready else (0 if needs_refresh and not active else progress.get('progress_pct', 0)),
                         'progress_stage': '다시 저장 필요' if needs_refresh and not active else progress.get('progress_stage', ''),
                         'progress_page': progress.get('progress_page'), 'progress_pages': progress.get('progress_pages'),
                         "warnings": warnings,
                         'warning_details': output.get('warning_details', output.get('warnings', [])),
                         "unit_count": output.get('unit_count'), "unit_label": output.get('unit_label', ''),
                         "markdown": output.get("markdown", ""), "assets": output.get('assets', ''), "error": error or ""})
        return rows

    def _legacy_report(self, output: dict) -> dict:
        from pubnexus import store
        sha = output.get('source_sha1') or ''
        if not re.fullmatch(r'[0-9a-f]{40}', sha):
            return {}
        key = (sha, output.get('markdown_sha256'))
        if key not in self._legacy_reports:
            doc = store.load(sha) or {}
            self._legacy_reports[key] = {'source': doc.get('source'),
                                         'pages': (doc.get('extraction_report') or {}).get('pages') or []}
        return self._legacy_reports[key]

    def _display_warnings(self, output: dict) -> list[str]:
        from pubnexus.warning_summary import summarize_warnings
        raw = output.get('warning_details', output.get('warnings', []))
        context = output.get('warning_context')
        if context is not None or not raw or not output.get('source_sha1'):
            return summarize_warnings(raw, context)
        key = (output['source_sha1'], output.get('markdown_sha256'), tuple(map(str, raw)))
        if key not in self._warning_cache:
            # Old exports lack page-level display context. Read their canonical report once.
            self._warning_cache[key] = summarize_warnings(raw, self._legacy_report(output).get('pages'))
        return self._warning_cache[key]

    # ── 문서 한 편 ──────────────────────────────────────────────────
    def source_view(self, i: int) -> dict:
        """Read the original preview without touching extraction, results or queue."""
        if not (0 <= i < len(self.pdfs)):
            return {"error": "없는 항목"}
        pdf = self.pdfs[i]
        suffix = pdf.suffix.lower()
        slides = suffix in {'.pptx', '.ppt', '.odp'}
        sheets = suffix in {'.xlsx', '.xls', '.ods'}
        res: dict = {"name": pdf.name, "path": str(pdf), "source_ready": True,
                     "source_label": ('원본 슬라이드' if slides else '원본 시트' if sheets else
                                      '원본 PDF' if suffix == '.pdf' else '원본 문서'),
                     "source_unit_label": '슬라이드' if slides else '쪽'}
        try:
            from pubnexus.preview import preview_path
            original_view = preview_path(pdf)
            res.update(self.renderer.open(original_view.path))
            res.update(source_converted=original_view.converted, source_engine=original_view.engine)
        except Exception as e:  # noqa: BLE001
            res["pdf_error"] = f"{type(e).__name__}: {e}"
            res["pages"] = []
        return res

    def paper(self, i: int) -> dict:
        res = self.source_view(i)
        if res.get('error'):
            return res
        pdf = self.pdfs[i]
        res["doc"] = self.doc_view(pdf)
        res['revision'] = res['doc'].get('revision')
        if not res["doc"].get("extracted"):
            res["doc"]["queued"] = self.bump(pdf)
        return res

    def result_view(self, i: int) -> dict:
        """Read a saved result without preparing its original preview or queue."""
        if not (0 <= i < len(self.pdfs)):
            return {"error": "없는 항목"}
        source = self.pdfs[i]
        doc = self.doc_view(source)
        return {"name": source.name, "path": str(source), "doc": doc,
                "revision": doc.get('revision'), "source_ready": False, "pages": []}

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

    def _saved_snapshot(self, source: Path, canonical: dict | None = None) -> dict | None:
        """Read a saved MD/manifest pair; a newer canonical cannot borrow its map."""
        output = dict(self.exported.get(_norm(source)) or {})
        path = Path(output['markdown']) if output.get('markdown') else None
        if path is None or not utils.path_exists(path):
            return None
        with open(utils.long_path(path), 'rb') as stream:
            data = stream.read()
        digest = hashlib.sha256(data).hexdigest()
        manifest = {}
        if output.get('assets'):
            try:
                manifest = utils.read_json(Path(output['assets']) / 'manifest.json') or {}
            except (OSError, ValueError):
                pass
        content_identity = 'canonical_content_revision' in manifest
        canonical_basis = ({k: v for k, v in canonical.items() if k != 'source_file'}
                           if canonical is not None and content_identity else canonical)
        canonical_revision = (hashlib.sha256(json.dumps(canonical_basis, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest() if canonical is not None else None)
        identity = manifest.get('canonical_content_revision') if content_identity else manifest.get('revision')
        valid = bool(canonical_revision and identity == canonical_revision
                     and manifest.get('markdown_sha256') == digest)
        reason = ('저장된 MD가 외부에서 변경되어 위치 연결을 껐습니다.'
                  if manifest and manifest.get('markdown_sha256') != digest else
                  '저장된 MD와 현재 정본이 달라 위치 연결을 껐습니다. 저장이 끝난 뒤 MD를 다시 여세요.')
        # Coordinates may change even when MD bytes do not: include both identities.
        revision = hashlib.sha256((digest + ':' + str(identity or '')).encode()).hexdigest()
        return {'markdown': data.decode('utf-8-sig', errors='replace'), 'revision': revision,
                'mapping_valid': valid, 'reason': reason}

    def doc_view(self, pdf: Path, lang: str = "en") -> dict:
        from pubnexus import store
        sha = self.sha_for(pdf, deep=True)
        if not sha:
            return {"extracted": False}
        d = store.load(sha)
        snapshot = self._saved_snapshot(pdf, d)
        output = self.exported.get(_norm(pdf)) or {}
        previous_source = bool(output and output.get('source_sha1') != sha)
        if snapshot and not previous_source and not snapshot['mapping_valid']:
            index = next((i for i, p in enumerate(self.pdfs) if p == pdf), None)
            return {'extracted': True, 'html': sdk_to_html(snapshot['markdown'], asset_index=index),
                    'md_text': snapshot['markdown'], 'revision': snapshot['revision'], 'mapping_valid': False,
                    'notes': [snapshot['reason']], 'note_details': [snapshot['reason']],
                    'markdown': output.get('markdown', ''), 'sha1': sha}
        if d is None:
            return {"extracted": False}
        # 옛 산출물은 본문 키가 sections 다(정본 스키마는 body_text).
        # render 는 body_text 만 보므로 여기서만 맞춰 끼운다 — 파일은 안 건드린다.
        if not d.get("body_text") and d.get("sections"):
            d = dict(d, body_text=d["sections"])
        from pubnexus import render, comparison
        d = comparison.attach(d, pdf)
        # 번역은 **정본 단계에서 갈아 끼운다** — 그 뒤 렌더·화면 코드가 하나뿐이어야
        # 어긋나지 않는다. 아직 안 된 문단은 원문 그대로라, 번역 중에도 읽을 수 있다.
        view, tr_done, tr_total = d, 0, 0
        if lang == "ko":
            from pubnexus import translate as tr
            cache = tr.load_cache(sha)
            tr_total = tr.unit_count(d)
            tr_done = sum(1 for k in cache if k)
            view = tr.apply(d, cache)
        md = render.to_markdown(view)
        if isinstance(view.get('sdk_markdown'), str):
            html = sdk_to_html(md, view.get('sdk_blocks') or [], sha)
        else:
            html = md_to_html(render.to_markdown(view, source_markers=True),
                              chips_html(d.get("meta") or {}), sha, source_markers=True)
        # Keep the MD bytes and identity captured before rendering together.
        # An export may publish a newer MD while this old canonical is rendered.
        saved = (snapshot['markdown'], False) if snapshot and not previous_source else None
        if saved:
            md, externally_changed = saved
            if externally_changed:
                index = next((i for i, p in enumerate(self.pdfs) if p == pdf), None)
                html = sdk_to_html(md, asset_index=index)

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
        notes = [x for x in ((d.get("qc") or {}).get("notes") or [])
                 if not _is_stat_note(str(x))]
        notes.extend((d.get('extraction_report') or {}).get('warnings') or [])
        note_details = list(dict.fromkeys(map(str, notes)))
        from pubnexus.warning_summary import summarize_warnings
        notes = summarize_warnings(notes, (d.get('extraction_report') or {}).get('pages'))
        if previous_source:
            notes.insert(0, '원본이 변경되어 이전 MD는 그대로 보존했습니다. 현재 원본을 다시 추출하세요.')
        if saved and saved[1]:
            notes.insert(0, '저장된 MD가 외부에서 변경되어 원본 위치 연결을 껐습니다.')
        if nchar < 500 and not d.get('tables') and (d.get('extraction_report') or {}).get('status') != 'complete':
            notes.insert(0, f"본문이 {nchar}자뿐 — 스캔본이거나 추출 실패일 수 있다")
        return {
            "extracted": True, "html": html,
            "info": " · ".join(bits), "notes": notes, 'note_details': note_details,
            "paper_id": str(d.get("paper_id") or ""),
            "json": str(store.doc_path(sha)), "sha1": sha,
            "thin": nchar < 500,
            "lang": lang, "tr_done": tr_done, "tr_total": tr_total,
            "markdown": (self.exported.get(_norm(pdf)) or {}).get("markdown", ""),
            "export_error": self.export_errors.get(_norm(pdf), ""),
            "md_text": md,
            'revision': snapshot['revision'] if snapshot and not previous_source else None,
            'mapping_valid': bool(snapshot and snapshot['mapping_valid'] and not previous_source),
        }

    def saved_markdown(self, source: Path):
        output = self.exported.get(_norm(source)) or {}
        path = output.get('markdown')
        if not path or not utils.path_exists(Path(path)):
            return None
        with open(utils.long_path(Path(path)), 'rb') as stream:
            data = stream.read()
        changed = hashlib.sha256(data).hexdigest() != output.get('markdown_sha256')
        return data.decode('utf-8-sig', errors='replace'), changed

    def comparison_doc(self, index: int) -> tuple[dict, Path]:
        from pubnexus import store, comparison
        from pubnexus.preview import preview_path, renderer_identity
        if type(index) is not int or not 0 <= index < len(self.pdfs):
            raise ValueError('문서를 선택하세요.')
        source = self.pdfs[index]
        source_stat = source.stat()
        output = self.exported.get(_norm(source)) or {}
        sha = self.sha_for(source, deep=True)
        doc = store.load(sha) if sha else None
        snapshot = self._saved_snapshot(source, doc)
        if snapshot and not snapshot['mapping_valid']:
            raise ValueError(snapshot['reason'])
        md_path = Path(output['markdown']) if output.get('markdown') else None
        md_stat = md_path.stat() if md_path and md_path.is_file() else None
        cache_key = (_norm(source), source_stat.st_size, source_stat.st_mtime_ns,
                     md_stat.st_size if md_stat else None, md_stat.st_mtime_ns if md_stat else None,
                     output.get('source_sha1'), snapshot['revision'] if snapshot else None, renderer_identity(source))
        hit = self._comparison_cache.get(cache_key)
        display_stat = hit[2].stat() if hit and hit[2].is_file() else None
        if hit and display_stat and hit[3] == (display_stat.st_size, display_stat.st_mtime_ns):
            self._comparison_cache.move_to_end(cache_key)
            return hit[0], hit[2]
        if not doc:
            raise ValueError('먼저 문서를 추출하세요.')
        if output and output.get('source_sha1') != sha:
            raise ValueError('원본이 변경되어 이전 결과의 위치 연결을 중지했습니다. 다시 추출하세요.')
        saved = self.saved_markdown(source)
        if saved and saved[1]:
            raise ValueError('저장된 MD가 외부에서 변경되어 위치 연결을 껐습니다.')
        if not doc.get('body_text') and doc.get('sections'):
            doc = dict(doc, body_text=doc['sections'])
        try:
            display_source = preview_path(source).path
        except RuntimeError:
            display_source = source
        doc = comparison.attach(doc, display_source)
        doc['_reader_revision'] = snapshot['revision'] if snapshot else None
        display_stat = display_source.stat()
        mapping = dict(comparison.document_map(doc, display_source), revision=doc['_reader_revision'])
        self._comparison_cache[cache_key] = (doc, mapping, display_source,
                                             (display_stat.st_size, display_stat.st_mtime_ns))
        while len(self._comparison_cache) > 4:
            self._comparison_cache.popitem(last=False)
        return doc, display_source

    def document_map(self, index: int) -> dict:
        from pubnexus import comparison
        try:
            doc, source = self.comparison_doc(index)
            hit = next((value for value in self._comparison_cache.values() if value[0] is doc), None)
            return dict(hit[1] if hit else comparison.document_map(doc, source), revision=doc.get('_reader_revision'))
        except (ValueError, OSError) as error:
            return {'blocks': [], 'pages': [], 'error': str(error), 'revision': None}

    # ── 추출 ────────────────────────────────────────────────────────
    def start(self, targets: list[Path], force: bool = False) -> list[Path]:
        with self._operation_lock:
            busy = self.extraction_busy()
            if self._closing or (busy and (not self._accepting_work or self.cancel.is_set())):
                raise RuntimeError('현재 추출을 중지하거나 마무리하는 중입니다. 잠시 후 다시 추가하세요.')
            if not busy:
                self.cancel.clear()
                self._background_queue = []
                self._pending_paths = set()
                self._background_total = 0
                self._accepting_work = True
            accepted = []
            for path in targets:
                key = _norm(path)
                if key in self._pending_paths:
                    continue
                accepted.append(path)
                self._pending_paths.add(key)
                self._file_progress[key] = {'progress_pct': 0, 'progress_stage': '대기'}
                self._background_queue.append((path, force))
            self._background_total += len(accepted)
            if not busy and accepted:
                self.worker = threading.Thread(target=self._background_work,
                                               args=(self.output_dir,), daemon=True)
                self.worker.start()
            elif not busy:
                self._accepting_work = False
        if busy and accepted:
            self.push('queued', {'files': self.file_rows(), 'total': self._background_total,
                                 'accepted_paths': [str(path) for path in accepted]})
        return accepted

    def _background_work(self, output_dir: Path) -> None:
        from concurrent.futures import ThreadPoolExecutor
        import queue as event_queue
        from pubnexus import extraction_process, resource_budget, store
        from pubnexus.general_pdf import ExtractionCancelled
        policy = resource_budget.Policy()
        events = event_queue.Queue(maxsize=2048)
        coordinator_failed = threading.Event()
        running = {}
        saved_count = 0
        partial_count = 0
        completed_count = 0
        fails = []
        self.push('run', {'on': True, 'total': self._background_total})

        def event(kind, payload, source):
            nonlocal saved_count, partial_count
            key = _norm(source)
            if kind == 'prog' and payload.get('path'):
                self._active_path = source
                stage = payload.get('stage') or ''
                if payload.get('elapsed_sec') is not None:
                    stage += f" · {payload['elapsed_sec']}초 경과"
                    payload = dict(payload, stage=stage)
                self._file_progress[key] = {
                    'progress_pct': payload.get('file_pct', 0), 'progress_stage': stage,
                    'progress_page': payload.get('page'), 'progress_pages': payload.get('pages')}
                payload = dict(payload, i=completed_count + 1, total=self._background_total,
                               pct=100 * (completed_count + sum(
                                   self._file_progress.get(k, {}).get('progress_pct', 0) / 100 for k in running))
                                   / max(1, self._background_total))
            elif kind == 'mark' and payload.get('path'):
                key = _norm(payload['path'])
                output = {k: v for k, v in payload.items() if k not in ('name', 'path', 'md_ready')}
                self.exported[key] = output
                if output.get('source_sha1'):
                    self.done[key] = self.found[key] = output['source_sha1']
                self.export_errors.pop(key, None)
                self._file_progress[key] = {'progress_pct': 100, 'progress_stage': '저장 완료'}
                for cache_key in list(self._comparison_cache):
                    if cache_key[0] == key:
                        self._comparison_cache.pop(cache_key, None)
                self.ui_save(exports=self.exported)
                saved_count += 1
                partial_count += int(output.get('status') == 'partial')
            elif kind == 'file_error' and payload.get('path'):
                key = _norm(payload['path'])
                self.export_errors[key] = payload.get('error') or '추출 실패'
                fails.append({'name': Path(payload['path']).name, 'why': self.export_errors[key]})
            elif kind == 'run':
                if not payload.get('on'):
                    if payload.get('stopped'):
                        self.cancel.set()
                    if payload.get('nfail'):
                        if key not in self.export_errors:
                            why = '; '.join(str(item.get('why') or '') for item in payload.get('fails', []))
                            event('file_error', {'path': str(source), 'error': why or '추출 실패'}, source)
                return  # A document child must never finish/reset the parent queue.
            elif kind == 'worker_started':
                self._worker_pids[key] = payload['pid']
                payload = dict(payload, path=str(source))
            self.push(kind, payload)

        def send(kind, payload, source):
            while not coordinator_failed.is_set():
                try:
                    events.put((kind, payload, source), timeout=.1)
                    return
                except event_queue.Full:
                    continue

        def run_document(source, force):
            try:
                extraction_process.run([source], output_dir, force=force, cancel=self.cancel.is_set,
                                       on_event=lambda kind, payload: send(kind, payload, source))
            except ExtractionCancelled:
                send('document_cancel', {}, source)
            except Exception as error:
                send('document_error', {'error': f'{type(error).__name__}: {error}'}, source)
            finally:
                send('document_exit', {}, source)

        snapshot = {}
        next_sample = next_start = 0
        waiting_reason = ''
        identities = {}
        try:
            with ThreadPoolExecutor(max_workers=policy.max_workers, thread_name_prefix='document-monitor') as pool:
                try:
                    while True:
                        # Only this coordinator changes result/progress state.
                        # Native work and event-file monitoring stay in owned processes/threads.
                        for _ in range(256):
                            try:
                                kind, payload, source = events.get_nowait()
                            except event_queue.Empty:
                                break
                            key = _norm(source)
                            if kind == 'document_exit':
                                job = running.pop(key)
                                policy.observe(source, job['peak'])
                                with self._operation_lock:
                                    self._active_paths.discard(key)
                                    self._worker_pids.pop(key, None)
                                    if not self.cancel.is_set():
                                        self._pending_paths.discard(key)
                                    self._active_path = next((item['source'] for item in running.values()), None)
                                completed_count += 1
                                self.push('queued', {'files': self.file_rows(), 'total': self._background_total})
                            elif kind == 'document_cancel':
                                self.cancel.set()
                            elif kind == 'document_error':
                                if key not in self.export_errors:
                                    event('file_error', dict(payload, path=str(source), name=source.name), source)
                                utils.log(f"[background] {source.name}: {payload['error']}")
                            else:
                                event(kind, payload, source)

                        now = time.monotonic()
                        if now >= next_sample:
                            snapshot = resource_budget.sample(dict(self._worker_pids))
                            for key, job in running.items():
                                observed = (snapshot.get('jobs', {}).get(key) or {}).get('peak_bytes', 0)
                                job['peak'] = max(job['peak'], observed)
                            with self._operation_lock:
                                queued_count = len(self._background_queue)
                            self._resource_state = dict(snapshot, running=len(running), queued=queued_count,
                                max_workers=policy.max_workers, total=self._background_total,
                                memory_budget_bytes=int(snapshot.get('total_bytes', 0) * .5),
                                waiting_reason=waiting_reason)
                            self.push('resources', self._resource_state)
                            next_sample = now + 1

                        with self._operation_lock:
                            candidates = list(self._background_queue)
                            if not running and (self.cancel.is_set() or not candidates):
                                self._accepting_work = False
                                break
                        if (not self.cancel.is_set() and len(running) < policy.max_workers and now >= next_start):
                            for source, force in candidates:
                                reservations = [max(job['estimate'], job['peak']) for job in running.values()]
                                allowed, reason, estimate = policy.can_start(source, reservations, snapshot)
                                if not allowed:
                                    waiting_reason = reason
                                    continue
                                try:
                                    stat = source.stat()
                                    identity_key = (_norm(source), stat.st_size, stat.st_mtime_ns)
                                    identity = identities.get(identity_key)
                                    if identity is None:
                                        identity = identities[identity_key] = store.file_sha1(source)
                                except OSError:
                                    # The document worker reports missing/unreadable originals in isolation.
                                    identity = _norm(source)
                                if any(job['identity'] == identity for job in running.values()):
                                    waiting_reason = '같은 내용 문서의 저장이 끝나기를 기다립니다.'
                                    continue
                                key = _norm(source)
                                with self._operation_lock:
                                    if self.cancel.is_set():
                                        break
                                    self._background_queue.remove((source, force))
                                    self._active_paths.add(key)
                                    self._active_path = source
                                    self.export_errors.pop(key, None)
                                running[key] = {'source': source, 'identity': identity, 'estimate': estimate, 'peak': 0}
                                event('prog', {'path': str(source), 'name': source.name, 'stage': '준비', 'file_pct': 0}, source)
                                pool.submit(run_document, source, force)
                                waiting_reason = ''
                                next_start = now + (1 if estimate >= 1024 ** 3 else .05)
                                break
                        time.sleep(.025)
                except BaseException:
                    coordinator_failed.set()
                    self.cancel.set()
                    raise
        except Exception as error:
            fails.append({'name': '(작업 관리)', 'why': f'{type(error).__name__}: {error}'})
            utils.log(f'[background coordinator] {type(error).__name__}: {error}')
        finally:
            with self._operation_lock:
                self._accepting_work = False
                if self.cancel.is_set():
                    for key in self._pending_paths:
                        progress = self._file_progress.setdefault(key, {})
                        if progress.get('progress_pct') != 100:
                            progress['status'] = 'paused'
                self._pending_paths.clear()
                self._background_queue.clear()
                self._active_path = None
                self._active_paths.clear()
                self._worker_pids.clear()
            self.scan_store()
            self._resource_state = dict(self._resource_state, running=0, queued=0, waiting_reason='')
            self.push('resources', self._resource_state)
            self.push('run', {'on': False, 'stopped': self.cancel.is_set(),
                              'done': saved_count, 'partial': partial_count,
                              'total': self._background_total, 'nfail': len(fails),
                              'fails': fails[:30], 'files': self.file_rows()})

    def _work(self, targets: list[Path], force: bool = False) -> None:
        from pubnexus import single, store, export, comparison
        from pubnexus.general_pdf import ExtractionCancelled
        total = len(targets)
        if not total:
            return
        self._pending_paths = {_norm(path) for path in targets}
        for path in targets:
            self._file_progress[_norm(path)] = {'progress_pct': 0, 'progress_stage': '대기'}
        self.push("run", {"on": True, "total": total})
        fails: list[tuple[str, str]] = []
        times: list[float] = []
        i = 0
        saved_count = 0
        partial_count = 0
        try:
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
                self._active_path = p
                i += 1
                t0 = time.time()
                self.prog(i, total, p.name, "준비", 0.0, times)

                def page_progress(page, pages, stage):
                    labels = {'native': '원문 확인', 'table': '표 추출', 'images': '이미지 보존',
                              'ocr': '문자 인식', 'docling': '표 구조 보완',
                              'docling_tables': '표 구조 보완', 'pdf_sdk': '문서 구조 추출',
                              'sdk_layout': '문서 구조 추출', 'sdk_complete': '문서 구조 추출 완료',
                              'sdk_assets': '원본 그림 보존',
                              'sdk_recovery': '누락된 쪽 재추출', 'pdf_validation': '본문 보존 확인', 'done': '쪽 처리'}
                    labels['sdk_ocr'] = '문자 인식'
                    low, high = ((95, 98) if stage == 'pdf_validation' else
                                 (5, 50) if stage in ('native', 'ocr') else
                                 (5, 94) if stage in ('pdf_sdk', 'sdk_layout', 'sdk_complete', 'sdk_ocr', 'sdk_recovery', 'sdk_assets') else
                                 (85, 95) if stage in ('docling', 'docling_tables') else (50, 85))
                    measured = low + (high - low) * max(0, min(page / max(pages, 1), 1))
                    self.prog(i, total, p.name, f'{labels.get(stage, stage)} · {page}/{pages}쪽', 0, times,
                              path=p, file_pct=measured, page=page, pages=pages)

                try:
                    # 정본 캐시와 휴대용 MD는 별개다. 캐시만 있는 입력도 저장한다.
                    sha = store.file_sha1(p)
                    source_stat = p.stat()
                    signature = (source_stat.st_size, source_stat.st_mtime_ns)
                    doc = None if force else store.load(sha)
                    if doc is not None:
                        if not doc.get("body_text") and doc.get("sections"):
                            doc = dict(doc, body_text=doc["sections"])
                        if p.suffix.lower() == '.pdf' and doc.get('source') in ('pdf_general', 'pdf_sdk'):
                            from pubnexus import pdf_pipeline, pdf_markdown
                            if not pdf_pipeline.current(doc) and doc.get('pdf_conversion_attempt') != pdf_markdown.engine_id():
                                doc = pdf_pipeline.extract(p, cancel=self.cancel.is_set, progress=page_progress)
                        doc["sha1"] = sha
                    elif p.suffix.lower() != '.pdf':
                        if p.suffix.lower() in EXTERNAL_DOCUMENT_EXTENSIONS:
                            from pubnexus.external_documents import extract_document
                        else:
                            from pubnexus.documents import extract_document
                        self.prog(i, total, p.name, '문서 구조 추출', .1, times)
                        doc = extract_document(p, cancel=self.cancel.is_set)
                    else:
                        from pubnexus import pdf_pipeline
                        doc = pdf_pipeline.extract(p, cancel=self.cancel.is_set, progress=page_progress)
                    extraction_status = (doc.get("extraction_report") or {}).get("status")
                    if extraction_status == "failed":
                        raise RuntimeError("원본 내용을 확인하지 못했습니다. 본문을 확인한 뒤 다시 추출하세요.")
                    if p.suffix.lower() == '.pdf':
                        from pubnexus.pdf_pipeline import upgrade_general
                        doc = upgrade_general(doc, p, cancel=self.cancel.is_set, progress=page_progress)
                    if p.suffix.lower() == '.pdf' and doc.get('source') != 'pdf_sdk':
                        from pubnexus.docling_tables import enhance_document
                        doc = enhance_document(doc, p, cancel=self.cancel.is_set, progress=page_progress)
                        if (doc.get('extraction_report') or {}).get('status') == 'failed':
                            raise RuntimeError('표 보완 중 원본 확인에 실패했습니다. 결과를 저장하지 않았습니다.')
                    current_stat = p.stat()
                    if signature != (current_stat.st_size, current_stat.st_mtime_ns):
                        raise RuntimeError("처리 중 원본이 변경됐습니다. 변경된 문서를 다시 추출하세요.")
                    if self.cancel.is_set():
                        break
                    doc = comparison.attach(doc, p)
                    store.save(sha, doc, p)
                    for cached_key in list(self._comparison_cache):
                        if cached_key[0] == _norm(p):
                            self._comparison_cache.pop(cached_key, None)
                    self.prog(i, total, p.name, "MD와 이미지 저장", .98, times)
                    result = export.export_document(doc, p, output_dir=self.output_dir,
                                                    asset_base=(doc.get('extraction_report') or {}).get('asset_base'))
                    after_export = p.stat()
                    if signature != (after_export.st_size, after_export.st_mtime_ns):
                        raise RuntimeError('저장 중 원본이 변경됐습니다. 저장된 이전 버전 MD를 보존했으며 다시 추출해야 합니다.')
                    sha = str(doc.get("sha1") or "")
                    if sha:
                        self.found[_norm(p)] = sha
                        self.done[_norm(p)] = sha
                    partial = (doc.get("extraction_report") or {}).get("status") == "partial"
                    units = (doc.get('source_map') or {}).get('units') or []
                    unit_kind = ('slide' if p.suffix.lower() in {'.pptx', '.odp'} else
                                 'sheet' if p.suffix.lower() in {'.xlsx', '.ods'} else None)
                    output = {"markdown": str(result.markdown_path),
                              "revision": result.revision,
                              "markdown_sha256": hashlib.sha256(result.markdown_path.read_bytes()).hexdigest(),
                              "assets": str(result.assets_dir),
                              "source_stat": signature, "source_sha1": sha,
                              "pages": len((doc.get("source_map") or {}).get("pages") or []) or None,
                              "unit_count": sum(u.get('kind') == unit_kind for u in units) if unit_kind else None,
                              "unit_label": {'slide': '슬라이드', 'sheet': '시트'}.get(unit_kind, ''),
                              "warnings": (doc.get("extraction_report") or {}).get("warnings") or [],
                              'warning_details': (doc.get('extraction_report') or {}).get('warnings') or [],
                              'warning_context': [{k: page.get(k) for k in ('page', 'status', 'ocr_performed')}
                                                  for page in (doc.get('extraction_report') or {}).get('pages') or []
                                                  if page.get('ocr_performed') or page.get('status') not in ('text_extracted',)],
                              'pdf_conversion_engine': doc.get('pdf_conversion_engine') or doc.get('sdk_engine'),
                              'pdf_conversion_attempt': doc.get('pdf_conversion_attempt'),
                              'source_type': doc.get('source'),
                              "status": "partial" if partial else "complete"}
                    output['warnings'] = self._display_warnings(output)
                    self.exported[_norm(p)] = output
                    self.ui_save(exports=self.exported)
                    self.export_errors.pop(_norm(p), None)
                    saved_count += 1
                    partial_count += int(partial)
                    self._pending_paths.discard(_norm(p))
                    self._active_path = None
                    self._file_progress[_norm(p)] = {'progress_pct': 100, 'progress_stage': '저장 완료'}
                    self.push("mark", {"name": p.name, "path": str(p), 'md_ready': True, **output})
                except ExtractionCancelled:
                    self.cancel.set()
                    break
                except Exception as e:  # noqa: BLE001 — 파일별 격리
                    self.export_errors[_norm(p)] = f"{type(e).__name__}: {e}"
                    self.push('file_error', {'name': p.name, 'path': str(p), 'error': self.export_errors[_norm(p)]})
                    fails.append((p.name, f"{type(e).__name__}: {e}"))
                    utils.log(f"[app] 실패 {p.name}: {type(e).__name__}: {e}")
                finally:
                    self._pending_paths.discard(_norm(p))
                    if self._active_path == p:
                        self._active_path = None
                    if self.cancel.is_set() and _norm(p) not in self.export_errors:
                        self._file_progress.setdefault(_norm(p), {})['status'] = 'paused'
                    q.finish(p)
                times.append(time.time() - t0)
        except Exception as e:  # noqa: BLE001
            fails.append(("(전체)", f"{type(e).__name__}: {e}"))
            utils.log(traceback.format_exc())
        finally:
            self.queue = None
            stopped = self.cancel.is_set()
            self._active_path = None
            if stopped:
                for key in self._pending_paths:
                    self._file_progress.setdefault(key, {})['status'] = 'paused'
            self._pending_paths.clear()
            self.scan_store()
            self.push("run", {
                "on": False, "files": self.file_rows(), "stopped": stopped,
                "done": saved_count, "partial": partial_count,
                "total": total, "nfail": len(fails),
                "fails": [{"name": n, "why": w} for n, w in fails[:30]]})

    def prog(self, i: int, total: int, name: str, stage: str, sub: float,
             times: list[float] | None = None, *, path: Path | None = None,
             file_pct: float | None = None, page: int | None = None, pages: int | None = None) -> None:
        """진행 표시 한 번. 남은 시간은 **믿을 만할 때만** 보낸다.

        틀린 예측은 없느니만 못하다 — 다섯 편 넘게 처리해 실측이 쌓인 뒤에,
        그것도 중앙값으로만 낸다(어쩌다 오래 걸린 한 편에 끌려가지 않게).
        """
        now = time.time()
        path = path or self._active_path
        previous = self._file_progress.get(_norm(path), {}) if path else {}
        measured = max(previous.get('progress_pct', 0), min(99, max(0, file_pct if file_pct is not None else sub * 95)))
        progress = {'progress_pct': measured, 'progress_stage': stage,
                    'progress_page': page, 'progress_pages': pages or previous.get('progress_pages')}
        if path:
            self._file_progress[_norm(path)] = progress
        if now - self._last_push < 0.12:
            return
        self._last_push = now
        eta = None
        if times and len(times) >= 5:
            s = sorted(times[-12:])
            med = s[len(s) // 2]
            eta = int(med * (total - i + 1))
        self.push("prog", {"i": i, "total": total, "name": name, "stage": stage,
                           'path': str(path) if path else '', 'file_pct': measured,
                           'page': page, 'pages': progress['progress_pages'],
                           "pct": 100.0 * (i - 1 + measured / 100) / max(total, 1),
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
        fp = fig_path(sha1, rel)
        ctype = self._IMG.get(fp.suffix.lower()) if fp else None
        if not ctype:
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
                            "files": self.app.file_rows(), "output_dir": str(self.app.output_dir)})
            elif u.path == "/open":
                index = int(q.get('p', ['-1'])[0])
                mode = q.get('mode', [''])[0]
                self._json(self.app.source_view(index) if mode == 'source' else
                           self.app.result_view(index) if mode == 'result' else self.app.paper(index))
            elif u.path == "/document-map":
                self._json(self.app.document_map(int(q.get('p', ['-1'])[0])))
            elif u.path == "/page":
                data = self.app.renderer.png(int(q["k"][0]), int(q["i"][0]),
                                             int(q["w"][0]))
                if data is None:
                    self.send_error(404)
                else:
                    self._send(data, "image/png", cache="max-age=600")
            elif u.path == "/fig":
                self._fig(q.get("s", [""])[0], q.get("f", [""])[0])
            elif u.path == '/asset':
                index = int(q.get('p', ['-1'])[0])
                if not 0 <= index < len(self.app.pdfs):
                    self.send_error(404)
                    return
                output = self.app.exported.get(_norm(self.app.pdfs[index])) or {}
                markdown = output.get('markdown')
                if not markdown or not Path(markdown).is_file():
                    self.send_error(404)
                    return
                base = Path(markdown).parent.resolve()
                assets_root = (base / 'assets').resolve()
                asset = (base / q.get('f', [''])[0]).resolve()
                # Only image files beneath the chosen result's assets folder.
                if not assets_root.is_relative_to(base) or not asset.is_relative_to(assets_root) or asset.suffix.lower() not in self._IMG:
                    self.send_error(404)
                    return
                self._send(asset.read_bytes(), self._IMG[asset.suffix.lower()])
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

    def settings(self) -> dict:
        return self._app.settings_state()

    def choose_output_folder(self) -> dict:
        import webview
        a = self._app
        if a.extraction_busy() or a._closing:
            return {'ok': False, 'why': '추출을 마치거나 중지한 뒤 저장 폴더를 바꾸세요.'}
        try:
            result = a.window.create_file_dialog(webview.FOLDER_DIALOG, directory=str(a.output_dir))
            if not result:
                return {'ok': False, 'cancelled': True}
            return a.change_output_dir(Path(result[0]))
        except (OSError, ValueError, RuntimeError) as error:
            return {'ok': False, 'why': str(error)}

    def reset_output_folder(self) -> dict:
        try:
            return self._app.change_output_dir(None)
        except (OSError, ValueError, RuntimeError) as error:
            return {'ok': False, 'why': str(error)}

    def set_auto_update(self, enabled: bool) -> dict:
        try:
            self._app.preferences.set_auto_update(enabled)
            state = self._app.settings_state()
            self._app.push('settings', state)
            if enabled:
                self._app.start_auto_update(hidden=self._app._hidden)
            return state
        except (OSError, ValueError) as error:
            return {'ok': False, 'why': str(error)}

    def set_theme(self, theme: str) -> dict:
        try:
            self._app.preferences.set_theme(theme)
            state = self._app.settings_state()
            self._app.push('settings', state)
            if self._app.window is not None and not getattr(self._app, '_hidden', False):
                paint_caption(timeout=.2, theme=theme)
            return state
        except (OSError, ValueError) as error:
            return {'ok': False, 'why': str(error)}

    def check_update(self) -> dict:
        return self._app.check_update_async()

    def install_update(self) -> dict:
        return self._app.request_install_update()

    def pick_folder(self) -> dict:
        import webview
        w = self._app.window
        res = w.create_file_dialog(webview.FOLDER_DIALOG,
                                   directory=str(self._app.folder or ""))
        if not res:
            return {"ok": False}
        folder = Path(res[0])
        self._app.add_files(self._app.folder_files(folder))
        self._app.ui_save(folder=str(folder))
        return {"ok": True, "path": str(self._app.folder)}

    def pick_files(self) -> dict:
        import webview
        a = self._app
        res = a.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                          directory=str(a.folder or ""),
                                          file_types=("Documents (" + ';'.join('*' + ext for ext in sorted(DOCUMENT_EXTENSIONS)) + ")",))
        if not res:
            return {"ok": False}
        a.add_files([Path(p) for p in res])
        return {"ok": True, "n": len(a.pdfs)}

    # ── 한국어 번역 ─────────────────────────────────────────────────
    def doc_lang(self, index: int, lang: str = "en") -> dict:
        """같은 논문을 그 언어로 다시 그린다. 번역 캐시가 없으면 원문 그대로."""
        a = self._app
        if not (0 <= index < len(a.pdfs)):
            return {"ok": False}
        return {"ok": True, "doc": a.doc_view(a.pdfs[index], lang=lang)}

    def translate(self, index: int) -> dict:
        """이 논문을 한국어로 옮기기 시작한다(워커). 진행은 push 로 알린다."""
        a = self._app
        if not (0 <= index < len(a.pdfs)):
            return {"ok": False, "why": "논문을 먼저 고르세요"}
        if a.tr_worker and a.tr_worker.is_alive():
            return {"ok": False, "why": "이미 번역 중입니다"}
        pdf = a.pdfs[index]
        sha = a.sha_for(pdf, deep=True)
        if not sha:
            return {"ok": False, "why": "먼저 추출해야 합니다"}
        from pubnexus import store, translate as tr
        d = store.load(sha)
        if d is None:
            return {"ok": False, "why": "먼저 추출해야 합니다"}
        cfg = (a.cfg.get("translate") or {})
        url = cfg.get("url") or tr.DEFAULT_URL
        model = cfg.get("model") or tr.DEFAULT_MODEL
        if not tr.is_alive(url):
            return {"ok": False, "why": "번역기가 꺼져 있습니다 — Ollama 를 켜 주세요"}
        if model not in tr.models(url):
            return {"ok": False,
                    "why": f"모델이 없습니다: ollama pull {model}"}

        a.tr_stop.clear()

        def run() -> None:
            def prog(done: int, total: int) -> None:
                a.push("trans", {"state": "run", "done": done, "total": total})
            try:
                tr.translate_doc(d, sha, url=url, model=model, on_progress=prog,
                                 should_stop=a.tr_stop.is_set)
            except Exception as e:               # noqa: BLE001 — 번역 실패가 앱을 멈추게 하지 않는다
                utils.log(f"[app] 번역 실패: {type(e).__name__}: {e}")
            a.push("trans", {"state": "done"})

        a.tr_worker = threading.Thread(target=run, daemon=True)
        a.tr_worker.start()
        return {"ok": True, "model": model}

    def translate_stop(self) -> dict:
        self._app.tr_stop.set()
        return {"ok": True}

    def save_figure(self, sha1: str, name: str) -> dict:
        """그림 한 장을 원장이 고른 자리에 저장한다(우클릭 → 다른 이름으로 저장).

        화면의 <img> 는 저장소 안을 가리키는 내부 주소라 브라우저 기본 저장이
        먹지 않는다. 파일을 그대로 복사해 준다.
        """
        import shutil
        import webview
        src = fig_path(sha1, name)
        if src is None:
            return {"ok": False, "why": "그림 파일을 찾지 못했습니다"}
        w = self._app.window
        res = w.create_file_dialog(webview.SAVE_DIALOG,
                                   save_filename=Path(name).name)
        if not res:
            return {"ok": False}                     # 취소는 실패가 아니다
        dst = Path(res[0] if isinstance(res, (list, tuple)) else res)
        if not dst.suffix:
            dst = dst.with_suffix(src.suffix)        # 확장자를 지우고 저장하는 사람이 있다
        try:
            shutil.copyfile(utils.long_path(src), utils.long_path(dst))
        except OSError as e:
            return {"ok": False, "why": f"저장하지 못했습니다: {e.strerror or e}"}
        return {"ok": True, "path": str(dst)}

    def extract(self, which: str, index: int = -1) -> dict:
        a = self._app
        if which == "one":
            if not (0 <= index < len(a.pdfs)):
                return {"ok": False, "why": "왼쪽에서 논문을 먼저 고르세요"}
            targets = [a.pdfs[index]]
        else:
            # 여기서만 내용 지문까지 확인한다. 옮겨오거나 이름만 바꾼 PDF 를
            # 다시 뽑느라 몇 분을 버리느니, 몇 초 더 읽는 편이 낫다.
            a.status("이미 처리된 것이 있는지 확인하는 중…", busy=True, force=True)
            targets = [p for p, row in zip(a.pdfs, a.file_rows()) if not row["done"]]
            a.status("", busy=False, force=True)
        if not targets:
            return {"ok": False, "why": "처리할 문서가 없습니다. 다시 추출할 문서를 선택하세요."}
        queued = a.extraction_busy()
        try:
            accepted = a.start(targets, force=which == "one")
        except RuntimeError as error:
            return {'ok': False, 'why': str(error)}
        return {"ok": True, "n": len(accepted), 'queued': queued,
                'accepted_paths': [str(path) for path in accepted]}

    def count_todo(self) -> dict:
        a = self._app
        todo = [p for p in a.pdfs if a.sha_for(p) is None]
        return {"todo": len(todo), "total": len(a.pdfs)}

    def extract_selected(self, indexes: list[int], force: bool = False) -> dict:
        a = self._app
        if not isinstance(indexes, list) or not isinstance(force, bool):
            return {"ok": False, "why": "문서 선택을 확인하세요."}
        rows = a.file_rows()
        picked = list(dict.fromkeys(i for i in indexes if type(i) is int and 0 <= i < len(a.pdfs)))
        targets = [a.pdfs[i] for i in picked if force or not rows[i]["done"]]
        if not targets:
            return {"ok": False, "why": "추출할 문서를 선택하세요."}
        queued = a.extraction_busy()
        try:
            accepted = a.start(targets, force=force)
        except RuntimeError as error:
            return {'ok': False, 'why': str(error)}
        return {"ok": True, "n": len(accepted), 'queued': queued,
                'accepted_paths': [str(path) for path in accepted]}

    def open_file(self, index: int, kind: str = "source") -> dict:
        a = self._app
        if type(index) is not int or not 0 <= index < len(a.pdfs):
            return {"ok": False, "why": "문서를 선택하세요."}
        source = a.pdfs[index]
        if kind == "source":
            target = source
        elif kind == "markdown":
            saved = (a.exported.get(_norm(source)) or {}).get("markdown")
            target = Path(saved) if saved else None
        else:
            return {"ok": False, "why": "지원하지 않는 열기 동작입니다."}
        if target is None or not target.is_file():
            return {"ok": False, "why": "저장된 파일이 없습니다. 먼저 추출·저장하세요."}
        try:
            os.startfile(str(target))
        except OSError as error:
            return {"ok": False, "why": f"파일을 열지 못했습니다: {error}"}
        return {"ok": True, "path": str(target)}

    def cancel(self) -> dict:
        self._app.cancel.set()
        return {"ok": True}

    def search(self, index: int, query: str, expected_revision: str | None = None) -> dict:
        from pubnexus import comparison
        try:
            snapshot = self._app._saved_snapshot(self._app.pdfs[index])
            if expected_revision is not None and (not snapshot or snapshot['revision'] != expected_revision):
                return {'ok': False, 'code': 'stale_revision', 'why': '새 Markdown이 저장되었습니다. MD를 다시 열어 검색하세요.', 'results': []}
            doc, source = self._app.comparison_doc(index)
            mapping = self._app.document_map(index)
            if expected_revision is not None and mapping.get('revision') != expected_revision:
                return {'ok': False, 'code': 'stale_revision', 'why': '새 Markdown이 저장되었습니다. MD를 다시 열어 검색하세요.', 'results': []}
            return comparison.search(doc, source, query, mapping=mapping)
        except (ValueError, OSError, IndexError) as error:
            return {'ok': False, 'why': str(error), 'results': []}

    def search_source(self, index: int, query: str) -> dict:
        """Search the preview itself even when no Markdown result exists."""
        from pubnexus import comparison
        from pubnexus.preview import preview_path
        import fitz
        try:
            if type(index) is not int or not 0 <= index < len(self._app.pdfs):
                raise ValueError('문서를 선택하세요.')
            source = preview_path(self._app.pdfs[index]).path
            with fitz.open(utils.long_path(source)) as pdf:
                pages = [{'width': p.rect.width, 'height': p.rect.height} for p in pdf]
            return comparison.search({}, source, query, mapping={'blocks': [], 'pages': pages})
        except (ValueError, OSError, RuntimeError) as error:
            return {'ok': False, 'why': str(error), 'results': []}

    def location(self, index: int, block_id: str, expected_revision: str | None = None) -> dict:
        data = self._app.document_map(index)
        if expected_revision is not None and data.get('revision') != expected_revision:
            return {'ok': False, 'code': 'stale_revision', 'why': '새 Markdown이 저장되었습니다. MD를 다시 열어 위치를 확인하세요.'}
        block = next((b for b in data['blocks'] if b['id'] == block_id), None)
        if not block or not block['locations']:
            return {'ok': False, 'why': data.get('error') or '이 부분의 원본 위치를 연결하지 못했습니다.'}
        return {'ok': True, 'locations': block['locations'], 'mapping': block['mapping'], **block['locations'][0]}

    def state(self) -> dict:
        from pubnexus import pdf_markdown
        a = self._app
        ui = a.ui_load()
        last = ui.get("paper") if _norm(ui.get("folder") or "") == _norm(a.folder or "") else None
        return {"path": str(a.folder or ""), "grobid": a.grobid.state,
                "sec": a.grobid.secs, "files": a.file_rows(), "last": last,
                "output_dir": str(a.output_dir), 'pdf_engine_available': pdf_markdown.available(),
                'resources': dict(a._resource_state), 'settings': a.settings_state()}

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

    def reveal_output(self) -> dict:
        try:
            self._app.output_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self._app.output_dir))
        except OSError as error:
            return {'ok': False, 'why': str(error)}
        return {'ok': True, 'path': str(self._app.output_dir)}


# ══════════════════════════════════════════════════════════════════════
#  화면 — CSS·JS 전부 인라인(바깥 자원 0)
# ══════════════════════════════════════════════════════════════════════
from pubnexus.ui import INDEX_HTML


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


def paint_caption(timeout: float = 8.0, *, theme: str = 'light') -> bool:
    r"""제목표시줄 색을 선택한 화면 테마에 맞춘다(윈도우 11).

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

        dark = theme == 'dark'
        put(20, int(dark))       # DWMWA_USE_IMMERSIVE_DARK_MODE
        put(35, 0x002B2B2B if dark else 0x00FFFFFF)  # DWMWA_CAPTION_COLOR
        put(36, 0x00EEEEEE if dark else 0x0028231F)  # DWMWA_TEXT_COLOR
        put(34, 0x00505050 if dark else 0x00EEE9E7)  # DWMWA_BORDER_COLOR
        return True
    except Exception:  # noqa: BLE001 — 구버전 윈도우: 그냥 기본 창틀로 둔다
        return False


def build(folder: str | Path | None = None, *, hidden: bool = False):
    """창을 만들어 돌려준다(테스트에서 직접 몰아보기 위해 분리)."""
    import webview

    app = App()
    app._hidden = hidden
    port = serve(app)
    api = Api(app)
    # 창을 **최대화해서** 띄운다. 세 칸을 나란히 보는 화면이라 넓을수록 좋고,
    # 무엇보다 고해상도(175% 배율) 화면에서 요청한 크기가 배율만큼 부풀어
    # 창 오른쪽이 화면 밖으로 나가는 일을 원천적으로 막는다.
    w, h = _screen()
    win = webview.create_window(
        "문서 추출기", url=f"http://127.0.0.1:{port}/", js_api=api,
        width=min(1360, max(1120, w - 120)), height=min(860, max(660, h - 120)),
        min_size=(1120, 660), maximized=not hidden, hidden=hidden,
        background_color="#ffffff", text_select=True)
    app.window = win
    win.events.closing += app.request_close

    def bind_drop() -> None:
        from webview.dom import DOMEventHandler
        def dropped(event):
            result = app.add_dropped_files(event)
            app.push('notice', {'text': (f"{result['n']}개 문서를 추가했습니다." if result.get('ok') else
                                         result.get('why', '파일을 추가하지 못했습니다.'))})
        try:
            body = win.dom.get_element('body')
            body.on('dragover', DOMEventHandler(lambda event: None, prevent_default=True))
            body.on('drop', DOMEventHandler(dropped, prevent_default=True))
        except Exception:
            utils.log('파일 끌어놓기를 연결하지 못했습니다. 파일/폴더 추가는 계속 사용할 수 있습니다.')
    win.events.loaded += bind_drop

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
        paint_caption(theme=app.preferences.theme)
        try:
            if start and start.is_dir():
                app.set_folder(start)
        except Exception:  # noqa: BLE001
            utils.log(traceback.format_exc())
        app.start_auto_update(hidden=hidden)

    return app, win, boot


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == '--extract-worker':
        from pubnexus.extraction_process import worker_cli
        raise SystemExit(worker_cli(sys.argv[2], App))
    if len(sys.argv) == 3 and sys.argv[1] == '--ocr-worker':
        from pubnexus.local_ocr import worker_cli
        raise SystemExit(worker_cli(sys.argv[2]))
    import webview
    test_port = os.environ.get('PNX_TEST_DEBUG_PORT')
    if '--hidden' in sys.argv[1:] and test_port:
        port = int(test_port)
        if not 1 <= port <= 65535:
            raise ValueError('검증용 포트 범위는 1..65535입니다.')
        webview.settings['REMOTE_DEBUGGING_PORT'] = port
    app, win, boot = build(hidden='--hidden' in sys.argv[1:])
    try:
        webview.start(lambda: threading.Thread(target=boot, daemon=True).start(),
                      debug=bool(os.environ.get("PNX_DEBUG")), private_mode=True)
    finally:
        app.renderer.close_all()
        app.apply_update_on_exit()


if __name__ == "__main__":
    main()
