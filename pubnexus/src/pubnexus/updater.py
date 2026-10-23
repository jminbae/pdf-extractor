"""Fixed-repository, checksum-required updates; no install during extraction.

Calls are synchronous worker operations. Notifications use a separate daemon
queue so the GUI can read snapshot/close without a callback holding our locks.
Only an in-memory verified preparation may launch; a local manifest is no trust
anchor. Installation is orchestrated by the application after normal close.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib import error, parse, request

from .version import VERSION

REPOSITORY = "jminbae/pdf-extractor"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
INSTALLER_NAME = "PDF-Extractor-Setup.exe"
CHECKSUM_NAME = INSTALLER_NAME + ".sha256"
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
_CDN = {"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


class _UpdateError(RuntimeError):
    def __init__(self, message, code="invalid_release", retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def _version(text):
    if not isinstance(text, str) or not re.fullmatch(r"v?(?:0|[1-9]\d{0,5})\.(?:0|[1-9]\d{0,5})(?:\.(?:0|[1-9]\d{0,5}))?", text):
        raise _UpdateError("배포 버전 형식이 올바르지 않습니다.")
    parts = tuple(int(part) for part in text.removeprefix("v").split("."))
    return parts + (0,) if len(parts) == 2 else parts


def _trusted_url(url, *, redirect=False):
    try:
        parsed = parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None, 443) or "\\" in url):
            raise ValueError()
        if parsed.hostname == "api.github.com" and url == LATEST_URL:
            return url
        if parsed.hostname == "github.com" and parsed.path.startswith(f"/{REPOSITORY}/releases/download/") and not parsed.query:
            tail = parsed.path.removeprefix(f"/{REPOSITORY}/releases/download/").split("/")
            if len(tail) == 2 and tail[1] in (INSTALLER_NAME, CHECKSUM_NAME):
                _version(tail[0])
                return url
        if redirect and parsed.hostname in _CDN:
            return url
    except (TypeError, ValueError):
        pass
    raise _UpdateError("허용된 GitHub 배포 다운로드 주소가 아닙니다.", "unsafe_url")


class _Redirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _trusted_url(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _default_cache():
    from . import store
    return store.root() / "updates"


class UpdateManager:
    def __init__(self, cache_dir=None, current_version=VERSION, notify=None):
        self.current_version = ".".join(map(str, _version(current_version)))
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache()
        self.cache_dir = self.cache_dir.resolve()
        self._state_lock = threading.RLock()
        with _LOCKS_GUARD:
            self._operation_lock = _LOCKS.setdefault(str(self.cache_dir).casefold(), threading.Lock())
        self._release = self._prepared = None
        self._launched = False
        self._state = {"status": "idle", "version": self.current_version,
                       "current_version": self.current_version, "message": "업데이트를 확인할 수 있습니다.",
                       "progress": 0, "retryable": False, "install_supported": self._install_supported()}
        self._events = queue.SimpleQueue() if notify else None
        if notify:
            events = self._events
            def dispatch():
                while True:
                    event = events.get()
                    try:
                        notify(event)
                    except Exception:
                        pass
            threading.Thread(target=dispatch, name="update-notify", daemon=True).start()

    @staticmethod
    def _install_supported():
        return (sys.platform == "win32" and bool(getattr(sys, "frozen", False))
                and "--hidden" not in sys.argv and "--ocr-worker" not in sys.argv)

    def snapshot(self):
        with self._state_lock:
            return copy.deepcopy(self._state)

    def _set(self, status, message, **values):
        with self._state_lock:
            self._state.update(status=status, message=message, **values)
            result = copy.deepcopy(self._state)
        if self._events is not None:
            self._events.put(copy.deepcopy(result))
        return result

    @contextmanager
    def _file_lock(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        stream = open(self.cache_dir / ".update.lock", "a+b")
        locked = False
        try:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise _UpdateError("다른 업데이트 작업이 진행 중입니다.", "busy", True) from exc
            yield
        finally:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()

    def _execute(self, operation):
        if not self._operation_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            with self._file_lock():
                return operation()
        except _UpdateError as exc:
            return self._set("error", str(exc), error=exc.code, retryable=exc.retryable)
        except OSError as exc:
            return self._set("error", "업데이트 캐시/실행 파일을 사용할 수 없습니다: " + str(exc), error="io", retryable=True)
        finally:
            self._operation_lock.release()

    def _fetch(self, url, limit, *, deadline, consume=None):
        _trusted_url(url)
        for attempt in range(2):
            if time.monotonic() >= deadline:
                raise _UpdateError("업데이트 요청 시간이 초과됐습니다.", "timeout", True)
            try:
                opener = request.build_opener(_Redirect())
                req = request.Request(url, headers={"Accept": "application/vnd.github+json" if url == LATEST_URL else "application/octet-stream",
                                                    "User-Agent": "PDF-Extractor/" + self.current_version,
                                                    "X-GitHub-Api-Version": "2022-11-28"})
                with opener.open(req, timeout=min(10, max(.1, deadline-time.monotonic()))) as response:
                    _trusted_url(response.geturl(), redirect=True)
                    length = response.headers.get("Content-Length")
                    if length is not None and (not str(length).isdigit() or int(length) > limit):
                        raise _UpdateError("업데이트 응답 크기 제한을 초과했습니다.", "limit")
                    parts, count = [], 0
                    while True:
                        if time.monotonic() >= deadline:
                            raise _UpdateError("업데이트 요청 시간이 초과됐습니다.", "timeout", True)
                        data = response.read(min(65536, limit-count+1))
                        if not data:
                            break
                        count += len(data)
                        if count > limit:
                            raise _UpdateError("업데이트 응답 크기 제한을 초과했습니다.", "limit")
                        if consume:
                            consume(data, count)
                        else:
                            parts.append(data)
                    if length is not None and count != int(length):
                        raise _UpdateError("업데이트 응답이 끝까지 도착하지 않았습니다.", "truncated", True)
                    return count if consume else b"".join(parts)
            except error.HTTPError as exc:
                if attempt == 0 and (exc.code == 429 or 500 <= exc.code < 600) and consume is None:
                    time.sleep(.1)
                    continue
                raise _UpdateError(f"업데이트 서버 응답 오류({exc.code}).", "http", exc.code == 429 or exc.code >= 500) from exc
            except (error.URLError, TimeoutError, ConnectionError, IncompleteRead) as exc:
                if attempt == 0 and consume is None:
                    time.sleep(.1)
                    continue
                if isinstance(exc, IncompleteRead):
                    raise _UpdateError("업데이트 응답이 끝까지 도착하지 않았습니다. 다시 확인해 주세요.", "truncated", True) from exc
                raise _UpdateError("업데이트 서버에 연결할 수 없습니다. 인터넷 연결 후 다시 확인해 주세요.", "offline", True) from exc

    def _check(self, download):
        self._release = self._prepared = None
        self._set("checking", "새 버전을 확인하고 있습니다.", progress=0, retryable=False, error=None)
        deadline = time.monotonic()+30
        try:
            metadata = json.loads(self._fetch(LATEST_URL, 1024*1024, deadline=deadline))
            if not isinstance(metadata, dict) or metadata.get("draft") is not False or metadata.get("prerelease") is not False:
                raise _UpdateError("정식 공개 배포 정보가 아닙니다.")
            version = _version(metadata.get("tag_name"))
            normalized = ".".join(map(str, version))
            if version <= _version(self.current_version):
                return self._set("up_to_date", "현재 버전이 최신입니다.", version=self.current_version,
                                 checked_at=datetime.now(timezone.utc).isoformat())
            assets = metadata.get("assets")
            if not isinstance(assets, list):
                raise _UpdateError("설치 파일 정보가 없습니다.")
            found = {}
            for name in (INSTALLER_NAME, CHECKSUM_NAME):
                matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
                if len(matches) != 1:
                    raise _UpdateError("검증된 설치 파일과 SHA256 체크섬 파일이 함께 있어야 합니다.", "checksum_required")
                asset = matches[0]
                expected_url = f'https://github.com/{REPOSITORY}/releases/download/{metadata["tag_name"]}/{name}'
                if asset.get("browser_download_url") != expected_url:
                    raise _UpdateError("설치 파일 주소가 신뢰된 배포 경로와 다릅니다.", "unsafe_url")
                _trusted_url(expected_url)
                size = asset.get("size")
                if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= (MAX_INSTALLER_BYTES if name == INSTALLER_NAME else 4096):
                    raise _UpdateError("설치 파일 크기가 제한 범위를 벗어났습니다.", "limit")
                found[name] = dict(asset)
            checksum_bytes = self._fetch(found[CHECKSUM_NAME]["browser_download_url"], 4096, deadline=deadline)
            if len(checksum_bytes) != found[CHECKSUM_NAME]["size"]:
                raise _UpdateError("체크섬 파일 크기가 배포 정보와 다릅니다.")
            checksum = checksum_bytes.decode("ascii").strip()
            match = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?" + re.escape(INSTALLER_NAME), checksum)
            if not match:
                raise _UpdateError("SHA256 체크섬 파일 형식/설치 파일명이 올바르지 않습니다.", "checksum_required")
            digest = match[1].lower()
            api_digest = found[INSTALLER_NAME].get("digest")
            if api_digest is not None and api_digest != "sha256:"+digest:
                raise _UpdateError("GitHub 배포 해시와 체크섬 파일이 일치하지 않습니다.", "checksum_mismatch")
            self._release = {"version": normalized, "sha256": digest, "url": found[INSTALLER_NAME]["browser_download_url"],
                             "size": found[INSTALLER_NAME]["size"]}
            self._set("available", "새 버전을 내려받을 수 있습니다.", version=normalized,
                      checked_at=datetime.now(timezone.utc).isoformat())
            return self._download() if download else self.snapshot()
        except (ValueError, UnicodeError, TypeError) as exc:
            raise _UpdateError("업데이트 배포 정보를 읽을 수 없습니다.") from exc

    def check(self, download=False):
        return self._execute(lambda: self._check(bool(download)))

    def check_and_download(self):
        return self.check(download=True)

    def _download(self):
        if self._release is None:
            raise _UpdateError("먼저 새 버전을 확인해 주세요.", "unconfigured")
        release = dict(self._release)
        folder = self.cache_dir / release["version"] / release["sha256"]
        if not folder.resolve().is_relative_to(self.cache_dir):
            raise _UpdateError("업데이트 캐시 경로가 외부를 가리킵니다.", "unsafe_path")
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.resolve().is_relative_to(self.cache_dir):
            raise _UpdateError("업데이트 캐시 경로가 외부를 가리킵니다.", "unsafe_path")
        installer = folder / INSTALLER_NAME
        self._set("downloading", "새 버전을 내려받고 있습니다.", version=release["version"], progress=0)
        if not installer.is_symlink() and installer.is_file() and installer.stat().st_size == release["size"] and _sha(installer) == release["sha256"]:
            return self._ready(release, installer)
        fd, temporary = tempfile.mkstemp(prefix=".update-", suffix=".part", dir=folder)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as stream:
                def consume(data, count):
                    stream.write(data)
                    digest.update(data)
                    self._set("downloading", "새 버전을 내려받고 있습니다.", progress=min(99, int(count/release["size"]*100)))
                size = self._fetch(release["url"], release["size"], deadline=time.monotonic()+180, consume=consume)
                stream.flush()
                os.fsync(stream.fileno())
            if size != release["size"] or digest.hexdigest() != release["sha256"]:
                raise _UpdateError("설치 파일 SHA256 검증에 실패했습니다. 설치하지 않습니다.", "checksum_mismatch")
            with open(temporary, "rb") as stream:
                if stream.read(2) != b"MZ":
                    raise _UpdateError("Windows 설치 실행 파일이 아닙니다.", "invalid_installer")
            os.replace(temporary, installer)
            return self._ready(release, installer)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _ready(self, release, installer):
        self._prepared = {**release, "installer": str(installer)}
        return self._set("ready", "업데이트 준비 완료 — 정상 종료할 때 설치할 수 있습니다.",
                         installer=str(installer), sha256=release["sha256"], progress=100)

    def download(self):
        return self._execute(self._download)

    def install(self, restart=False, *, busy=False):
        def launch():
            if self._launched:
                return self.snapshot()
            if not self._install_supported():
                return self._set("unsupported", "설치된 Windows 앱에서만 업데이트를 설치할 수 있습니다.")
            if self._prepared is None:
                return self._set("unconfigured", "검증된 업데이트를 먼저 내려받아 주세요.")
            if busy:
                return self._set("ready", "진행 중인 작업이 끝난 뒤 앱을 정상 종료하면 설치합니다.")
            prepared = dict(self._prepared)
            if _version(prepared["version"]) <= _version(self.current_version):
                raise _UpdateError("현재 버전보다 새 버전만 설치할 수 있습니다.")
            expected = self.cache_dir / prepared["version"] / prepared["sha256"] / INSTALLER_NAME
            installer = Path(prepared["installer"])
            resolved = installer.resolve(strict=True)
            if (installer.is_symlink() or resolved != expected or not resolved.is_relative_to(self.cache_dir)
                    or not resolved.is_file()):
                raise _UpdateError("검증된 업데이트 캐시 경로가 아닙니다.", "unsafe_path")
            if resolved.stat().st_size != prepared["size"] or _sha(resolved) != prepared["sha256"]:
                self._prepared = None
                raise _UpdateError("설치 직전 SHA256 검증에 실패했습니다. 다시 내려받아 주세요.", "checksum_mismatch")
            with open(resolved, "rb") as stream:
                if stream.read(2) != b"MZ":
                    raise _UpdateError("Windows 설치 실행 파일이 아닙니다.", "invalid_installer")
            app_dir = Path(sys.executable).resolve().parent
            command = [str(resolved), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART",
                       "/NOCLOSEAPPLICATIONS", "/NOFORCECLOSEAPPLICATIONS", "/DIR=" + str(app_dir),
                       "/LOG=" + str(resolved.parent / "install.log")]
            if restart:
                command.append("/PNXRESTART=1")
            process = subprocess.Popen(command, shell=False, cwd=str(self.cache_dir),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            self._launched = True
            return self._set("installing", "설치 프로그램을 시작했습니다. 다음 실행부터 새 버전을 사용합니다.",
                             restart_requested=bool(restart), pid=process.pid if isinstance(process.pid, int) else None)
        result = self._execute(launch)
        ok = result.get("status") == "installing" and self._launched
        return {**result, "ok": ok, "why": None if ok else result.get("message")}
