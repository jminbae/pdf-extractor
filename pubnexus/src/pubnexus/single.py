"""단일 PDF 진입점 — PDF 한 편을 즉시 정본 문서 dict 로 만든다(검수용 데스크탑 앱).

기존 파이프라인은 폴더 단위 배치다(inventory → manifest.jsonl → metadata → meta/*.json
→ pmc_xml/grobid/pdf_fallback → normalized/*.json). 검수 앱은 "PDF 한 편을 열어
그 자리에서 보여줘야" 하므로, 같은 설계 원칙과 **같은 단위 함수**를 그대로 쓰되
원장 파일 없이 메모리에서 한 편을 관통시킨다.

  1) probe      inventory.probe_pdf          — DOI·제목·텍스트층 판정
  2) DOI 보강   inventory.verify_truncated_dois / resolve_missing_dois
  3) 메타데이터 metadata.collect_one         — Europe PMC·Crossref·OpenAlex·PubMed·iCite
  4) 본문       pmc_xml.parse → grobid_client.parse_tei → pdf_fallback.parse_pdf
  5) 마무리     textfix.fix_document + qc.score_doc

원칙:
  · **원본 work_dir(data/)에는 아무것도 쓰지 않는다.** 배치 함수들이 work_dir 을
    전제하므로 임시 폴더로 갈아끼운 cfg 사본을 넘긴다(_prepare_cfg).
    실제 산출물은 호출부가 지정한 out_json 하나뿐이다.
  · **네트워크가 없어도 죽지 않는다.** 메타데이터 API·GROBID 가 전부 죽어도
    PyMuPDF 로 본문만이라도 뽑아 돌려준다. 제목이 비면 PDF 1페이지 추정 제목을 쓴다.
  · **어떤 경로로 뽑았는지 반환 dict 의 source 로 알 수 있다**
    ("pmc_xml" | "grobid" | "pdf_fallback"). 그 판단 근거는 qc.notes 에 남긴다.
  · 폴더 처리는 파일별 격리 — 한 편이 실패해도 나머지는 계속된다.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import utils
from .utils import log

# 설정 누락(구버전 config.yaml·frozen 배포)에도 동작하도록 하는 기본값.
# 사용자 cfg 가 항상 우선하며, 없는 키만 여기서 채운다.
_DEFAULTS: dict = {
    "project": {"work_dir": ""},          # 실행 시 임시 폴더로 덮어씀(data/ 보호)
    "identify": {
        "doi_regex": r"10\.\d{4,9}/[-._;()/:\w]+",
        "scan_pages": 2,
        "scanned_char_threshold": 200,
        "resolve_missing_doi": True,
        "verify_truncated_doi": True,
        "title_match_threshold": 90,
    },
    "metadata": {
        "email": "", "ncbi_api_key": "",
        "request_delay_sec": 0.34, "timeout_sec": 20,
    },
    "fulltext": {
        "europepmc_base": "https://www.ebi.ac.uk/europepmc/webservices/rest",
    },
    "grobid": {
        "url": "http://localhost:8070",
        "consolidate_header": 1, "include_raw_citations": 1,
        "segment_sentences": 0, "timeout_sec": 300,
        "probe_timeout_sec": 2,           # 살아있는지 확인은 짧게(없으면 바로 폴백)
    },
    "textfix": {"enabled": True, "carry_forward": True},
}


# ── 공개 API ────────────────────────────────────────────────────────
def default_json_path(pdf_path: str | Path) -> Path:
    """이 PDF 의 정본이 놓일 자리 — **앱 저장소** 안이다(PDF 옆이 아니다).

    2026-07-26 원장 결정. PDF 폴더를 어지럽히지 않고, 읽기 전용 위치에서도
    동작하며, ResearchMap 의 `%LOCALAPPDATA%` 저장소와 방식이 같다.
    짝은 **파일 내용(sha1)** 으로 맺으므로 PDF 를 옮기거나 이름을 바꿔도 찾는다.
    """
    from . import store
    return store.doc_path(store.file_sha1(pdf_path))


def is_extracted(pdf_path: str | Path, out_json: str | Path | None = None) -> bool:
    """이미 처리된 PDF 인가(정상적인 정본이 저장소에 있는가).

    깨진/반쪽 JSON 은 '없음'으로 본다 — 다시 처리하는 편이 안전하다.
    """
    p = _io_path(Path(out_json) if out_json else default_json_path(pdf_path))
    try:
        if not p.exists() or p.stat().st_size == 0:
            return False
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001 — 읽기/파싱 실패 = 미처리로 간주
        return False
    return isinstance(doc, dict) and bool(
        doc.get("schema_version") or doc.get("body_text") is not None)


def extract_one(pdf_path: str | Path, config: dict | None = None, *,
                out_json: str | Path | None = None,
                on_progress=None, use_grobid: bool = True) -> dict:
    """PDF 한 편 → 정본 문서 dict. out_json 을 주면 그 경로에 저장한다.

    처리 순서(설계 원칙 그대로):
      1) probe: DOI·제목·텍스트층 판정         (inventory.probe_pdf)
      2) DOI 없으면 Crossref 제목 매칭으로 보강 (inventory.resolve_missing_dois)
      3) 메타데이터 API 수집                    (metadata.collect_one)
      4) 본문: PMC 원문 XML → GROBID → PyMuPDF 폴백
      5) 정본 스키마로 조립(textfix 수리 + qc 점수)

    네트워크·GROBID 가 없어도 예외를 던지지 않는다. PDF 자체를 열 수 없을 때만
    예외를 올린다(호출부가 '이 파일은 실패'로 표면화할 수 있게).
    """
    cfg = _prepare_cfg(config)
    ctx = _Ctx(cfg)
    return _extract(Path(pdf_path), cfg, ctx, out_json=out_json,
                    on_progress=on_progress, use_grobid=use_grobid)


def extract_folder(folder: str | Path, config: dict | None = None, *,
                   on_progress=None, should_cancel=None,
                   overwrite: bool = False, queue: "WorkQueue | None" = None) -> dict:
    """폴더 안 모든 PDF 를 처리해 **앱 저장소**에 정본을 쓴다(PDF 옆이 아니다).

    반환 {'total','done','skipped','failed','failures':[(파일명, 사유)], 'cancelled'}

    파일별 격리 — 한 편의 실패가 전체를 멈추지 않는다. 저장소에 이미 정본이
    있으면 건너뛴다(overwrite=True 면 다시 처리). 짝은 파일 내용(sha1)으로 맺으므로
    PDF 를 옮기거나 이름을 바꿔도 다시 뽑지 않는다.
    should_cancel() 이 True 면 지금까지 쓴 것은 그대로 두고 즉시 멈춘다
    (재실행하면 남은 것만 처리).

    여러 편을 동시에 처리한다(_worker_count). 동시 편수는 남은 메모리에 맞춰
    정하고, GROBID 호출만 따로 좁힌다 — 몰리면 서비스가 죽는다.

    queue 를 넘기면 처리 **순서를 바꿀 수 있다**. 화면이 `queue.bump(pdf)` 로
    사용자가 지금 고른 논문을 맨 앞으로 당긴다(WorkQueue 참고).
    """
    root = Path(folder)
    pdfs = sorted(p for p in root.rglob("*.pdf") if p.is_file())
    total = len(pdfs)
    stats: dict = {"total": total, "done": 0, "skipped": 0, "failed": 0,
                   "failures": [], "cancelled": False}

    cfg = _prepare_cfg(config)
    ctx = _Ctx(cfg)                      # HTTP 세션·GROBID 판정을 폴더 전체에서 재사용
    log(f"[single] 폴더 처리 시작: PDF {total}개 @ {root}")
    _emit(on_progress, "start", 0, total, "", f"PDF {total}개 처리 시작")

    workers = _worker_count(cfg)
    log(f"[single] 동시 처리 {workers}편")
    lock = threading.Lock()
    done_n = 0                       # 진행 표시용 순번(완료 순서)

    def work(idx: int, pdf: Path) -> None:
        """한 편. 예외를 밖으로 내보내지 않는다 — 한 편의 실패가 전체를 멈추지 않게."""
        nonlocal done_n
        if _cancelled(should_cancel):
            return
        dest = default_json_path(pdf)
        if not overwrite and is_extracted(pdf, dest):
            with lock:
                stats["skipped"] += 1
                done_n += 1
                n = done_n
            log(f"  [{n}/{total}] 건너뜀(이미 처리): {pdf.name}")
            _emit(on_progress, "skip", n, total, pdf.name,
                  f"[{n}/{total}] 건너뜀 {pdf.name}")
            return
        try:
            doc = _extract(pdf, cfg, ctx, out_json=dest,
                           on_progress=_forward(on_progress, idx, total, pdf.name),
                           use_grobid=True)
            with lock:
                stats["done"] += 1
                done_n += 1
                n = done_n
            npar = sum(len(s.get("paragraphs") or []) for s in doc.get("body_text") or [])
            log(f"  [{n}/{total}] {doc.get('source')}: 섹션 "
                f"{len(doc.get('body_text') or [])} · 문단 {npar}  {pdf.name}")
            _emit(on_progress, "file", n, total, pdf.name,
                  f"[{n}/{total}] 완료 {pdf.name}")
        except BaseException as e:  # noqa: BLE001 — 파일별 격리
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            reason = f"{type(e).__name__}: {e}"
            with lock:
                stats["failed"] += 1
                stats["failures"].append((pdf.name, reason))
                done_n += 1
                n = done_n
            log(f"  [{n}/{total}] 실패(계속 진행): {pdf.name} — {reason}")
            _emit(on_progress, "failed", n, total, pdf.name,
                  f"[{n}/{total}] 실패 {pdf.name}: {reason}")

    # 처리 순서를 **일감 통**에 담아 하나씩 꺼내 쓴다.
    #   미리 전부 배정해 버리면 순서를 바꿀 수 없다. 원장이 목록 뒤쪽 논문을
    #   클릭하면 거기까지 기다려야 하는데, 그건 못 쓸 물건이다.
    #   queue.bump(pdf) 로 지금 보고 있는 논문을 맨 앞으로 당긴다.
    if queue is None:
        queue = WorkQueue()
    queue.reset(pdfs)

    def pump() -> None:
        """일감 통이 빌 때까지 하나씩 꺼내 처리한다."""
        while True:
            if _cancelled(should_cancel):
                return
            item = queue.take()
            if item is None:
                return
            idx, pdf = item
            try:
                work(idx, pdf)
            finally:
                queue.finish(pdf)

    if workers <= 1:
        pump()
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(pump) for _ in range(workers)]
            for f in futs:
                f.result()           # pump 안에서 예외를 삼키므로 여기서 안 터진다

    if _cancelled(should_cancel):
        stats["cancelled"] = True
        log(f"[single] 취소 요청 → {done_n}/{total} 에서 중단")
        _emit(on_progress, "cancelled", done_n, total, "",
              f"취소됨 — {done_n}/{total} 처리")

    log(f"[single] 폴더 완료: 처리 {stats['done']} · 건너뜀 {stats['skipped']} · "
        f"실패 {stats['failed']} / 전체 {total}")
    if not stats["cancelled"]:
        _emit(on_progress, "done", total, total, "",
              f"완료 {stats['done']} · 건너뜀 {stats['skipped']} · "
              f"실패 {stats['failed']}")
    return stats


# ── 파이프라인 본체 ──────────────────────────────────────────────────
def _extract(pdf: Path, cfg: dict, ctx: "_Ctx", *, out_json, on_progress,
             use_grobid: bool) -> dict:
    from . import inventory

    pdf = Path(pdf)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF 없음: {pdf}")
    ident = cfg.get("identify") or {}
    notes: list[str] = []
    steps = 5

    # 1) probe — DOI·제목·텍스트층 (예외를 밖으로 던지지 않는 함수다)
    _emit(on_progress, "probe", 0, steps, pdf.name, "PDF 프로빙")
    rec = inventory.probe_pdf(pdf, int(ident.get("scan_pages", 2)),
                              int(ident.get("scanned_char_threshold", 200)),
                              _doi_re(ident))
    if rec.get("error") and not rec.get("pages"):
        raise RuntimeError(rec["error"])       # 열 수 없는 PDF = 이 편은 실패
    if rec.get("error"):
        notes.append(f"probe: {rec['error']}")
    if rec.get("is_scanned_candidate"):
        notes.append("텍스트층 거의 없음(스캔본 후보) — 본문 품질이 낮을 수 있음")

    # 2) DOI 보강 — 끊긴 DOI 교정 → 그래도 없으면 Crossref 제목 매칭
    _emit(on_progress, "doi", 1, steps, pdf.name, "DOI 확인")
    if (rec.get("doi_candidates") and ident.get("verify_truncated_doi", True)
            and not ctx.http.offline):
        try:
            inventory.verify_truncated_dois([rec], cfg)
        except Exception as e:  # noqa: BLE001 — 네트워크 실패는 치명적이지 않다
            notes.append(f"DOI 검증 생략: {e}")
    if (not rec.get("doi") and rec.get("title_guess")
            and ident.get("resolve_missing_doi", True) and not ctx.http.offline):
        try:
            inventory.resolve_missing_dois([rec], cfg)
        except Exception as e:  # noqa: BLE001
            notes.append(f"Crossref 제목 매칭 실패: {e}")
    doi = rec.get("doi")
    if not doi:
        notes.append("DOI 미식별 — 메타데이터 없이 본문만 추출")

    # 3) 메타데이터 — 소스별 실패는 collect_one 안에서 이미 격리된다
    _emit(on_progress, "metadata", 2, steps, pdf.name, "메타데이터 수집")
    md = cfg.get("metadata") or {}
    meta: dict = {"doi": doi, "sources_ok": [], "sources_fail": []}
    if doi:
        try:
            from . import metadata
            meta = metadata.collect_one(
                ctx.http, doi, md.get("email", ""), md.get("ncbi_api_key", ""),
                (cfg.get("fulltext") or {}).get("europepmc_base", ""))
        except Exception as e:  # noqa: BLE001 — 오프라인이어도 본문은 뽑는다
            notes.append(f"메타데이터 수집 실패(오프라인?): {e}")
            meta = {"doi": doi, "sources_ok": [], "sources_fail": ["all"]}
    if not meta.get("sources_ok"):
        notes.append("메타데이터 API 응답 없음 — PDF 정보만으로 조립")
    if not meta.get("title"):
        # 제목이 비면 검수 화면에서 논문을 식별할 수 없다 → PDF 1페이지 추정 제목
        meta["title"] = rec.get("title_guess") or pdf.stem
        meta["title_source"] = "pdf"
        notes.append("제목: PDF 1페이지 추정값 사용")

    # 4) 본문 — PMC 원문 XML → GROBID → PyMuPDF 폴백
    _emit(on_progress, "fulltext", 3, steps, pdf.name, "본문 추출")
    doc = _fulltext(pdf, meta, rec, cfg, ctx, use_grobid, notes)

    # 5) 조립 — 정본 스키마 dict + 수리 + 품질점수
    _emit(on_progress, "assemble", 4, steps, pdf.name, "정본 조립")
    d = doc.to_dict()
    d["source_file"] = str(pdf)
    d["paper_id"] = _paper_id(d.get("paper_id"), rec, pdf)

    tf = cfg.get("textfix") or {}
    if tf.get("enabled", True):
        try:
            from . import textfix
            d, _st = textfix.fix_document(d, bool(tf.get("carry_forward", True)))
        except Exception as e:  # noqa: BLE001 — 수리 실패 시 원본을 그대로 쓴다
            notes.append(f"textfix 생략: {type(e).__name__}: {e}")

    # 잘려 나간 문단 앞부분을 PDF 에서 되살린다.
    try:
        from . import recover
        d, _st = recover.recover_document(d, pdf)
        if _st.get("recovered"):
            notes.append(f"문단 복원 {_st['recovered']}건")
    except Exception as e:  # noqa: BLE001
        notes.append(f"문단 복원 생략: {type(e).__name__}: {e}")

    # 빈 표를 PDF 괘선 좌표로 재구성한다. **이 단계가 빠져 있어 앱으로 뽑으면
    # 표가 캡션만 남고 비어 있었다**(실측 표 161개 중 49개). 일괄 처리 경로에는
    # 있었는데 한 편 처리 경로에는 없어서 산출물이 갈렸다.
    try:
        from . import tablefill
        d, _st = tablefill.fill_document(d, pdf)
        n = int(_st.get("filled", 0) or 0) + int(_st.get("repaired", 0) or 0)
        if n:
            notes.append(f"표 복원 {n}개")
        if _st.get("dropped"):
            notes.append(f"표 아닌 것 제거 {_st['dropped']}개")
    except Exception as e:  # noqa: BLE001
        notes.append(f"표 복원 생략: {type(e).__name__}: {e}")

    # 그림을 **실제 이미지로** 잘라 JSON 옆 `<paper_id>_figs/` 에 넣고
    # figures[].image 에 상대경로를 담는다. 이 단계가 없어 전 코퍼스의
    # figures[].image 가 전부 None 이었다(앱이 캡션만 띄우고 그림은 못 띄웠다).
    # tablefill 뒤에 둔다 — tables[].pdf_span 을 표 영역 장벽으로 쓴다.
    try:
        from . import figclip
        d, _st = figclip.fill_document(
            d, pdf, json_path=out_json or default_json_path(pdf))
        if _st.get("clipped"):
            notes.append(f"그림 추출 {_st['clipped']}장"
                         f"({_st['bytes'] // 1024}KB)")
    except Exception as e:  # noqa: BLE001
        notes.append(f"그림 추출 생략: {type(e).__name__}: {e}")

    # 범위 밖(한글 본문) 판정 — 억지로 뽑아 엉터리를 남기지 않는다.
    #   2단으로 조판된 한글 본문은 읽기 순서가 좌우로 섞여 '피부의 표 여 있는데'
    #   같은 뒤죽박죽이 되고, 그 문단이 제목 자리까지 올라간다(실측 26편 중 5편).
    #   영문 서지정보(제목·초록·저자)는 API 로 받은 것이라 정확하므로 **살린다**.
    #   본문만 비우고 이유를 남긴다 — 나중에 한글을 지원할 때 이어서 하면 된다.
    _body_txt = " ".join(p.get("text", "") for s in (d.get("body_text") or [])
                         for p in (s.get("paragraphs") or []))
    if _body_txt:
        _han = sum(1 for ch in _body_txt if "가" <= ch <= "힣")
        if _han / len(_body_txt) > 0.15:
            d["body_text"] = []
            d["scope"] = "non_english"
            notes.append(f"한글 본문({_han / len(_body_txt):.0%}) — 현재 영어 논문만 "
                         f"지원한다. 본문을 비우고 서지정보만 남긴다")
            # 뒤죽박죽 문단이 제목으로 올라간 경우도 함께 걷어낸다.
            _t = (d.get("meta") or {}).get("title") or ""
            if len(_t) > 150 or (len(_t) > 90 and _t.rstrip().endswith(("다.", ".", ","))):
                d["meta"]["title"] = ""
                notes.append("제목 자리에 본문이 올라가 있어 비웠다")

    # 합본 지면에서 **앞 논문의 DOI 를 이 논문 것으로 잡는** 사고를 바로잡는다.
    #   probe 는 '앞쪽 페이지에서 처음 보이는 DOI' 를 쓴다. 그런데 research letter
    #   지면은 앞 편의 꼬리와 이 편의 머리가 한 쪽에 같이 인쇄되므로, 읽기 순서상
    #   앞 편의 DOI 가 먼저 나온다(실측: Schwannoma 편지가 앞 편 Nephrogenic
    #   fibrosing dermopathy 의 10.1016/j.jaad.2006.04.061 로 파일링됐다).
    #   경계 판정은 '어느 구간에 이 본문이 실제로 놓여 있나' 를 알므로 그것으로 고른다.
    #   **Crossref 로 존재를 확인한 DOI 만 채택한다** — 추측한 DOI 를 기본키로 쓰면
    #   조용히 틀린 레코드가 된다.
    try:
        from . import boundary
        body_probe = " ".join(
            p.get("text", "") for s in (d.get("body_text") or [])
            for p in (s.get("paragraphs") or []))[:4000]
        bmap = boundary.analyze(pdf, {"doi": d.get("paper_id"),
                                      "title": (d.get("meta") or {}).get("title", "")},
                                body_probe=body_probe)
        conflict = bmap.identity_conflict or {}
        better = (conflict.get("correct_doi") or "").strip().lower()
        if better and better != str(d.get("paper_id", "")).lower():
            if utils.verify_doi(better):
                notes.append(f"신원 정정: {d.get('paper_id')} → {better} "
                             f"({conflict.get('how', '경계판정')})")
                d["paper_id"] = better
                d.setdefault("meta", {})["doi"] = better
                # 제목·저자도 앞 편 것이므로 새 DOI 로 다시 받는다.
                try:
                    from . import metadata
                    _md = cfg.get("metadata") or {}
                    fresh = metadata.collect_one(
                        ctx.http, better, _md.get("email", ""),
                        _md.get("ncbi_api_key", ""),
                        (cfg.get("fulltext") or {}).get("europepmc_base", "")) or {}
                    for k in ("title", "authors", "journal", "year", "pmid",
                              "pmcid", "mesh", "pub_types", "abstract_pubmed"):
                        if fresh.get(k):
                            d["meta"][k] = fresh[k]
                except Exception as e:  # noqa: BLE001
                    notes.append(f"정정 후 메타 재조회 실패: {type(e).__name__}: {e}")
            else:
                notes.append(f"신원 의심({better}) — Crossref 미확인이라 두었다")
    except Exception as e:  # noqa: BLE001 — 신원 판정 실패가 추출을 막지 않는다
        notes.append(f"신원 판정 생략: {type(e).__name__}: {e}")

    # 참고문헌 확정 — **번호·순서는 지면에서, 내용은 iCite 에서.**
    #   지면 파싱만 쓰면 서지값을 못 믿고(DOI 순열 뒤섞임), iCite 만 쓰면 순서가
    #   지면 번호가 아니다. 둘을 제목·DOI 로 짝지어야 본문 [15] 를 눌러 15번으로
    #   갈 수 있다. 일괄 경로(refmatch.run)에는 있고 이 경로에는 없어서 앱으로
    #   뽑으면 참고문헌이 파싱 그대로 남았다 — 산출물이 갈리지 않게 여기서도 부른다.
    #   네트워크가 없으면 지면 목록만으로 채운다(비우지 않는다).
    try:
        from . import refmatch
        pm = str((d.get("meta") or {}).get("pmid") or "")
        art: dict = {}
        detail: dict = {}
        if pm and not getattr(ctx.http, "offline", False):
            cache = utils.resolve(cfg["project"]["work_dir"]) / "icite"
            art = (refmatch.fetch_icite([pm], cache, ctx.http) or {}).get(pm) or {}
            ref_pmids = [str(x) for x in (art.get("references") or []) if x]
            if ref_pmids:
                detail = refmatch.fetch_icite(ref_pmids, cache, ctx.http)
        st = refmatch.reconcile(d, art, detail)
        paired = st["doi"] + st["title"] + st["author-year"]
        notes.append(f"참고문헌 {st['printed']}개 중 번호 {st['numbered']} · "
                     f"iCite 짝 {paired} · 인용링크 {st['linked_citations']}")
        if st.get("foreign_block"):
            notes.append(f"옆 논문 참고문헌 {st['foreign_block']}개 분리")
    except Exception as e:  # noqa: BLE001 — 참조 실패가 본문 추출을 무효화하지 않는다
        notes.append(f"참고문헌 확정 생략: {type(e).__name__}: {e}")

    try:
        from . import qc
        report = qc.score_doc(d, rec, meta)
        d["quality_score"] = report["quality_score"]
        d["qc"] = report
    except Exception as e:  # noqa: BLE001 — QC 실패가 추출을 무효화하지 않는다
        notes.append(f"QC 생략: {type(e).__name__}: {e}")
        d.setdefault("qc", {})

    d["qc"]["notes"] = notes
    d["qc"]["network"] = ("offline" if ctx.http.offline
                          else ("ok" if meta.get("sources_ok") else "unknown"))
    d["qc"]["source_file"] = str(pdf)

    # 파일 내용 지문 — 저장소가 이걸로 PDF 와 정본을 짝짓는다(경로가 아니라).
    d["sha1"] = rec.get("sha1") or ""

    if out_json:
        _write_doc(Path(out_json), d)           # 원자적 쓰기(반쪽 JSON 방지)
        # 저장소가 목록 화면용 요약도 갱신하게 한다(정본 자체는 위에서 이미 썼다).
        try:
            from . import store
            if d["sha1"] and Path(out_json) == store.doc_path(d["sha1"]):
                store._index_put(d["sha1"], d, pdf)
        except Exception as e:  # noqa: BLE001 — 요약 실패가 추출을 무효화하지 않는다
            notes.append(f"목록 갱신 생략: {type(e).__name__}: {e}")

    _emit(on_progress, "done", steps, steps, pdf.name,
          f"완료 ({d.get('source')})")
    return d


def _fulltext(pdf: Path, meta: dict, rec: dict, cfg: dict, ctx: "_Ctx",
              use_grobid: bool, notes: list[str]):
    """본문 추출 3단 폴백. 앞 경로가 본문을 못 만들면 다음으로 내려간다."""
    from . import utils

    best = None

    # (a) PMC 원문 XML — 출판사 정본이라 구조 오류가 원리적으로 0
    if meta.get("in_epmc") and meta.get("pmcid"):
        try:
            from . import pmc_xml
            base = (cfg.get("fulltext") or {}).get("europepmc_base", "")
            cache = utils.resolve(cfg["project"]["work_dir"]) / "xml"
            xml = pmc_xml.fetch_xml(ctx.http, base, meta["pmcid"], cache)
            if xml:
                doc = pmc_xml.parse(xml, meta, source_file=str(pdf))
                if _n_paragraphs(doc):
                    return doc
                best = best or doc
                notes.append("PMC XML 에 본문 없음 → 다음 경로")
            else:
                notes.append("PMC 원문 XML 미제공 → 다음 경로")
        except Exception as e:  # noqa: BLE001
            notes.append(f"PMC XML 실패: {type(e).__name__}: {e}")

    # (b) GROBID — 비-PMC born-digital PDF 의 정답(인용→참고문헌 링크 포함)
    if use_grobid and ctx.grobid_ready():
        try:
            from . import grobid_client
            gcfg = cfg.get("grobid") or {}
            tei = ctx.tei_cached(pdf, rec.get("sha1", ""))
            if tei is None:
                # GROBID 호출만 좁힌다 — 몰리면 서비스가 죽는다(실측).
                # 파싱·수리·PDF 작업은 그대로 병렬로 돈다.
                with ctx._gsem:
                    tei = grobid_client.process_pdf(gcfg.get("url", ""), pdf, gcfg)
                ctx.tei_store(pdf, rec.get("sha1", ""), tei)
            if tei:
                doc = grobid_client.parse_tei(tei, meta, source_file=str(pdf))
                if _n_paragraphs(doc):
                    return doc
                best = best or doc
                notes.append("GROBID 결과에 본문 없음 → PyMuPDF 폴백")
            else:
                notes.append("GROBID 변환 실패 → PyMuPDF 폴백")
        except Exception as e:  # noqa: BLE001
            notes.append(f"GROBID 실패: {type(e).__name__}: {e}")
    elif use_grobid:
        notes.append(f"GROBID 서버 없음({(cfg.get('grobid') or {}).get('url')}) "
                     f"→ PyMuPDF 폴백")
    else:
        notes.append("GROBID 사용 안 함(use_grobid=False) → PyMuPDF 폴백")

    # (c) PyMuPDF 폴백 — 네트워크·GROBID 가 전부 없어도 여기까진 온다
    from . import pdf_fallback
    try:
        doc = pdf_fallback.parse_pdf(pdf, meta)
    except Exception as e:  # noqa: BLE001
        if best is not None:
            notes.append(f"PyMuPDF 폴백 실패({type(e).__name__}) → 앞 경로 결과 사용")
            return best
        raise
    if _n_paragraphs(doc) or best is None:
        return doc
    notes.append("PyMuPDF 폴백도 본문 없음 → 앞 경로 결과 사용")
    return best


# ── 실행 컨텍스트(폴더 처리에서 재사용) ────────────────────────────────
class _Ctx:
    """HTTP 세션·GROBID 생존 판정·TEI 캐시를 한 번만 만들어 재사용한다."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        md = cfg.get("metadata") or {}
        self.http = _SingleHttp(
            email=md.get("email", ""),
            delay=float(md.get("request_delay_sec", 0.34) or 0.34),
            timeout=int(md.get("timeout_sec", 20) or 20),
        )
        self._grobid: bool | None = None
        # 배치 중 GROBID 가 죽었을 때 되살릴 횟수. 무한정 시도하면 설치가 없는
        # PC 에서 논문마다 40초씩 멎는다.
        self._revive_budget: int = 3
        # 동시 처리에서 여러 스레드가 이 객체를 함께 쓴다.
        #   · _glock — 생존 확인·되살리기가 겹치지 않게(여러 스레드가 동시에
        #     GROBID 를 띄우면 포트 충돌로 전부 실패한다. 실측으로 확인했다)
        #   · _gsem  — GROBID 에 동시에 보내는 요청 수 제한. 몰리면 서비스가
        #     죽는다(실측 175편 처리에 2회). 파싱·수리는 병렬로 두고
        #     **GROBID 호출만** 좁힌다.
        self._glock = threading.Lock()
        self._gsem = threading.Semaphore(2)
        self._tlock = threading.Lock()      # TEI 캐시 쓰기

    def grobid_ready(self) -> bool:
        """GROBID 가 쓸 수 있는 상태인가. 꺼져 있으면 **창 없이 띄우고 기다린다.**

        긴 배치 중 서비스가 죽는 일이 있다(실측 175편에 2회). 한 번 죽고 나면
        남은 논문이 전부 폴백으로 떨어져 산출물의 질이 중간에 갈린다. 그래서
        생존 확인을 한 번만 하지 않고, 죽은 것이 확인되면 되살린다.
        되살리기는 `_revive_budget` 만큼만 시도한다 — 설치가 아예 없거나 계속
        죽는 상황에서 매 논문마다 40초씩 기다리는 것을 막는다.
        """
        from . import grobid_service as gs
        g = self.cfg.get("grobid") or {}
        url = (g.get("url") or "").strip()
        with self._glock:                   # 여러 스레드가 동시에 띄우면 포트가 충돌한다
            return self._grobid_ready_locked(gs, g, url)

    def _grobid_ready_locked(self, gs, g: dict, url: str) -> bool:
        if not url:
            if self._grobid is None:
                self._grobid = False
                log("[single] GROBID 주소 없음 → PyMuPDF 폴백")
            return False

        probe = float(g.get("probe_timeout_sec", 2) or 2)
        if gs.is_alive(url, timeout=probe):
            if self._grobid is not True:
                log(f"[single] GROBID 사용 ({url})")
            self._grobid = True
            return True

        # 여기부터는 '지금 안 떠 있다'. 되살릴 여지가 있으면 되살린다.
        if self._revive_budget <= 0:
            if self._grobid is not False:
                log("[single] GROBID 되살리기 한도 소진 → PyMuPDF 폴백")
            self._grobid = False
            return False
        self._revive_budget -= 1
        log("[single] GROBID 가 응답하지 않는다 → 창 없이 되살리는 중(최대 120초)")
        ok = gs.ensure(url, timeout=120.0)
        self._grobid = bool(ok)
        log(f"[single] GROBID {'되살아남' if ok else '되살리기 실패 → PyMuPDF 폴백'}")
        return self._grobid

    # TEI 캐시: PDF 내용(sha1) 기준 — overwrite 재실행 때 GROBID 재호출을 아낀다
    def _tei_path(self, pdf: Path, sha1: str) -> Path:
        from . import utils
        stem = sha1[:16] if sha1 else utils.slug(Path(pdf).stem)[:60]
        return utils.resolve(self.cfg["project"]["work_dir"]) / "tei" / f"{stem}.tei.xml"

    def tei_cached(self, pdf: Path, sha1: str) -> bytes | None:
        p = self._tei_path(pdf, sha1)
        try:
            return p.read_bytes() if p.exists() and p.stat().st_size else None
        except OSError:
            return None

    def tei_store(self, pdf: Path, sha1: str, tei: bytes | None) -> None:
        if not tei:
            return
        p = self._tei_path(pdf, sha1)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(tei)
        except OSError as e:
            log(f"[single] TEI 캐시 저장 생략: {e}")


