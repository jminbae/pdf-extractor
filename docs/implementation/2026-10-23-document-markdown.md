# 문서 Markdown 추출기 구현 계획

## 최종 추가 요구 — 2026-10-23

- [x] 기본 출력은 Windows의 실제 `문서\문서 추출기`입니다. 파일·폴더·끌어놓기 모두 같은 저장 정책을 따릅니다.
- [x] 설정에서 저장 폴더 변경·초기화를 지원하고 재실행 후에도 유지합니다. 변경은 이후 추출에 적용하며 기존 MD·이미지는 이동하지 않습니다.
- [x] 추출 중에는 저장 폴더 변경과 업데이트 설치를 막습니다.
- [x] 업데이트 버튼과 자동 확인·다운로드를 연결합니다. 자동 설치는 앱 정상 종료 때 적용하며, 수동 설치는 완료 후 다시 엽니다. 실행 경계는 HTTP/Popen 대체 시험으로 검증했고 사용자 기존 설치를 교체하지 않았습니다.
- [x] 고정 GitHub 저장소의 안정 버전·설치 파일·SHA256 자산을 검증하고 다운로드/실행 실패를 표시합니다.
- [x] 최종 실행 파일과 검사값을 다시 빌드하고 실제 WebView2·OCR·설정·수동 GitHub 확인을 검증했습니다. 전체 181개 시험 PASS. 사용자 기존 설치 교체·릴리스 게시·별도 PC 설치는 수행하지 않았습니다.

이 절의 저장 정책은 아래 초기 단계의 원본 옆 `Markdown` 예시보다 우선합니다. `export_document`의 독립 API 기본값은 호환을 위해 유지하지만 데스크톱 앱은 설정한 출력 폴더를 명시해서 전달합니다.

> **For agentic workers:** subagent-driven-development와 TDD로 작업 단위마다 요구 검토·코드 검토를 거칩니다. 최신 제품 요구는 `docs/research/2026-10-23-simplified-scope.md`를 따릅니다.

**Goal:** 기존 논문 추출을 보존하면서 일반 문서 본문·표·자산을 Markdown으로 자동 저장하고 읽기 전용 검색·대조를 제공합니다.

**Architecture:** 기존 JSON 캐시를 정본으로 유지합니다. 최종 PDF 본문 보존 검사와 일반 PDF 어댑터, 공통 Markdown 내보내기를 분리하고 앱이 성공/부분 완료/저장 실패를 표시합니다. 위치 정보는 원본 좌표와 문단/표 ID로 보존합니다.

**Tech Stack:** Python 3.12, 기존 PyMuPDF·GROBID/JATS, unittest, pywebview/WebView2. 시험 파일은 TEMP, 빌드·캐시는 `D:\DevCache\260725 PDF 추출`.

## 작업 1: PDF 최종 본문 보존

- 소유: worker `existing_extractor`.
- 파일: `pubnexus/src/pubnexus/single.py`, 신규 `general_pdf.py`, `pubnexus/tests/test_general_pdf.py`.
- [x] 한글 본문 삭제·책의 부분 결과·참고문헌만 남은 결과를 합성 PDF로 재현합니다. 네트워크는 시험에서 차단합니다.
- [x] `ensure_fulltext(doc, pdf_path) -> (doc, report)`를 최종 저장 전에 호출합니다. 충분한 기존 논문 결과는 유지하고 부족한 본문만 원문 페이지/블록으로 보완합니다.
- [x] 짧은 문단·한글·참고문헌을 일반 PDF 경로에서 삭제하지 않으며 원문 쪽·bbox·ID와 미처리/스캔 경고를 보존합니다.
- [x] RED→GREEN 결과와 기존 논문 경로 보존을 독립 검토합니다.

## 작업 2: 공통 MD·자산 자동 저장

- 소유: worker `office_hwp_research`.
- 파일: 신규 `pubnexus/src/pubnexus/export.py`, `pubnexus/tests/test_export.py`.
- [x] TEMP의 원본·자산으로 `Markdown/<원본stem>.md`와 상대 이미지 링크, 원본/정본 불변을 시험합니다.
- [x] `export_document(doc, source_path, *, output_dir=None, asset_base=None)`는 저장된 경로·revision·멱등 여부를 반환합니다.
- [x] 긴 이름/동명 충돌/예약명, 수정된 기존 MD 보호, 복사·쓰기 실패를 실제 파일 입출력과 fault injection으로 시험합니다.
- [x] 자산을 먼저 안전하게 준비하고 MD를 공개하며 저장 실패를 성공으로 반환하지 않습니다.

## 작업 3: 앱 입력과 완료 상태 연결

- 소유: root. 파일: `pubnexus/app.py`, `pubnexus/tests/test_app_export.py`, `test_app_input.py`.
- [x] 추출 성공 후 MD 자동 저장을 호출하는 통합 시험을 먼저 실패시킵니다. 검증할 핵심 결과는 `source.parent / 'Markdown' / 'book.md'`가 존재하고 이미지 링크가 유효한지입니다.
- [x] 캐시된 추출물도 MD가 없으면 추출을 반복하지 않고 저장할 수 있게 합니다. 저장 실패 시 실패 상태를 유지하고 다음 파일은 처리합니다.
- [x] 파일 하나/여러 개 추가와 폴더 추가를 제공하고 `Markdown` 출력은 재수집하지 않습니다. 같은 이름의 다른 경로를 구분합니다.
- [x] 결과 데이터에 MD 경로·저장 상태·원본 경로를 제공하고 내부 JSON 위치를 사용자 결과 위치로 표시하지 않습니다.

## 후속 작업

- [x] 간결한 작업 목록 UI를 실제 앱에 연결하고 결과 보기·원본 대조를 필요할 때 엽니다.
- [x] 표 누락·본문 혼입과 위치 연결을 보완하고 원본 검색·양방향 클릭·스크롤 연동을 검증합니다.
- [x] rHWP 직접 연결과 Office 구조 어댑터를 순차 추가합니다. 표시용 변환과 MD 추출은 분리합니다.
- [x] 긴 문서·폴더 배치·중지/재개·쓰기 실패 회복과 실제 frozen 실행/설치 묶음 생성을 검증했습니다. 실제 설치 교체는 미실행입니다.

## 검증 명령과 진행 원칙

`C:\Users\Bae\pnxvenv\Scripts\python.exe -B -m unittest discover -s pubnexus/tests -v`

실행 시 `PYTHONPATH`에 `pubnexus/src`를 두고 `PUBNEXUS_STORE`는 TEMP의 시험 저장소로 격리합니다. 합성 파일은 종료 시 정리합니다. 시험 실패는 원인 확인 후 고치며, 기능 단위마다 검수·통과 결과를 기록하고 커밋합니다. 초기 브랜치는 `codex/document-markdown`입니다. 기존 생성물·기존 앱 설치본은 자동으로 이동/삭제하지 않습니다.
