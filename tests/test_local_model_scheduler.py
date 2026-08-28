import asyncio
import json
import threading
import time

import httpx
import pytest

from src import llm_core
from src import local_model_scheduler
from src.local_model_scheduler import (
    LocalModelAdmissionError,
    LocalModelAdmissionRegistry,
    LocalModelRequestGate,
    _refresh_foreground_marker,
    canonical_local_model_key,
    classify_local_model_request,
    is_maintenance_model_eligible,
    is_local_model_foreground_active,
    is_local_model_maintenance_busy,
    local_model_async_slot,
    local_model_admission_registry_snapshot,
    local_model_sync_slot,
    maintenance_cpu_checkpoint,
    read_local_model_foreground_marker,
    reset_local_model_gate_for_tests,
    should_gate_local_model,
    wait_for_local_model_foreground_clear,
)
from src.maintenance_model_policy import MaintenanceModelRole


def test_local_model_gate_only_targets_local_ollama():
    assert should_gate_local_model("http://ollama:11434/api/chat", provider="ollama")
    assert should_gate_local_model("http://ollama:11434/api/chat", provider="local_ollama")
    assert should_gate_local_model("http://localhost:11434/api", provider="ollama")
    assert should_gate_local_model(
        "http://localhost:11434/v1/chat/completions",
        provider="openai",
    )
    assert not should_gate_local_model("http://localhost:11434/api")
    assert not should_gate_local_model("https://ollama.com/api", provider="ollama")
    assert not should_gate_local_model("https://api.openai.com/v1/chat/completions", provider="openai")


def test_clarification_local_model_requests_are_foreground_even_with_memory_terms():
    assert (
        classify_local_model_request(
            surface="clarification",
            prompt_type="memory_maintenance_boundary_question_batch",
        )
        == "foreground"
    )
    assert classify_local_model_request(surface="memory", prompt_type="memory_consolidate") == "maintenance"


def test_typed_exact_maintenance_request_waits_for_the_gate():
    gate = LocalModelRequestGate(max_concurrency=1)
    blocker = gate.acquire(kind="maintenance", url="http://ollama:11434/api/chat", model="gemma3:4b")
    order: list[str] = []

    async def maintenance() -> None:
        async with local_model_async_slot(
            "http://ollama:11434/api/chat",
            "gemma3:4b",
            provider="ollama",
            role=MaintenanceModelRole.MAINTENANCE,
            gate=gate,
        ):
            order.append("maintenance")

    async def run() -> None:
        maintenance_task = asyncio.create_task(maintenance())
        await asyncio.sleep(0.02)
        assert order == []
        gate.release(blocker)
        await maintenance_task

    asyncio.run(run())

    assert order == ["maintenance"]


def test_generic_local_llm_calls_share_the_foreground_heavy_model_lane():
    reset_local_model_gate_for_tests(max_concurrency=1)
    active = 0
    max_active = 0
    starts: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        starts.append(time.perf_counter())
        await asyncio.sleep(0.04)
        active -= 1
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": "OK"}, "done": True},
        )

    async def call(index: int) -> str:
        transport = httpx.MockTransport(handler)
        return await llm_core.llm_call_async(
            "http://ollama:11434/api",
            "gemma3:4b",
            [{"role": "user", "content": f"Say OK {index}"}],
            temperature=0.0,
            max_tokens=8,
            timeout=20,
            prompt_type=f"document_review_{index}",
            surface="document",
            transport=transport,
        )

    async def run() -> list[str]:
        return await asyncio.gather(call(1), call(2))

    results = asyncio.run(run())

    assert results == ["OK", "OK"]
    assert max_active == 1
    assert len(starts) == 2


def test_ineligible_typed_async_and_sync_requests_fail_closed():
    gate = LocalModelRequestGate(max_concurrency=1)
    blocker = gate.acquire(kind="maintenance", url="http://ollama:11434/api/chat", model="gemma3:4b")

    async def run() -> None:
        with pytest.raises(LocalModelAdmissionError, match="admission rejected"):
            async with local_model_async_slot(
                "http://ollama:11434/api/chat",
                "gemma3:4b",
                provider="ollama",
                role="chat",  # type: ignore[arg-type]
                gate=gate,
            ):
                pytest.fail("invalid typed request acquired the gate")

    try:
        asyncio.run(run())
        with pytest.raises(LocalModelAdmissionError, match="admission rejected"):
            with local_model_sync_slot(
                "http://ollama:11434/api/chat",
                "gemma3:latest",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                gate=gate,
            ):
                pytest.fail("invalid typed request acquired the gate")
    finally:
        gate.release(blocker)


