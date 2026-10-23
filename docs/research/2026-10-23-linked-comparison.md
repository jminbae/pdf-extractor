# 읽기 전용 원본·Markdown 대조와 위치 연결

조사일: 2026-10-23 · 상태: 구현 전 기술노트

목표는 원본과 MD를 편집하지 않고 나란히 읽으며 **원본 단어 검색, 검색결과·문단·표 클릭의 양방향 이동, 스크롤 연동**을 제공하는 것입니다. 최신 요구는 [범위 문서](./2026-10-23-simplified-scope.md)를 따릅니다. 이 작업에서는 앱 코드 변경·설치·실문서 변환·성능 실험을 하지 않았습니다.

## 결론과 현재 앱에 맞는 우선안

**LLM 없이 구현할 수 있습니다. 추출 때 위치와 ID를 남기는 source map이 필요합니다.** 완성된 MD 문장을 원본에서 다시 검색하는 방식만으로는 반복 머리말·같은 숫자·합쳐진 문단·펼친 병합 표의 위치를 정확히 구별할 수 없습니다.

부모 작업의 현재 코드 검사에 따르면 원본은 **PyMuPDF로 만든 PNG를 `<img>`로 표시**하며 page index·크기·지연 로딩·확대·현재 쪽 감지가 있습니다. 반면 본문 정본의 page/bbox와 MD 렌더 ID가 빠지는 경로가 있고 일부 표에는 `pdf_span`이 있습니다. 이는 코드 구조 확인이며 실문서 재현 결과가 아닙니다.

따라서 **현재 PNG 미리보기를 유지하고 검색·클릭 위치를 별도 overlay로 얹는 방안부터 시험**합니다. 원본 파일에 주석을 쓰거나 MD를 편집하지 않습니다. PDF.js로의 전면 교체는 전제가 아닙니다.

## 공식 출처로 확인한 기반과 경계

| 기반 | 확인한 사실 | 보장하지 않는 것 |
| --- | --- | --- |
| PyMuPDF | words 결과에 word·bbox·block/line/word 번호가 있고 rawdict에 문자별 unicode·bbox가 있습니다. search 결과는 여러 rect/quad가 될 수 있습니다. | 같은 문구의 첫 hit가 우리 MD의 대응 위치라는 보장, 개정·엔진 변경을 넘는 ID 안정성 |
| PyMuPDF 좌표 | PDF/MuPDF 좌표 원점·y 방향이 다르며 transformation/rotation/derotation matrix를 제공합니다. | PDF bbox를 PNG/CSS pixel에 그대로 넣어도 맞는다는 보장 |
| Docling | DocItem의 self_ref·prov 목록, provenance의 page_no·bbox·charspan, TableCell의 optional bbox가 있습니다. | 모든 backend·셀의 문자 단위 위치 완전성, PDF.js/PyMuPDF offset과 동일한 범위 |
| rHWP v0.8.7 | render-tree의 page bbox와 원 문단·셀 주소, PageLayerTree의 source span·UTF-8/UTF-16 범위·optional stable_source_key가 있습니다. | CLI MD export가 같은 source map을 내보내는 계약, SVG DOM/PDF 구조 ID와 문서 node ID의 동일성 |

