"""파일럿 오케스트레이터 — 0~4단계를 순서대로 실행.

    python run_pilot.py            # 전체
    python run_pilot.py --skip-grobid   # GROBID 서비스 없을 때 XML 경로만

GROBID 서비스가 없으면 2단계b는 자동으로 건너뛴다(비-PMC 논문은 미처리로 남음).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pubnexus import (utils, inventory, metadata, pmc_xml, grobid_client,
                      pdf_fallback, refmatch, qc, render, audit)
from pubnexus.utils import log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--skip-grobid", action="store_true")
    ap.add_argument("--force-meta", action="store_true")
    args = ap.parse_args()

    cfg = utils.load_config(args.config)
    log("=" * 64)
    log("PubNexus 파일럿 파이프라인")
    log("=" * 64)

    inventory.build_manifest(cfg)
    metadata.collect_all(cfg, force=args.force_meta)
    pmc_xml.run(cfg)

    if args.skip_grobid:
        log("[2단계b] --skip-grobid: GROBID 경로 건너뜀")
    else:
        grobid_client.run(cfg)   # GROBID 서버 있으면 비-PMC 처리(고품질)

    # GROBID가 처리 못한 비-PMC 논문은 PyMuPDF 폴백으로 보완(저품질, 승격 대상)
    pdf_fallback.run(cfg)

    refmatch.run(cfg)   # 참고문헌 DOI를 Crossref 참조목록과 대조해 보강
    qc.run(cfg)
    render.run(cfg)
    audit.run(cfg)      # 내용 수준 전수 감사

    # 요약
    work = utils.resolve(cfg["project"]["work_dir"])
    manifest = utils.read_jsonl(work / "manifest.jsonl")
    norm = list((work / "normalized").glob("*.json"))
    reports = utils.read_jsonl(work / "qc_report.jsonl")
    n_primary = sum(r.get("is_primary") for r in manifest)
    n_pass = sum(r.get("pass") for r in reports)
    log("=" * 64)
    log(f"완료: 고유논문 {n_primary}편 중 정규화 {len(norm)}편, QC PASS {n_pass}편")
    log(f"산출물: {work}")
    log("=" * 64)


if __name__ == "__main__":
    main()
