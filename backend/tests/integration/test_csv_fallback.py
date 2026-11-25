"""사용할 수 없는 CSV를 고르면 같은 개념의 OpenAPI로 한 번 재선택한다."""

from datetime import date

import pytest

from app.catalog.catalog import sync_catalog_embeddings
from app.catalog.definitions import DAYCARE_FACILITY, POPULATION_BY_AGE
from app.domain.catalog import SourceAcquisition
from app.domain.state import DatasetSelection, RequiredData
from app.repositories.catalog_repository import upsert_dataset
from app.repositories.run_repository import get_run
from app.workflows.analysis_workflow import AnalysisWorkflow
from tests.fakes import FakeEmbedder, daycare_llm
from tests.support import QUESTION

pytestmark = pytest.mark.integration


def _register(conn, failure, with_api=True):
    csv = DAYCARE_FACILITY.model_copy(update={"dataset_id": "broken_csv", "category": "auto_discovered"})
    if failure == "coverage":
        csv = csv.model_copy(update={"spatial": csv.spatial.model_copy(update={"coverage": None})})
    elif failure == "no_location":
        csv = csv.model_copy(update={"columns": [c for c in csv.columns if c.name != "geom"], "join_keys": []})
    elif failure in {"empty", "column"}:
        conn.execute("CREATE TABLE datasets.broken_csv AS SELECT * FROM datasets.daycare_facility WHERE false")
        if failure == "column":
            conn.execute("INSERT INTO datasets.broken_csv SELECT * FROM datasets.daycare_facility")
            conn.execute("ALTER TABLE datasets.broken_csv DROP COLUMN geom")
    upsert_dataset(conn, csv)
    if with_api:
        upsert_dataset(
            conn,
            DAYCARE_FACILITY.model_copy(
                update={
                    "acquisition": SourceAcquisition(
                        source_id="daycare_facility",
                        connector="seoul_openapi",
                        service="Test",
                        fingerprint="test",
                        refresh_days=30,
                        fetched_at=date.today(),
                    ),
                }
            ),
        )
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())


def _selection(source):
    return DatasetSelection(
        datasets=[source, "population_by_age", "emd_boundary"],
        rationale=source,
        required_data=[RequiredData(concept="어린이집", dataset_id=source)],
    )


@pytest.mark.parametrize("failure", ["coverage", "no_location", "missing_table", "empty", "column"])
def test_invalid_csv_reselects_api_and_preserves_both_choices(pool, catalog, conn, failure):
    _register(conn, failure)
    llm = daycare_llm(selection=[_selection("broken_csv"), _selection("daycare_facility")])
    try:
        state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)
        assert state.status == "succeeded", state.errors
        assert "broken_csv" not in state.selected_datasets
        assert "daycare_facility" in state.selected_datasets
        prompts = llm.prompts_for(DatasetSelection)
        assert len(prompts) == 2
        assert "CSV 사용 실패" in prompts[1]
        assert '"dataset_id": "broken_csv"' not in prompts[1]
        stored = get_run(conn, state.run_id)["provenance"]
        assert [s["datasets"][0] for s in stored["dataset_selection_history"]] == ["broken_csv", "daycare_facility"]
    finally:
        conn.execute("DROP TABLE IF EXISTS datasets.broken_csv")
        conn.commit()


def test_missing_api_does_not_substitute_unrelated_data(pool, catalog, conn):
    _register(conn, "coverage", with_api=False)
    llm = daycare_llm(selection=[_selection("broken_csv")])
    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)
    assert state.errors[0].code == "DATA_NOT_AVAILABLE"
    assert "대체 OpenAPI 후보가 없습니다" in state.errors[0].message
    assert len(llm.prompts_for(DatasetSelection)) == 1


def test_reselection_is_bounded_and_rejected_csv_cannot_be_reused(pool, catalog, conn):
    _register(conn, "coverage")
    llm = daycare_llm(selection=[_selection("broken_csv")])
    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)
    assert state.errors[0].code == "DATA_NOT_AVAILABLE"
    assert state.selected_datasets == []
    assert len(llm.prompts_for(DatasetSelection)) == 2


@pytest.mark.parametrize("metadata", [DAYCARE_FACILITY, POPULATION_BY_AGE])
def test_usable_csv_with_points_or_join_keys_is_kept(pool, catalog, conn, metadata):
    upsert_dataset(conn, metadata.model_copy(update={"category": "auto_discovered"}))
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())
    llm = daycare_llm()
    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)
    assert state.status == "succeeded", state.errors
    assert len(llm.prompts_for(DatasetSelection)) == 1
    assert metadata.dataset_id in state.selected_datasets


def test_api_without_required_concept_still_reports_missing_data(pool, catalog, conn):
    _register(conn, "coverage")
    missing = DatasetSelection(
        datasets=["emd_boundary", "population_by_age"],
        rationale="대체 출처 없음",
        required_data=[RequiredData(concept="어린이집", dataset_id=None)],
    )
    llm = daycare_llm(selection=[_selection("broken_csv"), missing])
    state = AnalysisWorkflow(llm, pool, FakeEmbedder()).run(QUESTION)
    assert state.errors[0].code == "DATA_NOT_AVAILABLE"
    assert "'어린이집' 데이터가 없어" in state.errors[0].message
    assert state.selected_datasets == []
