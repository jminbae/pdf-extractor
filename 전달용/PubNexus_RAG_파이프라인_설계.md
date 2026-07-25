# PubNexus — 의학 논문 PDF 3만 편 RAG 파이프라인 설계

> 로컬 하드의 born-digital 의학 논문 PDF를 정확하게 구조화하여 RAG 데이터베이스로 구축하기 위한 아키텍처 제안.
> 전제: 스캔본 PDF는 1차 범위에서 제외, 로컬 처리 우선(클라우드 API 비용 0), Qwen 계열 모델 사용.

---

## 요약

**3만 개를 전부 PDF 파싱하려는 것 자체가 잘못된 출발점입니다.**

S2ORC(3,000만 편), CORE(3,400만 편), Semantic Scholar 등 대규모 학술 코퍼스를 실제로 구축한 곳들이 쓰는 방식은 일관됩니다:

1. PDF를 파싱하지 **않아도 되는** 것을 먼저 걸러낸다 (PMC 원문 XML)
2. 남은 것만 **학술 문서 전용** 파서(GROBID)로 처리한다
3. 메타데이터는 파싱하지 말고 **API로** 가져온다
4. 파서가 약한 부분(표·수식)만 **선택적으로** 2차 처리한다

이 순서를 지키면 비용은 0원, 품질은 범용 PDF→Markdown 변환기 대비 압도적으로 높아집니다.

### 파이프라인 개요

```
                        PDF 3만 편
                             │
                     ┌───────▼────────┐
                     │  식별 단계      │
                     │ DOI/PMID/PMCID │
                     └───┬───────┬────┘
                         │       │
         PMCID 있음 ◄────┘       └────► PMCID 없음
              │                              │
    ┌─────────▼──────────┐        ┌──────────▼──────────┐
    │  PMC 원문 JATS XML │        │  GROBID → TEI XML   │
    │  파싱 오류 원리적 0 │        │  인용 마커 태그됨    │
    └─────────┬──────────┘        └──────────┬──────────┘
              │                              │
              │                   ┌──────────▼──────────┐
              │                   │  MinerU 보강         │
              │                   │  표·수식만 재처리     │
              │                   └──────────┬──────────┘
              │                              │
    ┌─────────▼──────────┐                   │
    │  메타데이터 API     │                   │
    │ PubMed/iCite/      │                   │
    │ OpenAlex/Crossref  │                   │
    └─────────┬──────────┘                   │
              │                              │
              └──────────┬───────────────────┘
                         │
              ┌──────────▼──────────┐
              │   정규화 JSON        │
              │   (단일 정본 스키마)  │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  청킹 → 임베딩 → 검색 │
              └─────────────────────┘
```

---

## 1단계. PDF를 열기 전에 — 원문 XML 우선 확보

### 왜

의학 논문의 상당수는 PMC Open Access / Author Manuscript 서브셋에 **JATS XML 원문**이 공개되어 있습니다. Europe PMC는 4,200만 편 이상의 초록과 900만 편 이상의 전문을 API·FTP 벌크 다운로드로 제공합니다.

XML은 출판사가 직접 만든 정본입니다. 따라서:

- 파싱 오류가 **원리적으로 0**
- 섹션 구조, figure/table 캡션, 인라인 인용 마커가 전부 태그로 분리되어 있음
- **걱정하시던 두 문제(인용번호 오탐, figure/table)가 이 경로에서는 애초에 발생하지 않음**

### 식별 파이프라인

| 순서 | 방법 | 비고 |
|---|---|---|
| 1 | PDF 메타데이터 + 1페이지 텍스트에서 DOI 정규식 추출 | `10\.\d{4,9}/[-._;()/:\w]+` |
| 2 | 실패 시 제목 추출 → Crossref `/works?query.bibliographic=` 퍼지 매칭 | score 임계값 설정 |
| 3 | DOI → PMID/PMCID 변환 | NCBI ID Converter API, 배치 200건 |
| 4 | PMCID 있으면 Europe PMC `fullTextXML` 호출 | 성공 시 **PDF 미사용** |

**예상 커버리지**: 피부과·백반증 분야면 체감상 30~50%. 이것만으로 전체 작업량이 절반으로 줄고, 그 절반은 최고 품질입니다.

### 주의점

