# PDF·스캔·표·수식 추출 엔진 조사

- 조사일: 2026-10-23. 공식 문서, 개발사 모델 카드, 공식 저장소만 확인했습니다.
- 범위: 엔진 선정 전 조사입니다. 설치, 실제 PDF 변환, 사용자 문서 업로드, 속도·정확도 실험은 하지 않았습니다.
- 해석: 아래 `검증 사실`은 문서가 해당 기능·제약을 명시한다는 뜻입니다. 이 PC나 실제 업무 문서에서 작동했다는 뜻은 아닙니다. `권고`는 이를 토대로 한 설계 제안입니다.
- 버전 경계: `main`, `master`, `latest`는 이동합니다. 문서의 현재 내용과 과거 릴리스의 설명을 섞지 말고, 도입 전에 패키지 버전·소스 commit·모델 revision·LICENSE·의존성 lock을 함께 확정해야 합니다.

## 1. 결정에 필요한 결론

**권고:** 기존 JSON 정본·Markdown 뷰·오프라인 우선·틀린 것보다 없는 것 원칙을 유지하면서, 범용 DocumentIR에 엔진별 adapter를 연결하는 구성이 적합합니다. 1차 시험 후보는 Docling 표준 PDF 파이프라인, 독립 비교 후보는 PP-StructureV3입니다. 텍스트 PDF의 표 실패 영역에는 pdfplumber/Camelot의 원문 문자 기반 재추출을 비교합니다. GROBID는 논문 메타데이터·인용·TEI adapter로 보존합니다. MinerU와 Marker는 별도 런타임·모델 라이선스 확인 후 비교 후보로 둡니다. 이것은 성능 순위나 채택 확정이 아닙니다.

표의 병합 셀은 단순 pipe Markdown으로 평탄화하지 않습니다. `rowspan`, `colspan`, 원 셀·원문·페이지·좌표를 JSON에 보존하고, Markdown 안에는 HTML table을 넣거나 실패 시 원본 표 crop을 표시하는 방향을 권합니다. Azure 공식 Markdown 형식도 병합 셀을 위해 HTML table을 사용합니다. [Azure Markdown 요소](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/markdown-elements?view=doc-intel-4.0.0)

## 2. 엔진별 검증 사실과 미검증 경계

### Docling

**검증 사실**

