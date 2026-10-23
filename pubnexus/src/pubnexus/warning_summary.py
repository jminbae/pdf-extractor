"""Readable display summaries; canonical extraction warnings stay unchanged."""
from __future__ import annotations

import re


def _ranges(numbers):
    values = sorted(set(numbers))
    ranges = []
    for value in values:
        if ranges and value == ranges[-1][-1] + 1:
            ranges[-1].append(value)
        else:
            ranges.append([value])
    return ', '.join(str(group[0]) if len(group) == 1 else f'{group[0]}–{group[-1]}' for group in ranges)


def summarize_warnings(warnings, pages=None) -> list[str]:
    """Use page-level evidence to repair legacy OCR display contradictions."""
    raw = list(dict.fromkeys(str(value).strip() for value in warnings or [] if str(value).strip()))
    context = {p['page']: p for p in pages or [] if isinstance(p, dict) and type(p.get('page')) is int}
    failed = set()
    for text in raw:
        match = re.match(r'^(\d+)쪽\s+.*(?:OCR|Tesseract).*실패', text)
        if match:
            failed.add(int(match[1]))
    recognized, empty, unrun, other = set(), set(), {}, []
    generic = None
    for text in raw:
        if text.startswith('OCR로 읽은 텍스트입니다'):
            generic = text
            continue
        match = re.match(r'^(\d+)쪽\s+(.+)$', text)
        status_message = match and match[2].startswith(('로컬 OCR', '텍스트층 없음', 'Tesseract OCR',
                                                        'Tesseract 미설치', 'OCR 실행', 'OCR 미실행', 'OCR 결과'))
        if status_message:
            number, message = int(match[1]), match[2]
            page = context.get(number, {})
            if page.get('status') == 'blank' and number not in failed:
                continue
            if page.get('status') == 'ocr_extracted':
                recognized.add(number)
                continue
            if page.get('ocr_performed') and number not in failed:
                empty.add(number)
                continue
            if number in failed and '텍스트층 없음' in message:
                continue
            if '미실행' in message:
                unrun.setdefault(message, []).append(number)
                continue
        other.append(text)
    recognized.update(number for number, page in context.items() if page.get('status') == 'ocr_extracted')
    result = []
    if recognized:
        result.append(f'OCR로 읽은 쪽: {_ranges(recognized)}. 오인식·누락을 원본과 확인하세요.')
    elif generic and not context:
        result.append(generic)
    if empty:
        result.append(f'OCR을 실행했으나 문자를 찾지 못한 쪽: {_ranges(empty)} (빈 쪽·그림 여부 확인)')
    for message, numbers in unrun.items():
        result.append(f'{_ranges(numbers)}쪽 {message}')
    return list(dict.fromkeys(result + other))
