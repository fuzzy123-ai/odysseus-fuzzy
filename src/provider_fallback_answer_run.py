"""Read-only validator for provider/fallback answer run evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

_GATE_ID = "provider_fallback_answer_run"
_DECISIONS = (
    "provider_answer_run_ready",
    "needs_provider_evidence",
    "blocked",
    "deferred",
)
_STATUSES = ("go", "needs_provider_evidence", "blocked", "deferred")
_DEFAULT_ACTIONS = (
    "Record redacted query-index, default-model, and fallback-model evidence only.",
    "Review fallback behavior and known limits without provider or network execution.",
    "Do not treat this validator output as external 1.0 go evidence or release approval.",
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_decision(value: str) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized not in _DECISIONS:
        raise ValueError(f"Unsupported provider fallback decision: {value!r}")
    return normalized


def _normalize_status(value: str) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized not in _STATUSES:
        raise ValueError(f"Unsupported provider fallback status: {value!r}")
    return normalized


def _normalize_tuple(values: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return tuple(cleaned)


@dataclass(frozen=True)
class ProviderFallbackAnswerRun:
    gate_id: str
    decision: str
    status: str
    summary: str
    next_allowed_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _normalize_text(self.gate_id) or _GATE_ID)
        object.__setattr__(self, "decision", _normalize_decision(self.decision))
        object.__setattr__(self, "status", _normalize_status(self.status))
        object.__setattr__(self, "summary", _normalize_text(self.summary))
        object.__setattr__(
            self,
            "next_allowed_actions",
            _normalize_tuple(self.next_allowed_actions),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "decision": self.decision,
            "status": self.status,
            "summary": self.summary,
            "next_allowed_actions": list(self.next_allowed_actions),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Provider Fallback Answer Run",
            "",
            f"- Gate ID: `{self.gate_id}`",
            f"- Decision: `{self.decision}`",
            f"- Status: `{self.status}`",
            f"- Summary: {self.summary}",
        ]
        if self.next_allowed_actions:
            lines.append("- Next Allowed Actions:")
            lines.extend(f"  - {item}" for item in self.next_allowed_actions)
        return "\n".join(lines)


def build_provider_fallback_answer_run(
    *,
    ready_query_index_recorded: bool | None = False,
    default_model_recorded: bool | None = False,
    fallback_model_recorded: bool | None = False,
    answer_prompt_recorded: bool | None = False,
    answer_result_recorded_redacted: bool | None = False,
    fallback_behavior_explained: bool | None = False,
    known_limits_reviewed: bool | None = False,
    operator_confirmation_recorded: bool | None = False,
    provider_secret_persisted: bool | None = False,
    provider_secret_logged: bool | None = False,
    raw_provider_payload_persisted: bool | None = False,
    missing_ready_query_index: bool | None = False,
    fallback_behavior_unknown: bool | None = False,
    network_run_without_go: bool | None = False,
    plugin_scope_touched: bool | None = False,
    unsafe_evidence_logging_enabled: bool | None = False,
) -> ProviderFallbackAnswerRun:
    blocked_claimed = any(
        (
            provider_secret_persisted,
            provider_secret_logged,
            raw_provider_payload_persisted,
            missing_ready_query_index,
            fallback_behavior_unknown,
            network_run_without_go,
            plugin_scope_touched,
            unsafe_evidence_logging_enabled,
        )
    )
    all_positive_gates = all(
        (
            ready_query_index_recorded,
            default_model_recorded,
            fallback_model_recorded,
            answer_prompt_recorded,
            answer_result_recorded_redacted,
            fallback_behavior_explained,
            known_limits_reviewed,
            operator_confirmation_recorded,
        )
    )
    if blocked_claimed:
        return ProviderFallbackAnswerRun(
            gate_id=_GATE_ID,
            decision="blocked",
            status="blocked",
            summary=(
                "Provider fallback answer evidence is blocked because secret handling, "
                "raw payload persistence, missing query-index readiness, unknown fallback "
                "behavior, unauthorized network execution, plugin scope, or unsafe logging "
                "was claimed."
            ),
            next_allowed_actions=(),
        )
    if all_positive_gates:
        return ProviderFallbackAnswerRun(
            gate_id=_GATE_ID,
            decision="provider_answer_run_ready",
            status="go",
            summary=(
                "Redacted provider/fallback answer evidence is recorded with query-index "
                "readiness, default and fallback model coverage, explained fallback behavior, "
                "known-limits review, and operator confirmation. This validator-only result "
                "does not authorize external 1.0 provider or network execution."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    if any(
        value is None
        for value in (
            ready_query_index_recorded,
            default_model_recorded,
            fallback_model_recorded,
            answer_prompt_recorded,
            answer_result_recorded_redacted,
            fallback_behavior_explained,
            known_limits_reviewed,
            operator_confirmation_recorded,
        )
    ):
        return ProviderFallbackAnswerRun(
            gate_id=_GATE_ID,
            decision="deferred",
            status="deferred",
            summary=(
                "Provider fallback answer evidence is deferred until redacted query-index, "
                "model, answer, fallback, limits, and operator inputs are explicitly recorded."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    return ProviderFallbackAnswerRun(
        gate_id=_GATE_ID,
        decision="needs_provider_evidence",
        status="needs_provider_evidence",
        summary=(
            "Provider fallback answer run still needs redacted query-index, model, prompt, "
            "answer, fallback explanation, known-limits, and operator confirmation evidence."
        ),
        next_allowed_actions=_DEFAULT_ACTIONS,
    )
