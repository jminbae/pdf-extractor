# 문서 추출기 구현 진행 기록

## 최우선 방향 변경 — 공개 표준·기성 엔진 우선

사용자는 “이미 있는 걸 이용해야지 왜 다 코딩하느냐”, 이어 “항상 오픈된 방법·이미 검증된 표준 방법을 먼저 찾아 적용하고 우리 특이적인 부분만 우리 방법”이라고 명시했습니다. **직접 PDF 문단/읽기순서 복원 알고리즘을 확대하는 접근은 중단합니다.** 아래 `reading_flow` 구현은 비교 자료이며 production으로 확정하면 안 됩니다.

- root가 `general_pdf.ensure_fulltext`의 custom `reading_flow` 호출3줄을 제거했습니다. Docling table13 회귀를 확인 중입니다. App의 flowversion migration/진행/원본보기/경고정리는 선택한 SDK engine identity에 맞춰 연결해야 합니다. UI/root 기타 승인된 수정은 유지합니다.
- existing_extractor는 기존 Docling2.142.1 isolated runtime+384MB 모델로 **standard/Pdfium 전체 본문·그림·표 exporter** 실제49–54쪽 비교 중입니다. OCR/VLM/LLM/network는 끄고 기존SDK를 사용합니다. adapter는 결과 선택 후에만 작성합니다.
- office_hwp_research는 새 격리 `engines/pymupdf4llm-runtime`에서 **PyMuPDF2.0/4LLM2.0** 공식 API를 실측 중입니다. 2026-10-19 공식PyPI2.0은 4LLM 기능을 PyMuPDF로 통합했음을 확인했습니다. 기존 메인1.28/Doclingruntime는 변경하지 않습니다.
- 직접 복원 비교물은 DevCache/reading-flow-20261023/candidate-output 아래396쪽MD/184assets, reflow1.066초/본문6738→2918/마진358/비공백문자Counter보존/표18·그림184 유지/원본187파일hash불변입니다. **이 좋은 수치도 사용자의 표준 엔진 우선 원칙을 대체하지 않습니다.** 그림/각주 위치와 일관성 등엔진 비교 기준으로만 사용합니다.
- 근거 공식링크: https://pypi.org/project/pymupdf4llm/ , https://pymupdf.readthedocs.io/en/latest/changes.html , https://docling-project.github.io/docling/reference/docling_document/ .

## 진행 중 — 1.2.1 실제 책 품질 수정

사용자가 실제 책의 줄별 문단/`Page N`/쪽 번호 혼입/그림이 뒤에 모임/모순·반복 OCR 안내와 파일 목록 상태 부족을 제보했습니다. **아래 1.2.0 최종 상태는 이전 버전 검증 기록이며 실제 책 품질 통과를 의미하지 않습니다.**

- 최신 작업·재현 근거는 `docs/implementation/2026-10-23-book-reading-fix.md`입니다. 실제396쪽/178,356,365 bytes의 원본 SHA256을 기록했고 원본/현재Documents MD/AppData 정본에는 쓰지 않습니다.
- root: App source-only preview/search, perfile progress/md_ready, warning_summary, 원본 수정 renderer key/PNG 폐기, 옛MD/현재HTML 혼합 차단, source.locations 보존, 기존 general PDF needs_refresh를 구현/독립 승인했습니다. Workflow6/Warning3/Comparison9 PASS. version.py는1.2.1입니다.
- office worker: ui.py 원본더블클릭·검색/MDstrict/진행색+게이지/경고요약+원문상세, frozen harness 확장. 195738 UI1336/375 GREEN/독립승인. 추가로 막대를6px로 더 선명히 하고 실제 두 번 클릭 경계를 확인 중입니다.
- existing_extractor: reading_flow.py/general_pdf의 문단/마진/그림anchor/2단/Korean어휘 근거 단어 연결/OCRblank 상태 작업 및 실제396쪽 후보를 담당합니다. 네이티브/OCR 재실행 없이 기존JSON을 읽어후처리/원자산 읽기복사→DevCache 후보로 검증합니다.
- implementation_review가 각 slice를 독립 검수 중입니다. 최종 PDF QA→전체시험→1.2.1 빌드/새EXE2종→소스백업/최종문서가 남았습니다. 기존1.2.0 Setup은 새 빌드 전에 releases/1.2.0으로 보존합니다.

## 최종 상태 — 2026-10-23 / 1.2.0

