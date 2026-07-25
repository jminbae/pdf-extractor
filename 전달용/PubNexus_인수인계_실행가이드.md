# PubNexus 인수인계 실행 가이드

의학 논문 PDF를 구조화해 검색 가능한 RAG 데이터베이스로 만드는 로컬 파이프라인이다.
이 문서는 **파이썬을 처음 다루는 사용자가 0부터 따라 할 수 있도록** 쓴 실행 설명서다.

- 개발·파일럿 환경: 노트북(GPU 없음) — 0~4단계 실행 완료, 5~6단계는 모델 없이 구조만 검증
- **실제 운영 예정 환경: 데스크톱 `C:\Users\Bae` (Windows + NVIDIA GPU)** — 임베딩·검색은 여기서 실행
- 설계 문서: `전달용/PubNexus_RAG_파이프라인_설계.md`
- 기술 요약: `pubnexus/README.md`

> 표기 규칙
> - `※ 확인 필요` = 이 문서를 쓰는 시점에 검증하지 못한 항목. 실행 전에 직접 확인할 것.
> - `(추정)` = 파일럿 실측값에서 선형 외삽한 값. 실측이 아니다.

---

## 1. 현재 상태 (2026-07-25 실측)

파일럿으로 `전달용/예시` 폴더의 PDF 205개를 처리한 결과다. 아래 숫자는 전부
`pubnexus/data/` 의 실제 파일을 세어 재확인한 값이다.

### 1.1 처리 깔때기

| 단계 | 편수 | 근거 파일 |
|---|---:|---|
| 입력 PDF | 205 | `전달용/예시/*.pdf` (279.5 MB, 1,379쪽) |
| 중복 제외 후 고유 문서 | 201 | `manifest.jsonl` (`is_primary`) |
| DOI 확보 (고유 DOI) | 175 | PDF에서 177건 + Crossref 제목매칭 2건 |
| DOI 미확보 | 26 | `unidentified.jsonl` |
| **정본 JSON 생성** | **167** | `data/normalized/*.json` |
| ├ PMC JATS XML 경로 | 33 | `source: "pmc_xml"` |
| └ GROBID TEI 경로 | 134 | `source: "grobid"` |
| QC 게이트 PASS | 163 / 167 | `qc_report.jsonl` |
| 감사 무결점 | 163 / 167 | `audit_report.jsonl` |

- DOI는 있으나 본문 구조화에 실패한 8편이 있다(대부분 JAMA Dermatology). TEI 캐시가
  없어 GROBID 호출 단계에서 실패한 것으로 보이며, 재실행하면 자동으로 다시 시도한다.
- 감사 플래그 4건의 내역: `under_extracted` 2건, `no_title` 2건.

### 1.2 추출된 내용물

| 항목 | 수치 |
|---|---:|
| 섹션 | 1,271 |
| 문단 | 2,749 |
| 본문 글자수 | 1,840,236자 |
| 표 | 287 (내용이 있는 표 262) |
| 그림 캡션 | 332 |
| 초록 보유 | 121 / 167 (72.5%) |
| MeSH 용어 보유 | 138 / 167 |
| 참고문헌 | 4,169건 |
| **참고문헌 DOI 해소** | **3,559 / 4,169 = 85.4%** |

DOI 해소 85.4%는 Semantic Scholar·Crossref 참조목록 대조로 끌어올린 값이다.
PDF·GROBID만으로는 이보다 훨씬 낮다.

### 1.3 5~6단계 (청킹·인덱스) — 구조 검증만 완료

개발 노트북에서 **모델 없이(`hash` 스텁 백엔드) 배관만 검증한 상태**다.
아래는 **2026-07-25 21:34 기준 `data/chunks.jsonl` 실측**이다. 청킹 규칙을 손대면
숫자가 달라지므로, 인수 후에는 1.4절의 명령으로 직접 다시 재 보는 것을 권한다.

| 산출물 | 현재 값 |
|---|---|
| `data/chunks.jsonl` | **2,448 청크 / 166편** (편당 평균 14.7, 중앙값 13.5, 최대 70) |
| `data/vectors.npz` | 2,485 × 1024 float32 (flat 백엔드) — **청크보다 낡음, 아래 주의 참고** |
| `data/bm25_index.json.gz` | 2,485 청크 색인 — **동일하게 낡음** |

청크 종류별 구성:

| `kind` | 개수 | 설명 |
|---|---:|---|
| `text` | 1,673 | 본문 문단을 묶은 청크 |
| `table` | 344 | 표 캡션 + 내용 (상한 초과 시 헤더 반복하며 분할) |
| `figure` | 305 | 그림 캡션 |
| `abstract` | 126 | 초록 (논문당 1개가 원칙) |

섹션 타입별 구성:

| `section_type` | 청크 | 보유 논문 |
|---|---:|---:|
| `results` | 627 | 82편 |
| `other` | 576 | 128편 |
| `methods` | 483 | 88편 |
| `discussion` | 435 | 96편 |
| `intro` | 201 | 74편 |
| `abstract` | 126 | 121편 |

청크 크기: 중앙값 273토큰, 90분위 529토큰, 최대 700토큰(설정 상한과 일치).
상한을 넘는 청크는 0개다.

정본 167편 중 166편이 청크를 만들었다. 나머지 1편(`10.1016/j.jaad.2015.02.1123`)은
섹션·초록·표·그림이 모두 비어 있어 청킹할 내용 자체가 없다 — **검색되지 않는다.**

> **중요 1 — GPU PC에서 반드시 다시 만들어야 한다.**
> 현재 `vectors.npz` 의 메타는 `"encoder": "hash-1024"` 다. 즉 실제 임베딩 모델이
> 아니라 **결정적 해시 스텁**으로 채운 벡터다. 배관이 도는지 확인하는 용도이고
> **검색 품질은 보장되지 않는다.** GPU 데스크톱에서 `run_rag.py` 를 다시 돌리면
> Qwen3-Embedding 으로 재구축된다(5.2절).

> **중요 2 — 지금 저장된 인덱스는 청크 원장과 어긋나 있다.**
> `chunks.jsonl` 은 청킹 규칙을 마지막으로 손본 뒤(2,448개) 다시 만들었는데
> `vectors.npz`·`bm25_index.json.gz` 는 그 이전 상태(2,485개)다. 이 상태로 `ask.py`
> 를 돌리면 `[7단계] … 인덱스가 낡았습니다` 경고가 뜬다(검색은 되지만 일부 청크가
> 빠지거나 옛 본문이 나온다). **어차피 위의 이유로 전체 재구축이 필요하므로,
> 처음 할 일은 `run_rag.py` 한 번 돌리는 것이다.**

### 1.4 위 숫자를 직접 다시 재는 법

```powershell
& $PY -c "import json,collections;r=[json.loads(l) for l in open(r'data/chunks.jsonl',encoding='utf-8') if l.strip()];print('청크',len(r),'논문',len({x['paper_id'] for x in r}));print(collections.Counter(x['kind'] for x in r).most_common());print(collections.Counter(x['section_type'] for x in r).most_common())"
```

---

## 2. 실행 PC 요구사항

| 항목 | 최소 | 권장 | 비고 |
|---|---|---|---|
| OS | Windows 10/11 64bit | — | |
| 파이썬 | 3.10 | **3.12** | |
| RAM | 16 GB | 32 GB | GROBID Docker가 메모리를 많이 쓴다 |
| GPU | 없어도 0~5단계는 가능 | NVIDIA VRAM 6 GB↑ | 6~7단계(임베딩·리랭커)에 필요 |
| 디스크 여유 | 20 GB (파일럿 규모) | **100 GB** (3만 편 확장 시) | 7장 참고 |
| 인터넷 | 필요 | — | 1단계 API 조회, 모델 최초 다운로드 |

GPU가 없어도 임베딩은 CPU로 돌릴 수 있으나 매우 느리다. 구조만 점검하려면
`config.yaml` 의 `embedding.backend` 를 `hash` 로 두면 모델 없이 전 과정이 돌아간다
(단, **검색 품질은 보장되지 않는다**. 시험용 스텁이다).

---

## 3. 설치 (0부터)

아래 명령은 전부 **PowerShell** 기준이다. 시작 메뉴에서 "PowerShell"을 검색해 실행한다.

### 3.1 파이썬 설치

1. https://www.python.org/downloads/windows/ 에서 **Python 3.12.x** 의
   "Windows installer (64-bit)" 를 받는다.
2. 설치 화면 첫 페이지에서 **`Add python.exe to PATH` 체크박스를 반드시 켠다.**
3. 설치 후 확인:

```powershell
py --list
python --version
```

`Python 3.12.x` 가 나오면 성공이다.

### 3.2 프로젝트 폴더 확인

프로젝트는 Dropbox로 동기화된다. 데스크톱에서의 실제 경로를 확인해 둔다.
(개발 노트북은 `C:\Dropbox\...`, 파일럿 실행 PC는 `D:\Dropbox\...` 였다. PC마다 다르다.)

```powershell
# 아래 경로는 본인 PC에 맞게 수정
$REPO = "C:\Users\Bae\Dropbox\Claude Code\260725 PDF 추출"
cd $REPO
dir
```

`pubnexus`, `전달용` 두 폴더가 보이면 맞는 위치다.
설정 파일의 모든 경로는 **이 폴더 기준 상대경로**라 PC가 바뀌어도 그대로 동작한다.

### 3.3 가상환경 만들기

프로젝트 전용 파이썬 공간이다. 시스템 파이썬을 오염시키지 않는다.

```powershell
cd $REPO
py -3.12 -m venv .venv
```

이후 **모든 명령은 가상환경 파이썬을 전체 경로로 호출한다.** 이렇게 하면
PowerShell 실행 정책(ExecutionPolicy)을 건드리지 않아도 된다.

```powershell
$PY = "$REPO\.venv\Scripts\python.exe"
& $PY --version
```

> `.venv\Scripts\Activate.ps1` 로 활성화하는 방법도 있지만, PowerShell 기본 설정에서
> 스크립트 실행이 막혀 있을 수 있다. 위의 전체 경로 호출 방식이 더 안전하다.

### 3.4 기본 의존성 (필수)

0~5단계에 필요한 전부다.

```powershell
& $PY -m pip install --upgrade pip
& $PY -m pip install -r "$REPO\pubnexus\requirements.txt"
```

설치되는 것: PyMuPDF(PDF), requests(API), lxml(XML), PyYAML(설정),
rapidfuzz(제목 매칭), tqdm, numpy.

### 3.5 PyTorch (GPU용 — 6·7단계 필수)

**여기가 가장 실수가 잦은 부분이다.** PyTorch는 CUDA 버전별로 설치 명령이 다르고,
그 명령은 자주 바뀐다.

```powershell
# 1) 그래픽 드라이버가 지원하는 CUDA 버전 확인
nvidia-smi
```

출력 오른쪽 위 `CUDA Version:` 을 확인한 뒤,
**https://pytorch.org/get-started/locally/ 의 설치 선택기**에서
(Stable / Windows / Pip / Python / 해당 CUDA)를 골라 나온 명령을 그대로 쓴다.

