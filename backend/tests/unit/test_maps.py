import pytest

from app.domain.enums import MapFocus, MapView, MetricKind
from app.domain.maps import map_focus, map_style, metric_kind
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
    # 비율은 분자·분모를 함께 보는 이변량 지도로도 바꿔 볼 수 있다.
    assert (style.default_view, style.views) == (MapView.CHOROPLETH, [MapView.CHOROPLETH, MapView.BIVARIATE])
    assert (style.emphasis, style.legend_low, style.legend_high) == ("low", "공급 부족", "여유")


@pytest.mark.parametrize(
    ("operation", "extra"),
    [(COUNT_BY_AREA, [MapView.HEXBIN]), (_aggregate("sum"), []), (_aggregate("count"), [])],
)
def test_total_metric_defaults_to_proportional_symbols(operation, extra):
    plan = _summary_plan(operation)

    style = map_style(plan)

    assert metric_kind(plan) == MetricKind.TOTAL
    assert style.default_view == MapView.PROPORTIONAL_SYMBOL
    # Point 개수는 위치를 보내지 않아도 서버에서 센 육각 격자로 볼 수 있다.
    assert style.views == [MapView.PROPORTIONAL_SYMBOL, MapView.CHOROPLETH, *extra]
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


def test_emphasis_follows_the_ranking_direction_in_point_views_too():
    style = map_style(_summary_plan(COUNT_BY_AREA, order="asc"), MapFocus.LOCATION, has_points=True)
    assert (style.default_view, style.emphasis) == (MapView.POINT, "low")


@pytest.mark.parametrize(
    ("question", "phrase", "focus"),
    [
        ("서울 어린이집 위치를 보여줘", "위치", MapFocus.LOCATION),
        ("강남구 어린이집이 어디에 있는지 지도로 보여줘", "어디에 있는지", MapFocus.LOCATION),
        ("서울 어린이집 분포", "분포", MapFocus.LOCATION),
        ("서울에서 어린이집이 밀집한 곳은?", "밀집한 곳", MapFocus.DENSITY),
        ("어린이집이 몰려 있는 동네", "몰려 있는", MapFocus.DENSITY),
        ("어린이집이 밀집한 위치", "밀집한 위치", MapFocus.DENSITY),  # 밀집을 먼저 본다
    ],
)
def test_map_focus_comes_from_a_location_or_density_phrase_in_the_question(question, phrase, focus):
    assert map_focus(question, phrase) == focus


@pytest.mark.parametrize(
    ("question", "phrase"),
    [
        ("서울에서 어린이집이 가장 많은 곳은?", "가장 많은 곳"),  # 순위를 묻는 표현
        ("서울 자치구별 어린이집 수", None),
        ("서울 자치구별 어린이집 수", ""),
        ("서울 자치구별 어린이집 수", "위치"),  # 질문에 없는 표현
    ],
)
def test_map_focus_ignores_phrases_that_are_not_about_location_or_density(question, phrase):
    assert map_focus(question, phrase) is None


@pytest.mark.parametrize(
    ("focus", "view"),
    [(None, MapView.PROPORTIONAL_SYMBOL), (MapFocus.LOCATION, MapView.POINT), (MapFocus.DENSITY, MapView.HEATMAP)],
)
def test_point_counts_can_also_be_seen_as_points_and_density(focus, view):
    style = map_style(_summary_plan(COUNT_BY_AREA), focus, has_points=True)

    assert style.default_view == view
    # 버튼 순서는 질문과 관계없이 같다: 지역별 보기 다음에 점 보기
    assert style.views == [
        MapView.PROPORTIONAL_SYMBOL,
        MapView.CHOROPLETH,
        MapView.POINT,
        MapView.HEATMAP,
        MapView.HEXBIN,
    ]


@pytest.mark.parametrize(
    ("focus", "view"),
    [(None, MapView.PROPORTIONAL_SYMBOL), (MapFocus.LOCATION, MapView.HEXBIN), (MapFocus.DENSITY, MapView.HEXBIN)],
)
def test_too_many_points_fall_back_to_the_hexagon_grid(focus, view):
    # 점이 너무 많아 위치를 보내지 못하면 위치·밀집 질문은 같은 크기 육각형 칸마다 센 개수로 보인다.
    style = map_style(_summary_plan(COUNT_BY_AREA), focus, has_points=False)

    assert style.default_view == view
    assert style.views == [MapView.PROPORTIONAL_SYMBOL, MapView.CHOROPLETH, MapView.HEXBIN]


def test_focus_without_point_locations_keeps_the_regional_views():
    # 점 개수가 아닌 지표(인구 합계)이거나 점이 너무 많아 위치를 보내지 않으면 지역별 보기만 둔다.
    style = map_style(_summary_plan(_aggregate("sum")), MapFocus.LOCATION)

    assert (style.default_view, style.views) == (
        MapView.PROPORTIONAL_SYMBOL,
        [MapView.PROPORTIONAL_SYMBOL, MapView.CHOROPLETH],
    )


def test_rate_metric_stays_a_choropleth_when_location_is_asked():
    style = map_style(load_plan(), MapFocus.LOCATION, has_points=True)

    assert (style.default_view, style.views) == (MapView.CHOROPLETH, [MapView.CHOROPLETH, MapView.BIVARIATE])