**이 절이 아래 모든 과거 진행 기록보다 우선합니다.** 구현·검수·빌드·실제 새 EXE 검증을 마쳤습니다. 사용자 기존 설치 교체나 GitHub 게시를 실행한 것은 아닙니다.

- 기본 출력: Windows Documents KnownFolder / `문서 추출기` (`C:\Users\Bae\Documents\문서 추출기`). 설정에서 변경·초기화하고 재실행 후 유지합니다. MD와 상대 이미지 assets를 같이 자동 저장합니다. 이전 저장물은 이동하지 않습니다.
- 파일/폴더 선택·native FullPath 드래그, 읽기 전용 결과/MD 코드/원본 검색·대조, 기존 논문 경로+일반 PDF 본문 보완, 동봉 로컬 OCR, native/rHWP/선택 SDK 어댑터를 연결했습니다.
- 상단 설정/업데이트 버튼과 자동 옵션(default ON)을 추가했습니다. 정상 frozen 앱 시작 시 확인·다운로드, 정상 종료 시 설치; 수동 설치는 idle 종료 후 재실행입니다. 개발/hidden/OCR worker에서 자동 설치하지 않습니다.
- 고정 GitHub `jminbae/pdf-extractor`의 안정 버전 tag와 정확한 installer/SHA256 두 자산만 받습니다. 현재 공개 v1.0에는 SHA 자산이 없고 새 1.2.0은 미게시입니다. 실제 live check는 현재 1.2.0을 up_to_date로 판단했습니다. 다음 버전을 installer와 checksum을 함께 게시해야 업데이트가 전달됩니다.
- **전체181 tests PASS36.622s**, native JS bridge smoke PASS, 독립 검수 P1/P2 없음. 설정10/업데이터15, 잘린 HTTP/오류 반환/경로 링크/GUIdestroy 교착/늦은 설정 응답/CSV→PDF 오래된 오류 안내를 회귀 시험으로 고쳤습니다.
- 최종 EXE `D:\DevCache\260725 PDF 추출\dist\PDF Extractor\PDF Extractor.exe`, SHA256 `89de6a22fdad1af6885f823d60e4ec541552e28cc1e5b60b08d5ba1e4aaaf0c0`.
- 최종 Setup `D:\DevCache\260725 PDF 추출\PDF-Extractor-Setup.exe`, 60,228,988 bytes, SHA256 `2e10613fcb720bef21f1c3b9c5fa2a8be87af5bfacb31bf748f16013523330dc`. 같은 경로 `.exe.sha256` 생성. Inno 주석 구문 오류 수정 후 ISCC 단독 성공/검수 승인.
- 새 EXE 실제 default+명시 수동업데이트 `frozen-smoke\20261023-193557-71227161\receipt.json`와 scan `20261023-193557-3af8e403\receipt.json` 모두 PASS. 실제 settings1.2.0/readonly/path/auto, CSV표/MD 저장/PDF PNG 오류0/원본 불변/TEMP정리 검증. scan은 동봉 OCR→partial/인식 경고 유지. hidden screenshot은 timeout으로 saved=false를 기록하며 시험에서만 img eager를 사용했습니다.
- 이전 빌드 CDP native file drop→FullPath→목록 추가→MD6284: `frozen-smoke\20261023-193040-a5a26bbc\receipt.json` PASS. 실제 OS 마우스 drag gesture와는 구분합니다.
- 상세 검증·엔진/정확도 한계: `docs/implementation/2026-10-23-verification.md`. 실제 실패 책 원본/다른 PC 신규 설치/실제 기존 설치 교체는 미검증입니다. rHWP+OCR 데이터는 동봉; Docling/MarkItDown SDK·Pandoc·LibreOffice는 이 PC의 별도 엔진입니다.
- root UI 시험 서버53926는 정상 shutdown→exit0로 종료했고 새 frozen fixture TEMP/자신의 Job도 정리됐습니다. 자동 승인 검토가 거절한 과거 TEMP4개는 아래 기록 그대로 남겼으며 우회 정리하지 않았습니다.
- Git branch `codex/document-markdown`, base `990999a`. Git object 권한 오류로 commit은 하지 못했습니다. ACL/Dropbox 설정/기존 생성물을 변경하지 않고 소스 안전망을 `D:\DevCache\260725 PDF 추출\source-backup\20261023-193557-1.2.0`의 변경 소스 ZIP·git binary diff·manifest로 남깁니다.

