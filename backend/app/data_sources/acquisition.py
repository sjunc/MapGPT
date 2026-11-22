"""등록 출처 수집: 등록 파일로 승인한 외부 출처와 자동 후보를 필요할 때 받아 적재한다 (전달 문서 36번의 첫 단계).

흐름: 원본 받기(또는 주기 안의 원본 사본) → 등록 내용의 컬럼 매핑 → 자동 적재기로 검증·적재
→ 카탈로그 메타데이터와 검색 임베딩 갱신. 컬럼 의미는 추론하지 않고 등록 내용의 값을 쓴다.

자동 후보(포털 정보로 만든 등록 내용, reviewed=False)는 사람 대신 코드가 확인한다. 좌표가 위경도인지 미터 좌표인지
받은 값으로 판정하고, 미터 좌표의 좌표계는 계열 규칙이나 상세 화면 안내로 정한다. 위치가 대상 지역 안에 있는 행이
기준보다 적으면 쓰지 않는다.
"""

import csv
import json
import math
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg_pool import ConnectionPool

from app.catalog.catalog import sync_catalog_embeddings
from app.config import Settings
from app.data_sources.auto_csv import AutoCsvError, LoadedTable, load_table, profile_file
from app.data_sources.seoul_openapi import SeoulApiError, fetch_service
from app.data_sources.seoul_portal import PortalPage, fetch_portal_page
from app.data_sources.sources import SourceColumn, SourceDeclaration, SourceError, load_sources
from app.domain.catalog import (
    AUTO_PUBLIC_API_CATEGORY,
    PUBLIC_API_CATEGORY,
    ColumnMetadata,
    DatasetMetadata,
    RegionRef,
    SourceAcquisition,
    SpatialMetadata,
    TemporalMetadata,
)
from app.domain.enums import DataType, GeometryType, SemanticType, SpatialLevel
from app.domain.state import DataAcquisition
from app.gis.crs import CrsError, parse_source_srid
from app.llm.embeddings import Embedder
from app.repositories.catalog_repository import find_dataset, list_datasets, upsert_dataset

CONNECTOR_NAMES = {"seoul_openapi": "서울 열린데이터광장 OpenAPI"}
_GEOM_DESCRIPTION = "등록 내용의 좌표로 만든 위치 (EPSG:4326, 대상 지역 밖 좌표는 제외)"
_LATITUDE_RANGE = (32, 40)  # 한국 위경도 범위 (자동 적재기와 같다)
_LONGITUDE_RANGE = (123, 133)
_JUDGE_RATIO = 0.9  # 좌표 모양을 판정할 때 그 모양이어야 하는 값의 비율

Fetch = Callable[[SourceDeclaration, int | None], list[dict[str, Any]]]  # (출처, 최대 행 수) → 원본 행
ReadPage = Callable[[str], PortalPage | None]  # 상세 화면 주소 → 설명·좌표계 안내·이용 조건


class AcquisitionError(RuntimeError):
    pass


class AutoSourceRejected(AcquisitionError):
    """자동 후보가 코드 검사(좌표 모양·좌표계·지역 안 비율·걸러 낸 행)를 통과하지 못했다."""


def seoul_fetcher(api_key: str | None) -> Fetch:
    def fetch(source: SourceDeclaration, max_rows: int | None) -> list[dict[str, Any]]:
        if not api_key:
            raise AcquisitionError("SEOUL_OPEN_API_KEY가 설정되지 않았습니다 (.env)")
        return fetch_service(api_key, source.service, max_rows=max_rows)

    return fetch


def register_sources(conn: psycopg.Connection, sources: Mapping[str, SourceDeclaration]) -> list[str]:
    """등록 내용(등록 파일 또는 자동 후보)을 카탈로그에 '받기 전' 항목으로 올려 Dataset 검색에 걸리게 한다.

    새 출처와 등록 내용이 바뀐 출처만 쓴다. 바뀐 출처는 다음에 분석에 쓰일 때 다시 적재된다.
    """
    registered = []
    with conn.transaction():
        existing = {dataset.dataset_id: dataset for dataset in list_datasets(conn)}
        for source in sources.values():
            current = existing.get(source.source_id)
            if current is not None and current.acquisition is None:
                raise SourceError(f"이미 다른 Dataset이 쓰는 ID입니다: {source.source_id}")
            if current is not None and current.acquisition.fingerprint == source.fingerprint:
                continue
            upsert_dataset(conn, pending_metadata(source, _region(conn, source.region)))
            registered.append(source.source_id)
    return registered


