from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from time import perf_counter

import pytest

from src.builtin_tool_catalog import (
    build_builtin_descriptor_catalog,
    build_tool_analytics_identity_contract,
)
from src.tool_catalog import (
    ToolDescriptorV2,
    ToolSource,
)
from src.tool_usage_analytics import ToolUsageAnalyticsService
from src.tool_usage_context import TrustedToolUsageContext
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageBlockedReason,
    ToolUsageErrorClass,
    ToolUsageEventBuilder,
    ToolUsageEventError,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageResultShape,
    ToolUsageStatus,
    ToolUsageSurface,
)
from src.tool_usage_instrumentation import (
    ToolUsageInstrumentation,
    normalize_tool_usage_outcome,
)
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
    ("read_file", ToolSource.BUILTIN, ToolUsageSurface.AGENT),
    ("usage_plugin", ToolSource.PLUGIN, ToolUsageSurface.AGENT),
    ("mcp_lookup", ToolSource.MCP, ToolUsageSurface.MCP),
    ("read_file", ToolSource.BUILTIN, ToolUsageSurface.SCHEDULER),
    ("app_api", ToolSource.BUILTIN, ToolUsageSurface.API),
)

_IDENTITY_CONTRACT = build_tool_analytics_identity_contract()
_BUILTIN_DESCRIPTORS = {
    descriptor.tool_id: descriptor
    for descriptor in build_builtin_descriptor_catalog().descriptors
}


def _descriptor(
    runtime_name: str,
    source: ToolSource,
) -> ToolDescriptorV2:
    """Use the production identity resolver for built-in and dynamic fixtures."""

    if source == ToolSource.BUILTIN:
        return _BUILTIN_DESCRIPTORS[runtime_name]
    identity = _IDENTITY_CONTRACT.resolve(runtime_name, source=source)
    return ToolDescriptorV2.conservative_dynamic(
        tool_id=identity.analytics_id,
        source=identity.source,
        source_id="acceptance-source",
    )


def _event_pair(index: int):
    runtime_name, source, surface = LANES[index % len(LANES)]
    status = STATUSES[(index // len(LANES)) % len(STATUSES)]
    builder = ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY)
    occurred_at = NOW + timedelta(milliseconds=index)
    common = {
        "descriptor": _descriptor(runtime_name, source),
        "surface": surface,
        "agent_mode": (
            ToolUsageAgentMode.BACKGROUND_SYSTEM
            if surface == ToolUsageSurface.SCHEDULER
            else ToolUsageAgentMode.AGENT
        ),
        "model_scope": ToolUsageModelScope.LOCAL,
        "retry_ordinal": 1 if index % 10 == 0 else 0,
        "owner_identity": f"synthetic-owner-{index}",
        "session_identity": f"synthetic-session-{index}",
        "invocation_id": "tui_" + f"{index:032x}",
    }
    started = builder.build(
        **common,
        event_kind=ToolUsageEventKind.STARTED,
        event_id="tue_" + f"{index * 2:032x}",
        occurred_at=occurred_at,
    )
    terminal_metadata = {}
    if status == ToolUsageStatus.FAILED:
        terminal_metadata["error_class"] = ToolUsageErrorClass.EXECUTION
    elif status in {ToolUsageStatus.BLOCKED, ToolUsageStatus.REJECTED}:
        terminal_metadata["blocked_reason_code"] = ToolUsageBlockedReason.POLICY
    terminal = builder.build(
        **common,
        **terminal_metadata,
        event_kind=ToolUsageEventKind.TERMINAL,
        event_id="tue_" + f"{index * 2 + 1:032x}",
        occurred_at=occurred_at + timedelta(milliseconds=(index % 100) + 1),
        status=status,
        duration_ms=(index % 100) + 1,
        result_size_bytes=24,
        result_shape=ToolUsageResultShape.SCALAR,
    )
    return started, terminal


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def test_10000_invocations_are_deterministic_private_complete_and_within_budget():
    store = ToolUsageStore(":memory:")
    store.migrate()
    writer_ms_per_invocation = []
    try:
        for batch_start in range(0, INVOCATION_COUNT, BATCH_INVOCATIONS):
            events = []
            for index in range(batch_start, batch_start + BATCH_INVOCATIONS):
                events.extend(_event_pair(index))
            started_at = perf_counter()
            result = store.append_events(events)
            elapsed_ms = (perf_counter() - started_at) * 1_000
            assert result.accepted_count == BATCH_INVOCATIONS * 2
            assert result.duplicate_count == 0
            assert result.persistence_rejected_count == 0
            assert result.failure_count == 0
            writer_ms_per_invocation.append(elapsed_ms / BATCH_INVOCATIONS)

        writer_p95_ms = _percentile(writer_ms_per_invocation, 0.95)
        assert writer_p95_ms < WRITER_P95_BUDGET_MS

        service = ToolUsageAnalyticsService(store)
        day = NOW.date().isoformat()
        first = service.aggregate_day(day)
        second = service.aggregate_day(day)
        summary = service.summarize(day, day)
        assert first == second

        assert first["invocation_count"] == INVOCATION_COUNT
        assert first["terminal_count"] == INVOCATION_COUNT
        assert first["complete_count"] == INVOCATION_COUNT
        assert first["incomplete_count"] == 0
        assert first["distinct_owner_count"] == INVOCATION_COUNT
        assert first["distinct_session_count"] == INVOCATION_COUNT
        assert first["coverage"] == 1.0
        assert first["duplicates_rejected"] == 0
        assert first["writer_failures"] == 0

        assert summary["calls"] == INVOCATION_COUNT
        assert summary["retry_count"] == 1_000
        assert summary["duration_p50_ms"] == 50
        assert summary["duration_p95_ms"] == 100
        assert summary["quality"]["aggregation_complete_day_count"] == 1
        assert summary["quality"]["warning_codes"] == ()
        assert summary["status_counts"] == {
            **{status.value: 2_000 for status in STATUSES},
        }
        coverage = Counter()
        for row in summary["rows"]:
            coverage[(row["tool_source"], row["surface"])] += row["invocation_count"]
        assert coverage == {
            ("builtin", "agent"): 2_000,
            ("plugin", "agent"): 2_000,
            ("mcp", "mcp"): 2_000,
            ("builtin", "scheduler"): 2_000,
            ("builtin", "api"): 2_000,
        }
        analytics_ids = {row["tool_analytics_id"] for row in summary["rows"]}
        assert {"dynamic.plugin.unclassified", "dynamic.mcp.unclassified"} <= analytics_ids
        assert "usage_plugin" not in analytics_ids
        assert "mcp_lookup" not in analytics_ids

        encoded = json.dumps(summary, sort_keys=True)
        for private_marker in (
            "synthetic-owner",
            "synthetic-session",
            "h1_",
            "tue_",
            "tui_",
        ):
            assert private_marker not in encoded
        assert summary["raw_content_visible"] is False
    finally:
        store.close()


class _RaisingAppendSink:
    def append_best_effort(self, _events):
        raise RuntimeError("synthetic private sink detail")


class _WriterSpy:
    def __init__(self):
        self.calls = 0

    def append_best_effort(self, _events):
        self.calls += 1
        raise AssertionError("incognito must not write")


def _instrumentation(*, sink, context: TrustedToolUsageContext | None = None):
    return ToolUsageInstrumentation(
        builder=ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY),
        sink=sink,
        context=context
        or TrustedToolUsageContext.create(
            surface=ToolUsageSurface.AGENT,
            agent_mode=ToolUsageAgentMode.AGENT,
            model_scope=ToolUsageModelScope.LOCAL,
            owner_identity="private-owner",
            session_identity="private-session",
        ),
        clock=lambda: NOW,
    )