- PMC XML도 완벽하진 않습니다. 섹션 태그가 임의 깊이로 중첩되고, 수식·표가 텍스트 덩어리로 나오며, 위첨자 인용이 단어 끝에 붙는 경우가 있습니다. `tidypmc`(R) 같은 기존 파서 로직을 참고해 후처리하세요.
- Rate limit 준수 (Europe PMC, NCBI 모두 초당 요청 제한 있음). API 키 등록 시 완화됩니다.

---

## 2단계. 남은 born-digital PDF → GROBID

### 왜 GROBID인가 (인용번호 문제의 유일한 정답)

> `치료 효과가 있었다.15,16` 의 `15,16`

이것이 선생님이 지적하신 핵심 문제입니다. 그리고 **이걸 구조적으로 해결하는 도구는 사실상 GROBID 하나뿐입니다.**

- **Marker / MinerU / Docling**: "레이아웃 → Markdown" 범용 변환기. 인용번호를 그냥 텍스트로 흘려보냄.
- **GROBID**: 학술 문서 전용 CRF/딥러닝 캐스케이드. 인라인 인용을 `<ref type="bibr" target="#b14">15</ref>` 로 **태그**함 → 제거하거나 메타데이터로 옮기는 것이 선택 가능.

GROBID는 완전한 PDF 처리에서 **68개의 최종 레이블**을 사용해 세밀한 구조를 만듭니다 — 제목·저자·소속·DOI·PMID 같은 서지정보부터 섹션 제목, 문단, 참고문헌 마커, 머리말/꼬리말, figure 캡션까지.

S2ORC가 3,000만 편 코퍼스를 만들 때 쓴 방법도 정확히 이것입니다:
GROBID XML에서 (i) 메타데이터, (ii) 섹션 헤딩별 문단, (iii) figure/table 캡션을 뽑고, (iv) 수식·표 내용·머리말/꼬리말은 본문에서 **제거**하고, (v) 인라인 인용과 (vi) 참고문헌 항목의 **연결**까지 추출.

### 실무적 장점

- **CPU-only, GPU 불필요**
- Docker 한 줄로 구동, 문서당 1~2초 → 3만 편이면 하룻밤
- 비용 **0원**
- 참고문헌 목록이 `<listBibl>`로 완전 분리 → 본문 청킹에서 자동 제외
- 참고문헌 파싱 F1 약 0.87 (PubMed Central 독립 셋 기준, 딥러닝 citation 모델)

### 구동

```bash
docker run --rm -p 8070:8070 lfoppiano/grobid:latest-full
```

배치 처리는 `grobid_client_python` 사용, `processFulltextDocument` 엔드포인트에
`consolidateHeader=1`, `includeRawCitations=1` 옵션.

---

## 3단계. GROBID의 약점(표) 선택적 보강

GROBID는 표 **내용** 파싱이 약합니다. 인정하고 우회하는 것이 맞습니다.

### Figure

- 캡션만 텍스트 노드로 인덱싱
- 이미지 crop은 파일로 저장, JSON에는 경로만 기록
- 본문 청크에는 `refs_figure: ["fig2"]` 로 링크
- (선택) 추후 VLM으로 figure 내용 서술 생성 → 캡션 보강

### Table

임상 논문에서 표는 정보 밀도가 가장 높은 부분입니다 (환자 특성표, 결과표). 버리면 안 됩니다.

- 표가 중요한 논문(RCT, 메타분석 등)만 2차로 **MinerU** 실행
- MinerU 선택 이유: UniMERNet 기반 수식 인식이 강하고, 다단(multi-column) 학술 조판 레이아웃 처리가 우수. 임상 논문은 다단 + 수식이 흔함
- 대안: Docling의 TableFormer가 복잡한 중첩 표 구조에 강점
- **표는 절대 쪼개지 않고** "캡션 + 표 전체"를 하나의 청크로. 800토큰 초과 시 헤더 행을 반복하며 행 단위 분할

---

## 4단계. 메타데이터는 API로 (파싱하지 말 것)

선생님 판단이 정확합니다. GROBID의 참고문헌 파싱 F1 0.87에 의존할 이유가 없습니다.

| 소스 | 가져올 것 |
|---|---|
| **PubMed E-utilities** | 제목, 저자, 초록, MeSH terms, publication type, 저널, 발행일 |
| **iCite (NIH)** | RCR(Relative Citation Ratio), 인용 수, NIH percentile, 임상 인용 여부 |
| **OpenAlex** | 인용/피인용 그래프, concepts, 기관 정보 — **논문 간 연결 시각화에 최적** |
| **Crossref** | reference list DOI, 라이선스, funder |
| **Semantic Scholar** | influential citation count, TL;DR 요약 |

