from __future__ import annotations

import asyncio
import json
import threading

import pytest

from src import llm_core
from src import maintenance_llm_runtime
from src.llm_sync_call import llm_call_impl
from src.local_model_scheduler import (
    LocalModelAdmissionRegistry,
    local_model_admission_registry_snapshot,
    local_model_sync_slot,
    read_local_model_foreground_marker,
    reset_local_model_gate_for_tests,
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
from src.transcription_local_model import Gemma3LocalReviewTransport


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


class _SerialProbeStreamResponse:
    status_code = 200

    def __init__(self, *, native: bool, hold: asyncio.Event | None = None) -> None:
        self.native = native
        self.hold = hold

    async def aread(self):
        return b""

    async def aiter_lines(self):
        if self.native:
            yield '{"message":{"content":"ok"},"done":false}'
        else:
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
        if self.hold is not None:
            await self.hold.wait()
        if self.native:
            yield '{"message":{},"done":true}'
        else:
            yield "data: [DONE]"


class _SerialProbeStreamContext:
    def __init__(self, client, *, native: bool) -> None:
        self.client = client
        self.native = native

    async def __aenter__(self):
        self.client.active += 1
        self.client.max_active = max(self.client.max_active, self.client.active)
        await asyncio.sleep(0.03)
        return _SerialProbeStreamResponse(native=self.native, hold=self.client.hold)

    async def __aexit__(self, *_args):
        self.client.active -= 1
        return False


class _SerialProbeStreamClient:
    def __init__(self, *, hold: asyncio.Event | None = None) -> None:
        self.active = 0
        self.max_active = 0
        self.hold = hold

    def stream(self, _method, url, **_kwargs):
        return _SerialProbeStreamContext(
            self,
            native="/api/" in str(url),
        )


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


@pytest.mark.asyncio
async def test_native_and_v1_local_streams_share_one_lane_until_eof(monkeypatch) -> None:
    reset_local_model_gate_for_tests()
    client = _SerialProbeStreamClient()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_args, **_kwargs: None)

    async def consume(url: str) -> list[str]:
        return [
            chunk
            async for chunk in llm_core._stream_llm_impl(
                url,
                "foreground-model",
                [{"role": "user", "content": "local request"}],
                prompt_type="coding_task",
            )
        ]

    native, compatible = await asyncio.gather(
        consume("http://127.0.0.1:11434/api"),
        consume("http://127.0.0.1:11434/v1/chat/completions"),
    )
    snapshot = local_model_admission_registry_snapshot()

    assert native and compatible
    assert client.max_active == 1
    assert snapshot["active_lease_count"] == 0
    assert snapshot["waiting_lease_count"] == 0


@pytest.mark.asyncio
async def test_local_stream_aclose_releases_lane_before_upstream_eof(monkeypatch) -> None:
    reset_local_model_gate_for_tests()
    hold = asyncio.Event()
    client = _SerialProbeStreamClient(hold=hold)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_args, **_kwargs: None)

    stream = llm_core._stream_llm_impl(
        "http://127.0.0.1:11434/api",
        "foreground-model",
        [{"role": "user", "content": "local request"}],
        prompt_type="coding_task",
    )
    first = await stream.__anext__()
    assert first
    assert client.active == 1
    assert local_model_admission_registry_snapshot()["active_lease_count"] == 1

    await stream.aclose()

    assert client.active == 0
    snapshot = local_model_admission_registry_snapshot()
    assert snapshot["active_lease_count"] == 0
    assert snapshot["waiting_lease_count"] == 0


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


def test_untyped_generic_local_uses_foreground_lane_while_cloud_bypasses() -> None:
    registry = LocalModelAdmissionRegistry()

    with local_model_sync_slot(
        ENDPOINT,
        "gemma3:4b",
        provider="ollama",
        registry=registry,
    ) as local_lease:
        assert local_lease is not None
        assert local_lease.kind == "foreground"
    with local_model_sync_slot(
        "https://api.example.test/v1/chat/completions",
        "cloud-model",
        provider="openai",
        role=MaintenanceModelRole.MAINTENANCE,
        registry=registry,
    ) as cloud_lease:
        assert cloud_lease is None

    snapshot = registry.snapshot()
    assert snapshot["entry_count"] == 1
    assert snapshot["active_lease_count"] == 0


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


