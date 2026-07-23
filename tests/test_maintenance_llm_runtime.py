from __future__ import annotations

import asyncio
import json

import pytest

from src import llm_core
from src.local_model_scheduler import (
    LocalModelAdmissionRegistry,
    local_model_admission_registry_snapshot,
    local_model_sync_slot,
    read_local_model_foreground_marker,
)
from src.maintenance_llm_runtime import (
    MAINTENANCE_LLM_REQUEST_SCHEMA,
    MaintenanceLLMAdmissionError,
    MaintenanceLLMCallError,
    MaintenanceLLMContractError,
    MaintenanceLLMDisabledError,
    MaintenanceLLMMessage,
    MaintenanceLLMRequest,
    MaintenanceLLMUpstreamResponse,
    call_maintenance_llm,
    call_maintenance_llm_async,
)
from src.maintenance_model_policy import (
    MaintenanceModelProfile,
    MaintenanceModelRole,
)


ENDPOINT = "http://127.0.0.1:11434"
SECRET_INPUT = "private packet 42"
SECRET_OUTPUT = "private abstraction 84"


@pytest.fixture(autouse=True)
def _isolated_scheduler_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    monkeypatch.setenv(
        "ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER",
        str(tmp_path / "maintenance-marker.json"),
    )


def _profile(*, runtime_enabled: bool = True, **overrides) -> MaintenanceModelProfile:
    return MaintenanceModelProfile.create(runtime_enabled=runtime_enabled, **overrides)


def _request(**overrides) -> MaintenanceLLMRequest:
    values = {
        "endpoint": ENDPOINT,
        "messages": (MaintenanceLLMMessage("user", SECRET_INPUT),),
        "profile": _profile(),
    }
    values.update(overrides)
    return MaintenanceLLMRequest(**values)


class _RecordingRegistry(LocalModelAdmissionRegistry):
    def __init__(self) -> None:
        super().__init__(max_entries=4, idle_ttl_seconds=60.0)
        self.acquired_active_counts: list[int] = []
        self.released_active_counts: list[int] = []

    def acquire(self, **kwargs):
        handle = super().acquire(**kwargs)
        self.acquired_active_counts.append(int(self.snapshot()["active_lease_count"]))
        return handle

    def release(self, handle) -> None:
        super().release(handle)
        self.released_active_counts.append(int(self.snapshot()["active_lease_count"]))


class _ParallelStreamResponse:
    status_code = 200

    async def aread(self):
        return b""

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
        yield "data: [DONE]"


class _ParallelStreamContext:
    def __init__(self, client) -> None:
        self.client = client

    async def __aenter__(self):
        self.client.active += 1
        self.client.max_active = max(self.client.max_active, self.client.active)
        if self.client.active == 2:
            self.client.both_active.set()
        await asyncio.wait_for(self.client.both_active.wait(), timeout=1.0)
        return _ParallelStreamResponse()

    async def __aexit__(self, *_args):
        self.client.active -= 1
        return False


class _ParallelStreamClient:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.both_active = asyncio.Event()

    def stream(self, *_args, **_kwargs):
        return _ParallelStreamContext(self)


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.01)


def test_default_off_rejects_before_admission_or_transport() -> None:
    registry = LocalModelAdmissionRegistry()
    invoked = False

    def fake_attempt(_upstream):
        nonlocal invoked
        invoked = True
        raise AssertionError("transport must not run")

    request = _request(profile=_profile(runtime_enabled=False))

    with pytest.raises(MaintenanceLLMDisabledError):
        call_maintenance_llm(request, attempt=fake_attempt, registry=registry)

    assert invoked is False
    assert registry.snapshot()["entry_count"] == 0


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"endpoint": "https://example.com/api/chat"}, "local Ollama"),
        ({"endpoint": "http://user:pass@127.0.0.1:11434"}, "credentials"),
        ({"endpoint": "http://127.0.0.1:11434/v1/chat/completions"}, "Ollama API"),
        ({"role": "maintenance"}, "eligibility rejected"),
        ({"fallback_requested": True}, "eligibility rejected"),
        ({"truth_write_requested": True}, "eligibility rejected"),
        ({"max_tokens": 1201}, "max_tokens"),
        ({"timeout_ms": 45_001}, "timeout_ms"),
        ({"max_attempts": 4}, "max_attempts"),
        ({"messages": ({"role": "user", "content": "raw"},)}, "MaintenanceLLMMessage"),
        ({"schema": "unknown"}, "unsupported"),
    ],
)
def test_request_contract_fails_closed(overrides, match: str) -> None:
    with pytest.raises(MaintenanceLLMContractError, match=match):
        _request(**overrides)


def test_profile_provider_and_input_budget_fail_closed() -> None:
    foreign_profile = _profile(provider="cloud_ollama")
    with pytest.raises(MaintenanceLLMContractError, match="provider_mismatch"):
        _request(profile=foreign_profile)

    oversized = "x" * 6001
    with pytest.raises(MaintenanceLLMContractError, match="character budget"):
        _request(messages=(MaintenanceLLMMessage("user", oversized),))


