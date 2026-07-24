"""Read-only dry-run planning model for quality-gate command review."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.claim_evidence_gate import (
    AgentMaintenanceCompletionEvidence,
    evaluate_agent_maintenance_completion,
)

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

_PLAN_READY_CLASSES = {"focused_pytest", "read_only_git_status", "evidence_check"}
_UNSAFE_COMMAND_TEXT_RE = re.compile(r"[\x00-\x1f\x7f;&|><`]|\$\(")
_SAFE_TEST_TARGET_RE = re.compile(
    r"^tests/(?:[A-Za-z0-9_.-]+/)*test_[A-Za-z0-9_.-]+\.py(?:::[A-Za-z0-9_\[\].-]+)*$"
)
_SAFE_PYTEST_FLAGS = {"-q", "-x", "--disable-warnings"}
_SAFE_PYTEST_VALUE_FLAGS = {"--tb": {"short", "line", "native", "no"}}
_MAX_TIMEOUT_SECONDS = 300
_ALLOWED_REDACTED_LOG_POLICIES = {"command-only-no-secrets"}


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


def _parse_quality_gate_argv(command_text: str) -> tuple[str, ...] | None:
    if _UNSAFE_COMMAND_TEXT_RE.search(command_text):
        return None
    try:
        argv = tuple(shlex.split(command_text, posix=True))
    except ValueError:
        return None
    if not argv or any(not token or "\n" in token or "\r" in token for token in argv):
        return None
    return argv


def _focused_pytest_argv_is_allowed(argv: tuple[str, ...]) -> bool:
    if len(argv) < 4 or argv[:3] != ("python", "-m", "pytest"):
        return False
    saw_target = False
    for token in argv[3:]:
        normalized = token.replace("\\", "/")
        if _SAFE_TEST_TARGET_RE.fullmatch(normalized):
            saw_target = True
            continue
        if token in _SAFE_PYTEST_FLAGS:
            continue
        if token.startswith("--maxfail=") and token.removeprefix("--maxfail=").isdigit():
            continue
        if any(token == f"{flag}={value}" for flag, values in _SAFE_PYTEST_VALUE_FLAGS.items() for value in values):
            continue
        return False
    return saw_target


def quality_gate_command_is_allowed(command_class: Any, command_text: Any) -> bool:
    """Return whether a command is an exact, shell-free argv form for its class."""

    try:
        normalized_class = _normalize_command_class(command_class)
        normalized_text = _normalize_text(command_text, field_name="command_text")
    except (TypeError, ValueError):
        return False
    argv = _parse_quality_gate_argv(normalized_text)
    if argv is None:
        return False
    if normalized_class == "focused_pytest":
        return _focused_pytest_argv_is_allowed(argv)
    if normalized_class == "read_only_git_status":
        return argv == ("git", "status", "--short", "--branch")
    if normalized_class == "evidence_check":
        return argv in {
            ("git", "diff", "--check"),
            ("git", "diff", "--cached", "--check"),
            ("git", "rev-parse", "--verify", "HEAD^{commit}"),
        }
    return False


def quality_gate_command_text_is_allowed(command_text: Any) -> bool:
    return any(
        quality_gate_command_is_allowed(command_class, command_text)
        for command_class in _PLAN_READY_CLASSES
    )


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
        if type(timeout_seconds) is not int:
            raise ValueError("timeout_seconds must be an integer")
        normalized_timeout = timeout_seconds
        if normalized_timeout <= 0 or normalized_timeout > _MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 1 and {_MAX_TIMEOUT_SECONDS}")
        normalized_log_policy = _normalize_text(
            redacted_log_policy,
            field_name="redacted_log_policy",
        )
        if normalized_log_policy not in _ALLOWED_REDACTED_LOG_POLICIES:
            raise ValueError("unsupported redacted_log_policy")
        return cls(
            command_class=_normalize_command_class(command_class),
            command_text=_normalize_text(command_text, field_name="command_text"),
            timeout_seconds=normalized_timeout,
            redacted_log_policy=normalized_log_policy,
            operator_approval_required=(
                operator_approval_required if type(operator_approval_required) is bool else False
            ),
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


@dataclass(frozen=True, slots=True)
class LiveQualityGateExecutionAuthority:
    action: str
    plan_digest: str
    granted: bool


@dataclass(frozen=True, slots=True)
class LiveQualityGateExecutionDecision:
    allowed: bool
    completion_verified: bool
    action_authorized: bool
    operator_go: bool
    live_enabled: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.live_quality_gate_execution.v1",
            "allowed": self.allowed,
            "completion_verified": self.completion_verified,
            "action_authorized": self.action_authorized,
            "operator_go": self.operator_go,
            "live_enabled": self.live_enabled,
            "blockers": list(self.blockers),
        }


def build_live_quality_gate_execution_authority(
    plan: LiveQualityGateCommandPlan,
    *,
    granted: bool,
) -> LiveQualityGateExecutionAuthority:
    if type(granted) is not bool:
        raise ValueError("granted must be a boolean")
    if not _live_quality_gate_plan_is_valid(plan):
        raise ValueError("quality gate command plan is not canonical or builder-valid")
    return LiveQualityGateExecutionAuthority(
        action="quality_gate_command",
        plan_digest=_live_quality_gate_plan_digest(plan),
        granted=granted,
    )


def evaluate_live_quality_gate_execution(
    plan: LiveQualityGateCommandPlan,
    *,
    repo_root: str | Path,
    completion_evidence: AgentMaintenanceCompletionEvidence | None,
    authority: LiveQualityGateExecutionAuthority | None,
    operator_go: bool,
    live_enabled: bool,
) -> LiveQualityGateExecutionDecision:
    if not isinstance(plan, LiveQualityGateCommandPlan):
        raise ValueError("plan must be a LiveQualityGateCommandPlan")
    plan_valid = _live_quality_gate_plan_is_valid(plan)
    completion_verified = evaluate_agent_maintenance_completion(
        completion_evidence,
        repo_root=Path(repo_root).resolve(),
    ).completed
    action_authorized = (
        isinstance(authority, LiveQualityGateExecutionAuthority)
        and authority.action == "quality_gate_command"
        and plan_valid
        and authority.plan_digest == _live_quality_gate_plan_digest(plan)
        and authority.granted is True
    )
    blockers: list[str] = []
    if not plan_valid:
        blockers.append("quality gate command plan is not canonical or builder-valid")
    if plan.decision.decision != "plan_ready":
        blockers.append("quality gate command plan is not ready")
    if not completion_verified:
        blockers.append("current claims and machine verification receipt are required")
    if not action_authorized:
        blockers.append("typed quality gate command authority is required")
    if type(operator_go) is not bool:
        blockers.append("operator_go must be a boolean")
    elif not operator_go:
        blockers.append("operator_go=true is required")
    if type(live_enabled) is not bool:
        blockers.append("live_enabled must be a boolean")
    elif not live_enabled:
        blockers.append("live_enabled=true is required")
    unique = tuple(dict.fromkeys(blockers))
    return LiveQualityGateExecutionDecision(
        allowed=not unique,
        completion_verified=completion_verified,
        action_authorized=action_authorized,
        operator_go=operator_go if type(operator_go) is bool else False,
        live_enabled=live_enabled if type(live_enabled) is bool else False,
        blockers=unique,
    )


def _live_quality_gate_plan_digest(plan: LiveQualityGateCommandPlan) -> str:
    encoded = json.dumps(
        plan.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _live_quality_gate_plan_is_valid(plan: Any) -> bool:
    if not isinstance(plan, LiveQualityGateCommandPlan):
        return False
    try:
        rebuilt = build_live_quality_gate_command_plan(
            command_class=plan.command.command_class,
            command_text=plan.command.command_text,
            timeout_seconds=plan.command.timeout_seconds,
            redacted_log_policy=plan.command.redacted_log_policy,
            operator_approval_required=plan.command.operator_approval_required,
        )
        return rebuilt.to_dict() == plan.to_dict()
    except (AttributeError, TypeError, ValueError):
        return False


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
    allowed_argv = quality_gate_command_is_allowed(command.command_class, command.command_text)
    class_blocked = command.command_class in {"blocked_destructive", "blocked_host_command", "blocked_network"}
    placeholder_command = "tbd" in lowered_text

    if class_blocked or (not placeholder_command and not allowed_argv):
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
