import json

import pytest

from src.telegram_task_orchestrator import build_telegram_task_intent
from src.web_research_checkpoint import (
    WebResearchCheckpointError,
    advance_checkpoint,
    checkpoint_from_dict,
    create_initial_checkpoint,
    read_checkpoint,
    write_checkpoint,
)
from src.web_research_scope import build_web_research_scope


def _scope():
    intent = build_telegram_task_intent(
        {"kind": "text", "text": "analysiere https://www.asv-bw.de/hilfe und ins gedaechtnis"},
        workflow_context={"intent": "bounded_site_research_to_memory"},
    )
    return build_web_research_scope(intent.to_dict(), max_pages=3, max_depth=2, external_network_go=True).to_dict()


def test_checkpoint_advances_frontier_without_query_or_raw_content():
    checkpoint = create_initial_checkpoint(_scope())
    advanced = advance_checkpoint(
        checkpoint,
        visited_url="https://www.asv-bw.de/?private=1",
        content_hash="sha256:" + "a" * 64,
        discovered_urls=["https://www.asv-bw.de/kontakt?token=secret", "https://www.asv-bw.de/hilfe"],
        max_depth=2,
    )

    payload = advanced.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["pages_processed"] == 1
    assert payload["status"] == "running"
    assert payload["visited_urls"] == ("https://www.asv-bw.de/",)
    assert "https://www.asv-bw.de/kontakt" in [item["url"] for item in payload["frontier"]]
    assert "private=1" not in encoded
    assert "token=secret" not in encoded
    assert payload["raw_content_visible"] is False


def test_checkpoint_roundtrip_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checkpoint = create_initial_checkpoint(_scope())
    path = write_checkpoint("reports/web/checkpoint.json", checkpoint)
    loaded = read_checkpoint(path)

    assert loaded.to_dict()["scope_id"] == checkpoint.scope_id
    assert loaded.to_dict()["frontier"][0]["url"] == "https://www.asv-bw.de/"


def test_checkpoint_rejects_absolute_path_or_raw_fields(tmp_path):
    checkpoint = create_initial_checkpoint(_scope())
    with pytest.raises(WebResearchCheckpointError):
        write_checkpoint(tmp_path / "checkpoint.json", checkpoint)
    with pytest.raises(WebResearchCheckpointError):
        checkpoint_from_dict({
            "scope_id": "web_scope_abc",
            "frontier": [],
            "visited_urls": [],
            "content_hashes": [],
            "pages_processed": 0,
            "max_pages": 1,
            "status": "ready",
            "updated_at": "2026-07-02T10:00:00Z",
            "raw_text": "secret",
        })