## 최신 사용자 변경 / 배포 진행 — 2026-10-23 19:05 KST 무렵

**저장 위치 변경이 앞선 모든 '원본 옆 Markdown' 기록보다 우선합니다.** 사용자가 드래그 입력에서는 원래 위치를 의식하지 않으므로 Windows 문서 폴더로 모으는 것을 재차 설명했습니다. root는 기본을 **실제 Windows Documents KnownFolder / `문서 추출기`**로 바꾸겠다고 안내했습니다. MD와 `assets/<문서ID>`를 모두 이곳에 자동 저장합니다. 파일/폴더 선택도 동일하며 기존 MD/원본은 이동·삭제하지 않습니다.

- root 신규 `output_location.py`: SHGetKnownFolderPath Documents GUID 사용(리다이렉션 반영). 실제 읽기 결과 `C:\Users\Bae\Documents`. `PUBNEXUS_OUTPUT`가 있으면 명시 경로 override; 시험은 전부 TEMP로 지정합니다.
- App.output_dir→export.output_dir, state/folderpush/GETfiles.output_dir, Api.reveal_output() noargs(선택 파일 없어도 저장 폴더 열기), folder_files의 현재 중앙 output 재수집 제외를 연결했습니다.
- 신규 위치2 tests/중앙 동명CSV두폴더→같은출력/현재output재수집제외/빈목록저장폴더열기까지 **app28 tests PASS1.134s**. 독립 검수 요청 중입니다.
- officeworker 소유 ui.py 및 tests/ui_preview.py/native_smoke.py/frozen_smoke.py에서 actual output_dir 표시·reveal_output 호출·PUBNEXUS_OUTPUT TEMP 격리 반영 중입니다. source folder path와 output path는 구분합니다.
- 앞선 전체 **150 tests PASS35.933s**, native WebView2 JS→native→저장/원본불변 smoke PASS. 이후 새 KnownFolder/debug 조건 시험으로 최종 수가 증가하므로 다시 실행합니다.
- 첫 실제 빌드/Setup 생성 성공: `D:\DevCache\260725 PDF 추출\dist\PDF Extractor\PDF Extractor.exe` SHA256 `3350836780f0144d27600cae6522b1c02c89c1637a1f05683f58a732aeed1f15`, Setup57.4MB. rHWP+eng/kor언어데이터+라이선스 동봉. **새 저장 경로/아래 CDP 수정 전 artifact이므로 다시 빌드해야 합니다.**
- 첫 frozen_smoke(185250-79f825f6)는90초 CDP target timeout. 변환/부팅 실패로 단정하지 않습니다. EXE --ocr-worker 실제 호출은 `FROZEN OCR CHECK7391` 성공.
- **CDP 원인 확정:** Codex/shell elevated=True에서 WebView2는 환경변수 flag를 무시합니다(Microsoft 공식 문서). source A/B 동일hidden/elevated: env-only 로드는 정상/CDP없음15.67s, webview.settings.REMOTE_DEBUGGING_PORT 명시 시1.437s CDP정상. `webview-diagnostic-20261023/results.json`, DIAGNOSIS.md.
- root main은 **명시 --hidden + PNX_TEST_DEBUG_PORT** 둘 다 있을 때만 1..65535 정수 검증 후 pywebview REMOTE_DEBUGGING_PORT 속성을 적용합니다. 일반 실행은 환경값이 있어도 디버그 포트를 열지 않고 OCR early-dispatch도 유지합니다. API12 PASS/독립승인. 테스트 도구는 같은 예약 port를 PNX_TEST_DEBUG_PORT로 전달하도록 수정/승인했습니다. JobObject OFF A/B는 하지 않았고 옵션도 제거했습니다.
- 전 생산 코드의 이전 Docling/로컬OCR/외부adapter P1/P2는 최종 해결 승인됐습니다. 새 중앙 저장/UI diff 검수 및 재빌드→frozen default/--scan 두 실제검증이 남았습니다. 최종 실제 drag-drop도 가능하면 CDP native file event로 확인합니다(실제 OS 포인터 제스처와 구분).
- 자동 승인 검토가 거절한 ownTEMP: 기존 document-ui-z3fv0ryg 외 `pubnexus-docling-4izcc8qp`, `pubnexus-docling-it2qxcgu`, `pubnexus-docling-pxzk83gl`(모두 C:\Users\Bae\AppData\Local\Temp). 명시 사유 `rejected: blocked by policy`, 상세 사유 없음. 우회 삭제 안 했습니다. 최종 짧은 별도 문단에 요약합니다.

