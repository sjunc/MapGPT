# MapGPT

자연어 공간 분석 질문을 LLM이 **구조화된 Analysis Plan**으로 바꾸고, 결정론적 검증을 거쳐 **PostGIS**에서 실행한 뒤 지도·순위·설명으로 돌려주는 공간 분석 AI Agent입니다 (MVP: 첫 번째 Vertical Slice).

> "서울에서 어린이 인구 대비 어린이집이 부족한 행정동을 알려줘."
> → 행정동별 `어린이집 수 ÷ 0~5세 인구 × 1,000`을 계산하고 값이 작은 순으로 순위를 매긴 Choropleth 지도

- 설계 개요(팀 공유용): [docs/agent-design-overview.md](docs/agent-design-overview.md)
- 설계: [docs/design/mvp-design.md](docs/design/mvp-design.md)
- 요구사항 원문: [공간 분석 AI Agent 개발 전달 문서.md](docs/공간%20분석%20AI%20Agent%20개발%20전달%20문서.md)

LLM은 질문 해석 · Dataset 선택 · Analysis Plan 생성 · 결과 설명만 합니다. SQL이나 코드를 생성하지 않으며, 모든 계산은 검증된 Plan을 GIS Tool(parameterized SQL)이 실행합니다.

## 백엔드 최초 기동 순서

아래 명령은 모두 **`backend/`에서** 실행합니다. 프로젝트 최상위에 있다면 먼저 `cd backend`를 실행하세요.
각 단계가 성공한 것을 확인하고 다음 단계로 넘어갑니다.

