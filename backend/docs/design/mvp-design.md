# 공간 분석 AI Agent — MVP 설계안 (첫 번째 Vertical Slice)

> 기준 문서: `공간 분석 AI Agent 개발 전달 문서.md` (source of truth)
> 이 문서는 전달 문서 41번 "첫 작업 지시"의 16개 항목에 대한 설계안이며, 구현은 이 설계를 따른다.

## 0. 출발점

### 0.1 Repository 현황 (2026-10-07)

- 커밋된 파일은 `README.md` 하나뿐이다. 코드, 의존성, 인프라 설정이 없는 빈 저장소다.
- 개발 환경: conda `mapgpt` (Python 3.12), Docker Desktop (Apple Silicon), Node 23, Ollama 0.40 (모델 미설치).

### 0.2 사용자 결정 사항

| 항목 | 결정 |
|---|---|
| LLM | 운영은 **OpenAI**, 로컬 테스트는 **Ollama**. 환경변수로 전환한다. |
| Ollama 모델 | 사용자가 `qwen3.5:9b`를 준비했다. 실제 LLM E2E는 이 모델로 수행한다 (OpenAI는 키가 준비되면 같은 테스트로 확인). |
| 어린이집 데이터 | 서울 열린데이터광장 `ChildCareInfo` API. 사용자가 `SEOUL_OPEN_API_KEY`를 `.env`에 설정한다. |
| 경계·인구 원본 | 다운로드 허용 (`data/raw/`, git 제외) |
| 커밋 | 설계 1커밋(AGENTS.md·전달 문서 포함) + 구현 1커밋 |

### 0.3 데이터 조사 결과 (실제 파일 확인)

| Dataset | 원본 | 기준 시점 | 확인 사실 |
|---|---|---|---|
| `emd_boundary` | GitHub `vuski/admdongkor` `HangJeongDong_ver20260701.geojson` (통계청 SGIS 기반, CC BY 4.0), 34.6MB | 2026-07-01 | 전국 3,558개 / **서울 427개**, 전부 MultiPolygon, CRS84(=EPSG:4326 경위도 순서). `adm_cd2`가 10자리 행정안전부 행정기관코드이고, `adm_cd`는 8자리 통계청 코드다. |
| `population_by_age` | 공공데이터포털 `행정안전부_지역별(행정동) 성별 연령별 주민등록 인구수_20260930.csv`, 2.6MB | 2026-09-30 | cp949 인코딩의 wide 형식(230열: `0세남자`…`110세이상 여자`). 전국 3,619개 / **서울 427개** 행정동이며 `행정기관코드`는 10자리다. |
| `daycare_facility` | 서울 열린데이터광장 OpenAPI `ChildCareInfo` | 2026-10-07 | 총 9,495건(폐지·휴지 포함). `STCODE`(앞 5자리 = 시군구코드), `CRSTATUSNAME`(정상/휴지/폐지), `LA`/`LO`를 제공한다. 샘플 5건 중 3건이 **서울시청 좌표(37.566470, 126.977963) 기본값**을 갖고 있었다. |

- 경계와 인구의 10자리 코드는 **427개 모두 일치**한다(누락 0).
- 서울 0~5세 인구는 최소 33명(명동), 최대 2,502명(위례동), 합계 250,170명이다. 0명인 행정동은 없지만 0 분모 정책은 구현하고 테스트한다.

---

## 1. 전체 MVP Architecture

```text
 React + MapLibre UI ──POST /api/analyses──▶ FastAPI (app/api)
                                                  │
                                                  ▼
                                   AnalysisWorkflow (LangGraph, app/workflows)
   ┌───────────────┬───────────────┬──────────────┼───────────────┬───────────────┐
   ▼               ▼               ▼              ▼               ▼               ▼
 LLM Steps     DatasetCatalog  PlanValidator  OperationExecutor ResultValidator RunRepository
 (app/agents)  (app/catalog)   (app/validation) (app/executor)  (app/validation) (Provenance)
   │                                              │
   ▼                                              ▼
 StructuredLLM 포트 (app/llm)                 GIS Tools (app/gis)
 ├ OpenAIResponsesLLM                          get_dataset / get_boundary / filter_dataset
 └ OllamaChatLLM                               aggregate_by_area / aggregate
                                               calculate_ratio / rank / create_map_layer
                                               (내부) transform_crs / validate_geometry
                                                  │  parameterized SQL (psycopg 3)
                                                  ▼
                                   PostgreSQL + PostGIS (Docker)
                                                  ▲
 Ingestion CLI (app/data_sources) ── Provider Adapter ── 외부 원본(GeoJSON / CSV / Seoul API)
```

**계층 책임과 의존 방향**

| 계층 | 책임 | 의존 가능 |
|---|---|---|
| `domain` | Core Contract(Pydantic 모델, Enum, 분석 정책). 외부 I/O가 없다. | (없음) |
| `llm` | 공급자별 Structured Output 호출. 공급자 차이를 숨긴다. | domain |
| `agents` | 프롬프트 구성, LLM 4단계(해석·선택·계획·설명) | domain, llm |
| `catalog` | Dataset 메타데이터 조회, 임베딩 동기화, pgvector 유사도 후보 검색 | domain, llm(임베딩), repositories |
| `validation` | Plan/Result 결정론적 검증 | domain |
| `gis` | GIS Tool, CRS 정책, Filter → SQL 컴파일 | domain |
| `executor` | Operation → GIS Tool 매핑 실행 | domain, gis |
| `workflows` | LangGraph 분석 그래프(노드·조건부 엣지)와 실행기, 최종 응답 조립 | 위 전부, langgraph |
| `data_sources` | 외부 원본 → 내부 표준 Dataset 정규화·적재 | domain, catalog |
| `api` | HTTP 경계 | workflows, catalog |

LLM은 `AnalysisPlan`(구조화 JSON)까지만 만든다. 수치 계산과 공간 연산은 전부 `executor → gis → PostGIS`가 수행한다. LLM이 SQL이나 Python 코드를 생성하는 경로는 존재하지 않는다.

---

## 2. Project Directory

```text
MapGPT/
├── pyproject.toml              # 의존성, pytest 설정
├── docker-compose.yml          # PostGIS (imresamu/postgis:17-3.5, Apple Silicon 네이티브)
├── .env.example                # DATABASE_URL, LLM_PROVIDER, OPENAI_*, OLLAMA_*, SEOUL_OPEN_API_KEY
├── docs/design/mvp-design.md   # 본 문서
├── app/
│   ├── config.py               # Settings (pydantic-settings, .env)
│   ├── cli.py                  # init-db / download / ingest / analyze
│   ├── db/                     # connect·init_schema + schema.sql (DDL, idempotent)
│   ├── domain/                 # ── Core Contract ──
│   │   ├── base.py             # StrictModel (extra="forbid")
│   │   ├── enums.py
│   │   ├── catalog.py          # DatasetMetadata, ColumnMetadata, JoinKey, Spatial/TemporalMetadata
│   │   ├── filters.py          # FilterExpression
│   │   ├── operations.py       # Operation Discriminated Union
│   │   ├── plan.py             # AnalysisPlan, Region, DatasetFilter, OutputSpec
│   │   ├── results.py          # ExecutionResult, ValidationReport, MapLayer, FinalResponse
│   │   ├── state.py            # AgentState, InterpretedQuery, DatasetSelection, Explanation
│   │   └── policies.py         # 분석 정책 (supply_demand_gap: ×1000, 오름차순)
│   ├── catalog/
│   │   ├── definitions.py      # Trusted Dataset 3종의 정적 메타데이터
│   │   └── catalog.py          # 임베딩 문서·동기화, DatasetCatalog.search (pgvector)
│   ├── data_sources/
│   │   ├── adapters/           # admdongkor.py / mois_population.py / seoul_childcare.py
│   │   └── ingest.py           # 정규화 → 품질 규칙 → 적재 → 카탈로그 갱신
│   ├── gis/
│   │   ├── crs.py              # CRS Policy
│   │   ├── filter_sql.py       # FilterExpression → parameterized SQL
│   │   └── tools.py            # GIS Tools
│   ├── executor/
│   │   └── operation_executor.py
│   ├── validation/
│   │   ├── plan_validator.py
│   │   └── result_validator.py
│   ├── llm/
│   │   ├── base.py             # StructuredLLM 포트, LLMError
│   │   ├── schema.py           # Pydantic → 공급자 호환 JSON Schema
│   │   ├── openai_llm.py       # OpenAI Responses API
│   │   ├── ollama_llm.py       # Ollama (OpenAI 호환 Chat Completions)
│   │   ├── embeddings.py       # Embedder 포트, OpenAI 임베딩 (1536차원)
│   │   └── factory.py          # LLM_PROVIDER로 선택, 임베딩은 항상 OpenAI
│   ├── agents/
│   │   ├── prompts.py
│   │   └── steps.py            # interpret / select / plan / explain
│   ├── workflows/
│   │   ├── analysis_workflow.py # LangGraph 분석 그래프 + 실행기(Provenance 저장)
│   │   └── response.py          # 결정론적 응답 섹션(방법·가정·한계) 조립
│   ├── repositories/
│   │   ├── catalog_repository.py
│   │   └── run_repository.py   # Provenance 저장/조회
│   └── api/
│       ├── main.py             # FastAPI app, 라우트
│       └── schemas.py          # Request/Response
├── tests/
│   ├── unit/                   # DB·네트워크 없음
│   ├── integration/            # PostGIS 필요 (mapgpt_test DB)
│   ├── e2e/                    # 실제 데이터 + 실제 LLM (opt-in)
│   ├── fixtures/               # 소형 GeoJSON 샘플, 첫 Use Case 기대 Plan
│   ├── fakes.py                # FakeLLM
│   └── support.py              # 공용 테스트 헬퍼
├── frontend/                   # Vite + React + TypeScript + maplibre-gl
└── data/raw/                   # 원본 데이터 (gitignore)
```

전달 문서 28번의 관심사 분리(LLM/Agent, Domain, Catalog, Adapter, GIS Execution, DB Access, Validation, API)는 그대로 유지한다. 하위 폴더는 파일이 1~2개뿐인 곳을 모듈로 평탄화했다.

---

## 3. Core Domain Model

```text
DatasetMetadata ─┬─ ColumnMetadata[]          (의미 단위 컬럼 설명)
                 ├─ JoinKey[]                 (field, semantic_type, code_system)
                 ├─ SpatialMetadata           (level, geometry_type, crs, coverage, bbox, feature_count)
                 └─ TemporalMetadata          (reference_date, update_cycle)

AnalysisPlan ────┬─ Region                    (name, level)
                 ├─ DatasetFilter[] ── FilterExpression = FilterCondition | FilterGroup
                 ├─ Operation[]  (discriminated union by "type")
                 │     AggregateByArea | Aggregate | CalculateRatio | Rank
                 └─ OutputSpec ── RankingSpec

AgentState ──── InterpretedQuery, DatasetSelection, AnalysisPlan, ValidationReport×2,
                ExecutionResult, MapLayer, FinalResponse, WorkflowError[]

ExecutionResult ─ UnitRow[] (key, name, parent_name, values{metric→값}, null_reasons{metric→사유})
                 ├ MetricInfo[] / OperationLog[] (Provenance용 실행 기록)
                 └ boundary 통계 (unit_count, invalid_geometry_count, bbox)

IntentPolicy (domain/policies.py) ─ intent별 명시적 분석 정책 (ratio scale, rank 방향, 개념 정의, 고정 한계)
```

- 모든 계약 모델은 `StrictModel`(`extra="forbid"`)을 상속한다. 정의되지 않은 필드는 거부한다.
- Enum은 `StrEnum`으로 정의한다. Intent·Operator 등을 확장할 때는 멤버만 추가하면 된다.
- LLM 출력 모델(InterpretedQuery, DatasetSelection, AnalysisPlan, Explanation)은 **공급자 Structured Output 제약**을 만족하도록 설계한다(6.2 참고).

---

## 4. DatasetMetadata Pydantic Schema

