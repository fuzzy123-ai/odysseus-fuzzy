"""Provider-neutral contracts for Odysseus project versioning.

The public commit request intentionally has no provider selector. Provider
dispatch is derived from :mod:`src.project_forge_policy` after the request has
been validated and the local commit has succeeded.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit


PROJECT_COMMIT_TRANSACTION_SCHEMA = "odysseus.project_commit_transaction.v1"
FORGE_PROVIDERS = ("local", "nextcloud", "github")
FORGE_CAPABILITIES = (
    "repository",
    "commit",
    "branch",
    "checkpoint",
    "version",
    "artifact_refs",
    "restore",
    "webdav_sync",
    "readable_tree",
    "manifest",
    "artifacts",
    "recovery_bundle",
    "git_push",
    "git_fetch",
    "branch_refs",
    "pull_requests",
    "releases",
)

DEFAULT_PROVIDER_CAPABILITIES: Mapping[str, tuple[str, ...]] = {
    "local": ("repository", "commit", "branch", "checkpoint", "version", "artifact_refs", "restore"),
    "nextcloud": ("webdav_sync", "readable_tree", "manifest", "artifacts", "recovery_bundle", "restore"),
    "github": ("git_push", "git_fetch", "branch_refs"),
}

PROVIDER_STATUSES = (
    "not_configured",
    "pending",
    "sync_pending",
    "synced",
    "failed",
    "blocked",
    "diverged",
)
LOCAL_COMMIT_STATUSES = ("pending", "committed", "failed", "blocked")
COMMIT_TRANSACTION_STATUSES = ("pending", "committed", "synced", "partial", "sync_pending", "failed", "blocked")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SAFE_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9._/@+ -]{1,180}$")
_TRANSACTION_ID_RE = re.compile(r"^pct_[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|[\s('\"])[A-Z]:[\\/]")
_UNC_PATH_RE = re.compile(r"(?:^|[\s('\"])(?:\\\\|//)[^\s/\\]+[\\/]")
_PRIVATE_POSIX_PATH_RE = re.compile(r"(?:^|[\s('\"])/(?:home|users|root|private|var/(?:run|lib))/[^\s,;)]*", re.IGNORECASE)
_PRIVATE_MARKER_RE = re.compile(r"(?i)(?:^|[/\\])(?:\.env|\.ssh|id_rsa|id_dsa|id_ed25519)(?:$|[/\\])")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:access[_-]?token|auth[_-]?token|token|secret|password|passwd|api[_-]?key|private[_-]?key)\b\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_RE = re.compile(r"(?i)\b(?:https?|webdav|git\+https)://[^\s<>'\"]+")
_CREDENTIAL_QUERY_KEYS = {
    "access_token",
    "auth_token",
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "key",
}
_BLOCKED_PATH_PARTS = {".git", ".env", ".ssh", "id_rsa", "id_dsa", "id_ed25519"}


class ProjectForgeContractError(ValueError):
    """Raised when a project-forge contract is invalid or unsafe."""


def validate_persisted_text(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
    max_len: int = 500,
    multiline: bool = False,
) -> str:
    """Normalize safe persisted text and reject credential/private markers."""

    raw = str(value or "").strip()
    if multiline:
        text = "\n".join(line.rstrip() for line in raw.splitlines()).strip()
    else:
        if "\n" in raw or "\r" in raw:
            raise ProjectForgeContractError(f"{field_name} must be a single line")
        text = " ".join(raw.split())
    if not text and not allow_empty:
        raise ProjectForgeContractError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ProjectForgeContractError(f"{field_name} exceeds max length {max_len}")
    _reject_unsafe_persisted_text(text, field_name=field_name)
    return text


def validate_repo_relative_path(value: Any, *, field_name: str = "path") -> str:
    """Return a normalized repo-relative path that cannot escape its repo."""

    raw = str(value or "").strip()
    if not raw:
        raise ProjectForgeContractError(f"{field_name} must not be empty")
    if "\x00" in raw or "\\" in raw or raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", raw):
        raise ProjectForgeContractError(f"{field_name} must be repo-relative and use forward slashes")
    if not _SAFE_REPO_PATH_RE.fullmatch(raw) or any(marker in raw for marker in ("*", "?", "[", "]")):
        raise ProjectForgeContractError(f"{field_name} contains unsupported characters or patterns")
    normalized = posixpath.normpath(raw)
    parts = normalized.split("/")
    if normalized in ("", ".", "..") or normalized.startswith("../") or any(part in ("", ".", "..") for part in parts):
        raise ProjectForgeContractError(f"{field_name} must not contain traversal segments")
    if any(part.lower() in _BLOCKED_PATH_PARTS for part in parts):
        raise ProjectForgeContractError(f"{field_name} targets a blocked private path")
    if any(any(ord(character) < 32 for character in part) for part in parts):
        raise ProjectForgeContractError(f"{field_name} contains unsupported characters")
    _reject_unsafe_persisted_text(normalized, field_name=field_name)
    return normalized


@dataclass(frozen=True, slots=True)
class ForgeProviderCapabilities:
    """A provider and the known capabilities its adapter declares."""

    provider: str
    capabilities: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        provider = _normalize_choice(self.provider, field_name="provider", choices=FORGE_PROVIDERS)
        raw_capabilities: Iterable[Any] = (
            DEFAULT_PROVIDER_CAPABILITIES[provider] if self.capabilities is None else self.capabilities
        )
        capabilities = _normalize_capabilities(raw_capabilities)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "capabilities", capabilities)

    @classmethod
    def create(cls, *, provider: Any, capabilities: Iterable[Any] | None = None) -> "ForgeProviderCapabilities":
        return cls(
            provider=str(provider or ""),
            capabilities=tuple(capabilities) if capabilities is not None else None,
        )

    def supports(self, capability: Any) -> bool:
        normalized = _normalize_choice(capability, field_name="capability", choices=FORGE_CAPABILITIES)
        return normalized in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "capabilities": list(self.capabilities)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ForgeProviderCapabilities":
        data = _strict_mapping(payload, field_name="provider capabilities", allowed={"provider", "capabilities"})
        return cls.create(
            provider=_required(data, "provider"),
            capabilities=_list(data.get("capabilities"), field_name="capabilities") if "capabilities" in data else None,
        )


# Short, provider-neutral alias for callers that do not need the Forge prefix.
ProviderCapabilities = ForgeProviderCapabilities


@dataclass(frozen=True, slots=True)
class ForgeProvider:
    """Serializable provider declaration without credentials or endpoints."""

    provider: str
    capabilities: ForgeProviderCapabilities | Iterable[Any] | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        provider = _normalize_choice(self.provider, field_name="provider", choices=FORGE_PROVIDERS)
        raw_capabilities = self.capabilities
        if isinstance(raw_capabilities, ForgeProviderCapabilities):
            capability_record = raw_capabilities
            if capability_record.provider != provider:
                raise ProjectForgeContractError("provider capability record must match provider")
        else:
            capability_record = ForgeProviderCapabilities.create(
                provider=provider,
                capabilities=tuple(raw_capabilities) if raw_capabilities is not None else None,
            )
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "capabilities", capability_record)
        object.__setattr__(self, "enabled", _strict_bool(self.enabled, field_name="enabled"))

    @classmethod
    def create(
        cls,
        *,
        provider: Any,
        capabilities: ForgeProviderCapabilities | Iterable[Any] | None = None,
        enabled: Any = True,
    ) -> "ForgeProvider":
        return cls(provider=str(provider or ""), capabilities=capabilities, enabled=enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilities": list(self.capabilities.capabilities),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ForgeProvider":
        data = _strict_mapping(payload, field_name="provider", allowed={"provider", "capabilities", "enabled"})
        return cls.create(
            provider=_required(data, "provider"),
            capabilities=_list(data.get("capabilities"), field_name="capabilities") if "capabilities" in data else None,
            enabled=data.get("enabled", True),
        )


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Redacted result state for one external provider."""

    provider: str
    status: str
    retryable: bool = False
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _normalize_choice(self.provider, field_name="provider", choices=FORGE_PROVIDERS))
        object.__setattr__(self, "status", _normalize_choice(self.status, field_name="provider status", choices=PROVIDER_STATUSES))
        retryable = _strict_bool(self.retryable, field_name="retryable")
        if retryable and self.status not in ("pending", "sync_pending", "failed"):
            raise ProjectForgeContractError("retryable provider status must be pending, sync_pending, or failed")
        object.__setattr__(self, "retryable", retryable)
        object.__setattr__(
            self,
            "detail",
            validate_persisted_text(self.detail, field_name="provider detail", allow_empty=True, max_len=500),
        )

    @classmethod
    def create(
        cls,
        *,
        provider: Any,
        status: Any,
        retryable: Any = False,
        detail: Any = "",
    ) -> "ProviderStatus":
        return cls(provider=str(provider or ""), status=str(status or ""), retryable=retryable, detail=str(detail or ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "retryable": self.retryable,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProviderStatus":
        data = _strict_mapping(payload, field_name="provider status", allowed={"provider", "status", "retryable", "detail"})
        return cls.create(
            provider=_required(data, "provider"),
            status=_required(data, "status"),
            retryable=data.get("retryable", False),
            detail=data.get("detail", ""),
        )


@dataclass(frozen=True, slots=True)
class ProjectCommitRequest:
    """The single public commit input; deliberately contains no provider."""

    repo_id: str
    title: str
    description: str
    version_label: str = ""
    change_notes: tuple[str, ...] = ()
    reviewed_paths: tuple[str, ...] = ()
    checks_passed: bool = False
    content_reviewed: bool = False
    confirmed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_id", _normalize_id(self.repo_id, field_name="repo_id"))
        object.__setattr__(self, "title", validate_persisted_text(self.title, field_name="title", max_len=120))
        object.__setattr__(
            self,
            "description",
            validate_persisted_text(self.description, field_name="description", max_len=4000, multiline=True),
        )
        object.__setattr__(
            self,
            "version_label",
            validate_persisted_text(self.version_label, field_name="version_label", allow_empty=True, max_len=100),
        )
        object.__setattr__(self, "change_notes", _normalize_notes(self.change_notes))
        object.__setattr__(self, "reviewed_paths", _normalize_paths(self.reviewed_paths))
        object.__setattr__(self, "checks_passed", _strict_bool(self.checks_passed, field_name="checks_passed"))
        object.__setattr__(
            self,
            "content_reviewed",
            _strict_bool(self.content_reviewed, field_name="content_reviewed"),
        )
        object.__setattr__(self, "confirmed", _strict_bool(self.confirmed, field_name="confirmed"))

    @property
    def ready_for_commit(self) -> bool:
        return bool(self.reviewed_paths and self.checks_passed and self.content_reviewed and self.confirmed)

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.reviewed_paths:
            blockers.append("reviewed_paths are required")
        if not self.checks_passed:
            blockers.append("checks_passed=true is required")
        if not self.content_reviewed:
            blockers.append("content_reviewed=true is required")
        if not self.confirmed:
            blockers.append("confirmed=true is required")
        return tuple(blockers)

    @classmethod
    def create(
        cls,
        *,
        repo_id: Any,
        title: Any,
        description: Any,
        version_label: Any = "",
        change_notes: Iterable[Any] = (),
        reviewed_paths: Iterable[Any] = (),
        checks_passed: Any = False,
        content_reviewed: Any = False,
        confirmed: Any = False,
    ) -> "ProjectCommitRequest":
        return cls(
            repo_id=str(repo_id or ""),
            title=str(title or ""),
            description=str(description or ""),
            version_label=str(version_label or ""),
            change_notes=change_notes,
            reviewed_paths=reviewed_paths,
            checks_passed=checks_passed,
            content_reviewed=content_reviewed,
            confirmed=confirmed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "title": self.title,
            "description": self.description,
            "version_label": self.version_label,
            "change_notes": list(self.change_notes),
            "reviewed_paths": list(self.reviewed_paths),
            "checks_passed": self.checks_passed,
            "content_reviewed": self.content_reviewed,
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectCommitRequest":
        allowed = {
            "repo_id",
            "title",
            "description",
            "version_label",
            "change_notes",
            "reviewed_paths",
            "checks_passed",
            "content_reviewed",
            "confirmed",
        }
        data = _strict_mapping(payload, field_name="commit request", allowed=allowed)
        return cls.create(
            repo_id=_required(data, "repo_id"),
            title=_required(data, "title"),
            description=_required(data, "description"),
            version_label=data.get("version_label", ""),
            change_notes=_list(data.get("change_notes", []), field_name="change_notes"),
            reviewed_paths=_list(data.get("reviewed_paths", []), field_name="reviewed_paths"),
            checks_passed=data.get("checks_passed", False),
            content_reviewed=data.get("content_reviewed", False),
            confirmed=data.get("confirmed", False),
        )


@dataclass(frozen=True, slots=True)
class ProjectCommitResult:
    """Provider-neutral result of a local commit and configured syncs."""

    SCHEMA: ClassVar[str] = PROJECT_COMMIT_TRANSACTION_SCHEMA

    transaction_id: str
    repo_id: str
    commit_sha: str
    local_status: str
    provider_statuses: tuple[ProviderStatus, ...] = ()
    overall_status: str = ""
    retry_scheduled: bool | None = None

    def __post_init__(self) -> None:
        transaction_id = validate_persisted_text(self.transaction_id, field_name="transaction_id", max_len=132)
        if not _TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise ProjectForgeContractError("transaction_id must use the pct_ identifier format")
        repo_id = _normalize_id(self.repo_id, field_name="repo_id")
        local_status = _normalize_choice(self.local_status, field_name="local_status", choices=LOCAL_COMMIT_STATUSES)
        commit_sha = str(self.commit_sha or "").strip()
        if local_status == "committed":
            if not _COMMIT_SHA_RE.fullmatch(commit_sha):
                raise ProjectForgeContractError("commit_sha must be a hexadecimal Git object id after commit")
        elif commit_sha:
            raise ProjectForgeContractError("commit_sha must be empty when the local commit did not succeed")
        statuses = _normalize_provider_statuses(self.provider_statuses)
        derived_overall = _derive_overall_status(local_status, statuses)
        overall = self.overall_status or derived_overall
        overall = _normalize_choice(overall, field_name="overall_status", choices=COMMIT_TRANSACTION_STATUSES)
        if overall != derived_overall:
            raise ProjectForgeContractError(
                f"overall_status must be derived as {derived_overall!r} for the supplied statuses"
            )
        derived_retry = any(item.retryable or item.status in ("pending", "sync_pending") for item in statuses)
        retry_scheduled = derived_retry if self.retry_scheduled is None else _strict_bool(
            self.retry_scheduled,
            field_name="retry_scheduled",
        )
        if retry_scheduled != derived_retry:
            raise ProjectForgeContractError(
                f"retry_scheduled must be derived as {derived_retry!r} for the supplied statuses"
            )
        object.__setattr__(self, "transaction_id", transaction_id)
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "commit_sha", commit_sha)
        object.__setattr__(self, "local_status", local_status)
        object.__setattr__(self, "provider_statuses", statuses)
        object.__setattr__(self, "overall_status", overall)
        object.__setattr__(self, "retry_scheduled", retry_scheduled)

    @property
    def schema(self) -> str:
        return self.SCHEMA

    @classmethod
    def create(
        cls,
        *,
        transaction_id: Any,
        repo_id: Any,
        commit_sha: Any,
        local_status: Any,
        provider_statuses: Mapping[str, Any] | Iterable[ProviderStatus] = (),
        overall_status: Any = "",
        retry_scheduled: Any = None,
    ) -> "ProjectCommitResult":
        return cls(
            transaction_id=str(transaction_id or ""),
            repo_id=str(repo_id or ""),
            commit_sha=str(commit_sha or ""),
            local_status=str(local_status or ""),
            provider_statuses=_provider_statuses_from_value(provider_statuses),
            overall_status=str(overall_status or ""),
            retry_scheduled=retry_scheduled,
        )

    def status_for(self, provider: Any) -> ProviderStatus | None:
        name = _normalize_choice(provider, field_name="provider", choices=FORGE_PROVIDERS)
        return next((item for item in self.provider_statuses if item.provider == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "transaction_id": self.transaction_id,
            "repo_id": self.repo_id,
            "commit_sha": self.commit_sha,
            "local_status": self.local_status,
            "provider_statuses": {item.provider: item.status for item in self.provider_statuses},
            "retryable_providers": [item.provider for item in self.provider_statuses if item.retryable],
            "overall_status": self.overall_status,
            "retry_scheduled": self.retry_scheduled,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectCommitResult":
        allowed = {
            "schema",
            "transaction_id",
            "repo_id",
            "commit_sha",
            "local_status",
            "provider_statuses",
            "retryable_providers",
            "overall_status",
            "retry_scheduled",
        }
        data = _strict_mapping(payload, field_name="commit result", allowed=allowed)
        if _required(data, "schema") != cls.SCHEMA:
            raise ProjectForgeContractError(f"schema must be {cls.SCHEMA}")
        statuses = data.get("provider_statuses", {})
        if not isinstance(statuses, Mapping):
            raise ProjectForgeContractError("provider_statuses must be a mapping")
        retryable_providers = data.get("retryable_providers", [])
        if not isinstance(retryable_providers, list):
            raise ProjectForgeContractError("retryable_providers must be a list")
        retryable_names: set[str] = set()
        for provider in retryable_providers:
            normalized = _normalize_choice(provider, field_name="retryable provider", choices=FORGE_PROVIDERS)
            if normalized == "local" or normalized not in statuses:
                raise ProjectForgeContractError("retryable provider must identify a configured external status")
            if normalized in retryable_names:
                raise ProjectForgeContractError("retryable_providers must not contain duplicates")
            retryable_names.add(normalized)
        restored_statuses: dict[str, Any] = {}
        for provider, status in statuses.items():
            if isinstance(status, Mapping):
                item = dict(status)
                item.setdefault("retryable", provider in retryable_names)
                restored_statuses[provider] = item
            else:
                restored_statuses[provider] = {
                    "status": status,
                    "retryable": provider in retryable_names,
                }
        return cls.create(
            transaction_id=_required(data, "transaction_id"),
            repo_id=_required(data, "repo_id"),
            commit_sha=data.get("commit_sha", ""),
            local_status=_required(data, "local_status"),
            provider_statuses=restored_statuses,
            overall_status=data.get("overall_status", ""),
            retry_scheduled=data.get("retry_scheduled"),
        )


def _normalize_capabilities(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ProjectForgeContractError("capabilities must be a list")
    capabilities: list[str] = []
    for value in values:
        capability = _normalize_choice(value, field_name="capability", choices=FORGE_CAPABILITIES)
        if capability not in capabilities:
            capabilities.append(capability)
    return tuple(capabilities)


def _normalize_notes(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ProjectForgeContractError("change_notes must be a list")
    notes: list[str] = []
    for value in values:
        note = validate_persisted_text(value, field_name="change_note", max_len=500)
        if note not in notes:
            notes.append(note)
    if len(notes) > 50:
        raise ProjectForgeContractError("change_notes exceeds max length 50")
    return tuple(notes)


def _normalize_paths(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ProjectForgeContractError("reviewed_paths must be a list")
    paths: list[str] = []
    for value in values:
        if str(value or "").strip().endswith(("/", "\\")):
            raise ProjectForgeContractError("reviewed_path must identify an exact file, not a directory")
        path = validate_repo_relative_path(value, field_name="reviewed_path")
        if path not in paths:
            paths.append(path)
    if len(paths) > 80:
        raise ProjectForgeContractError("reviewed_paths exceeds max length 80")
    return tuple(paths)


def _normalize_provider_statuses(values: Iterable[ProviderStatus]) -> tuple[ProviderStatus, ...]:
    statuses: list[ProviderStatus] = []
    names: set[str] = set()
    for value in values:
        if not isinstance(value, ProviderStatus):
            raise ProjectForgeContractError("provider_statuses must contain ProviderStatus records")
        if value.provider == "local":
            raise ProjectForgeContractError("local status belongs in local_status, not provider_statuses")
        if value.provider in names:
            raise ProjectForgeContractError(f"duplicate provider status: {value.provider}")
        names.add(value.provider)
        statuses.append(value)
    return tuple(sorted(statuses, key=lambda item: FORGE_PROVIDERS.index(item.provider)))


def _provider_statuses_from_value(
    values: Mapping[str, Any] | Iterable[ProviderStatus],
) -> tuple[ProviderStatus, ...]:
    if isinstance(values, Mapping):
        statuses: list[ProviderStatus] = []
        for provider, raw_status in values.items():
            if isinstance(raw_status, ProviderStatus):
                if raw_status.provider != provider:
                    raise ProjectForgeContractError("provider status key must match its provider")
                statuses.append(raw_status)
            elif isinstance(raw_status, Mapping):
                item = dict(raw_status)
                item.setdefault("provider", provider)
                statuses.append(ProviderStatus.from_dict(item))
            else:
                statuses.append(ProviderStatus.create(provider=provider, status=raw_status))
        return tuple(statuses)
    if isinstance(values, (str, bytes)):
        raise ProjectForgeContractError("provider_statuses must be a mapping or list")
    return tuple(values)


def _derive_overall_status(local_status: str, statuses: tuple[ProviderStatus, ...]) -> str:
    if local_status != "committed":
        return local_status
    if not statuses:
        return "committed"
    if all(item.status == "synced" for item in statuses):
        return "synced"
    if all(item.status in ("pending", "sync_pending") for item in statuses):
        return "sync_pending"
    return "partial"


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = validate_persisted_text(value, field_name=field_name, max_len=80).lower().replace("-", "_")
    if text not in choices:
        raise ProjectForgeContractError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_id(value: Any, *, field_name: str) -> str:
    text = validate_persisted_text(value, field_name=field_name, max_len=100)
    if not _SAFE_ID_RE.fullmatch(text):
        raise ProjectForgeContractError(f"{field_name} contains unsupported characters")
    return text


def _strict_bool(value: Any, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise ProjectForgeContractError(f"{field_name} must be a boolean")
    return value


def _strict_mapping(payload: Mapping[str, Any], *, field_name: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProjectForgeContractError(f"{field_name} must be a mapping")
    data = dict(payload)
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProjectForgeContractError(f"{field_name} contains unknown fields: {', '.join(unknown)}")
    return data


def _required(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise ProjectForgeContractError(f"missing required field: {key}")
    return payload[key]


def _list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProjectForgeContractError(f"{field_name} must be a list")
    return value


def _reject_unsafe_persisted_text(value: str, *, field_name: str) -> None:
    if "\x00" in value:
        raise ProjectForgeContractError(f"{field_name} contains unsupported characters")
    if _SECRET_ASSIGNMENT_RE.search(value) or _BEARER_RE.search(value):
        raise ProjectForgeContractError(f"{field_name} appears to contain secret material")
    if _WINDOWS_ABSOLUTE_RE.search(value) or _UNC_PATH_RE.search(value) or _PRIVATE_POSIX_PATH_RE.search(value):
        raise ProjectForgeContractError(f"{field_name} must not contain host-local absolute paths")
    if _PRIVATE_MARKER_RE.search(value):
        raise ProjectForgeContractError(f"{field_name} appears to contain a private path")
    for match in _URL_RE.finditer(value):
        split = urlsplit(match.group(0).rstrip(".,;:)"))
        if split.username is not None or split.password is not None:
            raise ProjectForgeContractError(f"{field_name} must not contain credential URLs")
        for key, query_value in parse_qsl(split.query, keep_blank_values=True):
            normalized_key = key.lower().replace("-", "_")
            if normalized_key in _CREDENTIAL_QUERY_KEYS and query_value:
                raise ProjectForgeContractError(f"{field_name} must not contain credential URLs")
