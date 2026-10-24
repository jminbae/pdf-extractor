"""Read-only HWP/HWPX pages through the shipped rHWP native renderer.

The engine supplies every page's dimensions. Only requested PNGs are rasterized;
there is no PDF, Markdown, OCR, semantic extraction, or user-profile prerequisite.
"""
from __future__ import annotations

import copy
import io
import json
import math
import re
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from . import documents, preview
from .native_preview import _Read, _stat_key
from .preview import PreviewUnavailable, find_engine

MAX_PAGES = 2000
MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_TREE_BYTES = 8 * 1024 * 1024
MAX_TREE_NODES = 250_000
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_SEARCH_BYTES = 8 * 1024 * 1024
_metadata = OrderedDict()
_pixels = OrderedDict()
_texts = OrderedDict()
_lock = threading.Lock()


def _clear_cache():
    with _lock:
        _metadata.clear()
        _pixels.clear()
        _texts.clear()


def supports(source):
    return Path(source).suffix.lower() in {'.hwp', '.hwpx'}


def _check(cancel, deadline):
    if cancel is not None and cancel():
        raise PreviewUnavailable('원본 미리보기가 취소됐습니다.')
    if time.monotonic() >= deadline:
        raise PreviewUnavailable('원본 미리보기 제한 시간을 초과했습니다.')


def _renderer():
    engine = find_engine('rhwp')
    if engine is None:
        raise PreviewUnavailable('rHWP 원본 페이지 렌더러가 없습니다.')
    engine = engine.resolve(strict=True)
    return engine, (str(engine), *_stat_key(engine.stat()), 'native-png-v1')


def _unchanged(path, stat, signature):
    if (_stat_key(path.stat()) != stat
            or _stat_key(Path(signature[0]).stat()) != signature[1:4]):
        raise PreviewUnavailable('원본 또는 렌더러가 변경됐습니다. 다시 여세요.')


def _run(read, args, work):
    # Reuse the existing owned-process cancellation/descendant cleanup boundary.
    # Check output bytes while the engine runs, not only after loading JSON/PNG.
    def cancelled():
        read.check()
        files = list(work.rglob('*'))
        if len(files) > MAX_PAGES + 8 or sum(p.stat().st_size for p in files if p.is_file()) > MAX_METADATA_BYTES:
            raise PreviewUnavailable('원본 페이지 출력 크기 제한을 초과했습니다.')
        return False
    preview._run(args, work=work, timeout=max(.001, read.deadline - time.monotonic()), cancel=cancelled)
    read.check()
    output = work / 'stdout.log'
    if output.stat().st_size > (MAX_SEARCH_BYTES if args[1] == 'export-text' else 65536):
        raise PreviewUnavailable('페이지 정보 응답 크기 제한을 초과했습니다.')
    return output.read_bytes()


def _json(blob):
    try:
        return json.loads(blob)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise PreviewUnavailable('원본 페이지 정보를 읽지 못했습니다.') from error


def _tree_size(path, read, remaining):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_TREE_BYTES:
        raise PreviewUnavailable('페이지 구조 크기 제한을 초과했습니다.')
    tree = _json(path.read_bytes())
    if not isinstance(tree, dict) or tree.get('type') != 'Page':
        raise PreviewUnavailable('원본 페이지 경계 정보가 없습니다.')
    bbox = tree.get('bbox', {})
    values = [bbox.get(name) for name in ('x', 'y', 'w', 'h')]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise PreviewUnavailable('원본 페이지 크기가 올바르지 않습니다.')
    x, y, width, height = values
    if x != 0 or y != 0 or not 0 < width <= 20000 or not 0 < height <= 20000:
        raise PreviewUnavailable('원본 페이지 크기가 올바르지 않습니다.')
    pending = [tree]
    while pending:
        read.check()
        node = pending.pop()
        remaining -= 1
        if remaining < 0:
            raise PreviewUnavailable('페이지 구조 노드 제한을 초과했습니다.')
        if isinstance(node, dict):
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return [width, height], remaining


