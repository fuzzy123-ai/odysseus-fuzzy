from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.update_database import migrate_tool_usage_schema
import src.tool_execution as tool_execution
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_analytics import ToolUsageAnalyticsService
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageBlockedReason,
    ToolUsageErrorClass,
    ToolUsageEventBuilder,
    ToolUsageEventError,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageReferenceKind,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
    pseudonymize_reference,
)
from src.tool_usage_instrumentation import ToolUsageInstrumentation
from src.tool_usage_store import ToolUsageStore


NOW = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)
INVOCATION_COUNT = 10_000
BATCH_INVOCATIONS = 100
WRITER_P95_BUDGET_MS = 5.0
HMAC_KEY = b"synthetic-acceptance-key-material"
REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "plans"
    / "tool-usage-acceptance-report.md"
)

STATUSES = (
    ToolUsageStatus.SUCCEEDED,
    ToolUsageStatus.FAILED,
    ToolUsageStatus.BLOCKED,
    ToolUsageStatus.CANCELLED,
    ToolUsageStatus.REJECTED,
)
LANES = (
    ("read-file", ToolFamily.CODE_FILESYSTEM, ToolSource.BUILTIN, ToolUsageSurface.AGENT),
    ("usage-plugin", ToolFamily.PLUGINS_MCP, ToolSource.PLUGIN, ToolUsageSurface.AGENT),
    ("dynamic-mcp", ToolFamily.PLUGINS_MCP, ToolSource.MCP, ToolUsageSurface.MCP),
    ("read-file", ToolFamily.CODE_FILESYSTEM, ToolSource.BUILTIN, ToolUsageSurface.SCHEDULER),
    ("app-api", ToolFamily.ADMIN_SYSTEM, ToolSource.BUILTIN, ToolUsageSurface.API),
)


def _session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _event_pair(index: int):
    analytics_id, family, source, surface = LANES[index % len(LANES)]
    status = STATUSES[(index // len(LANES)) % len(STATUSES)]
    invocation_id = f"accept_inv_{index:016d}"
    owner_ref = pseudonymize_reference(
        f"synthetic-owner-{index}",
        hmac_key=HMAC_KEY,
        kind=ToolUsageReferenceKind.OWNER,
    )
    session_ref = pseudonymize_reference(
        f"synthetic-session-{index}",
        hmac_key=HMAC_KEY,
        kind=ToolUsageReferenceKind.SESSION,
    )
    retry_ordinal = 1 if index % 10 == 0 else 0
    occurred_at = NOW + timedelta(milliseconds=index)
    shared = {
        "invocation_id": invocation_id,
        "tool_analytics_id": analytics_id,
        "tool_family": family,
        "tool_source": source,
        "surface": surface,
        "argument_size_bucket": ToolUsageSizeBucket.XS,
        "model_scope": ToolUsageModelScope.LOCAL,
        "agent_mode": (
            ToolUsageAgentMode.BACKGROUND
            if surface == ToolUsageSurface.SCHEDULER
            else ToolUsageAgentMode.AGENT
        ),
        "app_version": "0.25.0",
        "retry_ordinal": retry_ordinal,
        "owner_ref": owner_ref,
        "session_ref": session_ref,
    }
    started = ToolUsageEventBuilder.build(
        event_id=f"accept_evt_s_{index:016d}",
        event_kind=ToolUsageEventKind.STARTED,
        occurred_at=occurred_at,
        result_size_bucket=ToolUsageSizeBucket.NONE,
        result_shape_bucket=ToolUsageResultShape.NONE,
        **shared,
    ).event
    terminal_metadata = {}
    if status == ToolUsageStatus.FAILED:
        terminal_metadata["error_class"] = ToolUsageErrorClass.EXECUTION_ERROR
    elif status in {ToolUsageStatus.BLOCKED, ToolUsageStatus.REJECTED}:
        terminal_metadata["blocked_reason_code"] = ToolUsageBlockedReason.POLICY
    terminal = ToolUsageEventBuilder.build(
        event_id=f"accept_evt_t_{index:016d}",
        event_kind=ToolUsageEventKind.TERMINAL,
        occurred_at=occurred_at + timedelta(milliseconds=(index % 100) + 1),
        duration_ms=(index % 100) + 1,
        status=status,
        result_size_bucket=ToolUsageSizeBucket.S,
        result_shape_bucket=ToolUsageResultShape.SCALAR,
        **terminal_metadata,
        **shared,
    ).event
    assert started is not None and terminal is not None
    return started, terminal


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def test_10000_invocations_are_deterministic_private_complete_and_within_budget():
    engine = create_engine("sqlite:///:memory:")
    migrate_tool_usage_schema(engine)
    store = ToolUsageStore(_session_factory(engine))
    writer_ms_per_invocation = []

    for batch_start in range(0, INVOCATION_COUNT, BATCH_INVOCATIONS):
        events = []
        for index in range(batch_start, batch_start + BATCH_INVOCATIONS):
            events.extend(_event_pair(index))
        started_at = perf_counter()
        result = store.write_events(events)
        elapsed_ms = (perf_counter() - started_at) * 1_000
        assert result.inserted == BATCH_INVOCATIONS * 2
        assert result.duplicates == 0
        assert result.failures == 0
        writer_ms_per_invocation.append(elapsed_ms / BATCH_INVOCATIONS)

    writer_p95_ms = _percentile(writer_ms_per_invocation, 0.95)
    assert writer_p95_ms < WRITER_P95_BUDGET_MS

    service = ToolUsageAnalyticsService(store, clock=lambda: NOW + timedelta(hours=1))
    first = service.aggregate_day(NOW.date())
    second = service.aggregate_day(NOW.date())
    assert first.to_safe_dict() == second.to_safe_dict()

    assert first.invocations_total == INVOCATION_COUNT
    assert first.terminal_invocations == INVOCATION_COUNT
    assert first.retry_invocations == 1_000
    assert first.distinct_owner_count == INVOCATION_COUNT
    assert first.distinct_session_count == INVOCATION_COUNT
    assert first.duration_p50_ms == 50
    assert first.duration_p95_ms == 100
    assert first.quality.coverage_rate == 1.0
    assert first.quality.incomplete == 0
    assert first.quality.duplicates_rejected == 0
    assert first.quality.writer_failures == 0
    assert first.quality.unknown_identity == 0
    assert first.quality.aggregation_complete is True
    assert first.quality.instrumentation_error is False

    safe = first.to_safe_dict()
    assert safe["summary"]["status_counts"] == {
        **{status.value: 2_000 for status in STATUSES},
        "incomplete": 0,
    }
    coverage = Counter()
    for row in first.rows:
        coverage[(row.tool_source.value, row.surface.value)] += row.invocation_count
    assert coverage == {
        ("builtin", "agent"): 2_000,
        ("plugin", "agent"): 2_000,
        ("mcp", "mcp"): 2_000,
        ("builtin", "scheduler"): 2_000,
        ("builtin", "api"): 2_000,
    }

    encoded = json.dumps(safe, sort_keys=True)
    assert "synthetic-owner" not in encoded
    assert "synthetic-session" not in encoded
    assert "h1_owner_" not in encoded
    assert "h1_session_" not in encoded
    assert "accept_inv_" not in encoded
    assert "accept_evt_" not in encoded
    assert safe["raw_content_visible"] is False
    assert safe["direct_identifiers_visible"] is False
    print(f"TUA11 writer_p95_ms={writer_p95_ms:.3f}")


@pytest.mark.asyncio
async def test_writer_database_exporter_and_incognito_failures_never_change_result(
    monkeypatch,
):
    expected = ("read_file: ok", {"stdout": "unchanged", "exit_code": 0})

    async def execute(*_args, **_kwargs):
        return expected

    monkeypatch.setattr(tool_execution, "_execute_tool_block_impl", execute)

    class RaisingWriter:
        def write_events(self, _events):
            raise RuntimeError("synthetic writer detail")

    class BrokenDatabaseFactory:
        def __call__(self):
            raise RuntimeError("synthetic database detail")

    class RaisingExporter:
        def __call__(self, _event):
            raise RuntimeError("synthetic exporter detail")

    sinks = (
        RaisingWriter(),
        ToolUsageStore(BrokenDatabaseFactory()),
        RaisingExporter(),
    )
    for sink in sinks:
        instrumentation = ToolUsageInstrumentation(
            sink=sink,
            hmac_key=HMAC_KEY,
            app_version="0.25.0",
            monotonic=lambda: 1.0,
            wall_clock=lambda: NOW,
        )
        actual = await tool_execution.execute_tool_block(
            SimpleNamespace(tool_type="read_file", content="forbidden-content-marker"),
            tool_usage_instrumentation=instrumentation,
        )
        assert actual is expected
        assert instrumentation.diagnostics()["counts"] == {"sink_failures": 2}
        encoded = json.dumps(instrumentation.diagnostics(), sort_keys=True)
        assert "synthetic writer detail" not in encoded
        assert "synthetic database detail" not in encoded
        assert "synthetic exporter detail" not in encoded

    class MustNotWrite:
        def write_events(self, _events):
            raise AssertionError("incognito reached writer")

    incognito = ToolUsageInstrumentation(
        sink=MustNotWrite(),
        hmac_key=HMAC_KEY,
        app_version="0.25.0",
        incognito=True,
        monotonic=lambda: 1.0,
        wall_clock=lambda: NOW,
    )
    actual = await tool_execution.execute_tool_block(
        SimpleNamespace(tool_type="read_file", content="forbidden-content-marker"),
        tool_usage_instrumentation=incognito,
    )
    assert actual is expected
    assert incognito.diagnostics()["counts"] == {"suppressed_invocations": 1}


@pytest.mark.parametrize("field", ["payload", "path", "email", "command"])
def test_forbidden_content_fields_and_direct_references_fail_closed(field):
    values = {
        "event_id": "accept_evt_forbidden_0001",
        "invocation_id": "accept_inv_forbidden_0001",
        "event_kind": ToolUsageEventKind.STARTED,
        "occurred_at": NOW,
        "tool_analytics_id": "read-file",
        "tool_family": ToolFamily.CODE_FILESYSTEM,
        "tool_source": ToolSource.BUILTIN,
        "surface": ToolUsageSurface.AGENT,
        "argument_size_bucket": ToolUsageSizeBucket.NONE,
        "result_size_bucket": ToolUsageSizeBucket.NONE,
        "result_shape_bucket": ToolUsageResultShape.NONE,
        "model_scope": ToolUsageModelScope.LOCAL,
        "agent_mode": ToolUsageAgentMode.AGENT,
        "app_version": "0.25.0",
        field: "forbidden-content-marker",
    }
    with pytest.raises(TypeError):
        ToolUsageEventBuilder.build(**values)

    values.pop(field)
    values["owner_ref"] = "direct-owner-marker"
    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(**values)


def test_acceptance_report_contains_aggregate_and_technical_status_only():
    report = REPORT_PATH.read_text(encoding="utf-8")

    assert "Overall result: `GO`" in report
    assert "10,000" in report
    assert "Built-in" in report
    assert "Plugin" in report
    assert "MCP" in report
    assert "Scheduler" in report
    assert "API" in report
    for forbidden in (
        "h1_owner_",
        "h1_session_",
        "accept_inv_",
        "accept_evt_",
        "forbidden-content-marker",
        "C:\\",
    ):
        assert forbidden not in report
