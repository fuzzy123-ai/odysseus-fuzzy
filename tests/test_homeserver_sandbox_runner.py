import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "homeserver" / "run-sandbox-job.py"


def _module():
    spec = importlib.util.spec_from_file_location("homeserver_sandbox_runner", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_homeserver_sandbox_runner_accepts_expected_podman_shapes():
    runner = _module()

    assert runner._validated_argv(["podman", "pod", "create", "--name", "odysseus-agent-demo", "--network", "none"])
    assert runner._validated_argv(["podman", "pod", "rm", "-f", "odysseus-agent-demo"])
    assert runner._validated_argv(
        [
            "podman",
            "run",
            "--rm",
            "--pod",
            "odysseus-agent-demo",
            "--memory",
            "512m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "256",
            "--security-opt",
            "no-new-privileges",
            "python:3.14-slim",
            "--version",
        ]
    )


def test_homeserver_sandbox_runner_rejects_privileged_or_socket_commands():
    runner = _module()

    with pytest.raises(ValueError):
        runner._validated_argv(["podman", "run", "--privileged", "alpine"])
    with pytest.raises(ValueError):
        runner._validated_argv(["podman", "run", "--mount", "type=bind,src=/run/podman/podman.sock,dst=/workspace/sock,ro", "alpine"])
    with pytest.raises(ValueError):
        runner._validated_argv(["bash", "-lc", "id"])


def test_homeserver_sandbox_runner_normalizes_repo_relative_mounts_only():
    runner = _module()

    mount = runner._normalize_mount("type=bind,src=data/project,dst=/workspace/project,rw")

    assert "src=/opt/odysseus/data/project" in mount
    assert "dst=/workspace/project" in mount
    with pytest.raises(ValueError):
        runner._normalize_mount("type=bind,src=../secret,dst=/workspace/secret,ro")
    with pytest.raises(ValueError):
        runner._normalize_mount("type=bind,src=data,dst=/host/data,ro")
