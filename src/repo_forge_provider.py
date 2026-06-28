"""Gated forge-provider bridge for registered repositories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from src.repo_registry import RepoRecord, RepoRegistry, RepoRegistryError, redact_remote_url


_PROVIDERS = ("github", "gitea", "forgejo")
_DECISIONS = ("blocked", "hold", "plan_ready")
_STATUSES = ("blocked", "plan_ready", "fetched", "failed")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_DEFAULT_API_BASES = {
    "github": "https://api.github.com",
    "gitea": "https://gitea.example.invalid/api/v1",
    "forgejo": "https://forgejo.example.invalid/api/v1",
}


class RepoForgeProviderError(ValueError):
    """Raised when a forge provider request is unsafe."""


class RepoForgeMetadataProvider(Protocol):
    def __call__(self, request: "RepoForgeMetadataRequest") -> "RepoForgeMetadata":
        ...


@dataclass(frozen=True, slots=True)
class RepoForgeMetadataRequest:
    provider: str
    namespace: str
    repo_name: str
    api_base_url_redacted: str
    integration_id: str

    @property
    def repo_full_name(self) -> str:
        return f"{self.namespace}/{self.repo_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "namespace": self.namespace,
            "repo_name": self.repo_name,
            "repo_full_name": self.repo_full_name,
            "api_base_url_redacted": self.api_base_url_redacted,
            "integration_id": self.integration_id,
        }


@dataclass(frozen=True, slots=True)
class RepoForgeMetadata:
    provider: str
    namespace: str
    repo_name: str
    default_branch: str
    permissions: tuple[str, ...]
    issue_count: int
    pull_request_count: int
    private: bool
    html_url_redacted: str
    clone_url_redacted: str

    @classmethod
    def create(
        cls,
        *,
        provider: Any,
        namespace: Any,
        repo_name: Any,
        default_branch: Any,
        permissions: Any = (),
        issue_count: Any = 0,
        pull_request_count: Any = 0,
        private: Any = False,
        html_url: Any = "",
        clone_url: Any = "",
    ) -> "RepoForgeMetadata":
        normalized_provider = _normalize_provider(provider)
        normalized_namespace = _normalize_name(namespace, field_name="namespace")
        normalized_repo = _normalize_name(repo_name, field_name="repo_name")
        html = _redact_url(html_url, allow_empty=True)
        clone = _redact_url(clone_url, allow_empty=True)
        return cls(
            provider=normalized_provider,
            namespace=normalized_namespace,
            repo_name=normalized_repo,
            default_branch=_normalize_branch(default_branch),
            permissions=_normalize_permissions(permissions),
            issue_count=_normalize_count(issue_count, field_name="issue_count"),
            pull_request_count=_normalize_count(pull_request_count, field_name="pull_request_count"),
            private=bool(private),
            html_url_redacted=html,
            clone_url_redacted=clone,
        )

    @property
    def repo_full_name(self) -> str:
        return f"{self.namespace}/{self.repo_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "namespace": self.namespace,
            "repo_name": self.repo_name,
            "repo_full_name": self.repo_full_name,
            "default_branch": self.default_branch,
            "permissions": list(self.permissions),
            "issue_count": self.issue_count,
            "pull_request_count": self.pull_request_count,
            "private": self.private,
            "html_url_redacted": self.html_url_redacted,
            "clone_url_redacted": self.clone_url_redacted,
        }


@dataclass(frozen=True, slots=True)
class RepoForgePlan:
    repo_id: str
    provider: str
    namespace: str
    repo_name: str
    api_base_url_redacted: str
    integration_id: str
    auth_ready: bool
    confirmed: bool
    operator_go: bool
    live_enabled: bool
    create_repo_requested: bool
    decision: str
    blockers: tuple[str, ...]
    planned_steps: tuple[dict[str, Any], ...]
    metadata_request: RepoForgeMetadataRequest
    provider_gate: str
    repo_creation_gate: str
    next_human_decision: str

    @property
    def can_fetch_metadata(self) -> bool:
        return self.decision == "plan_ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "provider": self.provider,
            "namespace": self.namespace,
            "repo_name": self.repo_name,
            "api_base_url_redacted": self.api_base_url_redacted,
            "integration_id": self.integration_id,
            "auth_ready": self.auth_ready,
            "confirmed": self.confirmed,
            "operator_go": self.operator_go,
            "live_enabled": self.live_enabled,
            "create_repo_requested": self.create_repo_requested,
            "can_fetch_metadata": self.can_fetch_metadata,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "metadata_request": self.metadata_request.to_dict(),
            "provider_gate": self.provider_gate,
            "repo_creation_gate": self.repo_creation_gate,
            "next_human_decision": self.next_human_decision,
        }


@dataclass(frozen=True, slots=True)
class RepoForgeReport:
    status: str
    executed: bool
    plan: RepoForgePlan
    metadata: RepoForgeMetadata | None
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executed": self.executed,
            "plan": self.plan.to_dict(),
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "blockers": list(self.blockers),
        }


def plan_repo_forge_metadata(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    provider: Any,
    namespace: Any,
    repo_name: Any | None = None,
    api_base_url: Any = "",
    integration_id: Any = "",
    auth_ready: bool = False,
    confirmed: bool = False,
    operator_go: bool = False,
    live_enabled: bool = False,
    create_repo_requested: bool = False,
) -> RepoForgeReport:
    record = _record(registry, repo_id)
    plan = build_repo_forge_plan(
        record=record,
        provider=provider,
        namespace=namespace,
        repo_name=repo_name,
        api_base_url=api_base_url,
        integration_id=integration_id,
        auth_ready=auth_ready,
        confirmed=confirmed,
        operator_go=operator_go,
        live_enabled=live_enabled,
        create_repo_requested=create_repo_requested,
    )
    return RepoForgeReport(
        status=plan.decision,
        executed=False,
        plan=plan,
        metadata=None,
        blockers=plan.blockers,
    )


def run_repo_forge_metadata(
    *,
    registry: RepoRegistry,
    repo_id: Any,
    provider: Any,
    namespace: Any,
    repo_name: Any | None = None,
    api_base_url: Any = "",
    integration_id: Any = "",
    auth_ready: bool = False,
    confirmed: bool = False,
    operator_go: bool = False,
    live_enabled: bool = False,
    create_repo_requested: bool = False,
    metadata_provider: RepoForgeMetadataProvider | None = None,
) -> RepoForgeReport:
    record = _record(registry, repo_id)
    plan = build_repo_forge_plan(
        record=record,
        provider=provider,
        namespace=namespace,
        repo_name=repo_name,
        api_base_url=api_base_url,
        integration_id=integration_id,
        auth_ready=auth_ready,
        confirmed=confirmed,
        operator_go=operator_go,
        live_enabled=live_enabled,
        create_repo_requested=create_repo_requested,
    )
    if not plan.can_fetch_metadata:
        return RepoForgeReport(
            status="blocked",
            executed=False,
            plan=plan,
            metadata=None,
            blockers=plan.blockers,
        )
    if metadata_provider is None:
        return RepoForgeReport(
            status="blocked",
            executed=False,
            plan=plan,
            metadata=None,
            blockers=("forge metadata provider client is not configured for live execution",),
        )
    try:
        metadata = metadata_provider(plan.metadata_request)
    except Exception as exc:
        return RepoForgeReport(
            status="failed",
            executed=True,
            plan=plan,
            metadata=None,
            blockers=(f"forge metadata provider failed: {_safe_error(exc)}",),
        )
    if not isinstance(metadata, RepoForgeMetadata):
        raise RepoForgeProviderError("metadata_provider must return RepoForgeMetadata")
    if metadata.provider != plan.provider or metadata.namespace != plan.namespace or metadata.repo_name != plan.repo_name:
        raise RepoForgeProviderError("metadata_provider returned metadata for a different repository")
    return RepoForgeReport(
        status="fetched",
        executed=True,
        plan=plan,
        metadata=metadata,
        blockers=(),
    )


def build_repo_forge_plan(
    *,
    record: RepoRecord,
    provider: Any,
    namespace: Any,
    repo_name: Any | None = None,
    api_base_url: Any = "",
    integration_id: Any = "",
    auth_ready: bool = False,
    confirmed: bool = False,
    operator_go: bool = False,
    live_enabled: bool = False,
    create_repo_requested: bool = False,
) -> RepoForgePlan:
    if not isinstance(record, RepoRecord):
        raise RepoForgeProviderError("record must be a RepoRecord")
    normalized_provider = _normalize_provider(provider)
    normalized_namespace = _normalize_name(namespace, field_name="namespace")
    normalized_repo = _normalize_name(repo_name if repo_name is not None else _repo_name_from_record(record), field_name="repo_name")
    base_url = _normalize_api_base_url(api_base_url, provider=normalized_provider)
    integration = _normalize_optional_id(integration_id, field_name="integration_id")

    blockers: list[str] = []
    if record.privacy_class == "sensitive":
        blockers.append("sensitive repos cannot use external forge metadata providers")
    if not auth_ready:
        blockers.append("auth_ready=true is required from secure handoff or server-side credentials")
    if not confirmed:
        blockers.append("confirmed=true is required before live forge metadata fetch")
    if not operator_go:
        blockers.append("operator_go=true is required before live forge metadata fetch")
    if not live_enabled:
        blockers.append("live_enabled=true is required before live forge metadata fetch")

    if record.privacy_class == "sensitive":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    request = RepoForgeMetadataRequest(
        provider=normalized_provider,
        namespace=normalized_namespace,
        repo_name=normalized_repo,
        api_base_url_redacted=base_url,
        integration_id=integration,
    )
    return RepoForgePlan(
        repo_id=record.repo_id,
        provider=normalized_provider,
        namespace=normalized_namespace,
        repo_name=normalized_repo,
        api_base_url_redacted=base_url,
        integration_id=integration,
        auth_ready=bool(auth_ready),
        confirmed=bool(confirmed),
        operator_go=bool(operator_go),
        live_enabled=bool(live_enabled),
        create_repo_requested=bool(create_repo_requested),
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(dict.fromkeys(blockers)),
        planned_steps=(
            {
                "step_id": "auth_gate",
                "summary": "verify secure handoff or server-side forge credentials are ready",
                "executes": False,
            },
            {
                "step_id": "fetch_repo_metadata",
                "summary": f"fetch read-only metadata for {normalized_provider}/{normalized_namespace}/{normalized_repo}",
                "executes": True,
            },
            {
                "step_id": "repo_creation_gate",
                "summary": _repo_creation_gate(
                    provider=normalized_provider,
                    namespace=normalized_namespace,
                    repo_name=normalized_repo,
                    requested=bool(create_repo_requested),
                ),
                "executes": False,
            },
        ),
        metadata_request=request,
        provider_gate=_provider_gate(
            provider=normalized_provider,
            namespace=normalized_namespace,
            repo_name=normalized_repo,
            api_base_url=base_url,
        ),
        repo_creation_gate=_repo_creation_gate(
            provider=normalized_provider,
            namespace=normalized_namespace,
            repo_name=normalized_repo,
            requested=bool(create_repo_requested),
        ),
        next_human_decision=_next_human_decision(decision, create_repo_requested=bool(create_repo_requested)),
    )


def normalize_forge_metadata_payload(payload: Mapping[str, Any], *, provider: Any, namespace: Any, repo_name: Any) -> RepoForgeMetadata:
    """Convert a GitHub/Gitea/Forgejo-like repo payload into redacted metadata."""

    if not isinstance(payload, Mapping):
        raise RepoForgeProviderError("forge metadata payload must be a mapping")
    permissions = payload.get("permissions") or payload.get("permission") or ()
    if isinstance(permissions, Mapping):
        permissions = _permission_names_from_mapping(permissions)
    return RepoForgeMetadata.create(
        provider=provider,
        namespace=namespace,
        repo_name=payload.get("name") or repo_name,
        default_branch=payload.get("default_branch") or payload.get("defaultBranch") or "main",
        permissions=permissions,
        issue_count=payload.get("open_issues_count", payload.get("open_issues", 0)),
        pull_request_count=payload.get("open_pull_requests_count", payload.get("open_prs", 0)),
        private=payload.get("private", False),
        html_url=payload.get("html_url") or payload.get("web_url") or "",
        clone_url=payload.get("clone_url") or payload.get("ssh_url") or payload.get("ssh_url_to_repo") or "",
    )


def _record(registry: RepoRegistry, repo_id: Any) -> RepoRecord:
    if not isinstance(registry, RepoRegistry):
        raise RepoForgeProviderError("registry must be a RepoRegistry")
    try:
        return registry.get(repo_id)
    except RepoRegistryError as exc:
        raise RepoForgeProviderError(str(exc)) from exc


def _repo_name_from_record(record: RepoRecord) -> str:
    path_tail = record.project_root.strip("/").rsplit("/", 1)[-1]
    return path_tail or record.repo_id


def _normalize_provider(value: Any) -> str:
    provider = _normalize_text(value, field_name="provider", max_len=40).lower().replace("-", "_")
    if provider not in _PROVIDERS:
        raise RepoForgeProviderError(f"unsupported provider: {value!r}")
    return provider


def _normalize_name(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name, max_len=100)
    if not _SAFE_NAME_RE.fullmatch(text) or text.startswith("-"):
        raise RepoForgeProviderError(f"{field_name} contains unsupported characters")
    return text


def _normalize_optional_id(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name, allow_empty=True, max_len=100)
    if not text:
        return ""
    if not _SAFE_NAME_RE.fullmatch(text):
        raise RepoForgeProviderError(f"{field_name} contains unsupported characters")
    return text


def _normalize_branch(value: Any) -> str:
    branch = _normalize_text(value, field_name="default_branch", max_len=120)
    if (
        not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$", branch)
        or branch.startswith(("-", "/", "."))
        or branch.endswith("/")
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or branch.lower().endswith(".lock")
    ):
        raise RepoForgeProviderError("default_branch contains unsupported branch syntax")
    return branch


def _normalize_count(value: Any, *, field_name: str) -> int:
    try:
        count = int(value)
    except Exception as exc:
        raise RepoForgeProviderError(f"{field_name} must be an integer") from exc
    if count < 0 or count > 1_000_000:
        raise RepoForgeProviderError(f"{field_name} is out of range")
    return count


def _normalize_permissions(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise RepoForgeProviderError("permissions must be a list")
    result: list[str] = []
    for item in values:
        permission = _normalize_text(item, field_name="permission", max_len=40).lower().replace("-", "_")
        if not re.fullmatch(r"[a-z_]{1,40}", permission):
            raise RepoForgeProviderError("permission contains unsupported characters")
        if permission not in result:
            result.append(permission)
    return tuple(result)


def _permission_names_from_mapping(values: Mapping[str, Any]) -> tuple[str, ...]:
    names = []
    for key, enabled in values.items():
        if bool(enabled):
            names.append(str(key))
    return tuple(names)


def _normalize_api_base_url(value: Any, *, provider: str) -> str:
    raw = _normalize_text(value, field_name="api_base_url", allow_empty=True, max_len=240)
    if not raw:
        return _DEFAULT_API_BASES[provider]
    return _redact_url(raw)


def _redact_url(value: Any, *, allow_empty: bool = False) -> str:
    raw = _normalize_text(value, field_name="url", allow_empty=allow_empty, max_len=240)
    if not raw:
        return ""
    split = urlsplit(raw)
    if not split.scheme or not split.netloc:
        raise RepoForgeProviderError("url must be absolute")
    safe_netloc = split.hostname or ""
    if split.port:
        safe_netloc = f"{safe_netloc}:{split.port}"
    redacted = urlunsplit((split.scheme.lower(), safe_netloc, split.path.rstrip("/"), "", ""))
    return redact_remote_url(redacted)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise RepoForgeProviderError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise RepoForgeProviderError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise RepoForgeProviderError(f"{field_name} appears to contain secret material")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name, max_len=80).lower().replace("-", "_")
    if text not in choices:
        raise RepoForgeProviderError(f"unsupported {field_name}: {value!r}")
    return text


def _provider_gate(*, provider: str, namespace: str, repo_name: str, api_base_url: str) -> str:
    return (
        f"{provider}/{namespace}/{repo_name} metadata fetch via {api_base_url} requires secure handoff "
        "or existing server-side credentials; credential secrets must stay outside chat and repo."
    )


def _repo_creation_gate(*, provider: str, namespace: str, repo_name: str, requested: bool) -> str:
    target = f"{provider}/{namespace}/{repo_name}"
    if requested:
        return f"Repo creation for {target} is requested but remains a separate confirmed live provider action."
    return f"Repo creation for {target} is not requested; keep creation behind a separate confirmation."


def _next_human_decision(decision: str, *, create_repo_requested: bool) -> str:
    if decision == "plan_ready" and create_repo_requested:
        return "Read-only metadata can run now; repo creation still needs a separate explicit provider-create Go."
    if decision == "plan_ready":
        return "Read-only metadata can run now; repo creation remains off."
    if decision == "blocked":
        return "Do not call the forge provider until the blocked privacy or input decision changes."
    if create_repo_requested:
        return (
            "Complete the metadata gates first; repo creation still needs a separate explicit "
            "provider-create Go."
        )
    return "Select provider/namespace and provide secure auth readiness, confirmed=true, operator_go=true, and live_enabled=true."


def _safe_error(exc: Exception) -> str:
    return _SECRET_RE.sub("[redacted-secret]", str(exc))[:400]
