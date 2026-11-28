"""GIS Tools: 검증된 Operation을 PostGIS(parameterized SQL)와 결정론적 코드로 실행한다.

데이터는 PostGIS에 두고, Tool 사이에는 View(메타데이터 + 필터) 참조만 주고받는다.
"""

import math
from collections import Counter
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql

from app.domain.catalog import DatasetMetadata, RegionRef, rollup_prefix_length
from app.domain.enums import (
    AggregationFunction,
    DataType,
    FilterOperator,
    GeometryType,
    MapType,
    SemanticType,
    SortOrder,
    SpatialLevel,
)
from app.domain.filters import FilterCondition, FilterExpression, describe_filter, iter_conditions
from app.domain.results import (
    BivariateAxis,
    BivariateSpec,
    Classification,
    HexLayer,
    MapLayer,
    MapStyle,
    MetricValue,
    PointLayer,
    UnitRow,
)
from app.gis.crs import AREA_SRID, MAP_OUTPUT_SRID, CrsError, parse_srid, transform_crs
from app.gis.filter_sql import FilterCompileError, compile_filter

QUANTILE_CLASSES = 5
POINT_LIMIT = 10_000  # 점 지도로 보낼 최대 점 수. 넘으면 응답 크기·화면 성능 때문에 위치를 보내지 않는다
HEX_TARGET_CELLS = 1_000  # 분석 지역 범위(사각형)에 들어갈 육각형 칸 수의 목표. 칸 크기를 이것으로 정한다
_HEX_AREA_FACTOR = 3 * math.sqrt(3) / 2  # 한 변이 s인 정육각형의 넓이 = 이 값 × s²
_VALUE_EXAMPLES = 20  # 조건에 맞는 데이터가 없을 때 보여 줄 실제 값 수
_REGION_CODE_COLUMN = {SpatialLevel.SIDO: SemanticType.SIDO_CODE, SpatialLevel.SIGUNGU: SemanticType.SIGUNGU_CODE}
_SQL_AGGREGATES = {
    AggregationFunction.COUNT: "count",
    AggregationFunction.SUM: "sum",
    AggregationFunction.AVG: "avg",
}


class GisToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DatasetView:
    metadata: DatasetMetadata
    filter: FilterExpression | None
    row_count: int

    @property
    def table(self) -> sql.Identifier:
        return sql.Identifier("datasets", self.metadata.dataset_id)

    def where(self) -> tuple[sql.Composable, list[Any]]:
        if self.filter is None:
            return sql.SQL("TRUE"), []
        try:
            return compile_filter(self.filter, self.metadata)
        except FilterCompileError as error:
            raise GisToolError("FILTER_INVALID", str(error)) from error


@dataclass(frozen=True)
class Unit:
    key: str
    name: str
    parent_name: str | None


@dataclass(frozen=True)
class BoundaryView(DatasetView):
    region: RegionRef
    key_field: str
    srid: int
    units: tuple[Unit, ...]
    invalid_geometry_count: int
    empty_geometry_count: int
    bbox: list[float] | None  # EPSG:4326

    @property
    def unit_keys(self) -> list[str]:
        return [unit.key for unit in self.units]


@dataclass
class MetricValues:
    name: str
    values: dict[str, MetricValue]
    null_reasons: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int | float] = field(default_factory=dict)


@dataclass(frozen=True)
class GeometryReport:
    invalid: int
    empty: int


def get_dataset(conn: psycopg.Connection, metadata: DatasetMetadata) -> DatasetView:
    """카탈로그에 등록된 내부 표준 Dataset 테이블 참조를 얻는다."""
    exists = conn.execute("SELECT to_regclass(%s)", (f"datasets.{metadata.dataset_id}",)).fetchone()[0]
    if exists is None:
        raise GisToolError(
            "DATASET_TABLE_MISSING", f"'{metadata.dataset_id}' 테이블이 없습니다. 적재(ingest)를 먼저 실행하세요"
        )
    view = DatasetView(metadata=metadata, filter=None, row_count=0)
    count = _count(conn, view)
    if count == 0:
        raise GisToolError("DATASET_EMPTY", f"'{metadata.dataset_id}' Dataset이 비어 있습니다")
    return replace(view, row_count=count)


