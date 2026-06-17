"""Read-only safe-mode planning model for plugin loader review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "manifest_validated",
    "capability_boundary_validated",
    "local_audit_clean",
    "top_level_import_blocked",
    "operator_review_required",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_review",
    "deferred",
)

_DECISION_VALUES = (
    "safe_mode_plan_ready",
    "needs_operator_review",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review plugin manifest and capability boundary manually",
    "confirm local audit evidence before any safe-mode follow-up",
    "keep top-level plugin import blocked during operator review",
    "record operator notes without enabling runtime plugin loading",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported plugin safe mode gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported plugin safe mode gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported plugin safe mode decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class PluginSafeModeGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "PluginSafeModeGate":
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
class PluginSafeModeDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LivePluginLoaderSafeModePlan:
    gates: tuple[PluginSafeModeGate, ...]
    decision: PluginSafeModeDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Plugin Loader Safe Mode Plan",
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


def build_live_plugin_loader_safe_mode_plan(
    *,
    manifest_validated: bool = False,
    capability_boundary_validated: bool = False,
    local_audit_clean: bool = False,
    top_level_import_blocked: bool = True,
    operator_review_required: bool = True,
    plugin_import_enabled: bool = False,
    setup_enabled: bool = False,
    host_access_enabled: bool = False,
    network_enabled: bool = False,
) -> LivePluginLoaderSafeModePlan:
    unsafe_runtime_claimed = any(
        (
            plugin_import_enabled,
            setup_enabled,
            host_access_enabled,
            network_enabled,
            not top_level_import_blocked,
        )
    )
    gate_map = {
        "manifest_validated": PluginSafeModeGate.create(
            gate_id="manifest_validated",
            status="go" if manifest_validated else "needs_operator_review",
            summary=(
                "plugin manifest validation is prepared for safe-mode review"
                if manifest_validated
                else "manual review of plugin manifest validation is still required"
            ),
        ),
        "capability_boundary_validated": PluginSafeModeGate.create(
            gate_id="capability_boundary_validated",
            status="go" if capability_boundary_validated else "needs_operator_review",
            summary=(
                "capability-boundary validation is prepared for safe-mode review"
                if capability_boundary_validated
                else "manual review of capability-boundary validation is still required"
            ),
        ),
        "local_audit_clean": PluginSafeModeGate.create(
            gate_id="local_audit_clean",
            status="go" if local_audit_clean else "needs_operator_review",
            summary=(
                "local audit evidence is clean for safe-mode review"
                if local_audit_clean
                else "manual review of local audit evidence is still required"
            ),
        ),
        "top_level_import_blocked": PluginSafeModeGate.create(
            gate_id="top_level_import_blocked",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "plugin import/setup/host/network runtime was claimed or enabled, which blocks safe-mode review"
                if unsafe_runtime_claimed
                else "top-level import remains blocked during safe-mode operator review"
            ),
        ),
        "operator_review_required": PluginSafeModeGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any safe-mode follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this safe-mode model"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore import/setup/host/network blocking and keep the plugin in offline safe-mode review"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "safe_mode_plan_ready"
        next_action = "perform manual operator review of the safe-mode plugin evidence without importing the plugin"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred safe-mode planning inputs before operator review"
    else:
        decision_value = "needs_operator_review"
        next_action = "complete the remaining safe-mode validation and audit review inputs"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LivePluginLoaderSafeModePlan(
        gates=gates,
        decision=PluginSafeModeDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )
