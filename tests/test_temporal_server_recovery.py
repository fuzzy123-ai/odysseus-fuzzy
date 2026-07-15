from __future__ import annotations

from pathlib import Path

from src.temporal_runtime.config import load_temporal_light_config


def _windows_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "LOCALAPPDATA": str(tmp_path / "local-app-data"),
        "ODYSSEUS_TEMPORAL_HOST": "127.0.0.1",
        "ODYSSEUS_TEMPORAL_PORT": "7233",
    }


def test_restart_contract_reuses_exact_external_database_and_identity(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    environment = _windows_environment(tmp_path)
    before = load_temporal_light_config(
        environment, repo_root=repo, platform_name="win32"
    )
    before.runtime_dir.mkdir(parents=True)
    before.db_path.write_bytes(b"durable-temporal-state")

    after = load_temporal_light_config(
        environment, repo_root=repo, platform_name="win32"
    )

    assert after.db_path == before.db_path
    assert after.db_path.read_bytes() == b"durable-temporal-state"
    assert after.start_command() == before.start_command()
    assert after.address == "127.0.0.1:7233"
    assert after.namespace == "default"
    assert after.task_queue == "odysseus-temporal-light"
    assert after.development_only is True
    assert str(repo) not in str(after.db_path)
    assert after.start_command()[after.start_command().index("--db-filename") + 1] == str(
        after.db_path
    )


def test_public_recovery_descriptor_is_stable_and_redacts_host_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = load_temporal_light_config(
        _windows_environment(tmp_path), repo_root=repo, platform_name="win32"
    )

    first = config.public_descriptor()
    second = load_temporal_light_config(
        _windows_environment(tmp_path), repo_root=repo, platform_name="win32"
    ).public_descriptor()

    assert first == second
    assert first["schema_id"] == "odysseus.temporal_light.config.v1"
    assert first["address"] == "127.0.0.1:7233"
    assert first["task_queue"] == "odysseus-temporal-light"
    assert str(tmp_path) not in repr(first)