def filter_dataset(conn: psycopg.Connection, view: DatasetView, expression: FilterExpression) -> DatasetView:
    """FilterExpression을 View에 적용한다 (데이터는 이동하지 않는다)."""
    filtered = replace(view, filter=expression)
    count = _count(conn, filtered)
    if count == 0:
        raise GisToolError("NO_MATCHING_DATA", _no_matching_data(conn, view, expression))
    return replace(filtered, row_count=count)


def _no_matching_data(conn: psycopg.Connection, view: DatasetView, expression: FilterExpression) -> str:
    """조건에 맞는 행이 없다는 안내. 조건이 틀렸는지 알 수 있게 문자열 컬럼의 실제 값을 함께 보여 준다."""
    message = f"'{describe_filter(expression)}' 조건에 맞는 '{view.metadata.title}' 데이터가 없습니다."
    for field_name in dict.fromkeys(condition.field for condition in iter_conditions(expression)):
        column = view.metadata.column(field_name)
        if column is None or column.data_type != DataType.STRING:
            continue
        rows = conn.execute(
            # DB 기본 정렬 규칙은 한글 순서가 뒤섞일 수 있어 바이트 순(한글 음절은 가나다순)으로 정렬한다.
            sql.SQL(
                'SELECT DISTINCT {column} COLLATE "C" FROM {table} WHERE {column} IS NOT NULL ORDER BY 1 LIMIT %s'
            ).format(column=sql.Identifier(field_name), table=view.table),
            (_VALUE_EXAMPLES + 1,),
        ).fetchall()
        values = [str(row[0]) for row in rows]
        if values:
            listed = ", ".join(values[:_VALUE_EXAMPLES]) + (" 등" if len(values) > _VALUE_EXAMPLES else "")
            message += f" '{field_name}'의 실제 값: {listed}"
    return message


