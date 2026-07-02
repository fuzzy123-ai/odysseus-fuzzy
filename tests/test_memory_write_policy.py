import pytest

from src.memory_candidate_schema import build_memory_candidates_from_synthesis
from src.memory_write_policy import MemoryWritePolicyError, decide_memory_write_policy


def _candidate(confidence=0.82, sensitivity="public"):
    synthesis = {
        "scope_id": "web_scope_abc",
        "topics": [{"name": "ASV BW Hilfe", "summary": "Beschreibt wichtige Hilfeprozesse."}],
        "source_refs": ("https://www.asv-bw.de/hilfe",),
        "confidence": confidence,
    }
    return build_memory_candidates_from_synthesis(synthesis, model="gemma4:e4b", sensitivity=sensitivity)[0].to_dict()


def test_memory_write_policy_allows_auto_write_only_when_enabled_and_confident():
    decision = decide_memory_write_policy(
        [_candidate()],
        operator_auto_write_enabled=True,
        model_route="api_allowed",
    ).to_dict()

    assert decision["action"] == "auto_write"
    assert decision["auto_write_allowed"] is True
    assert decision["review_required"] is False
    assert decision["blocked"] is False


def test_memory_write_policy_requires_review_when_auto_write_disabled_or_low_confidence():
    disabled = decide_memory_write_policy([_candidate()], operator_auto_write_enabled=False).to_dict()
    low = decide_memory_write_policy([_candidate(confidence=0.5)], operator_auto_write_enabled=True).to_dict()

    assert disabled["action"] == "review_required"
    assert "operator_auto_write_disabled" in disabled["reasons"]
    assert low["action"] == "review_required"
    assert "confidence_below_threshold" in low["reasons"]


def test_memory_write_policy_blocks_dsgvo_api_route_and_unsafe_payload():
    blocked = decide_memory_write_policy(
        [_candidate(sensitivity="sensitive")],
        dsgvo_mode=True,
        model_route="api_allowed",
        operator_auto_write_enabled=True,
    ).to_dict()

    assert blocked["action"] == "blocked"
    assert "dsgvo_requires_local_only" in blocked["reasons"]
    assert "sensitive_candidate_requires_local_only" in blocked["reasons"]

    candidate = _candidate()
    candidate["raw_text"] = "private raw text"
    with pytest.raises(MemoryWritePolicyError):
        decide_memory_write_policy([candidate])
