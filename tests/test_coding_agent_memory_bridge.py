import pytest

from src.coding_agent_memory_bridge import (
    CodingAgentMemoryBridgeError,
    build_coding_agent_capability_memory_write_intent,
    build_coding_agent_memory_write_intent,
)
from src.tool_capability_knowledge import build_coding_agent_capability_knowledge


def test_coding_agent_memory_bridge_builds_candidates_and_raptorgraph_mapping():
    intent = build_coding_agent_memory_write_intent(
        {
            "title": "Sandbox check result",
            "summary": "Focused backend tests passed in a sandbox dry run.",
            "content_hash": "sha256:" + "a" * 64,
            "confidence": 0.9,
            "sensitivity": "project",
            "artifacts": [{"content_hash": "sha256:" + "b" * 64}],
        },
        model="gemma4:e4b",
        operator_auto_write_enabled=False,
    )

    assert intent["policy"]["review_required"] is True
    assert intent["candidates"][0]["author_stamp"]["model"] == "gemma4:e4b"
    assert intent["raptorgraph_mapping"]["nodes"]
    assert intent["raw_content_visible"] is False


def test_coding_agent_memory_bridge_blocks_raw_or_secret_evidence():
    with pytest.raises(CodingAgentMemoryBridgeError):
        build_coding_agent_memory_write_intent(
            {
                "title": "Bad",
                "summary": "Authorization: Bearer abcdefghijk",
                "content_hash": "sha256:" + "a" * 64,
            },
            model="gemma4:e4b",
        )

    with pytest.raises(CodingAgentMemoryBridgeError):
        build_coding_agent_memory_write_intent(
            {
                "title": "Bad",
                "summary": "ok",
                "raw_content_visible": True,
            },
            model="gemma4:e4b",
        )


def test_coding_agent_memory_bridge_accepts_capability_knowledge_packet():
    knowledge = build_coding_agent_capability_knowledge(commit="abc1234")

    intent = build_coding_agent_capability_memory_write_intent(
        knowledge,
        model="gemma4:e4b",
        operator_auto_write_enabled=False,
    )

    assert intent["policy"]["review_required"] is True
    assert intent["candidates"][0]["author_stamp"]["model"] == "gemma4:e4b"
    assert intent["raptorgraph_mapping"]["nodes"]
    assert intent["raw_content_visible"] is False
