"""Deterministic dry-run command plan renderer for the Odysseus updater slice."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PLAN_TYPES = (
    "git_fetch",
    "focused_pytest",
    "backup_preupdate",
    "podman_compose",
    "smoke_check",
    "hold_note",
)

_DEFAULT_SAFETY_NOTES = (
    "rendered for operator review only; do not execute from this model",
    "keep logs redacted and avoid persisting private host or provider output",
    "require explicit operator approval before any later runner uses this plan",
)

_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|chat[_-]?id)\b\s*[:=]\s*\S+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+\S+")
_WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s`]+")
_UNIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])/(?:[^\s/`]+/)*[^\s`]*")
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|ssh)://\S+")


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_plan_type(value: Any) -> str:
    plan_type = _normalize_text(value, field_name="plan_type").lower().replace("-", "_")
    if plan_type not in _PLAN_TYPES:
        raise ValueError(f"unsupported plan_type: {value!r}")
    return plan_type


def _redact_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = _normalize_text(value, field_name=field_name, allow_empty=allow_empty)
    if not text:
        return text
    text = _SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BEARER_PATTERN.sub("Bearer [redacted]", text)
    text = _URL_PATTERN.sub("[redacted-url]", text)
    text = _WINDOWS_PATH_PATTERN.sub("[redacted-path]", text)
    text = _UNIX_PATH_PATTERN.sub("[redacted-path]", text)
    return text


def _normalize_optional_redacted(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    text = _redact_text(value, field_name=field_name, allow_empty=True)
    return text or None


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    for item in items:
        normalized = _normalize_text(item, field_name="plan_item", allow_empty=True)
        if not normalized:
            continue
        if normalized not in unique:
            unique.append(normalized)
    return tuple(unique)


@dataclass(frozen=True, slots=True)
class UpdaterCommandPlan:
    plan_type: str
    title: str
    summary: str
    dry_run_label: str
    commands: tuple[str, ...]
    notes: tuple[str, ...]
    safety_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_type": self.plan_type,
            "title": self.title,
            "summary": self.summary,
            "dry_run_label": self.dry_run_label,
            "commands": list(self.commands),
            "notes": list(self.notes),
            "safety_notes": list(self.safety_notes),
        }

    def to_text(self) -> str:
        lines = [
            f"# {self.title}",
            "",
            f"- Plan Type: `{self.plan_type}`",
            f"- Mode: `{self.dry_run_label}`",
            f"- Summary: {self.summary}",
        ]
        if self.commands:
            lines.extend(["", "## Planned Commands"])
            for index, command in enumerate(self.commands, start=1):
                lines.append(f"{index}. `{command}`")
        if self.notes:
            lines.extend(["", "## Operator Notes"])
            for note in self.notes:
                lines.append(f"- {note}")
        lines.extend(["", "## Safety Notes"])
        for note in self.safety_notes:
            lines.append(f"- {note}")
        return "\n".join(lines).rstrip()


def _build_template(
    plan_type: str,
    *,
    focus_label: str,
    note: str | None,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    if plan_type == "git_fetch":
        return (
            "Odysseus Updater Git Fetch Plan",
            "review deterministic fetch guidance for an operator-run ref refresh",
            (
                "git fetch --all --tags --prune",
                f"git fetch origin {focus_label}",
            ),
            _dedupe(
                (
                    "review the target ref before any later fetch execution",
                    note or "",
                )
            ),
        )
    if plan_type == "focused_pytest":
        return (
            "Odysseus Updater Focused Pytest Plan",
            "review the narrow offline pytest command for the updater slice",
            (
                f"python -m pytest {focus_label} -q",
            ),
            _dedupe(
                (
                    "keep the test target focused on the approved updater slice only",
                    note or "",
                )
            ),
        )
    if plan_type == "backup_preupdate":
        return (
            "Odysseus Updater Backup Plan",
            "review pre-update backup guidance without disclosing private paths or destinations",
            (
                f"backup-tool create --label preupdate --source {focus_label} --destination [redacted-backup-target]",
                "backup-tool verify --latest --summary-only",
            ),
            _dedupe(
                (
                    "confirm backup media and retention out of band before any live run",
                    note or "",
                )
            ),
        )
    if plan_type == "podman_compose":
        return (
            "Odysseus Updater Podman Compose Plan",
            "review Podman compose style steps as operator guidance only",
            (
                f"podman compose -f {focus_label} config",
                f"podman compose -f {focus_label} up --detach --no-build",
            ),
            _dedupe(
                (
                    "prefer config review before any later container lifecycle action",
                    note or "",
                )
            ),
        )
    if plan_type == "smoke_check":
        return (
            "Odysseus Updater Smoke Check Plan",
            "review a minimal smoke-check command set without executing any checks",
            (
                f"python -m pytest {focus_label} -q -k smoke",
            ),
            _dedupe(
                (
                    "keep smoke coverage small and audit-friendly",
                    note or "",
                )
            ),
        )
    return (
        "Odysseus Updater Hold Note",
        "record a hold state for manual review without any execution step",
        (),
        _dedupe(
            (
                "hold this slice until operator review clears the next action",
                note or "",
            )
        ),
    )


def build_odysseus_updater_command_plan(
    *,
    plan_type: Any,
    focus_label: Any | None = None,
    note: Any | None = None,
) -> UpdaterCommandPlan:
    normalized_plan_type = _normalize_plan_type(plan_type)

    default_focus = {
        "git_fetch": "<reviewed-ref>",
        "focused_pytest": "tests/test_odysseus_updater_command_plan.py",
        "backup_preupdate": "<reviewed-worktree>",
        "podman_compose": "<redacted-compose-file>",
        "smoke_check": "tests/test_odysseus_updater_command_plan.py",
        "hold_note": "<operator-hold>",
    }[normalized_plan_type]

    normalized_focus = _normalize_optional_redacted(focus_label, field_name="focus_label") or default_focus
    normalized_note = _normalize_optional_redacted(note, field_name="note")
    title, summary, commands, notes = _build_template(
        normalized_plan_type,
        focus_label=normalized_focus,
        note=normalized_note,
    )

    return UpdaterCommandPlan(
        plan_type=normalized_plan_type,
        title=title,
        summary=summary,
        dry_run_label="plan_only",
        commands=commands,
        notes=tuple(note_item for note_item in notes if note_item),
        safety_notes=_DEFAULT_SAFETY_NOTES,
    )
