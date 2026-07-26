"""0단계 — 인벤토리 원장(manifest).

모든 라우팅의 단일 진실. 각 PDF에 대해:
  파일경로 · sha1 · 쪽수 · 총글자수 · 텍스트층 여부 · DOI · 중복그룹
을 판정해 manifest.jsonl 로 기록한다. 이후 단계는 전부 이 원장을 읽는다.

대규모(3만 편) 대비 — 재개·격리·비차단이 제1원칙:
  · 파일 단위로 partial 원장에 증분 저장 → 중단 후 재개 가능(이미 스캔한 파일 건너뜀)
  · 부분 기록(마지막 줄이 잘린 원장)도 견딘다: 손상 줄은 버리고 원장을 재작성한 뒤 이어간다
  · PDF 한 편의 예외가 전체를 멈추지 않는다(파일별 격리) → 실패는 failures.jsonl 로 표면화
  · DOI 미추출 시 Crossref 제목 매칭으로 보강, 그래도 실패면 unidentified.jsonl 로
    '무음 탈락' 대신 명시적으로 분리(설계서 식별 파이프라인 2단계)
  · Crossref 조회 결과(hit/miss)를 원장에 남겨 재개 시 같은 질의를 반복하지 않는다

호스트 앱(ResearchMap) 연동용 선택 인자 — 기존 호출부는 그대로 동작한다:
  build_manifest(cfg, on_progress=cb, should_cancel=fn)

  on_progress(payload: dict)  — 항목마다 호출. payload 키(안정 계약):
      stage    "inventory"
      phase    "start" | "item" | "skip" | "failed" | "crossref" | "done" | "cancelled"
      done/total/ok/failed/skipped : int
      current  현재 파일명(또는 DOI)
      message  사람이 읽는 한 줄
    콜백에서 예외가 나도 파이프라인은 멈추지 않는다.

  should_cancel() -> bool  — True 면 지금까지 결과를 partial 원장에 안전하게 flush 하고
    즉시 멈춘다. 이때 manifest.jsonl(완료 산출물)은 **갱신하지 않는다** —
    잘린 원장으로 기존 완성본을 덮어써 후속 단계가 논문을 잃는 사고를 막기 위함.
    다시 실행하면 남은 파일만 이어서 처리한다.

설정(모두 선택, 기본값으로 기존 동작 유지):
  identify.checkpoint_every : partial 원장 flush 주기(건). 기본 1 = 매 파일 저장
  identify.retry_failed     : True 면 재개 시 실패 레코드를 다시 시도. 기본 False
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import fitz  # PyMuPDF
from rapidfuzz import fuzz

from . import utils
from .utils import HttpClient, log


def sha1_of(path: Path, buf_size: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(buf_size):
            h.update(chunk)
    return h.hexdigest()


def probe_pdf(path: Path, scan_pages: int, scanned_threshold: int,
              doi_re: re.Pattern) -> dict:
    """단일 PDF 프로빙: 쪽수, 글자수, 텍스트층, DOI.

    어떤 경우에도 예외를 밖으로 던지지 않는다(파일별 격리). 실패는 rec["error"] 로,
    일부 페이지만 깨진 경우는 rec["page_errors"] 로 표면화한다.
    """
    rec: dict = {
        "file": str(path), "filename": path.name, "sha1": "",
        "pages": 0, "total_chars": 0, "chars_per_page": 0,
        "has_text_layer": False, "is_scanned_candidate": False,
        "doi": None, "doi_source": None, "title_guess": "", "error": None,
        "page_errors": 0,
    }
    try:
        rec["sha1"] = sha1_of(path)
    except Exception as e:  # noqa: BLE001  — 읽기 불가(권한/삭제/네트워크 드라이브)
        rec["error"] = f"sha1_fail: {e}"
        return rec
    try:
        doc = fitz.open(path)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"open_fail: {e}"
        return rec
    try:
        rec["pages"] = doc.page_count
        # 페이지 단위 격리: 한 장이 깨져도 나머지 텍스트는 살린다
        page_texts: list[str] = []
        for i in range(doc.page_count):
            try:
                page_texts.append(doc[i].get_text())
            except Exception:  # noqa: BLE001
                page_texts.append("")
                rec["page_errors"] += 1
        total = sum(len(t) for t in page_texts)
        rec["total_chars"] = total
        rec["chars_per_page"] = int(total / doc.page_count) if doc.page_count else 0
        rec["has_text_layer"] = total >= scanned_threshold
        rec["is_scanned_candidate"] = not rec["has_text_layer"]

        # DOI: PDF 메타 → 앞쪽 페이지 → 전체 텍스트(일부 저널은 뒤쪽/각주에 표기)
        head = "".join(page_texts[:scan_pages])
        try:
            meta_subject = doc.metadata.get("subject", "") or ""
        except Exception:  # noqa: BLE001
            meta_subject = ""
        meta_doi = _find_doi(meta_subject, doi_re)
        doi = meta_doi or _find_doi(head, doi_re) or _find_doi("".join(page_texts), doi_re)
        rec["doi"] = doi
        rec["doi_source"] = "pdf" if doi else None

        # 조판 때문에 DOI 가 줄 중간에서 끊기면 접두부만 뽑혀 조회가 전부 실패한다
        # (예: '10.1200/JCO.18.\n01223' → 10.1200/jco.18). 접미가 짧아 의심스러울
        # 때만 이어붙인 후보를 남겨 두고, 실제 확인은 뒷단계(_resolve)에서 한다.
        if doi and len(doi.split("/", 1)[-1]) < 12:
            cands = [c for c in utils.doi_candidates("".join(page_texts), doi_re)
                     if c != doi]
            if cands:
                rec["doi_candidates"] = cands[:8]

        # 제목: 폰트 기반(1페이지 최대 폰트 텍스트) — 한국어/영어 모두 안정적
        try:
            rec["title_guess"] = _extract_title(doc[0]) if doc.page_count else ""
        except Exception:  # noqa: BLE001 — 제목 실패는 치명적이지 않다(Crossref 보강만 포기)
            rec["title_guess"] = ""
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"probe_fail: {e}"
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
    return rec


def _find_doi(text: str, doi_re: re.Pattern) -> str | None:
    m = doi_re.search(text or "")
    return utils.clean_doi(m.group(0)) if m else None


# 제목 자리에 앉은 조판 부속물 — 이것들은 본문 제목보다 폰트가 큰 일이 흔하다.
# (실측: 이 필터가 없으면 title_guess 가 'Author Offprint' · 'References' ·
#  'LETTERS RESEARCH LETTERS' · '+ Supplemental content' 로 잡혀
#  신원 대조 게이트가 30편을 오탐한다)
_TITLE_JUNK_RE = re.compile(
    r"^(?:references?|acknowledge?ments?|author\s+offprint|offprint"
    r"|research\s+letters?|letters?\s*(?:to\s+the\s+editor)?|reply"
    r"|correspondence|image\s+gallery|editorial|commentary|erratum"
    r"|supplement(?:al|ary)?(?:\s+content)?|see\s+also|continued"
    r"|abstract|introduction|summary|discussion|conclusions?"
    r"|original\s+articles?|brief\s+reports?|case\s+reports?|review"
    r"|clinical\s+challenge|solution|key\s+points)$", re.I)

# 저널 머리말(권/호/쪽/월) 줄
_TITLE_RUNNING_RE = re.compile(
    r"^(?:\W*\d[\d\s,.:;|–—-]*)?(?:vol\.?\s*\d|no\.?\s*\d"
    r"|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)


def _is_plausible_title(t: str) -> bool:
    """이 문자열을 논문 제목으로 믿어도 되는가.

    믿을 수 없으면 title_guess 를 비워 둔다 — 엉터리 제목을 남기면
    Crossref 제목매칭이 남의 논문을 물어오고(기본키 오염), 신원 대조
    게이트가 멀쩡한 논문을 불합격시킨다. **모르면 모른다고 둔다.**
    """
    t = (t or "").strip(" .,:;|-–—")
    if len(t) < 20:
        return False
    core = re.sub(r"[^\w\s]", " ", t).strip()
    if _TITLE_JUNK_RE.match(core):
        return False
    if _TITLE_RUNNING_RE.match(t):
        return False
    words = [w for w in core.split() if w]
    if len(words) < 3:
        return False
    # 숫자·기호 덩어리(쪽번호·표 조각)는 제목이 아니다
    letters = sum(ch.isalpha() for ch in t)
    return letters / max(len(t), 1) >= 0.6


def _extract_title(page) -> str:
    """1페이지에서 제목을 추출. 큰 폰트부터 훑되 조판 부속물은 건너뛴다.

    폰트 크기 하나만 보면 2단 조판 레터에서 러닝헤드('LETTERS')나
    'Author Offprint' 도장이 제목을 이긴다. 그래서 큰 폰트 그룹을 차례로
    내려가며 **제목다운 첫 후보**를 고르고, 끝까지 없으면 "" 를 돌려준다.
    """
    lines = []  # (size, y, text)
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            txt = utils.norm_text("".join(s["text"] for s in l["spans"]))
            sz = max((s["size"] for s in l["spans"]), default=0.0)
            if len(txt) >= 4:
                lines.append((round(sz, 1), l["bbox"][1], txt))
    if not lines:
        return ""
    for size in sorted({s for s, _, _ in lines}, reverse=True)[:6]:
        group = sorted((y, t) for s, y, t in lines if abs(s - size) < 0.15)
        cand = utils.norm_text(" ".join(t for _, t in group))[:300]
        if _is_plausible_title(cand):
            return cand
        # 같은 크기 줄이 여럿이면 러닝헤드가 섞였을 수 있다 → 한 줄씩도 본다
        for _, t in group:
            if _is_plausible_title(t):
                return utils.norm_text(t)[:300]
    return ""


def assign_dup_groups(records: list[dict]) -> None:
    """중복 그룹 부여(in-place).

    DOI가 있으면 DOI 키로, 없으면 sha1(바이트 동일 파일) 키로 그룹핑한다.
    sha1 조차 못 구한 실패 레코드는 경로를 키로 써서 서로 뭉치지 않게 한다.
    각 그룹의 첫 등장 레코드를 is_primary=True 로.
    """
    for r in records:
        if r.get("doi"):
            r["dup_group"] = f"doi:{r['doi']}"
        elif r.get("sha1"):
            r["dup_group"] = f"sha1:{r['sha1']}"
        else:
            r["dup_group"] = f"path:{r.get('file', '')}"
    seen: set[str] = set()
    for r in records:
        g = r["dup_group"]
        r["is_primary"] = g not in seen
        seen.add(g)


def verify_truncated_dois(records: list[dict], cfg: dict, *,
                          on_progress=None, should_cancel=None) -> int:
    """줄바꿈으로 끊긴 DOI 를 이어붙인 후보로 교정한다. 교정 건수 반환.

    probe_pdf 가 접미가 짧아 의심스러운 DOI 에만 doi_candidates 를 남겨 두었다.
    여기서 Crossref 에 실제 존재를 물어 첫 번째로 확인되는 후보로 바꾼다.
    확인된 뒤에는 doi_verified=True 를 남겨 재실행 시 다시 묻지 않는다.
    네트워크가 안 되면 조용히 원래 DOI 를 유지한다(판정 불가 ≠ 없음).
    """
    targets = [r for r in records
               if r.get("doi_candidates") and not r.get("doi_verified")]
    if not targets:
        return 0
    md = cfg["metadata"]
    work = utils.resolve(cfg["project"]["work_dir"])
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"],
                      timeout=md["timeout_sec"])
    n = 0
    log(f"[0단계] 끊긴 DOI 의심 {len(targets)}편 → 후보 검증")
    for j, r in enumerate(targets, 1):
        if _cancelled(should_cancel):
            log(f"[0단계] 취소 요청 — DOI 검증 {j - 1}/{len(targets)} 에서 중단")
            break
        old = r.get("doi")
        try:
            for cand in [old, *r["doi_candidates"]]:
                ok = utils.verify_doi(cand, http)
                if ok:
                    if ok != old:
                        r["doi"] = ok
                        r["doi_source"] = "pdf+stitch"
                        n += 1
                        log(f"        {old} → {ok}")
                    r["doi_verified"] = True
                    break
        except Exception as e:  # noqa: BLE001 — 한 편 실패가 전체를 막지 않는다
            _record_failure(work, "inventory.doi_verify", r.get("file", old or ""), e)
        _notify(on_progress, {"stage": "inventory", "phase": "doi_verify",
                              "done": j, "total": len(targets)})
    if n:
        log(f"[0단계] DOI 교정 {n}편")
    return n


def resolve_misfiled_dois(records: list[dict], cfg: dict, *,
                          on_progress=None, should_cancel=None) -> int:
    """앞 논문의 DOI 로 파일링된 레코드를 이 PDF 의 진짜 DOI 로 바로잡는다.

    2단 조판 저널은 한 PDF 에 앞 논문의 꼬리를 함께 싣는다. 그 꼬리의 DOI 가
    먼저 잡히면 레코드 전체(제목·저자·PMID·MeSH)가 남의 것이 된다. DOI 자체는
    멀쩡히 해소되므로 **해소 검사로는 절대 못 잡는다** — 제목으로 판정한다.

    후보는 **그 PDF 안에 실제로 인쇄된 DOI 로 제한한다.** Crossref 제목검색을
    믿으면 엉뚱한 곳으로 간다(실측: '10.1111/1346-8138.12933' 의 제목검색 1위가
    Qeios 프리프린트 10.32388/5t88py 였다. 정답 10.1111/1346-8138.12936 은
    그 PDF 안에 인쇄돼 있었다). 인쇄된 DOI + Crossref 제목 일치 둘 다 만족할
    때만 바꾸고, 아니면 손대지 않는다.
    """
    md = cfg["metadata"]
    thr = (cfg.get("identify", {}) or {}).get("title_match_threshold", 90)
    work = utils.resolve(cfg["project"]["work_dir"])
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"],
                      timeout=md["timeout_sec"])
    from . import metadata as _md

    targets = [r for r in records
               if r.get("doi") and not r.get("identity_verified")
               and _md._filename_title(r.get("file", ""))]
    if not targets:
        return 0
    log(f"[0단계] 신원 대조 {len(targets)}편 (파일명 제목 ↔ DOI 서지제목)")
    n = 0
    for j, r in enumerate(targets, 1):
        if _cancelled(should_cancel):
            log(f"[0단계] 취소 요청 — 신원 대조 {j - 1}/{len(targets)} 에서 중단")
            break
        want = _md._filename_title(r.get("file", ""))
        try:
            cur = _crossref_title(http, r["doi"], md["email"])
            if cur and fuzz.token_set_ratio(want.lower(), cur.lower()) >= thr:
                r["identity_verified"] = True
                continue
            # 이 PDF 에 인쇄된 다른 DOI 중 제목이 맞는 것을 찾는다
            best = None
            for cand in _pdf_doi_candidates(Path(r["file"]), cfg):
                if cand == r["doi"]:
                    continue
                t = _crossref_title(http, cand, md["email"])
                if not t:
                    continue
                s = fuzz.token_set_ratio(want.lower(), t.lower())
                if s >= thr and (best is None or s > best[0]):
                    best = (s, cand, t)
            if best:
                log(f"        {r['doi']} → {best[1]}  ({best[0]}) {best[2][:60]}")
                r["doi_previous"] = r["doi"]
                r["doi"] = best[1]
                r["doi_source"] = f"pdf+identity({best[0]})"
                r["identity_verified"] = True
                n += 1
            elif cur:
                r["identity_mismatch"] = True   # 표면화만 — 정답을 못 찾았다
        except Exception as e:  # noqa: BLE001
            _record_failure(work, "inventory.identity", r.get("file", ""), e)
        _notify(on_progress, {"stage": "inventory", "phase": "identity",
                              "done": j, "total": len(targets)})
    if n:
        log(f"[0단계] 신원 교정 {n}편")
    return n


def _crossref_title(http: HttpClient, doi: str, email: str) -> str:
    """DOI 의 서지 제목. 없는 DOI(404)는 예외가 아니라 빈 문자열이다.

    후보 DOI 중에는 이어붙이기가 빗나간 것이 섞여 있는 게 정상이라
    404 를 예외로 올리면 **첫 오답 하나가 그 논문 전체의 판정을 죽인다**
    (실측: 이 때문에 163편 중 162편이 조용히 건너뛰어졌다).
    """
    try:
        data = http.get_json(f"https://api.crossref.org/works/{doi}",
                             params={"mailto": email}, retries=1)
    except Exception:  # noqa: BLE001 — 404/네트워크 모두 '제목 없음'으로 다룬다
        return ""
    return ((data or {}).get("message", {}).get("title") or [""])[0]


def _pdf_doi_candidates(path: Path, cfg: dict) -> list[str]:
    """PDF 전체 텍스트에 인쇄된 DOI(줄바꿈으로 끊긴 것 이어붙인 것 포함)."""
    doi_re = re.compile(cfg["identify"]["doi_regex"], re.I)
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return []
    try:
        text = "".join(doc[i].get_text() for i in range(doc.page_count))
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
    return utils.doi_candidates(text, doi_re, limit=24)


def resolve_missing_dois(records: list[dict], cfg: dict, *,
                         on_progress=None, should_cancel=None) -> int:
    """DOI 미추출 레코드를 Crossref 제목 퍼지매칭으로 보강. 보강 건수 반환.

    조회한 레코드에는 doi_lookup = "hit"|"miss"|"error" 를 남긴다 →
    재개 시 이미 조회한 제목을 다시 묻지 않는다(3만 편 rate-limit 방어).
    """
    ident = cfg.get("identify", {}) or {}
    retry = bool(ident.get("retry_failed", False))
    missing = [r for r in records
               if not r.get("doi") and r.get("title_guess")
               and (retry or r.get("doi_lookup") not in ("miss", "hit"))]
    if not missing:
        return 0
    md = cfg["metadata"]
    thr = ident.get("title_match_threshold", 90)
    work = utils.resolve(cfg["project"]["work_dir"])
    http = HttpClient(email=md["email"], delay=md["request_delay_sec"],
                      timeout=md["timeout_sec"])
    n = 0
    log(f"[0단계] DOI 미추출 {len(missing)}편 → Crossref 제목 매칭 시도")
    for j, r in enumerate(missing, 1):
        if _cancelled(should_cancel):
            log(f"[0단계] 취소 요청 — Crossref 매칭 {j - 1}/{len(missing)} 에서 중단")
            break
        title = r["title_guess"]
        try:
            data = http.get_json("https://api.crossref.org/works",
                                 params={"query.bibliographic": title, "rows": 1,
                                         "mailto": md["email"]})
            items = (data or {}).get("message", {}).get("items", [])
            r["doi_lookup"] = "miss"
            if items:
                cand = items[0]
                cand_title = (cand.get("title") or [""])[0]
                score = fuzz.token_set_ratio(title, cand_title)
                if score >= thr and cand.get("DOI"):
                    r["doi"] = utils.clean_doi(cand["DOI"])
                    r["doi_source"] = f"crossref_title({score})"
                    r["doi_lookup"] = "hit"
                    n += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 막지 않는다
            r["doi_lookup"] = "error"
            log(f"      ! Crossref 매칭 실패: {e}")
            _record_failure(work, "inventory.crossref", r.get("file", title), e)
        _notify(on_progress, {
            "stage": "inventory", "phase": "crossref", "done": j,
            "total": len(missing), "ok": n, "failed": 0, "skipped": 0,
            "current": r.get("filename", ""),
            "message": f"Crossref 제목 매칭 {j}/{len(missing)} (보강 {n})",
        })
    log(f"[0단계] Crossref 보강 성공 {n}/{len(missing)}")
    return n


def build_manifest(config: dict | None = None, resume: bool = True, *,
                   on_progress=None, should_cancel=None) -> list[dict]:
    cfg = config or utils.load_config()
    input_dir = utils.resolve(cfg["project"]["input_dir"])
    work = utils.resolve(cfg["project"]["work_dir"])
    ident = cfg.get("identify", {}) or {}
    scan_pages = ident["scan_pages"]
    threshold = ident["scanned_char_threshold"]
    doi_re = re.compile(ident["doi_regex"], re.I)
    every = max(1, int(ident.get("checkpoint_every", 1) or 1))
    retry_failed = bool(ident.get("retry_failed", False))

    pdfs = sorted(input_dir.rglob("*.pdf"))
    total = len(pdfs)
    partial = work / "manifest.partial.jsonl"
    work.mkdir(parents=True, exist_ok=True)

    # ── 재개: 이미 스캔된 파일은 건너뜀(부분 기록된 원장도 견딘다) ──────────
    done: dict[str, dict] = {}
    prev_clean: dict[str, dict] = {}
    if resume:
        prev, broken = _read_jsonl_tolerant(partial)
        for rec in prev:
            if isinstance(rec, dict) and rec.get("file"):
                prev_clean[rec["file"]] = rec     # 같은 파일 중복 기록은 마지막 것
        if broken or _needs_newline(partial) or len(prev_clean) != len(prev):
            # 손상 줄/중복 제거 후 재작성 — 잘린 마지막 줄에 이어붙여 2차 오염되는 것 방지
            _write_jsonl_atomic(partial, list(prev_clean.values()))
            log(f"[0단계] partial 원장 복구: 손상 {broken}줄 폐기, "
                f"정상 {len(prev_clean)}건 유지")
        for key, rec in prev_clean.items():
            if rec.get("error") and retry_failed:
                continue
            done[key] = rec
    elif partial.exists():
        # 전면 재스캔: 기존 partial 에 이어붙이면 중복이 쌓인다 → 비우고 시작
        _write_jsonl_atomic(partial, [])
        log("[0단계] resume=False → partial 원장 초기화")

    log(f"[0단계] 인벤토리: PDF {total}개 @ {input_dir}"
        + (f" (재개: 기존 {len(done)}건)" if done else ""))
    _notify(on_progress, {
        "stage": "inventory", "phase": "start", "done": 0, "total": total,
        "ok": 0, "failed": 0, "skipped": 0, "current": "",
        "message": f"인벤토리 시작: PDF {total}개 (재개 {len(done)}건)",
    })

    records: list[dict] = []
    failures: list[tuple[str, str]] = []
    n_new = n_skip = n_page_err = 0
    cancelled = False
    ledger = _PartialLedger(partial, every)
    try:
        for i, p in enumerate(pdfs, 1):
            if _cancelled(should_cancel):
                cancelled = True
                log(f"[0단계] 취소 요청 감지 → {i - 1}/{total} 에서 안전 중단")
                break
            key = str(p)
            if key in done:
                records.append(done[key])
                n_skip += 1
                _notify(on_progress, {
                    "stage": "inventory", "phase": "skip", "done": i, "total": total,
                    "ok": n_new, "failed": len(failures), "skipped": n_skip,
                    "current": p.name, "message": f"[{i}/{total}] 재개 건너뜀 {p.name}",
                })
                continue
            try:
                rec = probe_pdf(p, scan_pages, threshold, doi_re)
            except BaseException as e:  # noqa: BLE001 — probe_pdf 밖으로 새는 예외까지 격리
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                rec = _error_record(p, e)
            records.append(rec)
            ledger.add(rec)      # 증분 저장(중단돼도 여기까진 보존)
            n_new += 1
            if rec.get("page_errors"):
                n_page_err += 1
            if rec.get("error"):
                failures.append((rec["filename"], rec["error"]))
                _record_failure(work, "inventory", key, rec["error"])
            flag = "ERR" if rec["error"] else ("scan?" if rec["is_scanned_candidate"] else "ok")
            log(f"  [{i:>3}/{total}] {flag:<5} {rec['pages']:>3}p "
                f"doi={rec['doi'] or '-'}  {rec['filename'][:46]}")
            _notify(on_progress, {
                "stage": "inventory", "phase": "failed" if rec["error"] else "item",
                "done": i, "total": total, "ok": n_new - len(failures),
                "failed": len(failures), "skipped": n_skip, "current": rec["filename"],
                "message": f"[{i}/{total}] {flag} {rec['filename']}",
            })
    finally:
        ledger.flush()   # 예외로 빠져나가도 지금까지 스캔분은 디스크에 남는다

    if cancelled:
        _summarize_failures(failures)
        log(f"[0단계] 취소로 중단 — partial 원장 {len(records)}건 보존 → {partial}")
        log("        manifest.jsonl 은 갱신하지 않음(잘린 원장으로 완성본을 덮지 않는다). "
            "다시 실행하면 남은 파일만 처리한다.")
        _notify(on_progress, {
            "stage": "inventory", "phase": "cancelled", "done": len(records),
            "total": total, "ok": n_new - len(failures), "failed": len(failures),
            "skipped": n_skip, "current": "",
            "message": f"취소됨 — {len(records)}/{total} 보존, 재실행 시 이어서 진행",
        })
        return records

    if ident.get("verify_truncated_doi", True):
        # 제목 매칭보다 먼저 — 잘린 DOI 를 살릴 수 있으면 제목 조회 자체가 불필요하다
        verify_truncated_dois(records, cfg, on_progress=on_progress,
                              should_cancel=should_cancel)

    if ident.get("resolve_missing_doi"):
        before = sum(1 for r in records if r.get("doi"))
        resolve_missing_dois(records, cfg, on_progress=on_progress,
                             should_cancel=should_cancel)
        after = sum(1 for r in records if r.get("doi"))
        if after != before or any("doi_lookup" in r for r in records):
            # 보강 결과를 partial 원장에도 반영 → 재개 시 같은 제목을 다시 묻지 않는다.
            # 입력 폴더에서 잠시 사라진 파일의 과거 기록은 지우지 않고 보존(병합 쓰기).
            merged = dict(prev_clean)
            for r in records:
                if r.get("file"):
                    merged[r["file"]] = r
            _write_jsonl_atomic(partial, list(merged.values()))

    assign_dup_groups(records)

    out = work / "manifest.jsonl"
    _write_jsonl_atomic(out, records)

    # 미식별(DOI 없음) 논문은 '무음 탈락' 대신 별도 큐로 표면화
    unidentified = [r for r in records if not r.get("doi")]
    if unidentified:
        _write_jsonl_atomic(work / "unidentified.jsonl", unidentified)

    n_text = sum(r["has_text_layer"] for r in records)
    n_doi = sum(bool(r["doi"]) for r in records)
    n_primary = sum(r["is_primary"] for r in records)
    log(f"[0단계] 완료 → {out}")
    log(f"        born-digital {n_text}/{len(records)} · DOI {n_doi}/{len(records)} · "
        f"고유논문 {n_primary} (중복 {len(records) - n_primary})")
    log(f"        신규 스캔 {n_new} · 재개 건너뜀 {n_skip}"
        + (f" · 일부 페이지 손상 {n_page_err}" if n_page_err else ""))
    if unidentified:
        log(f"        ⚠ 미식별(DOI 없음) {len(unidentified)}편 → unidentified.jsonl "
            f"(후속 단계 자동 제외)")
    _summarize_failures(failures)
    _notify(on_progress, {
        "stage": "inventory", "phase": "done", "done": len(records), "total": total,
        "ok": len(records) - len(failures), "failed": len(failures), "skipped": n_skip,
        "current": "", "message": f"인벤토리 완료 {len(records)}건 (실패 {len(failures)})",
    })
    return records


def run(config: dict | None = None, *, on_progress=None, should_cancel=None) -> None:
    """다른 단계와 동일한 진입점 규약(run). build_manifest 의 얇은 래퍼."""
    build_manifest(config, on_progress=on_progress, should_cancel=should_cancel)


# ── 원장/실패/진행 헬퍼 ──────────────────────────────────────────────
class _PartialLedger:
    """증분 원장 writer. checkpoint_every 건마다 디스크로 flush(+fsync)."""

    def __init__(self, path: Path, every: int = 1):
        self.path = Path(path)
        self.every = max(1, int(every))
        self._buf: list[dict] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, row: dict) -> None:
        self._buf.append(row)
        if len(self._buf) >= self.every:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        rows, self._buf = self._buf, []
        with open(self.path, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def _append_jsonl(path: Path, row: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_jsonl_tolerant(path: Path) -> tuple[list[dict], int]:
    """부분 기록된 원장도 읽는다. (정상 레코드, 손상 줄 수) 반환."""
    rows: list[dict] = []
    broken = 0
    p = Path(path)
    if not p.exists():
        return rows, broken
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001 — 잘린 줄/깨진 줄은 버린다
                broken += 1
    return rows, broken


def _needs_newline(path: Path) -> bool:
    """마지막 줄이 개행 없이 끝났는가(= 쓰다 만 흔적)."""
    p = Path(path)
    try:
        if not p.exists() or p.stat().st_size == 0:
            return False
        with open(p, "rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except Exception:  # noqa: BLE001
        return False


def _write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    """원자적 쓰기(임시파일 → os.replace). utils 에 헬퍼가 생기면 그것을 쓴다."""
    fn = (getattr(utils, "write_jsonl_atomic", None)
          or getattr(utils, "atomic_write_jsonl", None))
    if callable(fn):
        fn(path, rows)
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _record_failure(work: Path, stage: str, item: str, error) -> None:
    """실패 원장. utils.record_failure 가 있으면 위임, 없으면 work/failures.jsonl 에 append.

    utils 위임은 '작업 디렉터리를 인자로 받는' 시그니처일 때만 한다 —
    설정을 스스로 읽어 다른 경로에 쓰는 구현에 산출물이 새는 것을 막기 위함.
    """
    fn = getattr(utils, "record_failure", None)
    if callable(fn):
        try:
            _call_flexible(fn, work=work, work_dir=work, stage=stage, step=stage,
                           item=item, key=item, target=item, name=item,
                           error=str(error), err=str(error), reason=str(error),
                           message=str(error))
            return
        except Exception:  # noqa: BLE001 — 시그니처 불일치 → 로컬 원장으로 폴백
            pass
    _append_jsonl(Path(work) / "failures.jsonl", {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stage": stage, "item": str(item), "error": str(error)[:1000],
    })


def _call_flexible(fn, **candidates):
    """이름으로만 인자를 맞춰 호출. 필수 인자를 못 채우면 TypeError."""
    import inspect
    sig = inspect.signature(fn)
    kwargs = {}
    work_like = {"work", "work_dir"}
    got_work = False
    for name, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if name in candidates:
            kwargs[name] = candidates[name]
            got_work = got_work or name in work_like
        elif p.default is p.empty:
            raise TypeError(f"unmappable required param: {name}")
    if not got_work:
        raise TypeError("no work-dir parameter; 산출물 경로를 보장할 수 없음")
    return fn(**kwargs)


def _summarize_failures(failures: list[tuple[str, str]], show: int = 10) -> None:
    if not failures:
        return
    log(f"[0단계] ⚠ 실패 {len(failures)}건 (failures.jsonl 기록):")
    for name, err in failures[:show]:
        log(f"        - {name[:60]} :: {err[:100]}")
    if len(failures) > show:
        log(f"        … 외 {len(failures) - show}건")


def _error_record(path: Path, e: BaseException) -> dict:
    return {
        "file": str(path), "filename": path.name, "sha1": "",
        "pages": 0, "total_chars": 0, "chars_per_page": 0,
        "has_text_layer": False, "is_scanned_candidate": False,
        "doi": None, "doi_source": None, "title_guess": "",
        "error": f"probe_crash: {type(e).__name__}: {e}", "page_errors": 0,
    }


def _notify(cb, payload: dict) -> None:
    """진행 콜백. 콜백 예외는 파이프라인을 절대 멈추지 않는다."""
    if cb is None:
        return
    try:
        cb(payload)
    except Exception as e:  # noqa: BLE001
        log(f"      ! on_progress 콜백 예외 무시: {e}")


def _cancelled(should_cancel) -> bool:
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception as e:  # noqa: BLE001 — 취소 판정 실패는 '계속'으로 해석
        log(f"      ! should_cancel 예외 무시: {e}")
        return False


if __name__ == "__main__":
    build_manifest()
