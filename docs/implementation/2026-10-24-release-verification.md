# 1.2.2 검증·배포 기록

## 최신 — 공개 배포·개인용 설치·책 결과 반영 완료

[v1.2.2 공개 릴리스](https://github.com/jminbae/pdf-extractor/releases/tag/v1.2.2)를 게시했습니다. tag commit은 **`f47554563a9b6e2a0638df2d9b75c7035f2fc1e4`**, 기능 코드 동결은 **`63fe7c2dd4ebf1ac9c23629fbad06417de116ff5`**입니다. 이 절이 아래 배포 전 기록의 미게시·미설치 상태보다 우선합니다.

로컬 증거의 기준 폴더는 `D:\DevCache\260725 PDF 추출`입니다.

| 실제 수행 | 결과·근거 |
| --- | --- |
| 소스·설치본·checksum 공개 | 세 자산을 게시 후 다시 내려받아 SHA256 일치 확인, `releases/1.2.2/published-verification.json`의 `ok=true` |
| 기존 시스템 설치 제거 | 표준 제거 프로그램 exit0, `uninstall-1.2.2-system.log`; 사용자 데이터 삭제 규칙·정책·ACL을 추가하지 않음 |
| 현재 사용자 설치 | 게시된 Setup을 `/CURRENTUSER`와 명시 경로로 설치 exit0, `install-1.2.2-user.log`에 비관리 설치 모드·HKCU·성공 기록 |
| 설치 경로·버전 | `C:\Users\Bae\AppData\Local\Programs\PDF Extractor\PDF Extractor.exe`, HKCU1.2.2·기존 HKLM 설치 항목 없음 |
| 설치된 EXE 기능 | `frozen-1.2.2-installed-native/receipt.json`의 `ok=true`; PDF/CSV·JS bridge·자동 저장·설정1.2.2·PNG 대조·합성 원본 불변 PASS |
| 실제 GitHub 업데이트 확인 | 설치본의 수동 확인 `up_to_date`, 현재/공개 버전1.2.2; 숨김 QA의 `install_supported=false`는 자동 설치를 막는 정상 동작 |
| 실제 책 결과 반영 | `book-promotion-1.2.2.json`의 `ok=true`, 실제 Documents MD SHA와 SDK 그림186개 확인, 원본 PDF 불변 |
| 정상 설치 앱 실행·실제 책 열기 | PID28424의 실제 창 `문서 추출기` 확인, 앱 localhost `/files`·`/open`으로 실제 책 MD SHA 일치·준비됨/refresh=false·HTML 그림186/표18·가짜 Page 제목0 확인 |

설치 후 증거는 [설치·등록·정상 창 종합 영수증](<D:/DevCache/260725 PDF 추출/installed-1.2.2-verification.json>), [설치본 native 기능 영수증](<D:/DevCache/260725 PDF 추출/frozen-1.2.2-installed-native/receipt.json>), [정상 앱 실제 책 열기 영수증](<D:/DevCache/260725 PDF 추출/installed-book-open-1.2.2.json>)입니다. 독립 검수관은 실제 MD/manifest 그림186개·원본 PDF·백업189개·HKCU·ACL·실제 실행 파일 SHA를 다시 확인하고 미해결 P1/P2 없음으로 승인했습니다. 정상 앱은 사용자가 볼 수 있게 열어 두었습니다.

게시된 `PDF-Extractor-Setup.exe`는 **243,034,665 bytes**, SHA256 **`7301324632eec9a0b3376ab7a371c7f467c4b636ea96cb999f964b8c61927add`**입니다. 게시 소스 ZIP `PDF-Extractor-v1.2.2-source.zip`은 960,435 bytes·145 entries, SHA256 `f2ec09849cd339c55f90abf1da90eb807fb25b54abbb1fb3e972ab498289a614`입니다. checksum 파일 자체 SHA256은 `deca69469b5d1ae641a10a7ded8f150689d25f82dfc445008e540ea7b4eeb1d3`이며 내용의 installer 해시도 일치합니다. 설치된 App SHA256은 아래 빌드 App과 같은 `68f2eba82a0189cddcc344248b85c054081c65e818cfb193dc372ef4f85401be`입니다.

책 결과는 `C:\Users\Bae\Documents\문서 추출기\내지-백반증 바이블-3교.md`에 반영했고 SHA256은 **`6878bed61a231e362e3819032a31ced0f658e8a21e44468e41f4d93c2b4dfe4f`**입니다. SDK 그림186개·canonical·출력 index를 함께 갱신했으며 문서 상태는 OCR/교정 안내를 포함한 `partial`, MD는 준비된 상태입니다.

이전 결과와 복구 자료 **189개 파일**은 `D:\DevCache\260725 PDF 추출\deployment-backup-1.2.2`에 보존했습니다(`receipt.json`, `ok=true`). 반영 직전 보호 자료188개를 원래 SHA와 대조한 뒤 캐시·MD·자산·UI/index를 의도적으로 갱신했습니다. 따라서 아래의 185개 불변은 **v4 후보 검증 단계의 사실**이며, 현재 기존 결과 전체가 그대로라는 뜻이 아닙니다. 사용자 원본 PDF는 이 반영 후에도 동일 SHA를 유지합니다.

이 기기는 `EnableLUA=0`이며 실제 Explorer와 검증 프로세스가 관리자 High 토큰입니다. 정책·계정·ACL은 변경하지 않았습니다. `/CURRENTUSER`의 HKCU/사용자 경로 설치는 확인했지만 **실제 비승격 환경의 자동 설치를 실측한 것은 아닙니다.** 설치된 EXE 영수증의 숨김 스크린샷은 `saved=false`입니다. 정상 창의 존재와 실제 앱의 책 열기는 위 Win32/API 영수증으로 확인했으며, 스크린샷 성공이나 모든 페이지의 육안 검수를 수행했다고 주장하지 않습니다.

## 배포 전 검증 기록 — 게시·설치 이전 시점

기준 소스는 `63fe7c2dd4ebf1ac9c23629fbad06417de116ff5`, 앱 버전은 **1.2.2**입니다. 빌드·실제 EXE native/scan 기능 검증·전체 책 v4와 번들 모델/고지의 독립 최종 검수를 마쳤으며 미해결 P1/P2는 없습니다. **이 기록 시점에는 설치·GitHub 게시·배포 후 업데이트 검증을 아직 수행하지 않았습니다.** 이전 [1.2.1 검증](./2026-10-23-sdk-final.md)은 보존된 과거 결과입니다.

이 기기의 증거 기준 폴더는 `D:\DevCache\260725 PDF 추출`입니다. 아래 상대 증거 경로는 이 폴더를 기준으로 합니다. 사용자 원본과 기존 Documents 결과에는 쓰지 않고 후보·로그·빌드만 DevCache에 저장합니다.

## 배포 전 판정

| 항목 | 확인 상태 | 근거 |
| --- | --- | --- |
| 최종 소스 전체 회귀 | **242개 PASS, 92.482초** | `test-1.2.2-final.log` |
| 실제 1.2.2 EXE native 문서 | **PASS**: JS→native bridge, PDF·CSV 자동 저장, 읽기 전용 결과/MD 코드, 원본 미리보기·검색, 설정 버전, PNG 디코딩, 원본 불변 | `frozen-1.2.2-final-native/receipt.json` |
| 실제 1.2.2 EXE scan 문서 | **PASS**: SDK 로컬 OCR 본문 자동 저장, PNG·원본 불변, 인식 안내와 `partial` 유지 | `frozen-1.2.2-final-scan/receipt.json` |
| 실제 frozen SDK worker 품질 slice | **PASS, 25.344초**: 알려진 분절 잔여0, 한글6수정/쪽1연결, 그림3개·병합 HTML표 유지, 원본 불변 | `frozen-1.2.2-sdk-quality/receipt.json` |
| 실제 책 396쪽 v4 | **실행·독립 검수 PASS, 454.647초**; 인식/교정 확인 안내로 산출물 상태는 `partial` | `pymupdf4llm-benchmark-20261023/whole-396-v4-quality/final-quality-verification.json` |
| installer·번들 | **exit0**, 243,034,665 bytes, 인접 checksum 일치; 실제 모델/고지 독립 검수 승인 | `build-1.2.2-final.log`, 아래 artifact 표 |
| 설치·공개 배포·게시물 다운로드 대조 | 이 단계에서는 미수행; 이후 결과는 맨 위 최신 절 참조 | `releases/1.2.2/published-verification.json` |

native/scan EXE 영수증의 숨김 WebView 스크린샷은 `Page.captureScreenshot` timeout으로 `saved=false`입니다. 기능 검증 성공을 스크린샷 저장 성공으로 표현하지 않습니다. 원본 `<img>` eager 전환은 검증 하네스에서만 사용했습니다. scan PASS는 합성 입력의 인식·저장·안내 계약을 확인한 것이며 모든 OCR 문자의 정확도를 뜻하지 않습니다.

## 적용 원칙과 수정 범위

- 일반 PDF의 문단·읽기 순서는 **공식 PyMuPDF 2.0 Markdown SDK**가 담당합니다. 기존 정상 논문의 GROBID/JATS 경로와 별도 메인 PyMuPDF 1.28.2는 유지합니다. 자체 범용 `reading_flow`는 제품 경로에서 제거했습니다.
- SDK가 짧은 제목을 제외한 쪽은 공식 header/footer 옵션으로 재시도하여 공식 chunk를 교체합니다. OCR도 동봉된 로컬 공개 엔진만 사용합니다. LLM/VLM·유료 API·실행 중 모델 다운로드를 추가하지 않습니다.
- 한글 보완은 **SDK 본문 블록 안의 실제 native 물리줄 경계**에 한정합니다. Kiwi 0.24.1의 공개 `glue`·`space`·분석 결과와 원문 어휘 증거를 검사하고, 문자 교체 없이 확인된 공백만 보정합니다. 표·그림·캡션·코드·목록·참고문헌을 일반 문장처럼 재조립하지 않습니다.
- 인접 쪽 연결은 같은 글꼴·크기, 문단 기하, native 문자열 일치와 동일 어휘의 원문 증거가 맞는 지역만 허용합니다. p51/p52의 이어지는 본문을 연결한 후 사이의 그림·캡션은 내용과 상대 순서를 그대로 보존합니다. 최초 SDK raw와 단계별 입력, 수정 전후 문자범위, 여러 원본 위치를 각각 기록합니다.
- 병합 표는 공식 `Page.find_tables(refine=True)`·`Table.to_html`을 재사용합니다. 행·열별 셀 문자·숫자·기호가 일치하는 유일 후보만 수용하고 HTML span을 보존합니다. `HER2+`/`HER2-`, `>10`/`<10`, `12.50%`/`12.50`을 같은 값으로 정규화하지 않습니다. 합성 통합 결과는 `table-span-20261024/integration.json`에 있습니다.
- SDK 변환 실패 후 native 복구로 돌아갈 때 이전 `sdk_*`·변환 identity를 폐기합니다. 새 본문 위에 오래된 SDK MD/그림 문자범위를 재사용하는 캐시 혼입을 막는 회귀를 추가했습니다. 기존 사용자 MD 파일을 변경하는 동작은 아닙니다.

## 알려진 한글 분절의 실제 검증

`korean-boundaries-20261024/final-known-pages-proof.json`은 전체 원본 **396쪽의 native 어휘 corpus**와 공식 SDK의 대상 **51·52쪽 chunk**를 사용하는 공개 `refine_record` 호출입니다. 전체 396쪽 SDK 재변환 결과는 아니며 v4 완료 판정과 구분합니다.

| 확인 항목 | 결과 |
| --- | --- |
| 대상 물리줄 경계 | 24개 전수 열거, 블록 내부 공백 보정 9개 |
| 쪽 경계 문단 | 1개 연결; 두 쪽 source 위치 보존 |
| 알려진 `통 해`, `보이 지`, `멜라닌색 소가` | 대상 SDK 본문의 잔여 각각 0 |
| 사이 그림·캡션 | 보호 항목 4개 내용·순서 동일 |
| 정상 공백 반례 | `보호하며 일반적으로` 그대로, 잘못 붙임 없음, 수정 0 |
| 원본 PDF | SHA256 불변 |
| 의미 정확도 전체 측정 | **하지 않음** |

이전 2쪽만의 어휘 corpus에서는 `보이지` 증거가 없어 안전하게 수정을 거절했습니다. 그 실패 기록은 `subset-only-witness-proof.json`에 보존했습니다. 최종 검증은 실제 전체 문서에서 unbroken `보이지`를 p40·54·68·236·241·260·344에서 확인한 전제이며, 테스트 전용 판정 대체나 단어별 예외를 넣지 않았습니다. 상세 결정은 `all-boundaries.json`, 실제 모델 반례는 `anti-case.json`에 있습니다. 독립 검수는 한글 공개 시험 8개를 포함한 59개 PASS 및 위 증거를 확인하고 미해결 P1/P2 없음으로 승인했습니다.

원본 PDF의 기록된 SHA256은 `556f4011fb4de9cf53200ecbfe376e13847d073771ac9dafcb22b02a2767a0c1`입니다. 이 문서를 위해 사용자 원본을 추가로 열지 않았습니다.

## 전체 책 v4 결과

`pymupdf4llm-benchmark-20261023/whole-396-v4-quality`의 `receipt.json`, `final-quality-verification.json`, `quality-and-export.json`, `protected-originals-and-source.json`이 근거입니다. 실행 엔진은 **pymupdf-2.0-adapter-3**입니다. 396쪽 실제 변환·자동 검증과 독립 최종 검수 모두 PASS입니다. 독립 검수관은 canonical/MD/그림/export/manifest와 보호 파일·소스의 실제 해시를 다시 대조했습니다.

| 항목 | 관찰 결과 |
| --- | --- |
| 시간·크기 | 454.647초, SDK MD 254,813문자, 블록 1,934개 |
| 쪽 상태 | 텍스트368 / 백지13 / 단색배경11 / 물리 쪽번호만4 = 396 |
| 미해결 빈 쪽 상태 | `sdk_empty`·`ocr_required`·`ocr_empty` 각각0 |
| 공식 옵션 제목 복구 | 13쪽 모두 v3에서 복구된 공식 SDK 문자열과 동일 |
| 본문 보존 표본 | native sample coverage 0.999, 전체 본문 미대표 쪽0; 전체 문자 정확도 점수가 아님 |
| 한글 경계 보완 | 내부 공백1,148개 수정, 인접 쪽30개 연결; 알려진 3분절은 대상 본문 잔여0 |
| 그림 | 186개 모두 SDK 300dpi crop, 이전150dpi 대비 가로·세로 약2배, 좌표 반올림 차이 최대2px; 원본 JPEG 재사용0 |
| 표 | 18개, 그중 공식 refine HTML 병합 보완1개 |
| 원본·기존 결과·소스 | 원본 PDF 및 보호 파일185개·주요 소스4개 SHA256/크기/mtime_ns 불변 |
| 문자범위·내보내기 | 블록·그림 offset 검증 PASS, 불필요 자산0, MD export는 그림 목적지 URL만 변경 |

책 후보는 `whole-396-v4-quality/bundle/내지-백반증 바이블-3교.md`이며 SHA256은 **`6878bed61a231e362e3819032a31ced0f658e8a21e44468e41f4d93c2b4dfe4f`**입니다. SDK 원본 문자열 해시와 상대 이미지 URL을 적용한 export 파일 해시는 구분합니다. 기존 Documents MD를 덮어쓴 결과가 아닙니다.

OCR 수행·SDK 옵션 복구·모델 공백 보정 안내 때문에 최종 문서 상태는 `partial`을 유지합니다. 빈 쪽 누락 상태0이나 알려진 3분절 해결을 모든 내용의 의미/철자 정확성 보증으로 해석하지 않습니다.

## 빈 쪽·OCR·이미지와 기존 결과 보호

- 이전 SDK OCR 미선택 16쪽은 기존 실제 렌더/geometry 영수증에서 글자·내장 이미지·기타 가시 벡터가 없는 백지 또는 단색 배경으로 확인했습니다. `sdk-quality-20261024/sdk2-background-verification.json`의 `ok=true`, `source_unchanged=true`를 근거로 하며, 이 판정을 모든 빈 OCR 결과에 일반화하지 않습니다.
- 문자 이미지에는 공식 OCR 재시도, 원본 이미지 보존과 300dpi 경로를 구현했습니다. 이번 책의 그림186개는 모두 SDK crop 경로이며 원본 JPEG 재사용0입니다. 원본 이미지 바이트 재사용은 합성 회귀에서만 입증했습니다. OCR 오인식 가능성과 원본 대조 안내는 유지합니다.
- 기존 결과 보호 목록은 **고유 185개 파일**입니다. 이전 v3의 `protected-originals-verification.json`을 기준으로 v4 시작 전/완료 후 SHA256·크기·mtime_ns를 대조했습니다. v4의 `protected-originals-and-source.json`에서 최종 `ok=true`, 185개 불변을 확인했습니다.
- v4의 주요 변환 모듈 4개도 시작/종료 SHA256·크기·mtime_ns가 동일합니다. 후보는 `pymupdf4llm-benchmark-20261023/whole-396-v4-quality` 아래에만 저장했습니다.

## 소스·모델·라이선스 기준

| 동결 소스 | SHA256 |
| --- | --- |
| `korean_boundaries.py` | `293b5abddb497c81f484c4ef11b0350573decd900581f1e73bce3ba325c64c0f` |
| `pdf_markdown_child.py` | `89f38e15fd9e1104c9483efbf8d75684c1c1e62dae31c97639761484ef453630` |
| `pdf_markdown.py` | `d8a34e7f37efc623970b1c61b20228c4fabc83cff24fbc7843fc79aab197622c` |
| `pdf_table_spans.py` | `54363a49f44811677087219f0cbf9529e0cc4f09039b35e21c85070a85d635f7` |
| `pdf_pipeline.py` | `b2d79a6720decb8d6c9850fd42331b59792d277b638cc9ccd68bc8ea5f7f666b` |

메인 1.28.2의 공식 wheel과 해당 PyMuPDF/MuPDF 정확 소스는 `deployment-source-audit-20261024/pymupdf-1.28.2-verified.json`에 기록했습니다. 그 영수증 자체는 런타임 회귀 결과가 아니며 실제 회귀 결과는 위 242개 로그입니다. SDK2.0은 별도 frozen worker로 분리합니다.

번들 `build-manifest.json`에는 SDK2.0, PyInstaller6.21.0, Kiwi0.24.1/모델0.24.0, 어댑터 소스 SHA, SDK ONNX17개와 Kiwi 파일9개의 SHA를 기록합니다. Kiwi 코드와 모델은 각각 Apache-2.0 LICENSE/NOTICE를 수집하며 모델 출처 고지를 코드 라이선스로 대신하지 않습니다. PyMuPDF AGPL 전문과 의존성 고지도 수집했습니다. 최종 메인 `dist/PDF Extractor/licenses/manifest.json`의 **32패키지**, worker `licenses/dependencies/manifest.json`의 **21패키지**, 실제 ONNX17개·Kiwi9개 파일 SHA와 고지 포함은 독립 검수 승인을 받았습니다. 이전 사전 probe의 worker16패키지 수를 최종 번들 수로 사용하지 않습니다.

## 배포 전 artifact 기록

배포 전 확정한 빌드 파일은 아래와 같습니다. SHA256은 실제 로컬 파일에서 계산했고 Setup의 인접 `PDF-Extractor-Setup.exe.sha256`과 일치했습니다. 이후 같은 bytes를 게시·설치한 결과는 맨 위 최신 절에 기록했습니다.

| artifact | 크기·SHA256 |
| --- | --- |
| `PDF-Extractor-Setup.exe` | 243,034,665 bytes / `7301324632eec9a0b3376ab7a371c7f467c4b636ea96cb999f964b8c61927add` |
| `dist/PDF Extractor/PDF Extractor.exe` | `68f2eba82a0189cddcc344248b85c054081c65e818cfb193dc372ef4f85401be` |
| `dist/PDF Extractor/_internal/engines/pdf-markdown/pdf-markdown-worker.exe` | `5c189361e2e5ab211f095aebd944eacafd283b47d1b64545b549db21009301cc` |

두 실제 EXE 영수증의 App SHA와 frozen 품질 영수증의 worker SHA가 이 파일들과 일치합니다. worker manifest는 위 동결 child·한글·표 모듈 SHA와 같은 소스를 가리킵니다.

| 후속 검증 | 확정 결과·영수증 |
| --- | --- |
| 전체 책 v4 시간·쪽 상태·본문·표·이미지·원본/185파일 불변 | 실행·독립 재해시 검수 PASS, 미해결 P1/P2 없음 |
| installer 크기·SHA256·인접 checksum 일치 | 실제 파일 계산·독립 검수 완료 |
| App/worker SHA·실제 번들 모델/고지 대조 | SHA 대조 및 모델/고지 독립 승인 |
| 실제 EXE scan·최종 기능 검증 | native/scan/frozen 품질 slice PASS, 위 영수증 참조 |
| 소스/tag/installer/checksum 공개와 게시 bytes 대조 | 이후 완료, 맨 위 공개 자산 영수증 참조 |
| 실제 설치 후 버전·업데이트·기능 확인 | 이후 설치본 기능·실제 GitHub 확인 PASS, 정상 창·실제 책 열기까지 확인; 맨 위 영수증3개 참조 |

의미·맞춤법·수식·모든 복잡 표의 완전 복구를 보장하지 않습니다. Kiwi의 두 API 합의는 의미 보존의 독립 증명이 아니므로 교정 provenance와 원본 대조 안내를 유지합니다. 새 입력의 모호한 경계·OCR 오인식·비지원 구조는 원문을 보존하고 확인이 필요한 상태로 남깁니다.
