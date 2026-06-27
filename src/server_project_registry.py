"""Persistent registry for universal server-side projects."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from core.atomic_io import atomic_write_json
from src.server_project_runner import UniversalProjectSpec, build_server_project_runner_plan


_SCHEMA_VERSION = 1
_STATUSES = ("planning", "active", "paused", "archived")
_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class ServerProjectRegistryError(ValueError):
    """Raised when project registry payloads or operations are invalid."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ServerProjectRegistryError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectRegistryError(f"{field_name} exceeds max length {max_len}")
    _reject_sensitive_text(text, field_name=field_name)
    return text


def _normalize_status(value: Any) -> str:
    status = _normalize_text(value, field_name="status").lower().replace("-", "_")
    if status not in _STATUSES:
        raise ServerProjectRegistryError(f"unsupported project status: {value!r}")
    return status


def _reject_sensitive_text(value: str, *, field_name: str) -> None:
    lowered = value.lower()
    if any(token in lowered for token in ("token=", "secret=", "password=", "api_key=", "bearer ")):
        raise ServerProjectRegistryError(f"{field_name} appears to contain secret material")
    if re.search(r"[A-Za-z]:\\", value) or value.startswith("/"):
        raise ServerProjectRegistryError(f"{field_name} must not contain host-local absolute paths")


def _normalize_session_id(value: Any) -> str:
    session_id = _normalize_text(value, field_name="session_id", max_len=160)
    if not _SESSION_RE.fullmatch(session_id):
        raise ServerProjectRegistryError("session_id contains unsupported characters")
    return session_id


def _dedupe_sessions(values: Iterable[Any]) -> tuple[str, ...]:
    sessions: list[str] = []
    for value in values:
        session_id = _normalize_session_id(value)
        if session_id not in sessions:
            sessions.append(session_id)
    return tuple(sessions)


def _validate_project_spec(spec: UniversalProjectSpec) -> UniversalProjectSpec:
    if not isinstance(spec, UniversalProjectSpec):
        raise ServerProjectRegistryError("project_spec must be a UniversalProjectSpec")
    if spec.repo_name in {"odysseus", "odysseus-fuzzy"}:
        raise ServerProjectRegistryError("project repository must not default to Odysseus")
    if not spec.workspace_root.startswith(f"projects/{spec.project_slug}"):
        raise ServerProjectRegistryError("workspace_root must stay below projects/<project-slug>")
    if spec.chat_scope != f"project:{spec.project_slug}":
        raise ServerProjectRegistryError("chat_scope must match project:<project-slug>")
    return spec


@dataclass(frozen=True, slots=True)
class ServerProjectRecord:
    project_spec: UniversalProjectSpec
    status: str
    created_at: str
    updated_at: str
    chat_session_ids: tuple[str, ...]
    runner_state: str
    next_human_decision: str

    @classmethod
    def create(
        cls,
        *,
        project_spec: UniversalProjectSpec,
        status: Any = "planning",
        created_at: Any,
        updated_at: Any | None = None,
        chat_session_ids: Iterable[Any] = (),
        runner_state: Any = "registry_only",
        next_human_decision: Any = "Open planning chat or attach a project session.",
    ) -> "ServerProjectRecord":
        spec = _validate_project_spec(project_spec)
        created = _normalize_text(created_at, field_name="created_at", max_len=40)
        updated = _normalize_text(updated_at if updated_at is not None else created, field_name="updated_at", max_len=40)
        return cls(
            project_spec=spec,
            status=_normalize_status(status),
            created_at=created,
            updated_at=updated,
            chat_session_ids=_dedupe_sessions(chat_session_ids),
            runner_state=_normalize_text(runner_state, field_name="runner_state", max_len=80),
            next_human_decision=_normalize_text(
                next_human_decision,
                field_name="next_human_decision",
                allow_empty=True,
                max_len=260,
            ),
        )

    @property
    def project_slug(self) -> str:
        return self.project_spec.project_slug

    @property
    def chat_scope(self) -> str:
        return self.project_spec.chat_scope

    def with_chat_session(self, session_id: Any, *, updated_at: Any) -> "ServerProjectRecord":
        sessions = list(self.chat_session_ids)
        normalized_session = _normalize_session_id(session_id)
        if normalized_session not in sessions:
            sessions.append(normalized_session)
        return ServerProjectRecord.create(
            project_spec=self.project_spec,
            status=self.status,
            created_at=self.created_at,
            updated_at=updated_at,
            chat_session_ids=sessions,
            runner_state=self.runner_state,
            next_human_decision=self.next_human_decision,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_spec": self.project_spec.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "chat_session_ids": list(self.chat_session_ids),
            "runner_state": self.runner_state,
            "next_human_decision": self.next_human_decision,
        }


