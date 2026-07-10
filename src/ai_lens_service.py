"""Thread-safe, bounded in-memory snapshot service for AI Lens events.

The service is intentionally read-only with respect to external systems: it
does not persist, stream, enrich, or call providers.  Its only mutation is the
bounded in-memory ingestion/clear lifecycle exposed by this contract.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
from threading import RLock
from typing import Any, Callable, Iterable, Mapping

from src.ai_lens_events import (
    AiLensEvent,
    AiLensEventError,
    AiLensEventType,
    AiLensObservationOrigin,
    AiLensPrivacyLevel,
    AiLensRedactionLevel,
    AiLensSourceRef,
    AiLensTruthLevel,
    deterministic_fixture_events,
    validate_ai_lens_event,
    validate_event_batch,
)


AI_LENS_SNAPSHOT_SCHEMA = "odysseus.ai_lens.snapshot.v1"
AI_LENS_SESSION_SUMMARY_SCHEMA = "odysseus.ai_lens.session_summary.v1"

HARD_MAX_SESSIONS = 256
HARD_MAX_EVENTS_PER_SESSION = 4_096
HARD_MAX_BYTES_PER_SESSION = 16 * 1024 * 1024
HARD_MAX_SNAPSHOT_EVENTS = 1_024
HARD_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MIN_SNAPSHOT_BYTES = 4_096


class AiLensServiceError(ValueError):
    """Base error for invalid or unsafe snapshot service operations."""


class AiLensSessionNotFoundError(AiLensServiceError):
    """Raised when a requested in-memory session does not exist."""


def opaque_ai_lens_ref(kind: str, value: Any) -> str:
    """Return a stable opaque reference without exposing the input value."""

    normalized_kind = str(kind or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", normalized_kind):
        raise AiLensServiceError("opaque reference kind is invalid")
    raw = str(value or "")
    if not raw:
        raise AiLensServiceError("opaque reference input must not be empty")
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"{normalized_kind}:sha256:{digest}"


class AiLensServiceMode(StrEnum):
    RUNTIME = "runtime"
    FIXTURE = "fixture"


@dataclass(frozen=True, slots=True)
class AiLensServiceLimits:
    max_sessions: int = 32
    max_events_per_session: int = 256
    max_bytes_per_session: int = 2 * 1024 * 1024
    max_snapshot_events: int = 128
    max_snapshot_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        _bounded_int(self.max_sessions, field_name="max_sessions", minimum=1, maximum=HARD_MAX_SESSIONS)
        _bounded_int(
            self.max_events_per_session,
            field_name="max_events_per_session",
            minimum=1,
            maximum=HARD_MAX_EVENTS_PER_SESSION,
        )
        _bounded_int(
            self.max_bytes_per_session,
            field_name="max_bytes_per_session",
            minimum=MIN_SNAPSHOT_BYTES,
            maximum=HARD_MAX_BYTES_PER_SESSION,
        )
        _bounded_int(
            self.max_snapshot_events,
            field_name="max_snapshot_events",
            minimum=1,
            maximum=HARD_MAX_SNAPSHOT_EVENTS,
        )
        _bounded_int(
            self.max_snapshot_bytes,
            field_name="max_snapshot_bytes",
            minimum=MIN_SNAPSHOT_BYTES,
            maximum=HARD_MAX_SNAPSHOT_BYTES,
        )
        if self.max_snapshot_events > self.max_events_per_session:
            raise AiLensServiceError("max_snapshot_events must not exceed max_events_per_session")

    @classmethod
    def create(
        cls,
        *,
        max_sessions: Any = 32,
        max_events_per_session: Any = 256,
        max_bytes_per_session: Any = 2 * 1024 * 1024,
        max_snapshot_events: Any = 128,
        max_snapshot_bytes: Any = 1024 * 1024,
    ) -> "AiLensServiceLimits":
        return cls(
            max_sessions=_bounded_int(
                max_sessions, field_name="max_sessions", minimum=1, maximum=HARD_MAX_SESSIONS
            ),
            max_events_per_session=_bounded_int(
                max_events_per_session,
                field_name="max_events_per_session",
                minimum=1,
                maximum=HARD_MAX_EVENTS_PER_SESSION,
            ),
            max_bytes_per_session=_bounded_int(
                max_bytes_per_session,
                field_name="max_bytes_per_session",
                minimum=MIN_SNAPSHOT_BYTES,
                maximum=HARD_MAX_BYTES_PER_SESSION,
            ),
            max_snapshot_events=_bounded_int(
                max_snapshot_events,
                field_name="max_snapshot_events",
                minimum=1,
                maximum=HARD_MAX_SNAPSHOT_EVENTS,
            ),
            max_snapshot_bytes=_bounded_int(
                max_snapshot_bytes,
                field_name="max_snapshot_bytes",
                minimum=MIN_SNAPSHOT_BYTES,
                maximum=HARD_MAX_SNAPSHOT_BYTES,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_sessions": self.max_sessions,
            "max_events_per_session": self.max_events_per_session,
            "max_bytes_per_session": self.max_bytes_per_session,
            "max_snapshot_events": self.max_snapshot_events,
            "max_snapshot_bytes": self.max_snapshot_bytes,
        }


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise AiLensServiceError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise AiLensServiceError(f"{field_name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise AiLensServiceError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _event_bytes(event: AiLensEvent) -> int:
    return len(event.to_json().encode("utf-8"))


@dataclass(slots=True)
class _SessionBuffer:
    session_id: str
    origin: AiLensObservationOrigin
    events: deque[AiLensEvent] = field(default_factory=deque)
    retained_bytes: int = 0
    accepted_event_count: int = 0
    evicted_event_count: int = 0
    eviction_reasons: set[str] = field(default_factory=set)
    last_by_turn: dict[str, tuple[int, Any]] = field(default_factory=dict)
    retained_event_ids: set[str] = field(default_factory=set)

    def clone(self) -> "_SessionBuffer":
        return _SessionBuffer(
            session_id=self.session_id,
            origin=self.origin,
            events=deque(self.events),
            retained_bytes=self.retained_bytes,
            accepted_event_count=self.accepted_event_count,
            evicted_event_count=self.evicted_event_count,
            eviction_reasons=set(self.eviction_reasons),
            last_by_turn=dict(self.last_by_turn),
            retained_event_ids=set(self.retained_event_ids),
        )


class AiLensService:
    """Bounded per-session AI Lens event buffers and snapshot projections."""

    def __init__(
        self,
        *,
        limits: AiLensServiceLimits | None = None,
        mode: AiLensServiceMode | str = AiLensServiceMode.RUNTIME,
    ) -> None:
        self._limits = limits or AiLensServiceLimits()
        if not isinstance(self._limits, AiLensServiceLimits):
            raise AiLensServiceError("limits must be AiLensServiceLimits")
        try:
            self._mode = mode if isinstance(mode, AiLensServiceMode) else AiLensServiceMode(str(mode).lower())
        except ValueError as exc:
            raise AiLensServiceError("mode must be runtime or fixture") from exc
        self._sessions: OrderedDict[str, _SessionBuffer] = OrderedDict()
        self._evicted_session_count = 0
        self._lock = RLock()

    @classmethod
    def fixture(cls, *, limits: AiLensServiceLimits | None = None) -> "AiLensService":
        service = cls(limits=limits, mode=AiLensServiceMode.FIXTURE)
        service.ingest_batch(deterministic_fixture_events())
        return service

    from_fixture = fixture

    @property
    def mode(self) -> AiLensServiceMode:
        return self._mode

    @property
    def fixture_mode(self) -> bool:
        return self._mode == AiLensServiceMode.FIXTURE

    @property
    def limits(self) -> AiLensServiceLimits:
        return self._limits

    def ingest(self, event: AiLensEvent | Mapping[str, Any]) -> AiLensEvent:
        normalized = self.ingest_batch((event,))
        return normalized[0]

    def ingest_batch(
        self, events: Iterable[AiLensEvent | Mapping[str, Any]]
    ) -> tuple[AiLensEvent, ...]:
        """Validate and atomically ingest a batch without external side effects."""

        normalized = validate_event_batch(events)
        if not normalized:
            return ()
        expected_origin = (
            AiLensObservationOrigin.SYNTHETIC_FIXTURE
            if self._mode == AiLensServiceMode.FIXTURE
            else AiLensObservationOrigin.RUNTIME_OBSERVATION
        )
        if any(event.observation_origin != expected_origin for event in normalized):
            raise AiLensServiceError(
                f"{self._mode.value} service rejects events from a different observation origin"
            )

        with self._lock:
            staged = OrderedDict((key, buffer.clone()) for key, buffer in self._sessions.items())
            staged_evicted_sessions = self._evicted_session_count
            for event in normalized:
                if event.session_id not in staged:
                    if len(staged) >= self._limits.max_sessions:
                        staged.popitem(last=False)
                        staged_evicted_sessions += 1
                    staged[event.session_id] = _SessionBuffer(
                        session_id=event.session_id,
                        origin=event.observation_origin,
                    )
                self._append_event(staged[event.session_id], event)
            self._sessions = staged
            self._evicted_session_count = staged_evicted_sessions
        return normalized

    def _append_event(self, buffer: _SessionBuffer, event: AiLensEvent) -> None:
        if event.observation_origin != buffer.origin:
            raise AiLensServiceError("fixture and runtime observations must never share a session buffer")
        if event.event_id in buffer.retained_event_ids:
            raise AiLensServiceError("session contains duplicate event_id")
        previous = buffer.last_by_turn.get(event.turn_id)
        if previous is not None:
            previous_sequence, previous_timestamp = previous
            if event.sequence <= previous_sequence:
                raise AiLensServiceError("event sequence must increase within each session turn")
            if event.created_at < previous_timestamp:
                raise AiLensServiceError("event timestamps must not move backwards within each session turn")

        size = _event_bytes(event)
        if size > self._limits.max_bytes_per_session:
            raise AiLensServiceError("single event exceeds max_bytes_per_session")
        buffer.events.append(event)
        buffer.retained_event_ids.add(event.event_id)
        buffer.retained_bytes += size
        buffer.accepted_event_count += 1
        buffer.last_by_turn[event.turn_id] = (event.sequence, event.created_at)

        while (
            len(buffer.events) > self._limits.max_events_per_session
            or buffer.retained_bytes > self._limits.max_bytes_per_session
        ):
            if len(buffer.events) > self._limits.max_events_per_session:
                buffer.eviction_reasons.add("session_event_budget")
            if buffer.retained_bytes > self._limits.max_bytes_per_session:
                buffer.eviction_reasons.add("session_byte_budget")
            evicted = buffer.events.popleft()
            buffer.retained_event_ids.discard(evicted.event_id)
            buffer.retained_bytes -= _event_bytes(evicted)
            buffer.evicted_event_count += 1

    def list_sessions(self) -> tuple[str, ...]:
        """Return session IDs in deterministic oldest-session-first order."""

        with self._lock:
            return tuple(self._sessions)

    def list_session_summaries(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(self._session_summary(buffer) for buffer in self._sessions.values())

    def read_events(self, session_id: str) -> tuple[AiLensEvent, ...]:
        with self._lock:
            buffer = self._get_session(session_id)
            return tuple(validate_ai_lens_event(event) for event in buffer.events)

    def clear_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(str(session_id), None) is not None

    def snapshot(
        self,
        session_id: str,
        *,
        max_events: Any | None = None,
        include_insights: bool = False,
    ) -> dict[str, Any]:
        """Return a bounded JSON-ready snapshot without changing buffer state."""

        if not isinstance(include_insights, bool):
            raise AiLensServiceError("include_insights must be a boolean")

        with self._lock:
            buffer = self._get_session(session_id).clone()
        requested_event_limit = self._limits.max_snapshot_events
        if max_events is not None:
            requested_event_limit = _bounded_int(
                max_events,
                field_name="max_events",
                minimum=1,
                maximum=self._limits.max_snapshot_events,
            )

        retained = tuple(buffer.events)
        selected = list(retained[-requested_event_limit:])
        snapshot_reasons: set[str] = set()
        if len(selected) < len(retained):
            snapshot_reasons.add("snapshot_event_budget")
        while True:
            payload = self._snapshot_payload(buffer, selected, snapshot_reasons)
            if include_insights:
                from src.ai_lens_insights import build_ai_lens_insights

                payload["insights_included"] = True
                payload["insights"] = build_ai_lens_insights(payload)
            encoded_size = _final_snapshot_size(payload)
            if encoded_size <= self._limits.max_snapshot_bytes:
                return payload
            if not selected:
                raise AiLensServiceError("max_snapshot_bytes is too small for snapshot metadata")
            selected.pop(0)
            snapshot_reasons.add("snapshot_byte_budget")

    def snapshot_json(
        self,
        session_id: str,
        *,
        max_events: Any | None = None,
        include_insights: bool = False,
    ) -> str:
        return json.dumps(
            self.snapshot(
                session_id,
                max_events=max_events,
                include_insights=include_insights,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    get_snapshot = snapshot
    read_snapshot = snapshot

    def _snapshot_payload(
        self,
        buffer: _SessionBuffer,
        selected: list[AiLensEvent],
        snapshot_reasons: set[str],
    ) -> dict[str, Any]:
        retained = tuple(buffer.events)
        incomplete = buffer.evicted_event_count > 0
        truncated = len(selected) < len(retained)
        reasons = sorted(buffer.eviction_reasons | snapshot_reasons)
        return {
            "schema": AI_LENS_SNAPSHOT_SCHEMA,
            "session_id": buffer.session_id,
            "mode": self._mode.value,
            "observation_origin": buffer.origin.value,
            "fixture_mode": self._mode == AiLensServiceMode.FIXTURE,
            "accepted_event_count": buffer.accepted_event_count,
            "event_count": len(retained),
            "retained_event_count": len(retained),
            "returned_event_count": len(selected),
            "evicted_event_count": buffer.evicted_event_count,
            "retained_bytes": buffer.retained_bytes,
            "snapshot_bytes": 0,
            "turn_count": len({event.turn_id for event in retained}),
            "first_retained_at": retained[0].to_dict()["created_at"] if retained else None,
            "last_retained_at": retained[-1].to_dict()["created_at"] if retained else None,
            "incomplete": incomplete,
            "truncated": truncated,
            "truncation_reasons": reasons,
            "summary_scope": "retained_events",
            "phase_counts": _counts(event.phase.value for event in retained),
            "event_type_counts": _counts(event.event_type.value for event in retained),
            "truth_level_counts": _counts(event.truth_level.value for event in retained),
            "privacy_level_counts": _counts(event.privacy_level.value for event in retained),
            "events": [event.to_dict() for event in selected],
            "limits": self._limits.to_dict(),
            "raw_content_visible": False,
        }

    def _session_summary(self, buffer: _SessionBuffer) -> dict[str, Any]:
        events = tuple(buffer.events)
        return {
            "schema": AI_LENS_SESSION_SUMMARY_SCHEMA,
            "session_id": buffer.session_id,
            "mode": self._mode.value,
            "observation_origin": buffer.origin.value,
            "retained_event_count": len(events),
            "event_count": len(events),
            "accepted_event_count": buffer.accepted_event_count,
            "evicted_event_count": buffer.evicted_event_count,
            "retained_bytes": buffer.retained_bytes,
            "incomplete": buffer.evicted_event_count > 0,
            "last_retained_at": events[-1].to_dict()["created_at"] if events else None,
            "raw_content_visible": False,
        }

    def _get_session(self, session_id: str) -> _SessionBuffer:
        normalized = str(session_id or "").strip()
        if normalized not in self._sessions:
            raise AiLensSessionNotFoundError("AI Lens session was not found")
        return self._sessions[normalized]

    def service_summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self._mode.value,
                "fixture_mode": self._mode == AiLensServiceMode.FIXTURE,
                "session_count": len(self._sessions),
                "evicted_session_count": self._evicted_session_count,
                "limits": self._limits.to_dict(),
                "raw_content_visible": False,
            }


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _final_snapshot_size(payload: dict[str, Any]) -> int:
    size = 0
    for _ in range(4):
        payload["snapshot_bytes"] = size
        updated = len(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if updated == size:
            return size
        size = updated
    payload["snapshot_bytes"] = size
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


class AiLensEventEmitter:
    """Turn-bound fail-safe emitter for optional runtime instrumentation."""

    def __init__(
        self,
        *,
        service: AiLensService,
        session_ref: Any,
        turn_ref: Any,
        privacy_level: AiLensPrivacyLevel | str = AiLensPrivacyLevel.METADATA,
        redaction_level: AiLensRedactionLevel | str = AiLensRedactionLevel.METADATA_ONLY,
        clock: Callable[[], datetime] | None = None,
        start_sequence: int = 1,
    ) -> None:
        if not isinstance(service, AiLensService):
            raise AiLensServiceError("service must be an AiLensService")
        self._service = service
        self.session_id = opaque_ai_lens_ref("lens-session", session_ref)
        self.turn_id = opaque_ai_lens_ref("lens-turn", turn_ref)
        try:
            self._privacy_level = (
                privacy_level
                if isinstance(privacy_level, AiLensPrivacyLevel)
                else AiLensPrivacyLevel(str(privacy_level).strip().lower())
            )
            self._redaction_level = (
                redaction_level
                if isinstance(redaction_level, AiLensRedactionLevel)
                else AiLensRedactionLevel(str(redaction_level).strip().lower())
            )
        except ValueError as exc:
            raise AiLensServiceError("emitter privacy or redaction level is invalid") from exc
        if (
            self._privacy_level not in {AiLensPrivacyLevel.PUBLIC, AiLensPrivacyLevel.METADATA}
            and self._redaction_level == AiLensRedactionLevel.NONE
        ):
            raise AiLensServiceError("private instrumentation requires redaction")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._next_sequence = _bounded_int(
            start_sequence,
            field_name="start_sequence",
            minimum=1,
            maximum=1_000_000_000,
        )
        self._emitted_event_count = 0
        self._rejected_event_count = 0
        self._last_error_code = ""
        self._emitter_lock = RLock()

    @property
    def redaction_level(self) -> AiLensRedactionLevel:
        return self._redaction_level

    def emit(
        self,
        *,
        event_type: AiLensEventType | str,
        source_refs: Iterable[AiLensSourceRef | Mapping[str, Any]] = (),
        payload: Mapping[str, Any] | None = None,
        summary: str = "",
        status: Any = None,
        latency_ms: Any = 0,
    ) -> bool:
        with self._emitter_lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            event_id = opaque_ai_lens_ref(
                "lens-event",
                f"{self.session_id}\x1f{self.turn_id}\x1f{sequence}",
            )
            try:
                event = AiLensEvent.create(
                    event_id=event_id,
                    session_id=self.session_id,
                    turn_id=self.turn_id,
                    sequence=sequence,
                    created_at=self._clock(),
                    event_type=event_type,
                    status=status,
                    truth_level=AiLensTruthLevel.RUNTIME_TRACE,
                    observation_origin=AiLensObservationOrigin.RUNTIME_OBSERVATION,
                    privacy_level=self._privacy_level,
                    redaction_level=self._redaction_level,
                    source_refs=source_refs,
                    summary=summary,
                    payload=payload or {},
                    latency_ms=latency_ms,
                )
                self._service.ingest(event)
            except AiLensEventError:
                self._record_rejection("event_validation_failed")
                return False
            except (AiLensServiceError, TypeError, ValueError):
                self._record_rejection("service_ingest_failed")
                return False
            self._emitted_event_count += 1
            self._last_error_code = ""
            return True

    def record_rejection(self, reason_code: str = "instrumentation_evidence_rejected") -> None:
        safe_code = str(reason_code or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", safe_code):
            safe_code = "instrumentation_evidence_rejected"
        with self._emitter_lock:
            self._record_rejection(safe_code)

    def _record_rejection(self, reason_code: str) -> None:
        self._rejected_event_count += 1
        self._last_error_code = reason_code

    def diagnostics(self) -> dict[str, Any]:
        with self._emitter_lock:
            return {
                "schema": "odysseus.ai_lens.capture_diagnostics.v1",
                "emitted_event_count": self._emitted_event_count,
                "rejected_event_count": self._rejected_event_count,
                "last_error_code": self._last_error_code,
                "raw_content_visible": False,
            }


# Discoverable read aliases for route/service adapters in the next slice.
AiLensSnapshotService = AiLensService
read_snapshot = AiLensService.snapshot
AiLensCaptureEmitter = AiLensEventEmitter
