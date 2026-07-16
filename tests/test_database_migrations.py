from sqlalchemy import create_engine, inspect, text

from scripts.update_database import migrate_tool_usage_schema, rollback_tool_usage_schema
from src.tool_usage_store import TOOL_USAGE_SCHEMA_VERSION


def test_tool_usage_migration_is_versioned_and_idempotent():
    engine = create_engine("sqlite:///:memory:")

    first = migrate_tool_usage_schema(engine)
    second = migrate_tool_usage_schema(engine)
    tables = set(inspect(engine).get_table_names())

    assert first == {
        "schema_version": TOOL_USAGE_SCHEMA_VERSION,
        "created_table_count": 3,
        "changed": True,
        "domain_tables_touched": False,
        "raw_content_visible": False,
    }
    assert second["created_table_count"] == 0
    assert second["changed"] is False
    assert {
        "tool_usage_events",
        "tool_usage_daily_aggregates",
        "tool_usage_schema_versions",
    } <= tables
    with engine.connect() as connection:
        version = connection.execute(
            text(
                "SELECT version FROM tool_usage_schema_versions "
                "WHERE component = 'tool_usage_analytics'"
            )
        ).scalar_one()
    assert version == TOOL_USAGE_SCHEMA_VERSION


def test_event_and_daily_tables_have_unique_constraints_and_bounded_indexes():
    engine = create_engine("sqlite:///:memory:")
    migrate_tool_usage_schema(engine)
    inspector = inspect(engine)

    event_uniques = {item["name"] for item in inspector.get_unique_constraints("tool_usage_events")}
    daily_uniques = {
        item["name"]
        for item in inspector.get_unique_constraints("tool_usage_daily_aggregates")
    }
    event_indexes = {item["name"] for item in inspector.get_indexes("tool_usage_events")}
    daily_indexes = {
        item["name"] for item in inspector.get_indexes("tool_usage_daily_aggregates")
    }

    assert "uq_tool_usage_events_invocation_kind" in event_uniques
    assert "uq_tool_usage_daily_dimensions" in daily_uniques
    assert {
        "ix_tool_usage_events_day",
        "ix_tool_usage_events_analytics_id",
        "ix_tool_usage_events_family",
        "ix_tool_usage_events_source",
        "ix_tool_usage_events_surface",
        "ix_tool_usage_events_status",
        "ix_tool_usage_events_retention",
    } <= event_indexes
    assert {
        "ix_tool_usage_daily_day",
        "ix_tool_usage_daily_analytics_id",
        "ix_tool_usage_daily_family",
        "ix_tool_usage_daily_source",
        "ix_tool_usage_daily_surface",
        "ix_tool_usage_daily_status",
    } <= daily_indexes


def test_usage_tables_are_allowlist_only_and_have_no_private_domain_foreign_keys():
    engine = create_engine("sqlite:///:memory:")
    migrate_tool_usage_schema(engine)
    inspector = inspect(engine)
    event_columns = {item["name"] for item in inspector.get_columns("tool_usage_events")}
    daily_columns = {
        item["name"]
        for item in inspector.get_columns("tool_usage_daily_aggregates")
    }

    assert event_columns == {
        "event_id",
        "invocation_id",
        "event_kind",
        "occurred_at",
        "event_day",
        "duration_ms",
        "tool_analytics_id",
        "tool_family",
        "tool_source",
        "surface",
        "status",
        "error_class",
        "blocked_reason_code",
        "retry_ordinal",
        "argument_size_bucket",
        "result_size_bucket",
        "result_shape_bucket",
        "owner_ref",
        "session_ref",
        "run_ref",
        "correlation_ref",
        "model_scope",
        "agent_mode",
        "app_version",
        "aggregated_at",
    }
    assert daily_columns == {
        "id",
        "day",
        "tool_analytics_id",
        "tool_family",
        "tool_source",
        "surface",
        "status",
        "event_count",
        "invocation_count",
        "aggregation_complete",
        "aggregated_at",
    }
    forbidden = {"metadata", "payload", "args", "result", "command", "path", "url", "secret"}
    assert event_columns.isdisjoint(forbidden)
    assert daily_columns.isdisjoint(forbidden | {"owner_ref", "session_ref", "run_ref"})
    assert inspector.get_foreign_keys("tool_usage_events") == []
    assert inspector.get_foreign_keys("tool_usage_daily_aggregates") == []


def test_tool_usage_rollback_preserves_unrelated_domain_tables_and_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE domain_sentinel (id INTEGER PRIMARY KEY, value TEXT)"))
        connection.execute(text("INSERT INTO domain_sentinel (id, value) VALUES (1, 'preserve')"))
    migrate_tool_usage_schema(engine)

    report = rollback_tool_usage_schema(engine)

    assert report == {
        "schema_version": TOOL_USAGE_SCHEMA_VERSION,
        "dropped_table_count": 3,
        "rollback_applied": True,
        "domain_tables_touched": False,
        "raw_content_visible": False,
    }
    assert inspect(engine).get_table_names() == ["domain_sentinel"]
    with engine.connect() as connection:
        preserved = connection.execute(
            text("SELECT value FROM domain_sentinel WHERE id=1")
        ).scalar_one()
    assert preserved == "preserve"
