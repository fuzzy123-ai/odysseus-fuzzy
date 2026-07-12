"""Privacy-bounded local replay snapshots for AI Lens.

Replay means reading a previously validated snapshot.  This module never
reruns a model, provider, tool, or source operation and never deletes sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from src.ai_lens_events import (
    AiLensEvent,
    AiLensEventType,
    AiLensPrivacyLevel,
    AiLensRedactionLevel,
    validate_event_batch,
)
from src.ai_lens_graph import AI_LENS_GRAPH_PAGE_SCHEMA, validate_ai_lens_projection


AI_LENS_REPLAY_SCHEMA = "odysseus.ai_lens.replay.v1"
AI_LENS_REPLAY_SUMMARY_SCHEMA = "odysseus.ai_lens.replay_summary.v1"
MAX_REPLAY_EVENTS = 256
MAX_REASON_COUNT = 32
MAX_REASON_CHARS = 80


class AiLensReplayError(ValueError):
    """Raised when replay storage or content is invalid or unsafe."""


class AiLensReplayNotFoundError(AiLensReplayError):
    """Raised when a replay ID is unknown."""


class AiLensReplayExpiredError(AiLensReplayError):
    """Raised when an expired replay must no longer be returned."""


@dataclass(frozen=True, slots=True)
class AiLensReplayLimits:
    max_records: int = 100
    max_total_bytes: int = 16 * 1024 * 1024
    max_record_bytes: int = 1024 * 1024
    default_ttl_seconds: int = 24 * 60 * 60
    max_ttl_seconds: int = 7 * 24 * 60 * 60

    def __post_init__(self) -> None:
        _bounded_int(self.max_records, field_name="max_records", minimum=1, maximum=10_000)
        _bounded_int(self.max_total_bytes, field_name="max_total_bytes", minimum=4_096, maximum=1024 * 1024 * 1024)
        _bounded_int(self.max_record_bytes, field_name="max_record_bytes", minimum=4_096, maximum=64 * 1024 * 1024)
        _bounded_int(self.default_ttl_seconds, field_name="default_ttl_seconds", minimum=60, maximum=31 * 24 * 60 * 60)
        _bounded_int(self.max_ttl_seconds, field_name="max_ttl_seconds", minimum=60, maximum=31 * 24 * 60 * 60)
        if self.max_record_bytes > self.max_total_bytes:
            raise AiLensReplayError("max_record_bytes must not exceed max_total_bytes")
        if self.default_ttl_seconds > self.max_ttl_seconds:
            raise AiLensReplayError("default_ttl_seconds must not exceed max_ttl_seconds")

    @classmethod
    def create(cls, **values: Any) -> "AiLensReplayLimits":
        defaults = cls()
        return cls(
            max_records=_bounded_int(values.get("max_records", defaults.max_records), field_name="max_records", minimum=1, maximum=10_000),
            max_total_bytes=_bounded_int(values.get("max_total_bytes", defaults.max_total_bytes), field_name="max_total_bytes", minimum=4_096, maximum=1024 * 1024 * 1024),
            max_record_bytes=_bounded_int(values.get("max_record_bytes", defaults.max_record_bytes), field_name="max_record_bytes", minimum=4_096, maximum=64 * 1024 * 1024),
            default_ttl_seconds=_bounded_int(values.get("default_ttl_seconds", defaults.default_ttl_seconds), field_name="default_ttl_seconds", minimum=60, maximum=31 * 24 * 60 * 60),
            max_ttl_seconds=_bounded_int(values.get("max_ttl_seconds", defaults.max_ttl_seconds), field_name="max_ttl_seconds", minimum=60, maximum=31 * 24 * 60 * 60),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_records": self.max_records,
            "max_total_bytes": self.max_total_bytes,
            "max_record_bytes": self.max_record_bytes,
            "default_ttl_seconds": self.default_ttl_seconds,
            "max_ttl_seconds": self.max_ttl_seconds,
        }


@dataclass(frozen=True, slots=True)
class AiLensReplaySummary:
    replay_id: str
    session_id: str
    turn_id: str
    answer_ref: str
    created_at: str
    expires_at: str
    classification: str
    redaction_level: str
    event_count: int
    record_bytes: int
    content_hash: str
    incomplete: bool
    truncated: bool
    expired: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AI_LENS_REPLAY_SUMMARY_SCHEMA,
            "replay_id": self.replay_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "answer_ref": self.answer_ref,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "classification": self.classification,
            "redaction_level": self.redaction_level,
            "event_count": self.event_count,
            "record_bytes": self.record_bytes,
            "content_hash": self.content_hash,
            "incomplete": self.incomplete,
            "truncated": self.truncated,
            "expired": self.expired,
        }


_REPLAY_ID_RE = re.compile(r"^replay-[a-f0-9]{24}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRIVACY_ORDER = {
    AiLensPrivacyLevel.PUBLIC: 0,
    AiLensPrivacyLevel.METADATA: 1,
    AiLensPrivacyLevel.PRIVATE_METADATA: 2,
    AiLensPrivacyLevel.SENSITIVE_METADATA: 3,
    AiLensPrivacyLevel.DSGVO_LOCAL: 4,
}
_REDACTION_ORDER = {
    AiLensRedactionLevel.NONE: 0,
    AiLensRedactionLevel.METADATA_ONLY: 1,
    AiLensRedactionLevel.REDACTED: 2,
    AiLensRedactionLevel.HASHED: 3,
    AiLensRedactionLevel.LOCAL_ONLY: 4,
}
_PRIVACY_TTL_CAP_SECONDS = {
    AiLensPrivacyLevel.PUBLIC: 7 * 24 * 60 * 60,
    AiLensPrivacyLevel.METADATA: 7 * 24 * 60 * 60,
    AiLensPrivacyLevel.PRIVATE_METADATA: 3 * 24 * 60 * 60,
    AiLensPrivacyLevel.SENSITIVE_METADATA: 24 * 60 * 60,
    AiLensPrivacyLevel.DSGVO_LOCAL: 6 * 60 * 60,
}
_RECORD_KEYS = {
    "schema", "replay_id", "session_id", "turn_id", "answer_ref", "created_at", "expires_at",
    "classification", "redaction_level", "event_count", "events", "projection", "graph_metadata",
    "incomplete", "truncated", "incomplete_reasons", "truncated_reasons", "content_hash", "record_bytes",
    "replay_mode", "model_rerun_allowed", "tool_rerun_allowed", "provider_replay_allowed",
    "source_delete_allowed", "raw_content_visible",
}


class AiLensReplayStore:
    def __init__(
        self,
        *,
        storage_root: str | os.PathLike[str],
        limits: AiLensReplayLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._limits = limits or AiLensReplayLimits()
        if not isinstance(self._limits, AiLensReplayLimits):
            raise AiLensReplayError("limits must be AiLensReplayLimits")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._root = _prepare_root(storage_root)

    @property
    def storage_root(self) -> Path:
        return self._root

    @property
    def limits(self) -> AiLensReplayLimits:
        return self._limits

    def persist(
        self,
        *,
        events: Iterable[AiLensEvent | Mapping[str, Any]],
        answer_ref: Any,
        projection: Mapping[str, Any] | None = None,
        graph_page: Mapping[str, Any] | None = None,
        classification: AiLensPrivacyLevel | str | None = None,
        redaction_level: AiLensRedactionLevel | str | None = None,
        created_at: datetime | str | None = None,
        ttl_seconds: Any | None = None,
        incomplete_reasons: Iterable[Any] = (),
        truncated_reasons: Iterable[Any] = (),
    ) -> AiLensReplaySummary:
        normalized_events = _validated_events(events)
        session_id = normalized_events[0].session_id
        turn_id = normalized_events[0].turn_id
        safe_answer_ref = _safe_id(answer_ref, field_name="answer_ref")
        replay_id = _replay_id(session_id, turn_id, safe_answer_ref)
        inferred_privacy = max((event.privacy_level for event in normalized_events), key=_PRIVACY_ORDER.get)
        normalized_privacy = inferred_privacy if classification is None else _privacy(classification)
        if _PRIVACY_ORDER[normalized_privacy] < _PRIVACY_ORDER[inferred_privacy]:
            raise AiLensReplayError("replay classification cannot weaken event privacy")
        inferred_redaction = max((event.redaction_level for event in normalized_events), key=_REDACTION_ORDER.get)
        normalized_redaction = inferred_redaction if redaction_level is None else _redaction(redaction_level)
        if _REDACTION_ORDER[normalized_redaction] < _REDACTION_ORDER[inferred_redaction]:
            raise AiLensReplayError("replay redaction cannot weaken event redaction")
        normalized_projection = _validated_projection(projection, session_id=session_id, event_ids={event.event_id for event in normalized_events})
        graph_metadata = _validated_graph_metadata(graph_page, session_id=session_id, projection=normalized_projection)
        now = _utc(self._clock())
        created = now if created_at is None else _utc(created_at)
        if created > now + timedelta(minutes=5):
            raise AiLensReplayError("replay created_at cannot be in the future")
        requested_ttl = self._limits.default_ttl_seconds if ttl_seconds is None else _bounded_int(
            ttl_seconds, field_name="ttl_seconds", minimum=60, maximum=self._limits.max_ttl_seconds
        )
        effective_ttl = min(requested_ttl, self._limits.max_ttl_seconds, _PRIVACY_TTL_CAP_SECONDS[normalized_privacy])
        expires = created + timedelta(seconds=effective_ttl)
        if expires <= now:
            raise AiLensReplayError("replay would already be expired")
        incomplete = set(_safe_reasons(incomplete_reasons, field_name="incomplete_reasons"))
        truncated = set(_safe_reasons(truncated_reasons, field_name="truncated_reasons"))
        if normalized_projection:
            incomplete.update(normalized_projection["incomplete_reasons"])
            if normalized_projection["truncated"]:
                truncated.update(
                    reason for reason in normalized_projection["incomplete_reasons"]
                    if reason in {"node_budget", "edge_budget", "byte_budget"}
                )
        if graph_metadata:
            incomplete.update(graph_metadata["source_incomplete_reasons"])
            truncated.update(graph_metadata["page_reasons"])
        record = {
            "schema": AI_LENS_REPLAY_SCHEMA,
            "replay_id": replay_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "answer_ref": safe_answer_ref,
            "created_at": _time_text(created),
            "expires_at": _time_text(expires),
            "classification": normalized_privacy.value,
            "redaction_level": normalized_redaction.value,
            "event_count": len(normalized_events),
            "events": [event.to_dict() for event in normalized_events],
            "projection": normalized_projection,
            "graph_metadata": graph_metadata,
            "incomplete": bool(incomplete),
            "truncated": bool(truncated),
            "incomplete_reasons": sorted(incomplete),
            "truncated_reasons": sorted(truncated),
            "content_hash": "",
            "record_bytes": 0,
            "replay_mode": "snapshot_only",
            "model_rerun_allowed": False,
            "tool_rerun_allowed": False,
            "provider_replay_allowed": False,
            "source_delete_allowed": False,
            "raw_content_visible": False,
        }
        _finalize_record(record)
        if record["record_bytes"] > self._limits.max_record_bytes:
            raise AiLensReplayError("replay exceeds max_record_bytes")
        with self._lock:
            self.delete_expired(now=now)
            path = self._record_path(replay_id)
            if path.exists():
                existing = self._read_path(path, now=now, allow_expired=False)
                if _semantic_record(existing) != _semantic_record(record):
                    raise AiLensReplayError("replay identity already exists with different content")
                return _summary(existing, now=now)
            _atomic_write(path, record, root=self._root)
            self._enforce_retention(now=now)
            if not path.exists():
                raise AiLensReplayError("replay was evicted by retention limits")
            return _summary(self._read_path(path, now=now, allow_expired=False), now=now)

    def read(self, replay_id: Any) -> dict[str, Any]:
        safe_replay_id = _safe_replay_id(replay_id)
        now = _utc(self._clock())
        with self._lock:
            record = self._read_path(self._record_path(safe_replay_id), now=now, allow_expired=False)
            return json.loads(_canonical(record))

    replay = read

    def list(self) -> tuple[AiLensReplaySummary, ...]:
        now = _utc(self._clock())
        with self._lock:
            records = [self._read_path(path, now=now, allow_expired=True) for path in self._record_paths()]
        records.sort(key=lambda item: (item["created_at"], item["replay_id"]), reverse=True)
        return tuple(_summary(record, now=now) for record in records)

    list_replays = list

    def delete_expired(self, *, now: datetime | str | None = None) -> tuple[str, ...]:
        current = _utc(self._clock()) if now is None else _utc(now)
        deleted: list[str] = []
        with self._lock:
            for path in self._record_paths():
                record = self._read_path(path, now=current, allow_expired=True)
                if _utc(record["expires_at"]) <= current:
                    _safe_unlink(path, root=self._root)
                    deleted.append(record["replay_id"])
        return tuple(sorted(deleted))

    def _record_path(self, replay_id: str) -> Path:
        path = self._root / f"{replay_id}.json"
        _assert_contained(path, root=self._root)
        return path

    def _record_paths(self) -> tuple[Path, ...]:
        _assert_safe_root(self._root)
        paths: list[Path] = []
        for entry in self._root.iterdir():
            if _is_reparse(entry) or entry.is_dir():
                raise AiLensReplayError("replay storage contains an unsafe entry")
            if not re.fullmatch(r"replay-[a-f0-9]{24}\.json", entry.name):
                raise AiLensReplayError("replay storage contains an unexpected entry")
            _assert_contained(entry, root=self._root)
            paths.append(entry)
        return tuple(sorted(paths, key=lambda item: item.name))

    def _read_path(self, path: Path, *, now: datetime, allow_expired: bool) -> dict[str, Any]:
        _assert_contained(path, root=self._root)
        if not path.exists():
            raise AiLensReplayNotFoundError("replay was not found")
        if _is_reparse(path) or not path.is_file():
            raise AiLensReplayError("replay storage entry is unsafe")
        try:
            if path.stat().st_size > self._limits.max_record_bytes:
                raise AiLensReplayError("replay record exceeds configured size")
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except AiLensReplayError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AiLensReplayError("replay record is corrupt") from exc
        record = _validate_record(value, actual_bytes=len(raw.encode("utf-8")))
        created = _utc(record["created_at"])
        expires = _utc(record["expires_at"])
        classification = _privacy(record["classification"])
        ttl_cap = min(self._limits.max_ttl_seconds, _PRIVACY_TTL_CAP_SECONDS[classification])
        if (expires - created).total_seconds() > ttl_cap:
            raise AiLensReplayError("replay expiry exceeds privacy retention limits")
        if created > now + timedelta(minutes=5):
            raise AiLensReplayError("replay created_at is invalid")
        if _utc(record["expires_at"]) <= now and not allow_expired:
            raise AiLensReplayExpiredError("replay has expired")
        return record

    def _enforce_retention(self, *, now: datetime) -> None:
        records = [(self._read_path(path, now=now, allow_expired=True), path) for path in self._record_paths()]
        records.sort(key=lambda item: (item[0]["created_at"], item[0]["replay_id"]))
        total_bytes = sum(item[0]["record_bytes"] for item in records)
        while len(records) > self._limits.max_records or total_bytes > self._limits.max_total_bytes:
            record, path = records.pop(0)
            _safe_unlink(path, root=self._root)
            total_bytes -= record["record_bytes"]


def _validated_events(events: Iterable[AiLensEvent | Mapping[str, Any]]) -> tuple[AiLensEvent, ...]:
    try:
        normalized = validate_event_batch(events)
    except (TypeError, ValueError) as exc:
        raise AiLensReplayError("replay events are invalid") from exc
    if not normalized or len(normalized) > MAX_REPLAY_EVENTS:
        raise AiLensReplayError("replay must contain a bounded event batch")
    if len({event.session_id for event in normalized}) != 1 or len({event.turn_id for event in normalized}) != 1:
        raise AiLensReplayError("replay events must belong to one session turn")
    if len({event.observation_origin for event in normalized}) != 1:
        raise AiLensReplayError("fixture and runtime events must not be mixed")
    if not any(event.event_type == AiLensEventType.ANSWER_COMPLETED for event in normalized):
        raise AiLensReplayError("replay requires answer_completed evidence")
    return normalized


def _validated_projection(value: Mapping[str, Any] | None, *, session_id: str, event_ids: set[str]) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        projection = validate_ai_lens_projection(value)
    except ValueError as exc:
        raise AiLensReplayError("replay projection is invalid") from exc
    if projection["session_id"] != session_id:
        raise AiLensReplayError("replay projection session does not match events")
    referenced = {event_id for node in projection["nodes"] for event_id in node["evidence_event_ids"]}
    referenced.update(event_id for edge in projection["edges"] for event_id in edge["evidence_event_ids"])
    if not referenced.issubset(event_ids):
        raise AiLensReplayError("replay projection references events outside the snapshot")
    return projection


def _validated_graph_metadata(value: Mapping[str, Any] | None, *, session_id: str, projection: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or value.get("schema") != AI_LENS_GRAPH_PAGE_SCHEMA:
        raise AiLensReplayError("replay graph metadata is invalid")
    if value.get("raw_content_visible") is not False or value.get("truth_level") != "semantic_projection" or value.get("source_truth_level") != "runtime_trace":
        raise AiLensReplayError("replay graph metadata truth or privacy is invalid")
    if value.get("session_id") != session_id:
        raise AiLensReplayError("replay graph metadata session does not match events")
    if projection is not None:
        expected = "sha256:" + hashlib.sha256(_canonical(projection).encode("utf-8")).hexdigest()
        if value.get("source_projection_fingerprint") != expected:
            raise AiLensReplayError("replay graph metadata does not match projection")
    actual_bytes = len(_canonical(value).encode("utf-8"))
    if value.get("payload_bytes") != actual_bytes:
        raise AiLensReplayError("replay graph metadata payload size is invalid")
    mode = str(value.get("mode") or "")
    if mode not in {"orbit", "trace", "graph", "diagnostics"}:
        raise AiLensReplayError("replay graph metadata mode is invalid")
    if not isinstance(value.get("incomplete"), bool) or not isinstance(value.get("clipped"), bool):
        raise AiLensReplayError("replay graph metadata completeness flags are invalid")
    fingerprint = str(value.get("source_projection_fingerprint") or "")
    if not _HASH_RE.fullmatch(fingerprint):
        raise AiLensReplayError("replay graph metadata fingerprint is invalid")
    return {
        "schema": "odysseus.ai_lens.replay_graph_metadata.v1",
        "source_graph_schema": AI_LENS_GRAPH_PAGE_SCHEMA,
        "source_projection_fingerprint": fingerprint,
        "mode": mode,
        "page": _bounded_int(value.get("page"), field_name="graph.page", minimum=1, maximum=10_000),
        "limit": _bounded_int(value.get("limit"), field_name="graph.limit", minimum=1, maximum=128),
        "depth": _bounded_int(value.get("depth"), field_name="graph.depth", minimum=0, maximum=3),
        "node_count": _bounded_int(value.get("node_count"), field_name="graph.node_count", minimum=0, maximum=128),
        "edge_count": _bounded_int(value.get("edge_count"), field_name="graph.edge_count", minimum=0, maximum=512),
        "cluster_count": _bounded_int(value.get("cluster_count"), field_name="graph.cluster_count", minimum=0, maximum=5),
        "incomplete": value.get("incomplete"),
        "clipped": value.get("clipped"),
        "source_incomplete_reasons": _safe_reasons(value.get("source_incomplete_reasons") or (), field_name="graph.source_incomplete_reasons"),
        "page_reasons": _safe_reasons(value.get("page_reasons") or (), field_name="graph.page_reasons"),
        "raw_content_visible": False,
    }


def _validate_record(value: Any, *, actual_bytes: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RECORD_KEYS or value.get("schema") != AI_LENS_REPLAY_SCHEMA:
        raise AiLensReplayError("replay record schema is invalid")
    if value.get("raw_content_visible") is not False or value.get("replay_mode") != "snapshot_only":
        raise AiLensReplayError("replay record mode or privacy is invalid")
    for field in ("model_rerun_allowed", "tool_rerun_allowed", "provider_replay_allowed", "source_delete_allowed"):
        if value.get(field) is not False:
            raise AiLensReplayError("replay execution and source deletion must remain disabled")
    replay_id = _safe_replay_id(value.get("replay_id"))
    session_id = _safe_id(value.get("session_id"), field_name="session_id")
    turn_id = _safe_id(value.get("turn_id"), field_name="turn_id")
    answer_ref = _safe_id(value.get("answer_ref"), field_name="answer_ref")
    if replay_id != _replay_id(session_id, turn_id, answer_ref):
        raise AiLensReplayError("replay identity is invalid")
    created = _utc(value.get("created_at"))
    expires = _utc(value.get("expires_at"))
    if expires <= created:
        raise AiLensReplayError("replay expiry is invalid")
    events = _validated_events(value.get("events") or ())
    if len(events) != value.get("event_count") or events[0].session_id != session_id or events[0].turn_id != turn_id:
        raise AiLensReplayError("replay event metadata is inconsistent")
    privacy = _privacy(value.get("classification"))
    redaction = _redaction(value.get("redaction_level"))
    if any(_PRIVACY_ORDER[event.privacy_level] > _PRIVACY_ORDER[privacy] for event in events):
        raise AiLensReplayError("replay classification weakens event privacy")
    if any(_REDACTION_ORDER[event.redaction_level] > _REDACTION_ORDER[redaction] for event in events):
        raise AiLensReplayError("replay redaction weakens event redaction")
    projection = _validated_projection(value.get("projection"), session_id=session_id, event_ids={event.event_id for event in events})
    graph_metadata = value.get("graph_metadata")
    if graph_metadata is not None:
        if not isinstance(graph_metadata, dict) or set(graph_metadata) != {
            "schema", "source_graph_schema", "source_projection_fingerprint", "mode", "page", "limit", "depth",
            "node_count", "edge_count", "cluster_count", "incomplete", "clipped", "source_incomplete_reasons",
            "page_reasons", "raw_content_visible",
        } or graph_metadata.get("schema") != "odysseus.ai_lens.replay_graph_metadata.v1" or graph_metadata.get("source_graph_schema") != AI_LENS_GRAPH_PAGE_SCHEMA or graph_metadata.get("raw_content_visible") is not False:
            raise AiLensReplayError("replay graph metadata is invalid")
        fingerprint = str(graph_metadata.get("source_projection_fingerprint") or "")
        if not _HASH_RE.fullmatch(fingerprint):
            raise AiLensReplayError("replay graph metadata fingerprint is invalid")
        if projection is not None:
            expected_fingerprint = "sha256:" + hashlib.sha256(_canonical(projection).encode("utf-8")).hexdigest()
            if fingerprint != expected_fingerprint:
                raise AiLensReplayError("replay graph metadata does not match projection")
        if graph_metadata.get("mode") not in {"orbit", "trace", "graph", "diagnostics"}:
            raise AiLensReplayError("replay graph metadata mode is invalid")
        for field_name, minimum, maximum in (
            ("page", 1, 10_000), ("limit", 1, 128), ("depth", 0, 3),
            ("node_count", 0, 128), ("edge_count", 0, 512), ("cluster_count", 0, 5),
        ):
            _bounded_int(graph_metadata.get(field_name), field_name=f"graph.{field_name}", minimum=minimum, maximum=maximum)
        if not isinstance(graph_metadata.get("incomplete"), bool) or not isinstance(graph_metadata.get("clipped"), bool):
            raise AiLensReplayError("replay graph metadata completeness flags are invalid")
        graph_source_reasons = _safe_reasons(graph_metadata.get("source_incomplete_reasons"), field_name="graph.source_incomplete_reasons")
        graph_page_reasons = _safe_reasons(graph_metadata.get("page_reasons"), field_name="graph.page_reasons")
    incomplete_reasons = _safe_reasons(value.get("incomplete_reasons"), field_name="incomplete_reasons")
    truncated_reasons = _safe_reasons(value.get("truncated_reasons"), field_name="truncated_reasons")
    if bool(incomplete_reasons) != value.get("incomplete") or bool(truncated_reasons) != value.get("truncated"):
        raise AiLensReplayError("replay completeness flags are inconsistent")
    if projection is not None:
        if not set(projection["incomplete_reasons"]).issubset(incomplete_reasons):
            raise AiLensReplayError("replay omits projection incompleteness")
    if graph_metadata is not None:
        if not set(graph_source_reasons).issubset(incomplete_reasons) or not set(graph_page_reasons).issubset(truncated_reasons):
            raise AiLensReplayError("replay omits graph truncation metadata")
    if not _HASH_RE.fullmatch(str(value.get("content_hash") or "")):
        raise AiLensReplayError("replay content hash is invalid")
    if value.get("record_bytes") != actual_bytes:
        raise AiLensReplayError("replay record size is invalid")
    expected_hash = _content_hash(value)
    if value.get("content_hash") != expected_hash:
        raise AiLensReplayError("replay content hash mismatch")
    return value


def _finalize_record(record: dict[str, Any]) -> None:
    record["content_hash"] = _content_hash(record)
    size = 0
    for _ in range(4):
        record["record_bytes"] = size
        updated = len(_canonical(record).encode("utf-8"))
        if updated == size:
            return
        size = updated
    record["record_bytes"] = size


def _content_hash(record: Mapping[str, Any]) -> str:
    content = {key: value for key, value in record.items() if key not in {"content_hash", "record_bytes"}}
    return "sha256:" + hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()


def _semantic_record(record: Mapping[str, Any]) -> str:
    ignored = {"created_at", "expires_at", "content_hash", "record_bytes"}
    return _canonical({key: value for key, value in record.items() if key not in ignored})


def _summary(record: Mapping[str, Any], *, now: datetime) -> AiLensReplaySummary:
    return AiLensReplaySummary(
        replay_id=record["replay_id"], session_id=record["session_id"], turn_id=record["turn_id"],
        answer_ref=record["answer_ref"], created_at=record["created_at"], expires_at=record["expires_at"],
        classification=record["classification"], redaction_level=record["redaction_level"],
        event_count=record["event_count"], record_bytes=record["record_bytes"], content_hash=record["content_hash"],
        incomplete=record["incomplete"], truncated=record["truncated"], expired=_utc(record["expires_at"]) <= now,
    )


def _prepare_root(value: str | os.PathLike[str]) -> Path:
    raw = Path(value)
    if not raw.is_absolute() or ".." in raw.parts:
        raise AiLensReplayError("storage_root must be an absolute traversal-free path")
    _assert_no_reparse_components(raw)
    try:
        raw.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AiLensReplayError("storage_root could not be created") from exc
    _assert_no_reparse_components(raw)
    resolved = raw.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(raw.absolute())) or not resolved.is_dir():
        raise AiLensReplayError("storage_root must not resolve through a link")
    _assert_safe_root(resolved)
    return resolved


def _assert_safe_root(root: Path) -> None:
    _assert_no_reparse_components(root)
    if _is_reparse(root) or not root.is_dir():
        raise AiLensReplayError("storage_root is unsafe")


def _assert_no_reparse_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise AiLensReplayError("storage path contains a symlink or junction")


def _is_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _assert_contained(path: Path, *, root: Path) -> None:
    _assert_safe_root(root)
    if path.parent != root or not re.fullmatch(r"replay-[a-f0-9]{24}\.json", path.name):
        raise AiLensReplayError("replay path is outside the storage root")
    if path.exists() and _is_reparse(path):
        raise AiLensReplayError("replay path is a symlink or junction")


def _atomic_write(path: Path, value: Mapping[str, Any], *, root: Path) -> None:
    _assert_contained(path, root=root)
    temp = root / f".{path.stem}.{uuid4().hex}.tmp"
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        if _is_reparse(temp):
            raise AiLensReplayError("temporary replay path is unsafe")
        _assert_contained(path, root=root)
        os.replace(temp, path)
    except AiLensReplayError:
        raise
    except OSError as exc:
        raise AiLensReplayError("replay could not be persisted atomically") from exc
    finally:
        if temp.exists() and not _is_reparse(temp):
            try:
                temp.unlink()
            except OSError:
                pass


def _safe_unlink(path: Path, *, root: Path) -> None:
    _assert_contained(path, root=root)
    if _is_reparse(path) or not path.is_file():
        raise AiLensReplayError("replay retention path is unsafe")
    try:
        path.unlink()
    except OSError as exc:
        raise AiLensReplayError("expired replay could not be deleted") from exc


def _privacy(value: AiLensPrivacyLevel | str) -> AiLensPrivacyLevel:
    if isinstance(value, AiLensPrivacyLevel):
        return value
    try:
        return AiLensPrivacyLevel(str(value or "").strip().lower())
    except ValueError as exc:
        raise AiLensReplayError("replay classification is invalid") from exc


def _redaction(value: AiLensRedactionLevel | str) -> AiLensRedactionLevel:
    if isinstance(value, AiLensRedactionLevel):
        return value
    try:
        return AiLensRedactionLevel(str(value or "").strip().lower())
    except ValueError as exc:
        raise AiLensReplayError("replay redaction_level is invalid") from exc


def _safe_reasons(values: Iterable[Any], *, field_name: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise AiLensReplayError(f"{field_name} must be a bounded list")
    try:
        result = [str(value or "").strip().lower() for value in values]
    except TypeError as exc:
        raise AiLensReplayError(f"{field_name} must be a bounded list") from exc
    if len(result) > MAX_REASON_COUNT or any(len(value) > MAX_REASON_CHARS or not _REASON_RE.fullmatch(value) for value in result):
        raise AiLensReplayError(f"{field_name} contains an invalid reason")
    return sorted(set(result))


def _safe_id(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise AiLensReplayError(f"{field_name} is invalid")
    return text


def _safe_replay_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _REPLAY_ID_RE.fullmatch(text):
        raise AiLensReplayError("replay_id is invalid")
    return text


def _replay_id(session_id: str, turn_id: str, answer_ref: str) -> str:
    raw = f"{session_id}\x1f{turn_id}\x1f{answer_ref}".encode("utf-8")
    return "replay-" + hashlib.sha256(raw).hexdigest()[:24]


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AiLensReplayError("replay timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AiLensReplayError("replay timestamp requires a timezone")
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise AiLensReplayError(f"{field_name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise AiLensReplayError(f"{field_name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise AiLensReplayError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


ReplayStore = AiLensReplayStore
