"""Read-only source display through isolated local renderers, separate from MD."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import fitz

from . import store, utils


class PreviewUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Preview:
    path: Path
    converted: bool
    engine: str
    source_sha1: str


_locks_guard = threading.Lock()
_locks = {}


def find_engine(name: str) -> Path | None:
    if name not in {'rhwp', 'soffice'}:
        raise ValueError('Unknown preview engine')
    override = os.environ.get('PUBNEXUS_' + name.upper())
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    executable = shutil.which(name)
    if executable:
        return Path(executable)
    suffix = '.exe' if sys.platform == 'win32' else ''
    roots = [Path(sys.executable).parent / 'engines',
             Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'engines']
    settings = Path.home() / '.ai-workspace' / 'paths.md'
    if settings.is_file():
        text = settings.read_text(encoding='utf-8-sig')
        match = re.search(r'^cache_root:\s*(.+)$', text, re.M)
        if match:
            roots.append(Path(match.group(1).strip()) / '260725 PDF 추출' / 'engines')
    if sys.platform == 'win32' and name == 'soffice':
        for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)'):
            if os.environ.get(key):
                installed = Path(os.environ[key]) / 'LibreOffice' / 'program' / 'soffice.exe'
                if installed.is_file():
                    return installed
    patterns = [name + suffix]
    patterns += ['rhwp*/rhwp/' + name + suffix, 'rhwp*/' + name + suffix] if name == 'rhwp' else [
        'libreoffice*/image/program/' + name + suffix, 'libreoffice*/program/' + name + suffix]
    for root in roots:
        for pattern in patterns:
            for path in sorted(root.glob(pattern), reverse=True):
                if path.is_file():
                    return path
    return None


def renderer_identity(source: str | Path) -> str:
    if Path(source).suffix.lower() == '.pdf':
        return 'PyMuPDF:' + fitz.VersionBind
    engine = find_engine('rhwp' if Path(source).suffix.lower() in {'.hwp', '.hwpx'} else 'soffice')
    if not engine:
        return 'unavailable'
    stat = engine.stat()
    return f'{engine.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:preview-v3'


def _run(args, *, work: Path, timeout: float, cancel=None):
    if cancel and cancel():
        raise PreviewUnavailable('미리보기 생성을 중지했습니다.')
    # Files keep verbose native diagnostics out of RAM. Only the tail is reported.
    with open(work / 'stdout.log', 'wb') as out, open(work / 'stderr.log', 'wb') as err:
        process = subprocess.Popen([str(a) for a in args], stdout=out, stderr=err,
                                   stdin=subprocess.DEVNULL, cwd=work,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel and cancel():
                    raise PreviewUnavailable('미리보기 생성을 중지했습니다.')
                if time.monotonic() >= deadline:
                    raise PreviewUnavailable('미리보기 생성 제한 시간을 초과했습니다. 원본 파일 열기를 사용하세요.')
                time.sleep(.1)
            if process.returncode:
                raise PreviewUnavailable('로컬 렌더러가 원본 미리보기를 만들지 못했습니다.')
        finally:
            if process.poll() is None:
                if sys.platform == 'win32':
                    # Only descendants of this private renderer process are stopped.
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _profile(path: Path):
    user = path / 'user'
    user.mkdir(parents=True)
    # A private renderer profile never changes the user's LibreOffice settings.
    (user / 'registrymodifications.xcu').write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>
</oor:items>''', encoding='utf-8')


def preview_path(source: str | Path, *, timeout: float = 120, cancel=None) -> Preview:
    source = Path(source).resolve(strict=True)
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    sha = store.file_sha1(source)
    if source.suffix.lower() == '.pdf':
        return Preview(source, False, 'PyMuPDF', sha)
    hwp = source.suffix.lower() in {'.hwp', '.hwpx'}
    if not hwp and source.suffix.lower() not in {'.docx', '.pptx', '.xlsx', '.odt', '.odp', '.ods', '.rtf', '.doc', '.ppt', '.xls'}:
        raise PreviewUnavailable('이 형식은 원본 파일 열기로 확인하세요.')
    name = 'rhwp' if hwp else 'soffice'
    engine = find_engine(name)
    if not engine:
        raise PreviewUnavailable(('rHWP' if hwp else 'LibreOffice') + ' 미리보기 엔진을 찾지 못했습니다. 원본 파일 열기를 사용하세요.')
    identity = renderer_identity(source)
    key = sha + '-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
    folder = store.root() / 'previews' / key
    destination = folder / 'preview.pdf'
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if cancel and cancel():
            raise PreviewUnavailable('미리보기 생성을 중지했습니다.')
        if destination.is_file():
            try:
                with fitz.open(utils.long_path(destination)) as pdf:
                    if pdf.page_count and not pdf.needs_pass:
                        return Preview(destination, True, name, sha)
            except Exception:
                pass
        folder.mkdir(parents=True, exist_ok=True)
        with ExitStack() as cleanup:
            temp = cleanup.enter_context(tempfile.TemporaryDirectory(prefix='render-', dir=folder))
            work = Path(temp)
            input_copy = work / ('input' + source.suffix.lower())
            shutil.copyfile(utils.long_path(source), utils.long_path(input_copy))
            if hwp:
                output = work / 'source.pdf'
                args = [engine, 'export-pdf', input_copy, '-o', output, '--json']
            else:
                # LibreOffice adds long internal names below its profile on Windows.
                # Nesting it below the SHA cache can fail with exit 0 and no PDF.
                profile = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='pnx-lo-')))
                _profile(profile)
                args = [engine, '-env:UserInstallation=' + profile.as_uri(), '--headless', '--nologo',
                        '--nodefault', '--norestore', '--convert-to', 'pdf', '--outdir', work, input_copy]
                output = work / 'input.pdf'
            _run(args, work=work, timeout=timeout, cancel=cancel)
            if cancel and cancel():
                raise PreviewUnavailable('미리보기 생성을 중지했습니다.')
            if not output.is_file():
                raise PreviewUnavailable('렌더러가 PDF 결과를 만들지 못했습니다.')
            try:
                with fitz.open(utils.long_path(output)) as pdf:
                    if not pdf.page_count or pdf.needs_pass:
                        raise ValueError('No readable pages')
            except Exception as error:
                raise PreviewUnavailable('유효한 원본 미리보기를 만들지 못했습니다.') from error
            if store.file_sha1(source) != sha:
                raise PreviewUnavailable('미리보기 생성 중 원본이 변경됐습니다. 다시 여세요.')
            os.replace(output, destination)
        return Preview(destination, True, name, sha)
