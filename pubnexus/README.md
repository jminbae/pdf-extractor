# PubNexus — 의학 논문 PDF RAG 파이프라인

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
                              (5)청킹 → 임베딩 → 하이브리드 검색 → 리랭커
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

## 핵심 설계

- **인용번호 분리**: `치료 효과가 있었다.15,16` 의 `15,16` 을 본문에서 제거하되
  버리지 않고 문단 메타 `cited_refs` 로 옮긴다. 본문은 깨끗해져 임베딩 품질↑,
  "이 주장의 근거 논문" 을 문장 단위로 역추적 가능.
- **JSON이 정본, Markdown은 뷰**: 구조(section path·문단 경계·figure ref)는
  JSON에만 보존된다. Markdown → 구조 복원은 불가능.
- **범용 배포**: 모든 경로·모델·엔드포인트는 `config.yaml`. 사용자는 사양에 맞는
  임베딩 모델(0.6B/4B/8B, 또는 클라우드)을 선택.

## 실행

```bash
pip install -r requirements.txt

# GROBID 서비스 (비-PMC 논문 처리에 필요)
#   프로덕션: docker run --rm -p 8070:8070 lfoppiano/grobid:latest-full
#   config.yaml 의 grobid.url 을 http://localhost:8070 으로
python run_pilot.py               # 0~4단계 전체
python run_pilot.py --skip-grobid # XML 경로만 (GROBID 없이)
```

## 산출물 (`data/`)

```
manifest.jsonl        # 0단계 원장
meta/{doi}.json       # 1단계 API 메타데이터
xml/{pmcid}.xml       # 원문 JATS 캐시
tei/{doi}.tei.xml     # GROBID TEI 캐시
normalized/{doi}.json # 정본 문서 (RAG 입력)
markdown/{doi}.md     # 사람이 읽는 뷰
qc_report.jsonl       # 4단계 품질 리포트
```

## GROBID 런타임 결정

범용 배포 기준 **Docker 권장**(모든 사용자 PC에서 동일 버전·동작 재현). `grobid.url`
설정으로 로컬 Docker / Java 서비스 / 원격 서버를 자유롭게 지정. 파일럿 개발 환경에서는
포터블 JDK 17 + 소스 빌드(`./gradlew run`)로 구동.
