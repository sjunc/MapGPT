import pytest

from app.catalog.catalog import CatalogIndexError, DatasetCatalog, sync_catalog_embeddings
from app.catalog.definitions import TRUSTED_DATASETS
from app.domain.catalog import AUTO_PUBLIC_API_CATEGORY
from app.repositories.catalog_repository import upsert_dataset
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.integration

ALL_DATASETS = ["daycare_facility", "emd_boundary", "population_by_age"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("어린이집 위치", "daycare_facility"),
        ("연령별 주민등록 인구", "population_by_age"),
        ("행정동 경계 polygon", "emd_boundary"),
    ],
)
def test_search_returns_most_similar_dataset_first(pool, catalog, query, expected):
    hits = DatasetCatalog(pool, FakeEmbedder()).search(query)

    assert hits[0].metadata.dataset_id == expected
    assert sorted(hit.metadata.dataset_id for hit in hits) == ALL_DATASETS
    assert [hit.similarity for hit in hits] == sorted((hit.similarity for hit in hits), reverse=True)


def test_search_returns_at_most_limit_candidates(pool, catalog):
    hits = DatasetCatalog(pool, FakeEmbedder()).search("어린이집 위치", limit=1)

    assert [hit.metadata.dataset_id for hit in hits] == ["daycare_facility"]


def test_sync_reembeds_only_datasets_whose_document_changed(conn, catalog):
    embedder = FakeEmbedder()
    assert sync_catalog_embeddings(conn, embedder) == [] and embedder.calls == []

    conn.execute(
        "UPDATE catalog.dataset SET metadata = jsonb_set(metadata, '{spatial,feature_count}', '50') "
        "WHERE dataset_id = 'emd_boundary'"
    )
    conn.execute(
        "UPDATE catalog.dataset SET metadata = jsonb_set(metadata, '{description}', '\"새 설명\"') "
        "WHERE dataset_id = 'daycare_facility'"
    )
    conn.commit()

    assert sync_catalog_embeddings(conn, embedder) == ["daycare_facility"]
    [[document]] = embedder.calls
    assert "설명: 새 설명" in document
    stored = conn.execute(
        "SELECT source_text FROM catalog.dataset_embedding WHERE dataset_id = 'daycare_facility'"
    ).fetchone()
    assert stored == (document,)


def test_sync_reembeds_everything_for_another_model_or_refresh(conn, catalog):
    assert sync_catalog_embeddings(conn, FakeEmbedder(model="other-model")) == ALL_DATASETS
    assert sync_catalog_embeddings(conn, FakeEmbedder(model="other-model")) == []
    assert sync_catalog_embeddings(conn, FakeEmbedder(model="other-model"), refresh=True) == ALL_DATASETS
    assert conn.execute("SELECT DISTINCT model FROM catalog.dataset_embedding").fetchall() == [("other-model",)]


def test_search_refuses_missing_or_outdated_embeddings(pool, conn, catalog):
    conn.execute("DELETE FROM catalog.dataset_embedding WHERE dataset_id = 'population_by_age'")
    conn.commit()
    embedder = FakeEmbedder()

    with pytest.raises(CatalogIndexError, match="population_by_age\\. python -m app.cli embed-catalog"):
        DatasetCatalog(pool, embedder).search("0~5세 인구")
    with pytest.raises(CatalogIndexError, match="daycare_facility, emd_boundary, population_by_age"):
        DatasetCatalog(pool, FakeEmbedder(model="other-model")).search("0~5세 인구")
    assert embedder.calls == []  # 검색어 임베딩 전에 멈춘다


def _auto_copies(conn, count: int) -> list[str]:
    """어린이집 위치와 거의 같은 문서를 가진 자동 후보 count개 (검색어와 가까워 검토된 후보를 밀어낼 수 있는 것)."""
    base = TRUSTED_DATASETS["daycare_facility"]
    ids = [f"auto_daycare_{index}" for index in range(count)]
    for dataset_id in ids:
        upsert_dataset(conn, base.model_copy(update={"dataset_id": dataset_id, "category": AUTO_PUBLIC_API_CATEGORY}))
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())
    return ids


def test_auto_candidates_follow_reviewed_ones_and_never_push_them_out(pool, conn, catalog):
    auto_ids = _auto_copies(conn, 7)

    hits = DatasetCatalog(pool, FakeEmbedder()).search("어린이집 위치", limit=3)

    ids = [hit.metadata.dataset_id for hit in hits]
    assert sorted(ids[:3]) == ALL_DATASETS and ids[0] == "daycare_facility"
    assert len(ids[3:]) == 5 and set(ids[3:]) <= set(auto_ids)


def test_missing_or_other_model_auto_embeddings_do_not_stop_search(pool, conn, catalog):
    auto_ids = _auto_copies(conn, 3)
    conn.execute("DELETE FROM catalog.dataset_embedding WHERE dataset_id = %s", (auto_ids[0],))
    conn.execute("UPDATE catalog.dataset_embedding SET model = 'other-model' WHERE dataset_id = %s", (auto_ids[1],))
    conn.commit()

    hits = DatasetCatalog(pool, FakeEmbedder()).search("어린이집 위치")

    assert [hit.metadata.dataset_id for hit in hits if hit.metadata.dataset_id.startswith("auto_")] == [auto_ids[2]]


def test_region_and_boundary_lookups_skip_auto_candidates(pool, conn, catalog):
    _auto_copies(conn, 2)
    catalog_api = DatasetCatalog(pool, FakeEmbedder())

    assert sorted(d.dataset_id for d in catalog_api.list_all(include_auto=False)) == ALL_DATASETS
    assert len(catalog_api.list_all()) == len(ALL_DATASETS) + 2
