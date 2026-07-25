"""공통 유틸: 설정 로딩, HTTP(재시도/rate-limit), DOI 정규화, 경로 헬퍼,
스트리밍 JSONL I/O·원자적 쓰기·실패 원장·진행 보고(대규모/중단내성 토대)."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

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


# ── 원자적 쓰기(같은 디렉터리 임시파일 → os.replace) ──────────────────
# 수십 시간짜리 실행이 중간에 죽어도 산출물이 "반쯤 쓰인 채" 남지 않게 한다.
# os.replace 는 같은 볼륨 안에서 원자적이며 Windows 에서도 기존 파일을 덮어쓴다.
_REPLACE_RETRIES = 5          # Windows: 백신/Dropbox 가 잠깐 파일을 물고 있을 때 대비
_REPLACE_WAIT = 0.2


def _atomic_write(path: Path, writer: Callable[[Any], Any]) -> Any:
    """path 와 같은 디렉터리의 임시파일에 writer(f) 로 쓴 뒤 원자적으로 교체.

    실패하면 임시파일을 지우고 예외를 그대로 올린다(원본은 손대지 않음).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 긴 파일명 + 임시 접미사가 Windows 경로 길이 제한에 닿지 않도록 접두사를 자른다
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent),
                                    prefix=f".{p.name[:60]}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            result = writer(f)
            f.flush()
            try:
                os.fsync(f.fileno())     # 전원 차단까지 견디게(파일시스템이 지원하면)
            except OSError:
                pass
        last: OSError | None = None
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, p)       # ← 원자적 교체(기존 파일 덮어쓰기 포함)
                return result
            except PermissionError as e:  # 다른 프로세스가 대상 파일을 잠시 열고 있음
                last = e
                time.sleep(_REPLACE_WAIT * (attempt + 1))
        raise last  # type: ignore[misc]
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


# ── JSONL 원장 I/O ──────────────────────────────────────────────────
def write_jsonl(path: Path, rows: list[dict]):
    """전체 목록을 원자적으로 기록(기존 시그니처 유지)."""
    write_jsonl_stream(path, rows)


def write_jsonl_stream(path: Path, rows: Iterable[dict]) -> int:
    """제너레이터를 받아 메모리에 다 올리지 않고 원자적으로 기록. 쓴 줄 수 반환."""
    def _w(f) -> int:
        n = 0
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
        return n
    return _atomic_write(path, _w)


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def iter_jsonl(path: Path) -> Iterator[dict]:
    """JSONL 을 한 줄씩 흘려보낸다(전체를 메모리에 올리지 않음).

    깨진 줄은 건너뛰되 몇 줄을 건너뛰었는지 끝에서 로그로 알린다(조용히 삼키지 않음).
    바이너리로 읽어 줄 단위로 디코딩하므로 인코딩 깨진 한 줄이 전체 순회를 끊지 않는다.
    """
    p = Path(path)
    if not p.exists():
        return
    bad = 0
    first_bad: tuple[int, str] | None = None
    try:
        with open(p, "rb") as f:
            for lineno, raw in enumerate(f, 1):
                if lineno == 1:
                    raw = raw.lstrip(b"\xef\xbb\xbf")     # UTF-8 BOM
                if not raw.strip():
                    continue
                try:
                    yield json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as e:
                    bad += 1
                    if first_bad is None:
                        first_bad = (lineno, f"{type(e).__name__}: {e}"[:160])
    finally:
        # 소비자가 중간에 break 해도(제너레이터 조기 종료) 보고는 남긴다
        if bad and first_bad is not None:
            log(f"[utils] iter_jsonl: {p.name} 깨진 줄 {bad}개 건너뜀 "
                f"(첫 줄 {first_bad[0]}: {first_bad[1]})")


def count_jsonl(path: Path) -> int:
    """메모리에 올리지 않고 유효(비어있지 않은) 줄 수만 센다."""
    p = Path(path)
    if not p.exists():
        return 0
    n = 0
    with open(p, "rb") as f:
        for raw in f:
            if raw.strip():
                n += 1
    return n


