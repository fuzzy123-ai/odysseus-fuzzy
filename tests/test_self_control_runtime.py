from types import SimpleNamespace

from src.chat_processor import ChatProcessor
from src.self_control_runtime import build_self_control_context


class _Memory:
    def load(self, owner=None):
        return []


class _Docs:
    rag_manager = None


def test_self_control_context_distinguishes_api_and_local_models():
    api = SimpleNamespace(
        id="api-session",
        endpoint_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o",
    )
    local = SimpleNamespace(
        id="local-session",
        endpoint_url="http://localhost:11434/v1/chat/completions",
        model="gemma3:latest",
    )

    api_context = build_self_control_context(
        session=api,
        owner="alice",
        message="search later",
        settings={"dsgvo_mode": False, "disabled_tools": ["web_search"]},
    )
    local_context = build_self_control_context(
        session=local,
        owner="alice",
        settings={"dsgvo_mode": False, "disabled_tools": []},
    )

    assert "model_profile=api" in api_context
    assert "API model: external provider" in api_context
    assert "web_search" in api_context
    assert "request_secret" in api_context
    assert "model_profile=local" in local_context
    assert "local model: smaller/slower context" in local_context


def test_self_control_context_reports_dsgvo_external_provider_block():
    session = SimpleNamespace(
        id="secure-session",
        endpoint_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o",
    )

    context = build_self_control_context(
        session=session,
        owner="alice",
        message="search the web",
        settings={"dsgvo_mode": True, "disabled_tools": []},
    )

    assert "security_mode=secure" in context
    assert "dsgvo_mode=on" in context
    assert "provider_gate=blocked:external_provider_in_secure_chat" in context
    assert "web_search" in context
    assert "trigger_research" in context


def test_chat_processor_injects_self_control_context_into_preface():
    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())
    session = SimpleNamespace(
        id="chat-session",
        endpoint_url="http://localhost:11434/v1/chat/completions",
        model="gemma3:latest",
    )

    preface, _rag_sources, _web_sources = processor.build_context_preface(
        message="hello",
        session=session,
        use_web=False,
        use_rag=False,
        use_memory=False,
        use_context_providers=False,
        owner="alice",
    )

    joined = "\n".join(message["content"] for message in preface)
    assert "Odysseus Runtime Self-Control" in joined
    assert "model_profile=local" in joined
