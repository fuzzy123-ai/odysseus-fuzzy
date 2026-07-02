import json
import re

import pytest

from src.memory_candidate_schema import MemoryCandidateError, build_memory_candidates_from_synthesis


def _synthesis():
    return {
        "schema": "odysseus.web_research_synthesis.v1",
        "synthesis_id": "web_syn_abc",
        "scope_id": "web_scope_abc",
        "model_route": "api_allowed",
        "topics": [{"name": "ASV BW Hilfe", "summary": "Beschreibt wichtige Hilfeprozesse."}],
        "source_refs": ("https://www.asv-bw.de/hilfe",),
        "confidence": 0.82,
        "raw_content_visible": False,
    }


def test_memory_candidate_has_provenance_model_stamp_and_internal_ref():
    candidates = build_memory_candidates_from_synthesis(_synthesis(), model="gemma4:e4b")

    payload = candidates[0].to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["candidate_id"].startswith("memcand_")
    assert payload["title"] == "ASV BW Hilfe"
    assert payload["source_refs"] == ("https://www.asv-bw.de/hilfe",)
    assert payload["confidence"] == 0.82
    assert payload["author_stamp"]["model"] == "gemma4:e4b"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload["author_stamp"]["created_at"])
    assert payload["internal_ref"]["uri"].startswith("odysseus://memory/")
    assert payload["raw_content_visible"] is False
    assert "raw_text" not in encoded


def test_memory_candidate_rejects_missing_sources_or_secret_markers():
    no_source = dict(_synthesis())
    no_source["source_refs"] = ()
    with pytest.raises(MemoryCandidateError):
        build_memory_candidates_from_synthesis(no_source, model="gemma4:e4b")

    secret = dict(_synthesis())
    secret["topics"] = [{"name": "Bearer secret", "summary": "x"}]
    with pytest.raises(MemoryCandidateError):
        build_memory_candidates_from_synthesis(secret, model="gemma4:e4b")
