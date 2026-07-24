from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src import agent_loop, context_compactor, llm_core, model_context
from src.model_context import ContextLengthSnapshot


ENDPOINT = "http://127.0.0.1:11434/api"
MODEL = "foreign-local-model"


@pytest.fixture(autouse=True)
def _clear_request_snapshot_bindings():
    llm_core.clear_request_context_snapshots()
    yield
    llm_core.clear_request_context_snapshots()


class _StreamResponse:
    status_code = 200

    async def aread(self):
        return b""

    async def aiter_lines(self):
        yield json.dumps({"message": {"content": "ok"}, "done": True})


class _StreamContext:
    def __init__(self, client, payload) -> None:
        self.client = client
        self.payload = payload

    async def __aenter__(self):
        self.client.payloads.append(self.payload)
        return _StreamResponse()

    async def __aexit__(self, *_args):
        return False


class _StreamClient:
    def __init__(self) -> None:
        self.payloads = []

    def stream(self, _method, _url, **kwargs):
        return _StreamContext(self, kwargs.get("json"))


@pytest.mark.asyncio
async def test_compactor_agent_and_core_share_one_immutable_snapshot(monkeypatch) -> None:
    calls = 0
    discovered = ContextLengthSnapshot(32_768, True, "probe", 7)

    async def fake_snapshot(_url, _model):
        nonlocal calls
        calls += 1
        return discovered

    monkeypatch.setattr(llm_core, "get_context_snapshot_async", fake_snapshot)
    messages = [{"role": "user", "content": "short"}]

    compacted, context_length, was_compacted = await context_compactor.maybe_compact(
        None,
        ENDPOINT,
        MODEL,
        messages,
    )
    bound = llm_core.get_request_context_snapshot(ENDPOINT, MODEL)
    agent_snapshot = await agent_loop._resolve_agent_context_snapshot(
        ENDPOINT, MODEL, context_length
    )
    core_snapshot = await llm_core.resolve_request_context_snapshot(ENDPOINT, MODEL)

    assert compacted == messages
    assert was_compacted is False
    assert context_length == 32_768
    assert calls == 1
    assert bound is discovered
    assert agent_snapshot is bound
    assert core_snapshot is bound


def test_exact_gemma_profile_cap_and_foreign_model_noninterference() -> None:
    discovered = ContextLengthSnapshot(131_072, True, "probe", 3)
    gemma = llm_core.bind_request_context_snapshot(
        ENDPOINT, "gemma3:4b", discovered
    )
    foreign = llm_core.bind_request_context_snapshot(
        ENDPOINT, "qwen3:14b", discovered
    )
    unknown_gemma = llm_core.bind_request_context_snapshot(
        "http://127.0.0.1:11435/api",
        "gemma3:4b",
        ContextLengthSnapshot(model_context.DEFAULT_CONTEXT, False, "negative_cache", 4),
    )

    assert gemma.context_length == 8_192
    assert gemma.known is True
    assert gemma.cache_status == "gemma_profile_cap"
    assert unknown_gemma.context_length == 8_192
    assert unknown_gemma.known is True
    assert foreign is discovered
    assert foreign.context_length == 131_072


@pytest.mark.asyncio
async def test_explicit_foreign_context_is_preserved_without_probe(monkeypatch) -> None:
    async def forbidden_probe(*_args, **_kwargs):
        raise AssertionError("explicit context unexpectedly probed")

    monkeypatch.setattr(llm_core, "get_context_snapshot_async", forbidden_probe)
    snapshot = await agent_loop._resolve_agent_context_snapshot(
        ENDPOINT, MODEL, 12_345
    )

    assert snapshot.context_length == 12_345
    assert snapshot.known is True
    assert snapshot.cache_status == "caller_supplied"


@pytest.mark.asyncio
async def test_direct_agent_without_snapshot_stays_conservative_until_route_migration(
    monkeypatch,
) -> None:
    async def forbidden_probe(*_args, **_kwargs):
        raise AssertionError("GMI-09A direct-agent compatibility path probed")

    monkeypatch.setattr(llm_core, "get_context_snapshot_async", forbidden_probe)
    snapshot = await agent_loop._resolve_agent_context_snapshot(ENDPOINT, MODEL, 0)

    assert snapshot.context_length == model_context.DEFAULT_CONTEXT
    assert snapshot.known is False
    assert snapshot.cache_status == "request_default"


