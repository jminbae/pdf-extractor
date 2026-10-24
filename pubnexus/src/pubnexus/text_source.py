"""Exact, read-only locations in decoded source text, using Unicode codepoints."""
from bisect import bisect_right
import re

from . import comparison


def _location(view, offset, length):
    return {'kind': 'text', 'page': 0, 'line': bisect_right(view['line_starts'], offset),
            'offset': offset, 'length': length}


def document_map(doc, view):
    data = comparison.document_map(doc, '')
    raw = view['text']
    for block in data['blocks']:
        text = block['text']
        start = raw.find(text) if text else -1
        if start >= 0 and raw.find(text, start + 1) < 0:
            block['locations'] = [_location(view, start, len(text))]
            block['mapping'] = 'exact'
    data.update(source_kind='text', coordinate_space='text_codepoints',
                source_revision=view['source_revision'], logical=False)
    return data


def search(view, query, limit=200):
    query = str(query or '').strip()[:500]
    results = []
    if query:
        raw = view['text']
        for match in re.finditer(re.escape(query), raw, re.IGNORECASE):
            if len(results) == limit:
                return {'ok': True, 'results': results, 'truncated': True,
                        'source_revision': view['source_revision']}
            begin = raw.rfind('\n', 0, match.start()) + 1
            end = raw.find('\n', match.end())
            snippet = raw[begin:end if end >= 0 else len(raw)].rstrip('\r')
            results.append({'side': 'source', 'text': snippet[:500], 'page': 0,
                            'locations': [_location(view, match.start(), match.end() - match.start())],
                            'mapping': 'exact'})
    return {'ok': True, 'results': results, 'truncated': False, 'source_revision': view['source_revision']}
