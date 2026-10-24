"""Bounded native source windows, separate from extraction and PDF rendering.

All strings are literal content. Callers must use textContent, never innerHTML.
Asset IDs are opaque logical addresses; package member paths never cross the API.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
import zipfile

from lxml import etree

from . import documents, utils
from .preview import PreviewUnavailable

MAX_SOURCE_BYTES = documents.MAX_INPUT_BYTES
MAX_WINDOW_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 16 * 1024 * 1024
MAX_ASSET_CACHE_BYTES = 32 * 1024 * 1024
MAX_WINDOW_IMAGES = 128
MAX_SEARCH_CELLS = 250_000
_SUPPORTED = frozenset({'.docx', '.xlsx', '.odt', '.ods', '.odp', '.epub', '.hwp', '.hwpx', '.rtf',
                        '.html', '.htm', '.eml', '.mbox'})
_RASTER_MIME = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif',
                'WEBP': 'image/webp', 'BMP': 'image/bmp'}
_assets = OrderedDict()
_assets_lock = threading.Lock()


def _stat_key(stat):
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _remember_asset(read, identity, blob, mime):
    key = (str(read.path), read.revision, identity)
    with _assets_lock:
        _assets[key] = (_stat_key(read.before), blob, mime)
        _assets.move_to_end(key)
        while len(_assets) > 128 or sum(len(value[1]) for value in _assets.values()) > MAX_ASSET_CACHE_BYTES:
            _assets.popitem(last=False)


def supports(source: str | Path) -> bool:
    return Path(source).suffix.lower() in _SUPPORTED


class _Read:
    def __init__(self, source, revision, cancel, timeout):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise PreviewUnavailable('미리보기 제한 시간이 올바르지 않습니다.')
        self.cancel = cancel
        self.deadline = time.monotonic() + timeout
        self.started = time.monotonic()
        self.bytes = 0
        self.warnings = []
        self.check()
        self.path = Path(source).resolve(strict=True)
        self.before = self.path.stat()
        if not self.path.is_file() or self.before.st_size > MAX_SOURCE_BYTES:
            raise PreviewUnavailable('원본 문서 크기 제한을 초과했습니다.')
        digest = hashlib.sha256()
        with open(utils.long_path(self.path), 'rb') as stream:
            total = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                self.check()
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise PreviewUnavailable('원본 문서 크기 제한을 초과했습니다.')
                digest.update(chunk)
        self.revision = digest.hexdigest()
        if revision is not None and revision != self.revision:
            raise PreviewUnavailable('원본이 변경됐습니다. 다시 여세요.')

    def check(self):
        if self.cancel is not None and self.cancel():
            raise PreviewUnavailable('원본 미리보기가 취소됐습니다.')
        if time.monotonic() > self.deadline:
            raise PreviewUnavailable('원본 미리보기 제한 시간을 초과했습니다.')

    def text(self, value):
        self.check()
        text = '' if value is None else value.isoformat() if hasattr(value, 'isoformat') else str(value)
        self.bytes += len(text.encode('utf-8'))
        if self.bytes > MAX_WINDOW_BYTES:
            raise PreviewUnavailable('내용 창 크기 제한을 초과했습니다. 더 작은 범위를 여세요.')
        return text

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def finish(self, result):
        self.check()
        after = self.path.stat()
        if _stat_key(self.before) != _stat_key(after):
            raise PreviewUnavailable('원본이 변경됐습니다. 다시 여세요.')
        result.update(source_ready=True, source_revision=self.revision, revision=self.revision,
                      format=self.path.suffix[1:].lower(), pages=[], source_label='원본 내용',
                      coordinate_space='logical_document', load_seconds=time.monotonic() - self.started)
        result.update(warnings=self.warnings, partial=bool(self.warnings))
        return result


def _window_number(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PreviewUnavailable('미리보기 범위가 올바르지 않습니다.')


def _image_block(identity, blob):
    block = {'kind': 'image', 'id': identity}
    if len(blob) > MAX_ASSET_BYTES:
        return dict(block, unsupported=True, text='그림 미리보기 크기 제한을 초과했습니다.')
    from PIL import Image
    try:
        with Image.open(io.BytesIO(blob)) as image:
            mime = _RASTER_MIME.get(image.format)
            size = image.size
            if not mime or size[0] * size[1] > 64_000_000:
                raise ValueError('unsupported raster')
    except (OSError, ValueError, Image.DecompressionBombError):
        return dict(block, unsupported=True, text='이 그림 형식은 내용 보기에서 표시하지 못합니다.')
    return dict(block, asset_id=identity, mime=mime, image_sha256=hashlib.sha256(blob).hexdigest(),
                width=size[0], height=size[1])


def _docx_images(block):
    element = block._element
    for number, blip in enumerate(element.xpath('.//a:blip'), 1):
        relationship = blip.get(documents._R + 'embed')
        part = block.part.related_parts.get(relationship) if relationship else None
        if part is not None:
            yield number, part.blob


def _docx(read, start, count):
    from docx import Document
    from docx.table import Table
    parts = documents._guard_zip(read.path)
    native = Document(utils.long_path(read.path))
    if any(re.match(r'word/(?:header|footer|footnotes|endnotes|comments)', part) for part in parts):
        read.warn('내용 보기는 머리말·꼬리말·각주·미주·주석을 포함하지 않습니다. 원본 모양 보기에서 확인하세요.')
    blocks, more, image_count = [], False, 0
    for index, block in enumerate(native.iter_inner_content()):
        read.check()
        if index < start:
            continue
        if index >= start + count:
            more = True
            break
        identity = f'docx-b{index + 1:06d}'
        if block._element.xpath('.//m:oMath | .//w:txbxContent'):
            read.warn('수식·글상자는 내용 보기에 포함되지 않을 수 있습니다. 원본 모양 보기에서 확인하세요.')
        if isinstance(block, Table):
            rows, truncated = [], False
            for number, row in enumerate(block.rows):
                read.check()
                if number >= 200:
                    truncated = True
                    break
                rows.append([read.text(cell.text) for cell in row.cells[:64]])
                truncated |= len(row.cells) > 64
            blocks.append({'id': identity, 'kind': 'table', 'rows': rows, 'truncated': truncated})
        else:
            style = block.style.name if block.style is not None else ''
            blocks.append({'id': identity, 'kind': 'paragraph', 'text': read.text(block.text),
                           'heading': int(style[-1]) if re.fullmatch(r'Heading [1-9]', style) else 0})
        for number, blob in _docx_images(block):
            read.check()
            if image_count >= MAX_WINDOW_IMAGES:
                read.warn('이 내용 창의 그림 개수 제한을 초과했습니다. 더 작은 범위를 여세요.')
                break
            image_count += 1
            metadata = _image_block(f'{identity}-image{number:04d}', blob)
            if not metadata.get('unsupported'):
                _remember_asset(read, metadata['id'], blob, metadata['mime'])
            else:
                read.warn(metadata['text'])
            blocks.append(metadata)
    return {'source_kind': 'content', 'blocks': blocks, 'start': start, 'count': count,
            'next_start': start + count if more else None, 'truncated': more,
            'source_unit_label': '내용', 'title': read.text(native.core_properties.title or read.path.stem)}


class _Xlsx:
    """Reuse pinned openpyxl cell decoding, without its whole-sheet dimension scan.

    ExcelReader handles OPC metadata/shared strings/styles. WorkSheetParser keeps
    the standard date and shared/array-formula rules. No engine object is patched.
    """
    def __init__(self, read):
        from openpyxl.reader.excel import ExcelReader
        from openpyxl.styles.stylesheet import apply_stylesheet
        self.read = read
        documents._guard_zip(read.path)
        self.reader = ExcelReader(utils.long_path(read.path), read_only=True, data_only=False, keep_links=False)
        try:
            for step in (self.reader.read_manifest, self.reader.read_strings, self.reader.read_workbook):
                read.check()
                step()
            apply_stylesheet(self.reader.archive, self.reader.wb)
            read.check()
            self.entries = [(sheet, rel) for sheet, rel in self.reader.parser.find_sheets()
                            if rel.Type.endswith('/worksheet') and rel.target in self.reader.valid_files]
        except BaseException:
            self.reader.archive.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.reader.archive.close()

    def dimensions(self, index):
        from openpyxl.utils.cell import range_boundaries
        with self.reader.archive.open(self.entries[index][1].target) as stream:
            events = etree.iterparse(stream, events=('start',), resolve_entities=False,
                                     no_network=True, load_dtd=False, huge_tree=False)
            for _, element in events:
                self.read.check()
                if element.getroottree().docinfo.doctype:
                    raise PreviewUnavailable('DTD가 있는 시트는 읽지 않습니다.')
                if element.tag == documents._S + 'dimension':
                    return range_boundaries(element.get('ref'))
                if element.tag == documents._S + 'sheetData':
                    break
        return None, None, None, None

    def rows(self, index, *, cached=False):
        from openpyxl.worksheet._reader import WorkSheetParser
        class SourceParser(WorkSheetParser):
            def parse_cell(self, element):
                formula = element.find(documents._S + 'f')
                origin = {'text': formula.text, 'attributes': dict(formula.attrib)} if formula is not None else None
                cell = super().parse_cell(element)
                if origin is not None:
                    cell['formula_source'] = origin
                return cell
        with self.reader.archive.open(self.entries[index][1].target) as stream:
            parser = SourceParser(stream, self.reader.shared_strings, data_only=cached,
                epoch=self.reader.wb.epoch, date_formats=self.reader.wb._date_formats,
                timedelta_formats=self.reader.wb._timedelta_formats)
            for number, cells in parser.parse():
                self.read.check()
                if not 1 <= number <= 1_048_576 or len(cells) > 16_384:
                    raise PreviewUnavailable('시트 행 또는 열 제한을 초과했습니다.')
                yield number, cells


def _cell_value(cell):
    value = cell['value']
    return getattr(value, 'text', None) if cell['data_type'] == 'f' and not isinstance(value, str) else value


def _xlsx(read, sheet_index, start_row, start_col, row_count, col_count):
    from openpyxl.utils.cell import get_column_letter
    with _Xlsx(read) as native:
        sheets = [{'index': index, 'name': read.text(sheet.name), 'state': sheet.state}
                  for index, (sheet, _) in enumerate(native.entries)]
        if not 0 <= sheet_index < len(sheets):
            raise PreviewUnavailable('시트를 선택하세요.')
        _, _, max_col, max_row = native.dimensions(sheet_index)
        raw_rows, cached_rows = native.rows(sheet_index), native.rows(sheet_index, cached=True)
        rows, more, formulas, next_row = [], False, [], start_row
        end_row = start_row + row_count
        try:
            for (number, cells), (cached_number, cached_cells) in zip(raw_rows, cached_rows):
                read.check()
                if number < start_row:
                    continue
                while next_row < min(number, end_row):
                    rows.append([''] * col_count)
                    next_row += 1
                if number >= end_row:
                    more = True
                    break
                if number != cached_number:
                    raise PreviewUnavailable('시트 수식과 저장된 값의 주소가 일치하지 않습니다.')
                values = [''] * col_count
                cached_values = {cell['column']: cell['value'] for cell in cached_cells}
                for cell in cells:
                    column = cell['column']
                    if start_col <= column < start_col + col_count:
                        value = _cell_value(cell)
                        values[column - start_col] = read.text(value)
                        if 'formula_source' in cell:
                            cached = cached_values.get(column)
                            if hasattr(cached, 'isoformat'):
                                cached = cached.isoformat()
                            read.text(cached)
                            read.text(json.dumps(cell['formula_source'], ensure_ascii=False))
                            formulas.append({'cell': f'{get_column_letter(column)}{number}',
                                'formula': value, 'cached_value': cached, 'source': cell['formula_source']})
                rows.append(values)
                next_row = number + 1
        finally:
            raw_rows.close()
            cached_rows.close()
        return {'source_kind': 'sheet', 'sheets': sheets, 'sheet_index': sheet_index,
                'start_row': start_row, 'start_col': start_col, 'row_count': row_count, 'col_count': col_count,
                'rows': rows, 'formula_cells': formulas, 'max_row': max_row, 'max_col': max_col,
                'has_more': more, 'source_unit_label': '셀', 'title': sheets[sheet_index]['name']}


class _WindowDone(Exception):
    pass


class _NativeFallback(Exception):
    pass


class _AssetFound(Exception):
    def __init__(self, blob, mime):
        self.blob, self.mime = blob, mime


class _Content:
    """A bounded display sink for existing native adapters, without a stored document."""
    def __init__(self, read, start, count, visitor=None, asset_wanted=None):
        self.read, self.start, self.count = read, start, count
        self.kind = read.path.suffix[1:].lower()
        self.cancel, self.deadline = read.cancel, read.deadline
        self.blocks, self.cursor, self.more, self.images = [], 0, False, 0
        self.visitor, self.asset_wanted = visitor, asset_wanted
        self.pending_image = {}
        self.doc = {'meta': {'title': read.path.stem}, 'source_map': {'units': []},
                    'figures': [], 'extraction_report': {}}

    def check(self):
        self.read.check()

    def warn(self, message):
        if self.kind == 'rtf' and re.search(r'Unsupported code page', message, re.I):
            raise _NativeFallback
        self.read.warn(message)

    def _literal(self, text):
        return (self.read.text(text) if not self.visitor and not self.asset_wanted and
                self.start <= self.cursor < self.start + self.count else text)

    def _emit(self, block):
        self.check()
        identity = f'{self.kind}-b{self.cursor + 1:06d}'
        index, self.cursor = self.cursor, self.cursor + 1
        block['id'] = identity
        if self.asset_wanted:
            return identity
        if self.visitor is not None:
            if not self.visitor(block):
                self.more = True
                raise _WindowDone
        elif index >= self.start + self.count:
            self.more = True
            raise _WindowDone
        elif index >= self.start:
            self.blocks.append(block)
        return identity

    def unit(self, label, kind, **location):
        # The HTML adapter uses this presence marker to keep the first title.
        if not self.doc['source_map']['units']:
            self.doc['source_map']['units'].append({})
        if kind in ('heading', 'email_headers') and label:
            text = self._literal(label)
            self._emit({'kind': 'paragraph', 'heading': 1, 'text': text})
        if kind == 'email_headers':
            metadata = self.doc['mail_messages'][-1]
            for attachment in metadata['attachments']:
                self.check()
                value = attachment['filename'] or attachment['content_type']
                self._emit({'kind': 'paragraph', 'text': self._literal('첨부 파일: ' + value)})
        return {'source': location}

    def paragraph(self, section, text, **location):
        if text:
            value = self._literal(text)
            self._emit({'kind': 'paragraph', 'text': value})
        return {'refs_figure': [], 'refs_table': []}

    def table(self, section, rows, cols, cells, **location):
        if not self.visitor and (self.asset_wanted or not self.start <= self.cursor < self.start + self.count):
            return {'id': self._emit({'kind': 'table', 'rows': []})}
        matrix = [[''] * min(cols, 64) for _ in range(min(rows, 200))]
        for cell in cells:
            self.check()
            if cell['row'] < len(matrix) and cell['col'] < min(cols, 64):
                text = str(cell.get('text', ''))
                matrix[cell['row']][cell['col']] = self._literal(text)
        truncated = rows > 200 or cols > 64
        if truncated:
            self.warn('큰 표의 일부만 내용 보기에 표시합니다. 원본 모양 보기에서 전체 표를 확인하세요.')
        identity = self._emit({'kind': 'table', 'rows': matrix, 'truncated': truncated})
        return {'id': identity}

    def preview_asset(self, blob, extension):
        if self.visitor or self.cursor < self.start:
            self.pending_image = {}
            return 'unshown-image'
        if not self.asset_wanted and self.cursor >= self.start + self.count:
            self.more = True
            raise _WindowDone
        identity = 'native-image-' + hashlib.sha256(blob).hexdigest()
        if self.asset_wanted and self.asset_wanted != identity:
            return identity
        self.images += 1
        if self.images > MAX_WINDOW_IMAGES:
            self.warn('이 내용 창의 그림 개수 제한을 초과했습니다.')
            self.pending_image = {'unsupported': True, 'text': '그림 개수 제한을 초과했습니다.'}
            return identity
        metadata = _image_block(identity, blob)
        if not metadata.get('unsupported'):
            _remember_asset(self.read, identity, blob, metadata['mime'])
            if self.asset_wanted:
                raise _AssetFound(blob, metadata['mime'])
        else:
            self.warn(metadata['text'])
        self.pending_image = metadata
        return identity

    def origin(self, section, kind, **location):
        if kind == 'image':
            identity = self._emit(dict(self.pending_image, kind='image'))
            return {'block_id': identity}
        return {'block_id': f'{self.kind}-control-{self.cursor}'}


def _semantic(read, start, count, visitor=None, asset_wanted=None):
    sink = _Content(read, start, count, visitor, asset_wanted)
    suffix = read.path.suffix.lower()
    adapter = {'.hwp': documents._rhwp, '.hwpx': documents._rhwp, '.epub': documents._epub,
               '.rtf': _rtf, '.html': _html, '.htm': _html,
               '.eml': documents._eml, '.mbox': documents._mbox}.get(suffix, documents._odf)
    if suffix in ('.eml', '.mbox'):
        read.warn('메일 내용 보기는 지원하는 머리글·대표 본문·첨부 이름이며 첨부 문서 내용은 표시하지 않습니다.')
    try:
        adapter(sink, read.path)
    except _WindowDone:
        pass
    return {'source_kind': 'content', 'blocks': sink.blocks, 'start': start, 'count': count,
            'next_start': start + count if sink.more else None, 'truncated': sink.more,
            'source_unit_label': '내용', 'title': read.text(sink.doc['meta'].get('title') or read.path.stem)}


def _html(sink, source):
    sink.warn('HTML 내용 보기는 본문과 포함된 그림이며 스크립트·서식·원본 쪽 배치는 재현하지 않습니다.')

    def skip_linked_image(src):
        sink.warn('HTML 인접 파일·외부 연결 그림은 읽지 않았습니다. 원본에 포함된 그림만 표시합니다.')
        return None

    # The existing parser handles inline data images, active-content removal and
    # native tables. A linked image never reaches a filesystem/network loader.
    documents._html_content(sink, documents._read_text(source), '', skip_linked_image)


def _rtf(sink, source):
    if source.stat().st_size > documents.MAX_TEXT_BYTES:
        raise PreviewUnavailable('RTF 내용 보기의 원본 크기 제한을 초과했습니다.')
    sink.warn('RTF 내용 보기는 일반 텍스트이며 그림·서식·쪽 배치를 포함하지 않습니다. 원본 모양 보기에서 확인하세요.')
    try:
        from striprtf.striprtf import rtf_to_text, remove_pict_groups
    except ImportError:
        pass
    else:
        from importlib.metadata import version
        sink.check()
        with open(utils.long_path(source), 'rb') as stream:
            data = stream.read(documents.MAX_TEXT_BYTES + 1)
        if len(data) > documents.MAX_TEXT_BYTES or not re.match(br'\s*\{\\rtf[0-9]', data[:80]):
            raise PreviewUnavailable('RTF 원본 형식 또는 크기를 확인하세요.')
        # The upstream parser already removes binary picture groups. Preserve the
        # remaining byte values until the document's declared ANSI codec decodes them.
        carrier = remove_pict_groups(data.decode('latin-1')).encode('latin-1')
        codepage = re.search(br'\\ansicpg([0-9]{1,5})\b', carrier[:65536])
        encoding = 'cp' + codepage[1].decode('ascii') if codepage else 'cp1252'
        try:
            source_text = carrier.decode(encoding, errors='strict')
        except LookupError as error:
            raise PreviewUnavailable('RTF 문자 인코딩을 지원하지 않습니다: ' + encoding) from error
        text = rtf_to_text(source_text, encoding=encoding, errors='strict')
        # RTF \u values are UTF-16 units. Join valid surrogate pairs using the
        # standard codec; malformed pairs are errors, never replacement characters.
        text = text.encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'strict')
        sink.check()
        sink.doc['source_map'].update(engine='striprtf', engine_version=version('striprtf'))
        unit = sink.unit('', 'document')
        for line in text.splitlines():
            sink.check()
            if line:
                sink.paragraph(unit, line)
        return
    from . import external_documents
    engine = external_documents.find_engine('pandoc')
    if engine is None:
        raise PreviewUnavailable('RTF 내용 보기용 Pandoc이 없습니다. 원본 모양 보기를 사용하세요.')
    with tempfile.TemporaryDirectory(prefix='native-rtf-preview-') as folder:
        work = Path(folder)
        output = work / 'content.txt'
        external_documents._run(sink, [str(engine), '--sandbox', '--from=rtf', '--to=plain', '--wrap=none',
                                       '--output', str(output), '--', str(source)], work, output)
        if not output.is_file() or output.stat().st_size > documents.MAX_TEXT_BYTES:
            raise PreviewUnavailable('RTF 내용 보기 출력 크기 제한을 초과했습니다.')
        text = output.read_text(encoding='utf-8')
        unit = sink.unit('', 'document')
        for line in text.splitlines():
            sink.check()
            if line:
                sink.paragraph(unit, line)


def _ods_tables(read):
    documents._guard_zip(read.path)
    with zipfile.ZipFile(utils.long_path(read.path)) as archive:
        if 'META-INF/manifest.xml' in archive.namelist():
            manifest = documents._xml(archive.read('META-INF/manifest.xml'))
            if manifest.find('.//{*}encryption-data') is not None:
                raise PreviewUnavailable('암호화된 ODS 문서는 읽지 않습니다.')
        root = documents._xml(archive.read('content.xml'))
    read.check()
    body = root.find(documents._O + 'body/' + documents._O + 'spreadsheet')
    if body is None:
        raise PreviewUnavailable('ODS 본문을 읽지 못했습니다.')
    if root.find('.//' + documents._D + 'image') is not None:
        read.warn('ODS 내용 보기는 셀이며 그림은 원본 모양 보기에서 확인하세요.')
    return body.findall(documents._TB + 'table')


def _ods_rows(read, table):
    row_number = 1
    for row in documents._odf_row_nodes(table):
        read.check()
        repeat = documents._odf_positive(row.get(documents._TB + 'number-rows-repeated'))
        if row_number + repeat > 1_048_577:
            raise PreviewUnavailable('ODS 행 수 제한을 초과했습니다.')
        yield row_number, repeat, row
        row_number += repeat


def _ods_cells(read, row):
    column = 1
    for cell in row:
        if cell.tag not in (documents._TB + 'table-cell', documents._TB + 'covered-table-cell'):
            continue
        read.check()
        repeat = documents._odf_positive(cell.get(documents._TB + 'number-columns-repeated'))
        if column + repeat > 16_385:
            raise PreviewUnavailable('ODS 열 수 제한을 초과했습니다.')
        yield column, repeat, cell
        column += repeat


def _ods_value(read, cell):
    text = '\n'.join(documents._odf_text(node, read) for node in cell.findall(documents._T + 'p'))
    if not text:
        text = next((cell.get(documents._O + key) for key in ('string-value', 'value', 'date-value',
            'time-value', 'boolean-value') if cell.get(documents._O + key) is not None), '')
    return text, cell.get(documents._TB + 'formula')


def _ods(read, sheet_index, start_row, start_col, row_count, col_count):
    from openpyxl.utils.cell import get_column_letter
    tables = _ods_tables(read)
    sheets = [{'index': index, 'name': read.text(table.get(documents._TB + 'name', f'Sheet {index + 1}')),
               'state': 'hidden' if table.get(documents._TB + 'display') == 'false' else 'visible'}
              for index, table in enumerate(tables)]
    if not 0 <= sheet_index < len(tables):
        raise PreviewUnavailable('시트를 선택하세요.')
    rows, formulas, more, next_row = [], [], False, start_row
    end_row = start_row + row_count
    for number, repeat, row in _ods_rows(read, tables[sheet_index]):
        cells = list(_ods_cells(read, row))
        if not any(documents._odf_has_value(cell, read) for _, _, cell in cells):
            continue
        if number + repeat <= start_row:
            continue
        while next_row < min(number, end_row):
            rows.append([''] * col_count)
            next_row += 1
        if number >= end_row:
            more = True
            break
        visible = []
        for column, column_repeat, cell in cells:
            if column + column_repeat <= start_col or column >= start_col + col_count:
                continue
            cached, formula = _ods_value(read, cell)
            for col in range(max(column, start_col), min(column + column_repeat, start_col + col_count)):
                visible.append((col, cached, formula))
        for row_number in range(max(number, start_row), min(number + repeat, end_row)):
            values = [''] * col_count
            for column, cached, formula in visible:
                values[column - start_col] = read.text(formula if formula is not None else cached)
                if formula is not None:
                    read.text(cached)
                    formulas.append({'cell': f'{get_column_letter(column)}{row_number}',
                                     'formula': formula, 'cached_value': cached})
            rows.append(values)
            next_row = row_number + 1
        if number + repeat > end_row:
            more = True
            break
    return {'source_kind': 'sheet', 'sheets': sheets, 'sheet_index': sheet_index, 'rows': rows,
            'formula_cells': formulas, 'start_row': start_row, 'start_col': start_col,
            'row_count': row_count, 'col_count': col_count, 'has_more': more, 'max_row': None, 'max_col': None,
            'source_unit_label': '셀', 'title': sheets[sheet_index]['name']}


def view(source: str | Path, *, revision=None, start=0, count=80, sheet_index=0,
         start_row=1, start_col=1, row_count=60, col_count=24, cancel=None, timeout=10) -> dict | None:
    """Return a content/sheet window, or None so the existing renderer can handle it."""
    if not supports(source):
        return None
    for value, minimum, maximum in ((start, 0, 1_000_000), (count, 1, 200), (sheet_index, 0, 10000),
            (start_row, 1, 1_048_576), (start_col, 1, 16_384), (row_count, 1, 200), (col_count, 1, 64)):
        _window_number(value, minimum, maximum)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise PreviewUnavailable('미리보기 제한 시간이 올바르지 않습니다.')
    try:
        read = _Read(source, revision, cancel, timeout)
        suffix = read.path.suffix.lower()
        if suffix == '.xlsx':
            result = _xlsx(read, sheet_index, start_row, start_col, row_count, col_count)
        elif suffix == '.ods':
            result = _ods(read, sheet_index, start_row, start_col, row_count, col_count)
        elif suffix == '.docx':
            result = _docx(read, start, count)
        else:
            result = _semantic(read, start, count)
        return read.finish(result)
    except _NativeFallback:
        return None
    except PreviewUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile,
            etree.LxmlError, documents.DocumentExtractionError) as error:
        raise PreviewUnavailable('원본 내용을 읽지 못했습니다: ' + str(error)) from error


def read_asset(source: str | Path, asset_id: str, revision: str, *, cancel=None, timeout=10) -> tuple[bytes, str]:
    """Read one original raster image using an opaque, revision-bound block ID."""
    match = re.fullmatch(r'docx-b([0-9]{6})-image([0-9]{4})', str(asset_id))
    native_image = re.fullmatch(r'native-image-[0-9a-f]{64}', str(asset_id))
    if ((not match and not native_image) or not supports(source) or
            (match and Path(source).suffix.lower() != '.docx') or
            not re.fullmatch(r'[0-9a-f]{64}', str(revision or ''))):
        raise PreviewUnavailable('그림 주소가 올바르지 않습니다.')
    try:
        if cancel is not None and cancel():
            raise PreviewUnavailable('원본 미리보기가 취소됐습니다.')
        source_path = Path(source).resolve(strict=True)
        with _assets_lock:
            key = (str(source_path), revision, asset_id)
            hit = _assets.get(key)
            if hit is not None and hit[0] == _stat_key(source_path.stat()):
                _assets.move_to_end(key)
                return hit[1], hit[2]
        read = _Read(source, revision, cancel, timeout)
        if native_image:
            try:
                _semantic(read, 0, 200, asset_wanted=asset_id)
            except _AssetFound as found:
                read.finish({})
                return found.blob, found.mime
            raise PreviewUnavailable('원본 그림을 찾지 못했습니다.')
        documents._guard_zip(read.path)
        from docx import Document
        native = Document(utils.long_path(read.path))
        target, image_index = int(match[1]) - 1, int(match[2])
        for index, block in enumerate(native.iter_inner_content()):
            read.check()
            if index == target:
                for number, blob in _docx_images(block):
                    if number == image_index:
                        metadata = _image_block(asset_id, blob)
                        if metadata.get('unsupported'):
                            raise PreviewUnavailable(metadata['text'])
                        read.finish({})
                        _remember_asset(read, asset_id, blob, metadata['mime'])
                        return blob, metadata['mime']
                break
        raise PreviewUnavailable('원본 그림을 찾지 못했습니다.')
    except _NativeFallback:
        raise PreviewUnavailable('이 형식의 그림은 원본 모양 보기에서 확인하세요.') from None
    except PreviewUnavailable:
        raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, etree.LxmlError,
            documents.DocumentExtractionError) as error:
        raise PreviewUnavailable('원본 그림을 읽지 못했습니다: ' + str(error)) from error


def search(source: str | Path, query: str, revision=None, *, cancel=None, limit=200, timeout=10) -> dict | None:
    """Find literal text at native block/cell addresses without a display conversion."""
    if not supports(source):
        return None
    _window_number(limit, 1, 200)
    query = str(query or '').strip()[:500]
    try:
        read = _Read(source, revision, cancel, timeout)
        if not query:
            return read.finish({'ok': True, 'results': [], 'truncated': False})
        results, scanned, truncated = [], 0, False
        pattern = re.compile(re.escape(query), re.IGNORECASE) if query else None

        def add(text, location):
            nonlocal scanned, truncated
            read.check()
            scanned += len(text.encode('utf-8'))
            if scanned > documents.MAX_TEXT_BYTES:
                truncated = True
                return False
            for match in pattern.finditer(text) if pattern else ():
                if len(results) >= limit:
                    truncated = True
                    return False
                loc = dict(location, offset=match.start(), length=match.end() - match.start())
                results.append({'side': 'source', 'text': text[max(0, match.start() - 120):match.end() + 240],
                                'locations': [loc], 'mapping': 'exact', 'block_id': location.get('block_id')})
            return True

        if read.path.suffix.lower() == '.docx':
            documents._guard_zip(read.path)
            from docx import Document
            from docx.table import Table
            for index, block in enumerate(Document(utils.long_path(read.path)).iter_inner_content(), 1):
                read.check()
                location = {'kind': 'content', 'block_id': f'docx-b{index:06d}'}
                if isinstance(block, Table):
                    stop = False
                    for row_number, row in enumerate(block.rows, 1):
                        for col_number, cell in enumerate(row.cells, 1):
                            if not add(cell.text, dict(location, row=row_number, col=col_number)):
                                stop = True
                                break
                        if stop:
                            break
                    if stop:
                        break
                elif not add(block.text, location):
                    break
        elif read.path.suffix.lower() == '.xlsx':
            from openpyxl.utils.cell import get_column_letter
            with _Xlsx(read) as native:
                cells = 0
                stop = False
                for sheet_index, (sheet, _) in enumerate(native.entries):
                    iterator = native.rows(sheet_index)
                    try:
                        for row_number, row in iterator:
                            read.check()
                            for cell in row:
                                col_number = cell['column']
                                cells += 1
                                if cells > MAX_SEARCH_CELLS:
                                    truncated = stop = True
                                    break
                                value = _cell_value(cell)
                                text = '' if value is None else value.isoformat() if hasattr(value, 'isoformat') else str(value)
                                location = {'kind': 'sheet', 'sheet_index': sheet_index, 'sheet': sheet.name,
                                            'cell': f'{get_column_letter(col_number)}{row_number}',
                                            'row': row_number, 'col': col_number}
                                if not add(text, location):
                                    stop = True
                                    break
                            if stop:
                                break
                    finally:
                        iterator.close()
                    if stop:
                        break
        elif read.path.suffix.lower() == '.ods':
            from openpyxl.utils.cell import get_column_letter
            stop, examined = False, 0
            for sheet_index, table in enumerate(_ods_tables(read)):
                for row, row_repeat, row_node in _ods_rows(read, table):
                    for col, col_repeat, cell in _ods_cells(read, row_node):
                        text, formula = _ods_value(read, cell)
                        text = formula if formula is not None else text
                        examined += 1
                        if examined > MAX_SEARCH_CELLS:
                            truncated = stop = True
                            break
                        if not pattern or not pattern.search(text):
                            continue
                        for row_number in range(row, row + row_repeat):
                            for column in range(col, col + col_repeat):
                                loc = {'kind': 'sheet', 'sheet_index': sheet_index,
                                    'sheet': table.get(documents._TB + 'name', ''),
                                    'cell': f'{get_column_letter(column)}{row_number}', 'row': row_number, 'col': column}
                                if not add(text, loc):
                                    stop = True
                                    break
                            if stop:
                                break
                        if stop:
                            break
                    if stop:
                        break
                if stop:
                    break
        else:
            def visit(block):
                if block['kind'] == 'table':
                    for row, cells in enumerate(block['rows'], 1):
                        for column, text in enumerate(cells, 1):
                            if not add(text, {'kind': 'content', 'block_id': block['id'], 'row': row, 'col': column}):
                                return False
                    return True
                return add(block.get('text', ''), {'kind': 'content', 'block_id': block['id']})
            scanned_result = _semantic(read, 0, 200, visitor=visit)
            truncated |= scanned_result['truncated']
        return read.finish({'ok': True, 'results': results, 'truncated': truncated})
    except _NativeFallback:
        return None
    except PreviewUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile,
            etree.LxmlError, documents.DocumentExtractionError) as error:
        raise PreviewUnavailable('원본 내용을 검색하지 못했습니다: ' + str(error)) from error
