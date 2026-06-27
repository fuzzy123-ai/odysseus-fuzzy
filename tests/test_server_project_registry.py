import json
from pathlib import Path

import pytest

from src.server_project_registry import (
    ServerProjectRecord,
    ServerProjectRegistry,
    ServerProjectRegistryError,
)
from src.server_project_runner import build_server_project_runner_plan


def test_registry_creates_project_from_title_with_chat_scope_and_workspace():
    registry = ServerProjectRegistry()
    record = registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )

    assert record.project_slug == "kundenportal-mvp"
    assert record.project_spec.repo_name == "kundenportal-mvp"
    assert record.project_spec.workspace_root == "projects/kundenportal-mvp"
    assert record.chat_scope == "project:kundenportal-mvp"
    assert record.status == "planning"
    assert registry.resolve_chat_scope("project:kundenportal-mvp") == record


def test_attach_chat_session_is_idempotent_and_updates_timestamp():
    registry = ServerProjectRegistry()
    registry.create_project(project_title="Demo App", created_at="2026-06-27T10:00:00Z")

    first = registry.attach_chat_session(
        project_slug="demo-app",
        session_id="session-1",
        updated_at="2026-06-27T10:01:00Z",
    )
    second = registry.attach_chat_session(
        project_slug="demo-app",
        session_id="session-1",
        updated_at="2026-06-27T10:02:00Z",
    )

    assert first.chat_session_ids == ("session-1",)
    assert second.chat_session_ids == ("session-1",)
    assert second.updated_at == "2026-06-27T10:02:00Z"


def test_registry_roundtrips_through_atomic_json(tmp_path: Path):
    registry = ServerProjectRegistry()
    registry.create_project(
        project_title="Public Demo",
        project_type="website",
        cloudflare_tunnel_requested=True,
        created_at="2026-06-27T10:00:00Z",
    )
    registry.attach_chat_session(
        project_slug="public-demo",
        session_id="chat.public-demo.1",
        updated_at="2026-06-27T10:05:00Z",
    )

    path = tmp_path / "project-registry.json"
    registry.save_json(path)
    loaded = ServerProjectRegistry.load_json(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert loaded.get("public-demo").project_spec.cloudflare_tunnel_requested is True
    assert loaded.get("public-demo").chat_session_ids == ("chat.public-demo.1",)


def test_duplicate_project_slug_is_rejected():
    registry = ServerProjectRegistry()
    registry.create_project(project_title="Demo App", created_at="2026-06-27T10:00:00Z")

    with pytest.raises(ServerProjectRegistryError, match="project already exists"):
        registry.create_project(project_title="Demo App", created_at="2026-06-27T10:01:00Z")


def test_registry_rejects_odysseus_repo_as_universal_project_default():
    registry = ServerProjectRegistry()

    with pytest.raises(ServerProjectRegistryError, match="Odysseus"):
        registry.create_project(
            project_title="Not Odysseus",
            repo_name="odysseus",
            created_at="2026-06-27T10:00:00Z",
        )


def test_registry_rejects_chat_scope_outside_project_scope():
    plan = build_server_project_runner_plan(
        project_title="Demo App",
        chat_scope="global",
    )

    with pytest.raises(ServerProjectRegistryError, match="chat_scope"):
        ServerProjectRecord.create(
            project_spec=plan.project_spec,
            created_at="2026-06-27T10:00:00Z",
        )


def test_registry_rejects_absolute_host_paths_and_secret_like_text():
    registry = ServerProjectRegistry()

    with pytest.raises(ServerProjectRegistryError, match="absolute paths"):
        registry.create_project(
            project_title=r"D:\Sensitive\Private Project",
            created_at="2026-06-27T10:00:00Z",
        )

    with pytest.raises(ServerProjectRegistryError, match="secret material"):
        registry.create_project(
            project_title="Secret TOKEN=abc123 Project",
            created_at="2026-06-27T10:00:00Z",
        )


def test_audit_summary_is_sorted_and_contains_no_raw_session_ids():
    registry = ServerProjectRegistry()
    registry.create_project(project_title="Zeta", created_at="2026-06-27T10:00:00Z")
    registry.create_project(project_title="Alpha", created_at="2026-06-27T10:00:00Z")
    registry.attach_chat_session(project_slug="alpha", session_id="chat-alpha-1", updated_at="2026-06-27T10:01:00Z")

    summary = registry.audit_summary()

    assert [item["project_slug"] for item in summary["projects"]] == ["alpha", "zeta"]
    assert summary["projects"][0]["chat_session_count"] == 1
    assert "chat-alpha-1" not in json.dumps(summary)


def test_source_uses_atomic_json_and_no_live_runtime():
    source = Path("src/server_project_registry.py").read_text(encoding="utf-8")

    assert "atomic_write_json" in source
    forbidden = ("subprocess", "requests", "httpx", "paramiko", "podman", "docker", "systemctl")
    for fragment in forbidden:
        assert fragment not in source
