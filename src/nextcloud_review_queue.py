"""Offline review queue packets for Nextcloud private-source intake."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.nextcloud_intake_ledger import NextcloudIntakeLedgerEntry


REVIEW_STATUSES = {"needs_review", "failed", "permission_denied"}
ALLOWED_REVIEW_ACTIONS = (
    "review",
    "copy_to_review",
    "request_metadata",
    "defer",
    "skip",
)


@dataclass(frozen=True)
class NextcloudReviewQueueItem:
    """Metadata-only operator packet for one intake ledger entry."""

    digest: str
    path: str
    status: str
    reasons: tuple[str, ...]
    suggested_actions: tuple[str, ...]
    metadata_keys: tuple[str, ...]
    error_count: int = 0
    private_content_visible: bool = False
    secret_values_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "path": self.path,
            "status": self.status,
            "reasons": self.reasons,
            "suggested_actions": self.suggested_actions,
            "metadata_keys": self.metadata_keys,
            "error_count": self.error_count,
            "private_content_visible": self.private_content_visible,
            "secret_values_visible": self.secret_values_visible,
        }


def build_review_queue_item(
    entry: NextcloudIntakeLedgerEntry | Mapping[str, Any],
    *,
    reasons: Iterable[str] | None = None,
    suggested_actions: Iterable[str] | None = None,
) -> NextcloudReviewQueueItem:
    """Build a compact, redacted review packet from one ledger entry."""

    normalized_entry = (
        entry
        if isinstance(entry, NextcloudIntakeLedgerEntry)
        else NextcloudIntakeLedgerEntry.from_dict(entry)
    )
    report = normalized_entry.to_report()
    return NextcloudReviewQueueItem(
        digest=report["digest"],
        path=report["path"],
        status=report["status"],
        reasons=tuple(reasons) if reasons is not None else _default_reasons(report),
        suggested_actions=(
            _normalize_actions(suggested_actions)
            if suggested_actions is not None
            else _default_actions(report)
        ),
        metadata_keys=tuple(report["metadata_keys"]),
        error_count=report["error_count"],
    )


def build_review_queue(
    entries: Iterable[NextcloudIntakeLedgerEntry | Mapping[str, Any]],
    *,
    include_route_ready: bool = False,
) -> tuple[NextcloudReviewQueueItem, ...]:
    """Return review packets for items that need operator attention."""

    queue: list[NextcloudReviewQueueItem] = []
    for entry in entries:
        item = build_review_queue_item(entry)
        if item.status in REVIEW_STATUSES or item.error_count:
            queue.append(item)
        elif include_route_ready:
            queue.append(item)
    return tuple(queue)


def summarize_review_queue(items: Iterable[NextcloudReviewQueueItem]) -> dict[str, Any]:
    """Summarize review work without including private values."""

    by_status: dict[str, int] = {}
    by_action: dict[str, int] = {}
    normalized = tuple(items)
    for item in normalized:
        by_status[item.status] = by_status.get(item.status, 0) + 1
        for action in item.suggested_actions:
            by_action[action] = by_action.get(action, 0) + 1
    return {
        "total": len(normalized),
        "by_status": by_status,
        "by_action": by_action,
        "private_content_visible": False,
        "secret_values_visible": False,
        "items": tuple(item.to_dict() for item in normalized),
    }


def _default_reasons(report: Mapping[str, Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    if report["status"] in REVIEW_STATUSES:
        reasons.append(f"status_{report['status']}")
    if report["error_count"]:
        reasons.append("ledger_errors_present")
    if not reasons:
        reasons.append("review_not_required")
    return tuple(reasons)


def _default_actions(report: Mapping[str, Any]) -> tuple[str, ...]:
    if report["status"] == "needs_review":
        return ("review", "copy_to_review")
    if report["status"] in {"failed", "permission_denied"} or report["error_count"]:
        return ("review", "request_metadata", "defer")
    return ("skip",)


def _normalize_actions(actions: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for action in actions:
        value = str(action or "").strip().lower().replace("-", "_")
        if value not in ALLOWED_REVIEW_ACTIONS:
            raise ValueError(f"review action must be one of {ALLOWED_REVIEW_ACTIONS}")
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))
