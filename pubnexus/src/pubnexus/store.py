"""추출물 저장소 — 앱이 관리하는 한 곳에 모은다.

**PDF 옆에 두지 않는다**(2026-07-26 원장 결정). 이유 셋:
  · PDF 폴더가 지저분해진다 — 200편이면 JSON 200개, 그림까지 뽑으면 폴더가 논문 수만큼
  · 읽기 전용 위치(네트워크 드라이브·CD·권한 없는 폴더)에서는 아예 쓸 수 없다
  · ResearchMap 이 이미 `%LOCALAPPDATA%` 에 자체 저장소를 쓴다 — 나중에 합칠 때 맞다

**짝은 파일 내용(sha1)으로 맺는다.** 경로로 맺으면 PDF 를 옮기거나 폴더 이름을
바꾸는 순간 추출물을 잃는다. 내용으로 맺으면 파일을 어디로 옮기든, 이름을 무엇으로
바꾸든 그대로 찾는다. 같은 논문을 두 벌 넣어도 한 번만 뽑는다.
그 대가로 **PDF 를 조금이라도 고치면 다른 파일로 본다**(다시 뽑는다) — 맞는 동작이다.

    <저장소>/docs/<sha1 앞2자>/<sha1>.json     정본
    <저장소>/figs/<sha1 앞2자>/<sha1>/         그 논문의 그림
    <저장소>/index.json                        목록 화면용 요약(정본 아님, 언제든 다시 만들 수 있다)

DOI 이름은 정본 안(`paper_id`)에 그대로 있고, 내보내기 할 때 파일명으로 쓴다.
저장소 파일명까지 DOI 로 하면 DOI 를 못 찾은 논문을 담을 곳이 없어진다.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from pathlib import Path

from . import utils
from .utils import log

APP_NAME = "PDF Extractor"

_lock = threading.Lock()          # index.json 을 여러 스레드가 함께 쓴다


# ── 위치 ─────────────────────────────────────────────────────────────
def root() -> Path:
    """저장소 위치. 환경변수 PUBNEXUS_STORE 로 덮어쓸 수 있다(시험·이전용)."""
    env = os.environ.get("PUBNEXUS_STORE")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def _shard(sha1: str) -> str:
    """한 폴더에 수만 개가 쌓이면 탐색기도 느려진다 → 앞 두 자로 나눈다."""
    return (sha1 or "00")[:2]


def doc_path(sha1: str) -> Path:
    return root() / "docs" / _shard(sha1) / f"{sha1}.json"


def figs_dir(sha1: str) -> Path:
    return root() / "figs" / _shard(sha1) / sha1


def index_path() -> Path:
    return root() / "index.json"


# ── 파일 지문 ────────────────────────────────────────────────────────
def file_sha1(path: str | Path, chunk: int = 1 << 20) -> str:
    """PDF 내용의 sha1. 큰 파일도 통째로 메모리에 올리지 않는다."""
    h = hashlib.sha1()
    with open(utils.long_path(Path(path)), "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ── 읽기·쓰기 ────────────────────────────────────────────────────────
def has(sha1: str) -> bool:
    return utils.path_exists(doc_path(sha1))


def load(sha1: str) -> dict | None:
    p = doc_path(sha1)
    if not utils.path_exists(p):
        return None
    try:
        return utils.read_json(p)
    except Exception:  # noqa: BLE001 — 깨진 파일은 없는 것으로 보고 다시 뽑게 한다
        return None


def save(sha1: str, doc: dict, pdf_path: str | Path | None = None) -> Path:
    """정본을 저장하고 목록 요약을 갱신한다."""
    p = doc_path(sha1)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(doc)
    doc["sha1"] = sha1
    if pdf_path:
        doc["source_file"] = str(pdf_path)
    utils.write_json(p, doc)
    _index_put(sha1, doc, pdf_path)
    return p


# ── 목록 요약 ────────────────────────────────────────────────────────
def _index_put(sha1: str, doc: dict, pdf_path: str | Path | None) -> None:
    """목록 화면이 정본 수만 개를 다 열지 않아도 되게 요약만 모아 둔다.

    이 파일은 **정본이 아니다.** 지워도 `rebuild_index()` 로 다시 만들 수 있다.
    """
    m = doc.get("meta") or {}
    row = {
        "paper_id": doc.get("paper_id"),
        "title": m.get("title") or "",
        "journal": m.get("journal") or "",
        "year": m.get("year"),
        "source": doc.get("source"),
        "quality": doc.get("quality_score"),
        "pdf": str(pdf_path) if pdf_path else (doc.get("source_file") or ""),
        "scope": doc.get("scope") or "",
    }
    with _lock:
        idx = {}
        if utils.path_exists(index_path()):
            try:
                idx = utils.read_json(index_path()) or {}
            except Exception:  # noqa: BLE001
                idx = {}
        idx[sha1] = row
        try:
            utils.write_json(index_path(), idx)
        except Exception as e:  # noqa: BLE001 — 요약 실패가 추출을 무효화하지 않는다
            log(f"      ! 목록 갱신 실패: {e}")


def index() -> dict:
    if not utils.path_exists(index_path()):
        return {}
    try:
        return utils.read_json(index_path()) or {}
    except Exception:  # noqa: BLE001
        return {}


def rebuild_index() -> int:
    """정본을 전부 훑어 목록을 다시 만든다(요약이 깨졌을 때)."""
    idx: dict = {}
    d = root() / "docs"
    if not d.exists():
        return 0
    for p in d.rglob("*.json"):
        try:
            doc = utils.read_json(p)
        except Exception:  # noqa: BLE001
            continue
        sha1 = doc.get("sha1") or p.stem
        m = doc.get("meta") or {}
        idx[sha1] = {
            "paper_id": doc.get("paper_id"), "title": m.get("title") or "",
            "journal": m.get("journal") or "", "year": m.get("year"),
            "source": doc.get("source"), "quality": doc.get("quality_score"),
            "pdf": doc.get("source_file") or "", "scope": doc.get("scope") or "",
        }
    index_path().parent.mkdir(parents=True, exist_ok=True)
    utils.write_json(index_path(), idx)
    return len(idx)


# ── 내보내기 ─────────────────────────────────────────────────────────
def export(sha1: str, dest_dir: str | Path) -> Path | None:
    """정본을 DOI 이름으로 원하는 폴더에 꺼낸다.

    저장소 안에서는 내용 지문으로 이름을 짓지만, 사람이 받아 갈 때는 DOI 가 낫다.
    """
    doc = load(sha1)
    if not doc:
        return None
    name = utils.slug(str(doc.get("paper_id") or sha1))
    out = Path(dest_dir) / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    utils.write_json(out, doc)
    return out