def get_boundary(conn: psycopg.Connection, metadata: DatasetMetadata, region: RegionRef) -> BoundaryView:
    """분석 지역 안의 경계 Polygon 집합을 얻고 Geometry 유효성을 검증한다."""
    if not metadata.is_polygon:
        raise GisToolError("NOT_POLYGON", f"'{metadata.dataset_id}'는 Polygon Dataset이 아닙니다")
    srid = _srid(metadata)
    name_column = metadata.column_with(SemanticType.ADMINISTRATIVE_NAME)
    key_field = metadata.join_keys[0].field if metadata.join_keys else None
    # 중구·동구처럼 이름이 같은 지역이 여러 시도에 있으므로, 이름이 아니라 확정된 행정코드로 거른다.
    if region.level == SpatialLevel.COUNTRY:
        region_column = None
    elif region.level == metadata.spatial.level:
        region_column = metadata.column(key_field) if key_field else None
    else:
        region_column = metadata.column_with(_REGION_CODE_COLUMN.get(region.level))
    if (
        key_field is None
        or name_column is None
        or (region.level != SpatialLevel.COUNTRY and region_column is None)
        or metadata.geometry_column is None
    ):
        raise GisToolError(
            "BOUNDARY_METADATA_INCOMPLETE", f"'{metadata.dataset_id}'에 단위 키·이름·지역 컬럼 정보가 없습니다"
        )
    parent_semantic = (
        SemanticType.SIGUNGU_NAME if metadata.spatial.level == SpatialLevel.EMD else SemanticType.SIDO_NAME
    )
    parent_column = metadata.column_with(parent_semantic)

    view = get_dataset(conn, metadata)
    if region_column is not None:
        view = replace(
            view,
            filter=FilterCondition(field=region_column.name, operator=FilterOperator.EQ, value=region.code),
        )
    where, params = view.where()
    key, geom = sql.Identifier(key_field), sql.Identifier(metadata.geometry_column.name)
    parent = sql.Identifier(parent_column.name) if parent_column else sql.NULL
    units = tuple(
        Unit(key=k, name=n, parent_name=p)
        for k, n, p in conn.execute(
            sql.SQL("SELECT {}::text, {}, {} FROM {} WHERE {} ORDER BY 1").format(
                key, sql.Identifier(name_column.name), parent, view.table, where
            ),
            params,
        ).fetchall()
    )
    if not units:
        raise GisToolError("REGION_NOT_FOUND", f"'{metadata.dataset_id}'에 '{region.name}' 경계가 없습니다")
    if len({u.key for u in units}) != len(units):
        raise GisToolError("DUPLICATE_UNIT_KEY", f"'{metadata.dataset_id}'에 중복된 단위 키가 있습니다")

    view = replace(view, row_count=len(units))
    report = validate_geometry(conn, view)
    if report.invalid or report.empty:
        raise GisToolError(
            "INVALID_GEOMETRY",
            f"'{metadata.dataset_id}' 경계에 유효하지 않은 Geometry {report.invalid}개, 빈 Geometry {report.empty}개가 있습니다",
        )
    extent = conn.execute(
        sql.SQL(
            "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM (SELECT ST_Extent({}) AS e FROM {} WHERE {}) t"
        ).format(transform_crs(geom, srid, MAP_OUTPUT_SRID), view.table, where),
        params,
    ).fetchone()
    return BoundaryView(
        metadata=metadata,
        filter=view.filter,
        row_count=len(units),
        region=region,
        key_field=key_field,
        srid=srid,
        units=units,
        invalid_geometry_count=report.invalid,
        empty_geometry_count=report.empty,
        bbox=list(extent),
    )


def validate_geometry(conn: psycopg.Connection, view: DatasetView) -> GeometryReport:
    geom = sql.Identifier(view.metadata.geometry_column.name)
    where, params = view.where()
    invalid, empty = conn.execute(
        sql.SQL(
            "SELECT count(*) FILTER (WHERE NOT ST_IsValid({g})), count(*) FILTER (WHERE ST_IsEmpty({g})) "
            "FROM {t} WHERE {w}"
        ).format(g=geom, t=view.table, w=where),
        params,
    ).fetchone()
    return GeometryReport(invalid=invalid, empty=empty)


