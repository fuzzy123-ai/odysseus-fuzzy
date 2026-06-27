"""Project-scoped chat context helpers for the universal project runner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.server_project_registry import (
    ServerProjectRecord,
    ServerProjectRegistry,
    ServerProjectRegistryError,
)


_CONTEXT_SCHEMA = "odysseus.server_project_chat_context.v1"
_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class ServerProjectChatContextError(ValueError):
    """Raised when a chat session cannot be safely bound to a project."""


def _normalize_text(value: Any, *, field_name: str, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ServerProjectChatContextError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectChatContextError(f"{field_name} exceeds max length {max_len}")
    lowered = text.lower()
    if any(token in lowered for token in ("token=", "secret=", "password=", "api_key=", "bearer ")):
        raise ServerProjectChatContextError(f"{field_name} appears to contain secret material")
    if re.search(r"[A-Za-z]:\\", text) or text.startswith("/"):
        raise ServerProjectChatContextError(f"{field_name} must not contain host-local absolute paths")
    return text


def _normalize_session_id(value: Any) -> str:
    session_id = _normalize_text(value, field_name="session_id", max_len=160)
    if not _SESSION_RE.fullmatch(session_id):
        raise ServerProjectChatContextError("session_id contains unsupported characters")
    return session_id


def _metadata_dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ServerProjectChatContextError("metadata must be a mapping")
    return dict(value)


@dataclass(frozen=True, slots=True)
class ServerProjectChatContext:
    session_id: str
    project_slug: str
    chat_scope: str
    repo_name: str
    workspace_root: str
    project_type: str
    runner_state: str

    @classmethod
    def create(cls, *, session_id: Any, record: ServerProjectRecord) -> "ServerProjectChatContext":
        if not isinstance(record, ServerProjectRecord):
            raise ServerProjectChatContextError("record must be a ServerProjectRecord")
        return cls(
            session_id=_normalize_session_id(session_id),
            project_slug=record.project_slug,
            chat_scope=record.chat_scope,
            repo_name=record.project_spec.repo_name,
            workspace_root=record.project_spec.workspace_root,
            project_type=record.project_spec.project_type,
            runner_state=record.runner_state,
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "schema": _CONTEXT_SCHEMA,
            "project_slug": self.project_slug,
            "chat_scope": self.chat_scope,
            "repo_name": self.repo_name,
            "workspace_root": self.workspace_root,
            "project_type": self.project_type,
            "runner_state": self.runner_state,
        }

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema": _CONTEXT_SCHEMA,
            "project_slug": self.project_slug,
            "chat_scope": self.chat_scope,
            "repo_name": self.repo_name,
            "workspace_root": self.workspace_root,
            "project_type": self.project_type,
            "runner_state": self.runner_state,
            "session_bound": True,
        }


def bind_project_chat_session(
    *,
    registry: ServerProjectRegistry,
    project_slug: Any,
    session_id: Any,
    updated_at: Any,
) -> ServerProjectChatContext:
    if not isinstance(registry, ServerProjectRegistry):
        raise ServerProjectChatContextError("registry must be a ServerProjectRegistry")
    try:
        record = registry.attach_chat_session(
            project_slug=_normalize_text(project_slug, field_name="project_slug", max_len=80),
            session_id=session_id,
            updated_at=updated_at,
        )
    except ServerProjectRegistryError as exc:
        raise ServerProjectChatContextError(str(exc)) from exc
    return ServerProjectChatContext.create(session_id=session_id, record=record)


def apply_project_chat_metadata(
    *,
    message_metadata: Mapping[str, Any] | None,
    context: ServerProjectChatContext,
) -> dict[str, Any]:
    if not isinstance(context, ServerProjectChatContext):
        raise ServerProjectChatContextError("context must be a ServerProjectChatContext")
    metadata = _metadata_dict(message_metadata)
    existing_scope = metadata.get("chat_scope")
    existing_project = metadata.get("project_slug")
    if existing_scope and existing_scope != context.chat_scope:
        raise ServerProjectChatContextError("message metadata already belongs to a different project chat scope")
    if existing_project and existing_project != context.project_slug:
        raise ServerProjectChatContextError("message metadata already belongs to a different project")
    metadata.update(context.metadata())
    metadata["session_id"] = context.session_id
    return metadata


def resolve_project_chat_context(
    *,
    registry: ServerProjectRegistry,
    chat_scope: Any,
    session_id: Any,
) -> ServerProjectChatContext:
    if not isinstance(registry, ServerProjectRegistry):
        raise ServerProjectChatContextError("registry must be a ServerProjectRegistry")
    scope = _normalize_text(chat_scope, field_name="chat_scope", max_len=120)
    try:
        record = registry.resolve_chat_scope(scope)
    except ServerProjectRegistryError as exc:
        raise ServerProjectChatContextError(str(exc)) from exc
    normalized_session = _normalize_session_id(session_id)
    if normalized_session not in record.chat_session_ids:
        raise ServerProjectChatContextError("session is not attached to the requested project chat scope")
    return ServerProjectChatContext.create(session_id=normalized_session, record=record)
