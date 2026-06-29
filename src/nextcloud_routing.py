"""Offline routing and safe-placement plans for Nextcloud intake.

This module turns redacted ledger metadata and governed tags into decisions.
It never reads files, calls Nextcloud, or executes copy/tag/sidecar writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping

from src.nextcloud_intake_ledger import NextcloudIntakeLedgerEntry, redact_metadata
from src.nextcloud_tag_governance import (
    TagCandidate,
    TagGovernanceReport,
    govern_nextcloud_tags,
)


ROUTABLE_STATUSES = {"analyzed", "indexed", "routed", "routed_indexed", "metadata_written"}
REVIEW_STATUSES = {"needs_review", "failed", "permission_denied", "duplicate"}
SENSITIVE_CLASSES = {"secret", "sensitive", "local_sensitive", "unknown_private"}
ALLOWED_PLANNED_ACTIONS = ("copy", "write_sidecar", "project_tags")
FORBIDDEN_PLANNED_ACTIONS = ("delete", "move", "overwrite", "occ_admin")
_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True)
class NextcloudRoutingDecision:
    """A metadata-only decision for where an intake item should go."""

    digest: str
    source_path: str
    target_path: str
    status: str
    confidence: float
    review_required: bool
    review_reasons: tuple[str, ...]
    summary: str
    projected_tags: tuple[str, ...]
    preserved_user_tags: tuple[str, ...]
    metadata_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "status": self.status,
            "confidence": self.confidence,
            "review_required": self.review_required,
            "review_reasons": self.review_reasons,
            "summary": self.summary,
            "projected_tags": self.projected_tags,
            "preserved_user_tags": self.preserved_user_tags,
            "metadata_keys": self.metadata_keys,
        }


@dataclass(frozen=True)
class NextcloudPlacementAction:
    """A planned safe operation. Execution is intentionally out of scope."""

    action: str
    source_path: str
    target_path: str
    payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.action not in ALLOWED_PLANNED_ACTIONS:
            raise ValueError(f"placement action must be one of {ALLOWED_PLANNED_ACTIONS}")
        _validate_relative_path(self.source_path, field="source_path")
        _validate_relative_path(self.target_path, field="target_path")
        object.__setattr__(self, "payload", redact_metadata(self.payload or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "payload": dict(self.payload or {}),
        }


@dataclass(frozen=True)
class NextcloudSafePlacementPlan:
    """Dry-run plan for copy, sidecar, and tag projection."""

    decision: NextcloudRoutingDecision
    actions: tuple[NextcloudPlacementAction, ...]
    blocked_actions: tuple[str, ...] = FORBIDDEN_PLANNED_ACTIONS
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "actions": tuple(action.to_dict() for action in self.actions),
            "blocked_actions": self.blocked_actions,
            "execution_allowed": self.execution_allowed,
        }


def build_nextcloud_routing_decision(
    entry: NextcloudIntakeLedgerEntry | Mapping[str, Any],
    *,
    tag_candidates: Iterable[TagCandidate | Mapping[str, Any]] = (),
    target_root: str = "Archive",
    min_confidence: float = 0.78,
) -> NextcloudRoutingDecision:
    """Build a routing decision from ledger metadata and tag candidates."""

    normalized_entry = (
        entry
        if isinstance(entry, NextcloudIntakeLedgerEntry)
        else NextcloudIntakeLedgerEntry.from_dict(entry)
    )
    metadata = redact_metadata(normalized_entry.metadata)
    tag_report = govern_nextcloud_tags(tag_candidates)
    confidence = _normalize_confidence(metadata.get("confidence", min_confidence))
    review_reasons = _review_reasons(
        normalized_entry,
        metadata=metadata,
        tag_report=tag_report,
        confidence=confidence,
        min_confidence=min_confidence,
    )
    review_required = bool(review_reasons)
    status = "needs_review" if review_required else "routable"
    target_path = _target_path(
        normalized_entry.path,
        metadata=metadata,
        projected_tags=tag_report.projected_nextcloud_tags,
        target_root=target_root,
        review_required=review_required,
    )
    return NextcloudRoutingDecision(
        digest=normalized_entry.digest,
        source_path=normalized_entry.path,
        target_path=target_path,
        status=status,
        confidence=confidence,
        review_required=review_required,
        review_reasons=tuple(review_reasons),
        summary=_summary(normalized_entry, metadata=metadata, review_required=review_required),
        projected_tags=tag_report.projected_nextcloud_tags,
        preserved_user_tags=tag_report.preserved_user_tags,
        metadata_keys=tuple(sorted(metadata.keys())),
    )


def build_nextcloud_safe_placement_plan(
    decision: NextcloudRoutingDecision | Mapping[str, Any],
) -> NextcloudSafePlacementPlan:
    """Create a dry-run placement plan; it never performs the planned actions."""

    normalized_decision = (
        decision
        if isinstance(decision, NextcloudRoutingDecision)
        else NextcloudRoutingDecision(
            digest=str(decision.get("digest", "")),
            source_path=str(decision.get("source_path", "")),
            target_path=str(decision.get("target_path", "")),
            status=str(decision.get("status", "")),
            confidence=_normalize_confidence(decision.get("confidence", 0.0)),
            review_required=bool(decision.get("review_required", True)),
            review_reasons=tuple(decision.get("review_reasons") or ()),
            summary=str(decision.get("summary", "")),
            projected_tags=tuple(decision.get("projected_tags") or ()),
            preserved_user_tags=tuple(decision.get("preserved_user_tags") or ()),
            metadata_keys=tuple(decision.get("metadata_keys") or ()),
        )
    )
    actions: list[NextcloudPlacementAction] = [
        NextcloudPlacementAction(
            action="copy",
            source_path=normalized_decision.source_path,
            target_path=normalized_decision.target_path,
        ),
        NextcloudPlacementAction(
            action="write_sidecar",
            source_path=normalized_decision.source_path,
            target_path=f"{normalized_decision.target_path}.odysseus.json",
            payload={
                "digest": normalized_decision.digest,
                "status": normalized_decision.status,
                "confidence": normalized_decision.confidence,
                "review_required": normalized_decision.review_required,
                "review_reasons": normalized_decision.review_reasons,
                "metadata_keys": normalized_decision.metadata_keys,
            },
        ),
    ]
    if normalized_decision.projected_tags:
        actions.append(
            NextcloudPlacementAction(
                action="project_tags",
                source_path=normalized_decision.source_path,
                target_path=normalized_decision.target_path,
                payload={"tags": normalized_decision.projected_tags},
            )
        )
    return NextcloudSafePlacementPlan(decision=normalized_decision, actions=tuple(actions))


def _review_reasons(
    entry: NextcloudIntakeLedgerEntry,
    *,
    metadata: Mapping[str, Any],
    tag_report: TagGovernanceReport,
    confidence: float,
    min_confidence: float,
) -> list[str]:
    reasons: list[str] = []
    if entry.status in REVIEW_STATUSES:
        reasons.append(f"status_{entry.status}")
    if entry.errors:
        reasons.append("ledger_errors_present")
    if confidence < min_confidence:
        reasons.append("low_confidence")
    privacy_class = str(metadata.get("privacy_class") or metadata.get("classification") or "").lower()
    if privacy_class in SENSITIVE_CLASSES:
        reasons.append("sensitive_or_secret_class")
    if metadata.get("possible_duplicate") is True:
        reasons.append("possible_duplicate")
    if metadata.get("partial") is True or metadata.get("extraction_status") == "partial":
        reasons.append("partial_extraction")
    if metadata.get("target_conflict") is True:
        reasons.append("existing_target_conflict")
    if not metadata.get("target_area") and not tag_report.projected_nextcloud_tags:
        reasons.append("uncertain_target")
    if tag_report.review_tags or tag_report.blocked_tags:
        reasons.append("tag_policy_review")
    return tuple(dict.fromkeys(reasons))


def _target_path(
    source_path: str,
    *,
    metadata: Mapping[str, Any],
    projected_tags: tuple[str, ...],
    target_root: str,
    review_required: bool,
) -> str:
    file_name = PurePosixPath(source_path).name
    root = "Review Queue" if review_required else target_root
    target_area = metadata.get("target_area") or (projected_tags[0] if projected_tags else "unsorted")
    return "/".join(
        segment
        for segment in (
            _safe_segment(root, fallback="Archive"),
            _safe_segment(str(target_area), fallback="unsorted"),
            _safe_segment(file_name, fallback="item"),
        )
        if segment
    )


def _summary(
    entry: NextcloudIntakeLedgerEntry,
    *,
    metadata: Mapping[str, Any],
    review_required: bool,
) -> str:
    kind = str(metadata.get("document_type") or metadata.get("kind") or "document")
    state = "needs review" if review_required else "ready for placement"
    return f"{kind} from {entry.path} is {state}"


def _safe_segment(value: str, *, fallback: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("-", str(value or "").strip()).strip(".-_")
    return cleaned[:80] if cleaned else fallback


def _validate_relative_path(value: str, *, field: str) -> None:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise ValueError(f"{field} must be a relative path")
    if any(part == ".." for part in raw.split("/")):
        raise ValueError(f"{field} must not contain traversal segments")


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))