class _SingleHttp:
    """단발 처리용 HTTP 래퍼 — utils.HttpClient 를 감싸 '빨리 포기'하게 만든다.

    배치는 재시도 3회가 옳지만(장시간 실행), 검수 앱은 사용자가 화면 앞에서 기다린다.
    재시도를 줄이고, 연속 실패가 쌓이면 오프라인으로 판정해 이후 호출을 즉시
    끊는다 — 인터넷이 없을 때 API 5종 × 재시도만큼 사용자를 기다리게 하지 않는다.
    """

    def __init__(self, email: str = "", delay: float = 0.34, timeout: int = 20,
                 retries: int = 1, offline_after: int = 3):
        from .utils import HttpClient
        self._c = HttpClient(email=email, delay=delay, timeout=timeout)
        self.retries = max(1, int(retries))
        self.offline_after = max(1, int(offline_after))
        self.fails = 0
        self.offline = False

    @property
    def sess(self):
        return self._c.sess

    @property
    def email(self):
        return self._c.email

    def get(self, url: str, params: dict | None = None,
            accept: str | None = None, retries: int | None = None):
        if self.offline:
            raise ConnectionError("오프라인 판정 — 네트워크 호출 생략")
        try:
            r = self._c.get(url, params=params, accept=accept,
                            retries=self.retries if retries is None else retries)
        except Exception:
            self.fails += 1
            if self.fails >= self.offline_after and not self.offline:
                self.offline = True
                log("[single] 네트워크 연속 실패 → 오프라인으로 판정, "
                    "이후 API 호출은 생략한다(본문 추출은 계속)")
            raise
        self.fails = 0
        return r

    def get_json(self, url: str, params: dict | None = None, **kw):
        r = self.get(url, params=params, accept="application/json", **kw)
        return r.json() if r is not None else None


