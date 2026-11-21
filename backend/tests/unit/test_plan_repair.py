import pytest

from app.catalog.definitions import SEOUL, TRUSTED_DATASETS
from app.domain.catalog import RegionRef
from app.domain.enums import SpatialLevel
from app.domain.plan import AnalysisPlan
from app.validation.plan_repair import question_rank_order, repair_plan
from app.validation.plan_validator import validate_plan
from tests.support import load_plan_dict

SELECTED = list(TRUSTED_DATASETS)
GANGNAM = RegionRef(name="서울특별시 강남구", level=SpatialLevel.SIGUNGU, code="11680")


def _repair(data: dict, region: RegionRef = SEOUL):
    plan, issues = repair_plan(AnalysisPlan.model_validate(data), TRUSTED_DATASETS, region)
    assert {issue.code for issue in issues} <= {"PLAN_REPAIRED"}
    return plan, [issue.message for issue in issues], validate_plan(plan, TRUSTED_DATASETS, SELECTED, region)


def test_clean_plan_is_returned_unchanged():
    plan = AnalysisPlan.model_validate(load_plan_dict())

    assert repair_plan(plan, TRUSTED_DATASETS, SEOUL) == (plan, [])


def test_spatial_aggregation_of_a_population_table_becomes_a_population_sum():
    # gpt-4o-mini가 가장 자주 낸 계획 실수: 위치가 없는 인구 표를 행정동 경계로 공간 집계하려 했다.
    data = load_plan_dict()
    data["operations"][1] = {
        "type": "aggregate_by_area",
        "input": "population_by_age",
        "boundary": "emd_boundary",
        "aggregation": "count",
        "output": "child_population",
        "label": "0~5세 인구",
    }

    plan, notes, report = _repair(data)

    op = plan.operations[1]
    assert (op.type, op.group_by, op.aggregation.field, op.aggregation.function) == (
        "aggregate",
        ["adm_cd"],
        "population",
        "sum",
    )
    assert report.ok, report.errors
    assert notes == ["위치가 없는 'population_by_age'의 공간 집계(child_population)를 adm_cd별 population 합계로 바꿈"]


def test_filters_split_on_one_dataset_are_merged_into_one_and_filter():
    data = load_plan_dict()
    age = data["filters"][0]
    positive = {"dataset": "population_by_age", "where": {"field": "population", "operator": "gt", "value": 0}}
    data["filters"] = [age, age, positive]  # 같은 조건 두 번과 다른 조건 하나

    plan, notes, report = _repair(data)

    [merged] = plan.filters
    assert [(c.field, c.operator) for c in merged.where.conditions] == [("age", "between"), ("population", "gt")]
    assert report.ok, report.errors
    assert notes == ["'population_by_age'에 나뉘어 있던 필터 3개를 하나(AND)로 합침"]


def test_filters_with_or_are_left_for_regeneration():
    data = load_plan_dict()
    either = {
        "dataset": "population_by_age",
        "where": {
            "logic": "or",
            "conditions": [
                {"field": "age", "operator": "eq", "value": 0},
                {"field": "age", "operator": "eq", "value": 1},
            ],
        },
    }
    data["filters"].append(either)

    plan, notes, report = _repair(data)

    assert notes == []
    assert [issue.code for issue in report.errors] == ["DUPLICATE_FILTER"]


def test_boundary_filter_restating_the_region_is_dropped_but_others_are_kept():
    data = load_plan_dict()
    data["filters"].append(
        {"dataset": "emd_boundary", "where": {"field": "sigungu_nm", "operator": "eq", "value": "강남구"}}
    )

    # 강남구를 분석하면 '시군구명 = 강남구'는 지역을 다시 말할 뿐이다.
    plan, notes, report = _repair(data, GANGNAM)
    assert [f.dataset for f in plan.filters] == ["population_by_age"]
    assert notes == ["분석 지역 '서울특별시 강남구'을(를) 다시 말한 'emd_boundary' 필터를 뺌"]
    assert report.ok, report.errors

    # 서울 전체를 분석하는데 강남구로 좁히는 필터는 지역을 바꾸므로 그대로 오류로 남긴다.
    _, notes, report = _repair(data, SEOUL)
    assert notes == []
    assert "BOUNDARY_FILTER_UNSUPPORTED" in [issue.code for issue in report.errors]


