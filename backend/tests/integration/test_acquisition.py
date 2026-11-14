"""등록된 외부 출처(서울 OpenAPI)를 필요할 때 받아 적재하고 분석에 쓰는 흐름. 네트워크 대신 가짜 수집 함수를 쓴다."""

from datetime import date

import pytest

from app.catalog.catalog import sync_catalog_embeddings
from app.data_sources.acquisition import AcquisitionError, DataAcquirer, register_sources
from app.data_sources.ingest import ingest_boundary
from app.data_sources.seoul_openapi import SeoulApiError
from app.data_sources.sources import SourceDeclaration
from app.domain.catalog import AUTO_DISCOVERED_CATEGORY
from app.domain.enums import RunStatus, SemanticType, WorkflowStep
from app.domain.plan import AnalysisPlan
from app.domain.state import DatasetSelection
from app.repositories.catalog_repository import list_datasets
from app.workflows.analysis_workflow import AnalysisWorkflow
from tests.fakes import FakeEmbedder, daycare_llm, interpreted
from tests.support import FIXTURES

pytestmark = pytest.mark.integration

SOURCE = SourceDeclaration.model_validate(
    {
        "source_id": "test_points",
        "title": "시험 시설 위치",
        "description": "서울 시험 시설의 위치(점).",
        "semantic_tags": ["시험 시설"],
        "provider": "서울특별시",
        "source": "https://data.seoul.go.kr/",
        "license": "공공누리 제1유형",
        "connector": "seoul_openapi",
        "service": "TestPoints",
        "region": "서울특별시",
        "columns": [
            {"source": "ID", "name": "관리번호", "semantic": "identifier", "description": "시설 관리번호"},
            {"source": "NAME", "name": "시설명", "semantic": "facility_name", "description": "시설 이름"},
            {"source": "GU", "name": "자치구", "semantic": "sigungu_name", "description": "신고 자치구"},
            {"source": "LAT", "name": "위도", "semantic": "latitude", "description": "위도"},
            {"source": "LNT", "name": "경도", "semantic": "longitude", "description": "경도"},
        ],
    }
)
# 가가구 가동(126.9~127.0, 37.5~37.6)에 3개, 나나구 나동에 1개, 대상 지역 밖(부산) 1개, 한국 범위 밖 1개
ROWS = [
    {"ID": "1", "NAME": "가", "GU": "가가구", "LAT": "37.55", "LNT": "126.95"},
    {"ID": "2", "NAME": "나", "GU": "가가구", "LAT": "37.56", "LNT": "126.96"},
    {"ID": "3", "NAME": "다", "GU": "가가구", "LAT": "37.57", "LNT": "126.97"},
    {"ID": "4", "NAME": "라", "GU": "나나구", "LAT": "37.55", "LNT": "127.05"},
    {"ID": "5", "NAME": "마", "GU": "가가구", "LAT": "35.15", "LNT": "129.05"},
    {"ID": "6", "NAME": "바", "GU": "가가구", "LAT": "0", "LNT": "0"},
]


class FakeFetch:
    def __init__(self, rows=ROWS, error: Exception | None = None):
        self.rows, self.error, self.calls = rows, error, 0

    def __call__(self, source: SourceDeclaration, max_rows: int | None) -> list[dict]:
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.rows)


@pytest.fixture
def boundary(conn, tmp_path):
    path = tmp_path / "HangJeongDong_ver20260701.geojson"
    path.write_bytes((FIXTURES / "admdongkor_sample.geojson").read_bytes())
    ingest_boundary(conn, path)


def _acquirer(pool, tmp_path, fetch=None) -> DataAcquirer:
    return DataAcquirer(pool, {SOURCE.source_id: SOURCE}, fetch or FakeFetch(), tmp_path / "raw", FakeEmbedder())


def test_register_sources_adds_pending_entries_that_search_can_find(conn, boundary):
    assert register_sources(conn, {SOURCE.source_id: SOURCE}) == ["test_points"]

    [entry] = [d for d in list_datasets(conn) if d.dataset_id == "test_points"]
    assert entry.acquisition.fetched_at is None and entry.spatial.feature_count is None
    assert (entry.spatial.coverage.name, entry.spatial.coverage.code) == ("서울특별시", "11")
    assert [column.name for column in entry.columns][-3:] == ["위도", "경도", "geom"]
    assert register_sources(conn, {SOURCE.source_id: SOURCE}) == []  # 바뀐 것이 없으면 다시 쓰지 않는다


