import pytest

from app.domain.enums import MapView, MetricKind
from app.domain.maps import map_style, metric_kind
from app.domain.plan import AnalysisPlan
from tests.support import load_plan

COUNT_BY_AREA = {
    "type": "aggregate_by_area",
    "input": "daycare_facility",
    "boundary": "sigungu_boundary",
    "aggregation": "count",
}


def _aggregate(function: str) -> dict:
    return {
        "type": "aggregate",
        "input": "population_by_age",
        "group_by": ["adm_cd"],
        "aggregation": {"field": "population", "function": function},
    }


def _summary_plan(operation: dict, order: str = "desc") -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "intent": "regional_summary",
            "region": {"name": "서울특별시", "level": "sido"},
            "analysis_unit": "sigungu",
            "datasets": ["daycare_facility", "sigungu_boundary"],
            "operations": [
                {**operation, "output": "value", "label": "값"},
                {"type": "rank", "input": "value", "order": order, "output": "value_rank", "label": "값 순위"},
            ],
            "output": {"map_type": "choropleth", "metric": "value", "ranking": {"enabled": True, "limit": 10}},
        }
    )


def test_rate_metric_keeps_the_choropleth_and_supply_wording():
    plan = load_plan()  # 첫 Use Case: calculate_ratio 지표를 오름차순으로 순위

    style = map_style(plan)

    assert metric_kind(plan) == MetricKind.RATE
    assert (style.default_view, style.views) == (MapView.CHOROPLETH, [MapView.CHOROPLETH])
    assert (style.emphasis, style.legend_low, style.legend_high) == ("low", "공급 부족", "여유")


@pytest.mark.parametrize("operation", [COUNT_BY_AREA, _aggregate("sum"), _aggregate("count")])
def test_total_metric_defaults_to_proportional_symbols(operation):
    plan = _summary_plan(operation)

    style = map_style(plan)

    assert metric_kind(plan) == MetricKind.TOTAL
    assert style.default_view == MapView.PROPORTIONAL_SYMBOL
    assert style.views == [MapView.PROPORTIONAL_SYMBOL, MapView.CHOROPLETH]
    assert (style.emphasis, style.legend_low, style.legend_high) == ("high", "적음", "많음")


def test_average_metric_is_a_choropleth():
    plan = _summary_plan(_aggregate("avg"))

    style = map_style(plan)

    assert metric_kind(plan) == MetricKind.AVERAGE
    assert (style.default_view, style.views) == (MapView.CHOROPLETH, [MapView.CHOROPLETH])
    assert (style.legend_low, style.legend_high) == ("낮음", "높음")


def test_emphasis_follows_the_ranking_direction():
    # '가장 적은 구'처럼 오름차순으로 순위를 매기면 작은 값 쪽을 진하게 강조한다.
    assert map_style(_summary_plan(COUNT_BY_AREA, order="asc")).emphasis == "low"
    assert map_style(_summary_plan(COUNT_BY_AREA, order="desc")).emphasis == "high"
