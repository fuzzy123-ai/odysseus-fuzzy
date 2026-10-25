"""Closed contract tests for the inert Telegram turn observability value."""

from __future__ import annotations

import json

import pytest


def _snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "correlation_id": "telegram:0123456789abcdef",
        "intake_path": "webhook",
        "last_observed_phase": "final_sent",
        "outcome": "sent",
        "received_at": 100,
        "session_bound_at": 101,
        "agent_started_at": 102,
        "tool_selection_ms": 3,
        "first_delta_at": 103,
        "first_draft_sent_at": 104,
        "final_sent_at": 105,
    }
    value.update(overrides)
    return value


def _api():
    from plugins.telegram.turn_observability import (
        MAX_TURN_OBSERVABILITY_BYTES,
        TURN_OBSERVABILITY_SCHEMA,
        TelegramTurnObservabilityError,
        project_telegram_turn_observability,
    )

    return (
        TURN_OBSERVABILITY_SCHEMA,
        MAX_TURN_OBSERVABILITY_BYTES,
        TelegramTurnObservabilityError,
        project_telegram_turn_observability,
    )


def test_trs01a_public_api_schema_fields_enums_and_budgets_are_exact():
    schema, maximum, _, project = _api()
    result = project(_snapshot())
    assert schema == "odysseus.telegram.turn_observability.v1"
    assert maximum == 1024
    assert tuple(result) == (
        "schema", "correlation_id", "intake_path", "last_observed_phase", "outcome",
        "received_at", "session_bound_at", "agent_started_at", "tool_selection_ms",
        "first_delta_at", "first_draft_sent_at", "final_sent_at", "snapshot_atomic",
        "persistence_authorized", "lifecycle_transition_authorized", "live_action_authorized",
        "raw_content_visible", "raw_identifiers_visible",
    )
    assert result["schema"] == schema
    assert result["intake_path"] == "webhook"
    assert result["last_observed_phase"] == "final_sent"
    assert result["outcome"] == "sent"


def test_trs01a_webhook_and_polling_share_one_content_free_projection():
    _, _, _, project = _api()
    webhook = project(_snapshot(intake_path="webhook"))
    polling = project(_snapshot(intake_path="polling"))
    assert {webhook["intake_path"], polling["intake_path"]} == {"webhook", "polling"}
    assert webhook.keys() == polling.keys()
    assert all(value is False for key, value in webhook.items() if key.endswith("_authorized") or key == "snapshot_atomic")


def test_trs01a_exact_types_reject_subclasses_and_hostile_values_without_callbacks():
    _, _, error, project = _api()

    class DictSubclass(dict):
        pass

    class IntSubclass(int):
        pass

    class Hostile:
        @property
        def __class__(self):
            raise AssertionError("must not inspect hostile class")

    for value in (DictSubclass(_snapshot()), Hostile(), _snapshot(received_at=IntSubclass(100))):
        with pytest.raises(error) as raised:
            project(value)
        assert raised.value.args == ("invalid_snapshot",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


def test_trs01a_correlation_id_is_strictly_redacted_lowercase_and_bounded():
    _, _, error, project = _api()
    for correlation_id in ("telegram:0123456789abcde", "telegram:0123456789abcdef0", "telegram:0123456789ABCDEf", "chat:0123456789abcdef", "telegram:raw-chat-id"):
        with pytest.raises(error, match="^invalid_snapshot$"):
            project(_snapshot(correlation_id=correlation_id))


def test_trs01a_timestamps_phase_and_outcome_matrix_is_fail_closed():
    _, _, error, project = _api()
    for snapshot in (
        _snapshot(session_bound_at=99),
        _snapshot(agent_started_at=100),
        _snapshot(first_delta_at=101),
        _snapshot(first_draft_sent_at=102),
        _snapshot(final_sent_at=103),
        _snapshot(last_observed_phase="unknown"),
        _snapshot(outcome="unknown"),
        _snapshot(last_observed_phase="received", outcome="sent", session_bound_at=None, agent_started_at=None, tool_selection_ms=None, first_delta_at=None, first_draft_sent_at=None, final_sent_at=None),
    ):
        with pytest.raises(error, match="^invalid_timing$"):
            project(snapshot)
    with pytest.raises(error, match="^invalid_snapshot$"):
        project(_snapshot(received_at=-1))


def test_trs01a_capture_is_single_read_detached_and_mutation_safe():
    _, _, _, project = _api()
    snapshot = _snapshot()
    result = project(snapshot)
    snapshot["correlation_id"] = "telegram:ffffffffffffffff"
    assert result["correlation_id"] == "telegram:0123456789abcdef"
    assert result is not project(_snapshot())


def test_trs01a_errors_are_fresh_fixed_content_free_and_suppress_context():
    _, _, error, project = _api()
    first = second = None
    for _ in range(2):
        with pytest.raises(error) as raised:
            project(_snapshot(extra="private content"))
        if first is None:
            first = raised.value
        else:
            second = raised.value
    assert first is not second
    assert first.args == second.args == ("invalid_snapshot",)
    assert first.__cause__ is second.__cause__ is None
    assert first.__context__ is second.__context__ is None


def test_trs01a_canonical_projection_is_bounded_and_has_no_forbidden_fields():
    _, maximum, _, project = _api()
    result = project(_snapshot())
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    assert len(encoded) <= maximum
    assert not ({"chat_id", "sender_id", "token", "content", "text", "path", "error", "exception"} & set(result))
    assert result["raw_content_visible"] is False
    assert result["snapshot_atomic"] is False
    assert result["persistence_authorized"] is False
    assert result["lifecycle_transition_authorized"] is False
    assert result["live_action_authorized"] is False
    assert result["raw_identifiers_visible"] is False


def test_trs01a_import_and_projection_have_zero_io_clock_store_network_or_spawn_effects(monkeypatch):
    _, _, _, project = _api()
    import builtins
    import os
    import subprocess
    import time

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden effect")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    assert project(_snapshot())["schema"] == "odysseus.telegram.turn_observability.v1"


def test_trs01a_projection_cannot_persist_register_or_authorize_lifecycle():
    _, _, _, project = _api()
    result = project(_snapshot())
    assert result["persistence_authorized"] is False
    assert result["lifecycle_transition_authorized"] is False
    assert result["live_action_authorized"] is False
