# 범용 문서→Markdown: Office·한글·구조 문서 조사

조사일: 2026-10-23. 범위는 공식 문서·표준 제공기관·개발자 자신의 소스 저장소입니다. 아래의 **확인**은 문서 또는 소스 열람 결과이며 실행 시험 결과가 아닙니다. 설치, 개인 문서 변환, 문서 업로드, COM 호출은 하지 않았습니다. `main`, `latest` 링크는 변경될 수 있으므로 구현 시 릴리스·커밋·의존성 lock을 고정해야 합니다.

## 1. 권고하는 엔진 구성

**Docling의 기존 형식별 backend를 먼저 검증하고, 결손만 보완하는 방식**이 적합합니다. `native-first`는 DOCX/PPTX/XLSX/HWPX가 가진 문단·셀·수식·리소스 구조를 먼저 읽는다는 뜻이며, 처음부터 모든 파서를 새로 작성한다는 뜻은 아닙니다.

Docling의 공식 지원표에는 DOCX/XLSX/PPTX, DOC/XLS/PPT, RTF, ODT/ODS/ODP, EPUB, HTML/XHTML, MHTML/MHT, CSV, EML/MSG, LaTeX와 JATS XML이 있습니다. 구형 MS Office와 RTF에는 LibreOffice가 필요합니다. HWP/HWPX와 ipynb는 이 지원표에서 확인되지 않았습니다. **목록에 없다는 사실만으로 어떤 플러그인에서도 처리 불가능하다고 단정하지는 않습니다.** [공식 지원 형식](https://docling-project.github.io/docling/usage/supported_formats/)

제안하는 흐름은 `파일 식별 → 형식별 backend → 공통 구조 JSON → Markdown·HTML·CSV와 assets`입니다. Markdown은 JSON 정본의 내보내기 결과로 취급하고, 정본 revision/hash·exporter 버전·옵션을 기록해 JSON 수정 후 낡은 Markdown이 남는 것을 감지합니다. 이 흐름 및 우선순위는 이번 조사에 따른 설계 제안입니다.

| 우선순위 | 입력군 | 우선 시험 경로 | 필요 보완·한계 |
| --- | --- | --- | --- |
| P0 | DOCX | Docling Word backend | 변경 추적 정책, 주석·각주, 글상자, OMML, 중첩·병합 표 검증 |
| P0 | PPTX | Docling PowerPoint backend | 읽기 순서, 그룹 좌표, 수식, 발표자 노트·댓글·숨김 슬라이드 검증 |
| P0 | XLSX, CSV/TSV | Docling Excel/CSV backend | 수식 문자열과 캐시 값, 날짜·표시 형식·병합 범위 보완 |
| P0 | HWP 5.x, HWPX | rHWP 공통 IR 중심 별도 native adapter 후보 비교 | HWP5 OLE와 HWPX ZIP XML을 다른 parser로 처리. 병합 topology가 핵심 |
| P1 | DOC, PPT, XLS, RTF | Docling의 LibreOffice bridge | 현대 Office 형식으로 변환 후 구조 parser. 원본·변환 계보·변환 경고 유지 |
| P1 | ODT, ODP, ODS | Docling의 기존 ODF backend | 표 반복·중첩·수식·주석 등 형식별 적합성 시험 |
| P1 | HTML/XHTML, MHTML/MHT, EPUB | Docling backend, Pandoc 비교 | 리소스·링크·목차·독서 순서·한국어 인코딩, 원격 로드 차단 |
| P1 | EML, MSG | Docling email backend | 본문과 첨부 구분, MIME·한국어 헤더·CID 이미지 검증. 첨부 재귀는 별도 작업 |
| P1 | JATS XML, LaTeX | Docling 우선, Pandoc 비교 | 스키마·수식·인용·참고문헌·include 정책 |
| P1 | ipynb | Pandoc/nbformat adapter | 코드 실행 없이 저장된 셀·출력·MIME bundle만 추출 |
| P2 | HWP 2.x/3.x, HML/HWPML | 별도 식별, rhwp 등 비교 시험 | HWP5 지원으로 포괄하지 않음. 미검증 판본은 명시적 제한 상태 |
| 이후 | URL, Drive/Notion 등 | 별도 connector 계층 | 로컬 파일 지원과 인증·다운로드·버전·권한 설계를 분리 |

위 표의 P0/P1은 구현 계획상의 제안이며 해당 앱의 구현 완료나 품질 보장을 뜻하지 않습니다. 형식별 Docling 지원 근거는 위 공식 지원표이고, 실제 backend 관찰은 다음 절과 같습니다.

## 2. Docling에 이미 존재하는 Office 구조 처리

### DOCX

**확인:** 현재 Word backend 소스는 `python-docx`를 사용하고 OMML→LaTeX 경로가 있습니다. 표의 `gridSpan`, `vMerge`로 `row_span/col_span`을 만들고, 셀 안 복합 내용에는 `RichTableCell` 경로가 있습니다. 주석은 NOTES layer에 추가하고 대상 항목에 연결하는 코드가 있습니다. 외부 chart XML용 parser는 entity/DTD/network를 비활성화합니다. 차트 데이터 구조와 차트 래스터 이미지는 분리되어 있습니다. [Word backend 소스](https://github.com/docling-project/docling/blob/main/docling/backend/msword_backend.py)

**미확인:** 앱에 고정될 실제 릴리스에 같은 기능이 모두 있는지, 한국어 다단/떠 있는 글상자 순서, 변경 추적 전체 보존, 줄바꿈된 병합·중첩 표·수식·주석 anchor의 정확성은 fixture 시험이 필요합니다. 단순히 라이브러리에 기능 코드가 있다는 사실은 실제 문서 무손실 증명이 아닙니다.

### PPTX

**확인:** PowerPoint backend는 `python-pptx`를 사용하고, `.ppt`는 LibreOffice를 통해 `.pptx`로 바꿉니다. 위치 기준 shape 순회와 그룹 순회, 표 XML의 `rowSpan/gridSpan`, OMML 수식, 발표자 노트와 댓글의 NOTES layer, 숨김 슬라이드/shape의 INVISIBLE layer 코드가 있습니다. 댓글은 개별 shape보다 슬라이드 group에 연계됩니다. [PowerPoint backend 소스](https://github.com/docling-project/docling/blob/main/docling/backend/mspowerpoint_backend.py)

**미확인:** 다열 발표자료·애니메이션·SmartArt·복잡한 그룹의 의미상 읽기 순서와 레이아웃 충실도, 빈 병합 셀의 topology 보존, 기본 Markdown에서 NOTES/INVISIBLE layer를 어떤 정책으로 내보낼지는 별도 시험·제품 정책이 필요합니다. 슬라이드 좌표 정렬은 의미상 읽기 순서의 보증이 아닙니다.

### XLSX

**확인:** Excel backend는 `openpyxl.load_workbook(..., data_only=True)`로 읽습니다. 병합 anchor와 span 인덱스, 떨어진 셀 영역의 여러 표 추출, 이미지·기존 메모·threaded comment 처리 코드가 있습니다. Threaded comments의 현재 소스에는 Path 입력에 한정된 경로가 있습니다. [Excel backend 소스](https://github.com/docling-project/docling/blob/main/docling/backend/msexcel_backend.py)

**시사점/제안:** 기본 결과만으로 원래 수식 문자열까지 보존된다고 약속하면 안 됩니다. 수식 문자열, 캐시 값, 캐시 부재/신선도 미확인, 원래 number format, 원본 cell 주소를 JSON에 따로 넣는 보조 읽기가 유용합니다. 숫자처럼 보이는 ID/앞자리 0/긴 정수/오류 값/1900·1904 날짜 체계도 비교해야 합니다.

### LibreOffice bridge

**확인:** Docling의 `convert_to_modern_format`은 legacy Office를 DOCX/XLSX/PPTX로 변환합니다. 전체 문서를 반드시 PDF로 먼저 만드는 경로가 아닙니다. 임시 프로필, 매크로 실행 금지 설정, 링크 갱신 금지, timeout, 예상 출력 존재 검사가 구현되어 있습니다. 별도 도형/차트 rasterization은 PDF를 사용할 수 있습니다. Windows 프로세스 종료의 구현은 POSIX process-group과 다르므로 종료 후 자식 프로세스 정리는 앱 측에서 검증해야 합니다. [bridge 소스](https://github.com/docling-project/docling/blob/main/docling/backend/docx/drawingml/utils.py)

**제안:** 구조 경로와 렌더 보조 경로를 구분해 기록하십시오. 원본 전체를 PDF로 일괄 바꾼 뒤 재추출하면 문단·수식·셀·주석 구조를 렌더 좌표에서 다시 추측하게 됩니다. JSON 구조가 있는 원본에서는 이를 기본 경로로 삼지 않는 편이 적합합니다.

## 3. 한글 문서의 판본 구분과 엔진 후보

### 공식 형식 근거

한컴은 HWP 2.x/3.x와 5.x, HWPML, 배포용 문서·수식·차트 형식 자료를 공개합니다. 공개 규격을 참고한 결과물의 표기 요구와 규격 문서 재배포 조건이 안내되어 있습니다. **파일 형식 공개는 Hancom 앱/Automation 자체의 무제한 재배포 허가와 동일하지 않습니다.** [한컴 HWP/OWPML 형식 공개와 사용 조건](https://shop.hancom.co.kr/support/downloadCenter/hwpOwpml)

HWP5는 Compound File 구조 안의 FileHeader/DocInfo/BodyText/SectionN/BinData와 레코드를 해석해야 합니다. 압축은 zlib이며 압축·암호화 flag를 구분해야 합니다. [한컴의 HWP 파싱 설명](https://tech.hancom.com/python-hwp-parsing-1/)

HWPX는 OWPML에 기반한 ZIP 컨테이너의 여러 XML·리소스로 이루어져 있습니다. XML text를 전부 연결하는 것만으로 표 병합·다단·문단 계층이 보존되지는 않습니다. [한컴 HWPX 구조 설명](https://tech.hancom.com/hwpxformat/), [한컴의 구조 추출 설명](https://blog.hancom.com/en/hwp-hwpx-data-extraction-format-library/)

확장자가 `.hwp`라는 이유로 모두 HWP5/OLE이라고 가정하지 마십시오. 제품 제안은 magic/container/header로 HWP2/3·HWP5·HWPX·HML을 나누고 `encrypted`, `distribution`, `DRM/rights-protected`, `unsupported-version`, `partial`, `failed`를 구분하는 것입니다. 비밀번호 해제·권한 우회는 지원 기능에 포함하지 않습니다. 배포용 문서와 비밀번호 암호 문서도 동일 상태로 묶지 않습니다.

### 후보별 근거와 제약

| 후보 | 실제 확인 | 역할 제안 | 미확인·주의 |
| --- | --- | --- | --- |
| `hwp-hwpx-parser` | 순수 Python HWP5/HWPX reader, 그림·각주·미주·메모 API, 암호 감지, PyPI universal wheel | 가벼운 로컬 추출 비교 후보 | `TableData.rows`만으로는 병합 topology 부족. 자체 정본 adapter 보완 필요 |
| `pyhwp` / `hwp5txt` | HWP v5 parser, 내부 stream 분석, ODT/TXT 변환을 experimental로 표기 | 진단·교차검증·대체 추출 후보 | AGPLv3+이며 README의 Python 지원 목록이 오래됨. 현대 Windows 패키징·기능 적합성 미검증 |
| 한컴 `hwpx-owpml-model` | 공식 C++ OWPML 모델, element 추출/저장, Windows/VS 빌드 예제 | HWPX 구조의 참조·대체 parser 후보 | 제공 text sample은 첫 section만 추출한다고 명시. 그 sample 그대로 앱에 사용하면 다중 section 누락 |
| `rhwp` | Rust/WASM, HWP5/HWPX/HWP3 공통 Document IR 문서, MIT | 구형 HWP3를 포함한 구조 parser 비교 후보 | 자체 fixture·Windows 통합·IR 안정성·그림/수식/주석 보존을 별도로 검증 |
| `hwplib` / `hwpxlib` | Java HWP5 객체 모델·표 조작·텍스트 추출, HWPX 별도 프로젝트 | 병합·중첩 구조가 필요한 HWP5 교차검증 후보 | JRE 배포 추가. HWP 암호 문서·페이지 렌더/실제 페이지 수는 README상 제한 |
| Hancom Automation / SDK | 공식 Automation 이용 조건·API 문서, SDK 별도 라이선스 | 설치된 정품 앱을 쓰는 명시적 선택 fallback | Windows 앱·COM·버전·라이선스 의존. 기본 필수 의존으로 만들지 않음 |

### rHWP 기술을 구체적으로 재사용하는 방법

사용자가 언급한 rHWP의 canonical 저장소는 **[edwardkim/rhwp](https://github.com/edwardkim/rhwp)**입니다. GitHub API에서 확인한 latest release는 **v0.8.7**, 게시 시각은 2026-10-06 07:54:34 UTC였으며 Windows x86_64 ZIP과 SHA256SUMS.txt가 실제 asset 목록에 있습니다. 조사 시 main은 `1a76570e833917d15817415a53c09ad61ab3203f`였습니다. 다음 근거는 움직이는 main 대신 v0.8.7에 연결합니다. 바이너리를 다운로드하거나 실행하지는 않았습니다. [릴리스](https://github.com/edwardkim/rhwp/releases/tag/v0.8.7), [GitHub release API](https://api.github.com/repos/edwardkim/rhwp/releases/latest)

**그대로 가져올 가치가 있는 계층은 parser·공통 IR·읽기 전용 query입니다.** 원본을 PDF로 바꾸거나 렌더된 SVG의 글자를 다시 읽는 방식보다 구조 정보를 직접 얻을 수 있습니다.

| 재사용 지점 | 실제 소스에서 확인 | 이 앱에 연결할 방법 제안 |
| --- | --- | --- |
| `parse_document` / `parse_document_with_metadata` | HWP5/HWPX/HWP3/HML 자동 감지와 공통 `Document`, HML warnings/metadata 별도 반환 | 원본 바이트 한 번 파싱 후 app canonical JSON으로 adapter |
| `model::Document` | sections, DocInfo, binary assets, 원본 보조 stream/ZIP entry, source provenance | 원본 계보·section/paragraph 주소·미해석 요소를 정본과 진단에 연결 |
| `model::Table/Cell` | row/col, row_span/col_span, cell 안 `Vec<Paragraph>`, repeat-header·caption | HTML 병합 표와 JSON topology의 원자료로 사용 |
| `extract_tables(&Document)` | `TableGrid`, anchor cell·nested·isHeader·section/paragraph/control·containerPath | 표 수집·병합 보존을 새로 구현하기 전에 재사용, cell 내부 rich block은 원본 IR 보완 |
| `build_structure` | outline/조문/auto 분류와 제목 트리 | 실제 문서 heading 근거와 inferred 여부를 함께 기록 |
| `doclang::eqedit::convert_with_degraded` | EqEdit script→LaTeX와 degraded token 목록 | 원본 script·파생 LaTeX·손실 진단 병행 보존 |
| `rag::build_chunks` | 제목 경계·표 행 분리·반복 header·untrusted provenance | 기준선으로 비교; 아래 제한을 보완한 뒤 wiki ingest에 채택 |

parser는 DRM/빈 파일/unknown을 구분하고 일부 HWP3·HML만 지원한다고 명시합니다. HWP5 배포용 ViewText 및 알려진 비밀번호를 전달하는 별도 API도 있으나, 지원 암호화 방식은 제한되어 있습니다. 앱은 암호를 추정하거나 DRM을 우회하는 기능으로 확장하지 않습니다. [parser 진입점](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/parser/mod.rs)

공통 Document는 원본 포맷과 변환 계보를 보관합니다. 그 원본 계보와 parser가 보정한 레이아웃 값은 동일하지 않으므로, 원본 byte/hash·논리 구조·렌더 결과의 출처를 구분하는 adapter가 필요합니다. [Document 모델](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/model/document.rs)

Table의 병합 span과 셀 문단은 실제 모델 필드입니다. 반면 dense grid 재구성에는 4,000,000칸 상한이 있어 이 수치를 문서 형식 표준의 제한으로 설명해서는 안 됩니다. [Table/Cell 모델](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/model/table.rs)

`extract_tables`는 본문뿐 아니라 글상자·머리말/꼬리말·각주/미주를 재귀하고 중첩 표는 부모 cell 안에 둡니다. 다만 현재 `MAX_NEST_DEPTH=8`에 도달하면 빈 목록을 반환합니다. 이 한도를 넘는 문서를 조용히 완전 성공으로 처리하지 않도록 adapter에서 깊이/누락 여부를 검사해야 합니다. [표 추출 query](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/document_core/queries/table_extract.rs)

`build_structure`는 본문을 이전 제목의 body로 모으며 법률 조문 패턴도 사용할 수 있습니다. 일반 책/설명서에서 조문 패턴 추정이 과도하지 않은지 시험하고, 원본 개요 수준과 추정 제목을 구별하십시오. [구조 query](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/document_core/queries/structure.rs)

**rHWP의 기존 Markdown/RAG output을 무조건 정본으로 삼으면 안 되는 이유도 1차 문서에 명시되어 있습니다.** CLI manual은 `export-markdown --json`이 DOM이 아니라 산출물 manifest라고 설명합니다. Markdown은 병합을 잃을 수 있어 `export-tables --json`을 별도 제공합니다. 표 셀 자동번호는 IR text에 없고 render 때 주입되는 한계가 있습니다. CSV는 본문 최상위 표만 대상으로 하며 중첩 표는 v1 범위 밖입니다. [CLI 매뉴얼](https://github.com/edwardkim/rhwp/blob/v0.8.7/mydocs/manual/cli_commands.md)

`rag/mod.rs`는 본문 문단별 offset을 남기지 않고 제목 주소만 남긴다는 점, 같은 segment에서 본문 다음 표를 배치해 정밀 문단↔표 interleave가 미구현이라는 점, 물리 page 번호가 없다는 점을 직접 명시합니다. Token count도 실제 tokenizer가 아니라 `tokenEstimate`입니다. wiki corpus에서는 이 제한이 검색 근거와 문맥을 바꿀 수 있으므로 raw IR에서 순서/주소를 보완한 canonical adapter를 거친 뒤 chunking하는 편이 적합합니다. [RAG 모듈의 명시된 제한](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/rag/mod.rs)

chunker의 CJK 1글자·그 외 비공백 4글자당 1token 휴리스틱 이름은 `cjk1-latin4-v1`이며, dense table projection에는 별도 200,000칸 상한이 있습니다. 임베딩 모델 token 제한의 정확한 계산값으로 사용하지 않고 실제 tokenizer의 후단 검사를 붙이는 것이 적합합니다. [chunker 소스](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/rag/chunker.rs)

수식은 기존 public `convert_with_degraded`가 lexer→AST→LaTeX로 변환합니다. 알 수 없는 명령은 무조건 오류가 되는 것이 아니라 `text` 등으로 통과하고 `degraded`에 의미 손실을 알립니다. 단순 `convert`는 이 진단 목록을 버리므로 corpus adapter에는 `convert_with_degraded`가 적합합니다. 구조적으로 복구할 수 없는 괄호는 EqError입니다. [수식 변환 API](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/doclang/eqedit/mod.rs), [LaTeX emitter](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/doclang/eqedit/latex.rs)

수식 renderer는 별도로 EqEdit script→AST→layout→SVG/Canvas를 수행합니다. 따라서 화면에 수식이 보이는지와 추출 LaTeX가 의미를 보존하는지는 서로 다른 시험 항목입니다. [수식 renderer 문서](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/renderer/equation/README.md)

Markdown exporter는 페이지별 파일과 그림을 저장하지만 **그림 저장/추출 실패가 page 실패로 집계되지 않는 경로**가 있습니다. Exit 0과 MD 파일 존재만으로 그림까지 완전 추출했다고 판정하지 않도록 asset expected/written/missing 수를 별도로 점검해야 합니다. [Markdown exporter 소스](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/cli/outputs/text.rs)

배포 방식은 다음 순서로 비교하는 것이 적합합니다.

1. **비교 시험:** pinned Windows CLI를 별도 worker에서 `capabilities`와 읽기 전용 export 명령으로 시험하고, MD와 table JSON의 결손을 비교합니다. stdout contract·exit code·stderr warning을 수집합니다. 이 단계는 호출/출력 검증용이며 corpus 정본을 확정하는 단계는 아닙니다. [CLI 명령 디스패치](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/main.rs), [table JSON adapter](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/cli/outputs/tabular.rs)
2. **제품 통합:** Rust `rlib`를 thin sidecar에 연결해 parse 한 번으로 Document→canonical JSON/assets를 내보냅니다. Python 본체가 있더라도 앱 내부 교환은 versioned JSON으로 제한하면 upstream Rust 내부 타입 변경의 영향을 adapter에 모을 수 있습니다. 현재 crate는 `rlib/cdylib`이며 parser API가 public입니다. [Cargo 설정](https://github.com/edwardkim/rhwp/blob/v0.8.7/Cargo.toml), [public library API](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/lib.rs)
3. **선택 미리보기:** WASM `@rhwp/core`는 브라우저 뷰어가 필요할 때 검토합니다. 전체 editor UI나 GPU/Skia 렌더러를 문서 추출의 필수 조건으로 만들 필요는 없습니다. native-skia/gpu는 별도 feature입니다. WASM도 메모리/시간 budget과 worker 격리는 필요합니다. [공식 WASM 예제](https://github.com/edwardkim/rhwp/blob/v0.8.7/README.md), [feature 설정](https://github.com/edwardkim/rhwp/blob/v0.8.7/Cargo.toml)

MIT LICENSE의 저작권/허가 문구와 한컴 규격 참고 고지를 보존합니다. 포크를 하더라도 provenance·bounded decompression·DRM 거절 같은 경계를 삭제하지 않습니다. 배포 전 Cargo.lock의 전이 의존성과 선택 feature, 번들 폰트/이미지/샘플의 권리는 코드 MIT와 별도로 목록화합니다. 우선 parser sidecar에 필요한 코드만 사용하고 editor·폰트 asset 전체를 복제하지 않는 방식을 제안합니다. [rHWP MIT LICENSE](https://github.com/edwardkim/rhwp/blob/v0.8.7/LICENSE)

이 조사에서 rHWP는 HWP 계열의 재사용 후보입니다. 책 PDF를 읽는 엔진으로 확인한 것은 아니며, PDF 장문 안정성·OCR·챕터 추출은 별도 PDF backend 평가에 속합니다. HOP의 UI/adapter·플랫폼 통합 연구는 별도 담당 노트와 대조해야 합니다.

**hwp-hwpx-parser의 직접 소스 점검:** `models.py`의 표 모델은 `List[List[str]]`이며 row/col span 필드가 없습니다. Markdown/CSV 변환 시 셀 줄바꿈도 공백으로 평탄화합니다. 따라서 표가 출력된다는 사실과 병합·중첩 구조를 정본으로 보존한다는 사실은 구분해야 합니다. [모델 소스](https://github.com/KimDaehyeon6873/hwp-hwpx-parser/blob/main/src/hwp_hwpx_parser/models.py)

HWPX reader의 section 목록은 문자열 `sorted(section_files)`를 사용합니다. section이 11개 이상이면 숫자 순서와 문자열 순서가 달라질 수 있으므로 `section2`와 `section10` fixture를 필수로 넣어야 합니다. 이는 소스에서 도출한 **검증할 위험**이며 실제 변환 재현 결과는 아닙니다. 암호화 문서는 예외로 거절하는 경로가 있습니다. [HWPX reader 소스](https://github.com/KimDaehyeon6873/hwp-hwpx-parser/blob/main/src/hwp_hwpx_parser/hwpx.py)

HWP5 reader의 주요 section 순회는 `BodyText/SectionN`입니다. 현재 열람한 파일에서 `ViewText` 경로는 찾지 못했으므로 배포용 문서 지원을 추정하지 말고 별도 판본 시험을 해야 합니다. [HWP5 reader 소스](https://github.com/KimDaehyeon6873/hwp-hwpx-parser/blob/main/src/hwp_hwpx_parser/hwp5.py)

이 후보는 README와 pyproject에 Apache-2.0으로 선언되어 있으나 `main/LICENSE`의 저작권 허가 절이 표준 Apache 원문과 일부 다르게 작성되어 있습니다. 정확한 릴리스의 LICENSE/NOTICE 및 의존성을 대조한 뒤 배포 여부를 결정해야 합니다. 현재 pyproject에는 README에 서술한 olefile 외에 python-docx 의존성도 있습니다. [README](https://github.com/KimDaehyeon6873/hwp-hwpx-parser/blob/main/README.md), [pyproject](https://github.com/KimDaehyeon6873/hwp-hwpx-parser/blob/main/pyproject.toml), [LICENSE](https://github.com/KimDaehyeon6873/hwp-hwpx-parser/blob/main/LICENSE), [PyPI 릴리스](https://pypi.org/project/hwp-hwpx-parser/)

`pyhwp`의 기능 설명·experimental 표시는 개발자 README에서 확인했고, `hwp5txt` entry point 및 AGPLv3+ 표기는 setup.py에서 확인했습니다. 이 라이선스를 보조 프로세스라는 이유만으로 영향이 없다고 판단해서는 안 됩니다. [README](https://github.com/mete0r/pyhwp/blob/master/README), [setup.py](https://github.com/mete0r/pyhwp/blob/master/setup.py)

한컴 공식 HWPX 모델은 Apache-2.0입니다. README 빌드 예제는 VS2017/Windows10/x86이며 최신 도구 체인·x64 배포 검증은 하지 않았습니다. [공식 모델 README](https://github.com/hancom-io/hwpx-owpml-model/blob/main/README.md), [LICENSE](https://github.com/hancom-io/hwpx-owpml-model/blob/main/LICENSE.txt)

`rhwp`는 현재 README에서 HWP5/HWPX·표 병합·수식·각주 등을 설명하고, canonical 아키텍처 문서에는 HWP3 parser와 압축 해제 예산도 명시합니다. 이 주장은 해당 프로젝트의 문서 근거이며 이 앱에서 입증한 수치나 품질이 아닙니다. [README](https://github.com/edwardkim/rhwp/blob/main/README.md), [parser architecture](https://github.com/edwardkim/rhwp/blob/main/mydocs/tech/parser_architecture.md), [MIT LICENSE](https://github.com/edwardkim/rhwp/blob/main/LICENSE)

`hwplib`는 HWP5 공개 규격 기반이며 Apache-2.0이라고 명시합니다. README상 표 cell 조작·메모 모델·배포용 문서 읽기 변경 이력이 있으나 암호화 HWP 읽기/쓰기, 이미지/PDF/HTML 변환, 특정 페이지/전체 페이지 수는 지원하지 않는다고 설명합니다. [개발자 README](https://github.com/neolord0/hwplib/blob/main/README.md)

**LibreOffice를 모든 HWP의 대체 엔진으로 지정하지 마십시오.** 공식 filter 표는 `Hangul WP 97`을 나열하고, 공식 hwpfilter 문서는 더 새로운 판본을 제대로 처리하지 못해 조용한 손상을 일으킬 수 있다고 경고합니다. HWP3와 HWP5를 분리해 테스트해야 합니다. [filter 표](https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html), [hwpfilter 주의](https://docs.libreoffice.org/hwpfilter.html)

**Automation 조건:** 개인 비상업 이용 범위를 벗어난 상업 목적은 한컴 승인과 별도 라이선스가 필요하다고 공식 안내합니다. 파일 접근 보안 승인 메시지·보안 모듈 등록도 문서화되어 있습니다. 앱 설치가 있다는 사실만으로 무인 서비스 이용권까지 있다고 판단하지 않습니다. [한컴 Automation 공식 가이드](https://developer.hancom.com/hwpautomation)

한글2010이 설치된 환경이라도 현대 HWPX 전체 호환성은 별도 확인해야 합니다. 한컴은 한글2010 SE+부터 HWPX 필터 실행, 하위 호환성 대응은 2014VP부터 반영됐다고 안내합니다. 설치·COM·라이선스·변환 성능 확인은 이번 조사에서 하지 않았습니다. [한컴 HWPX FAQ](https://www.hancom.com/support/faqCenter/faq/detail/2784)

## 4. 비교·보완 도구의 정확한 역할

| 도구 | 확인된 역할 | 배포·한계 |
| --- | --- | --- |
| Docling | 공통 Document와 형식별 backend, JSON/Markdown 등 export | MIT 코드. 실제 pinned package와 선택한 모델/의존성 라이선스는 별도 목록 필요 |
| MarkItDown | 빠른 파일→Markdown 기준선, DOCX/PPTX/XLSX/XLS/HTML/EPUB/MSG 등 converter | MIT. 정본 구조 모델·복잡한 문서 무손실 보장 용도와는 차이 |
| Pandoc | 구조 markup 간 변환·AST·Markdown writer, DOCX/ODT/RTF/HTML/EPUB/JATS/LaTeX/ipynb 입력 | GPLv2+. Windows MSI/ZIP 제공. AST가 원본보다 단순해 손실 가능 |
| python-docx | DOCX 문단·표·comments·inline 그림에 직접 접근 | MIT. paragraph 목록만 읽으면 revision 내부 문단 등 누락 가능 |
| python-pptx | PPTX shape·그림·표·notes 객체 접근 | MIT. shape 순서는 기본적으로 z-order이며 의미상 읽기 순서가 아님 |
| openpyxl | XLSX 형식·셀·병합 범위·수식 문자열/캐시 접근 | MIT/Expat. 수식을 계산하지 않음. xls/ods reader로 사용하지 않음 |
| calamine | Rust 읽기 전용 XLS/XLSX/XLSM/XLSB/ODS reader | MIT. 값 중심 광범위 fallback 후보. 수식 계산·전체 스타일/그림/메모 충실도는 별도 검증 |
| Apache Tika | 광범위 형식 감지·본문/메타데이터·첨부 추출 fallback | Apache-2.0. 3.x는 Java11+ 필요. 구조화 Markdown 정본 엔진보다 발견·교차검증에 적합 |

라이선스 1차 근거: [Docling MIT](https://github.com/docling-project/docling/blob/main/LICENSE), [MarkItDown MIT](https://github.com/microsoft/markitdown/blob/main/LICENSE), [Pandoc COPYRIGHT](https://github.com/jgm/pandoc/blob/main/COPYRIGHT), [python-docx MIT](https://github.com/python-openxml/python-docx/blob/master/LICENSE), [python-pptx MIT](https://github.com/scanny/python-pptx/blob/master/LICENSE), [openpyxl 공식 문서](https://openpyxl.readthedocs.io/en/stable/), [calamine MIT](https://github.com/tafia/calamine/blob/master/LICENSE-MIT.md), [Tika 배포·라이선스](https://tika.apache.org/download.html). 이는 배포 설계에 필요한 확인 항목이며 법률 의견을 대신하지 않습니다.

### MarkItDown

공식 README는 LLM/text-analysis용 변환기이며 사람이 볼 고충실도 변환에 최선이 아닐 수 있다고 설명합니다. 실제 소스상 DOCX는 Mammoth→HTML→Markdown, PPTX는 python-pptx, XLSX는 pandas/openpyxl, XLS는 pandas/xlrd를 사용합니다. PowerPoint를 지원한다는 표현을 `.ppt` binary reader 지원으로 확대 해석하지 마십시오. [README](https://github.com/microsoft/markitdown), [DOCX converter](https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_docx_converter.py), [PPTX converter](https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_pptx_converter.py), [XLSX/XLS converter](https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_xlsx_converter.py)

PPTX converter는 top/left로 정렬하며 노트·그림/차트 경로도 있습니다. XLSX converter는 sheet DataFrame을 HTML 표로 만든 뒤 Markdown으로 바꾸므로 Excel 전체 계산·서식·병합 모델의 정본 보존과는 별도입니다. MSG converter의 현재 기본 출력은 From/To/Subject와 plain body 중심이므로 첨부·HTML·수신자 전체 정보를 별도로 확인해야 합니다. [MSG converter](https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_outlook_msg_converter.py)

### Pandoc의 최신 입력 형식 주의

현재 공식 manual/README에는 **PPTX와 XLSX도 input**에 있습니다. 과거의 “PPTX는 출력 전용” 설명을 그대로 쓰면 부정확합니다. 다만 두 reader 소스는 `Stability: alpha`로 표시되므로 비교 후보로 시험하고, 실제 배포할 바이너리에서 `--list-input-formats`로 확인해야 합니다. DOC/XLS/PPT binary와 HWP는 같은 입력 목록에서 확인되지 않았습니다. [최신 README 입력표](https://github.com/jgm/pandoc/blob/main/README.md), [PPTX reader](https://github.com/jgm/pandoc/blob/main/src/Text/Pandoc/Readers/Pptx.hs), [XLSX reader](https://github.com/jgm/pandoc/blob/main/src/Text/Pandoc/Readers/Xlsx.hs)

Pandoc은 구조 보존을 지향하지만 AST가 원본보다 덜 풍부해 완전 변환을 기대하지 말라고 명시합니다. DOCX `--track-changes=accept|reject|all`에 따라 변경·댓글 처리가 달라지고 기본 accept는 댓글을 무시합니다. `--extract-media`는 외부/로컬 리소스 접근도 가능하므로 로컬 전용 앱에서는 경계를 제한해야 합니다. `--sandbox`도 filter/PDF 생성의 모든 I/O를 막는 장치는 아닙니다. [공식 manual](https://pandoc.org/MANUAL.html)

Windows에서는 공식 MSI 외에 binaries와 문서를 담은 ZIP도 제공됩니다. Markdown 추출에 TeX 엔진 설치가 필수인 것은 아니며 PDF 생성 의존성과 구분합니다. [공식 설치 문서](https://pandoc.org/installing.html)

### 형식별 낮은 수준 API를 쓸 때

`python-docx.iter_inner_content()`는 문단/표 순회에 유용하지만 `paragraphs`에는 revision mark 내부 문단이 빠질 수 있고 `tables`는 최상위 표만 나열합니다. 재귀 문단/표/그림 순회가 필요하며 제목 수준도 font 크기만으로 추측하지 않는 편이 좋습니다. [python-docx Document API](https://python-docx.readthedocs.io/en/latest/api/document.html)

`python-pptx.shapes`는 첫 shape가 z-order 맨 뒤입니다. 병합 표에는 merge-origin과 spanned cell, span 크기 API가 있으므로 모든 grid cell 텍스트를 단순 중복 출력하지 않아야 합니다. [shape API](https://python-pptx.readthedocs.io/en/latest/api/shapes.html), [병합 표 공식 문서](https://python-pptx.readthedocs.io/en/latest/user/table.html)

`openpyxl`의 `data_only=True`는 마지막으로 저장된 수식 계산값을 읽는 설정이며 새로 계산하지 않습니다. 병합 후 anchor 외 셀은 값 None인 MergedCell이 될 수 있습니다. 병합 범위와 수식·캐시를 별도 보관하고 빈 셀과 결손 값을 구분하는 adapter가 필요합니다. [load_workbook](https://openpyxl.readthedocs.io/en/3.1/api/openpyxl.reader.excel.html), [수식](https://openpyxl.readthedocs.io/en/stable/simple_formulae.html), [병합 셀](https://openpyxl.readthedocs.io/en/stable/editing_worksheets.html)

`calamine`은 XLS/XLSX/XLSM/XLSB/ODS를 읽는 Rust 라이브러리이고 수식 읽기 예제가 있습니다. 지원 확장자와 모든 시각·논리 요소의 충실도는 동일하지 않습니다. [공식 저장소](https://github.com/tafia/calamine)

Tika는 OLE2/OOXML·ODF·EPUB·RTF·메일 등에서 텍스트·메타데이터를 추출합니다. 기본 generic XML parser는 XML 구조를 무시하고 텍스트를 추출하므로 JATS/HWPX 같은 구조 문서의 핵심 엔진으로 대체하면 안 됩니다. 탐지 형식 수와 실제 추출 지원 형식 수도 다르다고 명시합니다. [Tika 형식 설명](https://tika.apache.org/3.2.3/formats.html)

## 5. 표·수식·그림·검토 정보의 출력 계약 제안

Markdown pipe 표만으로 합쳐진 셀·표 안 문단·각주·중첩 표를 무손실로 나타내겠다고 약속하지 않습니다. 제안하는 병행 출력은 다음과 같습니다.

- **JSON 정본:** cell id, 시작 row/col, row_span/col_span, anchor, cell 내부 block 참조, header role, 원본 sheet/section/slide 주소를 보관합니다.
- **Markdown:** 단순 직사각형 표는 pipe 표, 복합 표는 안전하게 제한된 HTML 표 또는 별도 HTML 링크와 명확한 미리보기를 제공합니다.
- **HTML:** `rowspan/colspan`과 중첩 block을 보존하는 시각·구조 view입니다. script·event handler·외부 요청은 허용하지 않습니다.
- **CSV:** 분석용 2D projection입니다. 병합 셀을 anchor-only로 둘지 펼칠지 manifest에 명시하고, 원본 topology와 동등하다고 표시하지 않습니다.
- **assets:** 원본 그림 byte/hash와 원본 위치 참조, 렌더 preview·OCR/caption의 파생 여부를 구분합니다.

수식은 원본 OMML/MathML/HWP equation source와 파생 LaTeX를 구분하며, 계산식은 formula/cached_value를 구분합니다. 그림 OCR·VLM 설명은 원래 alt text와 다른 필드로 저장합니다. 댓글·발표자 노트·변경 추적·숨김 내용은 정본에는 상태를 보존하고 기본 Markdown 포함 여부는 명시적으로 정합니다. 이 절은 위에서 확인한 parser 경계에 대응하는 제품 설계 제안입니다.

## 6. 추가 형식과 보안 경계

Docling email backend는 `.msg`를 python-oxmsg로 읽어 RFC822 형태로 정규화하고 `.eml` 경로와 공유합니다. 본문/HTML/주소/첨부 이름 지원이 있다는 것과 첨부 문서가 재귀 변환된다는 것은 다른 기능입니다. [email backend 소스](https://github.com/docling-project/docling/blob/main/docling/backend/email_backend.py)

JATS는 단순 XML 텍스트가 아니라 학술논문 tagging 표준입니다. generic XML fallback보다 JATS 전용 reader를 우선하고 figure/table/xref/ref-list를 정본에 남기는 편이 적합합니다. [NLM JATS 표준 문서](https://jats.nlm.nih.gov/)

ipynb는 JSON에 cells·metadata·code source·저장된 rich output MIME bundle을 담습니다. 추출을 위해 코드를 재실행할 필요가 없으므로, 제품 기본 정책은 **셀 실행 금지, 저장된 결과만 추출**로 제안합니다. 임베디드 HTML/JavaScript 출력은 안전한 미리보기와 원본 payload를 구분해야 합니다. [Jupyter 공식 파일 형식](https://nbformat.readthedocs.io/en/latest/format_description.html)

마지막으로 “로컬에서 돈다”는 사실만으로 파일 접근·원격 링크·매크로·압축 폭탄 위험이 자동 차단되지는 않습니다. MarkItDown도 프로세스 권한으로 파일/URL을 읽으므로 좁은 `convert_local/convert_stream` 사용을 권고합니다. openpyxl은 기본 XML 공격 방어 한계를 명시합니다. [MarkItDown 보안 안내](https://github.com/microsoft/markitdown), [openpyxl 보안 안내](https://openpyxl.readthedocs.io/en/stable/)

구현 제안은 외부 네트워크가 차단된 worker, 최소 권한, 작업별 임시 디렉터리/프로필, 시간·메모리·압축 해제 용량·중첩 깊이 예산, 매크로/임베디드 실행 차단, 외부 링크 비활성, 원본 불변입니다. ZIP/CFB/XML/HTML 판별과 안전한 렌더를 함께 적용하며, 파일명·본문 로그에 민감정보를 남기지 않습니다. 인증 URL connector와 cloud parser는 이후 별도 사용자 선택·전송 이력·권한 설계에 둡니다.

## 7. 구현 전 최소 증거 세트

이 조사에서 변환 성공률·CER·표 정확도·속도·Windows 설치 성공은 측정하지 않았습니다. 다음 합성/공개 허가 fixture를 실제 pinned 엔진으로 확인한 뒤 지원 상태를 올리는 것이 적합합니다.

1. HWP5/HWPX 각각: 2방향 병합·중첩 표·표 안 각주/수식·그림 참조·메모·11개 section·한국어/옛한글·압축 flag·암호·배포용 문서·손상 컨테이너.
2. HWP3와 HML: HWP5와 다른 detector 및 전용 시험. 인식만 되는 파일과 구조 추출되는 파일을 다른 지원 상태로 표시.
3. DOCX: 문단↔표 교차 순서, revision, nested/merged table, OMML, foot/endnote, comment anchor, floating textbox.
4. PPTX: 실제 읽기 순서 정답이 있는 2열/그룹 shape, 병합 표, OMML/MathType, notes/comment/hidden content.
5. XLSX/XLS/ODS/CSV: 병합·멀티시트·빈 영역·수식/캐시 없음·날짜체계·앞자리 0·긴 ID·한글/CP949·줄바꿈/quote·threaded comments.
6. 공통: 원본 hash 불변, partial/failed 누락 경고, 정본 JSON→Markdown revision 동기화, 자식 프로세스 종료, 네트워크 0 요청, 악성 ZIP/XML/HTML 안전 거절.

지원 상태는 `검증됨`, `제한 지원`, `시험 중`, `추가 엔진 필요`, `암호/권한 필요`, `실패`처럼 사용자가 구별할 수 있어야 합니다. 파서가 빈 Markdown을 반환했다는 이유로 성공으로 집계하지 않습니다.
