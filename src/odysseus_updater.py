"""Offline updater bundle entrypoint for the Odysseus updater slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.odysseus_updater_backup_gate import (
    BackupGateReport,
    build_odysseus_updater_backup_gate,
)
from src.odysseus_updater_command_plan import (
    UpdaterCommandPlan,
    build_odysseus_updater_command_plan,
)
from src.odysseus_updater_plan import UpdatePlan, build_odysseus_updater_plan
from src.odysseus_updater_preflight import (
    PreflightReport,
    build_odysseus_updater_preflight_report,
)
from src.odysseus_updater_test_gate import (
    UpdaterTestGateReport,
    build_odysseus_updater_test_gate,
)

_DECISIONS = ("go", "partial", "no_go", "deferred")
_LIVE_UPDATE_DECISION = "no_go"
_MODE = "dry_run"


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _dedupe(items: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    unique: list[str] = []
    for item in items:
        normalized = _normalize_text(item, field_name=field_name, allow_empty=True)
        if not normalized or normalized in unique:
            continue
        unique.append(normalized)
    return tuple(unique)


def _normalize_decision(value: Any, *, field_name: str = "decision") -> str:
    decision = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if decision not in _DECISIONS:
        raise ValueError(f"unsupported {field_name}: {value!r}")
    return decision


def _map_preflight_status_to_decision(status: str) -> str:
    return {
        "ready": "go",
        "partial": "partial",
        "blocked": "no_go",
        "deferred": "deferred",
    }[status]


def _normalize_audit_summary_payload(summary: Any) -> dict[str, Any]:
    if hasattr(summary, "to_dict") and callable(summary.to_dict):
        payload = summary.to_dict()
        if isinstance(payload, dict):
            return payload
    if isinstance(summary, Mapping):
        return dict(summary)
    raise ValueError("audit_summary must provide to_dict() or be a mapping")


def _build_default_plan() -> UpdatePlan:
    return build_odysseus_updater_plan(
        source_ref="offline-snapshot",
        current_ref="offline-snapshot",
        target_ref="offline-review",
        reason="structured updater inputs have not been supplied yet; stay in review-only mode",
        risk_level="medium",
        required_gates=(
            {
                "gate_id": "scope_confirmed",
                "status": "pending",
                "summary": "scope confirmation is still waiting for structured offline input",
            },
            {
                "gate_id": "offline_slice_confirmed",
                "status": "pending",
                "summary": "offline slice confirmation is still waiting for structured offline input",
            },
            {
                "gate_id": "tests_defined",
                "status": "pending",
                "summary": "offline test scope is still waiting for structured offline input",
            },
        ),
        optional_gates=(
            {
                "gate_id": "audit_report_ready",
                "status": "pending",
                "summary": "optional audit summary has not been attached yet",
            },
        ),
        planned_commands=(
            {
                "command_plan_id": "cmd_01_hold",
                "argv": ("review-offline-updater-bundle",),
                "summary": "hold the updater bundle in dry-run review mode until structured inputs are supplied",
            },
        ),
    )


def _build_default_preflight() -> PreflightReport:
    return build_odysseus_updater_preflight_report(
        worktree_snapshot={
            "dirty": None,
            "staged_files": (),
            "allowed_staged_files": (),
            "hotfile_conflict": None,
        },
        branch_snapshot={
            "current_branch": None,
            "expected_branch": None,
            "branch_candidates": (),
            "detached": None,
            "ahead": None,
            "behind": None,
        },
        env_snapshot={
            "required_names": (),
            "present_names": (),
        },
        backup_snapshot={
            "mount_ready": None,
        },
    )


def _build_default_backup_gate() -> BackupGateReport:
    pending_evidence = []
    for evidence_id in ("pre_update_snapshot", "repository_check", "restore_smoke"):
        pending_evidence.append(
            {
                "evidence_id": evidence_id,
                "state": "pending",
                "result_label": "pending",
                "checked_at": "2026-01-01T00:00:00Z",
                "summary": f"{evidence_id} is waiting for a structured offline record",
                "blocker_reason": "live execution remains out of scope for this updater bundle",
            }
        )
    return build_odysseus_updater_backup_gate(
        risk_level="medium",
        evaluated_at="2026-01-01T00:00:00Z",
        evidence_inputs=pending_evidence,
    )


def _build_default_test_gate() -> UpdaterTestGateReport:
    return build_odysseus_updater_test_gate(
        allowed_suites=(
            {
                "suite_id": "offline_bundle_review",
                "required": True,
                "timeout_seconds": 300,
                "summary": "structured offline updater bundle review snapshot",
            },
        ),
        result_snapshots=(
            {
                "suite_id": "offline_bundle_review",
                "execution_status": "pending",
                "result_label": "pending",
                "summary": "offline test evidence is still waiting for a structured snapshot",
            },
        ),
    )


def _build_default_command_plan() -> UpdaterCommandPlan:
    return build_odysseus_updater_command_plan(
        plan_type="hold_note",
        focus_label="offline-review",
        note="stay in plan-only mode until the offline updater bundle is reviewed",
    )


def _coerce_plan(plan_input: UpdatePlan | Mapping[str, Any] | None) -> UpdatePlan:
    if plan_input is None:
        return _build_default_plan()
    if isinstance(plan_input, UpdatePlan):
        return plan_input
    if isinstance(plan_input, Mapping):
        return build_odysseus_updater_plan(**plan_input)
    raise ValueError("plan_input must be an UpdatePlan, mapping, or None")


def _coerce_preflight(preflight_input: PreflightReport | Mapping[str, Any] | None) -> PreflightReport:
    if preflight_input is None:
        return _build_default_preflight()
    if isinstance(preflight_input, PreflightReport):
        return preflight_input
    if isinstance(preflight_input, Mapping):
        return build_odysseus_updater_preflight_report(**preflight_input)
    raise ValueError("preflight_input must be a PreflightReport, mapping, or None")


def _coerce_backup_gate(
    backup_gate_input: BackupGateReport | Mapping[str, Any] | None,
) -> BackupGateReport:
    if backup_gate_input is None:
        return _build_default_backup_gate()
    if isinstance(backup_gate_input, BackupGateReport):
        return backup_gate_input
    if isinstance(backup_gate_input, Mapping):
        return build_odysseus_updater_backup_gate(**backup_gate_input)
    raise ValueError("backup_gate_input must be a BackupGateReport, mapping, or None")


def _coerce_test_gate(
    test_gate_input: UpdaterTestGateReport | Mapping[str, Any] | None,
) -> UpdaterTestGateReport:
    if test_gate_input is None:
        return _build_default_test_gate()
    if isinstance(test_gate_input, UpdaterTestGateReport):
        return test_gate_input
    if isinstance(test_gate_input, Mapping):
        return build_odysseus_updater_test_gate(**test_gate_input)
    raise ValueError("test_gate_input must be an UpdaterTestGateReport, mapping, or None")


def _coerce_command_plans(
    command_plan_inputs: Iterable[UpdaterCommandPlan | Mapping[str, Any]] | None,
) -> tuple[UpdaterCommandPlan, ...]:
    if command_plan_inputs is None:
        return (_build_default_command_plan(),)
    plans: list[UpdaterCommandPlan] = []
    for raw_plan in command_plan_inputs:
        if isinstance(raw_plan, UpdaterCommandPlan):
            plans.append(raw_plan)
            continue
        if isinstance(raw_plan, Mapping):
            plans.append(build_odysseus_updater_command_plan(**raw_plan))
            continue
        raise ValueError("command_plan_inputs items must be UpdaterCommandPlan objects or mappings")
    return tuple(plans) if plans else (_build_default_command_plan(),)


def _load_optional_audit_summary() -> dict[str, Any] | None:
    try:
        from src.odysseus_updater_audit_summary import build_odysseus_updater_audit_summary
    except ImportError:
        return None
    summary = build_odysseus_updater_audit_summary()
    return _normalize_audit_summary_payload(summary)


def _derive_bundle_decision(
    *,
    plan: UpdatePlan,
    preflight: PreflightReport,
    backup_gate: BackupGateReport,
    test_gate: UpdaterTestGateReport,
    audit_summary: dict[str, Any] | None,
) -> str:
    decisions = [
        _normalize_decision(plan.decision, field_name="plan.decision"),
        _map_preflight_status_to_decision(preflight.status),
        _normalize_decision(
            backup_gate.deployment_decision,
            field_name="backup_gate.deployment_decision",
        ),
        _normalize_decision(test_gate.decision, field_name="test_gate.decision"),
    ]
    if audit_summary:
        if "decision" in audit_summary:
            decisions.append(_normalize_decision(audit_summary["decision"], field_name="audit.decision"))
        elif "status" in audit_summary:
            decisions.append(_normalize_decision(audit_summary["status"], field_name="audit.status"))
    if "no_go" in decisions:
        return "no_go"
    if "deferred" in decisions:
        return "deferred"
    if "partial" in decisions:
        return "partial"
    return "go"


def _derive_summary(decision: str) -> str:
    if decision == "go":
        return (
            "Offline updater bundle is ready for manual review in dry-run mode; "
            "this is not a live-go signal and live execution stays blocked."
        )
    if decision == "partial":
        return (
            "Offline updater bundle is partially ready for manual review, but some "
            "offline evidence still needs completion; live execution stays blocked."
        )
    if decision == "no_go":
        return (
            "Offline updater bundle is no-go because one or more offline gates are "
            "blocked or failed; live execution stays blocked."
        )
    return (
        "Offline updater bundle is deferred until structured offline inputs close the "
        "remaining review gaps; live execution stays blocked."
    )


def _derive_reasons(
    *,
    decision: str,
    plan: UpdatePlan,
    preflight: PreflightReport,
    backup_gate: BackupGateReport,
    test_gate: UpdaterTestGateReport,
    audit_summary_status: str,
) -> tuple[str, ...]:
    reasons = [
        f"plan decision is {plan.decision}",
        f"preflight status is {preflight.status}",
        f"backup gate decision is {backup_gate.deployment_decision}",
        f"test gate decision is {test_gate.decision}",
        f"optional audit summary status is {audit_summary_status}",
    ]
    if decision == "go":
        reasons.append("all supplied offline updater components are review-ready while runtime remains blocked")
    return _dedupe(reasons, field_name="reason")


def _derive_next_actions(
    *,
    bundle_decision: str,
    plan: UpdatePlan,
    preflight: PreflightReport,
    backup_gate: BackupGateReport,
    test_gate: UpdaterTestGateReport,
    audit_summary_status: str,
) -> tuple[str, ...]:
    actions: list[str] = []
    actions.extend(preflight.next_actions)
    actions.extend(backup_gate.next_actions)
    actions.extend(test_gate.next_actions)
    if audit_summary_status == "not_available":
        actions.append("Optional audit summary module is absent; keep the offline updater bundle self-contained.")
    if bundle_decision == "go":
        actions.append("Use this bundle for manual operator review only; do not execute any live updater action.")
    else:
        actions.append("Keep the updater slice in dry-run mode until the remaining offline gaps are resolved.")
    return _dedupe(actions, field_name="next_action")


@dataclass(frozen=True, slots=True)
class OdysseusUpdaterBundle:
    mode: str
    decision: str
    live_update_decision: str
    live_execution_blocked: bool
    summary: str
    reasons: tuple[str, ...]
    next_actions: tuple[str, ...]
    component_decisions: dict[str, str]
    plan: UpdatePlan
    preflight: PreflightReport
    backup_gate: BackupGateReport
    test_gate: UpdaterTestGateReport
    command_plans: tuple[UpdaterCommandPlan, ...]
    audit_summary_status: str
    audit_summary: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "decision": self.decision,
            "live_update_decision": self.live_update_decision,
            "live_execution_blocked": self.live_execution_blocked,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "next_actions": list(self.next_actions),
            "component_decisions": dict(self.component_decisions),
            "plan": self.plan.to_compact_report(),
            "preflight": self.preflight.to_dict(),
            "backup_gate": self.backup_gate.to_compact_report(),
            "test_gate": self.test_gate.to_compact_report(),
            "command_plans": [plan.to_dict() for plan in self.command_plans],
            "audit_summary_status": self.audit_summary_status,
            "audit_summary": self.audit_summary,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Odysseus Offline Updater Bundle",
            "",
            f"- Mode: `{self.mode}`",
            f"- Offline Decision: `{self.decision}`",
            f"- Live Update Decision: `{self.live_update_decision}`",
            f"- Live Execution Blocked: `{str(self.live_execution_blocked).lower()}`",
            f"- Summary: {self.summary}",
            f"- Optional Audit Summary: `{self.audit_summary_status}`",
            "",
            "## Component Decisions",
        ]
        for key, value in self.component_decisions.items():
            lines.append(f"- `{key}`: `{value}`")
        lines.extend(["", "## Reasons"])
        for reason in self.reasons:
            lines.append(f"- {reason}")
        lines.extend(["", "## Next Actions"])
        for action in self.next_actions:
            lines.append(f"- {action}")
        lines.extend(["", "## Plan", self.plan.to_markdown()])
        lines.extend(["", "## Command Plans"])
        for command_plan in self.command_plans:
            lines.append(command_plan.to_text())
            lines.append("")
        return "\n".join(lines).rstrip()


def build_odysseus_updater(
    *,
    plan_input: UpdatePlan | Mapping[str, Any] | None = None,
    preflight_input: PreflightReport | Mapping[str, Any] | None = None,
    backup_gate_input: BackupGateReport | Mapping[str, Any] | None = None,
    test_gate_input: UpdaterTestGateReport | Mapping[str, Any] | None = None,
    command_plan_inputs: Iterable[UpdaterCommandPlan | Mapping[str, Any]] | None = None,
    include_audit_summary: bool = True,
) -> OdysseusUpdaterBundle:
    plan = _coerce_plan(plan_input)
    preflight = _coerce_preflight(preflight_input)
    backup_gate = _coerce_backup_gate(backup_gate_input)
    test_gate = _coerce_test_gate(test_gate_input)
    command_plans = _coerce_command_plans(command_plan_inputs)
    audit_summary = _load_optional_audit_summary() if include_audit_summary else None
    audit_summary_status = (
        "included"
        if audit_summary is not None
        else ("omitted" if not include_audit_summary else "not_available")
    )
    decision = _derive_bundle_decision(
        plan=plan,
        preflight=preflight,
        backup_gate=backup_gate,
        test_gate=test_gate,
        audit_summary=audit_summary,
    )
    component_decisions = {
        "plan": plan.decision,
        "preflight": _map_preflight_status_to_decision(preflight.status),
        "backup_gate": backup_gate.deployment_decision,
        "test_gate": test_gate.decision,
        "audit_summary": audit_summary_status,
        "live_update": _LIVE_UPDATE_DECISION,
    }
    return OdysseusUpdaterBundle(
        mode=_MODE,
        decision=decision,
        live_update_decision=_LIVE_UPDATE_DECISION,
        live_execution_blocked=True,
        summary=_derive_summary(decision),
        reasons=_derive_reasons(
            decision=decision,
            plan=plan,
            preflight=preflight,
            backup_gate=backup_gate,
            test_gate=test_gate,
            audit_summary_status=audit_summary_status,
        ),
        next_actions=_derive_next_actions(
            bundle_decision=decision,
            plan=plan,
            preflight=preflight,
            backup_gate=backup_gate,
            test_gate=test_gate,
            audit_summary_status=audit_summary_status,
        ),
        component_decisions=component_decisions,
        plan=plan,
        preflight=preflight,
        backup_gate=backup_gate,
        test_gate=test_gate,
        command_plans=command_plans,
        audit_summary_status=audit_summary_status,
        audit_summary=audit_summary,
    )
