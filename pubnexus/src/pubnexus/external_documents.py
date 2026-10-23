"""별도 설치한 로컬 엔진을 제한된 child process에서 사용한다."""
from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import documents as native, store, utils
from .documents import DocumentExtractionError

_MARKITDOWN_CHILD = r'''
import importlib.metadata, io, json, socket, sys
from pathlib import Path
def deny(*args, **kwargs):
    raise RuntimeError("Network disabled for local conversion")
socket.create_connection = deny
socket.socket.connect = deny
import requests
requests.sessions.Session.request = deny
from markitdown import MarkItDown, StreamInfo
from markitdown.converters import XlsConverter, OutlookMsgConverter
source, output, cell_limit, text_limit = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
engine = MarkItDown(enable_builtins=False, enable_plugins=False, llm_client=None)
if source.suffix == ".xls":
    import xlrd
    book = xlrd.open_workbook(str(source), on_demand=True, logfile=io.StringIO())
    try:
        cells = 0
        for index in range(book.nsheets):
            try:
                sheet = book.sheet_by_index(index)
                cells += sheet.nrows * max(1, sheet.ncols)
                if cells > cell_limit:
                    raise SystemExit(65)
            finally:
                book.unload_sheet(index)
    finally:
        book.release_resources()
    engine.register_converter(XlsConverter())
elif source.suffix == ".msg":
    import olefile
    with olefile.OleFileIO(str(source)) as message:
        streams = message.listdir()
        if len(streams) > 20000:
            raise SystemExit(65)
        names = {"/".join(parts) for parts in streams}
        if "EncryptedPackage" in names:
            raise ValueError("Encrypted container is not supported")
        if not any("__substg1.0_" + tag + kind in names for tag in ("0037", "1000") for kind in ("001F", "001E")):
            raise ValueError("No supported MSG subject/plain body streams")
        if sum(message.get_size(parts) for parts in streams) > text_limit:
            raise SystemExit(65)
    engine.register_converter(OutlookMsgConverter())
else:
    raise ValueError("Unsupported converter")
result = engine.convert_local(str(source), stream_info=StreamInfo(extension=source.suffix))
record = {"version": importlib.metadata.version("markitdown"), "markdown": result.markdown,
          "title": result.title or ""}
encoded = json.dumps(record, ensure_ascii=False).encode("utf-8")
if len(encoded) > text_limit:
    raise SystemExit(65)
output.write_bytes(encoded)
'''


def find_engine(name: str) -> Path | None:
    if name not in {"pandoc", "markitdown_python"}:
        raise ValueError("Unknown external document engine")
    override = os.environ.get("PUBNEXUS_" + name.upper())
    if override:
        path = Path(override)
        return path if path.is_file() else None
    if name == "pandoc":
        found = shutil.which("pandoc")
        if found:
            return Path(found)
        if os.environ.get("LOCALAPPDATA"):
            installed = Path(os.environ["LOCALAPPDATA"]) / "Pandoc" / "pandoc.exe"
            if installed.is_file():
                return installed
    roots = [Path(sys.executable).parent / "engines",
             Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "engines"]
    settings = Path.home() / ".ai-workspace" / "paths.md"
    if settings.is_file():
        match = re.search(r"^cache_root:\s*(.+)$", settings.read_text(encoding="utf-8-sig"), re.M)
        if match:
            roots.append(Path(match.group(1).strip()) / "260725 PDF 추출" / "engines")
    patterns = (["pandoc*/pandoc.exe", "pandoc/pandoc.exe"] if name == "pandoc" else
                ["docling-runtime/Scripts/python.exe", "markitdown-runtime/Scripts/python.exe"])
    for root in roots:
        for pattern in patterns:
            for candidate in sorted(root.glob(pattern), reverse=True):
                if candidate.is_file():
                    return candidate
    return None