```python
class ColumnMetadata(StrictModel):
    name: str
    description: str                 # LLM에 제공하는 의미 설명 (향후 embedding source)
    data_type: DataType              # string | integer | float | boolean | date | geometry
    semantic_type: SemanticType      # administrative_code, population, age, latitude, ...
    unit: str | None = None          # person, year, degree ...
    nullable: bool = False

class JoinKey(StrictModel):
    field: str
    semantic_type: SemanticType
    code_system: str | None = None   # 예: "mois_adm_cd" (행안부 10자리). 통계청 8자리와의 오조인 방지

class RegionRef(StrictModel):
    name: str                        # "서울특별시"
    level: SpatialLevel              # sido
    code: str                        # "11"

class SpatialMetadata(StrictModel):
    level: SpatialLevel | None       # 데이터가 표현하는 공간 단위 (점 데이터는 None)
    geometry_type: GeometryType | None   # Point | Polygon | MultiPolygon | None
    crs: str | None                  # "EPSG:4326" — 공간 데이터는 필수 (validator로 강제)
    coverage: RegionRef | None       # 데이터가 커버하는 지역
    bbox: list[float] | None = None  # EPSG:4326 [minx, miny, maxx, maxy], 적재 시 계산
    feature_count: int | None = None # 적재 시 계산

class TemporalMetadata(StrictModel):
    reference_date: date | None      # 적재 시 원본에서 추출
    update_cycle: str | None         # monthly | irregular ...

class DatasetMetadata(StrictModel):
    dataset_id: str
    title: str
    description: str
    category: str
    semantic_tags: list[str]
    provider: str
    source: str                      # 원본 URL/식별자
    license: str | None
    spatial: SpatialMetadata
    temporal: TemporalMetadata
    columns: list[ColumnMetadata]
    join_keys: list[JoinKey]
    quality_notes: list[str] = []    # 적재 품질 기록 (예: "좌표 오류로 N건 제외") → 결과의 한계로 노출
    acquisition: SourceAcquisition | None = None  # 등록 출처에서 받는 Dataset의 수집 상태 (받은 날짜, 등록 파일 해시)
```

- 저장 테이블은 `datasets.<dataset_id>` 규칙을 따르므로 메타데이터에 저장 위치 필드를 두지 않는다. 식별자는 카탈로그에 등록된 이름만 `sql.Identifier`로 사용한다.
- 지오메트리도 `data_type=geometry` 컬럼으로 `columns`에 포함한다. 필터 대상으로는 쓸 수 없다.

### 4.1 Trusted Dataset 3종 (내부 표준 형태)

| dataset_id | 컬럼 (내부 표준) | join key | spatial |
|---|---|---|---|
| `emd_boundary` | `adm_cd`(10자리), `adm_nm`(동명), `sido_cd`, `sido_nm`, `sigungu_cd`, `sigungu_nm`, `geom` | `adm_cd` (administrative_code, mois_adm_cd) | emd / MultiPolygon / EPSG:4326 / 서울특별시 |
| `population_by_age` | `adm_cd`, `age`(0~110, 110=110세 이상), `population`(남+여), `reference_date` | `adm_cd` (동일) | emd / 지오메트리 없음 / 대한민국 |
| `daycare_facility` | `facility_id`(STCODE), `name`, `facility_type`(국공립·민간·가정…), `latitude`, `longitude`, `geom` (기준 시점은 Dataset temporal) | 없음(공간 관계로 결합) | Point / EPSG:4326 / 서울특별시 |

`daycare_facility`의 포함 기준은 운영현황이 '정상' 또는 '재개'이고 좌표 품질 규칙을 통과한 시설이다(13.3). 제외 건수는 `quality_notes`에 기록한다.

---

## 5. FilterExpression Pydantic Schema

```python
Scalar = str | int | float | bool

class FilterCondition(StrictModel):
    field: str
    operator: FilterOperator        # eq neq gt gte lt lte between in not_in contains starts_with is_null is_not_null
    value: Scalar | list[Scalar] | None = None
    # model_validator: operator별 value 형태 검사
    #   is_null/is_not_null → None | between → 숫자 2개 [low<=high] | in/not_in → 비어있지 않은 list
    #   contains/starts_with → str | 비교 연산자 → 단일 scalar

class FilterGroup(StrictModel):
    logic: LogicalOperator          # and | or | not  (not은 조건 정확히 1개)
    conditions: list[FilterCondition]   # 1개 이상

FilterExpression = FilterCondition | FilterGroup   # 필드 구성으로 구분 (extra=forbid)
```

**설계 결정: Group은 1단계까지만 둔다(재귀 미사용).** 전달 문서는 재귀를 선택사항으로 두었다. 소형 로컬 모델(Ollama)의 출력 안정성과 MVP 필요 범위(13.2 예시)를 고려해 1단계로 제한한다. 필요해지면 깊이 제한 모델을 Union 멤버로 추가한다.

**컴파일 (`gis/filter_sql.py`)**: 메타데이터로 field 존재·타입을 확인한 뒤 `psycopg.sql`로 조립한다.

| operator | SQL | 비고 |
|---|---|---|
| eq / neq | `col = %s` / `col <> %s` | 값은 컬럼 data_type으로 Python 변환(int/float/str/date) |
| gt gte lt lte | `col > %s` … | 숫자·날짜 컬럼만 |
| between | `col BETWEEN %s AND %s` | |
| in / not_in | `col = ANY(%s)` / `NOT (col = ANY(%s))` | list 파라미터 |
| contains / starts_with | `col LIKE %s ESCAPE '\'` | `%`,`_`,`\` 이스케이프 후 `%v%` / `v%`. 문자열 컬럼만 |
| is_null / is_not_null | `col IS NULL` / `col IS NOT NULL` | |
| and / or / not | `(a AND b)` / `(a OR b)` / `NOT (a)` | |

컬럼명은 `sql.Identifier`로 쓰고 값은 전부 파라미터로 넘긴다. LLM이 만든 문자열이 SQL 텍스트에 들어가는 경로는 없다.

---

## 6. AnalysisPlan Pydantic Schema

```python
class Region(StrictModel):
    name: str                        # 공식 명칭 "서울특별시"
    level: SpatialLevel              # sido

class DatasetFilter(StrictModel):
    dataset: str
    where: FilterExpression

class RankingSpec(StrictModel):
    enabled: bool
    limit: int = Field(10, ge=1, le=50)

class OutputSpec(StrictModel):
    map_type: MapType                # choropleth
    metric: str                      # 지도에 표현할 Operation output
    ranking: RankingSpec

class AnalysisPlan(StrictModel):
    intent: AnalysisIntent           # supply_demand_gap
    region: Region
    analysis_unit: SpatialLevel      # emd
    datasets: list[str]              # min 1
    filters: list[DatasetFilter] = []
    operations: list[Operation]      # min 1, 실행 순서
    output: OutputSpec
```

### 6.1 첫 Use Case의 기대 Plan

```json
{
  "intent": "supply_demand_gap",
  "region": {"name": "서울특별시", "level": "sido"},
  "analysis_unit": "emd",
  "datasets": ["emd_boundary", "population_by_age", "daycare_facility"],
  "filters": [
    {"dataset": "population_by_age",
     "where": {"logic": "and", "conditions": [{"field": "age", "operator": "between", "value": [0, 5]}]}}
  ],
  "operations": [
    {"type": "aggregate_by_area", "label": "어린이집 수", "input": "daycare_facility",
     "boundary": "emd_boundary", "aggregation": "count", "output": "daycare_count"},
    {"type": "aggregate", "label": "0~5세 인구", "input": "population_by_age", "group_by": ["adm_cd"],
     "aggregation": {"field": "population", "function": "sum"}, "output": "child_population"},
    {"type": "calculate_ratio", "label": "0~5세 어린이 1,000명당 어린이집 수",
     "numerator": "daycare_count", "denominator": "child_population", "scale": 1000,
     "output": "daycare_per_1000_children"},
    {"type": "rank", "label": "부족 순위", "input": "daycare_per_1000_children", "order": "asc",
     "output": "shortage_rank"}
  ],
  "output": {"map_type": "choropleth", "metric": "daycare_per_1000_children",
             "ranking": {"enabled": true, "limit": 10}}
}
```

### 6.2 전달 문서 예시 대비 변경점과 이유

| 변경 | 이유 |
|---|---|
| `filters`: `{dataset_id: expr}` 객체 → `[{dataset, where}]` 리스트 | OpenAI strict / Ollama 문법 제약은 임의 키 객체(`additionalProperties`가 스키마인 경우)를 표현하지 못한다. 의미는 같다. |
| 각 Operation에 `label` (짧은 한국어 이름) 추가 | 최종 응답의 `metric.description`, 랭킹 표 헤더, 범례에 사람이 읽는 이름이 필요하다. 실제 계산 내용은 결정론적으로 렌더링한 `analysis_method`가 별도로 보여준다. |
| 모든 필드를 LLM 스키마에서 required로 둠 (nullable로 생략 표현) | OpenAI strict 모드 규칙이다. Pydantic 기본값은 코드에서 생성할 때만 쓰인다. |

---

## 7. Operation Discriminated Union

```python
class AggregateByAreaOperation(StrictModel):
    type: Literal["aggregate_by_area"]
    label: str
    input: str                       # Point Dataset ID
    boundary: str                    # Polygon Dataset ID
    aggregation: AreaAggregation     # count (MVP)
    output: MetricName               # ^[a-z][a-z0-9_]*$

class AggregationSpec(StrictModel):
    field: str
    function: AggregationFunction    # sum | count

class AggregateOperation(StrictModel):
    type: Literal["aggregate"]
    label: str
    input: str                       # Dataset ID
    group_by: list[str]              # MVP: 분석 단위 join key 1개
    aggregation: AggregationSpec
    output: MetricName

class CalculateRatioOperation(StrictModel):
    type: Literal["calculate_ratio"]
    label: str
    numerator: str                   # 앞선 Operation output
    denominator: str                 # 앞선 Operation output
    scale: float = Field(1.0, gt=0)
    output: MetricName

class RankOperation(StrictModel):
    type: Literal["rank"]
    label: str
    input: str                       # 앞선 Operation output
    order: SortOrder                 # asc | desc
    output: MetricName

Operation = Annotated[
    AggregateByAreaOperation | AggregateOperation | CalculateRatioOperation | RankOperation,
    Field(discriminator="type"),
]
```

- **참조 규칙**: `input`/`boundary`(공간·속성 집계)는 Dataset ID를, `numerator`/`denominator`/`rank.input`은 **앞에서 정의된** Operation output을 가리킨다. 모든 output은 분석 단위(행정동) key별 값이다.
- 새 Operation은 기존 스키마를 바꾸지 않고 Union 멤버와 executor handler를 추가하는 방식으로 확장한다. 단위 테스트로 Union의 모든 멤버에 handler가 있는지 확인한다.
- LLM용 JSON Schema 변환(`llm/schema.py`)에서는 Pydantic이 만든 `oneOf`+`discriminator`를 `anyOf`로 바꾸고, `const`는 `enum`으로 바꾸고, 수치·길이 제약은 제거한다. 제거한 제약은 응답을 받은 뒤 Pydantic이 다시 검증한다.

---

## 8. Agent State

```python
class InterpretedQuery(StrictModel):        # LLM 출력
    intent: AnalysisIntent | None            # None = 지원 범위 밖
    unsupported_reason: str | None
    region: Region | None
    region_phrase: str | None  # 질문에서 지역을 말한 표현 (없으면 분석 범위의 기준 지역)
    analysis_unit: SpatialLevel | None
    analysis_unit_phrase: str | None  # 질문에서 결과 단위를 말한 표현 (질문에 없으면 가장 세밀한 단위로 보정)
    restated_question: str                   # 분석 질문 재진술
    key_concepts: list[str]                  # 카탈로그 검색어 (예: 어린이집, 0~5세 인구)
    assumptions: list[str]                   # 해석 과정의 가정

class DatasetSearch(StrictModel):           # pgvector 후보 검색 기록
    query: str                               # restated_question + key_concepts
    embedding_model: str
    hits: list[DatasetHit]                   # dataset_id, similarity(코사인), 유사도 내림차순

class DatasetSelection(StrictModel):        # LLM 출력
    datasets: list[str]
    rationale: str

class Explanation(StrictModel):             # LLM 출력
    summary: str