```powershell
# 예시 (CUDA 12.4 계열) — ※ 확인 필요: 인덱스 URL의 cuXXX 부분은
#   pytorch.org 선택기가 알려주는 값으로 반드시 대체할 것
& $PY -m pip install torch --index-url https://download.pytorch.org/whl/cu124

# GPU가 없거나 CPU로만 시험할 때
& $PY -m pip install torch
```

설치 확인:

```powershell
& $PY -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

`True` 와 GPU 이름이 나와야 한다. `False` 면 CPU 버전이 설치된 것이니 위 명령을 다시 확인한다.

### 3.6 sentence-transformers (6·7단계 필수)

임베딩 모델과 리랭커를 불러오는 라이브러리다.

```powershell
& $PY -m pip install "sentence-transformers>=3.0" "transformers>=4.51"
```

- `transformers>=4.51` 은 **필수**다. 그 미만 버전은 Qwen3 계열 모델에서
  `KeyError: 'qwen3'` 로 실패한다(Qwen 공식 모델카드 명시).
- `sentence-transformers` 5.x 계열이면 `encode_query()` / `encode_document()` 같은
  최신 API도 쓸 수 있다. 코드는 구버전 방식으로도 동작하게 작성돼 있다.
- **`flash-attn` 은 설치하지 말 것.** Windows용 배포 휠이 없다. 코드는 대신
  PyTorch 내장 `sdpa` 를 쓴다.

모델 가중치는 최초 실행 시 Hugging Face에서 자동으로 내려받는다.

| 모델 | 크기 | 용도 |
|---|---|---|
| `Qwen/Qwen3-Embedding-0.6B` | 약 1.2 GB | 6단계 임베딩 |
| `Qwen/Qwen3-Reranker-0.6B` | 약 1.2 GB | 7단계 리랭킹 |

캐시 위치를 바꾸려면(C드라이브 용량이 부족할 때):

```powershell
$env:HF_HOME = "D:\hf_cache"
```

### 3.7 LanceDB (선택)

벡터를 파일 기반 DB에 저장한다. 서버 프로세스가 필요 없다.

```powershell
& $PY -m pip install lancedb
```

**설치하지 않아도 된다.** 설치가 없으면 6단계가 안내 한 줄을 남기고 자동으로
`flat` 으로 폴백해 `vectors.npz` 를 만들고, 7단계도 같은 파일을 읽는다. 안내 문구가
거슬리면 `pubnexus/config.yaml` 의 `vectordb.backend` 를 `lancedb` → `flat` 으로
바꾸면 된다. `flat` 은 numpy 배열에 저장해 전수 비교하는 방식으로, 파일럿 규모
(수천 청크)는 물론 수십만 청크까지도 실용적으로 충분히 빠르다.

### 3.8 GROBID (2단계b — PMC에 없는 논문 처리용)

PDF에서 학술 구조(섹션·인용·참고문헌)를 뽑는 전용 서버다. Docker로 띄운다.

1. **Docker Desktop 설치**: https://www.docker.com/products/docker-desktop/
   - Windows에서는 설치 중 **WSL2** 활성화를 요구한다. 안내에 따라 진행하고 재부팅한다.
2. GROBID 컨테이너 실행 (**최초 1회 이미지 다운로드가 수 GB로 오래 걸린다**):

```powershell
docker run --rm -p 8070:8070 lfoppiano/grobid:latest-full
```

3. 이 창은 **켜 둔 채로** 둔다. 다른 PowerShell 창에서 파이프라인을 실행한다.
4. 동작 확인: 브라우저에서 http://localhost:8070 을 열어 GROBID 화면이 나오면 성공.

`config.yaml` 의 `grobid.url` 이 이미 `http://localhost:8070` 으로 돼 있으므로
따로 고칠 것이 없다.

> GROBID를 띄우지 않아도 파이프라인은 죽지 않는다. 서버 응답이 없으면 안내 메시지를
> 남기고 2단계b를 건너뛴 뒤, PyMuPDF 폴백으로 저품질 추출만 남긴다.
> 나중에 GROBID를 띄우고 다시 실행하면 자동으로 고품질 결과로 덮어쓴다.

### 3.9 설치 최종 점검

```powershell
cd "$REPO\pubnexus"

# 1) 기본 의존성
& $PY -c "import fitz, requests, lxml, yaml, rapidfuzz, numpy; print('기본 OK')"

# 2) 5단계 청킹만 시험 (모델 불필요, 수 초)
& $PY run_rag.py --chunk-only

# 3) 6단계 임베더 자가점검 (모델 다운로드가 여기서 일어난다)
& $PY -c "import sys;sys.path.insert(0,'src');from pubnexus import embed; embed.run()"
```

마지막 명령이 `[6단계] 자가점검: st:Qwen/Qwen3-Embedding-0.6B@1024 · dim 1024 · 질의-문서 코사인 [...]`
같은 줄을 출력하면 임베딩 준비가 끝난 것이다.

---

## 4. 설정 파일 (`pubnexus/config.yaml`)

바꿀 일이 있는 항목만 추렸다. 나머지는 손대지 않아도 된다.

| 키 | 현재값 | 언제 바꾸나 |
|---|---|---|
| `project.input_dir` | `전달용/예시` | 처리할 PDF 폴더를 바꿀 때 (리포 루트 기준 상대경로) |
| `project.work_dir` | `pubnexus/data` | 산출물 위치를 다른 드라이브로 옮길 때 |
| `metadata.email` | `jminbae@gmail.com` | 본인 이메일로. API polite pool 식별용 |
| `metadata.ncbi_api_key` | 빈 값 | **3만 편 확장 시 발급 권장** (7장) |
| `metadata.request_delay_sec` | `0.34` | NCBI 키 발급 시 `0.11` 로 낮출 수 있음 |
| `grobid.url` | `http://localhost:8070` | 원격 GROBID 서버를 쓸 때 |
| `embedding.backend` | `auto` | GPU/모델 없이 배관만 시험하려면 `hash` |
| `embedding.dim` | `1024` | `512` 로 낮추면 벡터 저장공간 **절반**(7.1절 표와 동일). 품질 손실 폭은 `※ 확인 필요` — `evaluate.py` 로 직접 비교할 것 |
| `vectordb.backend` | `lancedb` | lancedb 미설치면 `flat` |
| `reranker.enabled` | `true` | GPU 메모리가 빠듯하면 `false` |

