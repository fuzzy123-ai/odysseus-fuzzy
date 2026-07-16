"""Synthetic-only metadata backfill preview for privacy-safe tool analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from itertools import islice
import re
from typing import Any, Iterable, Mapping

from src.builtin_tool_catalog import build_builtin_analytics_identity_contract
from src.tool_catalog import ToolAnalyticsIdentityIndex, ToolCatalogError
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS


TOOL_USAGE_BACKFILL_SCHEMA = "odysseus.tool_usage_backfill.v1"
SYNTHETIC_PRIMARY_SOURCE = "synthetic_primary_legacy"
MAX_BACKFILL_RECORDS = 10_000

_REQUIRED_FIELDS = frozenset(
    {
        "source_id",
        "legacy_event_id",
        "tool_name",
        "occurred_at",
        "status",
        "terminal",
    }
)
_SAFE_EVENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_STATUS_MAP = {
    "success": "succeeded",
    "succeeded": "succeeded",
    "error": "failed",
    "failed": "failed",
    "blocked": "blocked",
    "cancelled": "cancelled",
    "rejected": "rejected",
}
_COUNT_FIELDS = (
    "imported",
    "skipped",
    "deduped",
    "unsafe_rejected",
    "unknown",
)


class ToolUsageBackfillError(ValueError):
    """Raised when the bounded dry-run contract itself is invalid."""


@dataclass(frozen=True, slots=True)
class ToolUsageBackfillCheckpoint:
    """Immutable in-memory checkpoint; dry-runs never persist it."""

    seen_keys: frozenset[str] = frozenset()

    def contains(self, key: str) -> bool:
        return key in self.seen_keys


@dataclass(frozen=True, slots=True)
class ToolUsageBackfillPreviewRecord:
    dedupe_key: str
    occurred_at: str
    tool_analytics_id: str
    tool_family: str
    tool_source: str
    status: str
    duration_ms: None = None
    historical: bool = True

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "dedupe_key": self.dedupe_key,
            "occurred_at": self.occurred_at,
            "tool_analytics_id": self.tool_analytics_id,
            "tool_family": self.tool_family,
            "tool_source": self.tool_source,
            "status": self.status,
            "duration_ms": None,
            "historical": True,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageBackfillResult:
    records: tuple[ToolUsageBackfillPreviewRecord, ...]
    checkpoint: ToolUsageBackfillCheckpoint
    counts: tuple[tuple[str, int], ...]
    coverage_comparison: str

    def to_safe_report(self) -> dict[str, object]:
        return {
            "schema_version": TOOL_USAGE_BACKFILL_SCHEMA,
            "status": "dry_run",
            "source": "synthetic-fixture",
            "counts": dict(self.counts),
            "coverage_comparison": self.coverage_comparison,
            "writes_performed": False,
            "apply_mode_available": False,
            "raw_content_visible": False,
            "direct_identifiers_visible": False,
        }


@dataclass(frozen=True, slots=True)
class _ValidatedLegacyRecord:
    dedupe_key: str
    occurred_at: str
    tool_name: str
    status: str
    terminal: bool


def default_tool_usage_identity_contract() -> ToolAnalyticsIdentityIndex:
    """Return the canonical TAX10 identity and alias resolver."""

    return build_builtin_analytics_identity_contract(BUILTIN_TOOL_DESCRIPTIONS)


def preview_tool_usage_backfill(
    records: Iterable[Mapping[str, Any]],
    *,
    identity_contract: ToolAnalyticsIdentityIndex | None = None,
    checkpoint: ToolUsageBackfillCheckpoint | None = None,
    agent_ledger_coverage_count: int | None = None,
) -> ToolUsageBackfillResult:
    """Preview one primary legacy source without writes or count summation."""

    if isinstance(records, (str, bytes, Mapping)):
        raise ToolUsageBackfillError("records must be a bounded iterable of mappings")
    try:
        source_records = tuple(islice(records, MAX_BACKFILL_RECORDS + 1))
    except TypeError as exc:
        raise ToolUsageBackfillError(
            "records must be a bounded iterable of mappings"
        ) from exc
    if len(source_records) > MAX_BACKFILL_RECORDS:
        raise ToolUsageBackfillError("record count exceeds the dry-run limit")
    contract = identity_contract or default_tool_usage_identity_contract()
    if not isinstance(contract, ToolAnalyticsIdentityIndex):
        raise ToolUsageBackfillError("identity_contract must be a TAX10 identity index")
    prior = checkpoint or ToolUsageBackfillCheckpoint()
    if not isinstance(prior, ToolUsageBackfillCheckpoint):
        raise ToolUsageBackfillError("checkpoint must be immutable backfill state")
    coverage_count = _coverage_count(agent_ledger_coverage_count)

    counts = {field: 0 for field in _COUNT_FIELDS}
    seen = set(prior.seen_keys)
    preview: list[ToolUsageBackfillPreviewRecord] = []
    primary_terminal_count = 0

    for raw in source_records:
        if _is_primary_terminal_coverage_record(raw):
            primary_terminal_count += 1
        try:
            record = _validate_record(raw)
        except ToolUsageBackfillError:
            counts["unsafe_rejected"] += 1
            continue
        if record.dedupe_key in seen:
            counts["deduped"] += 1
            continue
        if not record.terminal:
            counts["skipped"] += 1
            seen.add(record.dedupe_key)
            continue
        try:
            identity = contract.resolve(record.tool_name)
        except (ToolCatalogError, TypeError, ValueError):
            counts["unsafe_rejected"] += 1
            continue
        if identity is None:
            counts["unknown"] += 1
            continue
        preview.append(
            ToolUsageBackfillPreviewRecord(
                dedupe_key=record.dedupe_key,
                occurred_at=record.occurred_at,
                tool_analytics_id=identity.analytics_id,
                tool_family=identity.family.value,
                tool_source=identity.source.value,
                status=record.status,
            )
        )
        counts["imported"] += 1
        seen.add(record.dedupe_key)

    comparison = _coverage_comparison(primary_terminal_count, coverage_count)
    return ToolUsageBackfillResult(
        records=tuple(preview),
        checkpoint=ToolUsageBackfillCheckpoint(frozenset(seen)),
        counts=tuple((field, counts[field]) for field in _COUNT_FIELDS),
        coverage_comparison=comparison,
    )


def _is_primary_terminal_coverage_record(raw: object) -> bool:
    """Count source coverage without accepting or inspecting unsafe payload fields."""

    return (
        isinstance(raw, Mapping)
        and raw.get("source_id") == SYNTHETIC_PRIMARY_SOURCE
        and raw.get("terminal") is True
    )


def _validate_record(raw: Mapping[str, Any]) -> _ValidatedLegacyRecord:
    if not isinstance(raw, Mapping) or set(raw) != _REQUIRED_FIELDS:
        raise ToolUsageBackfillError("record fields are not allowlisted")
    if raw.get("source_id") != SYNTHETIC_PRIMARY_SOURCE:
        raise ToolUsageBackfillError("record source is not the synthetic primary source")
    event_id = raw.get("legacy_event_id")
    if not isinstance(event_id, str) or not _SAFE_EVENT_ID_RE.fullmatch(event_id):
        raise ToolUsageBackfillError("legacy event ID is unsafe")
    tool_name = raw.get("tool_name")
    if not isinstance(tool_name, str) or not 1 <= len(tool_name) <= 80:
        raise ToolUsageBackfillError("tool name is unsafe")
    status = raw.get("status")
    if not isinstance(status, str) or status not in _STATUS_MAP:
        raise ToolUsageBackfillError("legacy status is not allowlisted")
    terminal = raw.get("terminal")
    if not isinstance(terminal, bool):
        raise ToolUsageBackfillError("terminal marker must be boolean")
    occurred_at = _utc_timestamp(raw.get("occurred_at"))
    dedupe_key = hashlib.sha256(
        f"{SYNTHETIC_PRIMARY_SOURCE}:{event_id}".encode("utf-8")
    ).hexdigest()
    return _ValidatedLegacyRecord(
        dedupe_key=dedupe_key,
        occurred_at=occurred_at,
        tool_name=tool_name,
        status=_STATUS_MAP[status],
        terminal=terminal,
    )


def _utc_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not 20 <= len(value) <= 35:
        raise ToolUsageBackfillError("timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolUsageBackfillError("timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ToolUsageBackfillError("timestamp must include an offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coverage_count(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_BACKFILL_RECORDS:
        raise ToolUsageBackfillError("coverage count must be bounded")
    return value


def _coverage_comparison(primary_count: int, reference_count: int | None) -> str:
    if reference_count is None:
        return "not_supplied"
    if primary_count == reference_count:
        return "equal"
    return "primary_lower" if primary_count < reference_count else "primary_higher"