def aggregate_by_area(
    conn: psycopg.Connection,
    view: DatasetView,
    boundary: BoundaryView,
    aggregation: str,
    name: str,
    field_name: str | None = None,
) -> MetricValues:
    """Point를 경계 Polygon 단위로 집계한다. 경계선 위 점은 단위 키가 작은 Polygon 하나에만 배정한다.

    CRS 정책: 위상 연산이므로 Point를 경계 Dataset의 CRS로 변환해서 비교한다.
    """
    if aggregation not in _SQL_AGGREGATES:
        raise GisToolError("UNSUPPORTED_AGGREGATION", f"aggregate_by_area는 '{aggregation}' 집계를 지원하지 않습니다")
    metadata = view.metadata
    if aggregation == AggregationFunction.COUNT:
        if field_name is not None:
            raise GisToolError("FIELD_UNEXPECTED", "count는 컬럼이 아니라 점의 개수를 셉니다")
        value = sql.NULL
        aggregate_sql = sql.SQL("count(a.point_id)")
    else:
        column = metadata.column(field_name) if field_name is not None else None
        if column is None:
            raise GisToolError("FIELD_UNKNOWN", f"'{metadata.dataset_id}'에 '{field_name}' 숫자 컬럼이 없습니다")
        if column.data_type not in (DataType.INTEGER, DataType.FLOAT):
            raise GisToolError("FIELD_NOT_NUMERIC", f"'{field_name}'은 숫자 컬럼이 아닙니다")
        value = sql.Identifier(column.name)
        aggregate_sql = sql.SQL("{}(a.value)").format(sql.SQL(_SQL_AGGREGATES[aggregation]))
    geometry_column = metadata.geometry_column
    if geometry_column is None:
        raise GisToolError("NO_GEOMETRY", f"'{metadata.dataset_id}'에 Geometry가 없습니다")
    if metadata.spatial.geometry_type != GeometryType.POINT:
        raise GisToolError("NOT_POINT", f"'{metadata.dataset_id}'는 Point Dataset이 아닙니다")
    point_geom = transform_crs(sql.Identifier(geometry_column.name), _srid(metadata), boundary.srid)

    boundary_where, boundary_params = boundary.where()
    point_where, point_params = view.where()
    query = sql.SQL(
        """
        WITH units AS NOT MATERIALIZED (
            SELECT {key}::text AS unit_key, {boundary_geom} AS geom FROM {boundary_table} WHERE {boundary_where}
        ),
        points AS NOT MATERIALIZED (
            SELECT ctid AS point_id, {point_geom} AS geom, {value} AS value FROM {point_table} WHERE {point_where}
        ),
        assigned AS (
            SELECT DISTINCT ON (p.point_id) p.point_id, p.value, u.unit_key
            FROM points p JOIN units u ON ST_Covers(u.geom, p.geom)
            ORDER BY p.point_id, u.unit_key
        )
        SELECT u.unit_key, {aggregate}, count(a.point_id)
        FROM units u LEFT JOIN assigned a USING (unit_key) GROUP BY u.unit_key
        """
    ).format(
        key=sql.Identifier(boundary.key_field),
        boundary_geom=sql.Identifier(boundary.metadata.geometry_column.name),
        boundary_table=boundary.table,
        boundary_where=boundary_where,
        point_geom=point_geom,
        point_table=view.table,
        point_where=point_where,
        value=value,
        aggregate=aggregate_sql,
    )
    rows = conn.execute(query, boundary_params + point_params).fetchall()
    values = {key: _number(value) for key, value, _ in rows}
    assigned = sum(count for _, _, count in rows)
    # 위치(geom)가 없는 행은 경계 밖 점이 아니다. 왜 위치가 없는지는 적재할 때 품질 노트로 남긴다.
    located = conn.execute(
        sql.SQL("SELECT count(*) FROM {} WHERE ({}) AND {} IS NOT NULL").format(
            view.table, point_where, sql.Identifier(geometry_column.name)
        ),
        point_params,
    ).fetchone()[0]
    return MetricValues(
        name=name,
        values=values,
        null_reasons={
            key: "no_matching_records" if count == 0 else "all_values_null"
            for key, value, count in rows
            if value is None
        },
        stats={
            "input_points": view.row_count,
            "points_without_location": view.row_count - located,
            "assigned_points": assigned,
            "unassigned_points": located - assigned,
        },
    )


