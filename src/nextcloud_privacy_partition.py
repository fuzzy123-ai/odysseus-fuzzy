"""Runtime-only privacy partitioning for Nextcloud migration planning.

The partitioner only classifies caller-provided relative paths. Sensitive root
markers are inputs, never serialized into reports, ledgers, or summaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


ARCHIVE_CANDIDATE = "archive_candidate"
LOCAL_SENSITIVE = "local_sensitive"
UNKNOWN_PRIVATE = "unknown_private"


class NextcloudPrivacyPartitionError(ValueError):
    """Raised when a path or runtime marker would be unsafe to classify."""


@dataclass(frozen=True, slots=True)
class NextcloudPrivacyDecision:
    privacy_class: str
    archive_allowed: bool
    mirror_to_new_nextcloud: bool
    memory_write_candidate: bool
    local_model_only: bool
    inspection_allowed: bool
    required_model_scope: str
    reason_code: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "privacy_class": self.privacy_class,
            "archive_allowed": self.archive_allowed,
            "mirror_to_new_nextcloud": self.mirror_to_new_nextcloud,
            "memory_write_candidate": self.memory_write_candidate,
            "local_model_only": self.local_model_only,
            "inspection_allowed": self.inspection_allowed,
            "memory_targets": ("raptor", "memory"),
            "required_model_scope": self.required_model_scope,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class NextcloudPrivacySummary:
    total: int
    archive_candidates: int
    local_sensitive: int
    unknown_private: int
    sensitive_root_marker_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "archive_candidates": self.archive_candidates,
            "local_sensitive": self.local_sensitive,
            "unknown_private": self.unknown_private,
            "sensitive_root_marker_count": self.sensitive_root_marker_count,
        }


def classify_nextcloud_relative_path(
    relative_path: Any,
    *,
    sensitive_roots: Iterable[str] = (),
    default_unknown_private: bool = False,
) -> NextcloudPrivacyDecision:
    """Classify a relative source path without exposing sensitive root names."""

    parts = _relative_parts(relative_path)
    sensitive_markers = _normalize_markers(sensitive_roots)
    top_level = _normalize_marker(parts[0])
    if top_level in sensitive_markers:
        return NextcloudPrivacyDecision(
            privacy_class=LOCAL_SENSITIVE,
            archive_allowed=False,
            mirror_to_new_nextcloud=False,
            memory_write_candidate=True,
            local_model_only=True,
            inspection_allowed=False,
            required_model_scope="local_only",
            reason_code="sensitive_root_runtime_match",
        )
    if default_unknown_private:
        return NextcloudPrivacyDecision(
            privacy_class=UNKNOWN_PRIVATE,
            archive_allowed=False,
            mirror_to_new_nextcloud=False,
            memory_write_candidate=False,
            local_model_only=True,
            inspection_allowed=False,
            required_model_scope="local_only",
            reason_code="default_unknown_private",
        )
    return NextcloudPrivacyDecision(
        privacy_class=ARCHIVE_CANDIDATE,
        archive_allowed=True,
        mirror_to_new_nextcloud=True,
        memory_write_candidate=True,
        local_model_only=False,
        inspection_allowed=False,
        required_model_scope="policy_selected",
        reason_code="no_sensitive_root_match",
    )


def summarize_nextcloud_privacy_partition(
    relative_paths: Iterable[Any],
    *,
    sensitive_roots: Iterable[str] = (),
    default_unknown_private: bool = False,
) -> NextcloudPrivacySummary:
    """Return aggregate counts only; never include path or marker values."""

    markers = _normalize_markers(sensitive_roots)
    total = archive_candidates = local_sensitive = unknown_private = 0
    for path in relative_paths:
        decision = classify_nextcloud_relative_path(
            path,
            sensitive_roots=markers,
            default_unknown_private=default_unknown_private,
        )
        total += 1
        if decision.privacy_class == ARCHIVE_CANDIDATE:
            archive_candidates += 1
        elif decision.privacy_class == LOCAL_SENSITIVE:
            local_sensitive += 1
        elif decision.privacy_class == UNKNOWN_PRIVATE:
            unknown_private += 1
    return NextcloudPrivacySummary(
        total=total,
        archive_candidates=archive_candidates,
        local_sensitive=local_sensitive,
        unknown_private=unknown_private,
        sensitive_root_marker_count=len(markers),
    )


def privacy_metadata_allows_archive(metadata: Mapping[str, Any]) -> bool:
    """Return whether an inventory metadata payload is safe for archive planning."""

    if "archive_allowed" not in metadata and "privacy_class" not in metadata:
        return True
    return (
        bool(metadata.get("archive_allowed"))
        and bool(metadata.get("mirror_to_new_nextcloud", metadata.get("archive_allowed")))
        and metadata.get("privacy_class") == ARCHIVE_CANDIDATE
    )


def _relative_parts(value: Any) -> tuple[str, ...]:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise NextcloudPrivacyPartitionError("relative_path must not be empty")
    if raw.startswith(("/", "~")) or (len(raw) >= 3 and raw[1:3] == ":/"):
        raise NextcloudPrivacyPartitionError("relative_path must be relative")
    parts = tuple(part.strip() for part in raw.split("/") if part.strip() and part.strip() != ".")
    if not parts or any(part == ".." for part in parts):
        raise NextcloudPrivacyPartitionError("relative_path must not contain traversal segments")
    if any(any(ord(ch) < 32 for ch in part) for part in parts):
        raise NextcloudPrivacyPartitionError("relative_path contains control characters")
    return parts


def _normalize_markers(values: Iterable[str]) -> frozenset[str]:
    markers = frozenset(_normalize_marker(value) for value in values if str(value or "").strip())
    if "" in markers:
        raise NextcloudPrivacyPartitionError("sensitive root markers must not be empty")
    return markers


def _normalize_marker(value: Any) -> str:
    marker = str(value or "").strip().replace("\\", "/").strip("/")
    if not marker:
        raise NextcloudPrivacyPartitionError("sensitive root markers must not be empty")
    if "/" in marker or marker in {".", ".."}:
        raise NextcloudPrivacyPartitionError("sensitive root markers must be top-level folder names")
    if any(ord(ch) < 32 for ch in marker):
        raise NextcloudPrivacyPartitionError("sensitive root markers contain control characters")
    return marker.casefold()
