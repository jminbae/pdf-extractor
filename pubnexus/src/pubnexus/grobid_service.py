"""GROBID 서비스를 **창 없이** 띄우고 기다린다.

사용자가 검정 콘솔창을 보게 하지 않는다. 배치파일(.bat)로 띄우면 `start` 가
새 콘솔을 열어 창이 뜬다. 여기서는 java 를 직접 부르되
  · `javaw.exe`  — 콘솔을 만들지 않는 자바 실행기
  · `CREATE_NO_WINDOW` — 그래도 콘솔이 생기지 않게 못박는다
  · `DETACHED_PROCESS` — 앱을 닫아도 서비스가 죽지 않게 분리
를 함께 쓴다. 표준출력은 로그 파일로 돌린다(창 대신 파일에 남는다).

윈도우 전용 경로다. 다른 OS 에서는 `start()` 가 조용히 False 를 돌려주고,
호출부는 'GROBID 없음' 경로(PyMuPDF 폴백)로 간다.

**설치 위치를 고정하지 않는다.** PC 마다 다르므로 아래 순서로 찾고, 어디에도
없으면 배포본을 내려받아 앱 저장소에 설치한다(`install()`). 그래야 exe 하나만
받은 사람도 GROBID 품질로 처리된다 — 폴백은 안전망이지 대안이 아니다
(실측: 품질 0.65 → 0.96, 표 0 → 286개, 참고문헌 0 → 4,169개).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests

from .utils import log

DEFAULT_URL = "http://localhost:8070"

# 우리가 패치해 빌드한 판(tools/build_grobid_windows.ps1 참고).
# 공식 배포본은 윈도우에서 돌지 않는다 — pdfalto 인자가 달라 늘 [NO_BLOCKS] 로 끝난다.
RUNTIME_VERSION = "0.9.0-win1"
RUNTIME_URL = ("https://github.com/jminbae/pdf-extractor/releases/download/"
               "grobid-{v}/grobid-win64.zip").format(v=RUNTIME_VERSION)
_VERSION_FILE = "PNX_RUNTIME_VERSION"

GROBID_ROOT = Path(r"C:\grobid")          # 옛 설치본 자리(하위호환용 후보 중 하나)

# 창을 만들지 않는 플래그. 윈도우가 아니면 0 이라 무해하다.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)


def is_alive(url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(url.rstrip("/") + "/api/isalive", timeout=timeout)
        return r.status_code == 200 and "true" in r.text.lower()
    except requests.RequestException:
        return False


def version(url: str = DEFAULT_URL, timeout: float = 3.0) -> str | None:
    """돌고 있는 서비스의 판 번호. 못 물으면 None."""
    try:
        r = requests.get(url.rstrip("/") + "/api/version", timeout=timeout)
        return r.text.strip() if r.status_code == 200 else None
    except requests.RequestException:
        return None


def _java(root: Path) -> Path | None:
    """콘솔을 만들지 않는 javaw 를 먼저 찾고, 없으면 java 로 내려간다."""
    for name in ("javaw.exe", "java.exe"):
        p = root / "jdk21" / "bin" / name
        if p.exists():
            return p
    return None


# ── 설치 위치 찾기 ──────────────────────────────────────────────────────
#  PC 마다 다르다. 고정하지 않고 아래 순서로 본다.
def install_root() -> Path:
    """우리가 설치하는 자리. 관리자 권한이 필요없는 앱 저장소 아래."""
    from . import store
    return store.root() / "grobid"


def _candidate_roots() -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()

    def add(p) -> None:
        if not p:
            return
        q = Path(p)
        if str(q).lower() not in seen:
            seen.add(str(q).lower())
            out.append(q)

    add(os.environ.get("GROBID_ROOT"))      # 손으로 지정한 것이 언제나 우선
    add(install_root())                     # 우리가 설치한 것
    add(GROBID_ROOT)                        # C:\grobid — 예전 방식으로 깔아 둔 것
    for drive in ("C:", "D:", "E:"):
        add(Path(drive + "\\") / "grobid")
    if getattr(sys, "frozen", False):       # exe 옆에 통째로 담아 배포한 경우
        add(Path(sys.executable).resolve().parent / "grobid")
    return out


def _layout_ok(root: Path) -> bool:
    """이 자리에 실제로 돌릴 수 있는 설치본이 있는가."""
    libs = root / "grobid" / "grobid-service" / "build" / "install" / "grobid-service" / "lib"
    conf = root / "grobid" / "grobid-home" / "config" / "grobid.yaml"
    return bool(_java(root)) and libs.is_dir() and any(libs.iterdir()) and conf.exists()


def find_root() -> Path | None:
    """쓸 수 있는 설치본을 찾는다. 없으면 None."""
    for r in _candidate_roots():
        try:
            if _layout_ok(r):
                return r
        except OSError:                     # 없는 드라이브·권한 없는 경로
            continue
    return None


def installed_version(root: Path) -> str | None:
    f = root / _VERSION_FILE
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return None                         # 손으로 깐 것에는 표식이 없다. 그래도 쓴다.


def install(on_progress=None) -> Path | None:
    """배포본을 내려받아 앱 저장소에 설치하고 그 자리를 돌려준다.

    on_progress(내려받은비율 0~100) 로 진행을 알린다. 실패하면 None —
    호출부는 폴백으로 간다(설치 실패가 앱을 멈추게 해선 안 된다).
    """
    if sys.platform != "win32":
        return None
    dest = install_root()
    tmp_zip = Path(tempfile.gettempdir()) / f"grobid-{RUNTIME_VERSION}.zip"
    try:
        log(f"      · 분석 엔진을 내려받는다 ({RUNTIME_URL})")
        with requests.get(RUNTIME_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(tmp_zip, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(int(done * 100 / total))
        # 반쯤 풀린 설치본이 남지 않게: 옆에 풀고 마지막에 바꿔 끼운다.
        staging = dest.with_name(dest.name + ".new")
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_zip) as z:
            z.extractall(staging)
        if not _layout_ok(staging):
            log("      ! 내려받은 분석 엔진의 구성이 예상과 다르다 → 폴백")
            shutil.rmtree(staging, ignore_errors=True)
            return None
        (staging / _VERSION_FILE).write_text(RUNTIME_VERSION, encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        staging.rename(dest)
        log(f"      · 분석 엔진 설치 완료: {dest}")
        return dest
    except Exception as e:                  # noqa: BLE001 — 설치 실패가 앱을 멈추게 하지 않는다
        log(f"      ! 분석 엔진 설치 실패: {e}")
        return None
    finally:
        try:
            tmp_zip.unlink()
        except OSError:
            pass


def start(url: str = DEFAULT_URL, root: Path | None = None) -> bool:
    """이미 살아 있으면 True. 아니면 창 없이 띄우고 True/False.

    기동만 시키고 기다리지는 않는다 — 기다리는 것은 `wait_ready()` 의 몫이다.
    화면을 얼리지 않으려면 호출부가 워커 스레드에서 둘을 이어 부르면 된다.
    """
    if is_alive(url):
        return True
    if sys.platform != "win32":
        return False
    root = Path(root) if root else find_root()
    if root is None:
        log("      · GROBID 설치를 찾지 못했다 → PyMuPDF 폴백으로 진행")
        return False
    java = _java(root)
    home = root / "grobid" / "grobid-home"
    libs = root / "grobid" / "grobid-service" / "build" / "install" / "grobid-service" / "lib"
    conf = home / "config" / "grobid.yaml"
    if not (java and libs.exists() and conf.exists()):
        log(f"      · GROBID 설치가 온전하지 않다({root}) → PyMuPDF 폴백으로 진행")
        return False

    cmd = [
        str(java), "-Xmx4G",
        f"-Djava.library.path={home / 'lib' / 'win-64'}",
        "--add-opens", "java.base/java.lang=ALL-UNNAMED",
        "-cp", str(libs / "*"),
        "org.grobid.service.main.GrobidServiceApplication",
        "server", str(conf),
    ]
    logf = root / "grobid-service.log"
    try:
        # 창을 만들지 않는다. 출력은 파일로 — 그래야 파이프가 차서 멎지 않는다.
        with open(logf, "ab") as out:
            subprocess.Popen(
                cmd, cwd=str(root / "grobid"),
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW | _DETACHED,
                close_fds=True,
                env={**os.environ, "JAVA_HOME": str(root / "jdk21")},
            )
    except OSError as e:
        log(f"      ! GROBID 기동 실패: {e}")
        return False
    log("      · GROBID 를 창 없이 띄웠다(콜드 기동 40~50초)")
    return True


def wait_ready(url: str = DEFAULT_URL, timeout: float = 120.0,
               on_progress=None) -> bool:
    """살아날 때까지 기다린다. 남은 초를 on_progress(초) 로 알려준다."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_alive(url, timeout=2.0):
            return True
        if on_progress:
            on_progress(int(time.time() - t0))
        time.sleep(2.0)
    return False


def ensure(url: str = DEFAULT_URL, timeout: float = 120.0,
           on_progress=None, allow_install: bool = True) -> bool:
    """켜져 있으면 그대로, 없으면 **설치까지 해서** 띄우고 준비될 때까지 기다린다.

    순서: 살아 있나 → 이 PC 어딘가에 깔려 있나 → 없으면 내려받아 설치.
    어느 단계에서 실패해도 False 를 돌려줄 뿐 예외를 올리지 않는다. 호출부는
    폴백으로 간다.
    """
    if is_alive(url):
        return True
    root = find_root()
    if root is None and allow_install:
        log("      · 이 PC 에 분석 엔진이 없다 → 설치를 시작한다(최초 1회)")
        root = install(on_progress=on_progress)
    if root is None:
        return False
    if not start(url, root=root):
        return False
    return wait_ready(url, timeout=timeout, on_progress=on_progress)
