import asyncio
from types import SimpleNamespace

from services.search import content as search_content
from services.search import core as search_core
from src.agent_tools.web_tools import WebFetchTool, WebSearchTool
from src.chat_processor import ChatProcessor
from src.privacy_runtime import EXTERNAL_IO_BLOCK_REASON, EXTERNAL_IO_BLOCK_MESSAGE
from src.tool_policy import build_effective_tool_policy


class _Memory:
    def load(self, owner=None):
        return []


class _Docs:
    rag_manager = None


def _processor():
    return ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())


def _session():
    return SimpleNamespace(
        id="session-1",
        endpoint_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o",
    )


def _joined_content(preface):
    return "\n".join(msg.get("content", "") for msg in preface)


def test_dsgvo_tool_policy_hides_external_and_unsafe_tools(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")

    policy = build_effective_tool_policy(last_user_message="search the web")

    for tool in ("web_search", "web_fetch", "trigger_research", "bash", "python"):
        assert policy.blocks(tool)
        assert tool in policy.hidden_tools
        assert "DSGVO mode requires local-only processing" in policy.reason_for(tool)

    assert not policy.blocks("read_file")


def test_dsgvo_blocks_direct_chat_web_search_without_calling_search(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")

    def fail_search(*_args, **_kwargs):
        raise AssertionError("web search must not run in DSGVO mode")

    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", fail_search)

    preface, _rag_sources, web_sources = _processor().build_context_preface(
        message="latest Odysseus news",
        session=_session(),
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_context_providers=False,
    )

    assert web_sources == []
    assert EXTERNAL_IO_BLOCK_MESSAGE in _joined_content(preface)


def test_dsgvo_blocks_url_auto_fetch_without_calling_fetch(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("URL auto-fetch must not run in DSGVO mode")

    monkeypatch.setattr("src.chat_processor.fetch_webpage_content", fail_fetch)

    preface, _rag_sources, _web_sources = _processor().build_context_preface(
        message="Please summarize https://example.com/privacy",
        session=_session(),
        use_web=False,
        use_rag=False,
        use_memory=False,
        use_context_providers=False,
    )

    content = _joined_content(preface)
    assert EXTERNAL_IO_BLOCK_MESSAGE in content
    assert "Source: web page auto-fetch policy" in content


def test_dsgvo_blocks_web_tools_before_search_or_fetch(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")

    search_result = asyncio.run(WebSearchTool().execute("latest python", {}))
    fetch_result = asyncio.run(WebFetchTool().execute("https://example.com", {}))

    assert search_result["exit_code"] == 1
    assert fetch_result["exit_code"] == 1
    assert search_result["blocked_by"] == EXTERNAL_IO_BLOCK_REASON
    assert fetch_result["blocked_by"] == EXTERNAL_IO_BLOCK_REASON


def test_dsgvo_blocks_central_search_and_fetch_before_network(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("search provider must not run in DSGVO mode")

    def fail_public_url(*_args, **_kwargs):
        raise AssertionError("HTTP fetch must not run in DSGVO mode")

    monkeypatch.setattr(search_core, "_call_provider", fail_provider)
    monkeypatch.setattr(search_content, "_get_public_url", fail_public_url)

    context, sources = search_core.comprehensive_web_search("latest python", return_sources=True)
    fetched = search_content.fetch_webpage_content("https://example.com")

    assert context == EXTERNAL_IO_BLOCK_MESSAGE
    assert sources == []
    assert fetched["success"] is False
    assert EXTERNAL_IO_BLOCK_REASON in fetched["error"]
