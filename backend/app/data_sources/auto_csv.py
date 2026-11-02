"""대용량 CSV 자동 발견·프로파일링·적재.

LLM에는 이 모듈이 만든 검증된 메타데이터만 제공한다. 좌표와 행정구역 코드는
컬럼명만 믿지 않고 표본 값과 전국 경계의 실제 코드 일치율로 판정한다.
"""

import csv
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql

from app.catalog.definitions import KOREA, MOIS_ADM_CD, MOIS_SIDO_CD, MOIS_SIGUNGU_CD
from app.domain.catalog import ColumnMetadata, DatasetMetadata, JoinKey, SpatialMetadata, TemporalMetadata
from app.domain.enums import DataType, GeometryType, SemanticType, SpatialLevel
from app.repositories.catalog_repository import upsert_dataset

SAMPLE_ROWS = 300
MIN_CODE_MATCH = 0.8
_KNOWN_CSV_PREFIXES = ("mois_population_by_age_",)


class AutoCsvError(RuntimeError):
    pass


@dataclass(frozen=True)
class CsvProfile:
    files: tuple[Path, ...]
    encoding: str
    columns: tuple[str, ...]
    samples: tuple[tuple[str, ...], ...]
    data_types: tuple[DataType, ...]

    @property
    def dataset_id(self) -> str:
        signature = "\x1f".join(self.columns).encode("utf-8")
        return f"auto_{hashlib.sha256(signature).hexdigest()[:12]}"


@dataclass(frozen=True)
class CodeMapping:
    source_column: str
    level: SpatialLevel
    code_system: str
    transform: str
    match_ratio: float

    def apply(self, value: str) -> str | None:
        raw = _code(value)
        if raw is None:
            return None
        if self.transform == "append_00":
            return raw + "00"
        if self.transform == "first_5":
            return raw[:5]
        if self.transform == "first_2":
            return raw[:2]
        return raw


def discover_csv_groups(raw_dir: Path) -> list[CsvProfile]:
    """같은 헤더를 가진 CSV partition을 하나의 논리 Dataset으로 묶는다."""
    groups: dict[tuple[str, ...], list[tuple[Path, str, list[tuple[str, ...]]]]] = {}
    for path in sorted(raw_dir.glob("*.csv")):
        if path.name.startswith(_KNOWN_CSV_PREFIXES):
            continue
        encoding, columns, rows = _sample(path)
        groups.setdefault(columns, []).append((path, encoding, rows))

    profiles = []
    for columns, parts in groups.items():
        encodings = {encoding for _, encoding, _ in parts}
        if len(encodings) != 1:
            names = ", ".join(path.name for path, _, _ in parts)
            raise AutoCsvError(f"같은 스키마 파일의 인코딩이 다릅니다: {names}")
        samples = [row for _, _, rows in parts for row in rows]
        profiles.append(
            CsvProfile(
                files=tuple(path for path, _, _ in parts),
                encoding=encodings.pop(),
                columns=columns,
                samples=tuple(samples[: SAMPLE_ROWS * 3]),
                data_types=tuple(_infer_type(name, [row[i] for row in samples]) for i, name in enumerate(columns)),
            )
        )
    return profiles


def ingest_discovered_csvs(conn: psycopg.Connection, raw_dir: Path) -> list[DatasetMetadata]:
    profiles = discover_csv_groups(raw_dir)
    if not profiles:
        raise AutoCsvError(f"자동 적재할 CSV가 없습니다: {raw_dir}")
    code_sets = _boundary_codes(conn)
    return [_ingest_profile(conn, profile, code_sets) for profile in profiles]