## 진행 갱신 — 2026-10-23 18:30 KST 무렵

- 외부4형식 `external_documents.py/test_external_documents.py` 동결/독립승인: Pandoc RTF·IPYNB, MarkItDown XLS·MSG의 실제 합성 입력, 원본 불변, 출력 예산, private subprocess 취소/timeout, 원격·file URI 미수집 **8 tests PASS**. XLS sheet-by-index 예산검사/unload P2 해결. root app4suffix+exclusive routing+실제 RTF→MD 연결도 API8 PASS/검수 승인.
- root 파일/폴더 drag-drop: pywebview 공식 DOMEventHandler/pywebviewFullPath 사용. fake name/path 추정 금지, 처리 중 추가 거절, 기존 폴더 Markdown 재수집 제외. input5 PASS·독립승인. 실제 OS 드래그 제스처 자체는 미검증, 기존 native smoke는 다시 PASS.
- root main은 명시 `--hidden`만 기존 build(hidden=True)를 사용하도록 연결/승인. 일반 실행은 그대로입니다.
- worker `office_hwp_research` 신규 소유 `tests/frozen_smoke.py`: TEMP/store/profile/합성PDF캐시+CSV만 대상으로 새 frozen EXE를 숨겨 띄우고, Node22 내장 WebSocket/CDP로 실제 bridge 저장/읽기/PDF 대조를 검증합니다. Windows own PID Job Object만 정리. prepare-only PASS, cleanup receipt에 관한 마지막 검수 보완 중. **빌드 아직 안 함**.
- Docling 실제 비교: 기본 parser+ASCII진단 44/44 셀 anchor, native29/44. 공식 Pdfium+cellmatch=True는 ruled/무테15+15 정확/merged12/14; False는 숫자누락이라 제외. 4행 작은 무테 표는 표0 검출이므로 모든 표 정확 보장 금지.
- worker `existing_extractor` 신규 소유 `docling_tables.py`, `docling_table_child.py`(CHILD_CODE 문자열), `test_docling_tables.py`. privateSDK `-I -B -c`/offline/모델사전설치 조건, caption확정 missing 표쪽만 선택. root app hook은 신규/캐시 PDF 모두 failed 거절 뒤 enhancement→sourceStat→export, API9 PASS. stage `docling_tables`를 한국어로 표시합니다.
- **Docling 최종 검수는 아직 진행 중입니다.** reviewer가 행별 숫자셀 교환을 성공 수락하는 P1을 실제 재현했습니다. 행/열/span 기하 검사, HTML셀정렬, 본문refs_table anchor, 원본hash 변경 rollback, O(n²) 검사/취소 보완 중입니다. root는 parent extraction_report.partial 반영과 총8쪽하드캡 대신8쪽 child batch로 전체후보 처리도 요청했습니다. 모듈 동결 전 통합 전체 PASS로 보고하지 않습니다.
- root는 별도 HTML 병합표 대조 bug도 발견했습니다: comparison._items가 raw HTML 태그/colspan을 원문문자로 찾아 연결을 놓침. canonical cell row/col 값 정렬의 plain text + legacy HTML no_network itertext로 수정, comparison8 PASS. 독립검수 대기.
- 빌드를 기다리는 officeworker에게 PyMuPDF 내장 OCR이 Tesseract.exe 없이 pinned eng/kor tessdata만으로 가능한지 DevCache 합성 스캔 proof를 부탁했습니다. production general_pdf 소유는 PDFworker이므로 이 단계에서 수정하지 않습니다. 별도 OCR framework/전역 설치 금지.

## 최신 갱신 — 2026-10-23 18:10 KST 무렵