class WorkflowError(StrictModel):
    step: WorkflowStep
    code: str                                # UNSUPPORTED_QUESTION, PLAN_INVALID, LLM_FAILED ...
    message: str
    issues: list[ValidationIssue] = []

class AgentState(BaseModel):
    run_id: str
    question: str
    scope: AnalysisScope = "seoul"           # 화면 설정의 분석 범위 (seoul | national)
    status: RunStatus                        # running | succeeded | failed
    current_step: WorkflowStep | None = None
    interpreted_query: InterpretedQuery | None = None
    resolved_region: RegionRef | None = None  # 등록 지역과 대조해 확정한 분석 지역 (행정코드로 경계를 거름)
    dataset_search: DatasetSearch | None = None      # 검색어·임베딩 모델·후보별 유사도
    dataset_candidates: list[DatasetMetadata] = []   # LLM이 본 메타데이터를 그대로 기록
    dataset_selection: DatasetSelection | None = None # LLM 선택 원본 (근거 포함, 검사 전에 기록)
    selected_datasets: list[str] = []                 # 검사를 통과한 선택 (중복 제거)
    data_acquisitions: list[DataAcquisition] = []     # 선택 뒤 등록 출처에서 받아 적재한 기록
    analysis_plan: AnalysisPlan | None = None
    plan_history: list[PlanAttempt] = []   # 재생성을 포함한 모든 계획 시도 (plan + 검증 결과)
    plan_validation: ValidationReport | None = None
    execution_results: ExecutionResult | None = None
    result_validation: ValidationReport | None = None
    map_layer: MapLayer | None = None
    final_response: FinalResponse | None = None
    errors: list[WorkflowError] = []
    llm_provider: str
    llm_model: str
    started_at: datetime
    finished_at: datetime | None = None
```

상태 전체를 Provenance(`analysis.run.state`)에 JSONB로 저장한다. "LLM이 무엇을 보고 무엇을 판단했는가"를 재현하기 위해서다. `AgentState`는 LangGraph 그래프 상태로 그대로 쓰이고, LLM·카탈로그·DB 세션처럼 기록하지 않는 실행 의존성은 Runtime context로 주입한다.

---

## 9. GIS Tool Interfaces

Tool은 `app/gis/tools.py`의 타입 있는 함수다. 실패하면 `GisToolError(code, message)`를 던진다. 데이터는 PostGIS에 그대로 두고, View는 (메타데이터 + 필터) 참조만 들고 다닌다(지연 평가).

```python
class DatasetView(BaseModel):     metadata; filter: FilterExpression | None; row_count: int
class BoundaryView(DatasetView):  region: RegionRef; key_field; name_field; parent_name_field
                                  unit_count; invalid_geometry_count; bbox: list[float]
class MetricValues(BaseModel):    name; values: dict[key, float|int|None]
                                  null_reasons: dict[key, str]; stats: dict[str, int|float]
```

전달 문서 18번의 `result_ref`는 Operation의 `output` 이름이다. Executor가 `MetricValues`를 그 이름으로 `ExecutionContext.metrics`에 등록한다.

| Tool | 목적 | Input → Output | Validation | 실패 조건 (code) |
|---|---|---|---|---|
| `get_dataset` | 등록 Dataset 테이블 참조 확보 | metadata → DatasetView | 테이블 존재, row>0 | `DATASET_TABLE_MISSING`, `DATASET_EMPTY` |
| `get_boundary` | 분석 지역의 경계 Polygon 집합 | metadata, region → BoundaryView | Polygon 계열, CRS 명시, 지역 해석, key 유일, `validate_geometry` | `NOT_POLYGON`, `CRS_MISSING`, `REGION_NOT_FOUND`, `DUPLICATE_UNIT_KEY`, `INVALID_GEOMETRY` |
| `filter_dataset` | FilterExpression을 View에 적용 | view, expr → DatasetView | field 존재, operator·값 타입 호환, 결과 row>0 | `FILTER_FIELD_UNKNOWN`, `FILTER_TYPE_MISMATCH`, `NO_MATCHING_DATA` |
| `aggregate_by_area` | Point → Polygon 공간 집계 | input view, boundary view, `count` → MetricValues | Point·Polygon 여부, CRS 정규화(`transform_crs`), 지원 aggregation | `NO_GEOMETRY`, `NOT_POINT`, `NOT_POLYGON`, `CRS_UNSUPPORTED`, `UNSUPPORTED_AGGREGATION` |
| `aggregate` | 속성 그룹 집계 | view, group_by, field, function → MetricValues | group_by 1개, field 존재·숫자형(sum) | `FIELD_UNKNOWN`, `FIELD_NOT_NUMERIC`, `MULTI_GROUP_UNSUPPORTED`, `UNSUPPORTED_AGGREGATION` |
| `calculate_ratio` | 분자/분모×scale | 두 MetricValues, scale, unit keys → MetricValues | 분모 0/없음 → `null` + 사유, 결과 유한값 | `NON_FINITE_RESULT` |
| `rank` | 순위 | MetricValues, order → MetricValues | null 제외, 동순위 표준 경쟁 순위(1,2,2,4) | — |
| `create_map_layer` | 지도 GeoJSON (단계 구분도 면 + 비례 원 대표점) | boundary view, 결과 행, metric, 지도 보기(`map_style`), 점 위치 → MapLayer | 출력 EPSG:4326, 좌표 경위도 범위, 모든 feature에 geometry·`label_point` | `OUTPUT_CRS_INVALID`, `GEOMETRY_MISSING` |
| `point_locations` | 지도 지표로 센 Point 위치 (점 지도·밀도 지도) | 필터가 적용된 Point view, boundary view, limit(1만) → PointLayer 또는 없음 | `aggregate_by_area`와 같은 단위 배정(분석 지역 밖·위치 없음 제외, 경계선 위 점은 한 단위), 출력 EPSG:4326, limit 초과면 보내지 않음 | `NOT_POINT`, `CRS_UNSUPPORTED` |
| `hex_bins` | 지도 지표로 센 Point의 육각 격자 | 필터가 적용된 Point view, boundary view, 목표 칸 수(1,000) → HexLayer | EPSG:5179에서 `ST_HexagonGrid`(칸 크기는 분석 지역 범위로 50m 단위), 점 위치와 같은 점 선택, 칸 경계 위 점은 한 칸, 점이 있는 칸만, 출력 EPSG:4326 | `NOT_POINT`, `CRS_UNSUPPORTED` |
| `bivariate_spec` | 비율 지표의 이변량 구간 | 결과 행, 분모·분자 (output, 라벨) → BivariateSpec | 두 값이 모두 있는 단위로 각각 3분위 경계값 | — |
| `transform_crs` (내부) | 연산 전 CRS 정규화 | geom SQL, src, dst → SQL 조각 | 지원 SRID {4326, 5179} | `CRS_UNSUPPORTED` |
| `validate_geometry` (내부) | 유효성 점검 | view → {invalid, empty, null} 개수 | `ST_IsValid`, `ST_IsEmpty` | (호출자가 판정) |

**aggregate_by_area SQL 요지**

```sql
WITH units AS (SELECT key, geom FROM boundary WHERE <region filter>),
     pts AS (SELECT id, <transform_crs(geom → boundary SRID)> AS geom FROM input WHERE <filter>),
     assigned AS (SELECT DISTINCT ON (p.id) p.id, u.key
                  FROM pts p JOIN units u ON ST_Covers(u.geom, p.geom)
                  ORDER BY p.id, u.key)              -- 경계선 위 점은 코드가 작은 행정동 하나에만 배정
SELECT u.key, count(a.id) FROM units u LEFT JOIN assigned a USING (key) GROUP BY u.key;
```

시설이 없는 행정동은 0이다. 지역 밖 점(미배정)의 수는 `stats.unassigned_points`에 기록하고 결과 검증에서 경고로 보고한다.

**CRS Policy (`gis/crs.py`)**: 지도 출력은 EPSG:4326이다. Point-in-Polygon처럼 위상 연산은 경계 Dataset의 SRID로 맞춘다. 거리·면적 연산(EPSG:5179)은 두 번째 Vertical Slice에서 정책에 추가한다. 지도 보기의 육각 격자는 같은 넓이의 칸을 미터로 만들어야 해서 EPSG:5179(`AREA_SRID`)에서 만든다. LLM은 CRS를 결정하지 않는다.

---

## 10. Plan Validator 구조

`validate_plan(plan, datasets: Mapping[str, DatasetMetadata], selected: Sequence[str]) -> ValidationReport`

순수 함수다(DB 없음). 규칙 함수들이 `ValidationIssue(code, severity, message, path)`를 누적한다. Operation을 순서대로 훑으면서 **심볼 테이블**(정의된 metric 이름 → 출처·숫자 여부)을 만든다. 오류가 1개라도 있으면 실행하지 않는다.

| 전달 문서 20번 항목 | 규칙 (code) |
|---|---|
| Dataset ID가 실제 존재하는가 | `UNKNOWN_DATASET` |
| 필요 Dataset이 모두 있는가 | `DATASET_NOT_DECLARED`(참조했는데 `datasets`에 없음), `DATASET_NOT_SELECTED`(선택 단계 밖), `UNUSED_DATASET`(경고) |
| Filter field가 존재하는가 | `FILTER_FIELD_UNKNOWN`, `FILTER_TYPE_MISMATCH`, `DUPLICATE_FILTER` |
| Operation input이 존재하는가 | `OPERATION_INPUT_UNKNOWN` (Dataset 또는 *앞서 정의된* metric) |
| output 이름 충돌 | `OUTPUT_NAME_CONFLICT` (중복, Dataset ID·경계 컬럼명과 충돌) |
| Geometry가 있는가 / 타입이 적절한가 | `GEOMETRY_REQUIRED`, `GEOMETRY_TYPE_INVALID` (input=Point, boundary=Polygon 계열) |
| CRS를 처리할 수 있는가 | `CRS_UNSUPPORTED` (CRS 누락 또는 정책 밖 SRID) |
| Join 가능한 키/공간 관계가 있는가 | `JOIN_KEY_INCOMPATIBLE` (group_by가 경계 join key와 semantic_type·code_system·level 일치) |
| Ratio denominator가 유효한가 | `RATIO_OPERAND_INVALID` (숫자 metric, 분자≠분모, 같은 Dataset·필터·집계로 만든 같은 값끼리 나누기 금지, rank 결과 사용 금지) |
| 지원하지 않는 Operation | Pydantic discriminated union이 1차로 거부하고, handler 누락은 테스트로 보장한다 |
| Output metric이 실제 계산되는가 | `OUTPUT_METRIC_NOT_COMPUTED`, `RANKING_NOT_DEFINED` (ranking.enabled면 output.metric에 대한 rank 존재) |
| (보완) 분석 단위·지역 | `UNIT_BOUNDARY_MISSING`/`AMBIGUOUS`(analysis_unit의 Polygon Dataset이 정확히 1개), `REGION_NOT_COVERED` (region이 Dataset coverage와 일치) |
| (보완) 분석 정책 준수 | `POLICY_VIOLATION` (supply_demand_gap: output.metric이 ratio이고 scale=1000, rank=asc) |
| (보완) 인구 집계 | `POPULATION_NOT_SUMMED` (인구 컬럼은 sum만. 나이별 행의 개수·평균은 지역 인구가 아님) |

**계획 보정**: 검증 전에 의도가 하나로 정해지는 기계적인 실수(위치 없는 인구 표의 공간 집계, 같은 Dataset의 나뉜 필터, 지역을 다시 말한 경계 필터, 순위를 지도 지표로 지정)를 고치고 `PLAN_REPAIRED` 경고와 원래 계획(`original_plan`)을 남긴다. 고친 계획도 같은 Validator를 통과해야 한다.

**재생성 루프**: Pydantic 파싱 실패나 Plan Validator 오류가 나면 오류 목록을 LLM에 피드백해 **최대 1회** 다시 만든다. `regional_summary`가 그래도 실패하면 메타데이터 기반 최소 대체 Plan을 같은 Validator로 검사해 실행하고, 대체 Plan도 만들 수 없으면 `PLAN_INVALID`로 종료한다. 대체 Plan은 필터를 쓰지 않으므로, 실패한 계획에 경계가 아닌 Dataset 필터(질문의 조건)가 있었으면 만들지 않는다.

---

## 11. Operation Executor 구조

```python
class ExecutionContext:                 # load_data 단계에서 생성
    conn                                # 읽기 전용 트랜잭션 (REPEATABLE READ, READ ONLY)
    plan: AnalysisPlan
    views: dict[str, DatasetView]       # 필터가 적용된 Dataset View
    boundary: BoundaryView              # 분석 단위 경계 (결과 프레임의 기준)
    metrics: dict[str, MetricValues]    # output 이름(result_ref) → 결과
    logs: list[OperationLog]            # type, output, tool, 입력 요약, stats, duration_ms