def test_scheduler_eligibility_requires_exact_model_and_typed_role():
    assert is_maintenance_model_eligible(
        "http://ollama:11434/api/chat",
        "gemma3:4b",
        provider="ollama",
        role=MaintenanceModelRole.MAINTENANCE,
    )
    assert not is_maintenance_model_eligible(
        "http://ollama:11434/api/chat",
        "gemma3:4b",
        provider="ollama",
        role=None,
    )


def test_canonical_key_collapses_request_paths_but_keeps_endpoint_identity():
    first = canonical_local_model_key("http://OLLAMA.:11434/api/chat", "gemma3:4b")
    second = canonical_local_model_key("http://ollama:11434/api/generate", "gemma3:4b")
    versioned = canonical_local_model_key("http://ollama:11434/v1/chat/completions", "gemma3:4b")

    assert first == second == versioned == ("http://ollama:11434", "gemma3:4b")


def test_registry_serializes_same_key_and_parallelizes_disjoint_keys():
    async def measure(urls: tuple[str, str]) -> int:
        registry = LocalModelAdmissionRegistry(max_entries=4)
        active = 0
        max_active = 0

        async def worker(url: str) -> None:
            nonlocal active, max_active
            async with local_model_async_slot(
                url,
                "gemma3:4b",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                registry=registry,
            ):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(*(worker(url) for url in urls))
        return max_active

    same_key = asyncio.run(
        measure(("http://ollama:11434/api", "http://ollama:11434/api/chat"))
    )
    different_keys = asyncio.run(
        measure(("http://ollama-a:11434/api", "http://ollama-b:11434/api"))
    )

    assert same_key == 1
    assert different_keys == 2


def test_registry_capacity_and_idle_ttl_evict_without_exposing_keys():
    now = [0.0]
    registry = LocalModelAdmissionRegistry(
        max_entries=2,
        idle_ttl_seconds=10.0,
        clock=lambda: now[0],
    )

    for host in ("ollama-a", "ollama-b"):
        with local_model_sync_slot(
            f"http://{host}:11434/api",
            "gemma3:4b",
            provider="ollama",
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ):
            pass
        now[0] += 1.0

    assert registry.snapshot()["entry_count"] == 2
    with local_model_sync_slot(
        "http://ollama-c:11434/api",
        "gemma3:4b",
        provider="ollama",
        role=MaintenanceModelRole.MAINTENANCE,
        registry=registry,
    ):
        pass
    capacity_snapshot = registry.snapshot()
    assert capacity_snapshot["entry_count"] == 2
    assert capacity_snapshot["evictions_total"] == 1

    now[0] = 20.0
    expired_snapshot = registry.snapshot()
    encoded = json.dumps(expired_snapshot, sort_keys=True)
    assert expired_snapshot["entry_count"] == 0
    assert expired_snapshot["evictions_total"] == 3
    assert "ollama-a" not in encoded
    assert "ollama-b" not in encoded
    assert "ollama-c" not in encoded
    assert "gemma3:4b" not in encoded


def test_sync_and_async_slots_share_one_registry_lease_for_same_key():
    registry = LocalModelAdmissionRegistry(max_entries=2)
    entered: list[str] = []

    async def run() -> None:
        async def async_waiter() -> None:
            async with local_model_async_slot(
                "http://ollama:11434/api/chat",
                "gemma3:4b",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                registry=registry,
            ):
                entered.append("async")

        with local_model_sync_slot(
            "http://ollama:11434/api",
            "gemma3:4b",
            provider="ollama",
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ):
            task = asyncio.create_task(async_waiter())
            await asyncio.sleep(0.02)
            assert entered == []
        await task

    asyncio.run(run())
    assert entered == ["async"]


