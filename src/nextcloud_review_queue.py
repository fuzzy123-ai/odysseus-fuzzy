"""Offline review queue packets for Nextcloud private-source intake."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.nextcloud_intake_ledger import NextcloudIntakeLedgerEntry
from src.nextcloud_routing import NextcloudRoutingDecision


REVIEW_STATUSES = {"needs_review", "failed", "permission_denied"}
ALLOWED_REVIEW_ACTIONS = (
    "review",
    "copy_to_review",
    "request_metadata",
    "defer",
    "skip",
)
REVIEW_ARTIFACT_SCHEMA = "odysseus.nextcloud.review_queue_artifact.v1"


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
    target_suggestion: str = ""
    confidence: float | None = None
    next_safe_options: tuple[str, ...] = ()
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
            "target_suggestion": self.target_suggestion,
            "confidence": self.confidence,
            "next_safe_options": self.next_safe_options,
            "private_content_visible": self.private_content_visible,
            "secret_values_visible": self.secret_values_visible,
        }

    def to_markdown(self) -> str:
        """Render a compact review artifact without private values."""

        lines = [
            "# Nextcloud Review Item",
            "",
            f"- Digest: `{self.digest}`",
            f"- Path: `{self.path}`",
            f"- Status: `{self.status}`",
            f"- Target suggestion: `{self.target_suggestion or 'n/a'}`",
            f"- Confidence: `{self.confidence if self.confidence is not None else 'n/a'}`",
            f"- Reasons: `{', '.join(self.reasons) or 'none'}`",
            f"- Next safe options: `{', '.join(self.next_safe_options or self.suggested_actions)}`",
            f"- Metadata keys: `{', '.join(self.metadata_keys) or 'none'}`",
            "",
            "Private contents and secret values are intentionally not included.",
        ]
        return "\n".join(lines)


def build_review_queue_item(
    entry: NextcloudIntakeLedgerEntry | Mapping[str, Any],
    *,
    routing_decision: NextcloudRoutingDecision | Mapping[str, Any] | None = None,
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
    decision = _coerce_routing_decision(routing_decision)
    normalized_actions = (
        _normalize_actions(suggested_actions)
        if suggested_actions is not None
        else _default_actions(report)
    )
    review_reasons = tuple(reasons) if reasons is not None else _default_reasons(report, decision)
    return NextcloudReviewQueueItem(
        digest=report["digest"],
        path=report["path"],
        status=report["status"],
        reasons=review_reasons,
        suggested_actions=normalized_actions,
        metadata_keys=tuple(report["metadata_keys"]),
        error_count=report["error_count"],
        target_suggestion=decision.target_path if decision is not None else "",
        confidence=decision.confidence if decision is not None else None,
        next_safe_options=_next_safe_options(report, decision, normalized_actions),
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


def build_review_queue_artifact(item: NextcloudReviewQueueItem) -> dict[str, Any]:
    """Return JSON and Markdown review artifacts for one item."""

    return {
        "schema": REVIEW_ARTIFACT_SCHEMA,
        "json": item.to_dict(),
        "markdown": item.to_markdown(),
        "private_content_visible": False,
        "secret_values_visible": False,
    }


def build_review_queue_artifacts(
    items: Iterable[NextcloudReviewQueueItem],
) -> tuple[dict[str, Any], ...]:
    return tuple(build_review_queue_artifact(item) for item in items)


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


def _default_reasons(
    report: Mapping[str, Any],
    decision: NextcloudRoutingDecision | None = None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if report["status"] in REVIEW_STATUSES:
        reasons.append(f"status_{report['status']}")
    if report["error_count"]:
        reasons.append("ledger_errors_present")
    if decision is not None:
        reasons.extend(decision.review_reasons)
        if decision.review_required:
            reasons.append("routing_needs_review")
    if not reasons:
        reasons.append("review_not_required")
    return tuple(dict.fromkeys(reasons))


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


def _next_safe_options(
    report: Mapping[str, Any],
    decision: NextcloudRoutingDecision | None,
    fallback_actions: tuple[str, ...],
) -> tuple[str, ...]:
    options: list[str] = list(fallback_actions)
    if decision is not None and decision.target_path:
        options.append("approve_target_suggestion")
    if report["status"] in {"failed", "permission_denied"} or report["error_count"]:
        options.append("request_new_metadata_only_scan")
    if decision is not None and decision.review_required:
        options.append("keep_in_review_queue")
    return tuple(dict.fromkeys(options))


def _coerce_routing_decision(
    decision: NextcloudRoutingDecision | Mapping[str, Any] | None,
) -> NextcloudRoutingDecision | None:
    if decision is None:
        return None
    if isinstance(decision, NextcloudRoutingDecision):
        return decision
    return NextcloudRoutingDecision(
        digest=str(decision.get("digest", "")),
        source_path=str(decision.get("source_path", "")),
        target_path=str(decision.get("target_path", "")),
        status=str(decision.get("status", "")),
        confidence=float(decision.get("confidence", 0.0)),
        review_required=bool(decision.get("review_required", True)),
        review_reasons=tuple(decision.get("review_reasons") or ()),
        summary=str(decision.get("summary", "")),
        projected_tags=tuple(decision.get("projected_tags") or ()),
        preserved_user_tags=tuple(decision.get("preserved_user_tags") or ()),
        metadata_keys=tuple(decision.get("metadata_keys") or ()),
    )
