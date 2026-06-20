"""Read-only dry-run planning model for quality-gate command review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_COMMAND_CLASSES = (
    "focused_pytest",
    "read_only_git_status",
    "evidence_check",
    "blocked_destructive",
    "blocked_host_command",
    "blocked_network",
)

_DECISION_VALUES = (
    "plan_ready",
    "needs_operator_approval",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review the command class and scope manually",
    "confirm timeout and redacted logging policy before any operator-run",
    "keep execution in dry-run review until explicit operator approval is recorded",
)

_BLOCKED_LIVE_ACTIONS = (
    "command_execution",
    "raw_stdout_capture",
    "raw_stderr_capture",
    "secret_env_capture",
    "destructive_git_action",
    "network_action",
)

_BLOCKED_PATTERNS = (
    "git reset --hard",
    "git clean -fd",
    "rm -rf",
    "remove-item -recurse",
    "docker run --privileged",
    "podman run --privileged",
    "curl ",
    "wget ",
    "invoke-webrequest",
    "shutdown",
    "reboot",
    "systemctl",
)

_PLAN_READY_CLASSES = {"focused_pytest", "read_only_git_status", "evidence_check"}


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_command_class(value: Any) -> str:
    text = _normalize_text(value, field_name="command_class").strip().lower()
    if text not in _COMMAND_CLASSES:
        raise ValueError("unsupported quality gate command class")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported quality gate command decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class QualityGateCommand:
    command_class: str
    command_text: str
    timeout_seconds: int
    redacted_log_policy: str
    operator_approval_required: bool

    @classmethod
    def create(
        cls,
        *,
        command_class: Any,
        command_text: Any,
        timeout_seconds: Any,
        redacted_log_policy: Any,
        operator_approval_required: bool,
    ) -> "QualityGateCommand":
        normalized_timeout = int(timeout_seconds)
        if normalized_timeout <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return cls(
            command_class=_normalize_command_class(command_class),
            command_text=_normalize_text(command_text, field_name="command_text"),
            timeout_seconds=normalized_timeout,
            redacted_log_policy=_normalize_text(redacted_log_policy, field_name="redacted_log_policy"),
            operator_approval_required=bool(operator_approval_required),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_class": self.command_class,
            "command_text": self.command_text,
            "timeout_seconds": self.timeout_seconds,
            "redacted_log_policy": self.redacted_log_policy,
            "operator_approval_required": self.operator_approval_required,
        }


@dataclass(frozen=True, slots=True)
class QualityGateCommandDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LiveQualityGateCommandPlan:
    command: QualityGateCommand
    decision: QualityGateCommandDecision
    next_allowed_actions: tuple[str, ...]
    blocked_live_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command.to_dict(),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
            "blocked_live_actions": self.blocked_live_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Quality Gate Command Runner Dry Run",
            "",
            f"- Command class: `{self.command.command_class}`",
            f"- Decision: `{self.decision.decision}`",
            f"- Next action: {self.decision.next_action}",
            f"- Operator approval required: `{str(self.command.operator_approval_required).lower()}`",
            "",
            "## Command",
            f"- `{self.command.command_text}`",
        ]
        if self.next_allowed_actions:
            lines.extend(["", "## Next Allowed Actions"])
            for action in self.next_allowed_actions:
                lines.append(f"- {action}")
        lines.extend(["", "## Blocked Live Actions"])
        for action in self.blocked_live_actions:
            lines.append(f"- {action}")
        return "\n".join(lines).rstrip()


def build_live_quality_gate_command_plan(
    *,
    command_class: str = "focused_pytest",
    command_text: str = "TBD operator-approved dry-run command",
    timeout_seconds: int = 60,
    redacted_log_policy: str = "command-only-no-secrets",
    operator_approval_required: bool = True,
) -> LiveQualityGateCommandPlan:
    command = QualityGateCommand.create(
        command_class=command_class,
        command_text=command_text,
        timeout_seconds=timeout_seconds,
        redacted_log_policy=redacted_log_policy,
        operator_approval_required=operator_approval_required,
    )

    lowered_text = command.command_text.lower()
    blocked_by_pattern = any(pattern in lowered_text for pattern in _BLOCKED_PATTERNS)
    class_blocked = command.command_class in {"blocked_destructive", "blocked_host_command", "blocked_network"}
    placeholder_command = "tbd" in lowered_text

    if blocked_by_pattern or class_blocked:
        decision_value = "blocked"
        next_action = "reject this command plan and replace it with a read-only dry-run review candidate"
    elif not command.operator_approval_required:
        decision_value = "blocked"
        next_action = "restore explicit operator approval before any quality-gate command can be reviewed"
    elif placeholder_command:
        decision_value = "needs_operator_approval"
        next_action = "replace the placeholder command with an explicitly reviewed read-only dry-run candidate"
    elif command.command_class in _PLAN_READY_CLASSES and command.redacted_log_policy:
        decision_value = "plan_ready"
        next_action = "present this command plan for manual operator approval without executing it"
    elif command.command_class in _PLAN_READY_CLASSES:
        decision_value = "deferred"
        next_action = "add a redacted log policy before operator review"
    else:
        decision_value = "needs_operator_approval"
        next_action = "narrow this command plan to an explicitly allowed read-only quality-gate class"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveQualityGateCommandPlan(
        command=command,
        decision=QualityGateCommandDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
        blocked_live_actions=_normalize_tuple(_BLOCKED_LIVE_ACTIONS, field_name="blocked_live_action"),
    )
