from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "homeserver" / "setup-sandbox-host-runner.sh"


def test_sandbox_host_runner_setup_wires_narrow_backend_env():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "ODYSSEUS_SANDBOX_RUNNER_BACKEND" in script
    assert 'set_env ODYSSEUS_SANDBOX_RUNNER_BACKEND "host_ssh"' in script
    assert "ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_TARGET" in script
    assert "ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_CONFIG" in script
    assert "ODYSSEUS_SANDBOX_HOST_RUNNER_REMOTE_COMMAND" in script
    assert "odysseus-sandbox-host" in script
    assert "host.containers.internal" in script
    assert "IdentityFile /app/.ssh/id_ed25519_sandbox_host_runner" in script
    assert "/opt/odysseus/ops/homeserver/run-sandbox-job.py" in script


def test_sandbox_host_runner_setup_installs_dedicated_key_without_printing_it():
    script = SCRIPT.read_text(encoding="utf-8")
    lower = script.lower()

    assert "ssh-keygen" in script
    assert "id_ed25519_sandbox_host_runner" in script
    assert "authorized_keys" in script
    assert "cat \"$key_path.pub\" >> \"$HOME/.ssh/authorized_keys\"" in script
    assert "cat \"$key_path\"" not in script
    assert "echo \"$key_path" not in script
    assert "token" not in lower
    assert "password" not in lower


def test_sandbox_host_runner_setup_does_not_recreate_or_expose_general_shell():
    script = SCRIPT.read_text(encoding="utf-8").lower()

    assert "podman-compose up" not in script
    assert "podman compose up" not in script
    assert "docker" not in script
    assert "bash -c" not in script
    assert "shell" not in script
    assert "next step:" in script