@pytest.mark.asyncio
async def test_async_nonstream_ollama_payload_uses_snapshot_not_sync_probe(monkeypatch) -> None:
    calls = 0
    captured = {}

    async def fake_snapshot(_url, _model):
        nonlocal calls
        calls += 1
        return ContextLengthSnapshot(24_576, True, "probe", 1)

    def forbidden_sync_probe(*_args, **_kwargs):
        raise AssertionError("synchronous context probe executed in event loop")

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": "OK"}, "done": True},
        )

    monkeypatch.setattr(llm_core, "get_context_snapshot_async", fake_snapshot)
    monkeypatch.setattr(llm_core, "get_context_length", forbidden_sync_probe)
    result = await llm_core._llm_call_async_impl(
        ENDPOINT,
        MODEL,
        [{"role": "user", "content": "unique snapshot request"}],
        max_retries=1,
        transport=httpx.MockTransport(handler),
    )

    assert result == "OK"
    assert calls == 1
    assert captured["options"]["num_ctx"] == 24_576


@pytest.mark.asyncio
async def test_exact_gemma_cap_reaches_async_upstream_payload(monkeypatch) -> None:
    captured = {}

    async def oversized_snapshot(_url, _model):
        return ContextLengthSnapshot(131_072, True, "probe", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": "OK"}, "done": True},
        )

    monkeypatch.setattr(llm_core, "get_context_snapshot_async", oversized_snapshot)
    result = await llm_core._llm_call_async_impl(
        ENDPOINT,
        "gemma3:4b",
        [{"role": "user", "content": "unique capped request"}],
        max_retries=1,
        transport=httpx.MockTransport(handler),
    )

    assert result == "OK"
    assert captured["options"]["num_ctx"] == 8_192


@pytest.mark.asyncio
async def test_async_stream_uses_compactor_bound_snapshot_without_second_probe(monkeypatch) -> None:
    calls = 0
    client = _StreamClient()

    async def fake_snapshot(_url, _model):
        nonlocal calls
        calls += 1
        return ContextLengthSnapshot(16_384, True, "probe", 2)

    def forbidden_sync_probe(*_args, **_kwargs):
        raise AssertionError("synchronous context probe executed in stream")

    monkeypatch.setattr(llm_core, "get_context_snapshot_async", fake_snapshot)
    monkeypatch.setattr(llm_core, "get_context_length", forbidden_sync_probe)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_args, **_kwargs: None)

    await context_compactor.maybe_compact(
        None,
        ENDPOINT,
        MODEL,
        [{"role": "user", "content": "short"}],
    )
    chunks = [
        chunk
        async for chunk in llm_core.stream_llm(
            ENDPOINT,
            MODEL,
            [{"role": "user", "content": "stream"}],
        )
    ]

    assert chunks
    assert calls == 1
    assert client.payloads[0]["options"]["num_ctx"] == 16_384


@pytest.mark.asyncio
async def test_slow_async_snapshot_keeps_event_loop_heartbeat_alive(monkeypatch) -> None:
    ticks = 0
    running = True

    async def slow_snapshot(_url, _model):
        await asyncio.sleep(0.06)
        return ContextLengthSnapshot(8_000, True, "probe", 0)

    async def heartbeat():
        nonlocal ticks
        while running:
            ticks += 1
            await asyncio.sleep(0)

    monkeypatch.setattr(llm_core, "get_context_snapshot_async", slow_snapshot)
    beat = asyncio.create_task(heartbeat())
    try:
        snapshot = await llm_core.resolve_request_context_snapshot(ENDPOINT, MODEL)
    finally:
        running = False
        await beat

    assert snapshot.context_length == 8_000
    assert ticks >= 100


def test_request_snapshot_binding_is_content_free_in_snapshot_audit() -> None:
    snapshot = llm_core.bind_request_context_snapshot(
        "https://secret-endpoint.example.test/v1",
        "secret-model",
        ContextLengthSnapshot(10_000, True, "probe", 1),
    )
    diagnostic = json.dumps(snapshot.audit_dict(), sort_keys=True)

    assert "secret-endpoint" not in diagnostic
    assert "secret-model" not in diagnostic
