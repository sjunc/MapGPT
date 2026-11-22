"""등록된 외부 출처(서울 OpenAPI)를 필요할 때 받아 적재하고 분석에 쓰는 흐름. 네트워크 대신 가짜 수집 함수를 쓴다."""

from datetime import date

import pytest

from app.catalog.catalog import sync_catalog_embeddings
from app.data_sources.acquisition import AcquisitionError, DataAcquirer, promote_source, register_sources
from app.data_sources.ingest import ingest_boundary
from app.data_sources.seoul_openapi import SeoulApiError
from app.data_sources.seoul_portal import PortalPage
from app.data_sources.sources import SourceDeclaration, SourceError, load_sources
from app.domain.catalog import AUTO_DISCOVERED_CATEGORY, AUTO_PUBLIC_API_CATEGORY
from app.domain.policies import AUTO_SOURCE_LIMITATIONS
from app.domain.enums import RunStatus, SemanticType, WorkflowStep
from app.domain.plan import AnalysisPlan
from app.domain.state import DatasetSelection, RequiredData
from app.repositories.catalog_repository import find_dataset, list_datasets
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


def _count_plan(dataset_id: str = "test_points") -> AnalysisPlan:
    return AnalysisPlan.model_validate(
        {
            "intent": "regional_summary",
            "region": {"name": "서울특별시", "level": "sido"},
            "analysis_unit": "sigungu",
            "datasets": [dataset_id, "sigungu_boundary"],
            "filters": [],
            "operations": [
                {
                    "type": "aggregate_by_area",
                    "input": dataset_id,
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


def _count_llm(dataset_id: str = "test_points", selection: DatasetSelection | None = None):
    return daycare_llm(
        interpreted=[
            interpreted(
                intent="regional_summary",
                analysis_unit="sigungu",
                analysis_unit_phrase="자치구별",
                restated_question="서울 자치구별 시험 시설 수",
                key_concepts=["시험 시설 위치"],
                assumptions=[],
            )
        ],
        selection=[
            selection
            or DatasetSelection(datasets=[dataset_id, "sigungu_boundary"], rationale="시설 위치와 자치구 경계")
        ],
        plans=[_count_plan(dataset_id)],
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
    # 위치가 없는 2행(대상 지역 밖·한국 범위 밖)은 품질 노트로 이미 알리므로 '경계 밖 점'으로 세지 않는다.
    stats = state.execution_results.operations[0].stats
    assert (stats["points_without_location"], stats["unassigned_points"]) == (2, 0)
    assert not any("경계 밖의 점" in note for note in state.final_response.limitations)


def test_pending_source_without_an_acquirer_stops_with_a_clear_error(pool, conn, boundary):
    _registered(conn)

    state = AnalysisWorkflow(_count_llm(), pool, FakeEmbedder()).run("서울 자치구별 시험 시설 수")

    assert (state.errors[0].step, state.errors[0].code) == (WorkflowStep.ACQUIRE_DATA, "DATA_ACQUISITION_UNAVAILABLE")


# ── 자동 후보 (등록 파일 없이 포털 정보로 만든 등록 내용, 사람 검토 전) ─────────────────────────────

AUTO = SourceDeclaration.model_validate(
    {**SOURCE.model_dump(mode="json"), "source_id": "auto_points", "license": None, "crs": None, "reviewed": False}
)
PAGE = PortalPage(
    description="시험 시설 위치입니다. ※ 좌표계 : WGS84",
    keywords=["시설"],
    license="공공누리 제1유형",
    update_cycle="매일",
)
INSIDE = ROWS[:4]  # 모두 서울(가가구·나나구) 안


def _auto_acquirer(pool, tmp_path, fetch=None, page=PAGE) -> DataAcquirer:
    # 등록 파일이 없다: 자동 후보의 등록 내용은 카탈로그에서 읽는다.
    return DataAcquirer(
        pool, {}, fetch or FakeFetch(INSIDE), tmp_path / "raw", FakeEmbedder(), read_page=lambda url: page
    )


def _register(conn, *declarations) -> None:
    register_sources(conn, {declaration.source_id: declaration for declaration in declarations})
    conn.commit()


def _plane(**overrides) -> SourceDeclaration:
    """미터 좌표(X·Y)와 영업상태가 있는 인허가형 자동 후보."""
    data = {
        **AUTO.model_dump(mode="json"),
        "source_id": "auto_licenses",
        "columns": [
            {"source": "BPLCNM", "name": "사업장명", "semantic": "facility_name", "description": "사업장명"},
            {"source": "TRDSTATENM", "name": "영업상태명", "semantic": "category", "description": "영업상태명"},
            {"source": "Y", "name": "좌표정보Y", "semantic": "latitude", "description": "좌표정보(Y)"},
            {"source": "X", "name": "좌표정보X", "semantic": "longitude", "description": "좌표정보(X)"},
        ],
        "crs": "EPSG:5174",
        "keep_rows": {"column": "TRDSTATENM", "values": ["영업/정상"]},
    }
    return SourceDeclaration.model_validate({**data, **overrides})


def _tm_rows(conn) -> list[dict]:
    """가가구 가동 안의 두 곳(영업 중)과 한 곳(폐업)을 EPSG:5174 미터 좌표로, 원본처럼 뒤에 공백을 붙여 만든다."""
    rows = []
    places = (("가", "영업/정상", 126.95, 37.55), ("나", "영업/정상", 126.96, 37.56), ("다", "폐업", 126.97, 37.57))
    for name, state, lon, lat in places:
        x, y = conn.execute(
            "SELECT ST_X(p), ST_Y(p) FROM ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 5174) p", (lon, lat)
        ).fetchone()
        rows.append({"BPLCNM": name, "TRDSTATENM": state, "X": f"{x}     ", "Y": f"{y}    "})
    return rows


def test_auto_candidate_keeps_its_declaration_in_the_catalog(conn, boundary):
    _register(conn, AUTO)

    entry = find_dataset(conn, "auto_points")

    assert entry.category == AUTO_PUBLIC_API_CATEGORY and entry.acquisition.fetched_at is None
    assert entry.acquisition.declaration["reviewed"] is False
    assert "아직 받지 않은 자동 후보" in entry.quality_notes[0]


def test_auto_candidate_is_acquired_from_its_catalog_declaration(pool, conn, boundary, tmp_path):
    _register(conn, AUTO)

    metadata, record = _auto_acquirer(pool, tmp_path).acquire("auto_points")

    assert (record.rows, record.located) == (4, 4)
    assert metadata.category == AUTO_PUBLIC_API_CATEGORY
    # 자동 수집 안내는 응답의 한계 문구 하나로 두고 품질 노트에는 되풀이하지 않는다.
    assert metadata.quality_notes[0] == f"서울 열린데이터광장 OpenAPI(TestPoints)에서 {date.today()} 수집, 4행"
    assert "좌표계: 받은 값으로 판정한 위경도(EPSG:4326)" in metadata.quality_notes
    # 상세 화면에서 읽은 이용 조건·갱신주기·설명·키워드를 남긴다. 등록 내용(지문)은 그대로다.
    assert (metadata.license, metadata.temporal.update_cycle) == ("공공누리 제1유형", "매일")
    assert "포털 설명: 시험 시설 위치입니다" in metadata.description and "시설" in metadata.semantic_tags
    assert metadata.acquisition.fingerprint == AUTO.fingerprint and metadata.acquisition.declaration is not None


def test_auto_candidate_with_too_few_rows_inside_the_region_is_refused(pool, conn, boundary, tmp_path):
    _register(conn, AUTO)

    with pytest.raises(AcquisitionError, match=r"4/6행\(67%\)뿐이라 쓰지 않습니다 \(자동 후보 기준 90%\)"):
        _auto_acquirer(pool, tmp_path, FakeFetch(ROWS)).acquire("auto_points")
    # 등록 파일로 검토한 출처는 같은 데이터도 그대로 쓴다 (사람이 확인했으므로).
    assert _acquirer(pool, tmp_path).acquire("test_points")[1].located == 4


def test_plane_coordinates_use_the_family_crs_and_keep_only_open_businesses(pool, conn, boundary, tmp_path):
    _register(conn, _plane())

    metadata, record = _auto_acquirer(pool, tmp_path, FakeFetch(_tm_rows(conn))).acquire("auto_licenses")

    assert (record.rows, record.located) == (2, 2)  # 폐업 1곳은 적재하지 않는다
    assert "'TRDSTATENM'이 영업/정상인 행만 적재 (1행 제외)" in metadata.quality_notes
    assert "좌표계: EPSG:5174 (계열 규칙)" in metadata.quality_notes
    assert metadata.spatial.bbox == pytest.approx([126.95, 37.55, 126.96, 37.56], abs=1e-6)
    assert conn.execute("SELECT count(*) FROM datasets.auto_licenses WHERE 영업상태명 = '폐업'").fetchone() == (0,)


def test_plane_coordinates_need_a_known_crs(pool, conn, boundary, tmp_path):
    _register(conn, _plane(source_id="auto_open_crs", crs=None))
    rows = _tm_rows(conn)

    with pytest.raises(AcquisitionError, match="미터 단위인데 좌표계를 확인할 수 없어"):
        _auto_acquirer(pool, tmp_path, FakeFetch(rows), page=None).acquire("auto_open_crs")
    noted = PortalPage(description="* 좌표안내 : 중부원점TM(EPSG:5174)", keywords=[], license=None, update_cycle=None)
    metadata, _ = _auto_acquirer(pool, tmp_path, FakeFetch(rows), page=noted).acquire("auto_open_crs")
    assert "좌표계: EPSG:5174 (상세 화면 안내)" in metadata.quality_notes


def test_family_crs_must_match_the_values(pool, conn, boundary, tmp_path):
    wgs84_rows = [{"BPLCNM": "가", "TRDSTATENM": "영업/정상", "X": "126.95", "Y": "37.55"}] * 3
    _register(conn, _plane())

    with pytest.raises(AcquisitionError, match="계열 규칙의 좌표계는 EPSG:5174인데 받은 좌표는 위경도"):
        _auto_acquirer(pool, tmp_path, FakeFetch(wgs84_rows)).acquire("auto_licenses")


def test_swapped_latitude_and_longitude_are_fixed_for_auto_candidates(pool, conn, boundary, tmp_path):
    _register(conn, AUTO)
    swapped = [{**row, "LAT": row["LNT"], "LNT": row["LAT"]} for row in INSIDE]

    metadata, record = _auto_acquirer(pool, tmp_path, FakeFetch(swapped)).acquire("auto_points")

    assert record.located == 4
    assert any("뒤바뀌어 바꿔 적용" in note for note in metadata.quality_notes)
    assert metadata.column("위도").semantic_type == SemanticType.LONGITUDE  # 'LAT' 자리에 경도가 왔다
    assert metadata.acquisition.fingerprint == AUTO.fingerprint  # 등록 내용은 바꾸지 않는다


def test_promote_source_writes_a_reviewed_registration_file(pool, conn, boundary, tmp_path):
    _register(conn, AUTO)
    _auto_acquirer(pool, tmp_path).acquire("auto_points")

    path = promote_source(conn, "auto_points", tmp_path / "sources")

    promoted = load_sources(tmp_path / "sources")["auto_points"]
    assert promoted.reviewed is True and promoted.crs is None  # 좌표계는 사람이 확인해 적는다
    assert (promoted.license, promoted.update_cycle) == ("공공누리 제1유형", "매일")
    assert path.read_text(encoding="utf-8").startswith("# 시험 시설 위치: 자동 후보에서 내보낸 등록 파일")
    with pytest.raises(SourceError, match="이미 있습니다"):
        promote_source(conn, "auto_points", tmp_path / "sources")
    with pytest.raises(SourceError, match="자동 후보가 아닙니다"):
        promote_source(conn, "test_points", tmp_path / "sources")


def test_auto_candidate_is_acquired_before_planning_and_marked_in_the_response(pool, conn, boundary, tmp_path):
    _register(conn, AUTO)
    sync_catalog_embeddings(conn, FakeEmbedder())
    conn.commit()
    workflow = AnalysisWorkflow(
        _count_llm("auto_points"), pool, FakeEmbedder(), acquirer=_auto_acquirer(pool, tmp_path)
    )

    state = workflow.run("서울 자치구별 시험 시설 수")

    assert state.status == RunStatus.SUCCEEDED, state.errors
    usage = {item.dataset_id: item for item in state.final_response.datasets}
    assert usage["auto_points"].reviewed is False and usage["sigungu_boundary"].reviewed is True
    assert usage["auto_points"].license == "공공누리 제1유형"
    assert AUTO_SOURCE_LIMITATIONS[0] in state.final_response.limitations


def test_auto_candidate_failing_the_checks_is_answered_as_no_data(pool, conn, boundary, tmp_path):
    _register(conn, AUTO)
    sync_catalog_embeddings(conn, FakeEmbedder())
    conn.commit()
    acquirer = _auto_acquirer(pool, tmp_path, FakeFetch(ROWS))  # 6행 중 4행만 서울 안: 기준 90% 미달

    state = AnalysisWorkflow(_count_llm("auto_points"), pool, FakeEmbedder(), acquirer=acquirer).run(
        "서울 자치구별 시험 시설 수"
    )

    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.ACQUIRE_DATA, "DATA_NOT_AVAILABLE")
    assert error.message.startswith("'시험 시설 위치' 데이터로 고른 자동 수집 후보를 쓸 수 없어 분석할 수 없습니다")
    assert "자동 후보 기준 90%" in error.message


def test_auto_candidate_whose_name_shares_no_word_with_the_data_is_not_used(pool, conn, boundary, tmp_path):
    _register(conn, AUTO)
    sync_catalog_embeddings(conn, FakeEmbedder())
    conn.commit()
    fetch = FakeFetch(INSIDE)
    substituted = DatasetSelection(
        required_data=[RequiredData(concept="평균 소득", dataset_id="auto_points")],
        datasets=["auto_points", "sigungu_boundary"],
        rationale="소득 대신 시설 위치",
    )
    llm = _count_llm("auto_points", selection=substituted)

    state = AnalysisWorkflow(llm, pool, FakeEmbedder(), acquirer=_auto_acquirer(pool, tmp_path, fetch)).run(
        "서울 자치구별 평균 소득"
    )

    [error] = state.errors
    assert (error.step, error.code) == (WorkflowStep.SELECT_DATASETS, "DATA_NOT_AVAILABLE")
    assert error.message.startswith(
        "'평균 소득' 데이터가 없어 분석할 수 없습니다 (자동 수집 후보 '시험 시설 위치'는 이름이 맞지 않아 쓰지 않음)"
    )
    assert fetch.calls == 0  # 받기 전에 멈춘다
