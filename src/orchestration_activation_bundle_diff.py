"""Diff helpers for orchestration activation bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.orchestration_activation_bundle import OrchestrationActivationBundle
from src.orchestration_activation_bundle_digest import digest_activation_bundle


@dataclass(frozen=True, slots=True)
class ActivationBundleDiff:
    changed: bool
    digest_changed: bool
    status_changed: bool
    previous_status: str
    current_status: str
    new_blockers: tuple[str, ...]
    resolved_blockers: tuple[str, ...]
    next_safe_action_changed: bool
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "digest_changed": self.digest_changed,
            "status_changed": self.status_changed,
            "previous_status": self.previous_status,
            "current_status": self.current_status,
            "new_blockers": self.new_blockers,
            "resolved_blockers": self.resolved_blockers,
            "next_safe_action_changed": self.next_safe_action_changed,
            "notes": self.notes,
        }


def build_activation_bundle_diff(
    previous_bundle: OrchestrationActivationBundle,
    current_bundle: OrchestrationActivationBundle,
) -> ActivationBundleDiff:
    if not isinstance(previous_bundle, OrchestrationActivationBundle):
        raise TypeError("previous_bundle must be an OrchestrationActivationBundle")
    if not isinstance(current_bundle, OrchestrationActivationBundle):
        raise TypeError("current_bundle must be an OrchestrationActivationBundle")

    previous_digest = digest_activation_bundle(previous_bundle)
    current_digest = digest_activation_bundle(current_bundle)
    digest_changed = previous_digest != current_digest

    previous_status = previous_bundle.summary.status_label
    current_status = current_bundle.summary.status_label
    status_changed = previous_status != current_status

    previous_blockers = set(previous_bundle.summary.blocking_reasons)
    current_blockers = set(current_bundle.summary.blocking_reasons)
    new_blockers = tuple(sorted(current_blockers - previous_blockers))
    resolved_blockers = tuple(sorted(previous_blockers - current_blockers))
    next_safe_action_changed = previous_bundle.summary.next_safe_action != current_bundle.summary.next_safe_action

    notes: list[str] = []
    if digest_changed:
        notes.append("bundle_digest_changed")
    if status_changed:
        notes.append("status_changed")
    if new_blockers:
        notes.append("new_blockers_detected")
    if resolved_blockers:
        notes.append("blockers_resolved")
    if next_safe_action_changed:
        notes.append("next_safe_action_changed")

    return ActivationBundleDiff(
        changed=bool(digest_changed or status_changed or new_blockers or resolved_blockers or next_safe_action_changed),
        digest_changed=digest_changed,
        status_changed=status_changed,
        previous_status=previous_status,
        current_status=current_status,
        new_blockers=new_blockers,
        resolved_blockers=resolved_blockers,
        next_safe_action_changed=next_safe_action_changed,
        notes=tuple(notes),
    )
