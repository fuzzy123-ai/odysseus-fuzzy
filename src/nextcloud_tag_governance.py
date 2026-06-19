"""Offline governance model for Nextcloud-safe tag projection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


TAG_CLASSES = ("user", "system", "semantic", "graph_only")
DECISION_STATUSES = ("preserved", "projected", "review", "blocked")

CANONICAL_TAG_VOCABULARY: Mapping[str, str] = {
    "archive": "archive",
    "area": "area",
    "decision": "decision",
    "meeting": "meeting",
    "person": "person",
    "project": "project",
    "reference": "reference",
    "task": "task",
}

TAG_ALIASES: Mapping[str, str] = {
    "action": "task",
    "actions": "task",
    "archive-note": "archive",
    "decision-log": "decision",
    "decisions": "decision",
    "docs": "reference",
    "meeting-notes": "meeting",
    "notes": "reference",
    "people": "person",
    "proj": "project",
    "projects": "project",
    "references": "reference",
    "todo": "task",
    "todos": "task",
}


@dataclass(frozen=True)
class TagProjectionPolicy:
    min_confidence: float = 0.8
    review_confidence: float = 0.5
    allow_system_projection: bool = True
    allow_semantic_projection: bool = True
    allow_alias_projection: bool = True


DEFAULT_POLICY = TagProjectionPolicy()


@dataclass(frozen=True)
class TagCandidate:
    tag: str
    tag_class: str
    confidence: float = 1.0


@dataclass(frozen=True)
class TagGovernanceDecision:
    input_tag: str
    normalized_tag: str
    tag_class: str
    confidence: float
    canonical_tag: str | None
    mapped_from_alias: bool
    status: str
    reason: str
    nextcloud_tag: str | None

    @property
    def allow_nextcloud_projection(self) -> bool:
        return self.status == "projected" and bool(self.nextcloud_tag)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tag": self.input_tag,
            "normalized_tag": self.normalized_tag,
            "tag_class": self.tag_class,
            "confidence": self.confidence,
            "canonical_tag": self.canonical_tag,
            "mapped_from_alias": self.mapped_from_alias,
            "status": self.status,
            "reason": self.reason,
            "nextcloud_tag": self.nextcloud_tag,
            "allow_nextcloud_projection": self.allow_nextcloud_projection,
        }


@dataclass(frozen=True)
class TagGovernanceReport:
    decisions: tuple[TagGovernanceDecision, ...]
    preserved_user_tags: tuple[str, ...]
    projected_nextcloud_tags: tuple[str, ...]
    review_tags: tuple[str, ...]
    blocked_tags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": tuple(decision.to_dict() for decision in self.decisions),
            "preserved_user_tags": self.preserved_user_tags,
            "projected_nextcloud_tags": self.projected_nextcloud_tags,
            "review_tags": self.review_tags,
            "blocked_tags": self.blocked_tags,
        }


def govern_nextcloud_tag(
    candidate: TagCandidate | Mapping[str, Any],
    *,
    policy: TagProjectionPolicy = DEFAULT_POLICY,
) -> TagGovernanceDecision:
    normalized_candidate = _coerce_candidate(candidate)
    normalized_tag = _normalize_tag(normalized_candidate.tag)
    confidence = _normalize_confidence(normalized_candidate.confidence)

    if not normalized_tag:
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag="",
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=None,
            mapped_from_alias=False,
            status="blocked",
            reason="empty_tag",
            nextcloud_tag=None,
        )

    if normalized_candidate.tag_class == "user":
        canonical_tag, mapped_from_alias = _resolve_canonical_tag(normalized_tag)
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag=normalized_tag,
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=canonical_tag,
            mapped_from_alias=mapped_from_alias,
            status="preserved",
            reason="manual_user_tag_preserved",
            nextcloud_tag=normalized_candidate.tag.strip(),
        )

    if normalized_candidate.tag_class == "graph_only":
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag=normalized_tag,
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=None,
            mapped_from_alias=False,
            status="blocked",
            reason="graph_only_never_projects",
            nextcloud_tag=None,
        )

    canonical_tag, mapped_from_alias = _resolve_canonical_tag(normalized_tag)
    if not canonical_tag:
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag=normalized_tag,
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=None,
            mapped_from_alias=False,
            status="review",
            reason="free_tag_requires_review",
            nextcloud_tag=None,
        )

    if mapped_from_alias and not policy.allow_alias_projection:
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag=normalized_tag,
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=canonical_tag,
            mapped_from_alias=True,
            status="blocked",
            reason="policy_blocks_alias_projection",
            nextcloud_tag=None,
        )

    if normalized_candidate.tag_class == "system" and not policy.allow_system_projection:
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag=normalized_tag,
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=canonical_tag,
            mapped_from_alias=mapped_from_alias,
            status="blocked",
            reason="policy_blocks_system_projection",
            nextcloud_tag=None,
        )

    if normalized_candidate.tag_class == "semantic" and not policy.allow_semantic_projection:
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag=normalized_tag,
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=canonical_tag,
            mapped_from_alias=mapped_from_alias,
            status="blocked",
            reason="policy_blocks_semantic_projection",
            nextcloud_tag=None,
        )

    if confidence < policy.min_confidence:
        reason = "confidence_below_minimum"
        if confidence < policy.review_confidence:
            reason = "confidence_far_below_minimum"
        return TagGovernanceDecision(
            input_tag=normalized_candidate.tag,
            normalized_tag=normalized_tag,
            tag_class=normalized_candidate.tag_class,
            confidence=confidence,
            canonical_tag=canonical_tag,
            mapped_from_alias=mapped_from_alias,
            status="review",
            reason=reason,
            nextcloud_tag=None,
        )

    return TagGovernanceDecision(
        input_tag=normalized_candidate.tag,
        normalized_tag=normalized_tag,
        tag_class=normalized_candidate.tag_class,
        confidence=confidence,
        canonical_tag=canonical_tag,
        mapped_from_alias=mapped_from_alias,
        status="projected",
        reason="mapped_to_canonical" if mapped_from_alias else "canonical_tag_allowed",
        nextcloud_tag=canonical_tag,
    )


def govern_nextcloud_tags(
    candidates: Iterable[TagCandidate | Mapping[str, Any]],
    *,
    policy: TagProjectionPolicy = DEFAULT_POLICY,
    existing_user_tags: Iterable[str] = (),
) -> TagGovernanceReport:
    decisions = tuple(govern_nextcloud_tag(candidate, policy=policy) for candidate in candidates)
    preserved_user_tags = tuple(dict.fromkeys(_normalize_existing_user_tags(existing_user_tags)))
    projected_nextcloud_tags = tuple(
        dict.fromkeys(
            decision.nextcloud_tag
            for decision in decisions
            if decision.status == "projected" and decision.nextcloud_tag
        )
    )
    review_tags = tuple(
        dict.fromkeys(
            decision.input_tag.strip()
            for decision in decisions
            if decision.status == "review" and decision.input_tag.strip()
        )
    )
    blocked_tags = tuple(
        dict.fromkeys(
            decision.input_tag.strip()
            for decision in decisions
            if decision.status == "blocked" and decision.input_tag.strip()
        )
    )
    return TagGovernanceReport(
        decisions=decisions,
        preserved_user_tags=preserved_user_tags,
        projected_nextcloud_tags=projected_nextcloud_tags,
        review_tags=review_tags,
        blocked_tags=blocked_tags,
    )


def _coerce_candidate(candidate: TagCandidate | Mapping[str, Any]) -> TagCandidate:
    if isinstance(candidate, TagCandidate):
        normalized = candidate
    elif isinstance(candidate, Mapping):
        normalized = TagCandidate(
            tag=str(candidate.get("tag", "")),
            tag_class=str(candidate.get("tag_class", "")).strip(),
            confidence=candidate.get("confidence", 1.0),
        )
    else:
        raise TypeError("candidate must be a TagCandidate or mapping")

    tag_class = normalized.tag_class.strip()
    if tag_class not in TAG_CLASSES:
        raise ValueError(f"tag_class must be one of {TAG_CLASSES}")
    return TagCandidate(
        tag=str(normalized.tag),
        tag_class=tag_class,
        confidence=normalized.confidence,
    )


def _resolve_canonical_tag(tag: str) -> tuple[str | None, bool]:
    if tag in CANONICAL_TAG_VOCABULARY:
        return CANONICAL_TAG_VOCABULARY[tag], False
    mapped = TAG_ALIASES.get(tag)
    if mapped:
        return mapped, True
    return None, False


def _normalize_existing_user_tags(tags: Iterable[str]) -> tuple[str, ...]:
    preserved: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        stripped = tag.strip()
        if stripped:
            preserved.append(stripped)
    return tuple(preserved)


def _normalize_tag(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _normalize_confidence(value: Any) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise ValueError("confidence must be numeric") from None
    if normalized < 0 or normalized > 1:
        raise ValueError("confidence must be between 0 and 1")
    return normalized