def test_rank_output_used_as_the_map_metric_is_replaced_by_the_ranked_metric():
    data = load_plan_dict()
    data["output"]["metric"] = "shortage_rank"

    plan, notes, report = _repair(data)

    assert plan.output.metric == "daycare_per_1000_children"
    assert report.ok, report.errors
    assert notes == ["지도 지표로 지정한 순위 'shortage_rank' 대신 순위를 매긴 지표 'daycare_per_1000_children'를 씀"]


def test_spatial_aggregation_of_a_table_without_population_is_left_for_regeneration():
    # 인구 컬럼이 없는 표는 행이 무엇인지(시설 하나, 연도별 통계 등) 알 수 없어 행 개수로 바꾸지 않는다.
    population = TRUSTED_DATASETS["population_by_age"]
    columns = [
        c.model_copy(update={"semantic_type": "measure"}) if c.name == "population" else c for c in population.columns
    ]
    datasets = {**TRUSTED_DATASETS, "population_by_age": population.model_copy(update={"columns": columns})}
    data = load_plan_dict()
    data["operations"][1] = {
        "type": "aggregate_by_area",
        "input": "population_by_age",
        "boundary": "emd_boundary",
        "aggregation": "count",
        "output": "child_population",
        "label": "0~5세 인구",
    }

    plan, issues = repair_plan(AnalysisPlan.model_validate(data), datasets, SEOUL)

    assert issues == [] and plan.operations[1].type == "aggregate_by_area"


def test_different_conditions_on_one_column_are_not_merged():
    # '0세'와 '5세'를 따로 건 필터는 OR을 뜻했을 수도 있어 AND로 합치지 않는다.
    data = load_plan_dict()
    data["filters"] = [
        {"dataset": "population_by_age", "where": {"field": "age", "operator": "eq", "value": 0}},
        {"dataset": "population_by_age", "where": {"field": "age", "operator": "eq", "value": 5}},
    ]

    _, notes, report = _repair(data)

    assert notes == []
    assert [issue.code for issue in report.errors] == ["DUPLICATE_FILTER"]


def test_boundary_filter_with_a_partial_code_is_kept():
    data = load_plan_dict()
    data["filters"].append({"dataset": "emd_boundary", "where": {"field": "sido_cd", "operator": "eq", "value": "1"}})

    _, notes, report = _repair(data)

    assert notes == []
    assert "BOUNDARY_FILTER_UNSUPPORTED" in [issue.code for issue in report.errors]


def test_lower_and_upper_bound_on_one_column_are_merged_into_a_range():
    # 실제 gpt-4o-mini 계획: '어린이 인구'를 나이 ≥ 0, 나이 ≤ 5 필터 두 개로 나눠 썼다.
    data = load_plan_dict()
    data["filters"] = [
        {"dataset": "population_by_age", "where": {"field": "age", "operator": "gte", "value": 0}},
        {"dataset": "population_by_age", "where": {"field": "age", "operator": "lte", "value": 5}},
    ]

    plan, notes, report = _repair(data)

    [merged] = plan.filters
    assert [(c.operator, c.value) for c in merged.where.conditions] == [("gte", 0), ("lte", 5)]
    assert report.ok, report.errors
    assert notes == ["'population_by_age'에 나뉘어 있던 필터 2개를 하나(AND)로 합침"]


def _daycare_count_plan(order: str) -> dict:
    return {
        "intent": "regional_summary",
        "region": {"name": "서울특별시", "level": "sido"},
        "analysis_unit": "emd",
        "datasets": ["daycare_facility", "emd_boundary"],
        "operations": [
            {
                "type": "aggregate_by_area",
                "input": "daycare_facility",
                "boundary": "emd_boundary",
                "aggregation": "count",
                "output": "daycare_count",
                "label": "어린이집 수",
            },
            {"type": "rank", "input": "daycare_count", "order": order, "output": "daycare_rank", "label": "순위"},
        ],
        "output": {"map_type": "choropleth", "metric": "daycare_count", "ranking": {"enabled": True, "limit": 10}},
    }