class DataAcquirer:
    """등록 출처와 자동 후보를 받아 적재하고, 같은 출처를 두 요청이 동시에 받지 않게 한다."""

    def __init__(
        self,
        pool: ConnectionPool,
        sources: Mapping[str, SourceDeclaration],
        fetch: Fetch,
        raw_dir: Path,
        embedder: Embedder,
        *,
        read_page: ReadPage | None = None,
        min_located_ratio: float = 0.9,
    ):
        self._pool = pool
        self.sources = dict(sources)
        self._fetch = fetch
        self._raw_dir = raw_dir / "sources"
        self._embedder = embedder
        self._read_page = read_page  # 자동 후보를 받을 때 상세 화면을 읽는다. None이면 읽지 않는다
        self._min_located_ratio = min_located_ratio  # 자동 후보: 위치가 대상 지역 안에 있어야 하는 행의 비율

    @classmethod
    def from_settings(cls, settings: Settings, pool: ConnectionPool, embedder: Embedder) -> "DataAcquirer":
        key = settings.seoul_open_api_key.get_secret_value() if settings.seoul_open_api_key else None
        return cls(
            pool,
            load_sources(settings.sources_dir),
            seoul_fetcher(key),
            settings.raw_data_dir,
            embedder,
            read_page=fetch_portal_page,
            min_located_ratio=settings.auto_source_min_located_ratio,
        )

    def needs_acquisition(self, metadata: DatasetMetadata, today: date | None = None) -> bool:
        """아직 받지 않았거나 다시 받을 때가 된 출처인지. 등록 내용이 없으면 받은 데이터를 그대로 쓴다."""
        if metadata.acquisition is None:
            return False
        source = self.sources.get(metadata.dataset_id) or declared_source(metadata)
        if source is None:
            return metadata.acquisition.fetched_at is None
        return metadata.acquisition.is_due(today or date.today(), source.fingerprint)

    def acquire(
        self, dataset_id: str, *, limit_rows: bool = True, refresh: bool = False
    ) -> tuple[DatasetMetadata, DataAcquisition]:
        """출처를 받아 적재한다. 주기 안의 원본 사본이 있으면 API를 부르지 않는다(refresh=True면 새로 받음)."""
        started = time.perf_counter()
        with self._pool.connection() as conn:
            with conn.transaction():
                # 같은 출처를 동시에 받지 않는다. 기다린 요청은 먼저 받은 원본 사본을 다시 쓴다.
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"acquire:{dataset_id}",))
                # 등록 파일이 없으면 카탈로그에 저장한 자동 후보의 등록 내용을 쓴다.
                source = self.sources.get(dataset_id) or declared_source(find_dataset(conn, dataset_id))
                if source is None:
                    raise AcquisitionError(f"등록 파일이 없는 출처입니다: {dataset_id}")
                region = _region(conn, source.region)
                rows, fetched_at, from_cache = self._rows(source, limit_rows, refresh)
                page = self._read_page(source.source) if self._read_page and not source.reviewed else None
                loaded, columns = self._load(conn, source, rows, region, page)
                metadata = _loaded_metadata(source, region, loaded, fetched_at, page=page, columns=columns)
                upsert_dataset(conn, metadata)
            sync_catalog_embeddings(conn, self._embedder)
        record = DataAcquisition(
            dataset_id=dataset_id,
            connector=source.connector,
            service=source.service,
            rows=loaded.rows,
            located=loaded.located or 0,
            fetched_at=fetched_at,
            from_cache=from_cache,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return metadata, record

    def _rows(self, source: SourceDeclaration, limit_rows: bool, refresh: bool) -> tuple[list[dict], date, bool]:
        cached = None if refresh else self._fresh_copy(source)
        if cached is not None:
            path, fetched_at = cached
            return json.loads(path.read_text(encoding="utf-8")), fetched_at, True
        try:
            rows = self._fetch(source, source.max_rows if limit_rows else None)
        except (SeoulApiError, OSError, ValueError) as error:
            raise AcquisitionError(
                f"'{source.title}'을(를) 받지 못했습니다: {error}. "
                f"행이 많으면 python -m app.cli fetch-source {source.source_id} 로 미리 받아 두세요"
            ) from error
        fetched_at = date.today()
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        path = self._raw_dir / f"{source.source_id}_{source.service}_{fetched_at:%Y%m%d}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows, fetched_at, False

    def _fresh_copy(self, source: SourceDeclaration) -> tuple[Path, date] | None:
        """다시 받을 주기 안에 받아 둔 원본 사본 중 가장 최근 것."""
        copies = []
        for path in self._raw_dir.glob(f"{source.source_id}_{source.service}_*.json"):
            try:
                fetched_at = date(*map(int, (path.stem[-8:-4], path.stem[-4:-2], path.stem[-2:])))
            except ValueError:
                continue
            if (date.today() - fetched_at).days < source.refresh_days:
                copies.append((fetched_at, path))
        if not copies:
            return None
        fetched_at, path = max(copies)
        return path, fetched_at

    def _load(
        self,
        conn: psycopg.Connection,
        source: SourceDeclaration,
        rows: list[dict],
        region: RegionRef,
        page: PortalPage | None,
    ) -> tuple[LoadedTable, list[SourceColumn]]:
        """적재한 결과와 실제로 쓴 컬럼 매핑(자동 후보는 위도·경도가 뒤바뀌었으면 바꾼 것)을 돌려준다."""
        if not rows:
            raise AcquisitionError(f"'{source.title}'에서 받은 행이 없습니다")
        missing = [column.source for column in source.columns if column.source not in rows[0]]
        if missing:
            raise AcquisitionError(f"'{source.title}' 원본에 등록 파일의 컬럼이 없습니다: {missing}")
        notes = []
        if source.keep_rows:
            rule = source.keep_rows
            kept = [row for row in rows if _cell(row.get(rule.column)) in rule.values]
            notes.append(f"'{rule.column}'이 {', '.join(rule.values)}인 행만 적재 ({len(rows) - len(kept):,}행 제외)")
            if not kept:
                error = AcquisitionError if source.reviewed else AutoSourceRejected
                raise error(f"'{source.title}'에 적재할 행이 없습니다 ({notes[-1]})")
            rows = kept
        columns, crs, note = _coordinate_system(source, rows, page)
        if note:
            notes.append(note)
        coordinates = (
            next(column.name for column in columns if column.semantic == SemanticType.LATITUDE),
            next(column.name for column in columns if column.semantic == SemanticType.LONGITUDE),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{source.source_id}.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow([column.name for column in columns])
                writer.writerows([_cell(row.get(column.source)) for column in columns] for row in rows)
            try:
                loaded = load_table(
                    conn,
                    profile_file(path),
                    source.source_id,
                    coordinates=coordinates,
                    region=region,
                    srid=parse_source_srid(crs),
                )
            except (AutoCsvError, CrsError) as error:
                raise AcquisitionError(f"'{source.title}'을(를) 적재하지 못했습니다: {error}") from error
        if not loaded.located:
            raise AcquisitionError(f"'{source.title}'에 {region.name} 안의 위치가 있는 행이 없습니다")
        if not source.reviewed and loaded.located < loaded.rows * self._min_located_ratio:
            raise AutoSourceRejected(
                f"'{source.title}'에서 위치가 {region.name} 안에 있는 행이 {loaded.located:,}/{loaded.rows:,}행"
                f"({loaded.located / loaded.rows:.0%})뿐이라 쓰지 않습니다 "
                f"(자동 후보 기준 {self._min_located_ratio:.0%})"
            )
        return replace(loaded, notes=[*notes, *loaded.notes]), columns


def pending_metadata(source: SourceDeclaration, region: RegionRef) -> DatasetMetadata:
    """받기 전 카탈로그 항목. 검색 임베딩 문서(제목·설명·태그·범위·컬럼)는 적재 뒤와 같다."""
    connector = CONNECTOR_NAMES[source.connector]
    kind = "등록 출처" if source.reviewed else "자동 후보(포털 정보로 자동 등록, 사람 검토 전)"
    note = f"아직 받지 않은 {kind}: 분석에 쓰이면 {connector}에서 받아 적재한다"
    return _metadata(
        source,
        region,
        column_types={
            column.name: DataType.FLOAT
            for column in source.columns
            if column.semantic in (SemanticType.LATITUDE, SemanticType.LONGITUDE)
        },
        spatial={},
        reference_date=None,
        notes=[note],
        fetched_at=None,
        rows=None,
    )


def declared_source(metadata: DatasetMetadata | None) -> SourceDeclaration | None:
    """카탈로그에 저장한 자동 후보의 등록 내용. 등록 파일이 있는 출처나 다른 Dataset이면 None이다."""
    if metadata is None or metadata.acquisition is None or metadata.acquisition.declaration is None:
        return None
    return SourceDeclaration.model_validate(metadata.acquisition.declaration)


def judge_coordinates(rows: list[dict], latitude: str, longitude: str) -> str:
    """위도·경도 자리 값의 모양: wgs84(위경도), swapped(위도·경도가 뒤바뀜), plane(미터 단위 평면좌표), unknown.

    (0, 0)처럼 비어 있는 좌표는 세지 않는다. 행의 90% 이상이 같은 모양이어야 그 모양으로 본다.
    """
    pairs = []
    for row in rows:
        try:
            y, x = float(_cell(row.get(latitude))), float(_cell(row.get(longitude)))
        except ValueError:
            continue
        if math.isfinite(x) and math.isfinite(y) and (x, y) != (0, 0):
            pairs.append((y, x))
    if not pairs:
        return "unknown"

    def share(test: Callable[[float, float], bool]) -> float:
        return sum(test(y, x) for y, x in pairs) / len(pairs)

    def inside(value: float, bounds: tuple[float, float]) -> bool:
        return bounds[0] <= value <= bounds[1]

    if share(lambda y, x: inside(y, _LATITUDE_RANGE) and inside(x, _LONGITUDE_RANGE)) >= _JUDGE_RATIO:
        return "wgs84"
    if share(lambda y, x: inside(x, _LATITUDE_RANGE) and inside(y, _LONGITUDE_RANGE)) >= _JUDGE_RATIO:
        return "swapped"
    if share(lambda y, x: 1e4 <= abs(x) <= 1e7 and 1e4 <= abs(y) <= 1e7) >= _JUDGE_RATIO:
        return "plane"
    return "unknown"


def _coordinate_system(
    source: SourceDeclaration, rows: list[dict], page: PortalPage | None
) -> tuple[list[SourceColumn], str, str | None]:
    """쓸 컬럼 매핑, 좌표계, 품질 노트. 등록 파일은 등록한 좌표계를 따르고, 자동 후보는 값과 규칙·안내로 정한다."""
    if source.reviewed:
        return source.columns, source.crs or "EPSG:4326", None
    by_semantic = {column.semantic: column for column in source.columns}
    latitude, longitude = by_semantic[SemanticType.LATITUDE], by_semantic[SemanticType.LONGITUDE]
    kind = judge_coordinates(rows, latitude.source, longitude.source)
    if kind in ("wgs84", "swapped"):
        if source.crs not in (None, "EPSG:4326"):
            raise AutoSourceRejected(
                f"'{source.title}': 계열 규칙의 좌표계는 {source.crs}인데 받은 좌표는 위경도입니다"
            )
        if kind == "wgs84":
            return source.columns, "EPSG:4326", "좌표계: 받은 값으로 판정한 위경도(EPSG:4326)"
        swapped = {SemanticType.LATITUDE: SemanticType.LONGITUDE, SemanticType.LONGITUDE: SemanticType.LATITUDE}
        columns = [
            column.model_copy(update={"semantic": swapped.get(column.semantic, column.semantic)})
            for column in source.columns
        ]
        note = f"좌표계: 받은 값으로 판정한 위경도(EPSG:4326). '{latitude.source}'·'{longitude.source}'가 뒤바뀌어 바꿔 적용"
        return columns, "EPSG:4326", note
    if kind == "plane":
        crs = source.crs or (page.crs if page else None)
        if crs is None or crs == "EPSG:4326":
            raise AutoSourceRejected(
                f"'{source.title}'의 좌표가 미터 단위인데 좌표계를 확인할 수 없어 쓰지 않습니다 (계열 규칙·상세 화면 안내 없음)"
            )
        return source.columns, crs, f"좌표계: {crs} ({'계열 규칙' if source.crs else '상세 화면 안내'})"
    raise AutoSourceRejected(f"'{source.title}'의 좌표 컬럼 값이 위경도나 미터 단위 좌표로 보이지 않아 쓰지 않습니다")


def promote_source(conn: psycopg.Connection, dataset_id: str, sources_dir: Path) -> Path:
    """자동 후보를 등록 파일로 내보낸다. 사람이 확인해 커밋하고 register-sources를 돌리면 검토된 출처가 된다."""
    metadata = find_dataset(conn, dataset_id)
    source = declared_source(metadata)
    if source is None:
        raise SourceError(f"자동 후보가 아닙니다: {dataset_id}")
    path = sources_dir / f"{dataset_id}.yaml"
    if path.exists():
        raise SourceError(f"등록 파일이 이미 있습니다: {path}")
    data = source.model_dump(mode="json", exclude_defaults=True)
    data.pop("reviewed", None)  # 등록 파일은 검토한 출처다 (기본값 True)
    if metadata.license and not data.get("license"):
        data["license"] = metadata.license  # 받을 때 상세 화면에서 읽은 이용 조건
    if metadata.temporal.update_cycle and not data.get("update_cycle"):
        data["update_cycle"] = metadata.temporal.update_cycle
    header = (
        f"# {source.title}: 자동 후보에서 내보낸 등록 파일 ({date.today()}).\n"
        "# 컬럼 이름·의미, 좌표계, 걸러 낼 행을 원본 화면과 대조해 고친 뒤 커밋하고 register-sources를 실행한다.\n"
    )
    sources_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _loaded_metadata(
    source: SourceDeclaration,
    region: RegionRef,
    loaded: LoadedTable,
    fetched_at: date,
    *,
    page: PortalPage | None = None,
    columns: list[SourceColumn] | None = None,
) -> DatasetMetadata:
    connector = CONNECTOR_NAMES[source.connector]
    notes = [f"{connector}({source.service})에서 {fetched_at} 수집, {loaded.rows:,}행", *loaded.notes]
    return _metadata(
        source,
        region,
        column_types=dict(zip(loaded.profile.columns, loaded.profile.data_types, strict=True)),
        spatial={"bbox": loaded.bbox, "feature_count": loaded.located},
        reference_date=fetched_at,
        notes=notes,
        fetched_at=fetched_at,
        rows=loaded.rows,
        page=page,
        columns=columns,
    )


def _metadata(
    source: SourceDeclaration,
    region: RegionRef,
    *,
    column_types: dict[str, DataType],
    spatial: dict[str, Any],
    reference_date: date | None,
    notes: list[str],
    fetched_at: date | None,
    rows: int | None,
    page: PortalPage | None = None,
    columns: list[SourceColumn] | None = None,
) -> DatasetMetadata:
    """카탈로그 항목. 지문과 등록 내용은 원래 등록 내용으로 남기고, 컬럼 의미는 실제로 쓴 매핑(columns)을 따른다.

    자동 후보는 받을 때 읽은 상세 화면의 설명·키워드·이용 조건·갱신주기를 함께 남긴다.
    """
    description, tags = source.description, list(source.semantic_tags)
    if page and page.description:
        description = f"{description}. 포털 설명: {page.description}"[:1500]
        tags = list(dict.fromkeys([*tags, *page.keywords]))
    mapping = columns or source.columns
    metadata_columns = [
        ColumnMetadata(
            name=column.name,
            description=column.description,
            data_type=column_types.get(column.name, DataType.STRING),
            semantic_type=column.semantic,
            nullable=True,
        )
        for column in mapping
    ]
    metadata_columns.append(
        ColumnMetadata(
            name="geom",
            description=_GEOM_DESCRIPTION,
            data_type=DataType.GEOMETRY,
            semantic_type=SemanticType.GEOMETRY,
            nullable=True,
        )
    )
    return DatasetMetadata(
        dataset_id=source.source_id,
        title=source.title,
        description=description,
        category=PUBLIC_API_CATEGORY if source.reviewed else AUTO_PUBLIC_API_CATEGORY,
        semantic_tags=tags,
        provider=source.provider,
        source=source.source,
        license=source.license or (page.license if page else None),
        spatial=SpatialMetadata(
            level=None, geometry_type=GeometryType.POINT, crs="EPSG:4326", coverage=region, **spatial
        ),
        temporal=TemporalMetadata(
            reference_date=reference_date, update_cycle=source.update_cycle or (page.update_cycle if page else None)
        ),
        columns=metadata_columns,
        join_keys=[],
        quality_notes=notes,
        acquisition=SourceAcquisition(
            source_id=source.source_id,
            connector=source.connector,
            service=source.service,
            fingerprint=source.fingerprint,
            refresh_days=source.refresh_days,
            fetched_at=fetched_at,
            rows=rows,
            declaration=None if source.reviewed else source.model_dump(mode="json"),
        ),
    )


def _region(conn: psycopg.Connection, name: str) -> RegionRef:
    row = conn.execute("SELECT sido_cd FROM datasets.sido_boundary WHERE unit_nm = %s", (name,)).fetchone()
    if row is None:
        raise AcquisitionError(
            f"대상 지역 '{name}'을(를) 전국 경계에서 찾을 수 없습니다. 먼저 python -m app.cli ingest emd_boundary"
        )
    return RegionRef(name=name, level=SpatialLevel.SIDO, code=row[0])


def _cell(value: Any) -> str:
    # 원본 값 앞뒤 공백은 뺀다 (인허가 데이터의 좌표는 '196549.89     '처럼 공백이 붙어 온다).
    return "" if value is None else str(value).strip()
