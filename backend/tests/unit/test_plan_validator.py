import pytest

from app.catalog.definitions import TRUSTED_DATASETS
from app.domain.catalog import RegionRef
from app.domain.enums import SpatialLevel
from app.domain.plan import AnalysisPlan
from app.validation.plan_validator import question_issues, validate_plan
from tests.support import load_plan_dict

SELECTED = list(TRUSTED_DATASETS)


def _codes(data: dict, datasets=TRUSTED_DATASETS, selected=SELECTED) -> list[str]:
    report = validate_plan(AnalysisPlan.model_validate(data), datasets, selected)
    return [issue.code for issue in report.errors]


def _with_dataset(dataset_id: str, **updates) -> dict:
    datasets = dict(TRUSTED_DATASETS)
    datasets[dataset_id] = datasets[dataset_id].model_copy(update=updates)
    return datasets


def test_first_use_case_plan_is_valid():
    report = validate_plan(AnalysisPlan.model_validate(load_plan_dict()), TRUSTED_DATASETS, SELECTED)

    assert report.ok
    assert report.issues == []


def test_unknown_and_unselected_datasets_are_rejected():
    data = load_plan_dict()
    data["datasets"].append("subway_station")

    assert _codes(data) == ["UNKNOWN_DATASET"]
    assert "DATASET_NOT_SELECTED" in _codes(load_plan_dict(), selected=["emd_boundary", "population_by_age"])


def test_operation_dataset_must_be_declared():
    data = load_plan_dict()
    data["datasets"].remove("daycare_facility")

    assert _codes(data) == ["DATASET_NOT_DECLARED"]


@pytest.mark.parametrize(
    ("condition", "code"),
    [
        ({"field": "ages", "operator": "between", "value": [0, 5]}, "FILTER_FIELD_UNKNOWN"),
        ({"field": "reference_date", "operator": "between", "value": [0, 5]}, "FILTER_TYPE_MISMATCH"),
    ],
)
def test_filter_fields_and_types_are_checked(condition, code):
    data = load_plan_dict()
    data["filters"][0]["where"] = condition

    assert _codes(data) == [code]


def test_filters_on_unit_boundary_and_duplicates_are_rejected():
    data = load_plan_dict()
    data["filters"].append(data["filters"][0])
    data["filters"].append(
        {"dataset": "emd_boundary", "where": {"field": "sigungu_nm", "operator": "eq", "value": "종로구"}}
    )

    assert _codes(data) == ["DUPLICATE_FILTER", "BOUNDARY_FILTER_UNSUPPORTED"]


def test_ratio_must_reference_previously_computed_metrics():
    data = load_plan_dict()
    data["operations"][2]["denominator"] = "population_by_age"

    assert _codes(data) == ["OPERATION_INPUT_UNKNOWN"]

    reordered = load_plan_dict()
    reordered["operations"] = [reordered["operations"][i] for i in (2, 0, 1, 3)]
    assert _codes(reordered)[:2] == ["OPERATION_INPUT_UNKNOWN", "OPERATION_INPUT_UNKNOWN"]


@pytest.mark.parametrize("output", ["child_population", "adm_cd", "unit_name", "emd_boundary"])
def test_output_names_must_not_conflict(output):
    data = load_plan_dict()
    data["operations"][0]["output"] = output
    data["operations"][2]["numerator"] = output

    assert "OUTPUT_NAME_CONFLICT" in _codes(data)


def test_spatial_aggregation_requires_point_input_and_unit_polygon_boundary():
    data = load_plan_dict()
    data["operations"][0]["input"] = "population_by_age"
    data["operations"][0]["boundary"] = "daycare_facility"

    assert _codes(data) == ["GEOMETRY_REQUIRED", "GEOMETRY_TYPE_INVALID"]


def test_unsupported_crs_is_rejected():
    spatial = TRUSTED_DATASETS["daycare_facility"].spatial.model_copy(update={"crs": "EPSG:3857"})

    assert _codes(load_plan_dict(), datasets=_with_dataset("daycare_facility", spatial=spatial)) == ["CRS_UNSUPPORTED"]


def test_group_by_must_be_a_compatible_join_key():
    data = load_plan_dict()
    data["operations"][1]["group_by"] = ["age"]
    assert _codes(data) == ["JOIN_KEY_INCOMPATIBLE"]

    other_code_system = [
        TRUSTED_DATASETS["population_by_age"].join_keys[0].model_copy(update={"code_system": "kostat_8"})
    ]
    datasets = _with_dataset("population_by_age", join_keys=other_code_system)
    assert _codes(load_plan_dict(), datasets=datasets) == ["JOIN_KEY_INCOMPATIBLE"]


