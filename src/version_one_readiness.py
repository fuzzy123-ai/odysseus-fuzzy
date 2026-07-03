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
) -> dict[str, Any]:
    mvp = _mvp_summary(mvp_state or {})
    legacy_chat = _legacy_chat_summary(legacy_roadmap or {})
    ui = _ui_summary(mvp_state or {}, legacy_roadmap or {}, ui_live=ui_live)

    version_ready = bool(mvp["complete"] and legacy_chat["backend_ready"] and ui["live"])
    status = "ready" if version_ready else _blocked_status(mvp, legacy_chat, ui)
    next_decision = (
        "Version 1.0 can be released."
        if version_ready
        else "Ship the new UI and verify it live before claiming Version 1.0."
    )
    return {
        "schema": VERSION_ONE_READINESS_SCHEMA,
        "status": status,
        "version_1_0_ready": version_ready,
        "mvp": mvp,
        "legacy_chat": legacy_chat,
        "ui": ui,
        "release": {
            "external_release_allowed": version_ready,
            "tag_allowed": version_ready,
            "deploy_allowed": version_ready,
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


def _blocked_status(mvp: Mapping[str, Any], legacy_chat: Mapping[str, Any], ui: Mapping[str, Any]) -> str:
    if not mvp.get("complete"):
        return "mvp_roadmaps_incomplete"
    if not legacy_chat.get("backend_ready"):
        return "backend_contracts_incomplete"
    if not ui.get("live"):
        return "ui_live_required"
    return "blocked"


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
