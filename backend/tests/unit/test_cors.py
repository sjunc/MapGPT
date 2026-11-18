import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app, get_pool
from app.config import Settings


def test_cors_origins_are_read_from_environment(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://mapgpt.example.com"]')
    assert Settings(_env_file=None).cors_allow_origins == ["https://mapgpt.example.com"]


@pytest.mark.parametrize(
    "origin,allowed",
    [
        ("https://mapgpt.example.com", True),
        ("https://unrelated.vercel.app", False),
    ],
)
def test_json_post_preflight_requires_allowed_origin(origin, allowed):
    app = create_app(Settings(_env_file=None, cors_allow_origins=["https://mapgpt.example.com"]))
    # lifespan을 실행하지 않아 DB·LLM 연결 없이 CORS 경계만 검사한다.
    client = TestClient(app)
    response = client.options(
        "/api/analyses",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == (200 if allowed else 400)
    assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)
    assert "access-control-allow-credentials" not in response.headers


def test_cors_headers_are_present_on_allowed_get_and_validation_error():
    origin = "https://mapgpt.example.com"
    app = create_app(Settings(_env_file=None, cors_allow_origins=[origin]))
    app.dependency_overrides[get_pool] = lambda: None
    client = TestClient(app)
    for path, status in [("/openapi.json", 200), ("/api/analyses/not-a-uuid", 422)]:
        response = client.get(path, headers={"Origin": origin})
        assert response.status_code == status
        assert response.headers["access-control-allow-origin"] == origin


def test_cors_is_disabled_by_default():
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/openapi.json", headers={"Origin": "https://mapgpt.example.com"})
    assert "access-control-allow-origin" not in response.headers