def test_sum_requires_numeric_field():
    data = load_plan_dict()
    data["operations"][1]["aggregation"]["field"] = "adm_cd"

    assert _codes(data) == ["FIELD_NOT_NUMERIC"]


@pytest.mark.parametrize("function", ["count", "avg"])
def test_population_is_only_summed(function):
    # 나이별 인구 행의 개수(모두 111)나 평균은 지역 인구가 아니다 (예: '평균 소득'으로 이름 붙인 나이별 평균).
    data = load_plan_dict()
    data["operations"][1]["aggregation"]["function"] = function

    assert _codes(data) == ["POPULATION_NOT_SUMMED"]


def test_ratio_operands_must_differ_and_not_be_ranks():
    data = load_plan_dict()
    data["operations"][2]["denominator"] = "daycare_count"
    assert _codes(data) == ["RATIO_OPERAND_INVALID"]

    ranked = load_plan_dict()
    ranked["operations"].insert(2, {**ranked["operations"][3], "input": "daycare_count", "output": "count_rank"})
    ranked["operations"][3]["numerator"] = "count_rank"
    assert _codes(ranked) == ["RATIO_OPERAND_INVALID"]


def test_ratio_of_two_identical_computations_is_rejected():
    # 실제 gpt-4o-mini 계획: '어린이 인구 ÷ 인구'를 같은 Dataset·필터로 두 번 합계해 모든 행정동이 1000이 됐다.
    data = load_plan_dict()
    data["operations"][0] = {**data["operations"][1], "output": "daycare_count", "label": "어린이 인구(중복)"}

    report = validate_plan(AnalysisPlan.model_validate(data), TRUSTED_DATASETS, SELECTED)

    assert [issue.code for issue in report.errors] == ["RATIO_OPERAND_INVALID"]
    assert "같은 필터·집계로 계산해 값이 같으므로" in report.errors[0].message


def test_output_metric_must_be_computed():
    data = load_plan_dict()
    data["output"]["metric"] = "unknown_metric"

    assert _codes(data)[0] == "OUTPUT_METRIC_NOT_COMPUTED"


def test_output_metric_must_not_be_the_rank_itself():
    data = load_plan_dict()
    data["output"]["metric"] = "shortage_rank"

    report = validate_plan(AnalysisPlan.model_validate(data), TRUSTED_DATASETS, SELECTED)

    assert report.errors[0].code == "OUTPUT_METRIC_IS_RANK"
    assert "'daycare_per_1000_children'" in report.errors[0].message


def test_ranking_requires_a_rank_on_the_output_metric():
    data = load_plan_dict()
    data["operations"][3]["input"] = "daycare_count"

    assert _codes(data)[0] == "RANKING_NOT_DEFINED"


def test_region_outside_dataset_coverage_is_rejected():
    data = load_plan_dict()
    data["region"] = {"name": "부산광역시", "level": "sido"}

    # 전국 인구는 부산을 포함하지만 서울 경계와 어린이집은 포함하지 않는다.
    assert _codes(data) == ["REGION_NOT_COVERED"] * 2


def test_region_not_covered_names_the_checked_region():
    # 범위는 해석 단계에서 확정한 지역(대한민국)으로 검사하므로, 계획에 적힌 지역(서울특별시)이 아니라 그 지역을 알린다.
    country = RegionRef(name="대한민국", level=SpatialLevel.COUNTRY, code="KR")

    report = validate_plan(AnalysisPlan.model_validate(load_plan_dict()), TRUSTED_DATASETS, SELECTED, country)

    messages = [issue.message for issue in report.errors if issue.code == "REGION_NOT_COVERED"]
    assert len(messages) == 2  # 서울 경계와 서울 어린이집
    assert all(message.startswith("분석 지역 '대한민국'(country)은") for message in messages)


def test_analysis_unit_needs_a_polygon_dataset_at_that_level():
    data = load_plan_dict()
    data["analysis_unit"] = "sigungu"

    assert "UNIT_BOUNDARY_MISSING" in _codes(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("operations", 2, "scale"), 10000),
        (("operations", 3, "order"), "desc"),
        (("output", "ranking", "enabled"), False),
    ],
)
def test_supply_demand_policy_is_enforced(path, value):
    data = load_plan_dict()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert "POLICY_VIOLATION" in _codes(data)


