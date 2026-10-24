# 1.2.4 간결한 목록과 문서 보기

## 진행 상태

구현·실제 UI·실행 파일 검증을 마쳤습니다. 이 절은 공개·설치 전 상태이며 이후 실제 완료 근거를 덧붙입니다. 제품 코드 checkpoint는 `981de5ebbe2d971d64e5337a85e89de393fb2651`입니다. 이전1.2.3 설치·복구 근거는 `2026-10-24-release-1.2.3.md`에 보존합니다.

## 사용자 요구와 적용 범위

- MD 보기에서 Ctrl+휠로 내용만 확대·축소하고 현재 배율을 표시합니다. 배율 버튼을 누르면100%로 돌아갑니다. 문서 DOM·검색어·위치 연결은 다시 만들지 않습니다.
- 원본·MD·비교 보기의 우상단 X는 보기 화면을 닫고 목록으로 돌아갑니다. 실행 중 추출을 중지하는 기능과 분리합니다.
- 문서 파일명과 검색을 같은 상단 행에 배치하고 긴 전체 경로는 기본 화면에서 빼냅니다.
- 자동 저장 위치·입력 폴더·개수 줄, 전체 완료/대기 합계·메모리/CPU·엔진 준비 줄, 중복된 전체 진행 줄과 정상 안내를 제거합니다.
- 문서별 상태·게이지·MD 준비 여부는 파일 목록에 유지합니다. 저장 위치는 설정에서, 원본/출력 경로·상세 경고는 필요할 때 여는 파일 정보에서 확인합니다. 실패·미리보기 오류·오래된 위치맵 문제는 숨기지 않습니다.
- 기존 Windows 도구창의 흰색/회색, 표 형식 목록과 버튼 체계를 유지합니다. 추출 엔진·메모리 진입 정책·자동 저장 형식은 변경하지 않습니다.
- 사용자 추가 요청으로 라이트/다크 전환을 함께 제공합니다. 선택값은 native `settings.json`에 저장해 앱 재시작 후 복원합니다. 브라우저 private profile의 localStorage를 제품 설정 저장소로 사용하지 않습니다. 문서 원본·이미지의 색은 유지하며 Windows11 제목 표시줄도 기존 DWM helper로 테마에 맞춥니다.

## 공개 표준

