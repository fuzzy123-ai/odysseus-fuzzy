"""GitHub Issue Fields projection helpers.

This module contains no token handling and no direct GitHub transport. Callers
must inject a bounded client, which keeps live writes gated outside this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, Sequence

from src.github_issue_fields import (
    IssueFieldProjection,
    build_issue_field_projection,
    default_issue_field_definitions,
    projection_to_write_report,
    validate_issue_fields,
)


_SECRET_MARKERS = ("authorization:", "bearer ", "ghp_", "github_pat_", "cookie:", "set-cookie:")


class GitHubIssueProjectionError(RuntimeError):
    """Raised when projection setup cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class GitHubIssueProjectField:
    name: str
    field_id: str


@dataclass(frozen=True, slots=True)
class GitHubIssueProjectionPlan:
    owner: str
    repository: str
    issue_ref: str
    issue_node_id: str
    cache_hit: bool
    field_ids: Mapping[str, str]
    projections: tuple[IssueFieldProjection, ...]

    def write_report(self) -> tuple[dict[str, str], ...]:
        return projection_to_write_report(self.projections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repository": self.repository,
            "issue_ref": self.issue_ref,
            "issue_node_id_present": bool(self.issue_node_id),
            "cache_hit": self.cache_hit,
            "cached_field_count": len(self.field_ids),
            "write_report": self.write_report(),
        }


@dataclass(frozen=True, slots=True)
class GitHubIssueProjectionResult:
    plan: GitHubIssueProjectionPlan
    write_report: tuple[dict[str, str], ...]

    @property
    def applied_count(self) -> int:
        return sum(1 for item in self.write_report if item.get("status") == "applied")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.write_report if item.get("status") == "failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.plan.to_dict(),
            "write_report": self.write_report,
            "applied_count": self.applied_count,
            "failed_count": self.failed_count,
        }


class GitHubIssueFieldCache(Protocol):
    def get_field_ids(self, *, owner: str, repository: str) -> Mapping[str, str] | None:
        ...

    def set_field_ids(self, *, owner: str, repository: str, field_ids: Mapping[str, str]) -> None:
        ...


class GitHubIssueProjectionClient(Protocol):
    def list_issue_fields(self, *, owner: str, repository: str) -> Sequence[GitHubIssueProjectField]:
        ...

    def set_issue_field(self, *, issue_node_id: str, field_id: str, value: str) -> None:
        ...

    def add_issue_label(self, *, issue_ref: str, label: str) -> None:
        ...


class InMemoryGitHubIssueFieldCache:
    """Small deterministic cache keyed by owner and repository."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], dict[str, str]] = {}

    def get_field_ids(self, *, owner: str, repository: str) -> Mapping[str, str] | None:
        value = self._cache.get((_safe_scope(owner), _safe_scope(repository)))
        if value is None:
            return None
        return dict(value)

    def set_field_ids(self, *, owner: str, repository: str, field_ids: Mapping[str, str]) -> None:
        self._cache[(_safe_scope(owner), _safe_scope(repository))] = {
            _canonical_field_name(name): _safe_target(field_id)
            for name, field_id in field_ids.items()
            if _canonical_field_name(name)
        }


def prepare_github_issue_projection(
    *,
    client: GitHubIssueProjectionClient,
    cache: GitHubIssueFieldCache,
    owner: str,
    repository: str,
    issue_ref: str,
    issue_node_id: str = "",
    fields: Mapping[str, Any],
    allow_label_fallback: bool = True,
    refresh_fields: bool = False,
) -> GitHubIssueProjectionPlan:
    """Build a projection plan and cache GitHub Issue Field IDs per repo."""

    safe_owner = _safe_scope(owner)
    safe_repository = _safe_scope(repository)
    normalized_fields = validate_issue_fields(fields)
    cached = None if refresh_fields else cache.get_field_ids(owner=safe_owner, repository=safe_repository)
    cache_hit = cached is not None
    field_ids = dict(cached or {})
    if cached is None:
        field_ids = _field_ids_from_client(
            client.list_issue_fields(owner=safe_owner, repository=safe_repository)
        )
        cache.set_field_ids(owner=safe_owner, repository=safe_repository, field_ids=field_ids)
    projections = build_issue_field_projection(
        normalized_fields,
        github_field_ids=field_ids,
        allow_label_fallback=allow_label_fallback,
    )
    return GitHubIssueProjectionPlan(
        owner=safe_owner,
        repository=safe_repository,
        issue_ref=_safe_target(issue_ref),
        issue_node_id=_safe_target(issue_node_id) if issue_node_id else "",
        cache_hit=cache_hit,
        field_ids=field_ids,
        projections=projections,
    )


def apply_github_issue_projection(
    *,
    client: GitHubIssueProjectionClient,
    plan: GitHubIssueProjectionPlan,
    apply: bool = False,
    apply_label_fallbacks: bool = True,
) -> GitHubIssueProjectionResult:
    """Apply or preview a projection plan with per-field redacted results."""

    report: list[dict[str, str]] = []
    for projection in plan.projections:
        item = {
            "field": projection.field,
            "method": projection.method,
            "status": "planned",
            "target": projection.target,
            "error_redacted": "",
        }
        if not apply:
            report.append(item)
            continue
        try:
            if projection.method == "github_field":
                if not plan.issue_node_id:
                    item["status"] = "skipped"
                    item["error_redacted"] = "missing issue_node_id"
                else:
                    client.set_issue_field(
                        issue_node_id=plan.issue_node_id,
                        field_id=projection.target,
                        value=projection.value,
                    )
                    item["status"] = "applied"
            elif projection.method == "label":
                if not apply_label_fallbacks:
                    item["status"] = "skipped"
                    item["error_redacted"] = "label fallback disabled"
                else:
                    client.add_issue_label(issue_ref=plan.issue_ref, label=projection.target)
                    item["status"] = "applied"
            else:
                item["status"] = "skipped"
                item["error_redacted"] = "missing provider field"
        except Exception as exc:  # pragma: no cover - exercised by tests
            item["status"] = "failed"
            item["error_redacted"] = _redact_error(exc)
        report.append(item)
    return GitHubIssueProjectionResult(plan=plan, write_report=tuple(report))


def _field_ids_from_client(fields: Sequence[GitHubIssueProjectField]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields:
        name = _canonical_field_name(field.name)
        if name:
            result[name] = _safe_target(field.field_id)
    return result


def _canonical_field_name(value: Any) -> str:
    text = " ".join(str(value or "").split()).lower().replace("-", "_").replace(" ", "_")
    definitions = default_issue_field_definitions()
    for name, definition in definitions.items():
        candidates = {
            name,
            definition.github_field_name.lower().replace("-", "_").replace(" ", "_"),
        }
        if text in candidates:
            return name
    return ""


def _safe_scope(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text or any(marker in text.lower() for marker in _SECRET_MARKERS):
        raise GitHubIssueProjectionError("unsafe projection scope")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|[A-Za-z0-9_.@-]+", text):
        raise GitHubIssueProjectionError("projection scope must be owner or owner/repo")
    return text


def _safe_target(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text or any(marker in text.lower() for marker in _SECRET_MARKERS):
        raise GitHubIssueProjectionError("unsafe projection target")
    if len(text) > 256:
        raise GitHubIssueProjectionError("projection target too long")
    return text


def _redact_error(exc: Exception) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return f"{exc.__class__.__name__}: redacted"
    return text[:160]
