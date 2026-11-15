"""결과 지도 보기: 지도 지표의 성격과 순위 방향, 질문의 초점으로 기본 보기·강조 방향·범례 문구를 정한다 (LLM 없음).

개수·합계를 색으로 칠하면 넓은 지역이 진해 보이므로 비례 원을 기본으로 하고, 비율·평균은 단계 구분도로 그린다.
지표가 Point 개수이고 질문이 위치나 밀집을 보려 하면 점 지도·밀도 지도를 기본으로 한다.
"""

from app.domain.enums import AggregationFunction, MapFocus, MapView, MetricKind, SortOrder
from app.domain.operations import (
    AggregateByAreaOperation,
    AggregateOperation,
    CalculateRatioOperation,
    Operation,
    RankOperation,
)
from app.domain.plan import AnalysisPlan
from app.domain.policies import POLICIES
from app.domain.results import MapStyle

_KIND_LABELS = {
    MetricKind.RATE: ("낮음", "높음"),
    MetricKind.AVERAGE: ("낮음", "높음"),
    MetricKind.TOTAL: ("적음", "많음"),
}

# 위치·밀집을 뜻하는 말 (공백 없이 비교). '밀집한 위치'는 밀집이므로 밀집을 먼저 본다.
_FOCUS_MARKS = (
    (MapFocus.DENSITY, ("밀집", "밀도", "몰려", "몰린", "모여", "모인", "집중")),
    (MapFocus.LOCATION, ("위치", "분포", "어디에있", "어디있", "점으로", "찍어")),
)
_FOCUS_VIEWS = {MapFocus.LOCATION: MapView.POINT, MapFocus.DENSITY: MapView.HEATMAP}

_NOTES = {
    MapView.PROPORTIONAL_SYMBOL: "개수·합계라서 원의 넓이로 비교합니다. 색으로 칠하면 넓은 지역이 진해 보일 수 있습니다.",
    MapView.POINT: "위치를 물어 하나하나를 점으로 보입니다. 가까운 점은 묶어 개수로 보이고, 확대하면 풀립니다.",
    MapView.HEATMAP: "밀집한 곳을 물어 점이 몰린 정도를 색 번짐으로 보입니다. 행정구역 경계와 관계없이 퍼져 보입니다.",
    MapView.HEXBIN: "위치·밀집을 물었지만 점이 많아, 같은 크기 육각형 칸마다 센 개수로 보입니다.",
    MapView.CHOROPLETH: "비율·평균이라서 지역 크기와 관계없이 값 구간별 색으로 비교합니다.",
}


def metric_operation(plan: AnalysisPlan) -> Operation:
    """지도 지표(output.metric)를 만든 Operation."""
    return next(op for op in plan.operations if op.output == plan.output.metric)


def point_count_operation(plan: AnalysisPlan) -> AggregateByAreaOperation | None:
    """지도 지표가 Point 개수면 그 Operation. 센 점의 위치와 육각 격자를 함께 보낼 수 있다."""
    operation = metric_operation(plan)
    return operation if isinstance(operation, AggregateByAreaOperation) else None


def ratio_operation(plan: AnalysisPlan) -> CalculateRatioOperation | None:
    """지도 지표가 비율이면 그 Operation. 분자·분모를 함께 보는 이변량 지도를 그릴 수 있다."""
    operation = metric_operation(plan)
    return operation if isinstance(operation, CalculateRatioOperation) else None


def metric_kind(plan: AnalysisPlan) -> MetricKind:
    """지도 지표를 만든 Operation으로 지표의 성격을 정한다."""
    operation = metric_operation(plan)
    if isinstance(operation, CalculateRatioOperation):
        return MetricKind.RATE
    if isinstance(operation, AggregateOperation) and operation.aggregation.function == AggregationFunction.AVG:
        return MetricKind.AVERAGE
    return MetricKind.TOTAL


def map_focus(question: str, phrase: str | None) -> MapFocus | None:
    """해석 LLM이 옮겨 적은 표현(예: '위치', '밀집한 곳')이 질문에 있고 위치·밀집을 뜻하면 그 초점을 돌려준다.

    '가장 많은 곳'처럼 순위를 묻는 표현을 옮겨 적는 경우가 있어, 위치·밀집을 뜻하는 말이 표현에 있어야 한다.
    """
    wanted = "".join((phrase or "").split())
    if not wanted or wanted not in "".join(question.split()):
        return None
    return next((focus for focus, marks in _FOCUS_MARKS if any(mark in wanted for mark in marks)), None)


def map_style(plan: AnalysisPlan, focus: MapFocus | None = None, has_points: bool = False) -> MapStyle:
    """has_points: 지도 지표로 센 Point의 위치를 함께 보내는지 (점 지도·밀도 지도를 그릴 수 있는지).

    Point 개수 지표는 위치를 보내지 못해도(점이 너무 많을 때) 육각 격자로 볼 수 있고, 비율 지표는 이변량 지도로 볼 수 있다.
    """
    kind = metric_kind(plan)
    policy = POLICIES[plan.intent]
    # 표의 순위와 같은 쪽을 강조한다. 지도 지표의 순위가 없으면 분석 정책의 기본 방향을 따른다.
    rank = next(
        (op for op in plan.operations if isinstance(op, RankOperation) and op.input == plan.output.metric), None
    )
    order = rank.order if rank else policy.rank_order
    low, high = policy.legend_labels or _KIND_LABELS[kind]
    common = {"emphasis": "low" if order == SortOrder.ASC else "high", "legend_low": low, "legend_high": high}
    if kind != MetricKind.TOTAL:
        views = [MapView.CHOROPLETH, MapView.BIVARIATE] if ratio_operation(plan) else [MapView.CHOROPLETH]
        return MapStyle(default_view=MapView.CHOROPLETH, views=views, note=_NOTES[MapView.CHOROPLETH], **common)
    # 버튼 순서는 질문과 관계없이 같다: 지역별 보기 다음에 점 보기
    views = [MapView.PROPORTIONAL_SYMBOL, MapView.CHOROPLETH]
    default = MapView.PROPORTIONAL_SYMBOL
    if has_points:
        views += [MapView.POINT, MapView.HEATMAP]
        default = _FOCUS_VIEWS.get(focus, default)
    if point_count_operation(plan):
        views.append(MapView.HEXBIN)
        if focus and not has_points:
            default = MapView.HEXBIN
    return MapStyle(default_view=default, views=views, note=_NOTES[default], **common)