- 사용자 추가 승인: “니가 가장 베스트라고 생각하는 방식으로 진행해”. Python 앱/오케스트레이션과 rHWP Rust 엔진 유지. 전면 Rust 재작성은 하지 않습니다. 유명 도구를 전부 매번 돌리지 않고 목적별 재사용/실측으로 선정합니다.
- native 형식 확장 최종 승인: EPUB/ODF/EML/MBOX/Markdown 로컬 이미지 포함 **documents29 tests**. ODF 반복 공백/셀 누적 예산, MIME 생성 part 제한·취소·중첩 오류 정형화까지 승인. app suffix/picker/ODP·ODS 단위 연결 및 markdown-it-py4.2.0/frozen import 추가도 승인.
- root 현재 전체 **116 tests PASS 5.803s**, Windows WebView2 실제 JS→native bridge→MD 저장 smoke PASS. receipt의 원본 digest/MD 존재도 실제 assert합니다. `native-smoke.json` 갱신.
- 최신 실제 UI에서 PDF2+DOCX/PPTX/XLSX 모두 저장 완료. Word 검색8461의 source/result hit·원본 영역 강조1개·table1개·editable0·가로 넘침0 재확인. 테스트 selector의 accessible name/input ID 착오 두 건은 제품 수정 없이 고쳤습니다.
- 실제 목록/Word 대조 screenshot 갱신: `D:\DevCache\260725 PDF 추출\ui-preview\actual-app-list.png`, `actual-word-compare.png`.
- 기존 UI harness61809/60950은 정상 shutdown. 현재 harness **session35609**, `http://127.0.0.1:53926/`, TEMP `document-ui-nixwswdx`; 동일 `/preview-api` shutdown 가능.
- UI 성능 worker 최종: 300쪽1200블록 source p95 5.9→0.7ms/result4.2→1.4ms, mapping180회 mismatch0/functional11PASS/JS error0. 검수 승인. 예전 성능fixture58771의 자기 프로세스만 종료.
- private SDK 설치 완료: `D:\DevCache\260725 PDF 추출\engines\docling-runtime\Scripts\python.exe`, Docling2.142.1/MarkItDown0.1.9/Torch2.14.1+cpu. 메인 pnxvenv/빌드에 heavy SDK를 넣지 않습니다.
- Docling 비교는 `existing_extractor` 소유 DevCache `docling-benchmark-20261023`. Heron171.7MB/TableFormer accurate212.8MB만 pinned snapshot 다운로드, OCR/VLM/classifier/codeformula 제외. 첫 실제 실행은 upstream docling_parse7.23.0의 **Windows 한글 설치 경로 resource 오류**가 재현됐습니다. 파일 존재 확인 후 ASCII package/resource 경로 진단 중이며 아직 품질 비교 성공/제품 연동으로 보고하지 않습니다.
- `office_hwp_research`는 신규 `external_documents.py`/`test_external_documents.py` 소유: Pandoc3.11 RTF/IPYNB, MarkItDown XLS/MSG를 별도 subprocess로 실제 검증/연결 중. 기존 documents.py는 동결, root가 app routing/suffix 연결 예정입니다.
- 검수관 `implementation_review` 재사용. core/app/export/native/UI/preview P1/P2 없음. 새 외부엔진 및 Docling 시험 코드는 작성 직후 검토.
- 실제 frozen build/Setup 생성은 외부 adapter 완료 후 수행할 남은 작업입니다. Git object 쓰기 실패/자동 승인 검토가 거절한 옛 TEMP 정리 제한은 아래 기록 그대로입니다.

## 최신 상태 — 2026-10-23 17:45 KST 무렵

아래 `작업 위치와 실행` 이후의 초기 기록보다 이 절이 우선합니다. 구현을 계속 진행 중이며 아직 최종 사용자 전달 전입니다.

