import pytest

from app.catalog.definitions import SEOUL, TRUSTED_DATASETS
from app.domain.enums import MapType, SpatialLevel
from app.domain.plan import AnalysisPlan
from app.domain.results import BoundaryStats, Classification, ExecutionResult, MapLayer, UnitRow, ValidationReport
from app.workflows.response import compose_response, explanation_facts
from tests.fakes import interpreted
from tests.support import QUESTION, load_plan_dict


@pytest.mark.parametrize(
    ("order", "direction"), [("asc", "오름차순(값이 작을수록 1위)"), ("desc", "내림차순(값이 클수록 1위)")]
)
def test_explanation_facts_state_the_ranking_order(order, direction):
    # 설명 LLM이 1위를 가장 큰 값으로 가정하면 오름차순 순위의 최솟값을 '가장 많다'고 쓴다.
    data = load_plan_dict()
    data["operations"][3]["order"] = order
    plan = AnalysisPlan.model_validate(data)
    rows = [
        UnitRow(
            key=f"111100000{i}",
            name=f"동{i}",
            parent_name="종로구",
            values={
                "daycare_count": i,
                "child_population": 100,
                "daycare_per_1000_children": i * 10.0,
                "shortage_rank": i,
            },
        )
        for i in range(1, 4)
    ]
    result = ExecutionResult(
        analysis_unit=SpatialLevel.EMD,
        unit_dataset="emd_boundary",
        region=SEOUL,
        analysis_crs="EPSG:4326",
        boundary=BoundaryStats(unit_count=3, invalid_geometry_count=0, empty_geometry_count=0, bbox=None),
        rows=rows,
        metrics=[],
        operations=[],
    )
    layer = MapLayer(
        type=MapType.CHOROPLETH,
        metric=plan.output.metric,
        metric_label="어린이집 수",
        crs="EPSG:4326",
        classification=Classification(method="quantile", class_count=0, breaks=[], min=None, max=None),
        feature_count=3,
        null_count=0,
        geojson={},
    )
    response = compose_response(
        run_id="run",
        result_id="result",
        question=QUESTION,
        interpreted=interpreted(),
        plan=plan,
        datasets=TRUSTED_DATASETS,
        result=result,
        result_validation=ValidationReport(issues=[]),
        map_layer=layer,
    )

    facts = explanation_facts(response, plan, result)

    assert facts["ranking_order"] == direction
    assert direction in "\n".join(response.analysis_method)
