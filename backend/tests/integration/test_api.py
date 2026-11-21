import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app, get_embedder, get_llm
from app.config import Settings
from app.llm.base import LLMError
from tests.fakes import FakeEmbedder, daycare_llm
from tests.support import QUESTION

pytestmark = pytest.mark.integration


@pytest.fixture
def client(database_url, catalog):
    settings = Settings(
        database_url=database_url,
        llm_provider="ollama",
        ollama_model=None,
        openai_api_key=None,
        openai_embedding_model="test-embedding",
    )
    app = create_app(settings)
    app.dependency_overrides[get_llm] = lambda: daycare_llm()
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    with TestClient(app) as test_client:
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


def test_export_csv_and_geojson_from_stored_result(client):
    body = client.post("/api/analyses", json={"question": QUESTION}).json()
    run_id = body["run_id"]

    csv_response = client.get(f"/api/analyses/{run_id}/export", params={"format": "csv"})
    geojson_response = client.get(f"/api/analyses/{run_id}/export", params={"format": "geojson"})

    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"] == "text/csv; charset=utf-8"
    assert csv_response.headers["content-disposition"] == f'attachment; filename="mapgpt-{run_id[:8]}.csv"'
    text = csv_response.content.decode("utf-8")
    lines = text.removeprefix("﻿").splitlines()
    assert text.startswith("﻿")
    assert lines[0].startswith("순위,지역 코드,지역 이름,상위 지역,")
    assert len(lines) == 1 + len(body["result"]["map_layer"]["geojson"]["features"])
    assert lines[1].split(",")[:3] == ["1", body["result"]["ranking"][0]["unit_key"], "카동"]

    assert geojson_response.status_code == 200
    assert geojson_response.headers["content-type"] == "application/geo+json"
    assert geojson_response.headers["content-disposition"].endswith(f'mapgpt-{run_id[:8]}.geojson"')
    assert geojson_response.json() == body["result"]["map_layer"]["geojson"]


def test_export_of_failed_or_unknown_run(client):
    client.app.dependency_overrides[get_llm] = lambda: daycare_llm(interpreted=[LLMError("OpenAI 호출 실패: 401")])
    failed = client.post("/api/analyses", json={"question": QUESTION}).json()

    assert failed["status"] == "failed"
    assert client.get(f"/api/analyses/{failed['run_id']}/export").status_code == 409
    assert client.get(f"/api/analyses/{uuid.uuid4()}/export").status_code == 404
    assert client.get(f"/api/analyses/{failed['run_id']}/export", params={"format": "xlsx"}).status_code == 422


def test_suggestions_only_use_registered_datasets(client):
    seoul = client.get("/api/suggestions").json()
    national = client.get("/api/suggestions", params={"scope": "national"}).json()

    # 테스트 카탈로그에는 행정동 경계·인구·어린이집만 있어 시군구 경계·전국 경계가 필요한 질문은 빠진다.
    assert seoul == [
        "서울 행정동 중 영유아 인구 대비 어린이집이 부족한 곳은?",
        "강남구 어린이집이 어디에 몰려 있나요?",
        "서울 행정동별 0~5세 인구",
    ]
    assert national == seoul
    assert client.get("/api/suggestions", params={"scope": "busan"}).status_code == 422
