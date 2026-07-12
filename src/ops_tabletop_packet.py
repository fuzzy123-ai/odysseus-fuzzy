"""Synthetic tabletop packet for the read-only Ops Security Console."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from src.ops_console_snapshot import build_ops_console_snapshot
from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_remediation_actions import prepare_remediation_plan
from src.security_response_policy import decide_incident_response


OPS_TABLETOP_PACKET_SCHEMA = "odysseus.ops_tabletop_packet.v1"

_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv|opt)/|~[\\/])", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
_FORBIDDEN_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "password",
    "cookie",
    "token=",
    "telegram_token",
    "chat_id",
    "private_document_text",
    "private_email_body",
    "raw_log",
    "raw_output",
    "unredacted_tool_output",
)


class OpsTabletopPacketError(ValueError):
    """Raised when a tabletop packet would be unsafe or invalid."""


def build_ops_tabletop_packet(
    *,
    scenario_id: str = "ops-tabletop-remediation-gate",
    title: str = "Synthetic remediation gate tabletop",
    generated_at: str = "2026-07-06T12:00:00Z",
    approved_gates: Iterable[Any] = (),
) -> dict[str, Any]:
    """Build a deterministic synthetic tabletop packet.

    The packet is a fixture contract only. It creates synthetic source packets,
    composes the read-only Ops Console snapshot and records expected operator
    assertions; it never performs live checks or remediation.
    """

    safe_id = _safe_label(scenario_id, field="scenario_id")
    safe_title = _safe_summary(title, field="title")
    actions = (
        build_recommended_action(
            action_type="redacted_debug_bundle",
            summary="Prepare a redacted diagnostic bundle for operator review.",
            risk="No private content, no host commands and no live mutation.",
            action_id="act-debug-bundle",
        ),
        build_recommended_action(
            action_type="service_restart",
            summary="Prepare a bounded service restart request for the affected component.",
            risk="Brief service interruption requires operator approval and rollback awareness.",
            action_id="act-service-restart",
        ),
    )
    incident = build_security_incident(
        incident_id="inc-tabletop-remediation",
        level=3,
        severity="high",
        confidence=0.88,
        status="open",
        trigger="Synthetic service health and security signals exceeded the tabletop threshold.",
        affected_surfaces=("system_health", "security", "remediation"),
        correlation_ids=("tabletop-corr-service-health",),
        evidence_refs=("tabletop-evidence-service-health", "tabletop-evidence-security-policy"),
        recommended_actions=actions,
    )
    response_policy = decide_incident_response(
        incident,
        approved_gates=tuple(_safe_label(gate, field="approved_gate") for gate in approved_gates),
        incident_mode=True,
    )
    remediation_plan = prepare_remediation_plan(
        incident,
        approved_gates=tuple(_safe_label(gate, field="approved_gate") for gate in approved_gates),
        requested_action_ids=("act-service-restart",),
        incident_mode=True,
    )
    snapshot = build_ops_console_snapshot(
        security_incident=incident,
        response_policy=response_policy,
        remediation_plan=remediation_plan,
        timeline_id=f"{safe_id}-timeline",
        generated_at=generated_at,
    )
    packet = {
        "schema": OPS_TABLETOP_PACKET_SCHEMA,
        "scenario_id": safe_id,
        "title": safe_title,
        "generated_at": generated_at,
        "mode": "synthetic_tabletop",
        "status": snapshot["status"],
        "snapshot": snapshot,
        "policy_decision": {
            "decision": str(response_policy.get("decision") or ""),
            "reason": str(response_policy.get("reason") or ""),
            "allowed_to_execute": bool(response_policy.get("allowed_to_execute")),
            "operator_gate_required": bool(response_policy.get("operator_gate_required")),
        },
        "remediation_action_results": tuple(
            {
                "action_id": str(action.get("action_id") or ""),
                "status": str(action.get("status") or ""),
                "policy_reason": str(action.get("policy_reason") or ""),
                "allowed_to_execute": bool(action.get("allowed_to_execute")),
                "writes_performed": bool(action.get("writes_performed")),
            }
            for action in remediation_plan.get("actions", ())
            if isinstance(action, Mapping)
        ),
        "expected_operator_steps": (
            "Review the timeline signal, decision and action-plan events.",
            "Confirm raw content, raw logs, host paths, tokens and chat targets are not visible.",
            "Decide whether to approve or deny the listed operator gates.",
            "If approved, perform live remediation only outside this tabletop packet with explicit Go.",
        ),
        "assertions": _tabletop_assertions(snapshot),
        "operator_gates": snapshot["operator_gates"],
        "live_go_required": tuple(snapshot["operator_gates"]),
        "live_actions_performed": False,
        "host_commands_performed": False,
        "writes_performed": False,
        "remediation_performed": False,
        "raw_content_visible": False,
        "raw_logs_visible": False,
        "host_paths_visible": False,
        "tokens_visible": False,
        "chat_targets_visible": False,
    }
    _reject_forbidden_payload(packet)
    return packet


def validate_ops_tabletop_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a tabletop packet without trusting its producer."""

    if not isinstance(packet, Mapping):
        raise OpsTabletopPacketError("packet must be a mapping")
    if packet.get("schema") != OPS_TABLETOP_PACKET_SCHEMA:
        raise OpsTabletopPacketError("unsupported tabletop packet schema")
    _reject_forbidden_payload(packet)
    snapshot = packet.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise OpsTabletopPacketError("snapshot must be present")
    if snapshot.get("schema") != "odysseus.ops_console.snapshot.v1":
        raise OpsTabletopPacketError("snapshot has unsupported schema")
    for flag in (
        "live_actions_performed",
        "host_commands_performed",
        "writes_performed",
        "remediation_performed",
        "raw_content_visible",
        "raw_logs_visible",
        "host_paths_visible",
        "tokens_visible",
        "chat_targets_visible",
    ):
        if bool(packet.get(flag)):
            raise OpsTabletopPacketError(f"{flag} must be false")
    gates = tuple(str(gate) for gate in packet.get("operator_gates") or ())
    if not gates:
        raise OpsTabletopPacketError("tabletop packet must include an operator gate")
    return {
        "schema": "odysseus.ops_tabletop_packet.validation.v1",
        "status": "valid",
        "scenario_id": str(packet.get("scenario_id") or ""),
        "operator_gate_count": len(gates),
        "timeline_event_count": int(snapshot.get("counts", {}).get("timeline_events") or 0),
        "raw_content_visible": False,
        "live_actions_performed": False,
    }