- Python 3.12 이상, PostGIS·pgvector를 지원하는 PostgreSQL, 화면 실행용 Node.js 20+가 필요합니다.
- 외부 PostgreSQL을 사용하면 Docker는 필요하지 않습니다. Docker로 로컬 DB를 만들 때만 Docker를 사용합니다.
- 이미 DB 초기화와 적재·임베딩을 완료했다면 [재기동](#재기동-이미-초기화적재한-경우) 절차를 따릅니다.

### 1. 가상환경 활성화와 의존성 설치

conda와 venv 중 하나를 사용합니다. 기존 환경이 있다면 생성 명령을 생략하고 해당 환경을 활성화합니다.

conda:

```bash
conda create -n mapgpt python=3.12
conda activate mapgpt
```

venv (macOS·Linux):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

venv (Windows PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1   # 실행 정책 오류가 나면: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

가상환경을 활성화한 뒤:

```bash
python -m pip install -e ".[dev]"
```

의존성은 `pyproject.toml`에 있으며, 코드 업데이트 후 의존성이 추가된 경우에도 위 설치 명령을 실행합니다.

### 2. `.env` 설정

`.env`가 없을 때만 `.env.example`을 복사합니다. 기존 `.env`는 덮어쓰지 않습니다.

Windows PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

macOS·Linux:

```bash
test -f .env || cp .env.example .env
```

`.env` 주요 항목:

| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | 사용할 PostgreSQL의 접속 문자열. 외부 DB를 쓰면 반드시 해당 서버 정보로 변경 |
| `LLM_PROVIDER` | `openai` 또는 `ollama` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | OpenAI Responses API (기본 `gpt-6.1-sol`) |
| `OPENAI_EMBEDDING_MODEL` | Dataset 후보 검색 임베딩 (기본 `text-embedding-3-small`). `LLM_PROVIDER=ollama`여도 `OPENAI_API_KEY`가 필요합니다 |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | 로컬 Ollama (예: `ollama pull qwen3.5:9b` 후 `OLLAMA_MODEL=qwen3.5:9b`) |
| `OLLAMA_REASONING_EFFORT` | thinking 모델의 추론 강도. `none`이면 thinking을 끕니다 (qwen3.5:9b 해석 단계 130초 → 6초) |
| `SEOUL_OPEN_API_KEY` | 서울 열린데이터광장 인증키. 어린이집 원본 캐시가 없으면 `ingest all` 전에 반드시 입력 |

외부 DB 접속 문자열 예시(실제 정보로 교체):

```dotenv
DATABASE_URL=postgresql://db_user:encoded_password@db_host:5432/mapgpt
```

사용자명·비밀번호의 `@`, `#`, `/` 등 특수문자는 URL 인코딩합니다. 서버가 TLS를 요구하면 제공받은 SSL 설정도 반영합니다.

### 3. DB 준비와 스키마 초기화

**외부 PostgreSQL:** DB 서버에 해당 PostgreSQL 버전용 PostGIS와 pgvector가 설치되어 있어야 합니다.
전용 데이터베이스와 계정을 먼저 준비하고, 해당 계정이 스키마·테이블을 생성하고 데이터를 읽고 쓸 수 있게 합니다.
확장을 활성화할 권한이 없다면 DB 관리자가 대상 DB에 `postgis`, `vector`를 먼저 활성화해야 합니다.
`init-db`는 데이터베이스 자체나 서버의 확장 패키지를 설치하지 않습니다.

**Docker로 로컬 DB를 만드는 경우에만:** `.env`의 `DATABASE_URL`을 로컬 DB 정보에 맞춘 뒤 실행합니다.

```bash
docker compose up -d --build   # 로컬 PostGIS + pgvector
```

어느 방식이든 접속 설정을 마쳤으면 새 DB에 스키마를 생성합니다.

```bash
python -m app.cli init-db
```

`스키마 적용 완료`를 확인합니다. 확장 기능·권한·접속 오류가 나면 먼저 해결하고 다음 단계로 넘어갑니다.

### 4. 기본 분석 데이터 적재

```bash
python -m app.cli download        # 전국 행정동 경계·인구 원본 → data/raw/ (이미 있으면 생략)
python -m app.cli ingest all      # 전국 경계 → 전국 인구 → 서울 어린이집 순서로 적재
```

`ingest all`은 해당 기본 데이터 테이블의 기존 내용을 교체합니다. 서버를 켤 때마다 실행하는 명령이 아닙니다.
어린이집은 `data/raw/seoul_childcare_YYYYMMDD.json` 캐시가 있으면 사용하고, 없으면 `SEOUL_OPEN_API_KEY`로 수집합니다.
경계·인구 적재 후 어린이집 단계에서 실패했다면 인증키 등을 해결하고 아래 명령으로 이어서 적재할 수 있습니다.

```bash
python -m app.cli ingest daycare_facility
```

어린이집은 `--refresh`로 원본을 다시 수집할 수 있습니다. 적재할 때 운영 중(운영현황 '정상'·'재개')인 시설만 남기고,
좌표 품질 규칙(좌표 없음, 다른 자치구 시설과 같은 기본값 좌표, 신고 자치구 밖 좌표)에 걸린 시설을 제외한 뒤
제외 건수를 카탈로그에 기록합니다.

### 5. 추가 CSV 적재 (선택)

기본 데이터 외에 `data/raw/`에 넣은 CSV도 분석에 사용할 때만 실행합니다. 이미 적재했다면 생략합니다.

`data/raw/`에 CSV를 추가하면 전체 파일을 메모리에 올리지 않고 표본을 읽어 인코딩·헤더·타입·좌표를 추론합니다.
`ingest-files`는 적재 전에 모든 행을 스트리밍으로 한 번 더 읽어 타입을 확정하므로, 표본 뒤에 숫자가 아닌 값('B1' 등)이
있는 컬럼은 문자열이 됩니다. 헤더가 같은 지역별 파일은 하나의 논리 Dataset으로 묶습니다. 행정구역 코드는 이름이나 길이만
믿지 않고 전국 경계 코드와 표본 값의 실제 일치율이 80% 이상일 때만 정규화하며, 시도·시군구·행정동 코드가 함께 있으면 가장
세밀한 단위를 씁니다. 좌표는 모든 행을 한국 범위로 검사하고, 분석 범위(coverage)와 bbox는 적재된 코드·좌표로 계산합니다
(한 시도에만 있으면 그 시도, 여러 시도면 대한민국).

```bash
python -m app.cli inspect-files  # 읽기 전용: 자동으로 묶일 Dataset과 컬럼 확인
python -m app.cli ingest-files   # 스트리밍 COPY → PostGIS 적재 → 카탈로그 임베딩 갱신
```

4단계의 `ingest all`이 성공했다면 전국 경계는 이미 준비되어 있습니다. 추가 CSV만 쓸 경우에는
`init-db`, `download`, `ingest emd_boundary`까지 실행한 뒤 `ingest-files`를 실행합니다. 자동 발견 Dataset은
파일명·헤더·값 패턴으로 확인 가능한 정보만 카탈로그에 기록합니다. 제공기관·라이선스처럼 원본에 없는 정보는 만들지 않으며,
검증된 좌표 또는 행정코드가 없는 Dataset은 공간 분석에 사용하지 않습니다. Dataset 선택과 분석 계획은 기존과 같이 LLM이
카탈로그를 보고 수행하고, SQL은 코드가 생성합니다.

`ingest-files`는 같은 Dataset의 기존 테이블을 교체합니다. 대용량 파일은 오래 걸릴 수 있으며,
현재는 모든 CSV 그룹의 적재가 끝난 뒤 결과를 출력하므로 실행 중 한동안 출력이 없을 수 있습니다.
대상은 `data/raw/` 바로 아래의 CSV이며, 기본 인구 CSV와 XLSX·GeoJSON·JSON은 이 명령으로 적재하지 않습니다.

### 6. 검색 임베딩 갱신 확인

적재 명령은 마지막에 Dataset 메타데이터(제목·설명·태그·컬럼 설명)를 OpenAI로 임베딩해
`catalog.dataset_embedding`에 저장합니다. 적재가 끝나도 임베딩 단계는 실패할 수 있으므로 마지막 출력까지 확인합니다.
임베딩이 없거나 오래되면 분석은 `CATALOG_INDEX_STALE`로 멈춥니다.

```bash
python -m app.cli embed-catalog   # 갱신이 필요한 Dataset만 처리. 모두 최신이면 '변경 없음'
```

`카탈로그 임베딩(...) 갱신: ...` 또는 `변경 없음`을 확인한 뒤 백엔드를 기동합니다.
이 명령은 실제 OpenAI 임베딩 API를 사용합니다. 적재 단계에서 이미 갱신에 성공했다면 생략해도 됩니다.

컬럼이 많은 CSV의 긴 설명은 8,191토큰 이하로 나누어 임베딩하고, 조각의 토큰 수로 가중 평균한 뒤 정규화해
Dataset당 벡터 하나로 저장합니다. 요청은 최대 32개 조각씩 보내 요청 전체의 토큰 제한도 지킵니다.
이전 버전에서 `maximum input length is 8192 tokens` 오류가 났다면, 수정된 코드에서 현재 가상환경의 의존성을
`python -m pip install -e ".[dev]"`로 갱신하고 `python -m app.cli embed-catalog`만 다시 실행하세요.
이미 적재된 CSV를 `ingest-files`로 다시 적재할 필요는 없습니다.

### 선택: 등록 출처 (서울 OpenAPI)

DB에 없는 데이터라도 `sources/*.yaml`로 등록해 둔 출처는 분석 중에 필요하면 받아서 씁니다. 등록 파일에는 서울 OpenAPI
서비스명, 원본 컬럼 → 적재 컬럼 이름과 의미(위도·경도 포함), 대상 지역, 제공기관·라이선스를 적고, 사람이 검토해 커밋합니다.

```bash
python -m app.cli register-sources          # 등록 파일을 카탈로그에 '받기 전' 항목으로 올림 (검색 임베딩 포함)
python -m app.cli fetch-source seoul_public_wifi   # 미리 받아 두기 (행 수 한도 없이 새로 받음)
```

분석에서 받기 전 출처가 선택되면 `acquire_data` 단계가 `SEOUL_OPEN_API_KEY`로 받아 적재한 뒤 계획을 세웁니다. 받은 원본은
`data/raw/sources/`에 날짜별로 남고, 다시 받을 주기(`refresh_days`, 기본 30일) 안에는 그 사본을 다시 씁니다. 질문 처리 중에는
`max_rows`(기본 10만 행) 이하만 받으며, 더 큰 출처는 `fetch-source`로 미리 받아 둡니다. 대상 지역 경계 밖에 떨어진 좌표와
한국 범위 밖 좌표는 위치를 만들지 않고 건수를 품질 노트로 남깁니다. 지금 등록된 출처는 서울시 공공와이파이 위치(OA-20883)입니다.

### 7. 백엔드 기동과 상태 확인

```bash
python -m uvicorn app.api.main:app --reload
```

기동한 뒤 브라우저에서 확인합니다.

- http://localhost:8000/api/health : `status`와 `database`가 모두 `ok`인지 확인
- http://localhost:8000/api/datasets : 적재한 Dataset 목록이 나오는지 확인

`health`는 DB 연결 상태를 확인하며, 데이터 적재나 임베딩 완료까지 보장하지는 않습니다. 4~6단계의 결과도 확인해야 합니다.

## 재기동 (이미 초기화·적재한 경우)

`backend/`에서 기존 가상환경을 활성화하고 아래 명령을 실행합니다. `.env`를 변경했다면 실행 중인 백엔드를 종료한 뒤 다시 기동합니다.

```bash
python -m uvicorn app.api.main:app --reload
```

평소 재기동에는 `download`, `ingest all`, `ingest-files`, `embed-catalog`를 반복하지 않습니다.
의존성이 바뀌면 1단계 설치 명령을, 데이터·메타데이터가 바뀌거나 임베딩이 실패했다면 해당 적재·임베딩 단계만 실행합니다.

## 화면 실행과 분석

백엔드는 켜 둔 상태로, 별도 터미널의 `backend/` 위치에서 실행합니다.

```bash
cd ../frontend
npm install   # 최초 실행 또는 의존성이 바뀐 경우
npm run dev
```

브라우저에서 http://localhost:5173 을 엽니다. CLI로 분석하려면 별도 터미널에서 `backend/`로 이동하고
Python 가상환경을 활성화한 뒤 실행합니다.

```bash
python -m app.cli analyze "서울에서 어린이 인구 대비 어린이집이 부족한 행정동을 알려줘."
python -m app.cli analyze "부산 중구 행정동별 인구 순위" --scope national   # 분석 범위: 서울(기본)·전국
```

API: `POST /api/analyses`, `GET /api/analyses/{run_id}` (Provenance), `GET /api/datasets`, `GET /api/health`

## 테스트

```bash
pytest              # unit + integration (integration은 docker compose의 PostGIS에 mapgpt_test DB를 만들어 사용)
python scripts/check.py   # 작업 후 검증: ruff, pytest(unit + integration, DB 필요), 화면 타입 검사·빌드
```

```bash
pytest -m e2e       # 실제 적재 데이터 + 실제 LLM·OpenAI 임베딩으로 성공 기준 1~12 확인
```

## 데이터 출처와 라이선스

| Dataset | 출처 | 기준 시점 | 라이선스 |
|---|---|---|---|
| 행정동 경계 | 통계청 SGIS 기반 [vuski/admdongkor](https://github.com/vuski/admdongkor) `ver20260701` | 2026-07-01 | CC BY 4.0 (출처: 통계청 SGIS) |
| 연령별 인구 | 행정안전부, [공공데이터포털 15097972](https://www.data.go.kr/data/15097972/fileData.do) | 2026-09-30 | 이용허락범위 제한 없음 |
| 어린이집 | 서울 열린데이터광장 `ChildCareInfo` (OA-20300) | 수집일 기준 | **공공누리 제4유형 (출처표시·상업적 이용금지·변경금지)** |
| 공공와이파이 위치 (등록 출처) | 서울 열린데이터광장 `TbPublicWifiInfo` ([OA-20883](https://data.seoul.go.kr/dataList/OA-20883/S/1/datasetView.do)) | 수집일 기준 | 공공누리 제1유형 (출처표시) |

어린이집 데이터는 상업적 이용이 금지되어 있습니다. 상업 서비스에 쓰려면 이용 조건을 따로 확인해야 합니다. 배경지도는 [OpenFreeMap](https://openfreemap.org) (© OpenMapTiles, OpenStreetMap)입니다.

.
