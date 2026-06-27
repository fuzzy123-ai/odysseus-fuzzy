import json
from pathlib import Path

import pytest

from src.server_project_chat_context import (
    ServerProjectChatContextError,
    apply_project_chat_metadata,
    bind_project_chat_session,
    resolve_project_chat_context,
)
from src.server_project_registry import ServerProjectRegistry


def _registry() -> ServerProjectRegistry:
    registry = ServerProjectRegistry()
    registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )
    registry.create_project(
        project_title="Marketing Site",
        project_type="website",
        created_at="2026-06-27T10:00:00Z",
    )
    return registry


def test_bind_project_chat_session_returns_context_and_updates_registry():
    registry = _registry()

    context = bind_project_chat_session(
        registry=registry,
        project_slug="kundenportal-mvp",
        session_id="chat-kundenportal-1",
        updated_at="2026-06-27T10:05:00Z",
    )

    assert context.project_slug == "kundenportal-mvp"
    assert context.chat_scope == "project:kundenportal-mvp"
    assert context.repo_name == "kundenportal-mvp"
    assert context.workspace_root == "projects/kundenportal-mvp"
    assert registry.get("kundenportal-mvp").chat_session_ids == ("chat-kundenportal-1",)


def test_apply_project_chat_metadata_adds_project_scope_without_overwriting_unrelated_metadata():
    registry = _registry()
    context = bind_project_chat_session(
        registry=registry,
        project_slug="kundenportal-mvp",
        session_id="chat-kundenportal-1",
        updated_at="2026-06-27T10:05:00Z",
    )

    metadata = apply_project_chat_metadata(
        message_metadata={"source": "user", "turn": "1"},
        context=context,
    )

    assert metadata["source"] == "user"
    assert metadata["project_slug"] == "kundenportal-mvp"
    assert metadata["chat_scope"] == "project:kundenportal-mvp"
    assert metadata["session_id"] == "chat-kundenportal-1"
    assert metadata["schema"] == "odysseus.server_project_chat_context.v1"


def test_cross_project_message_metadata_is_rejected():
    registry = _registry()
    context = bind_project_chat_session(
        registry=registry,
        project_slug="kundenportal-mvp",
        session_id="chat-kundenportal-1",
        updated_at="2026-06-27T10:05:00Z",
    )

    with pytest.raises(ServerProjectChatContextError, match="different project chat scope"):
        apply_project_chat_metadata(
            message_metadata={"chat_scope": "project:marketing-site"},
            context=context,
        )


def test_resolve_project_chat_context_requires_session_attachment():
    registry = _registry()
    bind_project_chat_session(
        registry=registry,
        project_slug="kundenportal-mvp",
        session_id="chat-kundenportal-1",
        updated_at="2026-06-27T10:05:00Z",
    )

    resolved = resolve_project_chat_context(
        registry=registry,
        chat_scope="project:kundenportal-mvp",
        session_id="chat-kundenportal-1",
    )

    assert resolved.project_slug == "kundenportal-mvp"
    with pytest.raises(ServerProjectChatContextError, match="not attached"):
        resolve_project_chat_context(
            registry=registry,
            chat_scope="project:kundenportal-mvp",
            session_id="chat-other",
        )


def test_unknown_project_or_scope_is_rejected():
    registry = _registry()

    with pytest.raises(ServerProjectChatContextError, match="unknown project"):
        bind_project_chat_session(
            registry=registry,
            project_slug="missing",
            session_id="chat-1",
            updated_at="2026-06-27T10:05:00Z",
        )

    with pytest.raises(ServerProjectChatContextError, match="unknown chat scope"):
        resolve_project_chat_context(
            registry=registry,
            chat_scope="project:missing",
            session_id="chat-1",
        )


def test_context_audit_summary_omits_raw_session_id():
    registry = _registry()
    context = bind_project_chat_session(
        registry=registry,
        project_slug="kundenportal-mvp",
        session_id="chat-kundenportal-1",
        updated_at="2026-06-27T10:05:00Z",
    )

    summary = context.audit_summary()

    assert summary["session_bound"] is True
    assert "chat-kundenportal-1" not in json.dumps(summary)


def test_rejects_secret_like_or_absolute_path_inputs():
    registry = _registry()

    with pytest.raises(ServerProjectChatContextError, match="secret material"):
        bind_project_chat_session(
            registry=registry,
            project_slug="kundenportal-mvp",
            session_id="token=abc123",
            updated_at="2026-06-27T10:05:00Z",
        )

    with pytest.raises(ServerProjectChatContextError, match="absolute paths"):
        bind_project_chat_session(
            registry=registry,
            project_slug=r"D:\Sensitive\Project",
            session_id="chat-1",
            updated_at="2026-06-27T10:05:00Z",
        )


def test_source_has_no_live_runtime_dependencies():
    source = Path("src/server_project_chat_context.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "podman", "docker", "systemctl")
    for fragment in forbidden:
        assert fragment not in source
