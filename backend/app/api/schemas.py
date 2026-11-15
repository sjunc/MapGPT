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
