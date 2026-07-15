import ast
from pathlib import Path

import pytest

from src import bg_jobs


ROOT = Path(__file__).resolve().parents[1]
BG_JOBS_PATH = ROOT / "src" / "bg_jobs.py"
BG_MONITOR_PATH = ROOT / "src" / "bg_monitor.py"


def test_local_detached_jobs_have_an_executable_one_hour_ceiling():
    assert bg_jobs.DEFAULT_MAX_RUNTIME_S == 3600
    assert bg_jobs.MAX_LOCAL_DETACHED_RUNTIME_S == 3600
    assert bg_jobs._effective_max_runtime({"max_runtime_s": 7200}) == 3600

    with pytest.raises(ValueError, match="3600-second"):
        bg_jobs.launch("echo unsafe", "session", max_runtime_s=3601)


def test_every_store_mutation_uses_the_one_process_wide_transaction_lock():
    tree = ast.parse(BG_JOBS_PATH.read_text(encoding="utf-8-sig"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    lock_call = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_STORE_LOCK" for target in node.targets)
    )
    assert isinstance(lock_call, ast.Call)
    assert isinstance(lock_call.func, ast.Attribute)
    assert isinstance(lock_call.func.value, ast.Name)
    assert (lock_call.func.value.id, lock_call.func.attr) == ("threading", "RLock")
    for name in {
        "_load",
        "_save",
        "launch",
        "refresh",
        "lease_pending_followups",
        "release_followup_lease",
        "mark_followed_up",
        "get",
        "kill",
    }:
        assert any(
            isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == "_STORE_LOCK"
                for item in node.items
            )
            for node in ast.walk(functions[name])
        ), name


def test_bg_jobs_contains_no_temporal_or_durable_agent_authority():
    original_jobs_source = BG_JOBS_PATH.read_text(encoding="utf-8-sig")
    jobs_source = original_jobs_source.lower()
    monitor_source = BG_MONITOR_PATH.read_text(encoding="utf-8-sig").lower()
    tree = ast.parse(original_jobs_source)

    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(module.startswith("temporal") for module in imported_modules)
    for forbidden in (
        "temporalio",
        "workflow_id",
        "workflow_history",
        "heartbeat_details",
        "resume_token",
        "fence_token",
    ):
        assert forbidden not in jobs_source
        assert forbidden not in monitor_source
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))


def test_monitor_uses_only_the_process_local_followup_lease_protocol():
    source = BG_MONITOR_PATH.read_text(encoding="utf-8-sig")

    assert "lease_pending_followups" in source
    assert "release_followup_lease" in source
    assert "mark_followed_up" in source
    assert "lease_token=lease_token" in source
    assert "pending_followups()" not in source
