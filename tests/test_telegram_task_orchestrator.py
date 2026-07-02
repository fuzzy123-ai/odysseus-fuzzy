import json

import pytest

from src.telegram_task_orchestrator import (
    TelegramTaskOrchestratorError,
    build_telegram_task_intent,
    build_telegram_task_status_message,
)


def test_builds_website_research_to_memory_intent_without_raw_text():
    intent = build_telegram_task_intent(
        {
            "kind": "text",
            "text": "Analysiere https://www.asv-bw.de/hilfe?private=1 vollstaendig und fasse alles im Gedaechtnis zusammen.",
        },
        workflow_context={
            "channel": "telegram",
            "message_kind": "text",
            "intent": "bounded_site_research_to_memory",
            "recent_attachment": {"present": False},
        },
    )

    payload = intent.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["task_type"] == "website_research_to_memory"
    assert payload["target_ref"] == "https://www.asv-bw.de/"
    assert payload["requested_output"] == "memory_and_raptorgraph_candidates"
    assert payload["status"] == "waiting_for_gate"
    assert payload["raw_content_visible"] is False
    assert "live_web_target_approval" in payload["gates_required"]
    assert "memory_write_policy" in payload["gates_required"]
    assert "private=1" not in encoded
    assert "vollstaendig" not in encoded


def test_asv_bw_text_without_url_requires_target_gate_but_uses_safe_hint():
    intent = build_telegram_task_intent(
        {"kind": "text", "text": "analysiere die asv bw hilfeseite vollstaendig und ins gedaechtnis"},
        workflow_context={"intent": "bounded_site_research_to_memory", "recent_attachment": {"present": False}},
    )

    payload = intent.to_dict()
    assert payload["target_ref"] == "domain:asv-bw.de"
    assert payload["target_status"] == "ready"
    assert "live_web_target_approval" in payload["gates_required"]
    assert "memory_write_policy" in payload["gates_required"]


def test_site_research_without_target_asks_for_missing_target():
    intent = build_telegram_task_intent(
        {"kind": "text", "text": "analysiere diese Hilfeseite und fasse sie zusammen"},
        workflow_context={"intent": "bounded_site_research_to_memory", "recent_attachment": {"present": False}},
    )

    assert intent.target_status == "needs_target_resolution"
    assert "target_resolution" in intent.gates_required
    assert "freigegebener Ziel-Link" in build_telegram_task_status_message(intent)


def test_builds_coding_agent_task_intent_with_repo_scope_gate():
    intent = build_telegram_task_intent(
        {"kind": "text", "text": "Baue im Projekt demo ein kleines Feature und teste es."},
        workflow_context={"intent": "coding_agent_task", "recent_attachment": {"present": False}},
    )

    payload = intent.to_dict()
    assert payload["task_type"] == "coding_agent_task"
    assert payload["target_kind"] == "repo"
    assert payload["target_ref"] == "repo:demo"
    assert payload["requested_output"] == "sandbox_coding_task"
    assert "coding_task_scope_review" in payload["gates_required"]
    assert "sandbox_execution_policy" in payload["gates_required"]
    assert payload["raw_content_visible"] is False


def test_coding_agent_task_without_repo_asks_for_resolution():
    intent = build_telegram_task_intent({"kind": "text", "text": "implementiere ein Feature und teste es"})

    assert intent.task_type == "coding_agent_task"
    assert intent.target_status == "needs_repo_resolution"
    assert "repo_resolution" in intent.gates_required
    assert "freigegebenes Projekt" in build_telegram_task_status_message(intent)


def test_rejects_untrusted_workflow_context_fields():
    with pytest.raises(TelegramTaskOrchestratorError):
        build_telegram_task_intent(
            {"kind": "text", "text": "hi"},
            workflow_context={"intent": "follow_up", "raw_text": "do not persist this"},
        )
