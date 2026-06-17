"""Operator review packet models for the system health plugin foundation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.system_health_agent_interface import HealthAgentInterfaceError
from src.system_health_plugin_audit_index import SystemHealthPluginAuditIndex
from src.system_health_plugin_readiness_score import SystemHealthPluginReadinessScore


_SECTION_IDS = (
    "packet_purpose",
    "included_artifacts",
    "review_order",
    "go_no_go_questions",
    "blocked_runtime_actions",
    "operator_signoff_inputs",
    "followup_slices",
)

_DECISION_STATES = (
    "review_ready",
    "blocked",
    "needs_operator_input",
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

_DEFAULT_REVIEW_ORDER = (
    "audit_index",
    "readiness_score",
    "blocked_runtime_actions",
    "operator_signoff_inputs",
)

_DEFAULT_GO_NO_GO_QUESTIONS = (
    "Do host-agent boundaries stay outside Odysseus core runtime paths?",
    "Are runtime no-go actions still explicitly blocked for foundation mode?",
    "Are review tests and deployment prerequisites documented for manual operator review?",
)

_DEFAULT_SIGNOFF_INPUTS = (
    "operator_name",
    "review_timestamp",
    "manual_go_no_go_decision",
    "followup_notes",
)

_DEFAULT_FOLLOWUP_SLICES = (
    "host-agent-runtime",
    "telegram-delivery",
    "container-runtime-probes",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise HealthAgentInterfaceError("unsupported operator review section_id")
    return text


def _normalize_decision_state(value: Any) -> str:
    text = _normalize_text(value, field_name="decision_state").strip().lower()
    if text not in _DECISION_STATES:
        raise HealthAgentInterfaceError("unsupported operator review decision_state")
    return text


def _normalize_str_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class OperatorReviewQuestion:
    question_id: str
    prompt: str

    @classmethod
    def create(cls, *, question_id: Any, prompt: Any) -> "OperatorReviewQuestion":
        return cls(
            question_id=_normalize_text(question_id, field_name="question_id").strip().lower().replace(" ", "_"),
            prompt=_normalize_text(prompt, field_name="prompt"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "prompt": self.prompt,
        }


@dataclass(frozen=True, slots=True)
class OperatorReviewSection:
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
    ) -> "OperatorReviewSection":
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
class SystemHealthPluginOperatorReviewPacket:
    decision_state: str
    included_artifacts: tuple[str, ...]
    review_order: tuple[str, ...]
    go_no_go_questions: tuple[OperatorReviewQuestion, ...]
    blocked_runtime_actions: tuple[str, ...]
    operator_signoff_inputs: tuple[str, ...]
    followup_slices: tuple[str, ...]
    sections: tuple[OperatorReviewSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_state": self.decision_state,
            "included_artifacts": self.included_artifacts,
            "review_order": self.review_order,
            "go_no_go_questions": tuple(question.to_dict() for question in self.go_no_go_questions),
            "blocked_runtime_actions": self.blocked_runtime_actions,
            "operator_signoff_inputs": self.operator_signoff_inputs,
            "followup_slices": self.followup_slices,
            "sections": tuple(section.to_dict() for section in self.sections),
        }

    def to_markdown(self) -> str:
        lines = [
            "# System Health Plugin Operator Review Packet",
            "",
            f"- Decision state: `{self.decision_state}`",
            f"- Included artifacts: {', '.join(self.included_artifacts) if self.included_artifacts else 'none'}",
            f"- Blocked runtime actions: {', '.join(self.blocked_runtime_actions)}",
            "",
            "## Go / No-Go Questions",
        ]
        for question in self.go_no_go_questions:
            lines.append(f"- {question.prompt}")
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


def build_system_health_plugin_operator_review_packet(
    *,
    audit_index: SystemHealthPluginAuditIndex | None = None,
    readiness_score: SystemHealthPluginReadinessScore | None = None,
) -> SystemHealthPluginOperatorReviewPacket:
    if audit_index is not None and not isinstance(audit_index, SystemHealthPluginAuditIndex):
        raise HealthAgentInterfaceError("audit_index must be a SystemHealthPluginAuditIndex or None")
    if readiness_score is not None and not isinstance(readiness_score, SystemHealthPluginReadinessScore):
        raise HealthAgentInterfaceError("readiness_score must be a SystemHealthPluginReadinessScore or None")

    included_artifacts = []
    if audit_index is not None:
        included_artifacts.append("audit_index")
    if readiness_score is not None:
        included_artifacts.append("readiness_score")
    included_artifacts_tuple = tuple(included_artifacts)

    blocked_runtime_actions = tuple(_REQUIRED_NO_GO_ACTIONS)
    missing_no_go_actions = ()
    if audit_index is not None:
        missing_no_go_actions = tuple(
            action for action in _REQUIRED_NO_GO_ACTIONS if action not in set(audit_index.no_go_runtime_actions)
        )

    if readiness_score is not None and readiness_score.decision_state == "blocked":
        decision_state = "blocked"
    elif missing_no_go_actions:
        decision_state = "blocked"
    elif not included_artifacts_tuple:
        decision_state = "deferred"
    elif readiness_score is None or audit_index is None:
        decision_state = "deferred"
    elif readiness_score.decision_state == "ready_for_manual_review":
        decision_state = "needs_operator_input"
    elif readiness_score.decision_state == "review_required":
        decision_state = "deferred"
    else:
        decision_state = "review_ready"

    review_order = _normalize_str_tuple(_DEFAULT_REVIEW_ORDER, field_name="review_order")
    go_no_go_questions = tuple(
        OperatorReviewQuestion.create(
            question_id=f"question_{index + 1}",
            prompt=prompt,
        )
        for index, prompt in enumerate(_DEFAULT_GO_NO_GO_QUESTIONS)
    )
    operator_signoff_inputs = _normalize_str_tuple(_DEFAULT_SIGNOFF_INPUTS, field_name="operator_signoff_input")
    followup_slices = _normalize_str_tuple(_DEFAULT_FOLLOWUP_SLICES, field_name="followup_slice")

    sections = (
        OperatorReviewSection.create(
            section_id="packet_purpose",
            summary="operator review packet summarizes foundation evidence without enabling runtime execution",
            detail_count=1,
        ),
        OperatorReviewSection.create(
            section_id="included_artifacts",
            summary=(
                "audit index and readiness score are attached for operator review"
                if len(included_artifacts_tuple) == 2
                else "operator packet is waiting for the full artifact set"
            ),
            detail_count=len(included_artifacts_tuple),
        ),
        OperatorReviewSection.create(
            section_id="review_order",
            summary="operator should review artifacts and runtime boundaries in a fixed order",
            detail_count=len(review_order),
        ),
        OperatorReviewSection.create(
            section_id="go_no_go_questions",
            summary="go/no-go questions remain manual and evidence-bound",
            detail_count=len(go_no_go_questions),
        ),
        OperatorReviewSection.create(
            section_id="blocked_runtime_actions",
            summary=(
                "runtime actions remain blocked and intact for foundation review"
                if not missing_no_go_actions
                else "critical runtime no-go boundaries are incomplete and block review"
            ),
            detail_count=len(blocked_runtime_actions),
        ),
        OperatorReviewSection.create(
            section_id="operator_signoff_inputs",
            summary="operator signoff fields are required before any manual go/no-go conclusion",
            detail_count=len(operator_signoff_inputs),
        ),
        OperatorReviewSection.create(
            section_id="followup_slices",
            summary="runtime follow-up slices remain deferred beyond the operator review packet",
            detail_count=len(followup_slices),
        ),
    )

    return SystemHealthPluginOperatorReviewPacket(
        decision_state=_normalize_decision_state(decision_state),
        included_artifacts=included_artifacts_tuple,
        review_order=review_order,
        go_no_go_questions=go_no_go_questions,
        blocked_runtime_actions=blocked_runtime_actions,
        operator_signoff_inputs=operator_signoff_inputs,
        followup_slices=followup_slices,
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
    )
