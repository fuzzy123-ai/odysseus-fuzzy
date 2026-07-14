import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src import bg_jobs


@pytest.fixture
def isolated_bg_store(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    store = tmp_path / "bg_jobs.json"
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "_STORE", store)
    monkeypatch.setattr(bg_jobs, "_STORE_LOCK", threading.RLock())
    return store, jobs_dir


def _write_store(store, records):
    store.write_text(json.dumps(records), encoding="utf-8")


def test_32_concurrent_synthetic_launches_retain_32_distinct_records(
    isolated_bg_store,
    monkeypatch,
):
    store, _ = isolated_bg_store
    pid_lock = threading.Lock()
    next_pid = 1000

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            nonlocal next_pid
            with pid_lock:
                next_pid += 1
                self.pid = next_pid

    monkeypatch.setattr(bg_jobs, "find_bash", lambda: None)
    monkeypatch.setattr(bg_jobs, "detached_popen_kwargs", lambda: {})
    monkeypatch.setattr(bg_jobs.subprocess, "Popen", FakeProcess)

    with ThreadPoolExecutor(max_workers=16) as pool:
        records = list(
            pool.map(
                lambda index: bg_jobs.launch(f"echo job-{index}", f"session-{index}"),
                range(32),
            )
        )

    persisted = bg_jobs._load()
    assert len(records) == 32
    assert len(persisted) == 32
    assert {rec["id"] for rec in records} == set(persisted)
    assert len({rec["pid"] for rec in records}) == 32
    assert all(rec["revision"] == 1 for rec in persisted.values())
    assert all(rec["followup_id"] == f"bg-followup:{rec['id']}" for rec in persisted.values())
    assert json.loads(store.read_text(encoding="utf-8")) == persisted


def test_refresh_and_followup_completion_do_not_lose_each_others_mutation(
    isolated_bg_store,
    monkeypatch,
):
    store, jobs_dir = isolated_bg_store
    jobs_dir.mkdir()
    exit_path = jobs_dir / "running.exit"
    exit_path.write_text("0", encoding="utf-8")
    _write_store(
        store,
        {
            "running": {
                "id": "running",
                "status": "running",
                "pid": 7,
                "started_at": time.time(),
                "max_runtime_s": 3600,
                "exit_path": str(exit_path),
                "revision": 1,
                "followed_up": False,
            },
            "terminal": {
                "id": "terminal",
                "status": "done",
                "exit_code": 0,
                "ended_at": time.time(),
                "revision": 1,
                "followup_id": "bg-followup:terminal",
                "followed_up": False,
            },
        },
    )
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda pid: True)
    barrier = threading.Barrier(2)

    def do_refresh():
        barrier.wait()
        bg_jobs.refresh()

    def do_complete():
        barrier.wait()
        assert bg_jobs.mark_followed_up("terminal") is True

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(do_refresh), pool.submit(do_complete)]
        for future in futures:
            future.result()

    persisted = bg_jobs._load()
    assert persisted["running"]["status"] == "done"
    assert persisted["running"]["exit_code"] == 0
    assert persisted["running"]["revision"] == 2
    assert persisted["terminal"]["followed_up"] is True
    assert persisted["terminal"]["followup_completed_id"] == "bg-followup:terminal"
    assert persisted["terminal"]["revision"] == 2


def test_two_monitor_attempts_cannot_hold_the_same_unexpired_followup_lease(
    isolated_bg_store,
):
    store, _ = isolated_bg_store
    _write_store(
        store,
        {
            "done": {
                "id": "done",
                "status": "done",
                "ended_at": time.time(),
                "followed_up": False,
                "revision": 1,
            }
        },
    )
    barrier = threading.Barrier(2)

    def lease(owner):
        barrier.wait()
        return bg_jobs.lease_pending_followups(
            lease_owner=owner,
            lease_s=60,
            limit=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [
            pool.submit(lease, "monitor-a"),
            pool.submit(lease, "monitor-b"),
        ]]

    assert sorted(len(result) for result in results) == [0, 1]
    record = next(result[0] for result in results if result)
    token = record["followup_lease"]["token"]
    assert record["followup_id"] == "bg-followup:done"
    assert bg_jobs.mark_followed_up("done", lease_token="wrong") is False
    assert bg_jobs.mark_followed_up("done", lease_token=token) is True
    assert bg_jobs.mark_followed_up("done", lease_token=token) is True
    persisted = bg_jobs._load()["done"]
    assert persisted["followup_completed_id"] == "bg-followup:done"
    assert "followup_lease" not in persisted
    assert persisted["revision"] == 3


