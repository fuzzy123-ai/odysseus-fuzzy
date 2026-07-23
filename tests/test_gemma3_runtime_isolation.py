from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

from src.gemma4_maintenance_router import (
    get_prompt_capsule,
    plan_gemma4_maintenance_route,
)
from src.local_model_scheduler import (
    LocalModelAdmissionRegistry,
    LocalModelRequestGate,
    local_model_async_slot,
    local_model_sync_slot,
    read_local_model_foreground_marker,
)
from src.maintenance_llm_runtime import (
    MaintenanceLLMContractError,
    MaintenanceLLMMessage,
    MaintenanceLLMRequest,
    MaintenanceLLMUpstreamResponse,
    call_maintenance_llm_async,
)
from src.maintenance_model_policy import (
    DEFAULT_MAINTENANCE_MODEL,
    DEFAULT_MAINTENANCE_PROVIDER,
    MaintenanceModelProfile,
    MaintenanceModelRole,
    MaintenanceWorkload,
)
from src.maintenance_output_validator import (
    MaintenanceOutputStatus,
    call_validated_maintenance_llm_async,
    maintenance_output_schema_instruction,
    validate_maintenance_output,
)
from src.model_context import AsyncModelContextService, ContextProbeHTTPResponse
from src.observability_metrics import (
    maintenance_runtime_metrics_snapshot,
    reset_maintenance_runtime_metrics,
)


ENDPOINT_A = "http://127.0.0.1:11434"
ENDPOINT_B = "http://127.0.0.1:11435"
CONTEXT_ENDPOINT = "https://context.example.test/v1/chat/completions"
MODEL = DEFAULT_MAINTENANCE_MODEL
SOURCE_HASH = "sha256:" + "a" * 64
SECRET = "private-runtime-isolation-secret-409d"


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_maintenance_runtime_metrics()
    yield
    reset_maintenance_runtime_metrics()


def _profile() -> MaintenanceModelProfile:
    return MaintenanceModelProfile.create(runtime_enabled=True)


def _request(endpoint: str = ENDPOINT_A) -> MaintenanceLLMRequest:
    return MaintenanceLLMRequest(
        endpoint=endpoint,
        messages=(
            MaintenanceLLMMessage("system", "isolated maintenance fixture"),
            MaintenanceLLMMessage("user", "bounded synthetic fixture"),
        ),
        profile=_profile(),
        role=MaintenanceModelRole.MAINTENANCE,
        max_attempts=1,
        stream=False,
        fallback_requested=False,
        truth_write_requested=False,
    )


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


@pytest.mark.asyncio
async def test_parallelism_matrix_serializes_same_key_but_not_disjoint_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    registry = LocalModelAdmissionRegistry()
    active = 0
    max_active = 0
    latencies: list[float] = []

    async def serial_attempt(_upstream):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.002)
        active -= 1
        return MaintenanceLLMUpstreamResponse(
            200,
            {"message": {"content": "{}"}},
        )

    async def one_serial_call() -> None:
        started = time.perf_counter()
        await call_maintenance_llm_async(
            _request(),
            attempt=serial_attempt,
            registry=registry,
        )
        latencies.append(time.perf_counter() - started)

    await asyncio.gather(*(one_serial_call() for _ in range(20)))

    assert max_active == 1
    assert _p95(latencies) < 30.0
    assert max(latencies) < 45.0
    assert registry.snapshot()["active_lease_count"] == 0
    assert registry.snapshot()["waiting_lease_count"] == 0

    active = 0
    max_active = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def parallel_attempt(_upstream):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            both_started.set()
        await release.wait()
        active -= 1
        return MaintenanceLLMUpstreamResponse(
            200,
            {"message": {"content": "{}"}},
        )

    tasks = [
        asyncio.create_task(
            call_maintenance_llm_async(
                _request(endpoint),
                attempt=parallel_attempt,
                registry=registry,
            )
        )
        for endpoint in (ENDPOINT_A, ENDPOINT_B)
    ]
    await asyncio.wait_for(both_started.wait(), timeout=1.0)
    release.set()
    await asyncio.gather(*tasks)

    assert max_active == 2
    assert registry.snapshot()["active_lease_count"] == 0