def describe(source, *, revision=None, cancel=None, timeout=15):
    """Return exact renderer page count/sizes (CSS px), with no raster prefetch."""
    if not supports(source):
        return None
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise PreviewUnavailable('미리보기 제한 시간이 올바르지 않습니다.')
    deadline = time.monotonic() + timeout
    try:
        _check(cancel, deadline)
        path = Path(source).resolve(strict=True)
        stat = _stat_key(path.stat())
        engine, signature = _renderer()
        key = (str(path), stat, signature)
        with _lock:
            cached = _metadata.get(key)
            if cached is not None:
                _metadata.move_to_end(key)
                result = copy.deepcopy(cached)
        if cached is not None:
            if revision is not None and revision != result['revision']:
                raise PreviewUnavailable('원본이 변경됐습니다. 다시 여세요.')
            _check(cancel, deadline)
            _unchanged(path, stat, signature)
            return result
        read = _Read(path, revision, cancel, max(.001, deadline - time.monotonic()))
        if path.suffix.lower() == '.hwpx':
            documents._guard_zip(path)
        with tempfile.TemporaryDirectory(prefix='hwp-page-meta-') as folder:
            work = Path(folder)
            info = _json(_run(read, [str(engine), 'info', str(path), '--json'], work))
            count = info.get('pageCount') if isinstance(info, dict) else None
            if type(count) is not int or not 0 < count <= MAX_PAGES:
                raise PreviewUnavailable('원본 쪽 수가 없거나 미리보기 한도를 초과했습니다.')
            output = work / 'trees'
            _run(read, [str(engine), 'export-render-tree', str(path), '-o', str(output)], work)
            files = list(output.iterdir()) if output.is_dir() else []
            expected = [output / f'render_tree_{page + 1:03}.json' for page in range(count)]
            if set(files) != set(expected) or sum(p.stat().st_size for p in files) > MAX_METADATA_BYTES:
                raise PreviewUnavailable('원본의 모든 쪽 경계 정보를 확인하지 못했습니다.')
            sizes, remaining = [], MAX_TREE_NODES
            for page in expected:
                size, remaining = _tree_size(page, read, remaining)
                sizes.append(size)
        read.check()
        _unchanged(path, stat, signature)
        result = {'source_kind': 'hwp_native', 'source_ready': True, 'source_label': '원본',
                  'source_unit_label': '쪽', 'coordinate_space': 'page_px', 'pages': sizes,
                  'page_count': count, 'source_revision': read.revision, 'revision': read.revision,
                  'source_stat': list(stat),
                  'engine': 'rHWP native PNG', 'renderer_identity': list(signature),
                  'format': path.suffix[1:].lower(), 'title': str(info.get('title') or path.stem),
                  'load_seconds': time.monotonic() - read.started}
        with _lock:
            _metadata[key] = copy.deepcopy(result)
            _metadata.move_to_end(key)
            while len(_metadata) > 16:
                _metadata.popitem(last=False)
        return result
    except (OSError, documents.DocumentExtractionError) as error:
        raise PreviewUnavailable(str(error)) from error


def read_page(source, revision, page, *, max_dimension=1600, cancel=None, timeout=15):
    """Return a single bounded PNG; revision is mandatory, page is zero-based."""
    if type(page) is not int or page < 0 or not isinstance(revision, str) or len(revision) != 64:
        raise PreviewUnavailable('원본 페이지 요청이 올바르지 않습니다.')
    if type(max_dimension) is not int:
        raise PreviewUnavailable('원본 이미지 크기가 올바르지 않습니다.')
    dimension = min(4096, max(320, max_dimension))
    started = time.monotonic()
    header = describe(source, revision=revision, cancel=cancel, timeout=timeout)
    if header is None or page >= header['page_count']:
        raise PreviewUnavailable('원본 쪽 범위를 벗어났습니다.')
    deadline = started + timeout
    try:
        path = Path(source).resolve(strict=True)
        stat = tuple(header['source_stat'])
        signature = tuple(header['renderer_identity'])
        engine = Path(signature[0])
        _unchanged(path, stat, signature)
        key = (str(path), stat, signature, revision, page, dimension)
        _check(cancel, deadline)
        with _lock:
            cached = _pixels.get(key)
            if cached is not None:
                _pixels.move_to_end(key)
        if cached is not None:
            _unchanged(path, stat, signature)
            return cached, 'image/png'
        # The metadata snapshot already hashed this stat/revision. Do not reread
        # a large original for each viewport image; verify the same stat twice.
        read = SimpleNamespace(deadline=deadline, check=lambda: _check(cancel, deadline))
        with tempfile.TemporaryDirectory(prefix='hwp-page-png-') as folder:
            work = Path(folder)
            output = work / 'page'
            # fast-preview defaults to 1x even when a larger cap is supplied.
            # Explicit engine scaling preserves detail when the viewport zooms.
            scale = (dimension - 2) / max(header['pages'][page])
            _run(read, [str(engine), 'export-png', str(path), '-p', str(page),
                        '--profile', 'fast-preview', '--scale', f'{scale:.8f}',
                        '--max-dimension', str(dimension),
                        '-o', str(output)], work)
            files = list(output.iterdir()) if output.is_dir() else []
            if len(files) != 1 or files[0].suffix.lower() != '.png' or files[0].is_symlink():
                raise PreviewUnavailable('요청한 원본 쪽 이미지를 확인하지 못했습니다.')
            if files[0].stat().st_size > MAX_PAGE_BYTES:
                raise PreviewUnavailable('원본 쪽 이미지 크기 제한을 초과했습니다.')
            blob = files[0].read_bytes()
            try:
                with Image.open(io.BytesIO(blob)) as image:
                    width, height = image.size
                    if image.format != 'PNG' or max(width, height) > dimension or width * height > 4096 ** 2:
                        raise ValueError('invalid page raster')
                    ratio = header['pages'][page][0] / header['pages'][page][1]
                    if abs(width / height / ratio - 1) > .01:
                        raise ValueError('page aspect ratio differs')
                    image.verify()
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                raise PreviewUnavailable('원본 쪽 이미지가 올바르지 않습니다.') from error
        _check(cancel, deadline)
        _unchanged(path, stat, signature)
        with _lock:
            _pixels[key] = blob
            _pixels.move_to_end(key)
            while len(_pixels) > 128 or sum(map(len, _pixels.values())) > MAX_CACHE_BYTES:
                _pixels.popitem(last=False)
        return blob, 'image/png'
    except OSError as error:
        raise PreviewUnavailable(str(error)) from error


