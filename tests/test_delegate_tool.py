import json

import pytest

import src.agent_loop as al
from src.agent_tools import ToolBlock
from src.delegate_tool import do_delegate
from src.plugin_system import register_context_provider, unregister_context_provider
from src.tool_execution import execute_tool_block


@pytest.mark.asyncio
async def test_delegate_uses_provider_context_and_returns_worker_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_DIR", str(tmp_path))
    captured = {}

    def retrieve(**kwargs):
        captured["provider_kwargs"] = kwargs
        return {
            "structured_state": {"Project.md": {"status": "active"}},
            "snippets": [{"path": "Project.md", "text": "Relevant context", "untrusted": True}],
        }

    register_context_provider({
        "id": "test.delegate_context",
        "capabilities": ["agent"],
        "retrieve": retrieve,
    })

    async def fake_llm_call_async(url, model, messages, **kwargs):
        captured["url"] = url
        captured["model"] = model
        captured["messages"] = messages
        return json.dumps({
            "status": "done",
            "summary": "Worker completed the inspection.",
            "findings": ["Finding A"],
            "suggested_next_step": "Review the result.",
        })

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)
    try:
        result = await do_delegate(
            json.dumps({
                "task": "Inspect the delegate interface.",
                "context_query": "delegate interface",
                "budget": 512,
            }),
            endpoint_url="http://llm.test/v1",
            model="test-model",
            headers={"Authorization": "Bearer test"},
            owner="alice",
            session_id="sess-1",
            context_length=4096,
        )
    finally:
        unregister_context_provider("test.delegate_context")

    assert result["status"] == "done"
    assert result["summary"] == "Worker completed the inspection."
    assert result["findings"] == ["Finding A"]
    assert captured["provider_kwargs"]["query"] == "delegate interface"
    assert captured["provider_kwargs"]["budget"] == 512
    assert any(msg["content"].startswith("Provider structured state") for msg in captured["messages"])
    state_doc = tmp_path / "_state" / "active_run.md"
    assert state_doc.exists()
    assert "Inspect the delegate interface." in state_doc.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_execute_tool_block_delegate_passes_current_endpoint(monkeypatch):
    captured = {}

    async def fake_do_delegate(content, **kwargs):
        captured["content"] = content
        captured["kwargs"] = kwargs
        return {
            "status": "done",
            "summary": "ok",
            "findings": [],
            "suggested_next_step": "",
            "exit_code": 0,
        }

    monkeypatch.setattr("src.delegate_tool.do_delegate", fake_do_delegate)
    desc, result = await execute_tool_block(
        ToolBlock("delegate", json.dumps({"task": "Check something"})),
        endpoint_url="http://endpoint.test",
        model="model-a",
        headers={"X-Test": "1"},
        owner="alice",
        session_id="sess-2",
        context_length=2048,
    )

    assert desc == "delegate"
    assert result["summary"] == "ok"
    assert captured["kwargs"]["endpoint_url"] == "http://endpoint.test"
    assert captured["kwargs"]["model"] == "model-a"
    assert captured["kwargs"]["headers"] == {"X-Test": "1"}


@pytest.mark.asyncio
async def test_orchestrator_agent_loop_initializes_state_doc_and_directive(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    captured = {}

    async def fake_stream(_candidates, messages, **kwargs):
        captured["messages"] = messages
        captured["tools"] = kwargs.get("tools")
        yield "data: " + json.dumps({"delta": "Done."}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream, raising=False)

    chunks = [
        chunk async for chunk in al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Coordinate the implementation."}],
            headers={},
            max_rounds=1,
            owner="alice",
            session_id="sess-orch",
            orchestrator_mode=True,
        )
    ]

    assert any("Done." in chunk for chunk in chunks)
    assert captured["messages"][0]["content"].startswith("## ORCHESTRATOR MODE")
    assert (tmp_path / "_state" / "active_run.md").exists()