PubNexus의 "논문 간 관계 시각화"는 **OpenAlex의 인용 그래프 + GROBID의 문단별 `cited_refs`** 조합으로 만드는 것이 가장 강력합니다. 전자는 논문 단위 연결, 후자는 **문장 단위 근거 추적**을 제공합니다.

---

## 5단계. 저장 형식 — JSON이 정본, Markdown은 뷰

> "마음은 markdown으로 보고 싶은데, 구조화된 문서는 json이 낫다는 얘기도 있더라"

**둘 다 맞습니다. 정답은 JSON을 정본으로 두고, Markdown은 거기서 렌더링해서 보여주는 것입니다.**

- JSON → Markdown 렌더러: 30줄이면 끝
- Markdown → 구조 복원: **불가능** (section path, 문단 경계, figure ref가 이미 소실됨)

### 정규화 스키마

```json
{
  "paper_id": "10.1111/jdv.12345",
  "source": "grobid",
  "quality_score": 0.92,
  "meta": {
    "pmid": "34567890",
    "pmcid": "PMC7654321",
    "title": "...",
    "authors": [...],
    "journal": "JEADV",
    "year": 2023,
    "mesh": ["Vitiligo", "Phototherapy", "..."],
    "pub_types": ["Randomized Controlled Trial"],
    "rcr": 2.4
  },
  "abstract": "...",
  "sections": [
    {
      "path": ["Results", "Repigmentation rate"],
      "paragraphs": [
        {
          "id": "p42",
          "text": "치료 12주 후 재색소침착률은 62%였다.",
          "cited_refs": ["10.1016/xxx", "10.1002/yyy"],
          "refs_figure": ["fig2"],
          "refs_table": []
        }
      ]
    }
  ],
  "figures": [
    { "id": "fig2", "caption": "...", "image": "img/10.1111_jdv.12345/fig2.png" }
  ],
  "tables": [
    { "id": "tab1", "caption": "...", "markdown": "| Group | n | ... |" }
  ],
  "references": [
    { "key": "b14", "doi": "10.1016/xxx", "title": "...", "year": 2019 }
  ]
}
```

### 핵심: `cited_refs`

인용번호를 본문에서 **지우되 버리지 않고** 문단 메타데이터로 옮깁니다.

- 본문은 깨끗해짐 → 임베딩·검색 품질 향상
- "이 주장의 근거 논문이 무엇인가"를 **문장 단위로 역추적** 가능
- PubNexus의 시각화 기능과 직결되는 차별화 포인트
- 일반 Markdown 변환기로는 이 데이터가 그냥 소실됨

---

## 6단계. 청킹 전략

| 규칙 | 내용 |
|---|---|
| **섹션 경계 준수** | Methods 끝과 Results 시작이 한 청크에 섞이면 검색 품질이 무너짐. 절대 넘지 않음 |
| **크기** | 문단 단위 병합, 목표 400~700 토큰 |
| **컨텍스트 헤더** | 각 청크 앞에 `[논문 제목] > Results > Repigmentation rate` 붙이기. 이것만으로 검색 정확도가 눈에 띄게 상승 |
| **표** | 통째로 별도 청크 (캡션 포함) |
| **Figure** | 캡션 청크로 별도 |
| **참고문헌 목록** | **청킹 대상에서 완전 제외** (API 메타데이터로 이미 보유) |
| **섹션 태깅** | 각 청크에 `section_type: methods|results|discussion` 부여 → "이 논문의 결과만" 필터 검색 가능 |

---

## 7단계. 검색 스택 (로컬, 비용 0)

### 임베딩

- **Qwen3-Embedding-0.6B** 로 시작
- 8B가 MTEB 최상위(약 70.6점, OpenAI 64.6 / Google 68.3 상회)이지만, 3만 논문 × 약 40청크 = **120만 청크** 규모에는 과함
- 검색 품질이 부족하면 4B로 상향
- Matryoshka로 512차원 절단 시 저장 공간 1/4, 손실 2~3% 수준

### 하이브리드 검색 (필수)

**BM25 + dense 병행.** 선택이 아니라 필수입니다.

