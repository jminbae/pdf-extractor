"""공통 유틸: 설정 로딩, HTTP(재시도/rate-limit), DOI 정규화, 경로 헬퍼,
스트리밍 JSONL I/O·원자적 쓰기·실패 원장·진행 보고(대규모/중단내성 토대)."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import requests
import yaml

# ── 프로젝트 루트 ──────────────────────────────────────────────────────
# 소스 실행: pubnexus/src/pubnexus/utils.py → 3단계 위가 프로젝트 루트.
# exe(PyInstaller) 실행: __file__ 이 임시 해제 폴더를 가리켜 위 계산이 무의미하다.
#   → exe 파일이 놓인 폴더를 루트로 본다(설정·데이터를 exe 옆에서 찾게).
# PUBNEXUS_ROOT 환경변수가 있으면 그것이 항상 우선(개발·검증용 override).
def _detect_root() -> Path:
    env = os.environ.get("PUBNEXUS_ROOT")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):            # PyInstaller 로 묶인 상태
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


ROOT = _detect_root()

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


# 다음 줄에서 DOI 가 이어지는지 볼 때 쓰는 조각(선행 공백 건너뛰고 DOI 문자만)
_DOI_CONT_RE = re.compile(r'^\s*([\w][-._\w]*)')


def doi_candidates(text: str, doi_re: re.Pattern | None = None,
                   limit: int = 12) -> list[str]:
    """줄바꿈으로 끊긴 DOI 를 이어붙인 후보들을 만든다.

    조판 때문에 DOI 가 줄 중간에서 끊기는 일이 흔하다:
        'https://doi.org/10.1200/JCO.18.\\n01223'  → 10.1200/jco.18 로 잘림
        'https://doi.org/10.5021/ad.22\\n22.150'   → 10.5021/ad.22 로 잘림
    공백을 전부 지우면 뒷 문장까지 끌고 오므로('...01223volume37') 쓸 수 없다.
    그래서 **끊긴 지점 다음 줄의 첫 조각만** 이어붙인 후보를 만들고, 어느 것이
    실제로 존재하는 DOI 인지는 호출부가 Crossref 로 확인한다(verify_doi).

    반환 순서 = 원본 매치들 먼저, 그다음 이어붙인 후보들(긴 것 우선).
    """
    doi_re = doi_re or DOI_RE
    bases: list[str] = []
    stitched: list[str] = []
    for m in doi_re.finditer(text or ""):
        raw = m.group(0)
        base = clean_doi(raw)
        if base and base not in bases:
            bases.append(base)
        # 매치가 줄 끝에서 멈췄을 때만 이어붙이기를 시도한다
        tail = (text or "")[m.end():m.end() + 80]
        if not tail[:1] in ("\n", "\r"):
            continue
        cont = _DOI_CONT_RE.match(tail.lstrip("\r\n"))
        if not cont:
            continue
        token = cont.group(1)
        # 'ad.22' 다음 줄이 '22.150' 인 2단 조판처럼, 토큰의 뒷부분만 이어야
        # 맞는 경우가 있다 → 점 기준 뒤쪽 조합을 모두 후보로 낸다.
        parts = token.split(".")
        pieces = {token}
        for i in range(1, len(parts)):
            pieces.add(".".join(parts[i:]))
        joiner = "" if raw.rstrip()[-1:] in "./-" else "."
        for piece in sorted(pieces, key=len, reverse=True):
            cand = clean_doi(raw.rstrip() + joiner + piece)
            if cand and cand not in bases and cand not in stitched:
                stitched.append(cand)
    return (bases + stitched)[:limit]


def verify_doi(doi: str, http: "HttpClient | None" = None) -> str | None:
    """Crossref 에 실제로 등록된 DOI 인지 확인하고, 맞으면 정규 DOI 를 돌려준다.

    네트워크가 안 되면 None(=판정 불가). 호출부가 '없음'과 구분해 다루어야 한다.
    """
    if not doi:
        return None
    client = http or HttpClient()
    try:
        r = client.get(f"https://api.crossref.org/works/{doi}",
                       accept="application/json", retries=1)
    except Exception:  # noqa: BLE001 — 네트워크 실패는 판정 불가로 처리
        return None
    if r is None or r.status_code != 200:
        return None
    try:
        return clean_doi(r.json()["message"].get("DOI") or doi)
    except Exception:  # noqa: BLE001
        return doi


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
        # 여러 논문을 동시에 처리하면 이 객체를 여러 스레드가 함께 쓴다.
        # 잠금이 없으면 간격 조절이 무너져 한꺼번에 몰려 나가고, NCBI 가
        # 429(요청 과다)로 막는다. 조절 자체를 직렬화한다.
        self._lock = threading.Lock()

    def _throttle(self, host: str):
        # rate-limit 은 호스트 단위로 적용(서로 다른 API 사이엔 대기 불필요).
        # 대기는 **잠금 밖에서** 한다 — 잠금을 쥔 채 자면 다른 호스트로 가는
        # 요청까지 막혀 동시 처리의 이득이 사라진다.
        with self._lock:
            now = time.monotonic()
            wait = self.delay - (now - self._last.get(host, 0.0))
            # 다음 차례를 미리 예약해 둔다(내가 잘 동안 남이 끼어들지 못하게).
            self._last[host] = now + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

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
_LONG_PATH_AT = 240           # 260자 제한에 임시파일 접미사까지 감안한 여유선


def long_path(path: str | Path) -> str:
    r"""Windows 260자 경로 제한을 넘는 경로를 확장 형식(\\?\C:\...)으로 바꾼다.

    실제로 터진다 — 논문 파일명이 120자쯤이고 Dropbox 한글 폴더까지 겹치면
    os.replace 가 FileNotFoundError(WinError 3) 로 죽는다. 앱이 PDF 옆에
    같은 이름의 .json 을 쓰기 때문에 이 조건이 흔하다.
    긴 경로 지원이 꺼져 있어도 이 접두사를 붙이면 API 수준에서 통과한다.
    """
    s = str(path)
    if sys.platform != "win32" or len(s) < _LONG_PATH_AT or s.startswith("\\\\?\\"):
        return s
    try:
        s = str(Path(s).resolve())
    except OSError:
        return s
    if s.startswith("\\\\"):                      # UNC 경로
        return "\\\\?\\UNC\\" + s.lstrip("\\")
    return "\\\\?\\" + s


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
                # 260자를 넘는 대상은 확장 형식으로 바꿔야 os.replace 가 통과한다
                os.replace(long_path(tmp), long_path(p))
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


def path_exists(path: str | Path) -> bool:
    """긴 경로에서도 정확한 존재 확인.

    Path.exists() 는 260자를 넘으면 조용히 False 를 돌려준다 — '없다'와
    '못 본다'를 구분하지 못해, 이미 만든 파일을 없다고 판단해 재처리하거나
    빈 결과를 반환하는 사고가 난다.
    """
    p = str(path)
    if Path(p).exists():
        return True
    lp = long_path(p)
    return lp != p and os.path.exists(lp)


def read_jsonl(path: Path) -> list[dict]:
    if not path_exists(path):
        return []
    with open(long_path(path), "r", encoding="utf-8") as f:
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
    with open(long_path(path), "r", encoding="utf-8") as f:
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