def aggregate(
    conn: psycopg.Connection,
    view: DatasetView,
    group_by: list[str],
    field_name: str,
    function: AggregationFunction,
    name: str,
    boundary: BoundaryView | None = None,
) -> MetricValues:
    """속성 Dataset을 그룹 키로 집계한다."""
    metadata = view.metadata
    if len(group_by) != 1:
        raise GisToolError("MULTI_GROUP_UNSUPPORTED", "aggregate는 그룹 키 1개만 지원합니다")
    for column_name in (group_by[0], field_name):
        if metadata.column(column_name) is None:
            raise GisToolError("FIELD_UNKNOWN", f"'{metadata.dataset_id}'에 '{column_name}' 컬럼이 없습니다")
    if function not in _SQL_AGGREGATES:
        raise GisToolError("UNSUPPORTED_AGGREGATION", f"'{function}' 집계는 지원하지 않습니다")
    if function in (AggregationFunction.SUM, AggregationFunction.AVG) and metadata.column(field_name).data_type not in (
        DataType.INTEGER,
        DataType.FLOAT,
    ):
        raise GisToolError("FIELD_NOT_NUMERIC", f"'{field_name}'은 숫자 컬럼이 아니어서 합계를 낼 수 없습니다")

    where, params = view.where()
    group = sql.Identifier(group_by[0])
    if boundary is not None:
        length = rollup_prefix_length(metadata.join_key(group_by[0]), boundary.metadata.join_key(boundary.key_field))
        if length is not None:
            group = sql.SQL("left({}::text, {})").format(group, sql.Literal(length))
    query = sql.SQL("SELECT {g}::text, {f}({v}) FROM {t} WHERE {w} GROUP BY {g}").format(
        g=group, f=sql.SQL(_SQL_AGGREGATES[function]), v=sql.Identifier(field_name), t=view.table, w=where
    )
    values = {key: _number(value) for key, value in conn.execute(query, params).fetchall()}
    return MetricValues(name=name, values=values, stats={"input_rows": view.row_count, "group_count": len(values)})


def calculate_ratio(
    numerator: MetricValues,
    denominator: MetricValues,
    scale: float,
    unit_keys: list[str],
    name: str,
) -> MetricValues:
    """numerator / denominator × scale. 분모가 0이거나 없으면 0으로 바꾸지 않고 null과 사유를 남긴다."""
    values: dict[str, MetricValue] = {}
    reasons: dict[str, str] = {}
    for key in unit_keys:
        top, bottom = numerator.values.get(key), denominator.values.get(key)
        if bottom is None:
            reasons[key] = "denominator_missing"
        elif bottom == 0:
            reasons[key] = "denominator_zero"
        elif top is None:
            reasons[key] = "numerator_missing"
        else:
            value = top / bottom * scale
            if not math.isfinite(value):
                raise GisToolError("NON_FINITE_RESULT", f"{key}: 비율 계산 결과가 유한하지 않습니다")
            values[key] = value
            continue
        values[key] = None
    stats: dict[str, int | float] = {"computed": len(unit_keys) - len(reasons), **Counter(reasons.values())}
    return MetricValues(name=name, values=values, null_reasons=reasons, stats=stats)


def rank(metric: MetricValues, order: SortOrder, unit_keys: list[str], name: str) -> MetricValues:
    """표준 경쟁 순위(1,2,2,4). null 값은 순위에서 제외한다."""
    present = [(key, metric.values[key]) for key in unit_keys if metric.values.get(key) is not None]
    ascending = order == SortOrder.ASC
    present.sort(key=lambda item: (item[1] if ascending else -item[1], item[0]))

    values: dict[str, MetricValue] = {key: None for key in unit_keys}
    reasons = {key: "input_null" for key in unit_keys if metric.values.get(key) is None}
    previous_value, previous_rank = None, 0
    for position, (key, value) in enumerate(present, start=1):
        current = previous_rank if value == previous_value else position
        values[key] = current
        previous_value, previous_rank = value, current
    return MetricValues(
        name=name, values=values, null_reasons=reasons, stats={"ranked": len(present), "excluded_null": len(reasons)}
    )