@dataclass(slots=True)
class ServerProjectRegistry:
    projects: dict[str, ServerProjectRecord] = field(default_factory=dict)

    def create_project(
        self,
        *,
        project_title: Any,
        project_type: Any = "generic",
        created_at: Any,
        repo_name: Any | None = None,
        cloudflare_tunnel_requested: bool = False,
    ) -> ServerProjectRecord:
        _normalize_text(project_title, field_name="project_title")
        if repo_name is not None:
            _normalize_text(repo_name, field_name="repo_name")
        plan = build_server_project_runner_plan(
            project_title=project_title,
            project_type=project_type,
            repo_name=repo_name,
            cloudflare_tunnel_requested=cloudflare_tunnel_requested,
        )
        record = ServerProjectRecord.create(
            project_spec=plan.project_spec,
            created_at=created_at,
            runner_state="registry_only",
            next_human_decision=plan.next_human_decision,
        )
        self.add(record)
        return record

    def add(self, record: ServerProjectRecord) -> None:
        if not isinstance(record, ServerProjectRecord):
            raise ServerProjectRegistryError("record must be a ServerProjectRecord")
        if record.project_slug in self.projects:
            raise ServerProjectRegistryError(f"project already exists: {record.project_slug}")
        if any(existing.chat_scope == record.chat_scope for existing in self.projects.values()):
            raise ServerProjectRegistryError(f"chat scope already exists: {record.chat_scope}")
        self.projects[record.project_slug] = record

    def get(self, project_slug: str) -> ServerProjectRecord:
        slug = _normalize_text(project_slug, field_name="project_slug", max_len=80)
        try:
            return self.projects[slug]
        except KeyError as exc:
            raise ServerProjectRegistryError(f"unknown project: {slug}") from exc

    def resolve_chat_scope(self, chat_scope: str) -> ServerProjectRecord:
        scope = _normalize_text(chat_scope, field_name="chat_scope", max_len=120)
        for record in self.projects.values():
            if record.chat_scope == scope:
                return record
        raise ServerProjectRegistryError(f"unknown chat scope: {scope}")

    def attach_chat_session(self, *, project_slug: str, session_id: Any, updated_at: Any) -> ServerProjectRecord:
        record = self.get(project_slug)
        updated = record.with_chat_session(session_id, updated_at=updated_at)
        self.projects[record.project_slug] = updated
        return updated

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "project_count": len(self.projects),
            "projects": [
                {
                    "project_slug": record.project_slug,
                    "repo_name": record.project_spec.repo_name,
                    "workspace_root": record.project_spec.workspace_root,
                    "chat_scope": record.chat_scope,
                    "status": record.status,
                    "chat_session_count": len(record.chat_session_ids),
                    "cloudflare_tunnel_requested": record.project_spec.cloudflare_tunnel_requested,
                }
                for record in self._sorted_records()
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "projects": [record.to_dict() for record in self._sorted_records()],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ServerProjectRegistry":
        if not isinstance(payload, dict):
            raise ServerProjectRegistryError("payload must be a dict")
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise ServerProjectRegistryError(f"schema_version must be {_SCHEMA_VERSION}")
        registry = cls()
        for raw_record in _list(payload.get("projects"), field_name="projects"):
            registry.add(_record_from_dict(raw_record))
        return registry

    def save_json(self, path: str | Path) -> None:
        atomic_write_json(str(path), self.to_dict(), indent=2)

    @classmethod
    def load_json(cls, path: str | Path) -> "ServerProjectRegistry":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def _sorted_records(self) -> tuple[ServerProjectRecord, ...]:
        return tuple(self.projects[key] for key in sorted(self.projects))


def _record_from_dict(payload: dict[str, Any]) -> ServerProjectRecord:
    if not isinstance(payload, dict):
        raise ServerProjectRegistryError("project record must be a dict")
    spec = _project_spec_from_dict(_required(payload, "project_spec"))
    return ServerProjectRecord.create(
        project_spec=spec,
        status=_required(payload, "status"),
        created_at=_required(payload, "created_at"),
        updated_at=_required(payload, "updated_at"),
        chat_session_ids=_list(payload.get("chat_session_ids", []), field_name="chat_session_ids"),
        runner_state=payload.get("runner_state", "registry_only"),
        next_human_decision=payload.get("next_human_decision", ""),
    )


def _project_spec_from_dict(payload: dict[str, Any]) -> UniversalProjectSpec:
    if not isinstance(payload, dict):
        raise ServerProjectRegistryError("project_spec must be a dict")
    return UniversalProjectSpec(
        project_title=_required(payload, "project_title"),
        project_slug=_required(payload, "project_slug"),
        project_type=_required(payload, "project_type"),
        repo_name=_required(payload, "repo_name"),
        workspace_root=_required(payload, "workspace_root"),
        chat_scope=_required(payload, "chat_scope"),
        default_branch=_required(payload, "default_branch"),
        cloudflare_tunnel_requested=bool(payload.get("cloudflare_tunnel_requested", False)),
        cloudflare_tunnel_gate=_required(payload, "cloudflare_tunnel_gate"),
    )


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ServerProjectRegistryError(f"missing required field: {key}")
    return payload[key]


def _list(value: Any, *, field_name: str) -> list[Any]:
    if value is None or not isinstance(value, list):
        raise ServerProjectRegistryError(f"{field_name} must be a list")
    return value
