from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "homeserver" / "run-sandbox-host-runner-smoke.sh"


def test_sandbox_host_runner_smoke_uses_odysseus_worker_not_general_shell():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "from src.agent_sandbox_worker import SandboxWorker" in script
    assert "SandboxJobRequest.create" in script
    assert "live_enabled=True" in script
    assert "operator_go=True" in script
    assert "python\", \"--version\"" in script
    assert "network_mode=\"none\"" in script
    assert "secrets_attached=False" in script
    assert "shell=True" not in script
    assert "bash -c" not in script
    assert "docker run" not in script.lower()
    assert "docker compose" not in script.lower()


def test_sandbox_host_runner_smoke_requires_host_runner_env_before_live_execution():
    script = SCRIPT.read_text(encoding="utf-8")

    env_check = script.index("ODYSSEUS_SANDBOX_RUNNER_BACKEND=host_ssh")
    podman_exec = script.index('podman exec "$container" python')

    assert env_check < podman_exec
    assert "setup-sandbox-host-runner.sh" in script
    assert "podman container exists" in script


def test_sandbox_host_runner_smoke_writes_redacted_evidence_ref():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "odysseus.sandbox_host_runner_live_smoke.v1" in script
    assert "data/reports/${report_ref}" in script
    assert "host-runner-live-smoke.json" in script
    assert '"raw_content_visible": False' in script
    assert '"tokens_visible": False' in script
    assert '"host_paths_visible": False' in script
    assert "TOKEN" not in script
    assert "PASSWORD" not in script