def test_registry_waits_for_capacity_when_every_existing_key_is_active():
    registry = LocalModelAdmissionRegistry(max_entries=1)
    entered: list[str] = []

    async def run() -> None:
        async def second_key() -> None:
            async with local_model_async_slot(
                "http://ollama-b:11434/api",
                "gemma3:4b",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                registry=registry,
            ):
                entered.append("second")

        with local_model_sync_slot(
            "http://ollama-a:11434/api",
            "gemma3:4b",
            provider="ollama",
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ):
            task = asyncio.create_task(second_key())
            await asyncio.sleep(0.02)
            snapshot = registry.snapshot()
            assert entered == []
            assert snapshot["entry_count"] == 1
            assert snapshot["active_key_count"] == 1
        await task

    asyncio.run(run())
    snapshot = registry.snapshot()
    assert entered == ["second"]
    assert snapshot["entry_count"] == 1
    assert snapshot["evictions_total"] == 1


def test_ineligible_request_fails_closed_without_allocating_a_registry_entry():
    registry = LocalModelAdmissionRegistry(max_entries=2)

    async def run() -> None:
        with pytest.raises(LocalModelAdmissionError, match="admission rejected"):
            async with local_model_async_slot(
                "http://ollama:11434/api",
                "gemma3:latest",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                registry=registry,
            ):
                pytest.fail("invalid typed request acquired the registry")

    asyncio.run(run())
    assert registry.snapshot()["entry_count"] == 0


def test_global_registry_snapshot_is_content_free_and_bounded():
    reset_local_model_gate_for_tests(registry_max_entries=3)
    snapshot = local_model_admission_registry_snapshot()
    encoded = json.dumps(snapshot, sort_keys=True)

    assert snapshot["schema"] == "odysseus.local_model_admission_registry.v1"
    assert snapshot["max_entries"] == 3
    assert snapshot["max_concurrency_per_key"] == 1
    assert snapshot["max_concurrency"] == 1
    assert "url" not in encoded
    assert "endpoint_ref" not in encoded
    assert "model_ref" not in encoded
    assert "gemma3:4b" not in encoded


def test_explicit_maintenance_gate_cannot_expand_per_key_concurrency():
    gate = LocalModelRequestGate(max_concurrency=2)

    async def run() -> None:
        with pytest.raises(ValueError, match="concurrency must be 1"):
            async with local_model_async_slot(
                "http://ollama:11434/api",
                "gemma3:4b",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                gate=gate,
            ):
                pass

    asyncio.run(run())


def test_registry_marker_tracks_active_and_waiting_and_old_reader_works(monkeypatch, tmp_path):
    marker_path = tmp_path / "busy.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    registry = LocalModelAdmissionRegistry(max_entries=2)

    async def run() -> None:
        async def waiter() -> None:
            async with local_model_async_slot(
                "http://ollama:11434/api/chat",
                "gemma3:4b",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                registry=registry,
            ):
                pass

        with local_model_sync_slot(
            "http://ollama:11434/api",
            "gemma3:4b",
            provider="ollama",
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ):
            task = asyncio.create_task(waiter())
            for _ in range(40):
                marker = read_local_model_foreground_marker(path=marker_path)
                if marker and marker.get("waiting_count") == 1:
                    break
                await asyncio.sleep(0.005)
            else:
                pytest.fail("waiting marker was not observed")
            assert marker["activity_scope"] == "maintenance"
            assert marker["model_scope"] == "gemma3_4b"
            assert marker["active_count"] == 1
            assert marker["waiting_count"] == 1
            encoded = json.dumps(marker, sort_keys=True)
            for forbidden in ("url", "endpoint", "owner", "user", "prompt", "content", "source_ref"):
                assert forbidden not in encoded
            assert is_local_model_foreground_active(path=marker_path)
            assert is_local_model_maintenance_busy(path=marker_path)
        await task

    asyncio.run(run())
    assert read_local_model_foreground_marker(path=marker_path) is None