def test_actual_gemma_reviewer_and_generic_local_call_serialize_on_default_broker(
    monkeypatch,
) -> None:
    reset_local_model_gate_for_tests()
    review_started = threading.Event()
    release_review = threading.Event()
    generic_started = threading.Event()
    failures: list[BaseException] = []
    review_results: list[str] = []
    generic_results: list[str] = []

    def maintenance_attempt(_upstream):
        review_started.set()
        assert release_review.wait(timeout=2)
        return MaintenanceLLMUpstreamResponse(200, {"message": {"content": "{}"}})

    monkeypatch.setattr(
        maintenance_llm_runtime,
        "_default_sync_attempt",
        maintenance_attempt,
    )
    transport = Gemma3LocalReviewTransport(ENDPOINT, _profile())

    class _GenericResponse:
        is_success = True

        @staticmethod
        def json():
            return {"message": {"content": "generic local response"}}

    def generic_post(*_args, **_kwargs):
        generic_started.set()
        return _GenericResponse()

    def run_reviewer() -> None:
        try:
            review_results.append(transport.review("{}"))
        except BaseException as exc:
            failures.append(exc)

    def run_generic() -> None:
        try:
            generic_results.append(
                llm_call_impl(
                    ENDPOINT,
                    "gemma3:4b",
                    [{"role": "user", "content": "generic local request"}],
                    temperature=0.0,
                    max_tokens=16,
                    headers=None,
                    timeout=1,
                    http_exception_cls=RuntimeError,
                    logger=object(),
                    provider_headers_func=lambda _provider: {},
                    detect_provider_func=lambda _url: "ollama",
                    sanitize_messages_func=lambda messages: messages,
                    visible_reasoning_guard_func=lambda messages, _model: messages,
                    get_cache_key_func=lambda *_args: "generic-local-test",
                    get_cached_response_func=lambda _key: None,
                    set_cached_response_func=lambda _key, _value: None,
                    normalize_anthropic_url_func=lambda url: url,
                    build_anthropic_headers_func=lambda _headers: {},
                    build_anthropic_payload_func=lambda *_args, **_kwargs: {},
                    normalize_ollama_url_func=lambda _url: f"{ENDPOINT}/api/chat",
                    build_ollama_payload_func=lambda *_args, **_kwargs: {},
                    get_context_length_func=lambda _url, _model: 0,
                    omit_temperature_func=lambda _provider, _model: False,
                    uses_max_completion_tokens_func=lambda _model: False,
                    supports_thinking_func=lambda _model: False,
                    mistral_reasoning_effort="",
                    note_model_activity_func=lambda _url, _model: None,
                    httpx_post_func=generic_post,
                    parse_anthropic_response_func=lambda _payload: "",
                    parse_ollama_response_func=lambda payload: payload["message"]["content"],
                    normalize_mistral_content_func=lambda _content: ("", ""),
                    parse_openai_message_func=lambda *_args, **_kwargs: "",
                )
            )
        except BaseException as exc:
            failures.append(exc)

    reviewer_thread = threading.Thread(target=run_reviewer)
    generic_thread = threading.Thread(target=run_generic)
    reviewer_thread.start()
    assert review_started.wait(timeout=2)
    generic_thread.start()
    try:
        assert not generic_started.wait(timeout=0.1)
    finally:
        release_review.set()
        reviewer_thread.join(timeout=2)
        generic_thread.join(timeout=2)

    assert not reviewer_thread.is_alive()
    assert not generic_thread.is_alive()
    assert failures == []
    assert review_results == ["{}"]
    assert generic_results == ["generic local response"]
    snapshot = local_model_admission_registry_snapshot()
    assert snapshot["active_lease_count"] == 0
    assert snapshot["waiting_lease_count"] == 0