@pytest.mark.parametrize("stream", [True, 1, "false"])
def test_streaming_is_rejected_before_admission_or_transport(stream) -> None:
    registry = LocalModelAdmissionRegistry()

    with pytest.raises(MaintenanceLLMContractError, match="stream"):
        _request(stream=stream)

    assert registry.snapshot()["entry_count"] == 0


def test_sync_success_uses_one_exact_lease_and_bounded_attempt() -> None:
    registry = _RecordingRegistry()
    observed = {}

    def fake_attempt(upstream):
        observed.update(
            number=upstream.number,
            target_url=upstream.target_url,
            timeout_seconds=upstream.timeout_seconds,
            model=upstream.payload["model"],
            stream=upstream.payload["stream"],
            active=registry.snapshot()["active_lease_count"],
        )
        return MaintenanceLLMUpstreamResponse(
            200, {"message": {"content": SECRET_OUTPUT}}
        )

    result = call_maintenance_llm(_request(), attempt=fake_attempt, registry=registry)

    assert result.text == SECRET_OUTPUT
    assert result.attempts == 1
    assert observed == {
        "number": 1,
        "target_url": "http://127.0.0.1:11434/api/chat",
        "timeout_seconds": 45.0,
        "model": "gemma3:4b",
        "stream": False,
        "active": 1,
    }
    assert registry.acquired_active_counts == [1]
    assert registry.released_active_counts == [0]
    assert registry.snapshot()["active_lease_count"] == 0


def test_sync_retry_releases_and_reacquires_per_upstream_attempt() -> None:
    registry = _RecordingRegistry()
    responses = [
        MaintenanceLLMUpstreamResponse(503, {"error": SECRET_OUTPUT}),
        MaintenanceLLMUpstreamResponse(200, {"message": {"content": "ok"}}),
    ]
    attempt_numbers: list[int] = []

    def fake_attempt(upstream):
        attempt_numbers.append(upstream.number)
        assert registry.snapshot()["active_lease_count"] == 1
        return responses.pop(0)

    result = call_maintenance_llm(
        _request(max_attempts=2), attempt=fake_attempt, registry=registry
    )

    assert result.text == "ok"
    assert result.attempts == 2
    assert attempt_numbers == [1, 2]
    assert registry.acquired_active_counts == [1, 1]
    assert registry.released_active_counts == [0, 0]


def test_sync_exception_releases_lease_and_redacts_exception_text() -> None:
    registry = _RecordingRegistry()

    def fake_attempt(_upstream):
        raise RuntimeError(f"transport included {SECRET_INPUT}")

    with pytest.raises(MaintenanceLLMCallError) as captured:
        call_maintenance_llm(_request(), attempt=fake_attempt, registry=registry)

    assert captured.value.reason == "transport_exception"
    assert SECRET_INPUT not in str(captured.value)
    assert SECRET_INPUT not in json.dumps(captured.value.audit_dict())
    assert registry.released_active_counts == [0]


@pytest.mark.asyncio
async def test_async_retry_has_one_lease_and_timeout_per_attempt() -> None:
    registry = _RecordingRegistry()
    attempt_numbers: list[int] = []

    async def fake_attempt(upstream):
        attempt_numbers.append(upstream.number)
        assert upstream.timeout_seconds == 0.2
        assert registry.snapshot()["active_lease_count"] == 1
        if upstream.number == 1:
            return MaintenanceLLMUpstreamResponse(429, None)
        return MaintenanceLLMUpstreamResponse(200, {"message": {"content": "done"}})

    result = await call_maintenance_llm_async(
        _request(timeout_ms=200, max_attempts=2),
        attempt=fake_attempt,
        registry=registry,
    )

    assert result.text == "done"
    assert result.attempts == 2
    assert attempt_numbers == [1, 2]
    assert registry.acquired_active_counts == [1, 1]
    assert registry.released_active_counts == [0, 0]


@pytest.mark.asyncio
async def test_async_timeout_is_per_attempt_and_releases_every_lease() -> None:
    registry = _RecordingRegistry()
    invoked = 0

    async def slow_attempt(_upstream):
        nonlocal invoked
        invoked += 1
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    with pytest.raises(MaintenanceLLMCallError) as captured:
        await call_maintenance_llm_async(
            _request(timeout_ms=10, max_attempts=2),
            attempt=slow_attempt,
            registry=registry,
        )

    assert invoked == 2
    assert captured.value.reason == "timeout"
    assert captured.value.attempts == 2
    assert registry.acquired_active_counts == [1, 1]
    assert registry.released_active_counts == [0, 0]


