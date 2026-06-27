"""Privacy metadata bridge for offline Nextcloud ingestion paths.

The helpers in this module derive safe metadata from relative Nextcloud paths
only. They do not read file contents, call providers, or persist sensitive root
marker names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.nextcloud_privacy_partition import (
    ARCHIVE_CANDIDATE,
    LOCAL_SENSITIVE,
    UNKNOWN_PRIVATE,
    NextcloudPrivacyDecision,
    classify_nextcloud_relative_path,
)

POLICY_VERSION = "nextcloud_privacy_partition:v1"


@dataclass(frozen=True, slots=True)
class NextcloudIngestionPrivacy:
    decision: NextcloudPrivacyDecision
    classification: str
    rag_index_candidate: bool
    policy_version: str = POLICY_VERSION

    @property
    def local_model_only(self) -> bool:
        return self.decision.local_model_only

    @property
    def memory_write_candidate(self) -> bool:
        return self.decision.memory_write_candidate

    def to_metadata(self) -> dict[str, Any]:
        privacy = self.decision.to_metadata()
        return {
            "source_provider": "nextcloud",
            "privacy_policy": self.policy_version,
            "privacy_class": self.decision.privacy_class,
            "classification": self.classification,
            "ai_classification": self.classification,
            "local_model_only": self.local_model_only,
            "required_model_scope": self.decision.required_model_scope,
            "memory_write_candidate": self.memory_write_candidate,
            "rag_index_candidate": self.rag_index_candidate,
            "archive_allowed": self.decision.archive_allowed,
            "mirror_to_new_nextcloud": self.decision.mirror_to_new_nextcloud,
            "reason_code": self.decision.reason_code,
            "privacy": privacy,
        }

    def for_rag_metadata(self) -> dict[str, Any]:
        metadata = self.to_metadata()
        metadata.pop("privacy", None)
        return metadata

    def for_memory_abstract(self) -> dict[str, Any]:
        return {
            "source_provider": "nextcloud",
            "privacy_policy": self.policy_version,
            "privacy_class": self.decision.privacy_class,
            "classification": self.classification,
            "local_model_only": self.local_model_only,
            "required_model_scope": self.decision.required_model_scope,
            "memory_write_candidate": self.memory_write_candidate,
            "reason_code": self.decision.reason_code,
        }

    def for_extraction_metadata(self) -> dict[str, Any]:
        return {
            "privacy": self.decision.to_metadata(),
            "classification": self.classification,
            "local_model_only": self.local_model_only,
            "required_model_scope": self.decision.required_model_scope,
            "memory_write_candidate": self.memory_write_candidate,
            "rag_index_candidate": self.rag_index_candidate,
            "privacy_policy": self.policy_version,
        }


def classify_nextcloud_ingestion_path(
    relative_path: Any,
    *,
    sensitive_roots: Iterable[str] = (),
    default_unknown_private: bool = False,
) -> NextcloudIngestionPrivacy:
    """Classify a Nextcloud path for extraction, memory, and RAG metadata."""

    decision = classify_nextcloud_relative_path(
        relative_path,
        sensitive_roots=sensitive_roots,
        default_unknown_private=default_unknown_private,
    )
    if decision.privacy_class in {LOCAL_SENSITIVE, UNKNOWN_PRIVATE}:
        classification = "sensitive"
    elif decision.privacy_class == ARCHIVE_CANDIDATE:
        classification = "private"
    else:
        classification = "private"
    return NextcloudIngestionPrivacy(
        decision=decision,
        classification=classification,
        rag_index_candidate=decision.memory_write_candidate,
    )


def build_nextcloud_rag_metadata(
    relative_path: Any,
    *,
    sensitive_roots: Iterable[str] = (),
    default_unknown_private: bool = False,
) -> dict[str, Any]:
    return classify_nextcloud_ingestion_path(
        relative_path,
        sensitive_roots=sensitive_roots,
        default_unknown_private=default_unknown_private,
    ).for_rag_metadata()


def build_nextcloud_memory_abstract_metadata(
    relative_path: Any,
    *,
    sensitive_roots: Iterable[str] = (),
    default_unknown_private: bool = False,
) -> dict[str, Any]:
    return classify_nextcloud_ingestion_path(
        relative_path,
        sensitive_roots=sensitive_roots,
        default_unknown_private=default_unknown_private,
    ).for_memory_abstract()