def test_unused_dataset_is_a_warning():
    data = load_plan_dict()
    data["datasets"] = ["emd_boundary", "population_by_age", "daycare_facility"]
    data["filters"] = []
    data["operations"] = [data["operations"][0], {**data["operations"][3], "input": "daycare_count"}]
    data["output"]["metric"] = "daycare_count"

    report = validate_plan(AnalysisPlan.model_validate(data), TRUSTED_DATASETS, SELECTED)

    assert [i.code for i in report.warnings] == ["UNUSED_DATASET"]


def test_region_conditions_on_data_tables_are_rejected():
    # 실제 gpt-4o-mini 계획: 인구 표에 지어낸 행정동 코드('부산중구행정동코드1')를 걸어 결과가 비었다.
    data = load_plan_dict()
    data["filters"][0]["where"] = {"field": "adm_cd", "operator": "in", "value": ["부산중구행정동코드1"]}

    assert _codes(data) == ["REGION_FILTER_UNSUPPORTED"]


def _question_codes(question: str, filters: list[dict], intent: str = "supply_demand_gap") -> list[str]:
    data = load_plan_dict()
    data["intent"] = intent
    data["filters"] = filters
    return [issue.code for issue in question_issues(AnalysisPlan.model_validate(data), TRUSTED_DATASETS, question)]


_PUBLIC = {"dataset": "daycare_facility", "where": {"field": "facility_type", "operator": "eq", "value": "국공립"}}


@pytest.mark.parametrize(
    ("question", "codes"),
    [
        ("서울 자치구별 어린이집 수 순위", ["FILTER_NOT_IN_QUESTION"]),  # 실제 gpt-4o-mini가 붙인 조건
        ("서울 국공립 어린이집이 가장 많은 자치구는?", []),
        ("서울에서 공립 어린이집이 부족한 동은?", []),  # 질문의 낱말이 값의 일부
    ],
)
def test_category_filter_values_must_come_from_the_question(question, codes):
    assert _question_codes(question, [_PUBLIC]) == codes


_CHILD = {"dataset": "population_by_age", "where": {"field": "age", "operator": "between", "value": [0, 5]}}
_CHILD_BOUNDS = {
    "dataset": "population_by_age",
    "where": {
        "logic": "and",
        "conditions": [
            {"field": "age", "operator": "gte", "value": 0},
            {"field": "age", "operator": "lte", "value": 5},
        ],
    },
}


@pytest.mark.parametrize(
    ("question", "filters", "intent", "codes"),
    [
        # 실제 gpt-4o-mini: 일반 통계 질문에 공급 부족 정책의 어린이 범위를 붙였다.
        ("서울 행정동별 인구 1,000명당 공공와이파이 수", [_CHILD], "regional_summary", ["FILTER_NOT_IN_QUESTION"]),
        (
            "서울 행정동별 인구 1,000명당 공공와이파이 수",
            [_CHILD_BOUNDS],
            "regional_summary",
            ["FILTER_NOT_IN_QUESTION"],
        ),
        ("서울 행정동별 어린이 인구 순위", [_CHILD], "regional_summary", []),
        ("서울에서 0~5세 인구가 가장 많은 행정동은?", [_CHILD], "regional_summary", []),
        ("서울 행정동별 인구 1,500명당 공공와이파이 수", [_CHILD], "regional_summary", ["FILTER_NOT_IN_QUESTION"]),
        # 공급 부족 분석은 정책이 수요 인구를 0~5세로 정하므로 질문에 없어도 된다.
        ("강남구에서 보육시설이 가장 부족한 행정동은?", [_CHILD], "supply_demand_gap", []),
    ],
)
def test_child_age_condition_needs_the_question_to_mention_children(question, filters, intent, codes):
    assert _question_codes(question, filters, intent) == codes


def test_null_checks_on_category_columns_are_not_in_the_question():
    # 실제 gpt-4o-mini 계획: 서울 기본 질문에 '시설 유형 값 없음'을 붙여 결과가 비었다.
    unknown_type = {"dataset": "daycare_facility", "where": {"field": "facility_type", "operator": "is_null"}}

    assert _question_codes("서울에서 어린이 인구 대비 어린이집이 부족한 행정동을 알려줘.", [unknown_type]) == [
        "FILTER_NOT_IN_QUESTION"
    ]
