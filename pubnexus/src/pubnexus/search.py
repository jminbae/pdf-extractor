"""7단계 — 하이브리드 검색(dense + BM25 → RRF 병합 → 리랭커).

설계서 7단계 "하이브리드는 선택이 아니라 필수"를 그대로 구현한다.
dense 만 쓰면 'NB-UVB', 'F-VASI', 'JAK inhibitor' 같은 의학 약어·측정도구명의
정확 매칭을 놓치고, BM25 만 쓰면 표현이 다른 동의 질의를 놓친다. 두 순위를
RRF(Reciprocal Rank Fusion)로 합친 뒤 선택적으로 Qwen3-Reranker 로 재정렬한다.

이 모듈은 **임포트만으로는 어떤 무거운 의존성도 끌어오지 않는다.**
numpy·embed·index·sentence-transformers 는 전부 함수 안에서 지연 임포트한다
(evaluate.py 가 `from pubnexus import search` 만 해도 모델이 로딩되면 안 된다).

산출물 의존:
    5단계 chunks.jsonl        ← cfg["chunk"]["output"]
    6단계 벡터 인덱스/BM25    ← index.load_vector_store(cfg), index.BM25.load(...)
셋 중 하나라도 없으면 IndexNotReady 를 던지고, 복구 명령을 한국어로 안내한다.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any

from . import utils
from .utils import log

# 리랭커 입력 문서의 상한(문자). 표 청크가 길어 시퀀스가 폭발하는 것을 막는다.
_RERANK_DOC_CHARS = 4000
# 필터가 걸리면 인덱스에서 더 많이 길어 올린다(사후 필터링이라 후보가 줄기 때문).
_FILTER_OVERFETCH = 6

# 프로세스 내 자원 캐시 — evaluate.py 는 질의를 십수 번 반복 호출한다.
# 매번 인코더(모델)와 인덱스를 다시 올리면 실행이 불가능해진다.
_CACHE: dict[tuple, Any] = {}
_RERANKER_CACHE: dict[tuple, Any] = {}
# 같은 안내 문구를 질의마다 반복 출력하지 않기 위한 플래그
_WARNED: set[str] = set()


class IndexNotReady(RuntimeError):
    """5~6단계 산출물(청크·벡터·BM25)이 없거나 서로 어긋날 때."""


@dataclass
class Hit:
    chunk_id: str
    paper_id: str
    score: float
    rank: int
    text: str
    context_header: str
    section_type: str
    section_path: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    # 왜 잡혔는지 설명용:
    #   {"dense_rank": int|None, "bm25_rank": int|None, "rrf": float, "rerank": float|None}
    #   (+ 참고용 원점수 dense_score / bm25_score)
    why: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id, "paper_id": self.paper_id,
            "score": self.score, "rank": self.rank, "text": self.text,
            "context_header": self.context_header,
            "section_type": self.section_type, "section_path": self.section_path,
            "meta": self.meta, "why": self.why,
        }


# ── 자원 로딩 ────────────────────────────────────────────────────────
@dataclass
class _Resources:
    chunks: dict[str, dict]      # chunk_id → 청크 레코드
    encoder: Any                 # embed.Encoder
    store: Any                   # index.load_vector_store(cfg) 반환값
    bm25: Any | None             # index.BM25 (하이브리드 아닐 땐 None)


def _guide(key: str, headline: str, hints: list[str]) -> None:
    """실패 원인 + 복구 명령을 한 번만 출력(grobid_client 의 안내 방식과 동일)."""
    if key in _WARNED:
        return
    _WARNED.add(key)
    log(headline)
    for h in hints:
        log(f"        → {h}")


def _store_len(store) -> int:
    """스토어에 들어 있는 벡터 개수(알 수 없으면 0).

    계약상 load_vector_store 는 (ids, matrix) 튜플도 돌려줄 수 있어서
    len(store) 를 그냥 쓰면 튜플 길이 2 를 벡터 수로 착각한다.
    """
    if isinstance(store, tuple) and len(store) == 2:
        try:
            return len(store[0])
        except TypeError:
            return 0
    try:
        return int(len(store))
    except (TypeError, ValueError):
        return 0


def _brief(e: BaseException) -> str:
    """예외 메시지의 첫 줄만. 6단계 모듈이 이미 여러 줄 안내를 붙여 오므로 중복을 자른다."""
    lines = str(e).strip().splitlines()
    return lines[0].strip() if lines else type(e).__name__


def _cmd(args: str = "") -> str:
    """리포 루트에서 바로 붙여넣을 수 있는 복구 명령(5~6단계 오케스트레이터)."""
    return f"cd pubnexus && python run_rag.py{' ' + args if args else ''}"


def _signature(cfg: dict) -> tuple:
    """자원 캐시 키 — 설정이 바뀌면 다시 로딩한다."""
    emb = cfg.get("embedding", {})
    vdb = cfg.get("vectordb", {})
    return (
        str(cfg.get("chunk", {}).get("output")),
        str(vdb.get("backend")), str(vdb.get("path")), str(vdb.get("table")),
        str(vdb.get("flat_path")), str(vdb.get("bm25_path")),
        str(emb.get("backend")), str(emb.get("model")), int(emb.get("dim") or 0),
    )   # search.hybrid 은 넣지 않는다 — 껐다 켜는 것만으로 모델을 다시 올리면 안 된다


def _import_stage6():
    """6단계 모듈 지연 임포트. 아직 없으면 친절한 안내 후 IndexNotReady."""
    try:
        from . import embed, index          # noqa: PLC0415 — 지연 임포트가 설계다
    except ImportError as e:
        _guide("stage6-missing",
               f"[7단계] 6단계 모듈을 불러오지 못했습니다: {type(e).__name__}: {e}",
               ["embed.py / index.py 가 아직 없습니다(6단계 미구현).",
                "requirements 설치 확인: pip install -r pubnexus/requirements.txt"])
        raise IndexNotReady(f"6단계 모듈 임포트 실패: {e}") from e
    return embed, index


def _load(cfg: dict) -> _Resources:
    sig = _signature(cfg)
    cached = _CACHE.get(sig)
    if cached is not None:
        return cached

    embed, index = _import_stage6()

    # 1) 5단계 청크 원장
    chunks_path = utils.resolve(cfg["chunk"]["output"])
    rows = utils.read_jsonl(chunks_path)
    if not rows:
        _guide("chunks-missing",
               f"[7단계] 청크 파일이 없거나 비었습니다: {chunks_path}",
               ["5단계 청킹부터: " + _cmd("--chunk-only"),
                "청킹 + 인덱스 한 번에: " + _cmd()])
        raise IndexNotReady(f"청크 파일 없음: {chunks_path}")
    chunks = {r["chunk_id"]: r for r in rows if r.get("chunk_id")}

    # 2) 벡터 스토어
    try:
        store = index.load_vector_store(cfg)
    except FileNotFoundError as e:
        _guide("vec-missing",
               f"[7단계] 벡터 인덱스가 없습니다: {_brief(e)}",
               ["6단계 인덱스 구축: " + _cmd(),
                "모델·GPU 없이 배관만 점검하려면: " + _cmd("--backend hash")])
        raise IndexNotReady(f"벡터 인덱스 없음: {_brief(e)}") from e
    except Exception as e:      # noqa: BLE001 — 백엔드(lancedb 등) 부재/손상 격리
        _guide("vec-fail",
               f"[7단계] 벡터 인덱스 로딩 실패: {type(e).__name__}: {_brief(e)}",
               ["vectordb.backend 가 lancedb 면 pip install lancedb 가 필요합니다.",
                "의존성 없이 쓰려면 vectordb.backend: flat 으로 바꾼 뒤 재구축하세요.",
                "재구축: " + _cmd("--skip-chunk")])
        raise IndexNotReady(f"벡터 인덱스 로딩 실패: {type(e).__name__}: {_brief(e)}") from e

    # 3) BM25 — hybrid 여부와 무관하게 열어 둔다(껐다 켜도 캐시가 살아 있도록).
    #    파일이 없을 때의 경고는 실제로 하이브리드를 쓸 때만 낸다.
    bm25 = None
    bm_path = utils.resolve(cfg["vectordb"]["bm25_path"])
    try:
        # 6단계가 로더 헬퍼를 주면 그걸 쓰고(경로 규칙이 한 곳에), 아니면 계약대로 직접
        loader = getattr(index, "load_bm25", None)
        bm25 = loader(cfg) if callable(loader) else index.BM25.load(bm_path)
    except Exception as e:      # noqa: BLE001 — BM25 없어도 dense 로는 검색 가능
        if cfg.get("search", {}).get("hybrid", True):
            _guide("bm25-missing",
                   f"[7단계] BM25 인덱스를 못 읽었습니다({type(e).__name__}) → dense 단독으로 진행",
                   [f"경로: {bm_path}",
                    "재구축: " + _cmd("--skip-chunk"),
                    "의학 약어(NB-UVB, F-VASI) 정확매칭이 약해지니 가급적 복구하세요."])

    # 4) 임베딩 인코더
    try:
        encoder = embed.get_encoder(cfg)
    except Exception as e:      # noqa: BLE001 — ST/torch 부재 등을 한 줄로 안내
        _guide("enc-fail",
               f"[7단계] 임베딩 백엔드 준비 실패: {type(e).__name__}: {_brief(e)}",
               ["모델 없이 구조만 시험하려면: " + _cmd("--backend hash")
                + "  (검색 품질은 보장되지 않습니다)"])
        raise IndexNotReady(f"임베딩 백엔드 준비 실패: {type(e).__name__}: {_brief(e)}") from e

    # 5) 인덱스 ↔ 청크 원장 규모 대조.
    #    낡은 인덱스의 id 는 조회 후 조용히 버려져 상위 k 가 슬그머니 줄어든다.
    #    후보에 섞여 들어올 때까지 기다리지 말고 로딩 시점에 바로 알린다.
    n_vec = _store_len(store)
    if n_vec and n_vec != len(chunks):
        _guide("count-mismatch",
               f"[7단계] 인덱스 벡터 {n_vec}개 ≠ 청크 원장 {len(chunks)}개 — 인덱스가 낡았습니다",
               ["원장에 없는 id 는 결과에서 조용히 빠져 상위 k 가 줄어들 수 있습니다.",
                "청킹 후 인덱스를 다시 만드세요: " + _cmd()])

    res = _Resources(chunks=chunks, encoder=encoder, store=store, bm25=bm25)
    _CACHE[sig] = res
    log(f"[7단계] 인덱스 로딩 완료: 청크 {len(chunks)}개 · 인코더 "
        f"{getattr(encoder, 'name', '?')}(dim={getattr(encoder, 'dim', '?')}) · "
        f"BM25 {'있음' if bm25 is not None else '없음'}")
    return res


# ── dense 검색 ───────────────────────────────────────────────────────
def _store_vectors(store) -> tuple[list[str], Any] | None:
    """벡터 스토어에서 (ids, 행렬) 을 꺼낸다. 못 꺼내면 None.

    index.load_vector_store 는 계약상 "(ids, matrix) 또는 테이블 핸들을 감싼 객체"를
    돌려주므로 두 형태를 모두 받아준다(백엔드 교체에 검색 코드가 흔들리지 않도록).
    """
    if isinstance(store, tuple) and len(store) == 2:
        return list(store[0]), store[1]
    for id_attr in ("ids", "chunk_ids"):
        ids = getattr(store, id_attr, None)
        if ids is None:
            continue
        for m_attr in ("matrix", "vectors", "vecs", "embeddings", "mat"):
            mat = getattr(store, m_attr, None)
            if mat is not None:
                return list(ids), mat
    return None


def _rows_to_pairs(rows) -> list[tuple[str, float]]:
    """스토어 자체 검색 API 의 반환을 (chunk_id, score) 목록으로 정규화."""
    out: list[tuple[str, float]] = []
    for r in rows or []:
        if isinstance(r, (tuple, list)) and len(r) >= 2:
            out.append((str(r[0]), float(r[1])))
        elif isinstance(r, dict):
            cid = r.get("chunk_id") or r.get("id")
            if "_distance" in r:                 # lancedb 는 거리(작을수록 가까움)
                score = 1.0 - float(r["_distance"])
            else:
                score = float(r.get("score", r.get("_score", 0.0)))
            if cid:
                out.append((str(cid), score))
    return out


def _check_dim(store, q_dim: int) -> None:
    """차원 불일치를 '결과 0건'이 아니라 명확한 오류로 만든다.

    인코더를 바꾸고 인덱스를 다시 안 만든 경우가 가장 흔한 사고인데,
    백엔드에 따라 조용히 빈 결과만 오면 원인을 찾기 어렵다.
    """
    dim = getattr(store, "dim", None)
    if not isinstance(dim, int) or dim <= 0:
        mat = getattr(store, "matrix", None)
        shape = getattr(mat, "shape", None)
        dim = int(shape[1]) if shape and len(shape) == 2 else 0
    if dim and dim != q_dim:
        raise IndexNotReady(
            f"차원 불일치: 인덱스 {dim}차원 vs 질의 {q_dim}차원. 인덱스를 만든 "
            "임베딩 백엔드와 지금 설정이 다릅니다. index.build() 재실행이 필요합니다.")


def _dense_search(store, qvec, top_k: int) -> list[tuple[str, float]]:
    """코사인 유사도 상위 top_k 를 [(chunk_id, score)] 로."""
    import numpy as np                            # noqa: PLC0415 — 지연 임포트

    q = np.asarray(qvec, dtype="float32").reshape(-1)
    _check_dim(store, int(q.shape[0]))

    # 1순위: 스토어가 자체 검색을 제공하면 그대로 쓴다(백엔드별 거리 변환은 index 소관)
    fn = getattr(store, "search", None)
    if callable(fn):
        try:
            return _rows_to_pairs(fn(q, top_k))
        except TypeError:                        # 시그니처가 키워드형인 경우
            try:
                return _rows_to_pairs(fn(q, top_k=top_k))
            except Exception as e:               # noqa: BLE001 — 탐색 실패 격리
                raise IndexNotReady(
                    f"벡터 스토어 검색 호출 실패: {type(e).__name__}: {e}") from e

    # 2순위: (ids, matrix) 만 주는 스토어 — numpy 브루트포스(6,700청크 규모면 충분)
    pair = _store_vectors(store)
    if pair is None:
        raise IndexNotReady(
            "벡터 스토어에서 검색 방법을 찾지 못했습니다 "
            "(ids/matrix 속성도, search() 도 없음). index.load_vector_store 반환값을 확인하세요.")
    ids, mat = pair
    m = np.asarray(mat, dtype="float32")
    if m.ndim != 2 or m.shape[0] == 0:
        raise IndexNotReady("벡터 인덱스가 비어 있습니다. 6단계를 다시 구축하세요.")
    if m.shape[1] != q.shape[0]:
        raise IndexNotReady(
            f"차원 불일치: 인덱스 {m.shape[1]}차원 vs 질의 {q.shape[0]}차원. "
            "인덱스를 만든 임베딩 백엔드와 지금 설정이 다릅니다. 재구축이 필요합니다.")
    qn = q / (float(np.linalg.norm(q)) or 1.0)
    norms = np.linalg.norm(m, axis=1)
    norms[norms == 0] = 1.0
    sims = (m @ qn) / norms
    n = max(1, min(int(top_k), len(ids)))
    idx = np.argpartition(-sims, n - 1)[:n] if n < len(ids) else np.arange(len(ids))
    idx = idx[np.argsort(-sims[idx])]
    return [(str(ids[i]), float(sims[i])) for i in idx]


def _bm25_search(bm25, query: str, top_k: int) -> list[tuple[str, float]]:
    if bm25 is None:
        return []
    try:
        return _rows_to_pairs(bm25.search(query, top_k))
    except Exception as e:      # noqa: BLE001 — BM25 실패가 검색 전체를 죽이면 안 된다
        _guide("bm25-search-fail",
               f"[7단계] BM25 검색 실패({type(e).__name__}: {_brief(e)}) → dense 결과만 사용",
               ["BM25 인덱스를 재구축하세요: " + _cmd("--skip-chunk")])
        return []


# ── RRF 병합 ─────────────────────────────────────────────────────────
def _rrf_merge(dense: list[tuple[str, float]], bm25: list[tuple[str, float]],
               rrf_k: int) -> list[dict]:
    """score = Σ 1/(rrf_k + rank). 어느 쪽 몇 위로 잡혔는지를 함께 남긴다."""
    info: dict[str, dict] = {}
    for rank, (cid, sc) in enumerate(dense, 1):
        info.setdefault(cid, {})["dense_rank"] = rank
        info[cid]["dense_score"] = round(sc, 6)
    for rank, (cid, sc) in enumerate(bm25, 1):
        info.setdefault(cid, {})["bm25_rank"] = rank
        info[cid]["bm25_score"] = round(sc, 6)

    merged: list[dict] = []
    for cid, w in info.items():
        rrf = 0.0
        if w.get("dense_rank"):
            rrf += 1.0 / (rrf_k + w["dense_rank"])
        if w.get("bm25_rank"):
            rrf += 1.0 / (rrf_k + w["bm25_rank"])
        merged.append({
            "chunk_id": cid, "rrf": rrf,
            "dense_rank": w.get("dense_rank"), "bm25_rank": w.get("bm25_rank"),
            "dense_score": w.get("dense_score"), "bm25_score": w.get("bm25_score"),
        })
    # 동점은 dense 순위 → chunk_id 로 결정적으로 정렬(재현 가능한 평가를 위해)
    merged.sort(key=lambda r: (-r["rrf"], r["dense_rank"] or 10 ** 9, r["chunk_id"]))
    return merged


# ── 필터 ─────────────────────────────────────────────────────────────
def _as_set(v) -> set[str]:
    if v is None:
        return set()
    if isinstance(v, str):
        v = [v]
    return {str(x).strip().lower() for x in v if str(x).strip()}


def _passes(rec: dict, f: dict) -> bool:
    """인덱스 조회 뒤 파이썬 필터. 코퍼스가 수천~수만 청크면 이걸로 충분하다."""
    meta = rec.get("meta") or {}

    want = _as_set(f.get("section_type"))
    if want and (rec.get("section_type") or "other").lower() not in want:
        return False

    want = _as_set(f.get("kind"))
    if want and (rec.get("kind") or "").lower() not in want:
        return False

    want = _as_set(f.get("paper_id"))
    if want and (rec.get("paper_id") or "").lower() not in want:
        return False

    ymin, ymax = f.get("year_min"), f.get("year_max")
    if ymin is not None or ymax is not None:
        year = meta.get("year")
        # 연도 미상은 연도 조건을 만족했다고 볼 수 없다 → 제외(엄격)
        if not isinstance(year, int):
            return False
        if ymin is not None and year < int(ymin):
            return False
        if ymax is not None and year > int(ymax):
            return False

    jn = f.get("journal")
    if jn and str(jn).strip().lower() not in (meta.get("journal") or "").lower():
        return False

    want = _as_set(f.get("pub_types"))
    if want and not (want & {str(p).lower() for p in (meta.get("pub_types") or [])}):
        return False

    return True


# ── 리랭커 ───────────────────────────────────────────────────────────
def _get_reranker(cfg: dict):
    """Qwen3-Reranker 지연 로딩. 실패하면 None(경고만 남기고 RRF 순위 유지)."""
    model_name = cfg.get("reranker", {}).get("model", "")
    device = cfg.get("embedding", {}).get("device", "auto")
    key = (model_name, device)
    if key in _RERANKER_CACHE:
        return _RERANKER_CACHE[key]

    ce = None
    try:
        # 6단계가 리랭커 헬퍼를 제공하면 그쪽을 우선 사용(중복 구현 방지)
        from . import embed                       # noqa: PLC0415 — 지연 임포트
        getter = getattr(embed, "get_reranker", None)
        if callable(getter):
            ce = getter(cfg)
    except Exception as e:      # noqa: BLE001 — 헬퍼 부재/실패는 폴백으로 흡수
        log(f"      ! embed.get_reranker 사용 실패: {type(e).__name__}: {e}")
        ce = None

    if ce is None:
        try:
            from sentence_transformers import CrossEncoder   # noqa: PLC0415
            import torch                                     # noqa: PLC0415

            dev = device
            if dev in (None, "", "auto"):
                dev = "cuda" if torch.cuda.is_available() else "cpu"
            kwargs: dict = {"attn_implementation": "sdpa"}   # Windows 는 flash-attn 불가
            if dev == "cuda":
                kwargs["torch_dtype"] = "bfloat16"
            ce = CrossEncoder(model_name, device=dev, model_kwargs=kwargs)
            log(f"[7단계] 리랭커 로딩: {model_name} @ {dev}")
        except Exception as e:  # noqa: BLE001 — 리랭커는 없어도 검색은 되어야 한다
            _guide("rerank-unavailable",
                   f"[7단계] 리랭커를 쓸 수 없습니다({type(e).__name__}: {_brief(e)}) → RRF 순위 그대로 반환",
                   ["설치: pip install -U sentence-transformers torch",
                    f"모델: {model_name} (최초 1회 다운로드 약 1.2GB)",
                    "리랭커 없이 쓰려면 config 의 reranker.enabled 를 false 로 두세요."])
            ce = None

    _RERANKER_CACHE[key] = ce
    return ce


def _rerank_scores(cfg: dict, query: str, docs: list[str]) -> list[float] | None:
    ce = _get_reranker(cfg)
    if ce is None or not docs:
        return None
    try:
        pairs = [(query, d[:_RERANK_DOC_CHARS]) for d in docs]
        batch = int(cfg.get("embedding", {}).get("batch_size", 16))
        scores = ce.predict(pairs, batch_size=batch)
        return [float(s) for s in scores]
    except Exception as e:      # noqa: BLE001 — 추론 실패도 크래시 금지
        _guide("rerank-predict-fail",
               f"[7단계] 리랭킹 실패({type(e).__name__}: {_brief(e)}) → RRF 순위 그대로 반환",
               ["VRAM 부족이면 reranker.top_k_in 을 줄이거나 embedding.device: cpu 로 시도하세요."])
        return None


# ── 공개 API ─────────────────────────────────────────────────────────
def search(cfg: dict, query: str, k: int | None = None,
           filters: dict | None = None, rerank: bool | None = None) -> list[Hit]:
    """질의 → 근거 청크 목록.

    filters: {"section_type": [...], "year_min": int, "year_max": int,
              "journal": str, "pub_types": [...], "paper_id": [...], "kind": [...]}
    rerank : None 이면 config 의 reranker.enabled 를 따른다.
    """
    q = utils.norm_text(query or "")
    if not q:
        return []

    scfg = cfg.get("search", {}) or {}
    rcfg = cfg.get("reranker", {}) or {}
    # k 는 None 일 때만 설정 기본값으로 떨어진다. 0/음수를 falsy 로 뭉뚱그리면
    #   k=0  → 기본값 8건이 나오고,
    #   k=-3 → 마지막 cands[:k] 가 음수 슬라이스가 되어 후보 "거의 전부"가 나온다.
    # 둘 다 조용히 틀린 분량을 돌려주므로 여기서 끊는다.
    if k is None:
        k = int(scfg.get("top_k") or rcfg.get("top_k_out") or 8)
    else:
        k = max(0, int(k))
    if k == 0:
        return []
    filters = {kk: vv for kk, vv in (filters or {}).items() if vv not in (None, [], "")}
    use_rerank = rcfg.get("enabled", False) if rerank is None else bool(rerank)
    hybrid = bool(scfg.get("hybrid", True))
    rrf_k = int(scfg.get("rrf_k", 60))

    res = _load(cfg)
    n_corpus = len(res.chunks)

    n_dense = int(scfg.get("dense_top_k", 50))
    n_bm25 = int(scfg.get("bm25_top_k", 50))
    if use_rerank:
        n_in = int(rcfg.get("top_k_in", 50))
        n_dense, n_bm25 = max(n_dense, n_in), max(n_bm25, n_in)
    if filters:
        # 사후 필터링이라 후보가 줄어든다 → 미리 더 길어 올린다
        mult = int(scfg.get("filter_overfetch", _FILTER_OVERFETCH))
        n_dense, n_bm25 = n_dense * mult, n_bm25 * mult
    n_dense = min(max(n_dense, k), n_corpus)
    n_bm25 = min(max(n_bm25, k), n_corpus)

    # 1) 질의 인코딩 — Qwen3 계열은 질의에만 instruction 접두를 붙인다(embed 가 처리)
    qvec = res.encoder.encode([q], is_query=True)

    # 2) 후보 수집: dense + BM25 → RRF 병합 → 필터
    def gather(nd: int, nb: int) -> tuple[list[dict], int]:
        """dense/BM25 → RRF → 필터 → (후보, 원장에 없는 id 수)."""
        dense = _dense_search(res.store, qvec[0], nd)
        sparse = _bm25_search(res.bm25, q, nb) if hybrid else []
        out: list[dict] = []
        gone = 0
        for m in _rrf_merge(dense, sparse, rrf_k):
            rec = res.chunks.get(m["chunk_id"])
            if rec is None:
                gone += 1
                continue
            if filters and not _passes(rec, filters):
                continue
            out.append({"m": m, "rec": rec})
        return out, gone

    cands, missing = gather(n_dense, n_bm25)

    # 필터가 촘촘하면(예: paper_id 한정) 사후 필터링만으로는 k 를 못 채운다.
    # 그럴 때 한 번만 후보 범위를 넓혀 다시 긁는다(질의 벡터는 재사용).
    if filters and len(cands) < k:
        wide = min(n_corpus, int(scfg.get("max_candidates", 5000)))
        if wide > max(n_dense, n_bm25):
            cands, missing = gather(wide, wide)

    if missing:
        _guide("stale-index",
               f"[7단계] 인덱스에만 있고 청크 원장에 없는 id {missing}개, 인덱스가 낡았습니다",
               ["청킹 후 인덱스를 다시 만드세요: " + _cmd()])

    # 3) 리랭킹(옵션) — 실패해도 RRF 순위 그대로
    rerank_scores: list[float] | None = None
    if use_rerank and cands:
        n_in = min(int(rcfg.get("top_k_in", 50)), len(cands))
        head = cands[:n_in]
        docs = [(c["rec"].get("embed_text")
                 or f"{c['rec'].get('context_header', '')}\n\n{c['rec'].get('text', '')}").strip()
                for c in head]
        rerank_scores = _rerank_scores(cfg, q, docs)
        if rerank_scores is not None:
            for c, s in zip(head, rerank_scores):
                c["rerank"] = s
            # 점수를 못 받은 후보가 섞여도(길이 불일치) 뒤로 밀릴 뿐 터지지 않는다
            head.sort(key=lambda c: -c.get("rerank", float("-inf")))
            cands = head + cands[n_in:]

    # 4) Hit 조립
    hits: list[Hit] = []
    for rank, c in enumerate(cands[:k], 1):
        rec, m = c["rec"], c["m"]
        rr = c.get("rerank")
        hits.append(Hit(
            chunk_id=rec.get("chunk_id", m["chunk_id"]),
            paper_id=rec.get("paper_id", ""),
            score=float(rr if rr is not None else m["rrf"]),
            rank=rank,
            text=rec.get("text", ""),
            context_header=rec.get("context_header", ""),
            section_type=rec.get("section_type", "other"),
            section_path=list(rec.get("section_path") or []),
            meta=dict(rec.get("meta") or {}),
            why={"dense_rank": m["dense_rank"], "bm25_rank": m["bm25_rank"],
                 "rrf": round(m["rrf"], 6),
                 "rerank": (round(rr, 6) if rr is not None else None),
                 "dense_score": m["dense_score"], "bm25_score": m["bm25_score"]},
        ))
    return hits


# ── 사람이 읽는 출력 ─────────────────────────────────────────────────
def _wrap(label: str, text: str, width: int, max_lines: int) -> list[str]:
    """'라벨  본문…' 형태로 접는다. 둘째 줄부터는 라벨 폭만큼 들여쓴다."""
    flat = " ".join((text or "").split()) or "(없음)"
    pad = " " * len(label)
    lines = textwrap.wrap(flat, width=max(20, width - len(label))) or ["(없음)"]
    out = [(label if i == 0 else pad) + ln for i, ln in enumerate(lines[:max_lines])]
    if len(lines) > max_lines:
        out[-1] = out[-1].rstrip() + " …"
    return out


def _why_text(why: dict) -> str:
    bits = []
    d, b = why.get("dense_rank"), why.get("bm25_rank")
    bits.append(f"dense #{d}" if d else "dense -")
    bits.append(f"BM25 #{b}" if b else "BM25 -")
    bits.append(f"RRF {why.get('rrf', 0):.4f}")
    if why.get("rerank") is not None:
        bits.append(f"리랭크 {why['rerank']:.4f}")
    return " · ".join(bits)


def format_hits(hits: list[Hit], width: int = 100) -> str:
    """터미널용 출력. 순위 / 논문 / 섹션 / 근거 / 본문 발췌."""
    if not hits:
        return "검색 결과가 없습니다. (필터를 완화하거나 질의를 바꿔 보세요)"

    out: list[str] = []
    for h in hits:
        m = h.meta or {}
        head = f"[{h.rank}] {h.score:.4f}  {h.paper_id or '(paper_id 없음)'}"
        src = " · ".join(str(x) for x in (m.get("year"), m.get("journal")) if x)
        if src:
            head += f"  ({src})"
        out.append(head)

        out += _wrap("  제목  ", m.get("title") or "(제목 없음)", width, 2)
        path = " > ".join(h.section_path) if h.section_path else "(섹션 경로 없음)"
        out += _wrap("  섹션  ", f"{path}  [{h.section_type}]", width, 2)
        out.append("  근거  " + _why_text(h.why or {}))
        out += _wrap("  본문  ", h.text, width, 4)
        out.append("")
    out.append(f"총 {len(hits)}건")
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    _cfg = utils.load_config()
    _q = " ".join(sys.argv[1:]) or "vitiligo repigmentation NB-UVB"
    print(format_hits(search(_cfg, _q)))
