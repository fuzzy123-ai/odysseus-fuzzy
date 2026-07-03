import json

import pytest

from mcp_servers.debug_server import call_debug_tool_contract
from src.debug_bundle import (
    DebugBundleError,
    build_redacted_debug_bundle,
    summarize_debug_bundle,
)
from src.runtime_event_envelope import build_runtime_event


def _event(**overrides):
    payload = {
        "surface": "telegram",
        "component": "reply_delivery",
        "event_type": "reply_send",
        "status": "failed",
        "severity": "error",
        "owner_scope": "debug_test",
        "correlation_id": "corr-debug-1",
        "privacy_level": "private_metadata",
        "metadata": {"reason": "send_failed"},
    }
    payload.update(overrides)
    owner_scope = payload.pop("owner_scope")
    return build_runtime_event(owner_scope=owner_scope, **payload)


def test_debug_bundle_packages_missing_telegram_reply_without_raw_content():
    bundle = build_redacted_debug_bundle(
        incident_ref="missing-telegram-reply",
        events=[
            _event(event_id="evt-inbound", event_type="inbound_message", status="received", severity="info"),
            _event(event_id="evt-agent", component="agent_turn", event_type="agent_turn", status="success", severity="info"),
            _event(event_id="evt-reply", component="reply_delivery", event_type="reply_send", status="failed"),
        ],
        summaries=({"surface": "telegram", "failure_count": 1, "private_note": "safe-redacted-marker"},),
    )
    encoded = json.dumps(bundle, sort_keys=True)

    assert bundle["schema"] == "odysseus.debug_bundle.v1"
    assert bundle["status"] == "ready"
    assert bundle["event_count"] == 3
    assert bundle["status_counts"]["failed"] == 1
    assert bundle["surface_counts"]["telegram"] == 3
    assert bundle["raw_content_visible"] is False
    assert "chat_id" not in encoded.lower()
    assert "private raw text" not in encoded.lower()


def test_debug_bundle_packages_scheduler_failure_and_memory_blocker():
    bundle = build_redacted_debug_bundle(
        incident_ref="ops-followup",
        events=[
            _event(
                surface="scheduler",
                component="task_delivery",
                event_type="scheduled_task_delivery",
                status="failed",
                correlation_id="corr-task",
                metadata={"delivery_target": "telegram"},
            ),
            _event(
                surface="universal_inbox",
                component="memory_write_intent",
                event_type="memory_write_intent",
                status="blocked",
                correlation_id="corr-memory",
                metadata={"reason": "analysis_policy_no_go"},
            ),
        ],
    )
    summary = summarize_debug_bundle(bundle)

    assert bundle["event_counts"]["scheduled_task_delivery"] == 1
    assert bundle["event_counts"]["memory_write_intent"] == 1
    assert bundle["status_counts"]["blocked"] == 1
    assert summary["event_count"] == 2
    assert summary["correlation_count"] == 2
    assert summary["writes_performed"] is False


def test_debug_bundle_rejects_raw_content_and_secret_markers():
    event = _event()
    event["raw_content_visible"] = True
    with pytest.raises(DebugBundleError):
        build_redacted_debug_bundle(incident_ref="bad", events=[event])

    with pytest.raises(DebugBundleError):
        unsafe = _event()
        unsafe["metadata"] = {"note": "Authorization: Bearer secret"}
        build_redacted_debug_bundle(
            incident_ref="bad",
            events=[unsafe],
        )


def test_debug_bundle_mcp_create_returns_redacted_bundle():
    event = _event(event_id="evt-mcp", correlation_id="corr-mcp")
    result = call_debug_tool_contract(
        "debug_bundle_create_redacted",
        {"incident_ref": "telegram-mcp", "events": [event], "limit": 5},
    )
    encoded = json.dumps(result, sort_keys=True)

    assert result["status"] == "success"
    assert result["reason"] == "bundle_created"
    assert result["writes_performed"] is False
    assert result["bundle"]["event_count"] == 1
    assert result["summary"]["event_count"] == 1
    assert "telegram-mcp" in encoded


def test_debug_bundle_mcp_create_requires_events():
    result = call_debug_tool_contract("debug_bundle_create_redacted", {"incident_ref": "empty"})

    assert result["status"] == "blocked"
    assert result["reason"] == "events_required"
    assert result["writes_performed"] is False
