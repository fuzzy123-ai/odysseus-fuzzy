"""Lightweight backend contract for data classification policy decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class DataClassificationError(ValueError):
    """Raised when classification inputs or overrides are unsafe or invalid."""


class DataClassification(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class ChatAccessMode(StrEnum):
    NORMAL = "normal_chat"
    SECURE = "secure_chat"


class _ClassificationRank(IntEnum):
    PUBLIC = 1
    PRIVATE = 2
    SENSITIVE = 3
    SECRET = 4


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise DataClassificationError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise DataClassificationError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise DataClassificationError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise DataClassificationError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _rank(classification: DataClassification) -> _ClassificationRank:
    return _ClassificationRank[classification.name]


def normalize_classification(value: DataClassification | str) -> DataClassification:
    if isinstance(value, DataClassification):
        return value
    normalized = _normalize_slug(value, field_name="classification")
    try:
        return DataClassification(normalized)
    except ValueError as exc:
        raise DataClassificationError("classification must be public, private, sensitive, or secret") from exc


def _normalize_chat_mode(value: ChatAccessMode | str) -> ChatAccessMode:
    if isinstance(value, ChatAccessMode):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "normal_chat": ChatAccessMode.NORMAL,
        "normal-chat": ChatAccessMode.NORMAL,
        "normal": ChatAccessMode.NORMAL,
        "secure_chat": ChatAccessMode.SECURE,
        "secure-chat": ChatAccessMode.SECURE,
        "secure": ChatAccessMode.SECURE,
    }
    if raw not in alias_map:
        raise DataClassificationError("mode must be normal_chat or secure_chat")
    return alias_map[raw]


@dataclass(frozen=True, slots=True)
class ClassificationResolution:
    normalized: DataClassification | None
    requires_review: bool
    block_reason: str
    raw_value: str


@dataclass(frozen=True, slots=True)
class ClassificationOverride:
    reviewed_by: str
    reason: str
    reviewed_at: str

    @classmethod
    def create(cls, *, reviewed_by: Any, reason: Any, reviewed_at: Any) -> "ClassificationOverride":
        return cls(
            reviewed_by=_normalize_slug(reviewed_by, field_name="reviewed_by"),
            reason=_normalize_text(reason, field_name="reason", allow_empty=False, limit=_MAX_LONG_TEXT),
            reviewed_at=_normalize_text(reviewed_at, field_name="reviewed_at", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    block_reason: str
    required_mode: ChatAccessMode
    local_only: bool
    classification: DataClassification | None
    requires_review: bool


def resolve_classification(value: DataClassification | str | None) -> ClassificationResolution:
    raw_value = _normalize_text(value, field_name="raw_value", allow_empty=True)
    if not raw_value:
        return ClassificationResolution(
            normalized=None,
            requires_review=True,
            block_reason="unknown_classification",
            raw_value="",
        )
    try:
        normalized = normalize_classification(raw_value)
    except DataClassificationError:
        return ClassificationResolution(
            normalized=None,
            requires_review=True,
            block_reason="invalid_classification",
            raw_value=raw_value,
        )
    return ClassificationResolution(
        normalized=normalized,
        requires_review=False,
        block_reason="",
        raw_value=raw_value,
    )


def merge_classifications(classifications: Iterable[DataClassification | str]) -> DataClassification:
    resolved: list[DataClassification] = []
    for item in classifications:
        resolution = resolve_classification(item)
        if resolution.normalized is None:
            raise DataClassificationError(f"cannot merge unresolved classification: {resolution.block_reason}")
        resolved.append(resolution.normalized)
    if not resolved:
        raise DataClassificationError("classifications must not be empty")
    return max(resolved, key=_rank)


def derive_artifact_classification(
    *,
    source_classifications: Iterable[DataClassification | str],
    requested_classification: DataClassification | str | None = None,
    override: ClassificationOverride | None = None,
) -> DataClassification:
    inherited = merge_classifications(source_classifications)
    if requested_classification is None:
        return inherited

    requested = normalize_classification(requested_classification)
    if _rank(requested) < _rank(inherited):
        if override is None:
            raise DataClassificationError("downgrade requires explicit review override")
        if not override.reason:
            raise DataClassificationError("downgrade override requires a reason")
    return requested if _rank(requested) >= _rank(inherited) or override else inherited


def decide_chat_access(
    *,
    classification: DataClassification | str | None,
    mode: ChatAccessMode | str,
) -> AccessDecision:
    normalized_mode = _normalize_chat_mode(mode)
    resolution = resolve_classification(classification)
    if resolution.normalized is None:
        return AccessDecision(
            allowed=False,
            block_reason=resolution.block_reason,
            required_mode=normalized_mode,
            local_only=False,
            classification=None,
            requires_review=True,
        )

    effective = resolution.normalized
    if normalized_mode == ChatAccessMode.NORMAL and effective in {DataClassification.SENSITIVE, DataClassification.SECRET}:
        return AccessDecision(
            allowed=False,
            block_reason="requires_secure_chat",
            required_mode=ChatAccessMode.SECURE,
            local_only=True,
            classification=effective,
            requires_review=False,
        )

    return AccessDecision(
        allowed=True,
        block_reason="",
        required_mode=normalized_mode,
        local_only=effective in {DataClassification.SENSITIVE, DataClassification.SECRET},
        classification=effective,
        requires_review=False,
    )
