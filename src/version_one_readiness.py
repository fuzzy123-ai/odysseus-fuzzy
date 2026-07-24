"""Machine-readable Version 1.0 readiness gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


VERSION_ONE_READINESS_SCHEMA = "odysseus.version_one_readiness.v1"

DEFAULT_MVP_STATE_PATH = Path("docs/plans/mvp-roadmap-runner-state.json")
DEFAULT_LEGACY_ROADMAP_PATH = Path("docs/plans/legacy-chat-new-functions-master-roadmap.json")


def load_version_one_readiness(
    *,
    mvp_state_path: str | Path = DEFAULT_MVP_STATE_PATH,
    legacy_roadmap_path: str | Path = DEFAULT_LEGACY_ROADMAP_PATH,
) -> dict[str, Any]:
    """Load the local roadmap evidence without probing live services."""

    return build_version_one_readiness(
        mvp_state=_load_json(Path(mvp_state_path)),
        legacy_roadmap=_load_json(Path(legacy_roadmap_path)),
    )


def build_version_one_readiness(
    *,
    mvp_state: Mapping[str, Any] | None = None,
    legacy_roadmap: Mapping[str, Any] | None = None,
    ui_live: bool | None = None,
    clarification_first: Any = None,
    harbor_one_live: Any = None,
    workspace_snapshot: Any = None,
    sandbox_python_acceptance: Any = None,
    memory_local_model: Any = None,
) -> dict[str, Any]:
    state = mvp_state or {}
    roadmap = legacy_roadmap or {}
    mvp = _mvp_summary(state)
    legacy_chat = _legacy_chat_summary(roadmap)
    ui = _ui_summary(state, roadmap, ui_live=ui_live)
    gates = _readiness_gates(
        state,
        roadmap,
        ui_live=ui["live"],
        clarification_first=clarification_first,
        harbor_one_live=harbor_one_live,
        workspace_snapshot=workspace_snapshot,
        sandbox_python_acceptance=sandbox_python_acceptance,
        memory_local_model=memory_local_model,
    )
    blocking_gate_ids = tuple(gate["id"] for gate in gates if not gate["ready"])
    partial_gate_ids = tuple(gate["id"] for gate in gates if gate["status"] == "partial")

    version_ready = bool(
        mvp["complete"] and legacy_chat["backend_ready"] and ui["live"] and not blocking_gate_ids
    )
    status = "ready" if version_ready else _blocked_status(mvp, legacy_chat, ui, gates)
    next_decision = _next_decision(version_ready=version_ready, status=status, gates=gates)
    return {
        "schema": VERSION_ONE_READINESS_SCHEMA,
        "status": status,
        "version_1_0_ready": version_ready,
        "mvp": mvp,
        "legacy_chat": legacy_chat,
        "ui": ui,
        "readiness_gates": gates,
        "blocking_gate_ids": blocking_gate_ids,
        "partial_gate_ids": partial_gate_ids,
        "missing_evidence": tuple(gate["summary"] for gate in gates if not gate["ready"]),
        "next_actions": tuple(gate["next_action"] for gate in gates if not gate["ready"]),
        "release": {
            "external_release_allowed": version_ready,
            "tag_allowed": version_ready,
            "deploy_allowed": version_ready,
            "blocked_gate_count": len(blocking_gate_ids),
        },
        "next_human_decision": next_decision,
        "live_probe_performed": False,
        "network_probe_performed": False,
        "ui_probe_performed": False,
        "raw_content_visible": False,
        "host_paths_visible": False,
        "token_values_visible": False,
        "chat_id_values_visible": False,
        "private_values_visible": False,
    }


def _readiness_gates(
    state: Mapping[str, Any],
    legacy_roadmap: Mapping[str, Any],
    *,
    ui_live: bool,
    clarification_first: Any,
    harbor_one_live: Any,
    workspace_snapshot: Any,
    sandbox_python_acceptance: Any,
    memory_local_model: Any,
) -> tuple[dict[str, Any], ...]:
    return (
        _gate(
            "clarification_first_acceptance",
            "Clarification-first acceptance",
            _gate_value(
                clarification_first,
                state,
                legacy_roadmap,
                "clarification_first_acceptance",
                "clarification_first_ready",
                "ask_user_acceptance",
            ),
            next_action="Run the clarification-first acceptance suite and attach summarized evidence.",
        ),
        _gate(
            "harbor_one_live",
            "Harbor One live",
            _gate_value(
                harbor_one_live,
                state,
                legacy_roadmap,
                "harbor_one_live",
                "frontpage_v3_live",
                "ui_live",
                "version_1_0_ui_live",
                fallback=ui_live,
            ),
            live_required=True,
            next_action="Verify Harbor One live with bounded operator Go before release.",
        ),
        _gate(
            "workspace_snapshot_green",
            "Workspace snapshot green",
            _gate_value(
                workspace_snapshot,
                state,
                legacy_roadmap,
                "workspace_snapshot_green",
                "workspace_snapshot",
            ),
            next_action="Produce a green workspace snapshot with no blocked or degraded sections.",
        ),
        _gate(
            "sandbox_python_acceptance",
            "Python sandbox acceptance",
            _gate_value(
                sandbox_python_acceptance,
                state,
                legacy_roadmap,
                "sandbox_python_acceptance",
                "python_sandbox_acceptance",
            ),
            next_action="Run the Python sandbox acceptance flow and attach summarized pass evidence.",
        ),
        _gate(
            "memory_local_model_acceptance",
            "Memory and local-model acceptance",
            _gate_value(
                memory_local_model,
                state,
                legacy_roadmap,
                "memory_local_model_acceptance",
                "local_model_memory_acceptance",
                "memory_maintenance_acceptance",
            ),
            next_action="Verify memory maintenance and local-model scheduling evidence before release.",
        ),
    )


def _gate_value(
    explicit: Any,
    state: Mapping[str, Any],
    legacy_roadmap: Mapping[str, Any],
    *keys: str,
    fallback: Any = None,
) -> Any:
    if explicit is not None:
        return explicit
    state_gate = state.get("version_1_0_gate") if isinstance(state.get("version_1_0_gate"), Mapping) else {}
    state_evidence = state.get("evidence") if isinstance(state.get("evidence"), Mapping) else {}
    legacy_evidence = legacy_roadmap.get("evidence") if isinstance(legacy_roadmap.get("evidence"), Mapping) else {}
    for source in (state_gate, state_evidence, legacy_evidence):
        for key in keys:
            if key in source:
                return source[key]
    return fallback


def _gate(
    gate_id: str,
    label: str,
    value: Any,
    *,
    live_required: bool = False,
    next_action: str,
) -> dict[str, Any]:
    ready = _ready_bool(value)
    status = "go" if ready else _gate_status(value)
    if status == "go":
        ready = True
    if status in {"required", "blocked", "no_go", "partial"}:
        ready = False
    return {
        "id": gate_id,
        "label": label,
        "status": status,
        "ready": ready,
        "required": True,
        "live_required": live_required,
        "summary": _gate_summary(label, status=status, ready=ready),
        "next_action": "" if ready else next_action,
        "raw_content_visible": False,
        "private_values_visible": False,
        "token_values_visible": False,
    }


def _ready_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        for key in ("ready", "ok", "passed", "pass", "green", "live", "accepted"):
            if isinstance(value.get(key), bool):
                return bool(value[key])
        blocked = _safe_int(value.get("blocked_count") or value.get("blocker_count"))
        degraded = _safe_int(value.get("degraded_count") or value.get("degrade_count"))
        pending = _safe_int(value.get("pending_count") or value.get("unresolved_required_count"))
        status = _safe_token(value.get("status") or value.get("state") or value.get("decision"))
        if blocked or degraded or pending:
            return False
        return status in {"ok", "go", "ready", "passed", "pass", "complete", "green", "live", "accepted"}
    status = _safe_token(value)
    return status in {"ok", "go", "ready", "passed", "pass", "complete", "green", "live", "accepted"}


def _gate_status(value: Any) -> str:
    if isinstance(value, Mapping):
        blocked = _safe_int(value.get("blocked_count") or value.get("blocker_count"))
        degraded = _safe_int(value.get("degraded_count") or value.get("degrade_count"))
        pending = _safe_int(value.get("pending_count") or value.get("unresolved_required_count"))
        status = _safe_token(value.get("status") or value.get("state") or value.get("decision"))
        if blocked:
            return "blocked"
        if degraded or status in {"partial", "warn", "warning", "degraded", "attention"}:
            return "partial"
        if pending or status in {"pending", "review", "needs_review", "manual_pending"}:
            return "required"
        if status in {"failed", "fail", "no_go", "nogo", "denied"}:
            return "no_go"
    if value is None:
        return "required"
    return "blocked"


def _gate_summary(label: str, *, status: str, ready: bool) -> str:
    if ready:
        return f"{label} evidence is green."
    if status == "partial":
        return f"{label} evidence is partial or degraded."
    if status == "no_go":
        return f"{label} evidence reported no-go."
    if status == "blocked":
        return f"{label} evidence is blocked."
    return f"{label} evidence is required."


def _mvp_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    roadmaps = [item for item in state.get("roadmaps") or () if isinstance(item, Mapping)]
    first_ten = [item for item in roadmaps if _safe_int(item.get("number")) in range(1, 11)]
    percents = [_bounded_percent(item.get("percent")) for item in first_ten]
    completed = sum(1 for percent in percents if percent >= 100)
    count = len(first_ten) or 10
    overall = _bounded_percent(round(sum(percents) / count)) if percents else 0
    complete = count == 10 and completed == 10 and overall >= 100
    return {
        "status": "complete" if complete else "incomplete",
        "complete": complete,
        "overall_percent": overall,
        "roadmap_count": count,
        "completed_roadmap_count": completed,
        "required_roadmap_count": 10,
    }


def _legacy_chat_summary(roadmap: Mapping[str, Any]) -> dict[str, Any]:
    evidence = roadmap.get("evidence") if isinstance(roadmap.get("evidence"), Mapping) else {}
    open_contracts = tuple(_safe_token(item) for item in roadmap.get("open_backend_contracts") or ())
    contract_keys = [key for key in evidence if str(key).startswith("lc") and str(key).endswith("_backend_contract")]
    backend_ready = not open_contracts and len(contract_keys) >= 10
    return {
        "status": "backend_ready" if backend_ready else "blocked",
        "backend_ready": backend_ready,
        "backend_contract_count": len(contract_keys),
        "open_backend_contracts": open_contracts,
        "ui_execution_required": True,
        "ui_code_included": False,
    }


def _ui_summary(
    state: Mapping[str, Any],
    legacy_roadmap: Mapping[str, Any],
    *,
    ui_live: bool | None,
) -> dict[str, Any]:
    if ui_live is None:
        gate = state.get("version_1_0_gate") if isinstance(state.get("version_1_0_gate"), Mapping) else {}
        evidence = legacy_roadmap.get("evidence") if isinstance(legacy_roadmap.get("evidence"), Mapping) else {}
        ui_live = bool(gate.get("ui_live") or evidence.get("version_1_0_ui_live"))
    return {
        "status": "live" if ui_live else "required",
        "live": bool(ui_live),
        "gate": "VERSION-1-UI-LIVE",
        "required": True,
    }


def _blocked_status(
    mvp: Mapping[str, Any],
    legacy_chat: Mapping[str, Any],
    ui: Mapping[str, Any],
    gates: tuple[Mapping[str, Any], ...],
) -> str:
    if not mvp.get("complete"):
        return "mvp_roadmaps_incomplete"
    if not legacy_chat.get("backend_ready"):
        return "backend_contracts_incomplete"
    if not ui.get("live"):
        return "ui_live_required"
    for gate in gates:
        if not gate.get("ready"):
            return f"{gate.get('id')}_required"
    return "blocked"


def _next_decision(*, version_ready: bool, status: str, gates: tuple[Mapping[str, Any], ...]) -> str:
    if version_ready:
        return "Version 1.0 can be released."
    first_blocker = next((gate for gate in gates if not gate.get("ready")), None)
    if first_blocker and status != "ui_live_required":
        return str(first_blocker.get("next_action") or "Collect the missing release evidence.")
    return "Ship the new UI and verify it live before claiming Version 1.0."


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _bounded_percent(value: Any) -> int:
    return max(0, min(100, _safe_int(value)))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch in "._:")[:100]