- 통합 unittest **106개 PASS**. 이후 EPUB/ODF/email 확장과 UI 성능 작업이 진행되므로 최종 시험/빌드는 다시 해야 합니다.
- core PDF 보존·표/병합/원본 숫자·이미지·선택 로컬 OCR·취소·쪽 checkpoint/reuse는 worker와 독립 검수 각 **30 PASS**, 미해결 P1/P2 없음.
- native DOCX/PPTX/XLSX/CSV/TSV/TXT/MD/HTML/HWP/HWPX는 **20 PASS**, 실제 합성 HWPX/HWP5/병합 CLI 포함 독립 승인. EPUB 1개 추가 회귀도 통과/승인했고 ODF/email/MD 이미지 수집은 `office_hwp_research`가 마무리 중입니다.
- 새 `ui.py`를 실제 `app.py`에 import했습니다. compact 회색 목록/속성/읽기 전용 결과/Markdown 코드/원본 비교/검색/양방향 클릭과 블록 기준 연동이 실제 App/Api와 동작합니다. `existing_extractor`가 300쪽/1200블록 스크롤 인덱스 성능 개선 및 용어/단위 표시를 마무리 중이며 ui.py만 소유합니다.
- root 신설 `comparison.py`: 고유 정규화 일치만 연결, 반복 문구 임의 첫 hit 금지. source.page는 1기반, UI locations.page는 0기반. 회전된 표시 좌표로 변환하고 finite/pageclip 검증. 깨끗한 export MD에는 source marker를 넣지 않고 내부 HTML에만 escaped `data-block-id`를 붙입니다.
- 앱은 실제 저장 MD·assets를 각 원본의 `Markdown` 아래 자동 저장합니다. 캐시와 저장 완료를 구분하고 수정된 원본·외부 편집된 MD의 옛 좌표를 거절합니다. 저장 MD는 외부 변경 시 실제 파일을 표시합니다. exports 기록은 store/ui.json에 유지합니다. after-export source stat도 재검사합니다.
- 새 `preview.py`: 원본 수정 없이 사본+private renderer로 PDF 미리보기. rHWP와 LibreOffice를 env/PATH/설치 위치/기기 cache_root에서 탐색합니다. 캐시 identity는 `preview-v3`; 표시 엔진이 바뀌면 앱 지도 캐시도 무효화합니다. Office 미리보기는 별도 LO가 필요하며 앱에 큰 LO 전체를 동봉하지 않습니다.
- **LO 주의:** 긴 SHA cache 아래 profile을 두면 exit 0/PDF 없음 재현. debugger가 입력/엔진/출력을 고정하고 profile만 짧은 TEMP로 바꿔 실패→성공을 입증했고 ExitStack+별도 TEMP profile로 수정했습니다. Writer Link=2, Calc Link=1이 각기 never이며 scripts disabled입니다. 사용자 Office COM은 사용하지 않습니다.
- 실제 DOCX/PPTX/XLSX 미리보기 성공·원본 bytes 불변, 실제 HWPX 미리보기→검색 가능한 PDF→문단/표 매핑→MD 저장도 통과했습니다. 실제 Word 앱 HTTP 대조에서 검색어8461의 원본·결과 모두 1쪽/강조, 편집 노드0 확인.
- 합성 300쪽/1200문단: first12.406초/cache3.390초, 첫·마지막 본문 보존, MD797788bytes, Python peak76.2MiB. 실제 책/스캔 성능 보장은 아닙니다. 자세한 근거는 `2026-10-23-verification.md`.
- Tesseract 바이너리가 없어 실제 OCR 인식률은 미검증. 단위시험의 로컬 OCR 호출/좌표/취소 경계만 통과했습니다. 일반 보완 단계의 page reuse이지 legacy GROBID 앞단 전체를 중간 재개하는 구현이 아닙니다.
- Git add/commit은 `.git/objects/2a/... Permission denied`로 실패. debugger ACL 읽기 점검으로 단정할 원인 없음. ACL/Dropbox/기존 생성물은 바꾸지 않았습니다. 브랜치 `codex/document-markdown`, 코드/문서는 아직 미커밋입니다.
- rHWP 공식 v0.8.7 zip 검증/해제: `D:\DevCache\260725 PDF 추출\engines\rhwp-v0.8.7\rhwp\rhwp.exe`. LO26.2.6 MSI 공식 checksum 검증 뒤 administrative image 해제(애플리케이션 설치 아님): `...\engines\libreoffice-26.2.6\image\program\soffice.exe`. 새 build_exe.ps1은 rHWP+MIT LICENSE를 동봉하고 빌드/Setup을 cache_root에 유지하도록 검수 완료. **아직 실제 빌드 안 함**.

### 현재 실행 중인 시험 서버/남은 정리

