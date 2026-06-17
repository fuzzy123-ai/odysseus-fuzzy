"""Read-only planning model for plugin operator review packets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "manifest_summary_available",
    "capability_preview_available",
    "local_audit_summary_available",
    "safe_mode_gate_recorded",
    "operator_review_required",
    "auto_approval_disabled",
    "runtime_enablement_disabled",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_review",
    "deferred",
)

_DECISION_VALUES = (
    "review_packet_ready",
    "needs_operator_review",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review manifest, capability, and audit summaries manually",
    "confirm safe-mode gate recording offline",
    "keep auto-approval, import, setup, and runtime enablement disabled during operator review",
    "record operator notes without enabling plugin runtime behavior",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported plugin operator review gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported plugin operator review gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported plugin operator review decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class PluginOperatorReviewGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "PluginOperatorReviewGate":
        return cls(
            gate_id=_normalize_gate_id(gate_id),
            status=_normalize_gate_status(status),
            summary=_normalize_text(summary, field_name="summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class PluginOperatorReviewDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LivePluginOperatorReviewPacket:
    gates: tuple[PluginOperatorReviewGate, ...]
    decision: PluginOperatorReviewDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Plugin Operator Review Packet",
            "",
            f"- Decision: `{self.decision.decision}`",
            f"- Next action: {self.decision.next_action}",
            "",
            "## Gates",
        ]
        for gate in self.gates:
            lines.append(f"- `{gate.gate_id}`: {gate.status} - {gate.summary}")
        if self.next_allowed_actions:
            lines.extend(["", "## Next Allowed Actions"])
            for action in self.next_allowed_actions:
                lines.append(f"- {action}")
        return "\n".join(lines).rstrip()


def build_live_plugin_operator_review_packet(
    *,
    manifest_summary_available: bool = False,
    capability_preview_available: bool = False,
    local_audit_summary_available: bool = False,
    safe_mode_gate_recorded: bool = False,
    operator_review_required: bool = True,
    auto_approval_disabled: bool = True,
    runtime_enablement_disabled: bool = True,
    plugin_import_enabled: bool = False,
    setup_enabled: bool = False,
    dynamic_import_enabled: bool = False,
    exec_enabled: bool = False,
    runtime_enablement_enabled: bool = False,
    auto_approval_enabled: bool = False,
    network_enabled: bool = False,
    host_access_enabled: bool = False,
    token_present: bool = False,
    unsafe_review_logging_enabled: bool = False,
) -> LivePluginOperatorReviewPacket:
    unsafe_runtime_claimed = any(
        (
            plugin_import_enabled,
            setup_enabled,
            dynamic_import_enabled,
            exec_enabled,
            runtime_enablement_enabled,
            auto_approval_enabled,
            network_enabled,
            host_access_enabled,
            token_present,
            unsafe_review_logging_enabled,
            not auto_approval_disabled,
            not runtime_enablement_disabled,
        )
    )
    gate_map = {
        "manifest_summary_available": PluginOperatorReviewGate.create(
            gate_id="manifest_summary_available",
            status="go" if manifest_summary_available else "needs_operator_review",
            summary=(
                "manifest summary is available for offline operator review"
                if manifest_summary_available
                else "manual review of the manifest summary is still required"
            ),
        ),
        "capability_preview_available": PluginOperatorReviewGate.create(
            gate_id="capability_preview_available",
            status="go" if capability_preview_available else "needs_operator_review",
            summary=(
                "capability preview is available for offline operator review"
                if capability_preview_available
                else "manual review of the capability preview is still required"
            ),
        ),
        "local_audit_summary_available": PluginOperatorReviewGate.create(
            gate_id="local_audit_summary_available",
            status="go" if local_audit_summary_available else "needs_operator_review",
            summary=(
                "local audit summary is available for offline operator review"
                if local_audit_summary_available
                else "manual review of the local audit summary is still required"
            ),
        ),
        "safe_mode_gate_recorded": PluginOperatorReviewGate.create(
            gate_id="safe_mode_gate_recorded",
            status="go" if safe_mode_gate_recorded else "needs_operator_review",
            summary=(
                "safe-mode gate is recorded for operator review"
                if safe_mode_gate_recorded
                else "manual confirmation of the safe-mode gate is still required"
            ),
        ),
        "operator_review_required": PluginOperatorReviewGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any packet follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this review packet"
            ),
        ),
        "auto_approval_disabled": PluginOperatorReviewGate.create(
            gate_id="auto_approval_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "auto-approval or runtime-affecting behavior was enabled, which blocks the review packet"
                if unsafe_runtime_claimed
                else "auto-approval remains disabled during operator review"
            ),
        ),
        "runtime_enablement_disabled": PluginOperatorReviewGate.create(
            gate_id="runtime_enablement_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "import/setup/dynamic-import/exec/runtime/network/host/token/unsafe-logging was enabled, which blocks the review packet"
                if unsafe_runtime_claimed
                else "runtime enablement remains disabled during operator review"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only review conditions and remove auto-approval or runtime enablement claims"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "review_packet_ready"
        next_action = "perform manual operator review of the packet without enabling any plugin runtime behavior"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred operator review packet inputs before signoff"
    else:
        decision_value = "needs_operator_review"
        next_action = "complete the remaining operator review packet inputs before signoff"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LivePluginOperatorReviewPacket(
        gates=gates,
        decision=PluginOperatorReviewDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )
