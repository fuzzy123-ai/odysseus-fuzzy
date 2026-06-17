"""Read-only release decision bundle model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

_GATE_ID = "release_decision_bundle"
_DECISIONS = (
    "release_go",
    "release_partial",
    "release_no_go",
    "release_deferred",
)
_DEFAULT_ACTIONS = (
    "Review remaining release evidence and keep runtime activation disabled.",
    "Confirm operator release decision only after all mandatory gates are recorded.",
    "Keep secrets, payloads, and plugin runtime out of the release evidence bundle.",
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_decision(value: str) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized not in _DECISIONS:
        raise ValueError(f"Unsupported release decision: {value!r}")
    return normalized


def _normalize_tuple(values: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return tuple(cleaned)


@dataclass(frozen=True)
class ReleaseDecisionBundle:
    gate_id: str
    decision: str
    summary: str
    next_allowed_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _normalize_text(self.gate_id) or _GATE_ID)
        object.__setattr__(self, "decision", _normalize_decision(self.decision))
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
            "summary": self.summary,
            "next_allowed_actions": list(self.next_allowed_actions),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Release Decision Bundle",
            "",
            f"- Gate ID: `{self.gate_id}`",
            f"- Decision: `{self.decision}`",
            f"- Summary: {self.summary}",
        ]
        if self.next_allowed_actions:
            lines.append("- Next Allowed Actions:")
            lines.extend(f"  - {item}" for item in self.next_allowed_actions)
        return "\n".join(lines)


def build_release_decision_bundle(
    *,
    provider_fallback_gate_recorded: bool | None = False,
    test_vault_rebuild_gate_recorded: bool | None = False,
    graph_memory_gate_recorded: bool | None = False,
    large_graph_gate_recorded: bool | None = False,
    telegram_boundary_recorded: bool | None = False,
    plugin_freeze_recorded: bool | None = False,
    known_limits_recorded: bool | None = False,
    operator_decision_recorded: bool | None = False,
    provider_gate_missing: bool | None = False,
    test_vault_gate_missing: bool | None = False,
    plugin_runtime_enabled: bool | None = False,
    secret_leak_detected: bool | None = False,
    unbounded_graph_enabled: bool | None = False,
    runtime_activation_without_go: bool | None = False,
    known_limits_missing: bool | None = False,
) -> ReleaseDecisionBundle:
    blockers = any(
        (
            provider_gate_missing,
            test_vault_gate_missing,
            plugin_runtime_enabled,
            secret_leak_detected,
            unbounded_graph_enabled,
            runtime_activation_without_go,
            known_limits_missing,
        )
    )
    required_ready = all(
        (
            provider_fallback_gate_recorded,
            test_vault_rebuild_gate_recorded,
            graph_memory_gate_recorded,
            large_graph_gate_recorded,
            telegram_boundary_recorded,
            plugin_freeze_recorded,
            known_limits_recorded,
            operator_decision_recorded,
        )
    )
    required_known = (
        provider_fallback_gate_recorded is not None
        and test_vault_rebuild_gate_recorded is not None
        and plugin_freeze_recorded is not None
        and known_limits_recorded is not None
        and operator_decision_recorded is not None
    )
    if blockers:
        return ReleaseDecisionBundle(
            gate_id=_GATE_ID,
            decision="release_no_go",
            summary=(
                "Release bundle is no-go because mandatory release evidence is missing or "
                "unsafe runtime, secret, or unbounded graph conditions were claimed."
            ),
            next_allowed_actions=(),
        )
    if required_ready:
        return ReleaseDecisionBundle(
            gate_id=_GATE_ID,
            decision="release_go",
            summary=(
                "All recorded release gates are present, known limits are captured, plugin "
                "runtime remains frozen, and the operator release decision is recorded."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    if not required_known or any(
        value is None
        for value in (
            graph_memory_gate_recorded,
            large_graph_gate_recorded,
            telegram_boundary_recorded,
        )
    ):
        return ReleaseDecisionBundle(
            gate_id=_GATE_ID,
            decision="release_deferred",
            summary=(
                "Release bundle is deferred until mandatory and supporting gate evidence is "
                "explicitly recorded without enabling runtime behavior."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    if all(
        (
            provider_fallback_gate_recorded,
            test_vault_rebuild_gate_recorded,
            plugin_freeze_recorded,
            known_limits_recorded,
            operator_decision_recorded,
        )
    ):
        return ReleaseDecisionBundle(
            gate_id=_GATE_ID,
            decision="release_partial",
            summary=(
                "Mandatory release evidence is recorded, but one or more supporting release "
                "gates still need review before a full release go can be claimed."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    return ReleaseDecisionBundle(
        gate_id=_GATE_ID,
        decision="release_deferred",
        summary=(
            "Release bundle remains deferred because mandatory release evidence has not yet "
            "been fully recorded."
        ),
        next_allowed_actions=_DEFAULT_ACTIONS,
    )
