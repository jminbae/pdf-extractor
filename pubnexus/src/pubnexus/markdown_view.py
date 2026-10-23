"""Read published Markdown with markdown-it; source spans only add UI links.

This module never edits the saved Markdown or decides document reading order.
Untrusted HTML is limited to the app's existing table allowlist.
"""
from __future__ import annotations

from bisect import bisect_right
import html
import re

from markdown_it import MarkdownIt


def to_html(markdown: str, blocks=(), *, asset_url=None, safe_table=None) -> str:
    parser = MarkdownIt('commonmark', {'html': True}).enable('table')
    offsets = [0]
    for line in markdown.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    # SDK offsets are Python Unicode code points, not UTF-8 bytes.
    spans = sorted((b['start'], b['end'], str(b.get('_view_id') or b['id']))
                   for b in blocks if type(b.get('start')) is int and type(b.get('end')) is int
                   and 0 <= b['start'] < b['end'] <= len(markdown) and b.get('id'))
    starts = [b[0] for b in spans]
    used = set()
    tokens = parser.parse(markdown)
    for token in tokens:
        if token.hidden or token.type not in {'list_item_open', 'paragraph_open', 'heading_open', 'table_open', 'fence', 'code_block', 'html_block'} or not token.map:
            continue
        begin, end = (offsets[min(n, len(offsets) - 1)] for n in token.map)
        # Intersect official character ranges with markdown-it's own line map.
        index = max(0, bisect_right(starts, begin) - 1)
        candidates = []
        while index < len(spans) and spans[index][0] < end:
            left, right, key = spans[index]
            overlap = min(right, end) - max(left, begin)
            if overlap > 0 and key not in used:
                candidates.append((overlap, key))
            index += 1
        if candidates:
            key = max(candidates)[1]
            token.attrSet('class', 'document-block')
            token.attrSet('data-block-id', key)
            used.add(key)

    def image_rule(tokens, index, options, env):
        token = tokens[index]
        destination = token.attrGet('src') or ''
        # Asset resolution belongs to the app's confined local asset route.
        resolved = asset_url(destination) if asset_url else None
        if not resolved:
            return html.escape(token.content)
        url, size = resolved
        dimensions = f' width="{size[0]}" height="{size[1]}"' if size else ''
        return (f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(token.content, quote=True)}"'
                f'{dimensions} loading="lazy">')

    def html_block_rule(tokens, index, options, env):
        token = tokens[index]
        fragment = token.content
        shown = (safe_table(fragment) if safe_table and re.match(r'\s*<table\b', fragment, re.I)
                 else html.escape(fragment))
        if token.attrGet('data-block-id'):
            return f'<div{parser.renderer.renderAttrs(token)}>{shown}</div>\n'
        return shown

    def inline_html_rule(tokens, index, options, env):
        fragment = tokens[index].content
        return fragment if re.fullmatch(r'</?(?:br|sub|sup|strong|em|b|i)\s*/?>', fragment, re.I) else html.escape(fragment)

    for name in ('fence', 'code_block'):
        original = parser.renderer.rules[name]
        def marked_code(tokens, index, options, env, base=original):
            shown = base(tokens, index, options, env)
            token = tokens[index]
            return (f'<div{parser.renderer.renderAttrs(token)}>{shown}</div>\n'
                    if token.attrGet('data-block-id') else shown)
        parser.renderer.rules[name] = marked_code
    parser.renderer.rules['image'] = image_rule
    parser.renderer.rules['html_block'] = html_block_rule
    parser.renderer.rules['html_inline'] = inline_html_rule
    return parser.renderer.render(tokens, parser.options, {})
