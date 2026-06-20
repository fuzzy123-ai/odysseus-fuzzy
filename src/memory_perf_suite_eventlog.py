"""Append-only event logs for offline Memory Perf Suite tests."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.memory_perf_suite_data import SyntheticMemoryEvent


EVENTLOG_SCHEMA = "odysseus.memory_perf_suite.eventlog.v1"
EVENTLOG_STATUSES = ("intent", "committed")


class MemoryPerfSuiteEventLogError(ValueError):
    """Raised when an event log operation is invalid."""


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status not in EVENTLOG_STATUSES:
        raise MemoryPerfSuiteEventLogError(f"unsupported event log status: {value!r}")
    return status


def _nonnegative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemoryPerfSuiteEventLogError(f"{field_name} must be a non-negative int")
    return value


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryPerfSuiteEventLogError(f"{field_name} must be a mapping")
    return value


def _event_from_any(event: SyntheticMemoryEvent | Mapping[str, Any]) -> SyntheticMemoryEvent:
    if isinstance(event, SyntheticMemoryEvent):
        return event
    return SyntheticMemoryEvent.from_dict(_mapping(event, field_name="event"))


def _stable_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest_record(data: Mapping[str, Any]) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EventLogRecord:
    sequence: int
    status: str
    event_id: str
    source_hash: str
    event: Mapping[str, Any]
    previous_digest: str
    digest: str
    schema: str = EVENTLOG_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        status: str,
        event: SyntheticMemoryEvent,
        previous_digest: str,
    ) -> "EventLogRecord":
        sequence = _nonnegative_int(sequence, field_name="sequence")
        status = _normalize_status(status)
        previous_digest = str(previous_digest or "ROOT")
        event_data = event.to_dict()
        data = {
            "schema": EVENTLOG_SCHEMA,
            "sequence": sequence,
            "status": status,
            "event_id": event.event_id,
            "source_hash": event.source_hash,
            "event": event_data,
            "previous_digest": previous_digest,
        }
        return cls(
            sequence=sequence,
            status=status,
            event_id=event.event_id,
            source_hash=event.source_hash,
            event=event_data,
            previous_digest=previous_digest,
            digest=_digest_record(data),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "sequence": self.sequence,
            "status": self.status,
            "event_id": self.event_id,
            "source_hash": self.source_hash,
            "event": dict(self.event),
            "previous_digest": self.previous_digest,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventLogRecord":
        record = _mapping(data, field_name="record")
        event = SyntheticMemoryEvent.from_dict(_mapping(record.get("event"), field_name="event"))
        parsed = cls(
            sequence=_nonnegative_int(record.get("sequence"), field_name="sequence"),
            status=_normalize_status(record.get("status")),
            event_id=str(record.get("event_id") or ""),
            source_hash=str(record.get("source_hash") or ""),
            event=event.to_dict(),
            previous_digest=str(record.get("previous_digest") or "ROOT"),
            digest=str(record.get("digest") or ""),
            schema=str(record.get("schema") or EVENTLOG_SCHEMA),
        )
        expected = EventLogRecord.create(
            sequence=parsed.sequence,
            status=parsed.status,
            event=event,
            previous_digest=parsed.previous_digest,
        )
        if parsed.event_id != event.event_id or parsed.source_hash != event.source_hash:
            raise MemoryPerfSuiteEventLogError("record id fields do not match event")
        if parsed.digest != expected.digest:
            raise MemoryPerfSuiteEventLogError("record digest mismatch")
        return parsed


@dataclass(frozen=True, slots=True)
class EventLogAppendResult:
    event_id: str
    source_hash: str
    duplicate: bool
    intent_record: EventLogRecord | None
    committed_record: EventLogRecord | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source_hash": self.source_hash,
            "duplicate": self.duplicate,
            "intent_record": self.intent_record.to_dict() if self.intent_record else None,
            "committed_record": self.committed_record.to_dict() if self.committed_record else None,
        }


@dataclass(frozen=True, slots=True)
class EventLogSummary:
    record_count: int
    committed_count: int
    unique_event_ids: int
    unique_source_hashes: int
    latest_digest: str
    schema: str = EVENTLOG_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "record_count": self.record_count,
            "committed_count": self.committed_count,
            "unique_event_ids": self.unique_event_ids,
            "unique_source_hashes": self.unique_source_hashes,
            "latest_digest": self.latest_digest,
        }


class AppendOnlyInMemoryEventLog:
    """Append-only intent/commit log backed by process memory."""

    def __init__(self, records: tuple[EventLogRecord, ...] = ()) -> None:
        self._records: list[EventLogRecord] = []
        self._event_ids: set[str] = set()
        self._source_hashes: set[str] = set()
        for record in records:
            self._append_loaded_record(record)

    @property
    def records(self) -> tuple[EventLogRecord, ...]:
        return tuple(self._records)

    def contains_event(self, *, event_id: str | None = None, source_hash: str | None = None) -> bool:
        return (event_id is not None and event_id in self._event_ids) or (
            source_hash is not None and source_hash in self._source_hashes
        )

    def append_event(self, event: SyntheticMemoryEvent | Mapping[str, Any]) -> EventLogAppendResult:
        parsed = _event_from_any(event)
        if self.contains_event(event_id=parsed.event_id, source_hash=parsed.source_hash):
            return EventLogAppendResult(
                event_id=parsed.event_id,
                source_hash=parsed.source_hash,
                duplicate=True,
                intent_record=None,
                committed_record=None,
            )
        intent_record = self.append_intent(parsed)
        committed_record = self.commit_event(parsed)
        return EventLogAppendResult(
            event_id=parsed.event_id,
            source_hash=parsed.source_hash,
            duplicate=False,
            intent_record=intent_record,
            committed_record=committed_record,
        )

    def append_intent(self, event: SyntheticMemoryEvent | Mapping[str, Any]) -> EventLogRecord:
        parsed = _event_from_any(event)
        if self.contains_event(event_id=parsed.event_id, source_hash=parsed.source_hash):
            raise MemoryPerfSuiteEventLogError("committed event already exists")
        intent_record = self._build_record(status="intent", event=parsed)
        self._append_new_record(intent_record)
        return intent_record

    def commit_event(self, event: SyntheticMemoryEvent | Mapping[str, Any]) -> EventLogRecord:
        parsed = _event_from_any(event)
        if self.contains_event(event_id=parsed.event_id, source_hash=parsed.source_hash):
            raise MemoryPerfSuiteEventLogError("committed event already exists")
        committed_record = self._build_record(status="committed", event=parsed)
        self._append_new_record(committed_record)
        self._event_ids.add(parsed.event_id)
        self._source_hashes.add(parsed.source_hash)
        return committed_record

    def committed_events(self) -> tuple[SyntheticMemoryEvent, ...]:
        return tuple(
            SyntheticMemoryEvent.from_dict(record.event)
            for record in self._records
            if record.status == "committed"
        )

    def summary(self) -> EventLogSummary:
        committed = [record for record in self._records if record.status == "committed"]
        latest_digest = self._records[-1].digest if self._records else "ROOT"
        return EventLogSummary(
            record_count=len(self._records),
            committed_count=len(committed),
            unique_event_ids=len(self._event_ids),
            unique_source_hashes=len(self._source_hashes),
            latest_digest=latest_digest,
        )

    def _build_record(self, *, status: str, event: SyntheticMemoryEvent) -> EventLogRecord:
        previous_digest = self._records[-1].digest if self._records else "ROOT"
        return EventLogRecord.create(
            sequence=len(self._records),
            status=status,
            event=event,
            previous_digest=previous_digest,
        )

    def _append_new_record(self, record: EventLogRecord) -> None:
        if record.sequence != len(self._records):
            raise MemoryPerfSuiteEventLogError("record sequence is not append-only")
        expected_previous = self._records[-1].digest if self._records else "ROOT"
        if record.previous_digest != expected_previous:
            raise MemoryPerfSuiteEventLogError("record digest chain is broken")
        self._records.append(record)

    def _append_loaded_record(self, record: EventLogRecord) -> None:
        self._append_new_record(record)
        if record.status == "committed":
            self._event_ids.add(record.event_id)
            self._source_hashes.add(record.source_hash)


class AppendOnlyJsonlEventLog(AppendOnlyInMemoryEventLog):
    """Append-only JSONL event log backed by an explicit caller-provided path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.exists() and self.path.is_dir():
            raise MemoryPerfSuiteEventLogError("event log path must be a file")
        records: list[EventLogRecord] = []
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        raise MemoryPerfSuiteEventLogError(f"invalid JSONL record at line {line_number}") from None
                    records.append(EventLogRecord.from_dict(_mapping(data, field_name="record")))
        super().__init__(tuple(records))

    def append_event(self, event: SyntheticMemoryEvent | Mapping[str, Any]) -> EventLogAppendResult:
        result = super().append_event(event)
        if result.duplicate:
            return result
        return result

    def append_intent(self, event: SyntheticMemoryEvent | Mapping[str, Any]) -> EventLogRecord:
        record = super().append_intent(event)
        self._write_record(record)
        return record

    def commit_event(self, event: SyntheticMemoryEvent | Mapping[str, Any]) -> EventLogRecord:
        record = super().commit_event(event)
        self._write_record(record)
        return record

    def _write_record(self, record: EventLogRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_stable_json(record.to_dict()) + "\n")
