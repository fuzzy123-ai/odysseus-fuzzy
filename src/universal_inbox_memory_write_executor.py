"""Execution gate for Universal Inbox memory write intents.

Live writes require a ready intent plus an explicit review confirmation. The
default path is dry-run and performs no provider or RaptorGraph mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


EXECUTION_SCHEMA = "odysseus.universal_inbox.memory_write_execution.v1"


class UniversalInboxMemoryWriteExecutionError(ValueError):
    """Raised when a memory write intent cannot be executed safely."""


@dataclass(frozen=True)
class UniversalInboxMemoryWriteExecutionReport:
    status: str
    reason: str
    memory_records_planned: int
    memory_records_written: int = 0
    raptorgraph_events_planned: int = 0
    raptorgraph_events_written: int = 0
    dry_run: bool = True
    writes_performed: bool = False
    raw_content_visible: bool = False
    schema: str = EXECUTION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "raw_content_visible": False,
            "memory_records_planned": self.memory_records_planned,
            "memory_records_written": self.memory_records_written,
            "raptorgraph_events_planned": self.raptorgraph_events_planned,
            "raptorgraph_events_written": self.raptorgraph_events_written,
        }


def execute_universal_inbox_memory_write_intent(
    intent: Mapping[str, Any],
    *,
    review_confirmed: bool = False,
    dry_run: bool = True,
    memory_writer: Callable[[Mapping[str, Any]], Any] | None = None,
    raptorgraph_writer: Callable[[Mapping[str, Any]], Any] | None = None,
) -> UniversalInboxMemoryWriteExecutionReport:
    """Plan or execute a redacted memory/RaptorGraph write intent."""

    if not isinstance(intent, Mapping):
        raise UniversalInboxMemoryWriteExecutionError("intent must be a mapping")
    memory_records = tuple(intent.get("memory_records") or ())
    raptorgraph_event = intent.get("raptorgraph_event") if isinstance(intent.get("raptorgraph_event"), Mapping) else {}
    planned_raptor = 1 if raptorgraph_event and memory_records else 0

    if str(intent.get("status") or "") != "ready" or not bool(intent.get("ready_to_write")):
        return UniversalInboxMemoryWriteExecutionReport(
            status="blocked",
            reason="intent_not_ready",
            memory_records_planned=len(memory_records),
            raptorgraph_events_planned=planned_raptor,
            dry_run=True,
        )
    if not review_confirmed:
        return UniversalInboxMemoryWriteExecutionReport(
            status="review",
            reason="review_confirmation_required",
            memory_records_planned=len(memory_records),
            raptorgraph_events_planned=planned_raptor,
            dry_run=True,
        )
    if dry_run:
        return UniversalInboxMemoryWriteExecutionReport(
            status="planned",
            reason="dry_run_only",
            memory_records_planned=len(memory_records),
            raptorgraph_events_planned=planned_raptor,
            dry_run=True,
        )
    if memory_writer is None:
        raise UniversalInboxMemoryWriteExecutionError("memory_writer is required for live execution")

    memory_written = 0
    for record in memory_records:
        if not isinstance(record, Mapping):
            raise UniversalInboxMemoryWriteExecutionError("memory record must be a mapping")
        memory_writer(record)
        memory_written += 1

    raptor_written = 0
    if raptorgraph_writer is not None and raptorgraph_event:
        raptorgraph_writer(raptorgraph_event)
        raptor_written = 1

    return UniversalInboxMemoryWriteExecutionReport(
        status="written",
        reason="review_confirmed_and_writers_completed",
        memory_records_planned=len(memory_records),
        memory_records_written=memory_written,
        raptorgraph_events_planned=planned_raptor,
        raptorgraph_events_written=raptor_written,
        dry_run=False,
        writes_performed=bool(memory_written or raptor_written),
    )
