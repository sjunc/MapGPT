from collections.abc import Iterable, Mapping

from pydantic import Field

from app.domain.base import StrictModel
from app.domain.catalog import DatasetMetadata, RegionRef
from app.domain.enums import AnalysisIntent, MapType, SpatialLevel
from app.domain.filters import FilterExpression
from app.domain.operations import Operation


class Region(StrictModel):
    name: str = Field(description="지역 공식 명칭 (예: 서울특별시)")
    level: SpatialLevel


class DatasetFilter(StrictModel):
    dataset: str = Field(description="필터를 적용할 Dataset ID")
    where: FilterExpression


class RankingSpec(StrictModel):
    enabled: bool
    limit: int = Field(default=10, ge=1, le=50)


class OutputSpec(StrictModel):
    map_type: MapType
    metric: str = Field(description="지도에 표현할 operation output")
    ranking: RankingSpec


class AnalysisPlan(StrictModel):
    intent: AnalysisIntent
    region: Region
    analysis_unit: SpatialLevel
    datasets: list[str] = Field(min_length=1)
    filters: list[DatasetFilter] = []
    operations: list[Operation] = Field(min_length=1, description="실행 순서대로 나열")
    output: OutputSpec

    def filter_for(self, dataset_id: str) -> FilterExpression | None:
        return next((f.where for f in self.filters if f.dataset == dataset_id), None)


def unit_boundary_candidates(plan: AnalysisPlan, datasets: Mapping[str, DatasetMetadata]) -> list[DatasetMetadata]:
    """분석 단위(analysis_unit)의 Polygon Dataset. 결과 프레임과 지도의 기준이 되며 정확히 1개여야 한다."""
    return [
        datasets[d]
        for d in plan.datasets
        if d in datasets and datasets[d].is_polygon and datasets[d].spatial.level == plan.analysis_unit
    ]


def resolve_region(region: Region | RegionRef, coverage: RegionRef | None) -> RegionRef | None:
    """Dataset 범위가 전국이면 그 아래 시도·시군구 지역도 포함한다."""
    if coverage is not None and coverage.name == region.name and coverage.level == region.level:
        return coverage
    if coverage is not None and coverage.level == SpatialLevel.COUNTRY:
        return RegionRef(name=region.name, level=region.level, code=getattr(region, "code", ""))
    return None


def analysis_units(datasets: Iterable[DatasetMetadata], region: Region | RegionRef) -> list[SpatialLevel]:
    """region을 범위로 가진 Polygon 경계 Dataset이 있어 분석 단위로 쓸 수 있는 공간 단위."""
    order = {
        SpatialLevel.COUNTRY: 0,
        SpatialLevel.SIDO: 1,
        SpatialLevel.SIGUNGU: 2,
        SpatialLevel.EMD: 3,
    }
    levels = {
        d.spatial.level
        for d in datasets
        if d.is_polygon
        and d.spatial.level is not None
        and order[d.spatial.level] >= order[region.level]
        and resolve_region(region, d.spatial.coverage)
    }
    return [level for level in SpatialLevel if level in levels]
