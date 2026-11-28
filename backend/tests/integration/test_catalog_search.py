import pytest

from app.catalog.catalog import CatalogIndexError, DatasetCatalog, sync_catalog_embeddings
from app.catalog.definitions import TRUSTED_DATASETS
from app.domain.catalog import AUTO_PUBLIC_API_CATEGORY
from app.repositories.catalog_repository import nearest_datasets, upsert_dataset
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
    assert set(ids[3:]) == set(auto_ids)  # 제목 일치 후보는 의미 검색의 5개 제한 밖에서도 보충한다.


def test_title_match_recovers_candidate_outside_vector_limit(pool, conn, catalog):
    base = TRUSTED_DATASETS["daycare_facility"]
    upsert_dataset(conn, base.model_copy(update={"dataset_id": "museum", "title": "서울 박물관 위치"}))
    conn.commit()
    sync_catalog_embeddings(conn, FakeEmbedder())
    conn.commit()
    hits = DatasetCatalog(pool, FakeEmbedder()).search("박물관", limit=1)
    assert "museum" in {hit.metadata.dataset_id for hit in hits}


def test_population_title_matches_question_with_particles(pool, catalog):
    hits = DatasetCatalog(pool, FakeEmbedder()).search("서울특별시의 자치구별 65세 이상 인구를 보여줘", limit=1)
    assert "population_by_age" in {hit.metadata.dataset_id for hit in hits}


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


def test_filtered_search_stays_exact_on_the_approximate_index_path(conn, catalog):
    """HNSW 근사 스캔이 뽑은 이웃만 거르면, 이웃이 모두 자동 후보일 때 검토된 Dataset이 빠진다 (2026-11-23 개발 DB)."""
    _auto_copies(conn, 12)
    # 근사 인덱스 경로를 쓰게 하고, 그 경로가 이웃을 하나만 뽑게 한다.
    conn.execute("SET enable_seqscan = off")
    conn.execute("SET enable_sort = off")
    conn.execute("SET hnsw.ef_search = 1")
    [vector] = FakeEmbedder().embed(["어린이집 위치"])

    reviewed = nearest_datasets(conn, vector, 10, auto=False)
    auto = nearest_datasets(conn, vector, 5, auto=True)

    assert sorted(metadata.dataset_id for metadata, _ in reviewed) == ALL_DATASETS
    assert len(auto) == 5