- `pubnexus/tests/ui_preview.py` session **21423**, URL `http://127.0.0.1:61809/`, TEMP `document-ui-nvd0vcm9`. 실제 PDF2+DOCX/PPTX/XLSX 합성 입력. 종료는 POST `/preview-api` JSON `{name:'shutdown',args:[]}`로 정상 정리합니다.
- 이전 같은 harness session **71562**, URL60950, TEMP `document-ui-v1i4cnrr`; shutdown 지원. 더 이상 필요 없으면 위 방식으로 종료합니다.
- 가장 처음 harness session95275를 Ctrl+C로 멈춰 TEMP `document-ui-z3fv0ryg`가 남았습니다. 해당 자기 시험 폴더의 명시적 Remove-Item 명령이 **자동 승인 검토 정책상 차단**되어 다른 수단으로 지우지 않았습니다. 사용자에게 이미 짧게 설명했고 최종에도 별도 문단으로 알릴 필요가 있습니다.
- 성능 fixture HTTP58771은 worker 소유. `D:\DevCache\260725 PDF 추출\scroll-perf-20261023\benchmark.js`는 별도 Playwright newContext를 만들어 rootpage를 유지합니다. tool filename loader는 DevCache를 거절하므로 exec Get-Content로 읽은 code 문자열을 browser_run_code_unsafe에 전달하면 됩니다. baseline source p95 8.7ms/max16.7ms, current未최적화 source p95 15ms/max20.2ms, 90scroll에서 rect270360/selector108630. worker 개선 후 다시 측정할 예정입니다.
- 실제 UI screenshot: `D:\DevCache\260725 PDF 추출\ui-preview\actual-app-list.png`, `actual-app-compare.png`, `actual-word-compare.png`. 마지막 screenshot은 table machine-ID caption 제거/새 label 반영 전입니다.
- LO 진단은 `D:\DevCache\260725 PDF 추출\lo-diagnostic-20261023`에 고정 증거. 알려진 첫 TEMP `tmprwz65j_w` 잔존 여부는 기록만 두고 사용자의 기존 생성물 정리로 범위를 넓히지 않습니다.

### 바로 다음

1. 두 worker의 최종 형식/성능 변경 검수. `implementation_review` 재사용, 작성 직후 호출.
2. 확장된 supported suffix를 app DOCUMENT_EXTENSIONS/파일 dialog에 반영. native units는 물리 page와 분리.
3. 전체 unittest/diff --check, 실제 UI 연동 새 label/단위/성능 재검증.
4. `build_exe.ps1` 실행(출력 DevCache), 실제 리뷰용 executable smoke 확인. 원래 설치본은 자동 덮어쓰지 않음.
5. docs 최신 count/남은 범위/실행 경로 갱신, Git 재시도는 같은 권한 실패를 무한 반복하지 않음. 사용자에게 검토용 실행 파일·실제 완료 범위·미검증/커밋 제한을 짧게 전달.

갱신: 2026-10-23. 사용자 `진행하자`로 전체 합의 계획의 구현을 승인했습니다. 연구·계획만 하던 제한은 해제됐으며 원본/기존 생성물 이동·삭제는 승인하지 않았습니다.

## 작업 위치와 실행

- 원본 checkout: `D:\Dropbox\Claude Code\260725 PDF 추출`
- 브랜치: `codex/document-markdown` (master에서 생성). 이 시점 아직 구현 커밋 전입니다.
- Python: `C:\Users\Bae\pnxvenv\Scripts\python.exe` (3.12.10, PyMuPDF 1.28.0).
- 시험: PowerShell에서 `$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH=(Join-Path (Get-Location) 'pubnexus\src')` 후 Python `-B -m unittest discover -s pubnexus/tests -v`.
- unittest를 사용하며 pytest 설치는 하지 않았습니다. 합성 파일·시험 저장소는 TEMP에 격리합니다.
- 새 빌드는 paths.md의 cache_root 아래 프로젝트 폴더로 향하도록 `build_exe.ps1`을 수정했습니다. 실제 빌드 전이며 기존 exe/설치본은 그대로입니다.

## 현재 구현과 소유

- `existing_extractor`: `single.py`, 신규 `general_pdf.py`, `test_general_pdf.py`. 최종 본문 guard·보존 fallback. 한글 삭제/책 부분본문/참고문헌만 남은 경우를 오프라인 합성 PDF로 재현·보완. 정상 논문을 머리말/메타 차이만으로 native 전체 대체하지 않도록 최종 검토 중입니다.
- `office_hwp_research`: 신규 `export.py`, `test_export.py`. MD+상대 자산, 긴 이름/충돌, 외부편집 보호, 부분 쓰기 journal/재시도, 자산경계와좌표sidecar. 개별 21시험 통과 보고, 최종 독립 검토 대기 중입니다.
- root: `app.py`, `render.py`, `test_app_export.py`, `test_app_input.py`, `test_app_api.py`, `test_render_general.py`, 빌드 스크립트. 추출→자동export 연결, 캐시 재추출 없이 내보내기, 저장 완료/추출 캐시 구분, failed report 성공 승격 금지, 변경 원본 invalidate, 파일/폴더 추가·경로 기반 식별, 선택 추출·외부 파일 열기 API. general 본문의 논문용 첫문단 수리도 우회했습니다.
- 검수관: `/root/implementation_review`. 각 작성 직후 검토하며 지적을 회귀시험으로 보완 중입니다. 한 번 model capacity 오류 후 같은 설정으로 재개했습니다.