def append_jsonl(path: Path, row: dict) -> None:
    """한 줄 추가(이어하기 원장·실패 원장용). 원자적 교체 대상이 아니다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, obj: Any):
    """JSON 을 원자적으로 기록(기존 시그니처 유지)."""
    _atomic_write(path, lambda f: json.dump(obj, f, ensure_ascii=False, indent=2))


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


# ── 실패 원장: 한 편이 죽어도 나머지가 계속 돌게 하는 기록 장치 ─────────
FAILURES_NAME = "failures.jsonl"


def record_failure(work_dir: Path, stage: str, item: str,
                   exc: BaseException) -> None:
    """work_dir/failures.jsonl 에 실패 1건을 append. 같은 (stage,item)도 그냥 쌓는다(이력).

    except 블록에서 불리므로 **절대 예외를 올리지 않는다**.
    """
    row = {
        "ts_iso": datetime.now().isoformat(),
        "stage": stage,
        "item": str(item)[:500],
        "error_type": type(exc).__name__,
        "message": str(exc)[:2000],
    }
    try:
        append_jsonl(Path(work_dir) / FAILURES_NAME, row)
    except Exception as e:            # 원장 기록 실패가 파이프라인을 멈추면 안 된다
        log(f"[utils] 실패 원장 기록 실패({stage}/{item}): {type(e).__name__}: {e}")


def _fire(cb: Callable | None, *args) -> None:
    """콜백 예외가 파이프라인을 멈추지 않도록 감싼다."""
    if cb is None:
        return
    try:
        cb(*args)
    except Exception as e:
        log(f"[utils] 콜백 예외 무시: {type(e).__name__}: {e}")


def _item_label(item: Any, idx: int = 0) -> str:
    """실패 원장에 남길 항목 이름을 최대한 사람이 알아보게 뽑는다."""
    if isinstance(item, dict):
        for k in ("paper_id", "chunk_id", "id", "pdf_path", "path", "file",
                  "filename", "doi", "pmid"):
            v = item.get(k)
            if v:
                return str(v)[:300]
    if isinstance(item, (str, Path)):
        return str(item)[:300]
    return f"#{idx}" if idx else str(item)[:300]


def safe_iter(items: Iterable[Any], stage: str, work_dir: Path,
              on_error: Callable[[Any, BaseException], None] | None = None
              ) -> Iterator[Any]:
    """항목별 예외를 실패 원장에 남기고 다음 항목으로 넘어가는 제너레이터.

    - 소스 이터레이터가 항목을 만들다 터지면(파일 하나가 깨진 경우 등) 그 항목만 버린다.
    - 항목이 무인자 호출가능 객체면 여기서 호출하고, 호출 실패도 같은 방식으로 격리한다.
    - 소비자(for 본문)에서 나는 예외는 파이썬 구조상 제너레이터가 가로챌 수 없다.
      본문까지 격리하려면 `with item_guard(stage, work_dir, 이름):` 을 같이 써라.
    """
    it = iter(items)
    idx = 0
    consecutive = 0
    while True:
        idx += 1
        try:
            item = next(it)
            consecutive = 0
        except StopIteration:
            return
        except Exception as e:
            record_failure(work_dir, stage, f"#{idx}", e)
            _fire(on_error, f"#{idx}", e)
            consecutive += 1
            if consecutive >= 100:       # 소스가 계속 같은 예외만 뱉는 경우 탈출
                log(f"[{stage}] safe_iter: 소스 연속 실패 {consecutive}회 → 중단")
                return
            continue
        if callable(item):
            try:
                value = item()
            except Exception as e:
                label = _item_label(getattr(item, "label", None) or item, idx)
                record_failure(work_dir, stage, label, e)
                _fire(on_error, item, e)
                continue
            yield value
        else:
            yield item


class item_guard:
    """`with item_guard("5단계", work, paper_id):` — 본문 예외를 원장에 남기고 넘어간다.

    __exit__ 이 True 를 돌려 예외를 삼키므로 바깥 for 루프는 다음 항목으로 계속 간다.
    KeyboardInterrupt/SystemExit(BaseException)는 삼키지 않는다 — 사용자 중단은 즉시 통해야 한다.
    """

    def __init__(self, stage: str, work_dir: Path, item: Any,
                 on_error: Callable[[Any, BaseException], None] | None = None):
        self.stage = stage
        self.work_dir = work_dir
        self.item = item
        self.on_error = on_error
        self.failed = False

    def __enter__(self) -> "item_guard":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False
        self.failed = True
        label = _item_label(self.item)
        record_failure(self.work_dir, self.stage, label, exc)
        log(f"[{self.stage}] 실패(계속 진행): {label} — {type(exc).__name__}: {exc}")
        _fire(self.on_error, self.item, exc)
        return True


# ── 진행 보고: 사람용 로그 + 선택적 콜백(앱이 진행률을 가져갈 수 있게) ──
class Progress:
    """단계 진행률을 stderr 로그와 on_progress 콜백에 함께 흘린다.

    on_progress 는 {stage, current, total, item, elapsed_s} dict 를 받는다.
    """

    def __init__(self, stage: str, total: int = 0, every: int = 25,
                 on_progress: Callable[[dict], None] | None = None):
        self.stage = stage
        self.total = int(total or 0)
        self.every = max(1, int(every))
        self.on_progress = on_progress
        self.current = 0
        self._t0 = time.monotonic()

    def update(self, n: int = 1, item: str = "") -> None:
        self.current += n
        if self.current % self.every == 0 or (self.total and self.current >= self.total):
            self.emit(item)

    def emit(self, item: str = "") -> None:
        pct = f" ({self.current * 100 // self.total}%)" if self.total else ""
        log(f"[{self.stage}] {self.current}/{self.total or '?'}{pct} "
            f"{item}".rstrip())
        _fire(self.on_progress, {
            "stage": self.stage, "current": self.current, "total": self.total,
            "item": item, "elapsed_s": round(time.monotonic() - self._t0, 1),
        })

    def done(self, item: str = "") -> None:
        self.emit(item or "완료")
