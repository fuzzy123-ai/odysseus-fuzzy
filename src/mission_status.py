"""Read-only mission snapshots built from detached agent-run ledgers."""

from __future__ import annotations

import re
from typing import Any

from src import agent_run_ledger
from src.readiness_gate import readiness_by_family, readiness_gate, sanitize_readiness_signal, signal_ready


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
    verifier_tool_keys: set[tuple[str, str]] = set()

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
        verifier_event = _is_verifier_event(payload)
        if verifier_event and payload_type == "tool_start":
            verifier_tool_keys.add(_tool_key(payload))
        elif payload_type == "tool_output" and _tool_key(payload) in verifier_tool_keys:
            verifier_event = True
        if verifier_event:
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
        "dag": _dag(phases),
        "summary": _summary(status, phases, ledger),
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
        "artifacts": {},
        "policy_tiers": {},
        "last_command_policy": None,
        "last_blocker": None,
        "readiness_signals": [],
        "latest_required_action": None,
        "started_at": None,
        "updated_at": None,
    }


def _role_counts() -> dict[str, Any]:
    return {
        "starts": 0,
        "outputs": 0,
        "blocked": 0,
        "last_exit_code": None,
        "artifacts": {},
        "policy_tiers": {},
        "last_command_policy": None,
        "last_blocker": None,
        "readiness_signals": [],
        "latest_required_action": None,
        "started_at": None,
        "updated_at": None,
    }


def _is_worker_event(payload: dict[str, Any]) -> bool:
    return payload.get("tool") == "delegate"


def _is_verifier_event(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("readiness_signal"), dict):
        return True
    if isinstance(payload.get("readiness_signals"), list):
        return True
    if payload.get("tool") in _BROWSER_TOOLS:
        return True
    if payload.get("has_screenshot"):
        return True
    command = str(payload.get("command_preview") or payload.get("command") or "")
    return bool(command and _VERIFY_COMMAND_RE.search(command))


def _tool_key(payload: dict[str, Any]) -> tuple[str, str]:
    return (str(payload.get("tool") or ""), str(payload.get("round") or ""))


def _apply_role_event(counts: dict[str, Any], payload: dict[str, Any], ts: Any) -> None:
    payload_type = payload.get("type")
    if payload_type == "tool_start":
        counts["starts"] += 1
        evidence = _verification_evidence_kind(payload)
        if evidence:
            counts["artifacts"][evidence] = counts["artifacts"].get(evidence, 0) + 1
        policy = _command_policy(payload)
        if policy:
            tier = str(policy.get("tier") or "unknown")
            counts["policy_tiers"][tier] = counts["policy_tiers"].get(tier, 0) + 1
            counts["last_command_policy"] = policy
            if policy.get("requires_confirmation"):
                counts["latest_required_action"] = _required_action("confirm_shell_command", payload, policy)
            if policy.get("blocked"):
                counts["blocked"] += 1
                counts["last_blocker"] = _blocker("command_policy", payload, policy)
        counts["started_at"] = counts["started_at"] or ts
        counts["updated_at"] = ts or counts["updated_at"]
    elif payload_type == "tool_output":
        counts["outputs"] += 1
        counts["last_exit_code"] = payload.get("exit_code")
        readiness_signals = _payload_readiness_signals(payload)
        if readiness_signals:
            counts["artifacts"]["readiness_check"] = (
                counts["artifacts"].get("readiness_check", 0) + len(readiness_signals)
            )
            counts["readiness_signals"].extend(sanitize_readiness_signal(signal) for signal in readiness_signals)
            counts["readiness_signals"] = counts["readiness_signals"][-25:]
        for signal in readiness_signals:
            family = str(signal.get("family") or "generic")
            artifact = f"{family}_readiness"
            counts["artifacts"][artifact] = counts["artifacts"].get(artifact, 0) + 1
            if not signal_ready(signal):
                counts["blocked"] += 1
                counts["last_blocker"] = _readiness_blocker(payload, signal)
        if payload.get("has_screenshot"):
            counts["artifacts"]["screenshot"] = counts["artifacts"].get("screenshot", 0) + 1
        if payload.get("blocked") or payload.get("exit_code") not in (None, 0):
            counts["blocked"] += 1
            counts["last_blocker"] = _blocker("tool_output", payload)
        counts["updated_at"] = ts or counts["updated_at"]


