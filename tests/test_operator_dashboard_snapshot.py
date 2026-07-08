import json

from src.operator_dashboard import (
    OPERATOR_DASHBOARD_SNAPSHOT_SCHEMA,
    SECTION_ORDER,
    build_operator_dashboard_snapshot,
)


def test_operator_dashboard_snapshot_merges_status_surfaces_without_private_values():
    payload = build_operator_dashboard_snapshot(
        review_gates={
            "schema": "odysseus.review_gate_state.v1",
            "status": "pending",
            "pending_count": 2,
            "blocked_count": 0,
            "gate_count": 4,
            "gates": [
                {
                    "id": "memory_write",
                    "raw_content": "PRIVATE NOTE",
                    "chat_id": "123456",
                    "path": "C:/Users/private/document.pdf",
                }
            ],
        },
        live_affordances={
            "schema": "odysseus.live_affordance_readiness.v1",
            "status": "ready",
            "needs_go_count": 1,
            "affordances": [{"target_url": "https://cloud.example.test/private?token=SECRET"}],
        },
        tasks_summary={
            "schema": "odysseus.tasks.summary.v1",
            "open_count": 3,
            "due_today_count": 1,
            "items": [{"title": "Private task body"}],
        },
        diagnostics_summary={
            "schema": "odysseus.operator_quick_status.v1",
            "status": "warn",
            "endpoint_count": 8,
            "command": "run-secret --token SECRET",
        },
        version_readiness={
            "schema": "odysseus.version_one.readiness.v1",
            "status": "partial",
            "overall_percent": 72,
            "remaining_count": 5,
        },
        orchestration_status={
            "schema": "odysseus.orchestration.dashboard.v1",
            "plan_status": "healthy",
            "progress_percent": 60,
            "next_actions": [{"title": "Continue private branch"}],
        },
        last_updated_at="2026-07-06T13:40:00+02:00",
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == OPERATOR_DASHBOARD_SNAPSHOT_SCHEMA
    assert payload["status"] == "attention"
    assert [section["id"] for section in payload["sections"]] == list(SECTION_ORDER)
    assert payload["counts"]["pending_count"] >= 1
    assert payload["counts"]["attention_count"] >= 1
    assert payload["write_action_available"] is False
    assert payload["live_probe_performed"] is False
    assert payload["live_mutation_performed"] is False
    assert payload["raw_content_visible"] is False
    assert payload["private_content_visible"] is False
    assert payload["path_values_visible"] is False
    assert payload["url_values_visible"] is False
    assert payload["token_value_visible"] is False
    assert payload["chat_id_value_visible"] is False
    assert payload["next_actions"][0]["mode"] == "read_only"
    assert all(action["write_action_enabled"] is False for action in payload["next_actions"])
    assert any(action["requires_live_go"] for action in payload["next_actions"])
    assert "PRIVATE NOTE" not in encoded
    assert "123456" not in encoded
    assert "C:/Users/private" not in encoded
    assert "cloud.example.test" not in encoded
    assert "SECRET" not in encoded
    assert "Private task body" not in encoded
    assert "run-secret" not in encoded
    assert "Continue private branch" not in encoded


def test_operator_dashboard_snapshot_marks_blockers_and_keeps_controls_gated():
    payload = build_operator_dashboard_snapshot(
        review_gates={"status": "clear", "pending_count": 0, "blocked_count": 0},
        live_affordances={"status": "blocked", "blocked_count": 1},
        diagnostics_summary={"status": "ok"},
        version_readiness={"status": "ready", "overall_percent": 100},
    )

    assert payload["status"] == "blocked"
    assert payload["counts"]["blocked_count"] == 1
    assert payload["controls"]["approve"]["state"] == "policy_gated"
    assert payload["controls"]["execute"]["state"] == "policy_gated"
    assert payload["controls"]["retry"]["state"] == "disabled"
    assert payload["next_actions"][0]["section_id"] == "live_affordances"
    assert payload["next_actions"][0]["requires_live_go"] is True


def test_operator_dashboard_snapshot_is_stable_and_metadata_only_for_empty_inputs():
    first = build_operator_dashboard_snapshot()
    second = build_operator_dashboard_snapshot()

    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["status"] == "partial"
    assert first["counts"]["section_count"] == len(SECTION_ORDER)
    assert first["next_actions"]
    assert all(ref["ref_hash"] for ref in first["evidence_refs"])