HANDLERS: dict[str, Handler] = {
    "aggregate_by_area": _run_aggregate_by_area,   # → tools.aggregate_by_area
    "aggregate":         _run_aggregate,           # → tools.aggregate
    "calculate_ratio":   _run_calculate_ratio,     # → tools.calculate_ratio
    "rank":              _run_rank,                # → tools.rank
}

def prepare_context(conn, plan, datasets) -> ExecutionContext   # get_boundary / get_dataset / filter_dataset
def execute_operations(ctx) -> ExecutionResult                  # 순서대로 handler 실행 → 결과 조립
```

- **결과 조립**: 경계 View의 행정동 목록(key, 동명, 자치구명)을 기준으로, 각 metric을 key로 LEFT JOIN해 `UnitRow`를 만든다.
- **결측 의미**: count는 0이다(관측된 0). sum 결과가 없는 단위는 `null`이다(인구 정보 없음). ratio의 분모가 0이면 `null` + `denominator_zero`, 분모가 없으면 `null` + `denominator_missing`이다. 0으로 대체하지 않는다.
- 단위 목록에 매칭되지 않은 metric key(예: 경계에 없는 인구 코드)는 `ExecutionResult.unmatched_keys`에 기록한다.

---

## 12. 첫 Vertical Slice Sequence

```text
UI ─POST /api/analyses {question}─▶ API ─▶ AnalysisWorkflow.run(question)
 1 interpret_query      LLM(InterpretedQuery)  ← 질문 + 지원 intent/단위 + 카탈로그 coverage + 분석 정책
                        intent=None이면 UNSUPPORTED_QUESTION, 데이터 없는 지역·단위면 REGION/UNIT_NOT_SUPPORTED로 종료
 2 select_datasets      재진술+key_concepts 임베딩 → pgvector 코사인 상위 10개 후보 → LLM(DatasetSelection)
                        → 후보 내 ID인지 확인. 임베딩이 없거나 오래되면 CATALOG_INDEX_STALE로 종료
 2-1 acquire_data      선택한 등록 출처가 아직 없거나 오래됐으면 서울 OpenAPI에서 받아 적재 (계획 전에)
 3 build_analysis_plan  LLM(AnalysisPlan)      ← 해석 + 선택 Dataset 메타데이터(컬럼/join key/공간/시간) + 정책
 4 validate_plan        PlanValidator → 오류면 3으로 1회 복귀(피드백), regional_summary 재실패 시 검증된 최소 대체 Plan, 불가하면 PLAN_INVALID
 5 load_data            get_boundary(emd_boundary, 서울특별시) / get_dataset(population_by_age)
                        + filter_dataset(age between 0~5) / get_dataset(daycare_facility)
 6 execute_operations   aggregate_by_area(count) → daycare_count
                        aggregate(sum population by adm_cd) → child_population
                        calculate_ratio(÷, ×1000) → daycare_per_1000_children
                        rank(asc) → shortage_rank → UnitRow 조립
 7 validate_result      ResultValidator → 오류면 RESULT_INVALID(지표가 모두 0이면 NO_MATCHING_DATA), 경고는 한계로 전달
 8 create_map_layer     create_map_layer → GeoJSON(EPSG:4326) + 5분위(quantile) 구간 + 대표점 + 지도 보기(style) + 점 위치(points)·육각 격자(hexbins)·이변량 구간(bivariate)
 9 generate_explanation LLM(Explanation)       ← 상위 랭킹·요약 통계·경고·기준 시점 (주어진 수치만 사용)
10 compose & persist    FinalResponse 조립(방법·가정·한계는 결정론적) → analysis.run / analysis.result 저장
 ◀─ {run_id, status, result, errors} ── UI: 지도 + Ranking + Metric + Dataset·기준 시점 + 방법·가정·한계
```

각 단계는 LangGraph 노드이고(노드 이름 = `WorkflowStep`), 바뀐 상태 필드만 반환한다. 단계 안의 예외는 노드가 `WorkflowError`로 `errors`에 기록하고(그 전에 기록한 값은 유지), 조건부 엣지가 `errors`를 보고 END로 보낸다. 계획 루프는 `build_analysis_plan`(스키마 불일치 → 자기 자신)과 `validate_plan`(오류 → `build_analysis_plan`) 뒤의 라우터가 정한다. 그래프가 끝나면 실행기가 `succeeded`/`failed`를 정하고, 성공이든 실패든 항상 Provenance를 저장한다.

**최종 응답(FinalResponse) 구성 원칙**: LLM이 쓰는 것은 `summary`뿐이다. `metric`, `ranking`, `map_layer`, `datasets`(+기준 시점), `analysis_method`(실행 Plan으로 렌더링), `assumptions`(정책 + 해석 가정), `limitations`(정책 고정 한계 + Dataset quality_notes + 검증 경고 + 기준 시점 차이)는 결정론적으로 조립한다.

---

## 13. 필요한 DB Table

### 13.1 스키마

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector (docker/postgres 이미지)
CREATE SCHEMA catalog;  CREATE SCHEMA datasets;  CREATE SCHEMA analysis;

-- Dataset Catalog
CREATE TABLE catalog.dataset (
  dataset_id text PRIMARY KEY,
  metadata   jsonb NOT NULL,              -- DatasetMetadata 전체 (적재 통계·품질 기록 포함)
  loaded_at  timestamptz NOT NULL
);

-- Dataset 후보 검색 임베딩 (Dataset당 1개)
CREATE TABLE catalog.dataset_embedding (
  dataset_id  text PRIMARY KEY REFERENCES catalog.dataset ON DELETE CASCADE,
  model       text NOT NULL,              -- 예: text-embedding-3-small
  source_text text NOT NULL,              -- 임베딩한 문서. 현재 메타데이터로 만든 문서와 다르면 오래된 임베딩
  embedding   vector(1536) NOT NULL,
  embedded_at timestamptz NOT NULL
);
CREATE INDEX ON catalog.dataset_embedding USING hnsw (embedding vector_cosine_ops);

-- 내부 표준 Dataset
CREATE TABLE datasets.emd_boundary (
  adm_cd text PRIMARY KEY, adm_nm text NOT NULL,
  sido_cd text NOT NULL, sido_nm text NOT NULL, sigungu_cd text NOT NULL, sigungu_nm text NOT NULL,
  geom geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX ON datasets.emd_boundary USING gist (geom);

CREATE TABLE datasets.population_by_age (
  adm_cd text NOT NULL, age integer NOT NULL CHECK (age >= 0),
  population integer NOT NULL CHECK (population >= 0), reference_date date NOT NULL,
  PRIMARY KEY (adm_cd, age)
);

CREATE TABLE datasets.daycare_facility (
  facility_id text PRIMARY KEY, name text NOT NULL, facility_type text,
  latitude double precision NOT NULL, longitude double precision NOT NULL,
  geom geometry(Point, 4326) NOT NULL
);
CREATE INDEX ON datasets.daycare_facility USING gist (geom);

-- Provenance
CREATE TABLE analysis.run (
  run_id uuid PRIMARY KEY, question text NOT NULL, status text NOT NULL,
  analysis_plan jsonb, datasets jsonb,      -- [{dataset_id, reference_date}]
  operations jsonb, crs text,               -- 실행 Operation 기록, 분석 CRS
  result_id uuid, llm_provider text, llm_model text,
  errors jsonb, state jsonb NOT NULL,       -- AgentState 전체
  started_at timestamptz NOT NULL, finished_at timestamptz
);
CREATE TABLE analysis.result (
  result_id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES analysis.run,
  response jsonb NOT NULL,                  -- FinalResponse (GeoJSON 포함)
  created_at timestamptz NOT NULL
);
```

전달 문서 24번 Provenance 항목(run_id, 질문, Dataset ID·기준 시점, Plan, 실행 Operation, CRS, 실행 시각, 결과 ID)은 모두 `analysis.run`의 컬럼에 매핑된다.

### 13.2 적재 (`python -m app.cli ingest all`)

순서: `emd_boundary` → `population_by_age` → `daycare_facility`(경계를 품질 규칙에 쓰므로 마지막에 적재). Dataset마다 한 트랜잭션에서 테이블을 교체한다. 그 뒤 `bbox`·`feature_count`·`reference_date`·`quality_notes`를 계산해 `catalog.dataset`을 upsert한다. 마지막으로 임베딩 문서가 바뀐 Dataset만 다시 임베딩한다(`python -m app.cli embed-catalog`와 같음).

임베딩 문서(전달 문서 34번)는 제목·설명·분류·태그·지역과 컬럼별 `이름: 설명 (semantic_type)`이다. 적재 통계(feature 수, bbox, 기준일)는 넣지 않으므로 다시 적재해도 임베딩은 바뀌지 않는다. 공간 데이터 자체(Polygon·Point)는 임베딩하지 않는다.

- `emd_boundary`: `sido == "11"`만 남긴다. `adm_cd2` → `adm_cd`, `adm_nm`은 마지막 토큰(동명), `ST_Multi(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)))`.
- `population_by_age`: cp949로 전국 행정동을 읽는다. 연령별 남+여를 long 형식 `(adm_cd, age, population)`으로 변환한다(110세 이상 → 110). `기준연월` → `reference_date`. 정합성(계 = 연령 합) 검사. 어댑터의 선택적 시도 코드 필터는 단위 테스트와 제한 적재에만 사용한다.
- `daycare_facility`: API를 1,000건 단위로 페이지 수집한다(원본 JSON은 `data/raw/`에 캐시).

### 13.3 어린이집 좌표 품질 규칙 (적재 시, 결정론적)

1. 운영현황 `정상`·`재개`(휴지 후 운영 재개)만 포함한다(휴지·폐지·미기재 제외).
2. 좌표가 없거나, 파싱되지 않거나, 대한민국 범위 밖이면 제외한다.
3. **서로 다른 자치구의 시설들이 같은 좌표를 공유하면** 기본값 좌표로 보고, 그 좌표의 시설을 모두 제외한다(서울시청 기본값 대응).
4. 좌표가 위치한 자치구가 신고 자치구(`STCODE` 앞 5자리)와 다르거나 서울 경계 밖이면 제외한다.

규칙별 제외 건수는 `quality_notes`에 기록하고 최종 응답의 한계에 노출한다. 실제 전체 데이터를 적재한 뒤 제외 규모를 확인해 보고한다.

---

## 14. REST API

| Method | Path | 설명 |
|---|---|---|
| `POST` | `/api/analyses` | `{"question": str, "scope": "seoul" \| "national" = "seoul"}` → 워크플로를 동기 실행한다. `200 {run_id, status, result: FinalResponse \| null, errors: WorkflowError[]}`. 분석 실패(지원 범위 밖, Plan 무효 등)도 run이 기록되므로 200 + `status="failed"`다. 요청 형식 오류는 422, 서버 장애는 500이다. |
| `GET` | `/api/analyses/{run_id}` | Provenance(상태 전체)와 결과를 조회한다. 없으면 404. |
| `GET` | `/api/datasets` | 카탈로그 메타데이터 목록 |
| `GET` | `/api/health` | DB 연결, LLM provider/model, 임베딩 모델 |

**FinalResponse (전달 문서 22번)**

```python
class FinalResponse(StrictModel):
    run_id: str; result_id: str; question: str
    summary: str                                   # LLM
    metric: MetricDescription                      # name, label(설명), interpretation("낮을수록 공급 부족")
    ranking: list[RankingItem]                     # rank, unit_key, unit_name, parent_name, value, values{}
    map_layer: MapLayer                            # GeoJSON FeatureCollection + quantile breaks
    analysis_method: list[str]                     # 실행 Plan 렌더링
    datasets: list[DatasetUsage]                   # dataset_id, title, provider, source, reference_date, license
    assumptions: list[str]
    limitations: list[str]
```

