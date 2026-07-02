from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.sandbox_worker_routes import setup_sandbox_worker_routes
from src.sandbox_job_templates import build_sandbox_job_from_template


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(setup_sandbox_worker_routes(ledger_root=tmp_path / "ledger"))
    return TestClient(app)


def test_sandbox_worker_routes_require_admin(tmp_path: Path):
    client = _client(tmp_path)
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_route").to_dict()

    response = client.post("/api/sandbox-worker/submit", json={"job": job})

    assert response.status_code == 403


def test_sandbox_worker_routes_submit_dry_run_and_status(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client(tmp_path)
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_route").to_dict()

    submit = client.post("/api/sandbox-worker/submit", json={"job": job})
    status = client.get("/api/sandbox-worker/status/pytest_route")
    artifacts = client.get("/api/sandbox-worker/artifacts/pytest_route")

    assert submit.status_code == 200
    assert submit.json()["success"] is True
    assert submit.json()["sandbox_worker"]["status"]["status"] == "dry_run"
    assert submit.json()["sandbox_worker"]["executed_live"] is False
    assert status.json()["status"]["status"] == "dry_run"
    assert artifacts.json()["artifact_count"] == 0


def test_sandbox_worker_routes_block_rw_mount_without_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client(tmp_path)
    job = build_sandbox_job_from_template("browser_smoke", job_id="browser_route").to_dict()

    response = client.post("/api/sandbox-worker/submit", json={"job": job})

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["sandbox_worker"]["status"]["status"] == "blocked"


def test_sandbox_worker_routes_cancel(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _client(tmp_path)

    response = client.post("/api/sandbox-worker/cancel/job1", json={"reason": "operator"})

    assert response.status_code == 200
    assert response.json()["status"]["status"] == "cancel_requested"
