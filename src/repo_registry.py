"""Persistent, repo-only registry for future Git control features."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from core.atomic_io import atomic_write_json
from src.constants import DATA_DIR


REPO_REGISTRY_FILE = str(Path(DATA_DIR) / "repo_registry.json")
_SCHEMA_VERSION = 1

_REPO_KINDS = ("odysseus", "project", "user", "external")
_PRIVACY_CLASSES = ("public", "private", "sensitive")
_PROVIDER_SCOPES = ("default", "local_only", "external_allowed")
_REMOTE_PURPOSES = ("origin", "fork", "mirror", "deploy", "backup", "other")
_PUSH_POLICIES = ("read_only", "push_allowed", "blocked")
_DEFAULT_ALLOWED_ACTIONS = ("status", "log", "diff_stat", "changed_paths", "remotes")
_ALLOWED_ACTIONS = _DEFAULT_ALLOWED_ACTIONS + (
    "register",
    "forget",
    "update_policy",
    "branch",
    "commit_plan",
    "commit",
    "push_plan",
    "push",
)

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,160}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SCP_LIKE_REMOTE_RE = re.compile(r"^(?P<user>[A-Za-z0-9._~+-]+)@(?P<target>[^:\s]+:.+)$")


class RepoRegistryError(ValueError):
    """Raised when repo registry payloads or operations are invalid."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise RepoRegistryError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise RepoRegistryError(f"{field_name} exceeds max length {max_len}")
    _reject_sensitive_text(text, field_name=field_name)
    return text


