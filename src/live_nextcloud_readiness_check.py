"""Redacted LIVE10 readiness summary for Nextcloud source-provider planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.nextcloud_source_provider import (
    NextcloudSourceReadinessReport,
    assess_nextcloud_source_provider,
)


_SUMMARY_STATUSES = (
    "ready_for_operator_review",
    "needs_operator_review",
    "blocked",
    "deferred",
)

_BLOCKED_LIVE_ACTIONS = (
    "nextcloud_api_call",
    "webdav_request",
    "credential_or_token_capture",
    "delete_move_or_overwrite",
    "nextcloud_tag_write",
    "inbox_worker_start",
    "graph_or_memory_write",
    "automatic_provider_enablement",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _SUMMARY_STATUSES:
        raise ValueError("unsupported live nextcloud readiness status")
    return text


def _normalize_tuple(values: tuple[Any, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class LiveNextcloudReadinessCheck:
    status: str
    provider_id: str
    source_status: str
    external_release_ready: bool
    reasons: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    next_actions: tuple[str, ...]
    blocked_live_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider_id": self.provider_id,
            "source_status": self.source_status,
            "external_release_ready": self.external_release_ready,
            "reasons": self.reasons,
            "errors": self.errors,
            "warnings": self.warnings,
            "next_actions": self.next_actions,
            "blocked_live_actions": self.blocked_live_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Nextcloud Readiness Check",
            "",
            f"- Status: `{self.status}`",
            f"- Provider: `{self.provider_id}`",
            f"- Source status: `{self.source_status}`",
            f"- External release ready: `{str(self.external_release_ready).lower()}`",
        ]
        if self.reasons:
            lines.extend(["", "## Reasons"])
            for reason in self.reasons:
                lines.append(f"- `{reason}`")
        if self.errors:
            lines.extend(["", "## Errors"])
            for error in self.errors:
                lines.append(f"- `{error}`")
        if self.warnings:
            lines.extend(["", "## Warnings"])
            for warning in self.warnings:
                lines.append(f"- `{warning}`")
        if self.next_actions:
            lines.extend(["", "## Next Actions"])
            for action in self.next_actions:
                lines.append(f"- {action}")
        if self.blocked_live_actions:
            lines.extend(["", "## Blocked Live Actions"])
            for action in self.blocked_live_actions:
                lines.append(f"- `{action}`")
        return "\n".join(lines).rstrip()


def _status_from_source(source_status: str) -> str:
    if source_status == "ready":
        return "ready_for_operator_review"
    if source_status == "partial":
        return "needs_operator_review"
    if source_status == "deferred":
        return "deferred"
    return "blocked"


def build_live_nextcloud_readiness_check(
    config_or_report: Mapping[str, Any] | NextcloudSourceReadinessReport,
) -> LiveNextcloudReadinessCheck:
    if isinstance(config_or_report, NextcloudSourceReadinessReport):
        source = config_or_report
    else:
        source = assess_nextcloud_source_provider(config_or_report)

    source_status = _normalize_text(source.status, field_name="source_status").strip().lower()
    status = _status_from_source(source_status)
    next_actions = () if status == "blocked" else source.next_actions

    return LiveNextcloudReadinessCheck(
        status=_normalize_status(status),
        provider_id=_normalize_text(source.provider_id, field_name="provider_id"),
        source_status=source_status,
        external_release_ready=False,
        reasons=_normalize_tuple(source.reasons, field_name="reason"),
        errors=tuple(issue.code for issue in source.errors),
        warnings=tuple(issue.code for issue in source.warnings),
        next_actions=_normalize_tuple(next_actions, field_name="next_action"),
        blocked_live_actions=_normalize_tuple(_BLOCKED_LIVE_ACTIONS, field_name="blocked_live_action"),
    )
