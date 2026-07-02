import json

import pytest

from src.telegram_task_orchestrator import build_telegram_task_intent
from src.web_research_scope import WebResearchScopeError, build_web_research_scope


def test_builds_bounded_scope_from_telegram_task_intent():
    intent = build_telegram_task_intent(
        {"kind": "text", "text": "analysiere https://www.asv-bw.de/hilfe?x=1 und ins gedaechtnis"},
        workflow_context={"intent": "bounded_site_research_to_memory"},
    )

    scope = build_web_research_scope(intent.to_dict(), max_pages=25, max_depth=2, external_network_go=True)
    payload = scope.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["seed_url"] == "https://www.asv-bw.de/"
    assert payload["allowed_domains"] == ("asv-bw.de",)
    assert payload["max_pages"] == 25
    assert payload["max_depth"] == 2
    assert payload["external_network_go"] is True
    assert "memory_write_policy" in payload["gates_required"]
    assert "external_network_go" not in payload["gates_required"]
    assert "x=1" not in encoded


def test_scope_without_external_go_is_policy_blocked():
    intent = build_telegram_task_intent(
        {"kind": "text", "text": "analysiere die asv bw hilfeseite und ins memory"},
        workflow_context={"intent": "bounded_site_research_to_memory"},
    )

    scope = build_web_research_scope(intent.to_dict())
    policy = scope.to_policy()
    decision = policy.decide_url("https://www.asv-bw.de/hilfe", depth=0, pages_seen=0)
    assert scope.seed_url == "https://asv-bw.de/"
    assert "external_network_go" in scope.gates_required
    assert decision.allowed is False
    assert decision.reason == "external_network_go_required"


def test_scope_rejects_non_website_task_or_secret_payload():
    with pytest.raises(WebResearchScopeError):
        build_web_research_scope({"task_type": "chat_followup", "target_ref": "https://example.test/"})
    with pytest.raises(WebResearchScopeError):
        build_web_research_scope({
            "task_type": "website_research",
            "target_ref": "https://example.test/",
            "raw_text": "do not persist",
        })