def _sample(path: Path) -> tuple[str, tuple[str, ...], list[tuple[str, ...]]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                reader = csv.reader(stream)
                columns = tuple(cell.strip() for cell in next(reader))
                if not columns or any(not column for column in columns) or len(set(columns)) != len(columns):
                    raise AutoCsvError(f"비어 있거나 중복된 CSV 헤더입니다: {path.name}")
                rows = []
                for row in reader:
                    if len(row) != len(columns):
                        raise AutoCsvError(f"헤더와 행의 컬럼 수가 다릅니다: {path.name}")
                    rows.append(tuple(row))
                    if len(rows) >= SAMPLE_ROWS:
                        break
                if not rows:
                    raise AutoCsvError(f"CSV에 데이터 행이 없습니다: {path.name}")
                return encoding, columns, rows
        except UnicodeDecodeError as error:
            last_error = error
    raise AutoCsvError(f"UTF-8/CP949로 읽을 수 없습니다: {path.name}: {last_error}")


def _infer_type(name: str, values: list[str]) -> DataType:
    present = [value.strip().replace(",", "") for value in values if value.strip()]
    normalized = re.sub(r"[\s_\-]", "", name).lower()
    if not present or any(token in normalized for token in ("코드", "번호", "우편", "id", "식별")):
        return DataType.STRING
    try:
        for value in present:
            int(value)
        return DataType.INTEGER
    except ValueError:
        pass
    try:
        for value in present:
            float(value)
        return DataType.FLOAT
    except ValueError:
        return DataType.STRING


def _boundary_codes(conn: psycopg.Connection) -> dict[SpatialLevel, set[str]]:
    if conn.execute("SELECT to_regclass('datasets.national_emd_boundary')").fetchone()[0] is None:
        raise AutoCsvError("전국 경계를 먼저 적재하세요: python -m app.cli ingest emd_boundary")
    return {
        SpatialLevel.EMD: {row[0] for row in conn.execute("SELECT adm_cd FROM datasets.national_emd_boundary")},
        SpatialLevel.SIGUNGU: {row[0] for row in conn.execute("SELECT sigungu_cd FROM datasets.sigungu_boundary")},
        SpatialLevel.SIDO: {row[0] for row in conn.execute("SELECT sido_cd FROM datasets.sido_boundary")},
    }


def _find_code_mapping(profile: CsvProfile, code_sets: dict[SpatialLevel, set[str]]) -> CodeMapping | None:
    candidates: list[CodeMapping] = []
    systems = {
        SpatialLevel.EMD: MOIS_ADM_CD,
        SpatialLevel.SIGUNGU: MOIS_SIGUNGU_CD,
        SpatialLevel.SIDO: MOIS_SIDO_CD,
    }
    for index, column in enumerate(profile.columns):
        if not any(token in column.lower() for token in ("행정", "시군구", "시도", "지역", "주소코드")):
            continue
        values = [_code(row[index]) for row in profile.samples]
        values = [value for value in values if value]
        if not values:
            continue
        for level, targets in code_sets.items():
            transforms = {"identity": lambda value: value}
            if level == SpatialLevel.EMD:
                transforms["append_00"] = lambda value: value + "00"
            if level == SpatialLevel.SIGUNGU:
                transforms["first_5"] = lambda value: value[:5]
            if level == SpatialLevel.SIDO:
                transforms["first_2"] = lambda value: value[:2]
            for transform_name, transform in transforms.items():
                ratio = sum(transform(value) in targets for value in values) / len(values)
                candidates.append(CodeMapping(column, level, systems[level], transform_name, ratio))
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item.match_ratio)
    return best if best.match_ratio >= MIN_CODE_MATCH else None


def _coordinate_columns(profile: CsvProfile) -> tuple[str, str] | None:
    latitude = longitude = None
    for index, column in enumerate(profile.columns):
        values = []
        try:
            values = [float(row[index]) for row in profile.samples if row[index].strip()]
        except ValueError:
            continue
        normalized = column.lower().replace("_", "")
        if values and all(32 <= value <= 40 for value in values) and normalized in {"위도", "latitude", "lat", "y"}:
            latitude = column
        if (
            values
            and all(123 <= value <= 133 for value in values)
            and normalized in {"경도", "longitude", "lon", "lng", "x"}
        ):
            longitude = column
    return (latitude, longitude) if latitude and longitude else None


def _ingest_profile(
    conn: psycopg.Connection, profile: CsvProfile, code_sets: dict[SpatialLevel, set[str]]
) -> DatasetMetadata:
    mapping = _find_code_mapping(profile, code_sets)
    coordinates = _coordinate_columns(profile)
    dataset_id = profile.dataset_id
    loading = f"{dataset_id}__loading"
    definitions = [
        sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(_postgres_type(data_type)))
        for name, data_type in zip(profile.columns, profile.data_types, strict=True)
    ]
    if mapping:
        definitions.append(sql.SQL("unit_key text"))

    with conn.transaction():
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier("datasets", loading)))
        conn.execute(
            sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier("datasets", loading), sql.SQL(", ").join(definitions))
        )
        copy_columns = [*profile.columns, *(("unit_key",) if mapping else ())]
        copy_query = sql.SQL("COPY {} ({}) FROM STDIN").format(
            sql.Identifier("datasets", loading), sql.SQL(", ").join(map(sql.Identifier, copy_columns))
        )
        with conn.cursor() as cursor, cursor.copy(copy_query) as copy:
            for path in profile.files:
                with path.open("r", encoding=profile.encoding, newline="") as stream:
                    reader = csv.reader(stream)
                    if tuple(next(reader)) != profile.columns:
                        raise AutoCsvError(f"프로파일 이후 헤더가 바뀌었습니다: {path.name}")
                    for row in reader:
                        if len(row) != len(profile.columns):
                            raise AutoCsvError(f"헤더와 행의 컬럼 수가 다릅니다: {path.name}")
                        converted = [
                            _convert(value, data_type) for value, data_type in zip(row, profile.data_types, strict=True)
                        ]
                        if mapping:
                            converted.append(mapping.apply(row[profile.columns.index(mapping.source_column)]))
                        copy.write_row(converted)
        if coordinates:
            latitude, longitude = map(sql.Identifier, coordinates)
            conn.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN geom geometry(Point, 4326)").format(
                    sql.Identifier("datasets", loading)
                )
            )
            conn.execute(
                sql.SQL(
                    "UPDATE {} SET geom = ST_SetSRID(ST_MakePoint({}, {}), 4326) WHERE {} IS NOT NULL AND {} IS NOT NULL"
                ).format(sql.Identifier("datasets", loading), longitude, latitude, longitude, latitude)
            )
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier("datasets", dataset_id)))
        conn.execute(
            sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                sql.Identifier("datasets", loading), sql.Identifier(dataset_id)
            )
        )
        if coordinates:
            conn.execute(
                sql.SQL("CREATE INDEX {} ON {} USING gist (geom)").format(
                    sql.Identifier(f"{dataset_id}_geom_idx"), sql.Identifier("datasets", dataset_id)
                )
            )
        if mapping:
            conn.execute(
                sql.SQL("CREATE INDEX {} ON {} (unit_key)").format(
                    sql.Identifier(f"{dataset_id}_unit_key_idx"), sql.Identifier("datasets", dataset_id)
                )
            )
        count = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("datasets", dataset_id))
        ).fetchone()[0]
        metadata = _metadata(profile, count, coordinates, mapping)
        upsert_dataset(conn, metadata)
    return metadata


