from datetime import date

from pydantic import Field

from app.domain.base import StrictModel
from app.domain.enums import DataType, GeometryType, SemanticType, SpatialLevel

AUTO_DISCOVERED_CATEGORY = "auto_discovered"  # CSV 자동 발견으로 등록한 Dataset의 category
PUBLIC_API_CATEGORY = "public_api"  # 등록 파일로 승인한 외부 API 출처에서 받아 적재하는 Dataset의 category


class SourceAcquisition(StrictModel):
    """등록 파일로 승인한 외부 출처에서 받아 적재하는 Dataset의 수집 상태."""

    source_id: str
    connector: str
    service: str
    fingerprint: str  # 등록 파일 내용 해시. 바뀌면 이미 받은 데이터도 다시 적재한다
    refresh_days: int
    fetched_at: date | None = None  # None이면 아직 받지 않은 등록 출처
    rows: int | None = None

    def is_due(self, today: date, fingerprint: str) -> bool:
        """아직 받지 않았거나, 다시 받을 주기가 지났거나, 등록 파일이 바뀌었으면 받아야 한다."""
        return (
            self.fetched_at is None
            or (today - self.fetched_at).days >= self.refresh_days
            or self.fingerprint != fingerprint
        )


class ColumnMetadata(StrictModel):
    name: str
    description: str
    data_type: DataType
    semantic_type: SemanticType
    unit: str | None = None
    nullable: bool = False


class JoinKey(StrictModel):
    field: str
    semantic_type: SemanticType
    # 같은 의미라도 코드 체계가 다르면 결합할 수 없다 (예: 행안부 10자리 vs 통계청 8자리).
    code_system: str | None = None


class RegionRef(StrictModel):
    name: str
    level: SpatialLevel
    code: str


class SpatialMetadata(StrictModel):
    level: SpatialLevel | None
    geometry_type: GeometryType | None
    crs: str | None
    coverage: RegionRef | None
    bbox: list[float] | None = Field(default=None, description="EPSG:4326 [minx, miny, maxx, maxy]")
    feature_count: int | None = None


class TemporalMetadata(StrictModel):
    reference_date: date | None
    update_cycle: str | None


class DatasetMetadata(StrictModel):
    dataset_id: str
    title: str
    description: str
    category: str
    semantic_tags: list[str]
    provider: str
    source: str
    license: str | None
    spatial: SpatialMetadata
    temporal: TemporalMetadata
    columns: list[ColumnMetadata]
    join_keys: list[JoinKey]
    quality_notes: list[str] = []
    acquisition: SourceAcquisition | None = None  # 등록 출처에서 받아 적재하는 Dataset만 있다

    def column(self, name: str) -> ColumnMetadata | None:
        return next((c for c in self.columns if c.name == name), None)

    def column_with(self, semantic_type: SemanticType) -> ColumnMetadata | None:
        return next((c for c in self.columns if c.semantic_type == semantic_type), None)

    def join_key(self, field: str) -> JoinKey | None:
        return next((k for k in self.join_keys if k.field == field), None)

    @property
    def geometry_column(self) -> ColumnMetadata | None:
        return next((c for c in self.columns if c.data_type == DataType.GEOMETRY), None)

    @property
    def is_polygon(self) -> bool:
        return self.spatial.geometry_type in (GeometryType.POLYGON, GeometryType.MULTIPOLYGON)
