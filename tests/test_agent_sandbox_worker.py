from pathlib import Path
import subprocess

from src.agent_sandbox_worker import (
    SandboxCommandResult,
    SandboxSshHostRunner,
    SandboxWorker,
    build_sandbox_command_runner_from_env,
    sandbox_runner_readiness,
)
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
    job = build_sandbox_job_from_template("document_convert", job_id="unsafe_rw")
    job = type(job).create(
        job_id="unsafe_rw",
        argv=job.argv,
        image=job.image,
        mounts=[{"source": "src", "target": "/workspace/repo/src", "mode": "rw"}],
        limits=job.limits,
        capabilities=job.capabilities,
    )

    result = worker.submit(job)

    assert result.executed_live is False
    assert result.status.status == "blocked"
    assert "rw_mount_not_allowed" in result.status.stderr_preview


def test_sandbox_worker_allows_default_screenshot_artifact_mount(tmp_path: Path):
    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path))
    job = build_sandbox_job_from_template("browser_smoke", job_id="browser_smoke")

    result = worker.submit(job)

    assert result.executed_live is False
    assert result.status.status == "dry_run"


def test_sandbox_worker_live_execution_uses_runner_and_records_artifact(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (Path.cwd() / "data/reports/autonomous_coding_agent/pytest_live.log").exists()


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


def test_sandbox_worker_builds_unavailable_runner_for_incomplete_host_backend(tmp_path: Path):
    runner = build_sandbox_command_runner_from_env(env={"ODYSSEUS_SANDBOX_RUNNER_BACKEND": "host_ssh"})
    worker = SandboxWorker(ledger=SandboxJobLedger(tmp_path), command_runner=runner)
    job = build_sandbox_job_from_template("python_pytest", job_id="pytest_host_backend_missing")

    result = worker.submit(job, live_enabled=True, operator_go=True)

    assert result.status.status == "failed"
    assert result.status.exit_code == 127
    assert "pod_create_error:SandboxWorkerError" in result.status.stderr_preview


def test_sandbox_ssh_host_runner_uses_fixed_ssh_command_and_json_payload():
    calls = []

    def fake_process(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout='{"exit_code":0,"stdout":"ok","stderr":"","timed_out":false,"duration_seconds":0.02}',
            stderr="",
        )

    runner = SandboxSshHostRunner(
        target="odysseus-homeserver",
        ssh_config="/app/.ssh/config",
        remote_command="/opt/odysseus/ops/homeserver/run-sandbox-job.py",
        process_runner=fake_process,
    )

    result = runner(("podman", "pod", "rm", "-f", "odysseus-agent-demo"), 30)

    assert result.ok is True
    argv, kwargs = calls[0]
    assert argv == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-F",
        "/app/.ssh/config",
        "odysseus-homeserver",
        "/opt/odysseus/ops/homeserver/run-sandbox-job.py",
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert '"argv":["podman","pod","rm","-f","odysseus-agent-demo"]' in kwargs["input"]


def test_sandbox_runner_readiness_accepts_host_ssh_without_local_podman():
    env = {
        "ODYSSEUS_SANDBOX_RUNNER_BACKEND": "host_ssh",
        "ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_TARGET": "odysseus-homeserver",
        "ODYSSEUS_SANDBOX_HOST_RUNNER_REMOTE_COMMAND": "/opt/odysseus/ops/homeserver/run-sandbox-job.py",
    }

    readiness = sandbox_runner_readiness(env=env, tool_lookup=lambda tool: "/usr/bin/ssh" if tool == "ssh" else None)

    assert readiness["backend"] == "host_ssh"
    assert readiness["runner_available"] is True
    assert "ssh_available" in readiness["required_gates"]
