"""정본을 보존하면서 원본 옆에 휴대 가능한 Markdown과 그림을 내보낸다."""
from __future__ import annotations

import copy
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import render, store, utils


@dataclass(frozen=True)
class ExportResult:
    markdown_path: Path
    assets_dir: Path
    manifest_path: Path
    revision: str
    unchanged: bool = False


class ExportError(RuntimeError):
    """저장이 완료되지 않았다. 호출부는 완료 상태로 표시하면 안 된다."""


class ExportConflictError(ExportError):
    """사용자가 편집했거나 소유권을 확인할 수 없어 보존한 결과물."""


_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)", re.I)


def _truncate_name(text: str, units: int) -> str:
    # stdlib만 사용한다. NFC 후 결합부호/emoji modifier/ZWJ/tag/국기 쌍을
    # 한 덩어리로 잡아 UTF-16 code unit 예산 안의 완전한 덩어리만 남긴다.
    clusters: list[str] = []
    for char in text:
        cp = ord(char)
        extend = (unicodedata.category(char).startswith("M") or char in "\u200c\u200d"
                  or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F)
        regional = 0x1F1E6 <= cp <= 0x1F1FF
        pair_flag = (regional and clusters and len(clusters[-1]) == 1
                     and 0x1F1E6 <= ord(clusters[-1]) <= 0x1F1FF)
        # Indic virama 뒤 자음도 앞 덩어리에 붙인다(보수적으로 더 길게 묶음).
        virama = bool(clusters and "VIRAMA" in unicodedata.name(clusters[-1][-1], ""))
        if clusters and (extend or clusters[-1].endswith("\u200d") or pair_flag or virama):
            clusters[-1] += char
        else:
            clusters.append(char)
    out = []
    used = 0
    for cluster in clusters:
        size = len(cluster.encode("utf-16-le")) // 2
        if used + size > units:
            break
        out.append(cluster)
        used += size
    return "".join(out).rstrip(" .")


def _filename(stem: str, short_id: str, *, suffix: bool = False) -> str:
    stem = _BAD_NAME.sub("_", unicodedata.normalize("NFC", stem)).strip(" .") or "document"
    if _RESERVED.match(stem):
        stem = "_" + stem
    suffix = suffix or len((stem + ".md").encode("utf-16-le")) // 2 > 180
    tail = f"--{short_id}.md" if suffix else ".md"
    return (_truncate_name(stem, 180 - len(tail)) or "document") + tail


