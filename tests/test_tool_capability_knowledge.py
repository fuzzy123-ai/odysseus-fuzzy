import json

import pytest

from src.tool_capability_knowledge import (
    ToolCapabilityKnowledgeError,
    build_coding_agent_capability_knowledge,
    coding_agent_capability_evidence,
)
from src.tool_capability_refresh import refresh_coding_agent_capability_knowledge


def test_coding_agent_capability_knowledge_lists_new_runner_features():
    packet = build_coding_agent_capability_knowledge(commit="abc1234", generated_at="2026-07-03T00:00:00Z")

    ids = {item["id"] for item in packet["capabilities"]}
    assert "project_scope_resolution" in ids
    assert "sandbox_check_evidence" in ids
    assert "telegram_runner_controls" in ids
    assert "publish_deploy_operator_gates" in ids
    assert "deploy-live-go" in packet["live_gates"]
    assert packet["fingerprint"].startswith("sha256:")
    assert packet["raw_content_visible"] is False


def test_coding_agent_capability_knowledge_rejects_secret_or_host_path():
    with pytest.raises(ToolCapabilityKnowledgeError):
        build_coding_agent_capability_knowledge(commit="token=abc123")


def test_coding_agent_capability_evidence_is_memory_safe():
    packet = build_coding_agent_capability_knowledge(commit="abc1234")
    evidence = coding_agent_capability_evidence(packet)

    assert evidence["title"] == "Autonomous coding capabilities"
    assert evidence["content_hash"] == packet["fingerprint"]
    encoded = json.dumps(evidence, sort_keys=True)
    assert "C:\\\\" not in encoded
    assert "token=" not in encoded.lower()


def test_coding_agent_capability_refresh_builds_memory_intent_without_writes():
    packet = refresh_coding_agent_capability_knowledge(commit="abc1234", model="gemma4:e4b")

    assert packet["status"] == "ready"
    assert packet["writes_performed"] is False
    assert packet["memory_write_intent"]["raw_content_visible"] is False
    assert packet["memory_write_intent"]["candidates"][0]["author_stamp"]["model"] == "gemma4:e4b"
    assert packet["memory_write_intent"]["raptorgraph_mapping"]["nodes"]