**UI (frontend/)**: 질문 입력, 단계 진행 표시, MapLibre 단계 구분도(5분위, 순위 방향 쪽을 진하게 강조, null은 회색, hover 툴팁)와 비례 원(개수·합계 지표의 기본 보기, 단계 구분도로 전환 가능), 점 지도·밀도 지도·육각 격자(지표가 시설 개수일 때, 위치·밀집 질문의 기본 보기), 이변량 지도(비율 지표의 분자·분모), 랭킹 표(순위·자치구·동·어린이집 수·0~5세 인구·지표), Metric/Dataset(기준 시점)/분석 방법/가정/한계 패널, 실패 시 단계와 오류 목록. 배경지도는 키가 필요 없는 OpenFreeMap 벡터 스타일(positron / dark, © OpenMapTiles · OpenStreetMap)이다. 개발 서버는 `/api`를 FastAPI로 프록시한다.

---

## 15. Test Strategy

| 계층 | 범위 | 환경 |
|---|---|---|
| **Unit** | 도메인 스키마(유효/무효 Plan, Filter value 형태, union 판별, 미지원 type 거부), LLM JSON Schema 변환(oneOf·dict·기본값 없음, 전 필드 required, additionalProperties=false), Filter SQL 컴파일(연산자별 SQL·파라미터, 주입 시도 값이 파라미터로만 전달), Plan Validator(규칙별 음성 케이스 + 정상 Plan), ratio/rank(0·결측 분모, 동순위, null), Result Validator 규칙, Adapter(소형 fixture 파일 파싱), LLM 어댑터 요청 형식(HTTP mock), Executor handler가 Union 전 멤버를 커버하는지 | DB·네트워크 없음 |
| **Integration** | GIS Tool을 합성 데이터로 검증한다: 정사각형 2개 경계, 내부/외부/공유 경계선 위 점, **EPSG:5179로 저장한 점의 CRS 정규화**, 0 분모·결측 단위 인구. 정확한 count·ratio·rank, Choropleth GeoJSON 유효성, 적재 파이프라인(fixture → 테이블 → 카탈로그), Workflow 전체를 FakeLLM으로 실행해 FinalResponse·Provenance 저장 확인, API(TestClient), FakeEmbedder(단어 해싱)로 pgvector 순위·임베딩 동기화·오래된 임베딩 거부 | Docker PostGIS의 `mapgpt_test` DB. 연결이 불가하면 사유와 함께 skip |
| **E2E** (opt-in `-m e2e`) | 실제 적재 데이터 + 실제 LLM(OpenAI 또는 Ollama)으로 대표 질문을 실행해 40번 성공 기준 1~12를 단언한다 | API 키 또는 Ollama 모델 필요 |

LLM 응답은 비결정적이므로 Unit/Integration에서는 FakeLLM(사전에 정한 구조화 출력)과 FakeEmbedder를 쓴다. "LLM이 올바르게 해석·선택·계획하는가"는 E2E에서 확인한다.

---

## 16. 구현 순서

| # | 단계 | 검증 |
|---|---|---|
| 1 | 골격: `pyproject.toml`, `docker-compose.yml`, `config`, `db`, `schema.sql`, `cli init-db`, `.gitignore`, `.env.example` | PostGIS 기동, `init-db` 반복 실행이 멱등 |
| 2 | **Phase 1 Core Schema**: domain 모델 전체 + LLM JSON Schema 변환 | Unit: 스키마 유효/무효, 첫 Use Case Plan 파싱 |
| 3 | **Phase 2 Dataset**: 3개 Adapter + 적재 + 카탈로그 정의 + 좌표 품질 규칙 | Unit(fixture), 실제 적재 후 행정동 427·코드 매칭·좌표 제외 규모 확인 |
| 4 | **Phase 3 GIS Execution**: Filter 컴파일, GIS Tools, Executor | Integration(합성 데이터 정답 비교), 6.1 Plan을 실제 데이터에 직접 실행 |
| 5 | **Phase 5 Validation**: Plan Validator, Result Validator, Geometry/CRS 검증 | Unit(규칙별), 실제 결과에 Result Validator 통과 |
| 6 | **Phase 4 LLM Integration**: 포트 + OpenAI/Ollama 어댑터 + 프롬프트 + 4단계 + 상태 머신 Workflow + Provenance | Unit(FakeLLM, HTTP mock), Integration(Workflow 전체) |
| 7 | **Phase 6 API/UI**: FastAPI + React/MapLibre | API 테스트, 브라우저에서 지도·랭킹·패널 확인 |
| 8 | 마무리: README 실행 방법, 전체 테스트, 커밋 | `pytest` 통과, diff 검토 |

Validation(5)을 LLM 연동(6)보다 먼저 한다. LLM 출력은 처음부터 검증 게이트를 통과해야만 실행되어야 하기 때문이다. 결정론적 파이프라인을 손으로 쓴 Plan으로 먼저 완성해 두면, LLM은 그 Plan을 생성하는 역할만 하면 된다.

---

## 부록 A. 명시적 가정

1. 데이터는 서울만 적재한다(coverage = 서울특별시). 다른 지역 질문은 Plan Validation에서 `REGION_NOT_COVERED`로 명시적으로 실패한다.
2. 행정동 코드는 행정안전부 10자리 행정기관코드로 통일한다(통계청 8자리 미사용).
3. 0~5세는 주민등록 기준 만 나이 0~5세(1세 단위 남+여 합계)다. 이 개념 정의는 분석 정책에 둔다.
4. 어린이집 수는 운영 중('정상'·'재개') 시설 수다. 정원·현원은 반영하지 않는다(한계로 명시).
5. 데이터 기준 시점은 경계 2026-07-01, 인구 2026-09-30, 어린이집 2026-10-07이며 서로 다르다는 점을 한계로 명시한다.
6. 경계선 위의 시설은 하나의 행정동에만 배정한다(코드 오름차순 우선).
7. 순위는 표준 경쟁 순위이고 null은 제외한다. 기본 상위 10개다.
8. `POST /api/analyses`는 분석이 끝날 때까지 응답을 기다린다(LLM 4회 호출). 비동기 Job은 범위 밖이다.
9. OpenAI 기본 모델은 `gpt-6.1-sol`(공식 모델 페이지, 2026-10 기준)이고 `OPENAI_MODEL`로 바꿀 수 있다. Ollama 모델은 `OLLAMA_MODEL`로 지정한다.

## 부록 B. LLM Provider 전략

- 포트: `StructuredLLM.generate(system, user, output_type: type[T]) -> T`. 공급자별 차이는 어댑터 안에 숨긴다.
- `OpenAIResponsesLLM`: Responses API, `text.format = {type: json_schema, strict: true}`.
- `OllamaChatLLM`: OpenAI SDK의 `base_url`을 Ollama `/v1`로 지정하고 Chat Completions `response_format=json_schema`, `temperature=0`을 쓴다. Ollama 권장사항에 따라 스키마를 프롬프트에도 포함한다.
- 공통: `llm/schema.py`가 만든 호환 스키마를 보내고, 응답은 `model_validate_json`으로 다시 검증한다. 거절·잘림(refusal, length)·JSON 오류는 `LLMError`로 명시적으로 처리한다.
- 전환: `.env`의 `LLM_PROVIDER=openai|ollama`.
- 임베딩: `Embedder.embed(texts) -> list[list[float]]` 포트, `OpenAIEmbedder`(`OPENAI_EMBEDDING_MODEL`, 기본 `text-embedding-3-small`, `dimensions=1536`). `LLM_PROVIDER`와 관계없이 OpenAI를 쓴다.

## 부록 C. 이번 범위에서 하지 않는 것

전달 문서 30번 목록을 따른다(Vector DB, Dynamic Discovery, Multi-Agent, MCP, OGC API, Sedona, Raster, Routing, Isochrone, 입지 최적화, Knowledge Graph, 임의 코드/SQL 실행, Plugin System). 단, pgvector Dataset 후보 검색은 사용자 요청으로 구현했다(부록 D). 그 밖에 다음도 하지 않는다: 비동기 Job 큐, 마이그레이션 도구(Alembic), 인증, Vector Tile, 행정동을 분석 지역으로 잡는 질의(시군구까지는 지원, 부록 D).

## 부록 D. 구현 중 변경·보완 사항

- **카카오 지도 화면 기능 맞춤(2026-11-21)**: Vercel 배포의 `/`가 카카오 화면(`?demo=1&map=kakao`)으로 가므로,
  사용자가 승인한 8단계 계획으로 카카오 화면을 MapLibre 화면(`App`)과 같은 기능으로 채웠다. `App`과 백엔드는 바꾸지 않았다.
  - **주소:** `map=kakao`만 있으면 카카오 화면을 열고, 그 안에서는 주소가 화면을 정한다. `run`이 있으면 저장된 실행(실패면 빈 지도와 오류),
    `demo=1`이면 가상 데모, 둘 다 없으면 빈 지도다. 분석하거나 실행을 열면 `?map=kakao&run=<id>`로 `pushState`하고,
    첫 진입과 뒤로 가기는 `App`처럼 요청 순번으로 늦은 응답을 버리며 되살린다. 데모는 늘 불러 두되 주소가 데모일 때만 보인다.
  - **실패:** `ErrorPanel`과 실행 기록(`ExecutionTrace`)을 보이고, `App`처럼 직전 성공 결과를 지도에 남기되 "직전 결과"로 표시한다.
  - **보기:** 카카오 API에 GeoJSON 레이어·히트맵이 없어 보기마다 객체를 만든다. 점은 `MarkerClusterer`로 모든 점을 묶고(700개 제한 제거),
    비례 원은 값이 0보다 큰 모든 단위를 MapLibre와 같은 크기(`symbolMaxRadius × √(값 ÷ 최댓값)`)로 그린다(150개 제한 제거).
    육각 격자는 칸 `Polygon`과 단위 경계 `Polyline`, 밀도는 지도 위 canvas에 `simpleheat`로 그린다. 밀도 크기는 카카오 레벨 L을
    MapLibre 줌 약 18.8 − L(레벨 9 축척 4km=58px)로 바꿔 MapLibre 화면과 같은 반지름·세기를 쓴다. simpleheat는 밀도를 불투명도로도 써서
    드문 곳이 MapLibre보다 옅다. 이변량은 실제 결과에 `theme.ts`의 색을 쓰고 시연용 팔레트는 데모에만 남겼다.
  - **범례·맞춤·순위:** 범례에 서버 구간 경계값과 `style.note`(MapLibre처럼 보기가 둘 이상이면 모든 보기)를 보인다. 결과 맞춤은
    `fitResultBounds`처럼 지도 위 UI 위치를 재서 여백을 두고(범례가 지도 폭의 절반보다 좁으면 왼쪽, 넓으면 아래, 지도는 80px 이상),
    순위 클릭도 같은 여백으로 그 지역에 맞춘다(기존 `panTo` 직후 `setLevel`은 이동이 끊겨 원래 중심에서 확대됐다).
    "전체 순위 보기"는 응답의 순위 값으로 정렬만 한다.
  - **확대·축소(2026-11-22):** 카카오 `ZoomControl`(오른쪽 위 세로 막대)이 모든 폭에서 상단 표시·지도 버튼(휴대폰에서는 보기 전환)과
    겹쳐, 사용자와 정해 지도 버튼 묶음의 +/− 버튼(MapLibre 화면의 +/−와 같다)으로 바꿨다. 막대를 끌어 배율을 고르는 기능은 없어지고,
    휠·핀치·더블클릭 확대는 그대로다.
  - **하지 않은 것:** 카카오 화면 다크 모드, 마우스 올림 툴팁, 진행 중 실행의 자동 조회.

- **개인 서버 API·Vercel 화면 분리 배포(2026-11-19)**: 사용자 배포 구성에 맞춰 백엔드는
  `CORS_ALLOW_ORIGINS` JSON 배열의 명시적 Origin만 허용한다(기본 `[]`, GET·POST, Content-Type, credentials 없음).
  화면은 `VITE_API_BASE_URL`을 분석·기록 API와 원본 JSON 링크에 공통 적용한다. 미설정 시 기존 상대 경로를 유지한다.
  공개 API의 HTTPS와 Origin별 허용 목록을 운영 환경에 설정하며, DB·LLM 계약과 인증 방식은 변경하지 않는다.

