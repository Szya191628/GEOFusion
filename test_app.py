import httpx
import pytest
from fastapi.testclient import TestClient
from app import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FUSION_API_TOKEN", "test-only-token")
    for key in ("GEORANK_BASE_URL", "MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME", "FUSION_REPO_ROOT"):
        monkeypatch.delenv(key, raising=False)
    return TestClient(app, headers={"Authorization": "Bearer test-only-token"})


def test_auth_required():
    assert TestClient(app).post("/v1/artifacts", json={}).status_code == 401


def test_artifacts_no_implicit_license_and_no_write(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = client.post("/v1/artifacts", json={"site_url": "https://example.org", "name": "Demo", "pages": []})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft" and body["publish_available"] is False
    assert body["result"]["facts.json"]["license"] == "none"
    assert "No reuse licence" in body["result"]["llms.txt"]
    assert list(tmp_path.iterdir()) == []


def test_snapshot_cannot_make_real_requests(client, monkeypatch):
    def fail(*a, **kw):
        raise AssertionError("Unexpected network access")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fail)
    response = client.post("/v1/audits", json={"site_url": "https://example.org", "resources": {
        "https://example.org/": {"body": "<html><title>Demo</title><h1>Demo</h1></html>"}}})
    assert response.status_code == 200
    body = response.json()
    assert body["coverage"] == "supplied-snapshot-only"
    assert body["missing_resources"]
    assert body["result"]["findings"]


def test_cross_origin_rejected(client):
    response = client.post("/v1/audits", json={"site_url": "https://example.org", "resources": {
        "http://127.0.0.1/": {"body": "secret"}}})
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint,payload", [
    ("keywords", {"seeds": ["GEO"]}), ("plans", {"brief": "Plan"}),
    ("content-drafts", {"brief": "Content"}),
    ("fix-previews", {"url": "https://example.org/", "finding_id": "seo.canonical.missing"}),
])
def test_unconfigured_capabilities_fail_explicitly(client, endpoint, payload):
    assert client.post("/v1/" + endpoint, json=payload).status_code == 503


def test_unavailable_model_is_not_success(client, monkeypatch):
    monkeypatch.setenv("MODEL_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    async def fail(*a, **kw):
        raise httpx.ConnectError("test")
    monkeypatch.setattr(httpx.AsyncClient, "post", fail)
    assert client.post("/v1/plans", json={"brief": "test"}).status_code == 502


def test_model_returns_draft(client, monkeypatch):
    monkeypatch.setenv("MODEL_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    async def respond(*a, **kw):
        return httpx.Response(200, json={"choices": [{"message": {"content": "30/60/90 draft"}}]},
                              request=httpx.Request("POST", "https://model.invalid"))
    monkeypatch.setattr(httpx.AsyncClient, "post", respond)
    result = client.post("/v1/plans", json={"brief": "test"}).json()
    assert result["status"] == "draft" and result["provider_status"] == "succeeded"


def test_keywords_does_not_claim_provider_success(client, monkeypatch):
    monkeypatch.setenv("GEORANK_BASE_URL", "https://georank.invalid")
    async def respond(*a, **kw):
        return httpx.Response(200, json={"dimensions": []}, request=httpx.Request("POST", "https://georank.invalid"))
    monkeypatch.setattr(httpx.AsyncClient, "post", respond)
    result = client.post("/v1/keywords", json={"seeds": ["GEO"]}).json()
    assert result["provider_status"] == "unknown"


def test_no_publish_endpoint(client):
    assert client.post("/v1/publish").status_code == 404
