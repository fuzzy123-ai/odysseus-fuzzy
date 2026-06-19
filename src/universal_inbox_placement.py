"""Dry-run placement plans for Universal Inbox routing decisions.

The plan prepares a future copy operation without performing any filesystem
write, copy, move, delete, existence check, or provider call.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


PLACEMENT_SCHEMA = "odysseus.universal_inbox.placement_plan.v1"
_UNSAFE_PATH_CHARS = set('<>:"|?*')


class UniversalInboxPlacementError(ValueError):
    """Raised when a placement input cannot be interpreted."""


@dataclass(frozen=True)
class UniversalInboxPlacementPlan:
    original_path: str
    target_path: str
    sidecar_path: str
    status: str
    reasons: tuple[str, ...]
    review_reasons: tuple[str, ...]
    no_go_reasons: tuple[str, ...]
    operation: str = "copy"
    copy_only: bool = True
    delete_original: bool = False
    overwrite_existing: bool = False
    dry_run: bool = True
    writes_performed: bool = False
    schema: str = PLACEMENT_SCHEMA

    @classmethod
    def from_routing_decision(cls, decision: Any) -> "UniversalInboxPlacementPlan":
        payload = _payload(decision)
        review_reasons = list(_reason_codes(payload.get("review_reasons") or ()))
        no_go_reasons: list[str] = []

        original_path = _safe_path_or_flag(
            payload.get("original_path"),
            "unsafe_original_path",
            no_go_reasons,
        )
        target_path = _safe_path_or_flag(
            payload.get("target_path") or payload.get("planned_path"),
            "unsafe_target_path",
            no_go_reasons,
        )
        sidecar_path = _safe_path_or_flag(
            payload.get("sidecar_path"),
            "unsafe_sidecar_path",
            no_go_reasons,
        )

        requested_operation = str(
            payload.get("operation") or payload.get("safe_operation") or "copy"
        ).strip().lower()
        if requested_operation != "copy":
            no_go_reasons.append("destructive_operation")

        if bool(payload.get("delete_original", False)):
            no_go_reasons.append("delete_original")
        if bool(payload.get("overwrite_existing", False)):
            no_go_reasons.append("overwrite_existing")
        if bool(payload.get("target_conflict", False)) or "target_conflict" in review_reasons:
            review_reasons.append("target_conflict")
        if str(payload.get("status") or "") == "needs_review":
            review_reasons.extend(_reason_codes(payload.get("warnings") or ()))

        review = tuple(dict.fromkeys(review_reasons))
        no_go = tuple(dict.fromkeys(no_go_reasons))
        if no_go:
            status = "no_go"
        elif review:
            status = "review"
        else:
            status = "planned"

        return cls(
            original_path=original_path,
            target_path=target_path,
            sidecar_path=sidecar_path,
            status=status,
            reasons=tuple(no_go + review),
            review_reasons=review,
            no_go_reasons=no_go,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reasons": self.reasons,
            "review_reasons": self.review_reasons,
            "no_go_reasons": self.no_go_reasons,
            "original_path": self.original_path,
            "target_path": self.target_path,
            "sidecar_path": self.sidecar_path,
            "operation": self.operation,
            "copy_only": self.copy_only,
            "delete_original": self.delete_original,
            "overwrite_existing": self.overwrite_existing,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
        }


def build_universal_inbox_placement_plan(
    decision: Any,
) -> UniversalInboxPlacementPlan:
    """Build a dry-run placement plan from a routing decision or mapping."""

    return UniversalInboxPlacementPlan.from_routing_decision(decision)


def _payload(decision: Any) -> Mapping[str, Any]:
    if hasattr(decision, "to_dict"):
        value = decision.to_dict()
    elif hasattr(decision, "__dict__"):
        value = vars(decision)
    else:
        value = decision
    if not isinstance(value, Mapping):
        raise UniversalInboxPlacementError("placement decision must be mapping-like")
    return value


def _safe_path_or_flag(value: Any, reason: str, no_go_reasons: list[str]) -> str:
    try:
        return _normalize_relative_path(value)
    except UniversalInboxPlacementError:
        no_go_reasons.append(reason)
        return ""


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise UniversalInboxPlacementError("path is required")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise UniversalInboxPlacementError("path must be relative")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() != "."]
    if not parts or any(part == ".." for part in parts):
        raise UniversalInboxPlacementError("path must not contain traversal segments")
    for part in parts:
        if any(ord(ch) < 32 for ch in part):
            raise UniversalInboxPlacementError("path contains control characters")
        if any(ch in _UNSAFE_PATH_CHARS for ch in part):
            raise UniversalInboxPlacementError("path contains unsafe segment")
    return "/".join(parts)


def _reason_codes(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (tuple, list)):
        return ()
    reasons = []
    for value in values:
        reason = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if reason:
            reasons.append(reason)
    return tuple(dict.fromkeys(reasons))
