from datetime import date
from typing import Any, Literal

from app.domain.base import StrictModel
from app.domain.catalog import RegionRef
from app.domain.enums import MapType, MapView, Severity, SpatialLevel

MetricValue = float | int | None

# 지도 GeoJSON properties에서 지표 이름과 겹치면 안 되는 예약 이름
RESERVED_PROPERTY_NAMES = frozenset({"unit_key", "unit_name", "parent_name", "null_reason", "label_point"})


class ValidationIssue(StrictModel):
    code: str
    severity: Severity
    message: str
    path: str | None = None


class ValidationReport(StrictModel):
    issues: list[ValidationIssue] = []

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors


class OperationLog(StrictModel):
    index: int
    type: str
    output: str
    label: str
    tool: str
    params: dict[str, Any]
    stats: dict[str, float | int]
    duration_ms: float


class UnitRow(StrictModel):
    key: str
    name: str
    parent_name: str | None
    values: dict[str, MetricValue]
    null_reasons: dict[str, str] = {}


class MetricInfo(StrictModel):
    name: str
    label: str
    operation: str
    null_count: int


class BoundaryStats(StrictModel):
    unit_count: int
    invalid_geometry_count: int
    empty_geometry_count: int
    bbox: list[float] | None


class ExecutionResult(StrictModel):
    analysis_unit: SpatialLevel
    unit_dataset: str
    region: RegionRef
    analysis_crs: str
    boundary: BoundaryStats
    rows: list[UnitRow]
    metrics: list[MetricInfo]
    operations: list[OperationLog]
    # 분석 단위에 매칭되지 않은 metric key (예: 경계에 없는 인구 행정동 코드)
    unmatched_keys: dict[str, list[str]] = {}


class Classification(StrictModel):
    method: Literal["quantile"]
    class_count: int
    breaks: list[float]
    min: float | None
    max: float | None


class MapStyle(StrictModel):
    """지도 지표의 성격과 순위 방향으로 코드가 정한 지도 보기 (domain/maps.py)."""

    default_view: MapView
    views: list[MapView]  # 화면에서 바꿔 볼 수 있는 보기 (기본 보기 포함)
    emphasis: Literal["low", "high"]  # 진하게 강조할 쪽 값 (순위와 같은 방향)
    legend_low: str  # 범례에서 작은 값 쪽 문구
    legend_high: str  # 범례에서 큰 값 쪽 문구
    note: str  # 이 보기를 고른 이유 (화면에 그대로 보인다)


class PointLayer(StrictModel):
    """지도 지표로 센 Point(시설)의 위치 (EPSG:4326). 점 지도·밀도 지도에 쓴다."""

    dataset_id: str
    title: str  # Dataset 이름 (범례 제목)
    count: int  # 분석 지역 안에 있어 지표에 센 점 수
    geojson: dict[str, Any]  # Point FeatureCollection. properties: name(시설 이름, 없으면 null), unit_key(배정된 단위)


class MapLayer(StrictModel):
    type: MapType
    metric: str
    metric_label: str
    crs: str
    classification: Classification
    feature_count: int
    null_count: int
    geojson: dict[str, Any]  # 단위마다 properties.label_point = [경도, 위도] (비례 원 위치)
    style: MapStyle
    # 지도 지표가 Point 개수일 때만 있다. 점이 너무 많으면(gis.tools.POINT_LIMIT) 보내지 않는다.
    points: PointLayer | None = None


class MetricDescription(StrictModel):
    name: str
    label: str
    interpretation: str


class RankingItem(StrictModel):
    rank: int
    unit_key: str
    unit_name: str
    parent_name: str | None
    value: float
    values: dict[str, MetricValue]


class DatasetUsage(StrictModel):
    dataset_id: str
    title: str
    provider: str
    source: str
    license: str | None
    reference_date: date | None


class FinalResponse(StrictModel):
    run_id: str
    result_id: str
    question: str
    summary: str
    metric: MetricDescription
    metric_labels: dict[str, str]  # operation output → label (랭킹 표·툴팁 열 이름)
    rank_metric: str | None  # ranking 순위를 담은 operation output
    ranking: list[RankingItem]
    map_layer: MapLayer
    analysis_method: list[str]
    datasets: list[DatasetUsage]
    assumptions: list[str]
    limitations: list[str]