def _name_exists(folder: Path, name: str) -> bool:
    if utils.path_exists(folder / name):
        return True
    key = unicodedata.normalize("NFC", name).casefold()
    return folder.is_dir() and any(
        unicodedata.normalize("NFC", p.name).casefold() == key for p in folder.iterdir())


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(utils.long_path(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_asset(original: Path, target: Path, digest: str) -> None:
    """내용 주소 자산은 교체하지 않는다. 복사가 끝난 파일만 공개한다."""
    if utils.path_exists(target):
        if _digest(target) != digest:
            raise ExportConflictError("외부에서 변경한 그림 파일을 덮어쓰지 않았습니다.")
        return
    fd, name = tempfile.mkstemp(prefix=".asset-", dir=utils.long_path(target.parent))
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(utils.long_path(original), utils.long_path(temporary))
        if _digest(temporary) != digest:
            raise ExportError("읽는 동안 그림이 변경되어 저장하지 않았습니다.")
        try:
            if os.name == "nt":
                os.rename(utils.long_path(temporary), utils.long_path(target))
            else:
                os.link(temporary, target)
        except FileExistsError:
            if _digest(target) != digest:
                raise ExportConflictError("동시에 변경된 그림 파일을 보존했습니다.")
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def _write_markdown(path: Path, data: bytes, expected_hash: str | None) -> None:
    fd, name = tempfile.mkstemp(prefix=".markdown-", dir=utils.long_path(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        exists = utils.path_exists(path)
        if exists and (expected_hash is None or _digest(path) != expected_hash):
            raise ExportConflictError("저장 중 변경된 Markdown을 덮어쓰지 않았습니다.")
        if expected_hash is not None:
            os.replace(utils.long_path(temporary), utils.long_path(path))
        elif os.name == "nt":
            os.rename(utils.long_path(temporary), utils.long_path(path))
        else:
            os.link(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def _validate_record(record: dict, source_key: str) -> None:
    if (not isinstance(record, dict) or record.get("version") != 1
            or record.get("source_key") != source_key):
        raise ExportConflictError("내보내기 관리 정보가 변경되었습니다.")
    name = record.get("markdown")
    if (not isinstance(name, str) or Path(name).name != name or _BAD_NAME.search(name)
            or not name.endswith(".md")
            or not isinstance(record.get("markdown_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record.get("markdown_sha256", ""))):
        raise ExportConflictError("내보내기 파일 관리 정보를 확인할 수 없습니다.")


def _read_record(path: Path) -> dict:
    try:
        return utils.read_json(path)
    except (ValueError, UnicodeError) as exc:
        raise ExportConflictError("손상된 내보내기 관리 정보를 보존했습니다.") from exc


def _write_metadata(path: Path, record: dict) -> None:
    try:
        utils.write_json(path, record)
    except OSError as exc:
        raise ExportError(f"관리 정보 저장 실패: {path.name}: {exc}") from exc


def _recover_pending(dest: Path, manifest: Path, pending: Path, source_key: str) -> None:
    if not utils.path_exists(pending):
        return
    journal = _read_record(pending)
    if not isinstance(journal, dict) or "next" not in journal or "previous_hash" not in journal:
        raise ExportConflictError("손상된 내보내기 복구 정보를 보존했습니다.")
    record = journal["next"]
    _validate_record(record, source_key)
    path = dest / record["markdown"]
    current = _digest(path) if utils.path_exists(path) else None
    if current == record["markdown_sha256"]:
        _write_metadata(manifest, record)
    elif current != journal["previous_hash"]:
        raise ExportConflictError("저장 실패 후 외부에서 편집한 Markdown을 보존했습니다.")
    pending.unlink()


def export_document(doc: dict, source_path: str | Path, *,
                    output_dir: str | Path | None = None,
                    asset_base: str | Path | None = None) -> ExportResult:
    """`source.parent/Markdown`에 저장한다. 입력 문서와 원본은 변경하지 않는다.

    그림 절대/상대경로는 asset_base 안에 있어야 하며, 생략하면 앱 sha1 저장소 또는 원본
    부모를 쓴다. 정본 JSON 저장은 호출부가 기존 store API로 계속 담당한다.
    """
    try:
        return _export_document(doc, source_path, output_dir=output_dir, asset_base=asset_base)
    except OSError as exc:
        raise ExportError(f"내보내기 저장 실패: {exc}") from exc


def _export_document(doc: dict, source_path: str | Path, *,
                     output_dir: str | Path | None,
                     asset_base: str | Path | None) -> ExportResult:
    source = Path(source_path).resolve(strict=True)
    source_key = os.path.normcase(str(source))
    short_id = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:16]
    dest = Path(output_dir).resolve() if output_dir is not None else source.parent / "Markdown"
    assets = dest / "assets" / short_id
    if not assets.resolve().is_relative_to(dest):
        raise ExportError("그림 저장 폴더가 Markdown 폴더 밖으로 연결되어 있습니다.")
    manifest = assets / "manifest.json"
    pending = assets / "pending.json"
    _recover_pending(dest, manifest, pending, source_key)
    previous = _read_record(manifest) if utils.path_exists(manifest) else None
    if previous is not None:
        _validate_record(previous, source_key)
    path = dest / (previous["markdown"] if previous else _filename(source.stem, short_id))
    if previous and utils.path_exists(path) and _digest(path) != previous.get("markdown_sha256"):
        raise ExportConflictError("외부에서 편집한 Markdown을 덮어쓰지 않았습니다.")
    if previous is None and _name_exists(dest, path.name):
        path = dest / _filename(source.stem, short_id, suffix=True)
        if _name_exists(dest, path.name):
            raise ExportConflictError("같은 이름의 기존 Markdown을 덮어쓰지 않았습니다.")
    expected_hash = _digest(path) if previous and utils.path_exists(path) else None
    if previous and expected_hash is not None and expected_hash != previous["markdown_sha256"]:
        raise ExportConflictError("외부에서 편집한 Markdown을 덮어쓰지 않았습니다.")
    assets.mkdir(parents=True, exist_ok=True)
    exported = copy.deepcopy(doc)
    base = Path(asset_base) if asset_base is not None else (
        store.figs_dir(str(doc["sha1"])) if doc.get("sha1") else source.parent)
    base = base.resolve()
    asset_hashes = {}
    sdk_destinations = {}
    for figure in list(exported.get("figures") or []) + list(exported.get("tables") or []):
        image = figure.get("image")
        if not image:
            continue
        original = Path(image)
        try:
            original = (original if original.is_absolute() else base / original).resolve(strict=True)
            if not original.is_relative_to(base):
                raise ExportError("그림 경로가 지정된 그림 폴더 밖에 있습니다.")
            digest = _digest(original)
            extension = original.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
                extension = ".bin"
            target = assets / (digest + extension)
            _copy_asset(original, target, digest)
        except OSError as exc:
            raise ExportError(f"그림 저장 실패: {original.name}: {exc}") from exc
        figure["image"] = target.relative_to(dest).as_posix()
        sdk_destinations[str(image)] = figure['image']
        if figure.get('markdown'):
            figure['markdown'] = figure['markdown'].replace('](' + str(image) + ')', '](' + figure['image'] + ')')
        asset_hashes[target.name] = digest
    if isinstance(exported.get('sdk_markdown'), str):
        text = exported['sdk_markdown']
        edits, referenced = [], set()
        for span in exported.get('sdk_assets') or []:
            start, end, image = span.get('start'), span.get('end'), span.get('image')
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                    or image not in sdk_destinations or text[start:end] != image):
                raise ExportError('SDK 그림 주소의 위치 정보를 확인하지 못했습니다.')
            edits.append((start, end, sdk_destinations[image]))
            referenced.add(image)
        if referenced != set(sdk_destinations):
            raise ExportError('SDK 그림과 Markdown 주소가 일치하지 않아 저장하지 않았습니다.')
        edits.sort()
        if any(left[1] > right[0] for left, right in zip(edits, edits[1:])):
            raise ExportError('SDK 그림 주소의 위치가 겹쳐 저장하지 않았습니다.')
        for start, end, destination in reversed(edits):
            text = text[:start] + destination + text[end:]
        exported['sdk_markdown'] = text
    markdown = render.to_markdown(exported).encode("utf-8")
    markdown_hash = hashlib.sha256(markdown).hexdigest()
    old_hash = _digest(path) if utils.path_exists(path) else None
    if old_hash != expected_hash:
        raise ExportConflictError("저장 중 외부에서 변경된 Markdown을 보존했습니다.")
    unchanged = old_hash == markdown_hash
    revision = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8")).hexdigest()
    record = {"version": 1, "source_key": source_key,
              "markdown": path.name, "revision": revision,
              "markdown_sha256": markdown_hash,
              "assets": asset_hashes,
              "source_map": {
                  "document": copy.deepcopy(doc.get("source_map")),
                  "sdk_blocks": [copy.deepcopy({k: b.get(k) for k in ('id', 'start', 'end', 'source')})
                                 for b in doc.get('sdk_blocks') or []],
                  "sdk_markdown_basis": ({"content": "canonical_sdk_markdown",
                      "offset_unit": "python_unicode_codepoints",
                      "sha256": hashlib.sha256(doc['sdk_markdown'].encode('utf-8')).hexdigest()}
                      if isinstance(doc.get('sdk_markdown'), str) else None),
                  "paragraphs": [copy.deepcopy({"id": p.get("id"), "source": p["source"]})
                                 for s in doc.get("body_text", [])
                                 for p in s.get("paragraphs", []) if p.get("source")],
                  "tables": [copy.deepcopy({"id": t.get("id"), "source": t["source"]})
                             for t in doc.get("tables", []) if t.get("source")],
                  "figures": [copy.deepcopy({"id": f.get("id"), "source": f["source"]})
                              for f in doc.get("figures", []) if f.get("source")],
              }}
    if not unchanged:
        _write_metadata(pending, {"previous_hash": old_hash, "next": record})
        try:
            _write_markdown(path, markdown, old_hash)
        except OSError as exc:
            raise ExportError(f"Markdown 저장 실패: {path.name}: {exc}") from exc
        _write_metadata(manifest, record)
        pending.unlink()
    elif previous != record:
        _write_metadata(manifest, record)
    return ExportResult(path, assets, manifest, revision, unchanged)
