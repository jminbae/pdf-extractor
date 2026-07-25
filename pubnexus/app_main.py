"""논문 PDF 구조화 도구 — 단독 실행용 진입점.

하는 일은 셋뿐이다: 폴더 지정 → 처리 → 결과 열어보기.
검색·임베딩은 이 앱의 일이 아니다(ResearchMap 이 이미 갖고 있다).

exe 로 묶었을 때 utils.ROOT 는 exe 가 놓인 폴더가 된다(utils._detect_root).
따라서 exe 를 프로젝트 루트에 두면 소스 실행과 똑같이 동작한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):            # 소스 실행일 때만 경로 보정
    sys.path.insert(0, str(Path(__file__).parent / "src"))


def _setup_console() -> None:
    """윈도우 콘솔을 UTF-8 로 — 안 하면 한글이 전부 깨진다(기본 cp949)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_console()

import yaml

from pubnexus import utils
from pubnexus.utils import log


def _cfg_path() -> Path:
    return utils.ROOT / "pubnexus" / "config.yaml"


def _load() -> dict:
    return utils.load_config()


def _save_input_dir(folder: str) -> None:
    """config.yaml 의 input_dir 만 바꿔 쓴다(주석 보존을 위해 통째 파싱 대신 줄 치환)."""
    p = _cfg_path()
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    out, done = [], False
    for line in lines:
        if not done and line.lstrip().startswith("input_dir:"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}input_dir: "{folder}"\n')
            done = True
        else:
            out.append(line)
    if not done:
        raise RuntimeError("config.yaml 에서 input_dir 줄을 찾지 못했습니다.")
    p.write_text("".join(out), encoding="utf-8")


def _paths(cfg: dict) -> tuple[Path, Path, Path]:
    work = utils.resolve(cfg["project"]["work_dir"])
    return work, work / "normalized", work / "markdown"


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))          # noqa: S606 — Windows 탐색기로 열기


# ── 메뉴 동작 ────────────────────────────────────────────────────────
def act_set_folder() -> None:
    cfg = _load()
    print(f"\n현재 폴더: {utils.resolve(cfg['project']['input_dir'])}")
    print("새 폴더 경로를 붙여넣으세요 (그냥 Enter 면 유지)")
    folder = input("> ").strip().strip('"')
    if not folder:
        return
    p = Path(folder)
    if not p.is_dir():
        print(f"  ! 그런 폴더가 없습니다: {p}")
        return
    n = len(list(p.rglob("*.pdf")))
    _save_input_dir(str(p).replace("\\", "/"))
    print(f"  → 저장했습니다. 이 폴더에 PDF {n}개가 있습니다.")


def act_process() -> None:
    cfg = _load()
    src = utils.resolve(cfg["project"]["input_dir"])
    pdfs = list(src.rglob("*.pdf"))
    if not pdfs:
        print(f"\n  ! PDF 가 없습니다: {src}\n    먼저 1번으로 폴더를 지정하세요.")
        return
    print(f"\n{src}\nPDF {len(pdfs)}개를 처리합니다.")
    print("중간에 멈춰도 다시 실행하면 이어서 합니다. 창을 닫지 마세요.\n")
    if input("시작할까요? (y/n) ").strip().lower() not in ("y", "yes", ""):
        return

    from pubnexus import (inventory, metadata, pmc_xml, grobid_client,
                          pdf_fallback, refmatch, qc, render, audit, textfix)
    steps = [
        ("논문 식별", lambda: inventory.build_manifest(cfg)),
        ("서지정보 수집", lambda: metadata.collect_all(cfg)),
        ("원문 XML 확보", lambda: pmc_xml.run(cfg)),
        ("PDF 구조화(GROBID)", lambda: grobid_client.run(cfg)),
        ("PDF 구조화(폴백)", lambda: pdf_fallback.run(cfg)),
        ("참고문헌 대조", lambda: refmatch.run(cfg)),
        ("품질 검사", lambda: qc.run(cfg)),
        ("추출 결함 수리", lambda: textfix.run(cfg)),
        ("읽기용 문서 생성", lambda: render.run(cfg)),
        ("전수 감사", lambda: audit.run(cfg)),
    ]
    failed = []
    for i, (name, fn) in enumerate(steps, 1):
        print(f"\n[{i}/{len(steps)}] {name}")
        try:
            fn()
        except Exception as e:                    # 한 단계 실패가 전체를 멈추지 않게
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ! 실패: {type(e).__name__}: {e}")
            traceback.print_exc(limit=2, file=sys.stderr)

    work, norm, md = _paths(cfg)
    print(f"\n{'=' * 56}")
    print(f"구조화된 논문 {len(list(norm.glob('*.json')))}편")
    print(f"읽기용 문서   {len(list(md.glob('*.md')))}편")
    if failed:
        print(f"\n실패한 단계 {len(failed)}개:")
        for n, e in failed:
            print(f"  - {n}: {e}")
        print("  ※ GROBID 서버가 없으면 'PDF 구조화(GROBID)' 실패는 정상입니다")
        print("     (폴백 경로로 처리되지만 품질이 낮습니다).")
    print(f"결과 위치: {work}")
    print("=" * 56)


def act_open_result() -> None:
    cfg = _load()
    work, norm, md = _paths(cfg)
    files = sorted(md.glob("*.md"))
    if not files:
        print("\n  ! 아직 결과가 없습니다. 2번으로 먼저 처리하세요.")
        return
    print(f"\n읽기용 문서 {len(files)}편이 있습니다. 탐색기로 엽니다.")
    print("아무 .md 파일이나 열어 보시면 됩니다 (메모장·마크다운 뷰어).")
    _open_folder(md)


def act_status() -> None:
    cfg = _load()
    work, norm, md = _paths(cfg)
    src = utils.resolve(cfg["project"]["input_dir"])
    n_pdf = len(list(src.rglob("*.pdf"))) if src.is_dir() else 0
    n_norm = len(list(norm.glob("*.json"))) if norm.is_dir() else 0
    n_md = len(list(md.glob("*.md"))) if md.is_dir() else 0
    fails = work / "failures.jsonl"
    print(f"""
  PDF 폴더        {src}
  그 안의 PDF     {n_pdf}개
  구조화된 논문   {n_norm}편
  읽기용 문서     {n_md}편
  결과 위치       {work}
  설정 파일       {_cfg_path()}""")
    if fails.exists():
        n = sum(1 for _ in fails.open(encoding="utf-8"))
        print(f"  실패 기록       {n}건 ({fails.name})")


MENU = """
============================================================
  논문 PDF 구조화 도구
============================================================

  1. PDF 폴더 지정
  2. 처리 시작          PDF → 구조화 문서 (이어하기 됨)
  3. 결과 열어보기      만들어진 문서를 탐색기로
  4. 현재 상태

  0. 끝내기
"""


def main() -> None:
    actions = {"1": act_set_folder, "2": act_process,
               "3": act_open_result, "4": act_status}
    while True:
        print(MENU)
        try:
            sel = input("번호 입력: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if sel == "0":
            return
        fn = actions.get(sel)
        if not fn:
            continue
        try:
            fn()
        except Exception as e:
            print(f"\n  ! 오류: {type(e).__name__}: {e}")
            traceback.print_exc(limit=3, file=sys.stderr)
        try:
            input("\nEnter 를 누르면 메뉴로 돌아갑니다...")
        except (EOFError, KeyboardInterrupt):
            return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("\n오류로 종료합니다. Enter...")
