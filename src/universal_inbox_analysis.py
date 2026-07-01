"""Safe file-content analysis contracts for Universal Inbox.

This module decides how a file may be analyzed and whether a derived memory
abstraction may be written. It does not call providers, write memory, write
RaptorGraph, or persist raw file contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from src.data_classification import DataClassification, resolve_classification
from src.memory_triage_contract import (
    normalize_memory_classification,
    normalize_memory_document_type,
)
from src.privacy_runtime import is_dsgvo_mode_enabled
from src.universal_inbox_memory import FORBIDDEN_MEMORY_KEYS
from src.universal_inbox_provenance import (
    build_universal_inbox_author_stamp,
    coerce_universal_inbox_author_stamp,
)


ANALYSIS_POLICY_SCHEMA = "odysseus.universal_inbox.file_analysis_policy.v1"
ANALYSIS_PACKET_SCHEMA = "odysseus.universal_inbox.file_analysis_packet.v1"

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_KEY_PATTERNS = (
    "raw_text",
    "content",
    "body",
    "payload",
    "bytes",
    "ocr_dump",
    "full_text",
    "email_body",
    "transcript",
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "chat_id",
)
_FORBIDDEN_ANALYSIS_KEYS = frozenset(
    {
        *FORBIDDEN_MEMORY_KEYS,
        "raw_text",
        "full_text",
        "text_sample",
        "email_body",
        "transcript",
        "content",
    }
)
_SENSITIVE_FILENAME_HINTS = (
    "privat",
    "private",
    "niklas",
    "maaike",
    "rechnung",
    "invoice",
    "vertrag",
    "contract",
    "steuer",
    "tax",
    "bank",
    "iban",
    "medical",
    "arzt",
)
_SECRET_TEXT_RE = re.compile(
    r"(api[_-]?key|bearer\s+[a-z0-9._-]{12,}|password\s*[:=]|passwd\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_RE = re.compile(
    r"\b(iban|rechnung|invoice|vertrag|contract|steuer|tax|krankenkasse|medical|adresse|address)\b",
    re.IGNORECASE,
)


class UniversalInboxAnalysisError(ValueError):
    """Raised when an analysis packet would be unsafe or invalid."""


@dataclass(frozen=True)
class UniversalInboxFileAnalysisPolicy:
    classification: str
    dsgvo_mode: bool
    local_only_required: bool
    api_model_allowed: bool
    local_model_required: bool
    memory_write_allowed: bool
    raptor_write_allowed: bool
    requires_review: bool
    review_reasons: tuple[str, ...] = ()
    no_go_reasons: tuple[str, ...] = ()
    schema: str = ANALYSIS_POLICY_SCHEMA

    @property
    def status(self) -> str:
        if self.no_go_reasons:
            return "no_go"
        if self.requires_review:
            return "review"
        return "go"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "classification": self.classification,
            "dsgvo_mode": self.dsgvo_mode,
            "local_only_required": self.local_only_required,
            "api_model_allowed": self.api_model_allowed,
            "local_model_required": self.local_model_required,
            "memory_write_allowed": self.memory_write_allowed,
            "raptor_write_allowed": self.raptor_write_allowed,
            "requires_review": self.requires_review,
            "review_reasons": self.review_reasons,
            "no_go_reasons": self.no_go_reasons,
        }


@dataclass(frozen=True)
class UniversalInboxFileAnalysisPacket:
    status: str
    document_type: str
    policy: UniversalInboxFileAnalysisPolicy
    abstract: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    blocked_field_count: int = 0
    schema: str = ANALYSIS_PACKET_SCHEMA
    raw_content_visible: bool = False
    raw_content_persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        abstract, abstract_blocked = _sanitize_mapping(self.abstract)
        metadata, metadata_blocked = _sanitize_mapping(self.metadata)
        blocked_count = self.blocked_field_count + abstract_blocked + metadata_blocked
        payload = {
            "schema": self.schema,
            "status": _normalize_token(self.status, field="status"),
            "document_type": _normalize_token(self.document_type, field="document_type"),
            "policy": self.policy.to_dict(),
            "abstract": abstract,
            "metadata": metadata,
            "raw_content_visible": False,
            "raw_content_persisted": False,
        }
        if blocked_count:
            payload["blocked_field_count"] = blocked_count
        return payload


def evaluate_universal_inbox_file_analysis_policy(
    payload: Mapping[str, Any] | Any,
    *,
    requested_classification: str | DataClassification | None = None,
    settings: Mapping[str, Any] | None = None,
    allow_sensitive_memory_write: bool = False,
    allow_secret_memory_write: bool = False,
) -> UniversalInboxFileAnalysisPolicy:
    """Decide model route and memory eligibility from metadata and hints."""

    item = _payload(payload)
    dsgvo_mode = is_dsgvo_mode_enabled(settings=settings)
    classification = _resolve_effective_classification(
        item,
        requested_classification=requested_classification,
    )
    review_reasons: list[str] = []
    no_go_reasons: list[str] = []

    if classification is None:
        classification_value = "unknown"
        review_reasons.append("classification_unknown_requires_review")
    else:
        classification_value = classification.value

    extraction_status = str(item.get("extraction_status") or item.get("status") or "").strip().lower()
    if extraction_status in {"partial", "metadata_only", "unsupported", "failed", "blocked"}:
        review_reasons.append("partial_or_missing_extraction")
    if bool(item.get("dangerous") or extraction_status == "blocked"):
        no_go_reasons.append("dangerous_file_blocked")
    if bool(item.get("raw_content_persistence") or item.get("raw_content_persisted")):
        no_go_reasons.append("raw_content_persistence")
    if item.get("document_type") in {None, ""}:
        review_reasons.append("unknown_document_type")

    is_sensitive = classification in {DataClassification.SENSITIVE, DataClassification.SECRET}
    is_secret = classification == DataClassification.SECRET
    local_only_required = bool(dsgvo_mode or is_sensitive)
    api_model_allowed = bool(not local_only_required and not no_go_reasons and classification is not None)
    local_model_required = bool(local_only_required and not no_go_reasons)

    memory_allowed = bool(classification is not None and not no_go_reasons)
    if is_secret and not allow_secret_memory_write:
        memory_allowed = False
        review_reasons.append("secret_memory_requires_explicit_review")
    elif classification == DataClassification.SENSITIVE and not allow_sensitive_memory_write:
        memory_allowed = False
        review_reasons.append("sensitive_memory_requires_explicit_review")
    elif classification is None:
        memory_allowed = False

    review_reasons = list(dict.fromkeys(review_reasons))
    no_go_reasons = list(dict.fromkeys(no_go_reasons))
    return UniversalInboxFileAnalysisPolicy(
        classification=classification_value,
        dsgvo_mode=dsgvo_mode,
        local_only_required=local_only_required,
        api_model_allowed=api_model_allowed,
        local_model_required=local_model_required,
        memory_write_allowed=memory_allowed,
        raptor_write_allowed=memory_allowed,
        requires_review=bool(review_reasons or no_go_reasons),
        review_reasons=tuple(review_reasons),
        no_go_reasons=tuple(no_go_reasons),
    )


def build_universal_inbox_file_analysis_packet(
    payload: Mapping[str, Any] | Any,
    *,
    text_sample: str = "",
    settings: Mapping[str, Any] | None = None,
    requested_classification: str | DataClassification | None = None,
    author_stamp: Mapping[str, Any] | None = None,
) -> UniversalInboxFileAnalysisPacket:
    """Build a redacted analysis packet from metadata plus ephemeral text."""

    item = _payload(payload)
    policy = evaluate_universal_inbox_file_analysis_policy(
        {**dict(item), "text_sample": text_sample},
        requested_classification=requested_classification,
        settings=settings,
    )
    document_type = _infer_document_type(item, text_sample=text_sample)
    abstract = {
        "summary": _safe_summary_for(item, document_type=document_type, policy=policy),
        "document_type": document_type,
        "classification": policy.classification,
        "memory_mode": "abstract_only" if policy.memory_write_allowed else "review_required",
        "source_material_stored": False,
        "local_only": policy.local_only_required,
    }
    metadata = {
        "extractor": str(item.get("extractor") or ""),
        "source_channel": _normalize_token(item.get("source_channel") or item.get("channel") or "unknown", field="source_channel"),
        "analysis_route": "local_model" if policy.local_model_required else ("api_or_local" if policy.api_model_allowed else "none"),
        "author_stamp": coerce_universal_inbox_author_stamp(author_stamp)
        if isinstance(author_stamp, Mapping)
        else build_universal_inbox_author_stamp(
            action="cataloged",
            route="deterministic_policy",
            model_id="deterministic_policy_v1",
            model_provider="odysseus_local",
        ),
    }
    return UniversalInboxFileAnalysisPacket(
        status=policy.status,
        document_type=document_type,
        policy=policy,
        abstract=abstract,
        metadata=metadata,
    )


def _resolve_effective_classification(
    item: Mapping[str, Any],
    *,
    requested_classification: str | DataClassification | None,
) -> DataClassification | None:
    candidates: list[DataClassification] = []
    for value in (
        requested_classification,
        item.get("classification"),
        item.get("ai_classification"),
        _classification_from_source(item),
        _classification_from_hints(item),
    ):
        normalized_value = normalize_memory_classification(value, fallback="")
        resolution = resolve_classification(normalized_value or value)
        if resolution.normalized is not None:
            candidates.append(resolution.normalized)
    if not candidates:
        return None
    return max(candidates, key=lambda value: _classification_rank(value))


def _classification_from_source(item: Mapping[str, Any]) -> str | None:
    source_labels = item.get("source_labels") or item.get("source_label") or ()
    if isinstance(source_labels, str):
        source_labels = (source_labels,)
    if not isinstance(source_labels, (tuple, list)):
        return None
    lowered = " ".join(str(value).lower() for value in source_labels)
    if any(hint in lowered for hint in ("privat", "private", "niklas", "maaike", "sensitive")):
        return "sensitive"
    return None


def _classification_from_hints(item: Mapping[str, Any]) -> str | None:
    text_sample = str(item.get("text_sample") or "")
    if _SECRET_TEXT_RE.search(text_sample):
        return "secret"
    if _SENSITIVE_TEXT_RE.search(text_sample):
        return "sensitive"
    filename = str(item.get("filename") or item.get("relative_path") or "").lower()
    if any(hint in filename for hint in _SENSITIVE_FILENAME_HINTS):
        return "sensitive"
    return None


def _classification_rank(value: DataClassification) -> int:
    return {
        DataClassification.PUBLIC: 1,
        DataClassification.PRIVATE: 2,
        DataClassification.SENSITIVE: 3,
        DataClassification.SECRET: 4,
    }[value]


def _infer_document_type(item: Mapping[str, Any], *, text_sample: str) -> str:
    explicit = str(item.get("document_type") or "").strip().lower()
    if explicit:
        return _normalize_token(
            normalize_memory_document_type(explicit, fallback=explicit, text=text_sample),
            field="document_type",
        )
    haystack = f"{item.get('filename') or ''} {item.get('relative_path') or ''} {text_sample[:500]}".lower()
    if any(hint in haystack for hint in ("rechnung", "invoice", "bill")):
        return "invoice"
    if any(hint in haystack for hint in ("worksheet", "arbeitsblatt", "uebungsblatt", "übungsblatt")):
        return "worksheet"
    if any(hint in haystack for hint in ("vertrag", "contract", "agreement")):
        return "contract"
    if any(hint in haystack for hint in ("projekt", "project", "spec", "planung", "podman", "docker", "roadmap")):
        return "project"
    return "reference"


def _safe_summary_for(
    item: Mapping[str, Any],
    *,
    document_type: str,
    policy: UniversalInboxFileAnalysisPolicy,
) -> str:
    source = str(item.get("source_channel") or item.get("channel") or "unknown").strip() or "unknown"
    return (
        f"{source} file classified as {policy.classification} {document_type}; "
        "only redacted abstraction is eligible for long-term memory."
    )


def _payload(item: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if hasattr(item, "to_dict"):
        value = item.to_dict()
    elif hasattr(item, "__dict__"):
        value = vars(item)
    else:
        value = item
    if not isinstance(value, Mapping):
        raise UniversalInboxAnalysisError("analysis item must be a mapping-like object")
    return value


def _sanitize_mapping(payload: Mapping[str, Any] | Any) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, Mapping):
        raise UniversalInboxAnalysisError("analysis payload must be a mapping")

    sanitized: dict[str, Any] = {}
    blocked_count = 0
    for key, value in payload.items():
        key_text = str(key)
        if _is_forbidden_key(key_text):
            blocked_count += 1
            continue
        clean_value, value_blocked_count = _sanitize_value(value)
        sanitized[key_text] = clean_value
        blocked_count += value_blocked_count
    return sanitized, blocked_count


def _sanitize_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (tuple, list)):
        clean_values: list[Any] = []
        blocked_count = 0
        for item in value:
            clean_item, item_blocked_count = _sanitize_value(item)
            clean_values.append(clean_item)
            blocked_count += item_blocked_count
        return tuple(clean_values), blocked_count
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value, 0
    return str(value), 0


def _is_forbidden_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _FORBIDDEN_ANALYSIS_KEYS:
        return True
    return any(pattern in normalized for pattern in _SENSITIVE_KEY_PATTERNS)


def _normalize_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise UniversalInboxAnalysisError(f"{field} must be a safe token")
    return token
