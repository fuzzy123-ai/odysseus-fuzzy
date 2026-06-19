"""Offline pre-update hook gate for the Odysseus updater."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.odysseus_updater_backup_gate import BackupGateReport

_STATUSES = ("ready", "partial", "blocked", "deferred")
_UPDATE_DECISIONS = ("continue_review", "block_update", "defer_update_review")
_EXPECTED_HOOK_PATH = "ops/homeserver/pre-update-snapshot.sh"


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a bool")


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ValueError(f"unsupported {field_name}: {value!r}")
    return text


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = _normalize_text(value, field_name="gate_item", allow_empty=True)
        if text and text not in result:
            result.append(text)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PreUpdateHookGate:
    hook_path: str
    status: str
    update_decision: str
    may_continue_update: bool
    live_execution_allowed: bool
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]
    backup_gate_status: str
    backup_gate_decision: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "hook_path", _normalize_text(self.hook_path, field_name="hook_path"))
        object.__setattr__(self, "status", _normalize_choice(self.status, field_name="status", choices=_STATUSES))
        object.__setattr__(
            self,
            "update_decision",
            _normalize_choice(self.update_decision, field_name="update_decision", choices=_UPDATE_DECISIONS),
        )
        object.__setattr__(self, "may_continue_update", _normalize_bool(self.may_continue_update, field_name="may_continue_update"))
        object.__setattr__(
            self,
            "live_execution_allowed",
            _normalize_bool(self.live_execution_allowed, field_name="live_execution_allowed"),
        )
        object.__setattr__(self, "blockers", _dedupe(self.blockers))
        object.__setattr__(self, "next_actions", _dedupe(self.next_actions))
        object.__setattr__(
            self,
            "backup_gate_status",
            _normalize_choice(self.backup_gate_status, field_name="backup_gate_status", choices=_STATUSES),
        )
        if self.live_execution_allowed:
            raise ValueError("live_execution_allowed must stay false for the offline pre-update hook gate")
        if self.may_continue_update and self.update_decision != "continue_review":
            raise ValueError("may_continue_update requires update_decision='continue_review'")
        if self.update_decision == "continue_review" and not self.may_continue_update:
            raise ValueError("continue_review requires may_continue_update=True")

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook_path": self.hook_path,
            "status": self.status,
            "update_decision": self.update_decision,
            "may_continue_update": self.may_continue_update,
            "live_execution_allowed": self.live_execution_allowed,
            "blockers": list(self.blockers),
            "next_actions": list(self.next_actions),
            "backup_gate_status": self.backup_gate_status,
            "backup_gate_decision": self.backup_gate_decision,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Odysseus Pre-Update Hook Gate",
            "",
            f"- Hook Path: `{self.hook_path}`",
            f"- Status: `{self.status}`",
            f"- Update Decision: `{self.update_decision}`",
            f"- May Continue Update: `{str(self.may_continue_update).lower()}`",
            f"- Live Execution Allowed: `{str(self.live_execution_allowed).lower()}`",
            f"- Backup Gate: `{self.backup_gate_status}` / `{self.backup_gate_decision}`",
        ]
        if self.blockers:
            lines.extend(["", "## Blockers"])
            lines.extend(f"- {item}" for item in self.blockers)
        if self.next_actions:
            lines.extend(["", "## Next Actions"])
            lines.extend(f"- {item}" for item in self.next_actions)
        return "\n".join(lines)


def build_pre_update_hook_gate(
    *,
    backup_gate: BackupGateReport,
    hook_path: Any = _EXPECTED_HOOK_PATH,
    hook_script_reviewed: bool = False,
    command_plan_reviewed: bool = False,
    live_execution_requested: bool = False,
) -> PreUpdateHookGate:
    normalized_hook_path = _normalize_text(hook_path, field_name="hook_path")
    blockers: list[str] = []
    next_actions: list[str] = []

    if normalized_hook_path != _EXPECTED_HOOK_PATH:
        blockers.append("pre-update hook path does not match the reviewed homeserver backup interface")
        next_actions.append("Use ops/homeserver/pre-update-snapshot.sh as the reviewed hook interface.")
    if not hook_script_reviewed:
        blockers.append("pre-update hook script has not been reviewed for this update packet")
        next_actions.append("Review the hook script path and expected non-zero blocking behavior before updater review.")
    if not command_plan_reviewed:
        blockers.append("pre-update hook command plan has not been reviewed")
        next_actions.append("Attach a redacted plan-only command record for the pre-update hook.")
    if backup_gate.deployment_decision != "go":
        blockers.append("backup evidence is not green; update must not proceed")
        next_actions.extend(backup_gate.next_actions)
    if live_execution_requested:
        blockers.append("live hook execution is out of scope for this offline gate")
        next_actions.append("Execute the hook only from a separate operator-approved runtime path.")

    if live_execution_requested or normalized_hook_path != _EXPECTED_HOOK_PATH:
        status = "blocked"
        update_decision = "block_update"
    elif blockers:
        status = backup_gate.status if backup_gate.status in {"partial", "blocked", "deferred"} else "deferred"
        update_decision = "block_update"
    else:
        status = "ready"
        update_decision = "continue_review"
        next_actions.append("Proceed to the next offline updater review gate; do not execute deployment from this model.")

    return PreUpdateHookGate(
        hook_path=normalized_hook_path,
        status=status,
        update_decision=update_decision,
        may_continue_update=update_decision == "continue_review",
        live_execution_allowed=False,
        blockers=tuple(blockers),
        next_actions=tuple(next_actions),
        backup_gate_status=backup_gate.status,
        backup_gate_decision=backup_gate.deployment_decision,
    )
