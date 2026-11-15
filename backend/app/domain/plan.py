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


_LEVEL_ORDER = {SpatialLevel.COUNTRY: 0, SpatialLevel.SIDO: 1, SpatialLevel.SIGUNGU: 2, SpatialLevel.EMD: 3}


def contains_region(outer: RegionRef, inner: Region | RegionRef) -> bool:
    """outer가 inner를 포함하는 더 넓은 지역인지. 전국은 모든 하위 지역을 포함한다.

    행안부 계층 코드라 하위 지역 코드는 상위 지역 코드로 시작한다 (강남구 11680 ⊂ 서울 11).
    코드를 모르는 지역(LLM이 쓴 이름만 있는 Region)은 전국 범위일 때만 포함으로 본다.
    """
    if _LEVEL_ORDER[inner.level] <= _LEVEL_ORDER[outer.level]:
        return False
    if outer.level == SpatialLevel.COUNTRY:
        return True
    code = getattr(inner, "code", "")
    return bool(code) and code.startswith(outer.code)


def resolve_region(region: Region | RegionRef, coverage: RegionRef | None) -> RegionRef | None:
    """Dataset 범위가 분석 지역과 같거나 분석 지역을 포함하면 분석에 쓸 지역을 돌려준다."""
    if coverage is None:
        return None
    if coverage.name == region.name and coverage.level == region.level:
        return coverage
    if contains_region(coverage, region):
        return RegionRef(name=region.name, level=region.level, code=getattr(region, "code", ""))
    return None


def match_regions(region: Region | RegionRef, regions: Iterable[RegionRef]) -> list[RegionRef]:
    """해석된 지역을 등록된 지역(이름·단위·행정코드)과 대조한다. 이름의 공백은 무시한다.

    시도는 앞부분만 써도('서울') 찾고, 시군구는 시도를 빼거나 줄여 써도('중구', '부산 중구') 찾는다.
    같은 이름의 시군구가 여러 시도에 있으면 후보가 여럿 나오므로, 하나로 정해졌는지는 호출하는 쪽이 확인한다.
    """
    registered = list(regions)
    wanted = _compact(region.name)
    same_level = [r for r in registered if r.level == region.level]
    exact = [r for r in same_level if _compact(r.name) == wanted]
    if exact or not wanted:
        return exact
    if region.level == SpatialLevel.SIDO:
        return [r for r in same_level if _compact(r.name).startswith(wanted)]
    if region.level == SpatialLevel.SIGUNGU:
        # 시군구의 등록 이름은 '시도 시군구'다. 시군구 이름으로 끝나고, 앞부분이 그 시도 이름의 앞부분이면 같은 지역이다.
        sido_names = {r.code: _compact(r.name) for r in registered if r.level == SpatialLevel.SIDO}
        matches = []
        for candidate in same_level:
            sido = sido_names.get(candidate.code[:2], "")
            short = _compact(candidate.name).removeprefix(sido)
            if wanted.endswith(short) and sido.startswith(wanted.removesuffix(short)):
                matches.append(candidate)
        return matches
    return []


def _compact(name: str) -> str:
    return "".join(name.split())


# 행정구역 이름 끝 글자. 단위 표현(예: '자치구', '행정동별', '시도 중')에는 고른 단위의 글자가 들어 있다.
_UNIT_MARKS = {SpatialLevel.EMD: "동읍면", SpatialLevel.SIGUNGU: "구군시", SpatialLevel.SIDO: "도시"}


def unit_named(question: str, phrase: str | None, region_name: str, unit: SpatialLevel | None) -> bool:
    """해석 LLM이 옮겨 적은 결과 단위 표현(예: '자치구')이 질문에 실제로 있고 고른 단위를 가리키는지 본다.

    공백은 무시한다. 지역 이름에 든 글자(예: '강남구'의 '구')는 단위가 아니므로 지역 이름의 각 부분을 질문에서
    지운 뒤 찾는다. 단위가 아닌 말('어린이집이 가장 많은 곳')을 옮겨 적는 경우가 있어, 고른 단위의 행정구역 글자
    (행정동 동·읍·면, 시군구 구·군·시, 시도 도·시)가 표현에 있어야 한다.
    """
    text = _compact(question)
    for part in region_name.split():
        text = text.replace(part, "")
    wanted = _compact(phrase or "")
    marks = _UNIT_MARKS.get(unit, "")
    return bool(wanted) and wanted in text and any(mark in wanted for mark in marks)


def analysis_units(datasets: Iterable[DatasetMetadata], region: Region | RegionRef) -> list[SpatialLevel]:
    """region을 범위로 가진 Polygon 경계 Dataset이 있어 분석 단위로 쓸 수 있는 공간 단위."""
    levels = {
        d.spatial.level
        for d in datasets
        if d.is_polygon
        and d.spatial.level is not None
        and _LEVEL_ORDER[d.spatial.level] >= _LEVEL_ORDER[region.level]
        and resolve_region(region, d.spatial.coverage)
    }
    return [level for level in SpatialLevel if level in levels]
