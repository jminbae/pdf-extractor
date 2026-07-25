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
from pathlib import Path

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
    """PDF 옆, 같은 이름의 .json 경로."""
    return Path(pdf_path).with_suffix(".json")


def is_extracted(pdf_path: str | Path, out_json: str | Path | None = None) -> bool:
    """이미 처리된 PDF 인가(정상적인 정본 JSON 이 옆에 있는가).

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
        doc.get("schema_version") or doc.get("sections") is not None)


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
                   overwrite: bool = False) -> dict:
    """폴더 안 모든 PDF 를 처리해 각 PDF 옆에 .json 을 쓴다.

    반환 {'total','done','skipped','failed','failures':[(파일명, 사유)], 'cancelled'}

    파일별 격리 — 한 편의 실패가 전체를 멈추지 않는다. 이미 .json 이 있으면
    건너뛴다(overwrite=True 면 다시 처리). should_cancel() 이 True 면
    지금까지 쓴 .json 은 그대로 두고 즉시 멈춘다(재실행하면 남은 것만 처리).
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

    for i, pdf in enumerate(pdfs, 1):
        if _cancelled(should_cancel):
            stats["cancelled"] = True
            log(f"[single] 취소 요청 → {i - 1}/{total} 에서 중단")
            _emit(on_progress, "cancelled", i - 1, total, "",
                  f"취소됨 — {i - 1}/{total} 처리")
            break
        dest = default_json_path(pdf)
        if not overwrite and is_extracted(pdf, dest):
            stats["skipped"] += 1
            log(f"  [{i}/{total}] 건너뜀(이미 처리): {pdf.name}")
            _emit(on_progress, "skip", i, total, pdf.name,
                  f"[{i}/{total}] 건너뜀 {pdf.name}")
            continue
        try:
            doc = _extract(pdf, cfg, ctx, out_json=dest,
                           on_progress=_forward(on_progress, i, total, pdf.name),
                           use_grobid=True)
            stats["done"] += 1
            npar = sum(len(s.get("paragraphs") or []) for s in doc.get("sections") or [])
            log(f"  [{i}/{total}] {doc.get('source')}: 섹션 "
                f"{len(doc.get('sections') or [])} · 문단 {npar}  {pdf.name}")
            _emit(on_progress, "file", i, total, pdf.name,
                  f"[{i}/{total}] 완료 {pdf.name}")
        except BaseException as e:  # noqa: BLE001 — 파일별 격리
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            reason = f"{type(e).__name__}: {e}"
            stats["failed"] += 1
            stats["failures"].append((pdf.name, reason))
            log(f"  [{i}/{total}] 실패(계속 진행): {pdf.name} — {reason}")
            _emit(on_progress, "failed", i, total, pdf.name,
                  f"[{i}/{total}] 실패 {pdf.name}: {reason}")

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

    if out_json:
        _write_doc(Path(out_json), d)           # 원자적 쓰기(반쪽 JSON 방지)

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

    def grobid_ready(self) -> bool:
        """GROBID 서버 생존 여부(폴더 전체에서 1회만 확인, 짧은 타임아웃)."""
        if self._grobid is None:
            from . import grobid_client
            g = self.cfg.get("grobid") or {}
            url = (g.get("url") or "").strip()
            probe = float(g.get("probe_timeout_sec", 2) or 2)
            self._grobid = bool(url) and grobid_client.is_alive(url, timeout=probe)
            log(f"[single] GROBID {'사용' if self._grobid else '미가동 → PyMuPDF 폴백'}"
                f" ({url or '주소 없음'})")
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
        return sum(len(s.paragraphs) for s in doc.sections)
    except Exception:  # noqa: BLE001
        return 0


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
