import json
import os
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.memory_runtime_metrics import get_memory_runtime_metrics_registry

from . import vault_service
from .derived_index import build_derived_index, derived_index_status
from .memory_ledger import memory_ledger_status, sync_memory_ledger
from .query_layer import query_layer_status
from .raptor_warming import raptor_cache_warming_status, warm_raptor_cache
from .vault_security import VaultSecurityError


JOB_ID = "obsidian.memory_automation"
AUTOMATION_REPORT_PATH = ".obsidian/odysseus/memory/automation_status.json"
_METRICS_CLOCK = time.perf_counter


def _record_automation_metrics(outcome: str, started: float) -> None:
    try:
        registry = get_memory_runtime_metrics_registry()
        registry.observe_histogram(
            "odysseus_memory_operation_duration_seconds",
            {
                "component": "memory",
                "operation": "automation",
                "phase": "total",
                "outcome": outcome,
                "runtime": "worker",
            },
            max(0.0, _METRICS_CLOCK() - started),
        )
        registry.increment_counter(
            "odysseus_memory_operations_total",
            {
                "component": "memory",
                "operation": "automation",
                "outcome": outcome,
                "runtime": "worker",
            },
        )
        registry.set_gauge(
            "odysseus_memory_worker_queue_depth",
            {"component": "memory", "operation": "automation", "runtime": "worker"},
            0,
        )
    except Exception:
        pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _cooldown_seconds() -> int:
    return max(0, int(os.getenv("ODYSSEUS_OBSIDIAN_MEMORY_AUTOMATION_COOLDOWN_SECONDS", "300") or 300))


def _max_sources_per_pass() -> int:
    return max(1, int(os.getenv("ODYSSEUS_OBSIDIAN_MEMORY_AUTOMATION_MAX_SOURCES", "500") or 500))


def _concurrency_limit() -> int:
    return max(1, int(os.getenv("ODYSSEUS_OBSIDIAN_MEMORY_AUTOMATION_CONCURRENCY", "1") or 1))


def _failure_backoff_seconds() -> int:
    return max(0, int(os.getenv("ODYSSEUS_OBSIDIAN_MEMORY_AUTOMATION_FAILURE_BACKOFF_SECONDS", "900") or 900))


def _report_abspath(vault_dir: str) -> str:
    return vault_service.secure_path(vault_dir, AUTOMATION_REPORT_PATH)


