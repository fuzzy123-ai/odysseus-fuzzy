"""Read-only closeout summary for live release evidence gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.current_manual_release_evidence_artifact import build_current_manual_release_evidence_artifact


_GATE_IDS = (
    "provider_fallback_answer_run",
    "test_vault_export_import_rebuild",
    "known_limits_review",
    "automated_release_gate",
)

_GATE_STATUSES = (
    "go",
    "no_go",
    "needs_manual_evidence",
)

_DECISION_VALUES = (
    "internal_release_candidate_ready",
    "external_go",
    "external_no_go",
    "needs_manual_evidence",
)

_ENTRY_GATE_IDS = (
    "provider_calls_disabled",
    "export_import_rebuild_disabled",
    "host_telegram_network_disabled",
    "secrets_and_raw_logs_blocked",
    "operator_review_required",
)

_MANUAL_NEXT_ALLOWED_SLICES = (
    "LIVE1-provider-proof-run",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported live release evidence gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported live release evidence gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported live release closeout decision")
    return text


def _normalize_entry_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="entry_gate_id").strip().lower()
    if text not in _ENTRY_GATE_IDS:
        raise ValueError("unsupported live phase entry gate_id")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class LiveEvidenceGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "LiveEvidenceGate":
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
class LiveCloseoutDecision:
    decision: str
    internal_release_candidate_ready: bool
    external_release_ready: bool
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "internal_release_candidate_ready": self.internal_release_candidate_ready,
            "external_release_ready": self.external_release_ready,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LivePhaseEntryGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "LivePhaseEntryGate":
        return cls(
            gate_id=_normalize_entry_gate_id(gate_id),
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
class LiveReleaseEvidenceCloseout:
    gates: tuple[LiveEvidenceGate, ...]
    live_phase_entry_gates: tuple[LivePhaseEntryGate, ...]
    decision: LiveCloseoutDecision
    next_allowed_slices: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "live_phase_entry_gates": tuple(gate.to_dict() for gate in self.live_phase_entry_gates),
            "decision": self.decision.to_dict(),
            "next_allowed_slices": self.next_allowed_slices,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Release Evidence Closeout",
            "",
            f"- Decision: `{self.decision.decision}`",
            f"- Internal release candidate ready: `{str(self.decision.internal_release_candidate_ready).lower()}`",
            f"- External release ready: `{str(self.decision.external_release_ready).lower()}`",
            f"- Next action: {self.decision.next_action}",
            "",
            "## Gates",
        ]
        for gate in self.gates:
            lines.append(f"- `{gate.gate_id}`: {gate.status} - {gate.summary}")
        lines.extend(["", "## Live Phase Entry Gates"])
        for gate in self.live_phase_entry_gates:
            lines.append(f"- `{gate.gate_id}`: {gate.status} - {gate.summary}")
        if self.next_allowed_slices:
            lines.extend(["", "## Next Allowed Slices"])
            for item in self.next_allowed_slices:
                lines.append(f"- {item}")
        return "\n".join(lines).rstrip()


def build_live_release_evidence_closeout() -> LiveReleaseEvidenceCloseout:
    current_artifact = build_current_manual_release_evidence_artifact()
    gate_map = {
        "provider_fallback_answer_run": LiveEvidenceGate.create(
            gate_id="provider_fallback_answer_run",
            status="go",
            summary="provider proof is recorded with isolated redacted cloud-answer evidence",
        ),
        "test_vault_export_import_rebuild": LiveEvidenceGate.create(
            gate_id="test_vault_export_import_rebuild",
            status="go",
            summary="test-vault export/import/rebuild proof is recorded with isolated redacted evidence",
        ),
        "known_limits_review": LiveEvidenceGate.create(
            gate_id="known_limits_review",
            status="go",
            summary="known limits remain reviewed without implying deploy, tag, or distribution execution",
        ),
        "automated_release_gate": LiveEvidenceGate.create(
            gate_id="automated_release_gate",
            status="go",
            summary="automated release gates are green for the internal release candidate",
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))
    live_phase_entry_gates = tuple(
        sorted(
            (
                LivePhaseEntryGate.create(
                    gate_id="provider_calls_disabled",
                    status="go",
                    summary="provider calls remain disabled until a separate operator-run flow is approved",
                ),
                LivePhaseEntryGate.create(
                    gate_id="export_import_rebuild_disabled",
                    status="go",
                    summary="export, import, and rebuild actions remain disabled until explicit operator execution",
                ),
                LivePhaseEntryGate.create(
                    gate_id="host_telegram_network_disabled",
                    status="go",
                    summary="host, Telegram, and network actions remain disabled for the closeout slice",
                ),
                LivePhaseEntryGate.create(
                    gate_id="secrets_and_raw_logs_blocked",
                    status="go",
                    summary="closeout artifacts allow only compact redacted status labels and evidence references",
                ),
                LivePhaseEntryGate.create(
                    gate_id="operator_review_required",
                    status="go",
                    summary="operator review remains required before any live integration follow-up executes",
                ),
            ),
            key=lambda item: item.gate_id,
        )
    )
    required_manual_gates_open = any(
        gate.status == "needs_manual_evidence"
        for gate in gates
        if gate.gate_id in {"provider_fallback_answer_run", "test_vault_export_import_rebuild"}
    )
    any_no_go = any(gate.status == "no_go" for gate in gates)

    if all(gate.status == "go" for gate in gates):
        decision_value = "external_go"
        next_action = "external release evidence is complete"
        next_allowed_slices: tuple[str, ...] = ()
        external_release_ready = True
    elif any_no_go:
        decision_value = "external_no_go"
        next_action = "resolve explicit no-go evidence before external release"
        next_allowed_slices = ()
        external_release_ready = False
    elif required_manual_gates_open:
        decision_value = "needs_manual_evidence"
        next_action = "complete the remaining manual provider-proof evidence run"
        next_allowed_slices = _normalize_tuple(_MANUAL_NEXT_ALLOWED_SLICES, field_name="next_allowed_slice")
        external_release_ready = False
    else:
        decision_value = "internal_release_candidate_ready"
        next_action = "keep external release gated until manual evidence stays complete"
        next_allowed_slices = ()
        external_release_ready = False

    if current_artifact.to_dict()["ok"] is False and decision_value == "external_go":
        raise ValueError("current manual release artifact still reports open gaps")

    decision = LiveCloseoutDecision(
        decision=_normalize_decision(decision_value),
        internal_release_candidate_ready=True,
        external_release_ready=external_release_ready,
        next_action=next_action,
    )
    return LiveReleaseEvidenceCloseout(
        gates=gates,
        live_phase_entry_gates=live_phase_entry_gates,
        decision=decision,
        next_allowed_slices=next_allowed_slices,
    )
