"""서울 OpenAPI 목록으로 자동 후보를 카탈로그와 맞추는 동기화. 네트워크 대신 가짜 목록·입출력 인자를 쓴다."""

import pytest
import yaml

from app.data_sources.ingest import ingest_boundary
from app.data_sources.seoul_catalog import sync_seoul_catalog
from app.data_sources.seoul_openapi import SeoulApiError
from app.domain.catalog import AUTO_PUBLIC_API_CATEGORY
from app.repositories.catalog_repository import find_dataset, list_datasets
from tests.support import FIXTURES
from tests.unit.test_seoul_catalog import LOCALDATA, WIFI, _io, _service

pytestmark = pytest.mark.integration

FAMILY = {
    "family_id": "localdata",
    "service_pattern": r"LOCALDATA_\d+",
    "crs": "EPSG:5174",
    "keep_rows": {"column": "TRDSTATENM", "values": ["영업/정상"]},
}
REVIEWED = {
    "source_id": "seoul_public_wifi",
    "title": "서울시 공공와이파이 위치",
    "description": "검토한 등록 파일",
    "semantic_tags": ["와이파이"],
    "provider": "서울특별시",
    "source": "https://data.seoul.go.kr/",
    "license": None,
    "connector": "seoul_openapi",
    "service": "TbPublicWifiInfo",
    "region": "서울특별시",
    "columns": [
        {"source": "LAT", "name": "위도", "semantic": "latitude", "description": "위도"},
        {"source": "LNT", "name": "경도", "semantic": "longitude", "description": "경도"},
    ],
}
SERVICES = [
    _service("TbPublicWifiInfo", "서울시 공공와이파이 서비스 위치 정보", "OA-20883"),  # 등록 파일이 있다
    _service("LOCALDATA_020301", "서울시 동물병원 인허가 정보", "OA-16007"),
    _service("CctvPoints", "서울시 방범 CCTV 위치", "OA-30001"),
    _service("LOCALDATA_020301_GA", "서울시 관악구 동물병원 인허가 정보", "OA-16200"),  # 자치구 데이터
    _service("GangseoPoints", "서울시 강서 시설", "OA-10004", host="http://openAPI.gangseo.seoul.kr:8088"),
    _service("SubwayTimetable", "서울시 지하철 시간표", "OA-101"),  # 좌표 없음
    _service("PowerUse2005", "서울시 행정동별 전력 사용량 2005년 위치정보", "OA-201"),  # 행정구역마다 값 하나인 통계
]
COLUMNS = {
    "OA-16007": LOCALDATA,
    "OA-30001": _io([("CCTV_ID", "관리번호"), ("LAT", "위도"), ("LOT", "경도")]),
    "OA-101": _io([("STATION_NM", "역명"), ("ARRIVETIME", "도착시간")]),
}


class FakeColumns:
    def __init__(self, columns=COLUMNS, failing=()):
        self.columns, self.failing, self.calls = columns, set(failing), []

    def __call__(self, inf_id: str) -> list[dict]:
        self.calls.append(inf_id)
        if inf_id in self.failing:
            raise SeoulApiError("ERROR-500")
        return self.columns.get(inf_id, WIFI)


@pytest.fixture
def boundary(conn, tmp_path):
    path = tmp_path / "HangJeongDong_ver20260701.geojson"
    path.write_bytes((FIXTURES / "admdongkor_sample.geojson").read_bytes())
    ingest_boundary(conn, path)


@pytest.fixture
def sources_dir(tmp_path):
    directory = tmp_path / "sources"
    (directory / "families").mkdir(parents=True)
    (directory / "seoul_public_wifi.yaml").write_text(yaml.safe_dump(REVIEWED, allow_unicode=True), encoding="utf-8")
    (directory / "families" / "localdata.yaml").write_text(yaml.safe_dump(FAMILY, allow_unicode=True), "utf-8")
    return directory


def _sync(conn, sources_dir, tmp_path, services=SERVICES, columns=None, refresh=False):
    report = sync_seoul_catalog(
        conn,
        fetch_list=lambda: list(services),
        fetch_columns=columns or FakeColumns(),
        sources_dir=sources_dir,
        cache_dir=tmp_path / "catalog_cache",
        refresh=refresh,
    )
    conn.commit()
    return report


def test_sync_registers_seoul_wide_services_with_coordinates_as_auto_candidates(conn, boundary, sources_dir, tmp_path):
    columns = FakeColumns()

    report = _sync(conn, sources_dir, tmp_path, columns=columns)

    assert report.services == 7
    assert report.candidates == report.registered == ["seoul_cctvpoints", "seoul_localdata_020301"]
    assert dict(report.skipped) == {
        "등록 파일·내장 Dataset이 있는 서비스": 1,
        "자치구 데이터": 1,
        "자치구 호스트": 1,
        "위도·경도(X·Y) 컬럼 한 쌍이 없음": 1,
        "행정구역별 통계 (점 개수로 셀 수 없음)": 1,
    }
    assert sorted(columns.calls) == ["OA-101", "OA-16007", "OA-30001"]  # 걸러진 서비스는 입출력 인자를 받지 않는다
    entry = find_dataset(conn, "seoul_localdata_020301")
    assert entry.category == AUTO_PUBLIC_API_CATEGORY and entry.acquisition.fetched_at is None
    assert entry.acquisition.declaration["crs"] == "EPSG:5174"  # 계열 규칙
    assert entry.source == "https://data.seoul.go.kr/dataList/OA-16007/S/1/datasetView.do"


def test_sync_reuses_cached_columns_and_skips_unchanged_candidates(conn, boundary, sources_dir, tmp_path):
    _sync(conn, sources_dir, tmp_path)
    columns = FakeColumns()

    report = _sync(conn, sources_dir, tmp_path, columns=columns)

    assert report.registered == [] and len(report.candidates) == 2
    assert columns.calls == []
    assert _sync(conn, sources_dir, tmp_path, columns=columns, refresh=True).registered == []
    assert sorted(columns.calls) == ["OA-101", "OA-16007", "OA-30001"]


def test_sync_removes_vanished_candidates_but_keeps_ones_it_could_not_check(conn, boundary, sources_dir, tmp_path):
    _sync(conn, sources_dir, tmp_path)
    without_cctv = [service for service in SERVICES if service["API_SERVICE_NM"] != "CctvPoints"]

    unreadable = _sync(conn, sources_dir, tmp_path, columns=FakeColumns(failing={"OA-16007"}), refresh=True)
    vanished = _sync(conn, sources_dir, tmp_path, services=without_cctv)

    assert unreadable.removed == [] and unreadable.skipped["입출력 인자를 받지 못함"] == 1
    assert vanished.removed == ["seoul_cctvpoints"]
    assert sorted(d.dataset_id for d in list_datasets(conn) if d.category == AUTO_PUBLIC_API_CATEGORY) == [
        "seoul_localdata_020301"
    ]


def test_sync_refuses_an_empty_service_list(conn, boundary, sources_dir, tmp_path):
    _sync(conn, sources_dir, tmp_path)

    with pytest.raises(SeoulApiError, match="목록이 비어"):
        _sync(conn, sources_dir, tmp_path, services=[])
    assert find_dataset(conn, "seoul_cctvpoints") is not None