# ── 설정·헬퍼 ───────────────────────────────────────────────────────
def _prepare_cfg(config: dict | None) -> dict:
    """기본값 위에 사용자 cfg 를 얹고, work_dir 을 임시 폴더로 갈아끼운 사본.

    배치 함수들(resolve_missing_dois·fetch_xml 등)이 work_dir 아래에 캐시·실패
    원장을 쓰므로, **원본 data/ 를 절대 건드리지 않도록** 여기서 차단한다.
    호출부가 넘긴 dict 는 변경하지 않는다.
    """
    cfg = config
    if cfg is None:
        try:
            from . import utils
            cfg = utils.load_config()
        except Exception as e:  # noqa: BLE001 — 설정이 없어도 기본값으로 돈다
            log(f"[single] config 로드 실패 → 기본값 사용: {type(e).__name__}: {e}")
            cfg = {}
    merged = _merge(_DEFAULTS, cfg or {})
    merged["project"] = dict(merged.get("project") or {})
    merged["project"]["work_dir"] = str(_scratch_dir())
    return merged


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if v is None and k in out:
            continue                        # yaml 빈 섹션이 기본값을 지우지 않게
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _scratch_dir() -> Path:
    """단일 처리용 임시 work_dir(XML/TEI 캐시·실패 원장). data/ 와 완전히 분리."""
    d = Path(tempfile.gettempdir()) / "pubnexus_single"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _win_long(p: Path) -> Path | None:
    r"""Windows 확장 길이 경로(\\?\ 접두) 형태. 해당 없으면 None."""
    if os.name != "nt":
        return None
    s = str(p)
    if s.startswith("\\\\?\\"):
        return p
    try:
        s = os.path.abspath(s)
    except OSError:
        return None
    return Path("\\\\?\\UNC" + s[1:]) if s.startswith("\\\\") else Path("\\\\?\\" + s)


