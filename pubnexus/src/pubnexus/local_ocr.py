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


def _run(request: Path, output: Path, cancel, deadline):
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
            while True:
                _check(cancel, deadline)
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


def extract_page(page, provider: dict, *, cancel=None, timeout=45) -> tuple[list, list, list]:
    """원본 회전을 변경하지 않고 한 쪽의 unrotated 문단/단어 좌표를 반환한다."""
    if timeout <= 0:
        raise ValueError("timeout은 양수여야 합니다.")
    deadline = time.monotonic() + timeout
    _check(cancel, deadline)
    if not isinstance(provider, dict) or provider.get("engine") != "pymupdf_builtin_tesseract":
        raise LocalOCRError("지원하는 로컬 OCR provider가 아닙니다.", "dependency")
    language = str(provider.get("language", ""))
    selected = language.split("+")
    if not selected or len(set(selected)) != len(selected) or any(item not in _MODELS for item in selected):
        raise LocalOCRError("검증된 OCR 언어 모델이 필요합니다.", "dependency")
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
        if not str(work).isascii():
            raise LocalOCRError("내장 OCR에는 ASCII 임시 경로가 필요합니다. 이 기기의 TEMP 경로를 확인해 주세요.", "dependency")
        models = work / "models"
        models.mkdir()
        for item in selected:
            _check(cancel, deadline)
            source = Path(provider["tessdata"]) / (item + ".traineddata")
            if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES or _hash(source) != _MODELS[item]:
                raise LocalOCRError("로컬 OCR 언어 모델이 없거나 변경됐습니다.", "dependency")
            target = models / source.name
            shutil.copyfile(utils.long_path(source), target)
            if _hash(target) != _MODELS[item]:
                raise LocalOCRError("OCR 모델 복사 무결성을 확인하지 못했습니다.", "io")
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
        image = request.parent / "page.png"
        if image.stat().st_size > MAX_IMAGE_BYTES:
            raise LocalOCRError("OCR 그림 크기 제한을 초과했습니다.", "limit")
        with open(image, "rb") as stream:
            header = stream.read(24)
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[8:16] != b"\0\0\0\rIHDR":
            raise LocalOCRError("OCR 입력은 유효한 PNG IHDR가 있어야 합니다.")
        width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
        if (width <= 0 or height <= 0 or width * height > MAX_PIXELS or max(width, height) > MAX_SIDE
                or [width, height] != [config["width"], config["height"]]):
            raise LocalOCRError("OCR PNG 픽셀 예산/요청 크기가 올바르지 않습니다.", "limit")
        pix = fitz.Pixmap(str(image))
        if (pix.width * pix.height > MAX_PIXELS or max(pix.width, pix.height) > MAX_SIDE
                or [pix.width, pix.height] != [config["width"], config["height"]]):
            raise LocalOCRError("OCR 그림 픽셀 예산/크기가 올바르지 않습니다.", "limit")
        with fitz.open() as pdf:
            page = pdf.new_page(width=pix.width, height=pix.height)
            page.insert_image(page.rect, stream=image.read_bytes())
            textpage = page.get_textpage_ocr(language=language, dpi=72, full=True,
                                           tessdata=fitz.get_tessdata(tessdata=str(models)))
            words = page.get_text("words", textpage=textpage)
            if len(words) > MAX_WORDS:
                raise LocalOCRError("OCR 단어 예산을 초과했습니다.", "limit")
        result = {"ok": True, "words": words, "engine_version": fitz.VersionBind}
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