@pytest.mark.asyncio
async def test_async_queue_wait_cancellation_releases_reservation_and_marker() -> None:
    registry = LocalModelAdmissionRegistry()
    holder = registry.acquire(url=ENDPOINT, model="gemma3:4b")
    invoked = False

    async def fake_attempt(_upstream):
        nonlocal invoked
        invoked = True
        return MaintenanceLLMUpstreamResponse(200, {"message": {"content": "no"}})

    task = asyncio.create_task(
        call_maintenance_llm_async(_request(), attempt=fake_attempt, registry=registry)
    )
    await _wait_until(lambda: registry.snapshot()["waiting_lease_count"] == 1)
    assert read_local_model_foreground_marker() is not None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert invoked is False
    assert registry.snapshot()["waiting_lease_count"] == 0
    assert registry.snapshot()["active_lease_count"] == 1
    registry.release(holder)
    assert registry.snapshot()["active_lease_count"] == 0
    assert read_local_model_foreground_marker() is None


@pytest.mark.asyncio
async def test_async_active_call_cancellation_releases_lease_and_marker() -> None:
    registry = LocalModelAdmissionRegistry()
    started = asyncio.Event()

    async def blocked_attempt(_upstream):
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(
        call_maintenance_llm_async(_request(), attempt=blocked_attempt, registry=registry)
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert registry.snapshot()["active_lease_count"] == 1
    assert read_local_model_foreground_marker() is not None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert registry.snapshot()["active_lease_count"] == 0
    assert registry.snapshot()["waiting_lease_count"] == 0
    assert read_local_model_foreground_marker() is None


@pytest.mark.asyncio
async def test_async_exception_is_content_free_and_releases_lease() -> None:
    registry = LocalModelAdmissionRegistry()

    async def failing_attempt(_upstream):
        raise RuntimeError(f"async transport leaked {SECRET_INPUT}")

    with pytest.raises(MaintenanceLLMCallError) as captured:
        await call_maintenance_llm_async(
            _request(), attempt=failing_attempt, registry=registry
        )

    assert captured.value.reason == "transport_exception"
    assert SECRET_INPUT not in str(captured.value)
    assert registry.snapshot()["active_lease_count"] == 0
    assert registry.snapshot()["waiting_lease_count"] == 0
    assert read_local_model_foreground_marker() is None


@pytest.mark.asyncio
async def test_parallel_generic_agent_cloud_streams_do_not_touch_gemma_lane(monkeypatch) -> None:
    client = _ParallelStreamClient()
    before = local_model_admission_registry_snapshot()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_core, "_record_ai_activity_safe", lambda *_args, **_kwargs: None)

    async def consume(model: str) -> list[str]:
        return [
            chunk
            async for chunk in llm_core.stream_llm(
                "https://api.example.test/v1/chat/completions",
                model,
                [{"role": "user", "content": "generic agent request"}],
                surface="coding_task",
            )
        ]

    left, right = await asyncio.gather(consume("cloud-a"), consume("cloud-b"))
    after = local_model_admission_registry_snapshot()

    assert left and right
    assert client.max_active == 2
    assert after == before
    assert after["active_lease_count"] == 0
    assert after["waiting_lease_count"] == 0
    assert read_local_model_foreground_marker() is None


def test_runtime_refuses_when_scheduler_lane_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "0")
    registry = LocalModelAdmissionRegistry()
    invoked = False

    def fake_attempt(_upstream):
        nonlocal invoked
        invoked = True
        return MaintenanceLLMUpstreamResponse(200, {"message": {"content": "no"}})

    with pytest.raises(MaintenanceLLMAdmissionError):
        call_maintenance_llm(_request(), attempt=fake_attempt, registry=registry)

    assert invoked is False
    assert registry.snapshot()["entry_count"] == 0


def test_untyped_generic_local_and_cloud_calls_never_allocate_lane() -> None:
    registry = LocalModelAdmissionRegistry()

    with local_model_sync_slot(
        ENDPOINT,
        "gemma3:4b",
        provider="ollama",
        registry=registry,
    ) as local_lease:
        assert local_lease is None
    with local_model_sync_slot(
        "https://api.example.test/v1/chat/completions",
        "cloud-model",
        provider="openai",
        role=MaintenanceModelRole.MAINTENANCE,
        registry=registry,
    ) as cloud_lease:
        assert cloud_lease is None

    assert registry.snapshot()["entry_count"] == 0


def test_request_result_and_error_diagnostics_are_content_free() -> None:
    request = _request()

    def success(_upstream):
        return MaintenanceLLMUpstreamResponse(
            200, {"message": {"content": SECRET_OUTPUT}}
        )

    result = call_maintenance_llm(
        request,
        attempt=success,
        registry=LocalModelAdmissionRegistry(),
    )
    diagnostic = json.dumps(
        {"request": request.audit_dict(), "result": result.audit_dict()}, sort_keys=True
    )

    assert request.audit_dict()["schema"] == MAINTENANCE_LLM_REQUEST_SCHEMA
    assert SECRET_INPUT not in diagnostic
    assert SECRET_OUTPUT not in diagnostic
    assert ENDPOINT not in diagnostic
    assert "fallback_allowed" in diagnostic
    assert "truth_write_allowed" in diagnostic
