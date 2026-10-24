"""Small Explorer thumbnails from PDF page 1 or an existing package thumbnail.

No conversion, extraction, shell handler, network request or persistent write.
The bounded memory cache is invalidated by original path/size/mtime identity.
"""
from functools import lru_cache
import io
from pathlib import Path
import threading
import zipfile

from . import utils

_slots = threading.BoundedSemaphore(2)
_PACKAGES = {'.docx', '.pptx', '.xlsx', '.odt', '.odp', '.ods', '.hwpx'}
_MEMBERS = ('docProps/thumbnail.jpeg', 'docProps/thumbnail.jpg', 'docProps/thumbnail.png',
            'Thumbnails/thumbnail.png', 'Preview/PrvImage.png')
_MAX_IMAGE_BYTES = 4 * 1024 * 1024


def _shrink(data):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                return None
            image.thumbnail((240, 240))
            output = io.BytesIO()
            image.convert('RGBA' if 'A' in image.getbands() else 'RGB').save(output, format='PNG')
            return output.getvalue(), 'image/png'
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


@lru_cache(maxsize=96)
def _read(path, size, modified):
    source = Path(path)
    if source.suffix.lower() == '.pdf':
        import fitz
        with fitz.open(utils.long_path(source)) as pdf:
            if not len(pdf) or pdf.needs_pass:
                return None
            page = pdf[0]
            length = max(page.rect.width, page.rect.height)
            if length <= 0:
                return None
            scale = min(1.0, 240 / length)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            result = pix.tobytes('png'), 'image/png'
    else:
        with zipfile.ZipFile(utils.long_path(source)) as package:
            if len(package.infolist()) > 10000:
                return None
            result = None
            for name in _MEMBERS:
                try:
                    info = package.getinfo(name)
                except KeyError:
                    continue
                if not 0 < info.file_size <= _MAX_IMAGE_BYTES or info.flag_bits & 1:
                    continue
                with package.open(info) as stream:
                    data = stream.read(_MAX_IMAGE_BYTES + 1)
                if len(data) <= _MAX_IMAGE_BYTES:
                    result = _shrink(data)
                if result:
                    break
    current = source.stat()
    return result if (size, modified) == (current.st_size, current.st_mtime_ns) else None


def read(source: str | Path):
    """Return PNG bytes/MIME, or None so the list keeps its ordinary file icon."""
    source = Path(source)
    if source.suffix.lower() not in _PACKAGES | {'.pdf'}:
        return None
    if not _slots.acquire(timeout=.2):
        return None
    try:
        before = source.stat()
        if not source.is_file() or before.st_size > 512 * 1024 * 1024:
            return None
        return _read(str(source.resolve()), before.st_size, before.st_mtime_ns)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        return None
    finally:
        _slots.release()