@pytest.mark.asyncio
async def test_cloud_and_foreign_models_bypass_without_shared_serialization(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    registry = LocalModelAdmissionRegistry()
    active = 0
    max_active = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def bypassed(url: str, model: str, provider: str) -> None:
        nonlocal active, max_active
        async with local_model_async_slot(
            url,
            model,
            provider=provider,
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ) as lease:
            assert lease is None
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_started.set()
            await release.wait()
            active -= 1

    tasks = [
        asyncio.create_task(
            bypassed(ENDPOINT_A, "foreign-local-model", DEFAULT_MAINTENANCE_PROVIDER)
        ),
        asyncio.create_task(
            bypassed("https://api.example.test/v1", "cloud-model", "openai")
        ),
    ]
    await asyncio.wait_for(both_started.wait(), timeout=1.0)
    release.set()
    await asyncio.gather(*tasks)

    assert max_active == 2
    assert registry.snapshot()["entry_count"] == 0


@pytest.mark.asyncio
async def test_exact_100_way_context_singleflight_keeps_heartbeat_below_100ms() -> None:
    calls = 0
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    async def slow_transport(_url: str, _timeout: float) -> ContextProbeHTTPResponse:
        nonlocal calls
        calls += 1
        probe_started.set()
        await release_probe.wait()
        return ContextProbeHTTPResponse(
            200,
            {"data": [{"id": MODEL, "context_length": 16_384}]},
        )

    service = AsyncModelContextService(transport=slow_transport)
    heartbeat_times: list[float] = []
    done = asyncio.Event()

    async def heartbeat() -> None:
        while not done.is_set():
            heartbeat_times.append(time.perf_counter())
            await asyncio.sleep(0.005)

    lookup_tasks = [
        asyncio.create_task(service.get_snapshot(CONTEXT_ENDPOINT, MODEL))
        for _ in range(100)
    ]
    await asyncio.wait_for(probe_started.wait(), timeout=1.0)
    for _ in range(500):
        joining = await service.registry_snapshot()
        if joining["singleflight_joins_total"] == 99:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("all 99 same-key callers did not join the single probe")
    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.15)
    release_probe.set()
    results = await asyncio.gather(*lookup_tasks)
    done.set()
    await heartbeat_task
    gaps = [
        right - left
        for left, right in zip(heartbeat_times, heartbeat_times[1:])
    ]
    snapshot = await service.registry_snapshot()

    assert calls == 1
    assert snapshot["singleflight_joins_total"] == 99
    assert {result.context_length for result in results} == {16_384}
    assert len(heartbeat_times) >= 10
    assert max(gaps, default=0.0) < 0.1


