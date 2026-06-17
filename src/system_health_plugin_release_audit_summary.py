"""Release audit summary models for the system health plugin foundation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.system_health_agent_interface import HealthAgentInterfaceError
from src.system_health_plugin_foundation_readiness_index import (
    SystemHealthPluginFoundationReadinessIndex,
)
from src.system_health_plugin_operator_review_packet import (
    SystemHealthPluginOperatorReviewPacket,
)
from src.system_health_plugin_readiness_score import SystemHealthPluginReadinessScore


_SECTION_IDS = (
    "summary_purpose",
    "included_foundation_artifacts",
    "verification_references",
    "manual_go_no_go",
    "runtime_boundaries",
    "release_risks",
    "next_allowed_slices",
)

_STATUS_VALUES = (
    "release_review_ready",
    "blocked",
    "needs_operator_input",
    "deferred",
)

_REQUIRED_RUNTIME_BOUNDARIES = (
    "host_commands_from_core",
    "telegram_tokens",
    "webhook_activation",
    "podman_docker_socket_mount",
    "privileged_container_access",
    "direct_smart_access_from_container",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _STATUS_VALUES:
        raise HealthAgentInterfaceError("unsupported release audit status")
    return text


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise HealthAgentInterfaceError("unsupported release audit section_id")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class ReleaseAuditRisk:
    risk_id: str
    summary: str

    @classmethod
    def create(cls, *, risk_id: Any, summary: Any) -> "ReleaseAuditRisk":
        return cls(
            risk_id=_normalize_text(risk_id, field_name="risk_id").strip().lower().replace(" ", "_"),
            summary=_normalize_text(summary, field_name="summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_id": self.risk_id,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class ReleaseAuditSection:
    section_id: str
    summary: str
    detail_count: int

    @classmethod
    def create(
        cls,
        *,
        section_id: Any,
        summary: Any,
        detail_count: int,
    ) -> "ReleaseAuditSection":
        if detail_count < 0:
            raise HealthAgentInterfaceError("detail_count must be non-negative")
        return cls(
            section_id=_normalize_section_id(section_id),
            summary=_normalize_text(summary, field_name="summary"),
            detail_count=detail_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "summary": self.summary,
            "detail_count": self.detail_count,
        }


@dataclass(frozen=True, slots=True)
class SystemHealthPluginReleaseAuditSummary:
    status: str
    runtime_disabled: bool
    included_foundation_artifacts: tuple[str, ...]
    verification_references: tuple[str, ...]
    release_risks: tuple[ReleaseAuditRisk, ...]
    next_allowed_slices: tuple[str, ...]
    sections: tuple[ReleaseAuditSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "runtime_disabled": self.runtime_disabled,
            "included_foundation_artifacts": self.included_foundation_artifacts,
            "verification_references": self.verification_references,
            "release_risks": tuple(item.to_dict() for item in self.release_risks),
            "next_allowed_slices": self.next_allowed_slices,
            "sections": tuple(section.to_dict() for section in self.sections),
        }

    def to_markdown(self) -> str:
        lines = [
            "# System Health Plugin Release Audit Summary",
            "",
            f"- Status: `{self.status}`",
            f"- Runtime disabled: `{str(self.runtime_disabled).lower()}`",
            f"- Included artifacts: {', '.join(self.included_foundation_artifacts) if self.included_foundation_artifacts else 'none'}",
            "",
            "## Release Risks",
        ]
        for risk in self.release_risks:
            lines.append(f"- `{risk.risk_id}`: {risk.summary}")
        lines.append("")
        for section in self.sections:
            lines.extend(
                [
                    f"## {section.section_id}",
                    f"- Summary: {section.summary}",
                    f"- Count: {section.detail_count}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()


def build_system_health_plugin_release_audit_summary(
    *,
    foundation_readiness_index: SystemHealthPluginFoundationReadinessIndex | None = None,
    operator_review_packet: SystemHealthPluginOperatorReviewPacket | None = None,
    readiness_score: SystemHealthPluginReadinessScore | None = None,
) -> SystemHealthPluginReleaseAuditSummary:
    if foundation_readiness_index is not None and not isinstance(
        foundation_readiness_index, SystemHealthPluginFoundationReadinessIndex
    ):
        raise HealthAgentInterfaceError(
            "foundation_readiness_index must be a SystemHealthPluginFoundationReadinessIndex or None"
        )
    if operator_review_packet is not None and not isinstance(
        operator_review_packet, SystemHealthPluginOperatorReviewPacket
    ):
        raise HealthAgentInterfaceError(
            "operator_review_packet must be a SystemHealthPluginOperatorReviewPacket or None"
        )
    if readiness_score is not None and not isinstance(readiness_score, SystemHealthPluginReadinessScore):
        raise HealthAgentInterfaceError("readiness_score must be a SystemHealthPluginReadinessScore or None")

    included_artifacts_list = []
    verification_references_list = []
    if foundation_readiness_index is not None:
        included_artifacts_list.append("foundation_readiness_index")
        verification_references_list.append(f"foundation_status:{foundation_readiness_index.status}")
    if operator_review_packet is not None:
        included_artifacts_list.append("operator_review_packet")
        verification_references_list.append(f"review_packet:{operator_review_packet.decision_state}")
    if readiness_score is not None:
        included_artifacts_list.append("readiness_score")
        verification_references_list.append(f"readiness_score:{readiness_score.decision_state}")

    included_artifacts = tuple(included_artifacts_list)
    verification_references = _normalize_tuple(
        verification_references_list,
        field_name="verification_reference",
    )

    runtime_disabled = True
    missing_runtime_boundaries = ()
    if operator_review_packet is not None:
        missing_runtime_boundaries = tuple(
            boundary
            for boundary in _REQUIRED_RUNTIME_BOUNDARIES
            if boundary not in set(operator_review_packet.blocked_runtime_actions)
        )
        runtime_disabled = not missing_runtime_boundaries

    release_risks_list: list[ReleaseAuditRisk] = []
    if foundation_readiness_index is None:
        release_risks_list.append(
            ReleaseAuditRisk.create(
                risk_id="missing_foundation_index",
                summary="foundation readiness index is missing, so release audit review stays deferred",
            )
        )
    elif foundation_readiness_index.status == "blocked":
        release_risks_list.append(
            ReleaseAuditRisk.create(
                risk_id="foundation_blocked",
                summary="foundation readiness index is blocked and prevents release audit review",
            )
        )
    elif foundation_readiness_index.status == "review_required":
        release_risks_list.append(
            ReleaseAuditRisk.create(
                risk_id="operator_signoff_pending",
                summary="manual operator signoff is still required before release audit review can conclude",
            )
        )

    if missing_runtime_boundaries:
        release_risks_list.append(
            ReleaseAuditRisk.create(
                risk_id="runtime_boundary_gap",
                summary=f"runtime-disabled boundaries are incomplete: {', '.join(missing_runtime_boundaries)}",
            )
        )

    if readiness_score is not None and readiness_score.decision_state == "blocked":
        release_risks_list.append(
            ReleaseAuditRisk.create(
                risk_id="readiness_score_blocked",
                summary="readiness score is blocked and prevents release audit review",
            )
        )

    release_risks = tuple(release_risks_list)
    next_allowed_slices = (
        foundation_readiness_index.next_allowed_slices
        if foundation_readiness_index is not None
        else ("host-agent-runtime", "telegram-delivery", "container-runtime-probes")
    )

    if foundation_readiness_index is not None and foundation_readiness_index.status == "blocked":
        status = "blocked"
    elif readiness_score is not None and readiness_score.decision_state == "blocked":
        status = "blocked"
    elif missing_runtime_boundaries:
        status = "blocked"
    elif not included_artifacts:
        status = "deferred"
    elif (
        foundation_readiness_index is None
        or operator_review_packet is None
        or readiness_score is None
    ):
        status = "deferred"
    elif foundation_readiness_index.status == "review_required":
        status = "needs_operator_input"
    elif operator_review_packet.decision_state == "needs_operator_input":
        status = "needs_operator_input"
    elif foundation_readiness_index.status == "foundation_ready" and runtime_disabled:
        status = "release_review_ready"
    else:
        status = "deferred"

    sections = (
        ReleaseAuditSection.create(
            section_id="summary_purpose",
            summary="release audit summary packages foundation evidence for operator review without enabling runtime execution",
            detail_count=1,
        ),
        ReleaseAuditSection.create(
            section_id="included_foundation_artifacts",
            summary=(
                "foundation artifacts are present for release audit review"
                if len(included_artifacts) == 3
                else "release audit is waiting for the full foundation artifact set"
            ),
            detail_count=len(included_artifacts),
        ),
        ReleaseAuditSection.create(
            section_id="verification_references",
            summary="verification references capture the attached foundation and operator review states",
            detail_count=len(verification_references),
        ),
        ReleaseAuditSection.create(
            section_id="manual_go_no_go",
            summary=(
                "manual go/no-go remains open until operator inputs are resolved"
                if status in {"needs_operator_input", "deferred"}
                else "manual go/no-go package is ready for release-audit review"
            ),
            detail_count=3,
        ),
        ReleaseAuditSection.create(
            section_id="runtime_boundaries",
            summary=(
                "runtime boundaries remain disabled during release audit review"
                if runtime_disabled
                else "runtime boundary gaps block release audit review"
            ),
            detail_count=len(_REQUIRED_RUNTIME_BOUNDARIES),
        ),
        ReleaseAuditSection.create(
            section_id="release_risks",
            summary="release risks are tracked conservatively for operator review",
            detail_count=len(release_risks),
        ),
        ReleaseAuditSection.create(
            section_id="next_allowed_slices",
            summary="next allowed slices stay deferred until release audit review is complete",
            detail_count=len(next_allowed_slices),
        ),
    )

    return SystemHealthPluginReleaseAuditSummary(
        status=_normalize_status(status),
        runtime_disabled=runtime_disabled,
        included_foundation_artifacts=included_artifacts,
        verification_references=verification_references,
        release_risks=release_risks,
        next_allowed_slices=tuple(next_allowed_slices),
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
    )