def test_acquire_loads_only_locations_inside_the_declared_region(pool, conn, boundary, tmp_path):
    fetch = FakeFetch()

    metadata, record = _acquirer(pool, tmp_path, fetch).acquire("test_points")

    assert conn.execute("SELECT count(*), count(geom) FROM datasets.test_points").fetchone() == (6, 4)
    assert (record.rows, record.located, record.from_cache, fetch.calls) == (6, 4, False, 1)
    assert metadata.spatial.feature_count == 4 and metadata.spatial.coverage.name == "서울특별시"
    assert metadata.spatial.bbox == pytest.approx([126.95, 37.55, 127.05, 37.57])
    assert metadata.category != AUTO_DISCOVERED_CATEGORY
    assert metadata.column("자치구").semantic_type == SemanticType.SIGUNGU_NAME
    assert metadata.acquisition.fetched_at == date.today()
    assert metadata.quality_notes == [
        f"서울 열린데이터광장 OpenAPI(TestPoints)에서 {date.today()} 수집, 6행",
        "대상 지역(서울특별시) 밖 좌표 1건은 위치(geom)를 만들지 않음",
        "한국 범위 밖 좌표 1건은 위치(geom)를 만들지 않음",
    ]
    assert list((tmp_path / "raw" / "sources").glob("test_points_*.json"))


def test_acquire_reuses_the_raw_copy_within_the_refresh_period(pool, conn, boundary, tmp_path):
    fetch = FakeFetch()
    acquirer = _acquirer(pool, tmp_path, fetch)
    acquirer.acquire("test_points")

    _, record = acquirer.acquire("test_points")

    assert record.from_cache and fetch.calls == 1


def test_acquire_failures_explain_how_to_prefetch(pool, conn, boundary, tmp_path):
    fetch = FakeFetch(error=SeoulApiError("전체 200,000건이 한도 100,000건보다 많습니다"))

    with pytest.raises(AcquisitionError, match="fetch-source test_points"):
        _acquirer(pool, tmp_path, fetch).acquire("test_points")


def _count_plan() -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "intent": "regional_summary",
            "region": {"name": "서울특별시", "level": "sido"},
            "analysis_unit": "sigungu",
            "datasets": ["test_points", "sigungu_boundary"],
            "filters": [],
            "operations": [
                {
                    "type": "aggregate_by_area",
                    "input": "test_points",
                    "boundary": "sigungu_boundary",
                    "aggregation": "count",
                    "output": "facility_count",
                    "label": "시험 시설 수",
                },
                {
                    "type": "rank",
                    "input": "facility_count",
                    "order": "desc",
                    "output": "facility_rank",
                    "label": "시험 시설 수 순위",
                },
            ],
            "output": {"map_type": "choropleth", "metric": "facility_count", "ranking": {"enabled": True, "limit": 10}},
        }
    )


def _registered(conn) -> None:
    register_sources(conn, {SOURCE.source_id: SOURCE})
    sync_catalog_embeddings(conn, FakeEmbedder())


def _count_llm():
    return daycare_llm(
        interpreted=[
            interpreted(
                intent="regional_summary",
                analysis_unit="sigungu",
                restated_question="서울 자치구별 시험 시설 수",
                key_concepts=["시험 시설 위치"],
                assumptions=[],
            )
        ],
        selection=[DatasetSelection(datasets=["test_points", "sigungu_boundary"], rationale="시설 위치와 자치구 경계")],
        plans=[_count_plan()],
    )


def test_pending_source_is_acquired_before_planning(pool, conn, boundary, tmp_path):
    _registered(conn)

    state = AnalysisWorkflow(_count_llm(), pool, FakeEmbedder(), acquirer=_acquirer(pool, tmp_path)).run(
        "서울 자치구별 시험 시설 수"
    )

    assert state.status == RunStatus.SUCCEEDED, state.errors
    [record] = state.data_acquisitions
    assert (record.dataset_id, record.rows, record.located) == ("test_points", 6, 4)
    ranking = [(item.rank, item.unit_name, item.value) for item in state.final_response.ranking]
    assert ranking == [(1, "가가구", 3), (2, "나나구", 1)]
    assert f"[시험 시설 위치] 서울 열린데이터광장 OpenAPI(TestPoints)에서 {date.today()} 수집, 6행" in (
        state.final_response.limitations
    )


def test_pending_source_without_an_acquirer_stops_with_a_clear_error(pool, conn, boundary):
    _registered(conn)

    state = AnalysisWorkflow(_count_llm(), pool, FakeEmbedder()).run("서울 자치구별 시험 시설 수")

    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.ACQUIRE_DATA, "DATA_ACQUISITION_UNAVAILABLE")