def _tabletop_assertions(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return (
        {
            "id": "snapshot_is_read_only",
            "passed": all(
                bool(snapshot.get(flag)) is False
                for flag in ("live_queries_performed", "host_commands_performed", "writes_performed", "remediation_performed")
            ),
        },
        {
            "id": "operator_gate_present",
            "passed": bool(snapshot.get("operator_gates")),
        },
        {
            "id": "timeline_contains_remediation_action_plan",
            "passed": any(
                event.get("surface") == "remediation" and event.get("stage") == "action_plan"
                for event in snapshot.get("timeline", {}).get("events", ())
                if isinstance(event, Mapping)
            ),
        },
        {
            "id": "timeline_contains_operator_gate",
            "passed": any(
                event.get("stage") == "operator_gate"
                for event in snapshot.get("timeline", {}).get("events", ())
                if isinstance(event, Mapping)
            ),
        },
    )


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OpsTabletopPacketError(f"{field} must not be empty")
    _reject_forbidden_text(text, field=field)
    if len(text) > 180:
        raise OpsTabletopPacketError(f"{field} is too long")
    if not _SAFE_LABEL_RE.fullmatch(text):
        raise OpsTabletopPacketError(f"{field} contains unsafe characters")
    return text


def _safe_summary(value: Any, *, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise OpsTabletopPacketError(f"{field} must not be empty")
    _reject_forbidden_text(text, field=field)
    if len(text) > 280:
        raise OpsTabletopPacketError(f"{field} is too long")
    return text


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_forbidden_payload(nested)
        return
    if isinstance(value, (tuple, list, set)):
        for nested in value:
            _reject_forbidden_payload(nested)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
            raise OpsTabletopPacketError("packet contains a forbidden marker")
        if _HOST_PATH_RE.search(value) or _IPV4_RE.search(value) or _EMAIL_RE.search(value):
            raise OpsTabletopPacketError("packet contains a raw private identifier")


def _reject_forbidden_text(text: str, *, field: str) -> None:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
        raise OpsTabletopPacketError(f"{field} contains a forbidden marker")
    if _HOST_PATH_RE.search(str(text or "")) or _IPV4_RE.search(str(text or "")) or _EMAIL_RE.search(str(text or "")):
        raise OpsTabletopPacketError(f"{field} contains a raw private identifier")
