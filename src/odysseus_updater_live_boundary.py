"""Offline live-boundary model for future Odysseus updater execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_DECISIONS = ("ready_for_operator_go", "hold", "no_go", "deferred")
_STATUSES = ("ready", "partial", "blocked", "deferred")
_OPERATORS = ("go", "hold", "no_go", "deferred", "missing")


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ValueError(f"unsupported {field_name}: {value!r}")
    return text


def _dedupe(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = _normalize_text(value, field_name=field_name, allow_empty=True)
        if item and item not in result:
            result.append(item)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class UpdaterLiveBoundary:
    status: str
    decision: str
    live_execution_allowed: bool
    operator_decision: str
    required_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _normalize_choice(self.status, field_name="status", choices=_STATUSES))
        object.__setattr__(self, "decision", _normalize_choice(self.decision, field_name="decision", choices=_DECISIONS))
        object.__setattr__(
            self,
            "operator_decision",
            _normalize_choice(self.operator_decision, field_name="operator_decision", choices=_OPERATORS),
        )
        object.__setattr__(self, "required_evidence", _dedupe(self.required_evidence, field_name="required_evidence"))
        object.__setattr__(self, "blockers", _dedupe(self.blockers, field_name="blocker"))
        object.__setattr__(self, "next_actions", _dedupe(self.next_actions, field_name="next_action"))
        if self.live_execution_allowed:
            raise ValueError("live_execution_allowed must stay false in the offline boundary model")
        if self.decision == "ready_for_operator_go" and self.operator_decision != "go":
            raise ValueError("ready_for_operator_go requires operator_decision='go'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decision": self.decision,
            "live_execution_allowed": self.live_execution_allowed,
            "operator_decision": self.operator_decision,
            "required_evidence": list(self.required_evidence),
            "blockers": list(self.blockers),
            "next_actions": list(self.next_actions),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Odysseus Updater Live Boundary",
            "",
            f"- Status: `{self.status}`",
            f"- Decision: `{self.decision}`",
            f"- Live execution allowed: `{str(self.live_execution_allowed).lower()}`",
            f"- Operator decision: `{self.operator_decision}`",
        ]
        if self.blockers:
            lines.extend(["", "## Blockers"])
            lines.extend(f"- {blocker}" for blocker in self.blockers)
        if self.next_actions:
            lines.extend(["", "## Next Actions"])
            lines.extend(f"- {action}" for action in self.next_actions)
        return "\n".join(lines)


def build_odysseus_updater_live_boundary(
    *,
    pre_update_snapshot_green: bool = False,
    repository_check_green: bool = False,
    restore_smoke_green: bool = False,
    focused_tests_green: bool = False,
    command_plan_reviewed: bool = False,
    operator_decision: Any = "missing",
    secret_or_private_output_risk: bool = False,
    live_command_requested: bool = False,
) -> UpdaterLiveBoundary:
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATORS,
    )
    required_evidence = (
        "pre_update_snapshot",
        "repository_check",
        "restore_smoke",
        "focused_tests",
        "command_plan_review",
        "operator_decision",
    )
    blockers: list[str] = []
    if not pre_update_snapshot_green:
        blockers.append("pre-update snapshot evidence is missing or not green")
    if not repository_check_green:
        blockers.append("repository check evidence is missing or not green")
    if not restore_smoke_green:
        blockers.append("restore smoke evidence is missing or not green")
    if not focused_tests_green:
        blockers.append("focused test evidence is missing or not green")
    if not command_plan_reviewed:
        blockers.append("command plan has not been reviewed as dry-run operator guidance")
    if normalized_operator != "go":
        blockers.append("operator decision is not go")
    if secret_or_private_output_risk:
        blockers.append("secret or private output risk blocks live boundary readiness")
    if live_command_requested:
        blockers.append("live command execution is out of scope for this offline boundary model")

    if secret_or_private_output_risk or live_command_requested:
        status = "blocked"
        decision = "no_go"
    elif blockers:
        status = "partial" if any(
            (
                pre_update_snapshot_green,
                repository_check_green,
                restore_smoke_green,
                focused_tests_green,
                command_plan_reviewed,
            )
        ) else "deferred"
        decision = "hold"
    else:
        status = "ready"
        decision = "ready_for_operator_go"

    next_actions = (
        "Run or record pre-update snapshot evidence through the operator-approved hook.",
        "Record repository check and restore smoke evidence before live deployment review.",
        "Keep this model offline; a separate operator path must execute any live update.",
    )
    if decision == "ready_for_operator_go":
        next_actions = (
            "Hand the dry-run bundle to the operator for a separate live Go/No-Go decision.",
            "Do not execute deployment from this model.",
        )

    return UpdaterLiveBoundary(
        status=status,
        decision=decision,
        live_execution_allowed=False,
        operator_decision=normalized_operator,
        required_evidence=required_evidence,
        blockers=tuple(blockers),
        next_actions=next_actions,
    )