- **긴 카탈로그 설명 임베딩(2026-11-19)**: 컬럼 248개의 범죄 통계 CSV 설명이 임베딩 입력 한도를 넘어
  전체 갱신이 실패했다. 어댑터에서 `tiktoken`의 `cl100k_base`로 입력을 8,191토큰 이하로 나누고,
  최대 32개 조각씩 요청해 입력별·요청 전체 토큰 제한을 지킨다. 조각별 벡터를 토큰 수로 가중 평균한 뒤
  L2 정규화해 기존 Dataset당 1,536차원 벡터 계약을 유지한다. 짧은 입력의 벡터는 그대로 쓴다.
  원문을 자르지 않으므로 뒤쪽 컬럼도 반영되지만, 여러 의미를 평균한 검색 품질은 별도 평가가 필요하다.
  카탈로그 원문·DB 스키마는 바꾸지 않으며, 의존성 갱신 후 `embed-catalog`만 재실행하면 된다.

- **전국 경계와 자동 CSV 발견(2026-11-02)**: 기존 서울 행정동 경계는 첫 Use Case 호환용으로 유지하고, 같은 원본에서
  `national_emd_boundary`, `sigungu_boundary`, `sido_boundary`를 적재한다. `inspect-files`는 UTF-8/CP949 CSV를
  스트리밍 표본 검사하고 동일 헤더 partition을 묶으며, `ingest-files`는 typed table을 트랜잭션에서 교체한다. 좌표는
  국내 범위와 컬럼 의미가 함께 맞을 때 Point로 만들고, 행정코드는 전국 경계의 실제 코드와 표본 일치율 80% 이상일 때만
  `unit_key`로 정규화한다. LLM은 자동 생성된 카탈로그에서 Dataset과 typed plan을 선택하되 SQL은 계속 만들지 않는다.
  원본에 없는 제공기관·라이선스는 추측하지 않고 미확인으로 남긴다. 이는 전달 문서에서 후속 범위로 둔 Dynamic Dataset
  Discovery를 사용자가 앞당겨 요청한 변경이다.

설계를 따르되 구현과 검증 과정에서 아래를 바꾸거나 보완했다.

