"""좌표만 있는 관측 자료를 AI 계획으로 지역에 연결해 평균·합계·순위로 실행한다."""

import pytest

from app.catalog.definitions import DAYCARE_FACILITY, EMD_BOUNDARY, SEOUL
from app.domain.catalog import ColumnMetadata
from app.domain.enums import MapView
from app.domain.maps import map_style, point_count_operation
from app.domain.plan import AnalysisPlan
from app.executor.operation_executor import execute_operations, prepare_context
from app.gis import tools
from app.validation.plan_repair import repair_plan
from app.validation.plan_validator import validate_plan
from app.validation.result_validator import validate_result
from app.workflows.response import compose_response
from tests.fakes import interpreted
from tests.integration.synthetic import load_synthetic

pytestmark = pytest.mark.integration


@pytest.fixture
def observations(conn):
    datasets = load_synthetic(conn)
    conn.execute("CREATE TABLE datasets.observations (temperature double precision, geom geometry(Point, 4326))")
    # A: -6, null, 공유 경계 -2 → 평균 -4, B: 0, G: null만, 나머지: 관측 없음.
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO datasets.observations VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            [
                (-6, 126.92, 37.55),
                (None, 126.95, 37.55),
                (-2, 127.0, 37.55),
                (0, 127.05, 37.55),
                (None, 126.93, 37.65),
                (-100, 129.05, 35.15),
                (10, None, None),
            ],
        )
    datasets["observations"] = DAYCARE_FACILITY.model_copy(
        update={
            "dataset_id": "observations",
            "title": "겨울 관측기온",
            "join_keys": [],
            "columns": [
                ColumnMetadata(
                    name="temperature", description="평균기온", data_type="float", semantic_type="text", nullable=True
                ),
                DAYCARE_FACILITY.geometry_column,
            ],
        }
    )
    try:
        yield datasets
    finally:
        conn.rollback()


def _plan(aggregation="avg", field="temperature"):
    return AnalysisPlan.model_validate(
        {
            "intent": "regional_summary",
            "region": {"name": "서울특별시", "level": "sido"},
            "analysis_unit": "emd",
            "datasets": ["observations", "emd_boundary"],
            "operations": [
                {
                    "type": "aggregate_by_area",
                    "input": "observations",
                    "boundary": "emd_boundary",
                    "aggregation": aggregation,
                    "field": field,
                    "output": "winter_value",
                    "label": "겨울 기온",
                },
                {
                    "type": "rank",
                    "input": "winter_value",
                    "order": "desc",
                    "output": "winter_rank",
                    "label": "추운 순위",
                },
            ],
            "output": {"map_type": "choropleth", "metric": "winter_value", "ranking": {"enabled": True, "limit": 10}},
        }
    )


@pytest.mark.parametrize(("function", "expected"), [("avg", -4), ("sum", -8)])
def test_spatial_measures_preserve_nulls_and_assign_boundary_points_once(conn, observations, function, expected):
    boundary = tools.get_boundary(conn, EMD_BOUNDARY, SEOUL)
    view = tools.get_dataset(conn, observations["observations"])
    result = tools.aggregate_by_area(conn, view, boundary, function, "winter_value", "temperature")
    assert result.values["1111000100"] == expected
    assert result.values["1111000200"] == 0  # 영도는 결측이 아니다.
    assert result.values["1111000300"] is None
    assert result.null_reasons["1111000300"] == "all_values_null"
    assert result.null_reasons["1114000100"] == "no_matching_records"
    assert result.stats == {
        "input_points": 7,
        "points_without_location": 1,
        "assigned_points": 5,
        "unassigned_points": 1,
    }


def test_coldest_ranking_accepts_negative_sparse_observations_and_describes_the_method(conn, observations):
    plan, _ = repair_plan(_plan(), observations, SEOUL, "겨울이 가장 추운지역순위")
    assert plan.operations[1].order == "asc"
    assert validate_plan(plan, observations, plan.datasets, SEOUL).ok
    ctx = prepare_context(conn, plan, observations, SEOUL)
    result = execute_operations(ctx)
    report = validate_result(result, plan, observations)
    assert report.ok, report.errors
    assert "SPARSE_OBSERVATIONS" in [issue.code for issue in report.warnings]
    assert point_count_operation(plan) is None
    style = map_style(plan)
    assert style.views == [MapView.CHOROPLETH] and style.emphasis == "low"
    layer = tools.create_map_layer(conn, ctx.boundary, result.rows, "winter_value", "겨울 기온", style)
    response = compose_response(
        run_id="test",
        result_id="test",
        question="겨울이 가장 추운지역순위",
        interpreted=interpreted(),
        plan=plan,
        datasets=observations,
        result=result,
        result_validation=report,
        map_layer=layer,
    )
    assert [(r.unit_key, r.value) for r in response.ranking] == [("1111000100", -4), ("1111000200", 0)]
    assert layer.null_count == 8
    assert "temperature 평균" in " ".join(response.analysis_method)
    assert "산술평균" in " ".join(response.limitations)


@pytest.mark.parametrize(
    ("function", "field", "code"),
    [
        ("avg", None, "FIELD_UNKNOWN"),
        ("sum", "unknown", "FIELD_UNKNOWN"),
        ("avg", "geom", "FIELD_NOT_NUMERIC"),
        ("count", "temperature", "FIELD_UNEXPECTED"),
    ],
)
def test_spatial_measure_fields_are_validated_before_sql(conn, observations, function, field, code):
    plan = _plan(function, field)
    assert code in [issue.code for issue in validate_plan(plan, observations, plan.datasets, SEOUL).errors]
    with pytest.raises(tools.GisToolError) as error:
        tools.aggregate_by_area(
            conn,
            tools.get_dataset(conn, observations["observations"]),
            tools.get_boundary(conn, EMD_BOUNDARY, SEOUL),
            function,
            "value",
            field,
        )
    assert error.value.code == code


def test_all_missing_observations_still_fail(conn, observations):
    conn.execute("UPDATE datasets.observations SET temperature = NULL")
    plan = _plan()
    result = execute_operations(prepare_context(conn, plan, observations, SEOUL))
    assert "HIGH_NULL_RATE" in [issue.code for issue in validate_result(result, plan, observations).errors]


def test_spatial_average_filter_is_applied_before_aggregation(conn, observations):
    from app.domain.filters import FilterCondition

    view = tools.filter_dataset(
        conn,
        tools.get_dataset(conn, observations["observations"]),
        FilterCondition(field="temperature", operator="gte", value=-2),
    )
    result = tools.aggregate_by_area(
        conn, view, tools.get_boundary(conn, EMD_BOUNDARY, SEOUL), "avg", "value", "temperature"
    )
    assert result.values["1111000100"] == -2
    assert result.values["1111000200"] == 0
