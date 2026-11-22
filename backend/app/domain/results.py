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


class HexLayer(StrictModel):
    """지도 지표로 센 Point를 같은 크기 육각형 칸마다 센 격자 (EPSG:4326). 점이 많아 위치를 못 보낼 때도 있다."""

    cell_size_m: int  # 육각형 한 변 길이 (EPSG:5179 미터)
    cell_area_km2: float
    count: int  # 칸으로 센 점 수 (분석 지역 안)
    classification: Classification  # 칸별 점 수의 분위 구간
    geojson: dict[str, Any]  # 점이 있는 칸만 담은 Polygon FeatureCollection. properties: count


class BivariateAxis(StrictModel):
    metric: str  # operation output
    label: str
    breaks: list[float]  # 3분위 경계값 (최대 2개, 같은 값이 많으면 줄어든다)


class BivariateSpec(StrictModel):
    """비율 지표의 분모(x)·분자(y)를 3분위씩 나눈 이변량 지도 구간. 화면이 3×3 색으로 두 값을 함께 그린다."""

    x: BivariateAxis  # 분모 (예: 0~5세 인구, 수요)
    y: BivariateAxis  # 분자 (예: 어린이집 수, 공급)


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
    hexbins: HexLayer | None = None  # 지도 지표가 Point 개수일 때만 있다
    bivariate: BivariateSpec | None = None  # 지도 지표가 비율(calculate_ratio)일 때만 있다


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
    reviewed: bool = True  # 포털 정보로 자동 등록한 출처(자동 후보)면 False


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
