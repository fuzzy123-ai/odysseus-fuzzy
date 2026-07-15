from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import (
    SCHEMA_VERSION,
    ToolUsageAgentMode,
    ToolUsageBlockedReason,
    ToolUsageErrorClass,
    ToolUsageEventBuilder,
    ToolUsageEventError,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
    size_bucket_for_count,
)


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _base(**overrides):
    values = {
        "event_kind": ToolUsageEventKind.STARTED,
        "event_id": "evt_000000000001",
        "invocation_id": "inv_000000000001",
        "occurred_at": NOW,
        "tool_analytics_id": "read-file",
        "tool_family": ToolFamily.CODE_FILESYSTEM,
        "tool_source": ToolSource.BUILTIN,
        "surface": ToolUsageSurface.AGENT,
        "argument_size_bucket": ToolUsageSizeBucket.S,
        "result_size_bucket": ToolUsageSizeBucket.NONE,
        "result_shape_bucket": ToolUsageResultShape.NONE,
        "model_scope": ToolUsageModelScope.LOCAL,
        "agent_mode": ToolUsageAgentMode.AGENT,
        "app_version": "0.25.0",
    }
    values.update(overrides)
    return values


def _terminal(status=ToolUsageStatus.SUCCEEDED, **overrides):
    values = _base(
        event_kind=ToolUsageEventKind.TERMINAL,
        event_id="evt_000000000002",
        duration_ms=25,
        status=status,
        result_size_bucket=ToolUsageSizeBucket.M,
        result_shape_bucket=ToolUsageResultShape.MAPPING,
    )
    if status == ToolUsageStatus.FAILED:
        values["error_class"] = ToolUsageErrorClass.EXECUTION_ERROR
    if status in {ToolUsageStatus.BLOCKED, ToolUsageStatus.REJECTED}:
        values["blocked_reason_code"] = ToolUsageBlockedReason.POLICY
    values.update(overrides)
    return values


def test_started_event_has_strict_v1_identity_and_deterministic_safe_serialization():
    first = ToolUsageEventBuilder.build(**_base())
    second = ToolUsageEventBuilder.build(**_base())

    assert first == second
    assert first.persistence_allowed is True
    assert first.suppression_reason is None
    assert first.event.schema_version == SCHEMA_VERSION
    assert first.event.status is None
    assert first.event.duration_ms is None
    assert first.to_safe_dict()["raw_content_visible"] is False
    payload = first.event.to_safe_dict()
    assert payload["schema_version"] == "odysseus.tool_usage_event.v1"
    assert payload["occurred_at"] == "2026-01-02T03:04:05Z"
    assert payload["tool_analytics_id"] == "read-file"
    assert payload["tool_family"] == "code_filesystem"
    assert payload["tool_source"] == "builtin"
    assert payload["raw_content_visible"] is False


@pytest.mark.parametrize(
    "status",
    [
        ToolUsageStatus.SUCCEEDED,
        ToolUsageStatus.FAILED,
        ToolUsageStatus.BLOCKED,
        ToolUsageStatus.CANCELLED,
        ToolUsageStatus.REJECTED,
    ],
)
def test_terminal_statuses_are_bounded_and_have_valid_metadata(status):
    result = ToolUsageEventBuilder.build(**_terminal(status))

    assert result.persistence_allowed is True
    assert result.event.status == status
    assert result.event.duration_ms == 25
    assert result.event.event_kind == ToolUsageEventKind.TERMINAL


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": ToolUsageStatus.SUCCEEDED},
        {"duration_ms": 1},
        {"error_class": ToolUsageErrorClass.UNKNOWN},
        {"blocked_reason_code": ToolUsageBlockedReason.POLICY},
        {"result_size_bucket": ToolUsageSizeBucket.S},
        {"result_shape_bucket": ToolUsageResultShape.SCALAR},
    ],
)
def test_started_event_rejects_terminal_only_fields(overrides):
    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(**_base(**overrides))


@pytest.mark.parametrize(
    "values",
    [
        _terminal(status=None),
        _terminal(duration_ms=None),
        _terminal(ToolUsageStatus.SUCCEEDED, error_class=ToolUsageErrorClass.UNKNOWN),
        _terminal(ToolUsageStatus.FAILED, error_class=None),
        _terminal(
            ToolUsageStatus.FAILED,
            blocked_reason_code=ToolUsageBlockedReason.POLICY,
        ),
        _terminal(ToolUsageStatus.BLOCKED, blocked_reason_code=None),
        _terminal(ToolUsageStatus.REJECTED, error_class=ToolUsageErrorClass.POLICY_ERROR),
        _terminal(ToolUsageStatus.CANCELLED, error_class=ToolUsageErrorClass.UNKNOWN),
    ],
)
def test_terminal_event_rejects_inconsistent_status_metadata(values):
    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_kind", "progress"),
        ("tool_family", "other"),
        ("tool_source", "unbounded"),
        ("surface", "desktop"),
        ("argument_size_bucket", "huge"),
        ("result_size_bucket", "tiny"),
        ("result_shape_bucket", "record-with-fields"),
        ("model_scope", "provider-name"),
        ("agent_mode", "interactive-owner"),
    ],
)
def test_event_rejects_values_outside_controlled_enums(field, value):
    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(**_base(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "short"),
        ("event_id", "evt:000000000001"),
        ("invocation_id", "../invalid-reference"),
        ("tool_analytics_id", "Read-File"),
        ("tool_analytics_id", "read_file"),
        ("app_version", "version with spaces"),
        ("app_version", "host:name"),
    ],
)
def test_event_rejects_unsafe_ids_and_versions(field, value):
    with pytest.raises(ToolUsageEventError):
        ToolUsageEventBuilder.build(**_base(**{field: value}))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, ToolUsageSizeBucket.NONE),
        (1, ToolUsageSizeBucket.XS),
        (128, ToolUsageSizeBucket.XS),
        (129, ToolUsageSizeBucket.S),
        (1025, ToolUsageSizeBucket.M),
        (8193, ToolUsageSizeBucket.L),
        (65537, ToolUsageSizeBucket.XL),
    ],
)
def test_size_bucketing_discards_raw_size(value, expected):
    assert size_bucket_for_count(value) == expected


@pytest.mark.parametrize("value", [-1, True, "10"])
def test_size_bucketing_rejects_invalid_counts(value):
    with pytest.raises(ToolUsageEventError):
        size_bucket_for_count(value)


def test_event_value_is_immutable():
    event = ToolUsageEventBuilder.build(**_base()).event
    with pytest.raises(FrozenInstanceError):
        event.status = ToolUsageStatus.SUCCEEDED


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "odysseus.tool_usage_event.v0"},
        {"event_kind": "started"},
        {"status": ToolUsageStatus.SUCCEEDED},
        {"occurred_at": NOW.astimezone(timezone(timedelta(hours=1)))},
        {"retry_ordinal": 101},
    ],
)
def test_dataclass_validation_cannot_be_bypassed_by_direct_replacement(changes):
    event = ToolUsageEventBuilder.build(**_base()).event
    with pytest.raises(ToolUsageEventError):
        replace(event, **changes)
