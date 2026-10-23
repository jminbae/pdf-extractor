"""Preserve merged cells using the published PyMuPDF Table HTML exporter.

Only existing SDK table ranges with the same ordered text and numeric tokens
are eligible. Reading order, text detection and span inference remain upstream.
"""
from __future__ import annotations

import copy
import hashlib
from html.parser import HTMLParser
import re

from markdown_it import MarkdownIt


class _CellText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.text = []
        self.cells = []
        self.row = -1
        self.col = 0
        self.occupied = set()
        self.span = (1, 1)

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row += 1
            self.col = 0
        if tag in ('td', 'th'):
            while (self.row, self.col) in self.occupied:
                self.col += 1
            values = dict(attrs)
            self.span = (int(values.get('rowspan', 1)), int(values.get('colspan', 1)))
            if not all(1 <= number <= 1000 for number in self.span):
                raise ValueError('table span budget')
            self.text = []
            self.depth += 1
        elif tag == 'br' and self.depth:
            self.text.append(' ')

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            self.depth = max(0, self.depth - 1)
            rows, cols = self.span
            if rows * cols > 10000:
                raise ValueError('table cell budget')
            self.cells.append((self.row, self.col, rows, cols, ''.join(self.text)))
            for row in range(self.row, self.row + rows):
                for col in range(self.col, self.col + cols):
                    if (row, col) in self.occupied:
                        raise ValueError('overlapping table cells')
                    self.occupied.add((row, col))
            self.col += cols

    def handle_data(self, text):
        if self.depth:
            self.text.append(text)


def _key(text):
    # Both parsers have already decoded entities and removed style markup.
    # Signs, comparison operators, percentages and punctuation are evidence.
    return ' '.join(text.split())


def _same_cells(markdown, candidate):
    # Reuse the application's CommonMark/GFM parser; do not split pipe syntax.
    expected, actual = _CellText(), _CellText()
    expected.feed(MarkdownIt('commonmark', {'html': True}).enable('table').render(markdown))
    actual.feed(candidate)
    if not expected.cells or expected.occupied != actual.occupied:
        return False
    grid = {(row, col): text for row, col, rows, cols, text in expected.cells
            if rows == cols == 1}
    if len(grid) != len(expected.occupied):
        return False
    for row, col, rows, cols, text in actual.cells:
        if _key(grid.get((row, col), '')) != _key(text):
            return False
        for r in range(row, row + rows):
            for c in range(col, col + cols):
                if _key(grid[r, c]) not in (_key(text), _key('')):
                    return False
    return True


def _overlap(left, right):
    x = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    y = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    area = x * y
    total = ((left[2] - left[0]) * (left[3] - left[1]) +
             (right[2] - right[0]) * (right[3] - right[1]) - area)
    return area / total if total > 0 else 0


def _source_box(box, page):
    matrix = page.get('sdk_to_source', [1, 0, 0, 1, 0, 0])
    a, b, c, d, e, f = matrix
    points = [(a*x+c*y+e, b*x+d*y+f) for x in (box[0], box[2]) for y in (box[1], box[3])]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def refine_record_tables(record, pdf):
    audit = []
    for page_index, chunk in enumerate(record['chunks']):
        boxes = [box for box in chunk['page_boxes'] if box['class'] == 'table']
        if not boxes:
            continue
        page = pdf[page_index]  # Table objects weakly reference this owner.
        try:
            tables = page.find_tables(refine=True).tables
        except Exception as error:
            # Preserve the SDK table if this optional upstream refinement fails.
            record.setdefault('table_refinement_warnings', []).append(
                {'page': page_index + 1, 'reason': type(error).__name__})
            continue
        edits = []
        for box in boxes:
            page_info = (record.get('pages') or [{}] * len(record['chunks']))[page_index]
            bounds = _source_box(box['bbox'], page_info)
            # SDK layout boxes enclose text while ruled-table bounds include
            # padding. Upstream's own native/layout matching uses IoU >= .6.
            candidates = [table for table in tables if _overlap(bounds, table.bbox) >= .6]
            if len(candidates) != 1:
                continue
            start, end = box['pos']
            before = chunk['text'][start:end]
            try:
                candidate = candidates[0].to_html()
                merged = re.search(r'(?:colspan|rowspan)=["\'](?:[2-9]|\d{2,})["\']', candidate)
                verified = merged and _same_cells(before, candidate)
            except (ValueError, TypeError, KeyError, IndexError, RuntimeError, ReferenceError):
                verified = False
            if not verified:
                continue
            edits.append((start, end, candidate + '\n\n', box))
        if not edits:
            continue
        record.setdefault('markdown_raw', record['markdown'])
        record.setdefault('chunks_raw', copy.deepcopy(record['chunks']))
        for start, end, replacement, box in sorted(edits, reverse=True, key=lambda item: item[0]):
            before = chunk['text'][start:end]
            chunk['text'] = chunk['text'][:start] + replacement + chunk['text'][end:]
            delta = len(replacement) - (end - start)
            for other in chunk['page_boxes']:
                left, right = other['pos']
                if other is box:
                    other['pos'] = [start, start + len(replacement)]
                elif left >= end:
                    other['pos'] = [left + delta, right + delta]
            audit.append({'page': page_index + 1, 'box_index': box['index'],
                          'method': 'PyMuPDF.Table.to_html', 'options': {'refine': True},
                          'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
                          'after_sha256': hashlib.sha256(replacement.encode()).hexdigest(),
                          'validation': 'row_column_cell_text_and_numeric_tokens_equal'})
    record['markdown'] = ''.join(chunk['text'] for chunk in record['chunks'])
    record['table_refinements'] = audit
    return record
