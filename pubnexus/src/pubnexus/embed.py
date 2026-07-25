"""6단계 — 임베딩 백엔드(Qwen3-Embedding · ONNX · 해시 스텁).

검색 품질의 대부분은 임베더에서 갈리므로, 백엔드를 설정으로 갈아끼울 수 있게
인코더를 한 겹 추상화했다. 세 가지 구현이 같은 인터페이스(Encoder)를 노출한다.

  · SentenceTransformerEncoder — 운영용. Qwen3-Embedding-0.6B.
      질의에만 instruction 접두를 붙이고(문서는 원문 그대로), Matryoshka 절단 후
      재정규화까지 직접 처리한다.
  · OnnxEncoder — GPU 없는 PC용. sentence-transformers 의 onnx 백엔드를 태운다.
  · HashEncoder — 모델·GPU 없이 5~7단계 배관만 검증하는 결정적 스텁(품질 보장 없음).

무거운 의존성(torch / sentence-transformers)은 **전부 함수 안에서 지연 import** 한다.
이 모듈을 import 하는 것만으로 모델을 내려받거나 CUDA 를 건드리는 일은 없어야 한다.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

import numpy as np

from . import utils
from .utils import log

# Qwen3-Embedding 공식 프롬프트 형식(레포의 config_sentence_transformers.json 과 동일).
# 콜론 뒤에 공백이 없다 — ST 기본 prompts["query"] 와 글자 단위로 맞춘 값이다.
QUERY_PROMPT_FMT = "Instruct: {task}\nQuery:{text}"

# HashEncoder 전용: 단어 토큰 정규식(하이픈·슬래시 결합 유지)과 문자 n-gram 크기
# 문자 클래스는 index.BM25.tokenize 와 동일하게 "밑줄 뺀 유니코드 낱말 문자"로 둔다.
# (ASCII 화이트리스트면 TGF-β·μg·Sjögren 같은 표기가 잘려 dense/BM25 표면이 어긋난다)
_WORD_RE = re.compile(r"[^\W_]+(?:[-/][^\W_]+)*")
_NGRAM_N = 3
_GRAM_WEIGHT = 0.35        # 문자 n-gram 은 단어보다 낮은 가중(표면형 노이즈 억제)

# blake2b 결과 캐시: 3-gram 은 문서 간 대량 중복이라 캐시 효과가 크다.
_HASH_CACHE: dict[str, int] = {}
_HASH_CACHE_MAX = 500_000


# ── 공통 헬퍼 ────────────────────────────────────────────────────────
def _hash_int(prefix: str, token: str) -> int:
    """결정적 해시(파이썬 내장 hash 는 프로세스마다 salt 가 달라 쓰면 안 된다)."""
    key = prefix + token
    v = _HASH_CACHE.get(key)
    if v is None:
        v = int.from_bytes(
            hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "big")
        if len(_HASH_CACHE) >= _HASH_CACHE_MAX:
            _HASH_CACHE.clear()
        _HASH_CACHE[key] = v
    return v


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    """행 단위 L2 정규화(영벡터는 그대로 둔다 — 0 나눗셈 방지)."""
    if mat.size == 0:
        return mat
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (mat / norms).astype(np.float32, copy=False)


def _pick_device(want: str) -> str:
    """device 설정이 auto 면 CUDA 가용 여부로 결정."""
    if want and want != "auto":
        return want
    try:
        import torch  # 지연 import — 없으면 그냥 cpu
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001 — torch 부재/초기화 실패는 cpu 로 처리
        pass
    return "cpu"


# ── 인코더 인터페이스 ────────────────────────────────────────────────
class Encoder:
    """임베딩 백엔드 프로토콜.

    구현체는 name(식별자) · dim(차원) · encode() 세 가지를 반드시 노출한다.
    encode 는 (n, dim) float32 배열을 돌려주며, is_query=True 일 때만
    질의용 instruction 접두를 적용한다(문서 쪽에는 절대 붙이지 않는다).
    """

    name: str = "base"
    dim: int = 0

    def encode(self, texts: list[str], is_query: bool = False,
               batch_size: int | None = None) -> np.ndarray:
        raise NotImplementedError

    def encode_one(self, text: str, is_query: bool = False) -> np.ndarray:
        """단건 인코딩 편의 래퍼 — (dim,) 벡터."""
        return self.encode([text], is_query=is_query)[0]

    def __repr__(self) -> str:  # 로그 가독성
        return f"<{type(self).__name__} name={self.name} dim={self.dim}>"


class HashEncoder(Encoder):
    """의존성이 numpy 뿐인 결정적 해시 임베더 — **구조 검증 전용 스텁**.

    ⚠ 검색 품질을 전혀 보장하지 않는다. 의미(semantic) 유사도가 아니라
    문자 3-gram·단어 표면형만 해시 버킷에 뿌린 것이므로, 동의어·다국어 질의는
    잡지 못한다. GPU·모델 없이 5~7단계 배관(청킹→인덱스→하이브리드 검색)이
    끝까지 도는지 오프라인에서 점검하기 위한 용도이며, 실제 운영 인덱스를
    이걸로 만들면 안 된다.

    결정성: 파이썬 내장 hash() 는 문자열에 대해 프로세스마다 salt 가 달라지므로
    쓰지 않고 blake2b 를 쓴다. 같은 입력이면 실행·머신과 무관하게 같은 벡터다.
    """

    def __init__(self, dim: int = 1024, normalize: bool = True):
        self.dim = max(16, int(dim))
        self.normalize = normalize
        self.name = f"hash-{self.dim}"

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        t = utils.norm_text(text or "").lower()
        if not t:
            return vec
        # 1) 단어 토큰(하이픈 결합 유지) — sublinear tf
        for tok, tf in Counter(_WORD_RE.findall(t)).items():
            h = _hash_int("w:", tok)
            idx = h % self.dim
            sign = 1.0 if (h // self.dim) & 1 else -1.0
            vec[idx] += sign * (1.0 + math.log(tf))
        # 2) 문자 3-gram — 철자 변형/약어 부분일치 흡수
        grams = Counter(t[i:i + _NGRAM_N] for i in range(len(t) - _NGRAM_N + 1))
        for g, tf in grams.items():
            h = _hash_int("g:", g)
            idx = h % self.dim
            sign = 1.0 if (h // self.dim) & 1 else -1.0
            vec[idx] += _GRAM_WEIGHT * sign * (1.0 + math.log(tf))
        return vec

    def encode(self, texts: list[str], is_query: bool = False,
               batch_size: int | None = None) -> np.ndarray:
        # is_query 는 무시한다(스텁에는 instruction 개념이 없음).
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        mat = np.vstack([self._vector(t) for t in texts]).astype(np.float32, copy=False)
        return l2_normalize(mat) if self.normalize else mat


class SentenceTransformerEncoder(Encoder):
    """운영용 인코더 — sentence-transformers 로 Qwen3-Embedding 을 로드한다.

    · 질의에만 "Instruct: {task}\\nQuery:{text}" 접두를 붙인다(문서는 원문 그대로).
    · device="auto" 면 CUDA 가용 시 cuda, 아니면 cpu.
    · dim 이 모델 차원보다 작으면 Matryoshka 절단 후 **재정규화**한다
      (절단→정규화 순서를 ST 에 맡기지 않고 직접 처리).
    """

    def __init__(self, cfg: dict, st_backend: str = "torch"):
        ecfg = cfg["embedding"]
        model_id = ecfg["model"]
        self.task = ecfg.get("query_instruction", "")
        self.normalize = bool(ecfg.get("normalize", True))
        self.batch_size = int(ecfg.get("batch_size", 16))
        self.device = _pick_device(str(ecfg.get("device", "auto")))
        self.st_backend = st_backend

        try:
            from sentence_transformers import SentenceTransformer  # 지연 import
        except ImportError as e:
            raise RuntimeError(_INSTALL_HINT_ST) from e

        log(f"[6단계] 임베더 로드: {model_id} (device={self.device}, "
            f"backend={st_backend})")
        kwargs: dict = {"device": self.device}
        model_kwargs: dict = {}
        if st_backend == "torch":
            # flash-attn 은 Windows 휠이 없어 sdpa 를 쓴다. bf16 은 CUDA 에서만.
            model_kwargs["attn_implementation"] = "sdpa"
            if self.device.startswith("cuda"):
                model_kwargs["torch_dtype"] = "bfloat16"
        else:
            kwargs["backend"] = st_backend      # onnx 는 ORT 가 설정을 잡는다
        try:
            self.model = SentenceTransformer(model_id, model_kwargs=model_kwargs, **kwargs)
        except TypeError:
            # 구버전 ST: model_kwargs/backend 인자 미지원 → 최소 인자로 재시도
            self.model = SentenceTransformer(model_id, device=self.device)

        # ST 5.x 에서 get_sentence_embedding_dimension → get_embedding_dimension 로 개명.
        # 구버전도 지원해야 하므로 새 이름을 먼저 찾고 없으면 옛 이름으로 떨어진다.
        _dim_fn = (getattr(self.model, "get_embedding_dimension", None)
                   or self.model.get_sentence_embedding_dimension)
        native = int(_dim_fn() or 0)
        want = int(ecfg.get("dim", native) or native)
        if native and want > native:
            log(f"      ! 설정 dim={want} 이 모델 차원 {native} 보다 큼 → {native} 로 조정")
            want = native
        self.native_dim = native or want
        self.dim = want
        self.name = f"st:{model_id}@{self.dim}" if st_backend == "torch" \
            else f"onnx:{model_id}@{self.dim}"

    def _prepare(self, texts: list[str], is_query: bool) -> list[str]:
        if not is_query:
            return texts                     # 문서는 접두 없음(모델카드 명시)
        return [QUERY_PROMPT_FMT.format(task=self.task, text=t) for t in texts]

    def encode(self, texts: list[str], is_query: bool = False,
               batch_size: int | None = None) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = self.model.encode(
            self._prepare(texts, is_query),
            batch_size=int(batch_size or self.batch_size),
            normalize_embeddings=False,      # 절단 후 직접 정규화한다
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vecs = np.asarray(vecs, dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        if self.dim and vecs.shape[1] > self.dim:
            vecs = vecs[:, :self.dim]        # Matryoshka 절단
        return l2_normalize(vecs) if self.normalize else vecs


# 설치 안내 문구(예외 메시지·로그 양쪽에서 재사용)
_INSTALL_HINT_ST = (
    "sentence-transformers 가 설치돼 있지 않다(embedding.backend=auto 는 자동으로 "
    "저품질 hash 백엔드로 떨어지지 않는다).\n"
    "        → 설치: pip install \"sentence-transformers>=3.0\" \"transformers>=4.51\"\n"
    "        → torch 는 사양에 맞춰 별도 설치(CPU: pip install torch)\n"
    "        → 모델·GPU 없이 배관만 점검하려면 config.yaml 의 "
    "embedding.backend 를 \"hash\" 로 두고 실행할 것(검색 품질 보장 없음)."
)


def _st_available() -> bool:
    """sentence-transformers import 가능 여부(모델 로딩은 하지 않는다)."""
    import importlib.util
    return importlib.util.find_spec("sentence_transformers") is not None


def encoder_name(config: dict | None = None) -> str:
    """모델을 로드하지 않고 인코더 식별자만 계산한다(인덱스 메타 대조용)."""
    cfg = config or utils.load_config()
    ecfg = cfg["embedding"]
    backend = str(ecfg.get("backend", "auto")).lower()
    dim = int(ecfg.get("dim", 1024) or 1024)
    model_id = ecfg.get("model", "")
    if backend == "hash":
        return f"hash-{max(16, dim)}"
    if backend == "onnx":
        return f"onnx:{model_id}@{dim}"
    return f"st:{model_id}@{dim}"          # auto | sentence_transformers


def get_encoder(config: dict | None = None) -> Encoder:
    """설정의 embedding.backend 에 맞는 인코더를 만든다.

    backend=auto 는 sentence-transformers 가 있으면 그것을 쓰고, 없으면
    **명확한 한국어 예외**를 던진다. 자동으로 hash 로 떨어지면 사용자가
    저품질 인덱스를 모르고 쓰게 되므로 폴백하지 않는다.
    """
    cfg = config or utils.load_config()
    ecfg = cfg["embedding"]
    backend = str(ecfg.get("backend", "auto")).lower()

    if backend == "hash":
        enc = HashEncoder(dim=int(ecfg.get("dim", 1024) or 1024),
                          normalize=bool(ecfg.get("normalize", True)))
        log(f"[6단계] 해시 스텁 인코더 사용: {enc.name} "
            "(구조 검증 전용 — 검색 품질 보장 없음)")
        return enc

    if backend in ("auto", "sentence_transformers"):
        if backend == "auto" and not _st_available():
            log("[6단계] sentence-transformers 미설치 — auto 백엔드는 폴백하지 않는다.")
            for line in _INSTALL_HINT_ST.splitlines()[1:]:
                log(line if line.startswith(" ") else f"        {line}")
            raise RuntimeError(_INSTALL_HINT_ST)
        return SentenceTransformerEncoder(cfg, st_backend="torch")

    if backend == "onnx":
        return SentenceTransformerEncoder(cfg, st_backend="onnx")

    raise ValueError(
        f"알 수 없는 embedding.backend: {backend!r} "
        "(auto | sentence_transformers | onnx | hash 중 하나여야 한다)")


def run(config: dict | None = None) -> None:
    """자가 점검 — 인코더를 로드해 샘플 2건을 인코딩하고 차원/유사도를 보고한다."""
    cfg = config or utils.load_config()
    enc = get_encoder(cfg)
    docs = ["Narrowband UVB phototherapy induced repigmentation in vitiligo patients.",
            "Tofacitinib is a Janus kinase inhibitor used for alopecia areata."]
    dv = enc.encode(docs)
    qv = enc.encode(["백반증 NB-UVB 재색소침착률"], is_query=True)
    sims = (dv @ qv[0]) if dv.size and qv.size else np.array([])
    log(f"[6단계] 자가점검: {enc.name} · dim {dv.shape[1] if dv.size else enc.dim} · "
        f"질의-문서 코사인 {[round(float(s), 4) for s in sims]}")


if __name__ == "__main__":
    run()
