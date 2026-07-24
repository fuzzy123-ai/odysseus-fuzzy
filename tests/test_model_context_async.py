from __future__ import annotations

import asyncio
import json

import pytest

import src.model_context as model_context
from src.model_context import (
    ASYNC_CONTEXT_REGISTRY_SCHEMA,
    AsyncModelContextService,
    ContextProbeHTTPResponse,
)


MODEL = "custom-context-model"
ENDPOINT = "https://context-one.example.test/v1/chat/completions"


class _Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _models_response(context_length: int, *, model: str = MODEL) -> ContextProbeHTTPResponse:
    return ContextProbeHTTPResponse(
        200,
        {"data": [{"id": model, "context_length": context_length}]},
    )


class _SequenceTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float]] = []

    async def __call__(self, url: str, timeout: float) -> ContextProbeHTTPResponse:
        self.calls.append((url, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _BlockingTransport:
    def __init__(self, response: ContextProbeHTTPResponse) -> None:
        self.response = response
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, _url: str, _timeout: float) -> ContextProbeHTTPResponse:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.response


class _StaleRefreshTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.refresh_started = asyncio.Event()
        self.release_refresh = asyncio.Event()

    async def __call__(self, _url: str, _timeout: float) -> ContextProbeHTTPResponse:
        self.calls += 1
        if self.calls == 1:
            return _models_response(8_192)
        self.refresh_started.set()
        await self.release_refresh.wait()
        return _models_response(16_384)


class _CapacityTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.two_started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, _url: str, _timeout: float) -> ContextProbeHTTPResponse:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.calls == 2:
            self.two_started.set()
        await self.release.wait()
        self.active -= 1
        return _models_response(12_000)


class _GenerationTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.old_started = asyncio.Event()
        self.release_old = asyncio.Event()

    async def __call__(self, _url: str, _timeout: float) -> ContextProbeHTTPResponse:
        self.calls += 1
        if self.calls == 1:
            self.old_started.set()
            await self.release_old.wait()
            return _models_response(8_000)
        return _models_response(16_000)


@pytest.mark.asyncio
async def test_positive_ttl_uses_injected_clock_and_transport() -> None:
    clock = _Clock()
    transport = _SequenceTransport([_models_response(8_192)])
    service = AsyncModelContextService(
        fresh_ttl_seconds=10,
        stale_ttl_seconds=20,
        negative_ttl_seconds=3,
        clock=clock,
        transport=transport,
    )

    first = await service.get_snapshot(ENDPOINT, MODEL)
    clock.advance(9)
    second = await service.get_snapshot(ENDPOINT, MODEL)

    assert first.context_length == 8_192
    assert first.known is True
    assert first.cache_status == "probe"
    assert second.context_length == 8_192
    assert second.cache_status == "fresh_cache"
    assert len(transport.calls) == 1
    assert transport.calls[0][0].endswith("/models")
    assert transport.calls[0][1] == 5.0


@pytest.mark.asyncio
async def test_stale_while_revalidate_returns_immediately_then_refreshes() -> None:
    clock = _Clock()
    transport = _StaleRefreshTransport()
    service = AsyncModelContextService(
        fresh_ttl_seconds=5,
        stale_ttl_seconds=20,
        negative_ttl_seconds=2,
        clock=clock,
        transport=transport,
    )
    await service.get_snapshot(ENDPOINT, MODEL)
    clock.advance(6)

    stale = await asyncio.wait_for(service.get_snapshot(ENDPOINT, MODEL), timeout=0.2)
    await asyncio.wait_for(transport.refresh_started.wait(), timeout=1.0)

    assert stale.context_length == 8_192
    assert stale.cache_status == "stale_cache"
    transport.release_refresh.set()
    await service.wait_for_idle()

    refreshed = await service.get_snapshot(ENDPOINT, MODEL)
    assert refreshed.context_length == 16_384
    assert refreshed.cache_status == "fresh_cache"
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_negative_cache_retries_only_after_negative_ttl() -> None:
    clock = _Clock()
    empty = ContextProbeHTTPResponse(200, {"data": []})
    transport = _SequenceTransport([empty, empty])
    service = AsyncModelContextService(
        fresh_ttl_seconds=10,
        stale_ttl_seconds=20,
        negative_ttl_seconds=3,
        clock=clock,
        transport=transport,
    )

    first = await service.get_snapshot(ENDPOINT, MODEL)
    clock.advance(2)
    cached = await service.get_snapshot(ENDPOINT, MODEL)
    clock.advance(2)
    retried = await service.get_snapshot(ENDPOINT, MODEL)

    assert first.context_length == model_context.DEFAULT_CONTEXT
    assert first.known is False
    assert cached.cache_status == "negative_cache"
    assert retried.cache_status == "probe"
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_same_key_concurrency_is_single_flight() -> None:
    transport = _BlockingTransport(_models_response(24_000))
    service = AsyncModelContextService(transport=transport)

    tasks = [
        asyncio.create_task(service.get_snapshot(ENDPOINT, MODEL))
        for _ in range(100)
    ]
    await asyncio.wait_for(transport.started.wait(), timeout=1.0)
    await asyncio.sleep(0)
    snapshot = await service.registry_snapshot()

    assert snapshot["inflight_count"] == 1
    assert snapshot["registry_key_count"] == 1
    transport.release.set()
    results = await asyncio.gather(*tasks)

    assert transport.calls == 1
    assert {result.context_length for result in results} == {24_000}
    final = await service.registry_snapshot()
    assert final["singleflight_joins_total"] == 99
    assert final["inflight_count"] == 0


def test_default_stale_grace_matches_binding_roadmap_contract() -> None:
    service = AsyncModelContextService()

    assert service.stale_ttl_seconds == 3_600.0