---

## 5. 단계별 실행

모든 명령은 `pubnexus` 폴더 안에서 실행한다.

```powershell
$REPO = "C:\Users\Bae\Dropbox\Claude Code\260725 PDF 추출"   # 본인 경로로 수정
$PY   = "$REPO\.venv\Scripts\python.exe"
cd "$REPO\pubnexus"
```

### 5.1 0~4단계 — PDF → 정본 JSON

GROBID 컨테이너를 먼저 띄운 상태에서:

```powershell
& $PY run_pilot.py
```

GROBID 없이 PMC XML 경로만 돌리려면:

```powershell
& $PY run_pilot.py --skip-grobid
```

진행 상황은 화면(stderr)에 `[0단계] … [1단계] …` 형식으로 실시간 출력된다.

| 단계 | 하는 일 | 산출물 |
|---|---|---|
| 0 인벤토리 | PDF 열어 DOI·쪽수·텍스트층·중복 판정 | `data/manifest.jsonl`, `data/unidentified.jsonl` |
| 1 메타데이터 | PubMed·iCite·OpenAlex·Crossref 조회 | `data/meta/{doi}.json` |
| 2a PMC XML | PMCID 있으면 원문 JATS 확보·파싱 | `data/xml/{pmcid}.xml` → `data/normalized/` |
| 2b GROBID | 나머지 PDF를 TEI로 변환·파싱 | `data/tei/{doi}.tei.xml` → `data/normalized/` |
| (폴백) | GROBID 실패분을 PyMuPDF로 저품질 추출 | `data/normalized/` |
| (참조보강) | 참고문헌 DOI를 S2·Crossref로 대조 | `data/s2_refs/`, `data/crossref_refs/` |
| 3~4 정규화·QC | 품질점수 산정, 실패 건 표시 | `data/qc_report.jsonl` |
| (뷰) 렌더 | 사람이 읽을 Markdown | `data/markdown/{doi}.md` |
| (감사) | 내용 수준 전수 조사 | `data/audit_report.jsonl` |

### 5.2 5~6단계 — 청킹 + 인덱스 구축 (`run_rag.py`)

```powershell
& $PY run_rag.py                  # 청킹 → 임베딩 → 인덱스 구축
```

| 옵션 | 뜻 |
|---|---|
| `--chunk-only` | 5단계 청킹까지만. **모델이 필요 없고 수 초면 끝난다** |
| `--skip-chunk` | 기존 `chunks.jsonl` 을 재사용하고 인덱스만 다시 만듦 |
| `--backend hash` | 모델 없이 구조만 검증 (검색 품질 보장 없음) |
| `--device cpu` | GPU 대신 CPU 사용 (매우 느림) |
| `--vectordb flat` | lancedb 대신 numpy 파일 사용 |

**GPU 설치를 끝내기 전이라면 먼저 이것부터 해 보는 것을 권한다.**
모델 없이 5~7단계 배관 전체가 도는지 몇 초 만에 확인할 수 있다.

```powershell
& $PY run_rag.py --backend hash --vectordb flat
```

| 단계 | 하는 일 | 산출물 |
|---|---|---|
| 5 청킹 | 섹션 경계를 지키며 목표 550토큰으로 분할, 각 청크에 `[제목] > Results > …` 컨텍스트 헤더 부착 | `data/chunks.jsonl` |
| 6 임베딩·인덱스 | 청크를 벡터로 변환해 저장 + BM25 색인 구축 | `data/lancedb/` 또는 `data/vectors.npz`, `data/bm25_index.json.gz` |

참고문헌 목록은 청킹에서 제외된다(이미 메타데이터로 갖고 있다).
표는 캡션+내용을 한 청크로 묶고(700토큰을 넘으면 헤더 행을 반복하며 분할),
그림은 캡션 한 청크, 초록도 단독 청크로 만든다.

정본 JSON이 하나도 없으면 `[중단] 정본 문서가 없다` 를 출력하고 멈춘다.
그때는 5.1절(`run_pilot.py`)을 먼저 실행한다.

**어떤 인코더로 만든 인덱스인지 확인하는 법** (`flat` 백엔드일 때):

```powershell
& $PY -c "import numpy as np;print(str(np.load(r'data/vectors.npz',allow_pickle=True)['meta']))"
```

`"encoder": "hash-1024"` 가 나오면 **스텁 벡터**다. 실제 모델로 만든 인덱스는
`"encoder": "st:Qwen/Qwen3-Embedding-0.6B@1024"` 로 나온다.

### 5.3 7단계 — 질의 (`ask.py`)

```powershell
& $PY ask.py "백반증 NB-UVB 재색소침착률" -k 8
& $PY ask.py "survival analysis" --section methods --year 2018-2025
& $PY ask.py "VASI" --kind table --json
& $PY ask.py "prevalence" --year=-2015
```

