"""Readiness checklist models for orchestration activation handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.orchestration_activation_audit_trail import ActivationAuditError


class HandoffChecklistStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


_ITEM_IDS = (
    "handoff_present",
    "commit_present",
    "tests_reported",
    "scope_verified",
    "worktree_clean",
    "no_hotfile_overlap",
    "no_foreign_staged_files",
    "operator_approval_required",
    "runtime_hooks_disabled",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ActivationAuditError(f"{field_name} must not be empty")
    return text


def _normalize_item_id(value: Any) -> str:
    text = _normalize_text(value, field_name="item_id").strip().lower()
    if text not in _ITEM_IDS:
        raise ActivationAuditError("unsupported handoff checklist item_id")
    return text


def _normalize_status(value: HandoffChecklistStatus | str) -> HandoffChecklistStatus:
    if isinstance(value, HandoffChecklistStatus):
        return value
    text = _normalize_text(value, field_name="status").strip().lower()
    try:
        return HandoffChecklistStatus(text)
    except ValueError as exc:
        raise ActivationAuditError("unsupported handoff checklist status") from exc


def _overall_status(items: tuple["HandoffChecklistItem", ...]) -> str:
    statuses = {item.status for item in items}
    if HandoffChecklistStatus.FAIL in statuses:
        return "blocked"
    if HandoffChecklistStatus.WARN in statuses or HandoffChecklistStatus.UNKNOWN in statuses:
        return "needs_review"
    return "ready"


def _status_from_bool(value: bool | None, *, invert: bool = False) -> HandoffChecklistStatus:
    if value is None:
        return HandoffChecklistStatus.UNKNOWN
    normalized = not value if invert else value
    return HandoffChecklistStatus.PASS if normalized else HandoffChecklistStatus.FAIL


@dataclass(frozen=True, slots=True)
class HandoffChecklistItem:
    item_id: str
    status: HandoffChecklistStatus
    summary: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        item_id: Any,
        status: HandoffChecklistStatus | str,
        summary: Any,
        next_action: Any = "",
    ) -> "HandoffChecklistItem":
        normalized_status = _normalize_status(status)
        normalized_next = _normalize_text(next_action, field_name="next_action", allow_empty=True)
        if normalized_status in {HandoffChecklistStatus.WARN, HandoffChecklistStatus.FAIL, HandoffChecklistStatus.UNKNOWN} and not normalized_next:
            normalized_next = "operator review required before activation handoff"
        return cls(
            item_id=_normalize_item_id(item_id),
            status=normalized_status,
            summary=_normalize_text(summary, field_name="summary"),
            next_action=normalized_next,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "status": self.status.value,
            "summary": self.summary,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class HandoffChecklistReport:
    mode: str
    overall_status: str
    items: tuple[HandoffChecklistItem, ...]

    @classmethod
    def create(
        cls,
        *,
        mode: Any,
        items: Iterable[HandoffChecklistItem],
    ) -> "HandoffChecklistReport":
        normalized_items = tuple(items)
        if not normalized_items:
            raise ActivationAuditError("items must not be empty")
        if any(not isinstance(item, HandoffChecklistItem) for item in normalized_items):
            raise ActivationAuditError("items must contain HandoffChecklistItem instances")
        if {item.item_id for item in normalized_items} != set(_ITEM_IDS):
            raise ActivationAuditError("items must cover the full handoff checklist")
        return cls(
            mode=_normalize_text(mode, field_name="mode"),
            overall_status=_overall_status(normalized_items),
            items=tuple(sorted(normalized_items, key=lambda item: item.item_id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "overall_status": self.overall_status,
            "items": tuple(item.to_dict() for item in self.items),
        }


def build_handoff_checklist_report(
    *,
    mode: str = "pre-runtime",
    handoff_present: bool | None = True,
    commit_present: bool | None = None,
    tests_reported: bool | None = True,
    scope_verified: bool | None = True,
    worktree_clean: bool | None = None,
    no_hotfile_overlap: bool | None = True,
    no_foreign_staged_files: bool | None = None,
    operator_approval_required: bool | None = True,
    runtime_hooks_disabled: bool | None = True,
) -> HandoffChecklistReport:
    return HandoffChecklistReport.create(
        mode=mode,
        items=(
            HandoffChecklistItem.create(
                item_id="handoff_present",
                status=_status_from_bool(handoff_present),
                summary="handoff metadata is present for operator review",
            ),
            HandoffChecklistItem.create(
                item_id="commit_present",
                status=_status_from_bool(commit_present),
                summary="a focused commit is present for the slice",
            ),
            HandoffChecklistItem.create(
                item_id="tests_reported",
                status=_status_from_bool(tests_reported),
                summary="tests are reported alongside the handoff",
            ),
            HandoffChecklistItem.create(
                item_id="scope_verified",
                status=_status_from_bool(scope_verified),
                summary="the implementation scope has been verified",
            ),
            HandoffChecklistItem.create(
                item_id="worktree_clean",
                status=_status_from_bool(worktree_clean),
                summary="the worktree is clean or explicitly accounted for",
            ),
            HandoffChecklistItem.create(
                item_id="no_hotfile_overlap",
                status=_status_from_bool(no_hotfile_overlap),
                summary="no hot-file overlap remains unresolved",
            ),
            HandoffChecklistItem.create(
                item_id="no_foreign_staged_files",
                status=_status_from_bool(no_foreign_staged_files),
                summary="no foreign staged files are mixed into the slice",
            ),
            HandoffChecklistItem.create(
                item_id="operator_approval_required",
                status=_status_from_bool(operator_approval_required),
                summary="operator approval remains an explicit gating step",
                next_action="keep operator approval mandatory before any live activation",
            ),
            HandoffChecklistItem.create(
                item_id="runtime_hooks_disabled",
                status=_status_from_bool(runtime_hooks_disabled),
                summary="runtime hooks remain disabled in pre-runtime mode",
            ),
        ),
    )


def default_handoff_checklist_report() -> HandoffChecklistReport:
    return build_handoff_checklist_report(
        mode="pre-runtime",
        commit_present=None,
        worktree_clean=None,
        no_foreign_staged_files=None,
    )
