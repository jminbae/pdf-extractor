# PubNexus — 의학 논문 PDF RAG 파이프라인

> 이 문서는 **파이프라인의 구조**를 설명한다. 무엇을 왜 만드는지, 무엇이 되면
> 완성인지는 저장소 루트의 **[PRD.md](../PRD.md)** 에 있다. 거기서 시작하라.

born-digital 의학 논문 PDF를 **정확하게 구조화**하여 RAG 데이터베이스로 만드는 파이프라인.
설계 원칙: **PDF를 열기 전에 원문 XML·메타데이터를 API로 확보**하고, 남은 것만 학술 전용
파서(GROBID)로 처리한다. 클라우드 API 비용 0, 전 과정 로컬·오픈소스.

## 파이프라인

```
PDF → [0]인벤토리 → [1]메타데이터 API → 분기 ┬ PMCID有 → [2a]PMC JATS XML
                                            └ PMCID無 → [2b]GROBID TEI
                                                    ↓
                              [3]정규화 JSON(정본) → [4]QC 게이트 → Markdown 뷰
                                                    ↓
                              [4.5]정본 수리(textfix) → [5]청킹
                                                    ↓
                              [6]임베딩·인덱스 → [7]하이브리드 검색 → 리랭커
```

| 단계 | 모듈 | 하는 일 | AI 모델 | 비용 |
|---|---|---|---|---|
| 0 | `inventory.py` | DOI/텍스트층/쪽수/중복 판정 → manifest | ✗ | 무료 |
| 1 | `metadata.py` | PubMed·iCite·OpenAlex·Crossref (초록/MeSH/RCR/ref) | ✗ | 무료 |
| 2a | `pmc_xml.py` + `jats.py` | PMC 원문 XML → 섹션/인용/표/그림 | ✗ | 무료 |
| 2b | `grobid_client.py` | GROBID TEI → 인용 태그 분리 | ✅ CRF(로컬) | 무료 |
| 3 | `schema.py` | 두 경로를 단일 정본 스키마로 병합 | ✗ | 무료 |
| 4 | `qc.py` | 초록/참조수/깨짐 검사 → 품질점수 | ✗ | 무료 |
| 뷰 | `render.py` | 정본 JSON → Markdown | ✗ | 무료 |
| 4.5 | `textfix.py` | 자간 아티팩트·러닝헤더·캡션누수 수리, 섹션 타입 재분류 | ✗ | 무료 |
| 4.6 | `recover.py` · `symfont.py` | 버려진 문단 앞부분 복원, 기호 글꼴 글자 복원 | ✗ | 무료 |
| 4.7 | `captions.py` · `tablefill.py` | 캡션을 좌표·글꼴로 확정, 표를 PDF 괘선으로 재구성 | ✗ | 무료 |
| 4.8 | `figclip.py` | **그림을 실제 이미지로** — 내장 이미지는 원본 그대로, 벡터는 렌더 | ✗ | 무료 |
| 참조 | `refmatch.py` | 번호·순서는 지면에서, 내용은 iCite 에서 → 짝짓기 | ✗ | 무료 |
| 5 | `chunk.py` | 섹션 경계 지키며 분할 + 컨텍스트 헤더 부착 | ✗ | 무료 |
| 6 | `embed.py` + `index.py` | 임베딩 → 벡터 인덱스 + BM25 인덱스 | ✅ Qwen3-Embedding-0.6B(로컬) | 무료 |
| 7 | `search.py` | BM25+dense RRF 병합 → 리랭커 → top-k | ✅ Qwen3-Reranker-0.6B(로컬) | 무료 |

## 핵심 설계

- **인용번호 분리**: `치료 효과가 있었다.15,16` 의 `15,16` 을 본문에서 제거하되
  버리지 않고 문단 메타 `cited_refs` 로 옮긴다. 본문은 깨끗해져 임베딩 품질↑,
  "이 주장의 근거 논문" 을 문장 단위로 역추적 가능.
- **JSON이 정본, Markdown은 뷰**: 구조(section path·문단 경계·figure ref)는
  JSON에만 보존된다. Markdown → 구조 복원은 불가능.
- **범용 배포**: 모든 경로·모델·엔드포인트는 `config.yaml`. 사용자는 사양에 맞는
  임베딩 모델(0.6B/4B/8B, 또는 클라우드)을 선택.
- **하이브리드 검색은 선택이 아니라 필수**: `NB-UVB`·`F-VASI`·`JAK inhibitor` 같은
  약어·측정도구명은 dense 임베딩이 자주 놓친다. BM25 결과를 RRF로 병합해 보완한다.

## 실행

