from pathlib import Path

from src.agent_sandbox_worker import SandboxCommandResult, SandboxWorker
from src.sandbox_job_ledger import SandboxJobLedger
from src.sandbox_job_templates import build_sandbox_job_from_template


def test_sandbox_worker_dry_run_records_plan(tmp_path: Path):
    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path))
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_smoke")

    result = worker.submit(job)

    assert result.executed_live is False
    assert result.status.status == "dry_run"
    assert result.plan.to_dict()["executes_live"] is False
    assert worker.status("pytest_smoke").status == "dry_run"
    assert worker.artifacts("pytest_smoke") == ()


def test_sandbox_worker_blocks_unsafe_rw_mount_without_gate(tmp_path: Path):
    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path))
    job = build_sandbox_job_from_template("browser_smoke", job_id="browser_smoke")

    result = worker.submit(job)

    assert result.executed_live is False
    assert result.status.status == "blocked"
    assert "rw_mount_not_allowed" in result.status.stderr_preview


def test_sandbox_worker_live_execution_uses_runner_and_records_artifact(tmp_path: Path):
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv, timeout_seconds):
        calls.append(tuple(argv))
        return SandboxCommandResult(exit_code=0, stdout="ok")

    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path), command_runner=fake_runner)
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_live")

    result = worker.submit(job, live_enabled=True, operator_go=True)

    assert result.executed_live is True
    assert result.status.status == "succeeded"
    assert result.evidence["exit_code"] == 0
    assert len(calls) == 3
    assert calls[0][:3] == ("podman", "pod", "create")
    assert calls[1][:2] == ("podman", "run")
    assert calls[2][:3] == ("podman", "pod", "rm")
    assert worker.artifacts("pytest_live") == ("data/reports/autonomous_coding_agent/pytest_live.log",)


def test_sandbox_worker_live_runner_exception_records_failed_status(tmp_path: Path):
    calls: list[tuple[str, ...]] = []

    def missing_runner(argv, timeout_seconds):
        calls.append(tuple(argv))
        raise FileNotFoundError("podman")

    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path), command_runner=missing_runner)
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_missing_podman")

    result = worker.submit(job, live_enabled=True, operator_go=True)
    status = worker.status("pytest_missing_podman")

    assert result.executed_live is True
    assert result.status.status == "failed"
    assert result.status.exit_code == 127
    assert "pod_create_error:FileNotFoundError" in result.status.stderr_preview
    assert status.status == "failed"
    assert status.exit_code == 127
    assert "pod_create_error" in status.stdout_preview
    assert calls[0][:3] == ("podman", "pod", "create")
    assert calls[1][:3] == ("podman", "pod", "rm")
