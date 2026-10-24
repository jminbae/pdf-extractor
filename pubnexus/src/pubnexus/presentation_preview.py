"""Bounded PPTX metadata and assets for the offline, sandboxed browser viewer.

Rendering belongs to the unmodified pinned upstream viewer. This adapter only
reads OPC package metadata and exposes native logical locators. Display bounds
come from the upstream renderer, not inferred PDF or group coordinates.
"""
from __future__ import annotations

from functools import lru_cache
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import zipfile

from lxml import etree

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


def _source_digests(source):
    sha1, sha256, total = hashlib.sha1(), hashlib.sha256(), 0
    with open(utils.long_path(source), 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError('빠른 슬라이드 보기의 파일 크기 한도를 초과했습니다.')
            sha1.update(chunk)
            sha256.update(chunk)
    return sha1.hexdigest(), sha256.hexdigest()


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
    source_sha1, revision = _source_digests(source)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('원본이 변경됐습니다. 다시 여세요.')
    return {'source_kind': 'presentation', 'source_ready': True, 'source_revision': revision,
            'source_sha1': source_sha1,
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


def _native_shape_index(source: Path, header: dict) -> dict:
    """Resolve old order paths to native IDs only from the exact original package."""
    before = source.stat()
    namespace = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
    shape_tags = {namespace + name for name in ('sp', 'grpSp', 'graphicFrame', 'cxnSp', 'pic', 'contentPart')}
    result = {}
    with _checked_package(source) as archive:
        for slide_number, part in enumerate(header['slide_parts'], 1):
            try:
                root = documents._xml(archive.read(part))
            except (documents.DocumentExtractionError, etree.XMLSyntaxError, KeyError, zipfile.BadZipFile) as error:
                raise ValueError('슬라이드의 원본 도형 정보를 읽지 못했습니다: ' + str(error)) from error
            tree = root.find('.//' + namespace + 'spTree')
            if tree is None:
                continue
            records = {}

            def walk(parent, prefix='', groups=()):
                shapes = [node for node in parent if node.tag in shape_tags]
                for number, node in enumerate(shapes, 1):
                    path = f'{prefix}/{number}' if prefix else str(number)
                    properties = [child.find(namespace + 'cNvPr') for child in node]
                    identifiers = [prop.get('id') for prop in properties if prop is not None]
                    identity = identifiers[0] if len(identifiers) == 1 else None
                    if not isinstance(identity, str) or not re.fullmatch(r'[1-9][0-9]*', identity):
                        identity = None
                    records[path] = {'node_id': identity, 'group_node_ids': list(groups)}
                    if node.tag == namespace + 'grpSp':
                        walk(node, path, (*groups, identity))

            walk(tree)
            counts = Counter(record['node_id'] for record in records.values())
            for path, record in records.items():
                ids = [record['node_id'], *record['group_node_ids']]
                if all(identity and counts[identity] == 1 for identity in ids):
                    result[(slide_number, path)] = record
    source_sha1, revision = _source_digests(source)
    after = source.stat()
    if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or
            revision != header.get('source_revision') or source_sha1 != header.get('source_sha1')):
        raise ValueError('원본이 변경됐습니다. 다시 여세요.')
    return result


def document_map(doc: dict, header: dict, source: Path | None = None) -> dict:
    """Keep logical slide hints; only the active renderer can supply display bounds."""
    pages = [{'width': size[0], 'height': size[1]} for size in header['pages']]
    verified = bool(doc.get('sha1') and doc['sha1'] == header.get('source_sha1'))
    items = list(comparison._items(doc))
    legacy = (verified and source is not None and any(
        (item.get('source') or {}).get('shape') and not (item.get('source') or {}).get('source_shape_id')
        for item, *_ in items))
    shape_index = _native_shape_index(Path(source), header) if legacy else {}
    image_sha256 = {}
    if verified:
        for figure in doc.get('figures') or []:
            asset_name = figure.get('image')
            digest = Path(asset_name).stem if isinstance(asset_name, str) else ''
            if re.fullmatch(r'[0-9a-f]{64}', digest):
                image_sha256[figure.get('id')] = digest
    blocks = []
    for item, fallback, kind, text in items:
        origin = item.get('source') or {}
        slide = origin.get('slide')
        slide_hint = slide - 1 if type(slide) is int and 1 <= slide <= len(pages) else None
        logical = {key: origin[key] for key in ('slide', 'shape', 'paragraph', 'shape_bbox_emu', 'shape_bbox_space',
                   'image_crop_fraction', 'shape_transform', 'group_transforms', 'image_sha256',
                   'image_source_crop_px', 'source_image_size_px', 'ocr_image_size_px', 'ocr_orientation',
                   'image_bbox_px', 'raw_ocr_bbox_px', 'coordinate_space', 'geometry_status') if key in origin}
        if verified and slide_hint is not None:
            identity = origin.get('source_shape_id')
            groups = origin.get('group_shape_ids') or []
            if not identity:
                native = shape_index.get((slide, origin.get('shape'))) or {}
                identity, groups = native.get('node_id'), native.get('group_node_ids') or []
            if (isinstance(identity, str) and re.fullmatch(r'[1-9][0-9]*', identity) and
                    all(isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value) for value in groups)):
                logical.update(node_id=identity, group_node_ids=list(groups))
        image_id = origin.get('image_id') or (item.get('id') if kind == 'figure' else None)
        if not image_id and len(item.get('refs_figure') or []) == 1:
            image_id = item['refs_figure'][0]
        if image_id:
            logical['actual_source_image_id'] = image_id
            if 'image_sha256' not in logical and image_id in image_sha256:
                logical['image_sha256'] = image_sha256[image_id]
        blocks.append({'id': item.get('_view_id') or item.get('id') or fallback, 'kind': kind,
                       'text': text, 'locations': [], 'mapping': 'unmapped', 'slide': slide_hint,
                       'logical_source': logical})
    return {'source_kind': 'presentation', 'source_revision': header['source_revision'],
            'blocks': blocks, 'pages': pages, 'coordinate_space': 'presentation_intrinsic_pixels', 'logical': True}
