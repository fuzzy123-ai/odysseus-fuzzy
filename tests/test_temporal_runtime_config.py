from __future__ import annotations

import importlib.metadata
from pathlib import Path
import subprocess

import pytest

from src.temporal_runtime.config import (
    CLI_VERSION,
    SDK_VERSION,
    TemporalLightConfigError,
    assert_installed_capabilities,
    check_temporal_health,
    load_temporal_light_config,
)


def _windows_env(tmp_path: Path) -> dict[str, str]:
    return {"LOCALAPPDATA": str(tmp_path / "LocalAppData")}


def _config(tmp_path: Path, **overrides: str):
    env = _windows_env(tmp_path)
    env.update(overrides)
    return load_temporal_light_config(
        env, repo_root=tmp_path / "repo", platform_name="win32"
    )


def test_sdk_dependency_is_exactly_pinned_and_importable():
    assert importlib.metadata.version("temporalio") == SDK_VERSION == "1.30.0"


def test_default_config_is_loopback_headless_and_outside_repo(tmp_path):
    config = _config(tmp_path)

    assert config.address == "127.0.0.1:7233"
    assert config.namespace == "default"
    assert config.task_queue == "odysseus-temporal-light"
    assert config.headless is True
    assert config.development_only is True
    assert config.db_path.name == "temporal.db"
    assert config.repo_root not in config.runtime_dir.parents


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "::1", "localhost", "10.0.0.5"])
def test_non_exact_loopback_host_is_rejected(tmp_path, host):
    with pytest.raises(TemporalLightConfigError, match="bind exactly"):
        _config(tmp_path, ODYSSEUS_TEMPORAL_HOST=host)


@pytest.mark.parametrize("port", ["0", "7000", "8233", "not-a-port"])
def test_nonstandard_or_invalid_port_is_rejected(tmp_path, port):
    with pytest.raises(TemporalLightConfigError, match="port"):
        _config(tmp_path, ODYSSEUS_TEMPORAL_PORT=port)


def test_runtime_persistence_inside_repository_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    env = _windows_env(tmp_path)
    env["ODYSSEUS_TEMPORAL_RUNTIME_DIR"] = str(repo / ".runtime" / "temporal")

    with pytest.raises(TemporalLightConfigError, match="outside the repository"):
        load_temporal_light_config(env, repo_root=repo, platform_name="win32")


def test_start_command_is_persistent_headless_and_local(tmp_path):
    config = _config(tmp_path)
    command = config.start_command()

    assert command[1:3] == ("server", "start-dev")
    assert command[command.index("--ip") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "7233"
    assert command[command.index("--db-filename") + 1] == str(config.db_path)
    assert "--headless" in command
    assert "--ui-port" not in command


def test_health_check_uses_the_locked_address(tmp_path):
    config = _config(tmp_path)
    seen = []

    def runner(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, "temporal.api.workflowservice.v1: SERVING\n", ""
        )

    health = check_temporal_health(config, runner=runner)

    assert health.healthy is True
    assert health.address == "127.0.0.1:7233"
    assert seen[0][0] == list(config.health_command())
    assert seen[0][1]["timeout"] == 10


def test_health_check_fails_closed_on_cli_error(tmp_path):
    config = _config(tmp_path)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "connection refused")

    with pytest.raises(TemporalLightConfigError, match="connection refused"):
        check_temporal_health(config, runner=runner)


def test_capability_check_requires_the_exact_cli_version(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.cli_path.parent.mkdir(parents=True)
    config.cli_path.touch()
    monkeypatch.setattr(importlib.metadata, "version", lambda name: SDK_VERSION)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "temporal version 1.7.3", "")

    with pytest.raises(TemporalLightConfigError, match=f"CLI {CLI_VERSION}"):
        assert_installed_capabilities(config, runner=runner)


def test_capability_check_accepts_the_pinned_versions(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.cli_path.parent.mkdir(parents=True)
    config.cli_path.touch()
    monkeypatch.setattr(importlib.metadata, "version", lambda name: SDK_VERSION)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, f"temporal version {CLI_VERSION} (Server 1.31.2)", ""
        )

    assert_installed_capabilities(config, runner=runner)


def test_public_descriptor_redacts_private_host_paths(tmp_path):
    config = _config(tmp_path)
    descriptor = config.public_descriptor()

    assert str(tmp_path) not in str(descriptor)
    assert descriptor["development_only"] is True
    assert descriptor["address"] == "127.0.0.1:7233"


def test_repository_artifacts_lock_the_same_nonproduction_contract():
    repo = Path(__file__).resolve().parents[1]
    requirements = (repo / "requirements.txt").read_text(encoding="utf-8")
    powershell = (repo / "scripts" / "run_temporal_light.ps1").read_text(encoding="utf-8")
    shell = (repo / "scripts" / "run_temporal_light.sh").read_text(encoding="utf-8")
    runbook = (
        repo / "docs" / "plans" / "temporal-light-local-runbook.md"
    ).read_text(encoding="utf-8")

    assert requirements.count(f"temporalio=={SDK_VERSION}") == 1
    assert "src.temporal_runtime.config" in powershell
    assert "src.temporal_runtime.config" in shell
    assert "127.0.0.1:7233" in runbook
    assert "ausschließlich lokale Entwicklung" in runbook
    assert "Produktion" in runbook


def test_package_exports_are_lazy_but_available():
    import src.temporal_runtime as runtime

    assert runtime.SDK_VERSION == SDK_VERSION
    assert runtime.TemporalLightConfigError is TemporalLightConfigError
