from fastapi.testclient import TestClient

from apps.api.main import app


def test_health():
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "ok"
    assert r.headers.get("x-trace-id")
    assert r.headers.get("x-request-id")


def test_health_respects_incoming_trace_id():
    client = TestClient(app)
    r = client.get("/v1/health", headers={"x-trace-id": "custom-trace"})
    assert r.headers["x-trace-id"] == "custom-trace"


def test_metrics_exposed():
    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "api_requests_total" in r.text