def _emit_pair(instrumentation: ToolUsageInstrumentation) -> None:
    invocation = instrumentation.begin("read_file", "private argument marker")
    instrumentation.finish(
        invocation,
        outcome=normalize_tool_usage_outcome(result={"output": "private result"}),
        duration_ms=1,
    )


def test_instrumentation_sinks_fail_open_with_redacted_current_diagnostics():
    closed_store = ToolUsageStore(":memory:")
    closed_store.migrate()
    closed_store.close()

    sinks = (
        _RaisingAppendSink(),
        closed_store,
        lambda _event: (_ for _ in ()).throw(RuntimeError("private callable detail")),
    )
    for sink in sinks:
        instrumentation = _instrumentation(sink=sink)
        _emit_pair(instrumentation)
        diagnostics = instrumentation.diagnostics()
        expected_failure = "sink_rejected" if sink is closed_store else "sink_failure"
        assert diagnostics["failures"] == {expected_failure: 2}
        assert diagnostics["suppressed"] == {}
        assert diagnostics["raw_content_visible"] is False
        encoded = json.dumps(diagnostics, sort_keys=True)
        for marker in ("private argument", "private result", "private sink detail", "private callable detail"):
            assert marker not in encoded

    writer = _WriterSpy()
    incognito = _instrumentation(
        sink=writer,
        context=TrustedToolUsageContext.create(
            surface="chat",
            agent_mode="background_system",
            model_scope="remote",
            owner_identity="private-owner",
            session_identity="private-session",
            incognito=True,
        ),
    )
    assert incognito.begin("read_file", "private argument marker") is None
    assert writer.calls == 0
    assert incognito.diagnostics()["suppressed"] == {"incognito": 1}


@pytest.mark.parametrize("field", ["payload", "path", "email", "command"])
def test_forbidden_content_fields_and_direct_references_fail_closed(field):
    builder = ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY)
    descriptor = _descriptor("read_file", ToolSource.BUILTIN)
    common = {
        "descriptor": descriptor,
        "event_kind": ToolUsageEventKind.STARTED,
        "surface": ToolUsageSurface.AGENT,
        "agent_mode": ToolUsageAgentMode.AGENT,
        "owner_identity": "allowed-input-before-hmac",
        "event_id": "tue_" + "a" * 32,
        "invocation_id": "tui_" + "b" * 32,
        "occurred_at": NOW,
    }
    valid = builder.build(**common)
    assert valid.owner_ref is not None and valid.owner_ref.startswith("h1_")

    with pytest.raises(TypeError):
        builder.build(**common, **{field: "forbidden-content-marker"})
    with pytest.raises(TypeError):
        builder.build(**common, owner_ref="h1_" + "c" * 32)
    with pytest.raises(ToolUsageEventError):
        builder.build(**{**common, "owner_identity": lambda: "direct-owner"})


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
        "h1_",
        "tue_",
        "tui_",
        "forbidden-content-marker",
        "C:\\",
    ):
        assert forbidden not in report
