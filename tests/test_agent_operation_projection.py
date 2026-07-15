from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent_operation_projection import (
    AGENT_OPERATION_PROJECTION_SCHEMA_ID,
    AgentOperationProjectionError,
    allowed_commands_for,
    build_agent_operation_projection,
    derive_heartbeat_health,
    project_history,
)


NOW = "2026-07-15T12:00:00Z"
PLAN_REF = {
    "project_id": "project-1",
    "roadmap_id": "roadmap-1",
    "revision": 7,
    "content_hash": "sha256:" + "a" * 64,
}


def _run(**overrides):
    value = {
        "agent_run_id": "arun-" + "1" * 32,
        "workflow_id": "odysseus-abc/0123456789abcdef/arun-" + "1" * 32,
        "workflow_run_id": "temporal-run-1",
        "history_segment": 0,
        "run_state": "running",
        "run_version": 4,
        "slice_states": {"node-a": "activity_running", "node-b": "retry_wait"},
        "gate_states": {"gate-a": "pending"},
        "started_at": "2026-07-15T11:00:00Z",
        "updated_at": "2026-07-15T11:59:50Z",
        "completed_at": None,
        "deadline_at": "2026-07-16T11:00:00Z",
        "waiting_reason": None,
    }
    value.update(overrides)
    return value


def _activity(**overrides):
    value = {
        "activity_id": "activity-1",
        "node_id": "node-a",
        "type": "execute_slice",
        "state": "running",
        "attempt": 1,
        "max_attempts": 3,
        "retryable": True,
        "next_retry_at": None,
        "started_at": "2026-07-15T11:58:00Z",
        "updated_at": "2026-07-15T11:59:45Z",
        "completed_at": None,
        "last_heartbeat_at": "2026-07-15T11:59:45Z",
        "heartbeat_timeout_seconds": 90,
        "error_code": None,
    }
    value.update(overrides)
    return value


def test_projection_has_exact_pinned_plan_and_server_derived_controls():
    projection = build_agent_operation_projection(
        plan_ref=PLAN_REF,
        run=_run(),
        activities=[_activity()],
        observed_at=NOW,
    )

    assert projection["schema_id"] == AGENT_OPERATION_PROJECTION_SCHEMA_ID
    assert projection["run"]["plan_ref"] == PLAN_REF
    assert projection["run"]["current_node_ids"] == ["node-a", "node-b"]
    assert projection["run"]["allowed_commands"] == [
        "pause",
        "cancel",
        "retry_activity",
        "steer_run",
    ]
    assert projection["activities"][0]["heartbeat_health"] == "healthy"
    assert "manifest" not in repr(projection).lower()


@pytest.mark.parametrize(
    ("state", "last_heartbeat", "started_at", "expected"),
    [
        ("running", "2026-07-15T11:59:00Z", None, "healthy"),
        ("running", "2026-07-15T11:58:00Z", None, "late"),
        ("running", "2026-07-15T11:56:00Z", None, "stale"),
        ("completed", "2026-07-15T11:56:00Z", None, "not_expected"),
        ("running", None, None, "stale"),
    ],
)
def test_heartbeat_health_is_derived_from_server_time(
    state, last_heartbeat, started_at, expected
):
    assert (
        derive_heartbeat_health(
            activity_state=state,
            last_heartbeat_at=last_heartbeat,
            heartbeat_timeout_seconds=90,
            observed_at=datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
            started_at=started_at,
        )
        == expected
    )


def test_allowed_commands_matches_retry_and_gate_preconditions():
    assert allowed_commands_for("paused") == ["resume", "cancel", "steer_run"]
    assert allowed_commands_for("waiting_gate", gate_states={"g": "pending"}) == [
        "pause",
        "cancel",
        "decide_gate",
    ]
    assert allowed_commands_for("completed") == []
    assert "retry_activity" not in allowed_commands_for(
        "running", slice_states={"node": "failed"}
    )


def test_history_is_bounded_to_200_and_resumes_without_overlap():
    events = [
        {
            "history_segment": 2,
            "event_id": number,
            "event_type": "activity_task_completed",
            "occurred_at": f"2026-07-15T11:{number // 60:02d}:{number % 60:02d}Z",
            "node_id": "node-a",
            "activity_id": "activity-1",
            "summary": "activity completed",
            "ref_ids": ["receipt-1"],
        }
        for number in range(1, 206)
    ]

    first = project_history(events, limit=200)
    second = project_history(events, after=first["next_cursor"], limit=200)

    assert len(first["events"]) == 200
    assert first["has_more"] is True
    assert [event["event_id"] for event in second["events"]] == [
        "h2:201",
        "h2:202",
        "h2:203",
        "h2:204",
        "h2:205",
    ]
    assert set(event["event_id"] for event in first["events"]).isdisjoint(
        event["event_id"] for event in second["events"]
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        {"raw_history": {"payloads": ["provider bytes"]}},
        {"secret": "not-public"},
        {"changed_path": r"C:\\Users\\alice\\private.txt"},
        {"changed_path": "/home/alice/private.txt"},
        {"message": r"failed while reading C:\\Users\\alice\\private.txt"},
        {"message": "token sk-0123456789abcdefghijklmnop"},
    ],
)
def test_projection_rejects_raw_secret_and_absolute_path_fields(unsafe):
    with pytest.raises(AgentOperationProjectionError) as caught:
        build_agent_operation_projection(
            plan_ref=PLAN_REF,
            run=_run(),
            evidence=[unsafe],
            observed_at=NOW,
        )

    assert caught.value.code == "unsafe_projection"


def test_history_rejects_unbounded_page_request():
    with pytest.raises(AgentOperationProjectionError) as caught:
        project_history([], limit=201)
    assert caught.value.code == "invalid_limit"