@pytest.mark.asyncio
async def test_registry_is_bounded_for_concurrent_unique_keys() -> None:
    transport = _CapacityTransport()
    service = AsyncModelContextService(max_entries=2, transport=transport)
    endpoints = [f"https://context-{index}.example.test/v1" for index in range(3)]
    tasks = [
        asyncio.create_task(service.get_snapshot(endpoint, MODEL))
        for endpoint in endpoints
    ]

    await asyncio.wait_for(transport.two_started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)
    during = await service.registry_snapshot()

    assert transport.calls == 2
    assert transport.max_active == 2
    assert during["registry_key_count"] == 2
    assert during["inflight_count"] == 2
    transport.release.set()
    await asyncio.gather(*tasks)

    final = await service.registry_snapshot()
    assert transport.calls == 3
    assert final["registry_key_count"] == 2
    assert final["entry_count"] == 2
    assert final["evictions_total"] == 1


@pytest.mark.asyncio
async def test_registry_eviction_is_deterministic_lru() -> None:
    transport = _SequenceTransport([_models_response(10_000)] * 4)
    service = AsyncModelContextService(max_entries=2, transport=transport)
    first = "https://first.example.test/v1"
    second = "https://second.example.test/v1"
    third = "https://third.example.test/v1"

    await service.get_snapshot(first, MODEL)
    await service.get_snapshot(second, MODEL)
    await service.get_snapshot(third, MODEL)
    await service.get_snapshot(first, MODEL)

    snapshot = await service.registry_snapshot()
    assert len(transport.calls) == 4
    assert snapshot["entry_count"] == 2
    assert snapshot["evictions_total"] == 2


@pytest.mark.asyncio
async def test_endpoint_generation_fences_old_inflight_probe() -> None:
    transport = _GenerationTransport()
    service = AsyncModelContextService(max_entries=2, transport=transport)
    old_task = asyncio.create_task(service.get_snapshot(ENDPOINT, MODEL))
    await asyncio.wait_for(transport.old_started.wait(), timeout=1.0)

    generation = await service.invalidate_endpoint(ENDPOINT)
    current = await service.get_snapshot(ENDPOINT, MODEL)
    transport.release_old.set()
    old = await old_task
    cached = await service.get_snapshot(ENDPOINT, MODEL)

    assert generation == 1
    assert current.context_length == 16_000
    assert current.endpoint_generation == 1
    assert old.context_length == 8_000
    assert old.cache_status == "generation_superseded"
    assert cached.context_length == 16_000
    assert cached.cache_status == "fresh_cache"
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_local_slots_probe_wins_without_models_call() -> None:
    transport = _SequenceTransport(
        [ContextProbeHTTPResponse(200, [{"n_ctx": 32_768}])]
    )
    service = AsyncModelContextService(transport=transport)

    result = await service.get_snapshot(
        "http://127.0.0.1:11434/v1/chat/completions", MODEL
    )

    assert result.context_length == 32_768
    assert result.known is True
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "http://127.0.0.1:11434/slots"


@pytest.mark.asyncio
async def test_known_model_fallback_survives_transport_failure() -> None:
    transport = _SequenceTransport([RuntimeError("offline")])
    service = AsyncModelContextService(transport=transport)

    result = await service.get_snapshot(ENDPOINT, "gpt-5")

    assert result.context_length == 400_000
    assert result.known is True
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_async_service_never_calls_synchronous_probe(monkeypatch) -> None:
    def forbidden_sync_probe(*_args, **_kwargs):
        raise AssertionError("async service called synchronous context probe")

    monkeypatch.setattr(model_context, "_query_context_length", forbidden_sync_probe)
    transport = _SequenceTransport([_models_response(9_000)])
    service = AsyncModelContextService(transport=transport)

    value = await service.get_context_length(ENDPOINT, MODEL)
    known = await service.get_context_length_known(ENDPOINT, MODEL)

    assert value == 9_000
    assert known == (9_000, True)
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_public_async_api_uses_async_service(monkeypatch) -> None:
    transport = _SequenceTransport([_models_response(11_000)])
    service = AsyncModelContextService(transport=transport)
    monkeypatch.setattr(model_context, "_async_context_service", service)

    value = await model_context.get_context_length_async(ENDPOINT, MODEL)
    known = await model_context.get_context_length_known_async(ENDPOINT, MODEL)
    snapshot = await model_context.get_context_snapshot_async(ENDPOINT, MODEL)

    assert value == 11_000
    assert known == (11_000, True)
    assert snapshot.context_length == 11_000
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_registry_and_result_diagnostics_are_content_free() -> None:
    secret_endpoint = "https://secret-tenant.example.test/v1"
    secret_model = "secret-private-model"
    transport = _SequenceTransport(
        [_models_response(7_000, model=secret_model)]
    )
    service = AsyncModelContextService(transport=transport)

    result = await service.get_snapshot(secret_endpoint, secret_model)
    registry = await service.registry_snapshot()
    diagnostic = json.dumps(
        {"result": result.audit_dict(), "registry": registry}, sort_keys=True
    )

    assert registry["schema"] == ASYNC_CONTEXT_REGISTRY_SCHEMA
    assert secret_endpoint not in diagnostic
    assert "secret-tenant" not in diagnostic
    assert secret_model not in diagnostic
    assert registry["entry_count"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fresh_ttl_seconds": 0},
        {"stale_ttl_seconds": -1},
        {"negative_ttl_seconds": float("nan")},
        {"max_entries": 0},
        {"request_timeout_seconds": True},
    ],
)
def test_service_configuration_fails_closed(kwargs) -> None:
    with pytest.raises(ValueError):
        AsyncModelContextService(**kwargs)