def test_cancelled_queue_wait_releases_reservation_and_marker(monkeypatch, tmp_path):
    marker_path = tmp_path / "cancel.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    registry = LocalModelAdmissionRegistry(max_entries=2)

    async def run() -> None:
        async def waiter() -> None:
            async with local_model_async_slot(
                "http://ollama:11434/api/chat",
                "gemma3:4b",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                registry=registry,
            ):
                pytest.fail("cancelled waiter acquired the gate")

        with local_model_sync_slot(
            "http://ollama:11434/api",
            "gemma3:4b",
            provider="ollama",
            role=MaintenanceModelRole.MAINTENANCE,
            registry=registry,
        ):
            for _cycle in range(10):
                task = asyncio.create_task(waiter())
                for _ in range(40):
                    snapshot = registry.snapshot()
                    if snapshot["waiting_lease_count"] == 1:
                        break
                    await asyncio.sleep(0.005)
                else:
                    pytest.fail("waiter did not enter the registry")
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                snapshot = registry.snapshot()
                assert snapshot["active_lease_count"] == 1
                assert snapshot["waiting_lease_count"] == 0
                marker = read_local_model_foreground_marker(path=marker_path)
                assert marker is not None
                assert marker["waiting_count"] == 0

    asyncio.run(run())
    snapshot = registry.snapshot()
    assert snapshot["active_lease_count"] == 0
    assert snapshot["waiting_lease_count"] == 0
    assert read_local_model_foreground_marker(path=marker_path) is None


def test_exception_inside_slot_releases_lease_and_marker(monkeypatch, tmp_path):
    marker_path = tmp_path / "exception.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    registry = LocalModelAdmissionRegistry(max_entries=2)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            async with local_model_async_slot(
                "http://ollama:11434/api",
                "gemma3:4b",
                provider="ollama",
                role=MaintenanceModelRole.MAINTENANCE,
                registry=registry,
            ):
                raise RuntimeError("synthetic failure")

    asyncio.run(run())
    assert registry.snapshot()["active_lease_count"] == 0
    assert read_local_model_foreground_marker(path=marker_path) is None


def test_reentrant_sync_and_async_slots_fail_closed_without_leaking_a_lease():
    registry = LocalModelAdmissionRegistry(max_entries=2)

    with local_model_sync_slot(
        "http://ollama:11434/api",
        "foreground-model",
        provider="ollama",
        registry=registry,
    ):
        with pytest.raises(LocalModelAdmissionError, match="admission rejected"):
            with local_model_sync_slot(
                "http://ollama:11434/api/chat",
                "foreground-model",
                provider="ollama",
                registry=registry,
            ):
                pytest.fail("reentrant sync slot acquired")

    async def run() -> None:
        async with local_model_async_slot(
            "http://ollama:11434/v1/chat/completions",
            "foreground-model",
            provider="openai",
            registry=registry,
        ):
            with pytest.raises(LocalModelAdmissionError, match="admission rejected"):
                async with local_model_async_slot(
                    "http://ollama:11434/v1/chat/completions",
                    "foreground-model",
                    provider="openai",
                    registry=registry,
                ):
                    pytest.fail("reentrant async slot acquired")
            with pytest.raises(LocalModelAdmissionError, match="admission rejected"):
                await asyncio.to_thread(_nested_sync_local_call, registry)

    asyncio.run(run())
    snapshot = registry.snapshot()
    assert snapshot["active_lease_count"] == 0
    assert snapshot["waiting_lease_count"] == 0


def _nested_sync_local_call(registry: LocalModelAdmissionRegistry) -> None:
    with local_model_sync_slot(
        "http://ollama:11434/api",
        "foreground-model",
        provider="ollama",
        registry=registry,
    ):
        pytest.fail("to_thread reentrant slot acquired")


