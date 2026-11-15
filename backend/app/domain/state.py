from datetime import date, datetime

from pydantic import Field

from app.domain.base import StrictModel
from app.domain.catalog import DatasetMetadata, RegionRef
from app.domain.enums import AnalysisIntent, AnalysisScope, RunStatus, SpatialLevel, WorkflowStep
from app.domain.plan import AnalysisPlan, Region
from app.domain.results import (
    ExecutionResult,
    FinalResponse,
    MapLayer,
    ValidationIssue,
    ValidationReport,
)


class InterpretedQuery(StrictModel):
    """자연어 공간 분석 질문의 해석 결과."""

    intent: AnalysisIntent | None = Field(description="지원하는 분석 유형. 지원 범위 밖이면 null")
    unsupported_reason: str | None = Field(description="intent가 null일 때 그 이유, 아니면 null")
    region: Region | None
    # 지역을 말하지 않은 질문에도 LLM이 지역을 채우므로, 질문 속 표현을 옮겨 적게 해 코드가 확인한다.
    region_phrase: str | None = Field(
        default=None,
        description="질문에서 분석 지역을 말한 표현을 질문 글자 그대로 옮긴 것 (예: '서울', '강남구'). 없으면 null",
    )
    analysis_unit: SpatialLevel | None
    # 참/거짓으로 물으면 같은 질문에도 판단이 흔들려서, 질문 속 표현을 옮겨 적게 하고 코드가 질문에 있는지 확인한다.
    analysis_unit_phrase: str | None = Field(
        default=None,
        description="질문에서 결과를 나눌 단위를 직접 말한 표현을 질문 글자 그대로 옮긴 것 (예: '자치구', '행정동별'). 없으면 null",
    )
    restated_question: str = Field(description="분석 질문을 명확하게 다시 쓴 문장")
    key_concepts: list[str] = Field(description="필요한 데이터 개념 (예: 어린이집, 0~5세 인구, 행정동 경계)")
    assumptions: list[str] = Field(description="해석 과정에서 둔 가정")


class DatasetHit(StrictModel):
    dataset_id: str
    similarity: float


class DatasetSearch(StrictModel):
    """Dataset 후보 검색 기록 (pgvector 코사인 유사도)."""

    query: str
    embedding_model: str
    hits: list[DatasetHit]


class RequiredData(StrictModel):
    """분석에 필요한 데이터 하나와 그 데이터를 주는 후보 Dataset."""

    concept: str = Field(description="필요한 데이터의 짧은 한국어 이름 (예: 0~5세 인구)")
    dataset_id: str | None = Field(description="이 데이터를 주는 후보 dataset_id. 어느 후보로도 만들 수 없으면 null")


class DatasetSelection(StrictModel):
    """후보 Dataset 중 분석에 필요한 Dataset 선택 결과."""

    # LLM은 스키마 순서대로 답하므로, 필요한 데이터마다 출처부터 정한 뒤 ID 목록을 쓰도록 앞에 둔다.
    required_data: list[RequiredData] = Field(
        default=[], description="질문의 지표·조건에 필요한 데이터마다 그 데이터를 주는 후보 Dataset"
    )
    datasets: list[str] = Field(description="선택한 Dataset ID 목록")
    rationale: str


class DataAcquisition(StrictModel):
    """분석 중에 등록 출처에서 받아 적재한 기록."""

    dataset_id: str
    connector: str
    service: str
    rows: int
    located: int  # 위치(geom)를 만든 행 수
    fetched_at: date
    from_cache: bool  # 주기 안의 원본 사본을 다시 써서 API를 부르지 않았는지
    duration_ms: float


class Explanation(StrictModel):
    """분석 결과에 대한 한국어 설명."""

    summary: str


class PlanAttempt(StrictModel):
    """LLM이 만든 계획 한 번과 그 검증 결과 (스키마를 통과하지 못했으면 plan은 null)."""

    plan: AnalysisPlan | None
    validation: ValidationReport
    # 계획 보정이 고쳤으면 LLM이 낸 원래 계획 (plan은 고친 계획, 고친 내용은 validation의 PLAN_REPAIRED 경고)
    original_plan: AnalysisPlan | None = None


class WorkflowError(StrictModel):
    step: WorkflowStep
    code: str
    message: str
    issues: list[ValidationIssue] = []


class AgentState(StrictModel):
    run_id: str
    question: str
    scope: AnalysisScope = AnalysisScope.SEOUL  # 화면 설정의 분석 범위
    status: RunStatus = RunStatus.RUNNING
    current_step: WorkflowStep | None = None
    interpreted_query: InterpretedQuery | None = None
    resolved_region: RegionRef | None = None  # 해석한 지역을 등록 지역과 대조해 확정한 지역 (행정코드 포함)
    dataset_search: DatasetSearch | None = None
    dataset_candidates: list[DatasetMetadata] = []
    dataset_selection: DatasetSelection | None = None  # LLM 선택 원본 (근거 포함)
    selected_datasets: list[str] = []  # 검사를 통과한 선택 (중복 제거)
    data_acquisitions: list[DataAcquisition] = []  # 선택 뒤 등록 출처에서 받아 적재한 기록
    analysis_plan: AnalysisPlan | None = None
    plan_validation: ValidationReport | None = None
    plan_history: list[PlanAttempt] = []  # 재생성을 포함한 모든 계획 시도
    execution_results: ExecutionResult | None = None
    result_validation: ValidationReport | None = None
    map_layer: MapLayer | None = None
    final_response: FinalResponse | None = None
    errors: list[WorkflowError] = []
    llm_provider: str
    llm_model: str
    started_at: datetime
    finished_at: datetime | None = None