| 옵션 | 뜻 |
|---|---|
| `-k`, `--top-k` | 반환할 청크 수 (기본: `config.yaml` 의 `reranker.top_k_out` = 8) |
| `--section` | 섹션 필터: `abstract` `intro` `methods` `results` `discussion` `back` `other` (쉼표로 여러 개). `back`(감사의 글·이해상충 등)은 청킹 단계에서 제외되므로 현재 코퍼스에서는 항상 0건이다 |
| `--kind` | 청크 종류: `abstract` `text` `table` `figure` |
| `--year` | `2018-2025` / `2018-` / `-2015` / `2020` |
| `--journal` | 저널명 부분일치 |
| `--pub-type` | 출판 유형 (예: `"Randomized Controlled Trial"`) |
| `--paper` | 특정 논문(DOI)으로 한정 |
| `--no-rerank` | 리랭커를 끄고 RRF 순위 그대로 (빠름) |
| `--dense-only` | BM25 없이 벡터 검색만 (하이브리드 효과 비교용) |
| `--json` | 결과를 JSON으로 출력 (다른 프로그램에 넘길 때) |
| `--width` | 터미널 출력 폭 (기본 100) |

내부 동작: dense 벡터 top-50 + BM25 top-50 → RRF로 병합 → Qwen3-Reranker로 top-8 선별.

> **주의 — `ask.py` 에는 임베딩 백엔드 옵션이 없다.** 질의도 인덱스를 만든 것과
> **같은 인코더**로 벡터화해야 하므로, `config.yaml` 의 `embedding.backend` 값을
> 인덱스 구축 때와 똑같이 두어야 한다. `run_rag.py --backend hash` 로 만든 인덱스를
> 질의하려면 `config.yaml` 도 `hash` 여야 한다(다르면 `[7단계] 임베딩 백엔드 준비
> 실패` 또는 `차원 불일치` 가 뜬다).

결과 본문은 stdout, 진행·경고 로그는 stderr로 나간다. 따라서 `--json` 출력을
파일로 받아도 로그가 섞이지 않는다.

```powershell
& $PY ask.py "VASI threshold" --json | Out-File -Encoding utf8 hits.json
```

> PowerShell에서는 `>` 대신 `| Out-File -Encoding utf8` 을 쓸 것.
> `>` 로 저장하면 한글이 깨질 수 있다.

`--section` 필터는 8.1절의 이유로 현재 신뢰도가 낮다. **처음에는 필터 없이 검색할 것.**

### 5.4 검색 품질 측정

```powershell
& $PY evaluate.py            # 기본 설정 평가
& $PY evaluate.py --compare  # 하이브리드 vs dense 단독 vs 리랭커 유무 비교
```

`pubnexus/eval_queries.yaml` 의 골든 세트(파일럿 167편 기준)로 `hit@k` 와 `MRR` 을 낸다.
청킹 파라미터나 임베딩 모델을 바꿨을 때 **좋아졌는지 나빠졌는지를 감이 아니라 숫자로**
판단하기 위한 도구다. 하이브리드가 dense 단독보다 나와야 정상이다.

---

## 6. 재실행 안전성 (이어하기)

**모든 단계는 중간에 끊고 다시 실행해도 안전하다.** 이미 끝난 작업은 캐시를 보고 건너뛴다.

| 단계 | 이어하기 방식 | 처음부터 다시 하려면 |
|---|---|---|
| 0 인벤토리 | `manifest.partial.jsonl` 에 파일 단위로 증분 저장. 이미 스캔한 PDF는 건너뜀 | `manifest.partial.jsonl` 삭제 |
| 1 메타데이터 | `data/meta/{doi}.json` 이 있으면 재조회 안 함. 단, 모든 소스가 실패한 캐시는 '완료'로 보지 않고 재수집 | `run_pilot.py --force-meta` |
| 2a PMC XML | `data/xml/{pmcid}.xml` 캐시 재사용 | 해당 xml 파일 삭제 |
| 2b GROBID | `data/tei/{doi}.tei.xml` 캐시 재사용 | 해당 tei 파일 삭제 |
| (폴백) | 이미 GROBID 결과(`source: grobid`)가 있으면 덮어쓰지 않음 | — |
| (참조보강) | `s2_refs/`, `crossref_refs/` 캐시 재사용. **rate limit으로 실패한 건은 캐시하지 않고 다음에 재시도** | 해당 캐시 폴더 삭제 |
| 5 청킹 | 매번 `chunks.jsonl` 전체를 새로 씀 (빠르므로 문제없음) | — |
| 6 인덱스 | 매번 새로 구축. 청킹을 건너뛰려면 `run_rag.py --skip-chunk` | — |

중단 방법은 실행 중인 창에서 `Ctrl + C` 다.

> **주의**: 청킹 규칙이나 임베딩 모델·차원을 바꾸면 **반드시 5·6단계를 함께 다시 돌려야 한다.**
> 인덱스에는 임베더 이름·차원·청크 수가 메타로 함께 저장돼 있어, 불일치하면 검색 시 경고가 뜬다.

---

## 7. 3만 편 확장 체크리스트

파일럿 167편의 **실측값에서 선형 외삽**한 값이다. 실제 코퍼스 구성(쪽수·PMC 비율)에 따라 달라진다.

### 7.1 디스크 용량

용량 단위는 **Windows 탐색기 표기와 같은 2진 단위**(1 GB = 1024 MB)로 적었다.

