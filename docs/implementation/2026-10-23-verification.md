# 최신 검증 안내

최종1.2.1/adapter-2 결과는 [공개 SDK 최종 검증](./2026-10-23-sdk-final.md)을 따릅니다. 전체218 PASS, 메인 EXE native/scan PASS, 실제396쪽397.178초·제목13개 복구·그림186/표18 및 원본 불변을 확인했습니다. 아래 기록은 이전 버전/중간 단계의 관찰을 보존한 것입니다.

# 구현 검증 기록

이 문서는 실행한 검증과 아직 확인하지 않은 범위를 구분합니다. 사용자 원본은 시험에 사용하지 않았습니다.

## 최종 소스·실행 파일 검증 — 2026-10-23

- 설치 묶음 1.2.0: `D:\DevCache\260725 PDF 추출\PDF-Extractor-Setup.exe`, 60,228,988 bytes. SHA256 `2e10613fcb720bef21f1c3b9c5fa2a8be87af5bfacb31bf748f16013523330dc`. 동일 폴더에 ASCII `PDF-Extractor-Setup.exe.sha256`을 만들었습니다. PyInstaller 성공 후 Inno `[Code]` 아래 기존 세미콜론 주석 오류를 Pascal 주석으로 고쳐 ISCC 단독 컴파일도 성공했습니다.
- 전체 unittest **181개 PASS / 36.622초**. 마지막 소스 변경 뒤 실행했고 native WebView2 smoke도 PASS입니다. `git diff --check`는 오류 없이 통과했습니다.
- 설정 10개·업데이터 15개를 독립 검수관이 별도로 실행했습니다. 발견한 GUI 종료 교착, 설정 초기 응답 경합, CSV 오류 안내의 PDF 화면 잔류, 업데이트 반환 계약/잘린 HTTP 응답/캐시 링크 경계를 회귀 시험으로 고쳤습니다. 미해결 P1/P2는 없습니다.
- 최종 EXE SHA256: `89de6a22fdad1af6885f823d60e4ec541552e28cc1e5b60b08d5ba1e4aaaf0c0`.
- 실제 EXE 일반 문서 + 수동 GitHub 확인: `D:\DevCache\260725 PDF 추출\frozen-smoke\20261023-193557-71227161\receipt.json` PASS.
- 실제 EXE 스캔 PDF + 동봉 OCR worker: `D:\DevCache\260725 PDF 추출\frozen-smoke\20261023-193557-3af8e403\receipt.json` PASS.
- 두 실행 모두 실제 JS→native bridge, MD 두 개 자동 저장, CSV 표, 편집 불가능한 결과/MD 코드, 정상 PDF 이미지 디코딩과 대조, 원본 SHA256 불변, 자신의 Job/입력 TEMP 정리를 확인했습니다. 새 설정 API/실제 dialog의 버전 1.2.0·출력 경로·자동 업데이트 옵션도 확인했습니다.
- 스캔은 native 텍스트가 없는 합성 PDF에서 실제 frozen OCR worker가 `7391`을 읽고 부분 완료/OCR 경고를 유지했습니다. 실제 책 전체 OCR의 인식률 시험과 구분합니다.
- 실제 수동 GitHub 확인은 `up_to_date / 1.2.0`였습니다. 실제 설치 프로그램 교체나 GitHub 게시를 실행하지 않았습니다. 설치 직전 hash/프로세스 실행 경계와 App 종료 연결은 실제 UpdateManager에서 HTTP·Popen만 대체하여 시험했습니다.
- 숨긴 WebView는 compositor/lazy-load 활성화가 달라 스크린샷은 비필수로 분리하고 성공/생략을 기록했습니다. 시험에서만 원본 `<img>`를 eager로 바꿔 실제 PNG 응답·디코딩을 확인했으며 제품의 lazy-load 정책은 유지합니다.
- 실제 CDP 파일 드롭→pywebviewFullPath→목록 추가→MD 저장: `frozen-smoke\20261023-193040-a5a26bbc\receipt.json` PASS. 실제 탐색기 마우스 제스처를 수행한 시험은 아닙니다.
- 설정 UI: `settings-ui-proof\20261023-192607\receipt.json`의 1336px/375px 경로·진행률 분리·지연 응답 회귀 PASS, `settings-ui-proof\20261023-193258`의 CSV→PDF 오류 문구 초기화 회귀 PASS.

## 저장·업데이트 동작

