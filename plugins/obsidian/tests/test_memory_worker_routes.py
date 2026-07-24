import asyncio
import os
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv(
    "ODYSSEUS_ROOT",
    os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")),
)
for _path in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from backend import routes
from backend.memory_worker import MemoryWorkerQueueFull
from backend.vault_security import VaultSecurityError


def _request():
    return SimpleNamespace(state=SimpleNamespace(api_token=False))


@pytest.mark.asyncio
async def test_slow_status_route_keeps_100ms_heartbeat_responsive(monkeypatch):
    with tempfile.TemporaryDirectory() as vault_dir:
        monkeypatch.setattr(routes, "get_unlocked_vault_path", lambda _request: vault_dir)
        monkeypatch.setattr(
            routes,
            "memory_status",
            lambda _vault: (time.sleep(0.25), {"ready": True})[1],
        )
        heartbeat = {"ticks": 0}

        async def tick():
            deadline = asyncio.get_running_loop().time() + 0.2
            while asyncio.get_running_loop().time() < deadline:
                heartbeat["ticks"] += 1
                await asyncio.sleep(0.01)

        status, _ = await asyncio.gather(routes.memory_status_route(_request()), tick())
        assert status == {"ready": True}
        assert heartbeat["ticks"] >= 10


@pytest.mark.asyncio
async def test_route_maps_full_queue_to_content_free_503(monkeypatch):
    with tempfile.TemporaryDirectory() as vault_dir:
        monkeypatch.setattr(routes, "get_unlocked_vault_path", lambda _request: vault_dir)

        async def full(*_args, **_kwargs):
            raise MemoryWorkerQueueFull("private queue detail")

        monkeypatch.setattr(routes, "run_memory_work", full)
        with pytest.raises(HTTPException) as exc_info:
            await routes.raptor_status_route(_request())
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Memory worker queue is full"
        assert "private" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_locked_vault_fails_before_worker_submission(monkeypatch):
    def locked(_request):
        raise VaultSecurityError("Vault is locked")

    called = {"worker": False}

    async def worker(*_args, **_kwargs):
        called["worker"] = True

    monkeypatch.setattr(routes, "get_unlocked_vault_path", locked)
    monkeypatch.setattr(routes, "run_memory_work", worker)
    with pytest.raises(VaultSecurityError, match="locked"):
        await routes.query_layer_status_route(_request())
    assert called["worker"] is False


@pytest.mark.asyncio
async def test_query_route_runs_async_pipeline_inside_worker_thread(monkeypatch):
    with tempfile.TemporaryDirectory() as vault_dir:
        monkeypatch.setattr(routes, "get_unlocked_vault_path", lambda _request: vault_dir)
        thread_ids = []

        async def fake_answer(_vault, query, **_kwargs):
            import threading

            thread_ids.append(threading.get_ident())
            await asyncio.sleep(0)
            return {"query": query, "ready": True}

        monkeypatch.setattr(routes, "answer_query_async", fake_answer)
        import threading

        event_loop_thread = threading.get_ident()
        result = await routes.query_layer_route(_request(), "synthetic")
        assert result == {"query": "synthetic", "ready": True}
        assert thread_ids and thread_ids[0] != event_loop_thread