def point_locations(
    conn: psycopg.Connection, view: DatasetView, boundary: BoundaryView, limit: int = POINT_LIMIT
) -> PointLayer | None:
    """aggregate_by_area가 센 Point를 EPSG:4326 GeoJSON으로 만든다 (점 지도·밀도 지도용).

    필터가 적용된 View를 그대로 쓰고, 분석 지역 밖의 점과 위치가 없는 행은 빠진다. 경계선 위 점은 aggregate_by_area처럼
    단위 키가 작은 단위 하나에만 배정한다. 점이 limit보다 많으면 None을 돌려준다 (지역별 지도만 보인다).
    """
    metadata = view.metadata
    geometry_column = metadata.geometry_column
    if geometry_column is None or metadata.spatial.geometry_type != GeometryType.POINT:
        raise GisToolError("NOT_POINT", f"'{metadata.dataset_id}'는 Point Dataset이 아닙니다")
    srid = _srid(metadata)
    geom = sql.Identifier(geometry_column.name)
    name_column = metadata.column_with(SemanticType.FACILITY_NAME)
    boundary_where, boundary_params = boundary.where()
    point_where, point_params = view.where()
    query = sql.SQL(
        """
        WITH units AS NOT MATERIALIZED (
            SELECT {key}::text AS unit_key, {boundary_geom} AS geom FROM {boundary_table} WHERE {boundary_where}
        ),
        points AS NOT MATERIALIZED (
            SELECT ctid AS point_id, {name} AS name, {overlay_geom} AS geom, {map_geom} AS map_geom
            FROM {point_table} WHERE {point_where}
        ),
        assigned AS (
            SELECT DISTINCT ON (p.point_id) p.point_id, p.name, p.map_geom, u.unit_key
            FROM points p JOIN units u ON ST_Covers(u.geom, p.geom)
            ORDER BY p.point_id, u.unit_key
        )
        SELECT name, unit_key, ST_X(map_geom), ST_Y(map_geom) FROM assigned ORDER BY unit_key, point_id LIMIT {limit}
        """
    ).format(
        key=sql.Identifier(boundary.key_field),
        boundary_geom=sql.Identifier(boundary.metadata.geometry_column.name),
        boundary_table=boundary.table,
        boundary_where=boundary_where,
        name=sql.SQL("{}::text").format(sql.Identifier(name_column.name)) if name_column else sql.NULL,
        overlay_geom=transform_crs(geom, srid, boundary.srid),
        map_geom=transform_crs(geom, srid, MAP_OUTPUT_SRID),
        point_table=view.table,
        point_where=point_where,
        limit=sql.Literal(limit + 1),
    )
    rows = conn.execute(query, boundary_params + point_params).fetchall()
    if len(rows) > limit:
        return None
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(x, 6), round(y, 6)]},
            "properties": {"name": name, "unit_key": unit_key},
        }
        for name, unit_key, x, y in rows
    ]
    return PointLayer(
        dataset_id=metadata.dataset_id,
        title=metadata.title,
        count=len(features),
        geojson={"type": "FeatureCollection", "features": features},
    )


