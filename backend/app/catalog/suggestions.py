"""처음 쓰는 사람에게 보여 줄 예시 질문. 필요한 Dataset이 모두 등록된 질문만 보여 준다 (I/O 없음).

질문은 실제 모델로 성공한 실행 기록(analysis.run)의 질문 형태에서 골랐다. 새 분석 주제나 Dataset을 더하면 함께 고친다.
"""

from dataclasses import dataclass

from app.domain.enums import AnalysisScope


@dataclass(frozen=True)
class Suggestion:
    question: str
    datasets: frozenset[str]  # 이 질문에 필요한 Dataset (모두 등록되어 있어야 보인다)
    scopes: frozenset[AnalysisScope] = frozenset(AnalysisScope)  # 이 질문을 물을 수 있는 분석 범위


SUGGESTIONS: tuple[Suggestion, ...] = (
    Suggestion(
        "서울 행정동 중 영유아 인구 대비 어린이집이 부족한 곳은?",
        frozenset({"emd_boundary", "population_by_age", "daycare_facility"}),
    ),
    Suggestion("서울 자치구별 어린이집 수", frozenset({"sigungu_boundary", "daycare_facility"})),
    Suggestion("강남구 어린이집이 어디에 몰려 있나요?", frozenset({"emd_boundary", "daycare_facility"})),
    Suggestion("서울 행정동별 0~5세 인구", frozenset({"emd_boundary", "population_by_age"})),
    # 서울 밖 지역은 전국 범위에서만 물을 수 있다.
    Suggestion(
        "부산 중구 행정동별 인구 순위",
        frozenset({"national_emd_boundary", "population_by_age"}),
        frozenset({AnalysisScope.NATIONAL}),
    ),
)


def suggested_questions(scope: AnalysisScope, registered: set[str]) -> list[str]:
    return [s.question for s in SUGGESTIONS if scope in s.scopes and s.datasets <= registered]