HIGH_QUESTIONS = [
    "서울에서 어린이집이 가장 많은 행정동은?",
    "65세 이상 인구 비율이 높은 동 10곳",
    "어린이집 수 상위 5개 자치구",
    "어린이집이 1위인 곳",
    "어린이집이 적지 않은 동을 보여줘",  # 부정: 많은 쪽
    "어린이집이 밀집한 곳은?",
    # gpt-4o-mini가 asc로 계획한 실제 질문들. 방향을 말하지 않으면 desc다 (프롬프트 규칙 8).
    "서울 자치구별 어린이집 수",
    "서울 어린이집 위치를 보여줘",
    "면적이 넓은 행정동 순위",  # '면적'의 '적'은 방향이 아니다
]
LOW_QUESTIONS = [
    "어린이집이 가장 적은 행정동은?",
    "인구가 낮은 순으로 보여줘",
    "어린이집 수 하위 10곳",
    "어린이집이 많이 부족한 동",  # 강조: '많이'가 있어도 적은 쪽
    "어린이집이 많지 않은 자치구",  # 부정: 적은 쪽
    "공공와이파이가 최소인 구",
]
MIXED_QUESTIONS = [
    "어린이집이 가장 많은 곳과 가장 적은 곳",
    "인구는 많은데 어린이집이 적은 동",
]


@pytest.mark.parametrize("question", HIGH_QUESTIONS)
def test_questions_asking_for_the_high_side_rank_descending(question):
    assert question_rank_order(question) == "desc"


@pytest.mark.parametrize("question", LOW_QUESTIONS)
def test_questions_asking_for_the_low_side_rank_ascending(question):
    assert question_rank_order(question) == "asc"


@pytest.mark.parametrize("question", MIXED_QUESTIONS)
def test_questions_asking_for_both_sides_have_no_single_direction(question):
    assert question_rank_order(question) is None


@pytest.mark.parametrize(
    ("question", "planned", "expected", "note"),
    [
        (
            "서울에서 어린이집이 가장 많은 행정동은?",
            "asc",
            "desc",
            "질문이 많은·높은 쪽을 물어 'daycare_rank' 순위를 asc에서 desc로 바꿈",
        ),
        (
            "어린이집이 가장 적은 행정동은?",
            "desc",
            "asc",
            "질문이 적은·낮은 쪽을 물어 'daycare_rank' 순위를 desc에서 asc로 바꿈",
        ),
    ],
)
def test_regional_summary_rank_against_the_question_is_flipped(question, planned, expected, note):
    original = AnalysisPlan.model_validate(_daycare_count_plan(planned))

    plan, issues = repair_plan(original, TRUSTED_DATASETS, SEOUL, question)

    assert plan.operations[1].order == expected
    assert [issue.message for issue in issues] == [note]
    assert original.operations[1].order == planned  # 원래 계획은 그대로 남는다 (original_plan으로 기록)
    assert validate_plan(plan, TRUSTED_DATASETS, SELECTED, SEOUL).ok


@pytest.mark.parametrize(
    ("question", "order"),
    [
        ("어린이집이 가장 많은 곳과 가장 적은 곳", "asc"),  # 방향이 섞이면 고치지 않는다
        ("서울에서 어린이집이 가장 많은 행정동은?", "desc"),  # 이미 맞다
    ],
)
def test_regional_summary_rank_is_left_alone_when_unclear_or_already_right(question, order):
    plan = AnalysisPlan.model_validate(_daycare_count_plan(order))

    assert repair_plan(plan, TRUSTED_DATASETS, SEOUL, question) == (plan, [])


def test_rank_direction_of_other_intents_is_left_to_their_policy():
    # 수요 대비 공급 부족은 '가장 많은 곳'을 물어도 부족한 쪽(asc) 정책이 정하고, Plan Validator가 검사한다.
    plan = AnalysisPlan.model_validate(load_plan_dict())

    assert repair_plan(plan, TRUSTED_DATASETS, SEOUL, "어린이집이 가장 많이 필요한 곳") == (plan, [])
