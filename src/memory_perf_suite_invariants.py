"""Recovery invariant checks for synthetic memory durability runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.memory_perf_suite_data import SyntheticMemoryEvent, assert_no_forbidden_durable_keys
from src.memory_perf_suite_eventlog import AppendOnlyInMemoryEventLog, EventLogRecord


class MemoryPerfSuiteInvariantError(ValueError):
    """Raised when recovery state cannot be checked safely."""


@dataclass(frozen=True, slots=True)
class RecoveredMemoryState:
    committed_event_ids: tuple[str, ...]
    canonical_memory: dict[str, dict[str, Any]]
    derived_edges: tuple[tuple[str, str], ...]
    duplicate_event_ids: tuple[str, ...] = ()

    @property
    def committed_count(self) -> int:
        return len(self.committed_event_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "committed_event_ids": self.committed_event_ids,
            "canonical_count": len(self.canonical_memory),
            "derived_edges": self.derived_edges,
            "duplicate_event_ids": self.duplicate_event_ids,
        }


@dataclass(frozen=True, slots=True)
class InvariantCheckResult:
    status: str
    expected_event_count: int
    recovered_event_count: int
    recovered_state: RecoveredMemoryState
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "expected_event_count": self.expected_event_count,
            "recovered_event_count": self.recovered_event_count,
            "failures": self.failures,
            "recovered_state": self.recovered_state.to_dict(),
        }


def recover_memory_state(records: Iterable[EventLogRecord]) -> RecoveredMemoryState:
    committed_ids: list[str] = []
    duplicate_ids: list[str] = []
    canonical_memory: dict[str, dict[str, Any]] = {}
    derived_edges: list[tuple[str, str]] = []

    for record in records:
        if record.status != "committed":
            continue
        event = SyntheticMemoryEvent.from_dict(record.event)
        assert_no_forbidden_durable_keys(event.to_dict())
        if event.event_id in canonical_memory:
            duplicate_ids.append(event.event_id)
            continue
        committed_ids.append(event.event_id)
        canonical_memory[event.event_id] = {
            "event_id": event.event_id,
            "source_hash": event.source_hash,
            "subject_hash": event.subject_hash,
            "event_type": event.event_type,
            "durable_fields": dict(event.durable_fields),
        }
        derived_edges.append((event.event_id, event.subject_hash))

    return RecoveredMemoryState(
        committed_event_ids=tuple(committed_ids),
        canonical_memory=canonical_memory,
        derived_edges=tuple(derived_edges),
        duplicate_event_ids=tuple(dict.fromkeys(duplicate_ids)),
    )


def check_recovery_invariants(
    expected_committed_events: Iterable[SyntheticMemoryEvent],
    event_log: AppendOnlyInMemoryEventLog,
) -> InvariantCheckResult:
    expected = tuple(expected_committed_events)
    expected_ids = tuple(event.event_id for event in expected)
    recovered = recover_memory_state(event_log.records)
    failures: list[str] = []

    missing = tuple(event_id for event_id in expected_ids if event_id not in recovered.committed_event_ids)
    unexpected = tuple(event_id for event_id in recovered.committed_event_ids if event_id not in expected_ids)
    if missing:
        failures.append("committed_event_missing")
    if unexpected:
        failures.append("unexpected_committed_event")
    if recovered.duplicate_event_ids:
        failures.append("duplicate_canonical_memory")
    if len(recovered.derived_edges) != recovered.committed_count:
        failures.append("derived_rebuild_mismatch")

    return InvariantCheckResult(
        status="failed" if failures else "passed",
        expected_event_count=len(expected_ids),
        recovered_event_count=recovered.committed_count,
        recovered_state=recovered,
        failures=tuple(dict.fromkeys(failures)),
    )
