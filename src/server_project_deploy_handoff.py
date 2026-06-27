"""Operator-gated deploy handoff model for universal server projects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.odysseus_updater_backup_gate import BackupGateReport, build_odysseus_updater_backup_gate
from src.odysseus_updater_live_boundary import UpdaterLiveBoundary, build_odysseus_updater_live_boundary
from src.server_project_git_review import ProjectGitReviewPlan
from src.server_project_quality_gate import ProjectQualityGateBundle
from src.server_project_registry import ServerProjectRecord


_DECISIONS = ("ready_for_operator_go", "hold", "no_go")


class ServerProjectDeployHandoffError(ValueError):
    """Raised when a deploy handoff cannot be safely modeled."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectDeployHandoffError(f"{field_name} must not be empty")
    lowered = text.lower()
    if any(token in lowered for token in ("token=", "secret=", "password=", "api_key=", "bearer ")):
        raise ServerProjectDeployHandoffError(f"{field_name} appears to contain secret material")
    return text


@dataclass(frozen=True, slots=True)
class ProjectDeployHandoff:
    project_slug: str
    repo_name: str
    decision: str
    live_execution_allowed: bool
    blockers: tuple[str, ...]
    backup_gate: BackupGateReport
    live_boundary: UpdaterLiveBoundary
    planned_steps: tuple[Mapping[str, Any], ...]
    next_human_decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_name": self.repo_name,
            "decision": self.decision,
            "live_execution_allowed": self.live_execution_allowed,
            "blockers": list(self.blockers),
            "backup_gate": self.backup_gate.to_compact_report(),
            "live_boundary": self.live_boundary.to_dict(),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "next_human_decision": self.next_human_decision,
        }


def build_project_deploy_handoff(
    *,
    record: ServerProjectRecord,
    quality_bundle: ProjectQualityGateBundle,
    git_review_plan: ProjectGitReviewPlan,
    backup_evidence_inputs: Iterable[Mapping[str, Any]],
    evaluated_at: Any,
    operator_decision: Any = "missing",
    command_plan_reviewed: bool = False,
    secret_or_private_output_risk: bool = False,
    live_command_requested: bool = False,
) -> ProjectDeployHandoff:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectDeployHandoffError("record must be a ServerProjectRecord")
    if not isinstance(quality_bundle, ProjectQualityGateBundle):
        raise ServerProjectDeployHandoffError("quality_bundle must be a ProjectQualityGateBundle")
    if not isinstance(git_review_plan, ProjectGitReviewPlan):
        raise ServerProjectDeployHandoffError("git_review_plan must be a ProjectGitReviewPlan")
    normalized_operator = _normalize_text(operator_decision, field_name="operator_decision").lower().replace("-", "_")
    backup_gate = build_odysseus_updater_backup_gate(
        risk_level="medium",
        evaluated_at=_normalize_text(evaluated_at, field_name="evaluated_at"),
        evidence_inputs=backup_evidence_inputs,
    )
    live_boundary = build_odysseus_updater_live_boundary(
        pre_update_snapshot_green=_evidence_green(backup_gate, "pre_update_snapshot"),
        repository_check_green=_evidence_green(backup_gate, "repository_check"),
        restore_smoke_green=_evidence_green(backup_gate, "restore_smoke"),
        focused_tests_green=quality_bundle.deploy_gate_ready,
        command_plan_reviewed=command_plan_reviewed and git_review_plan.push_allowed,
        operator_decision=normalized_operator,
        secret_or_private_output_risk=secret_or_private_output_risk,
        live_command_requested=live_command_requested,
    )

    blockers: list[str] = []
    if quality_bundle.decision != "plan_ready":
        blockers.append(f"quality gates are {quality_bundle.decision}")
    if git_review_plan.decision != "plan_ready":
        blockers.append(f"git review is {git_review_plan.decision}")
    if backup_gate.deployment_decision != "go":
        blockers.append(f"backup gate deployment decision is {backup_gate.deployment_decision}")
    blockers.extend(live_boundary.blockers)

    if secret_or_private_output_risk or live_command_requested or live_boundary.decision == "no_go":
        decision = "no_go"
    elif blockers:
        decision = "hold"
    else:
        decision = "ready_for_operator_go"

    return ProjectDeployHandoff(
        project_slug=record.project_slug,
        repo_name=record.project_spec.repo_name,
        decision=decision,
        live_execution_allowed=False,
        blockers=tuple(dict.fromkeys(blockers)),
        backup_gate=backup_gate,
        live_boundary=live_boundary,
        planned_steps=_planned_steps(record),
        next_human_decision=_next_human_decision(decision),
    )


def _evidence_green(report: BackupGateReport, evidence_id: str) -> bool:
    return any(item.evidence_id == evidence_id and item.state == "green" and item.result_label == "pass" for item in report.evidence)


def _planned_steps(record: ServerProjectRecord) -> tuple[Mapping[str, Any], ...]:
    return (
        {
            "step_id": "pre_update_snapshot",
            "summary": "require operator-approved pre-update snapshot before deployment",
            "executes": False,
        },
        {
            "step_id": "version_metadata_refresh",
            "summary": f"prepare git metadata refresh for {record.project_spec.repo_name}",
            "executes": False,
        },
        {
            "step_id": "podman_deploy_handoff",
            "summary": "handoff Podman compose deployment to the existing operator-gated updater path",
            "executes": False,
        },
        {
            "step_id": "healthcheck",
            "summary": "require app and dependency health checks after any future operator deploy",
            "executes": False,
        },
        {
            "step_id": "smoke_gate",
            "summary": "require bounded project smoke tests before stable outcome",
            "executes": False,
        },
        {
            "step_id": "rollback_or_hold",
            "summary": "record rollback or hold decision if health or smoke evidence is not green",
            "executes": False,
        },
    )


def _next_human_decision(decision: str) -> str:
    if decision == "ready_for_operator_go":
        return "Operator may make a separate live Go/No-Go decision; this handoff still does not execute deploy."
    if decision == "no_go":
        return "Do not deploy; clear no-go risk before preparing another handoff."
    return "Complete quality, git, backup, command-plan and operator-go evidence before live review."