| 항목 | 파일럿 실측 | 편당 | 30,000편 (추정) |
|---|---:|---:|---:|
| 원본 PDF | 279.5 MB / 205개 | 1.36 MB | **40 GB** |
| `data/` 중간산출물 (청크·인덱스 제외) | 21.0 MB / 167편 | 129 KB | **3.7 GB** |
| ├ `normalized/` | 6.0 MB | 36 KB | 1.1 GB |
| ├ `tei/` | 7.3 MB (134편) | 56 KB | 1.6 GB |
| ├ `markdown/` | 2.7 MB | 17 KB | 0.5 GB |
| ├ `xml/` | 2.0 MB (33편) | 62 KB | PMC 보유분만 |
| └ `meta/` | 0.55 MB (175편) | 3 KB | 0.1 GB |
| `chunks.jsonl` | 7.8 MB / 2,448청크 | 14.7청크 = 48 KB | **1.4 GB** (약 440,000청크) |
| `vectors.npz` (1024차원 float32) | 10.1 MB / 2,485청크 | 4.2 KB/청크 | **1.8 GB** (512차원이면 0.9 GB) |
| `bm25_index.json.gz` | 0.54 MB | 0.22 KB/청크 | **0.1 GB** |
| 모델 캐시 (HF) | — | — | 약 2.5 GB |
| PyTorch CUDA 패키지 | — | — | 약 3 GB |

**합계 약 52 GB. 작업 여유분 포함 100 GB 확보를 권장한다.**

청크·벡터·BM25 항목은 파일럿에서 실제로 만들어진 파일 크기를 잰 값이다
(벡터는 압축하지 않고 저장하므로 임베딩 모델이 바뀌어도 크기는 같다).

### 7.2 예상 소요 시간

| 단계 | 파일럿 실측(편당) | 30,000편 (추정) | 비고 |
|---|---:|---:|---|
| 0 인벤토리 | 미측정 | 수 시간 | PDF 앞 2쪽만 파싱 |
| 1 메타데이터 | **2.0초** (중앙값) | **약 17시간** | API rate limit이 병목. NCBI 키 발급 시 단축 |
| 2a PMC XML | 캐시 기준 즉시 | 수 시간 | 다운로드 시간 지배 |
| 2b GROBID | **2.0초** | **약 17시간** | 파일럿 PC의 로컬 GROBID 기준. CPU 성능에 비례 |
| (참조보강) | — | 수 시간 ~ 하루 | Semantic Scholar rate limit 영향 큼 |
| 3~4 정규화·QC·감사 | 즉시 | 1~2시간 | |
| 5 청킹 | 167편에 수 초 | 수십 분 (추정) | CPU만 사용 |
| 6 임베딩·인덱스 | — | **2~5시간 (추정)** | GPU 기준, 약 440,000청크. 설계서는 120만 청크에 6~12시간으로 잡음 |
| 7 질의 | — | 초 단위 | 인덱스만 있으면 즉시 |

**총 2~3일 정도의 연속 실행**을 예상하면 된다. 밤새 돌리고 아침에 로그를 확인하는 식이 현실적이다.

### 7.3 API rate limit

| API | 한도 | 대응 |
|---|---|---|
| NCBI E-utilities (PubMed) | 키 없음 **초당 3회**, 키 있음 **초당 10회** | **3만 편이면 키 발급 필수.** https://account.ncbi.nlm.nih.gov 에서 무료 발급 → `config.yaml` 의 `metadata.ncbi_api_key` 에 입력하고 `request_delay_sec` 을 `0.11` 로 |
| Crossref | polite pool — `mailto` 제공 시 우대 | `metadata.email` 을 본인 이메일로 (이미 설정됨) |
| OpenAlex | polite pool — `mailto` 제공 시 우대. 구체적 상한은 `※ 확인 필요` | 위와 동일 |
| iCite | 명시된 상한 없음 (`※ 확인 필요`) | — |
| Europe PMC | 명시된 상한 없음 (`※ 확인 필요`) | — |
| Semantic Scholar | 키 없이 공유 풀 사용 → **429가 자주 뜬다** | 코드가 3초씩 늘려가며 5회 재시도하고, 실패분은 캐시하지 않아 다음 실행에서 재시도한다. 대규모라면 API 키 신청 검토 |

코드에 이미 들어 있는 보호 장치: 호스트별 요청 간격 제어, HTTP 429 시 지수 백오프(2·3·5초),
요청당 최대 3회 재시도. **rate limit 때문에 배치가 통째로 죽지는 않는다.**

### 7.4 중단·재개 절차

1. 실행 창에서 `Ctrl + C` 로 중단한다.
2. 같은 명령을 다시 실행한다. 6장의 캐시 규칙에 따라 끝난 작업은 건너뛴다.
3. 로그를 파일로 남겨 두면 나중에 원인 추적이 쉽다:

```powershell
& $PY run_pilot.py 2>&1 | Tee-Object -FilePath "$REPO\pubnexus\data\run_30k.log"
```

4. 며칠에 걸쳐 돌릴 때는 **Windows 절전/최대 절전 모드를 꺼 둔다**
   (설정 → 시스템 → 전원 → 화면 및 절전 모드).

### 7.5 스캔본 큐 처리

텍스트 레이어가 없는 스캔 PDF는 GROBID로 처리할 수 없다.

- 판정 기준: `config.yaml` 의 `identify.scanned_char_threshold: 200`
  (전체 글자수가 200자 미만이면 스캔본 후보)
- 결과: `manifest.jsonl` 의 `is_scanned_candidate: true` 로 표시되고 파이프라인에서 제외된다.
- **파일럿 실측: 205개 중 스캔본 후보 1개, 텍스트층 없음 2개, 열기 실패 1개.**
  3만 편으로 외삽하면 150~500편 정도가 예상된다(추정).
- 처리 방침: 별도 큐에 모아 두었다가 나중에 일괄 판단한다. OCR 도입은 **현재 미구현**이다.

스캔본만 뽑아 보는 법:

```powershell
& $PY -c "import json;rows=[json.loads(l) for l in open(r'data/manifest.jsonl',encoding='utf-8')];[print(r['filename']) for r in rows if r.get('is_scanned_candidate') or not r.get('has_text_layer')]"
```

### 7.6 확장 전 점검 목록

- [ ] `metadata.email` 을 본인 이메일로 교체했는가
- [ ] NCBI API 키를 발급받아 `config.yaml` 에 넣었는가
- [ ] 디스크 여유가 100 GB 이상인가
- [ ] GROBID Docker가 안정적으로 떠 있는가 (수십 시간 연속 가동)
- [ ] 절전 모드를 껐는가
- [ ] 파일럿 167편으로 5~7단계를 **실제 임베딩 모델로 한 번 끝까지 돌려 봤는가** (가장 중요)
- [ ] `vectors.npz` 의 `encoder` 가 `hash-1024` 가 아니라 `st:Qwen/...` 인지 확인했는가
- [ ] `evaluate.py` 로 기준선 점수를 기록해 뒀는가 (확장 후 비교용)

---

## 8. 알려진 한계

정직하게 적는다. 아래는 현재 구현의 **실제 상태**다.

### 8.1 논문 40%는 IMRaD 섹션 구분이 없다 (섹션 필터의 한계)

원본 정본 JSON에서는 **1,271개 섹션 중 1,019개(80.2%)가 `other`** 였다. 원인은
(a) GROBID가 계층 없는 평면 구조를 내보내 하위 절이 부모 섹션을 잃음,
(b) 일부 출판사 PDF의 자간(글자 사이 공백) 아티팩트로 `I N TRODUC TION` 같은 제목이 생김,
(c) 하위 절 제목(`Statistical analysis`, `Study population` 등)을 분류기가 모름 — 세 가지다.

5단계 청킹 직전에 `textfix.py` 가 이 세 가지를 보정하도록 만들어 상당 부분 복구했다.
아래 "보정 후"는 실제로 검색되는 단위인 `chunks.jsonl` 기준이다(167편 중 비율).

| 섹션 타입 | 보정 전 (보유 논문) | **보정 후 (보유 논문)** |
|---|---:|---:|
| `results` | 28편 (16.8%) | **82편 (49.1%)** |
| `methods` | 44편 (26.3%) | **88편 (52.7%)** |
| `discussion` | 83편 (49.7%) | **96편 (57.5%)** |
| `intro` | 62편 (37.1%) | **74편 (44.3%)** |
| IMRaD 타입이 하나도 없는 논문 | 81편 | **68편** |

전체 청크 기준으로는 `other` 가 **2,448개 중 576개(23.5%)** 로 내려왔다
(보정 전 섹션 기준 80.2%). 남은 68편은 대부분 Letter·Case report라 애초에
IMRaD 구조가 없어 더 복구할 여지가 적다.

**영향**: `ask.py --section results` 는 이제 167편 중 82편(49%)만 조회 대상으로 삼는다.
빠지는 85편에는 IMRaD가 없는 68편이 포함된다. **섹션 필터는 결과가 너무 많을 때
좁히는 용도로 쓰고, 처음에는 필터 없이 검색할 것.**

### 8.2 표 내용 파싱 (MinerU 미도입)

- 표 287개 중 **25개는 내용이 비어 있다**(캡션만 있음). 14편에 걸쳐 있다.
  이 빈 표도 캡션만 담긴 청크로 색인된다.
- 표 48개는 청크 상한(700토큰)을 넘어 헤더 행을 반복하며 최대 4조각으로 나뉜다
  (표 287개 → 표 청크 344개). 한 표의 내용이 여러 청크에 흩어진다는 뜻이다.
- 설계서가 제안한 **MinerU 2차 보강은 도입하지 않았다.** RCT·메타분석의 핵심 수치가
  표에 있는 경우 검색으로 잡히지 않을 수 있다.
- 실무 대응: 표 관련 질문은 검색 결과의 논문을 찾아 **원본 PDF를 직접 확인**해야 한다.

### 8.3 Figure 이미지

- 정본 JSON의 `figures[].image` 는 **167편 전부 비어 있다.** 캡션 텍스트만 추출된다.
- `data/figures/` 에 PNG 9개가 있으나 이는 논문 1편에 대한 시험 산출물이며,
  현재 파이프라인이 생성하는 것이 아니다.
- 그림 캡션 332개 중 28개는 캡션조차 비어 있어 청크가 되지 못한다(그림 청크 305개).
  남은 캡션 중 24개는 20토큰 미만으로 짧아 검색 단서가 거의 없다.
- 즉 **그림 자체를 검색하거나 보여 주는 기능은 없다.**

### 8.4 초록 부재

- 167편 중 **46편(27.5%)에 초록이 없다.** 대부분 Letter/Case report 형식이라 원래 초록이 없다.
- 초록은 검색 진입점으로 가치가 크므로, 이 46편은 본문 청크로만 검색된다.

### 8.5 인용 역추적 커버리지

- `cited_refs` 를 가진 문단은 전체 2,749개 중 **1,060개(38.6%)** 다.
- **22편은 인용 링크가 하나도 없다**(그중 4편은 본문 문단 자체가 추출되지 않았다).
- "이 주장의 근거 논문" 역추적은 나머지 61%의 문단에서는 동작하지 않는다.

### 8.6 인용 지표 없음

- `meta.rcr`(상대 인용 비율), `meta.citation_count` 가 **167편 전부 null** 이다.
- 인용수 기준 정렬·가중은 현재 불가능하다.

### 8.7 리랭커 사양

| 항목 | 값 |
|---|---|
| 모델 | `Qwen/Qwen3-Reranker-0.6B` (약 1.2 GB) |
| VRAM | 임베더(1.2 GB)와 동시 상주 시 **6 GB에서 동작 가능**하나 배치가 크면 빠듯하다 |
| 없을 때 동작 | 경고 로그를 남기고 **RRF 순위를 그대로 반환한다. 프로그램이 죽지 않는다** |
| 끄는 법 | `config.yaml` 의 `reranker.enabled: false`, 또는 `ask.py --no-rerank` |

