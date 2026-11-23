"""자동 후보의 이름이 질문의 핵심 개념과 그 후보로 만들 데이터 이름에 겹치는지 보는 검사 (DB·LLM 없음)."""

import pytest

from app.catalog.definitions import TRUSTED_DATASETS
from app.domain.catalog import AUTO_PUBLIC_API_CATEGORY
from app.domain.state import DatasetSelection, RequiredData
from app.workflows.analysis_workflow import _ungrounded_auto


def _candidate(title: str, category: str, tags: tuple[str, ...]):
    base = TRUSTED_DATASETS["daycare_facility"]
    return base.model_copy(
        update={"dataset_id": "candidate", "title": title, "category": category, "semantic_tags": list(tags)}
    )


def _ungrounded(concept: str, title: str, category=AUTO_PUBLIC_API_CATEGORY, key_concepts=None, tags=()):
    selection = DatasetSelection(
        required_data=[RequiredData(concept=concept, dataset_id="candidate")], datasets=["candidate"], rationale="-"
    )
    # 핵심 개념을 따로 주지 않으면 질문도 같은 데이터를 묻는다고 본다.
    return _ungrounded_auto(selection, {"candidate": _candidate(title, category, tags)}, key_concepts or [concept])


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


def test_data_named_after_the_candidate_must_still_match_the_question():
    # 질문은 범죄를 묻는데, LLM이 데이터 이름을 고른 후보에 맞춰 '불법주정차 신고'로 적었다 (gpt-4o-mini 실제 사례).
    ungrounded = _ungrounded(
        "불법주정차 신고",
        "서울시 불법주정차 신고 현황",
        key_concepts=["범죄 발생", "지역", "통계"],
        tags=("불법주정차", "신고"),
    )

    assert ungrounded == [("범죄 발생", "서울시 불법주정차 신고 현황")]


@pytest.mark.parametrize(
    ("key_concepts", "concept", "title", "grounded"),
    [
        (["불법주정차 신고 건수"], "불법주정차 신고", "서울시 불법주정차 신고 현황", True),
        (["범죄 발생"], "화재 발생", "서울시 화재발생 현황", False),  # 흔한 낱말 '발생'만 겹친다
        (["체육 시설"], "복지 시설", "서울시 노인복지시설 현황", False),  # '시설'만 겹친다
        (["어린이집", "소득"], "소득", "서울시 국공립어린이집 위치", False),  # 질문과는 겹쳐도 그 데이터 이름과 다르다
        (["지역", "통계"], "약국", "서울시 약국 인허가 정보", False),  # 질문에 대상 낱말이 없다
    ],
)
def test_auto_candidates_must_match_both_the_question_and_the_data_name(key_concepts, concept, title, grounded):
    assert (_ungrounded(concept, title, key_concepts=key_concepts) == []) is grounded


def test_reviewed_datasets_are_not_checked():
    assert _ungrounded("평균 소득", "서울특별시 어린이집 위치", category="facility") == []
