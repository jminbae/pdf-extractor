"""Conservative document block links, source search, and display coordinates.

Normalized text alignment accepts only a unique occurrence. Repeated unlocated
text stays unmapped; native parser addresses are preferred over alignment.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import re

import fitz

from . import utils


def _fold(text):
    return ''.join(c for c in str(text).casefold() if c.isalnum())


def _table_text(table):
    cells = [cell for cell in table.get('cells') or [] if isinstance(cell, dict)]
    if cells:
        def position(cell):
            return tuple(cell.get(key) if type(cell.get(key)) is int else 0 for key in ('row', 'col'))
        content = '\n'.join(str(cell.get('text') or '') for cell in sorted(cells, key=position))
    else:
        content = str(table.get('markdown') or '')
        if re.search(r'<\s*table\b', content, re.I):
            from lxml import etree, html
            try:
                root = html.fragment_fromstring(content, create_parent='div', parser=html.HTMLParser(no_network=True))
                content = ' '.join(root.itertext())
            except (etree.ParserError, ValueError):
                # Keep malformed markup visible; do not fabricate a location.
                pass
    return '\n'.join(part for part in (str(table.get('caption') or ''), content) if part)


def _items(doc):
    if isinstance(doc.get('sdk_markdown'), str):
        for index, item in enumerate(doc.get('sdk_blocks') or []):
            kind = {'picture': 'figure', 'text': 'paragraph'}.get(item.get('kind'), item.get('kind', 'paragraph'))
            yield item, f'sdk-{index}', kind, item.get('text') or ''
        return
    for si, sec in enumerate(doc.get('body_text') or []):
        for pi, item in enumerate(sec.get('paragraphs') or []):
            yield item, f'paragraph-{si}-{pi}', 'paragraph', item.get('text') or ''
    for kind, key in (('table', 'tables'), ('figure', 'figures')):
        for index, item in enumerate(doc.get(key) or []):
            text = _table_text(item) if kind == 'table' else str(item.get('caption') or '')
            yield item, f'{kind}-{index}', kind, text


def attach(doc, source):
    """Copy canonical data, assign render IDs, and link unique native PDF text."""
    out = copy.deepcopy(doc)
    used = set()
    for item, fallback, _, _ in _items(out):
        candidate = str(item.get('id') or fallback)
        original, attempt = candidate, 0
        while candidate in used:
            attempt += 1
            candidate = fallback + '-' + hashlib.sha256(original.encode()).hexdigest()[:8] + (f'-{attempt}' if attempt > 1 else '')
        item['_view_id'] = candidate
        used.add(candidate)
    if Path(source).suffix.lower() != '.pdf':
        return out
    if all((item.get('source') or {}).get('bbox') or (item.get('source') or {}).get('locations')
           for item, _, _, _ in _items(out)):
        return out
    chunks, native = [], []
    offset = 0
    with fitz.open(utils.long_path(Path(source))) as pdf:
        if pdf.needs_pass:
            return out
        for page_index, page in enumerate(pdf):
            for block in page.get_text('blocks', sort=True):
                if len(block) > 6 and block[6] != 0:
                    continue
                text = _fold(block[4])
                if not text:
                    continue
                native.append((offset, offset + len(text), page_index + 1, list(block[:4])))
                chunks.append(text)
                offset += len(text)
    full = ''.join(chunks)
    for item, _, _, text in _items(out):
        origin = item.get('source') or {}
        if origin.get('locations') or (origin.get('page') and origin.get('bbox')):
            continue
        needle = _fold(text)
        if len(needle) < 12:
            continue
        start = full.find(needle)
        if start < 0 or full.find(needle, start + 1) >= 0:
            continue
        spans = [{'page': page, 'bbox': bbox} for begin, end, page, bbox in native
                 if begin < start + len(needle) and end > start]
        if spans:
            item['source'] = {'locations': spans, 'mapping': 'normalized'}
    return out


def document_map(doc, source):
    """Map source coordinates to the rotated image coordinates used by the UI."""
    pages, transforms = [], []
    if Path(source).suffix.lower() == '.pdf':
        with fitz.open(utils.long_path(Path(source))) as pdf:
            for page in pdf:
                pages.append({'width': float(page.rect.width), 'height': float(page.rect.height)})
                transforms.append(page.rotation_matrix)
    blocks = []
    for item, fallback, kind, text in _items(doc):
        origin = item.get('source') or {}
        locations = origin.get('locations') or ([origin] if origin.get('page') else [])
        display = []
        for loc in locations:
            number = loc.get('page')
            bbox = loc.get('bbox')
            if type(number) is not int or not 1 <= number <= len(pages) or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            if not all(type(v) in (int, float) and math.isfinite(v) for v in bbox):
                continue
            try:
                rect = fitz.Rect(bbox) * transforms[number - 1]
                size = pages[number - 1]
                rect = rect & fitz.Rect(0, 0, size['width'], size['height'])
                if rect.is_empty or rect.is_infinite:
                    continue
                display.append({'page': number - 1, 'bbox': list(rect), **pages[number - 1]})
            except (ValueError, TypeError):
                continue
        blocks.append({'id': item.get('_view_id') or item.get('id') or fallback,
                       'kind': kind, 'text': text, 'locations': display,
                       'mapping': origin.get('mapping', 'exact') if display else 'unmapped'})
    digest = hashlib.sha256(repr([(b['id'], b['text'], b['locations']) for b in blocks]).encode()).hexdigest()
    return {'blocks': blocks, 'pages': pages, 'coordinate_space': 'pymupdf_display_points',
            'revision': digest, 'logical': not bool(pages)}


def search(doc, source, query, limit=200, *, mapping=None):
    query = str(query or '').strip()[:500]
    if not query:
        return {'ok': True, 'results': [], 'truncated': False}
    data = mapping if mapping is not None else document_map(doc, source)
    results = []
    for block in data['blocks']:
        if query.casefold() in block['text'].casefold():
            locs = block['locations']
            results.append({'block_id': block['id'], 'text': block['text'][:500],
                            'page': locs[0]['page'] if locs else None, 'locations': locs,
                            'side': 'result', 'mapping': block['mapping']})
    if Path(source).suffix.lower() == '.pdf':
        with fitz.open(utils.long_path(Path(source))) as pdf:
            for index, page in enumerate(pdf):
                for rect in page.search_for(query):
                    shown = rect * page.rotation_matrix
                    candidates = []
                    for block in data['blocks']:
                        for loc in block['locations']:
                            if loc['page'] == index and (shown & fitz.Rect(loc['bbox'])).get_area() > shown.get_area() * .5:
                                candidates.append(block['id'])
                    candidates = set(candidates)
                    linked = next(iter(candidates)) if len(candidates) == 1 else None
                    results.append({'block_id': linked, 'text': page.get_textbox(rect) or query,
                                    'page': index, 'locations': [{'page': index, 'bbox': list(shown), **data['pages'][index]}],
                                    'side': 'source', 'mapping': 'exact' if linked else 'unmapped'})
                    if len(results) > limit:
                        return {'ok': True, 'results': results[:limit], 'truncated': True}
    return {'ok': True, 'results': results[:limit], 'truncated': len(results) > limit}
