import json

import pytest

from src.web_research_inventory import WebResearchInventoryError, build_web_research_inventory
from src.web_research_scope import build_web_research_scope
from src.telegram_task_orchestrator import build_telegram_task_intent


def _scope():
    intent = build_telegram_task_intent(
        {"kind": "text", "text": "analysiere https://www.asv-bw.de/hilfe und ins gedaechtnis"},
        workflow_context={"intent": "bounded_site_research_to_memory"},
    )
    return build_web_research_scope(intent.to_dict(), external_network_go=True).to_dict()


def test_inventory_records_source_metadata_without_raw_text_or_query():
    inventory = build_web_research_inventory(
        _scope(),
        [
            {
                "url": "https://www.asv-bw.de/hilfe?session=private",
                "canonical_url": "https://www.asv-bw.de/hilfe",
                "title": "ASV BW Hilfe",
                "text": "Eine Seite mit Hilfe und Anleitung.",
                "headings": ["Hilfe", "Anleitung"],
                "links": ["/kontakt", "https://example.org/out"],
            }
        ],
    )

    payload = inventory.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    source = payload["sources"][0]
    assert payload["source_count"] == 1
    assert source["url"] == "https://www.asv-bw.de/hilfe"
    assert source["content_hash"].startswith("sha256:")
    assert source["text_chars"] == len("Eine Seite mit Hilfe und Anleitung.")
    assert source["internal_links"] == ("https://www.asv-bw.de/kontakt",)
    assert source["external_hosts"] == ("example.org",)
    assert source["raw_content_visible"] is False
    assert "eine seite mit hilfe" not in encoded
    assert "session=private" not in encoded


def test_inventory_tracks_duplicates_and_gaps():
    inventory = build_web_research_inventory(
        _scope(),
        [
            {"url": "https://www.asv-bw.de/a", "title": "", "text": "", "headings": [], "links": []},
            {"url": "https://www.asv-bw.de/b", "title": "", "text": "", "headings": [], "links": []},
            {"url": "https://evil.test/", "title": "External", "text": "x", "headings": ["x"], "links": []},
        ],
    )

    payload = inventory.to_dict()
    assert payload["source_count"] == 1
    assert payload["sources"][0]["gaps"] == ("empty_text", "missing_title", "missing_headings")
    assert {"url": "https://www.asv-bw.de/b", "reason": "duplicate_content"} in payload["skipped"]
    assert {"url": "https://evil.test/", "reason": "domain_not_allowed"} in payload["skipped"]


def test_inventory_skips_raw_html_or_secret_markers_without_persisting_them():
    inventory = build_web_research_inventory(
        _scope(),
        [
            {"url": "https://www.asv-bw.de/raw", "raw_html": "<html>raw</html>"},
            {"url": "https://www.asv-bw.de/secret", "title": "Bearer secret", "text": "x"},
        ],
    )

    payload = inventory.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["source_count"] == 0
    assert len(payload["skipped"]) == 2
    assert "raw_html" not in encoded
    assert "bearer secret" not in encoded
