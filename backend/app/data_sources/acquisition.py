"""등록 출처 수집: 등록 파일로 승인한 외부 출처를 필요할 때 받아 적재한다 (전달 문서 36번의 첫 단계).

흐름: 원본 받기(또는 주기 안의 원본 사본) → 등록 파일의 컬럼 매핑 → 자동 적재기로 검증·적재
→ 카탈로그 메타데이터와 검색 임베딩 갱신. 컬럼 의미는 추론하지 않고 등록 파일 값을 쓴다.
"""

import csv
import json
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from app.catalog.catalog import sync_catalog_embeddings
from app.config import Settings
from app.data_sources.auto_csv import AutoCsvError, LoadedTable, load_table, profile_file
from app.data_sources.seoul_openapi import SeoulApiError, fetch_service
from app.data_sources.sources import SourceDeclaration, SourceError, load_sources
from app.domain.catalog import (
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
from app.llm.embeddings import Embedder
from app.repositories.catalog_repository import list_datasets, upsert_dataset

CONNECTOR_NAMES = {"seoul_openapi": "서울 열린데이터광장 OpenAPI"}
_GEOM_DESCRIPTION = "등록 파일의 위경도로 만든 위치 (대상 지역 밖 좌표는 제외)"

Fetch = Callable[[SourceDeclaration, int | None], list[dict[str, Any]]]  # (출처, 최대 행 수) → 원본 행


class AcquisitionError(RuntimeError):
    pass


def seoul_fetcher(api_key: str | None) -> Fetch:
    def fetch(source: SourceDeclaration, max_rows: int | None) -> list[dict[str, Any]]:
        if not api_key:
            raise AcquisitionError("SEOUL_OPEN_API_KEY가 설정되지 않았습니다 (.env)")
        return fetch_service(api_key, source.service, max_rows=max_rows)

    return fetch


def register_sources(conn: psycopg.Connection, sources: Mapping[str, SourceDeclaration]) -> list[str]:
    """등록 파일을 카탈로그에 '받기 전' 항목으로 올려 Dataset 검색에 걸리게 한다.

    새 출처와 등록 파일이 바뀐 출처만 쓴다. 바뀐 출처는 다음에 분석에 쓰일 때 다시 적재된다.
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
    """등록 출처를 받아 적재하고, 같은 출처를 두 요청이 동시에 받지 않게 한다."""

    def __init__(
        self,
        pool: ConnectionPool,
        sources: Mapping[str, SourceDeclaration],
        fetch: Fetch,
        raw_dir: Path,
        embedder: Embedder,
    ):
        self._pool = pool
        self.sources = dict(sources)
        self._fetch = fetch
        self._raw_dir = raw_dir / "sources"
        self._embedder = embedder

    @classmethod
    def from_settings(cls, settings: Settings, pool: ConnectionPool, embedder: Embedder) -> "DataAcquirer":
        key = settings.seoul_open_api_key.get_secret_value() if settings.seoul_open_api_key else None
        return cls(pool, load_sources(settings.sources_dir), seoul_fetcher(key), settings.raw_data_dir, embedder)

    def needs_acquisition(self, metadata: DatasetMetadata, today: date | None = None) -> bool:
        """아직 받지 않았거나 다시 받을 때가 된 등록 출처인지. 등록 파일이 없으면 받은 데이터를 그대로 쓴다."""
        if metadata.acquisition is None:
            return False
        source = self.sources.get(metadata.dataset_id)
        if source is None:
            return metadata.acquisition.fetched_at is None
        return metadata.acquisition.is_due(today or date.today(), source.fingerprint)

    def acquire(
        self, dataset_id: str, *, limit_rows: bool = True, refresh: bool = False
    ) -> tuple[DatasetMetadata, DataAcquisition]:
        """출처를 받아 적재한다. 주기 안의 원본 사본이 있으면 API를 부르지 않는다(refresh=True면 새로 받음)."""
        source = self.sources.get(dataset_id)
        if source is None:
            raise AcquisitionError(f"등록 파일이 없는 출처입니다: {dataset_id}")
        started = time.perf_counter()
        with self._pool.connection() as conn:
            with conn.transaction():
                # 같은 출처를 동시에 받지 않는다. 기다린 요청은 먼저 받은 원본 사본을 다시 쓴다.
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"acquire:{dataset_id}",))
                region = _region(conn, source.region)
                rows, fetched_at, from_cache = self._rows(source, limit_rows, refresh)
                loaded = self._load(conn, source, rows, region)
                metadata = _loaded_metadata(source, region, loaded, fetched_at)
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
        self, conn: psycopg.Connection, source: SourceDeclaration, rows: list[dict], region: RegionRef
    ) -> LoadedTable:
        if not rows:
            raise AcquisitionError(f"'{source.title}'에서 받은 행이 없습니다")
        missing = [column.source for column in source.columns if column.source not in rows[0]]
        if missing:
            raise AcquisitionError(f"'{source.title}' 원본에 등록 파일의 컬럼이 없습니다: {missing}")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{source.source_id}.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow([column.name for column in source.columns])
                writer.writerows([_cell(row.get(column.source)) for column in source.columns] for row in rows)
            try:
                loaded = load_table(
                    conn, profile_file(path), source.source_id, coordinates=source.coordinate_columns, region=region
                )
            except AutoCsvError as error:
                raise AcquisitionError(f"'{source.title}'을(를) 적재하지 못했습니다: {error}") from error
        if not loaded.located:
            raise AcquisitionError(f"'{source.title}'에 {region.name} 안의 위치가 있는 행이 없습니다")
        return loaded


def pending_metadata(source: SourceDeclaration, region: RegionRef) -> DatasetMetadata:
    """받기 전 카탈로그 항목. 검색 임베딩 문서(제목·설명·태그·범위·컬럼)는 적재 뒤와 같다."""
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
        notes=[f"아직 받지 않은 등록 출처: 분석에 쓰이면 {CONNECTOR_NAMES[source.connector]}에서 받아 적재한다"],
        fetched_at=None,
        rows=None,
    )


def _loaded_metadata(
    source: SourceDeclaration, region: RegionRef, loaded: LoadedTable, fetched_at: date
) -> DatasetMetadata:
    connector = CONNECTOR_NAMES[source.connector]
    return _metadata(
        source,
        region,
        column_types=dict(zip(loaded.profile.columns, loaded.profile.data_types, strict=True)),
        spatial={"bbox": loaded.bbox, "feature_count": loaded.located},
        reference_date=fetched_at,
        notes=[f"{connector}({source.service})에서 {fetched_at} 수집, {loaded.rows:,}행", *loaded.notes],
        fetched_at=fetched_at,
        rows=loaded.rows,
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
) -> DatasetMetadata:
    columns = [
        ColumnMetadata(
            name=column.name,
            description=column.description,
            data_type=column_types.get(column.name, DataType.STRING),
            semantic_type=column.semantic,
            nullable=True,
        )
        for column in source.columns
    ]
    columns.append(
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
        description=source.description,
        category=PUBLIC_API_CATEGORY,
        semantic_tags=source.semantic_tags,
        provider=source.provider,
        source=source.source,
        license=source.license,
        spatial=SpatialMetadata(
            level=None, geometry_type=GeometryType.POINT, crs="EPSG:4326", coverage=region, **spatial
        ),
        temporal=TemporalMetadata(reference_date=reference_date, update_cycle=source.update_cycle),
        columns=columns,
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
    return "" if value is None else str(value)
