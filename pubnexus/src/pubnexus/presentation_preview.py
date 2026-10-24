"""Bounded PPTX metadata and assets for the offline, sandboxed browser viewer.

Rendering belongs to the unmodified pinned upstream viewer. This adapter only
reads OPC package metadata and exposes native logical locators. Display bounds
come from the upstream renderer, not inferred PDF or group coordinates.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import zipfile

from . import comparison, documents, utils

VERSION = '1.4.0'
MAX_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 4000
MAX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_SLIDES = 5000
EMU_PER_PX = 9525
CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
       "img-src data: blob:; font-src data: blob:; worker-src blob:; connect-src 'none'; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; frame-ancestors 'self'")


def _web_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'web'
    return Path(__file__).resolve().parents[2] / 'web'


def _vendor_root() -> Path | None:
    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(_web_root() / 'pptx-renderer')
    if os.environ.get('PUBNEXUS_PRESENTATION_ASSETS'):
        candidates.append(Path(os.environ['PUBNEXUS_PRESENTATION_ASSETS']))
    settings = Path.home() / '.ai-workspace/paths.md'
    if settings.is_file():
        match = re.search(r'^cache_root:\s*(.+)$', settings.read_text(encoding='utf-8-sig'), re.M)
        if match:
            candidates.append(Path(match.group(1).strip()) / '260725 PDF 추출/engines' / ('pptx-renderer-' + VERSION))
    return next((p for p in candidates if (p / 'renderer.mjs').is_file() and (p / 'manifest.json').is_file()), None)


@lru_cache(maxsize=4)
def _verify_vendor(root: str, size: int, mtime: int, manifest_size: int, manifest_mtime: int) -> bool:
    path = Path(root)
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    return (isinstance(manifest, dict) and manifest.get('version') == VERSION and
            hashlib.sha256((path / 'renderer.mjs').read_bytes()).hexdigest() == manifest.get('sha256'))


def available() -> bool:
    try:
        root = _vendor_root()
        if not root or not all((_web_root() / name).is_file() for name in ('presentation-frame.html', 'presentation-frame.mjs')):
            return False
        bundle, manifest = (root / 'renderer.mjs').stat(), (root / 'manifest.json').stat()
        return _verify_vendor(str(root), bundle.st_size, bundle.st_mtime_ns, manifest.st_size, manifest.st_mtime_ns)
    except (OSError, ValueError, TypeError):
        return False


def asset(name: str) -> bytes:
    if name == 'vendor':
        root = _vendor_root()
        if not root or not available():
            raise ValueError('슬라이드 표시 도구가 준비되지 않았습니다.')
        path = root / 'renderer.mjs'
    elif name in {'presentation-frame.html', 'presentation-frame.mjs'}:
        path = _web_root() / name
    else:
        raise ValueError('Unknown presentation asset')
    return path.read_bytes()


def _checked_package(source: Path):
    if source.suffix.lower() != '.pptx' or source.stat().st_size > MAX_BYTES:
        raise ValueError('빠른 슬라이드 보기의 파일 크기 또는 형식을 확인하세요.')
    try:
        documents._guard_zip(source)
        archive = zipfile.ZipFile(utils.long_path(source))
    except (documents.DocumentExtractionError, zipfile.BadZipFile) as error:
        raise ValueError(str(error)) from error
    try:
        entries = archive.infolist()
        if (len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_BYTES or
                any(e.file_size > MAX_ENTRY_BYTES for e in entries)):
            raise ValueError('빠른 슬라이드 보기의 압축 해제 한도를 초과했습니다.')
        return archive
    except BaseException:
        archive.close()
        raise


def describe(source: Path, index: int) -> dict:
    source = Path(source)
    before = source.stat()
    with _checked_package(source) as archive:
        root = documents._xml(archive.read('ppt/presentation.xml'))
        ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'}
        size = root.find('p:sldSz', ns)
        slides = root.findall('p:sldIdLst/p:sldId', ns)
        if size is None or not 1 <= len(slides) <= MAX_SLIDES:
            raise ValueError('슬라이드 크기와 개수를 읽지 못했습니다.')
        width, height = int(size.get('cx', '0')) / EMU_PER_PX, int(size.get('cy', '0')) / EMU_PER_PX
        if not all(math.isfinite(v) and 0 < v <= 100000 for v in (width, height)):
            raise ValueError('유효하지 않은 슬라이드 크기입니다.')
        relationships = documents._relationships(archive, 'ppt/presentation.xml')
        ids = [relationships.get(node.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')) for node in slides]
        if any(not part or part not in archive.namelist() for part in ids) or len(set(ids)) != len(ids):
            raise ValueError('슬라이드 순서 정보를 확인하지 못했습니다.')
    with open(utils.long_path(source), 'rb') as stream:
        revision = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('원본이 변경됐습니다. 다시 여세요.')
    return {'source_kind': 'presentation', 'source_ready': True, 'source_revision': revision,
            'source_frame_url': '/presentation-frame',
            'source_data_url': f'/presentation-data?p={index}&r={revision}',
            'renderer_version': VERSION, 'source_unit_label': '슬라이드', 'source_label': '원본 슬라이드',
            'pages': [[width, height] for _ in slides], 'slide_parts': ids,
            'coordinate_space': 'presentation_intrinsic_pixels'}


def read_data(source: Path, revision: str) -> bytes:
    if not re.fullmatch(r'[0-9a-f]{64}', str(revision or '')):
        raise ValueError('슬라이드를 다시 여세요.')
    with _checked_package(source):
        with open(utils.long_path(source), 'rb') as stream:
            body = stream.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != revision:
        raise ValueError('원본이 변경됐습니다. 다시 여세요.')
    return body


def document_map(doc: dict, header: dict) -> dict:
    """Keep logical slide hints; only the active renderer can supply display bounds."""
    pages = [{'width': size[0], 'height': size[1]} for size in header['pages']]
    blocks = []
    for item, fallback, kind, text in comparison._items(doc):
        origin = item.get('source') or {}
        slide = origin.get('slide')
        slide_hint = slide - 1 if type(slide) is int and 1 <= slide <= len(pages) else None
        blocks.append({'id': item.get('_view_id') or item.get('id') or fallback, 'kind': kind,
                       'text': text, 'locations': [], 'mapping': 'unmapped', 'slide': slide_hint,
                       'logical_source': {key: origin[key] for key in ('slide', 'shape', 'paragraph') if key in origin}})
    return {'source_kind': 'presentation', 'source_revision': header['source_revision'],
            'blocks': blocks, 'pages': pages, 'coordinate_space': 'presentation_intrinsic_pixels', 'logical': True}
