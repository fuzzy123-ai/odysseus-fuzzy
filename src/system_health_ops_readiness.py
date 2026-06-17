"""Security and ops readiness checklist models for system health foundations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.system_health_agent_interface import HealthAgentInterfaceError


class OpsReadinessStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


_ALLOWED_ITEM_IDS = (
    "host_agent_boundary",
    "no_core_host_commands",
    "no_socket_mount_required",
    "token_logging_blocked",
    "collector_unknown_safe",
    "alert_dedupe_defined",
    "no_auto_repair",
)


def _normalize_item_id(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip().lower()
    if text not in _ALLOWED_ITEM_IDS:
        allowed = ", ".join(_ALLOWED_ITEM_IDS)
        raise HealthAgentInterfaceError(f"item_id must be one of: {allowed}")
    return text


def _normalize_status(value: OpsReadinessStatus | str) -> OpsReadinessStatus:
    if isinstance(value, OpsReadinessStatus):
        return value
    text = " ".join(str(value or "").split()).strip().lower()
    try:
        return OpsReadinessStatus(text)
    except ValueError as exc:
        raise HealthAgentInterfaceError("unsupported ops readiness status") from exc


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _derive_overall(items: tuple["OpsReadinessItem", ...]) -> str:
    statuses = {item.status for item in items}
    if OpsReadinessStatus.FAIL in statuses:
        return "no_go"
    if OpsReadinessStatus.WARN in statuses or OpsReadinessStatus.UNKNOWN in statuses:
        return "warn"
    return "go"


@dataclass(frozen=True, slots=True)
class OpsReadinessItem:
    item_id: str
    status: OpsReadinessStatus
    summary: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        item_id: Any,
        status: OpsReadinessStatus | str,
        summary: Any,
        next_action: Any = "",
    ) -> "OpsReadinessItem":
        normalized_status = _normalize_status(status)
        normalized_next = _normalize_text(next_action, field_name="next_action", allow_empty=True)
        if normalized_status in {OpsReadinessStatus.WARN, OpsReadinessStatus.FAIL, OpsReadinessStatus.UNKNOWN} and not normalized_next:
            normalized_next = "operator review required before live rollout"
        return cls(
            item_id=_normalize_item_id(item_id),
            status=normalized_status,
            summary=_normalize_text(summary, field_name="summary"),
            next_action=normalized_next,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "status": self.status.value,
            "summary": self.summary,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class OpsReadinessReport:
    mode: str
    overall_status: str
    items: tuple[OpsReadinessItem, ...]

    @classmethod
    def create(
        cls,
        *,
        mode: Any,
        items: Iterable[OpsReadinessItem],
    ) -> "OpsReadinessReport":
        normalized_items = tuple(items)
        if not normalized_items:
            raise HealthAgentInterfaceError("items must not be empty")
        if any(not isinstance(item, OpsReadinessItem) for item in normalized_items):
            raise HealthAgentInterfaceError("items must contain OpsReadinessItem instances")
        return cls(
            mode=_normalize_text(mode, field_name="mode"),
            overall_status=_derive_overall(normalized_items),
            items=tuple(sorted(normalized_items, key=lambda item: item.item_id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "overall_status": self.overall_status,
            "items": tuple(item.to_dict() for item in self.items),
        }


def build_foundation_ops_readiness_report() -> OpsReadinessReport:
    return OpsReadinessReport.create(
        mode="foundation",
        items=(
            OpsReadinessItem.create(
                item_id="host_agent_boundary",
                status="pass",
                summary="host access remains outside the core application boundary",
            ),
            OpsReadinessItem.create(
                item_id="no_core_host_commands",
                status="pass",
                summary="core health models do not execute host commands directly",
            ),
            OpsReadinessItem.create(
                item_id="no_socket_mount_required",
                status="pass",
                summary="container and host checks do not require socket mounts in foundation mode",
            ),
            OpsReadinessItem.create(
                item_id="token_logging_blocked",
                status="pass",
                summary="alerting and telegram models avoid token-handling and secret logging paths",
            ),
            OpsReadinessItem.create(
                item_id="collector_unknown_safe",
                status="pass",
                summary="collector parsers degrade to unknown or unsupported instead of guessing health",
            ),
            OpsReadinessItem.create(
                item_id="alert_dedupe_defined",
                status="pass",
                summary="alert dedupe and cooldown decisions are modeled explicitly",
            ),
            OpsReadinessItem.create(
                item_id="no_auto_repair",
                status="warn",
                summary="foundation mode intentionally omits auto-repair and operator execution hooks",
                next_action="keep operator-owned remediation outside the model layer until runtime review is approved",
            ),
        ),
    )