출처: [PyMuPDF TextPage](https://pymupdf.readthedocs.io/en/latest/textpage.html), [Page 좌표·검색 API](https://pymupdf.readthedocs.io/en/latest/page.html), [Docling 타입](https://docling-project.github.io/docling/reference/docling_document/), [rHWP render tree](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/renderer/render_tree.rs), [rHWP page layer](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/paint/layer_tree.rs).

PDF.js는 추후 선택할 수 있는 viewer 후보입니다. 공식 FindController는 페이지별 match 위치·길이와 정규화 역매핑을 관리하고 TextHighlighter는 text-layer DOM 범위에 강조를 연결합니다. **우리 MD와의 연결은 별도로 만들어야 하며**, PDF.js 검색 offset을 추출기 offset으로 그대로 쓰면 안 됩니다. [FindController](https://raw.githubusercontent.com/mozilla/pdf.js/master/web/pdf_find_controller.js), [Highlighter](https://raw.githubusercontent.com/mozilla/pdf.js/master/web/text_highlighter.js)

## 앱이 보존할 최소 source map — 제안

- **동일 자료:** source hash, 추출 revision, renderer/엔진·설정 버전. 오래된 map과 다른 판본을 섞지 않습니다.
- **원본 주소:** page/slide/sheet/section과 paragraph/span/table/cell ID·범위, bbox/quad 목록. 논리 주소가 없는 형식에는 가짜 주소를 만들지 않습니다.
- **좌표 계약:** 원점·단위·회전·crop, 원 page→렌더 image→CSS 표시 변환. 확대·HiDPI·회전·OCR 기울기 보정 뒤에도 역변환할 수 있어야 합니다.
- **MD 주소:** block/cell ID와 해당 MD/AST 범위·revision. export와 미리보기에서 ID가 사라지지 않게 합니다.
- **연결:** source 범위 집합↔MD 항목 집합을 다대다로 허용하고 연결 방법·정밀도·경고를 남깁니다. offset은 bytes/Unicode code point/UTF-16 중 어느 단위인지 명시합니다.

한 문단이 원본 두 쪽에 걸치거나, 원본 병합 머리글이 여러 MD 열에 반복되면 하나의 사각형·일대일 관계로 축소하지 않습니다. rHWP의 순번 node ID나 Docling pointer는 문서 revision과 묶어 사용합니다. 내부 map은 사용자가 매번 JSON을 선택해야 하는 화면을 의미하지 않습니다.

## 사용 흐름과 fallback — 제안

1. **원본 검색:** page별 occurrence·범위·좌표·짧은 문맥을 결과 목록으로 보여 줍니다. 같은 문구라도 occurrence를 구분합니다. 스캔은 OCR 보조 검색임을 표시하고 native text 검색과 섞어 숨기지 않습니다.
2. **검색결과 클릭:** 해당 원본 범위를 PNG 위에 강조하고 source map으로 MD를 이동합니다. MD 대응이 없으면 원본만 열고 대응 없음/근사를 표시합니다.
3. **MD 클릭:** block/표 ID로 원본 영역을 엽니다. 셀 bbox를 검증한 형식에만 셀 단위로 이동하고, 없으면 표·문단·쪽 단위로 낮춥니다.
4. **원본 클릭:** CSS 클릭점을 원 page 좌표로 돌려 region hit-test 후 MD ID를 찾습니다. 후보가 여러 개면 관련 후보를 표시하며 동일 문자열 첫 결과로 점프하지 않습니다.
5. **스크롤 연동:** 활성 pane의 mapped block을 anchor로 상대 pane을 이동합니다. 전체 scroll percentage를 같게 맞추지 않습니다. 연동 on/off, 프로그램 이동의 왕복 loop 차단, 최신 generation/revision 확인이 필요합니다.

머리말처럼 MD에서 숨긴 항목과 실제 미추출도 구분합니다. 연결 근거가 부족하면 가까운 검증 anchor나 쪽만 열고 `근사 위치`를 표시합니다. 모든 형식의 모든 문자를 100% 정밀하게 연결한다고 보장하지 않습니다.

연동 범위는 **앱 내부 읽기 전용 미리보기**입니다. 외부 Word·한글·PDF 앱의 현재 스크롤·선택까지 일반 파일 열기만으로 동기화할 수 있다고 약속하지 않습니다. HWP/Office 렌더는 비교용 snapshot이며 글꼴·쪽나눔이 원 앱과 동일하다는 보장도 없습니다.

## 장문과 검증 경계

현재 지연 로딩을 활용해 원본·MD 모두 현재 쪽/장 주변만 렌더하고 overlay·DOM·image cache를 제한합니다. 검색 index와 source map은 페이지별로 읽고 검색 진행 범위를 표시합니다. 문서 전환/빠른 연속 클릭에서는 이전 검색·렌더 응답이 새 화면을 움직이지 않게 합니다. 내부 검색 cache까지 포함한 메모리는 실제 책으로 측정해야 합니다.

구현 시험에는 반복 문구·숫자, 다단/회전/crop/확대, 쪽을 넘는 문단·표, 평탄화 병합 셀, OCR 오류·map 없음·다른 revision, 빠른 양방향 스크롤과 연속 클릭을 포함합니다. UI가 움직인다는 사실과 올바른 원본 영역에 연결됐다는 사실을 별도로 검증합니다. 이번 조사에는 설치·실행·100% 매핑 정확도·처리속도 측정 결과가 없습니다.
