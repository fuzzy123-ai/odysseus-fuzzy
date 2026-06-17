"""Renderers for operator activation packet summaries."""

from __future__ import annotations

import json
from typing import Any

from src.orchestration_operator_activation_packet import (
    OperatorActivationPacket,
    OperatorActivationPacketSection,
    OperatorActivationPacketState,
)


def _normalize_packet(packet: OperatorActivationPacket) -> OperatorActivationPacket:
    if not isinstance(packet, OperatorActivationPacket):
        raise TypeError("packet must be an OperatorActivationPacket")
    return packet


def _section_map(packet: OperatorActivationPacket) -> dict[str, OperatorActivationPacketSection]:
    return {section.section_id: section for section in packet.sections}


def _decision_copy(state: OperatorActivationPacketState) -> tuple[str, str]:
    if state == OperatorActivationPacketState.READY_FOR_REVIEW:
        return "Ready for review", "Operator may review the packet, but runtime actions remain blocked."
    if state == OperatorActivationPacketState.BLOCKED:
        return "Blocked", "Activation cannot proceed until blocked checklist or gate conditions are resolved."
    if state == OperatorActivationPacketState.APPROVED_PENDING_RUNTIME_GATE:
        return "Approved pending runtime gate", "Operator approval exists, but runtime gates still stay closed."
    if state == OperatorActivationPacketState.CANCELLED:
        return "Cancelled", "Activation has been cancelled and should not advance."
    return "Deferred", "Activation stays deferred until review inputs are complete."


def render_activation_packet_json(packet: OperatorActivationPacket) -> str:
    normalized = _normalize_packet(packet)
    return json.dumps(normalized.to_dict(), indent=2, sort_keys=True)


def render_activation_packet_markdown(packet: OperatorActivationPacket) -> str:
    normalized = _normalize_packet(packet)
    sections = _section_map(normalized)
    decision_title, decision_copy = _decision_copy(normalized.state)

    audit = sections.get("audit")
    checklist = sections.get("checklist")
    runtime = sections.get("runtime_gates")

    blocked_actions = ", ".join(normalized.blocked_runtime_actions) if normalized.blocked_runtime_actions else "none"
    operator_next_step = {
        OperatorActivationPacketState.READY_FOR_REVIEW: "Review checklist and audit evidence, then keep runtime hooks disabled until an explicit runtime phase is approved.",
        OperatorActivationPacketState.BLOCKED: "Resolve blocked checklist or gate conditions before revisiting activation.",
        OperatorActivationPacketState.APPROVED_PENDING_RUNTIME_GATE: "Do not execute runtime hooks yet; preserve the approval trail and wait for the runtime-gate phase.",
        OperatorActivationPacketState.CANCELLED: "Keep the cancellation recorded and stop further activation preparation.",
        OperatorActivationPacketState.DEFERRED: "Fill missing review inputs and keep the packet in deferred state.",
    }[normalized.state]

    lines = [
        "# Operator Activation Packet",
        "",
        "## Summary",
        f"- State: `{normalized.state.value}`",
        f"- Decision: {decision_title}",
        f"- Packet sections: {len(normalized.sections)}",
        "",
        "## Decision",
        decision_copy,
        "",
        "## Gate Status",
        f"- Runtime gates: {runtime.status if runtime else 'missing'}",
        f"- Runtime summary: {runtime.summary if runtime else 'runtime gate summary unavailable'}",
        "",
        "## Handoff Checklist",
        f"- Checklist status: {checklist.status if checklist else 'missing'}",
        f"- Checklist summary: {checklist.summary if checklist else 'handoff checklist not provided'}",
        f"- Checklist items: {checklist.item_count if checklist else 0}",
        "",
        "## Audit Events",
        f"- Audit status: {audit.status if audit else 'missing'}",
        f"- Audit summary: {audit.summary if audit else 'audit trail not provided'}",
        f"- Event count: {audit.item_count if audit else 0}",
        "",
        "## Evidence",
        "- Evidence is summarized only; raw prompts and logs are intentionally omitted.",
        f"- Evidence placeholder: {'available via audit summary' if audit and audit.item_count else 'not provided'}",
        "",
        "## Blocked Runtime Actions",
        f"- {blocked_actions}",
        "",
        "## Operator Next Step",
        operator_next_step,
    ]
    return "\n".join(lines)
