"""5~6단계 오케스트레이터 — 정본 JSON을 검색 가능한 인덱스로 만든다.

    python run_rag.py                    # 청킹 → 임베딩 → 인덱스 구축
    python run_rag.py --chunk-only       # 5단계만 (모델 불필요, 수초)
    python run_rag.py --backend hash     # 모델 없이 구조만 검증 (검색 품질 보장 없음)
    python run_rag.py --backend sentence_transformers --device cpu

0~4단계는 run_pilot.py 가 담당한다. 이 스크립트는 normalized/*.json 이 이미 있다고 전제한다.
구축이 끝나면 ask.py 로 질의하고 evaluate.py 로 품질을 측정한다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pubnexus import utils, textfix, chunk, index
from pubnexus.utils import log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--chunk-only", action="store_true", help="5단계 청킹까지만")
    ap.add_argument("--skip-chunk", action="store_true", help="기존 chunks.jsonl 재사용")
    ap.add_argument("--skip-textfix", action="store_true",
                    help="정본 수리를 건너뛴다(이미 수리된 정본을 그대로 쓸 때)")
    ap.add_argument("--textfix-dry-run", action="store_true",
                    help="수리 내역만 보고 파일은 쓰지 않는다")
    ap.add_argument("--backend", default=None,
                    choices=["auto", "sentence_transformers", "onnx", "hash"],
                    help="임베딩 백엔드 (config.yaml 값을 덮어씀)")
    ap.add_argument("--device", default=None, choices=["auto", "cuda", "cpu"])
    ap.add_argument("--vectordb", default=None, choices=["lancedb", "flat"])
    args = ap.parse_args()

    cfg = utils.load_config(args.config)
    if args.backend:
        cfg["embedding"]["backend"] = args.backend
    if args.device:
        cfg["embedding"]["device"] = args.device
    if args.vectordb:
        cfg["vectordb"]["backend"] = args.vectordb

    work = utils.resolve(cfg["project"]["work_dir"])
    norm_dir = work / "normalized"
    n_docs = len(list(norm_dir.glob("*.json"))) if norm_dir.exists() else 0
    if not n_docs:
        log(f"[중단] 정본 문서가 없다: {norm_dir}")
        log("       먼저 run_pilot.py 로 0~4단계를 돌려야 한다.")
        sys.exit(1)

    log("=" * 64)
    log("PubNexus 5~6단계 — 청킹 · 임베딩 · 인덱스")
    log(f"  정본 문서 {n_docs}편 @ {norm_dir}")
    log("=" * 64)

    t0 = time.time()

    # 4.5단계 — 정본 수리. 청킹보다 먼저 돌아야 섹션 타입·본문 결함이 청크에 그대로 굳지 않는다.
    if args.skip_textfix or not (cfg.get("textfix") or {}).get("enabled", True):
        log("[수리] 건너뜀")
    else:
        textfix.run(cfg, dry_run=args.textfix_dry_run)
        if args.textfix_dry_run:
            log("[수리] dry-run 이므로 여기서 멈춘다(정본이 바뀌지 않아 청킹할 의미가 없다)")
            return

    if args.skip_chunk:
        log("[5단계] --skip-chunk: 기존 chunks.jsonl 재사용")
    else:
        chunk.run(cfg)

    if args.chunk_only:
        log(f"완료(청킹까지) — {time.time() - t0:.1f}초")
        return

    if cfg["embedding"]["backend"] == "hash":
        log("[주의] hash 백엔드는 구조 검증용 스텁이다. 검색 품질은 보장하지 않는다.")

    index.build(cfg)

    log("=" * 64)
    log(f"완료 — 총 {time.time() - t0:.1f}초")
    log('  질의:  python ask.py "백반증 NB-UVB 재색소침착률"')
    log("  평가:  python evaluate.py --compare")
    log("=" * 64)


if __name__ == "__main__":
    main()
