"""Read-only planning model for plugin capability preview indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "manifest_metadata_available",
    "capability_metadata_available",
    "audit_policy_selected",
    "operator_review_required",
    "runtime_enablement_disabled",
    "import_path_disabled",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_review",
    "deferred",
)

_DECISION_VALUES = (
    "preview_index_ready",
    "needs_operator_review",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review manifest and capability metadata manually",
    "confirm audit policy selection offline",
    "keep import, setup, dynamic import, exec, and runtime enablement disabled during preview review",
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
        raise ValueError("unsupported plugin capability preview gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported plugin capability preview gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported plugin capability preview decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class PluginCapabilityPreviewGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "PluginCapabilityPreviewGate":
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
class PluginCapabilityPreviewDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LivePluginCapabilityPreviewIndex:
    gates: tuple[PluginCapabilityPreviewGate, ...]
    decision: PluginCapabilityPreviewDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Plugin Capability Preview Index",
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


def build_live_plugin_capability_preview_index(
    *,
    manifest_metadata_available: bool = False,
    capability_metadata_available: bool = False,
    audit_policy_selected: bool = False,
    operator_review_required: bool = True,
    runtime_enablement_disabled: bool = True,
    import_path_disabled: bool = True,
    plugin_import_enabled: bool = False,
    setup_enabled: bool = False,
    dynamic_import_enabled: bool = False,
    exec_enabled: bool = False,
    runtime_enablement_enabled: bool = False,
    network_enabled: bool = False,
    host_access_enabled: bool = False,
    token_present: bool = False,
    unsafe_capability_logging_enabled: bool = False,
) -> LivePluginCapabilityPreviewIndex:
    unsafe_runtime_claimed = any(
        (
            plugin_import_enabled,
            setup_enabled,
            dynamic_import_enabled,
            exec_enabled,
            runtime_enablement_enabled,
            network_enabled,
            host_access_enabled,
            token_present,
            unsafe_capability_logging_enabled,
            not runtime_enablement_disabled,
            not import_path_disabled,
        )
    )
    gate_map = {
        "manifest_metadata_available": PluginCapabilityPreviewGate.create(
            gate_id="manifest_metadata_available",
            status="go" if manifest_metadata_available else "needs_operator_review",
            summary=(
                "manifest metadata is available for offline capability preview review"
                if manifest_metadata_available
                else "manual review of manifest metadata is still required"
            ),
        ),
        "capability_metadata_available": PluginCapabilityPreviewGate.create(
            gate_id="capability_metadata_available",
            status="go" if capability_metadata_available else "needs_operator_review",
            summary=(
                "capability metadata is available for offline preview review"
                if capability_metadata_available
                else "manual review of capability metadata is still required"
            ),
        ),
        "audit_policy_selected": PluginCapabilityPreviewGate.create(
            gate_id="audit_policy_selected",
            status="go" if audit_policy_selected else "needs_operator_review",
            summary=(
                "audit policy is selected for capability preview review"
                if audit_policy_selected
                else "manual selection of an audit policy is still required"
            ),
        ),
        "operator_review_required": PluginCapabilityPreviewGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any preview follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this preview model"
            ),
        ),
        "runtime_enablement_disabled": PluginCapabilityPreviewGate.create(
            gate_id="runtime_enablement_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "import/setup/dynamic-import/exec/runtime/network/host/token/unsafe-logging was enabled, which blocks the preview index"
                if unsafe_runtime_claimed
                else "runtime enablement remains disabled during capability preview review"
            ),
        ),
        "import_path_disabled": PluginCapabilityPreviewGate.create(
            gate_id="import_path_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "import/setup/dynamic-import/exec/runtime/network/host/token/unsafe-logging was enabled, which blocks the preview index"
                if unsafe_runtime_claimed
                else "import paths remain disabled during capability preview review"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only preview conditions and remove import/runtime enablement claims"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "preview_index_ready"
        next_action = "perform manual operator review of the capability preview index without importing any plugin"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred capability preview inputs before operator review"
    else:
        decision_value = "needs_operator_review"
        next_action = "complete the remaining capability preview review inputs before operator signoff"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LivePluginCapabilityPreviewIndex(
        gates=gates,
        decision=PluginCapabilityPreviewDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )
