from datetime import date

import pytest
from psycopg import sql

from app.data_sources.auto_csv import SAMPLE_ROWS, ingest_discovered_csvs
from app.data_sources.ingest import IngestError, ingest_boundary, ingest_daycare, ingest_population
from app.domain.enums import DataType, SemanticType
from app.repositories.catalog_repository import list_datasets
from tests.support import FIXTURES, population_csv

pytestmark = pytest.mark.integration


def _row(stcode: str, la: float, lo: float) -> dict[str, str]:
    return {
        "STCODE": stcode,
        "CRNAME": f"시설{stcode}",
        "CRTYPENAME": "민간",
        "CRSTATUSNAME": "정상",
        "LA": str(la),
        "LO": str(lo),
        "DATASTDRDT": "2026-10-07",
    }


def test_boundary_ingest_loads_region_and_records_catalog_stats(conn, tmp_path):
    path = tmp_path / "HangJeongDong_ver20260701.geojson"
    path.write_bytes((FIXTURES / "admdongkor_sample.geojson").read_bytes())

    metadata = ingest_boundary(conn, path)

    assert conn.execute("SELECT array_agg(adm_cd ORDER BY adm_cd) FROM datasets.emd_boundary").fetchone()[0] == [
        "1111000100",
        "1114000100",
    ]
    assert metadata.temporal.reference_date == date(2026, 7, 1)
    assert metadata.spatial.feature_count == 2
    assert metadata.spatial.bbox == pytest.approx([126.9, 37.5, 127.1, 37.6])
    assert [d.dataset_id for d in list_datasets(conn)] == [
        "emd_boundary",
        "national_emd_boundary",
        "sido_boundary",
        "sigungu_boundary",
    ]


def test_daycare_ingest_excludes_points_outside_declared_sigungu(conn, tmp_path):
    path = tmp_path / "HangJeongDong_ver20260701.geojson"
    path.write_bytes((FIXTURES / "admdongkor_sample.geojson").read_bytes())
    ingest_boundary(conn, path)
    rows = [
        _row("11110000001", 37.55, 126.95),  # 가가구 안 (신고 자치구 일치)
        _row("11110000002", 37.55, 126.96),
        _row("11110000003", 37.56, 126.97),
        _row("11140000001", 37.55, 127.05),  # 나나구 안
        _row("11140000002", 37.55, 126.99),  # 나나구 신고지만 가가구 안 → 제외
    ]
    rows += [_row(f"1111000001{i}", 37.51 + i / 100, 126.91) for i in range(5)]

    metadata = ingest_daycare(conn, rows)

    [ids] = conn.execute("SELECT array_agg(facility_id ORDER BY facility_id) FROM datasets.daycare_facility").fetchone()
    assert "11140000002" not in ids and len(ids) == 9
    assert metadata.quality_notes == ["좌표가 신고 자치구가 아닌 다른 자치구에 있는 시설 1건 제외"]
    assert metadata.temporal.reference_date == date(2026, 10, 7)
    assert metadata.spatial.feature_count == 9


def test_daycare_ingest_stops_when_most_points_fall_outside(conn, tmp_path):
    path = tmp_path / "HangJeongDong_ver20260701.geojson"
    path.write_bytes((FIXTURES / "admdongkor_sample.geojson").read_bytes())
    ingest_boundary(conn, path)

    with pytest.raises(IngestError, match="자치구 코드 체계나 좌표계"):
        ingest_daycare(conn, [_row("11110000001", 37.55, 126.95), _row("11110000002", 37.45, 126.95)])


def test_auto_csv_ingest_groups_partitions_and_creates_point_dataset(conn, tmp_path):
    boundary = tmp_path / "HangJeongDong_ver20260701.geojson"
    boundary.write_bytes((FIXTURES / "admdongkor_sample.geojson").read_bytes())
    ingest_boundary(conn, boundary)
    header = "업소번호,행정동코드,업종명,경도,위도\n"
    (tmp_path / "상가_서울.csv").write_text(header + "001,11110001,카페,126.95,37.55\n", encoding="utf-8")
    (tmp_path / "상가_부산.csv").write_text(header + "002,26110001,음식점,129.05,35.15\n", encoding="utf-8")

    [metadata] = ingest_discovered_csvs(conn, tmp_path)

    table = sql.Identifier("datasets", metadata.dataset_id)
    assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(table)).fetchone()[0] == 2
    assert metadata.spatial.geometry_type == "Point"
    assert metadata.join_keys[0].field == "unit_key"
    assert metadata.spatial.coverage.name == "대한민국"
    assert "포함 시도 2곳: 서울특별시, 부산광역시" in metadata.quality_notes
    assert metadata.quality_notes[-1].endswith("표본 일치율 100.0%")


def _ingest_sample_boundary(conn, tmp_path) -> None:
    path = tmp_path / "HangJeongDong_ver20260701.geojson"
    path.write_bytes((FIXTURES / "admdongkor_sample.geojson").read_bytes())
    ingest_boundary(conn, path)


def test_auto_csv_ingest_fixes_types_after_reading_every_row(conn, tmp_path):
    # 표본(앞 300행)에서는 숫자뿐이던 층정보에 뒤에서 'B1'이 나온다 (가가구 가동 안의 점).
    _ingest_sample_boundary(conn, tmp_path)
    rows = [f"{i:04d},1,126.95,37.55" for i in range(SAMPLE_ROWS)] + ["9999,B1,126.95,37.55"]
    (tmp_path / "상가_서울.csv").write_text("업소번호,층정보,경도,위도\n" + "\n".join(rows) + "\n", encoding="utf-8")

    [metadata] = ingest_discovered_csvs(conn, tmp_path)

    assert metadata.column("층정보").data_type == DataType.STRING
    table = sql.Identifier("datasets", metadata.dataset_id)
    [floors] = conn.execute(
        sql.SQL('SELECT array_agg(DISTINCT "층정보" ORDER BY "층정보") FROM {}').format(table)
    ).fetchone()
    assert floors == ["1", "B1"]


def test_auto_csv_ingest_accepts_header_cells_with_spaces(conn, tmp_path):
    _ingest_sample_boundary(conn, tmp_path)
    (tmp_path / "시설.csv").write_text("시설명, 경도, 위도\n가나다,126.95,37.55\n", encoding="utf-8")

    [metadata] = ingest_discovered_csvs(conn, tmp_path)

    assert [column.name for column in metadata.columns[:3]] == ["시설명", "경도", "위도"]
    assert metadata.spatial.feature_count == 1


def test_auto_csv_metadata_reflects_loaded_rows(conn, tmp_path):
    # 서울(가가구 가동) 안의 점 300개와, 표본 뒤에 나오는 한국 범위 밖 좌표 1개
    _ingest_sample_boundary(conn, tmp_path)
    rows = [f"시설{i},서울특별시,{126.91 + i / 10000:.4f},37.55" for i in range(SAMPLE_ROWS)]
    rows.append("먼곳,서울특별시,200.0,95.0")
    (tmp_path / "시설_서울.csv").write_text("시설명,시도명,경도,위도\n" + "\n".join(rows) + "\n", encoding="utf-8")

    [metadata] = ingest_discovered_csvs(conn, tmp_path)

    assert (metadata.spatial.coverage.name, metadata.spatial.coverage.code) == ("서울특별시", "11")
    assert metadata.spatial.feature_count == SAMPLE_ROWS
    assert metadata.spatial.bbox == pytest.approx([126.91, 37.55, 126.91 + (SAMPLE_ROWS - 1) / 10000, 37.55])
    assert metadata.column("시도명").semantic_type == SemanticType.SIDO_NAME
    assert "한국 범위 밖 좌표 1건은 위치(geom)를 만들지 않음" in metadata.quality_notes


def test_population_ingest_notes_codes_missing_from_the_national_boundary(conn, tmp_path):
    _ingest_sample_boundary(conn, tmp_path)
    path = tmp_path / "mois_population_by_age_20260930.csv"
    rows = [
        {"code": "1111000100", "male": {3: 10}, "female": {3: 10}},
        {"code": "4711000100", "male": {30: 5}, "female": {30: 7}},  # 경계(7월판)에 없는 행정동
    ]
    path.write_bytes(population_csv(rows))

    metadata = ingest_population(conn, path)

    assert metadata.quality_notes == [
        "전국 행정동 경계에 없는 행정동 코드 1개(인구 12명)는 경계와 결합되지 않아 분석에서 빠짐: 시도 코드 47 1개"
    ]
