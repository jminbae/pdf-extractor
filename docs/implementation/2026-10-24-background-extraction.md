# 추출 중 다른 문서 사용 정지 진단과 공통 PDF 경로

2026-10-24 기준 작업 기록입니다. 이 문서의 소스 시험·실행 관찰은 설치된 1.2.2의 수정 배포 완료를 뜻하지 않습니다.

## 원인과 실제 관측

- 설치된 앱 PID 28424에서 `/files`, `/document-map` 요청이 각각 5초 제한 시간에 응답하지 않았습니다. 화면의 `서지정보 조회 4%`는 이전 단계 표시가 남은 상태였습니다.
- 같은 PID의 py-spy 스택에서 추출 스레드가 `single.extract_one → GROBID TEI → captions.document_lines → page.get_text(DICT)`에서 `active+gil`인 것을 확인했습니다. 종료 직전 보존한 스택에서도 `single._extract → tablefill.fill_document → get_text(DICT)`가 `active+gil`이었습니다.
- 모든 PDF보다 논문용 서지·GROBID 처리가 먼저 실행됐고, 무거운 MuPDF 호출이 GUI·HTTP 서버와 같은 Python 프로세스의 스레드에서 실행됐습니다. 따라서 표시만 바꾸거나 타임아웃을 늘리는 것으로 원인을 해결할 수 없습니다.
- 정상 종료 요청도 8초 안에 완료되지 않아 해당 앱 PID만 강제 종료했습니다. 종료 전 기존 Markdown·canonical·UI/index 사본과 스택을 [background-freeze-1.2.3](<D:/DevCache/260725 PDF 추출/background-freeze-1.2.3>)에 보존했습니다. 사용자 원본 PDF는 수정하지 않았습니다.

PyMuPDF는 다중 스레드 사용을 지원하지 않으며 PDF 처리를 별도 프로세스로 나누는 공식 예제를 제공합니다. 이번 변경도 이 경계를 사용합니다. [공식 multiprocessing 문서](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)

## 적용한 수정 범위

- 새 추출·강제 재추출 PDF는 문서 의미를 책·논문으로 분류하지 않고 `pdf_pipeline.extract(source, *, cancel, progress)`의 공통 공식 SDK 경로로 시작합니다. DOI·PubMed·GROBID·논문 캡션 보정은 선행하지 않습니다.
- SDK 이후 기존 native 본문 보존 검사를 재사용합니다. 원문이 있는 전체 쪽이 누락되거나 기존 손실 기준을 위반하면 기존 native/OCR 추출로 보존합니다. SDK를 적용하지 못한 결과에는 진단과 동일 엔진 재시도 방지 표시를 남깁니다. 취소·원본 변경·자산 충돌은 일반 fallback 성공으로 삼키지 않습니다.
- 부모 App은 추출·저장 작업을 소유한 자식 프로세스에 맡깁니다. SDK·native 검사·저장·export가 부모 GUI 인터프리터를 점유하지 않으며 진행/결과만 전달합니다. 중지 시 자식 프로세스 트리를 정리합니다. 정상 캐시 결과는 유지합니다. 최초 QA는 단일 책 작업이며, 후속 사용자 지시의 문서별 독립 프로세스·작업 중 추가 큐는 App 담당의 별도 구현/검증 범위입니다.
- 페이지 처리와 `본문 보존 확인` 단계를 실제 진행 메시지에 연결합니다. 기존 캡션 텍스트 수집에는 공식 `TEXTFLAGS_DICT & ~TEXT_PRESERVE_IMAGES`를 사용해 필요 없는 이미지 바이트를 제외했습니다. 텍스트·bbox 동등성은 실제 합성 PDF로 확인했습니다. [공식 추출 flags 문서](https://pymupdf.readthedocs.io/en/latest/app1.html)
- 원본·Markdown·검색·목록의 추출 중 상호작용과 읽기 snapshot은 App/UI 담당 작업입니다. 실제 브라우저·frozen 검증은 아래 소스 시험과 구분합니다.
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
| 새 UI·frozen 실행·설치·배포 | 아직 이 기록에서 완료로 확인하지 않았습니다. |

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

## 남은 확인

1. 최신 동시 scheduler·자원 정책을 포함한 전체 회귀와 최종 frozen 앱의 추출 중 원본/결과/검색/목록·추가 큐·취소를 확인합니다.
2. 빌드·설치·공개 배포 상태를 별도로 기록합니다. 이 문서는 소스 QA를 새 설치·배포 완료로 표시하지 않습니다.
