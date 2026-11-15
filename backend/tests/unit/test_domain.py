import json

import pytest
from pydantic import ValidationError

from app.domain.filters import FilterCondition, FilterGroup
from app.domain.operations import (
    AggregateByAreaOperation,
    AggregateOperation,
    CalculateRatioOperation,
    RankOperation,
)
from app.catalog.definitions import KOREA, SEOUL, TRUSTED_DATASETS
from app.agents.prompts import interpret_prompt
from app.domain.enums import SpatialLevel
from app.domain.catalog import RegionRef
from app.domain.plan import (
    AnalysisPlan,
    DatasetFilter,
    Region,
    analysis_units,
    match_regions,
    resolve_region,
    unit_named,
)
from app.domain.state import DatasetSelection, Explanation, InterpretedQuery
from app.llm.schema import to_llm_json_schema
from tests.support import load_plan, load_plan_dict


def test_first_use_case_plan_parses_into_typed_operations():
    plan = load_plan()

    assert [type(op) for op in plan.operations] == [
        AggregateByAreaOperation,
        AggregateOperation,
        CalculateRatioOperation,
        RankOperation,
    ]
    assert plan.filter_for("population_by_age") == FilterGroup.model_validate(
        {"logic": "and", "conditions": [{"field": "age", "operator": "between", "value": [0, 5]}]}
    )
    assert plan.filter_for("daycare_facility") is None


def test_unknown_operation_type_is_rejected():
    data = load_plan_dict()
    data["operations"].append({"type": "buffer", "input": "daycare_facility", "distance": 500, "output": "x"})

    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        AnalysisPlan.model_validate(data)


def test_extra_fields_are_rejected():
    data = load_plan_dict()
    data["operations"][2]["sql"] = "SELECT 1"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AnalysisPlan.model_validate(data)


@pytest.mark.parametrize("output", ["Daycare Count", "1count", "count-1", ""])
def test_operation_output_must_be_snake_case(output):
    data = load_plan_dict()
    data["operations"][0]["output"] = output

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(data)


def test_ratio_scale_must_be_positive():
    data = load_plan_dict()
    data["operations"][2]["scale"] = 0

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(data)


def test_ranking_limit_is_bounded():
    data = load_plan_dict()
    data["output"]["ranking"]["limit"] = 500

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(data)


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "age", "operator": "between", "value": [5]},
        {"field": "age", "operator": "between", "value": [5, 0]},
        {"field": "age", "operator": "between", "value": [0, "5"]},
        {"field": "age", "operator": "in", "value": []},
        {"field": "age", "operator": "eq", "value": [1, 2]},
        {"field": "age", "operator": "gt", "value": None},
        {"field": "name", "operator": "contains", "value": 3},
        {"field": "name", "operator": "is_null", "value": "x"},
        {"field": "age", "operator": "like", "value": "1"},
    ],
)
def test_filter_condition_value_shape_is_validated(condition):
    with pytest.raises(ValidationError):
        FilterCondition.model_validate(condition)


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "age", "operator": "between", "value": [0, 5]},
        {"field": "reference_date", "operator": "between", "value": ["2026-01-01", "2026-12-31"]},
        {"field": "facility_type", "operator": "in", "value": ["국공립", "민간"]},
        {"field": "name", "operator": "starts_with", "value": "서울"},
        {"field": "facility_type", "operator": "is_not_null", "value": None},
        {"field": "age", "operator": "lte", "value": 5},
    ],
)
def test_filter_condition_accepts_valid_shapes(condition):
    FilterCondition.model_validate(condition)


def test_filter_expression_distinguishes_condition_and_group():
    condition = DatasetFilter.model_validate({"dataset": "d", "where": {"field": "age", "operator": "eq", "value": 3}})
    group = DatasetFilter.model_validate(
        {"dataset": "d", "where": {"logic": "or", "conditions": [{"field": "age", "operator": "eq", "value": 3}]}}
    )

    assert isinstance(condition.where, FilterCondition)
    assert isinstance(group.where, FilterGroup)


def test_not_group_requires_exactly_one_condition():
    conditions = [{"field": "age", "operator": "eq", "value": 1}, {"field": "age", "operator": "eq", "value": 2}]

    with pytest.raises(ValidationError, match="not 그룹"):
        FilterGroup.model_validate({"logic": "not", "conditions": conditions})


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


@pytest.mark.parametrize("model", [AnalysisPlan, InterpretedQuery, DatasetSelection, Explanation])
def test_llm_schema_follows_strict_structured_output_rules(model):
    schema = to_llm_json_schema(model)
    text = json.dumps(schema)

    for keyword in ("oneOf", "discriminator", '"const"', '"default"', '"pattern"', '"minimum"', '"minItems"'):
        assert keyword not in text
    for node in _walk(schema):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])
        if "$ref" in node:
            assert list(node) == ["$ref"]


def test_llm_schema_expresses_operations_as_any_of_with_enum_tags():
    schema = to_llm_json_schema(AnalysisPlan)

    variants = schema["properties"]["operations"]["items"]["anyOf"]
    tags = [schema["$defs"][v["$ref"].split("/")[-1]]["properties"]["type"]["enum"] for v in variants]
    assert tags == [["aggregate_by_area"], ["aggregate"], ["calculate_ratio"], ["rank"]]


