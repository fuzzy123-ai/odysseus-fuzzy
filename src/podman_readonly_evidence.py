"""Read-only Podman evidence command planning for the MCP workbench lane."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class PodmanReadOnlyEvidenceError(ValueError):
    """Raised when a Podman evidence plan would be unsafe or ambiguous."""


_SAFE_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_ALLOWED_ACTIONS = {"ps", "logs", "inspect", "port", "healthcheck"}


@dataclass(frozen=True, slots=True)
class PodmanEvidenceCommand:
    command_id: str
    action: str
    argv: tuple[str, ...]
    redacts_output: bool
    max_output_chars: int
    mutation_allowed: bool = False

    @property
    def command_text(self) -> str:
        return " ".join(self.argv)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "action": self.action,
            "argv": self.argv,
            "command_text": self.command_text,
            "redacts_output": self.redacts_output,
            "max_output_chars": self.max_output_chars,
            "mutation_allowed": self.mutation_allowed,
        }


@dataclass(frozen=True, slots=True)
class PodmanEvidencePlan:
    schema: str
    runtime: str
    status: str
    commands: tuple[PodmanEvidenceCommand, ...]
    forbidden_actions: tuple[str, ...]
    live_execution_required: bool
    operator_go_required: bool
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "runtime": self.runtime,
            "status": self.status,
            "commands": tuple(command.to_dict() for command in self.commands),
            "forbidden_actions": self.forbidden_actions,
            "live_execution_required": self.live_execution_required,
            "operator_go_required": self.operator_go_required,
            "notes": self.notes,
        }


def build_podman_readonly_evidence_plan(
    *,
    actions: Iterable[str] | None = None,
    targets: Iterable[str] | None = None,
    tail: int = 120,
    max_output_chars: int = 12000,
) -> PodmanEvidencePlan:
    """Build a bounded Podman read-only evidence plan without executing it."""

    normalized_actions = _normalize_actions(actions)
    normalized_targets = _normalize_targets(targets)
    normalized_tail = _normalize_int(tail, field_name="tail", minimum=1, maximum=500)
    normalized_max_output = _normalize_int(max_output_chars, field_name="max_output_chars", minimum=1000, maximum=50000)

    commands: list[PodmanEvidenceCommand] = []
    for action in normalized_actions:
        if action == "ps":
            commands.append(
                _command(
                    command_id="podman-ps",
                    action=action,
                    argv=("podman", "ps", "--format", "json"),
                    max_output_chars=normalized_max_output,
                )
            )
            continue
        if not normalized_targets:
            raise PodmanReadOnlyEvidenceError(f"{action} requires at least one target")
        for target in normalized_targets:
            if action == "logs":
                argv = ("podman", "logs", "--tail", str(normalized_tail), target)
            elif action == "inspect":
                argv = ("podman", "inspect", "--format", "json", target)
            elif action == "port":
                argv = ("podman", "port", target)
            elif action == "healthcheck":
                argv = ("podman", "inspect", "--format", "{{json .State.Health}}", target)
            else:
                raise PodmanReadOnlyEvidenceError(f"unsupported action: {action}")
            commands.append(
                _command(
                    command_id=f"podman-{action}-{_target_id(target)}",
                    action=action,
                    argv=argv,
                    max_output_chars=normalized_max_output,
                )
            )

    _assert_no_mutating_commands(commands)
    return PodmanEvidencePlan(
        schema="odysseus.podman_readonly_evidence_plan.v1",
        runtime="podman",
        status="planned",
        commands=tuple(commands),
        forbidden_actions=(
            "exec",
            "run",
            "start",
            "stop",
            "restart",
            "rm",
            "rmi",
            "kill",
            "compose up",
            "compose down",
            "image prune",
            "system prune",
        ),
        live_execution_required=True,
        operator_go_required=True,
        notes=(
            "Plan only; this module never invokes podman.",
            "Use bounded tails and redacted output when a live host probe is approved.",
            "Docker MCP is intentionally not modeled for this Podman/pods deployment.",
        ),
    )


def summarize_podman_evidence_plan(plan: PodmanEvidencePlan | Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact redacted summary suitable for roadmap evidence."""

    payload = plan.to_dict() if isinstance(plan, PodmanEvidencePlan) else dict(plan)
    commands = payload.get("commands") if isinstance(payload.get("commands"), (list, tuple)) else ()
    actions = []
    for command in commands:
        if isinstance(command, Mapping):
            action = str(command.get("action") or "").strip()
            if action and action not in actions:
                actions.append(action)
    return {
        "runtime": str(payload.get("runtime") or "podman"),
        "status": str(payload.get("status") or "planned"),
        "command_count": len(commands),
        "actions": tuple(actions),
        "mutation_allowed": False,
        "operator_go_required": bool(payload.get("operator_go_required", True)),
        "live_execution_required": bool(payload.get("live_execution_required", True)),
    }


def _command(*, command_id: str, action: str, argv: tuple[str, ...], max_output_chars: int) -> PodmanEvidenceCommand:
    return PodmanEvidenceCommand(
        command_id=command_id,
        action=action,
        argv=argv,
        redacts_output=True,
        max_output_chars=max_output_chars,
    )


def _normalize_actions(actions: Iterable[str] | None) -> tuple[str, ...]:
    raw_actions = tuple(str(action or "").strip().lower().replace("_", "-") for action in (actions or ("ps", "logs", "inspect", "port")))
    if not raw_actions:
        raise PodmanReadOnlyEvidenceError("at least one action is required")
    normalized = []
    aliases = {"ports": "port", "health": "healthcheck", "health-check": "healthcheck"}
    for action in raw_actions:
        action = aliases.get(action, action)
        if action not in _ALLOWED_ACTIONS:
            raise PodmanReadOnlyEvidenceError(f"unsupported action: {action}")
        if action not in normalized:
            normalized.append(action)
    return tuple(normalized)


def _normalize_targets(targets: Iterable[str] | None) -> tuple[str, ...]:
    normalized = []
    for target in targets or ():
        value = " ".join(str(target or "").split())
        if not value:
            continue
        if not _SAFE_TARGET_RE.match(value):
            raise PodmanReadOnlyEvidenceError("target contains unsupported characters")
        if value.lower() in {"docker", "systemctl", "cloudflared"}:
            raise PodmanReadOnlyEvidenceError("target name is reserved")
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _normalize_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PodmanReadOnlyEvidenceError(f"{field_name} must be an integer") from exc
    if number < minimum or number > maximum:
        raise PodmanReadOnlyEvidenceError(f"{field_name} must be between {minimum} and {maximum}")
    return number


def _target_id(target: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", target).strip("-").lower()[:48] or "target"


def _assert_no_mutating_commands(commands: Iterable[PodmanEvidenceCommand]) -> None:
    forbidden = {"exec", "run", "start", "stop", "restart", "rm", "rmi", "kill", "prune", "up", "down"}
    for command in commands:
        argv_tail = set(command.argv[1:])
        if argv_tail & forbidden:
            raise PodmanReadOnlyEvidenceError(f"mutating podman command is forbidden: {command.command_id}")
