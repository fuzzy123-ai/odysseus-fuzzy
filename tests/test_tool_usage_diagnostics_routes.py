from datetime import datetime, timedelta, timezone
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.database as core_database
from routes import diagnostics_routes
from scripts.update_database import migrate_tool_usage_schema
from src import tool_usage_analytics
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageErrorClass,
    ToolUsageEventBuilder,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
    pseudonymize_reference,
)
from src.tool_usage_store import ToolUsageStore


BASE_TIME = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
HMAC_KEY = b"synthetic-api-key-material"


def _app():
    app = FastAPI()
    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))
    return app


def _factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    migrate_tool_usage_schema(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(core_database, "SessionLocal", factory)
    return factory


def _event(
    *,
    event_index,
    invocation_index,
    event_kind,
    occurred_at,
    duration_ms=None,
    status=None,
    retry_ordinal=0,
    tool_analytics_id="read-file",
    tool_family=ToolFamily.CODE_FILESYSTEM,
    tool_source=ToolSource.BUILTIN,
    surface=ToolUsageSurface.AGENT,
    owner_ref=None,
    session_ref=None,
):
    kwargs = {}
    if status == ToolUsageStatus.FAILED:
        kwargs["error_class"] = ToolUsageErrorClass.EXECUTION_ERROR
    built = ToolUsageEventBuilder.build(
        event_id=f"evt_{event_index:016d}",
        invocation_id=f"inv_{invocation_index:016d}",
        event_kind=event_kind,
        occurred_at=occurred_at,
        duration_ms=duration_ms,
        tool_analytics_id=tool_analytics_id,
        tool_family=tool_family,
        tool_source=tool_source,
        surface=surface,
        status=status,
        retry_ordinal=retry_ordinal,
        argument_size_bucket=ToolUsageSizeBucket.XS,
        result_size_bucket=(
            ToolUsageSizeBucket.S
            if event_kind == ToolUsageEventKind.TERMINAL
            else ToolUsageSizeBucket.NONE
        ),
        result_shape_bucket=(
            ToolUsageResultShape.SCALAR
            if event_kind == ToolUsageEventKind.TERMINAL
            else ToolUsageResultShape.NONE
        ),
        owner_ref=owner_ref,
        session_ref=session_ref,
        model_scope=ToolUsageModelScope.LOCAL,
        agent_mode=ToolUsageAgentMode.AGENT,
        app_version="0.25.0",
        **kwargs,
    )
    assert built.event is not None
    return built.event


def _pair(index, *, occurred_at, duration_ms, status, **kwargs):
    return (
        _event(
            event_index=index * 2,
            invocation_index=index,
            event_kind=ToolUsageEventKind.STARTED,
            occurred_at=occurred_at,
            retry_ordinal=kwargs.get("retry_ordinal", 0),
            tool_analytics_id=kwargs.get("tool_analytics_id", "read-file"),
            tool_family=kwargs.get("tool_family", ToolFamily.CODE_FILESYSTEM),
            tool_source=kwargs.get("tool_source", ToolSource.BUILTIN),
            surface=kwargs.get("surface", ToolUsageSurface.AGENT),
            owner_ref=kwargs.get("owner_ref"),
            session_ref=kwargs.get("session_ref"),
        ),
        _event(
            event_index=index * 2 + 1,
            invocation_index=index,
            event_kind=ToolUsageEventKind.TERMINAL,
            occurred_at=occurred_at + timedelta(milliseconds=duration_ms),
            duration_ms=duration_ms,
            status=status,
            **kwargs,
        ),
    )


def _seed_events(monkeypatch):
    factory = _factory(monkeypatch)
    owner_a = pseudonymize_reference(
        "synthetic-owner-a",
        hmac_key=HMAC_KEY,
        kind="owner",
    )
    owner_b = pseudonymize_reference(
        "synthetic-owner-b",
        hmac_key=HMAC_KEY,
        kind="owner",
    )
    session_a = pseudonymize_reference(
        "synthetic-session-a",
        hmac_key=HMAC_KEY,
        kind="session",
    )
    events = (
        *_pair(
            1,
            occurred_at=BASE_TIME,
            duration_ms=10,
            status=ToolUsageStatus.SUCCEEDED,
            owner_ref=owner_a,
            session_ref=session_a,
        ),
        *_pair(
            2,
            occurred_at=BASE_TIME,
            duration_ms=100,
            status=ToolUsageStatus.FAILED,
            retry_ordinal=1,
            tool_analytics_id="usage-plugin",
            tool_family=ToolFamily.PLUGINS_MCP,
            tool_source=ToolSource.PLUGIN,
            surface=ToolUsageSurface.SCHEDULER,
            owner_ref=owner_b,
            session_ref=session_a,
        ),
        _event(
            event_index=6,
            invocation_index=3,
            event_kind=ToolUsageEventKind.STARTED,
            occurred_at=BASE_TIME + timedelta(days=1),
            tool_analytics_id="dynamic-unclassified",
            tool_family=ToolFamily.UNCLASSIFIED_DYNAMIC,
            tool_source=ToolSource.DYNAMIC,
            surface=ToolUsageSurface.SYSTEM,
        ),
    )
    assert ToolUsageStore(factory).write_events(events).inserted == 5
    return owner_a, owner_b, session_a


def test_tool_usage_route_fails_closed_for_unauthenticated_and_non_admin(monkeypatch):
    calls = []

    def read_usage(**_kwargs):
        calls.append("read")
        return {"schema_version": "unexpected"}

    monkeypatch.setattr(tool_usage_analytics, "read_tool_usage_analytics", read_usage)
    client = TestClient(_app())

    def deny_unauthenticated(_request: Request):
        raise HTTPException(401, "Authentication required")

    monkeypatch.setattr(diagnostics_routes, "require_admin", deny_unauthenticated)
    assert client.get("/api/diagnostics/tool-usage").status_code == 401
    assert calls == []

    def deny_non_admin(_request: Request):
        raise HTTPException(403, "Admin only")

    monkeypatch.setattr(diagnostics_routes, "require_admin", deny_non_admin)
    assert client.get("/api/diagnostics/tool-usage").status_code == 403
    assert calls == []


def test_tool_usage_route_returns_only_bounded_aggregate_projection(monkeypatch):
    owner_a, owner_b, session_a = _seed_events(monkeypatch)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)

    response = TestClient(_app()).get(
        "/api/diagnostics/tool-usage",
        params={"start": "2026-07-15", "end": "2026-07-16"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["range"] == {"start": "2026-07-15", "end": "2026-07-16", "days": 2}
    assert payload["summary"]["calls"] == 3
    assert payload["summary"]["active_days"] == 2
    assert payload["summary"]["pseudonymous_distinct_owner_count"] == 2
    assert payload["summary"]["pseudonymous_distinct_session_count"] == 1
    assert payload["summary"]["calls_per_session"] == 3.0
    assert payload["summary"]["coverage_rate"] == 2 / 3
    assert payload["summary"]["duration_p50_ms"] == 10
    assert payload["summary"]["duration_p95_ms"] == 100
    assert payload["summary"]["retry_invocations"] == 1
    assert payload["summary"]["status_counts"]["succeeded"] == 1
    assert payload["summary"]["status_counts"]["failed"] == 1
    assert payload["summary"]["status_rates"]["succeeded"] == 0.5
    assert payload["summary"]["status_rates"]["failed"] == 0.5
    assert payload["quality"]["incomplete"] == 1
    assert payload["quality"]["unknown_identity"] == 1
    assert payload["quality"]["warnings"] == [
        "incomplete_invocations",
        "unknown_identity",
    ]
    assert payload["raw_records_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["direct_identifiers_visible"] is False
    encoded = json.dumps(payload, sort_keys=True)
    assert owner_a not in encoded
    assert owner_b not in encoded
    assert session_a not in encoded
    assert "evt_" not in encoded
    assert "inv_" not in encoded


def test_filters_are_canonical_and_result_rows_are_bounded(monkeypatch):
    _seed_events(monkeypatch)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    client = TestClient(_app())

    filtered = client.get(
        "/api/diagnostics/tool-usage",
        params={
            "start": "2026-07-15",
            "end": "2026-07-16",
            "tool": "read-file",
            "family": "code_filesystem",
            "source": "builtin",
            "surface": "agent",
            "status": "succeeded",
        },
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["summary"]["calls"] == 1
    assert filtered_payload["rows"] == [
        {
            "tool_analytics_id": "read-file",
            "tool_family": "code_filesystem",
            "tool_source": "builtin",
            "surface": "agent",
            "status": "succeeded",
            "calls": 1,
        }
    ]

    bounded = client.get(
        "/api/diagnostics/tool-usage",
        params={"start": "2026-07-15", "end": "2026-07-16", "limit": 1},
    )
    assert bounded.status_code == 200
    bounded_payload = bounded.json()
    assert len(bounded_payload["rows"]) == 1
    assert bounded_payload["summary"]["calls"] == 3
    assert bounded_payload["quality"]["result_truncated"] is True
    assert "result_truncated" in bounded_payload["quality"]["warnings"]


def test_invalid_filters_and_ranges_fail_without_echoing_values(monkeypatch):
    _factory(monkeypatch)
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    client = TestClient(_app())

    invalid_family = client.get(
        "/api/diagnostics/tool-usage",
        params={"family": "synthetic-invalid-family"},
    )
    assert invalid_family.status_code == 400
    assert invalid_family.json() == {"detail": "invalid family filter"}

    invalid_tool = client.get(
        "/api/diagnostics/tool-usage",
        params={"tool": "read-file' OR 1=1"},
    )
    assert invalid_tool.status_code == 400
    assert invalid_tool.json() == {"detail": "invalid tool filter"}
    assert "OR 1=1" not in invalid_tool.text

    invalid_date = client.get(
        "/api/diagnostics/tool-usage",
        params={"start": "synthetic-private-date"},
    )
    assert invalid_date.status_code == 400
    assert invalid_date.json() == {"detail": "invalid start date"}
    assert "synthetic-private-date" not in invalid_date.text

    reversed_range = client.get(
        "/api/diagnostics/tool-usage",
        params={"start": "2026-07-16", "end": "2026-07-15"},
    )
    assert reversed_range.status_code == 400
    assert reversed_range.json() == {"detail": "range start must not be after range end"}

    oversized = client.get(
        "/api/diagnostics/tool-usage",
        params={"start": "2026-01-01", "end": "2026-07-16"},
    )
    assert oversized.status_code == 400
    assert oversized.json() == {"detail": "range must not exceed 90 days"}
    assert client.get("/api/diagnostics/tool-usage", params={"limit": 201}).status_code == 422


def test_no_raw_or_identifier_detail_route_is_registered(monkeypatch):
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    client = TestClient(_app())

    for suffix in ("raw", "owners", "sessions", "correlations"):
        assert client.get(f"/api/diagnostics/tool-usage/{suffix}").status_code == 404


def test_internal_failure_response_is_redacted(monkeypatch):
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)

    def fail(**_kwargs):
        raise RuntimeError("synthetic private failure detail")

    monkeypatch.setattr(tool_usage_analytics, "read_tool_usage_analytics", fail)
    response = TestClient(_app()).get("/api/diagnostics/tool-usage")

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to retrieve tool usage diagnostics"}
    assert "private failure" not in response.text