@pytest.mark.asyncio
async def test_queue_runtime_and_probe_cancellation_leave_no_leaks(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    marker_path = tmp_path / "maintenance-marker.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    registry = LocalModelAdmissionRegistry()
    holder = registry.acquire(url=ENDPOINT_A, model=MODEL)
    assert read_local_model_foreground_marker() is not None

    async def queued() -> None:
        async with local_model_async_slot(
            ENDPOINT_A,
            MODEL,
            provider=DEFAULT_MAINTENANCE_PROVIDER,
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ):
            pytest.fail("cancelled waiter acquired the lane")

    waiting_task = asyncio.create_task(queued())
    for _ in range(200):
        if registry.snapshot()["waiting_lease_count"] == 1:
            break
        await asyncio.sleep(0.001)
    waiting_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting_task
    registry.release(holder)

    attempt_started = asyncio.Event()

    async def blocked_attempt(_upstream):
        attempt_started.set()
        await asyncio.Future()

    active_task = asyncio.create_task(
        call_maintenance_llm_async(
            _request(),
            attempt=blocked_attempt,
            registry=registry,
        )
    )
    await asyncio.wait_for(attempt_started.wait(), timeout=1.0)
    active_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active_task

    probe_started = asyncio.Event()

    async def blocked_probe(_url: str, _timeout: float) -> ContextProbeHTTPResponse:
        probe_started.set()
        await asyncio.Future()

    service = AsyncModelContextService(transport=blocked_probe)
    caller = asyncio.create_task(service.get_snapshot(ENDPOINT_A, MODEL))
    await asyncio.wait_for(probe_started.wait(), timeout=1.0)
    async with service._condition:
        probe_task = next(iter(service._inflight.values()))
    probe_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await service.wait_for_idle()

    assert registry.snapshot()["active_lease_count"] == 0
    assert registry.snapshot()["waiting_lease_count"] == 0
    assert read_local_model_foreground_marker() is None
    assert (await service.registry_snapshot())["inflight_count"] == 0


@pytest.mark.asyncio
async def test_strict_output_has_exactly_one_repair_attempt_then_review(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    calls = 0

    async def malformed(_upstream):
        nonlocal calls
        calls += 1
        return MaintenanceLLMUpstreamResponse(
            200,
            {"message": {"content": "truncated {"}},
        )

    run = await call_validated_maintenance_llm_async(
        _request(),
        capsule=get_prompt_capsule(MaintenanceWorkload.INBOX_TRIAGE),
        allowed_source_hashes=(SOURCE_HASH,),
        attempt=malformed,
        registry=LocalModelAdmissionRegistry(),
    )

    assert calls == 2
    assert run.call_count == 2
    assert run.retry_count == 1
    assert run.validation.status is MaintenanceOutputStatus.REVIEW
    assert run.validation.review_required is True
    assert run.validation.parsed == {}
    assert run.audit_dict()["truth_write_performed"] is False


def test_warmed_bypass_and_uncontended_admission_p95_meet_slos(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    gate = LocalModelRequestGate(max_concurrency=1)

    def baseline() -> None:
        return None

    def bypass() -> None:
        with local_model_sync_slot(
            "https://api.example.test/v1",
            "cloud-model",
            provider="openai",
            role=MaintenanceModelRole.MAINTENANCE,
            gate=gate,
        ) as lease:
            assert lease is None

    def admit() -> None:
        with local_model_sync_slot(
            ENDPOINT_A,
            MODEL,
            provider=DEFAULT_MAINTENANCE_PROVIDER,
            role=MaintenanceModelRole.MAINTENANCE,
            gate=gate,
        ) as lease:
            assert lease is not None

    for _ in range(20):
        bypass()
        admit()
    reset_maintenance_runtime_metrics()

    baseline_times: list[float] = []
    bypass_times: list[float] = []
    admission_times: list[float] = []
    for _ in range(300):
        started = time.perf_counter()
        baseline()
        baseline_times.append(time.perf_counter() - started)
        started = time.perf_counter()
        bypass()
        bypass_times.append(time.perf_counter() - started)
        started = time.perf_counter()
        admit()
        admission_times.append(time.perf_counter() - started)

    baseline_p95 = _p95(baseline_times)
    bypass_p95 = _p95(bypass_times)
    admission_p95 = _p95(admission_times)
    allowed_added = max(baseline_p95 * 0.02, 0.005)

    assert bypass_p95 < 0.001
    assert bypass_p95 - baseline_p95 <= allowed_added
    assert admission_p95 < 0.005


@pytest.mark.asyncio
async def test_warmed_fresh_context_hit_p95_is_below_one_millisecond() -> None:
    async def transport(_url: str, _timeout: float) -> ContextProbeHTTPResponse:
        return ContextProbeHTTPResponse(
            200,
            {"data": [{"id": MODEL, "context_length": 8_192}]},
        )

    service = AsyncModelContextService(transport=transport)
    await service.get_snapshot(ENDPOINT_A, MODEL)
    reset_maintenance_runtime_metrics()
    durations: list[float] = []
    for _ in range(300):
        started = time.perf_counter()
        snapshot = await service.get_snapshot(ENDPOINT_A, MODEL)
        durations.append(time.perf_counter() - started)
        assert snapshot.cache_status == "fresh_cache"

    assert _p95(durations) < 0.001


def test_prompt_retrieval_caps_and_redacted_report_are_stric() -> None:
    profile = MaintenanceModelProfile.create(runtime_enabled=True)
    capsule = get_prompt_capsule(MaintenanceWorkload.INBOX_TRIAGE)
    plan = plan_gemma4_maintenance_route(
        surface="universal_inbox",
        workload=MaintenanceWorkload.INBOX_TRIAGE,
        source_refs=tuple(f"source:{index}:{SECRET}" for index in range(10)),
        excerpt=SECRET,
        chunk_count=4,
        profile=profile,
    )
    prompt = plan.capsule.build_prompt(
        metadata={"surface": "universal_inbox"},
        excerpt=SECRET,
    ) + "\n" + maintenance_output_schema_instruction(
        plan.capsule,
        allowed_source_hashes=plan.source_hashes,
    )
    request = MaintenanceLLMRequest(
        endpoint=ENDPOINT_A,
        messages=(MaintenanceLLMMessage("user", prompt),),
        profile=profile,
        max_tokens=profile.token_budget,
    )

    assert len(prompt) <= profile.max_input_chars
    assert len(prompt) <= 6_144
    assert request.max_tokens <= 1_200
    assert plan.chunk_count == 4
    assert len(plan.source_hashes) == 4
    assert plan.queue_policy.decide(input_chars=plan.input_chars, chunk_count=4)["status"] == "admit"
    assert plan.queue_policy.decide(input_chars=plan.input_chars, chunk_count=5)["status"] == "prepare_smaller_packet"

    with pytest.raises(MaintenanceLLMContractError, match="character budget"):
        MaintenanceLLMRequest(
            endpoint=ENDPOINT_A,
            messages=(MaintenanceLLMMessage("user", "x" * (profile.max_input_chars + 1)),),
            profile=profile,
        )

    validation = validate_maintenance_output(
        {
            "status": "ready",
            "classification": "private",
            "document_type": "reference",
            "confidence": 0.9,
            "review_reason": "",
            "provenance": {"source_hash": SOURCE_HASH},
            "raw_text": SECRET,
        },
        capsule=capsule,
        allowed_source_hashes=(SOURCE_HASH,),
    )
    reset_maintenance_runtime_metrics()
    metrics = maintenance_runtime_metrics_snapshot()
    report = {
        "request": request.audit_dict(),
        "route": plan.to_dict(),
        "validation": validation.audit_dict(),
        "metrics": metrics,
    }
    encoded = json.dumps(report, sort_keys=True)

    assert validation.status is MaintenanceOutputStatus.BLOCKED
    for forbidden in (
        SECRET,
        ENDPOINT_A,
        "raw_text",
        "raw_prompt",
        "raw_output",
        "authorization",
        "api_key",
        "C:\\Users\\private",
    ):
        assert forbidden.lower() not in encoded.lower()
    assert report["request"]["model_scope"] == "gemma3_4b"
    assert report["validation"]["truth_write_authorized"] is False
