import json

import pytest

from src.web_research_synthesis import WebResearchSynthesisError, build_web_research_synthesis


def _inventory():
    return {
        "schema": "odysseus.web_research_inventory.v1",
        "scope_id": "web_scope_abc",
        "source_count": 1,
        "sources": [
            {
                "canonical_url": "https://www.asv-bw.de/hilfe",
                "content_hash": "sha256:" + "a" * 64,
                "raw_content_visible": False,
            }
        ],
        "skipped": [{"url": "https://www.asv-bw.de/login", "reason": "login_page_blocked"}],
    }


def test_synthesis_builds_source_linked_topics_without_raw_content():
    synthesis = build_web_research_synthesis(
        _inventory(),
        [
            {
                "source_ref": "https://www.asv-bw.de/hilfe",
                "topics": [{"name": "Login Hilfe", "summary": "Beschreibt den Zugang."}],
                "processes": [{"name": "Support kontaktieren", "summary": "Kontaktweg bei Problemen."}],
                "faqs": [{"question": "Wie finde ich Hilfe?", "answer": "Ueber die Hilfeseite."}],
                "gaps": ["contact_details_missing"],
            }
        ],
        dsgvo_mode=False,
        sensitivity="public",
        preferred_model_route="api_allowed",
    )

    payload = synthesis.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["model_route"] == "api_allowed"
    assert payload["topics"][0]["name"] == "Login Hilfe"
    assert payload["processes"][0]["source_refs"] == ("https://www.asv-bw.de/hilfe",)
    assert "login_page_blocked" in payload["gaps"]
    assert "contact_details_missing" in payload["gaps"]
    assert payload["confidence"] > 0.5
    assert payload["raw_content_visible"] is False
    assert "raw_text" not in encoded


def test_synthesis_forces_local_route_for_dsgvo_or_sensitive_content():
    payload = build_web_research_synthesis(
        _inventory(),
        [{"source_ref": "sha256:" + "b" * 64, "topics": ["Datenschutz"]}],
        dsgvo_mode=True,
        sensitivity="sensitive",
        preferred_model_route="api_allowed",
    ).to_dict()

    assert payload["model_route"] == "local_only"


def test_synthesis_rejects_raw_or_secret_input():
    with pytest.raises(WebResearchSynthesisError):
        build_web_research_synthesis(_inventory(), [{"raw_text": "private raw text"}])
    with pytest.raises(WebResearchSynthesisError):
        build_web_research_synthesis(
            _inventory(),
            [{"source_ref": "https://www.asv-bw.de/hilfe", "topics": [{"name": "Bearer secret"}]}],
        )