def _stop_owned_process(process) -> None:
    """Stop only this spawn PID and its descendants, including Windows venv launchers."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        executable = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(executable), "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run(builder, args: list[str], work: Path, output: Path | None = None) -> str:
    builder.check()
    stdout, stderr = work / "stdout.log", work / "stderr.log"
    environment = os.environ.copy()
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", HF_HUB_OFFLINE="1",
                       TRANSFORMERS_OFFLINE="1", HF_HOME=str(work / "hf-cache"))
    with open(stdout, "wb") as out, open(stderr, "wb") as err:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   cwd=str(work), env=environment, shell=False,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while True:
                builder.check()
                if stdout.stat().st_size > 65536 or stderr.stat().st_size > 65536:
                    raise DocumentExtractionError("외부 엔진 진단 출력 예산을 초과했습니다.", "limit")
                if output is not None and output.exists() and output.stat().st_size > native.MAX_TEXT_BYTES:
                    raise DocumentExtractionError("외부 엔진 변환 출력 예산을 초과했습니다.", "limit")
                if process.poll() is not None:
                    break
                time.sleep(0.05)
        finally:
            _stop_owned_process(process)
    diagnostic = stderr.read_bytes().decode("utf-8", "replace").strip()
    if process.returncode:
        encrypted = re.search(r"encrypt|password|암호|DRM", diagnostic, re.I)
        dependency = re.search(r"ModuleNotFoundError|MissingDependencyException|ImportError", diagnostic)
        raise DocumentExtractionError("로컬 외부 엔진 변환 실패: " + (diagnostic[:2000] or str(process.returncode)),
                                      "limit" if process.returncode == 65 else
                                      "encrypted" if encrypted else "dependency" if dependency else "unsupported")
    if diagnostic:
        builder.warn("외부 엔진 진단: " + diagnostic[:2000])
    return stdout.read_bytes().decode("utf-8", "replace").strip()


def _pandoc(builder, source: Path, work: Path):
    engine = find_engine("pandoc")
    if engine is None:
        raise DocumentExtractionError("Pandoc이 없습니다. PUBNEXUS_PANDOC 실행 파일 경로를 설정해 주세요.", "dependency")
    version = _run(builder, [str(engine), "--version"], work).splitlines()[0]
    output = work / "document.html"
    _run(builder, [str(engine), "--sandbox", "--embed-resources", "--standalone",
                   "--from=" + builder.kind, "--to=html5", "--wrap=none",
                   "--output", str(output), "--", str(source)], work, output)
    if not output.is_file() or output.stat().st_size > native.MAX_TEXT_BYTES:
        raise DocumentExtractionError("Pandoc 변환 결과가 없거나 출력 예산을 초과했습니다.", "limit")
    native._html_content(builder, output.read_text(encoding="utf-8"), "", lambda src: None,
                         unit_kind="converted_document", use_title=False)
    builder.doc["source_map"].update(engine="Pandoc", engine_version=version,
                                     logical_origin="converted_html_dom", page_accuracy="none")
    if builder.kind == "ipynb":
        builder.doc["extraction_report"]["notebook_executed"] = False
        builder.warn("노트북 코드는 실행하지 않고 파일에 저장된 본문/출력만 읽었습니다.")
    builder.warn("외부 엔진의 중간 HTML 논리 주소입니다. 원본 쪽수/블록과의 정확한 위치 대응은 제공하지 않습니다.")


def _markitdown(builder, source: Path, work: Path):
    from markdown_it import MarkdownIt
    engine = find_engine("markitdown_python")
    if engine is None:
        raise DocumentExtractionError("별도 MarkItDown Python이 없습니다. PUBNEXUS_MARKITDOWN_PYTHON 경로를 설정해 주세요.", "dependency")
    output = work / "document.json"
    _run(builder, [str(engine), "-I", "-B", "-c", _MARKITDOWN_CHILD, str(source), str(output),
                   str(native.MAX_CELLS), str(native.MAX_TEXT_BYTES)], work, output)
    if not output.is_file() or output.stat().st_size > native.MAX_TEXT_BYTES:
        raise DocumentExtractionError("MarkItDown 결과가 없거나 출력 예산을 초과했습니다.", "limit")
    record = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or not isinstance(record.get("markdown"), str) or not record.get("version"):
        raise DocumentExtractionError("MarkItDown 결과 계약이 올바르지 않습니다.")
    if record.get("title"):
        builder.doc["meta"]["title"] = str(record["title"])
    builder.doc["native_markdown"] = record["markdown"]
    rendered = MarkdownIt("commonmark", {"html": True}).enable("table").render(record["markdown"])
    native._html_content(builder, rendered, "", lambda src: None,
                         unit_kind="converted_document", use_title=False)
    builder.doc["source_map"].update(engine="MarkItDown", engine_version=str(record["version"]),
                                     logical_origin="converted_markdown_blocks", page_accuracy="none")
    if builder.kind == "xls":
        builder.warn("구형 Excel은 시트 값/평면 표만 읽었습니다. 병합 구조·수식 원문·그림·주석은 이 엔진의 추출 범위 밖입니다.")
    else:
        builder.warn("MSG는 지원하는 메일 머리글/일반 텍스트 본문만 읽었습니다. HTML/RTF 대체 본문·첨부·내장 그림은 추출하지 않았습니다.")


def extract_document(path: str | Path, *, asset_dir: str | Path | None = None,
                     timeout: float = 120, cancel=None) -> dict:
    """외부 엔진 지원 형식만 변환하며 원본은 읽기만 한다."""
    if timeout <= 0:
        raise ValueError("timeout은 양수여야 합니다.")
    try:
        source = Path(path).resolve(strict=True)
        if source.suffix.lower() not in {".rtf", ".ipynb", ".xls", ".msg"}:
            raise DocumentExtractionError("아직 검증하지 않은 외부 엔진 형식입니다: " + source.suffix, "unsupported")
        if not source.is_file() or source.stat().st_size > native.MAX_TEXT_BYTES:
            raise DocumentExtractionError("외부 변환 입력 크기 제한을 초과했습니다.", "limit")
        builder = native._Builder(source, asset_dir, timeout, cancel)
        with tempfile.TemporaryDirectory(prefix="pubnexus-external-") as temporary:
            work = Path(temporary)
            private = work / ("source" + source.suffix.lower())
            with open(utils.long_path(source), "rb") as stream:
                data = stream.read(native.MAX_TEXT_BYTES + 1)
            if len(data) > native.MAX_TEXT_BYTES:
                raise DocumentExtractionError("외부 변환 입력 크기 제한을 초과했습니다.", "limit")
            private.write_bytes(data)
            if source.suffix.lower() in {".xls", ".msg"}:
                _markitdown(builder, private, work)
            else:
                _pandoc(builder, private, work)
        if store.file_sha1(source) != builder.sha1:
            raise DocumentExtractionError("추출 도중 원본이 변경되어 결과를 확정하지 않았습니다.")
        builder.doc["extraction_report"]["external_engine"] = {
            "name": builder.doc["source_map"]["engine"], "version": builder.doc["source_map"]["engine_version"],
            "network": False, "llm": False, "source_copy": True,
        }
        return native._finish(builder)
    except DocumentExtractionError:
        raise
    except OSError as exc:
        raise DocumentExtractionError("외부 변환 파일 읽기/쓰기 실패: " + str(exc), "io") from exc
    except (ValueError, IndexError) as exc:
        raise DocumentExtractionError("외부 변환 결과를 읽을 수 없습니다: " + str(exc)) from exc
