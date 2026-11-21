from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import AnalysisScope, RunStatus
from app.domain.results import FinalResponse
from app.domain.state import WorkflowError


class AnalysisRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    scope: AnalysisScope = AnalysisScope.SEOUL  # 화면 설정의 분석 범위 (서울·전국)


class AnalysisResponse(BaseModel):
    run_id: str
    status: RunStatus
    result: FinalResponse | None
    errors: list[WorkflowError]


class AnalysisJobResponse(BaseModel):
    """백그라운드 분석 작업을 받았다는 응답. 진행은 events_url(SSE), 결과는 result_url로 받는다."""

    run_id: str
    status: RunStatus
    events_url: str
    result_url: str


class ErrorCodeCount(BaseModel):
    code: str
    runs: int  # 이 오류 코드가 기록된 실행 수


class ModelCount(BaseModel):
    llm_model: str  # 모델을 정하기 전에 끝난 실행은 빈 문자열
    runs: int


class RunStats(BaseModel):
    """최근 days일 동안 시작한 분석 실행(analysis.run) 통계."""

    days: int
    since: datetime
    total: int
    succeeded: int
    failed: int
    success_rate: float | None  # 실행이 없으면 null
    duration_p50_seconds: float | None
    duration_p90_seconds: float | None
    top_error_codes: list[ErrorCodeCount]  # 많은 순 최대 5개
    by_llm_model: list[ModelCount]
