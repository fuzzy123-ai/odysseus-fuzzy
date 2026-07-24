"""Route-level regression tests for GET /api/diagnostics/logs."""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("starlette.testclient")

from fastapi import FastAPI, HTTPException, Request
from starlette.testclient import TestClient

# Importing the route module pulls a few app deps; skip cleanly if unavailable.
diag = pytest.importorskip("routes.diagnostics_routes")

CANARY = "synthetic-log-canary-never-emit"


def _client_with_admin_gate(monkeypatch, gate, tmp_path=None):
    """Mount the diagnostics router with a mock require_admin and DATA_DIR."""
    monkeypatch.setattr(diag, "require_admin", gate)
    if tmp_path:
        monkeypatch.setattr(diag, "DATA_DIR", str(tmp_path))

    app = FastAPI()
    app.include_router(diag.setup_diagnostics_routes(
        rag_manager=None, rag_available=False, research_handler=None,
        memory_vector=None))
    return TestClient(app, raise_server_exceptions=False)


def _assert_log_projection(
    body,
    *,
    state,
    log_file_present,
    sampled_line_count,
):
    assert set(body) == {
        "schema",
        "source_id",
        "status",
        "presence",
        "counts",
        "states",
        "refusal_code",
    }
    assert body == {
        "schema": "odysseus.secret_safe_diagnostic.v1",
        "source_id": "diagnostic_log_summary",
        "status": "accepted",
        "presence": {"log_file_present": log_file_present},
        "counts": {"sampled_line_count": sampled_line_count},
        "states": {"status": state},
        "refusal_code": None,
    }
    assert "logs" not in body


def test_logs_unauthenticated_rejected(monkeypatch):
    def gate(_request: Request):
        raise HTTPException(401, "Not authenticated")
    client = _client_with_admin_gate(monkeypatch, gate)
    r = client.get("/api/diagnostics/logs")
    assert r.status_code == 401


def test_logs_non_admin_forbidden(monkeypatch):
    def gate(_request: Request):
        raise HTTPException(403, "Admin only")
    client = _client_with_admin_gate(monkeypatch, gate)
    r = client.get("/api/diagnostics/logs")
    assert r.status_code == 403


def test_logs_missing_file(monkeypatch, tmp_path):
    def gate(_request: Request):
        return None
    client = _client_with_admin_gate(monkeypatch, gate, tmp_path)
    r = client.get("/api/diagnostics/logs")
    assert r.status_code == 200
    _assert_log_projection(
        r.json(),
        state="missing",
        log_file_present=False,
        sampled_line_count=0,
    )


def test_logs_secret_safe_count_projection_and_clamping(monkeypatch, tmp_path):
    # Setup mock log file
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    # Write 1500 log lines
    lines = [f"{CANARY} Log line {i}\n" for i in range(1, 1501)]
    log_file.write_text("".join(lines), encoding="utf-8")

    def gate(_request: Request):
        return None
    client = _client_with_admin_gate(monkeypatch, gate, tmp_path)

    # 1. Default limit (200)
    r = client.get("/api/diagnostics/logs")
    assert r.status_code == 200
    _assert_log_projection(
        r.json(),
        state="available",
        log_file_present=True,
        sampled_line_count=200,
    )
    assert CANARY not in r.text

    # 2. Clamped upper bound (limit=2000 -> clamps to 1000)
    r = client.get("/api/diagnostics/logs?limit=2000")
    assert r.status_code == 200
    _assert_log_projection(
        r.json(),
        state="available",
        log_file_present=True,
        sampled_line_count=1000,
    )
    assert CANARY not in r.text

    # 3. Clamped lower bound (limit=-5 -> clamps to 1)
    r = client.get("/api/diagnostics/logs?limit=-5")
    assert r.status_code == 200
    _assert_log_projection(
        r.json(),
        state="available",
        log_file_present=True,
        sampled_line_count=1,
    )
    assert CANARY not in r.text

    # 4. Clamp limit=0 -> clamps to 1
    r = client.get("/api/diagnostics/logs?limit=0")
    assert r.status_code == 200
    _assert_log_projection(
        r.json(),
        state="available",
        log_file_present=True,
        sampled_line_count=1,
    )
    assert CANARY not in r.text

    # 5. Exact custom limit
    r = client.get("/api/diagnostics/logs?limit=5")
    assert r.status_code == 200
    _assert_log_projection(
        r.json(),
        state="available",
        log_file_present=True,
        sampled_line_count=5,
    )
    assert CANARY not in r.text
