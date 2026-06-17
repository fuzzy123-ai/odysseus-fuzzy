"""Readiness scoring models for system health plugin operator review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.system_health_agent_interface import HealthAgentInterfaceError
from src.system_health_plugin_audit_index import SystemHealthPluginAuditIndex


_DIMENSION_IDS = (
    "foundation_completeness",
    "host_boundary_safety",
    "audit_coverage",
    "operator_docs",
    "runtime_no_go_integrity",
    "deployment_prerequisites",
)

_DECISION_STATES = (
    "ready_for_manual_review",
    "blocked",
    "review_required",
    "deferred",
)

_DIMENSION_STATUSES = (
    "pass",
    "blocked",
    "review_required",
    "deferred",
)

_REQUIRED_NO_GO_ACTIONS = (
    "host_commands_from_core",
    "telegram_tokens",
    "webhook_activation",
    "podman_docker_socket_mount",
    "privileged_container_access",
    "direct_smart_access_from_container",
)

_REQUIRED_ARCHITECTURE_NOTES = (
    "podman_first",
    "docker_compatible",
    "host_agent_required",
    "odysseus_consumes_sanitized_snapshot",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_dimension_id(value: Any) -> str:
    text = _normalize_text(value, field_name="dimension_id").strip().lower()
    if text not in _DIMENSION_IDS:
        raise HealthAgentInterfaceError("unsupported readiness score dimension_id")
    return text


def _normalize_decision_state(value: Any) -> str:
    text = _normalize_text(value, field_name="decision_state").strip().lower()
    if text not in _DECISION_STATES:
        raise HealthAgentInterfaceError("unsupported readiness decision_state")
    return text


def _normalize_dimension_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _DIMENSION_STATUSES:
        raise HealthAgentInterfaceError("unsupported readiness dimension status")
    return text


@dataclass(frozen=True, slots=True)
class ReadinessScoreDimension:
    dimension_id: str
    score: int
    status: str
    summary: str

    @classmethod
    def create(
        cls,
        *,
        dimension_id: Any,
        score: int,
        status: Any,
        summary: Any,
    ) -> "ReadinessScoreDimension":
        if score < 0 or score > 100:
            raise HealthAgentInterfaceError("score must be between 0 and 100")
        return cls(
            dimension_id=_normalize_dimension_id(dimension_id),
            score=score,
            status=_normalize_dimension_status(status),
            summary=_normalize_text(summary, field_name="summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "score": self.score,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class ReadinessScoreSummary:
    decision_state: str
    runtime_ready: bool
    overall_score: int
    next_action: str
    blocker_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_state": self.decision_state,
            "runtime_ready": self.runtime_ready,
            "overall_score": self.overall_score,
            "next_action": self.next_action,
            "blocker_count": self.blocker_count,
        }


@dataclass(frozen=True, slots=True)
class SystemHealthPluginReadinessScore:
    decision_state: str
    runtime_ready: bool
    dimensions: tuple[ReadinessScoreDimension, ...]
    summary: ReadinessScoreSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_state": self.decision_state,
            "runtime_ready": self.runtime_ready,
            "dimensions": tuple(item.to_dict() for item in self.dimensions),
            "summary": self.summary.to_dict(),
        }

    def to_markdown(self) -> str:
        lines = [
            "# System Health Plugin Readiness Score",
            "",
            f"- Decision state: `{self.decision_state}`",
            f"- Runtime ready: `{str(self.runtime_ready).lower()}`",
            f"- Overall score: `{self.summary.overall_score}`",
            f"- Next action: {self.summary.next_action}",
            "",
            "## Dimensions",
        ]
        for dimension in self.dimensions:
            lines.append(
                f"- `{dimension.dimension_id}`: {dimension.status} ({dimension.score}) - {dimension.summary}"
            )
        return "\n".join(lines).rstrip()


def _get_section_count(index: SystemHealthPluginAuditIndex, section_id: str) -> int:
    for section in index.sections:
        if section.section_id == section_id:
            return section.detail_count
    return 0


def build_system_health_plugin_readiness_score(
    audit_index: SystemHealthPluginAuditIndex | None = None,
) -> SystemHealthPluginReadinessScore:
    if audit_index is not None and not isinstance(audit_index, SystemHealthPluginAuditIndex):
        raise HealthAgentInterfaceError("audit_index must be a SystemHealthPluginAuditIndex or None")

    if audit_index is None:
        dimensions = (
            ReadinessScoreDimension.create(
                dimension_id="foundation_completeness",
                score=0,
                status="review_required",
                summary="audit index is missing, so foundation completeness cannot be confirmed",
            ),
            ReadinessScoreDimension.create(
                dimension_id="host_boundary_safety",
                score=0,
                status="review_required",
                summary="host boundary safety cannot be verified without an audit index",
            ),
            ReadinessScoreDimension.create(
                dimension_id="audit_coverage",
                score=0,
                status="review_required",
                summary="audit coverage is unknown until an audit index is attached",
            ),
            ReadinessScoreDimension.create(
                dimension_id="operator_docs",
                score=0,
                status="review_required",
                summary="operator-facing audit references are missing until the audit index is attached",
            ),
            ReadinessScoreDimension.create(
                dimension_id="runtime_no_go_integrity",
                score=0,
                status="review_required",
                summary="runtime no-go integrity cannot be confirmed without an audit index",
            ),
            ReadinessScoreDimension.create(
                dimension_id="deployment_prerequisites",
                score=0,
                status="review_required",
                summary="deployment prerequisites remain unknown without an audit index",
            ),
        )
        summary = ReadinessScoreSummary(
            decision_state="review_required",
            runtime_ready=False,
            overall_score=0,
            next_action="attach the system health plugin audit index before manual review",
            blocker_count=0,
        )
        return SystemHealthPluginReadinessScore(
            decision_state="review_required",
            runtime_ready=False,
            dimensions=dimensions,
            summary=summary,
        )

    no_go_actions = set(audit_index.no_go_runtime_actions)
    architecture_notes = set(audit_index.architecture_notes)
    missing_no_go = sorted(set(_REQUIRED_NO_GO_ACTIONS) - no_go_actions)
    missing_architecture_notes = sorted(set(_REQUIRED_ARCHITECTURE_NOTES) - architecture_notes)
    review_test_count = len(audit_index.required_review_tests)
    foundation_count = _get_section_count(audit_index, "plugin_foundation_artifacts")
    deployment_count = _get_section_count(audit_index, "deployment_prerequisites")
    operator_checklist_count = _get_section_count(audit_index, "operator_audit_checklist")

    if missing_no_go:
        foundation_dimension = ReadinessScoreDimension.create(
            dimension_id="foundation_completeness",
            score=60 if foundation_count else 20,
            status="review_required",
            summary="foundation artifacts exist but the no-go runtime list is incomplete",
        )
        host_boundary_dimension = ReadinessScoreDimension.create(
            dimension_id="host_boundary_safety",
            score=100 if not missing_architecture_notes else 50,
            status="review_required" if missing_architecture_notes else "pass",
            summary=(
                "host boundary notes are complete"
                if not missing_architecture_notes
                else "host boundary notes are incomplete and require review"
            ),
        )
        audit_dimension = ReadinessScoreDimension.create(
            dimension_id="audit_coverage",
            score=100 if review_test_count else 40,
            status="pass" if review_test_count else "review_required",
            summary=(
                "audit references include required review tests"
                if review_test_count
                else "required review tests are missing from the audit index"
            ),
        )
        operator_docs_dimension = ReadinessScoreDimension.create(
            dimension_id="operator_docs",
            score=100 if operator_checklist_count else 40,
            status="pass" if operator_checklist_count else "review_required",
            summary=(
                "operator checklist references are present"
                if operator_checklist_count
                else "operator checklist references are missing"
            ),
        )
        no_go_dimension = ReadinessScoreDimension.create(
            dimension_id="runtime_no_go_integrity",
            score=0,
            status="blocked",
            summary=f"unsafe runtime no-go entries are missing: {', '.join(missing_no_go)}",
        )
        deployment_dimension = ReadinessScoreDimension.create(
            dimension_id="deployment_prerequisites",
            score=100 if deployment_count >= 2 else 50,
            status="pass" if deployment_count >= 2 else "review_required",
            summary=(
                "deployment prerequisites are documented for manual review"
                if deployment_count >= 2
                else "deployment prerequisites are incomplete and need review"
            ),
        )
        dimensions = (
            foundation_dimension,
            host_boundary_dimension,
            audit_dimension,
            operator_docs_dimension,
            no_go_dimension,
            deployment_dimension,
        )
        overall_score = sum(item.score for item in dimensions) // len(dimensions)
        summary = ReadinessScoreSummary(
            decision_state="blocked",
            runtime_ready=False,
            overall_score=overall_score,
            next_action="restore all required no-go runtime actions before operator review",
            blocker_count=1,
        )
        return SystemHealthPluginReadinessScore(
            decision_state="blocked",
            runtime_ready=False,
            dimensions=tuple(sorted(dimensions, key=lambda item: item.dimension_id)),
            summary=summary,
        )

    dimensions = (
        ReadinessScoreDimension.create(
            dimension_id="foundation_completeness",
            score=100 if foundation_count else 40,
            status="pass" if foundation_count else "review_required",
            summary=(
                "foundation artifacts are present for operator review"
                if foundation_count
                else "foundation artifacts are missing and need review"
            ),
        ),
        ReadinessScoreDimension.create(
            dimension_id="host_boundary_safety",
            score=100 if not missing_architecture_notes else 50,
            status="pass" if not missing_architecture_notes else "review_required",
            summary=(
                "host boundary safety notes are complete"
                if not missing_architecture_notes
                else "host boundary safety notes are incomplete and need review"
            ),
        ),
        ReadinessScoreDimension.create(
            dimension_id="audit_coverage",
            score=100 if review_test_count else 40,
            status="pass" if review_test_count else "review_required",
            summary=(
                "audit coverage includes required review tests"
                if review_test_count
                else "required review tests are missing from the audit index"
            ),
        ),
        ReadinessScoreDimension.create(
            dimension_id="operator_docs",
            score=100 if operator_checklist_count else 40,
            status="pass" if operator_checklist_count else "review_required",
            summary=(
                "operator checklist references are present"
                if operator_checklist_count
                else "operator checklist references are missing"
            ),
        ),
        ReadinessScoreDimension.create(
            dimension_id="runtime_no_go_integrity",
            score=100,
            status="pass",
            summary="runtime no-go integrity is preserved and runtime remains intentionally disabled",
        ),
        ReadinessScoreDimension.create(
            dimension_id="deployment_prerequisites",
            score=100 if deployment_count >= 2 else 50,
            status="pass" if deployment_count >= 2 else "review_required",
            summary=(
                "deployment prerequisites are documented for manual review"
                if deployment_count >= 2
                else "deployment prerequisites are incomplete and need review"
            ),
        ),
    )

    review_required = any(item.status == "review_required" for item in dimensions)
    if review_required:
        decision_state = "review_required"
        next_action = "fill missing audit evidence before asking for manual operator review"
    else:
        decision_state = "ready_for_manual_review"
        next_action = "request manual operator review while keeping runtime execution disabled"

    overall_score = sum(item.score for item in dimensions) // len(dimensions)
    summary = ReadinessScoreSummary(
        decision_state=decision_state,
        runtime_ready=False,
        overall_score=overall_score,
        next_action=next_action,
        blocker_count=0,
    )
    return SystemHealthPluginReadinessScore(
        decision_state=decision_state,
        runtime_ready=False,
        dimensions=tuple(sorted(dimensions, key=lambda item: item.dimension_id)),
        summary=summary,
    )