- 기본은 실제 Windows Documents KnownFolder의 `문서 추출기`입니다. 이 기기에서는 `C:\Users\Bae\Documents\문서 추출기`입니다. 설정에서 변경·초기화하고 재실행 후 유지합니다. MD와 `assets/<문서ID>`의 상대 이미지를 같이 저장합니다.
- 변경은 이후 추출에 적용하며 기존 출력은 이동하지 않습니다. 추출 중 경로 변경과 설치를 거절하고 진행 중 앱 종료도 막습니다. 시험은 `PUBNEXUS_STORE`·`PUBNEXUS_OUTPUT`으로 TEMP만 사용했습니다.
- 자동 업데이트 기본 ON: 일반 설치본 시작 시 확인·다운로드하고, 정상 종료할 때 검증된 설치 파일을 실행합니다. 수동 버튼은 작업이 없을 때 설치 후 앱을 다시 여는 명령을 연결합니다. 개발 실행/숨김 검증/OCR worker에서는 자동 설치하지 않습니다.
- 배포 원본은 고정 `jminbae/pdf-extractor` GitHub 안정 버전입니다. 새 버전은 `PDF-Extractor-Setup.exe`와 `PDF-Extractor-Setup.exe.sha256` 두 자산이 필요합니다. 단일 `version.py`에서 앱/설치 버전을 읽고 빌드가 검사값 파일을 만듭니다. 현재 공개된 이전 버전에는 검사값 자산이 없어 새 버전을 이 계약으로 게시한 다음부터 업데이트 전달이 가능합니다.
- 별도 PC의 신규 설치와 모든 선택 엔진의 설치까지 검증한 것은 아닙니다. rHWP와 OCR 언어 데이터는 앱에 동봉하고 Docling/MarkItDown SDK·Pandoc·LibreOffice는 현재 PC의 별도 로컬 엔진을 사용합니다.

## 자동 시험과 독립 검수

- Python 3.12.10, PyMuPDF 1.28.0, `C:\Users\Bae\pnxvenv\Scripts\python.exe`.
- 1차 통합: unittest 53개 통과. PDF 보존·자동 저장 기반입니다.
- 2차 통합: unittest 106개 통과. PDF 30개, native 문서 20개, 미리보기 5개 및 앱·위치·저장 회귀가 포함됩니다. 이후 형식 확장 뒤 최종 수는 다시 기록합니다.
- 각 코드 작성 뒤 독립 code-reviewer가 검토했고 발견한 본문 소실·외부 링크 설정·엔진 변경 후 오래된 좌표 사용 등을 회귀 시험으로 재현한 뒤 수정했습니다.

## 실제 앱 HTTP/bridge 통합

`pubnexus/tests/ui_preview.py`는 실제 App/Api/Handler/내보내기를 사용합니다. 네이티브 파일 선택만 TEMP의 합성 문서 선택으로 바꾸며, 이 시험용 HTTP RPC는 제품에 포함되지 않습니다.

- PDF 2개 선택 → 실제 자동 MD 저장 → `저장 완료` 확인.
- 결과 화면에 편집 가능한 노드 0개, 문단 ID 3개.
- 고유 검색어 `739`의 원본·결과 검색이 모두 2쪽을 가리킴.
- MD 문단 클릭 → 원본 3쪽, 원본 2쪽 영역 클릭 → 해당 MD 문단 강조.
- 1280px 화면에서 가로 넘침 없음.
- 화면: `D:\DevCache\260725 PDF 추출\ui-preview\actual-app-list.png`, `actual-app-compare.png`.
- 시안의 fake 타이머가 아니라 실제 작업 이벤트와 생성 파일을 검증했습니다.

## 합성 긴 문서 측정

- 입력: 300쪽, 쪽당 네 문단, 총 1,200문단. 텍스트층이 있는 합성 PDF입니다.
- 최초 일반 PDF 보완: 12.406초. 완료 쪽 재사용: 3.390초.
- MD 797,788바이트, 첫 문단과 마지막 쪽 네 번째 문단 모두 존재.
- 해당 Python 프로세스 peak working set 76.2MiB. 별도 OCR/렌더러 프로세스는 포함하지 않는 수치입니다.
- 결과: `D:\DevCache\260725 PDF 추출\book-benchmark.json`.
- 이 수치는 이미지 많은 실제 책·스캔·복잡한 표의 속도나 완전성을 보증하지 않습니다.

## 실제 로컬 엔진

