"""Dashboard-facing summary for offline live-integration readiness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.live_integration_readiness_index import (
    LiveIntegrationReadinessIndex,
    build_live_integration_readiness_index,
)


_DASHBOARD_STATUSES = (
    "ready_for_operator_review",
    "needs_manual_evidence",
    "blocked",
    "deferred",
)

_BLOCKED_LIVE_ACTIONS = (
    "host_command_execution",
    "network_request",
    "provider_call",
    "telegram_send",
    "plugin_import",
    "runtime_enablement",
    "secret_or_token_capture",
    "automatic_release_go",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _DASHBOARD_STATUSES:
        raise ValueError("unsupported live dashboard readiness status")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class LiveDashboardReadinessTile:
    tile_id: str
    status: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_id": self.tile_id,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class LiveDashboardReadinessSummary:
    status: str
    external_release_ready: bool
    tiles: tuple[LiveDashboardReadinessTile, ...]
    next_actions: tuple[str, ...]
    blocked_live_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "external_release_ready": self.external_release_ready,
            "tiles": tuple(tile.to_dict() for tile in self.tiles),
            "next_actions": self.next_actions,
            "blocked_live_actions": self.blocked_live_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Dashboard Readiness Summary",
            "",
            f"- Status: `{self.status}`",
            f"- External release ready: `{str(self.external_release_ready).lower()}`",
            "",
            "## Tiles",
        ]
        for tile in self.tiles:
            lines.append(f"- `{tile.tile_id}`: {tile.status} - {tile.summary}")
        if self.next_actions:
            lines.extend(["", "## Next Actions"])
            for action in self.next_actions:
                lines.append(f"- {action}")
        if self.blocked_live_actions:
            lines.extend(["", "## Blocked Live Actions"])
            for action in self.blocked_live_actions:
                lines.append(f"- `{action}`")
        return "\n".join(lines).rstrip()


def _dashboard_status_from_index(index: LiveIntegrationReadinessIndex) -> str:
    decision = index.decision.decision
    if decision == "integration_readiness_ready":
        return "ready_for_operator_review"
    if decision == "needs_manual_evidence":
        return "needs_manual_evidence"
    if decision == "deferred":
        return "deferred"
    return "blocked"


def build_live_dashboard_readiness_summary(
    index: LiveIntegrationReadinessIndex | None = None,
) -> LiveDashboardReadinessSummary:
    source = index or build_live_integration_readiness_index()
    if not isinstance(source, LiveIntegrationReadinessIndex):
        raise ValueError("index must be a LiveIntegrationReadinessIndex")

    status = _dashboard_status_from_index(source)
    tiles = tuple(
        LiveDashboardReadinessTile(
            tile_id=gate.gate_id,
            status=gate.status,
            summary=gate.summary,
        )
        for gate in source.gates
    )
    next_actions = (
        ()
        if status == "blocked"
        else _normalize_tuple(source.next_allowed_actions, field_name="next_action")
    )

    return LiveDashboardReadinessSummary(
        status=_normalize_status(status),
        external_release_ready=False,
        tiles=tiles,
        next_actions=next_actions,
        blocked_live_actions=_normalize_tuple(_BLOCKED_LIVE_ACTIONS, field_name="blocked_live_action"),
    )
