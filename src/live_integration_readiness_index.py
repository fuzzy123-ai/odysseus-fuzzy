"""Read-only planning model for live integration readiness indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "live_slices_recorded",
    "provider_proof_manual_gate_recorded",
    "test_vault_rebuild_manual_gate_recorded",
    "runtime_enablement_disabled",
    "network_actions_disabled",
    "plugin_imports_disabled",
    "operator_review_required",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_manual_evidence",
    "deferred",
)

_DECISION_VALUES = (
    "integration_readiness_ready",
    "needs_manual_evidence",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review recorded live-integration slices and manual evidence gates",
    "complete provider-proof evidence manually",
    "keep runtime, network, and plugin-import paths disabled during readiness review",
    "record operator notes without claiming external 1.0.0 release go",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported live integration readiness gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported live integration readiness gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported live integration readiness decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class LiveIntegrationReadinessGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "LiveIntegrationReadinessGate":
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
class LiveIntegrationReadinessDecision:
    decision: str
    next_action: str
    external_release_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
            "external_release_ready": self.external_release_ready,
        }


@dataclass(frozen=True, slots=True)
class LiveIntegrationReadinessIndex:
    gates: tuple[LiveIntegrationReadinessGate, ...]
    decision: LiveIntegrationReadinessDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Integration Readiness Index",
            "",
            f"- Decision: `{self.decision.decision}`",
            f"- External release ready: `{str(self.decision.external_release_ready).lower()}`",
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


def build_live_integration_readiness_index(
    *,
    live_slices_recorded: bool = False,
    provider_proof_manual_gate_recorded: bool = False,
    test_vault_rebuild_manual_gate_recorded: bool = True,
    runtime_enablement_disabled: bool = True,
    network_actions_disabled: bool = True,
    plugin_imports_disabled: bool = True,
    operator_review_required: bool = True,
    runtime_enablement_enabled: bool = False,
    network_enabled: bool = False,
    plugin_import_enabled: bool = False,
    host_access_enabled: bool = False,
    token_present: bool = False,
    auto_approval_enabled: bool = False,
    unsafe_evidence_logging_enabled: bool = False,
) -> LiveIntegrationReadinessIndex:
    unsafe_runtime_claimed = any(
        (
            runtime_enablement_enabled,
            network_enabled,
            plugin_import_enabled,
            host_access_enabled,
            token_present,
            auto_approval_enabled,
            unsafe_evidence_logging_enabled,
            not runtime_enablement_disabled,
            not network_actions_disabled,
            not plugin_imports_disabled,
        )
    )
    gate_map = {
        "live_slices_recorded": LiveIntegrationReadinessGate.create(
            gate_id="live_slices_recorded",
            status="go" if live_slices_recorded else "needs_manual_evidence",
            summary=(
                "live integration slices are recorded for internal readiness review"
                if live_slices_recorded
                else "manual recording of live integration slices is still required"
            ),
        ),
        "provider_proof_manual_gate_recorded": LiveIntegrationReadinessGate.create(
            gate_id="provider_proof_manual_gate_recorded",
            status="go" if provider_proof_manual_gate_recorded else "needs_manual_evidence",
            summary=(
                "provider-proof manual gate is recorded for internal readiness review"
                if provider_proof_manual_gate_recorded
                else "manual provider-proof evidence is still required"
            ),
        ),
        "test_vault_rebuild_manual_gate_recorded": LiveIntegrationReadinessGate.create(
            gate_id="test_vault_rebuild_manual_gate_recorded",
            status="go" if test_vault_rebuild_manual_gate_recorded else "needs_manual_evidence",
            summary=(
                "test-vault rebuild manual gate is recorded for internal readiness review"
                if test_vault_rebuild_manual_gate_recorded
                else "manual test-vault rebuild evidence is still required"
            ),
        ),
        "runtime_enablement_disabled": LiveIntegrationReadinessGate.create(
            gate_id="runtime_enablement_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "runtime, network, plugin-import, host, token, auto-approval, or unsafe logging was enabled, which blocks readiness"
                if unsafe_runtime_claimed
                else "runtime enablement remains disabled during readiness review"
            ),
        ),
        "network_actions_disabled": LiveIntegrationReadinessGate.create(
            gate_id="network_actions_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "runtime, network, plugin-import, host, token, auto-approval, or unsafe logging was enabled, which blocks readiness"
                if unsafe_runtime_claimed
                else "network actions remain disabled during readiness review"
            ),
        ),
        "plugin_imports_disabled": LiveIntegrationReadinessGate.create(
            gate_id="plugin_imports_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "runtime, network, plugin-import, host, token, auto-approval, or unsafe logging was enabled, which blocks readiness"
                if unsafe_runtime_claimed
                else "plugin imports remain disabled during readiness review"
            ),
        ),
        "operator_review_required": LiveIntegrationReadinessGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any live integration follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this readiness index"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only readiness conditions and remove runtime, network, or plugin-import claims"
        external_release_ready = False
    elif all(gate.status == "go" for gate in gates):
        decision_value = "integration_readiness_ready"
        next_action = "internal readiness index is complete, but external release still waits on real manual evidence execution"
        external_release_ready = False
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred live integration readiness inputs before operator review"
        external_release_ready = False
    else:
        decision_value = "needs_manual_evidence"
        next_action = "complete the remaining manual provider-proof evidence gate"
        external_release_ready = False

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveIntegrationReadinessIndex(
        gates=gates,
        decision=LiveIntegrationReadinessDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
            external_release_ready=external_release_ready,
        ),
        next_allowed_actions=next_allowed_actions,
    )
