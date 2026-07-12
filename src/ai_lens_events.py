"""Bounded, redacted AI Lens event contract.

This module defines schema v1 only.  It deliberately has no persistence,
streaming, provider, or runtime-hook behavior.  Events describe either an
observed runtime fact or an explicitly labelled deterministic fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


AI_LENS_EVENT_SCHEMA = "odysseus.ai_lens.event.v1"
MAX_PAYLOAD_BYTES = 16_384
MAX_EVENT_BYTES = 24_576
MAX_PAYLOAD_DEPTH = 5
MAX_PAYLOAD_FIELDS = 32
MAX_PAYLOAD_LIST_ITEMS = 50
MAX_SOURCE_REFS = 8
MAX_PREVIEW_CHARS = 160
MAX_SUMMARY_CHARS = 240
MAX_EVENT_BATCH = 256


class AiLensEventError(ValueError):
    """Raised when an AI Lens event is invalid or unsafe to expose."""


class AiLensEventType(StrEnum):
    LENS_SESSION_STARTED = "lens_session_started"
    QUERY_RECEIVED = "query_received"
    EMBEDDING_CREATED = "embedding_created"
    MEMORY_SEARCH_STARTED = "memory_search_started"
    MEMORY_HIT = "memory_hit"
    RAG_SEARCH_STARTED = "rag_search_started"
    RAG_HIT = "rag_hit"
    CONTEXT_ITEM_SELECTED = "context_item_selected"
    CONTEXT_ITEM_EXCLUDED = "context_item_excluded"
    CONTEXT_PACK_COMPOSED = "context_pack_composed"
    MODEL_ROUTE_SELECTED = "model_route_selected"
    MODEL_STREAM_STARTED = "model_stream_started"
    MODEL_STREAM_DELTA = "model_stream_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_RESULT = "tool_call_result"
    SAFETY_GATE_TRIGGERED = "safety_gate_triggered"
    RETRIEVAL_RANKING_SUMMARY = "retrieval_ranking_summary"
    SOURCE_COVERAGE_SUMMARY = "source_coverage_summary"
    SOURCE_CONFLICT_DETECTED = "source_conflict_detected"
    CONTEXT_BUDGET_UPDATED = "context_budget_updated"
    ANSWER_PROVENANCE_SUMMARY = "answer_provenance_summary"
    ANSWER_COMPLETED = "answer_completed"
    LENS_REPLAY_SNAPSHOT_SAVED = "lens_replay_snapshot_saved"
    LOCAL_MODEL_INTERNAL_SAMPLE = "local_model_internal_sample"


class AiLensPhase(StrEnum):
    SESSION = "session"
    INPUT = "input"
    EMBEDDING = "embedding"
    RETRIEVAL = "retrieval"
    CONTEXT = "context"
    MODEL = "model"
    TOOL = "tool"
    SAFETY = "safety"
    RESPONSE = "response"
    REPLAY = "replay"
    LOCAL_MODEL = "local_model"


class AiLensStatus(StrEnum):
    QUEUED = "queued"
    RECEIVED = "received"
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    WARNING = "warning"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class AiLensTruthLevel(StrEnum):
    RUNTIME_TRACE = "runtime_trace"
    SEMANTIC_PROJECTION = "semantic_projection"
    LOCAL_MODEL_INTERNALS = "local_model_internals"
    VISUAL_EFFECT = "visual_effect"


class AiLensObservationOrigin(StrEnum):
    RUNTIME_OBSERVATION = "runtime_observation"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class AiLensPrivacyLevel(StrEnum):
    PUBLIC = "public"
    METADATA = "metadata"
    PRIVATE_METADATA = "private_metadata"
    SENSITIVE_METADATA = "sensitive_metadata"
    DSGVO_LOCAL = "dsgvo_local"


class AiLensRedactionLevel(StrEnum):
    NONE = "none"
    METADATA_ONLY = "metadata_only"
    REDACTED = "redacted"
    HASHED = "hashed"
    LOCAL_ONLY = "local_only"


class AiLensSourceKind(StrEnum):
    SESSION = "session"
    QUERY = "query"
    MEMORY = "memory"
    RAG = "rag"
    DOCUMENT = "document"
    MODEL = "model"
    TOOL = "tool"
    POLICY = "policy"
    ANSWER = "answer"
    EVENT = "event"
    FIXTURE = "fixture"
    LOCAL_MODEL = "local_model"


PHASE_BY_EVENT_TYPE: Mapping[AiLensEventType, AiLensPhase] = MappingProxyType({
    AiLensEventType.LENS_SESSION_STARTED: AiLensPhase.SESSION,
    AiLensEventType.QUERY_RECEIVED: AiLensPhase.INPUT,
    AiLensEventType.EMBEDDING_CREATED: AiLensPhase.EMBEDDING,
    AiLensEventType.MEMORY_SEARCH_STARTED: AiLensPhase.RETRIEVAL,
    AiLensEventType.MEMORY_HIT: AiLensPhase.RETRIEVAL,
    AiLensEventType.RAG_SEARCH_STARTED: AiLensPhase.RETRIEVAL,
    AiLensEventType.RAG_HIT: AiLensPhase.RETRIEVAL,
    AiLensEventType.CONTEXT_ITEM_SELECTED: AiLensPhase.CONTEXT,
    AiLensEventType.CONTEXT_ITEM_EXCLUDED: AiLensPhase.CONTEXT,
    AiLensEventType.CONTEXT_PACK_COMPOSED: AiLensPhase.CONTEXT,
    AiLensEventType.MODEL_ROUTE_SELECTED: AiLensPhase.MODEL,
    AiLensEventType.MODEL_STREAM_STARTED: AiLensPhase.MODEL,
    AiLensEventType.MODEL_STREAM_DELTA: AiLensPhase.MODEL,
    AiLensEventType.TOOL_CALL_STARTED: AiLensPhase.TOOL,
    AiLensEventType.TOOL_CALL_RESULT: AiLensPhase.TOOL,
    AiLensEventType.SAFETY_GATE_TRIGGERED: AiLensPhase.SAFETY,
    AiLensEventType.RETRIEVAL_RANKING_SUMMARY: AiLensPhase.RETRIEVAL,
    AiLensEventType.SOURCE_COVERAGE_SUMMARY: AiLensPhase.RETRIEVAL,
    AiLensEventType.SOURCE_CONFLICT_DETECTED: AiLensPhase.RETRIEVAL,
    AiLensEventType.CONTEXT_BUDGET_UPDATED: AiLensPhase.CONTEXT,
    AiLensEventType.ANSWER_PROVENANCE_SUMMARY: AiLensPhase.RESPONSE,
    AiLensEventType.ANSWER_COMPLETED: AiLensPhase.RESPONSE,
    AiLensEventType.LENS_REPLAY_SNAPSHOT_SAVED: AiLensPhase.REPLAY,
    AiLensEventType.LOCAL_MODEL_INTERNAL_SAMPLE: AiLensPhase.LOCAL_MODEL,
})


_DEFAULT_STATUS_BY_TYPE: Mapping[AiLensEventType, AiLensStatus] = MappingProxyType({
    AiLensEventType.LENS_SESSION_STARTED: AiLensStatus.STARTED,
    AiLensEventType.QUERY_RECEIVED: AiLensStatus.RECEIVED,
    AiLensEventType.MEMORY_SEARCH_STARTED: AiLensStatus.STARTED,
    AiLensEventType.RAG_SEARCH_STARTED: AiLensStatus.STARTED,
    AiLensEventType.MODEL_STREAM_STARTED: AiLensStatus.STARTED,
    AiLensEventType.MODEL_STREAM_DELTA: AiLensStatus.RUNNING,
    AiLensEventType.TOOL_CALL_STARTED: AiLensStatus.STARTED,
    AiLensEventType.SAFETY_GATE_TRIGGERED: AiLensStatus.WARNING,
    AiLensEventType.SOURCE_CONFLICT_DETECTED: AiLensStatus.WARNING,
    AiLensEventType.ANSWER_COMPLETED: AiLensStatus.SUCCEEDED,
})

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$")
_PRIVATE_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/(?:home|Users|var/lib|mnt|srv)/)", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"(?:authorization\s*:|bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+|"
    r"\bsk-[A-Za-z0-9_-]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)
_FORBIDDEN_CONTENT_MARKERS = (
    "raw private content",
    "private raw text",
    "raw provider output",
    "full prompt",
)
_FORBIDDEN_FIELD_NAMES = frozenset({
    "rawsecret",
    "secret",
    "clientsecret",
    "token",
    "accesstoken",
    "refreshtoken",
    "password",
    "credential",
    "privatekey",
    "apikey",
    "authorization",
    "authorizationheader",
    "cookie",
    "sessioncookie",
    "rawprivatecontent",
    "privatecontent",
    "rawcontent",
    "privatedocumenttext",
    "prompt",
    "rawprompt",
    "fullprompt",
    "provideroutput",
    "rawprovideroutput",
    "rawoutput",
    "tooloutput",
    "rawarguments",
    "arguments",
    "absolutepath",
    "absoluteprivatepath",
    "absoluteprivatehostpath",
    "chatid",
    "telegramchatid",
    "emailbody",
    "messagetext",
    "documenttext",
    "unredactedtooloutput",
    "rawpreview",
})


def _enum(value: Any, enum_type: type[StrEnum], *, field_name: str) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value or "").strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise AiLensEventError(f"{field_name} must be one of: {allowed}") from exc


def _safe_id(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AiLensEventError(f"{field_name} must not be empty")
    _reject_unsafe_string(text, field_name=field_name)
    if not _SAFE_ID_RE.fullmatch(text):
        raise AiLensEventError(f"{field_name} must be a bounded safe identifier")
    return text


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise AiLensEventError("created_at must be an explicit timestamp")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AiLensEventError("created_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AiLensEventError("created_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _reject_unsafe_string(value: str, *, field_name: str) -> None:
    lowered = value.lower()
    if _PRIVATE_PATH_RE.search(value):
        raise AiLensEventError(f"{field_name} contains a private absolute path")
    if _SECRET_VALUE_RE.search(value):
        raise AiLensEventError(f"{field_name} contains secret or token material")
    if any(marker in lowered for marker in _FORBIDDEN_CONTENT_MARKERS):
        raise AiLensEventError(f"{field_name} contains forbidden private/raw content")


def _safe_text(value: Any, *, field_name: str, max_chars: int, truncate: bool = False) -> str:
    text = " ".join(str(value or "").split())
    _reject_unsafe_string(text, field_name=field_name)
    if len(text) > max_chars:
        if not truncate:
            raise AiLensEventError(f"{field_name} exceeds max length {max_chars}")
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def redacted_preview(value: Any) -> str:
    """Return a whitespace-normalized bounded preview after safety checks.

    The helper does not pretend it can infer privacy.  Obvious secret, raw
    content, and private-path input is rejected rather than silently exposed.
    """

    return _safe_text(value, field_name="redacted_preview", max_chars=MAX_PREVIEW_CHARS, truncate=True)


def _freeze_payload(value: Any, *, key: str = "payload", depth: int = 0) -> Any:
    if depth > MAX_PAYLOAD_DEPTH:
        raise AiLensEventError(f"{key} exceeds max payload depth {MAX_PAYLOAD_DEPTH}")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 1_000_000_000_000:
            raise AiLensEventError(f"{key} numeric value is out of bounds")
        return value
    if isinstance(value, float):
        if not (-1_000_000_000_000 <= value <= 1_000_000_000_000):
            raise AiLensEventError(f"{key} numeric value is out of bounds")
        return value
    if isinstance(value, str):
        limit = MAX_PREVIEW_CHARS if _normalized_key(key) == "redactedpreview" else MAX_SUMMARY_CHARS
        return _safe_text(
            value,
            field_name=key,
            max_chars=limit,
            truncate=_normalized_key(key) == "redactedpreview",
        )
    if isinstance(value, Mapping):
        if len(value) > MAX_PAYLOAD_FIELDS:
            raise AiLensEventError(f"{key} exceeds max field count {MAX_PAYLOAD_FIELDS}")
        result: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            safe_key = str(raw_key or "").strip()
            if not safe_key or len(safe_key) > 64 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", safe_key):
                raise AiLensEventError(f"{key} contains an invalid field name")
            if _normalized_key(safe_key) in _FORBIDDEN_FIELD_NAMES:
                raise AiLensEventError(f"{key}.{safe_key} is a forbidden raw/secret field")
            result[safe_key] = _freeze_payload(value[raw_key], key=f"{key}.{safe_key}", depth=depth + 1)
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_PAYLOAD_LIST_ITEMS:
            raise AiLensEventError(f"{key} exceeds max list length {MAX_PAYLOAD_LIST_ITEMS}")
        return tuple(_freeze_payload(item, key=key, depth=depth + 1) for item in value)
    raise AiLensEventError(f"{key} contains a non-JSON value")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class AiLensSourceRef:
    source_id: str
    kind: AiLensSourceKind
    redaction_level: AiLensRedactionLevel
    redacted_preview: str = ""

    @classmethod
    def create(
        cls,
        *,
        source_id: Any,
        kind: AiLensSourceKind | str,
        redaction_level: AiLensRedactionLevel | str = AiLensRedactionLevel.METADATA_ONLY,
        redacted_preview: Any = "",
    ) -> "AiLensSourceRef":
        normalized_redaction = _enum(
            redaction_level, AiLensRedactionLevel, field_name="source_ref.redaction_level"
        )
        preview = globals()["redacted_preview"](redacted_preview) if redacted_preview else ""
        if preview and normalized_redaction == AiLensRedactionLevel.NONE:
            raise AiLensEventError("source_ref preview requires a redaction level")
        return cls(
            source_id=_safe_id(source_id, field_name="source_ref.source_id"),
            kind=_enum(kind, AiLensSourceKind, field_name="source_ref.kind"),
            redaction_level=normalized_redaction,
            redacted_preview=preview,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AiLensSourceRef":
        if not isinstance(value, Mapping):
            raise AiLensEventError("source_ref must be an object")
        return cls.create(
            source_id=value.get("source_id"),
            kind=value.get("kind"),
            redaction_level=value.get("redaction_level", AiLensRedactionLevel.METADATA_ONLY),
            redacted_preview=value.get("redacted_preview", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "redaction_level": self.redaction_level.value,
            "redacted_preview": self.redacted_preview,
        }


@dataclass(frozen=True, slots=True)
class AiLensEvent:
    event_id: str
    session_id: str
    turn_id: str
    sequence: int
    created_at: datetime
    event_type: AiLensEventType
    phase: AiLensPhase
    status: AiLensStatus
    truth_level: AiLensTruthLevel
    observation_origin: AiLensObservationOrigin
    privacy_level: AiLensPrivacyLevel
    redaction_level: AiLensRedactionLevel
    source_refs: tuple[AiLensSourceRef, ...] = ()
    summary: str = ""
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    model_id: str = ""
    latency_ms: int = 0

    @classmethod
    def create(
        cls,
        *,
        event_id: Any,
        session_id: Any,
        turn_id: Any,
        sequence: Any,
        created_at: Any,
        event_type: AiLensEventType | str,
        phase: AiLensPhase | str | None = None,
        status: AiLensStatus | str | None = None,
        truth_level: AiLensTruthLevel | str = AiLensTruthLevel.RUNTIME_TRACE,
        observation_origin: AiLensObservationOrigin | str = AiLensObservationOrigin.RUNTIME_OBSERVATION,
        privacy_level: AiLensPrivacyLevel | str = AiLensPrivacyLevel.METADATA,
        redaction_level: AiLensRedactionLevel | str = AiLensRedactionLevel.METADATA_ONLY,
        source_refs: Iterable[AiLensSourceRef | Mapping[str, Any]] = (),
        source_ref: AiLensSourceRef | Mapping[str, Any] | None = None,
        summary: Any = "",
        payload: Mapping[str, Any] | None = None,
        model_id: Any = "",
        latency_ms: Any = 0,
    ) -> "AiLensEvent":
        normalized_type = _enum(event_type, AiLensEventType, field_name="event_type")
        expected_phase = PHASE_BY_EVENT_TYPE[normalized_type]
        normalized_phase = expected_phase if phase is None else _enum(phase, AiLensPhase, field_name="phase")
        if normalized_phase != expected_phase:
            raise AiLensEventError(
                f"phase {normalized_phase.value} does not match {normalized_type.value} ({expected_phase.value})"
            )
        normalized_status = (
            _DEFAULT_STATUS_BY_TYPE.get(normalized_type, AiLensStatus.COMPLETED)
            if status is None
            else _enum(status, AiLensStatus, field_name="status")
        )
        normalized_truth = _enum(truth_level, AiLensTruthLevel, field_name="truth_level")
        normalized_origin = _enum(
            observation_origin, AiLensObservationOrigin, field_name="observation_origin"
        )
        normalized_privacy = _enum(privacy_level, AiLensPrivacyLevel, field_name="privacy_level")
        normalized_redaction = _enum(redaction_level, AiLensRedactionLevel, field_name="redaction_level")

        try:
            normalized_sequence = int(sequence)
        except (TypeError, ValueError) as exc:
            raise AiLensEventError("sequence must be a positive integer") from exc
        if normalized_sequence < 1:
            raise AiLensEventError("sequence must be a positive integer")
        try:
            normalized_latency = int(latency_ms or 0)
        except (TypeError, ValueError) as exc:
            raise AiLensEventError("latency_ms must be a non-negative integer") from exc
        if normalized_latency < 0 or normalized_latency > 86_400_000:
            raise AiLensEventError("latency_ms is out of bounds")

        normalized_refs: list[AiLensSourceRef] = []
        combined_refs = ([source_ref] if source_ref is not None else []) + list(source_refs)
        for item in combined_refs:
            if isinstance(item, AiLensSourceRef):
                normalized_refs.append(AiLensSourceRef.create(
                    source_id=item.source_id,
                    kind=item.kind,
                    redaction_level=item.redaction_level,
                    redacted_preview=item.redacted_preview,
                ))
            elif isinstance(item, Mapping):
                normalized_refs.append(AiLensSourceRef.from_dict(item))
            else:
                raise AiLensEventError("source_refs must contain AiLensSourceRef objects")
        if len(normalized_refs) > MAX_SOURCE_REFS:
            raise AiLensEventError(f"source_refs must not exceed {MAX_SOURCE_REFS}")
        if len({item.source_id for item in normalized_refs}) != len(normalized_refs):
            raise AiLensEventError("source_refs must have unique source_id values")

        if payload is not None and not isinstance(payload, Mapping):
            raise AiLensEventError("payload must be an object")
        frozen_payload = _freeze_payload(payload or {})
        payload_dict = _thaw(frozen_payload)
        payload_bytes = len(json.dumps(payload_dict, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if payload_bytes > MAX_PAYLOAD_BYTES:
            raise AiLensEventError(f"payload exceeds max JSON size {MAX_PAYLOAD_BYTES}")

        if normalized_privacy not in {AiLensPrivacyLevel.PUBLIC, AiLensPrivacyLevel.METADATA}:
            if normalized_redaction == AiLensRedactionLevel.NONE:
                raise AiLensEventError("private event metadata requires redaction")
            if any(item.redaction_level == AiLensRedactionLevel.NONE for item in normalized_refs):
                raise AiLensEventError("private event source_refs require redaction")

        if normalized_truth == AiLensTruthLevel.LOCAL_MODEL_INTERNALS:
            if normalized_type != AiLensEventType.LOCAL_MODEL_INTERNAL_SAMPLE:
                raise AiLensEventError("local_model_internals truth is only valid for local model samples")
            if normalized_origin != AiLensObservationOrigin.RUNTIME_OBSERVATION:
                raise AiLensEventError("local model internals cannot be synthetic fixture data")
            if payload_dict.get("local_runtime_observed") is not True:
                raise AiLensEventError("local model internals require local_runtime_observed=true")
        elif normalized_type == AiLensEventType.LOCAL_MODEL_INTERNAL_SAMPLE:
            raise AiLensEventError("local model samples require local_model_internals truth")

        event = cls(
            event_id=_safe_id(event_id, field_name="event_id"),
            session_id=_safe_id(session_id, field_name="session_id"),
            turn_id=_safe_id(turn_id, field_name="turn_id"),
            sequence=normalized_sequence,
            created_at=_parse_timestamp(created_at),
            event_type=normalized_type,
            phase=normalized_phase,
            status=normalized_status,
            truth_level=normalized_truth,
            observation_origin=normalized_origin,
            privacy_level=normalized_privacy,
            redaction_level=normalized_redaction,
            source_refs=tuple(normalized_refs),
            summary=_safe_text(summary, field_name="summary", max_chars=MAX_SUMMARY_CHARS),
            payload=frozen_payload,
            model_id=_safe_id(model_id, field_name="model_id") if model_id else "",
            latency_ms=normalized_latency,
        )
        encoded_size = len(event.to_json().encode("utf-8"))
        if encoded_size > MAX_EVENT_BYTES:
            raise AiLensEventError(f"event exceeds max JSON size {MAX_EVENT_BYTES}")
        return event

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AiLensEvent":
        if not isinstance(value, Mapping):
            raise AiLensEventError("event must be an object")
        if value.get("schema") != AI_LENS_EVENT_SCHEMA:
            raise AiLensEventError("unsupported AI Lens event schema")
        if value.get("raw_content_visible") not in (None, False):
            raise AiLensEventError("raw_content_visible must be false")
        source_refs = value.get("source_refs") or ()
        source_ref = value.get("source_ref")
        if source_ref and source_refs:
            first = source_refs[0] if isinstance(source_refs, Sequence) and source_refs else None
            if first != source_ref:
                raise AiLensEventError("source_ref must match the first source_refs item")
            source_ref = None
        return cls.create(
            event_id=value.get("event_id"),
            session_id=value.get("session_id"),
            turn_id=value.get("turn_id"),
            sequence=value.get("sequence"),
            created_at=value.get("created_at"),
            event_type=value.get("event_type"),
            phase=value.get("phase"),
            status=value.get("status"),
            truth_level=value.get("truth_level"),
            observation_origin=value.get("observation_origin"),
            privacy_level=value.get("privacy_level"),
            redaction_level=value.get("redaction_level"),
            source_ref=source_ref,
            source_refs=source_refs,
            summary=value.get("summary", ""),
            payload=value.get("payload") or {},
            model_id=value.get("model_id", ""),
            latency_ms=value.get("latency_ms", 0),
        )

    def to_dict(self) -> dict[str, Any]:
        refs = [item.to_dict() for item in self.source_refs]
        return {
            "schema": AI_LENS_EVENT_SCHEMA,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "created_at": _timestamp_text(self.created_at),
            "event_type": self.event_type.value,
            "phase": self.phase.value,
            "status": self.status.value,
            "truth_level": self.truth_level.value,
            "observation_origin": self.observation_origin.value,
            "privacy_level": self.privacy_level.value,
            "redaction_level": self.redaction_level.value,
            "source_ref": refs[0] if refs else None,
            "source_refs": refs,
            "summary": self.summary,
            "payload": _thaw(self.payload),
            "model_id": self.model_id,
            "latency_ms": self.latency_ms,
            "raw_content_visible": False,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_ai_lens_event(value: AiLensEvent | Mapping[str, Any]) -> AiLensEvent:
    """Validate an event instance or serialized mapping and return a safe event."""

    if isinstance(value, AiLensEvent):
        return AiLensEvent.from_dict(value.to_dict())
    return AiLensEvent.from_dict(value)


def events_to_json(events: Iterable[AiLensEvent]) -> str:
    normalized = validate_event_batch(events)
    return json.dumps(
        [event.to_dict() for event in normalized],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_event_batch(events: Iterable[AiLensEvent | Mapping[str, Any]]) -> tuple[AiLensEvent, ...]:
    """Validate bounds, identity, and per-turn ordering for one event batch."""

    normalized = tuple(validate_ai_lens_event(event) for event in events)
    if len(normalized) > MAX_EVENT_BATCH:
        raise AiLensEventError(f"event batch must not exceed {MAX_EVENT_BATCH}")
    event_ids: set[str] = set()
    last_by_turn: dict[tuple[str, str], tuple[int, datetime]] = {}
    for event in normalized:
        if event.event_id in event_ids:
            raise AiLensEventError("event batch contains duplicate event_id")
        event_ids.add(event.event_id)
        stream_key = (event.session_id, event.turn_id)
        previous = last_by_turn.get(stream_key)
        if previous is not None:
            previous_sequence, previous_timestamp = previous
            if event.sequence <= previous_sequence:
                raise AiLensEventError("event sequence must increase within each session turn")
            if event.created_at < previous_timestamp:
                raise AiLensEventError("event timestamps must not move backwards within each session turn")
        last_by_turn[stream_key] = (event.sequence, event.created_at)
    return normalized


def deterministic_fixture_events() -> tuple[AiLensEvent, ...]:
    """Build a stable, explicitly synthetic trace for UI and contract tests."""

    base = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    common = {
        "session_id": "fixture-session-001",
        "turn_id": "fixture-turn-001",
        "observation_origin": AiLensObservationOrigin.SYNTHETIC_FIXTURE,
        "truth_level": AiLensTruthLevel.RUNTIME_TRACE,
        "privacy_level": AiLensPrivacyLevel.METADATA,
        "redaction_level": AiLensRedactionLevel.REDACTED,
    }
    definitions: tuple[dict[str, Any], ...] = (
        {
            "event_type": AiLensEventType.LENS_SESSION_STARTED,
            "summary": "Deterministic AI Lens fixture session started.",
            "payload": {"fixture": True, "schema_version": 1},
        },
        {
            "event_type": AiLensEventType.QUERY_RECEIVED,
            "summary": "A bounded fixture query was received.",
            "source_refs": (AiLensSourceRef.create(
                source_id="fixture-query-001",
                kind=AiLensSourceKind.FIXTURE,
                redaction_level=AiLensRedactionLevel.REDACTED,
                redacted_preview="Explain the selected project context.",
            ),),
            "payload": {"fixture": True, "input_chars": 37},
        },
        {
            "event_type": AiLensEventType.MEMORY_SEARCH_STARTED,
            "summary": "Fixture memory search started.",
            "payload": {"fixture": True, "candidate_budget": 8},
        },
        {
            "event_type": AiLensEventType.MEMORY_HIT,
            "summary": "One redacted fixture memory matched.",
            "source_refs": (AiLensSourceRef.create(
                source_id="fixture-memory-001",
                kind=AiLensSourceKind.MEMORY,
                redaction_level=AiLensRedactionLevel.REDACTED,
                redacted_preview="[redacted fixture memory summary]",
            ),),
            "payload": {"fixture": True, "rank": 1, "score": 0.91},
            "latency_ms": 12,
        },
        {
            "event_type": AiLensEventType.CONTEXT_PACK_COMPOSED,
            "summary": "Fixture context pack was composed within budget.",
            "payload": {"fixture": True, "included_count": 1, "excluded_count": 2, "used_tokens": 144},
        },
        {
            "event_type": AiLensEventType.MODEL_ROUTE_SELECTED,
            "summary": "Fixture model route selected.",
            "model_id": "fixture-model",
            "payload": {"fixture": True, "route_kind": "fixture_only", "local_internals_available": False},
        },
        {
            "event_type": AiLensEventType.TOOL_CALL_STARTED,
            "summary": "Fixture read-only tool call started.",
            "source_refs": (AiLensSourceRef.create(
                source_id="fixture-tool-001",
                kind=AiLensSourceKind.TOOL,
                redaction_level=AiLensRedactionLevel.METADATA_ONLY,
            ),),
            "payload": {"fixture": True, "tool_kind": "read_only"},
        },
        {
            "event_type": AiLensEventType.TOOL_CALL_RESULT,
            "status": AiLensStatus.SUCCEEDED,
            "summary": "Fixture tool call completed without raw output.",
            "source_refs": (AiLensSourceRef.create(
                source_id="fixture-tool-001",
                kind=AiLensSourceKind.TOOL,
                redaction_level=AiLensRedactionLevel.METADATA_ONLY,
            ),),
            "payload": {"fixture": True, "result_count": 1, "result_redacted": True},
            "latency_ms": 9,
        },
        {
            "event_type": AiLensEventType.ANSWER_COMPLETED,
            "summary": "Fixture answer completed with bounded provenance.",
            "source_refs": (AiLensSourceRef.create(
                source_id="fixture-answer-001",
                kind=AiLensSourceKind.ANSWER,
                redaction_level=AiLensRedactionLevel.METADATA_ONLY,
            ),),
            "payload": {"fixture": True, "supporting_source_count": 1, "unsupported_segment_count": 0},
            "latency_ms": 84,
        },
    )
    return tuple(
        AiLensEvent.create(
            event_id=f"fixture-event-{index:03d}",
            sequence=index,
            created_at=base + timedelta(milliseconds=(index - 1) * 25),
            **common,
            **definition,
        )
        for index, definition in enumerate(definitions, start=1)
    )


# Clear, discoverable aliases for callers that prefer build/generate wording.
build_ai_lens_event_fixture = deterministic_fixture_events
generate_fixture_events = deterministic_fixture_events
build_fixture_events = deterministic_fixture_events
validate_event = validate_ai_lens_event

# Concise public aliases keep the contract ergonomic without duplicating types.
EventType = AiLensEventType
EventPhase = AiLensPhase
EventStatus = AiLensStatus
TruthLevel = AiLensTruthLevel
ObservationOrigin = AiLensObservationOrigin
PrivacyLevel = AiLensPrivacyLevel
RedactionLevel = AiLensRedactionLevel
SourceKind = AiLensSourceKind
SourceRef = AiLensSourceRef
