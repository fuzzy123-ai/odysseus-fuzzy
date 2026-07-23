"""Synthetic-only metadata backfill planning for privacy-safe tool usage.

This module intentionally has no database writer and no production-data
reader. It validates the one bundled persisted-chat metadata fixture, builds
content-free terminal events in memory, and returns category counts only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from src.builtin_tool_catalog import resolve_tool_analytics_identity
from src.tool_catalog import ToolAnalyticsIdentityV1, ToolSource
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageErrorClass,
    ToolUsageEventKind,
    ToolUsageEventV1,
    ToolUsageModelScope,
    ToolUsagePersistenceReason,
    ToolUsageReferenceState,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
)


BACKFILL_SCHEMA = "odysseus.tool_usage_legacy_backfill.v1"
SYNTHETIC_FIXTURE_SCHEMA = "odysseus.synthetic_tool_usage_legacy_fixture.v1"
PRIMARY_SOURCE = "persisted_chat_tool_metadata"
BACKFILL_COUNT_FIELDS = (
    "imported",
    "skipped",
    "deduped",
    "unsafe_rejected",
    "unknown",
)
MAX_SYNTHETIC_RECORDS = 10_000
_MAX_COUNT = 1_000_000_000
_SOURCE_RECORD_ID_RE = re.compile(r"^syn_[a-z0-9_-]{1,64}$")
_LEGACY_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_CHECKPOINT_RE = re.compile(r"^bf1_[0-9a-f]{32}$")


class ToolUsageBackfillError(ValueError):
    """Raised when the synthetic backfill contract is malformed."""


@dataclass(frozen=True, slots=True)
class BackfillCounts:
    imported: int = 0
    skipped: int = 0
    deduped: int = 0
    unsafe_rejected: int = 0
    unknown: int = 0

    def __post_init__(self) -> None:
        for field_name in BACKFILL_COUNT_FIELDS:
            _bounded_count(getattr(self, field_name), field_name)

    def to_dict(self) -> dict[str, int]:
        return {field: int(getattr(self, field)) for field in BACKFILL_COUNT_FIELDS}


@dataclass(frozen=True, slots=True)
class BackfillReport:
    counts: BackfillCounts

    def __post_init__(self) -> None:
        if not isinstance(self.counts, BackfillCounts):
            raise ToolUsageBackfillError("backfill report requires bounded counts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": BACKFILL_SCHEMA,
            "dry_run": True,
            "counts": self.counts.to_dict(),
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class LegacyCoverageComparison:
    primary_record_count: int
    agent_ledger_start_count: int
    agent_ledger_imported_count: int = 0
    additive: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "primary_record_count",
            "agent_ledger_start_count",
            "agent_ledger_imported_count",
        ):
            _bounded_count(getattr(self, field_name), field_name)
        if self.agent_ledger_imported_count != 0 or self.additive is not False:
            raise ToolUsageBackfillError("agent ledger must remain coverage-only")


@dataclass(frozen=True, slots=True)
class LegacyBackfillDryRunResult:
    report: BackfillReport
    coverage: LegacyCoverageComparison


class BackfillCheckpoint:
    """In-memory, content-free dedupe checkpoint for synthetic dry-runs."""

    def __init__(self, fingerprints: Iterable[str] = ()):
        self._fingerprints: set[str] = set()
        for fingerprint in fingerprints:
            self.add(fingerprint)

    def __contains__(self, fingerprint: object) -> bool:
        return isinstance(fingerprint, str) and fingerprint in self._fingerprints

    def __len__(self) -> int:
        return len(self._fingerprints)

    def add(self, fingerprint: str) -> None:
        text = str(fingerprint or "")
        if not _CHECKPOINT_RE.fullmatch(text):
            raise ToolUsageBackfillError("checkpoint fingerprint is not canonical")
        self._fingerprints.add(text)

    def snapshot(self) -> tuple[str, ...]:
        return tuple(sorted(self._fingerprints))


@dataclass(frozen=True, slots=True)
class _LegacyCandidate:
    fingerprint: str
    occurred_at: str
    identity: ToolAnalyticsIdentityV1
    status: ToolUsageStatus


class _SkipRecord(Exception):
    pass


class _UnsafeRecord(Exception):
    pass


def dry_run_synthetic_fixture(
    fixture: Mapping[str, Any],
    *,
    checkpoint: BackfillCheckpoint | None = None,
) -> LegacyBackfillDryRunResult:
    """Validate the bundled source shape and perform an in-memory dry-run."""

    records, agent_start_count = _fixture_inputs(fixture)
    state = checkpoint if checkpoint is not None else BackfillCheckpoint()
    totals = {field: 0 for field in BACKFILL_COUNT_FIELDS}

    for record in records:
        try:
            candidate = _candidate_from_record(record)
        except _SkipRecord:
            totals["skipped"] += 1
            continue
        except (ToolUsageBackfillError, _UnsafeRecord, ValueError, TypeError):
            totals["unsafe_rejected"] += 1
            continue

        if candidate.fingerprint in state:
            totals["deduped"] += 1
            continue
        _build_terminal_event(candidate)
        state.add(candidate.fingerprint)
        totals["imported"] += 1
        if candidate.identity.source_bucket:
            totals["unknown"] += 1

    counts = BackfillCounts(**totals)
    return LegacyBackfillDryRunResult(
        report=BackfillReport(counts),
        coverage=LegacyCoverageComparison(
            primary_record_count=len(records),
            agent_ledger_start_count=agent_start_count,
        ),
    )


def _fixture_inputs(
    fixture: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], int]:
    if not isinstance(fixture, Mapping):
        raise ToolUsageBackfillError("synthetic fixture must be a mapping")
    if set(fixture) != {
        "schema",
        "source",
        "records",
        "agent_ledger_coverage",
    }:
        raise ToolUsageBackfillError("synthetic fixture fields are not allowlisted")
    if fixture.get("schema") != SYNTHETIC_FIXTURE_SCHEMA:
        raise ToolUsageBackfillError("synthetic fixture schema is unsupported")
    if fixture.get("source") != PRIMARY_SOURCE:
        raise ToolUsageBackfillError("only persisted chat metadata is a primary source")
    raw_records = fixture.get("records")
    if not isinstance(raw_records, (list, tuple)):
        raise ToolUsageBackfillError("synthetic records must be a bounded sequence")
    if len(raw_records) > MAX_SYNTHETIC_RECORDS:
        raise ToolUsageBackfillError("synthetic record limit exceeded")

    coverage = fixture.get("agent_ledger_coverage")
    if not isinstance(coverage, Mapping) or set(coverage) != {"start_count"}:
        raise ToolUsageBackfillError("agent ledger coverage must be count-only")
    agent_start_count = _bounded_count(
        coverage.get("start_count"), "agent ledger start count"
    )
    return tuple(raw_records), agent_start_count


def _candidate_from_record(record: Mapping[str, Any]) -> _LegacyCandidate:
    if not isinstance(record, Mapping):
        raise _UnsafeRecord

    source_record_id = record.get("source_record_id")
    tool_name = record.get("tool")
    occurred_at = record.get("occurred_at")
    if source_record_id in (None, "") or tool_name in (None, "") or occurred_at in (None, ""):
        raise _UnsafeRecord
    if "exit_code" not in record or record.get("exit_code") is None:
        raise _SkipRecord
    if not isinstance(source_record_id, str) or not _SOURCE_RECORD_ID_RE.fullmatch(
        source_record_id
    ):
        raise _UnsafeRecord
    if not isinstance(tool_name, str) or not _LEGACY_TOOL_NAME_RE.fullmatch(tool_name):
        raise _UnsafeRecord
    timestamp = _canonical_timestamp(occurred_at)
    exit_code = record.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise _UnsafeRecord
    if exit_code < -255 or exit_code > 255:
        raise _UnsafeRecord

    identity = resolve_tool_analytics_identity(tool_name, source=ToolSource.LEGACY)
    status = ToolUsageStatus.SUCCEEDED if exit_code == 0 else ToolUsageStatus.FAILED
    fingerprint_payload = {
        "source_record_id": source_record_id,
        "occurred_at": timestamp,
        "tool_analytics_id": identity.analytics_id,
        "status": status.value,
    }
    encoded = json.dumps(
        fingerprint_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = "bf1_" + hashlib.sha256(encoded).hexdigest()[:32]
    return _LegacyCandidate(
        fingerprint=fingerprint,
        occurred_at=timestamp,
        identity=identity,
        status=status,
    )


def _build_terminal_event(candidate: _LegacyCandidate) -> ToolUsageEventV1:
    if not isinstance(candidate, _LegacyCandidate):
        raise ToolUsageBackfillError("legacy candidate is invalid")
    seed = candidate.fingerprint.encode("ascii")
    invocation_id = "tui_" + hashlib.sha256(b"invocation\0" + seed).hexdigest()[:32]
    event_id = "tue_" + hashlib.sha256(b"event\0" + seed).hexdigest()[:32]
    return ToolUsageEventV1(
        event_id=event_id,
        invocation_id=invocation_id,
        event_kind=ToolUsageEventKind.TERMINAL,
        occurred_at=candidate.occurred_at,
        duration_ms=None,
        tool_analytics_id=candidate.identity.analytics_id,
        tool_family=candidate.identity.family,
        tool_source=candidate.identity.source,
        surface=ToolUsageSurface.CHAT,
        status=candidate.status,
        error_class=(
            ToolUsageErrorClass.EXECUTION
            if candidate.status == ToolUsageStatus.FAILED
            else None
        ),
        blocked_reason_code=None,
        retry_ordinal=0,
        argument_size_bucket=ToolUsageSizeBucket.NONE,
        result_size_bucket=ToolUsageSizeBucket.NONE,
        result_shape_bucket=ToolUsageResultShape.NONE,
        owner_ref=None,
        session_ref=None,
        run_ref=None,
        correlation_ref=None,
        reference_state=ToolUsageReferenceState.NOT_REQUESTED,
        model_scope=ToolUsageModelScope.UNKNOWN,
        agent_mode=ToolUsageAgentMode.CHAT,
        app_version="0.25.0-legacy",
        persistence_allowed=True,
        persistence_reason=ToolUsagePersistenceReason.ALLOWED,
    )


def _canonical_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise _UnsafeRecord
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise _UnsafeRecord from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise _UnsafeRecord
    normalized = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if normalized != value:
        raise _UnsafeRecord
    return normalized


def _bounded_count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolUsageBackfillError(f"{field_name} must be an integer")
    if value < 0 or value > _MAX_COUNT:
        raise ToolUsageBackfillError(f"{field_name} is outside the bounded range")
    return value
