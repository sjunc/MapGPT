import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.main import KST, create_app, get_embedder, get_llm
from app.config import Settings
from tests.fakes import FakeEmbedder, daycare_llm
from tests.support import QUESTION

pytestmark = pytest.mark.integration


def _test_client(database_url: str, **overrides) -> TestClient:
    settings = Settings(
        database_url=database_url,
        llm_provider="ollama",
        ollama_model=None,
        openai_api_key=None,
        openai_embedding_model="test-embedding",
        **overrides,
    )
    app = create_app(settings)
    app.dependency_overrides[get_llm] = lambda: daycare_llm()
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    return TestClient(app)


@pytest.fixture
def client(database_url, catalog):
    with _test_client(database_url) as test_client:
        yield test_client


def test_analysis_endpoint_returns_map_ranking_and_explanation(client):
    response = client.post("/api/analyses", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded" and body["errors"] == []
    result = body["result"]
    assert result["metric"]["name"] == "daycare_per_1000_children"
    assert result["ranking"][0]["unit_name"] == "카동"
    assert result["map_layer"]["geojson"]["type"] == "FeatureCollection"
    assert result["map_layer"]["style"]["default_view"] == "choropleth"
    assert len(result["map_layer"]["geojson"]["features"][0]["properties"]["label_point"]) == 2
    assert {d["dataset_id"]: d["reference_date"] for d in result["datasets"]} == {
        "emd_boundary": "2026-07-01",
        "population_by_age": "2026-09-30",
        "daycare_facility": "2026-10-07",
    }

    stored = client.get(f"/api/analyses/{body['run_id']}").json()
    assert stored["status"] == "succeeded"
    assert stored["provenance"]["analysis_plan"]["intent"] == "supply_demand_gap"
    assert stored["result"]["result_id"] == result["result_id"]


def test_analysis_scope_is_recorded_and_validated(client):
    body = client.post("/api/analyses", json={"question": QUESTION, "scope": "national"}).json()

    assert client.get(f"/api/analyses/{body['run_id']}").json()["provenance"]["scope"] == "national"
    assert client.post("/api/analyses", json={"question": QUESTION, "scope": "busan"}).status_code == 422


def test_unknown_run_is_404(client):
    assert client.get(f"/api/analyses/{uuid.uuid4()}").status_code == 404


def test_empty_question_is_rejected(client):
    assert client.post("/api/analyses", json={"question": ""}).status_code == 422


def test_missing_llm_configuration_is_503(client):
    client.app.dependency_overrides.pop(get_llm)

    response = client.post("/api/analyses", json={"question": QUESTION})

    assert response.status_code == 503
    assert "OLLAMA_MODEL" in response.json()["detail"]


def test_missing_embedding_configuration_is_503(client):
    client.app.dependency_overrides.pop(get_embedder)

    response = client.post("/api/analyses", json={"question": QUESTION})

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_catalog_and_health_endpoints(client):
    datasets = client.get("/api/datasets").json()
    health = client.get("/api/health").json()

    assert [d["dataset_id"] for d in datasets] == ["daycare_facility", "emd_boundary", "population_by_age"]
    assert health == {
        "status": "ok",
        "database": "ok",
        "llm_provider": "ollama",
        "llm_model": "",
        "embedding_model": "test-embedding",
    }


def test_analysis_requests_are_rate_limited_per_client(database_url, catalog):
    with _test_client(database_url, analysis_rate_limit="2/minute") as limited:
        statuses = [limited.post("/api/analyses", json={"question": QUESTION}).status_code for _ in range(3)]
        blocked = limited.post("/api/analyses", json={"question": QUESTION})
        health = limited.get("/api/health")

    assert statuses == [200, 200, 429]
    assert "너무 잦습니다" in blocked.json()["detail"]
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert health.status_code == 200  # 분석 요청만 센다


@pytest.mark.parametrize("rate_limit", ["", "0"])
def test_empty_or_zero_rate_limit_disables_it(database_url, catalog, rate_limit):
    with _test_client(database_url, analysis_rate_limit=rate_limit) as unlimited:
        statuses = {unlimited.post("/api/analyses", json={"question": QUESTION}).status_code for _ in range(6)}

    assert statuses == {200}


def _insert_run(conn, started_at: datetime) -> None:
    conn.execute(
        "INSERT INTO analysis.run (run_id, question, status, state, started_at) VALUES (%s, 'q', 'failed', '{}', %s)",
        (uuid.uuid4(), started_at),
    )
    conn.commit()


def test_daily_limit_counts_runs_since_kst_midnight(database_url, catalog, conn):
    midnight = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    _insert_run(conn, midnight - timedelta(seconds=1))  # 어제 실행은 세지 않는다
    _insert_run(conn, midnight)

    with _test_client(database_url, analysis_daily_limit=2, analysis_rate_limit="") as capped:
        allowed = capped.post("/api/analyses", json={"question": QUESTION})
        blocked = capped.post("/api/analyses", json={"question": QUESTION})

    assert allowed.status_code == 200
    assert blocked.status_code == 429
    assert "오늘의 분석 한도(2건)" in blocked.json()["detail"]
    assert 1 <= int(blocked.headers["Retry-After"]) <= 24 * 3600
    assert conn.execute("SELECT count(*) FROM analysis.run").fetchone()[0] == 3  # 막힌 요청은 실행하지 않는다