def _io_path(p: Path) -> Path:
    """260자 제한에 걸릴 경로만 확장 형태로 바꾼다(그 외는 원본 그대로).

    Dropbox·한글 폴더 + 긴 논문 파일명이 겹치면 260자를 쉽게 넘고, 그 순간
    os.replace 가 WinError 3 으로 실패해 정본 JSON 이 저장되지 않는다.
    논리 경로(source_file 등)는 손대지 않고 파일시스템 호출에만 이 형태를 쓴다.
    """
    if os.name != "nt" or len(str(p)) < 240:
        return p
    return _win_long(p) or p


def _write_doc(dest: Path, doc: dict) -> None:
    """정본 JSON 저장(원자적). 긴 경로 실패는 확장 경로로 한 번 더 시도한다."""
    from . import utils
    try:
        utils.write_json(_io_path(dest), doc)
        return
    except OSError as e:
        forced = _win_long(dest)
        if forced is None or str(forced) == str(_io_path(dest)):
            raise
        log(f"[single] 저장 실패({type(e).__name__}) → 긴 경로 형식으로 재시도")
        utils.write_json(forced, doc)


def _doi_re(ident: dict):
    import re
    try:
        return re.compile(ident.get("doi_regex") or "", re.I)
    except Exception:  # noqa: BLE001 — 잘못된 정규식이면 기본 패턴으로
        from .utils import DOI_RE
        return DOI_RE