- 의학 약어·유전자명·약품명·측정도구명은 BM25가 압도적으로 강함
- dense만 쓰면 "NB-UVB", "F-VASI", "JAK inhibitor" 같은 정확 매칭을 놓침
- RRF(Reciprocal Rank Fusion)로 두 결과 병합

### 벡터 DB

- **LanceDB** 권장 — 파일 기반 임베디드, 데스크탑 앱에 서버 프로세스 불필요
- 대안: Qdrant embedded 모드

### 리랭커

- **Qwen3-Reranker-0.6B** 로 top-50 → top-8
- 체감 품질 향상이 가장 큰 단계. 반드시 넣으세요

---

## 8단계. QC 게이트 — 3만 개는 눈으로 못 봅니다

실무에서 가장 중요한데 대부분 빼먹는 부분입니다. 자동 품질 점수를 계산해 실패 건만 걸러냅니다.

### 품질 신호

| 신호 | 정상 범위 / 판정 |
|---|---|
| 본문 글자수 ÷ 페이지 수 | 페이지당 2,500~4,500자 |
| 섹션 헤딩 개수 | 0개면 실패 |
| **초록 일치도** | GROBID 추출 초록 vs PubMed API 초록 — 강력한 검증 신호 |
| 참고문헌 수 | Crossref reference count와 대조 |
| 텍스트 레이어 유무 | 없으면 스캔본 → 별도 큐 보관 |
| 문자 깨짐 비율 | 비정상 유니코드 / CID 폰트 이슈 감지 |

### 처리

- 점수 하위 5~10%만 MinerU 재처리
- 그래도 실패하면 보류 큐 → 나중에 일괄 판단
- 전체 품질을 지키면서 비용은 거의 늘지 않음

---

## 9. 실행 순서와 비용

| 단계 | 소요 시간 | 비용 |
|---|---|---|
| 인벤토리 + 식별 (DOI/PMID) | 1~2일 (API rate limit) | 0 |
| 메타데이터 수집 | 반나절 | 0 |
| PMC 원문 XML 확보 | 반나절 | 0 |
| GROBID 배치 (CPU) | 하룻밤 | 0 |
| QC 게이트 | 1시간 | 0 |
| MinerU 보강 (10~20%) | GPU 있으면 하룻밤 | 0 |
| 정규화 JSON 생성 | 2~3시간 | 0 |
| 청킹 + 임베딩 (120만 청크) | GPU 있으면 6~12시간 | 0 |

**클라우드 API 비용 0원.** 스캔본은 별도 큐에 모아두고 추후 판단.

### 우선순위 조언

식별 단계(1단계)가 전체의 성패를 좌우합니다. DOI 매칭률이 낮으면 XML 경로도, 메타데이터도, 중복 제거도 전부 무너집니다. 여기에 시간을 가장 많이 투자하세요.

---

## 10. 미결 사항

1. **GPU 사양** — VRAM 용량에 따라 MinerU 실행 가능 여부와 임베딩 모델 크기 권장이 달라집니다. GPU가 없다면 GROBID 단독(CPU) + Qwen3-Embedding-0.6B ONNX 조합으로 재설계 필요.
2. **스캔본 비율** — 텍스트 레이어 검사를 먼저 돌려 실제 비율을 확인하면 2차 계획 수립이 쉬워집니다.
3. **중복 논문 처리** — 3만 편 중 동일 논문의 다른 버전(preprint / accepted / published)이 섞여 있을 가능성. DOI 기준 dedup 정책 필요.
4. **한국어 논문 포함 여부** — 대한피부과학회지 등이 섞여 있다면 GROBID의 CJK 처리 검증 필요.

---

## 참고

- GROBID — https://github.com/kermitt2/grobid
- Europe PMC RESTful API — https://europepmc.org/RestfulWebService
- Europe PMC 벌크 다운로드 — https://europepmc.org/downloads
- NCBI ID Converter — https://www.ncbi.nlm.nih.gov/pmc/tools/id-converter-api/
- iCite — https://icite.od.nih.gov/api
- OpenAlex — https://docs.openalex.org
- MinerU — https://github.com/opendatalab/MinerU
- Docling — https://github.com/docling-project/docling
- Marker — https://github.com/datalab-to/marker
- S2ORC 논문 (파이프라인 참고) — arXiv:1911.02782