def test_analysis_units_come_from_polygon_datasets_covering_the_region():
    datasets = TRUSTED_DATASETS.values()

    assert analysis_units(datasets, SEOUL) == [SpatialLevel.SIDO, SpatialLevel.SIGUNGU, SpatialLevel.EMD]
    expected = [SpatialLevel.SIDO, SpatialLevel.SIGUNGU, SpatialLevel.EMD]
    assert analysis_units(datasets, Region(name="서울특별시", level="sido")) == expected
    assert analysis_units(datasets, Region(name="부산광역시", level="sido")) == expected


def test_interpret_prompt_uses_finest_unit_when_question_omits_unit():
    system, _ = interpret_prompt("대구에서 상가가 많은 곳은?", [(SEOUL, [SpatialLevel.SIGUNGU, SpatialLevel.EMD])])

    assert "가장 세밀한 단위(emd > sigungu > sido 순)" in system


REGIONS = [
    RegionRef(name="대한민국", level=SpatialLevel.COUNTRY, code="KR"),
    RegionRef(name="서울특별시", level=SpatialLevel.SIDO, code="11"),
    RegionRef(name="부산광역시", level=SpatialLevel.SIDO, code="26"),
    RegionRef(name="서울특별시 중구", level=SpatialLevel.SIGUNGU, code="11140"),
    RegionRef(name="서울특별시 강남구", level=SpatialLevel.SIGUNGU, code="11680"),
    RegionRef(name="부산광역시 중구", level=SpatialLevel.SIGUNGU, code="26110"),
]


@pytest.mark.parametrize(
    ("name", "level", "codes"),
    [
        ("부산광역시 중구", "sigungu", ["26110"]),
        ("부산광역시중구", "sigungu", ["26110"]),  # 실제 qwen3.5:9b 해석
        ("부산 중구", "sigungu", ["26110"]),
        ("강남구", "sigungu", ["11680"]),
        ("중구", "sigungu", ["11140", "26110"]),  # 시도 없이는 여러 곳
        ("서울", "sido", ["11"]),
        ("대한민국", "country", ["KR"]),
        ("서울특별시", "emd", []),  # 같은 이름이라도 단위가 다르면 다른 지역
        ("대구광역시", "sido", []),
    ],
)
def test_match_regions_resolves_interpreted_names_to_registered_regions(name, level, codes):
    assert [region.code for region in match_regions(Region(name=name, level=level), REGIONS)] == codes


def test_interpretation_reports_how_the_question_named_a_unit():
    system, _ = interpret_prompt("서울 자치구 중 어린이집이 가장 많은 곳은?", [(SEOUL, [SpatialLevel.SIGUNGU])])

    assert "analysis_unit_phrase" in system
    assert "analysis_unit_phrase" in to_llm_json_schema(InterpretedQuery)["required"]


@pytest.mark.parametrize(
    ("question", "phrase", "region", "unit", "named"),
    [
        ("서울에서 공공와이파이가 가장 많은 자치구는?", "자치구", "서울특별시", "sigungu", True),
        ("노원구 행정동 별 어린이집 수", "행정동별", "서울특별시 노원구", "emd", True),  # 공백은 무시한다
        ("시도 중 인구가 가장 많은 곳은?", "시도", "대한민국", "sido", True),
        ("서울에서 어린이집이 가장 많은 곳은?", None, "서울특별시", "sigungu", False),
        ("서울에서 어린이집이 가장 많은 곳은?", "구별", "서울특별시", "sigungu", False),  # 질문에 없는 표현
        ("강남구에서 어린이집이 가장 부족한 곳은?", "구", "서울특별시 강남구", "sigungu", False),  # 지역 이름의 일부
        # 질문에 있지만 단위가 아닌 말 (gpt-4o-mini가 실제로 옮겨 적음)
        ("서울에서 어린이집이 가장 많은 곳은?", "어린이집이 가장 많은 곳", "서울특별시", "sigungu", False),
        ("서울 자치구 중 인구가 가장 많은 곳은?", "자치구", "서울특별시", "emd", False),  # 고른 단위와 맞지 않음
    ],
)
def test_unit_phrase_must_appear_in_the_question_and_name_the_unit(question, phrase, region, unit, named):
    assert unit_named(question, phrase, region, SpatialLevel(unit)) is named


GANGNAM = RegionRef(name="서울특별시 강남구", level=SpatialLevel.SIGUNGU, code="11680")


@pytest.mark.parametrize(
    ("region", "coverage", "covered"),
    [
        (SEOUL, SEOUL, True),
        (GANGNAM, SEOUL, True),  # 강남구(11680)는 서울(11) 안
        (RegionRef(name="역삼1동", level=SpatialLevel.EMD, code="1168064000"), GANGNAM, True),
        (GANGNAM, KOREA, True),
        (RegionRef(name="부산광역시 중구", level=SpatialLevel.SIGUNGU, code="26110"), SEOUL, False),
        (SEOUL, GANGNAM, False),  # 넓은 지역은 좁은 범위에 들지 않는다
        (Region(name="서울특별시 강남구", level="sigungu"), SEOUL, False),  # 코드를 모르면 같은 지역일 때만
    ],
)
def test_resolve_region_accepts_regions_inside_the_dataset_coverage(region, coverage, covered):
    resolved = resolve_region(region, coverage)

    assert (resolved is not None) is covered
    if covered and region != coverage:
        assert (resolved.name, resolved.code) == (region.name, region.code)