- rHWP v0.8.7 Windows 공식 ZIP의 SHA256을 공식 SHA256SUMS와 대조했습니다: `f7ac326e98f595ae8fecd8da7a614ee9a37db77bdf938782a8e07c9da473ec6f`.
- 실제 합성 HWPX/HWP5의 본문·2x2 표·병합 표를 추출했습니다. HOP는 사용하지 않습니다.
- HWPX → rHWP PDF 표시 → PyMuPDF 검색 가능한 텍스트 → 보수적 문단/표 연결 → MD 자동 저장도 실행했습니다. 원본 bytes 불변이며 빈 anchor 포함 4개 항목 중 3개 연결입니다.
- LibreOffice 26.2.6 공식 MSI SHA256: `f9877032fd908beb9c0ddf06df4af5c2e85f419c42e14876c4cce5aae5fb2660`. 설치 대신 DevCache에 administrative image를 풀었습니다. 사용자 Office 인스턴스는 사용하지 않습니다.
- 실제 합성 DOCX/PPTX/XLSX PDF 미리보기와 텍스트·표 내용, 원본 bytes 불변을 확인했습니다. DOCX 8.09초, PPTX 7.50초, XLSX 6.98초는 작은 시험 문서 측정입니다.
- LO의 긴 cache 아래 profile은 exit 0이어도 PDF가 안 생겼습니다. 동일 입력/엔진/출력에서 profile만 짧은 별도 TEMP로 옮긴 A/B로 실패 조건을 분리했고 `preview-v3`에 반영했습니다. 정확한 실패 Win32 syscall까지 추적한 것은 아닙니다.
- 별도 Tesseract 실행 파일 없이 PyMuPDF에 내장된 Tesseract OCR을 사용합니다. SHA256을 고정한 `eng`·`kor` fast 언어 데이터를 동봉하며, 개별 쪽을 시간·픽셀 한도가 있는 별도 프로세스에서 읽습니다. 합성 이미지 전용 PDF와 90도 회전 원본에서 텍스트·단어 좌표·중지 경계를 확인했습니다. 한글 일부는 오인식했으므로 OCR 결과는 인식 안내와 부분 완료 상태를 유지합니다. 실제 책 전체 OCR의 정확도를 보장하지 않습니다.

## Docling의 실제 표 보완

- 기존 정상 표는 유지하고 캡션이 있으나 구조가 없는 후보 쪽만 격리된 Docling 2.142.1 standard/Pdfium/Heron/TableFormer accurate로 보완합니다. CPU 4 thread, OCR·VLM·LLM·외부 네트워크는 사용하지 않습니다.
- 복잡한 병합·희소 표는 이 보완 경로의 수용 대상에서 제외합니다. 원문과 숫자·문자·행열 좌표가 맞는 표만 수용하고 미확인 내용은 경고와 함께 원래 텍스트로 남깁니다.
- 실제 생산 Python 모듈로 합성 3쪽의 선 있는 표·선 없는 표·병합 표를 검사했습니다. native 정상 표를 그대로 유지한 혼합 결과는 셀 anchor 44/44, 숫자 18개 각각 1회, 본문 6개 표식과 한글을 보존했습니다.
- native 0.286초 + 격리 SDK 최초 12.845초, 새 프로세스 캐시 재사용 시 native 0.064초 + 보완 0.235초입니다. 이 수치는 합성 3쪽의 측정이며 실제 책의 속도를 의미하지 않습니다.
- 근거: `D:\DevCache\260725 PDF 추출\docling-benchmark-20261023\hybrid-production.json`, `hybrid-metrics.json`, `RESULTS.md`. 실제 frozen EXE 검증과 구분합니다.

## 남은 실제 검증과 알려진 경계

- 사용자가 실패를 겪은 실제 책 PDF는 현재 입력으로 제공되지 않았으므로 그 문서 자체의 해결을 확인하지 않았습니다.
- 일반 PDF의 본문 보존과 기본 표 복원은 복잡한 다단/수식/선 없는 표의 완전 복원을 뜻하지 않습니다. 구조가 불확실한 표는 원문 텍스트·표 이미지와 경고를 보존합니다.
- 논문 앞단 GROBID/기존 파싱 단계의 단일 네이티브 호출은 즉시 강제 중단하지 않습니다. 페이지 checkpoint/reuse는 일반 PDF 보완 단계에 적용됩니다.
- Office/HWP 변환 PDF는 원본 프로그램과 글꼴·줄바꿈이 다를 수 있습니다. 유일하게 정규화 일치한 문단만 연결하며 반복 문구는 임의의 첫 위치로 연결하지 않습니다.
- 외부에서 수정한 MD는 덮어쓰지 않습니다. 실제 저장 MD를 보여 주며 과거 좌표 연결을 끕니다.
- Git add/commit은 `.git/objects/2a/... Permission denied`로 실패했습니다. ACL 변경·기존 데이터 정리는 하지 않았습니다.
- 이전 UI 시험을 강제 종료해 남은 TEMP `document-ui-z3fv0ryg`의 명시적 정리 명령은 자동 승인 검토에서 정책상 차단됐습니다. 해당 폴더를 다른 수단으로 지우지 않았습니다.