def _metadata(
    profile: CsvProfile,
    count: int,
    coordinates: tuple[str, str] | None,
    mapping: CodeMapping | None,
) -> DatasetMetadata:
    common = os.path.commonprefix([path.stem for path in profile.files]).rstrip("_ -(")
    title = common or profile.files[0].stem
    columns = [
        ColumnMetadata(
            name=name,
            description=f"원본 컬럼 '{name}'",
            data_type=data_type,
            semantic_type=_semantic_type(name, data_type, coordinates),
            nullable=True,
        )
        for name, data_type in zip(profile.columns, profile.data_types, strict=True)
    ]
    join_keys = []
    if mapping:
        columns.append(
            ColumnMetadata(
                name="unit_key",
                description=f"전국 경계와 {mapping.match_ratio:.1%} 일치해 정규화한 {mapping.level} 코드",
                data_type=DataType.STRING,
                semantic_type=SemanticType.ADMINISTRATIVE_CODE,
                nullable=True,
            )
        )
        join_keys.append(
            JoinKey(field="unit_key", semantic_type=SemanticType.ADMINISTRATIVE_CODE, code_system=mapping.code_system)
        )
    if coordinates:
        columns.append(
            ColumnMetadata(
                name="geom",
                description="검증된 위경도로 만든 위치",
                data_type=DataType.GEOMETRY,
                semantic_type=SemanticType.GEOMETRY,
                nullable=True,
            )
        )
    notes = [
        f"CSV 자동 발견: {len(profile.files)}개 파일, {profile.encoding}, {count:,}행",
        "제공기관·라이선스·컬럼 의미는 파일 자체에서 확인할 수 없어 자동 추론 메타데이터로 등록",
    ]
    if mapping:
        notes.append(
            f"{mapping.source_column} → {mapping.level} ({mapping.transform}), 표본 일치율 {mapping.match_ratio:.1%}"
        )
    return DatasetMetadata(
        dataset_id=profile.dataset_id,
        title=title,
        description=f"{title} CSV를 자동 발견해 등록한 Dataset. 컬럼: {', '.join(profile.columns)}",
        category="auto_discovered",
        semantic_tags=[title, *profile.columns[:12]],
        provider="원본 CSV 파일명에서 확인 필요",
        source=(
            f"로컬 CSV {len(profile.files)}개: "
            + ", ".join(path.name for path in profile.files[:5])
            + (" 외" if len(profile.files) > 5 else "")
        ),
        license=None,
        spatial=SpatialMetadata(
            level=mapping.level if mapping else None,
            geometry_type=GeometryType.POINT if coordinates else None,
            crs="EPSG:4326" if coordinates else None,
            coverage=KOREA,
            feature_count=count if coordinates else None,
        ),
        temporal=TemporalMetadata(reference_date=_reference_date(profile.files), update_cycle=None),
        columns=columns,
        join_keys=join_keys,
        quality_notes=notes,
    )


def _semantic_type(name: str, data_type: DataType, coordinates: tuple[str, str] | None) -> SemanticType:
    if coordinates and name == coordinates[0]:
        return SemanticType.LATITUDE
    if coordinates and name == coordinates[1]:
        return SemanticType.LONGITUDE
    if any(token in name.lower() for token in ("날짜", "일자", "년월", "기준일", "date")):
        return SemanticType.DATE
    if any(token in name.lower() for token in ("코드", "번호", "id")):
        return SemanticType.IDENTIFIER
    if data_type in (DataType.INTEGER, DataType.FLOAT):
        return SemanticType.MEASURE
    if any(token in name for token in ("분류", "유형", "구분", "업종", "상태")):
        return SemanticType.CATEGORY
    if any(token in name for token in ("명", "이름", "상호")):
        return SemanticType.FACILITY_NAME
    return SemanticType.TEXT


def _reference_date(files: tuple[Path, ...]) -> date | None:
    matches = [re.search(r"(20\d{2})(\d{2})(\d{2})?", path.stem) for path in files]
    values = [match for match in matches if match]
    if not values:
        return None
    year, month, day = values[0].group(1), values[0].group(2), values[0].group(3)
    return date(int(year), int(month), int(day or 1))


def _code(value: str) -> str | None:
    raw = value.strip()
    if raw.endswith(".0"):
        raw = raw[:-2]
    return raw if re.fullmatch(r"\d{2,10}", raw) else None


def _postgres_type(data_type: DataType) -> str:
    return {DataType.INTEGER: "bigint", DataType.FLOAT: "double precision"}.get(data_type, "text")


def _convert(value: str, data_type: DataType):
    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace(",", "")
    if data_type == DataType.INTEGER:
        return int(normalized)
    if data_type == DataType.FLOAT:
        return float(normalized)
    return raw
