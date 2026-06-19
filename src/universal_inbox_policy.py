"""Policy gate for Universal Inbox routing and memory writes.

The gate is intentionally offline-only. It classifies already-known metadata
and analysis flags into go/review/no_go without touching files or providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


POLICY_SCHEMA = "odysseus.universal_inbox.policy_decision.v1"
POLICY_STATUSES = ("go", "review", "no_go")

REVIEW_REASONS = (
    "duplicate",
    "partial_extraction",
    "secret_detected",
    "sensitive",
    "target_conflict",
    "unknown_document_type",
    "unknown_domain",
    "low_confidence",
)

NO_GO_REASONS = (
    "unsafe_target_path",
    "destructive_operation",
    "delete_original",
    "overwrite_existing",
    "raw_content_persistence",
)


class UniversalInboxPolicyError(ValueError):
    """Raised when a policy input is invalid."""


@dataclass(frozen=True)
class UniversalInboxPolicyDecision:
    status: str
    reasons: tuple[str, ...]
    review_reasons: tuple[str, ...]
    no_go_reasons: tuple[str, ...]
    confidence: float
    schema: str = POLICY_SCHEMA

    @property
    def allows_automatic_routing(self) -> bool:
        return self.status == "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reasons": self.reasons,
            "review_reasons": self.review_reasons,
            "no_go_reasons": self.no_go_reasons,
            "confidence": self.confidence,
            "allows_automatic_routing": self.allows_automatic_routing,
        }


def evaluate_universal_inbox_policy(
    item: Any,
    *,
    allowed_domains: tuple[str, ...] | list[str] = ("private", "work"),
    min_auto_route_confidence: float = 0.82,
    domain: str = "",
    document_type: str = "",
    confidence: float | None = None,
) -> UniversalInboxPolicyDecision:
    """Evaluate routing risk from metadata without mutating external state."""

    payload = _payload(item)
    normalized_domain = str(domain or payload.get("domain") or "").strip().lower()
    normalized_document_type = str(document_type or payload.get("document_type") or "").strip().lower()
    normalized_confidence = _normalize_confidence(
        confidence if confidence is not None else payload.get("confidence", payload.get("routing_confidence", 0.0))
    )
    allowed = tuple(str(value).strip().lower() for value in allowed_domains)
    min_confidence = _normalize_confidence(min_auto_route_confidence)

    review_reasons: list[str] = []
    no_go_reasons: list[str] = []

    for reason in ("duplicate", "partial_extraction", "secret_detected", "sensitive", "target_conflict"):
        if bool(payload.get(reason, False)):
            review_reasons.append(reason)

    if normalized_domain not in allowed:
        review_reasons.append("unknown_domain")
    if not normalized_document_type:
        review_reasons.append("unknown_document_type")
    if normalized_confidence < min_confidence:
        review_reasons.append("low_confidence")

    for reason in NO_GO_REASONS:
        if bool(payload.get(reason, False)):
            no_go_reasons.append(reason)

    no_go_reasons = list(dict.fromkeys(no_go_reasons))
    review_reasons = list(dict.fromkeys(review_reasons))
    if no_go_reasons:
        status = "no_go"
    elif review_reasons:
        status = "review"
    else:
        status = "go"
    reasons = tuple(no_go_reasons + review_reasons)
    return UniversalInboxPolicyDecision(
        status=status,
        reasons=reasons,
        review_reasons=tuple(review_reasons),
        no_go_reasons=tuple(no_go_reasons),
        confidence=normalized_confidence,
    )


def _payload(item: Any) -> Mapping[str, Any]:
    if hasattr(item, "to_dict"):
        value = item.to_dict()
    elif hasattr(item, "__dict__"):
        value = vars(item)
    else:
        value = item
    if not isinstance(value, Mapping):
        raise UniversalInboxPolicyError("policy item must be a mapping-like object")
    return value


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise UniversalInboxPolicyError("confidence must be numeric") from None
    if confidence < 0 or confidence > 1:
        raise UniversalInboxPolicyError("confidence must be between 0 and 1")
    return confidence