def _read_report(vault_dir: str) -> Dict[str, Any]:
    path = _report_abspath(vault_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_report(vault_dir: str, payload: Dict[str, Any]) -> None:
    path = _report_abspath(vault_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def memory_automation_status(vault_dir: str) -> Dict[str, Any]:
    ledger = memory_ledger_status(vault_dir)
    derived = derived_index_status(vault_dir)
    query = query_layer_status(vault_dir)
    raptor_warming = raptor_cache_warming_status(vault_dir)
    report = _read_report(vault_dir)
    now = _utcnow()
    cooldown = _cooldown_seconds()
    failure_backoff = _failure_backoff_seconds()
    last_run_raw = str(report.get("last_run_at") or "").strip()
    last_failure_raw = str(report.get("last_failure_at") or "").strip()
    next_eligible_at = ""
    cooling_down = False
    if last_run_raw:
        try:
            last_run = datetime.fromisoformat(last_run_raw.replace("Z", "+00:00"))
            next_eligible = last_run + timedelta(seconds=cooldown)
            next_eligible_at = _utc_iso(next_eligible.astimezone(timezone.utc))
            cooling_down = next_eligible > now
        except ValueError:
            next_eligible_at = ""
    failure_backoff_until = ""
    failure_backoff_active = False
    if last_failure_raw and failure_backoff > 0:
        try:
            last_failure = datetime.fromisoformat(last_failure_raw.replace("Z", "+00:00"))
            backoff_until = last_failure + timedelta(seconds=failure_backoff)
            failure_backoff_until = _utc_iso(backoff_until.astimezone(timezone.utc))
            failure_backoff_active = backoff_until > now
        except ValueError:
            failure_backoff_until = ""
    pending_actions: List[str] = []
    if int((ledger.get("summary") or {}).get("pending_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("stale_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("total_sources") or 0) == 0:
        pending_actions.append("sync_memory_ledger")
    if not bool(derived.get("configured")) or not bool((derived.get("readiness") or {}).get("ready", False)):
        pending_actions.append("build_derived_index")
    if not bool((query.get("readiness") or {}).get("ready", False)):
        pending_actions.append("query_layer_waits_on_index")
    if raptor_warming.get("pending"):
        pending_actions.append("warm_raptor_cache")
    warnings: List[str] = []
    if cooling_down:
        warnings.append("Memory automation cooldown is active; the next periodic pass will wait until the cooldown expires.")
    if failure_backoff_active:
        warnings.append("Memory automation failure backoff is active; the next automatic pass will wait until the backoff expires.")
    return {
        "enabled": True,
        "job_id": JOB_ID,
        "report_path": AUTOMATION_REPORT_PATH,
        "pending_actions": pending_actions,
        "cost_controller": {
            "cooldown_seconds": cooldown,
            "max_sources_per_pass": _max_sources_per_pass(),
            "concurrency_limit": _concurrency_limit(),
            "cooling_down": cooling_down,
            "next_eligible_at": next_eligible_at,
            "failure_backoff_seconds": failure_backoff,
            "failure_backoff_active": failure_backoff_active,
            "failure_backoff_until": failure_backoff_until,
        },
        "safety": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "allowed_actions": ["sync_memory_ledger", "build_derived_index", "warm_raptor_cache"],
        },
        "layers": {
            "ledger": ledger.get("summary", {}),
            "derived_index": derived.get("summary", {}),
            "query_layer": query.get("summary", {}),
            "raptor_cache_warming": raptor_warming,
        },
        "last_run": {
            "last_run_at": last_run_raw,
            "last_trigger": str(report.get("last_trigger") or ""),
            "last_actions": list(report.get("actions_executed") or []),
            "skipped": bool(report.get("skipped", False)),
            "failed": bool(report.get("failed", False)),
            "reason": str(report.get("reason") or ""),
            "last_failure_at": last_failure_raw,
            "consecutive_failures": int(report.get("consecutive_failures") or 0),
            "last_error": str(report.get("last_error") or ""),
            "warnings": list(report.get("warnings") or []),
        },
        "warnings": warnings,
    }


def _run_memory_automation_impl(
    owner: Optional[str] = None,
    trigger: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    try:
        vault_dir = vault_service.unlocked_vault_path_for_owner(owner)
    except VaultSecurityError:
        return {"skipped": True, "reason": "vault_locked", "actions_executed": []}

    status_before = memory_automation_status(vault_dir)
    now = _utcnow()
    cost = dict(status_before.get("cost_controller") or {})
    last_run = dict(status_before.get("last_run") or {})
    if cost.get("cooling_down") and not force:
        payload = {
            "job_id": JOB_ID,
            "last_run_at": _utc_iso(now),
            "last_trigger": trigger or "",
            "actions_executed": [],
            "skipped": True,
            "failed": False,
            "reason": "cooldown_active",
            "summary": {
                "source_note_writes": False,
                "derived_data_writes_only": True,
            },
        }
        _write_report(vault_dir, payload)
        return {"skipped": True, "reason": "cooldown_active", "actions_executed": []}
    if cost.get("failure_backoff_active") and not force:
        payload = {
            "job_id": JOB_ID,
            "last_run_at": _utc_iso(now),
            "last_trigger": trigger or "",
            "actions_executed": [],
            "skipped": True,
            "failed": False,
            "reason": "failure_backoff_active",
            "last_failure_at": str(last_run.get("last_failure_at") or ""),
            "consecutive_failures": int(last_run.get("consecutive_failures") or 0),
            "last_error": str(last_run.get("last_error") or ""),
            "summary": {
                "source_note_writes": False,
                "derived_data_writes_only": True,
            },
        }
        _write_report(vault_dir, payload)
        return {"skipped": True, "reason": "failure_backoff_active", "actions_executed": []}

    actions_executed: List[str] = []
    warnings: List[str] = []
    errors: List[str] = []
    total_sources = int((status_before.get("layers") or {}).get("ledger", {}).get("total_sources") or 0)
    if total_sources > _max_sources_per_pass():
        warnings.append(
            f"Memory automation skipped rebuild because the vault has {total_sources} sources and the per-pass cap is {_max_sources_per_pass()}."
        )
    else:
        try:
            ledger = memory_ledger_status(vault_dir)
            if int((ledger.get("summary") or {}).get("pending_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("stale_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("total_sources") or 0) == 0:
                sync_memory_ledger(vault_dir)
                actions_executed.append("sync_memory_ledger")
            derived = derived_index_status(vault_dir)
            if not bool(derived.get("configured")) or not bool((derived.get("readiness") or {}).get("ready", False)):
                build_derived_index(vault_dir)
                actions_executed.append("build_derived_index")
            warming = warm_raptor_cache(vault_dir)
            if not warming.get("skipped") and warming.get("warmed"):
                actions_executed.append("warm_raptor_cache")
        except Exception as exc:
            error_text = str(exc) or exc.__class__.__name__
            errors.append(error_text)
            failure_count = int(last_run.get("consecutive_failures") or 0) + 1
            payload = {
                "job_id": JOB_ID,
                "last_run_at": _utc_iso(now),
                "last_trigger": trigger or "",
                "actions_executed": actions_executed,
                "skipped": False,
                "failed": True,
                "reason": "action_failed",
                "last_failure_at": _utc_iso(now),
                "consecutive_failures": failure_count,
                "last_error": error_text,
                "warnings": warnings,
                "summary": {
                    "source_note_writes": False,
                    "derived_data_writes_only": True,
                    "raptor_cache_warming": True,
                },
                "context": {
                    "owner": owner or "default",
                    "trigger": trigger or "",
                    "event_keys": sorted((context or {}).keys())[:10],
                },
            }
            _write_report(vault_dir, payload)
            return {
                "skipped": False,
                "failed": True,
                "reason": "action_failed",
                "actions_executed": actions_executed,
                "errors": errors,
                "warnings": warnings,
                "safety": {
                    "source_note_writes": False,
                    "derived_data_writes_only": True,
                },
            }

    after = memory_automation_status(vault_dir)
    payload = {
        "job_id": JOB_ID,
        "last_run_at": _utc_iso(now),
        "last_trigger": trigger or "",
        "actions_executed": actions_executed,
        "skipped": False,
        "failed": False,
        "reason": "",
        "last_failure_at": "",
        "consecutive_failures": 0,
        "last_error": "",
        "summary": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "raptor_cache_warming": True,
            "pending_actions_after": after.get("pending_actions", []),
        },
        "warnings": warnings,
        "context": {
            "owner": owner or "default",
            "trigger": trigger or "",
            "event_keys": sorted((context or {}).keys())[:10],
        },
    }
    _write_report(vault_dir, payload)
    return {
        "skipped": False,
        "failed": False,
        "actions_executed": actions_executed,
        "status": after,
        "errors": errors,
        "warnings": warnings,
        "safety": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "raptor_cache_warming": True,
        },
    }


def run_memory_automation(
    owner: Optional[str] = None,
    trigger: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    started = _METRICS_CLOCK()
    outcome = "error"
    try:
        result = _run_memory_automation_impl(
            owner=owner,
            trigger=trigger,
            context=context,
            force=force,
        )
        if bool(result.get("skipped")):
            outcome = "blocked"
        elif bool(result.get("failed")):
            outcome = "error"
        else:
            outcome = "success"
        return result
    except (asyncio.CancelledError, TimeoutError):
        outcome = "cancelled"
        raise
    except (FileNotFoundError, PermissionError, ValueError, VaultSecurityError):
        outcome = "blocked"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        _record_automation_metrics(outcome, started)


def job_spec() -> Dict[str, Any]:
    return {
        "id": JOB_ID,
        "label": "Obsidian Memory Automation",
        "priority": 60,
        "capabilities": ["periodic", "chat_completed", "vault", "obsidian", "memory"],
        "run": run_memory_automation,
    }