def search(source, query, revision=None, *, cancel=None, limit=200, timeout=15):
    """Search the engine's page-numbered literal text, without invented boxes."""
    if not supports(source):
        return None
    if type(limit) is not int or not 1 <= limit <= 200:
        raise PreviewUnavailable('원본 검색 개수 제한이 올바르지 않습니다.')
    query = str(query or '').strip()[:500]
    started = time.monotonic()
    header = describe(source, revision=revision, cancel=cancel, timeout=timeout)
    deadline = started + timeout
    results = []
    result = {'ok': True, 'results': results, 'source_revision': header['revision'],
              'truncated': False, 'omitted_count': 0}
    if not query:
        return result
    try:
        path = Path(source).resolve(strict=True)
        stat, signature = tuple(header['source_stat']), tuple(header['renderer_identity'])
        _unchanged(path, stat, signature)
        key = (str(path), stat, signature, header['revision'])
        _check(cancel, deadline)
        with _lock:
            cached = _texts.get(key)
            if cached is not None:
                _texts.move_to_end(key)
        if cached is None:
            read = SimpleNamespace(deadline=deadline, check=lambda: _check(cancel, deadline))
            with tempfile.TemporaryDirectory(prefix='hwp-page-search-') as folder:
                blob = _run(read, [signature[0], 'export-text', str(path), '--json',
                                   '--max-chars', '1000000'], Path(folder))
            if len(blob) > MAX_SEARCH_BYTES:
                raise PreviewUnavailable('원본 검색 본문 크기 제한을 초과했습니다.')
            envelope = _json(blob)
            if (not isinstance(envelope, dict) or envelope.get('pageCount') != header['page_count']
                    or type(envelope.get('truncated')) is not bool
                    or type(envelope.get('omittedCount')) is not int or envelope['omittedCount'] < 0
                    or not isinstance(envelope.get('pages'), list)):
                raise PreviewUnavailable('원본 검색의 페이지 정보를 확인하지 못했습니다.')
            seen, pages, size = set(), [], 0
            for item in envelope['pages']:
                _check(cancel, deadline)
                page = item.get('page') if isinstance(item, dict) else None
                text = item.get('text') if isinstance(item, dict) else None
                if (type(page) is not int or not 0 <= page < header['page_count'] or page in seen
                        or not isinstance(text, str)):
                    raise PreviewUnavailable('원본 검색 페이지가 올바르지 않습니다.')
                seen.add(page)
                size += len(text.encode('utf-8'))
                if size > MAX_SEARCH_BYTES:
                    raise PreviewUnavailable('원본 검색 본문 크기 제한을 초과했습니다.')
                pages.append((page, text))
            if not envelope['truncated'] and len(seen) != header['page_count']:
                raise PreviewUnavailable('원본 검색의 나머지 쪽을 확인하지 못했습니다.')
            cached = (tuple(pages), envelope['truncated'], envelope['omittedCount'], size)
            _unchanged(path, stat, signature)
            with _lock:
                _texts[key] = cached
                _texts.move_to_end(key)
                while len(_texts) > 8 or sum(value[3] for value in _texts.values()) > 16 * 1024 * 1024:
                    _texts.popitem(last=False)
        result.update(truncated=cached[1], omitted_count=cached[2])
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        for page, text in cached[0]:
            _check(cancel, deadline)
            for match in pattern.finditer(text):
                if len(results) >= limit:
                    result['truncated'] = True
                    break
                snippet = text[max(0, match.start() - 120):match.end() + 240]
                results.append({'side': 'source', 'page': page, 'text': snippet, 'snippet': snippet,
                                'locations': [{'kind': 'hwp_native', 'page': page, 'offset': match.start(),
                                               'length': match.end() - match.start()}], 'mapping': 'exact'})
            if len(results) >= limit and result['truncated']:
                break
        _check(cancel, deadline)
        _unchanged(path, stat, signature)
        return result
    except OSError as error:
        raise PreviewUnavailable(str(error)) from error