def _n_paragraphs(doc) -> int:
    try:
        return sum(len(s.paragraphs) for s in doc.body_text)
    except Exception:  # noqa: BLE001
        return 0


class WorkQueue:
    """처리 순서를 담는 통. **사용자가 지금 보는 논문을 앞으로 당길 수 있다.**

    폴더 순서대로만 처리하면, 원장이 목록 뒤쪽 논문을 클릭했을 때 거기까지
    전부 끝나기를 기다려야 한다. 화면은 `bump(pdf)` 로 그 논문을 맨 앞에 놓는다.

    이미 다른 일꾼이 그 논문을 잡고 있으면 앞으로 당기지 않는다 — **같은 논문을
    두 번 처리하면** 같은 파일에 동시에 쓰게 되어 반쪽 산출물이 나온다.
    화면은 `is_running(pdf)` 로 '지금 처리 중' 을 표시하고 기다리면 된다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._order: list[Path] = []          # 남은 일감(앞이 먼저)
        self._idx: dict[Path, int] = {}       # 원래 순번(진행 표시용)
        self._running: set[Path] = set()

    def reset(self, pdfs) -> None:
        with self._lock:
            self._order = list(pdfs)
            self._idx = {p: i for i, p in enumerate(self._order, 1)}
            self._running.clear()

    def take(self) -> tuple[int, Path] | None:
        with self._lock:
            if not self._order:
                return None
            p = self._order.pop(0)
            self._running.add(p)
            return self._idx.get(p, 0), p

    def finish(self, pdf: Path) -> None:
        with self._lock:
            self._running.discard(pdf)

    def bump(self, pdf: str | Path) -> str:
        """그 논문을 맨 앞으로. 'queued'|'running'|'done'|'unknown' 을 돌려준다."""
        p = Path(pdf)
        with self._lock:
            if p in self._running:
                return "running"
            try:
                self._order.remove(p)
            except ValueError:
                return "done" if p in self._idx else "unknown"
            self._order.insert(0, p)
            return "queued"

    def is_running(self, pdf: str | Path) -> bool:
        with self._lock:
            return Path(pdf) in self._running

    def remaining(self) -> int:
        with self._lock:
            return len(self._order)


def _worker_count(cfg: dict) -> int:
    """동시에 처리할 편수. **일반 사용자 PC 에서 메모리가 모자라지 않는 선**으로 잡는다.

    한 편이 도는 동안 PyMuPDF 가 PDF 전체를 열고 페이지를 렌더한다. 실측으로
    한 편이 대략 250~400MB 를 쓰므로, 넉넉히 **한 편당 0.5GB** 로 보고 **남은
    메모리의 절반** 안에서만 늘린다. 나머지 절반은 사용자가 쓰던 프로그램 몫이다.

    시간의 대부분은 API 응답 대기라 CPU 코어 수보다 조금 많아도 이득이 있지만,
    GROBID 가 몰리면 죽으므로(실측) 위를 8 로 막는다. 설정으로 덮을 수 있게 둔다.
    """
    want = (cfg.get("project") or {}).get("workers")
    if want:
        try:
            return max(1, int(want))
        except (TypeError, ValueError):
            pass
    n = max(1, (os.cpu_count() or 4) - 1)
    try:                                   # 있으면 실제 여유 메모리로 다시 깎는다
        import psutil                      # noqa: PLC0415
        free_gb = psutil.virtual_memory().available / (1024 ** 3)
        n = min(n, max(1, int(free_gb * 0.5 / 0.5)))
    except Exception:                      # noqa: BLE001 — 없으면 코어 수만으로
        n = min(n, 4)
    return max(1, min(n, 8))


def _paper_id(current: str | None, rec: dict, pdf: Path) -> str:
    """DOI → PMID → 파일 sha1 → 파일명. 'unknown' 을 남기지 않는다."""
    if current and current != "unknown":
        return current
    if rec.get("sha1"):
        return f"sha1:{rec['sha1'][:16]}"
    return f"file:{pdf.stem}"


def _emit(cb, stage: str, done: int, total: int, file: str,
          message: str = "") -> None:
    """진행 콜백. 콜백 예외는 처리를 절대 멈추지 않는다."""
    if cb is None:
        return
    try:
        cb({"stage": stage, "done": done, "total": total, "file": file,
            "message": message})
    except Exception as e:  # noqa: BLE001
        log(f"[single] on_progress 콜백 예외 무시: {type(e).__name__}: {e}")


def _forward(cb, idx: int, total: int, name: str):
    """한 편 안의 단계 진행을 폴더 진행률(파일 단위)로 환산해 넘긴다."""
    if cb is None:
        return None

    def _fwd(ev: dict) -> None:
        cb({"stage": ev.get("stage", ""), "done": idx - 1, "total": total,
            "file": name, "message": ev.get("message", "")})

    return _fwd


def _cancelled(should_cancel) -> bool:
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception as e:  # noqa: BLE001 — 취소 판정 실패는 '계속'으로 해석
        log(f"[single] should_cancel 예외 무시: {type(e).__name__}: {e}")
        return False
