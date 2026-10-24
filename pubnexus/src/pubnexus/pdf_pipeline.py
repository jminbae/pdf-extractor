"""Common SDK-first PDF extraction, with native preservation on SDK failure."""
from __future__ import annotations

import copy
from pathlib import Path

from . import general_pdf, pdf_markdown, store
from .general_pdf import ExtractionCancelled


def _sdk_text(markdown):
    # Compare visible text with native glyphs, not HTML tag names such as "br".
    # Parsing never changes the saved SDK Markdown or duplicates its occurrence
    # budget; layout remains entirely owned by the published converter.
    from markdown_it import MarkdownIt
    from lxml import html
    rendered = MarkdownIt('commonmark', {'html': True}).enable('table').render(markdown)
    return ''.join(html.fragment_fromstring(rendered, create_parent='div').itertext())


def _unchanged(source, identity, cancel):
    general_pdf._check_cancel(cancel)
    try:
        unchanged = store.file_sha1(source) == identity
    except OSError as error:
        raise pdf_markdown.PDFMarkdownError('처리 중 원본 PDF를 확인할 수 없습니다.', 'source_changed') from error
    if not unchanged:
        raise pdf_markdown.PDFMarkdownError('처리 중 원본 PDF가 변경되었습니다.', 'source_changed')
    general_pdf._check_cancel(cancel)


def _native_validation(converted, source, cancel, progress):
    # The published converter owns layout. Reuse the existing loss guard only
    # after it runs; text-only native access never invokes OCR or paper repair.
    sections, pages = [], []
    with general_pdf.fitz.open(str(source)) as pdf:
        total = pdf.page_count
        general_pdf._notify(progress, 0, total, 'pdf_validation', cancel)
        for index in range(total):
            general_pdf._check_cancel(cancel)
            page, paragraphs, _ = general_pdf._read_native_page(pdf, index)
            pages.append(page)
            if paragraphs:
                sections.append({'paragraphs': paragraphs})
            general_pdf._notify(progress, index + 1, total, 'pdf_validation', cancel)
    baseline, _ = general_pdf._coverage_sections(sections, pages)
    coverage = general_pdf._sampled_coverage(baseline, general_pdf._fold(_sdk_text(converted.get('sdk_markdown') or '')))
    coverage['baseline_available'] = any(page['native_chars'] for page in coverage['page_coverage'])
    if coverage['wholly_unrepresented_pages'] or (coverage['baseline_available'] and coverage['sampled_text_coverage'] < .50):
        raise pdf_markdown.PDFMarkdownError('원문 확인에서 SDK 본문의 누락이 발견됐습니다.', 'incomplete_body')
    converted.setdefault('extraction_report', {})['native_coverage'] = coverage


def extract(source, *, cancel=None, progress=None):
    """Every new/forced PDF starts with the SDK, independent of document meaning.

    No DOI lookup, network metadata or GROBID/caption repair precedes it. Native
    text validates SDK coverage afterwards; only a failed conversion/guard uses
    the existing native/OCR extractor. The caller owns persistence and export.
    """
    general_pdf._check_cancel(cancel)
    source = Path(source).resolve()
    identity = store.file_sha1(source)
    general_pdf._notify(progress, 0, 1, 'pdf_sdk', cancel)
    try:
        converted = pdf_markdown.convert(source, store.figs_dir(identity), cancel=cancel, progress=progress)
        general_pdf._check_cancel(cancel)
        _native_validation(converted, source, cancel, progress)
    except ExtractionCancelled:
        raise
    except Exception as error:
        if getattr(error, 'code', None) in ('source_changed', 'asset_conflict'):
            raise
        _unchanged(source, identity, cancel)
        seed = {'paper_id': identity, 'sha1': identity, 'source_file': str(source),
                'source': 'pdf_general', 'scope': 'general_pdf', 'meta': {'title': source.stem},
                'body_text': [], 'references': [], 'figures': [], 'tables': []}
        recovered, report = general_pdf.ensure_fulltext(seed, source, cancel=cancel, progress=progress)
        _unchanged(source, identity, cancel)
        if report.get('status') != 'failed':
            report['status'] = 'partial'
        report.setdefault('warnings', []).append(f'공개 PDF 엔진을 적용하지 못해 원문 추출을 보존했습니다: {error}')
        recovered['extraction_report'] = report
        recovered['pdf_conversion_attempt'] = pdf_markdown.engine_id()
        recovered['sha1'], recovered['source_file'] = identity, str(source)
        return recovered
    _unchanged(source, identity, cancel)
    converted['sha1'], converted['source_file'] = identity, str(source)
    converted['pdf_conversion_engine'] = pdf_markdown.engine_id()
    return converted


def current(doc):
    identity = (doc.get('pdf_conversion_engine') or
                ((doc.get('extraction_report') or {}).get('engine') or {}).get('identity') or doc.get('sdk_engine'))
    return (doc.get('source') == 'pdf_sdk' and isinstance(doc.get('sdk_markdown'), str)
            and identity == pdf_markdown.engine_id())


def upgrade_general(doc, source, *, cancel=None, progress=None):
    # Healthy structured papers never enter the general conversion engine.
    if (current(doc) or doc.get('source') not in ('pdf_general', 'pdf_sdk')
            or doc.get('pdf_conversion_attempt') == pdf_markdown.engine_id()):
        return doc
    if not pdf_markdown.available():
        return doc
    try:
        sha = store.file_sha1(source)
        converted = pdf_markdown.convert(source, store.figs_dir(sha), progress=progress, cancel=cancel)
        # Reuse the existing loss guard as a check, never as a layout algorithm.
        # A layout model excluding a whole body page must not replace its text.
        if (doc.get('source') == 'pdf_general' and
                (doc.get('source_map') or {}).get('coordinate_space') == 'pymupdf_unrotated_points'):
            from .general_pdf import _coverage_sections, _fold, _sampled_coverage
            baseline, _ = _coverage_sections(doc.get('body_text') or [],
                                             (doc.get('extraction_report') or {}).get('pages') or [])
            coverage = _sampled_coverage(baseline, _fold(_sdk_text(converted.get('sdk_markdown') or '')))
            coverage['baseline_available'] = any(page['native_chars'] for page in coverage['page_coverage'])
            if coverage['wholly_unrepresented_pages'] or (coverage['baseline_available'] and coverage['sampled_text_coverage'] < .50):
                raise pdf_markdown.PDFMarkdownError('기존 원문 검사에서 본문 누락이 발견됐습니다.', 'incomplete_body')
            converted.setdefault('extraction_report', {})['native_coverage'] = coverage
    except ExtractionCancelled:
        raise
    except Exception as error:
        if getattr(error, 'code', None) in ('source_changed', 'asset_conflict'):
            raise
        # Preserve the already validated native/OCR result on unsupported scans,
        # timeouts, missing resources or converter failure. Never label it SDK.
        fallback = copy.deepcopy(doc)
        report = fallback.setdefault('extraction_report', {})
        if report.get('status') != 'failed':
            report['status'] = 'partial'
        report.setdefault('warnings', []).append(f'공개 PDF 엔진을 적용하지 못해 기존 추출을 보존했습니다: {error}')
        fallback['pdf_conversion_attempt'] = pdf_markdown.engine_id()
        return fallback
    converted['sha1'] = sha
    converted['source_file'] = str(source)
    converted['pdf_conversion_engine'] = pdf_markdown.engine_id()
    # Source-native coverage/OCR diagnostics are provenance, never extra body or
    # stale GROBID references appended to the published converter's Markdown.
    converted['native_validation'] = copy.deepcopy(doc.get('extraction_report') or {})
    return converted
