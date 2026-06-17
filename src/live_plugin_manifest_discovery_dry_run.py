"""Read-only planning model for plugin manifest discovery dry runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "manifest_path_selected",
    "manifest_schema_reviewed",
    "capability_metadata_present",
    "local_audit_policy_selected",
    "operator_review_required",
    "import_path_disabled",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_review",
    "deferred",
)

_DECISION_VALUES = (
    "discovery_plan_ready",
    "needs_operator_review",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review the manifest path and schema manually",
    "confirm capability metadata and local audit policy offline",
    "keep import, setup, dynamic import, and exec paths disabled during dry-run review",
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
        raise ValueError("unsupported plugin manifest discovery gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported plugin manifest discovery gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported plugin manifest discovery decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class PluginManifestDiscoveryGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "PluginManifestDiscoveryGate":
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
class PluginManifestDiscoveryDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LivePluginManifestDiscoveryDryRunPlan:
    gates: tuple[PluginManifestDiscoveryGate, ...]
    decision: PluginManifestDiscoveryDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Plugin Manifest Discovery Dry Run Plan",
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


def build_live_plugin_manifest_discovery_dry_run_plan(
    *,
    manifest_path_selected: bool = False,
    manifest_schema_reviewed: bool = False,
    capability_metadata_present: bool = False,
    local_audit_policy_selected: bool = False,
    operator_review_required: bool = True,
    import_path_disabled: bool = True,
    plugin_import_enabled: bool = False,
    setup_enabled: bool = False,
    dynamic_import_enabled: bool = False,
    exec_enabled: bool = False,
    network_enabled: bool = False,
    host_access_enabled: bool = False,
    token_present: bool = False,
    unsafe_manifest_logging_enabled: bool = False,
) -> LivePluginManifestDiscoveryDryRunPlan:
    unsafe_runtime_claimed = any(
        (
            plugin_import_enabled,
            setup_enabled,
            dynamic_import_enabled,
            exec_enabled,
            network_enabled,
            host_access_enabled,
            token_present,
            unsafe_manifest_logging_enabled,
            not import_path_disabled,
        )
    )
    gate_map = {
        "manifest_path_selected": PluginManifestDiscoveryGate.create(
            gate_id="manifest_path_selected",
            status="go" if manifest_path_selected else "needs_operator_review",
            summary=(
                "manifest path is selected for dry-run discovery review"
                if manifest_path_selected
                else "manual review of the manifest path is still required"
            ),
        ),
        "manifest_schema_reviewed": PluginManifestDiscoveryGate.create(
            gate_id="manifest_schema_reviewed",
            status="go" if manifest_schema_reviewed else "needs_operator_review",
            summary=(
                "manifest schema review is prepared for dry-run discovery"
                if manifest_schema_reviewed
                else "manual manifest-schema review is still required"
            ),
        ),
        "capability_metadata_present": PluginManifestDiscoveryGate.create(
            gate_id="capability_metadata_present",
            status="go" if capability_metadata_present else "needs_operator_review",
            summary=(
                "capability metadata is present for dry-run discovery review"
                if capability_metadata_present
                else "manual review of capability metadata is still required"
            ),
        ),
        "local_audit_policy_selected": PluginManifestDiscoveryGate.create(
            gate_id="local_audit_policy_selected",
            status="go" if local_audit_policy_selected else "needs_operator_review",
            summary=(
                "local audit policy is selected for dry-run discovery"
                if local_audit_policy_selected
                else "manual selection of a local audit policy is still required"
            ),
        ),
        "operator_review_required": PluginManifestDiscoveryGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any discovery follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this dry-run model"
            ),
        ),
        "import_path_disabled": PluginManifestDiscoveryGate.create(
            gate_id="import_path_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "import/setup/dynamic-import/exec/network/host/token/unsafe-logging was enabled, which blocks discovery dry-run"
                if unsafe_runtime_claimed
                else "import and runtime-affecting paths remain disabled during manifest discovery review"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only discovery conditions and remove import/setup/runtime claims"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "discovery_plan_ready"
        next_action = "perform manual operator review of the manifest discovery plan without importing any plugin"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred manifest discovery inputs before operator review"
    else:
        decision_value = "needs_operator_review"
        next_action = "complete the remaining manifest discovery review inputs before operator signoff"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LivePluginManifestDiscoveryDryRunPlan(
        gates=gates,
        decision=PluginManifestDiscoveryDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )
