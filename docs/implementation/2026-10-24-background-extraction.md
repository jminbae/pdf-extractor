# 추출 중 다른 문서 사용 정지 진단과 공통 PDF 경로

2026-10-24 기준 작업 기록입니다. 최종 1.2.3 후보의 시험·산출물 검증까지 기록하며, **아직 1.2.3 공개·설치 완료 상태가 아닙니다.** 아래 이전 후보와 전체 책 변환의 근거는 최종 후보의 지연 시험과 구분합니다.

## 원인과 실제 관측

- 설치된 앱 PID 28424에서 `/files`, `/document-map` 요청이 각각 5초 제한 시간에 응답하지 않았습니다. 화면의 `서지정보 조회 4%`는 이전 단계 표시가 남은 상태였습니다.
- 같은 PID의 py-spy 스택에서 추출 스레드가 `single.extract_one → GROBID TEI → captions.document_lines → page.get_text(DICT)`에서 `active+gil`인 것을 확인했습니다. 종료 직전 보존한 스택에서도 `single._extract → tablefill.fill_document → get_text(DICT)`가 `active+gil`이었습니다.
- 모든 PDF보다 논문용 서지·GROBID 처리가 먼저 실행됐고, 무거운 MuPDF 호출이 GUI·HTTP 서버와 같은 Python 프로세스의 스레드에서 실행됐습니다. 따라서 표시만 바꾸거나 타임아웃을 늘리는 것으로 원인을 해결할 수 없습니다.
- 정상 종료 요청도 8초 안에 완료되지 않아 해당 앱 PID만 강제 종료했습니다. 종료 전 기존 Markdown·canonical·UI/index 사본과 스택을 [background-freeze-1.2.3](<D:/DevCache/260725 PDF 추출/background-freeze-1.2.3>)에 보존했습니다. 사용자 원본 PDF는 수정하지 않았습니다.

PyMuPDF는 다중 스레드 사용을 지원하지 않으며 PDF 처리를 별도 프로세스로 나누는 공식 예제를 제공합니다. 이번 변경도 이 경계를 사용합니다. [공식 multiprocessing 문서](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)

## 적용한 수정 범위

- 새 추출·강제 재추출 PDF는 문서 의미를 책·논문으로 분류하지 않고 `pdf_pipeline.extract(source, *, cancel, progress)`의 공통 공식 SDK 경로로 시작합니다. DOI·PubMed·GROBID·논문 캡션 보정은 선행하지 않습니다.
- SDK 이후 기존 native 본문 보존 검사를 재사용합니다. 원문이 있는 전체 쪽이 누락되거나 기존 손실 기준을 위반하면 기존 native/OCR 추출로 보존합니다. SDK를 적용하지 못한 결과에는 진단과 동일 엔진 재시도 방지 표시를 남깁니다. 취소·원본 변경·자산 충돌은 일반 fallback 성공으로 삼키지 않습니다.
- 부모 App은 문서마다 별도의 추출·저장 프로세스를 실행하고 진행/결과만 전달합니다. 여러 문서를 동시에 실행하고 작업 중 추가 대기열을 받으며, 같은 내용 SHA와 export commit은 각각 직렬화합니다. 중지 시 소유한 자식 트리를 정리하고 정상 저장 결과는 유지합니다. 아래 440.109초 전체 책 실행은 이 최종 동시 스케줄러가 동결되기 전의 별도 품질 근거입니다.
- 페이지 처리와 `본문 보존 확인` 단계를 실제 진행 메시지에 연결합니다. 기존 캡션 텍스트 수집에는 공식 `TEXTFLAGS_DICT & ~TEXT_PRESERVE_IMAGES`를 사용해 필요 없는 이미지 바이트를 제외했습니다. 텍스트·bbox 동등성은 실제 합성 PDF로 확인했습니다. [공식 추출 flags 문서](https://pymupdf.readthedocs.io/en/latest/app1.html)
- 추출 중에도 원본·기존 저장 Markdown·검색·목록·새 파일 추가를 허용합니다. 재추출 전 저장된 MD는 계속 읽되, 열린 화면의 revision과 새 정본의 좌표를 섞지 않습니다. 새 문서는 실제 MD 저장 전까지 MD 열기를 허용하지 않습니다. 실제 브라우저·frozen 검증은 아래 소스 시험과 구분하며 모든 입력의 2초 응답을 보장하지 않습니다.
- `build().boot()`와 추출 진입부에는 GROBID 자동 기동·warmup 대기 호출이 없습니다. 남아 있는 `Grobid.ensure_async/wait` 정의가 공통 PDF 경로에서 실행되는 것으로 오해하지 않습니다. 논문 추가정보 작업을 공통 추출의 선행 대기로 복원하지 않습니다.

## 실제 책에서 발견한 보존 검사 오탐

첫 별도 저장소 실행에서는 SDK가 454.969초에 396쪽 처리를 마쳤으나 새 보존 검사가 정상 표가 있는 344–346쪽을 누락으로 오인했습니다. 원인은 Markdown의 `<br>` 등 표기 문자를 native 본문과 직접 비교한 것입니다. 이 실행은 native fallback으로 끝나 품질 QA에 실패했으며, [최초 receipt](<D:/DevCache/260725 PDF 추출/background-1.2.3-whole-book/receipt.json>)를 그대로 보존했습니다.

공식 MarkdownIt의 표 파싱과 lxml의 표시 텍스트를 사용해 두 비교 입력을 맞췄습니다. SDK Markdown·문자 순서·손실 판정 임계값은 변경하지 않았습니다. 실제 표 세 쪽의 probe는 각각 1.0으로 회복됐고 전체 쪽 누락 오탐이 없어졌습니다. 합성 실제 PDF의 pipe 표·병합 HTML 표를 RED→GREEN으로 확인했으며, 동일 본문 두 쪽을 한 번만 남기는 손실은 계속 거절하는 회귀도 추가했습니다. 기존 정본을 재검사한 [guard-fix-proof](<D:/DevCache/260725 PDF 추출/background-1.2.3-whole-book/guard-fix-proof.json>)와 이후 실제 SDK 재실행은 별도 근거입니다.

## 동시에 같은 이름을 저장하는 경계

같은 stem의 서로 다른 원본 두 개를 독립 프로세스에서 동시에 저장하면 파일명 결정이 경합해 한쪽이 실패하는 RED를 재현했습니다. 출력 폴더의 표준 OS 파일 잠금(`msvcrt.locking`/`fcntl.flock`)으로 파일명 결정부터 pending journal·Markdown·manifest commit까지 직렬화했습니다. 추출·SDK 처리는 이 잠금 밖에서 실행합니다. 잠금을 얻지 못하거나 60초를 넘으면 `ExportError`로 중단하며 무잠금 저장으로 전환하지 않습니다.

Windows에서 실제 두 프로세스의 두 결과·각 본문·원본 불변, 잠금 보유 프로세스 종료 후 OS 잠금 해제·다음 저장, 잠금 파일의 폴더 밖 링크 거절을 시험했습니다. 기존 pending 복구·외부 편집 보호와 `canonical_content_revision` 동작은 유지합니다. 이 수정은 아래 책 실행 도중 병렬로 추가됐으므로 최종 동시 저장 기능의 증거는 별도 회귀시험·frozen 검증으로 구분합니다. 잠금 API의 비차단 실패 계약은 [Python msvcrt](https://docs.python.org/3.12/library/msvcrt.html)·[fcntl 공식 문서](https://docs.python.org/3.12/library/fcntl.html)에서도 확인했습니다. POSIX 분기의 실제 실행 검증은 이 Windows 시험에 포함되지 않습니다.

## 현재 검증 상태

| 범위 | 확인 결과 |
| --- | --- |
| 공통 PDF entry·캡션·App export·기존 native/OCR 회귀 | 64개 PASS, 2.679초. SDK 우선, 실패 보존, 회전 쪽 손실 방지, 빈 native baseline, 취소/원본 변경/충돌, 표 표시 텍스트 비교, 이미지 payload 제외를 포함합니다. |
| App export 기존 시험의 새 entry 이행 | 기존 실패 격리·자동 저장·캐시 보존 의미를 유지했습니다. 현재 64개 회귀에 App export 9개·PDF pipeline 22개가 포함됩니다. |
| 동시 export·기존 저장 경계 | 25개 PASS, 3.222초. 실제 독립 프로세스·OS 잠금 시험을 포함합니다. |
| 소유 수정의 최종 통합 회귀 | 위 공통 PDF·App export·native/OCR·저장·동시 export를 함께 실행해 89개 PASS, 9.939초. [최종 로그](<D:/DevCache/260725 PDF 추출/background-1.2.3-whole-book-final/owned-regressions-final.log>)에 보존했습니다. |
| 실제 396쪽 SDK-first 전체 실행 | 440.109초, SDK 본문·보존 검사·저장 완료. 본문 품질과 부모 API 결과는 아래에 기록합니다. App/최종 scheduler/export 전체 동결 검증으로 확대하지 않습니다. |
| 독립 코드 검수 | 보존 검사 수정 후 관련 64개 독립 PASS, 2.430초. export 잠금은 신규 3개·기존 export 22개를 포함한 관련 68개 독립 PASS, 10.285초. 두 소유 범위 모두 미해결 P1/P2 없음으로 승인받았습니다. |
| 실제 변환 영수증 독립 검수 | Markdown·manifest/canonical의 그림 186개 SHA·원시 SDK MD 동등·18표·보호 3파일의 현재 SHA/크기/mtime를 독립 재확인했습니다. 변환 통과/전체 소스 불변 실패/최종 릴리스 미검증의 구분을 포함해 미해결 P1/P2 없음으로 승인받았습니다. |
| 이전 frozen 후보의 추출 중 상호작용 | 아래 `a0419c…` 후보의 읽기/검색·CSV·취소 관찰입니다. 최종 `7a634a…` 후보는 아래 별도 표에 기록하며, 최신 hidden 지연 시험의 raw 실패를 이 결과로 덮지 않습니다. |

실제 전체 QA 하네스와 전후 SHA, 페이지 진행, 원시 SDK 정본은 [background-1.2.3-whole-book-final](<D:/DevCache/260725 PDF 추출/background-1.2.3-whole-book-final>)에 기록했습니다. 원본 PDF·실제 Documents Markdown·실제 AppData canonical은 SHA·크기·mtime 전후 동일합니다. 새 결과는 별도 DevCache STORE/OUTPUT에만 저장했습니다.

## 실제 전체 실행 결과와 검증 범위

- 실제 `App.start(force=True)`가 자식 PID 16592를 실행했습니다. 396쪽·1,934 SDK 블록·18표·186그림이 저장됐고 결과 HTML에도 표 18개·그림 186개가 있습니다. 지정된 본문 분절 세 종류는 0개이며 native 전체 쪽 누락은 0개입니다. SDK의 기존 `partial` 상태를 성공한 완전 인식으로 바꾸지 않았습니다.
- Markdown SHA256은 `6878bed61a231e362e3819032a31ced0f658e8a21e44468e41f4d93c2b4dfe4f`로 보호한 실제 Documents의 검증된 책 결과와 같습니다. 별도 저장한 원시 SDK Markdown과 canonical의 SDK Markdown도 같습니다.
- 표시 텍스트를 사용하는 새 native 표본 coverage는 0.956입니다. 이는 형식·목록 표기 차이를 포함하는 손실 감지 휴리스틱이며 의미 정확도·완전성 비율이 아닙니다. 이전 다른 비교 입력으로 계산한 0.999와 동일 지표로 비교하지 않습니다.
- 실행 중 App의 scheduler/읽기 기능은 병렬 수정됐습니다. 따라서 [raw receipt](<D:/DevCache/260725 PDF 추출/background-1.2.3-whole-book-final/receipt.json>)의 `ok=false`·전체 App 파일 불변 실패를 그대로 유지했습니다. 런타임 품질 assertion 오류는 없고, 추출 코어 네 모듈은 전후 SHA가 같습니다. [conversion-verification](<D:/DevCache/260725 PDF 추출/background-1.2.3-whole-book-final/conversion-verification.json>)은 이 조건의 본문 변환·보호 자료·부모 API 검사만 별도로 통과했음을 기록합니다.
- `_work` 함수의 줄 위치를 제외한 코드 SHA는 리소스 관측 시작~끝 동일합니다. 실행 최초 loaded App/export 코드 snapshot은 수집하지 않았으므로 이를 사후 동결 증거로 만들지 않았습니다. 최종 scheduler·export·frozen 릴리스 검증은 별도입니다.

부모 프로세스에서는 작은 합성 PDF 원본·검색, 별도 저장 CSV 결과, 목록을 각각 146회 직접 호출했고 오류는 0개입니다. 브라우저 클릭 증거나 지연 상한 보장이 아닙니다.

| 직접 호출 | 평균 | 관측 최대 |
| --- | ---: | ---: |
| 목록 | 0.787ms | 19.225ms |
| 다른 작은 PDF 원본 정보 | 1.463ms | 15.507ms |
| 다른 작은 PDF 원본 검색 | 5.060ms | 23.466ms |
| 다른 저장 Markdown 결과 | 2.267ms | 11.386ms |

## 자원 사용 관측

[resource-summary](<D:/DevCache/260725 PDF 추출/background-1.2.3-whole-book-final/resource-summary.json>)는 소유 추출 자식·SDK·OCR 프로세스 트리만 5초 간격, 55표본/약 283.8초 관찰한 결과입니다. 전체 440초 중 시작 약 156초는 관측하지 않았습니다. 24 logical CPU에서 전체 기기 기준 정규화한 해당 트리 CPU는 평균 41.09%, 표본 최대 94.92%였습니다. 관측 private 합계 최대는 2,020,597,760바이트(약 1.88GiB), RSS 합계 최대는 1,188,757,504바이트(약 1.11GiB)였습니다.

이 값은 이 책 한 건의 관측이며 모든 PDF의 보통 사용량이 아닙니다. RSS 합계에는 공유 페이지가 중복될 수 있고 5초 표본 최대는 정확한 순간/lifetime 최대가 아닙니다. 병렬 빌드 등이 포함된 시스템 전체 CPU와 이 추출 트리 CPU도 구분해 저장했습니다. 동시 문서 수·자원 admission 정책은 별도 scheduler 담당 검증 범위입니다.

## 실제 frozen 앱: API 응답과 외부 CDP 지연의 분리

설치·배포 전 후보 EXE `a0419c6779c8eeebb36a46ee1fb23a4402314c20ce82f117c65708e94baf0eb8`를 숨김 WebView2와 별도 TEMP 입력/저장소로 실행했습니다. 최초 [실패 영수증](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-parallel-book/receipt.json>)은 상태 왕복 2초 초과·책 실행 상태·기존 MD 준비 상태를 한 오류로 묶어 원인을 확정할 수 없었습니다. 후속 진단에서 책은 `running`, 기존 MD는 준비된 상태였고 외부 왕복 3.84초만 실패했습니다. 이 실패 기록은 삭제하거나 PASS로 바꾸지 않았습니다.

동일 후보를 사용한 [분리 계측 영수증](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-book-profile-default-v2/receipt.json>)에서 브라우저 내부 `performance.now()`로 실제 API 대기 시간을 측정하고, Node 외부 HTTP·브라우저 HTTP·CDP 명령 왕복을 각각 기록했습니다.

| 측정 대상 | 실제 관측 |
| --- | --- |
| 브라우저 내부 상태 API 4회 | 2.7 / 3.4 / 18.5 / 3.0ms |
| Node 외부 `/files` HTTP 4회 | 2–4ms |
| 브라우저 내부 `/files` HTTP 4회 | 3.9–9.3ms |
| 브라우저 내부 원본 검색 API | 145.7ms, 성공·12개 결과 |
| 외부 CDP 별도 명령 | ping 최대 4,030ms, 검색 명령 전체 왕복 5,443ms |

2초 기준은 실제 브라우저 내부 API 대기와 HTTP 요청/응답에 그대로 적용했습니다. 외부 디버그 명령이 브라우저에서 실행되기까지의 추가 지연을 같은 API 처리 시간으로 간주하지 않았습니다. 이는 **모든 사용자 입력부터 화면 표시까지 2초 이내**라는 검증이 아닙니다. 숨김 WebView2/CDP 왕복에서 지연이 관찰됐다는 사실과 실제 요청 처리의 짧은 응답을 함께 보존합니다. 이 차이의 WebView2 내부 원인까지 확정한 것은 아닙니다.

책이 실행 중인 동안 다른 문서 원본/MD·검색·파일 추가 컨트롤이 사용 가능했고, 추가한 CSV는 두 번째 독립 자식 PID에서 저장을 마쳤습니다. 중복 책 요청은 새 작업으로 추가되지 않았습니다. 취소는 430ms에 완료되어 책은 `paused`, 미완료 책 MD는 공개되지 않았고 기존 MD·검색어·완료 CSV는 유지됐습니다. 사용자 원본 책과 모든 합성 입력의 SHA는 전후 같고 소유 Job·TEMP 정리도 확인했습니다. 숨김 창의 선택적 스크린샷 성공을 이 결과의 근거로 주장하지 않습니다.

책 단계의 소유 앱 PID 39520만 대상으로 py-spy 15초/20Hz/`--idle`을 실행해 2,318표본·오류 0개를 수집했습니다. [프로파일 요약](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-book-profile-default-v2/profile-summary.json>)에서 부모의 무거운 MuPDF/GROBID/캡션/표 복원 프레임은 0개, 자원 수집 프레임은 5개, WebView script 대기 프레임은 93개였습니다. 시작/종료 스택의 GUI·서버·작업 monitor는 대기 상태였습니다. 이 수치는 모든 스레드의 idle 포함 wall-clock 표본이며 CPU 사용률·GIL 소유율이 아닙니다. 관측 구간에서 기존 GIL 독점 문제가 재현됐다는 근거는 없습니다.

이후 적용한 표준 `BELOW_NORMAL_PRIORITY_CLASS`는 별도의 백그라운드 작업 정책입니다. 위 디버그 지연의 원인이나 개선 효과로 단정하지 않습니다. 아래 최종 후보에서도 브라우저 측 지연 시험은 실패했으므로 우선순위 관측 PASS와 분리합니다.

## 최종 1.2.3 후보 검증 당시 — 공개·설치 전

전체 회귀는 **285개 PASS, 83.407초**입니다. [최종 시험 로그](<D:/DevCache/260725 PDF 추출/test-1.2.3-priority-final.log>)와 [빌드 로그](<D:/DevCache/260725 PDF 추출/build-1.2.3-priority-final.log>)를 기준으로 합니다. 이전 284개/93.332초·`a0419c…` 후보의 승인 기록은 보존된 이전 단계이며 최신 시험 수·산출물로 사용하지 않습니다.

| 대상 | 최종 후보 실측 |
| --- | --- |
| Setup | 243,151,591바이트 · SHA256 `e43b9ecfd5747ac451b685728e4ba231342c5dc740be2a921b2dad4e65e67f33`, 인접 checksum 일치 |
| 메인 EXE | 11,821,811바이트 · SHA256 `7a634a70e994ac4506b5bc5114d7418ca16a5330d35f49bdb33a80ae301455ac` |
| PDF SDK 워커 | 10,817,172바이트 · SHA256 `89865a4d08783e34ca6ad53731faf6c7b4641f30240f6725c1a82df60e01105e` |
| 독립 산출물 감사 | 메인 44모듈·워커 4소스의 실제 코드와 현재 소스 동등, 고지 33/21패키지·psutil 7.2.2 BSD-3-Clause, ONNX 17개·Kiwi 9개 hash 일치. [최종 감사](<D:/DevCache/260725 PDF 추출/build-1.2.3-priority-artifacts-verification.json>) PASS. 이전 candidate 감사 파일은 보존 |
| 실제 frozen scan | [최종 scan receipt](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-priority-scan/receipt.json>) `ok=true`, 원본 불변·소유 TEMP 정리 확인 |
| 실제 우선순위 | 실제 책 작업 GUI PID23588 `NORMAL=32`, 문서 PID38080·직계 SDK PID10056 `BELOW_NORMAL=16384`를 각각 7회 관측. [읽기 전용 관측](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-priority-book/priority-verification.json>) PASS |
| 최신 hidden 책 지연 시험 | [raw receipt](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-priority-book/receipt.json>) `ok=false` 유지. 브라우저 내부 `/files` fetch 완료 측정 1회가 4,175.7ms로 기존 2,000ms 기준을 초과 |
| 진단 환경 flag A/B | [진단 receipt](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-priority-book-foreground/receipt.json>) `ok=false`, `foreground_test.applied=false`. 요청한 flag의 실제 적용이 확인되지 않아 유효한 A/B·개선 근거가 아님 |

최신 hidden 책 시험의 상태 bridge 4회는 2.0/3.2/23.2/3.8ms, Node `/files` HTTP는 3/2/2/38ms였습니다. 브라우저 내부 HTTP는 3.1/4.4/**4,175.7**/5.4ms였으며 이 실패를 외부 CDP 왕복 지연만으로 재분류하지 않습니다. 같은 round의 bridge·Node HTTP가 짧았다는 관측만으로 브라우저 지연의 원인을 확정하지 않습니다. 원본 검색 111.1ms·12결과, 기존 MD 읽기/검색어 보존, 추가 CSV 독립 저장, 중복 요청 0건 추가, 취소 321ms 및 원본 불변/TEMP 정리는 확인했지만 전체 지연 gate는 실패입니다.

최종 자원 정책은 이 PC의 물리16/논리24 CPU에서 동시 8개(절대 상한 10)이며 신규 문서를 순차적으로 시작해 자원을 재관찰합니다. RAM 50% 예산, 시스템 예비 15%·최소 2GiB, CPU 90% 이상 대기를 사용합니다. 작은 PC의 단일 작업 예외도 예상 잔여 512MiB를 요구합니다. 이는 **새 작업 시작에 쓰는 메모리 추정·관측 기준이며 실행 중 프로세스의 강제 메모리/CPU 상한이 아닙니다.** 이미 실행 중인 작업을 자원 정책이 강제 종료하지 않습니다.

## 최종 대조와 배포

### 최종 명시적 포커스·가시성 조건 대조

같은 최종 `7a634a…` EXE의 [최종 영수증](<D:/DevCache/260725 PDF 추출/frozen-1.2.3-book-focus-emulation-final/receipt.json>)은 `ok=true`입니다. 시험 전용 공식 [CDP focus emulation](https://chromedevtools.github.io/devtools-protocol/tot/Emulation/#method-setFocusEmulationEnabled)을 사용했습니다. 적용 전 `hasFocus=false/visibility=hidden`, 적용 후·4회 측정·검색·취소에서 `hasFocus=true/visibility=visible/hidden=false`, 해제 성공을 모두 확인했습니다. 제품 코드·설정·실행 파일은 바꾸지 않았습니다.

4회 bridge3.6/3.6/11.0/2.6ms, Node HTTP4/2/2/1ms, 브라우저 HTTP5.0/86.2/3.8/2.8ms로 2초 기준을 그대로 통과했습니다. 처리쪽 관측은0/0/0/28이며, 초기 SDK 진입을 완료쪽으로 세지 않습니다. 책과 추가 CSV는 서로 다른 PID30328/36996에서 실행됐고 CSV가 먼저 저장됐습니다. 검색108.6ms·12건, 중지463ms에31쪽 paused, 미완료 책 MD 비공개·기존 MD/검색어 유지·원본 SHA/TEMP 정리를 확인했습니다.

이는 포커스·가시성 에뮬레이션 조건의 기능/응답 시험입니다. 기본 hidden 지연의 원인 확정, 실제 보이는 창의 모든 입력부터 paint까지 2초 보장을 뜻하지 않습니다. 이전 기본 hidden false와 적용되지 않은 플래그 false는 보존합니다. 최종 배포 결과는 [1.2.3 배포 검증](./2026-10-24-release-1.2.3.md)에 별도로 기록합니다.

기본 hidden 지연의 raw 실패와 적용되지 않은 A/B는 관찰로 보존합니다. 소스 시험·scan·우선순위·활성 페이지 API시험만으로 모든 GUI 입력 지연이 해결됐다고 쓰지 않습니다. 이후 **[v1.2.3 공개](https://github.com/jminbae/pdf-extractor/releases/tag/v1.2.3)·게시 자산 재다운로드·기존 HKCU 설치 갱신·실제 설치본 native/업데이트/기존396쪽 책 열기를 완료했습니다.** tag/source는 `e7f6eaabe07f159efffaa9f85fc83576df2e7495`입니다. 설치main/worker SHA일치, 사용자191개/백업193개 검증, 정상앱 PID3732의 책 MD/그림186/표18/가짜Page0과 원본 불변을 확인했습니다. [최종 배포 검증](./2026-10-24-release-1.2.3.md)과 [HANDOFF 확인란](./HANDOFF.md)이 후보 당시 상태보다 우선합니다.
