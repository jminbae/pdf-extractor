"""Prepare visible embedded pixels for the existing local Tesseract boundary.

The original image remains an asset. OCR coordinates describe the cropped OCR
bitmap before host shape transforms, never invented slide/PDF coordinates.
"""
from __future__ import annotations

import hashlib
import io
import math

from PIL import Image, UnidentifiedImageError

from .local_ocr import LocalOCRError, MAX_IMAGE_BYTES, MAX_PIXELS, MAX_SIDE

MAX_SOURCE_PIXELS = 32_000_000


def prepare_picture(blob: bytes, crop=(0, 0, 0, 0)) -> dict:
    if len(blob) > MAX_IMAGE_BYTES:
        raise LocalOCRError('그림 OCR 입력 크기 예산을 초과했습니다.', 'limit')
    if len(crop) != 4 or not all(type(value) in (int, float) and math.isfinite(value) for value in crop):
        raise LocalOCRError('PPTX 그림의 표시 자르기 값이 올바르지 않습니다.')
    try:
        with Image.open(io.BytesIO(blob)) as image:
            width, height = image.size
            if width * height > MAX_SOURCE_PIXELS:
                raise LocalOCRError('그림 OCR 원본 픽셀 예산을 초과했습니다.', 'limit')
            left, top, right, bottom = crop
            box = [left * width, top * height, (1 - right) * width, (1 - bottom) * height]
            cropped_width, cropped_height = box[2] - box[0], box[3] - box[1]
            if (min(cropped_width, cropped_height) <= 0 or
                    cropped_width * cropped_height > MAX_SOURCE_PIXELS or
                    max(cropped_width, cropped_height) > MAX_SOURCE_PIXELS):
                raise LocalOCRError('PPTX 그림의 표시 자르기 픽셀 예산을 초과했습니다.', 'limit')
            box = [round(value) for value in box]
            if box[2] <= box[0] or box[3] <= box[1]:
                raise LocalOCRError('PPTX 그림의 표시 자르기가 한 픽셀보다 작습니다.', 'limit')
            # RGBA makes any negative crop's exposed margin transparent; flatten
            # against white rather than introducing black OCR borders.
            cropped = image.convert('RGBA').crop(tuple(box))
            pixels = Image.new('RGB', cropped.size, 'white')
            pixels.paste(cropped, mask=cropped.getchannel('A'))
            scale = min(1, math.sqrt(MAX_PIXELS / max(pixels.width * pixels.height, 1)),
                        MAX_SIDE / max(pixels.size))
            if scale < 1:
                pixels.thumbnail((max(1, int(pixels.width * scale)), max(1, int(pixels.height * scale))),
                                 Image.Resampling.LANCZOS)
            output = io.BytesIO()
            pixels.save(output, format='PNG')
            png = output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise LocalOCRError(f'표시 그림을 로컬 OCR용으로 읽지 못했습니다: {error}', 'unsupported') from error
    if len(png) > MAX_IMAGE_BYTES:
        raise LocalOCRError('그림 OCR PNG 크기 예산을 초과했습니다.', 'limit')
    return {'id': hashlib.sha256(png).hexdigest(), 'png': png,
            'image_sha256': hashlib.sha256(blob).hexdigest(), 'source_image_size_px': [width, height],
            'image_source_crop_px': box, 'image_crop_fraction': list(crop),
            'ocr_image_size_px': list(pixels.size), 'ocr_orientation': 'source_image_before_shape_transform'}


def lines(record):
    """Keep the OCR engine's line/word order and unmodified recognized strings."""
    groups = {}
    for word in record['words']:
        groups.setdefault((word[5], word[6]), []).append(word)
    for group in groups.values():
        def bounded(word):
            return 0 <= word[0] < word[2] <= record['width'] and 0 <= word[1] < word[3] <= record['height']
        bounds = [min(w[0] for w in group), min(w[1] for w in group),
                  max(w[2] for w in group), max(w[3] for w in group)]
        located = all(bounded(word) for word in group)
        yield ' '.join(word[4] for word in group), {
            'method': 'tesseract', 'coordinate_space': 'ocr_image_pixels',
            'image_bbox_px': bounds if located else None, 'raw_ocr_bbox_px': bounds,
            'geometry_status': 'image_bounded' if located else 'unlocated_ocr',
            'words': [{'text': word[4], 'bbox_px': word[:4] if bounded(word) else None,
                       'raw_ocr_bbox_px': word[:4], 'confidence': None} for word in group],
            'confidence': None, 'language': record['language'], 'engine': record['engine'],
            'engine_identity': record['engine_identity'], 'engine_version': record['engine_version'],
        }
