"""결과 지도 보기: 지도 지표의 성격과 순위 방향으로 기본 보기·강조 방향·범례 문구를 정한다 (LLM 없음).

개수·합계를 색으로 칠하면 넓은 지역이 진해 보이므로 비례 원을 기본으로 하고, 비율·평균은 단계 구분도로 그린다.
"""

from app.domain.enums import AggregationFunction, MapView, MetricKind, SortOrder
from app.domain.operations import AggregateOperation, CalculateRatioOperation, RankOperation
from app.domain.plan import AnalysisPlan
from app.domain.policies import POLICIES
from app.domain.results import MapStyle

_KIND_LABELS = {
    MetricKind.RATE: ("낮음", "높음"),
    MetricKind.AVERAGE: ("낮음", "높음"),
    MetricKind.TOTAL: ("적음", "많음"),
}


def metric_kind(plan: AnalysisPlan) -> MetricKind:
    """지도 지표(output.metric)를 만든 Operation으로 지표의 성격을 정한다."""
    operation = next(op for op in plan.operations if op.output == plan.output.metric)
    if isinstance(operation, CalculateRatioOperation):
        return MetricKind.RATE
    if isinstance(operation, AggregateOperation) and operation.aggregation.function == AggregationFunction.AVG:
        return MetricKind.AVERAGE
    return MetricKind.TOTAL


def map_style(plan: AnalysisPlan) -> MapStyle:
    kind = metric_kind(plan)
    policy = POLICIES[plan.intent]
    # 표의 순위와 같은 쪽을 강조한다. 지도 지표의 순위가 없으면 분석 정책의 기본 방향을 따른다.
    rank = next(
        (op for op in plan.operations if isinstance(op, RankOperation) and op.input == plan.output.metric), None
    )
    order = rank.order if rank else policy.rank_order
    low, high = policy.legend_labels or _KIND_LABELS[kind]
    common = {"emphasis": "low" if order == SortOrder.ASC else "high", "legend_low": low, "legend_high": high}
    if kind == MetricKind.TOTAL:
        return MapStyle(
            default_view=MapView.PROPORTIONAL_SYMBOL,
            views=[MapView.PROPORTIONAL_SYMBOL, MapView.CHOROPLETH],
            note="개수·합계라서 원의 넓이로 비교합니다. 색으로 칠하면 넓은 지역이 진해 보일 수 있습니다.",
            **common,
        )
    return MapStyle(
        default_view=MapView.CHOROPLETH,
        views=[MapView.CHOROPLETH],
        note="비율·평균이라서 지역 크기와 관계없이 값 구간별 색으로 비교합니다.",
        **common,
    )
