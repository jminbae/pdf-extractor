"""GROBID 서비스를 **창 없이** 띄우고 기다린다.

사용자가 검정 콘솔창을 보게 하지 않는다. 배치파일(.bat)로 띄우면 `start` 가
새 콘솔을 열어 창이 뜬다. 여기서는 java 를 직접 부르되
  · `javaw.exe`  — 콘솔을 만들지 않는 자바 실행기
  · `CREATE_NO_WINDOW` — 그래도 콘솔이 생기지 않게 못박는다
  · `DETACHED_PROCESS` — 앱을 닫아도 서비스가 죽지 않게 분리
를 함께 쓴다. 표준출력은 로그 파일로 돌린다(창 대신 파일에 남는다).

윈도우 전용 경로다. 다른 OS 에서는 `start()` 가 조용히 False 를 돌려주고,
호출부는 'GROBID 없음' 경로(PyMuPDF 폴백)로 간다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from .utils import log

DEFAULT_URL = "http://localhost:8070"
GROBID_ROOT = Path(r"C:\grobid")

# 창을 만들지 않는 플래그. 윈도우가 아니면 0 이라 무해하다.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)


def is_alive(url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(url.rstrip("/") + "/api/isalive", timeout=timeout)
        return r.status_code == 200 and "true" in r.text.lower()
    except requests.RequestException:
        return False


def _java(root: Path) -> Path | None:
    """콘솔을 만들지 않는 javaw 를 먼저 찾고, 없으면 java 로 내려간다."""
    for name in ("javaw.exe", "java.exe"):
        p = root / "jdk21" / "bin" / name
        if p.exists():
            return p
    return None


def start(url: str = DEFAULT_URL, root: Path | None = None) -> bool:
    """이미 살아 있으면 True. 아니면 창 없이 띄우고 True/False.

    기동만 시키고 기다리지는 않는다 — 기다리는 것은 `wait_ready()` 의 몫이다.
    화면을 얼리지 않으려면 호출부가 워커 스레드에서 둘을 이어 부르면 된다.
    """
    if is_alive(url):
        return True
    if sys.platform != "win32":
        return False
    root = Path(root or GROBID_ROOT)
    java = _java(root)
    home = root / "grobid" / "grobid-home"
    libs = root / "grobid" / "grobid-service" / "build" / "install" / "grobid-service" / "lib"
    conf = home / "config" / "grobid.yaml"
    if not (java and libs.exists() and conf.exists()):
        log("      · GROBID 설치를 찾지 못했다 → PyMuPDF 폴백으로 진행")
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
           on_progress=None) -> bool:
    """켜져 있으면 그대로, 아니면 창 없이 띄우고 준비될 때까지 기다린다."""
    if is_alive(url):
        return True
    if not start(url):
        return False
    return wait_ready(url, timeout=timeout, on_progress=on_progress)
