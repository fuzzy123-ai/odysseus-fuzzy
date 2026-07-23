from unittest.mock import Mock

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("starlette.testclient")

from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from routes import diagnostics_routes
from src import ai_activity_ledger, memory_provenance_ledger


def _client(monkeypatch, *, api_token: bool, scopes: list[str], admin_allowed: bool):
    admin_gate = Mock()
    if not admin_allowed:
        admin_gate.side_effect = HTTPException(403, "Admin only")
    monkeypatch.setattr(diagnostics_routes, "require_admin", admin_gate)

    app = FastAPI()

    @app.middleware("http")
    async def _auth_state(request, call_next):
        request.state.api_token = api_token
        request.state.api_token_scopes = list(scopes)
        request.state.current_user = "api" if api_token else "browser-admin"
        return await call_next(request)

    app.include_router(
        diagnostics_routes.setup_diagnostics_routes(None, False, None)
    )
    return TestClient(app, raise_server_exceptions=False), admin_gate


def test_browser_admin_scrape_uses_no_ledgers(monkeypatch):
    ai_read = Mock(side_effect=AssertionError("AI ledger read during scrape"))
    memory_read = Mock(side_effect=AssertionError("Memory ledger read during scrape"))
    monkeypatch.setattr(ai_activity_ledger, "read_ai_activity", ai_read)
    monkeypatch.setattr(memory_provenance_ledger, "read_memory_provenance", memory_read)
    client, admin_gate = _client(
        monkeypatch, api_token=False, scopes=[], admin_allowed=True
    )

    response = client.get("/api/diagnostics/runtime-metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE odysseus_metrics_samples_dropped_total counter" in response.text
    assert "local_model_latency_seconds" not in response.text
    assert "raptorgraph_maintenance_failures_total" not in response.text
    admin_gate.assert_called_once()
    ai_read.assert_not_called()
    memory_read.assert_not_called()


def test_exact_observability_token_scrapes_without_browser_admin(monkeypatch):
    client, admin_gate = _client(
        monkeypatch,
        api_token=True,
        scopes=["observability:read"],
        admin_allowed=False,
    )

    response = client.get("/api/diagnostics/runtime-metrics")

    assert response.status_code == 200
    admin_gate.assert_not_called()


@pytest.mark.parametrize(
    "scopes",
    [
        [],
        ["chat"],
        ["observability:read", "chat"],
        ["observability:read", "observability:read"],
    ],
)
def test_scrape_rejects_nonexact_token_scopes(monkeypatch, scopes):
    client, admin_gate = _client(
        monkeypatch, api_token=True, scopes=scopes, admin_allowed=True
    )

    response = client.get("/api/diagnostics/runtime-metrics")

    assert response.status_code == 403
    admin_gate.assert_not_called()