VRAM이 부족하면 `reranker.enabled: false` 로 두거나, `embedding.dim` 을 512로 낮춰
메모리와 저장공간을 줄인다.

### 8.8 기타

- `normalized/*.json` 의 `source_file` 경로는 전부 `D:\` 로 시작한다(파일럿 실행 PC 기준).
  다른 PC에서는 이 경로로 원본 PDF를 열 수 없다. 검색 자체에는 영향이 없다.
- 텍스트 잔여 결함(정본 JSON 기준 실측. 5단계 청킹 때 `textfix.py` 가 상당수를
  보정하지만 전부는 아니다. 검출기 정의에 따라 ±10% 정도 달라진다):
  자간 아티팩트(`I N TRODUC TION` 류) 91건/22편, 문단으로 새어든 캡션 10건/10편,
  하이픈 줄바꿈 분리(`treat- ment`) 28건/12편, 완전 중복 문단 7편.
- OCR은 미구현이다(7.5절 스캔본 큐 항목 참고).

---

## 9. 문제가 생겼을 때

| 증상 | 원인 / 대처 |
|---|---|
| `python: command not found` | 3.1절에서 PATH 체크박스를 놓쳤다. 파이썬을 재설치하거나 `py` 명령을 쓴다 |
| `ModuleNotFoundError: No module named 'fitz'` | 3.4절 의존성 설치를 안 했거나 시스템 파이썬으로 실행 중이다. `$PY` 전체 경로로 실행 |
| `[2단계b] GROBID 서버 응답 없음` | Docker 컨테이너가 꺼져 있다. 3.8절 명령을 다시 실행 |
| `KeyError: 'qwen3'` | `transformers` 가 4.51 미만이다. `pip install -U "transformers>=4.51"` |
| `torch.cuda.is_available()` 이 False | CPU 버전 torch가 설치됐다. 3.5절을 다시 확인 |
| `[6단계] sentence-transformers 미설치` | 3.6절 설치. 일부러 폴백하지 않는 설계다(저품질 인덱스를 모르고 쓰는 사고 방지) |
| `[중단] 정본 문서가 없다` | `run_pilot.py` 로 0~4단계를 먼저 실행 |
| `[7단계] 청크 파일이 없거나 비었습니다` | `run_rag.py` 를 먼저 실행 |
| `[7단계] 벡터 인덱스가 없습니다` | 6단계 미실행. `run_rag.py` 실행 (모델 없이 확인만 할 거면 `--backend hash`) |
| `[7단계] 벡터 인덱스 로딩 실패` | 인덱스 파일 손상 또는 LanceDB 오류. `run_rag.py --skip-chunk` 로 재구축 (lancedb **미설치**는 이 오류가 아니다 — 자동으로 flat 폴백된다) |
| `[7단계] … 인덱스가 낡았습니다` | 청킹 후 인덱스를 다시 안 만들었다. `run_rag.py --skip-chunk` |
| `[7단계] 차원 불일치: 인덱스 N차원 vs 질의 M차원` | `embedding.dim`/모델을 바꾸고 인덱스를 재구축하지 않았다. `run_rag.py --skip-chunk` |
| `[7단계] 임베딩 백엔드 준비 실패` | `ask.py` 에는 백엔드 옵션이 없다. `config.yaml` 의 `embedding.backend` 가 인덱스를 만든 값과 같은지 확인 (5.3절 주의) |
| CUDA out of memory | `config.yaml` 의 `embedding.batch_size` 를 8이나 4로 낮춘다. 그래도 안 되면 `reranker.enabled: false` |
| 검색 결과가 엉뚱하다 | `embedding.backend` 가 `hash` 로 돼 있는지 확인. hash는 시험용 스텁이라 품질 보장이 없다 |

오류 메시지는 대부분 한국어로 원인과 해결 명령을 함께 출력하도록 작성돼 있다.
**메시지를 끝까지 읽으면 다음에 실행할 명령이 적혀 있다.**

---

## 10. 요약 — 처음 한 번은 이 순서대로

```powershell
# 1) 경로 설정 (본인 PC에 맞게)
$REPO = "C:\Users\Bae\Dropbox\Claude Code\260725 PDF 추출"
$PY   = "$REPO\.venv\Scripts\python.exe"

# 2) 설치 (3장) — 최초 1회
py -3.12 -m venv "$REPO\.venv"
& $PY -m pip install --upgrade pip
& $PY -m pip install -r "$REPO\pubnexus\requirements.txt"
& $PY -m pip install torch --index-url https://download.pytorch.org/whl/cu124   # ※ pytorch.org에서 확인한 명령으로
& $PY -m pip install "sentence-transformers>=3.0" "transformers>=4.51" lancedb

# 3) GROBID 켜기 (별도 창)
docker run --rm -p 8070:8070 lfoppiano/grobid:latest-full

# 4) 파이프라인 실행
cd "$REPO\pubnexus"
& $PY run_pilot.py        # 0~4단계: PDF → 정본 JSON
& $PY run_rag.py          # 5~6단계: 청킹 → 임베딩 → 인덱스

# 5) 검색
& $PY ask.py "백반증 NB-UVB 재색소침착률" -k 8
& $PY evaluate.py
```

3만 편으로 확장하기 전에 **파일럿 167편으로 이 흐름을 끝까지 한 번 완주**해 볼 것.
6~7단계는 아직 실제 임베딩 모델로 돌아간 적이 없으므로, 첫 완주에서 나오는 로그와
`evaluate.py` 점수가 가장 중요한 정보다.
