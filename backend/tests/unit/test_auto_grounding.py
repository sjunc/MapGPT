"""자동 후보의 이름이 그 후보로 만들 데이터 이름과 겹치는지 보는 검사 (DB·LLM 없음)."""

import pytest

from app.catalog.definitions import TRUSTED_DATASETS
from app.domain.catalog import AUTO_PUBLIC_API_CATEGORY
from app.domain.state import DatasetSelection, RequiredData
from app.workflows.analysis_workflow import _ungrounded_auto


def _candidate(title: str, category: str = AUTO_PUBLIC_API_CATEGORY):
    base = TRUSTED_DATASETS["daycare_facility"]
    return base.model_copy(
        update={"dataset_id": "candidate", "title": title, "category": category, "semantic_tags": []}
    )


def _ungrounded(concept: str, title: str, category: str = AUTO_PUBLIC_API_CATEGORY):
    selection = DatasetSelection(
        required_data=[RequiredData(concept=concept, dataset_id="candidate")], datasets=["candidate"], rationale="-"
    )
    return _ungrounded_auto(selection, {"candidate": _candidate(title, category)}, [])


@pytest.mark.parametrize(
    ("concept", "title", "grounded"),
    [
        ("평균 소득", "서울시 여름철 평균기온 위치정보", False),  # 통계 낱말 '평균'만 겹친다
        ("평균소득", "서울시 여름철 평균기온 위치정보", False),
        ("평균 기온", "서울시 여름철 평균기온 위치정보", True),
        ("평균기온", "서울시 여름철 평균기온 위치정보", True),
        ("평균 소득", "서울시 자치구별 TOE 환산량 2006년 위치정보", False),
        ("동물병원 수", "서울시 동물병원 인허가 정보", True),
        ("인구 대비 주차장 비율", "서울시 공영주차장 안내 정보", True),  # '주차장'이 '공영주차장' 안에 있다
        ("개수", "서울시 공영주차장 안내 정보", False),  # 대상 낱말이 없으면 대신한 것으로 본다
    ],
)
def test_auto_candidates_must_share_a_subject_word_with_the_data(concept, title, grounded):
    assert (_ungrounded(concept, title) == []) is grounded


def test_reviewed_datasets_are_not_checked():
    assert _ungrounded("평균 소득", "서울특별시 어린이집 위치", category="facility") == []