MD의 로컬 확대는 브라우저 표준 [wheel event](https://developer.mozilla.org/en-US/docs/Web/API/Element/wheel_event)와 [CSS zoom](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/zoom)을 사용합니다. Ctrl+휠 기본 확대를 MD 영역에서만 막고, 일반 휠 스크롤과 원본 영역은 보존합니다. 실제 WebView2에서는 CDP의 trusted mouseWheel 입력으로 배율·원본/툴바 불변을 검증합니다.

Windows 제목 표시줄은 공식 [DWMWINDOWATTRIBUTE](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)의20/34/35/36 속성을 기존 함수에서 사용합니다. 지원하지 않는 Windows 버전에서는 기본 창틀을 유지합니다.

## 검증 계획과 초기 근거

- 실제 UI를 그대로 사용하는 TEMP 합성 fixture에서 zoom 행동 검증을 먼저 추가했습니다. 기존 UI는 `UI_MINIMAL_ZOOM_RED`, 확대값1·배율 표시 없음으로 실패하여 기능 부재를 확인했습니다.
- MD 확대/100% 복귀, 검색/문서 DOM/현재 읽던 위치, 비교의 원본 좌표 연결, 일반 휠/원본 영역, 보기 모드별 X와 작업 지속을 검증합니다.
- 기존 동시 실행/큐/리소스 이벤트 UI 검증은 제거된 표시 문구만 정정하고, 개별 진행률·행 DOM·검색/스크롤·오래된 정본 거부·읽기 전용·오류 감지를 유지합니다.
- 새 실행 파일의 PDF/CSV/OCR 저장·읽기·대조와 실제 확대/닫기, 공개 파일 hash·설치본·기존 사용자 자료 보존을 확인한 뒤 완료 상태로 갱신합니다.

## 최종 후보 검증

증거 경로는 `D:\DevCache\260725 PDF 추출`입니다.

| 대상 | 실제 결과 |
| --- | --- |
| 전체 회귀 | `test-1.2.4-final.log`, **289개 PASS / 88.980초**. 테마 restart·다른 설정 보존·invalid 입력·쓰기 실패4개를 포함 |
| 실제 UI 회귀 | `ui-minimal-1.2.4/regression-final-receipt.json`, minimal/reader/queue/resources4종 PASS 및 별도 브라우저 독립 재실행 PASS |
| MD 확대 | 실제 Ctrl+휠110%·DOM동일·앵커 차이 약0.92px, 확대 상태 양방향 위치 연결·100%복귀·50–250% 상한 확인 |
| 닫기 | MD/원본/대조3모드의 X가 목록으로 복귀하며 진행 상태 유지·취소 호출0 |
| 테마·상세 정보 | dark 실제저장호출/reload복원·쓰기 실패 원상태, 경고 원문2건/파일정보 X·ESC/포커스 복귀 확인 |
| 두 테마 화면 | `ui-minimal-1.2.4/final-shots/visual-final-receipt.json`, 1336/375px 라이트·다크20화면. 가로 넘침0·원본 흰종이/필터없음·체크박스14px |
| 실제 EXE | `frozen-1.2.4-book/receipt.json`과 `frozen-1.2.4-scan/receipt.json`, 모두`ok=true`/실행파일 SHA동일. native 저장·원본/MD 대조·실제 OCR provenance 유지 |
| 실제 EXE 확대·테마 | trusted CDP wheel 이벤트와 defaultPrevented,110%/내용·body폭불변/100%복귀, X/재열기·footer/savebar비노출, native theme저장·실제WebView reload복원·원본 필터없음 확인 |
| 실제 책 동시 실행 | 포커스/가시성 에뮬레이션 조건의 SDK 진행0/0/0/28쪽 관측, 상태 bridge최대6.3ms/브라우저HTTP최대16.5ms·기존2초기준 PASS, 추가CSV 독립저장·중지456ms·원본/TEMP보존 |
| 산출물 감사 | `build-1.2.4-artifacts-verification.json ok=true`, main44/worker4 소스·내장코드·고지33/21패키지·ONNX17/Kiwi9·입력275개 불변 확인 |

소스/UI SHA256은 `c7a1fa5a18e715fa0851144040dbeaec011dc9e0644177c4dd2dc5ee789bc8f1`입니다. 브라우저 검증은 실제 UI에 합성 native 응답을 연결한 것이며, 실제 EXE 검증은 별도 영수증입니다. 실제 책 시험은 동시 실행·취소를 위한 부분 실행으로396쪽 완주나 모든 입력→paint 성능 보장을 뜻하지 않습니다. 추출 코어 알고리즘은 이번 버전에서 바꾸지 않았습니다.

| 최종 후보 | bytes | SHA256 |
| --- | ---: | --- |
| Setup | 243140721 | `e989c2f995702bdf3f049e5206daf45ee63a75f6fe3c5bcd6246db51ab20b0cd` |
| main EXE | 11822697 | `1daca99e6bfabc48a845a7e44b03152d39b522d03d4499538950694acfb4c6bf` |
| SDK worker | 10817172 | `3ca3f9504f64c9f25888112b9fffbfcb7d25cc0ea79451c6b7910f7d6ce5ef5b` |

기존 프로그램·사용자 자료193개는 `deployment-backup-1.2.4/receipt.json`으로 SHA 대조 백업했습니다. 원본 PDF는 수정하지 않았으며,1.2.3 rollback Setup도 보존합니다. 새 버전의 공개·재다운로드·동일 HKCU 설치와 설치본 확인은 다음 단계입니다.
