# HOP 원천 기술 조사 — HWP/HWPX 수집 어댑터와 Windows 검수 화면

조사일: 2026-10-23. 범위는 공식 저장소·릴리즈·설정·소스의 읽기 전용 조사입니다. 설치, 빌드, 코드 실행, 사용자 문서 열기·변환, 성능 측정은 하지 않았습니다. 이 문서는 구현 계획의 근거이며 기능 수용 시험 결과가 아닙니다.

## 판단

사용자가 말씀하신 HWP 관련 HOP는 **[golbin/hop](https://github.com/golbin/hop)**로 식별됩니다. 공식 README가 “HOP is Open HWP”와 HWP/HWPX 데스크톱 앱임을 명시하고, 파싱·렌더링은 `edwardkim/rhwp`에 맡긴다고 설명합니다. 동명 프로젝트를 추측해 연결한 결과가 아닙니다. [README](https://github.com/golbin/hop/blob/v0.4.5/README.md)

**AI ingest/wiki용 수집 앱에서는 rhwp를 추출 엔진 후보로 평가하고, HOP는 Windows 파일 열기·미리보기·세션·업스트림 관리의 참고 구현으로 사용하는 구성이 적절합니다.** 이는 확인된 역할 경계에 따른 설계 제안입니다. HOP GUI 자체를 Markdown 추출 엔진으로 간주하거나, HWP를 HOP에서 PDF로 바꾼 뒤 다시 추출하는 경로를 기본으로 삼을 근거는 없습니다. HOP는 native 제품 셸을 소유하고 엔진은 어댑터를 통해 참조하도록 구성되어 있습니다. [업스트림 경계](https://github.com/golbin/hop/blob/v0.4.5/docs/architecture/UPSTREAM.md), [Rust adapter](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/rhwp-adapter/src/lib.rs)

## 확인 기준선

| 항목 | 확인값 | 1차출처 |
|---|---|---|
| HOP 안정 릴리즈 | 조사 시점 `releases/latest`는 `v0.4.5`, 2026-10-08 공개 | [공식 릴리즈](https://github.com/golbin/hop/releases/tag/v0.4.5) |
| HOP 릴리즈 커밋 | `34bd15c97e0851627cb7f055042c5340198aeb7f` | [태그가 가리키는 커밋](https://github.com/golbin/hop/commit/34bd15c97e0851627cb7f055042c5340198aeb7f) |
| 엔진 source | `https://github.com/edwardkim/rhwp` | [고정 설정](https://github.com/golbin/hop/blob/v0.4.5/config/rhwp-upstream.json) |
| 고정 엔진 | `rhwp v0.8.7`, `1a76570e833917d15817415a53c09ad61ab3203f` | [고정 설정](https://github.com/golbin/hop/blob/v0.4.5/config/rhwp-upstream.json) |
| 도구 기준선 | Rust `1.93.1`, wasm-pack `0.14.0`; 앱 루트는 pnpm `10.33.0` | [엔진 설정](https://github.com/golbin/hop/blob/v0.4.5/config/rhwp-upstream.json), [package.json](https://github.com/golbin/hop/blob/v0.4.5/package.json) |

아래 HOP 소스 링크는 움직이는 `main` 대신 `v0.4.5`를 사용합니다. `main`과 릴리즈의 차이 또는 향후 업데이트를 이번 문서의 검증 결과로 포함하지 않습니다.

## HOP 자체 기능과 엔진 기능의 구분

| 관심 기능 | 공식 코드·문서에서 확인한 범위 | 수집 앱에 대한 의미 |
|---|---|---|
| HWP/HWPX 입력 | UI 경로 필터와 Rust 경로 판정이 `.hwp`, `.hwpx`를 받음 | 확장자 외 실제 파서 판정·오류는 엔진 어댑터가 담당해야 함 |
| 저장 | HWP 저장·다른 이름으로 저장; HWPX 원본 저장은 안전성 이유로 거부 | 읽기 지원을 같은 형식의 안전한 재저장 보장으로 확대하면 안 됨 |
| PDF 출력 | upstream SVG→PDF 프로토콜 사용; 검색 가능한 텍스트 임베딩 옵션 설정 | 검수용 미리보기 참고 가능. 원래 표 구조를 추출하는 API와 별개 |
| 표·병합셀 | HOP 자체의 별도 표 추출기가 아님. rhwp IR은 셀 좌표·row/col span·셀 내 문단을 모델링 | ingest에는 엔진의 구조 결과를 보존하고 Markdown만 정본으로 삼지 않는 편이 유리 |
| 수식 | 엔진과 편집기·렌더러를 상속하는 구조 | 수식을 화면에 표시하는 능력과 정확한 LaTeX/Markdown 추출은 별도 시험 대상 |
| Markdown | 확인한 HOP 파일 명령 표면에는 HWP 저장·PDF export가 있으며 Markdown export는 확인하지 못함 | rhwp CLI/IR의 추출 API를 직접 감싸는 별도 어댑터가 필요 |
| PDF/Word/PPT 입력 | HOP의 파일 필터는 HWP/HWPX에 한정 | 책 PDF 안정성을 해결하는 범용 파서 후보로 세지 않음 |

근거: [입력 필터](https://github.com/golbin/hop/blob/v0.4.5/apps/studio-host/src/core/document-files.ts), [TauriBridge](https://github.com/golbin/hop/blob/v0.4.5/apps/studio-host/src/core/tauri-bridge.ts), [Rust 명령 등록](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/src-tauri/src/lib.rs), [PDF adapter](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/rhwp-adapter/src/lib.rs), [rhwp 표 모델](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/model/table.rs).

`query_document`라는 이름만 보고 문서 전체 JSON 추출 API라고 이해하면 안 됩니다. HOP의 해당 Rust 분기는 `documentInfo`, `pageCount`, `pageInfo`, `pageDef`, `cursorRect`, `hitTest`, `paragraphLength`, `paragraphCount`입니다. 추출 표·전체 읽기 순서·수식 AST를 반환하는 범용 ingest 계약은 이 표면에서 확인하지 못했습니다. [state.rs](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/src-tauri/src/state.rs)

## 가져올 수 있는 구체적 기술

| 소유 모듈 | 확인 내용 | 이번 앱의 재사용 방향 |
|---|---|---|
| `apps/desktop/rhwp-adapter` | upstream Rust 직접 import를 한 crate로 모으고 `DocumentCore`·thumbnail·PDF 경계를 노출 | 형식별 엔진 교체점을 어댑터 하나에 고립하는 패턴 참고. HOP 전체를 이식할 필요는 없음 |
| `apps/studio-host/src/upstream/core.ts` | upstream TS import를 명시적 port로 재노출 | 기존 Python 앱과 Rust/Node sidecar를 연결할 때 공개 계약만 사용 |
| `apps/desktop/src-tauri/src/state.rs` | 문서별 session/revision, 외부 변경 확인, 저장 바이트 검증, staging 후 저장 | 읽기 전용 ingest에서는 원본을 수정하지 않고 별도 결과만 저장. 향후 편집 기능 추가 시 안전한 파일 경계 참고 |
| `apps/studio-host/src/core/document-open.ts` | 암호 입력·재시도·취소를 명시적으로 처리; DRM 오류를 별도 취급 | 암호 요구·미지원 보호·손상 파일을 일반 추출 실패와 구별 |
| `apps/studio-host/src/core/chunked-fs.ts` | 4MiB 단위 파일 I/O와 크기 검증 | 대량 IPC 한 번에 전달하는 문제를 줄이는 참고. 전체 메모리 상한 보장은 별도 |
| `config/rhwp-upstream.json`와 `scripts/verify-rhwp-upstream.mjs` | source/tag/commit/toolchain·WASM provenance·artifact hash·Cargo baseline 정합 검사 | 수집 결과에 실제 엔진 버전·설정·해시를 기록하고 업데이트 시 회귀 검수하는 근거 |

근거: [Rust adapter](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/rhwp-adapter/src/lib.rs), [TS port](https://github.com/golbin/hop/blob/v0.4.5/apps/studio-host/src/upstream/core.ts), [세션·저장 상태](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/src-tauri/src/state.rs), [암호 열기](https://github.com/golbin/hop/blob/v0.4.5/apps/studio-host/src/core/document-open.ts), [chunked I/O](https://github.com/golbin/hop/blob/v0.4.5/apps/studio-host/src/core/chunked-fs.ts), [기준선 검증](https://github.com/golbin/hop/blob/v0.4.5/scripts/verify-rhwp-upstream.mjs).

### 실제 의존성

HOP의 native 코드는 `hop-rhwp-adapter → third_party/rhwp` path dependency입니다. 데스크톱은 Tauri 2 및 dialog/fs/log/single-instance/store/updater/window-state plugin, serde/json, uuid, tempfile 등을 사용합니다. PDF 경로에는 upstream의 vendored `svg2pdf` patch를 적용합니다. [adapter Cargo](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/rhwp-adapter/Cargo.toml), [desktop Cargo](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/src-tauri/Cargo.toml)

웹 부분은 rhwp-studio 위 HOP overlay이며 Vite·TypeScript를 사용합니다. HOP가 모든 브라우저 기능을 자동 상속하는 것은 아니며, 공개 API를 우선 사용하고 예외 override는 manifest와 counterpart hash로 관리합니다. [studio package](https://github.com/golbin/hop/blob/v0.4.5/apps/studio-host/package.json), [경계 문서](https://github.com/golbin/hop/blob/v0.4.5/docs/architecture/UPSTREAM.md), [override manifest](https://github.com/golbin/hop/blob/v0.4.5/config/rhwp-studio-overrides.json)

## Markdown·AI ingest에서 놓치면 안 되는 한계

HOP의 고정 엔진 rhwp에는 `export-markdown`과 `export-tables`가 있습니다. 전자는 페이지별 Markdown 파일 출력이며 `--json`은 **문서 구조 JSON이 아니라 산출물 manifest**입니다. 공식 매뉴얼은 평문·Markdown에서 병합 의미가 사라질 수 있다고 명시하고, 기계 소비에는 앵커 셀과 `rowSpan`/`colSpan`을 가진 표 JSON을 제공합니다. 자동번호 셀은 IR 텍스트에 없어 빈 값이 될 수 있습니다. [rhwp CLI 계약](https://github.com/edwardkim/rhwp/blob/v0.8.7/mydocs/manual/cli_commands.md#export-markdown-파일-옵션)

따라서 제안은 **구조 JSON·표 격자·자산을 우선 보존한 뒤 Markdown을 생성**하는 것입니다. 병합표는 HTML table 또는 span을 잃지 않는 별도 표현을 사용하고, 화면상 비어 보이는 셀을 실제 빈 셀로 단정하지 않는 검수 항목을 둡니다. 이는 우리 앱이 구현·검증할 정책이며 HOP가 이미 제공한다고 주장하지 않습니다.

수식 추출에 직접 참고할 upstream API도 있습니다. `rhwp::doclang::eqedit::convert_with_degraded(script)`는 EqEdit script를 lexer→AST→LaTeX로 바꾸고 `EqOutcome { latex, degraded }`를 반환합니다. 알 수 없는 명령은 텍스트 등으로 통과시킬 수 있으므로 성공 응답에도 의미 손실이 있을 수 있습니다. `convert()`는 손실 목록을 버리는 편의 함수입니다. ingest 어댑터는 원 script와 변환 LaTeX를 함께 보존하고 `degraded` 및 변환 오류를 보고하는 쪽을 검토합니다. HOP의 현재 Markdown export 기능을 확인한 것은 아닙니다. [EqEdit 변환 소스](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/doclang/eqedit/mod.rs)

rhwp의 RAG 모듈도 본문 문단별 오프셋 부재, 문단과 표의 정확한 interleave 미구현, 물리 페이지 번호 부재, 토큰 수 추정치라는 한계를 직접 적고 있습니다. 즉 구조 인지 청크 제공을 그대로 정확한 원문 순서·쪽수 인용 보장으로 받아들이면 안 됩니다. [rhwp RAG 모듈](https://github.com/edwardkim/rhwp/blob/v0.8.7/src/rag/mod.rs)

대형 문서는 추가 검수 대상입니다. HOP의 chunked read는 알려진 파일 크기로 전체 `Uint8Array`를 할당하거나 읽은 chunk들을 마지막에 하나로 합칩니다. **4MiB chunk라는 이유로 메모리가 4MiB에 제한되는 구조가 아닙니다.** [chunked-fs.ts](https://github.com/golbin/hop/blob/v0.4.5/apps/studio-host/src/core/chunked-fs.ts)

## Windows 적용 계획

공식 배포·CI에는 Windows x64 MSI와 `x86_64-pc-windows-msvc` 대상이 있습니다. 이는 Windows 제품화 경로가 있다는 근거이며, 이 PC에서 설치·렌더·변환을 검증했다는 의미가 아닙니다. [배포 운영 문서](https://github.com/golbin/hop/blob/v0.4.5/docs/operations/DESKTOP_RELEASE.md), [릴리즈 workflow](https://github.com/golbin/hop/blob/v0.4.5/.github/workflows/hop-desktop.yml)

적용 우선순위는 다음과 같습니다.

1. **추출 sidecar 후보:** 고정 rhwp native API/CLI를 읽기 전용 어댑터로 감싸 우리 공통 문서 모델로 변환합니다. HOP 창을 띄워 메뉴를 누르는 자동화를 수집 엔진의 기본으로 사용하지 않습니다.
2. **검수 프리뷰 후보:** HOP의 `DocumentCore`/페이지 SVG·thumbnail 경계와 upstream port 패턴을 참고해 원본 보기만 별도 연결합니다. 추출 결과와 프리뷰가 같은 엔진을 공유하더라도 둘이 서로 독립적인 정답 증인은 아닙니다.
3. **업데이트 회귀:** source/tag/commit 고정, WASM/native 정합 검사, 병합·중첩·각주·수식·암호·손상·대문서 fixture를 통과한 버전만 승격합니다.

구체적 형식별 어댑터·문서 모델·시험 순서는 [통합 계획](2026-10-23-document-markdown-plan.md), rhwp 파서·모델·추출 API 근거는 [Office/HWP 조사](2026-10-23-office-hwp-sources.md)와 함께 사용합니다. 이 HOP 조사가 책 PDF의 정확도·메모리·재개 안정성을 검증하는 것은 아닙니다.

## 라이선스와 도입 경계

HOP 저장소의 `LICENSE`와 package/Cargo manifest는 MIT를 선언합니다. 실제 소스 재사용 시 해당 저작권·허가 고지를 포함해야 합니다. 이 선언을 vendored 렌더러·폰트·모든 transitive dependency에 대한 일괄 라이선스 판정으로 확대하지 않습니다. [HOP LICENSE](https://github.com/golbin/hop/blob/v0.4.5/LICENSE), [desktop Cargo](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/src-tauri/Cargo.toml)

번들 설정은 Noto Sans KR font와 OFL 고지 파일을 리소스로 포함합니다. 수집만 하는 sidecar와 편집기·PDF 렌더러 전체 배포는 필요한 의존성·자산 범위가 다르므로 실제 포함 목록으로 검토해야 합니다. [Tauri bundle 설정](https://github.com/golbin/hop/blob/v0.4.5/apps/desktop/src-tauri/tauri.conf.json)

## 다음 검증에서 남길 증거

- HWP/HWPX별 지원·미지원 보호 형식과 실패 원인이 보고서에 보이는지.
- 표 수, 셀 좌표, 병합 span, 중첩 깊이, 각주·수식·그림 누락을 원문과 비교했는지.
- Markdown 파일 생성 성공과 자산·표 완전성을 별도 판정하는지.
- 원본 문단·표의 순서, 인용 주소, 물리 쪽수의 실제 근거와 추정치를 구별하는지.
- 큰 문서에서 최대 RSS, 처리 시간, 취소 반응, 프로세스 종료 후 재개를 측정했는지.
- 엔진 버전·commit·설정·원본 해시·부분 성공 경고가 결과 묶음에 남는지.

위 항목은 후속 수용 시험 계획입니다. 이번에는 한 항목도 실행 검증했다고 주장하지 않습니다.