def test_atomic_marker_writes_remain_valid_under_threads(tmp_path):
    marker_path = tmp_path / "atomic.json"

    def writer(index: int) -> None:
        for _ in range(10):
            _refresh_foreground_marker(
                model="gemma3:4b",
                reason="active" if index % 2 else "waiting",
                path=marker_path,
                activity_scope="maintenance",
                active_count=index % 2,
                waiting_count=(index + 1) % 2,
            )

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    payload = json.loads(marker_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "odysseus.local_model_foreground_marker.v1"
    assert payload["model_scope"] == "gemma3_4b"
    assert list(tmp_path.glob(f".{marker_path.name}.*.tmp")) == []


def test_stale_marker_is_removed_and_foreign_model_does_not_block_or_yield(monkeypatch, tmp_path):
    marker_path = tmp_path / "stale.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    _refresh_foreground_marker(model="gemma3:4b", reason="active", path=marker_path)
    payload = json.loads(marker_path.read_text(encoding="utf-8"))
    payload["expires_at_epoch"] = 1.0
    marker_path.write_text(json.dumps(payload), encoding="utf-8")

    assert read_local_model_foreground_marker(path=marker_path, now=2.0) is None
    assert not marker_path.exists()

    gate = LocalModelRequestGate(max_concurrency=1)
    lease = gate.acquire(kind="foreground", url="http://ollama:11434/api", model="qwen3:4b")
    try:
        wait = wait_for_local_model_foreground_clear(
            path=marker_path,
            timeout_seconds=0.01,
            poll_seconds=0.001,
        )
        checkpoint = maintenance_cpu_checkpoint(
            gate=gate,
            sleep_seconds=0.001,
            max_pause_seconds=0.003,
        )
        assert wait.reason == "clear"
        assert checkpoint.yielded is False
        assert checkpoint.reason == "clear"
    finally:
        gate.release(lease)


def test_cpu_checkpoint_yields_for_registry_gemma_activity(monkeypatch, tmp_path):
    marker_path = tmp_path / "registry.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    registry = LocalModelAdmissionRegistry(max_entries=2)

    with local_model_sync_slot(
        "http://ollama:11434/api",
        "gemma3:4b",
        provider="ollama",
        role=MaintenanceModelRole.MAINTENANCE,
        registry=registry,
    ):
        result = maintenance_cpu_checkpoint(
            registry=registry,
            sleep_seconds=0.001,
            max_pause_seconds=0.003,
        )

    assert result.yielded is True
    assert result.reason == "max_pause_reached"


def test_maintenance_cpu_checkpoint_yields_while_local_model_is_active():
    gate = LocalModelRequestGate(max_concurrency=1)
    lease = gate.acquire(kind="foreground", url="http://ollama:11434/api/chat", model="gemma3:4b")
    try:
        result = maintenance_cpu_checkpoint(gate=gate, sleep_seconds=0.001, max_pause_seconds=0.003)
    finally:
        gate.release(lease)

    assert result.yielded is True
    assert result.sleep_count >= 1
    assert result.reason == "max_pause_reached"


def test_maintenance_cpu_checkpoint_never_sleeps_past_max_pause_between_clock_reads(monkeypatch):
    gate = LocalModelRequestGate(max_concurrency=1)
    lease = gate.acquire(kind="foreground", url="http://ollama:11434/api/chat", model="gemma3:4b")
    clock_values = iter((0.0, 0.5, 1.1, 1.1))
    sleep_calls = []

    monkeypatch.setattr(local_model_scheduler.time, "monotonic", lambda: next(clock_values))

    def controlled_sleep(seconds):
        assert seconds >= 0
        sleep_calls.append(seconds)

    monkeypatch.setattr(local_model_scheduler.time, "sleep", controlled_sleep)
    try:
        result = maintenance_cpu_checkpoint(gate=gate, sleep_seconds=5.0, max_pause_seconds=1.0)
    finally:
        gate.release(lease)

    assert sleep_calls == [0.5]
    assert result.yielded is True
    assert result.sleep_count == 1
    assert result.reason == "max_pause_reached"


def test_foreground_slot_writes_and_clears_process_marker(monkeypatch, tmp_path):
    marker_path = tmp_path / "foreground.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    gate = LocalModelRequestGate(max_concurrency=1)

    lease = gate.acquire(kind="foreground", url="http://ollama:11434/api/chat", model="gemma3:4b")
    try:
        payload = read_local_model_foreground_marker(path=marker_path)
        assert payload is not None
        assert payload["model"] == "gemma3:4b"
        assert is_local_model_foreground_active(path=marker_path)
    finally:
        gate.release(lease)

    assert not is_local_model_foreground_active(path=marker_path)


def test_wait_for_foreground_clear_times_out_on_active_marker(monkeypatch, tmp_path):
    marker_path = tmp_path / "foreground.json"
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_FOREGROUND_MARKER", str(marker_path))
    gate = LocalModelRequestGate(max_concurrency=1)
    lease = gate.acquire(kind="foreground", url="http://ollama:11434/api/chat", model="gemma3:4b")
    try:
        result = wait_for_local_model_foreground_clear(
            path=marker_path,
            timeout_seconds=0.002,
            poll_seconds=0.001,
        )
    finally:
        gate.release(lease)

    assert result.reason == "timeout"
    assert result.slept_seconds >= 0.0
    _refresh_foreground_marker,