def hex_bins(
    conn: psycopg.Connection, view: DatasetView, boundary: BoundaryView, target_cells: int = HEX_TARGET_CELLS
) -> HexLayer:
    """aggregate_by_area가 센 Point를 같은 크기 육각형 칸마다 센다 (EPSG:5179에서 만들고 EPSG:4326으로 낸다).

    칸 크기는 분석 지역 범위에 칸이 target_cells개쯤 들어가도록 50m 단위로 정한다(서울 650m, 구 하나 200m 안팎).
    분석 지역 밖·위치 없는 점은 빠지고, 칸 경계 위 점은 번호가 작은 칸 하나에만 센다. 점이 있는 칸만 보낸다.
    """
    metadata = view.metadata
    geometry_column = metadata.geometry_column
    if geometry_column is None or metadata.spatial.geometry_type != GeometryType.POINT:
        raise GisToolError("NOT_POINT", f"'{metadata.dataset_id}'는 Point Dataset이 아닙니다")
    srid = _srid(metadata)
    geom = sql.Identifier(geometry_column.name)
    boundary_geom = sql.Identifier(boundary.metadata.geometry_column.name)
    boundary_where, boundary_params = boundary.where()
    point_where, point_params = view.where()
    width, height = conn.execute(
        sql.SQL(
            "SELECT ST_XMax(e) - ST_XMin(e), ST_YMax(e) - ST_YMin(e) FROM (SELECT ST_Extent({}) AS e FROM {} WHERE {}) t"
        ).format(transform_crs(boundary_geom, boundary.srid, AREA_SRID), boundary.table, boundary_where),
        boundary_params,
    ).fetchone()
    size = max(100, round(math.sqrt(width * height / (_HEX_AREA_FACTOR * target_cells)) / 50) * 50)
    query = sql.SQL(
        """
        WITH units AS NOT MATERIALIZED (
            SELECT {boundary_geom} AS geom FROM {boundary_table} WHERE {boundary_where}
        ),
        points AS NOT MATERIALIZED (
            SELECT ctid AS point_id, {overlay_geom} AS geom, {grid_geom} AS grid_geom FROM {point_table} WHERE {point_where}
        ),
        inside AS (
            SELECT DISTINCT ON (p.point_id) p.point_id, p.grid_geom
            FROM points p JOIN units u ON ST_Covers(u.geom, p.geom)
            ORDER BY p.point_id
        ),
        cells AS (
            SELECT DISTINCT ON (p.point_id) p.point_id, h.i, h.j, h.geom
            FROM inside p CROSS JOIN LATERAL ST_HexagonGrid({size}, p.grid_geom) AS h
            WHERE ST_Intersects(h.geom, p.grid_geom)
            ORDER BY p.point_id, h.i, h.j
        )
        SELECT count(*), ST_AsGeoJSON({map_geom}, 6)::json FROM cells GROUP BY i, j ORDER BY i, j
        """
    ).format(
        boundary_geom=boundary_geom,
        boundary_table=boundary.table,
        boundary_where=boundary_where,
        overlay_geom=transform_crs(geom, srid, boundary.srid),
        grid_geom=transform_crs(geom, srid, AREA_SRID),
        point_table=view.table,
        point_where=point_where,
        size=sql.Literal(size),
        map_geom=transform_crs(sql.SQL("any_value(geom)"), AREA_SRID, MAP_OUTPUT_SRID),
    )
    rows = conn.execute(query, boundary_params + point_params).fetchall()
    counts = [count for count, _ in rows]
    breaks = quantile_breaks(counts, QUANTILE_CLASSES)
    return HexLayer(
        cell_size_m=size,
        cell_area_km2=round(_HEX_AREA_FACTOR * size**2 / 1e6, 3),
        count=sum(counts),
        classification=Classification(
            method="quantile",
            class_count=len(breaks) + 1 if counts else 0,
            breaks=breaks,
            min=min(counts, default=None),
            max=max(counts, default=None),
        ),
        geojson={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": geometry, "properties": {"count": count}} for count, geometry in rows
            ],
        },
    )


def bivariate_spec(rows: list[UnitRow], x: tuple[str, str], y: tuple[str, str]) -> BivariateSpec:
    """비율의 분모(x)·분자(y)를 3분위로 나눈다. 두 값이 모두 있는 단위만 쓴다 (그 단위만 이변량 색을 칠한다).

    x, y는 (operation output, 라벨)이다.
    """
    pairs = [
        (row.values[x[0]], row.values[y[0]])
        for row in rows
        if row.values.get(x[0]) is not None and row.values.get(y[0]) is not None
    ]
    return BivariateSpec(
        x=BivariateAxis(metric=x[0], label=x[1], breaks=quantile_breaks([a for a, _ in pairs], 3)),
        y=BivariateAxis(metric=y[0], label=y[1], breaks=quantile_breaks([b for _, b in pairs], 3)),
    )


