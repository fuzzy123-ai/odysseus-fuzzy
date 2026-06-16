"""Small backend contract for agent identity and scope namespacing.

This module is intentionally runtime-agnostic. It provides a normalized,
validated identity object that later slices can pass through run state,
context-capsule payloads, and namespaced memory/workspace stores.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_MAX_FIELD_LENGTH = 80
_INVALID_PATH_BITS = ("..", "/", "\\", ":")
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class AgentIdentityError(ValueError):
    """Raised when an agent identity field cannot be normalized safely."""


def _normalize_slug(value: str, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise AgentIdentityError(f"{field_name} must not be empty")
    if len(raw) > _MAX_FIELD_LENGTH:
        raise AgentIdentityError(f"{field_name} exceeds max length {_MAX_FIELD_LENGTH}")
    if any(token in raw for token in _INVALID_PATH_BITS):
        raise AgentIdentityError(f"{field_name} must not contain path-like segments")

    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise AgentIdentityError(f"{field_name} must contain at least one slug character")
    if len(normalized) > _MAX_FIELD_LENGTH:
        raise AgentIdentityError(f"{field_name} exceeds max length {_MAX_FIELD_LENGTH} after normalization")
    return normalized


def _build_scope_key(**parts: str) -> str:
    return "|".join(f"{name}:{value}" for name, value in parts.items())


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    agent_id: str
    role_id: str
    project_id: str
    memory_scope: str
    workspace_scope: str
    run_id: str

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        role_id: str,
        project_id: str,
        memory_scope: str,
        workspace_scope: str,
        run_id: str,
    ) -> "AgentIdentity":
        return cls(
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            role_id=_normalize_slug(role_id, field_name="role_id"),
            project_id=_normalize_slug(project_id, field_name="project_id"),
            memory_scope=_normalize_slug(memory_scope, field_name="memory_scope"),
            workspace_scope=_normalize_slug(workspace_scope, field_name="workspace_scope"),
            run_id=_normalize_slug(run_id, field_name="run_id"),
        )

    def identity_key(self) -> str:
        return _build_scope_key(
            project=self.project_id,
            agent=self.agent_id,
            role=self.role_id,
            run=self.run_id,
        )

    def memory_scope_key(self) -> str:
        return _build_scope_key(
            project=self.project_id,
            memory=self.memory_scope,
            agent=self.agent_id,
            role=self.role_id,
        )

    def workspace_scope_key(self) -> str:
        return _build_scope_key(
            project=self.project_id,
            workspace=self.workspace_scope,
            agent=self.agent_id,
            role=self.role_id,
        )


def scope_key_for(identity: AgentIdentity, *, scope_kind: str) -> str:
    kind = _normalize_slug(scope_kind, field_name="scope_kind")
    if kind == "memory":
        return identity.memory_scope_key()
    if kind == "workspace":
        return identity.workspace_scope_key()
    if kind == "identity":
        return identity.identity_key()
    raise AgentIdentityError(f"unsupported scope_kind: {scope_kind}")