def _normalize_slug(value: Any, *, field_name: str, fallback: Any | None = None) -> str:
    raw = _normalize_text(value if value not in (None, "") else fallback, field_name=field_name, max_len=160)
    slug = _SLUG_RE.sub("-", raw.lower())
    slug = "-".join(part for part in slug.strip("-._").split("-") if part)
    if not slug:
        raise RepoRegistryError(f"{field_name} must produce a non-empty slug")
    if not _SAFE_NAME_RE.fullmatch(slug):
        raise RepoRegistryError(f"{field_name} contains unsupported characters")
    return slug[:80]


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name, max_len=80).lower().replace("-", "_")
    if text not in choices:
        raise RepoRegistryError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_branch(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    branch = _normalize_text(value, field_name=field_name, allow_empty=allow_empty, max_len=160)
    if allow_empty and not branch:
        return ""
    if (
        not _BRANCH_RE.fullmatch(branch)
        or branch.startswith(("/", "."))
        or branch.endswith("/")
        or ".." in branch
        or "//" in branch
        or "@{" in branch
    ):
        raise RepoRegistryError(f"{field_name} contains unsupported branch syntax")
    return branch


def _normalize_path_ref(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    path = str(value or "").strip().replace("\\", "/")
    if not path:
        if allow_empty:
            return ""
        raise RepoRegistryError(f"{field_name} must not be empty")
    _reject_sensitive_text(path, field_name=field_name)
    if "\x00" in path or path.startswith(("~", "//")) or _WINDOWS_ABSOLUTE_RE.match(path) or path.startswith("/"):
        raise RepoRegistryError(f"{field_name} must not contain host-local absolute paths")
    normalized = posixpath.normpath(path)
    if normalized in ("", ".") or normalized == ".." or normalized.startswith("../"):
        raise RepoRegistryError(f"{field_name} must stay inside its declared workspace")
    return normalized


def _dedupe_actions(values: Iterable[Any] | None) -> tuple[str, ...]:
    if values is None:
        return _DEFAULT_ALLOWED_ACTIONS
    result: list[str] = []
    for value in values:
        action = _normalize_choice(value, field_name="allowed_actions", choices=_ALLOWED_ACTIONS)
        if action not in result:
            result.append(action)
    return tuple(result) or _DEFAULT_ALLOWED_ACTIONS


def _dedupe_remotes(values: Iterable["RepoRemote"]) -> tuple["RepoRemote", ...]:
    result: list[RepoRemote] = []
    names: set[str] = set()
    for remote in values:
        if not isinstance(remote, RepoRemote):
            raise RepoRegistryError("remotes must contain RepoRemote records")
        if remote.name in names:
            raise RepoRegistryError(f"duplicate remote name: {remote.name}")
        names.add(remote.name)
        result.append(remote)
    return tuple(result)


def _is_same_or_child_path(child: str, parent: str) -> bool:
    return child == parent or child.startswith(f"{parent}/")


def _reject_sensitive_text(value: str, *, field_name: str) -> None:
    if _SECRET_RE.search(value):
        raise RepoRegistryError(f"{field_name} appears to contain secret material")
    if _WINDOWS_ABSOLUTE_RE.search(value) or value.startswith(("/", "\\")):
        raise RepoRegistryError(f"{field_name} must not contain host-local absolute paths")


def redact_remote_url(value: Any) -> str:
    """Return a non-secret remote reference suitable for registry persistence."""

    raw = str(value or "").strip()
    if not raw:
        raise RepoRegistryError("remote url must not be empty")
    if "\x00" in raw:
        raise RepoRegistryError("remote url contains unsupported characters")

    split = urlsplit(raw)
    if split.scheme and split.netloc:
        host = split.hostname or ""
        if not host:
            raise RepoRegistryError("remote url host must not be empty")
        netloc = host
        if split.port:
            netloc = f"{netloc}:{split.port}"
        redacted = urlunsplit((split.scheme.lower(), netloc, split.path, "", ""))
    else:
        match = _SCP_LIKE_REMOTE_RE.match(raw)
        if match and match.group("user") != "git":
            redacted = match.group("target")
        else:
            redacted = raw.split("?", 1)[0].split("#", 1)[0]

    _reject_sensitive_text(redacted, field_name="remote url")
    return redacted


@dataclass(frozen=True, slots=True)
class RepoRemote:
    name: str
    url_redacted: str
    purpose: str
    push_policy: str

    @classmethod
    def create(
        cls,
        *,
        name: Any,
        url: Any | None = None,
        url_redacted: Any | None = None,
        purpose: Any = "other",
        push_policy: Any = "read_only",
    ) -> "RepoRemote":
        remote_name = _normalize_text(name, field_name="remote.name", max_len=80)
        if not _SAFE_NAME_RE.fullmatch(remote_name) or remote_name.startswith("-"):
            raise RepoRegistryError("remote.name contains unsupported characters")
        raw_url = url if url is not None else url_redacted
        return cls(
            name=remote_name,
            url_redacted=redact_remote_url(raw_url),
            purpose=_normalize_choice(purpose, field_name="remote.purpose", choices=_REMOTE_PURPOSES),
            push_policy=_normalize_choice(push_policy, field_name="remote.push_policy", choices=_PUSH_POLICIES),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url_redacted": self.url_redacted,
            "purpose": self.purpose,
            "push_policy": self.push_policy,
        }


@dataclass(frozen=True, slots=True)
class RepoRecord:
    repo_id: str
    title: str
    repo_kind: str
    owner: str
    path_ref: str
    workspace_root: str
    project_root: str
    system_root: str
    default_branch: str
    current_branch: str
    remotes: tuple[RepoRemote, ...]
    privacy_class: str
    provider_scope: str
    allowed_actions: tuple[str, ...]
    linked_project_slug: str
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        title: Any,
        owner: Any,
        workspace_root: Any,
        project_root: Any,
        created_at: Any,
        repo_id: Any | None = None,
        repo_kind: Any = "project",
        path_ref: Any | None = None,
        system_root: Any = "",
        default_branch: Any = "main",
        current_branch: Any = "",
        remotes: Iterable[RepoRemote] = (),
        privacy_class: Any = "private",
        provider_scope: Any | None = None,
        allowed_actions: Iterable[Any] | None = None,
        linked_project_slug: Any = "",
        updated_at: Any | None = None,
    ) -> "RepoRecord":
        normalized_title = _normalize_text(title, field_name="title", max_len=180)
        normalized_repo_id = _normalize_slug(repo_id, field_name="repo_id", fallback=normalized_title)
        normalized_workspace = _normalize_path_ref(workspace_root, field_name="workspace_root")
        normalized_project = _normalize_path_ref(project_root, field_name="project_root")
        if not _is_same_or_child_path(normalized_project, normalized_workspace):
            raise RepoRegistryError("project_root must stay below workspace_root")

        privacy = _normalize_choice(privacy_class, field_name="privacy_class", choices=_PRIVACY_CLASSES)
        scope_default = "default" if privacy == "public" else "local_only"
        scope = _normalize_choice(provider_scope or scope_default, field_name="provider_scope", choices=_PROVIDER_SCOPES)
        if privacy == "sensitive" and scope != "local_only":
            raise RepoRegistryError("sensitive repos must use local_only provider_scope")

        remote_records = _dedupe_remotes(remotes)
        actions = _dedupe_actions(allowed_actions)
        if "push" in actions and not any(remote.push_policy == "push_allowed" for remote in remote_records):
            raise RepoRegistryError("push action requires a push_allowed remote")

        created = _normalize_text(created_at, field_name="created_at", max_len=40)
        updated = _normalize_text(updated_at if updated_at is not None else created, field_name="updated_at", max_len=40)
        return cls(
            repo_id=normalized_repo_id,
            title=normalized_title,
            repo_kind=_normalize_choice(repo_kind, field_name="repo_kind", choices=_REPO_KINDS),
            owner=_normalize_text(owner, field_name="owner", max_len=120),
            path_ref=_normalize_path_ref(path_ref if path_ref is not None else normalized_project, field_name="path_ref"),
            workspace_root=normalized_workspace,
            project_root=normalized_project,
            system_root=_normalize_path_ref(system_root, field_name="system_root", allow_empty=True),
            default_branch=_normalize_branch(default_branch, field_name="default_branch"),
            current_branch=_normalize_branch(current_branch, field_name="current_branch", allow_empty=True),
            remotes=remote_records,
            privacy_class=privacy,
            provider_scope=scope,
            allowed_actions=actions,
            linked_project_slug=_normalize_slug(
                linked_project_slug,
                field_name="linked_project_slug",
                fallback="",
            )
            if linked_project_slug
            else "",
            created_at=created,
            updated_at=updated,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "title": self.title,
            "repo_kind": self.repo_kind,
            "owner": self.owner,
            "path_ref": self.path_ref,
            "workspace_root": self.workspace_root,
            "project_root": self.project_root,
            "system_root": self.system_root,
            "default_branch": self.default_branch,
            "current_branch": self.current_branch,
            "remotes": [remote.to_dict() for remote in self.remotes],
            "privacy_class": self.privacy_class,
            "provider_scope": self.provider_scope,
            "allowed_actions": list(self.allowed_actions),
            "linked_project_slug": self.linked_project_slug,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class RepoRegistry:
    repos: dict[str, RepoRecord] = field(default_factory=dict)

    def add(self, record: RepoRecord) -> None:
        if not isinstance(record, RepoRecord):
            raise RepoRegistryError("record must be a RepoRecord")
        if record.repo_id in self.repos:
            raise RepoRegistryError(f"repo already exists: {record.repo_id}")
        if any(existing.path_ref == record.path_ref for existing in self.repos.values()):
            raise RepoRegistryError(f"repo path already registered: {record.repo_id}")
        self.repos[record.repo_id] = record

    def put(self, record: RepoRecord) -> None:
        if not isinstance(record, RepoRecord):
            raise RepoRegistryError("record must be a RepoRecord")
        self.repos[record.repo_id] = record

    def get(self, repo_id: Any) -> RepoRecord:
        normalized_repo_id = _normalize_slug(repo_id, field_name="repo_id")
        try:
            return self.repos[normalized_repo_id]
        except KeyError as exc:
            raise RepoRegistryError(f"unknown repo: {normalized_repo_id}") from exc

    def forget(self, repo_id: Any) -> bool:
        normalized_repo_id = _normalize_slug(repo_id, field_name="repo_id")
        return self.repos.pop(normalized_repo_id, None) is not None

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "repo_count": len(self.repos),
            "repos": [
                {
                    "repo_id": record.repo_id,
                    "title": record.title,
                    "repo_kind": record.repo_kind,
                    "owner": record.owner,
                    "privacy_class": record.privacy_class,
                    "provider_scope": record.provider_scope,
                    "remote_count": len(record.remotes),
                    "push_remote_count": sum(1 for remote in record.remotes if remote.push_policy == "push_allowed"),
                    "allowed_actions": list(record.allowed_actions),
                    "linked_project_slug": record.linked_project_slug,
                }
                for record in self._sorted_records()
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "repos": [record.to_dict() for record in self._sorted_records()],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepoRegistry":
        if not isinstance(payload, dict):
            raise RepoRegistryError("payload must be a dict")
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise RepoRegistryError(f"schema_version must be {_SCHEMA_VERSION}")
        registry = cls()
        for raw_record in _list(payload.get("repos"), field_name="repos"):
            registry.add(_record_from_dict(raw_record))
        return registry

    def save_json(self, path: str | Path = REPO_REGISTRY_FILE) -> None:
        atomic_write_json(str(path), self.to_dict(), indent=2)

    @classmethod
    def load_json(cls, path: str | Path = REPO_REGISTRY_FILE) -> "RepoRegistry":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def load_or_empty(cls, path: str | Path = REPO_REGISTRY_FILE) -> "RepoRegistry":
        registry_path = Path(path)
        if not registry_path.exists():
            return cls()
        return cls.load_json(registry_path)

    def _sorted_records(self) -> tuple[RepoRecord, ...]:
        return tuple(self.repos[key] for key in sorted(self.repos))


def _remote_from_dict(payload: dict[str, Any]) -> RepoRemote:
    if not isinstance(payload, dict):
        raise RepoRegistryError("remote must be a dict")
    return RepoRemote.create(
        name=_required(payload, "name"),
        url_redacted=_required(payload, "url_redacted"),
        purpose=payload.get("purpose", "other"),
        push_policy=payload.get("push_policy", "read_only"),
    )


def _record_from_dict(payload: dict[str, Any]) -> RepoRecord:
    if not isinstance(payload, dict):
        raise RepoRegistryError("repo record must be a dict")
    return RepoRecord.create(
        repo_id=_required(payload, "repo_id"),
        title=_required(payload, "title"),
        repo_kind=_required(payload, "repo_kind"),
        owner=_required(payload, "owner"),
        path_ref=_required(payload, "path_ref"),
        workspace_root=_required(payload, "workspace_root"),
        project_root=_required(payload, "project_root"),
        system_root=payload.get("system_root", ""),
        default_branch=_required(payload, "default_branch"),
        current_branch=payload.get("current_branch", ""),
        remotes=tuple(_remote_from_dict(item) for item in _list(payload.get("remotes", []), field_name="remotes")),
        privacy_class=payload.get("privacy_class", "private"),
        provider_scope=payload.get("provider_scope"),
        allowed_actions=payload.get("allowed_actions"),
        linked_project_slug=payload.get("linked_project_slug", ""),
        created_at=_required(payload, "created_at"),
        updated_at=_required(payload, "updated_at"),
    )


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise RepoRegistryError(f"missing required field: {key}")
    return payload[key]


def _list(value: Any, *, field_name: str) -> list[Any]:
    if value is None or not isinstance(value, list):
        raise RepoRegistryError(f"{field_name} must be a list")
    return value
