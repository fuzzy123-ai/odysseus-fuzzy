from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest

from src.local_model_scheduler import (
    LocalModelAdmissionRegistry,
    local_model_async_slot,
    local_model_sync_slot,
    maintenance_cpu_checkpoint,
)
from src.maintenance_model_policy import (
    DEFAULT_MAINTENANCE_MODEL,
    DEFAULT_MAINTENANCE_PROVIDER,
    MaintenanceModelRole,
)
from src.model_context import (
    AsyncModelContextService,
    ContextProbeHTTPResponse,
)
from src.observability_metrics import (
    GMI_RUNTIME_METRIC_NAMES,
    ObservabilityMetricsError,
    maintenance_runtime_metrics_snapshot,
    record_gmi_runtime_event,
    render_prometheus_text,
    reset_maintenance_runtime_metrics,
)


ENDPOINT = "http://127.0.0.1:11434"
MODEL = DEFAULT_MAINTENANCE_MODEL


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_maintenance_runtime_metrics()
    yield
    reset_maintenance_runtime_metrics()


def _sample(snapshot: dict, name: str, status: str) -> dict:
    matches = [
        sample
        for sample in snapshot["samples"]
        if sample["name"] == name and sample["labels"].get("status") == status
    ]
    assert len(matches) == 1
    return matches[0]


def test_registry_uses_closed_events_statuses_and_fixed_cardinality() -> None:
    record_gmi_runtime_event("admission", status="admitted")
    record_gmi_runtime_event("queue_depth", status="current", value=2)
    record_gmi_runtime_event("queue_wait", status="observed", value=0.025)
    record_gmi_runtime_event("runtime", status="completed", value=0.5)
    record_gmi_runtime_event("context_cache", status="hit")
    record_gmi_runtime_event("context_probe", status="success", value=0.01)
    record_gmi_runtime_event("yield", status="yielded")
    record_gmi_runtime_event("cancellation", status="runtime")

    snapshot = maintenance_runtime_metrics_snapshot()

    assert snapshot["model_scope"] == "gemma3_4b"
    assert snapshot["fixed_metric_names"] == tuple(sorted(GMI_RUNTIME_METRIC_NAMES))
    assert snapshot["allowed_labels"] == (
        "component",
        "model_scope",
        "queue",
        "runtime",
        "status",
    )
    assert _sample(snapshot, "gemma_maintenance_admission_total", "admitted")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_queue_depth", "current")["value"] == 2.0
    assert _sample(snapshot, "gemma_maintenance_queue_wait_seconds", "observed")["count"] == 1
    assert _sample(snapshot, "gemma_maintenance_runtime_seconds", "completed")["sum"] == 0.5

    with pytest.raises(ObservabilityMetricsError, match="unsupported maintenance metric event"):
        record_gmi_runtime_event("private_endpoint", status="ready")
    with pytest.raises(ObservabilityMetricsError, match="unsupported maintenance metric status"):
        record_gmi_runtime_event("admission", status="owner-alice")
    with pytest.raises(ObservabilityMetricsError, match="increment by one"):
        record_gmi_runtime_event("admission", status="admitted", value=2)


def test_registry_counter_updates_are_thread_safe() -> None:
    def record_many() -> None:
        for _ in range(250):
            record_gmi_runtime_event("context_cache", status="hit")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: record_many(), range(8)))

    snapshot = maintenance_runtime_metrics_snapshot()
    sample = _sample(snapshot, "gemma_maintenance_context_cache_total", "hit")
    assert sample["value"] == 2_000.0


def test_sync_scheduler_emits_admission_wait_runtime_depth_and_bypass(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    registry = LocalModelAdmissionRegistry()

    with local_model_sync_slot(
        ENDPOINT,
        MODEL,
        provider=DEFAULT_MAINTENANCE_PROVIDER,
        role=MaintenanceModelRole.MAINTENANCE,
        registry=registry,
    ) as lease:
        assert lease is not None
        time.sleep(0.002)

    with local_model_sync_slot(
        ENDPOINT,
        "foreign-model",
        provider=DEFAULT_MAINTENANCE_PROVIDER,
        role=MaintenanceModelRole.MAINTENANCE,
        registry=registry,
    ) as lease:
        assert lease is None

    snapshot = maintenance_runtime_metrics_snapshot()
    assert _sample(snapshot, "gemma_maintenance_admission_total", "admitted")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_admission_total", "bypassed")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_queue_wait_seconds", "observed")["count"] == 1
    assert _sample(snapshot, "gemma_maintenance_runtime_seconds", "completed")["count"] == 1
    assert _sample(snapshot, "gemma_maintenance_queue_depth", "current")["value"] == 0.0


@pytest.mark.asyncio
async def test_async_scheduler_observes_real_queue_wait(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    registry = LocalModelAdmissionRegistry()
    holder = registry.acquire(url=ENDPOINT, model=MODEL)
    entered = asyncio.Event()

    async def queued() -> None:
        async with local_model_async_slot(
            ENDPOINT,
            MODEL,
            provider=DEFAULT_MAINTENANCE_PROVIDER,
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ) as lease:
            assert lease is not None
            entered.set()

    task = asyncio.create_task(queued())
    for _ in range(200):
        if registry.snapshot()["waiting_lease_count"] == 1:
            break
        await asyncio.sleep(0.001)
    else:
        pytest.fail("maintenance request did not enter the queue")
    await asyncio.sleep(0.025)
    registry.release(holder)
    await asyncio.wait_for(task, timeout=1.0)
    assert entered.is_set()

    snapshot = maintenance_runtime_metrics_snapshot()
    wait = _sample(snapshot, "gemma_maintenance_queue_wait_seconds", "observed")
    assert wait["count"] == 1
    assert wait["sum"] >= 0.02


@pytest.mark.asyncio
async def test_async_queue_and_active_runtime_cancellations_are_counted(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    registry = LocalModelAdmissionRegistry()
    holder = registry.acquire(url=ENDPOINT, model=MODEL)

    async def queued() -> None:
        async with local_model_async_slot(
            ENDPOINT,
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

    active_started = asyncio.Event()

    async def active() -> None:
        async with local_model_async_slot(
            ENDPOINT,
            MODEL,
            provider=DEFAULT_MAINTENANCE_PROVIDER,
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ):
            active_started.set()
            await asyncio.Future()

    active_task = asyncio.create_task(active())
    await asyncio.wait_for(active_started.wait(), timeout=1.0)
    active_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active_task

    snapshot = maintenance_runtime_metrics_snapshot()
    assert _sample(snapshot, "gemma_maintenance_cancellation_total", "queue_wait")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_cancellation_total", "runtime")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_runtime_seconds", "cancelled")["count"] == 1
    assert registry.snapshot()["active_lease_count"] == 0
    assert registry.snapshot()["waiting_lease_count"] == 0


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _models_response(context_length: int = 8_192) -> ContextProbeHTTPResponse:
    return ContextProbeHTTPResponse(
        200,
        {"data": [{"id": MODEL, "context_length": context_length}]},
    )


@pytest.mark.asyncio
async def test_context_service_emits_hit_stale_miss_negative_and_probe_duration() -> None:
    clock = _Clock()

    async def positive_transport(_url: str, _timeout: float) -> ContextProbeHTTPResponse:
        await asyncio.sleep(0)
        return _models_response()

    service = AsyncModelContextService(
        fresh_ttl_seconds=1,
        stale_ttl_seconds=10,
        negative_ttl_seconds=1,
        clock=clock,
        transport=positive_transport,
    )
    await service.get_snapshot(ENDPOINT, MODEL)
    await service.get_snapshot(ENDPOINT, MODEL)
    clock.advance(2)
    stale = await service.get_snapshot(ENDPOINT, MODEL)
    assert stale.cache_status == "stale_cache"
    await service.wait_for_idle()

    async def negative_transport(_url: str, _timeout: float) -> ContextProbeHTTPResponse:
        return ContextProbeHTTPResponse(200, {"data": []})

    negative = AsyncModelContextService(transport=negative_transport)
    await negative.get_snapshot(ENDPOINT, MODEL)
    cached = await negative.get_snapshot(ENDPOINT, MODEL)
    assert cached.cache_status == "negative_cache"

    snapshot = maintenance_runtime_metrics_snapshot()
    assert _sample(snapshot, "gemma_maintenance_context_cache_total", "miss")["value"] == 2.0
    assert _sample(snapshot, "gemma_maintenance_context_cache_total", "hit")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_context_cache_total", "stale")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_context_cache_total", "negative")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_context_probe_seconds", "success")["count"] == 2
    assert _sample(snapshot, "gemma_maintenance_context_probe_seconds", "failure")["count"] == 1


@pytest.mark.asyncio
async def test_context_wait_and_probe_cancellation_are_content_free() -> None:
    started = asyncio.Event()

    async def blocked_transport(_url: str, _timeout: float) -> ContextProbeHTTPResponse:
        started.set()
        await asyncio.Future()

    service = AsyncModelContextService(transport=blocked_transport)
    caller = asyncio.create_task(service.get_snapshot(ENDPOINT, MODEL))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    async with service._condition:
        probe = next(iter(service._inflight.values()))
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await service.wait_for_idle()

    snapshot = maintenance_runtime_metrics_snapshot()
    assert _sample(snapshot, "gemma_maintenance_cancellation_total", "context_probe")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_cancellation_total", "context_wait")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_context_probe_seconds", "cancelled")["count"] == 1


def test_cpu_checkpoint_emits_continued_yielded_and_disabled(monkeypatch) -> None:
    registry = LocalModelAdmissionRegistry()
    continued = maintenance_cpu_checkpoint(registry=registry)
    assert continued.yielded is False

    holder = registry.acquire(url=ENDPOINT, model=MODEL)
    yielded = maintenance_cpu_checkpoint(
        registry=registry,
        sleep_seconds=0.001,
        max_pause_seconds=0.004,
    )
    registry.release(holder)
    assert yielded.yielded is True

    monkeypatch.setenv("ODYSSEUS_MAINTENANCE_CPU_YIELD", "0")
    disabled = maintenance_cpu_checkpoint(registry=registry)
    assert disabled.reason == "disabled"

    snapshot = maintenance_runtime_metrics_snapshot()
    assert _sample(snapshot, "gemma_maintenance_yield_total", "continued")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_yield_total", "yielded")["value"] == 1.0
    assert _sample(snapshot, "gemma_maintenance_yield_total", "disabled")["value"] == 1.0


def test_prometheus_snapshot_is_content_free_and_has_only_fixed_labels() -> None:
    for event, status, value in (
        ("admission", "admitted", 1),
        ("context_cache", "negative", 1),
        ("queue_wait", "observed", 0.1),
        ("context_probe", "failure", 0.2),
        ("cancellation", "context_wait", 1),
    ):
        record_gmi_runtime_event(event, status=status, value=value)
    snapshot = maintenance_runtime_metrics_snapshot()
    text = render_prometheus_text(snapshot)
    encoded = json.dumps(snapshot, sort_keys=True) + text

    assert 'model_scope="gemma3_4b"' in text
    assert set(snapshot["definitions"]) == set(GMI_RUNTIME_METRIC_NAMES)
    for sample in snapshot["samples"]:
        assert set(sample["labels"]).issubset(set(snapshot["allowed_labels"]))
        assert sample["labels"]["model_scope"] == "gemma3_4b"
    for forbidden in (
        ENDPOINT,
        "gemma3:4b",
        "owner-alice",
        "vault-private",
        "raw prompt",
        "private document",
        "source_ref",
    ):
        assert forbidden.lower() not in encoded.lower()
    assert snapshot["raw_content_visible"] is False
    assert snapshot["high_cardinality_labels_allowed"] is False
    assert snapshot["live_scrape_configured"] is False
