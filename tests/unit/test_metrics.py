from __future__ import annotations

from fastapi.testclient import TestClient

from rlm_rs.api.app import create_app


def test_metrics_endpoint_exposed_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_METRICS", "true")

    app = create_app()
    client = TestClient(app)

    live = client.get("/health/live")
    assert live.status_code == 200

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "rlm_api_requests_total" in response.text
