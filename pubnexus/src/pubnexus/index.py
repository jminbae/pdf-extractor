"""6단계 — 인덱스 구축(벡터 스토어 + BM25).

chunks.jsonl 한 벌에서 두 개의 인덱스를 만든다.

  · 벡터 인덱스 — embed.get_encoder() 로 청크를 임베딩해 저장.
      vectordb.backend="lancedb" 면 LanceDB 테이블, "flat" 이면 numpy .npz.
      lancedb 가 없으면 안내 후 flat 으로 자동 폴백한다(품질 손실이 없는 폴백이라 허용).
  · BM25 인덱스 — 순수 파이썬 Okapi BM25. 의학 약어("NB-UVB", "IL-17", "F-VASI")는
      dense 임베딩이 자주 놓치므로 하이브리드 검색의 절반을 담당한다.
      하이픈 결합 토큰을 원형과 분해형 양쪽으로 색인해 표기 흔들림을 흡수한다.

인덱스에는 벡터/토큰만 넣고 청크 본문·메타는 chunks.jsonl 에 그대로 둔다.
7단계 필터(section_type/year/journal 등)는 조회 후 파이썬에서 거르는 편이
6,700 청크 규모에서 훨씬 단순하고 빠르다.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import embed, utils
from .utils import log

# 의학 텍스트용 토큰 정규식: 하이픈/슬래시로 이어진 낱말 결합을 한 덩어리로 잡는다.
# (NB-UVB, IL-17, F-VASI, 25-OH-D, mg/kg …)
#
# 문자 클래스는 "밑줄을 뺀 모든 유니코드 낱말 문자"([^\W_]) 다. ASCII+한글 화이트리스트로
# 두면 그리스문자·악센트가 **구분자**로 취급돼 용어가 잘려 나간다:
#   TGF-β → "tgf" / IFN-γ → "ifn" / μg → "g" / Sjögren → "sj","gren" / α-MSH → "msh"
# 즉 TGF-α 와 TGF-β 가 같은 토큰이 되고, μg 와 g 가 충돌한다.
# (실측: 이 코퍼스에서 약 9%의 청크가 영향) 한글·한자도 같은 클래스로 함께 살린다.
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-/][^\W_]+)*")
_SPLIT_RE = re.compile(r"[-/]")

# 불용어는 아주 짧은 영어 기본셋만. 의학 용어를 실수로 지우지 않도록 최소한으로 유지한다.
_STOPWORDS = frozenset("""
a an and are as at be been by for from had has have in into is it its of on or
that the their there these this those to was were which with we our
""".split())

_BM25_SCHEMA = 1        # BM25 저장 포맷 버전
_VEC_SCHEMA = 1         # 벡터 스토어 메타 버전


# ── BM25 ────────────────────────────────────────────────────────────
class BM25:
    """순수 파이썬 Okapi BM25(외부 패키지 없음).

    postings: term → [(doc_idx, tf), ...] 정렬 리스트. 6,700 청크 규모에서는
    역색인 전체가 수십 MB 안에 들어와 gzip+json 저장/로드로 충분하다.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = float(k1)
        self.b = float(b)
        self.doc_ids: list[str] = []
        self.doc_len: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.avgdl: float = 0.0

    # -- 토크나이저 -------------------------------------------------
    @staticmethod
    def tokenize(text: str) -> list[str]:
        """의학 텍스트용 토큰화.

        소문자화 후 하이픈/슬래시 결합 토큰을 **원형과 분해형 둘 다** 낸다.
          "NB-UVB"  → ["nb-uvb", "nb", "uvb"]
          "IL-17"   → ["il-17", "il", "17"]
          "F-VASI"  → ["f-vasi", "vasi"]        (1글자 조각은 노이즈라 버린다)
          "TGF-β"   → ["tgf-β", "tgf"]          (그리스문자 보존 — β/α 구분)
        1글자 토큰을 버리는 이유: 코퍼스에 자간(글자 사이 공백) 아티팩트가 있어
        단독 알파벳이 대량으로 섞여 들어오기 때문이다.
        """
        t = utils.norm_text(text or "").lower()
        if not t:
            return []
        out: list[str] = []
        for tok in _TOKEN_RE.findall(t):
            if len(tok) > 1 and tok not in _STOPWORDS:
                out.append(tok)
            if "-" in tok or "/" in tok:
                for part in _SPLIT_RE.split(tok):
                    if len(part) > 1 and part not in _STOPWORDS:
                        out.append(part)
        return out

    # -- 색인 -------------------------------------------------------
    def build(self, docs: list[tuple[str, str]]) -> None:
        """(chunk_id, text) 목록으로 역색인을 만든다."""
        self.doc_ids, self.doc_len = [], []
        acc: dict[str, list[tuple[int, int]]] = {}
        for i, (cid, text) in enumerate(docs):
            toks = self.tokenize(text)
            self.doc_ids.append(cid)
            self.doc_len.append(len(toks))
            for term, tf in Counter(toks).items():
                acc.setdefault(term, []).append((i, tf))
        self.postings = acc
        n = len(self.doc_ids)
        self.avgdl = (sum(self.doc_len) / n) if n else 0.0

    # -- 검색 -------------------------------------------------------
    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """상위 top_k 를 [(chunk_id, score)] 로. 빈 질의·빈 코퍼스는 []."""
        n = len(self.doc_ids)
        if n == 0 or top_k <= 0:
            return []
        terms = self.tokenize(query)
        if not terms:
            return []
        avgdl = self.avgdl or 1.0
        scores: dict[int, float] = {}
        for term in dict.fromkeys(terms):        # 중복 제거(순서 보존)
            plist = self.postings.get(term)
            if not plist:
                continue
            df = len(plist)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for idx, tf in plist:
                dl = self.doc_len[idx] or 1
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                scores[idx] = scores.get(idx, 0.0) + idf * tf * (self.k1 + 1.0) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        return [(self.doc_ids[i], round(s, 6)) for i, s in ranked]

    # -- 영속화(gzip + json) ----------------------------------------
    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # postings 는 [idx, tf, idx, tf, ...] 로 평탄화해 저장 크기를 줄인다.
        flat = {t: [v for pair in pl for v in pair] for t, pl in self.postings.items()}
        payload = {
            "schema": _BM25_SCHEMA, "k1": self.k1, "b": self.b,
            "n_docs": len(self.doc_ids), "avgdl": self.avgdl,
            "doc_ids": self.doc_ids, "doc_len": self.doc_len, "postings": flat,
        }
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    @classmethod
    def load(cls, path: str | Path) -> "BM25":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"BM25 인덱스가 없다: {p} — 먼저 index.build() 를 실행하라.")
        with gzip.open(p, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        obj = cls(k1=payload.get("k1", 1.5), b=payload.get("b", 0.75))
        obj.doc_ids = list(payload.get("doc_ids", []))
        obj.doc_len = list(payload.get("doc_len", []))
        obj.avgdl = float(payload.get("avgdl", 0.0))
        obj.postings = {t: [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
                        for t, flat in payload.get("postings", {}).items()}
        return obj

    def __len__(self) -> int:
        return len(self.doc_ids)


# ── 벡터 스토어 ──────────────────────────────────────────────────────
@dataclass
class VectorStore:
    """벡터 인덱스 핸들 — flat(numpy) 과 lancedb 를 같은 얼굴로 감싼다.

    `ids, matrix = load_vector_store(cfg)` 같은 언패킹도 되도록 __iter__ 를 준다
    (lancedb 백엔드에서는 matrix 가 None).
    """

    backend: str
    ids: list[str]
    matrix: np.ndarray | None = None
    meta: dict = field(default_factory=dict)
    table: Any = None

    @property
    def dim(self) -> int:
        if self.matrix is not None and self.matrix.size:
            return int(self.matrix.shape[1])
        return int(self.meta.get("dim", 0))

    def __len__(self) -> int:
        return len(self.ids) if self.ids else int(self.meta.get("n_chunks", 0))

    def __iter__(self):
        yield self.ids
        yield self.matrix

    def search(self, qvec: np.ndarray, top_k: int) -> list[tuple[str, float]]:
        """코사인 상위 top_k 를 [(chunk_id, score)] 로."""
        if top_k <= 0:
            return []
        q = np.asarray(qvec, dtype=np.float32).reshape(-1)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn

        if self.backend == "lancedb" and self.table is not None:
            res = (self.table.search(q.tolist())
                   .metric("cosine").limit(int(top_k)).to_list())
            # LanceDB 의 cosine 은 거리(0=동일) → 유사도로 되돌린다.
            return [(r["chunk_id"], round(1.0 - float(r.get("_distance", 1.0)), 6))
                    for r in res]

        if self.matrix is None or self.matrix.size == 0:
            return []
        m = self.matrix
        if m.shape[1] != q.shape[0]:
            log(f"      ! 질의 차원 {q.shape[0]} ≠ 인덱스 차원 {m.shape[1]} — 검색 불가")
            return []
        sims = m @ q                     # 저장 시 L2 정규화 → 내적 = 코사인
        k = min(int(top_k), sims.shape[0])
        part = np.argpartition(-sims, k - 1)[:k]
        order = part[np.argsort(-sims[part], kind="stable")]
        return [(self.ids[i], round(float(sims[i]), 6)) for i in order]


# ── 내부 헬퍼 ────────────────────────────────────────────────────────
def _chunk_text_for_index(row: dict) -> str:
    """색인 대상 텍스트: embed_text(컨텍스트 헤더 포함)를 우선 사용."""
    txt = (row.get("embed_text") or "").strip()
    if txt:
        return txt
    header = (row.get("context_header") or "").strip()
    body = (row.get("text") or "").strip()
    return f"{header}\n\n{body}".strip() if header else body


def _save_flat(path: Path, ids: list[str], mat: np.ndarray, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ids/meta 는 유니코드 배열로 저장 → 로드 시 allow_pickle 불필요.
    # 경로 문자열 대신 **파일 객체**로 넘기는 게 핵심이다: np.savez 는 경로를 받으면
    # 확장자가 .npz 가 아닐 때 멋대로 ".npz" 를 덧붙인다. 그러면 설정한 flat_path 와
    # 실제 파일명이 어긋나, build 는 성공했는데 load 는 "인덱스가 없다"고 하는
    # 가장 헷갈리는 오류가 난다.
    with open(path, "wb") as f:
        np.savez(f,
                 vectors=mat,
                 ids=np.array(ids, dtype=np.str_),
                 meta=np.array(json.dumps(meta, ensure_ascii=False), dtype=np.str_))


def _save_lancedb(cfg: dict, ids: list[str], mat: np.ndarray, meta: dict) -> bool:
    """LanceDB 테이블 기록. 성공 True / 미설치·실패 False(→ flat 폴백)."""
    try:
        import lancedb  # 지연 import
    except ImportError:
        log("[6단계] lancedb 미설치 — 벡터 백엔드를 flat(numpy)으로 폴백한다.")
        log("        → 설치하려면: pip install \"lancedb>=0.13\"")
        log("        → 계속 flat 으로 쓰려면 config.yaml 의 vectordb.backend 를 "
            "\"flat\" 으로 바꿔 두면 이 안내가 사라진다.")
        return False
    db_path = utils.resolve(cfg["vectordb"]["path"])
    table = cfg["vectordb"].get("table", "chunks")
    try:
        db_path.mkdir(parents=True, exist_ok=True)
        conn = lancedb.connect(str(db_path))
        rows = [{"chunk_id": cid, "vector": mat[i].tolist()} for i, cid in enumerate(ids)]
        conn.create_table(table, data=rows, mode="overwrite")
        utils.write_json(db_path / "pubnexus_meta.json", meta)
        return True
    except Exception as e:  # noqa: BLE001 — 벡터DB 실패는 flat 폴백으로 흡수
        log(f"      ! LanceDB 기록 실패({type(e).__name__}: {e}) → flat 으로 폴백")
        return False


# ── 진입점 ──────────────────────────────────────────────────────────
def build(config: dict | None = None) -> None:
    """chunks.jsonl → 벡터 인덱스 + BM25 인덱스."""
    cfg = config or utils.load_config()
    chunks_path = utils.resolve(cfg["chunk"]["output"])
    rows = utils.read_jsonl(chunks_path)
    if not rows:
        log(f"[6단계] 청크 파일이 없거나 비어 있다: {chunks_path}")
        log("        → 먼저 5단계 청킹을 실행하라: "
            "python -c \"from pubnexus import chunk; chunk.run()\"")
        raise FileNotFoundError(
            f"청크 파일이 없거나 비어 있다: {chunks_path} — 먼저 chunk.run() 을 실행하라.")

    ecfg = cfg["embedding"]
    vcfg = cfg["vectordb"]
    ids = [r.get("chunk_id") or f"chunk{i:06d}" for i, r in enumerate(rows)]
    texts = [_chunk_text_for_index(r) for r in rows]
    log(f"[6단계] 인덱스 구축: 청크 {len(rows)}개 @ {chunks_path}")

    # 1) BM25 — 의존성이 없어 항상 먼저 만들어 둔다(임베더가 없어도 검색 절반은 산다).
    bm25_path = utils.resolve(vcfg["bm25_path"])
    bm25 = BM25()
    bm25.build(list(zip(ids, texts)))
    bm25.save(bm25_path)
    log(f"  [BM25] 어휘 {len(bm25.postings)}개 · 평균길이 {bm25.avgdl:.1f}토큰 "
        f"→ {bm25_path}")

    # 2) 벡터 — 배치 인코딩
    enc = embed.get_encoder(cfg)
    batch = int(ecfg.get("batch_size", 16) or 16)
    step = max(batch, 200)                  # 진행 로그 간격
    parts: list[np.ndarray] = []
    t0 = time.monotonic()
    for start in range(0, len(texts), step):
        seg = texts[start:start + step]
        parts.append(enc.encode(seg, is_query=False, batch_size=batch))
        done = min(start + step, len(texts))
        log(f"  [{done}/{len(texts)}] 임베딩 {done * 100 // max(len(texts), 1)}% "
            f"({time.monotonic() - t0:.1f}s)")
    mat = (np.vstack(parts) if parts
           else np.zeros((0, enc.dim), dtype=np.float32)).astype(np.float32, copy=False)

    # 청크 원장의 지문(크기·mtime)을 함께 남긴다. 재청킹 후 재색인을 잊으면
    # chunk_id 는 그대로인데 본문만 달라져, 벡터와 표시 텍스트가 조용히 어긋난다
    # (검색은 멀쩡히 도는데 결과만 틀린 가장 잡기 어려운 사고). 로드 시 대조해 경고한다.
    try:
        cst = chunks_path.stat()
        chunks_size, chunks_mtime = int(cst.st_size), int(cst.st_mtime)
    except OSError:
        chunks_size, chunks_mtime = 0, 0

    meta = {
        "schema": _VEC_SCHEMA,
        "encoder": enc.name,
        "model": ecfg.get("model", ""),
        "embedding_backend": str(ecfg.get("backend", "auto")).lower(),
        "dim": int(mat.shape[1]) if mat.size else int(enc.dim),
        "n_chunks": len(ids),
        "normalized": bool(ecfg.get("normalize", True)),
        "chunks_path": str(chunks_path),
        "chunks_size": chunks_size,
        "chunks_mtime": chunks_mtime,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    backend = str(vcfg.get("backend", "flat")).lower()
    meta["backend"] = "lancedb" if backend == "lancedb" else "flat"
    dest: Path | None = None
    if backend == "lancedb" and _save_lancedb(cfg, ids, mat, meta):
        dest = utils.resolve(vcfg["path"])
    if dest is None:                        # flat 백엔드 또는 lancedb 폴백
        meta["backend"] = "flat"
        dest = utils.resolve(vcfg["flat_path"])
        _save_flat(dest, ids, mat, meta)

    log(f"  [벡터] {meta['encoder']} · dim {meta['dim']} · {len(ids)}개 → {dest}")
    log(f"[6단계] 완료 → {dest} (벡터 {len(ids)}, BM25 어휘 {len(bm25.postings)})")


def run(config: dict | None = None) -> None:
    """build() 별칭 — 다른 단계 모듈과 동일한 진입점."""
    build(config)


def load_vector_store(config: dict | None = None) -> VectorStore:
    """저장된 벡터 인덱스를 연다. 설정과 메타가 어긋나면 경고 로그를 남긴다."""
    cfg = config or utils.load_config()
    vcfg = cfg["vectordb"]
    backend = str(vcfg.get("backend", "flat")).lower()
    store: VectorStore | None = None

    if backend == "lancedb":
        db_path = utils.resolve(vcfg["path"])
        try:
            import lancedb  # 지연 import
            conn = lancedb.connect(str(db_path))
            table = conn.open_table(vcfg.get("table", "chunks"))
            meta_p = db_path / "pubnexus_meta.json"
            meta = utils.read_json(meta_p) if meta_p.exists() else {}
            try:
                ids = table.to_arrow().column("chunk_id").to_pylist()
            except Exception:  # noqa: BLE001 — id 목록은 부가정보라 없어도 검색은 된다
                ids = []
            store = VectorStore(backend="lancedb", ids=ids, matrix=None,
                                meta=meta, table=table)
        except ImportError:
            log("[6단계] lancedb 미설치 — flat 인덱스로 폴백해 로드한다.")
        except Exception as e:  # noqa: BLE001 — 벡터DB 오류는 flat 폴백으로 흡수
            log(f"      ! LanceDB 로드 실패({type(e).__name__}: {e}) → flat 폴백")

    if store is None:
        flat_path = utils.resolve(vcfg["flat_path"])
        if not flat_path.exists():
            raise FileNotFoundError(
                f"벡터 인덱스가 없다: {flat_path} — 먼저 index.build() 를 실행하라.")
        with np.load(flat_path) as npz:
            mat = np.asarray(npz["vectors"], dtype=np.float32)
            ids = [str(x) for x in npz["ids"].tolist()]
            meta = json.loads(str(npz["meta"].item())) if "meta" in npz else {}
        if not meta.get("normalized", True) and mat.size:
            mat = embed.l2_normalize(mat)   # 코사인 계산을 위해 로드 시 1회 정규화
        store = VectorStore(backend="flat", ids=ids, matrix=mat, meta=meta)

    # 설정 ↔ 인덱스 메타 불일치 경고(다시 build 해야 하는 상황을 조기에 알린다)
    want_name = embed.encoder_name(cfg)
    if store.meta.get("encoder") and store.meta["encoder"] != want_name:
        log(f"      ! 인덱스 인코더 불일치: 저장 {store.meta['encoder']} ≠ "
            f"설정 {want_name} → index.build() 재실행 권장")
    want_dim = int(cfg["embedding"].get("dim", 0) or 0)
    if want_dim and store.dim and store.dim != want_dim:
        log(f"      ! 인덱스 차원 불일치: 저장 {store.dim} ≠ 설정 {want_dim} "
            "→ index.build() 재실행 권장")
    if store.ids and store.meta.get("n_chunks") and \
            len(store.ids) != store.meta["n_chunks"]:
        log(f"      ! 청크 수 불일치: 벡터 {len(store.ids)} ≠ 메타 "
            f"{store.meta['n_chunks']}")

    # 인덱스를 만든 뒤 chunks.jsonl 이 바뀌었는지(=재청킹 후 재색인 누락) 확인.
    # chunk_id 가 그대로면 검색은 성공하지만 벡터와 본문이 어긋나므로 반드시 알린다.
    if store.meta.get("chunks_size"):
        try:
            cst = utils.resolve(cfg["chunk"]["output"]).stat()
            if (int(cst.st_size) != store.meta["chunks_size"]
                    or int(cst.st_mtime) != store.meta.get("chunks_mtime")):
                log("      ! 청크 원장이 인덱스 생성 이후 변경됨 "
                    f"(색인 시점 {store.meta.get('created_at', '?')}) "
                    "→ 벡터와 본문이 어긋날 수 있다. index.build() 재실행 권장")
        except (OSError, KeyError):
            pass                            # 청크 파일 부재는 여기서 다룰 문제가 아니다
    return store


def load_bm25(config: dict | None = None) -> BM25:
    """저장된 BM25 인덱스를 연다(7단계 검색에서 사용)."""
    cfg = config or utils.load_config()
    return BM25.load(utils.resolve(cfg["vectordb"]["bm25_path"]))


if __name__ == "__main__":
    run()
