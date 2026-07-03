"""Provider-neutral GitHub issue field contract.

This module intentionally does not talk to GitHub. It validates Odysseus'
canonical issue fields and prepares deterministic projection plans for later
GitHub Issue Fields or label fallback adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class GitHubIssueFieldError(ValueError):
    """Raised when an issue field contract would fail open."""


@dataclass(frozen=True, slots=True)
class IssueFieldDefinition:
    name: str
    field_type: str
    allowed_values: tuple[str, ...] = ()
    github_field_name: str = ""
    label_prefix: str = ""
    allow_label_fallback: bool = True

    def __post_init__(self) -> None:
        normalized_name = _normalize_name(self.name)
        object.__setattr__(self, "name", normalized_name)
        if self.field_type not in {"single_select", "text", "date", "issue_ref"}:
            raise GitHubIssueFieldError(f"unsupported issue field type: {self.field_type}")
        object.__setattr__(self, "allowed_values", tuple(_normalize_token(v) for v in self.allowed_values))
        if self.field_type == "single_select" and not self.allowed_values:
            raise GitHubIssueFieldError(f"single_select field {self.name} requires allowed values")
        if self.label_prefix:
            object.__setattr__(self, "label_prefix", _normalize_prefix(self.label_prefix))


@dataclass(frozen=True, slots=True)
class IssueFieldProjection:
    field: str
    value: str
    method: str
    target: str

    def to_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "value": self.value,
            "method": self.method,
            "target": self.target,
        }


def default_issue_field_definitions() -> dict[str, IssueFieldDefinition]:
    return {definition.name: definition for definition in DEFAULT_FIELD_DEFINITIONS}


def validate_issue_fields(
    fields: Mapping[str, Any],
    *,
    definitions: Mapping[str, IssueFieldDefinition] | None = None,
) -> dict[str, str]:
    """Validate and normalize a field payload.

    Unknown fields are rejected unless the caller supplies an explicit
    definition map containing them.
    """

    definition_map = dict(definitions or default_issue_field_definitions())
    normalized: dict[str, str] = {}
    for raw_name, raw_value in fields.items():
        name = _normalize_name(raw_name)
        definition = definition_map.get(name)
        if definition is None:
            raise GitHubIssueFieldError(f"unknown issue field: {name}")
        value = _normalize_value(raw_value, definition=definition)
        if value:
            normalized[name] = value
    return normalized


def build_issue_field_projection(
    fields: Mapping[str, Any],
    *,
    github_field_ids: Mapping[str, str] | None = None,
    definitions: Mapping[str, IssueFieldDefinition] | None = None,
    allow_label_fallback: bool = True,
) -> tuple[IssueFieldProjection, ...]:
    """Build deterministic write plans without performing writes."""

    definition_map = dict(definitions or default_issue_field_definitions())
    normalized_fields = validate_issue_fields(fields, definitions=definition_map)
    github_ids = {_normalize_name(k): _normalize_text(v) for k, v in (github_field_ids or {}).items()}
    projections: list[IssueFieldProjection] = []
    for name in sorted(normalized_fields):
        definition = definition_map[name]
        value = normalized_fields[name]
        github_field_id = github_ids.get(name)
        if github_field_id:
            projections.append(
                IssueFieldProjection(
                    field=name,
                    value=value,
                    method="github_field",
                    target=github_field_id,
                )
            )
            continue
        if allow_label_fallback and definition.allow_label_fallback and definition.label_prefix:
            projections.append(
                IssueFieldProjection(
                    field=name,
                    value=value,
                    method="label",
                    target=f"{definition.label_prefix}{_normalize_label_value(value)}",
                )
            )
            continue
        projections.append(
            IssueFieldProjection(
                field=name,
                value=value,
                method="local_only",
                target=definition.github_field_name or name,
            )
        )
    return tuple(projections)


def projection_to_write_report(projections: tuple[IssueFieldProjection, ...]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "field": projection.field,
            "method": projection.method,
            "status": "planned",
            "target": projection.target,
            "error_redacted": "",
        }
        for projection in projections
    )


def _normalize_name(value: Any) -> str:
    text = _normalize_text(value).lower().replace("-", "_").replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise GitHubIssueFieldError("issue field name must be a safe identifier")
    return text


def _normalize_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise GitHubIssueFieldError("issue field value must not be empty")
    if any(marker in text.lower() for marker in ("authorization:", "ghp_", "github_pat_", "bearer ")):
        raise GitHubIssueFieldError("issue field value contains a forbidden secret marker")
    return text


def _normalize_token(value: Any) -> str:
    text = _normalize_text(value).lower().replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise GitHubIssueFieldError("issue field token must be a safe identifier")
    return text


def _normalize_prefix(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text.endswith("/"):
        raise GitHubIssueFieldError("label_prefix must end with /")
    prefix = text[:-1]
    if not _SAFE_LABEL_RE.fullmatch(prefix):
        raise GitHubIssueFieldError("label_prefix must be a safe label prefix")
    return f"{prefix}/"


def _normalize_value(value: Any, *, definition: IssueFieldDefinition) -> str:
    if value is None or value == "":
        return ""
    if definition.field_type == "single_select":
        token = _normalize_token(value)
        if token not in definition.allowed_values:
            raise GitHubIssueFieldError(f"unsupported value for {definition.name}: {token}")
        return token
    if definition.field_type == "date":
        text = _normalize_text(value)
        if not _DATE_RE.fullmatch(text):
            raise GitHubIssueFieldError(f"{definition.name} must use YYYY-MM-DD")
        return text
    if definition.field_type == "issue_ref":
        text = _normalize_text(value)
        if not re.fullmatch(r"(#\d+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)", text):
            raise GitHubIssueFieldError(f"{definition.name} must be #123 or owner/repo#123")
        return text
    return _normalize_label_value(_normalize_text(value))


def _normalize_label_value(value: str) -> str:
    text = value.strip().lower().replace(" ", "-").replace("_", "-")
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-")
    if not text or not _SAFE_LABEL_RE.fullmatch(text):
        raise GitHubIssueFieldError("issue field label value must be safe")
    return text


DEFAULT_FIELD_DEFINITIONS: tuple[IssueFieldDefinition, ...] = (
    IssueFieldDefinition(
        name="type",
        field_type="single_select",
        allowed_values=("bug", "task", "feature", "question", "docs"),
        github_field_name="Type",
        label_prefix="type/",
    ),
    IssueFieldDefinition(
        name="priority",
        field_type="single_select",
        allowed_values=("urgent", "high", "medium", "low"),
        github_field_name="Priority",
        label_prefix="priority/",
    ),
    IssueFieldDefinition(
        name="effort",
        field_type="single_select",
        allowed_values=("high", "medium", "low"),
        github_field_name="Effort",
        label_prefix="effort/",
    ),
    IssueFieldDefinition(
        name="area",
        field_type="text",
        github_field_name="Area",
        label_prefix="area/",
    ),
    IssueFieldDefinition(
        name="status",
        field_type="single_select",
        allowed_values=("triage", "ready", "blocked", "in_progress", "done"),
        github_field_name="Status",
        label_prefix="status/",
    ),
    IssueFieldDefinition(
        name="start_date",
        field_type="date",
        github_field_name="Start date",
        allow_label_fallback=False,
    ),
    IssueFieldDefinition(
        name="target_date",
        field_type="date",
        github_field_name="Target date",
        allow_label_fallback=False,
    ),
    IssueFieldDefinition(
        name="duplicate_of",
        field_type="issue_ref",
        github_field_name="Duplicate of",
        label_prefix="duplicate/",
    ),
)