## API·위치 계약

- `general_pdf.ensure_fulltext(doc, pdf_path) -> (doc, report)`.
- general source는 `pdf_general`. `extraction_report.status`는 complete/partial/failed. failed는 자동저장 완료로 올리지 않습니다.
- native 문단 ID 예: `pdf-p000002-b000000`; `paragraph.source={page:2,block_id,bbox:[x0,y0,x1,y1]}`.
- source_map coordinate_space는 `pymupdf_unrotated_points`; 페이지번호 1부터. pages에는 크기·회전·추출상태·시도수·block IDs, blocks에는 page/bbox/ID가 있습니다. 정상 paper의 미연결 상태를 native 정밀 연결이라고 표시하지 않습니다.
- `export.export_document(doc, source_path, *, output_dir=None, asset_base=None) -> ExportResult(markdown_path,assets_dir,manifest_path,revision,unchanged)`.
- 일반 결과명은 원본stem.md, 길이/충돌에만 suffix. `assets/<경로ID>/manifest.json`에 결과 hash/좌표 자료, journal은 pending.json입니다. export가 정상 반환돼야 완료입니다.
- `Api.pick_files()`, `pick_folder()`, `extract_selected(indexes,force=False)`, `open_file(index,kind='source'|'markdown')` 추가. 기존 bridge와 `/open?p=`, `/page?k=&i=&w=`는 유지합니다.
- file_rows는 name/path/done/cached/status/size_bytes/pages/warnings/markdown/error를 반환합니다. push mark는 실제export완료만, cache 발견은 cached 이벤트입니다.

## 검증 상태와 주의

- root 앱/입력/캐시 시험 11개, 외부열기·선택API 3개, general renderer 1개가 통과했습니다. full suite는 두 worker의 마지막 변경 이후 다시 실행해야 합니다.
- 일반 PDF·export 각 worker는 추가 회귀를 작성 중이므로 전역 최종 통과로 보고하지 않습니다.
- 첫 폴더 단위시험에서 background `_reconcile`이 TEMP PDF를 읽는 중 정리와 경합해 WinError32가 났습니다. 폴더필터 시험에서는 무관한 background I/O를 stub해 해결했고 실제 thread 통합시험은 완료 대기가 필요합니다. 이전 실패 TEMP 폴더 `tmprwz65j_w` 잔존 여부는 종료 시 확인·자기 시험자료만 정리합니다.
- 실제 문제 책·설치 앱·GUI·HWP/Office 변환은 아직 시험하지 않았습니다. 기존 UI는 아직 legacy 3분할이며 승인된 native 목록 UI 적용·검색/양방향 대조·표 개선·형식확대·패키징이 남아 있습니다.
- 기존 연구 때 Git object 쓰기 실패가 있었으나 이번 브랜치 생성은 성공했습니다. 최종검수 후 stage/commit을 시도하고 실제 결과를 기록해야 합니다.

## 계속할 순서

1. 두 worker의 최종 검수·공통 테스트를 통과시키고 PDF 보존/자동저장 기반을 커밋합니다.
2. 승인된 `document-extractor-native.html`/`document-extractor-screens.html` 스타일을 실제 `ui.py`로 구현합니다. 시안 fake 데이터·시연타이머는 제품에 넣지 않습니다. root는 app/API 연결을 담당합니다.
3. 원문좌표↔MD블록의 실제 source map과 읽기 전용 검색/클릭/스크롤 연동을 구현합니다. 기존 PNG preview를 활용할 수 있으며 PDF.js 교체는 전제가 아닙니다.
4. rHWP CLI, DOCX/PPTX/XLSX 등의 구조 어댑터와 미리보기 어댑터를 순차 추가하고 오프라인 회귀·긴 문서/폴더 안정성을 검증합니다.
5. 새 cache_root 경로로 빌드하고 검토용 실행을 확인합니다. 기존 설치본을 검증 없이 덮어쓰지 않습니다.
