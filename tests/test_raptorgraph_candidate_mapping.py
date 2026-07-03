import json

import pytest

from src.memory_candidate_schema import build_memory_candidates_from_synthesis
from src.raptorgraph_candidate_mapping import (
    RaptorGraphCandidateMappingError,
    map_memory_candidates_to_raptorgraph,
)


def _candidate():
    synthesis = {
        "scope_id": "web_scope_abc",
        "topics": [{"name": "ASV BW Hilfe", "summary": "Beschreibt wichtige Hilfeprozesse."}],
        "source_refs": ("https://www.asv-bw.de/hilfe",),
        "confidence": 0.82,
    }
    return build_memory_candidates_from_synthesis(synthesis, model="gemma4:e4b")[0].to_dict()


def test_maps_memory_candidate_to_raptor_nodes_and_edges():
    mapping = map_memory_candidates_to_raptorgraph([_candidate()])
    payload = mapping.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert payload["mapping_id"].startswith("rgmap_")
    assert payload["correlation_id"].startswith("sha256:")
    assert payload["runtime_event"]["surface"] == "raptorgraph"
    assert payload["runtime_event"]["component"] == "candidate_mapping"
    assert payload["runtime_event"]["status"] == "queued"
    assert payload["runtime_event"]["raw_content_visible"] is False
    assert payload["runtime_event"]["metadata"]["node_count"] == 2
    assert payload["runtime_event"]["metadata"]["edge_count"] == 1
    assert len(payload["nodes"]) == 2
    assert len(payload["edges"]) == 1
    assert payload["nodes"][0]["internal_ref"]["uri"].startswith("odysseus://raptor/node/")
    assert payload["edges"][0]["internal_ref"]["uri"].startswith("odysseus://raptor/edge/")
    assert payload["edges"][0]["truth_write_allowed"] is False
    assert payload["raw_content_visible"] is False
    assert "raw_text" not in encoded


def test_mapping_rejects_secret_or_invalid_source_ref():
    candidate = _candidate()
    candidate["title"] = "Bearer secret"
    with pytest.raises(RaptorGraphCandidateMappingError):
        map_memory_candidates_to_raptorgraph([candidate])

    candidate = _candidate()
    candidate["source_refs"] = ("https://www.asv-bw.de/hilfe?token=secret",)
    with pytest.raises(RaptorGraphCandidateMappingError):
        map_memory_candidates_to_raptorgraph([candidate])