```bash
pip install -r requirements.txt

# GROBID 서비스 (비-PMC 논문 처리에 필요)
#   프로덕션: docker run --rm -p 8070:8070 lfoppiano/grobid:latest-full
#   config.yaml 의 grobid.url 을 http://localhost:8070 으로
python run_pilot.py               # 0~4단계 전체
python run_pilot.py --skip-grobid # XML 경로만 (GROBID 없이)

# 4.5~6단계: 정본 수리 → 청킹 → 임베딩 → 인덱스 (모델은 최초 1회 자동 다운로드)
python run_rag.py                 # 전체
python run_rag.py --chunk-only    # 수리+청킹까지만 (모델 불필요, 수초)
python run_rag.py --textfix-dry-run  # 수리 내역만 미리보기 (파일 안 씀)
python run_rag.py --backend hash --vectordb flat   # 모델 없이 구조만 검증
#   ↑ 로 만든 인덱스를 질의하려면 config.yaml 의 embedding.backend 도 hash 여야 한다
#     (ask.py 에는 백엔드 옵션이 없다 — 질의는 인덱스와 같은 인코더로 벡터화해야 하므로)

# 7단계: 질의
python ask.py "백반증 NB-UVB 재색소침착률" -k 8
python ask.py "survival analysis" --section methods --year 2018-2025
python ask.py "VASI" --kind table --json
python evaluate.py --compare      # 골든 세트로 검색 품질 측정
```

임베딩·리랭커에는 GPU(권장 VRAM 6GB↑)와 `sentence-transformers`·`torch` 설치가 필요하다.
설치 절차는 `전달용/PubNexus_인수인계_실행가이드.md` 참고.

## 산출물 (`data/`)

```
manifest.jsonl        # 0단계 원장
meta/{doi}.json       # 1단계 API 메타데이터
xml/{pmcid}.xml       # 원문 JATS 캐시
tei/{doi}.tei.xml     # GROBID TEI 캐시
normalized/{doi}.json # 정본 문서 (RAG 입력)
normalized_raw/       # 4.5단계 수리 전 원본 백업 (되돌리기용)
textfix_report.jsonl  # 4.5단계 문서별 수리 내역
markdown/{doi}.md     # 사람이 읽는 뷰
qc_report.jsonl       # 4단계 품질 리포트
audit_report.jsonl    # 내용 수준 전수 감사
chunks.jsonl          # 5단계 청크 원장 (RAG 검색 단위)
lancedb/              # 6단계 벡터 인덱스 (vectordb.backend: lancedb)
vectors.npz           #   〃              (vectordb.backend: flat, numpy 브루트포스)
bm25_index.json.gz    # 6단계 BM25 인덱스 (순수 파이썬, gzip+json)
```

## GROBID 런타임 (2026-07-26 확정)

**윈도우 네이티브로 돈다. Docker 는 필요 없다.** 다만 공식 배포본 그대로는 안 되고
소스를 세 군데 고쳐 빌드해야 한다.

윈도우에 번들된 `pdfalto` 는 0.1 이라(리눅스·맥은 0.5/0.6, kermitt2 가 윈도우
바이너리를 갱신하지 않는다) GROBID 가 넘기는 `-noLineNumbers`·`-onlyGraphsCoord`
를 모른다. 그래서 변환이 시작도 못 하고 죽어 늘 이렇게 끝났다:

```
[NO_BLOCKS] PDF parsing resulted in empty content
```

`-blocks` 로 바꾸면 된다. 재현은 스크립트 하나로 끝난다.

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_grobid_windows.ps1   # 빌드
powershell -ExecutionPolicy Bypass -File tools\package_grobid_runtime.ps1 # 배포용 묶기
```

> ⚠ "GROBID 파서가 `<BLOCK>`/`<TOKEN>` 커스텀 포맷을 기대하므로 윈도우는 불가능"
> 이라는 옛 기록은 **틀렸다**. 0.9.0 의 `PDFALTOSaxHandler` 는 표준 ALTO
> (`Page`·`TextBlock`·`TextLine`·`String`)를 읽는다 — pdfalto 0.1 이 내는 그 형식이다.
> 문제는 포맷이 아니라 인자였다. 같은 결론으로 돌아가지 말 것.

**앱은 엔진을 스스로 찾고, 없으면 설치한다.** `grobid_service.py` 가
`GROBID_ROOT` 환경변수 → 앱 저장소 → `C:\grobid` → 다른 드라이브 → exe 옆 순으로
찾고, 어디에도 없으면 GitHub Releases 에서 내려받아 `%LOCALAPPDATA%` 에 푼다
(관리자 권한 불필요, 최초 1회 약 435MB·82초). `GROBID_ROOT` 를 지정하면 **그 자리만**
본다. `config.yaml` 의 `grobid.url` 로 원격 서버를 가리킬 수도 있다.

실측: 영어 논문 6/6 성공. 다만 **윈도우는 리눅스보다 표 검출이 약하다**
(같은 134편: TEI 표 231 → 93). 표가 특히 중요하면 GROBID 를 리눅스에 두고
`grobid.url` 로 가리키는 선택지가 남아 있다.

## 데스크탑 앱 (PDF Extractor)

`app.py` — pywebview + WebView2 3분할 검수 화면(1:2:2). 왼쪽 파일목록 / 가운데
PDF 원본 / 오른쪽 추출 결과.

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

`--onedir` 로 묶은 뒤 Inno Setup 설치프로그램(`tools\installer.iss`)으로 만든다.
`--onefile` 은 실행할 때 자기 안을 임시폴더에 풀어 돌리는데, 그 동작 때문에
Defender 가 `Trojan:Win32/Wacatac.H!ml` 로 잡아 **파일을 지운다**(오탐).

배포: https://github.com/jminbae/pdf-extractor/releases

화면 쪽 규약(인용·그림 팝업, 이미지 크기 지정이 왜 필수인지 등)은
`전달용/ResearchMap_이식_가이드.md` 4-B장에 정리돼 있다.
