"""Read-only mission snapshots built from detached agent-run ledgers."""

from __future__ import annotations

import re
from typing import Any

from src import agent_run_ledger


_VERIFY_COMMAND_RE = re.compile(
    r"\b(pytest|node\s+--check|npm\s+(test|run\s+test)|playwright|browser|python\s+-m\s+pytest)\b",
    re.IGNORECASE,
)
_BROWSER_TOOLS = {"browser", "browser_check", "builtin_browser"}


def summarize_mission(
    session_id: str,
    *,
    tail: int = 20,
    active: bool = False,
    memory_status: str | None = None,
) -> dict[str, Any]:
    """Return a compact manager/worker/verifier lifecycle snapshot.

    The snapshot is derived from the existing append-only run ledger, so it does
    not start, stop, or mutate any agent work. It intentionally reuses the
    ledger's event summaries instead of full chat/tool output.
    """

    ledger = agent_run_ledger.summarize_run(session_id, tail=tail)
    events = agent_run_ledger.read_events(session_id)
    status = _mission_status(ledger.get("status"), active, memory_status)
    phases = {
        "manager": _phase("manager", "running" if active else ("done" if ledger.get("exists") else "idle")),
        "worker": _phase("worker", "idle"),
        "verifier": _phase("verifier", "idle"),
    }
    worker = _role_counts()
    verifier = _role_counts()

    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("event") == "run_started":
            phases["manager"]["started_at"] = phases["manager"]["started_at"] or event.get("ts")
            phases["manager"]["updated_at"] = event.get("ts") or phases["manager"]["updated_at"]
            continue
        if event.get("event") != "sse_event":
            continue

        payload_type = payload.get("type")
        if payload_type in {"agent_step", "rounds_exhausted", "budget_exceeded"}:
            phases["manager"]["updated_at"] = event.get("ts") or phases["manager"]["updated_at"]
        if _is_worker_event(payload):
            _apply_role_event(worker, payload, event.get("ts"))
        if _is_verifier_event(payload):
            _apply_role_event(verifier, payload, event.get("ts"))

    _finish_phase(phases["worker"], worker, active)
    _finish_phase(phases["verifier"], verifier, active)
    if status in {"error", "stopped"}:
        phases["manager"]["status"] = status
    elif active:
        phases["manager"]["status"] = "running"
    elif ledger.get("exists"):
        phases["manager"]["status"] = "done"

    return {
        "mission_id": str(session_id),
        "session_id": str(session_id),
        "status": status,
        "active": bool(active),
        "memory_status": memory_status,
        "ledger": ledger,
        "phases": phases,
        "next_actions": _next_actions(status, phases),
    }


def _mission_status(ledger_status: Any, active: bool, memory_status: str | None) -> str:
    if active:
        return "running"
    status = str(memory_status or ledger_status or "")
    if status in {"done", "error", "stopped"}:
        return status
    return "unknown"


def _phase(role: str, status: str) -> dict[str, Any]:
    return {
        "role": role,
        "status": status,
        "starts": 0,
        "outputs": 0,
        "blocked": 0,
        "last_exit_code": None,
        "started_at": None,
        "updated_at": None,
    }


def _role_counts() -> dict[str, Any]:
    return {"starts": 0, "outputs": 0, "blocked": 0, "last_exit_code": None, "started_at": None, "updated_at": None}


def _is_worker_event(payload: dict[str, Any]) -> bool:
    return payload.get("tool") == "delegate"


def _is_verifier_event(payload: dict[str, Any]) -> bool:
    if payload.get("tool") in _BROWSER_TOOLS:
        return True
    if payload.get("has_screenshot"):
        return True
    command = str(payload.get("command_preview") or "")
    return bool(command and _VERIFY_COMMAND_RE.search(command))


def _apply_role_event(counts: dict[str, Any], payload: dict[str, Any], ts: Any) -> None:
    payload_type = payload.get("type")
    if payload_type == "tool_start":
        counts["starts"] += 1
        counts["started_at"] = counts["started_at"] or ts
        counts["updated_at"] = ts or counts["updated_at"]
    elif payload_type == "tool_output":
        counts["outputs"] += 1
        counts["last_exit_code"] = payload.get("exit_code")
        if payload.get("blocked") or payload.get("exit_code") not in (None, 0):
            counts["blocked"] += 1
        counts["updated_at"] = ts or counts["updated_at"]


def _finish_phase(phase: dict[str, Any], counts: dict[str, Any], active: bool) -> None:
    phase.update(counts)
    if counts["starts"] == 0:
        phase["status"] = "idle"
    elif counts["blocked"] > 0:
        phase["status"] = "blocked"
    elif active and counts["outputs"] < counts["starts"]:
        phase["status"] = "running"
    else:
        phase["status"] = "done"


def _next_actions(status: str, phases: dict[str, dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    if status == "running":
        actions.append("watch_or_resume_stream")
    if phases["worker"]["status"] == "blocked":
        actions.append("inspect_worker_handoff")
    if phases["verifier"]["status"] == "idle" and status == "done":
        actions.append("run_focused_verification")
    if phases["verifier"]["status"] == "blocked":
        actions.append("inspect_verification_failure")
    return actions