def test_failed_attempt_can_release_and_reacquire_same_stable_followup_identity(
    isolated_bg_store,
):
    store, _ = isolated_bg_store
    _write_store(
        store,
        {
            "done": {
                "id": "done",
                "status": "failed",
                "ended_at": time.time(),
                "followed_up": False,
                "revision": 4,
            }
        },
    )

    first = bg_jobs.lease_pending_followups(lease_owner="monitor", limit=1)[0]
    first_token = first["followup_lease"]["token"]
    assert bg_jobs.release_followup_lease("done", "wrong") is False
    assert bg_jobs.release_followup_lease("done", first_token) is True
    second = bg_jobs.lease_pending_followups(lease_owner="monitor", limit=1)[0]

    assert second["followup_id"] == first["followup_id"] == "bg-followup:done"
    assert second["followup_lease"]["token"] != first_token
    assert second["revision"] == 7


def test_expired_lease_can_be_reacquired_but_unexpired_lease_cannot(
    isolated_bg_store,
    monkeypatch,
):
    store, _ = isolated_bg_store
    _write_store(
        store,
        {
            "done": {
                "id": "done",
                "status": "done",
                "ended_at": 10.0,
                "followed_up": False,
                "revision": 1,
            }
        },
    )
    now = 100.0
    monkeypatch.setattr(bg_jobs.time, "time", lambda: now)

    first = bg_jobs.lease_pending_followups(lease_owner="monitor-a", lease_s=30, limit=1)
    assert len(first) == 1
    assert bg_jobs.lease_pending_followups(lease_owner="monitor-b", lease_s=30, limit=1) == []

    now = 131.0
    second = bg_jobs.lease_pending_followups(lease_owner="monitor-b", lease_s=30, limit=1)
    assert len(second) == 1
    assert second[0]["followup_id"] == first[0]["followup_id"]
    assert second[0]["followup_lease"]["token"] != first[0]["followup_lease"]["token"]


def test_refresh_caps_legacy_runtime_at_one_hour_without_real_kill(
    isolated_bg_store,
    monkeypatch,
):
    store, _ = isolated_bg_store
    now = 10_000.0
    _write_store(
        store,
        {
            "legacy": {
                "id": "legacy",
                "status": "running",
                "pid": 55,
                "started_at": now - 3601,
                "max_runtime_s": 86_400,
                "exit_path": "",
                "followed_up": False,
                "revision": 2,
            }
        },
    )
    killed = []
    monkeypatch.setattr(bg_jobs.time, "time", lambda: now)
    monkeypatch.setattr(bg_jobs, "_kill", lambda pid: killed.append(pid))

    record = bg_jobs.refresh()["legacy"]

    assert killed == [55]
    assert record["status"] == "failed"
    assert record["timed_out"] is True
    assert record["exit_code"] == -1
    assert record["revision"] == 3


def test_refresh_prunes_only_completed_followups_after_retention(
    isolated_bg_store,
    monkeypatch,
):
    store, jobs_dir = isolated_bg_store
    jobs_dir.mkdir()
    artifact = jobs_dir / "stale.log"
    artifact.write_text("done", encoding="utf-8")
    now = 10_000.0
    _write_store(
        store,
        {
            "stale": {
                "id": "stale",
                "status": "done",
                "ended_at": now - 3601,
                "followed_up": True,
                "followup_id": "bg-followup:stale",
                "followup_completed_id": "bg-followup:stale",
                "revision": 3,
            },
            "pending": {
                "id": "pending",
                "status": "done",
                "ended_at": now - 7200,
                "followed_up": False,
                "revision": 1,
            },
        },
    )
    monkeypatch.setattr(bg_jobs.time, "time", lambda: now)

    records = bg_jobs.refresh()

    assert "stale" not in records
    assert "pending" in records
    assert not artifact.exists()


def test_kill_and_followup_lease_transactions_preserve_unrelated_records(
    isolated_bg_store,
    monkeypatch,
):
    store, _ = isolated_bg_store
    _write_store(
        store,
        {
            "running": {
                "id": "running",
                "status": "running",
                "pid": 99,
                "started_at": time.time(),
                "followed_up": False,
                "revision": 1,
            },
            "done": {
                "id": "done",
                "status": "done",
                "ended_at": time.time(),
                "followed_up": False,
                "revision": 1,
            },
        },
    )
    killed = []
    monkeypatch.setattr(bg_jobs, "_kill", lambda pid: killed.append(pid))
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda pid: True)
    barrier = threading.Barrier(2)

    def do_kill():
        barrier.wait()
        return bg_jobs.kill("running")

    def do_lease():
        barrier.wait()
        return bg_jobs.lease_pending_followups(lease_owner="monitor", limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        kill_future = pool.submit(do_kill)
        lease_future = pool.submit(do_lease)
        killed_record = kill_future.result()
        leased = lease_future.result()

    persisted = bg_jobs._load()
    assert killed == [99]
    assert killed_record["killed"] is True
    assert persisted["running"]["followup_completed_id"] == "bg-followup:running"
    assert leased[0]["id"] == "done"
    assert persisted["done"]["followup_lease"]["token"] == leased[0]["followup_lease"]["token"]
