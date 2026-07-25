"""검색 품질 평가 — eval_queries.yaml 골든 세트로 재현율을 숫자로 낸다.

    python evaluate.py                 # 하이브리드(기본 설정) 평가
    python evaluate.py -k 10           # top-k 조정
    python evaluate.py --lang ko       # 한국어 질의만
    python evaluate.py --compare       # 하이브리드 vs dense 단독 vs 리랭커 유무 비교

측정 지표
    hit@k   질의의 정답 논문(anchor) 중 하나라도 top-k 안에 들어온 비율 — 재현율 하한
    MRR     첫 정답이 나온 순위의 역수 평균 — 상위 노출 정도
    filter  섹션 필터를 건 질의에서 반환 청크가 전부 필터를 만족하는지 (0/1)

설계서 7단계의 "검색 품질이 부족하면 4B로 상향" 판단을 감으로 하지 않기 위한 도구다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import yaml

from pubnexus import utils, search as search_mod
from pubnexus.utils import log


def load_queries(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _paper_ids(hits) -> list[str]:
    """검색 결과에서 논문 id 를 순서대로(중복 제거) 뽑는다."""
    seen, out = set(), []
    for h in hits:
        pid = (getattr(h, "paper_id", None) or "").lower()
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def eval_one(cfg: dict, q: dict, k: int, lang: str, rerank: bool | None) -> dict:
    lang = q.get("force_lang") or lang          # 의역 질의는 언어가 시험의 핵심이다
    text = q.get(lang) or q.get("ko") or q.get("en") or ""
    anchors = [a.lower() for a in (q.get("must_hit_any") or [])]
    filters = q.get("filters")
    try:
        hits = search_mod.search(cfg, text, k=k, filters=filters, rerank=rerank)
    except Exception as e:                      # 인덱스 미구축 등
        return {"id": q["id"], "error": f"{type(e).__name__}: {e}"}

    pids = _paper_ids(hits)
    rank = None
    for i, pid in enumerate(pids, 1):
        if pid in anchors:
            rank = i
            break

    # 필터 질의: 반환 청크가 전부 필터를 만족하는지
    filter_ok = None
    if filters and filters.get("section_type"):
        want = set(filters["section_type"])
        got = [getattr(h, "section_type", None) for h in hits]
        filter_ok = bool(hits) and all(g in want for g in got)

    return {
        "id": q["id"], "query": text, "n_hits": len(hits),
        "anchors": len(anchors), "rank": rank,
        "hit": (rank is not None) if anchors else None,
        "filter_ok": filter_ok,
        "top": pids[:3],
        "hard": q.get("lexical_overlap") == "low",
    }


def run_set(cfg: dict, spec: dict, k: int, lang: str, rerank: bool | None,
            label: str) -> dict:
    rows = [eval_one(cfg, q, k, lang, rerank) for q in spec["queries"]]
    errs = [r for r in rows if r.get("error")]
    if errs:
        log(f"  !! {len(errs)}개 질의에서 오류: {errs[0]['error']}")

    scored = [r for r in rows if r.get("hit") is not None]
    hit = sum(1 for r in scored if r["hit"])
    mrr = sum(1.0 / r["rank"] for r in scored if r["rank"]) / max(len(scored), 1)
    # 어휘가 겹치는 질의(BM25로 쉬움)와 의역 질의(dense 능력 시험)를 나눠 본다
    hard = [r for r in scored if r.get("hard")]
    easy = [r for r in scored if not r.get("hard")]
    filt = [r for r in rows if r.get("filter_ok") is not None]
    filt_ok = sum(1 for r in filt if r["filter_ok"])

    print(f"\n{'=' * 78}\n{label}  (k={k}, lang={lang}, rerank={rerank})\n{'=' * 78}")
    print(f"{'질의':28s} {'정답순위':>8s} {'판정':>6s}  상위 논문")
    print("-" * 78)
    for r in rows:
        if r.get("error"):
            print(f"{r['id']:28s} {'-':>8s} {'ERROR':>6s}  {r['error'][:40]}")
            continue
        rank = str(r["rank"]) if r["rank"] else ("-" if r["anchors"] else "n/a")
        if r["hit"] is None:
            mark = "OK" if r["filter_ok"] else ("FAIL" if r["filter_ok"] is False else "-")
        else:
            mark = "HIT" if r["hit"] else "MISS"
        print(f"{r['id']:28s} {rank:>8s} {mark:>6s}  {', '.join(r['top'][:2])[:36]}")
    print("-" * 78)
    print(f"hit@{k} = {hit}/{len(scored)} ({hit / max(len(scored), 1):.1%})   MRR = {mrr:.3f}"
          + (f"   필터정합 {filt_ok}/{len(filt)}" if filt else ""))
    if hard:
        he = sum(1 for r in easy if r["hit"])
        hh = sum(1 for r in hard if r["hit"])
        print(f"   ├ 어휘겹침 질의 {he}/{len(easy)}   └ 의역 질의(dense 시험) {hh}/{len(hard)}")
    return {"label": label, "hit": hit, "n": len(scored), "mrr": mrr,
            "hard": (sum(1 for r in hard if r["hit"]), len(hard)), "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--queries", default=None, help="기본: pubnexus/eval_queries.yaml")
    ap.add_argument("-k", type=int, default=None)
    ap.add_argument("--lang", choices=["ko", "en"], default="en",
                    help="임베딩 모델이 다국어여도 코퍼스는 영문이라 en 이 기본")
    ap.add_argument("--compare", action="store_true",
                    help="하이브리드 / dense 단독 / 리랭커 유무를 나란히 비교")
    ap.add_argument("--backend", default=None,
                    choices=["auto", "sentence_transformers", "onnx", "hash"],
                    help="인덱스를 만든 백엔드와 반드시 같아야 한다")
    ap.add_argument("--no-rerank", action="store_true",
                    help="리랭커 없이 (CPU에서 훨씬 빠름)")
    args = ap.parse_args()

    cfg = utils.load_config(args.config)
    if args.backend:
        cfg["embedding"]["backend"] = args.backend
    if args.no_rerank:
        cfg["reranker"]["enabled"] = False
    qpath = Path(args.queries) if args.queries else utils.ROOT / "pubnexus" / "eval_queries.yaml"
    spec = load_queries(qpath)
    k = args.k or spec.get("default_k", 8)

    log(f"[평가] 골든 세트 {len(spec['queries'])}개 질의, 코퍼스={spec.get('corpus')}")

    if not args.compare:
        rr = False if args.no_rerank else None
        run_set(cfg, spec, k, args.lang, rr, "기본 설정")
        return

    import copy
    results = []
    results.append(run_set(cfg, spec, k, args.lang, False, "하이브리드(BM25+dense), 리랭커 없음"))

    dense_only = copy.deepcopy(cfg)
    dense_only["search"]["hybrid"] = False
    results.append(run_set(dense_only, spec, k, args.lang, False, "dense 단독, 리랭커 없음"))

    if cfg.get("reranker", {}).get("enabled"):
        results.append(run_set(cfg, spec, k, args.lang, True, "하이브리드 + 리랭커"))

    print(f"\n{'=' * 78}\n요약\n{'=' * 78}")
    for r in results:
        hh, hn = r["hard"]
        print(f"  {r['label']:38s} hit@{k} {r['hit']}/{r['n']}  MRR {r['mrr']:.3f}"
              + (f"  의역 {hh}/{hn}" if hn else ""))
    print("\n※ 하이브리드가 dense 단독보다 나아야 정상이다(의학 약어·측정도구명 때문).")


if __name__ == "__main__":
    main()