def _verification_evidence_kind(payload: dict[str, Any]) -> str | None:
    tool = str(payload.get("tool") or "")
    if tool in _BROWSER_TOOLS:
        return "browser_check"
    command = str(payload.get("command_preview") or payload.get("command") or "")
    if command and _VERIFY_COMMAND_RE.search(command):
        return "test_command"
    return None


def _payload_readiness_signals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    signals = payload.get("readiness_signals")
    if isinstance(signals, list):
        return [signal for signal in signals if isinstance(signal, dict)]
    signal = payload.get("readiness_signal")
    if isinstance(signal, dict):
        return [signal]
    return []


def _command_policy(payload: dict[str, Any]) -> dict[str, Any] | None:
    policy = payload.get("command_policy")
    if not isinstance(policy, dict) or not policy.get("tier"):
        return None
    return {
        key: policy.get(key)
        for key in ("tier", "reason", "requires_confirmation", "blocked", "audit")
        if key in policy
    }


def _required_action(action: str, payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "tool": str(payload.get("tool") or ""),
        "policy_tier": str(policy.get("tier") or "unknown"),
        "reason": str(policy.get("reason") or ""),
        "command_preview": str(payload.get("command_preview") or ""),
    }


def _blocker(kind: str, payload: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    blocker = {
        "kind": kind,
        "tool": str(payload.get("tool") or ""),
    }
    if payload.get("exit_code") is not None:
        blocker["exit_code"] = payload.get("exit_code")
    if policy:
        blocker.update({
            "policy_tier": str(policy.get("tier") or "unknown"),
            "reason": str(policy.get("reason") or ""),
            "command_preview": str(payload.get("command_preview") or ""),
        })
    elif payload.get("blocked"):
        blocker["reason"] = "tool_reported_blocked"
    elif payload.get("exit_code") not in (None, 0):
        blocker["reason"] = "nonzero_exit_code"
    return blocker


def _readiness_blocker(payload: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    gaps = signal.get("gaps") if isinstance(signal.get("gaps"), list) else []
    return {
        "kind": "readiness_signal",
        "tool": str(payload.get("tool") or ""),
        "reason": "readiness_gaps",
        "family": str(signal.get("family") or "generic"),
        "source": str(signal.get("source") or "unknown"),
        "state": str(signal.get("state") or "unknown"),
        "gaps": [str(item) for item in gaps[:10]],
        "gap_count": int(signal.get("gap_count") or len(gaps)),
    }


def _finish_phase(phase: dict[str, Any], counts: dict[str, Any], active: bool) -> None:
    phase.update(counts)
    phase["readiness_gate"] = readiness_gate(phase.get("readiness_signals") or [])
    if counts["starts"] == 0 and counts["outputs"] == 0:
        phase["status"] = "idle"
    elif counts["blocked"] > 0:
        phase["status"] = "blocked"
    elif active and counts["outputs"] < counts["starts"]:
        phase["status"] = "running"
    else:
        phase["status"] = "done"


def _summary(status: str, phases: dict[str, dict[str, Any]], ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    worker = phases["worker"]
    verifier = phases["verifier"]
    verification = _verification_gate(status, verifier)
    readiness_signals = verifier.get("readiness_signals") or []
    gate = readiness_gate(readiness_signals)
    memory_diagnostics = dict((ledger or {}).get("memory_diagnostics") or {})
    return {
        "status": status,
        "worker_status": worker["status"],
        "verifier_status": verifier["status"],
        "worker_blocked": worker["blocked"],
        "verifier_blocked": verifier["blocked"],
        "verifier_artifacts": dict(verifier.get("artifacts") or {}),
        "verification_required": verification["required"],
        "verification_satisfied": verification["satisfied"],
        "verification_evidence": verification["evidence"],
        "verification_gaps": verification["gaps"],
        "readiness_gate": gate,
        "readiness_signals": readiness_signals,
        "readiness_by_family": readiness_by_family(readiness_signals),
        "memory_diagnostics": memory_diagnostics,
        "policy_tiers": _merge_counts(worker.get("policy_tiers") or {}, verifier.get("policy_tiers") or {}),
        "latest_blocker": _latest_phase_value("last_blocker", phases),
        "latest_required_action": _latest_phase_value("latest_required_action", phases),
    }


def _verification_gate(status: str, verifier: dict[str, Any]) -> dict[str, Any]:
    required = status == "done"
    evidence = dict(verifier.get("artifacts") or {})
    satisfied = verifier.get("status") == "done" and bool(evidence)
    gaps: list[str] = []
    if required and not satisfied:
        if verifier.get("status") == "idle":
            gaps.append("focused_verification_missing")
        elif verifier.get("status") == "blocked":
            gaps.append("verification_blocked")
        else:
            gaps.append("verification_incomplete")
    return {
        "required": required,
        "satisfied": satisfied,
        "evidence": evidence,
        "gaps": gaps,
    }


def _dag(phases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "nodes": [
            _dag_node(phases["manager"]),
            _dag_node(phases["worker"]),
            _dag_node(phases["verifier"]),
        ],
        "edges": [
            {"source": "manager", "target": "worker", "kind": "delegates"},
            {"source": "worker", "target": "verifier", "kind": "handoff"},
            {"source": "manager", "target": "verifier", "kind": "direct_verification"},
        ],
    }


def _dag_node(phase: dict[str, Any]) -> dict[str, Any]:
    readiness_gate = phase.get("readiness_gate") if isinstance(phase.get("readiness_gate"), dict) else {}
    readiness_gaps = readiness_gate.get("gaps") if isinstance(readiness_gate.get("gaps"), list) else []
    return {
        "id": phase["role"],
        "role": phase["role"],
        "status": phase["status"],
        "started_at": phase.get("started_at"),
        "updated_at": phase.get("updated_at"),
        "starts": phase.get("starts", 0),
        "outputs": phase.get("outputs", 0),
        "blocked": phase.get("blocked", 0),
        "has_blocker": bool(phase.get("last_blocker")),
        "has_required_action": bool(phase.get("latest_required_action")),
        "readiness_state": readiness_gate.get("state", "not_applicable"),
        "readiness_blocked": readiness_gate.get("state") == "blocked",
        "readiness_gaps": len(readiness_gaps),
    }


def _merge_counts(*groups: dict[str, Any]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for group in groups:
        for key, value in group.items():
            merged[str(key)] = merged.get(str(key), 0) + int(value or 0)
    return dict(sorted(merged.items()))


def _latest_phase_value(key: str, phases: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        phase for phase in phases.values()
        if isinstance(phase.get(key), dict)
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda phase: str(phase.get("updated_at") or ""))
    return {
        "role": latest["role"],
        **latest[key],
    }


def _next_actions(status: str, phases: dict[str, dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    if status == "running":
        actions.append("watch_or_resume_stream")
    if phases["worker"]["status"] == "blocked":
        actions.append("inspect_worker_handoff")
    if phases["worker"].get("latest_required_action"):
        actions.append(phases["worker"]["latest_required_action"]["action"])
    if phases["verifier"]["status"] == "idle" and status == "done":
        actions.append("run_focused_verification")
    if any(not signal_ready(signal) for signal in phases["verifier"].get("readiness_signals") or []):
        actions.append("resolve_readiness_gaps")
    if phases["verifier"]["status"] == "blocked":
        actions.append("inspect_verification_failure")
    if phases["verifier"].get("latest_required_action"):
        actions.append(phases["verifier"]["latest_required_action"]["action"])
    actions = list(dict.fromkeys(actions))
    return actions
