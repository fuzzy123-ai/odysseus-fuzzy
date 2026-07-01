import json

import pytest

import src.agent_tools  # noqa: F401 - registers dynamic built-in tools
from src.sensitive_local_worker import (
    SENSITIVE_LOCAL_ANALYSIS_TOOL,
    SensitiveLocalWorkerError,
    build_sensitive_local_worker_result,
)
from src.tool_execution import execute_tool_block
from src.tool_registry import get_tool
from src.tool_security import ORCHESTRATOR_MODE_ALLOWED_TOOLS, orchestrator_mode_disabled_tools


def test_sensitive_local_worker_builds_redacted_external_safe_result():
    result = build_sensitive_local_worker_result({
        "source_ref": "inbox:abc123",
        "classification": "sensitive",
        "task": "Summarize invoice obligations without exposing personal data.",
        "redacted_context": "Invoice-like document; amount/date/person details redacted.",
        "local_only_required": True,
    }).to_dict()

    assert result["status"] == "ready"
    assert result["raw_content_visible"] is False
    assert result["raw_content_returned"] is False
    assert result["external_model_may_see_result"] is True
    assert result["redacted_abstraction"]["model_scope"] == "local_only"
    assert "Invoice-like document" in result["redacted_abstraction"]["summary"]
    assert "source_hash" in result["redacted_abstraction"]


def test_sensitive_local_worker_rejects_raw_text_arguments():
    with pytest.raises(SensitiveLocalWorkerError, match="forbidden_argument_key:raw_text"):
        build_sensitive_local_worker_result({
            "source_ref": "inbox:abc123",
            "classification": "sensitive",
            "task": "analyze",
            "raw_text": "private raw document",
        })


def test_sensitive_local_worker_rejects_host_paths_and_chat_ids():
    with pytest.raises(SensitiveLocalWorkerError, match="host_path"):
        build_sensitive_local_worker_result({
            "source_ref": r"C:\\Users\\nkatz\\Nextcloud\\Privat\\file.pdf",
            "classification": "sensitive",
            "task": "analyze",
        })
    with pytest.raises(SensitiveLocalWorkerError, match="forbidden_argument_key:chat_id"):
        build_sensitive_local_worker_result({
            "source_ref": "inbox:abc123",
            "classification": "sensitive",
            "task": "analyze",
            "chat_id": "12345",
        })


@pytest.mark.asyncio
async def test_sensitive_local_worker_tool_is_registered_and_executable(monkeypatch):
    monkeypatch.setattr("src.tool_execution.owner_is_admin_or_single_user", lambda owner: True)
    assert get_tool(SENSITIVE_LOCAL_ANALYSIS_TOOL) is not None

    _desc, result = await execute_tool_block(
        type("Block", (), {
            "tool_type": SENSITIVE_LOCAL_ANALYSIS_TOOL,
            "content": json.dumps({
                "source_ref": "telegram:attachment:abc123",
                "classification": "sensitive",
                "task": "Extract a safe summary.",
                "redacted_context": "Document about a school worksheet; names redacted.",
                "local_only_required": True,
            }),
        })(),
        owner="admin",
    )

    assert result["exit_code"] == 0
    assert result["status"] == "ready"
    assert result["raw_content_visible"] is False
    assert "Document about a school worksheet" in result["redacted_abstraction"]["summary"]


@pytest.mark.asyncio
async def test_sensitive_local_worker_tool_blocks_raw_transcript(monkeypatch):
    monkeypatch.setattr("src.tool_execution.owner_is_admin_or_single_user", lambda owner: True)
    _desc, result = await execute_tool_block(
        type("Block", (), {
            "tool_type": SENSITIVE_LOCAL_ANALYSIS_TOOL,
            "content": json.dumps({
                "source_ref": "voice:abc123",
                "classification": "sensitive",
                "task": "analyze",
                "transcript": "raw voice transcript must not cross the boundary",
            }),
        })(),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert result["status"] == "blocked"
    assert "forbidden_argument_key:transcript" in result["error"]
    assert result["raw_content_visible"] is False


def test_sensitive_local_worker_is_allowed_in_orchestrator_mode():
    assert SENSITIVE_LOCAL_ANALYSIS_TOOL in ORCHESTRATOR_MODE_ALLOWED_TOOLS
    assert SENSITIVE_LOCAL_ANALYSIS_TOOL not in orchestrator_mode_disabled_tools()
