"""검증된 로컬 tessdata와 MuPDF 내장 Tesseract만 쓰는 격리 OCR."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

import fitz

from . import utils
from .external_documents import _stop_owned_process

MAX_PIXELS = 8_000_000
MAX_SIDE = 4096
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_WORDS = 100_000
MAX_BATCH_IMAGES = 500
MAX_BATCH_BYTES = 256 * 1024 * 1024
_MODELS = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "kor": "6b85e11d9bbf07863b97b3523b1b112844c43e713df8b66418a081fd1060b3b2",
}


class LocalOCRError(RuntimeError):
    def __init__(self, message: str, code="ocr"):
        super().__init__(message)
        self.code = code


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(utils.long_path(path), "rb") as stream:
        while chunk := stream.read(256 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_models(folder: Path) -> list[dict]:
    models = []
    for language in ("kor", "eng"):
        path = folder / (language + ".traineddata")
        if path.is_file() and 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            digest = _hash(path)
            if digest == _MODELS[language]:
                models.append({"language": language, "path": str(path.resolve()),
                               "sha256": digest, "bytes": path.stat().st_size})
    return models


def discover() -> dict | None:
    """모델을 받지 않는다. 공식 고정 모델이 이미 있을 때만 provider를 반환한다."""
    override = os.environ.get("PUBNEXUS_TESSDATA")
    roots = [Path(override)] if override else []
    if not override:
        if os.environ.get("TESSDATA_PREFIX"):
            roots.append(Path(os.environ["TESSDATA_PREFIX"]))
        roots.extend([Path(sys.executable).parent / "engines" / "tessdata",
                      Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "engines" / "tessdata"])
        settings = Path.home() / ".ai-workspace" / "paths.md"
        if settings.is_file():
            match = re.search(r"^cache_root:\s*(.+)$", settings.read_text(encoding="utf-8-sig"), re.M)
            if match:
                roots.append(Path(match.group(1).strip()) / "260725 PDF 추출" / "engines" / "tessdata")
    for folder in roots:
        try:
            models = _verified_models(folder)
        except OSError:
            continue
        if models:
            language = "+".join(model["language"] for model in models)
            fingerprint = hashlib.sha256(json.dumps([fitz.VersionBind, models], sort_keys=True).encode()).hexdigest()
            return {"engine": "pymupdf_builtin_tesseract", "tessdata": str(folder.resolve()),
                    "language": language, "identity": f"pymupdf-{fitz.VersionBind}:{fingerprint}",
                    "models": models, "model_license": "Apache-2.0"}
    return None


def _check(cancel, deadline):
    if cancel is not None and cancel():
        from .general_pdf import ExtractionCancelled
        raise ExtractionCancelled("OCR이 취소되었습니다.")
    if time.monotonic() >= deadline:
        raise LocalOCRError("로컬 OCR 제한 시간을 초과했습니다.", "timeout")


def _run(request: Path, output: Path, cancel, deadline, *, on_event=None, item_timeout=None):
    arguments = ([sys.executable, "--ocr-worker", str(request)] if getattr(sys, "frozen", False) else
                 [sys.executable, "-B", "-m", "pubnexus.local_ocr", "--worker", str(request)])
    environment = os.environ.copy()
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PATH="",
                       TESSDATA_PREFIX=str(request.parent / "models"))
    if not getattr(sys, "frozen", False):
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    with open(request.parent / "stdout.log", "wb") as stdout, open(request.parent / "stderr.log", "wb") as stderr:
        _check(cancel, deadline)
        process = subprocess.Popen(arguments, cwd=str(request.parent), env=environment,
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            position, last_activity = 0, time.monotonic()
            while True:
                _check(cancel, deadline)
                events = request.parent / 'events.jsonl'
                if on_event and events.is_file():
                    if events.stat().st_size > MAX_OUTPUT_BYTES:
                        raise LocalOCRError('OCR 진행 출력 예산을 초과했습니다.', 'limit')
                    with events.open('rb') as stream:
                        stream.seek(position)
                        while line := stream.readline(MAX_OUTPUT_BYTES + 1):
                            if not line.endswith(b'\n'):
                                break
                            on_event(json.loads(line))
                            position = stream.tell()
                            last_activity = time.monotonic()
                if item_timeout is not None and time.monotonic() - last_activity > item_timeout:
                    raise LocalOCRError('로컬 OCR 그림 한 건의 제한 시간을 초과했습니다.', 'timeout')
                if any((request.parent / name).stat().st_size > 65536 for name in ("stdout.log", "stderr.log")):
                    raise LocalOCRError("OCR 진단 출력 예산을 초과했습니다.", "limit")
                if output.exists() and output.stat().st_size > MAX_OUTPUT_BYTES:
                    raise LocalOCRError("OCR 결과 출력 예산을 초과했습니다.", "limit")
                if process.poll() is not None:
                    break
                time.sleep(0.05)
        finally:
            _stop_owned_process(process)
    if not output.is_file() or output.stat().st_size > MAX_OUTPUT_BYTES:
        raise LocalOCRError("OCR worker 결과가 없거나 출력 예산을 초과했습니다.")
    record = json.loads(output.read_text(encoding="utf-8"))
    if not record.get("ok") or process.returncode:
        raise LocalOCRError(str(record.get("error") or "OCR worker가 실패했습니다."), record.get("code", "ocr"))
    return record


def _selected_models(provider):
    if not isinstance(provider, dict) or provider.get('engine') != 'pymupdf_builtin_tesseract':
        raise LocalOCRError('지원하는 로컬 OCR provider가 아닙니다.', 'dependency')
    language = str(provider.get('language', ''))
    selected = language.split('+')
    if not selected or len(set(selected)) != len(selected) or any(item not in _MODELS for item in selected):
        raise LocalOCRError('검증된 OCR 언어 모델이 필요합니다.', 'dependency')
    return language, selected


def _copy_models(work, provider, selected, cancel, deadline):
    if not str(work).isascii():
        raise LocalOCRError('내장 OCR에는 ASCII 임시 경로가 필요합니다. 이 기기의 TEMP 경로를 확인해 주세요.', 'dependency')
    models = work / 'models'
    models.mkdir()
    for item in selected:
        _check(cancel, deadline)
        source = Path(provider['tessdata']) / (item + '.traineddata')
        if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES or _hash(source) != _MODELS[item]:
            raise LocalOCRError('로컬 OCR 언어 모델이 없거나 변경됐습니다.', 'dependency')
        target = models / source.name
        shutil.copyfile(utils.long_path(source), target)
        if _hash(target) != _MODELS[item]:
            raise LocalOCRError('OCR 모델 복사 무결성을 확인하지 못했습니다.', 'io')


def _png_size(data):
    if (len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[8:16] != b'\0\0\0\rIHDR'):
        raise LocalOCRError('OCR 입력은 유효한 PNG IHDR가 있어야 합니다.')
    width, height = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
        raise LocalOCRError('OCR PNG 픽셀 예산이 올바르지 않습니다.', 'limit')
    return width, height


def extract_images(images: dict[str, bytes], provider: dict, *, cancel=None, timeout=900,
                   progress=None) -> dict:
    """OCR bounded PNGs in one owned worker; copy verified models once per batch.

    Results are image-pixel words, never PDF/slide coordinates. Completed image
    records survive a later OCR error; cancellation always propagates.
    """
    if timeout <= 0:
        raise ValueError('timeout은 양수여야 합니다.')
    deadline = time.monotonic() + timeout
    _check(cancel, deadline)
    language, selected = _selected_models(provider)
    if (not images or len(images) > MAX_BATCH_IMAGES or
            sum(len(data) for data in images.values()) > MAX_BATCH_BYTES):
        raise LocalOCRError('OCR 그림 묶음 예산을 초과했습니다.', 'limit')
    entries, results = [], {}
    for key, data in images.items():
        _check(cancel, deadline)
        if not re.fullmatch(r'[a-f0-9]{64}', key) or len(data) > MAX_IMAGE_BYTES:
            raise LocalOCRError('OCR 그림 키 또는 크기 제한이 올바르지 않습니다.', 'limit')
        width, height = _png_size(data)
        entries.append({'id': key, 'width': width, 'height': height})

    def receive(event):
        if event.get('state') != 'complete':
            return
        key, record = event.get('id'), event.get('result')
        if key not in images or not isinstance(record, dict):
            raise LocalOCRError('OCR 그림 결과 주소가 올바르지 않습니다.')
        results[key] = record
        if progress:
            progress(key)

    with tempfile.TemporaryDirectory(prefix='pubnexus-image-ocr-') as temporary:
        work = Path(temporary)
        _copy_models(work, provider, selected, cancel, deadline)
        for index, data in enumerate(images.values(), 1):
            _check(cancel, deadline)
            (work / f'image-{index:06d}.png').write_bytes(data)
        request, output = work / 'request.json', work / 'result.json'
        request.write_text(json.dumps({'language': language, 'images': entries}), encoding='utf-8')
        try:
            record = _run(request, output, cancel, deadline, on_event=receive, item_timeout=45)
            for entry in record.get('images') or []:
                if entry.get('id') not in results:
                    receive({'state': 'complete', **entry})
        except LocalOCRError as error:
            for key in images:
                results.setdefault(key, {'ok': False, 'code': error.code, 'error': str(error)[:1000]})
    _check(cancel, deadline)
    for entry in entries:
        record = results.get(entry['id'])
        if not isinstance(record, dict):
            raise LocalOCRError('OCR 그림 결과가 없습니다.')
        if not record.get('ok'):
            continue
        words = record.get('words')
        if not isinstance(words, list) or len(words) > MAX_WORDS:
            results[entry['id']] = {'ok': False, 'code': 'limit', 'error': 'OCR 단어 결과 계약/예산이 올바르지 않습니다.'}
            continue
        invalid = False
        for word in words:
            if (not isinstance(word, list) or len(word) != 8 or not isinstance(word[4], str)
                    or not all(type(v) in (int, float) and math.isfinite(v) for v in word[:4])
                    or not word[0] < word[2] or not word[1] < word[3]
                    or not all(type(word[index]) is int and word[index] >= 0 for index in (5, 6, 7))):
                invalid = True
                break
        if invalid:
            results[entry['id']] = {'ok': False, 'code': 'invalid_output',
                                    'error': 'OCR 단어/그림 좌표 결과가 올바르지 않습니다.'}
            continue
        record.update(width=entry['width'], height=entry['height'], language=language,
                      engine=provider['engine'], engine_identity=provider['identity'])
    return results


def extract_page(page, provider: dict, *, cancel=None, timeout=45) -> tuple[list, list, list]:
    """원본 회전을 변경하지 않고 한 쪽의 unrotated 문단/단어 좌표를 반환한다."""
    if timeout <= 0:
        raise ValueError("timeout은 양수여야 합니다.")
    deadline = time.monotonic() + timeout
    _check(cancel, deadline)
    language, selected = _selected_models(provider)
    rect = page.rect * page.derotation_matrix
    scale = min(150 / 72, math.sqrt(MAX_PIXELS / max(rect.get_area(), 1)),
                MAX_SIDE / max(rect.width, rect.height, 1)) * 0.999
    pix = page.get_pixmap(matrix=page.derotation_matrix * fitz.Matrix(scale, scale),
                          colorspace=fitz.csRGB, alpha=False)
    if pix.width * pix.height > MAX_PIXELS or max(pix.width, pix.height) > MAX_SIDE:
        raise LocalOCRError("OCR 래스터 픽셀 예산을 초과했습니다.", "limit")
    _check(cancel, deadline)
    with tempfile.TemporaryDirectory(prefix="pubnexus-local-ocr-") as temporary:
        work = Path(temporary)
        _copy_models(work, provider, selected, cancel, deadline)
        image = work / "page.png"
        pix.save(str(image))
        if image.stat().st_size > MAX_IMAGE_BYTES:
            raise LocalOCRError("OCR 그림 크기 제한을 초과했습니다.", "limit")
        request, output = work / "request.json", work / "result.json"
        request.write_text(json.dumps({"language": language, "width": pix.width, "height": pix.height}), encoding="utf-8")
        record = _run(request, output, cancel, deadline)
    _check(cancel, deadline)
    words = record.get("words")
    if not isinstance(words, list) or len(words) > MAX_WORDS:
        raise LocalOCRError("OCR 단어 결과 계약/예산이 올바르지 않습니다.", "limit")
    groups = {}
    for word in words:
        _check(cancel, deadline)
        if (not isinstance(word, list) or len(word) != 8 or not isinstance(word[4], str)
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in word[:4])
                or not 0 <= word[0] < word[2] <= pix.width + 1
                or not 0 <= word[1] < word[3] <= pix.height + 1):
            raise LocalOCRError("OCR 단어/좌표 결과가 올바르지 않습니다.")
        groups.setdefault((word[5], word[6]), []).append(word)
    paragraphs, mapped = [], []
    for index, group in enumerate(groups.values(), 1):
        converted = []
        for word in group:
            box = [rect.x0 + word[0] * rect.width / pix.width, rect.y0 + word[1] * rect.height / pix.height,
                   rect.x0 + word[2] * rect.width / pix.width, rect.y0 + word[3] * rect.height / pix.height]
            converted.append({"text": word[4], "bbox": box, "confidence": None})
        box = [min(w["bbox"][0] for w in converted), min(w["bbox"][1] for w in converted),
               max(w["bbox"][2] for w in converted), max(w["bbox"][3] for w in converted)]
        block_id = f"pdf-p{page.number + 1:06d}-ocr{index:06d}"
        origin = {"page": page.number + 1, "block_id": block_id, "bbox": box, "method": "tesseract",
                  "language": language, "confidence": None, "words": converted,
                  "engine": "pymupdf_builtin_tesseract", "engine_identity": provider["identity"]}
        paragraphs.append({"id": block_id, "text": " ".join(w["text"] for w in converted), "source": origin,
                           "cited_refs": [], "cited_keys": [], "refs_figure": [], "refs_table": []})
        mapped.append({"id": block_id, **origin})
    warnings = ["OCR로 읽은 텍스트입니다. 오인식/누락을 원본과 확인하세요. 이 엔진은 단어 신뢰도 점수를 제공하지 않습니다."]
    if "kor" not in selected:
        warnings.append("한국어 OCR 모델이 없어 설치된 영어 모델만 사용했습니다.")
    return paragraphs, mapped, warnings


def _recognize_image(image, config, models, language):
    if image.stat().st_size > MAX_IMAGE_BYTES:
        raise LocalOCRError('OCR 그림 크기 제한을 초과했습니다.', 'limit')
    with image.open('rb') as stream:
        width, height = _png_size(stream.read(24))
    if [width, height] != [config['width'], config['height']]:
        raise LocalOCRError('OCR PNG 요청 크기가 올바르지 않습니다.', 'limit')
    pix = fitz.Pixmap(str(image))
    if [pix.width, pix.height] != [width, height]:
        raise LocalOCRError('OCR 그림 픽셀 크기가 올바르지 않습니다.', 'limit')
    with fitz.open() as pdf:
        page = pdf.new_page(width=pix.width, height=pix.height)
        page.insert_image(page.rect, stream=image.read_bytes())
        textpage = page.get_textpage_ocr(language=language, dpi=72, full=True,
                                       tessdata=fitz.get_tessdata(tessdata=str(models)))
        words = page.get_text('words', textpage=textpage)
        if len(words) > MAX_WORDS:
            raise LocalOCRError('OCR 단어 예산을 초과했습니다.', 'limit')
    return {'ok': True, 'words': words, 'engine_version': fitz.VersionBind}


def worker_cli(request_json: str | Path) -> int:
    """앱 early CLI hook에서 호출. 고정된 private 파일 이름만 사용한다."""
    request = Path(request_json).resolve(strict=True)
    output = request.parent / "result.json"
    try:
        if request.stat().st_size > 65536:
            raise LocalOCRError("OCR 요청 크기 제한을 초과했습니다.", "limit")
        config = json.loads(request.read_text(encoding="utf-8"))
        language = config["language"]
        if not isinstance(language, str) or any(item not in _MODELS for item in language.split("+")):
            raise LocalOCRError("지원하지 않는 OCR 언어입니다.", "dependency")
        models = request.parent / "models"
        for item in language.split("+"):
            model = models / (item + ".traineddata")
            if not model.is_file() or model.stat().st_size > MAX_IMAGE_BYTES or _hash(model) != _MODELS[item]:
                raise LocalOCRError("검증된 로컬 OCR 모델이 필요합니다.", "dependency")
        if 'images' in config:
            images = config['images']
            if (not isinstance(images, list) or not 0 < len(images) <= MAX_BATCH_IMAGES or
                    any(not isinstance(item, dict) or not isinstance(item.get('id'), str) or
                        not re.fullmatch(r'[a-f0-9]{64}', item['id']) for item in images) or
                    len({item['id'] for item in images}) != len(images)):
                raise LocalOCRError('OCR 그림 묶음 요청이 올바르지 않습니다.', 'limit')
            if sum((request.parent / f'image-{index:06d}.png').stat().st_size
                   for index in range(1, len(images) + 1)) > MAX_BATCH_BYTES:
                raise LocalOCRError('OCR 그림 묶음 크기 예산을 초과했습니다.', 'limit')
            records = []
            with (request.parent / 'events.jsonl').open('a', encoding='utf-8', buffering=1) as events:
                for index, entry in enumerate(images, 1):
                    events.write(json.dumps({'state': 'started', 'id': entry['id']}) + '\n')
                    try:
                        recognized = _recognize_image(request.parent / f'image-{index:06d}.png', entry, models, language)
                    except Exception as error:
                        recognized = {'ok': False, 'error': f'{type(error).__name__}: {error}'[:1000],
                                      'code': getattr(error, 'code', 'ocr')}
                    record = {'id': entry['id'], 'result': recognized}
                    records.append(record)
                    events.write(json.dumps({'state': 'complete', **record}, ensure_ascii=False) + '\n')
            result = {'ok': True, 'images': records, 'engine_version': fitz.VersionBind}
        else:
            result = _recognize_image(request.parent / 'page.png', config, models, language)
        code = 0
    except Exception as error:
        result = {"ok": False, "error": f"{type(error).__name__}: {error}", "code": getattr(error, "code", "ocr")}
        code = 2
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        encoded = b'{"ok":false,"code":"limit","error":"OCR output budget exceeded"}'
        code = 2
    temporary = output.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, output)
    return code


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("usage: python -m pubnexus.local_ocr --worker request.json")
    raise SystemExit(worker_cli(sys.argv[2]))