- 문서 구조·reading order·표·수식·OCR를 다루며, `DoclingDocument`는 text/table/picture, 문서 계층, furniture 구분, 가능한 항목의 bbox와 provenance를 표현합니다. JSON은 내부 구조 보존용이며 Markdown은 직렬화 결과입니다. [공식 README](https://github.com/docling-project/docling), [문서 모델](https://docling-project.github.io/docling/concepts/docling_document/)
- Windows/macOS/Linux, x86_64/arm64 설치를 문서화합니다. 모델 실행은 PyTorch에 의존하고 CPU 전용 또는 가속기용 배포판 선택이 필요합니다. [설치](https://docling-project.github.io/docling/getting_started/installation/)
- 모델·OCR 자산을 미리 마련하고 `artifacts_path`를 지정하면 격리된 오프라인 실행이 가능합니다. 외부 서비스를 쓰는 경로는 별도 opt-in입니다. [FAQ](https://docling-project.github.io/docling/faq/), [고급 옵션](https://docling-project.github.io/docling/usage/advanced_options/)
- 표 모델은 페이지 layout의 table 영역과 table structure를 구분합니다. `do_cell_matching=False`는 PDF 셀 연결 때문에 여러 열이 잘못 합쳐질 때 시도할 공식 옵션이며, TableFormer `ACCURATE`/`FAST` 모드가 있습니다. 이는 정확성을 보장하는 스위치가 아닙니다. [고급 옵션](https://docling-project.github.io/docling/usage/advanced_options/), [공식 모델 카드](https://huggingface.co/docling-project/docling-models)
- 신뢰도 문서의 `layout_score`, `ocr_score`, `parse_score`와 달리 **`table_score`는 아직 구현되지 않았다고 명시**합니다. 숫자 0~1이 셀 정답 확률이라고 해석할 근거는 없습니다. [신뢰도](https://docling-project.github.io/docling/concepts/confidence_scores/)
- 코드 MIT와 모델 라이선스는 별개입니다. 공식 `docling-models` 카드에는 Apache-2.0/CDLA-Permissive-2.0 표시가 있으며, 선택 OCR·수식·VLM 모델은 각각 확인해야 합니다. [코드 라이선스 안내](https://github.com/docling-project/docling#license), [모델 카드](https://huggingface.co/docling-project/docling-models)
- 수식 인식은 별도 enrichment 설정으로 활성화하는 예제가 있고, 그림은 추출 이미지를 별도로 저장할 수 있습니다. 그림을 보존하는 것과 그림 내용을 수치·설명으로 재생성하는 것은 별도 단계입니다. [수식 예제](https://docling-project.github.io/docling/_generated/examples/code_formula_granite_docling/), [그림 내보내기](https://docling-project.github.io/docling/_generated/examples/export_figures/)

**미검증/도입 전 확인:** 이 PC의 CPU/GPU별 RAM·VRAM 최소치, 한글·의학 표·작은 각주·회전/중첩 표·페이지 경계 표의 성공률, 자동 연결의 오탐률, 취소/timeout 동작은 측정하지 않았습니다. PDF의 굵게/밑줄 등 텍스트 스타일 보존은 FAQ에서 아직 지원되지 않는 경계로 설명합니다. 문서에 있는 기능 목록을 시각적 완전 복원 보장으로 해석하지 않습니다. [FAQ](https://docling-project.github.io/docling/faq/)

### PaddleOCR / PP-StructureV3

**검증 사실**

- layout, 일반 OCR, 표, 수식 등 모듈이 구분돼 있고, 다단 reading order와 Markdown 출력을 제공합니다. 출력에는 page index, block bbox/label/content, reading-order 목록, 표 HTML, 셀 bbox, OCR 문자열·인식 score가 포함됩니다. 기본 인식 모델의 언어 조건을 확인하고 한국어용 모델을 명시적으로 선택해야 합니다. [PP-StructureV3](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-StructureV3.html)
- 표 전용 v2 pipeline 예제는 `pred_html`에 rowspan/colspan을 포함하며 셀 box와 OCR 결과를 별개로 제공합니다. 이 표현력이 모든 병합 구조를 올바르게 예측한다는 뜻은 아닙니다. [표 인식 v2](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/table_recognition_v2.html)
- 현재 설치 문서는 `paddleocr[doc-parser]`와 별도 inference engine 설치를 설명합니다. 단순 `pip install paddleocr`만으로 전체 문서 pipeline의 모든 런타임이 갖춰졌다고 볼 수 없습니다. [설치](https://www.paddleocr.ai/main/en/version3.x/installation.html)
- 공식 코드 LICENSE는 Apache-2.0이고, 예를 들어 공식 `SLANet_plus` model card도 Apache-2.0입니다. 이 한 모델의 조건을 다른 layout/OCR/수식/VLM 가중치 전체에 대입하면 안 됩니다. [코드 LICENSE](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE), [SLANet_plus](https://huggingface.co/PaddlePaddle/SLANet_plus)
- `concatenate_markdown_pages()`는 여러 페이지 Markdown을 하나로 잇는 기능으로 문서화돼 있습니다. 그 자체가 페이지 경계 셀·행의 의미를 복원하는 기능이라는 근거는 확인하지 못했습니다. [PP-StructureV3 출력](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-StructureV3.html)

**미검증/도입 전 확인:** 이 PC의 Windows 전체 PP-StructureV3 조합, offline model path로 완전히 네트워크가 차단되는지, GPU wheel/driver 호환성, 필요한 실제 peak RAM/VRAM을 확인하지 않았습니다. 일반 OCR의 Windows 배포 문서가 있다고 해서 수식·표·모든 가속기 backend까지 같은 범위로 지원된다고 확대하지 않습니다. native PDF 문자를 그대로 보존하는 경로가 기본인지도 adapter 구현 전에 실제 소스로 확인해야 합니다.

### MinerU

**검증 사실**

- 현재 master는 4.0 문서를 제공합니다. Python 범위는 `>=3.10,<3.15`, base는 ONNX small model+llama.cpp, 가속용 full은 Linux vLLM/Windows LMDeploy를 구분합니다. Windows CUDA torch는 별도 설치 경로를 안내합니다. 패키지의 Python 범위와 모든 엔진 wheel 지원 범위는 다릅니다. [공식 README](https://github.com/opendatalab/MinerU/blob/master/README.md), [extras 제약](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/quick_start/extension_modules.md)
- Flash는 native text/preview, Basic은 OCR·표·수식 small model, Standard는 small model+VLM, Advanced는 같은 모델로 계산량을 늘리는 단계입니다. base의 CPU 실행 가능성은 업무 처리 속도가 충분하다는 뜻이 아닙니다. [tier/runtime](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/usage/tiers.md)
- 모델 사전 download/verify와 local source를 제공하고, 원격 문서 파싱·원격 VLM·LLM 후처리를 별도 연결로 구분합니다. LLM title leveling과 cross-page cell continuation은 기본 비활성입니다. [model source](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/usage/model_source.md)
- MiddleJson, structured content, CLI JSON 응답은 다른 계약입니다. `mineru parse --json`을 MiddleJson으로 간주하면 안 되며, source page index, producer/schema version, assets를 함께 보존해야 합니다. [출력 계약](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/reference/output_files.md)
- 현재 코드 LICENSE는 Apache-2.0 기반 추가조건을 명시합니다. 합산 MAU 1억 초과 또는 월매출 USD 2천만 초과 시 별도 라이선스, 제3자 온라인 서비스에서 MinerU 사용 표시를 요구합니다. **순수 Apache-2.0로 요약하면 부정확합니다.** [LICENSE](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)
- 과거 공식 `MinerU2.5-2509-1.2B` model card는 AGPL-3.0을 표시합니다. 이것이 4.0의 모든 선택 모델에 동일하게 적용된다는 뜻도, 4.0 코드 조건으로 모델 조건이 바뀌었다는 뜻도 아닙니다. 모델별 확인이 남아 있습니다. [공식 모델 카드](https://huggingface.co/opendatalab/MinerU2.5-2509-1.2B)

**한계 증거와 주의:** 공식 저장소에는 페이지 경계에서 왼쪽 key 열이 사라지거나 두 행이 합쳐진다는 보고, nested cross-page table 분리 보고, wrap을 두 행으로 인식한다는 보고가 있습니다. 이는 특정 자료·버전의 실패 보고이며 모든 버전의 보편적 실패율이나 현재 재현 증거가 아닙니다. 자동 표 연결만 믿지 않고 원 페이지별 데이터를 보존해야 할 이유입니다. [#5417](https://github.com/opendatalab/MinerU/issues/5417), [#5345](https://github.com/opendatalab/MinerU/issues/5345), [#5418](https://github.com/opendatalab/MinerU/issues/5418)

**미검증/도입 전 확인:** 4.0 릴리스 artifact와 master의 일치, 실제 선택 VLM 모델 revision·라이선스, offline 실행과 multi-page merge 동작, 이 PC peak RAM/VRAM·속도는 확인하지 않았습니다. 오래된 `magic-pdf`/2.x/3.x 설치법을 4.x에 그대로 적용하지 않습니다.

### Marker

**검증 사실**

- 현재 master 설명은 Surya VLM+로컬 inference server 구조입니다. `fast`는 작은 layout detector와 PDF text layer 중심, `balanced`는 VLM layout 및 더 넓은 OCR를 사용합니다. 표는 native text layer를 이용해 재구성하다 낮은 confidence에서 VLM로 fallback하는 설명입니다. 깨끗한 digital PDF에서 VLM을 최소화하는 방식과 모든 문자를 OCR로 다시 읽는 방식은 구분해야 합니다. [공식 README](https://github.com/datalab-to/marker/blob/master/README.md)
- Python 3.10+/PyTorch, NVIDIA는 Docker+NVIDIA Container Toolkit, CPU/Apple Silicon은 llama.cpp server를 안내합니다. 이 설명만으로 Windows 단일 exe 내장이나 vLLM native Windows 실행을 보장하지 않습니다. [설치·backend](https://github.com/datalab-to/marker/blob/master/README.md#inference-backend-prerequisites)
- 코드 Apache-2.0와 가중치의 modified AI Pubs Open Rail-M 조건이 다릅니다. README는 연구·개인·funding/revenue USD 5M 미만 startup의 무료 사용 범위와 그 밖의 상업 모델 사용 조건 확인을 구분합니다. [상업 사용](https://github.com/datalab-to/marker/blob/master/README.md#commercial-usage)
- `--use_llm`은 표의 페이지 간 연결 등을 위한 별도 단계입니다. 복잡한 nested table/form은 한계로 명시돼 있습니다. [공식 README](https://github.com/datalab-to/marker/blob/master/README.md)

**미검증/도입 전 확인:** 모델 조건의 실제 사용자 적용 여부, Windows backend 구성, air-gap 동작, 모델 크기별 VRAM, 실제 표/수식 결과는 확인하지 않았습니다. README 자체 비교 점수나 특정 GPU 성능을 이 프로젝트의 정확도·속도로 인용하지 않습니다. 외부 LLM을 활성화하면 오프라인·API 비용 0 요구와 별도 결정이 필요합니다.

### PyMuPDF / pdfplumber / Camelot / Table Transformer

| 도구 | 확인한 역할 | 주요 경계 |
| --- | --- | --- |
| PyMuPDF | PDF 원 문자·word/block 좌표, region 추출, 테이블 API 등 PDF 기반 도구 | 평문 text 추출은 문서 내부 저장 순서여서 자연 reading order가 아닐 수 있습니다. y/x sort만으로 다단·표 의미 복구를 보장하지 않습니다. |
| pdfplumber | 문자·line·rect 등 상세 geometry, crop/filter, 표 탐지 조정과 시각 디버깅 | OCR 자체가 없고 OCR 문서 표에 강한 지원을 제공하지 않는다고 명시합니다. |
| Camelot | ruled/whitespace/alignment 표, 현재 latest 2.0.0에는 opt-in TATR ML+OCR | 전체 문서 reading order/그림/수식 엔진의 대체재가 아닙니다. |
| Table Transformer | table detection과 structure recognition을 구분하는 이미지 모델 | HTML/CSV의 셀 text는 PDF/OCR의 별도 입력이 필요합니다. |

출처: [PyMuPDF text](https://pymupdf.readthedocs.io/en/latest/recipes-text.html), [pdfplumber](https://github.com/jsvine/pdfplumber), [Camelot 원리](https://camelot-py.readthedocs.io/en/latest/user/how-it-works.html), [TATR](https://github.com/microsoft/table-transformer).

**검증 사실**

- Camelot latest 2.0.0은 `ml` 구조 모델의 bbox를 PDF 좌표로 환산하고 **원 PDF text layer의 문자를 셀에 할당**하는 방식을 명시합니다. 스캔용 `[ocr]`는 별도입니다. 따라서 구버전의 “Camelot은 스캔 PDF를 전혀 다루지 못한다” 설명을 무조건 재사용하면 틀립니다. [2.0 migration](https://camelot-py.readthedocs.io/en/latest/user/migration.html), [공식 README 소스](https://raw.githubusercontent.com/camelot-dev/camelot/master/README.md)
- 다만 stable/advanced에는 OCR 별도 전처리 설명이 남아 있습니다. latest/stable·과거 블로그를 섞지 않고 실제 설치 버전의 API와 소스로 확인해야 합니다. confidence/parsing report는 검토 우선순위에 쓸 수 있어도 숫자 정답 보증은 아닙니다. [advanced](https://camelot-py.readthedocs.io/en/stable/user/advanced.html), [2.0 API](https://camelot-py.readthedocs.io/en/latest/api.html)
- TATR는 코드 MIT이고 Microsoft structure model card도 MIT입니다. 공식 저장소는 **2026-09-21 archive되어 read-only** 상태입니다. 신규 앱의 직접 핵심 기반보다 비교 실험·GriTS 평가·다른 유지보수 도구의 dependency 확인 대상으로 두는 편을 권합니다. [저장소](https://github.com/microsoft/table-transformer), [공식 모델 카드](https://huggingface.co/microsoft/table-transformer-structure-recognition)
- pdfplumber/Camelot은 MIT, PyMuPDF는 AGPL/commercial 경계입니다. 배포 방식 결정을 하기 전에 선택 버전의 실제 LICENSE를 확인해야 합니다. pdfplumber README의 다른 라이브러리 비교는 구식일 수 있으므로 PyMuPDF의 기능은 PyMuPDF 문서에서 확인합니다. [pdfplumber LICENSE/설명](https://github.com/jsvine/pdfplumber), [Camelot 공식 비교](https://camelot-py.readthedocs.io/en/latest/user/comparison.html), [PyMuPDF 공식 저장소](https://github.com/pymupdf/PyMuPDF)

**권고:** GPU가 없더라도 native PDF 문자 좌표와 표 구조를 대조할 도구는 유지합니다. 원문 문자 보존도 잘못된 셀 할당·누락·encoding 오류까지 막지는 못합니다. pdfplumber/Camelot을 추가할 때 서로 다른 PDF/image 좌표 원점·회전·crop 변환을 DocumentIR에서 통일해야 합니다.

### GROBID

**검증 사실**

- scholarly article의 title/sections/references 등 논리 구조·TEI에 특화되어 있습니다. 표 영역과 bbox는 얻을 수 있지만 row/cell 구조 추출은 Camelot/Tabula 같은 전문 도구를 쓰도록 안내합니다. 시각적 원 레이아웃 보존, 신뢰할 수 있는 section depth, superscript/subscript 직렬화에는 제약이 있습니다. [FAQ](https://grobid.readthedocs.io/en/latest/Frequently-asked-questions/), [좌표](https://grobid.readthedocs.io/en/latest/Coordinates-in-PDF/)
- Windows는 Docker+REST 경로를 권합니다. CPU CRF 이미지와 DL full 이미지를 구분하며, 후자는 더 많은 자원과 Linux GPU 경로를 설명합니다. fulltext RAM 4GB, 병렬 batch 6~8GB는 문서 권장치이며 이 PC의 측정치가 아닙니다. [quickstart](https://grobid.readthedocs.io/en/latest/getting_started/), [Docker](https://grobid.readthedocs.io/en/latest/Grobid-docker/)
- consolidation이 활성화되면 Crossref/biblio-glutton 네트워크 조회가 생깁니다. offline 실행에서는 header/citation consolidation을 꺼야 하며, PDF에 없는 DOI/발행정보는 원문 관측값과 구분해야 합니다. [FAQ](https://grobid.readthedocs.io/en/latest/Frequently-asked-questions/)
- 코드 LICENSE는 Apache-2.0입니다. bundle·모델·pdfalto 등 배포 의존성은 별도 inventory가 필요합니다. [LICENSE](https://raw.githubusercontent.com/grobidOrg/grobid/master/LICENSE)

**권고:** 일반 문서 범위를 넓혀도 기존 paper profile의 GROBID/JATS/iCite 경로는 없애지 않습니다. 서지 보강 정보는 추출 원문을 조용히 덮어쓰지 않고 외부 출처·조회 시각·원 추출값을 함께 둡니다.

## 3. 텍스트 PDF의 표가 본문 평문에 섞이는 문제

다음은 공식 기능을 조합한 **설계 권고**입니다. 특정 엔진이 이 순서를 그대로 구현한다는 주장이 아닙니다. 근거는 TATR의 구조/문자 분리, Docling의 cell matching 옵션, PyMuPDF의 저장 순서 경고입니다. [TATR](https://github.com/microsoft/table-transformer), [Docling 고급 옵션](https://docling-project.github.io/docling/usage/advanced_options/), [PyMuPDF text](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)

1. **원 증거 수집:** 원 PDF hash, 페이지 크기/회전, native char/word bbox·원문, 렌더 crop을 보존합니다. “텍스트가 있다”와 “문자가 올바르다”는 별개로 판정합니다.
2. **영역 탐지:** table bbox를 얻고 body/figure/formula와 겹침을 해결합니다. 표 탐지 실패와 표 구조 실패를 다른 오류 코드로 둡니다.
3. **구조 재구성:** 행·열·헤더·spanning cell을 얻습니다. 직사각형 grid로 평탄화해 정보를 버리지 않고 cell의 시작 index와 row/col span을 저장합니다.
4. **셀 text 할당:** native 문자가 신뢰 가능하면 geometry로 원문을 셀에 할당합니다. image-only/garbled 영역에 OCR를 사용하고 native/OCR 양쪽 원값을 보존합니다. 단위·음수·소수점·지수·각주 기호는 값의 일부입니다.
5. **본문과 표 조정:** 수용한 표에 소유권을 준 word/char가 본문 paragraph로 또 나가지 않도록 bbox/ID 기반으로 제외합니다. 원 PDF를 지우는 masking이 아니라 IR serialization의 중복 억제입니다. 표 전체를 대체 노드로 reading order에 삽입하고 caption/footnote는 별도 관계로 연결합니다.
6. **실패를 숨기지 않기:** 표가 불확실해도 원문/crop은 남깁니다. 정답처럼 보이는 table 대신 `structure_unresolved`와 검토 표시를 둡니다. body masking은 bbox 안의 모든 텍스트를 무조건 삭제하는 방식이 아니라 실제 할당된 span을 중심으로 해야 캡션·각주 손실을 피할 수 있습니다.
7. **영역 재시도:** 전체 PDF를 다시 돌리기 전에 같은 table crop에 다른 구조 모드/엔진, OCR 해상도/언어를 비교합니다. 각 후보와 선택 사유를 남기고 본문까지 전부 VLM로 재작성하지 않습니다.
8. **페이지 간 연결:** 같은 열 수만으로 merge하지 않습니다. 열 경계·header·caption·continuation·단위 등을 비교하고, repeated header와 잘린 row를 구분합니다. 연결 후에도 각 원 페이지의 table segment와 bbox가 살아 있어야 합니다.

**숫자 보호 규칙:** 합계나 문맥을 이용해 빠진 숫자를 생성하거나 OCR 후보를 조용히 교정하지 않습니다. 검산은 오류 표시를 위한 signal입니다. 원문과 불일치·셀 중복·미할당 text·수치 토큰 누락은 review queue로 보냅니다. “검출 confidence가 높다”와 “문자와 셀이 모두 맞다”를 같은 상태로 표시하지 않습니다.

**최소 provenance 제안:** source hash, page index와 label, 원 bbox·좌표계·render transform, native span IDs, raw text/OCR 후보, row/col/span, engine/version/model revision/config hash, 단계별 score와 warning, retry 후보·선택 사유, 수동 수정 전후와 시각을 보존합니다. Markdown은 이 JSON에서 재생성합니다.

## 4. API fallback의 위치와 비용

**권고:** 현재의 오프라인 우선·API 비용 0 요구에서는 기본 실행 경로에 외부 API를 넣지 않습니다. 아래는 향후 명시적인 사용자 선택이 생겼을 때의 비교 근거입니다. 이번 조사에서 사용자 문서를 보내지 않았습니다.

| 후보 | 검증한 기능/한계 | 현재 결정 |
| --- | --- | --- |
| Azure Document Intelligence Layout | 셀 row/col/span, polygon, 원문 content의 span을 반환합니다. GA Markdown은 merged/multirow 표현을 위해 HTML table을 사용합니다. 여러 페이지 표는 후처리 연결이 필요하다고 안내합니다. | page/crop 단위 선택 fallback 시험 후보. 실제 가격·한국 리전·처리량·보존조건 계약은 미확인. |
| Google Document AI Gemini Layout Parser | PDF의 표·그림·heading 등을 식별하지만 여러 페이지 표가 두 표로 나뉠 수 있음을 명시합니다. online/batch별 제한이 다릅니다. | 구조/API 계약 확인 후보. 실제 가격·처리 속도·이 문서군 정확도는 미확인. |

출처: [Azure Layout](https://learn.microsoft.com/azure/ai-services/document-intelligence/prebuilt/layout), [Azure Markdown](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/markdown-elements?view=doc-intel-4.0.0), [Google Layout](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk), [Google 제한](https://docs.cloud.google.com/document-ai/limits).

클라우드의 `batch/offline`라는 단어는 로컬 air-gap과 다릅니다. 요청 page 수·재시도·옵션에 따라 요금이 달라질 수 있어 이 조사에서는 단가나 월 예상액을 적지 않습니다. 로컬 실행도 hardware·전기·운영·라이선스 비용이 없다는 뜻은 아니며, API 과금 0과 운영비 0을 구분해야 합니다.

## 5. 채택 전 검증 계획

**권고:** 설치/제품 적용은 후속 구현 단계에서 아래 순서로 진행합니다.

1. 버전·모델·LICENSE inventory를 먼저 확정하고 cache/models/시험 산출물을 Dropbox 밖에 둡니다. 엔진 dependency가 충돌하면 별도 worker process/environment로 분리합니다.
2. 단문 native PDF, 다단 논문, 한글·영문 혼합, 스캔, 작은 숫자, ruled/borderless/rowspan/colspan/nested/multi-page 표, 수식, caption/footnote, 깨진 text layer를 포함한 합법적 synthetic/public golden을 만듭니다. 한국어 실제 HWP 파생 PDF 등 업무 표본은 별도 golden이 필요합니다.
3. Docling 표준과 PP-StructureV3를 같은 golden에서 비교하고, 실패 영역에만 native 표 fallback을 비교합니다. MinerU/Marker는 라이선스·설치·자원 검토를 통과한 후 추가합니다.
4. 전체 평균점수 외에 native/OCR 문자 정확도, **숫자 exact match와 누락/추가**, table detection, 셀 구조, 셀 text, reading order, caption 관계, 페이지 간 연결, provenance 완전성, 실패 시 abstain을 따로 평가합니다.
5. warm/cold 시간, p50/p95, peak RAM/VRAM, 모델 디스크 크기, 취소/timeout, 긴 PDF의 메모리 상한, 오프라인 네트워크 관측을 실제 PC에서 기록합니다. 이 문서에는 측정값이 없습니다.

OmniDocBench는 text/table/formula/reading-order별 평가를 제공하며 GriTS는 table topology/location/content를 구분합니다. 공개 leaderboard만으로 한국어 업무 문서 성능을 결론 내리지 않습니다. 현재 OmniDocBench 알려진 한계에는 text 평가가 중국어/영어 중심이라는 점도 명시돼 있습니다. [OmniDocBench](https://github.com/opendatalab/OmniDocBench), [GriTS 공식 저장소](https://github.com/microsoft/table-transformer)

## 6. 남아 있는 결정

- 배포 방식: 로컬 개인 도구/직원용 내장 앱/서버/외부 배포에 따라 모델·의존성 라이선스 inventory 범위가 달라집니다. 이 조사로 법적 적용을 확정하지 않았습니다.
- 자원 목표: 현재 PC와 저사양 PC의 허용 설치 용량·RAM·첫 실행 시간·CPU-only 목표가 정해져야 시험 결과로 엔진을 골라낼 수 있습니다.
- 지원 수준: `지원`은 인식 가능, 구조 추출 가능, 시각 레이아웃 복원 가능, 편집 가능한 export 가능을 나눠 명시해야 합니다.
- 보수적 결과: 표/수식 복원이 실패한 경우 원 crop+warning을 정식 산출물로 인정할지 정해야 합니다. 본 조사 권고는 이를 인정하며, 생성된 숫자로 실패를 숨기지 않는 것입니다.

## 7. 책 한 권·장문 PDF와 AI/wiki 입력을 위한 보완 조사

추가 목표는 논문에 한정되지 않는 정보·문서를 Markdown으로 수집해 AI 입력과 wiki 재료로 쓰는 것입니다. 장문 처리는 “파일을 열 수 있음”, “모든 페이지를 빠짐없이 처리함”, “중단 후 재개함”, “전역 구조와 출처를 보존함”, “PC와 UI가 응답함”을 각각 검증해야 합니다. 아래 기능이 존재한다는 이유만으로 책 한 권의 처리 성공이나 고정 RAM 상한을 보장하지 않습니다.

### 7.1 엔진의 실제 기능과 앱이 맡아야 할 재개 기능

| 엔진 | 공식 문서·소스로 확인한 장문 관련 기능 | 이것만으로 확인되지 않는 것 |
| --- | --- | --- |
| Docling | `convert`에 `page_range`, `max_file_size`, `max_num_pages`; `convert_all`은 여러 **문서의** `ConversionResult` iterator; progress callback. | `convert_all`이 한 책의 페이지 IR을 durable하게 한 장씩 저장한다는 보장은 없습니다. 제한 초과 문서는 변환하지 않는 정책이며 자동 분할이 아닙니다. |
| PP-StructureV3 | 현재 wrapper의 `predict_iter()`는 underlying prediction iterator를 반환하고, `predict()`는 이를 `list(...)`로 모읍니다. | iterator 사용만으로 PDF renderer·모델 내부까지 모든 메모리가 page bounded라는 보장은 없습니다. 내장 page checkpoint/restart 계약은 이번 범위에서 확인하지 못했습니다. |
| MinerU 4 | `--pages` 선택, 로컬 library의 background parsing/cache/read continuation, stateless 폴더 batch. stdout의 읽기 cursor와 artifact export를 구분합니다. | cursor는 파싱 실행 checkpoint가 아닙니다. stateless `mineru-kit parse`는 library DB/cache를 쓰지 않으며, 프로세스 crash 후 특정 stage부터 재개하는 보장도 확인하지 못했습니다. |
| Marker | zero-based `--page_range`, 문서별 worker pool, `--skip_existing`으로 기존 결과가 있는 파일 건너뛰기, worker recycle 옵션. | `skip_existing`은 책 중간 페이지나 stage부터 재개하는 기능이 아닙니다. 출력 존재 검사가 무결성·입력 hash·모델 버전 일치까지 보장하는지 별도 확인해야 합니다. |
| GROBID | fulltext REST에 one-based `start`/`end` 페이지 선택, service 기반 병렬 처리. | 책 전체의 전역 논리 구조·페이지별 durable resume·범용 wiki 추출 엔진으로서의 적합성을 보장하지 않습니다. |

출처: [Docling API](https://docling-project.github.io/docling/reference/document_converter/), [PaddleOCR wrapper 소스](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/paddleocr/_pipelines/pp_structurev3.py), [MinerU CLI](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/usage/cli_tools.md), [Marker batch 소스](https://raw.githubusercontent.com/datalab-to/marker/master/marker/scripts/convert.py), [Marker 옵션](https://raw.githubusercontent.com/datalab-to/marker/master/README.md), [GROBID REST](https://grobid.readthedocs.io/en/latest/Grobid-service/).

**MinerU의 완료 범위 주의:** `mineru parse`의 기본은 첫 10 PDF 페이지입니다. `--pages all`과 stdout의 `--limit`/`--after`는 다른 축입니다. 파일 export는 stdout cursor와 무관하게 **요청한 페이지 범위 전체**를 저장하므로, 전체 책을 원한다면 전체 페이지 요청이 필요합니다. `--wait` timeout은 background parse의 실패·취소를 뜻하지 않습니다. 종료코드만으로 책 변환 상태를 판정하면 안 됩니다. [CLI 계약](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/usage/cli_tools.md)

**앱 설계 권고:** source hash·총 페이지 수·engine/config/model revision을 갖는 job manifest, 요청 window별 상태와 산출물 checksum, 완료 후 atomic commit을 앱이 관리합니다. 재개할 때 같은 입력·설정인 검증 완료 window만 재사용하고, 실행 중/부분 기록 window를 다시 큐에 넣습니다. 엔진 진행 이벤트를 받았다는 이유만으로 checkpoint 완료로 쓰지 않습니다. 앱이 반환된 window IR·assets를 저장하고 검증한 시점이 완료 경계입니다. 엔진 cache를 쓰더라도 앱 산출물 정합성 검증은 남습니다.

### 7.2 메모리를 제한할 수 있는 범위

**검증 사실**

- Docling 공식 옵션은 stage 사이 decoded page buffer를 `queue_max_size`, threaded docling-parse의 선행 decode를 `max_concurrent_results`로 제한합니다. 모델·문서 IR·export까지 전체 process RAM을 고정 상한으로 만든다는 주장은 아닙니다. [자원 제한](https://raw.githubusercontent.com/docling-project/docling/main/docs/usage/advanced_options.md)
- Docling 현재 표준 pipeline 소스는 selected page 목록을 `conv_res.pages`에 두고, assembly 단계에서 페이지 element들을 모아 reading order와 heading hierarchy를 실행합니다. 이미지 cache를 비우거나 backend를 unload하는 경로가 있어도 전체 결과가 일정 메모리라는 뜻은 아닙니다. blocking inference/backend thread는 cleanup 대기 후에도 실행하며 자원을 잡을 수 있다는 소스 경고가 있습니다. [표준 pipeline 소스](https://raw.githubusercontent.com/docling-project/docling/main/docling/pipeline/standard_pdf_pipeline.py)
- MinerU는 처리 window 크기, PDF render worker/timeout, pixel budget을 문서화합니다. 선택적 `MINERU_MALLOC_TRIM`은 Linux/glibc 대상이고 live object·GPU memory·render-worker memory를 해제하지 않으며 지원되지 않는 플랫폼에서는 no-op입니다. 공식 memory regression 문서도 동일 환경에서 실제 workload를 측정한 뒤 RSS/OOM 개선을 주장하도록 요구합니다. [CLI 자원 옵션](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/usage/cli_tools.md), [memory regression](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/usage/pdf_memory.md)
- Marker는 batch worker 수를 조절하며 문서가 길어 OOM이 나면 worker 수를 줄이거나 PDF 분할을 고려하도록 안내합니다. 작업 사이 worker recycle도 소스에 있습니다. 이 기능은 단일 PDF 내부의 peak RAM 상한을 보장하지 않습니다. [공식 README](https://raw.githubusercontent.com/datalab-to/marker/master/README.md), [worker 소스](https://raw.githubusercontent.com/datalab-to/marker/master/marker/scripts/convert.py)

**앱 설계 권고**

- 한꺼번에 모든 페이지 bitmap·crop·base64와 전체 HTML/Markdown을 메모리에 올리지 않습니다. 원본은 read-only file 참조, assets는 디스크 파일로 유지하고 UI에는 현재 페이지와 작은 thumbnail만 전달합니다.
- 동시에 처리할 문서 수와 문서당 page window·render buffer·모델 batch를 따로 제한합니다. `worker 수 × 각 worker의 모델/bitmap/IR 메모리`까지 측정하며, 최대 병렬화가 기본값이 되지 않게 합니다. GPU inference server를 공유하는 엔진에서는 각 worker의 숨은 concurrency도 확인합니다.
- local supervisor와 변환 subprocess를 분리하고 heartbeat·hard deadline·메모리 관측을 둡니다. 취소 후 정상 종료를 기다리되 응답 없는 소유 subprocess만 정리할 수 있는 경계가 필요합니다. 엔진의 Python thread timeout 하나로 PC 안정성을 보장하지 않습니다.
- window 크기는 임의의 고정 수치를 성능 보장처럼 쓰지 않고 실제 저사양·기준 PC의 peak RSS/VRAM으로 결정합니다. 반복 OOM/timeout은 window 축소 후 제한 횟수로 재시도하고, 계속 실패하면 해당 범위의 원문/crop·오류를 남깁니다.

**미확인:** 조사한 모든 엔진이 원 PDF file bytes를 전부 RAM에 읽는지, PDF backend별 mapping·page cache·stream decode 방식, page range 요청이 원 파일 전체 parsing을 얼마나 줄이는지까지 증명하지 않았습니다. 따라서 “page range가 있으니 어떤 책도 일정 RAM”이라고 약속하지 않습니다.

### 7.3 window 분할과 책 전체 구조의 충돌

**검증 사실:** Docling의 현재 heading hierarchy 단계는 bookmark→numbering→visual style 신호를 쓰고 기본 비활성입니다. 깊이는 문서에 실제 등장한 신호들 사이의 **상대 단계**이며, OCR에는 font weight/slant 정보가 없습니다. 스타일 기반 hierarchy는 parsed pages 유지도 필요합니다. 따라서 window마다 별도 처리한 heading depth를 그대로 이어 붙이는 것은 전역 책 구조와 같다고 볼 수 없습니다. [heading levels](https://docling-project.github.io/docling/usage/heading_levels/)

**검증 사실:** MinerU의 선택적 LLM title leveling은 `is_full_document=true`일 때만 실행되고 page-selected 결과에서는 건너뜁니다. 페이지 간 표의 boundary-cell continuation도 별도 기능입니다. bounded extraction의 성공과 문서 전체 계층·연결 성공은 다른 요구입니다. [후처리 계약](https://raw.githubusercontent.com/opendatalab/MinerU/master/docs/en/usage/quick_usage.md)

**앱 설계 권고:** window IR을 먼저 보존한 뒤, 필요한 구조 metadata만 읽는 별도 전역 정리 단계를 둡니다. 다음 관계를 분할 경계 양쪽에서 검사합니다.

- **목차:** embedded outline과 인쇄된 목차 페이지를 다른 증거로 보존합니다. 원 source page index, PDF page label, 인쇄된 page number, chapter anchor를 구분하고 일치하지 않는 link를 임의 생성하지 않습니다. 한국어 `제N장` 등 구조는 별도 golden으로 검증합니다.
- **reading order:** 페이지 순서, 페이지 내부 다단 순서, 페이지를 넘긴 문단 연결을 나눕니다. window의 출력 순서나 callback 도착 순서를 책의 읽기 순서로 쓰지 않습니다.
- **표:** window 끝/다음 시작의 표 segment를 연결 후보로 두고, 원 segment와 page/bbox를 유지합니다. repeated header 제거와 잘린 row 연결은 다른 연산이며, 미확정 연결은 분리 상태로 남깁니다.
- **각주·endnote:** 마커와 본문을 별개 block으로 남기고 page/chapter/document scope를 구분합니다. 같은 숫자가 반복되는 책에서 마커 번호만으로 연결하지 않습니다. 경계 밖 본문을 못 찾으면 unresolved link를 남깁니다.
- **머리말·꼬리말·색인·참고문헌:** 반복 furniture 감지에 여러 페이지의 통계가 필요할 수 있습니다. wiki 본문에서 숨기더라도 raw IR에는 보존합니다. 색인/참고문헌을 문서 종류만으로 모두 노이즈 처리하지 않습니다.

겹치는 context page를 재추출하는 방식은 시험할 수 있으나, source span ID 기반 중복 제거와 경계 재구성 결정 기록이 필요합니다. 최종 전역 단계에서 실패한 관계 때문에 이미 검증한 다른 페이지의 결과를 폐기하지 않습니다.

### 7.4 장문 export·미리보기에서 UI 멈춤을 피하기

다음은 **앱 제안**이며 엔진의 기본 UI가 이미 보장하는 기능이 아닙니다.

- parsing, review, export, wiki indexing을 별도 job/stage로 둡니다. export도 background worker에서 처리해 UI main thread가 큰 JSON parse·Markdown 직렬화·HTML render·ZIP 생성을 직접 맡지 않게 합니다.
- 진행률은 페이지 완료, 구조 정리, assets 저장, export finalize를 구분합니다. Docling은 페이지들이 out-of-order로 완료될 수 있고 마지막 페이지 뒤 enrichment가 실행될 수 있다고 명시하므로, `page_no/total`이나 page bar 100%만으로 완료를 표시하지 않습니다. [진행 이벤트](https://raw.githubusercontent.com/docling-project/docling/main/docs/usage/advanced_options.md)
- UI는 페이지/장별 미리보기를 가상화·지연 렌더하고 큰 결과 전체를 단일 textarea/DOM에 매번 붙이지 않습니다. progress 이벤트는 적절히 모아서 보내며 원문·assets를 매 tick 재전송하지 않습니다.
- canonical IR은 page/window별 디스크 shard+manifest로 관리할 수 있게 하고, Markdown·HTML·ZIP은 순서대로 쓰거나 bounded window로 생성합니다. 선택 엔진 serializer가 전체 string만 돌려주면 그 제한을 명시하고 작은 구간 export adapter의 정확성을 별도 검증합니다.
- 최종 artifact는 `.partial` 상태에서 생성·검증한 뒤 완료 파일명으로 바꿉니다. 디스크 부족·파일명 충돌·asset 유실·취소를 정상 실패로 처리하고, 완료되지 않은 책을 완료 표시하지 않습니다. 재시작은 검증 완료 shard부터 이어갑니다.

### 7.5 AI 입력·wiki를 위한 장문 산출물과 수용 시험

**앱 설계 권고:** 한 권 전체 Markdown과 장/절별 Markdown을 같은 JSON 정본에서 생성하고, `index.md`에는 원 제목·문서 ID·장 목록·원 출처/locator·추출 상태를 둡니다. 검색용 chunk는 heading path·source page range·표/수식 block ID를 갖게 하며, 표를 token 수 때문에 셀 중간에서 자르지 않습니다. AI용 정규화 텍스트와 원 관측 text, 추출된 facts와 생성된 summary/wiki prose를 별도 provenance로 보존합니다. 전체 문서를 한 모델 prompt에 넣는 것을 완전성의 기준으로 삼지 않습니다.

책 분량 검증에는 다음을 추가합니다. 이는 목표 시험 항목이며 이번 조사에서 실행하지 않았습니다.

1. 수백/수천 페이지 synthetic/public PDF에서 요청 페이지와 실제 산출 page IDs의 일치, 빈 페이지 포함 coverage, 순서·중복·누락을 검사합니다. 총 페이지 수가 아닌 검증 완료 범위를 기준으로 완료 판정합니다.
2. 2개 이상 window에 걸친 목차·문단·표·각주 fixture와 printed/PDF page label 차이를 넣어, 전체 실행과 분할·재개 실행의 원문/수치/locator 동등성을 비교합니다.
3. 파싱·assembly·export 각 단계에서 강제 중단·앱 재시작·worker timeout·디스크 부족을 주입하고, 검증 완료 shard 보존과 재시도 범위를 확인합니다.
4. 큰 그림·스캔·표 집중 장, 혼합 page rotation, 긴 수식·깨진 text layer로 peak RSS/VRAM·디스크·취소 지연을 측정합니다. 어느 페이지 수에서도 무조건 성공한다고 약속하지 않습니다.
5. 큰 결과의 scroll·검색·검토 전환·export 중 UI 응답성을 직접 측정하고, export 완료 후 모든 Markdown asset link와 chapter anchor, JSON schema·source hash를 검사합니다.
