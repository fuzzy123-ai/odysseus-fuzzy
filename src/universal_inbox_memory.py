"""Offline memory abstraction model for Universal Inbox events.

The model serializes classification abstractions and provenance only. It does
not read files, call providers, or retain raw document/message payload fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


MEMORY_SCHEMA = "odysseus.universal_inbox.memory_abstraction.v1"
RAPTORGRAPH_EVENT_SCHEMA = "odysseus.universal_inbox.raptorgraph_memory_event.v1"

FORBIDDEN_MEMORY_KEYS = frozenset(
    {
        "raw_text",
        "content",
        "body",
        "payload",
        "bytes",
        "binary",
        "ocr_dump",
        "transcript",
        "full_text",
        "page_text",
        "email_body",
        "attachment_bytes",
        "secret",
        "token",
        "password",
        "api_key",
        "credential",
        "chat_id",
    }
)

_SENSITIVE_KEY_PATTERNS = ("secret", "token", "password", "api_key", "credential", "chat_id")
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEX_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{32,128}$")
_UNSAFE_PATH_CHARS = set('<>:"|?*')


class UniversalInboxMemoryError(ValueError):
    """Raised when a memory abstraction would be unsafe to serialize."""


@dataclass(frozen=True)
class UniversalInboxMemoryProvenance:
    source_hash: str
    original_path: str
    planned_path: str
    current_path: str
    routing_policy: str
    confidence: float
    review_status: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "UniversalInboxMemoryProvenance":
        routing_event = _routing_event_payload(payload)
        planned_path = (
            payload.get("planned_path")
            or routing_event.get("planned_path")
            or payload.get("target_path")
            or ""
        )
        current_path = payload.get("current_path") or payload.get("target_path") or planned_path
        review_status = (
            payload.get("review_status")
            or payload.get("ledger_status")
            or payload.get("status")
            or "unknown"
        )
        return cls(
            source_hash=_normalize_source_hash(
                payload.get("source_hash")
                or routing_event.get("source_hash")
                or payload.get("sha256")
                or ""
            ),
            original_path=_normalize_relative_path(
                payload.get("original_path") or routing_event.get("original_path")
            ),
            planned_path=_normalize_relative_path(planned_path),
            current_path=_normalize_relative_path(current_path),
            routing_policy=str(
                payload.get("routing_policy") or routing_event.get("routing_policy") or ""
            ).strip(),
            confidence=_normalize_confidence(
                payload.get(
                    "confidence",
                    routing_event.get("confidence", payload.get("routing_confidence", 0.0)),
                )
            ),
            review_status=_normalize_token(review_status, field="review status"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_hash": self.source_hash,
            "original_path": self.original_path,
            "planned_path": self.planned_path,
            "current_path": self.current_path,
            "routing_policy": self.routing_policy,
            "confidence": self.confidence,
            "review_status": self.review_status,
        }


@dataclass(frozen=True)
class UniversalInboxMemoryAbstraction:
    provenance: UniversalInboxMemoryProvenance
    domain: str
    document_type: str
    title: str = ""
    abstract: Mapping[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    schema: str = MEMORY_SCHEMA
    blocked_field_count: int = 0

    @classmethod
    def from_routing_decision(
        cls,
        decision: Any,
        *,
        abstract: Mapping[str, Any] | None = None,
        tags: tuple[str, ...] | list[str] = (),
    ) -> "UniversalInboxMemoryAbstraction":
        payload = decision.to_dict() if hasattr(decision, "to_dict") else decision
        if not isinstance(payload, Mapping):
            raise UniversalInboxMemoryError("routing decision must be a mapping or expose to_dict()")
        return cls.from_mapping(payload, abstract=abstract, tags=tags)

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        abstract: Mapping[str, Any] | None = None,
        tags: tuple[str, ...] | list[str] = (),
    ) -> "UniversalInboxMemoryAbstraction":
        sanitized_abstract, blocked_count = _sanitize_mapping(
            abstract if abstract is not None else payload.get("abstract") or {}
        )
        routing_event = _routing_event_payload(payload)
        domain = payload.get("domain") or routing_event.get("domain") or "unknown"
        document_type = payload.get("document_type") or routing_event.get("document_type") or "unknown"
        review_reasons, review_blocked_count = _sanitize_sequence(
            payload.get("review_reasons") or ()
        )
        safe_tags, tag_blocked_count = _sanitize_sequence(tags or payload.get("tags") or ())

        return cls(
            provenance=UniversalInboxMemoryProvenance.from_mapping(payload),
            domain=_normalize_token(domain, field="domain"),
            document_type=_normalize_token(document_type, field="document type"),
            title=str(payload.get("title") or routing_event.get("title") or "").strip(),
            abstract=sanitized_abstract,
            tags=tuple(str(tag).strip() for tag in safe_tags if str(tag).strip()),
            review_reasons=tuple(str(reason).strip() for reason in review_reasons if str(reason).strip()),
            blocked_field_count=blocked_count + review_blocked_count + tag_blocked_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "domain": self.domain,
            "document_type": self.document_type,
            "title": self.title,
            "abstract": dict(self.abstract),
            "tags": self.tags,
            "review_reasons": self.review_reasons,
            "provenance": self.provenance.to_dict(),
            "blocked_field_count": self.blocked_field_count,
        }

    def to_raptorgraph_event(self) -> dict[str, Any]:
        provenance = self.provenance.to_dict()
        return {
            "schema": RAPTORGRAPH_EVENT_SCHEMA,
            "event": "universal_inbox_memory_abstraction",
            "memory_schema": self.schema,
            "source_provider": "universal_inbox",
            "domain": self.domain,
            "document_type": self.document_type,
            "title": self.title,
            "abstract": dict(self.abstract),
            "tags": self.tags,
            "review_reasons": self.review_reasons,
            "source_hash": provenance["source_hash"],
            "original_path": provenance["original_path"],
            "planned_path": provenance["planned_path"],
            "current_path": provenance["current_path"],
            "routing_policy": provenance["routing_policy"],
            "confidence": provenance["confidence"],
            "review_status": provenance["review_status"],
            "provenance": provenance,
            "blocked_field_count": self.blocked_field_count,
        }


def to_raptorgraph_event(
    memory: UniversalInboxMemoryAbstraction | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a RaptorGraph event from a memory abstraction or safe mapping."""

    abstraction = (
        memory
        if isinstance(memory, UniversalInboxMemoryAbstraction)
        else UniversalInboxMemoryAbstraction.from_mapping(memory)
    )
    return abstraction.to_raptorgraph_event()


def _sanitize_mapping(payload: Mapping[str, Any] | Any) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, Mapping):
        raise UniversalInboxMemoryError("abstract must be a mapping")

    sanitized: dict[str, Any] = {}
    blocked_count = 0
    for key, value in payload.items():
        key_text = str(key)
        if _is_forbidden_key(key_text):
            blocked_count += 1
            continue
        clean_value, value_blocked_count = _sanitize_value(value)
        blocked_count += value_blocked_count
        sanitized[key_text] = clean_value
    return sanitized, blocked_count


def _routing_event_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    routing_event = payload.get("raptorgraph_event") or {}
    return routing_event if isinstance(routing_event, Mapping) else {}


def _sanitize_sequence(values: Any) -> tuple[tuple[Any, ...], int]:
    if isinstance(values, str):
        return (values,), 0
    if not isinstance(values, (tuple, list)):
        return (), 0

    sanitized: list[Any] = []
    blocked_count = 0
    for value in values:
        clean_value, value_blocked_count = _sanitize_value(value)
        sanitized.append(clean_value)
        blocked_count += value_blocked_count
    return tuple(sanitized), blocked_count


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
    if normalized in FORBIDDEN_MEMORY_KEYS:
        return True
    return any(pattern in normalized for pattern in _SENSITIVE_KEY_PATTERNS)


def _normalize_source_hash(value: Any) -> str:
    source_hash = str(value or "").strip()
    if not source_hash:
        raise UniversalInboxMemoryError("source_hash is required")
    if not _HEX_RE.fullmatch(source_hash):
        raise UniversalInboxMemoryError("source_hash must be a sha256-like hex digest")
    return source_hash.lower()


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise UniversalInboxMemoryError("path is required")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise UniversalInboxMemoryError("path must be relative")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() != "."]
    if not parts or any(part == ".." for part in parts):
        raise UniversalInboxMemoryError("path must not contain traversal segments")
    for part in parts:
        if any(ord(ch) < 32 for ch in part):
            raise UniversalInboxMemoryError("path contains control characters")
        if any(ch in _UNSAFE_PATH_CHARS for ch in part):
            raise UniversalInboxMemoryError("path contains unsafe segment")
    return "/".join(parts)


def _normalize_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _SAFE_TOKEN_RE.fullmatch(token):
        raise UniversalInboxMemoryError(f"{field} must be a safe token")
    return token


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise UniversalInboxMemoryError("confidence must be numeric") from None
    if confidence < 0 or confidence > 1:
        raise UniversalInboxMemoryError("confidence must be between 0 and 1")
    return confidence
