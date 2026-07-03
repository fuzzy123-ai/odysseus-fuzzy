import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routes import diagnostics_routes
from src import ai_activity_ledger, memory_provenance_ledger, tool_capability_maintenance
from src.observability_diagnostics_bridge import (
    ObservabilityDiagnosticsBridgeError,
    build_observability_diagnostic_packet,
    classify_observability_question,
)
from src.observability_metrics import build_runtime_metric_sample, build_runtime_metrics_snapshot


def _snapshot(*samples):
    return build_runtime_metrics_snapshot(samples)


def test_classifies_operator_questions_without_returning_raw_query_text():
    assert classify_observability_question("Warum kam heute um 9 keine Telegram To-Do Liste?") == "telegram_or_reminder_delivery"
    assert classify_observability_question("Warum wurde die PDF nicht importiert?") == "file_import_or_inbox"
    assert classify_observability_question("Ist Gemma langsam?") == "model_runtime"


def test_telegram_reminder_packet_points_to_scheduler_and_telegram_signals():
    packet = build_observability_diagnostic_packet(
        question="Warum kam heute um 9 keine Telegram To-Do Liste?",
        metrics_snapshot=_snapshot(
            build_runtime_metric_sample("telegram_poll_failure_total", 1),
            build_runtime_metric_sample("scheduler_delivery_failures_total", 2),
            build_runtime_metric_sample("scheduler_due_tasks", 3),
        ),
        quick_summary={"status": "ok"},
    )
    encoded = json.dumps(packet, sort_keys=True)
    codes = {finding["code"] for finding in packet["findings"]}

    assert packet["schema"] == "odysseus.observability_diagnostic_bridge.v1"
    assert packet["status"] == "needs_attention"
    assert packet["intent"] == "telegram_or_reminder_delivery"
    assert "telegram_poll_failures" in codes
    assert "scheduler_delivery_failures" in codes
    assert "inspect_scheduler_queue" in packet["recommended_next_actions"]
    assert packet["query_text_included"] is False
    assert "Warum kam heute" not in encoded
    assert "chat_id" not in encoded.lower()


def test_file_import_packet_points_to_inbox_and_memory_blockers():
    packet = build_observability_diagnostic_packet(
        question="Warum wurde die Datei nicht importiert?",
        metrics_snapshot=_snapshot(
            build_runtime_metric_sample("universal_inbox_blocked_total", 1),
            build_runtime_metric_sample("memory_write_blocked_total", 1),
        ),
    )
    codes = {finding["code"] for finding in packet["findings"]}

    assert packet["status"] == "attention"
    assert "universal_inbox_blocked" in codes
    assert "memory_write_blocked" in codes
    assert "inspect_universal_inbox_blockers" in packet["recommended_next_actions"]
    assert "inspect_memory_write_policy" in packet["recommended_next_actions"]


def test_raptorgraph_and_model_runtime_packets_are_bounded():
    raptor = build_observability_diagnostic_packet(
        question="Warum ist RaptorGraph Maintenance fehlgeschlagen?",
        metrics_snapshot=_snapshot(build_runtime_metric_sample("raptorgraph_maintenance_failures_total", 1)),
    )
    model = build_observability_diagnostic_packet(
        question="Ist Gemma E4B zu langsam?",
        metrics_snapshot=_snapshot(build_runtime_metric_sample("local_model_latency_seconds", 45)),
    )

    assert raptor["status"] == "needs_attention"
    assert raptor["findings"][0]["code"] == "raptorgraph_maintenance_failures"
    assert "inspect_raptorgraph_maintenance" in raptor["recommended_next_actions"]
    assert model["status"] == "attention"
    assert model["findings"][0]["code"] == "local_model_latency_high"
    assert "inspect_local_model_latency" in model["recommended_next_actions"]


def test_general_packet_uses_alert_routes_and_rejects_unsafe_summary():
    packet = build_observability_diagnostic_packet(
        question="Was ist kaputt?",
        metrics_snapshot=_snapshot(build_runtime_metric_sample("llm_call_failures_total", 2)),
    )

    assert packet["status"] == "attention"
    assert packet["findings"][0]["code"] == "llm-call-failing"
    assert packet["raw_content_visible"] is False
    assert packet["live_queries_performed"] is False
    assert packet["writes_performed"] is False

    with pytest.raises(ObservabilityDiagnosticsBridgeError):
        build_observability_diagnostic_packet(
            question="status",
            quick_summary={"status": "warn", "note": r"C:\Users\nkatz\private"},
        )


def test_observability_bridge_route_is_admin_gated_and_redacted(monkeypatch):
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        ai_activity_ledger,
        "read_ai_activity",
        lambda **_kwargs: {
            "status": "success",
            "count": 1,
            "summary": {"by_status": {"failed": 1}, "avg_duration_ms": 45_000},
        },
    )
    monkeypatch.setattr(
        memory_provenance_ledger,
        "read_memory_provenance",
        lambda **_kwargs: {
            "status": "success",
            "count": 1,
            "summary": {"by_status": {"blocked": 1}, "by_event_type": {"memory_write_intent": 1}},
        },
    )
    monkeypatch.setattr(
        tool_capability_maintenance,
        "read_tool_capability_diagnostics",
        lambda: {"status": "success", "snapshot": {"index_status": {"status": "ok", "healthy": True}}},
    )
    app = FastAPI()
    app.include_router(diagnostics_routes.setup_diagnostics_routes(None, False, None))

    response = TestClient(app).get(
        "/api/diagnostics/observability-bridge",
        params={"question": "Warum wurde meine private Datei nicht importiert?"},
    )
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["intent"] == "file_import_or_inbox"
    assert payload["query_text_included"] is False
    assert payload["raw_content_visible"] is False
    assert "private Datei" not in encoded
    assert "chat_id" not in encoded.lower()
