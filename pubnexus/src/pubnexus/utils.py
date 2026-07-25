"""공통 유틸: 설정 로딩, HTTP(재시도/rate-limit), DOI 정규화, 경로 헬퍼."""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import requests
import yaml

# ── 프로젝트 루트: pubnexus/src/pubnexus/utils.py → 3단계 위 ────────────
ROOT = Path(__file__).resolve().parents[3]

DOI_RE = re.compile(r'10\.\d{4,9}/[-._;()/:\w]+', re.I)
# DOI 끝에 붙는 흔한 잡꼬리 제거용
_DOI_TRAILERS = ").,;:]"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else ROOT / "pubnexus" / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve(path_str: str) -> Path:
    """설정의 상대경로를 프로젝트 루트 기준 절대경로로."""
    p = Path(path_str)
    return p if p.is_absolute() else (ROOT / p)


def clean_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    d = raw.strip()
    while d and d[-1] in _DOI_TRAILERS:
        d = d[:-1]
    return d.lower() or None


def extract_doi(text: str) -> str | None:
    m = DOI_RE.search(text or "")
    return clean_doi(m.group(0)) if m else None


def slug(s: str) -> str:
    """paper_id → 파일명 안전 문자열."""
    s = (s or "unknown").replace("/", "_").replace("\\", "_")
    return re.sub(r'[^-_.\w]', "_", s)


def norm_text(s: str) -> str:
    """유니코드 정규화 + 공백 정리."""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r'[ \t]+', " ", s).strip()


def approx_tokens(text: str) -> int:
    """토큰 근사(영문 기준 ~4자/토큰). 청킹 크기 제어용."""
    return max(1, len(text) // 4)


# ── HTTP: polite pool + 재시도 + 429 백오프 ──────────────────────────
class HttpClient:
    def __init__(self, email: str = "", delay: float = 0.34,
                 timeout: int = 30, user_agent: str = "PubNexus/0.1"):
        self.email = email
        self.delay = delay
        self.timeout = timeout
        self.sess = requests.Session()
        self.sess.headers.update({
            "User-Agent": f"{user_agent} (mailto:{email})" if email else user_agent,
        })
        self._last: dict[str, float] = {}   # 호스트별 마지막 요청 시각

    def _throttle(self, host: str):
        # rate-limit 은 호스트 단위로 적용(서로 다른 API 사이엔 대기 불필요)
        wait = self.delay - (time.monotonic() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None,
            accept: str | None = None, retries: int = 3):
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        headers = {"Accept": accept} if accept else {}
        for attempt in range(retries):
            self._throttle(host)
            try:
                r = self.sess.get(url, params=params, headers=headers,
                                  timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(2 ** attempt + 1)
                    continue
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return None

    def get_json(self, url: str, params: dict | None = None, **kw):
        r = self.get(url, params=params, accept="application/json", **kw)
        return r.json() if r is not None else None


# ── JSONL 원장 I/O ──────────────────────────────────────────────────
def write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)