| 항목 | 내용 | 이유 |
|---|---|---|
| 배경지도 | CARTO 래스터 → OpenFreeMap 벡터 스타일, Choropleth는 도로·면 위·레이블 아래에 삽입 | CARTO 타일이 API 키를 요구하게 되어 워터마크가 표시됨 |
| MapLibre worker | `maplibre-gl-worker.mjs?worker&url` + `setWorkerUrl` | MapLibre v6는 번들러 환경에서 worker 경로를 직접 지정해야 함 |
| Choropleth 색 | 단일 색상(blue) ordinal 5단계, 라이트는 진할수록·다크는 밝을수록 공급 부족, 값 없음은 회색 | 부족 지역 강조. dataviz `validate_palette.js --ordinal` 라이트·다크 모두 통과 |
| FinalResponse | `metric_labels`(output → label), `rank_metric` 추가 | 랭킹 표·툴팁 열 이름과 전체 표의 순위 열을 결정론적으로 제공 |
| Plan Validator | `BOUNDARY_FILTER_UNSUPPORTED`, `BOUNDARY_NOT_ANALYSIS_UNIT`, `DUPLICATE_FILTER`, `FIELD_UNKNOWN`, `FIELD_NOT_NUMERIC` 추가 | 경계 Dataset 필터는 기대 단위 수 검증과 충돌하고, 공간 집계 결과는 분석 단위에 키가 맞아야 함 |
| Workflow 검증 | 해석 단계에서 데이터 없는 지역은 `REGION_NOT_SUPPORTED`로 바로 종료, 계획 검증에 `PLAN_INTERPRETATION_MISMATCH`(Plan이 해석된 intent·지역·단위를 바꾸지 않았는지) 추가 | LLM이 지원 지역으로 몰래 바꾸는 것을 결정론적으로 차단하고 불필요한 LLM 호출을 줄임 |
| GIS 런타임 객체 | `DatasetView`·`BoundaryView`·`MetricValues`는 dataclass | 외부 계약이 아닌 실행 중 참조 객체. 계약(로그·결과)은 Pydantic으로 남김 |
| 실행 트랜잭션 | load_data~create_map_layer를 하나의 `REPEATABLE READ, READ ONLY` 트랜잭션에서 실행 | 단계 사이 데이터 일관성, 쓰기 차단 |
| 어린이집 운영 기준 | 운영현황 `정상`과 `재개`를 운영 중으로 포함 | 실데이터 확인 결과 `재개` 75건은 폐지일이 없고 대부분 현원이 있는 운영 시설 |
| 어린이집 행별 기준일 | 원본 `DATASTDRDT`를 열로 두지 않고 최댓값을 Dataset 기준 시점으로만 기록 | 행별 값은 시설 정보 갱신일이라 기준일 필터로 오인되어, 실제 LLM이 정상 시설 45건을 제외하는 계획을 만들었음 |
| Plan Validator | `OUTPUT_METRIC_IS_RANK` 추가 | 실제 LLM이 순위 결과를 지도 지표로 지정하는 오류를 반복해, 고칠 방향을 담은 피드백이 필요했음 |
| Provenance | `plan_attempts`(횟수) 대신 `plan_history`(시도별 Plan·검증 결과) | 재생성 전후의 판단을 모두 추적 |
| Ollama | `OLLAMA_REASONING_EFFORT`(선택) → `reasoning_effort` | thinking 모델(qwen3.5:9b) 해석 단계가 130초→6초로 단축되어 로컬 E2E가 실용적이 됨 |
| 플래너·해석 프롬프트 | 요구되지 않은 필터(기준일·유형 전체 나열) 금지, 정책 정의를 가정으로 반복하지 않기 | 실제 LLM 실행에서 관찰된 과잉 조건·중복 가정 |
| 분석 단위 조기 확인 | 해석 프롬프트에 지역별 분석 가능한 단위를 표시하고 "사용자가 말한 단위를 바꾸지 않는다" 규칙 추가, 해석 직후 경계 데이터가 없는 단위는 `UNIT_NOT_SUPPORTED`로 종료 | 시군구 질문이 LLM 4회 이상 호출 뒤 `PLAN_INVALID`로 끝나던 것을 1회 호출로 줄임. 가능한 단위만 보여주면 모델이 '구별'을 행정동으로 바꿔버릴 수 있어 단위 목록은 유지 |
| 데이터 선택 근거 기록 | `AgentState.dataset_selection`에 LLM 선택 원본(근거 포함)을 검사 전에 저장 | 선택 근거가 버려져 Agent 판단을 추적할 수 없었음. 잘못된 선택으로 실패한 경우도 기록 |
| Dataset 후보 검색 | 카탈로그 전체(dataset_id 순 10개) 대신 pgvector 코사인 유사도 상위 10개(OpenAI `text-embedding-3-small`, HNSW). 검색 기록은 `dataset_search`로 Provenance에 남김. 임베딩이 없거나 다른 모델이거나 임베딩 문서가 현재 메타데이터와 다르면 `CATALOG_INDEX_STALE`로 종료 | 사용자 요청. 전달 문서 30번은 MVP 범위 밖으로 두었지만 33·34번 구조(pgvector는 후보 검색, LLM은 최종 선택, Validator는 검증)를 그대로 적용. 임베딩이 빠진 Dataset이 후보에서 조용히 사라지는 것을 막음 |
| 해석 프롬프트 region.level | `region.level`은 지역 자체의 행정 단위(서울특별시 → sido)이고 결과를 나눌 단위는 `analysis_unit`이라는 규칙 추가. `REGION_NOT_SUPPORTED` 메시지에 지역 단위를 함께 표시 | gpt-4o-mini가 `region.level`에 분석 단위(emd)를 넣어 해석 10회 중 8회가 `REGION_NOT_SUPPORTED`로 실패(수정 후 10회 모두 sido). 필드 description은 strict 스키마 변환에서 `$ref` 옆 키워드로 제거되므로 프롬프트에 둠 |
| Workflow 구현 | 직접 작성한 상태 머신(`WorkflowStep → 핸들러` dict) → LangGraph `StateGraph`. 상태는 `AgentState` 그대로, 의존성은 Runtime context, 실패는 `errors` + 조건부 엣지로 END. 외부 인터페이스 `AnalysisWorkflow.run`은 유지 | 사용자 요청. 전달 문서 25번은 LangGraph를 선택 사항으로 두고 Core Domain·GIS가 프레임워크에 종속되지 않을 것을 요구하므로 LangGraph 의존을 `app/workflows`에 한정 |
| 질문 해석 재판정 | 첫 해석이 `unsupported`이면 지역별 개수·합계·평균·비율·순위 가능성을 명시해 1회 재시도하고, 두 번째도 지원 불가일 때만 종료 | 같은 상가 순위 질문이 성공과 실패를 반복하는 실제 모델 오판을 줄이되, Plan/Result 검증과 결정론적 계산은 유지 |
| 전국 인구 적재 | `population_by_age`를 서울 코드만 선별하지 않고 원본의 전국 3,619개 행정동 전체로 적재하고 coverage를 대한민국으로 변경 | 전국 경계만 확장되고 인구는 서울로 남아 대구 인구 질문에서 경계 Dataset을 인구처럼 쓰는 잘못된 Plan이 생성되던 불일치 해소 |
| 분석 단위·경계 보정 | 질문에 단위 표현이 없으면 가능한 가장 세밀한 단위를 선택하고, Dataset 선택 뒤 해당 단위·지역과 맞는 경계를 코드가 추가 | LLM이 같은 질문에서 `emd`와 `sigungu`를 오가거나 해석과 다른 경계를 선택해 Plan 검증이 실패하는 변동 제거 |
| 지역 통계 대체 Plan | `regional_summary` Plan이 두 번 모두 실패하면 Point 개수 또는 호환 join key별 수치 합계와 내림차순 순위를 메타데이터로 구성하고 다시 검증 | 검증을 제거하거나 수치를 환각하지 않으면서 관련 Dataset으로 계산 가능한 지도를 최대한 반환 |
| 지역 확정 (2026-11-14) | 해석한 지역을 등록 지역(Dataset coverage, 전국 경계의 시도·시군구)과 대조해 하나로 정하고 행정코드를 `AgentState.resolved_region`에 기록. 시군구 이름은 '시도 시군구'로 고유하게 만들고, 경계는 이름 대신 이 코드로 거른다. 여러 곳에 맞으면 `REGION_AMBIGUOUS` | 전국 경계에서 '중구'를 고르면 서울·부산 등 5곳의 행정동이 한 분석에 섞였고, LLM이 '부산광역시중구'로 쓰면 `REGION_NOT_FOUND`가 났음. 시도 coverage Dataset(서울 어린이집 등)의 하위 시군구 분석은 여전히 범위 밖 |
| 분석 단위 판정 (2026-11-14) | 키워드 목록 대신 LLM이 `analysis_unit_specified`로 질문에 결과 단위가 있는지 판단하고, false일 때만 가장 세밀한 단위로 보정 | '자치구 중', '가장 많은 구는?', '시도 중', '시·도별'에서 사용자가 말한 단위를 행정동으로 덮어썼음 (qwen3.5:9b에서 자치구 질문이 행정동 순위로 나옴) |
| 결합 실패 경고 범위 (2026-11-14) | `UNMATCHED_KEYS`는 분석 지역 코드로 시작하는 코드만 알림 (결합 키가 행안부 계층 코드라는 전제) | 전국 인구로 서울을 분석하면 다른 시도 코드 3,192개가 결합 실패로 한계에 붙었음 |
| 자동 CSV 적재 보완 (2026-11-14) | 코드 컬럼이 여럿이면 가장 세밀한 단위 선택, 적재 전 모든 행으로 타입 확정(nan·inf는 숫자로 보지 않음), 헤더 셀 공백 정리, 좌표를 모든 행에서 한국 범위로 검사, coverage·bbox를 적재된 값으로 계산, 시도명·시군구명·행정동명 의미 유형 지정 | 시도코드가 앞에 있으면 시도 단위로 등록됐고, 표본 뒤 'B1' 하나로 적재 전체가 실패했으며, 헤더 공백만으로 실패하고, 서울·부산 데이터가 대한민국 범위로 기록되는 등 메타데이터가 실제와 달랐음 |
| 인구·경계 불일치 노트 (2026-11-14) | 인구 적재 시 전국 경계에 없는 행정동 코드 수·인구를 품질 노트로 기록 | 경계(2026-07-01판)와 인구(2026-09-30) 기준 시점 차이로 62개 행정동(약 26만 명)이 경고 없이 분석에서 빠졌음 |
| 자동 Dataset 한계 문구 (2026-11-14) | 자동 발견 Dataset 한계를 `regional_summary` 정책이 아니라 그 Dataset을 쓴 분석에만 붙임 | 기존 Dataset만 쓴 지역 통계에도 자동 발견 한계가 표시됐음 |
| 등록 출처 수집 (2026-11-14) | 출처 등록 파일(`backend/sources/*.yaml`, PyYAML `safe_load` + Pydantic 검증)로 승인한 서울 OpenAPI 출처를 카탈로그에 "받기 전" 항목으로 올리고, 선택되면 `acquire_data` 단계가 계획 전에 받아 적재. 등록 출처는 `public_api` 분류이고 컬럼 의미는 등록 파일 값을 쓰며, 대상 지역 밖 좌표는 위치를 만들지 않음. 질문 처리 중 한도 10만 행, 다시 받는 주기 기본 30일, 원본 사본 재사용, 동시 수집은 advisory lock | DB에 없는 데이터를 공공 API로 받아 답하려는 요구. 전달 문서 36번(검증 없이 쓰지 않음)과 데이터 전달 문서 5.3의 선언 파일·신뢰 단계를 따르되, 서울 일반 인증키 하나로 모든 일반 OpenAPI를 부를 수 있어 서울부터 시작. YAML은 데이터 전달 문서의 선언 파일 예시와 같고 데이터 쪽에서 가장 흔한 형식 |
| 하위 지역 분석 (2026-11-14) | Dataset 범위가 분석 지역을 포함하면(행정안전부 계층 코드 앞자리로 판단: 강남구 11680 ⊂ 서울 11) 그 Dataset을 쓴다. 해석에서 확정한 지역(`resolved_region`)을 분석 단위 계산·경계 보정·계획 검증·실행 준비에 넘기고, 범위가 더 넓은 점 데이터의 지역 밖 점은 `UNASSIGNED_POINTS`로 알리지 않는다 | 서울 범위 Dataset(어린이집·공공와이파이)으로 "서울특별시 강남구" 같은 시군구 지역을 분석하면 `REGION_NOT_COVERED`로 멈췄음. 코드를 모르는 이름만으로는 판단하지 않아 서울 데이터를 서울 밖에 쓰는 일은 계속 막힌다 |
| 데이터 없음 안내 (2026-11-15) | 선택 결과에 `required_data`(필요한 데이터마다 그 데이터를 주는 후보 Dataset, 없으면 null)를 두고, 출처가 null이거나 후보에 없는 ID면 선택 단계에서 `DATA_NOT_AVAILABLE`로 멈추며 검색 후보(경계 제외)를 함께 안내한다. 선택한 Dataset 범위가 분석 지역을 포함하지 않을 때도 계획 전에 같은 코드로 멈춘다 | 약국처럼 등록되지 않은 데이터를 물으면 LLM이 없는 ID(`pharmacy_facility`)를 지어내 `INVALID_DATASET_SELECTION`이라는 내부 문구로 멈췄고, 모델에 따라 비슷한 다른 데이터로 대신 답할 위험이 있었음. 범위 밖 지역은 계획을 두 번 만든 뒤에야 계획 검증에서 멈췄음. 처음에는 '없는 데이터' 목록만 받았는데, gpt-4o-mini가 '어린이 인구'·'0~5세 인구'처럼 필터로 만드는 데이터와 지역이 붙은 표현을 없다고 적어 성공해야 할 질문 5개 중 4개를 막았음. 출처를 묻는 형식으로 바꿔 고르면서 없다고 하는 모순이 생기지 않게 했다 |
| 틀린 답 막기 (2026-11-15) | 계획 검증에 `POPULATION_NOT_SUMMED`(인구 컬럼은 합계만), 결과 검증에 `CONSTANT_METRIC`(값 있는 단위가 2개 이상인데 출력 지표가 모두 같으면 오류)를 추가했다. 실패한 계획에 경계가 아닌 Dataset 필터가 있었으면 `regional_summary` 대체 Plan을 쓰지 않는다. 설명용 사실에 순위 방향(`ranking_order`)을 넣고 설명 규칙에 반영했으며, 플래너에 "`regional_summary` 순위는 적은 쪽을 묻지 않는 한 desc" 규칙을 추가했다. `REGION_NOT_COVERED` 문구는 계획에 적힌 지역 대신 실제로 검사한 지역을 쓴다 | gpt-4o-mini 재현에서 성공한 7건 중 5건이 틀린 답이었음. '평균 소득'을 0~5세 인구의 나이별 평균으로 답했고, '부유한 동네'·'평균 소득'(gpt-4o-mini)과 '0~5세 인구'(qwen3.5:9b)에는 대체 Plan이 조건을 버리고 전체 인구로 답했으며, 오름차순 순위의 최솟값을 '가장 많다'고 설명했음. 팀원이 본 '모두 1'(경계 행 세기 등)도 검증을 그대로 통과했음. 대체 Plan(BE/jjc 추가)은 조건이 없는 질문(공공와이파이 개수 등)에는 계속 쓴다 |
| 해석 근거 확인 (2026-11-15) | `analysis_unit_specified`(참/거짓) 대신 `analysis_unit_phrase`(질문에서 결과 단위를 말한 표현을 그대로 옮긴 것)를 받는다. 그 표현이 질문에 실제로 있고(공백 무시, 지역 이름 부분 제외) 고른 단위의 행정구역 글자(동·읍·면 / 구·군·시 / 도·시)가 들어 있을 때만 LLM이 고른 단위를 쓰고, 아니면 가장 세밀한 단위로 보정한다. 해석 규칙에 "`supply_demand_gap`은 시설이 인구에 비해 부족한지 물을 때만, 시설 수나 인구 수 한 가지만 세거나 순위를 매기면 `regional_summary`"를 추가했다 | gpt-4o-mini 해석 단계 8문항×3회에서, 단위를 자치구로 맞게 고르고도 "말하지 않았다"고 표시해 행정동으로 바뀐 경우가 3회, 개수·인구 질문('자치구별 어린이집 수 순위', '0~5세 인구가 가장 많은 행정동')을 `supply_demand_gap`으로 해석한 경우가 7회였음. 수정 후 같은 조건에서 각각 1회, 0회였고, 남은 1회(단위가 아닌 말 "어린이집이 가장 많은 곳"을 옮겨 적음)는 행정구역 글자 확인으로 막았다(기록된 출력 24개로 다시 판정해 0회). 11-14의 "키워드 목록 대신 LLM 판단" 결정은 유지하고, 판단 근거를 질문 글자로 확인한다 |
| 분석 범위 토글 (2026-11-15) | 화면 헤더의 "분석 범위" 토글(서울·전국, 기본 서울, 브라우저에 기억)이 `POST /api/analyses`에 `scope`를 함께 보내고, 실행 기록(`AgentState.scope`)에 남는다. 서울 범위면 해석에 서울과 그 안의 구만 보여 준다. LLM이 옮겨 적은 지역 표현(`region_phrase`)이 질문에 있고 해석한 지역을 가리킬 때만 그 지역을 쓰고, 아니면 범위의 기준 지역(서울특별시·대한민국) 전체를 분석하며 가정에 남긴다. 서울 범위에서 서울 밖 등록 지역을 물으면 `REGION_OUT_OF_SCOPE`로 멈추고 전국으로 바꾸라고 안내한다 | 사용자 결정: 서울 OpenAPI가 잘 갖춰져 있어 서울을 기본으로 하고 전국은 차차 확장한다. 지역을 말하지 않은 질문이 '대한민국'으로 해석돼 실패했고(팀 피드백), 전국 273곳 지역 목록이 해석 프롬프트의 87%를 차지했다(서울 범위 3,233자). 범위는 서버 설정이 아니라 요청 값이라 사용자마다 다르게 쓸 수 있고 실행을 다시 확인할 수 있다 |
| 계획 보정 (2026-11-15) | 계획 검증 전에 `plan_repair`가 의도가 하나로 정해지는 기계적인 실수만 고친다. ① 위치가 없는 인구 표의 공간 집계 → 행정코드별 인구 합계 ② 같은 Dataset에 나뉜 필터 → AND 하나(같은 조건의 반복, 서로 다른 컬럼, 한 컬럼의 하한·상한 한 쌍일 때만) ③ 확정 지역을 다시 말한 경계 필터(지역 이름·코드와 정확히 같을 때) → 제거 ④ 순위를 지도 지표로 지정 → 순위를 매긴 지표. 고친 내용은 `PLAN_REPAIRED` 경고로, LLM이 낸 원래 계획은 `PlanAttempt.original_plan`으로 남고, 고친 계획도 같은 Validator를 통과해야 실행된다. ⑤ `ranking.limit`은 스키마에서 1 미만이면 10, 50 초과면 50으로 맞춘다. 같은 Dataset·필터·집계로 만든 두 지표의 비율은 `RATIO_OPERAND_INVALID`로 막고, 플래너에 "`regional_summary`는 질문이 묻지 않은 비율·연령 조건을 만들지 않는다" 규칙을 더했다 | gpt-4o-mini 실행 42회 중 계획 단계까지 간 34회의 79%가 계획을 두 번 만들었고(재시도마다 3~15초), 14회는 계획 실패로 끝났다. 가장 많은 원인은 인구 표의 공간 집계였다. 보정 후 8문항에서 재생성 5→1회, 시간 150→106초였지만, 고쳐진 계획 3건이 '어린이 인구 ÷ 인구'를 같은 필터로 계산해 결과가 모두 1000이 됐다(`CONSTANT_METRIC`). 같은 값 비율 검사와 플래너 규칙을 더한 뒤 같은 8문항 성공이 4→7개(재생성 2/8, 118초)가 됐다. 다만 성공 중 1건은 질문에 없는 '시설 유형 = 국공립' 필터를 붙인 틀린 답이었고, '인구 1,000명당 와이파이' 질문에서는 규칙에도 0~5세 조건이 붙었다(질문에 없는 조건을 막는 일은 남은 과제). 뜻이 갈릴 수 있는 경우(인구 컬럼 없는 표의 행 개수, 같은 컬럼의 다른 조건, 지역 코드 일부)는 답이 바뀔 수 있어 고치지 않는다. 없는 Dataset ID를 지어낸 경우와 순위 연산 누락(방향을 알 수 없음)도 고치지 않는다 |
| 필터 근거 확인 (2026-11-15) | 질문에 근거가 없는 필터를 고치지 않고 오류로 알려 다시 만들게 한다. ① 범주 값(시설 유형·범주·이름) 조건은 값이 질문에 있거나 질문의 낱말이 값의 일부여야 한다(`FILTER_NOT_IN_QUESTION`) ② 일반 통계 계획의 나이 0~5세 조건은 질문이 어린이·영유아·유아·아동·아이나 0~5세를 말해야 한다(공급 부족 분석은 정책이 정하므로 제외, 같은 코드) ③ 경계가 아닌 데이터 표에도 행정코드·지역 이름 필터를 두지 않는다(`REGION_FILTER_UNSUPPORTED`) ④ 범주 컬럼의 값 유무(is_null 등) 조건도 질문에 없으면 같은 오류로 알린다. 플래너 프롬프트에는 이 규칙을 예시와 함께 넣지 않는다 | gpt-4o-mini가 '서울 자치구별 어린이집 수 순위'에 '시설 유형 = 국공립'(다른 실행에서는 '0', '어린이집')을, '인구 1,000명당 공공와이파이 수'에 0~5세 조건을, '부산 중구 인구 순위'에 지어낸 행정동 코드를 붙였다. 앞의 둘은 성공으로 나간 틀린 답이었다. 계획 보정처럼 자동으로 지우면 필요한 조건을 잘못 지울 때 다른 질문의 답이 되므로 오류로만 알린다. 다른 말로 표현한 값('공공' → '국공립')이나 목록에 없는 어린이 표현은 다시 만들거나 실패할 수 있다. 질문에 있는 말이지만 실제 값이 아닌 경우(시설 유형 = '어린이집')는 실제 값 정보가 필요해 남긴다. 처음에 플래너 규칙 3에 '행정코드·지역 이름', '시설 유형'을 예로 들어 넣었더니 gpt-4o-mini가 서울 기본 질문에 '시설 유형 값 없음', 강남구 질문에 잘못된 구 코드 필터를 새로 붙여 두 질문이 실패했다. 그래서 프롬프트 문구는 되돌렸다 |
| 조건에 맞는 데이터 없음 (2026-11-15) | 필터 결과가 0건이면 `FILTER_EMPTY_RESULT` 대신 `NO_MATCHING_DATA`로 멈추고, 조건 문구와 조건에 쓴 문자열 컬럼의 실제 값(최대 20개)을 함께 안내한다. 값 있는 단위가 2개 이상인데 출력 지표가 모두 0이면 결과 검증이 `CONSTANT_METRIC` 대신 `NO_MATCHING_DATA`로 "이 지역에는 조건에 맞는 데이터가 없습니다"라고 알리고, 워크플로는 `RESULT_INVALID` 대신 이 코드로 멈춘다. 화면은 `DATA_NOT_AVAILABLE`·`NO_MATCHING_DATA`면 제목을 "데이터가 없습니다"로 바꾼다 | 조건에 맞는 데이터가 없는 질문(서울에 없는 시설 유형, 직장 어린이집이 없는 구 등)이 내부 코드(`FILTER_EMPTY_RESULT`)나 "값이 모두 같아 비교할 수 없다"는 계획 오류 문구로 끝나, '없다'는 답인지 시스템 실패인지 알 수 없었다. 실제 값을 함께 보여 주면 값 표기가 틀린 조건(시설 유형 = '어린이집' 등)도 알아볼 수 있다. 0이 아닌 같은 값(경계 행을 세어 모두 1 등)은 계획 실수 신호라 `CONSTANT_METRIC`으로 남긴다 |
| 지도 보기 (2026-11-15) | 지도 지표를 만든 Operation으로 성격을 정해(`domain/maps.py`) 비율·평균은 단계 구분도, 개수·합계는 비례 원을 기본으로 하고 단계 구분도로 바꿔 볼 수 있게 했다. 단위마다 `label_point`(`ST_PointOnSurface`)를 담고, 색 강조 방향은 지도 지표의 순위 방향, 범례 양끝 문구는 정책(`legend_labels`) 또는 지표 성격으로 정한다(`MapLayer.style`). LLM 프롬프트·스키마는 바꾸지 않았다 | 원 요구사항 23번의 "기본 지도 표현: Choropleth"를 넓혔다(사용자 요청). 개수·합계를 색으로 칠하면 넓은 지역이 진해 보이고, 일반 통계에서도 가장 작은 값이 가장 진하고 범례가 "공급 부족"으로 나와 '많은 곳' 질문의 답과 반대로 읽혔다 |
| 지도 보기 2단계: 점 지도·밀도 지도 (2026-11-15) | 지도 지표가 Point 개수(`aggregate_by_area`)이면 센 점의 위치를 `MapLayer.points`로 함께 보내고(`point_locations`, 필터·지역·단위 배정은 집계와 같음, 1만 개 초과면 보내지 않음) 점 지도(묶음)·밀도 지도를 보기 목록에 더한다. 해석 LLM이 지도로 위치·밀집을 보려는 표현을 옮겨 적고(`InterpretedQuery.map_focus_phrase`, 해석 규칙 8), 코드가 질문에 있는지와 위치·밀집을 뜻하는 말이 있는지 확인해(`map_focus`) 위치면 점 지도, 밀집이면 밀도 지도를 기본으로 한다. 해석 규칙 1에 '위치·밀집을 지도로 보여 달라는 질문도 지역별 시설 수를 세는 regional_summary'를 더했다. 밀도 색은 파랑 100~700단계 연속 램프다 | 사용자가 승인한 지도 보기 3단계 계획의 2단계. 위치·밀집 질문에 지역별 원·색만 보이면 시설이 실제로 어디 있는지 알 수 없다. 단위 표현처럼 키워드 목록만으로 고르면 '가장 많은 곳은 어디야'도 위치로 오인하므로 LLM이 표현을 고르고 코드가 확인한다. 점 수 한도는 응답 크기 때문이다(어린이집 3,895곳 ≈ 0.6MB, 저장은 압축돼 실행당 0.34MB). gpt-4o-mini 확인 5회: 위치·어디에 있는지 → 점 지도, 밀집한 곳 → 밀도 지도, 자치구별 수 → 비례 원, 공급 부족 → 단계 구분도로 모두 의도대로 골랐다. 같은 확인에서 개수 질문 4건 중 3건이 순위를 오름차순으로 만들어 가장 적은 곳이 1위가 됐다(기존 남은 문제 'desc 순위 규칙 미준수', 이번 변경과 무관) |
| 지도 보기 3단계: 육각 격자·이변량 지도 (2026-11-16) | 지도 지표가 Point 개수면 같은 크기 육각형 칸마다 센 격자(`MapLayer.hexbins`, `hex_bins`)를 함께 보낸다. EPSG:5179에서 `ST_HexagonGrid`로 만들고, 칸 크기는 분석 지역 범위에 칸이 1,000개쯤 들어가도록 50m 단위로 정한다. 서버에서 세므로 점이 1만 개를 넘어 위치를 못 보낼 때도 보내고, 그때 위치·밀집 질문은 육각 격자가 기본이다. 지도 지표가 비율이면 분모(x)·분자(y)를 3분위씩 나눈 구간(`MapLayer.bivariate`, `bivariate_spec`)을 보내 이변량 지도로 바꿔 볼 수 있게 한다(기본은 단계 구분도). 색은 브랜드 파랑(분자)·주황(분모)을 섞은 3×3이다. 발산형 색(평균 대비 위·아래)은 넣지 않았다 | 사용자가 승인한 지도 보기 3단계 계획. 행정동은 넓이가 수십 배 달라 같은 수라도 밀도가 다르게 보이는데, 같은 넓이 칸은 이를 피하고 정확한 수를 보인다(밀도 지도는 화면 배율에 따른 상대값이다). 점 한도를 넘는 큰 Dataset의 대안도 된다. 비율 하나로는 공급이 적어서인지 수요가 많아서인지 알 수 없어, 공급 부족 분석의 분자·분모를 함께 보게 했다. 이변량 색은 팔레트 지침에 없어 dataviz 검증 함수로 이웃 칸 구분을 계산해 골랐다(라이트 일반 15.4·색각이상 12.0, 다크 일반 10.3·색각이상 8.2; 다크는 일반 시각 기준 15에 못 미쳐 툴팁의 두 값으로 보완). 칸 경계 위 점은 `ST_HexagonGrid`가 점의 사각 범위로 칸을 찾아 두 칸을 돌려주므로 `ST_Intersects`로 거른 뒤 한 칸만 센다(거르지 않으면 서울 최대 칸이 28→32로 잘못 셌다). 발산형 색은 '기준 대비 많음·적음'을 정할 기준값이 분석마다 달라 일반 규칙을 세우기 어려워 미뤘다 |
| 공개 데모 운영 안정성 (2026-11-22) | ① `POST /api/analyses`에 IP별 요청 제한(slowapi, `ANALYSIS_RATE_LIMIT` 기본 `5/minute`, 비우거나 0이면 끔)과 한국 시간 자정부터 센 `analysis.run` 수의 하루 한도(`ANALYSIS_DAILY_LIMIT` 기본 300, 0이면 무제한)를 두고, 넘으면 429 + 한국어 `detail` + `Retry-After`. IP는 uvicorn이 넘긴 클라이언트 주소이고 믿을 프록시는 uvicorn `--forwarded-allow-ips`로 정한다(앱은 `X-Forwarded-For`를 직접 읽지 않음) ② LLM·임베딩 호출에 `LLM_TIMEOUT_SECONDS`(60)·`LLM_MAX_RETRIES`(1) ③ 클라이언트를 앱 시작 때 한 번 만들고, 설정이 없으면 그 오류를 요청마다 503으로 알림 ④ `GZipMiddleware(minimum_size=1000)` ⑤ `analysis.result(run_id)`·`analysis.run(started_at)` 인덱스 ⑥ `GET /api/stats?days=7`(성공률, 소요 시간 p50·p90, 많은 오류 코드 5개, 모델별 건수) ⑦ GitHub Actions CI | 공개 데모 전 비용·남용 방지와 운영 확인(백엔드 전달 문서 8.2·8.3·8.7·8.9). 인증이 없어 사용자 대신 IP로 세고, 비용은 분석 1건의 호출 수가 거의 정해져 있어(LLM 4~5회, 임베딩 1회) 건수로 묶는다. 하루 한도 기본값을 0이 아니라 300으로 둔 것은 설정을 잊고 공개해도 비용이 묶이게 하기 위해서다. gpt-4o-mini로 분석 1건이 1센트 미만으로 추정돼(토큰 사용량은 기록하지 않아 실측 아님) 300건은 하루 몇 달러 안이고, 로컬 개발과 테스트(별도 DB)는 300건에 닿지 않는다. 실패한 실행도 LLM을 부르므로 함께 센다. 요청 제한 저장소는 프로세스 메모리라 `--workers N`이면 프로세스마다 따로 세고, 하루 한도는 끝난 실행만 세므로 동시 요청 수만큼 넘을 수 있다. 타임아웃 60초는 gpt-4o-mini 호출(수 초)에 여유가 있고, SDK 기본값(읽기 600초 × 3번)으로는 화면 프록시 300초 안에 끝날 수 없었다. 저장된 결과 20건 기준 성공 응답 중앙값 591KB → 117KB(gzip) |
| 지역 통계 순위 방향 보정 (2026-11-22) | `regional_summary` 계획에서 지도 지표의 rank order가 질문과 반대이면 `plan_repair`가 바꾸고 `PLAN_REPAIRED`로 남긴다(원래 계획은 `original_plan`). 방향은 공백을 지운 질문에서 낱말로 정한다: 많·높·큰·최대·최고·상위·1위·내림차순·밀집 → desc, 적은·낮·작은·최소·최저·하위·꼴찌·부족·모자·덜·오름차순 → asc. '적지 않은'·'부족하지 않은'은 desc, '많지 않은'·'높지 않은'·'많이 부족한'은 asc로 먼저 뜻을 정하고, '면적'·'누적'처럼 낱말 속 '적'은 방향으로 보지 않는다. 방향 낱말이 없으면 desc, 두 방향이 섞이면('가장 많은 곳과 가장 적은 곳', '인구는 많은데 어린이집이 적은') 고치지 않는다. 메타데이터 대체 계획도 같은 판단을 쓴다(섞이면 desc). 다른 분석 유형은 정책(`rank_order`)과 Plan Validator가 정하므로 건드리지 않는다 | 11-15에 플래너 규칙 8("적은 쪽을 묻지 않는 한 desc")을 넣었지만, 11-15 지도 보기 확인에서 개수 질문 4건 중 3건이, 개발 DB 실행 기록에서도 gpt-4o-mini의 `regional_summary` 순위 6건 중 3건('서울 자치구별 어린이집 수', '서울 어린이집 위치를 보여줘', '강남구 어린이집이 어디에 있는지')이 asc여서 가장 적은 곳이 1위로 나오고 설명이 그곳을 '가장 많다'고 했다. 방향 낱말이 없을 때 desc로 고치는 것은 규칙 8과 정책 기본값(`rank_order = desc`)이 이미 정한 뜻이라 새 해석이 아니다. 낱말 목록으로 판단하므로 목록 밖 표현(예: '드문')은 고치지 못하고, 프롬프트는 바꾸지 않아 실제 모델 재확인은 하지 않았다 |
