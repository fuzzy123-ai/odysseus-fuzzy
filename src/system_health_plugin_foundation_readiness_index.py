"""Foundation readiness index models for the system health plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.system_health_agent_interface import HealthAgentInterfaceError
from src.system_health_plugin_audit_index import SystemHealthPluginAuditIndex
from src.system_health_plugin_operator_review_packet import SystemHealthPluginOperatorReviewPacket
from src.system_health_plugin_readiness_score import SystemHealthPluginReadinessScore


_SECTION_IDS = (
    "foundation_artifacts",
    "readiness_evidence",
    "manual_review_gates",
    "runtime_still_disabled",
    "known_limits",
    "next_allowed_slices",
)

_STATUS_VALUES = (
    "foundation_ready",
    "review_required",
    "blocked",
    "deferred",
)

_REQUIRED_DISABLED_ACTIONS = (
    "host_commands_from_core",
    "telegram_tokens",
    "webhook_activation",
    "podman_docker_socket_mount",
    "privileged_container_access",
    "direct_smart_access_from_container",
)

_DEFAULT_KNOWN_LIMITS = (
    "no_live_host_agent_runtime",
    "no_telegram_delivery",
    "no_container_runtime_calls",
)

_DEFAULT_NEXT_ALLOWED_SLICES = (
    "host-agent-runtime",
    "telegram-delivery",
    "container-runtime-probes",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _STATUS_VALUES:
        raise HealthAgentInterfaceError("unsupported foundation readiness status")
    return text


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise HealthAgentInterfaceError("unsupported foundation readiness section_id")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class FoundationReadinessEvidence:
    evidence_id: str
    label: str

    @classmethod
    def create(cls, *, evidence_id: Any, label: Any) -> "FoundationReadinessEvidence":
        return cls(
            evidence_id=_normalize_text(evidence_id, field_name="evidence_id").strip().lower().replace(" ", "_"),
            label=_normalize_text(label, field_name="label"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class FoundationReadinessSection:
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
    ) -> "FoundationReadinessSection":
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
class SystemHealthPluginFoundationReadinessIndex:
    status: str
    runtime_disabled: bool
    artifacts_present: tuple[str, ...]
    readiness_evidence: tuple[FoundationReadinessEvidence, ...]
    known_limits: tuple[str, ...]
    next_allowed_slices: tuple[str, ...]
    sections: tuple[FoundationReadinessSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "runtime_disabled": self.runtime_disabled,
            "artifacts_present": self.artifacts_present,
            "readiness_evidence": tuple(item.to_dict() for item in self.readiness_evidence),
            "known_limits": self.known_limits,
            "next_allowed_slices": self.next_allowed_slices,
            "sections": tuple(section.to_dict() for section in self.sections),
        }

    def to_markdown(self) -> str:
        lines = [
            "# System Health Plugin Foundation Readiness Index",
            "",
            f"- Status: `{self.status}`",
            f"- Runtime disabled: `{str(self.runtime_disabled).lower()}`",
            f"- Artifacts present: {', '.join(self.artifacts_present) if self.artifacts_present else 'none'}",
            "",
            "## Readiness Evidence",
        ]
        for item in self.readiness_evidence:
            lines.append(f"- `{item.evidence_id}`: {item.label}")
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


def build_system_health_plugin_foundation_readiness_index(
    *,
    audit_index: SystemHealthPluginAuditIndex | None = None,
    readiness_score: SystemHealthPluginReadinessScore | None = None,
    operator_review_packet: SystemHealthPluginOperatorReviewPacket | None = None,
) -> SystemHealthPluginFoundationReadinessIndex:
    if audit_index is not None and not isinstance(audit_index, SystemHealthPluginAuditIndex):
        raise HealthAgentInterfaceError("audit_index must be a SystemHealthPluginAuditIndex or None")
    if readiness_score is not None and not isinstance(readiness_score, SystemHealthPluginReadinessScore):
        raise HealthAgentInterfaceError("readiness_score must be a SystemHealthPluginReadinessScore or None")
    if operator_review_packet is not None and not isinstance(
        operator_review_packet, SystemHealthPluginOperatorReviewPacket
    ):
        raise HealthAgentInterfaceError(
            "operator_review_packet must be a SystemHealthPluginOperatorReviewPacket or None"
        )

    artifacts_present_list = []
    readiness_evidence_list = []
    if audit_index is not None:
        artifacts_present_list.append("audit_index")
        readiness_evidence_list.append(
            FoundationReadinessEvidence.create(
                evidence_id="audit_index",
                label=f"audit index status `{audit_index.overall_status}` is attached",
            )
        )
    if readiness_score is not None:
        artifacts_present_list.append("readiness_score")
        readiness_evidence_list.append(
            FoundationReadinessEvidence.create(
                evidence_id="readiness_score",
                label=f"readiness score decision `{readiness_score.decision_state}` is attached",
            )
        )
    if operator_review_packet is not None:
        artifacts_present_list.append("operator_review_packet")
        readiness_evidence_list.append(
            FoundationReadinessEvidence.create(
                evidence_id="operator_review_packet",
                label=f"operator review packet decision `{operator_review_packet.decision_state}` is attached",
            )
        )

    artifacts_present = tuple(artifacts_present_list)
    readiness_evidence = tuple(readiness_evidence_list)
    known_limits = _normalize_tuple(_DEFAULT_KNOWN_LIMITS, field_name="known_limit")
    next_allowed_slices = _normalize_tuple(_DEFAULT_NEXT_ALLOWED_SLICES, field_name="next_allowed_slice")

    runtime_disabled = True
    missing_disabled_actions = ()
    if operator_review_packet is not None:
        missing_disabled_actions = tuple(
            action
            for action in _REQUIRED_DISABLED_ACTIONS
            if action not in set(operator_review_packet.blocked_runtime_actions)
        )
        runtime_disabled = not missing_disabled_actions

    if operator_review_packet is not None and operator_review_packet.decision_state == "blocked":
        status = "blocked"
    elif readiness_score is not None and readiness_score.decision_state == "blocked":
        status = "blocked"
    elif missing_disabled_actions:
        status = "blocked"
    elif not artifacts_present:
        status = "deferred"
    elif audit_index is None or readiness_score is None or operator_review_packet is None:
        status = "deferred"
    elif operator_review_packet.decision_state == "needs_operator_input":
        status = "review_required"
    elif operator_review_packet.decision_state == "review_ready" and runtime_disabled:
        status = "foundation_ready"
    elif readiness_score.decision_state in {"ready_for_manual_review", "review_required"}:
        status = "review_required"
    else:
        status = "deferred"

    sections = (
        FoundationReadinessSection.create(
            section_id="foundation_artifacts",
            summary=(
                "foundation artifacts are attached for operator review"
                if len(artifacts_present) == 3
                else "foundation artifact set is incomplete"
            ),
            detail_count=len(artifacts_present),
        ),
        FoundationReadinessSection.create(
            section_id="readiness_evidence",
            summary="readiness evidence references remain read-only and operator-facing",
            detail_count=len(readiness_evidence),
        ),
        FoundationReadinessSection.create(
            section_id="manual_review_gates",
            summary=(
                "manual review gates remain open until operator signoff is complete"
                if status in {"review_required", "deferred"}
                else "manual review gates are satisfied for foundation-only readiness"
            ),
            detail_count=3,
        ),
        FoundationReadinessSection.create(
            section_id="runtime_still_disabled",
            summary=(
                "runtime remains explicitly disabled during foundation readiness review"
                if runtime_disabled
                else "runtime disable boundaries are incomplete and block readiness"
            ),
            detail_count=len(_REQUIRED_DISABLED_ACTIONS),
        ),
        FoundationReadinessSection.create(
            section_id="known_limits",
            summary="known limits keep host, telegram, and container runtime actions out of scope",
            detail_count=len(known_limits),
        ),
        FoundationReadinessSection.create(
            section_id="next_allowed_slices",
            summary="follow-up slices remain deferred until operator review clears the foundation packet",
            detail_count=len(next_allowed_slices),
        ),
    )

    return SystemHealthPluginFoundationReadinessIndex(
        status=_normalize_status(status),
        runtime_disabled=runtime_disabled,
        artifacts_present=artifacts_present,
        readiness_evidence=readiness_evidence,
        known_limits=known_limits,
        next_allowed_slices=next_allowed_slices,
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
    )