def create_map_layer(
    conn: psycopg.Connection,
    boundary: BoundaryView,
    rows: list[UnitRow],
    metric: str,
    metric_label: str,
    style: MapStyle,
    points: PointLayer | None = None,
    hexbins: HexLayer | None = None,
    bivariate: BivariateSpec | None = None,
) -> MapLayer:
    """결과 행을 EPSG:4326 GeoJSON FeatureCollection과 분위 구간으로 만든다.

    단위마다 비례 원을 그릴 대표점(label_point)을 함께 담아, 화면이 같은 결과를 단계 구분도·비례 원으로 그린다.
    points는 지도 지표로 센 Point 위치(point_locations, 점 지도·밀도 지도용), hexbins는 그 육각 격자(hex_bins),
    bivariate는 비율 지표의 분자·분모 구간(bivariate_spec)이다.
    """
    bbox = boundary.bbox or []
    if len(bbox) != 4 or not (-180 <= bbox[0] <= bbox[2] <= 180 and -90 <= bbox[1] <= bbox[3] <= 90):
        raise GisToolError("OUTPUT_CRS_INVALID", f"지도 좌표가 경위도 범위를 벗어났습니다: {bbox}")
    where, params = boundary.where()
    geom = transform_crs(sql.Identifier(boundary.metadata.geometry_column.name), boundary.srid, MAP_OUTPUT_SRID)
    # ST_PointOnSurface: 오목한 지역에서도 Polygon 안에 있는 점 (무게중심은 밖에 있을 수 있다)
    shapes = {
        key: (geometry, [round(x, 6), round(y, 6)])
        for key, geometry, x, y in conn.execute(
            sql.SQL(
                "SELECT {key}::text, ST_AsGeoJSON(g, 6)::json, ST_X(ST_PointOnSurface(g)), ST_Y(ST_PointOnSurface(g)) "
                "FROM (SELECT {key}, {geom} AS g FROM {table} WHERE {where}) AS units"
            ).format(key=sql.Identifier(boundary.key_field), geom=geom, table=boundary.table, where=where),
            params,
        ).fetchall()
    }

    features = []
    for row in rows:
        if row.key not in shapes:
            raise GisToolError("GEOMETRY_MISSING", f"{row.key} ({row.name})의 Geometry가 없습니다")
        geometry, label_point = shapes[row.key]
        properties = {"unit_key": row.key, "unit_name": row.name, "parent_name": row.parent_name, **row.values}
        properties["null_reason"] = row.null_reasons.get(metric)
        properties["label_point"] = label_point
        features.append({"type": "Feature", "id": row.key, "geometry": geometry, "properties": properties})

    values = [row.values[metric] for row in rows if row.values.get(metric) is not None]
    breaks = quantile_breaks(values, QUANTILE_CLASSES)
    return MapLayer(
        type=MapType.CHOROPLETH,
        metric=metric,
        metric_label=metric_label,
        crs=f"EPSG:{MAP_OUTPUT_SRID}",
        classification=Classification(
            method="quantile",
            class_count=len(breaks) + 1 if values else 0,
            breaks=breaks,
            min=min(values, default=None),
            max=max(values, default=None),
        ),
        feature_count=len(features),
        null_count=len(rows) - len(values),
        geojson={"type": "FeatureCollection", "features": features},
        style=style,
        points=points,
        hexbins=hexbins,
        bivariate=bivariate,
    )


def quantile_breaks(values: list[float], classes: int) -> list[float]:
    """분위 경계값(최대 classes-1개). MapLibre step 식에서 '경계값 이상'이 다음 구간이 된다."""
    if not values:
        return []
    ordered = sorted(values)
    breaks: list[float] = []
    for i in range(1, classes):
        candidate = ordered[round(i * (len(ordered) - 1) / classes)]
        if candidate > ordered[0] and (not breaks or candidate > breaks[-1]):
            breaks.append(candidate)
    return breaks


def _count(conn: psycopg.Connection, view: DatasetView) -> int:
    where, params = view.where()
    return conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE {}").format(view.table, where), params).fetchone()[0]


def _srid(metadata: DatasetMetadata) -> int:
    try:
        return parse_srid(metadata.spatial.crs)
    except CrsError as error:
        raise GisToolError("CRS_UNSUPPORTED", f"'{metadata.dataset_id}': {error}") from error


def _number(value: Any) -> MetricValue:
    return float(value) if isinstance(value, Decimal) else value
