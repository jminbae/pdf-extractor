"""7단계 CLI — 코퍼스에 자연어로 묻고 근거 청크를 돌려받는다.

    python ask.py "백반증 NB-UVB 재색소침착률" -k 8
    python ask.py "Cox proportional hazards" --section methods --year 2018-2025
    python ask.py "VASI threshold" --no-rerank --json > hits.json

결과 본문은 stdout, 진행/경고 로그는 stderr 로 나간다(utils.log 규칙).
따라서 `--json` 출력만 파이프로 받아도 로그가 섞이지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pubnexus import utils, search as search_mod
from pubnexus.utils import log


def _safe_streams(json_mode: bool) -> None:
    """한글 Windows 콘솔(cp949)은 en-dash 같은 글자를 못 그린다.

    논문 본문에는 '2-5' 의 en-dash, em-dash 가 흔해서 그냥 print 하면
    UnicodeEncodeError 로 CLI 가 죽는다. 사람이 읽는 출력은 대체문자로 넘기고,
    기계가 읽는 --json 은 파이프/리다이렉트에서 온전한 UTF-8 이 되게 한다.
    """
    try:
        if json_mode:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        else:
            sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass    # 파이썬이 오래됐거나 스트림이 텍스트가 아니면 그냥 넘어간다

# 청크 필터에 쓰는 닫힌 어휘(schema.py 의 section_type / chunk.py 의 kind)
# back 은 config.yaml 의 chunk.exclude_back_matter 가 true 인 한 청크가 만들어지지
# 않으므로 항상 0건이다. 그 설정을 false 로 바꿨을 때만 의미가 있다.
SECTION_TYPES = ("abstract", "intro", "methods", "results", "discussion", "back", "other")
KINDS = ("abstract", "text", "table", "figure")

# "2018-2025" | "2018-" | "-2015" | "2020"
_YEAR_RE = re.compile(r"^\s*(\d{4})?\s*(-)?\s*(\d{4})?\s*$")


def parse_year(spec: str) -> tuple[int | None, int | None]:
    """연도 범위 문자열 → (year_min, year_max)."""
    m = _YEAR_RE.match(spec or "")
    if not m or not (m.group(1) or m.group(3)):
        raise ValueError(f"연도 형식을 이해할 수 없습니다: {spec!r} "
                         "(예: 2018-2025, 2018-, -2015, 2020)")
    lo, dash, hi = m.group(1), m.group(2), m.group(3)
    if not dash:
        # "2018 2025" 처럼 하이픈 없이 두 해가 오면 뒤쪽이 조용히 버려진다 → 막는다
        if lo and hi:
            raise ValueError(f"연도 범위에는 하이픈이 필요합니다: {spec!r} "
                             f"(예: {lo}-{hi})")
        y = int(lo or hi)                     # "2020" → 그 해만
        return y, y
    return (int(lo) if lo else None), (int(hi) if hi else None)


def split_multi(values: list[str] | None) -> list[str]:
    """--section results,methods 와 --section results --section methods 를 모두 받는다."""
    out: list[str] = []
    for v in values or []:
        out += [x.strip().lower() for x in v.split(",") if x.strip()]
    return out


def build_filters(args) -> dict:
    f: dict = {}
    body_text = split_multi(args.section)
    bad = [s for s in sections if s not in SECTION_TYPES]
    if bad:
        raise ValueError(f"모르는 섹션 타입: {', '.join(bad)} "
                         f"(가능: {', '.join(SECTION_TYPES)})")
    if sections:
        f["section_type"] = sections

    kinds = split_multi(args.kind)
    bad = [x for x in kinds if x not in KINDS]
    if bad:
        raise ValueError(f"모르는 청크 종류: {', '.join(bad)} (가능: {', '.join(KINDS)})")
    if kinds:
        f["kind"] = kinds

    if args.year:
        ymin, ymax = parse_year(args.year)
        if ymin is not None:
            f["year_min"] = ymin
        if ymax is not None:
            f["year_max"] = ymax
    if args.journal:
        f["journal"] = args.journal
    if args.pub_type:
        f["pub_types"] = split_multi(args.pub_type)
    if args.paper:
        f["paper_id"] = [p.strip().lower() for p in split_multi(args.paper)]
    return f


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ask.py",
        description="PubNexus 하이브리드 검색(dense + BM25 → RRF → 리랭커)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="예시:\n"
               '  python ask.py "백반증 NB-UVB 재색소침착률" -k 8\n'
               '  python ask.py "survival analysis" --section methods --year 2018-2025\n'
               '  python ask.py "VASI" --kind table --json\n'
               '  python ask.py "prevalence" --year=-2015        # 2015년 이전\n')
    ap.add_argument("query", nargs="+", help="검색 질의(따옴표 없이 여러 낱말도 가능)")
    ap.add_argument("-k", "--top-k", type=int, default=None,
                    help="반환할 청크 수 (기본: config 의 reranker.top_k_out)")
    ap.add_argument("--section", action="append", metavar="TYPE",
                    help=f"섹션 필터, 쉼표 구분 가능 ({'|'.join(SECTION_TYPES)})")
    ap.add_argument("--kind", action="append", metavar="KIND",
                    help=f"청크 종류 필터 ({'|'.join(KINDS)})")
    ap.add_argument("--year", metavar="RANGE",
                    help="연도 범위: 2018-2025 / 2018- / -2015 / 2020")
    ap.add_argument("--journal", metavar="TEXT", help="저널명 부분일치(대소문자 무시)")
    ap.add_argument("--pub-type", action="append", metavar="TYPE",
                    help='출판 유형 필터 (예: "Randomized Controlled Trial")')
    ap.add_argument("--paper", action="append", metavar="DOI",
                    help="특정 논문(paper_id/DOI)으로 한정, 쉼표 구분 가능")
    ap.add_argument("--rerank", dest="rerank", action="store_true", default=None,
                    help="리랭커 강제 사용")
    ap.add_argument("--no-rerank", dest="rerank", action="store_false",
                    help="리랭커 끄기(모델 없이 빠르게 확인할 때)")
    ap.add_argument("--dense-only", action="store_true",
                    help="BM25 없이 dense 단독 검색(하이브리드 효과 비교용)")
    ap.add_argument("--json", action="store_true", help="기계가 읽는 JSON 을 stdout 으로")
    ap.add_argument("--width", type=int, default=100, help="터미널 출력 폭(기본 100)")
    ap.add_argument("--config", default=None, help="config.yaml 경로")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    _safe_streams(args.json)
    query = " ".join(args.query).strip()
    if not query:
        log("질의가 비었습니다.")
        return 2
    if args.top_k is not None and args.top_k < 1:
        log(f"인자 오류: -k 는 1 이상이어야 합니다 (받은 값: {args.top_k})")
        return 2

    try:
        cfg = utils.load_config(args.config)
    except OSError as e:
        log(f"설정을 읽지 못했습니다: {type(e).__name__}: {e}")
        return 2

    if args.dense_only:
        cfg.setdefault("search", {})["hybrid"] = False

    try:
        filters = build_filters(args)
    except ValueError as e:
        log(f"인자 오류: {e}")
        return 2

    try:
        hits = search_mod.search(cfg, query, k=args.top_k,
                                 filters=filters or None, rerank=args.rerank)
    except search_mod.IndexNotReady as e:
        # 원인별 복구 명령은 search.py 가 이미 stderr 로 안내했으므로 첫 줄만 다시 짚는다
        log(f"검색을 실행할 수 없습니다: {str(e).strip().splitlines()[0]}")
        return 3
    except KeyboardInterrupt:
        log("중단됨")
        return 130
    except Exception as e:      # noqa: BLE001 — CLI 는 트레이스백 대신 한 줄로
        log(f"검색 실패: {type(e).__name__}: {e}")
        return 1

    if args.json:
        payload = {
            "query": query,
            "k": args.top_k,
            "filters": filters,
            "hybrid": bool(cfg.get("search", {}).get("hybrid", True)),
            "rerank": args.rerank,
            "n_hits": len(hits),
            "hits": [h.to_dict() for h in hits],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        head = f'질의: "{query}"'
        if filters:
            head += "  |  필터: " + ", ".join(f"{k}={v}" for k, v in filters.items())
        print(head)
        print("=" * min(args.width, 120))
        print(search_mod.format_hits(hits, width=args.width))
    return 0


if __name__ == "__main__":
    sys.exit(main())
