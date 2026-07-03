from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.coding_agent_routes import setup_coding_agent_routes
from src.coding_agent_backend import CodingCheckCommand, build_coding_task_plan
from src.coding_agent_sandbox_bridge import build_sandbox_jobs_for_coding_plan, dispatch_coding_checks_to_sandbox
from src.agent_sandbox_worker import SandboxCommandResult, SandboxWorker
from src.repo_registry import RepoRecord, RepoRegistry
from src.sandbox_job_ledger import SandboxJobLedger


def _repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repos" / "demo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _registry(path: Path | None = None) -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Demo Repo",
            repo_kind="project",
            owner="fuzzy123-ai",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            allowed_actions=["status", "log", "diff_stat", "changed_paths", "branch"],
            created_at="2026-07-02T10:00:00Z",
        )
    )
    if path is not None:
        registry.save_json(path)
    return registry


def _plan(tmp_path: Path):
    _repo_root(tmp_path)
    return build_coding_task_plan(
        registry=_registry(),
        repo_id="demo",
        workspace_base=tmp_path,
        objective="Run focused checks",
        allowed_paths=["src", "tests"],
        checks=[CodingCheckCommand.create(argv=["python", "-m", "pytest", "tests/test_demo.py", "-q"])],
        task_id="focused-checks",
        worktree_base=tmp_path / "worktrees",
        live_enabled=True,
        operator_decision="go",
    )


def test_build_sandbox_jobs_for_coding_plan_uses_allowed_paths(tmp_path: Path):
    plan = _plan(tmp_path)

    jobs = build_sandbox_jobs_for_coding_plan(plan)

    assert len(jobs) == 1
    assert jobs[0].job_id == "focused-checks-check-1"
    assert jobs[0].argv == ("python", "-m", "pytest", "tests/test_demo.py", "-q")
    assert [mount.source for mount in jobs[0].mounts] == ["src", "tests"]
    assert jobs[0].network_mode == "none"
    assert jobs[0].secrets_attached is False


def test_dispatch_coding_checks_to_sandbox_returns_quality_gate(tmp_path: Path):
    plan = _plan(tmp_path)
    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path / "ledger"))

    dispatch = dispatch_coding_checks_to_sandbox(plan=plan, worker=worker, changed_paths=["src/example.py"])

    assert dispatch.statuses[0].status == "dry_run"
    assert dispatch.quality_gate["verified"] is True
    assert dispatch.evidence_bundle["verdict"] == "passed"
    assert dispatch.evidence_bundle["raw_content_visible"] is False
    assert dispatch.to_dict()["raw_content_visible"] is False


def test_dispatch_coding_checks_to_sandbox_turns_live_failures_into_quality_blockers(tmp_path: Path):
    plan = _plan(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], timeout_seconds: int):
        calls.append(argv)
        if "run" in argv:
            return SandboxCommandResult(exit_code=2, stderr="test failed")
        return SandboxCommandResult(exit_code=0, stdout="pod ready")

    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path / "ledger"), command_runner=runner)

    dispatch = dispatch_coding_checks_to_sandbox(
        plan=plan,
        worker=worker,
        changed_paths=["src/example.py"],
        live_enabled=True,
        operator_go=True,
    )

    assert calls
    assert dispatch.statuses[0].status == "failed"
    assert dispatch.quality_gate["verified"] is False
    assert dispatch.evidence_bundle["verdict"] == "failed"
    assert dispatch.to_dict()["raw_content_visible"] is False


def test_coding_agent_route_dispatches_sandbox_checks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _repo_root(tmp_path)
    registry_path = tmp_path / "repos.json"
    _registry(registry_path)
    app = FastAPI()
    app.include_router(
        setup_coding_agent_routes(
            registry_path=registry_path,
            workspace_base=tmp_path,
            worktree_base=tmp_path / "worktrees",
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/coding-agent/repos/demo/sandbox-checks",
        json={
            "objective": "Run focused checks",
            "allowed_paths": ["src", "tests"],
            "checks": [{"argv": ["python", "-m", "pytest", "tests/test_demo.py", "-q"]}],
            "task_id": "focused-checks",
            "operator_decision": "go",
            "live_enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["sandbox_dispatch"]["statuses"][0]["status"] == "dry_run"
