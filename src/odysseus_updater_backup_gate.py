"""Offline backup gate model for the Odysseus updater module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

_RISK_LEVELS = ("low", "medium", "high", "critical")
_REPORT_STATUSES = ("ready", "partial", "blocked", "deferred")
_DEPLOYMENT_DECISIONS = ("go", "deferred", "no_go")
_EVIDENCE_STATES = ("green", "yellow", "red", "missing", "pending")
_RESULT_LABELS = ("pass", "partial", "fail", "blocked", "pending", "missing")
_REQUIRED_EVIDENCE_IDS = (
    "pre_update_snapshot",
    "repository_check",
    "restore_smoke",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_slug(value: Any, *, field_name: str) -> str:
    text = (
        _normalize_text(value, field_name=field_name)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    return "_".join(part for part in text.split("_") if part)


def _normalize_risk_level(value: Any) -> str:
    risk_level = _normalize_slug(value, field_name="risk_level")
    if risk_level not in _RISK_LEVELS:
        raise ValueError(f"unsupported risk_level: {value!r}")
    return risk_level


def _normalize_report_status(value: Any) -> str:
    status = _normalize_slug(value, field_name="status")
    if status not in _REPORT_STATUSES:
        raise ValueError(f"unsupported status: {value!r}")
    return status


def _normalize_deployment_decision(value: Any) -> str:
    decision = _normalize_slug(value, field_name="deployment_decision")
    if decision not in _DEPLOYMENT_DECISIONS:
        raise ValueError(f"unsupported deployment_decision: {value!r}")
    return decision


def _normalize_evidence_state(value: Any) -> str:
    state = _normalize_slug(value, field_name="state")
    if state not in _EVIDENCE_STATES:
        raise ValueError(f"unsupported evidence state: {value!r}")
    return state


def _normalize_result_label(value: Any) -> str:
    label = _normalize_slug(value, field_name="result_label")
    if label not in _RESULT_LABELS:
        raise ValueError(f"unsupported result_label: {value!r}")
    return label


def _normalize_string_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        items.append(_normalize_text(value, field_name=field_name))
    return tuple(items)


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    return text


@dataclass(frozen=True, slots=True)
class BackupGateEvidence:
    evidence_id: str
    state: str
    result_label: str
    checked_at: str
    summary: str
    blocker_reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        evidence_id: Any,
        state: Any,
        result_label: Any,
        checked_at: Any,
        summary: Any,
        blocker_reason: Any | None = None,
    ) -> "BackupGateEvidence":
        evidence_key = _normalize_slug(evidence_id, field_name="evidence_id")
        if evidence_key not in _REQUIRED_EVIDENCE_IDS:
            raise ValueError(f"unsupported evidence_id: {evidence_id!r}")
        normalized_state = _normalize_evidence_state(state)
        normalized_label = _normalize_result_label(result_label)
        normalized_blocker = (
            _normalize_text(blocker_reason, field_name="blocker_reason")
            if blocker_reason is not None
            else None
        )
        if normalized_state == "green" and normalized_label != "pass":
            raise ValueError("green evidence must use result_label='pass'")
        if normalized_state == "pending" and normalized_label != "pending":
            raise ValueError("pending evidence must use result_label='pending'")
        if normalized_state == "red" and normalized_label not in {"fail", "blocked"}:
            raise ValueError("red evidence must use result_label='fail' or 'blocked'")
        if normalized_state == "missing" and normalized_label not in {"missing", "partial", "blocked"}:
            raise ValueError(
                "missing evidence must use result_label='missing', 'partial', or 'blocked'"
            )
        if normalized_state == "yellow" and normalized_label != "partial":
            raise ValueError("yellow evidence must use result_label='partial'")
        return cls(
            evidence_id=evidence_key,
            state=normalized_state,
            result_label=normalized_label,
            checked_at=_normalize_timestamp(checked_at, field_name="checked_at"),
            summary=_normalize_text(summary, field_name="summary"),
            blocker_reason=normalized_blocker,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "state": self.state,
            "result_label": self.result_label,
            "checked_at": self.checked_at,
            "summary": self.summary,
            "blocker_reason": self.blocker_reason,
        }


@dataclass(frozen=True, slots=True)
class BackupGateReport:
    risk_level: str
    status: str
    deployment_decision: str
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]
    evidence: tuple[BackupGateEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "status": self.status,
            "deployment_decision": self.deployment_decision,
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "next_actions": list(self.next_actions),
            "evidence": [item.to_dict() for item in self.evidence],
        }

    def to_compact_report(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "status": self.status,
            "deployment_decision": self.deployment_decision,
            "evidence_labels": {
                item.evidence_id: item.result_label for item in self.evidence
            },
            "evidence_states": {
                item.evidence_id: item.state for item in self.evidence
            },
            "blockers": list(self.blockers),
            "next_actions": list(self.next_actions),
        }

    def to_evidence_packet(self) -> dict[str, Any]:
        """Return a compact operator packet safe for docs, audit, and handoff."""

        return {
            "feature": "homeserver_backup_gate",
            "risk_level": self.risk_level,
            "status": self.status,
            "deployment_decision": self.deployment_decision,
            "required_evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "state": item.state,
                    "result_label": item.result_label,
                    "checked_at": item.checked_at,
                    "summary": item.summary,
                }
                for item in self.evidence
            ],
            "blockers": list(self.blockers),
            "next_actions": list(self.next_actions),
            "secret_values_visible": False,
            "host_output_visible": False,
        }


def _default_missing_evidence(
    *,
    evidence_id: str,
    risk_level: str,
    evaluated_at: str,
) -> BackupGateEvidence:
    if evidence_id == "restore_smoke":
        if risk_level in {"low", "medium"}:
            return BackupGateEvidence.create(
                evidence_id=evidence_id,
                state="missing",
                result_label="partial",
                checked_at=evaluated_at,
                summary="restore smoke evidence is missing from the structured backup gate input",
                blocker_reason="restore smoke evidence still needs a structured offline record",
            )
        return BackupGateEvidence.create(
            evidence_id=evidence_id,
            state="missing",
            result_label="blocked",
            checked_at=evaluated_at,
            summary="restore smoke evidence is mandatory for high-risk or critical updates",
            blocker_reason="restore smoke evidence is required before high-risk deployment review",
        )
    return BackupGateEvidence.create(
        evidence_id=evidence_id,
        state="missing",
        result_label="missing",
        checked_at=evaluated_at,
        summary=f"{evidence_id} evidence is missing from the structured backup gate input",
        blocker_reason=f"{evidence_id} evidence must be supplied as structured offline data",
    )


def _build_evidence_tuple(
    evidence_inputs: Iterable[Mapping[str, Any]],
    *,
    risk_level: str,
    evaluated_at: str,
) -> tuple[BackupGateEvidence, ...]:
    evidence_by_id: dict[str, BackupGateEvidence] = {}
    for raw_item in evidence_inputs:
        evidence = BackupGateEvidence.create(
            evidence_id=raw_item.get("evidence_id"),
            state=raw_item.get("state"),
            result_label=raw_item.get("result_label"),
            checked_at=raw_item.get("checked_at"),
            summary=raw_item.get("summary"),
            blocker_reason=raw_item.get("blocker_reason"),
        )
        if evidence.evidence_id in evidence_by_id:
            raise ValueError(f"duplicate evidence_id: {evidence.evidence_id}")
        evidence_by_id[evidence.evidence_id] = evidence
    for evidence_id in _REQUIRED_EVIDENCE_IDS:
        if evidence_id not in evidence_by_id:
            evidence_by_id[evidence_id] = _default_missing_evidence(
                evidence_id=evidence_id,
                risk_level=risk_level,
                evaluated_at=evaluated_at,
            )
    return tuple(evidence_by_id[evidence_id] for evidence_id in _REQUIRED_EVIDENCE_IDS)


def _derive_report_status(evidence: tuple[BackupGateEvidence, ...]) -> str:
    labels = {item.result_label for item in evidence}
    if "blocked" in labels or "fail" in labels or "missing" in labels:
        return "blocked"
    if "pending" in labels:
        return "deferred"
    if "partial" in labels:
        return "partial"
    return "ready"


def _derive_deployment_decision(status: str) -> str:
    if status == "ready":
        return "go"
    if status == "blocked":
        return "no_go"
    return "deferred"


def _derive_reasons(
    *,
    status: str,
    evidence: tuple[BackupGateEvidence, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for item in evidence:
        if item.result_label == "pass":
            reasons.append(f"{item.evidence_id} is green and structurally recorded")
        elif item.result_label == "partial":
            reasons.append(f"{item.evidence_id} is incomplete and keeps the gate in partial review")
        elif item.result_label == "pending":
            reasons.append(f"{item.evidence_id} is still pending structured validation")
        elif item.result_label in {"fail", "blocked", "missing"}:
            reasons.append(f"{item.evidence_id} is not green and blocks updater promotion")
    if status == "ready":
        reasons.append("all required backup gate evidence is green")
    elif status == "partial":
        reasons.append("deployment remains deferred until every required evidence record is green")
    elif status == "blocked":
        reasons.append("deployment is blocked because at least one required evidence record is red or missing")
    else:
        reasons.append("deployment is deferred until pending backup evidence is resolved")
    return tuple(reasons)


def _derive_blockers(evidence: tuple[BackupGateEvidence, ...]) -> tuple[str, ...]:
    blockers: list[str] = []
    for item in evidence:
        if item.result_label in {"fail", "blocked", "missing"} and item.blocker_reason:
            blockers.append(item.blocker_reason)
    return tuple(blockers)


def _derive_next_actions(evidence: tuple[BackupGateEvidence, ...]) -> tuple[str, ...]:
    actions: list[str] = []
    for item in evidence:
        if item.result_label == "pass":
            continue
        if item.result_label == "pending":
            actions.append(
                f"Finalize the structured {item.evidence_id} record and replace the pending state with a green result."
            )
        elif item.result_label == "partial":
            actions.append(
                f"Add the missing structured {item.evidence_id} evidence so the backup gate can move from partial to ready."
            )
        else:
            actions.append(
                f"Repair or replace the structured {item.evidence_id} evidence before updater deployment review continues."
            )
    if not actions:
        actions.append("Proceed with deployment review because all required backup evidence is green.")
    return tuple(actions)


def build_odysseus_updater_backup_gate(
    *,
    risk_level: Any,
    evaluated_at: Any,
    evidence_inputs: Iterable[Mapping[str, Any]],
) -> BackupGateReport:
    normalized_risk_level = _normalize_risk_level(risk_level)
    normalized_evaluated_at = _normalize_timestamp(evaluated_at, field_name="evaluated_at")
    evidence = _build_evidence_tuple(
        evidence_inputs,
        risk_level=normalized_risk_level,
        evaluated_at=normalized_evaluated_at,
    )
    status = _normalize_report_status(_derive_report_status(evidence))
    return BackupGateReport(
        risk_level=normalized_risk_level,
        status=status,
        deployment_decision=_normalize_deployment_decision(_derive_deployment_decision(status)),
        reasons=_normalize_string_tuple(
            _derive_reasons(status=status, evidence=evidence),
            field_name="reasons",
        ),
        blockers=_normalize_string_tuple(
            _derive_blockers(evidence),
            field_name="blockers",
        )
        if _derive_blockers(evidence)
        else (),
        next_actions=_normalize_string_tuple(
            _derive_next_actions(evidence),
            field_name="next_actions",
        ),
        evidence=evidence,
    )
