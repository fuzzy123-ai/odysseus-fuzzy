from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3

import pytest

from src.tool_catalog import (
    ToolAvailability,
    ToolDescriptorV2,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)
from src.tool_usage_events import ToolUsageEventBuilder
from src.tool_usage_store import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    ToolUsageStore,
    ToolUsageStoreError,
)


def _descriptor() -> ToolDescriptorV2:
    return ToolDescriptorV2.create(
        tool_id="read_file",
        display_name="Read File",
        description="Read a workspace file through the bounded adapter.",
        family=ToolFamily.CODE_FILESYSTEM,
        source=ToolSource.BUILTIN,
        lifecycle=ToolLifecycle.ACTIVE,
        availability=ToolAvailability.AVAILABLE,
        default_enabled=True,
        default_visibility=ToolVisibility.VISIBLE,
        risk_level=ToolRiskLevel.SAFE,
        permission=ToolPermission.USER,
        effect_class=ToolEffectClass.READ,
        requires_confirmation=False,
        schema_ref="function:read_file",
        handler_ref="builtin:read_file",
        prompt_ref="index:read_file",
        introduced_in="0.24.0",
    )


def _event(
    *,
    event_kind="terminal",
    event_hex="a",
    invocation_hex="b",
    occurred_at="2026-07-17T10:00:00.000Z",
    incognito=False,
):
    terminal = event_kind == "terminal"
    return ToolUsageEventBuilder(app_version="0.25.0", hmac_key=b"s" * 32).build(
        descriptor=_descriptor(),
        event_kind=event_kind,
        surface="agent",
        agent_mode="agent",
        status="succeeded" if terminal else None,
        duration_ms=10 if terminal else None,
        result_size_bytes=20 if terminal else 0,
        result_shape="scalar" if terminal else "none",
        event_id="tue_" + event_hex * 32,
        invocation_id="tui_" + invocation_hex * 32,
        occurred_at=occurred_at,
        incognito=incognito,
    )


def _store(tmp_path):
    store = ToolUsageStore(tmp_path / "usage.sqlite3")
    store.migrate()
    return store


def test_migration_is_idempotent_and_creates_versioned_tables_and_indexes(tmp_path):
    database = tmp_path / "usage.sqlite3"
    with ToolUsageStore(database) as store:
        first = store.migrate()
        second = store.migrate()

    assert first == second == {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "event_table_ready": True,
        "daily_table_ready": True,
        "raw_content_visible": False,
    }
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        version = connection.execute(
            "SELECT schema_version FROM tool_usage_schema_meta WHERE schema_name = ?",
            (SCHEMA_NAME,),
        ).fetchone()[0]

    assert {"tool_usage_schema_meta", "tool_usage_events", "tool_usage_daily"} <= tables
    assert {
        "ix_tool_usage_events_occurred_at",
        "ix_tool_usage_events_tool_status",
        "ix_tool_usage_daily_day",
    } <= indexes
    assert version == SCHEMA_VERSION


def test_start_and_terminal_store_once_and_terminal_updates_daily_aggregate(tmp_path):
    store = _store(tmp_path)
    start = _event(event_kind="started", event_hex="1", invocation_hex="2")
    terminal = _event(event_kind="terminal", event_hex="3", invocation_hex="2")

    first = store.append_events([start, terminal])
    duplicate = store.append_events([start, terminal])

    assert first.to_dict() == {
        "accepted_count": 2,
        "duplicate_count": 0,
        "persistence_rejected_count": 0,
        "failure_count": 0,
        "raw_content_visible": False,
    }
    assert duplicate.accepted_count == 0
    assert duplicate.duplicate_count == 2
    assert store.counts() == {
        "event_count": 2,
        "daily_aggregate_count": 1,
        "raw_content_visible": False,
    }
    store.close()


def test_parallel_duplicate_terminal_is_persisted_and_aggregated_at_most_once(tmp_path):
    store = _store(tmp_path)
    event = _event(event_hex="4", invocation_hex="5")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.append_events([event]), range(12)))

    assert sum(item.accepted_count for item in results) == 1
    assert sum(item.duplicate_count for item in results) == 11
    assert store.counts()["event_count"] == 1
    assert store.counts()["daily_aggregate_count"] == 1
    store.close()


def test_incognito_event_is_rejected_before_any_write(tmp_path):
    store = _store(tmp_path)
    event = _event(event_hex="6", invocation_hex="7", incognito=True)

    result = store.append_events([event])

    assert result.persistence_rejected_count == 1
    assert result.accepted_count == 0
    assert store.counts()["event_count"] == 0
    store.close()


def test_invalid_batch_fails_before_transaction_and_preserves_database(tmp_path):
    store = _store(tmp_path)
    event = _event(event_hex="8", invocation_hex="9")

    with pytest.raises(ToolUsageStoreError, match="ToolUsageEventV1"):
        store.append_events([event, {"raw": "payload"}])

    assert store.counts()["event_count"] == 0
    store.close()


def test_best_effort_store_failure_never_raises_and_uses_bounded_counts(tmp_path):
    store = _store(tmp_path)
    event = _event(event_hex="a", invocation_hex="c")
    store.close()

    result = store.append_best_effort([event])

    assert result.failure_count == 1
    assert result.accepted_count == 0
    assert store.failure_counts() == {
        "store_failure": 1,
        "raw_content_visible": False,
    }


def test_retention_dry_run_is_count_only_and_protects_non_aggregated_days(tmp_path):
    store = _store(tmp_path)
    incomplete = _event(
        event_kind="started",
        event_hex="d",
        invocation_hex="e",
        occurred_at="2024-01-02T10:00:00.000Z",
    )
    aggregated = _event(
        event_hex="e",
        invocation_hex="f",
        occurred_at="2024-01-03T10:00:00.000Z",
    )
    store.append_events([incomplete, aggregated])
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)

    dry_run = store.apply_retention(now=now, dry_run=True)
    applied = store.apply_retention(now=now, dry_run=False)

    assert dry_run.scanned_event_count == 2
    assert dry_run.deletable_event_count == 1
    assert dry_run.protected_event_count == 1
    assert dry_run.deletable_daily_count == 1
    assert dry_run.deleted_event_count == 0
    assert dry_run.deleted_daily_count == 0
    assert dry_run.to_dict()["raw_content_visible"] is False
    assert applied.deleted_event_count == 1
    assert applied.deleted_daily_count == 1
    assert store.counts()["event_count"] == 1
    assert store.counts()["daily_aggregate_count"] == 0
    store.close()


def test_schema_version_mismatch_rolls_back_without_touching_existing_meta(tmp_path):
    database = tmp_path / "usage.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE tool_usage_schema_meta (schema_name TEXT PRIMARY KEY, schema_version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO tool_usage_schema_meta(schema_name, schema_version) VALUES (?, ?)",
            (SCHEMA_NAME, 999),
        )

    with ToolUsageStore(database) as store:
        with pytest.raises(ToolUsageStoreError, match="unsupported"):
            store.migrate()

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT schema_version FROM tool_usage_schema_meta WHERE schema_name = ?",
            (SCHEMA_NAME,),
        ).fetchone()[0]
    assert version == 999


def test_event_table_has_only_allowlisted_content_free_columns(tmp_path):
    database = tmp_path / "usage.sqlite3"
    with ToolUsageStore(database) as store:
        store.migrate()
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tool_usage_events)")
        }

    forbidden = {
        "args",
        "command",
        "content",
        "error_message",
        "metadata",
        "output",
        "payload",
        "prompt",
        "result",
        "token",
        "url",
    }
    assert not (columns & forbidden)
