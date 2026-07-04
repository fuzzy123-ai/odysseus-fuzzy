"""Read-only status packet for the consolidated open-work roadmap."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.roadmap_safe_queue_audit import audit_plan_dir


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN_DIR = ROOT / "docs" / "plans"
DEFAULT_ROADMAP_PATH = DEFAULT_PLAN_DIR / "open-work-completion-master-roadmap.json"
DEFAULT_MVP_STATE_PATH = DEFAULT_PLAN_DIR / "mvp-roadmap-runner-state.json"

SCHEMA = "odysseus.open_work_completion_status.v1"
ROADMAP_KIND = "odysseus.open_work_completion_master_roadmap"

LANE_FIELDS = (
    "id",
    "name",
    "priority",
    "status",
    "why_open",
    "source_gate_family",
    "safe_default",
    "operator_go_phrase",
    "done_when",
)


def build_open_work_completion_status(
    *,
    roadmap_path: Path | str | None = None,
    plan_dir: Path | str | None = None,
    mvp_state_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a compact, content-free status packet for operator diagnostics.

    The packet intentionally reports roadmap/gate metadata only. It does not
    read runtime documents, chat history, secrets, provider responses, or host
    paths outside the repository.
    """

    roadmap = Path(roadmap_path) if roadmap_path is not None else DEFAULT_ROADMAP_PATH
    plans = Path(plan_dir) if plan_dir is not None else DEFAULT_PLAN_DIR
    mvp_state = Path(mvp_state_path) if mvp_state_path is not None else DEFAULT_MVP_STATE_PATH

    audit = audit_plan_dir(plans, mvp_state_path=mvp_state)
    if not roadmap.exists():
        return {
            "schema": SCHEMA,
            "status": "missing",
            "roadmap": {
                "path": _display_path(roadmap),
                "exists": False,
            },
            "audit": _compact_audit(audit),
            "raw_records_included": False,
        }

    try:
        data = json.loads(roadmap.read_text(encoding="utf-8"))
    except Exception:
        return {
            "schema": SCHEMA,
            "status": "invalid_json",
            "roadmap": {
                "path": _display_path(roadmap),
                "exists": True,
            },
            "audit": _compact_audit(audit),
            "raw_records_included": False,
        }

    lanes = [_compact_lane(item) for item in data.get("completion_lanes") or [] if isinstance(item, dict)]
    lanes.sort(key=lambda item: (int(item.get("priority") or 999), str(item.get("id") or "")))

    return {
        "schema": SCHEMA,
        "status": str(data.get("status") or "unknown"),
        "roadmap": {
            "path": _display_path(roadmap),
            "exists": True,
            "kind": data.get("kind"),
            "kind_ok": data.get("kind") == ROADMAP_KIND,
            "updated_at": data.get("updated_at"),
            "abc_mode": data.get("abc_mode"),
            "goal": data.get("goal"),
            "goal_command": data.get("goal_command"),
            "source_of_truth": _safe_repo_paths(data.get("source_of_truth") or []),
        },
        "current_position": _safe_dict(data.get("current_position") or {}),
        "queue": {
            "safe_open_slices": audit.get("safe_open_count", 0),
            "unique_live_gates": audit.get("unique_live_gate_count", 0),
            "unique_design_gates": audit.get("unique_design_gate_count", 0),
            "queue_exhausted": bool(audit.get("queue_exhausted")),
        },
        "completion_lanes": lanes,
        "decision_packets": _compact_decision_packets(audit.get("decision_packets") or []),
        "recommended_execution_order": [str(item) for item in (data.get("recommended_execution_order") or [])],
        "recommended_next_human_decision": data.get("recommended_next_human_decision"),
        "audit": _compact_audit(audit),
        "raw_records_included": False,
    }


def _compact_lane(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in LANE_FIELDS if field in item}


def _compact_decision_packets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        packets.append(
            {
                "family": item.get("family"),
                "priority": item.get("priority"),
                "unique_gate_count": item.get("unique_gate_count"),
                "entry_count": item.get("entry_count"),
                "gate_ids": [str(gate_id) for gate_id in (item.get("gate_ids") or [])],
                "decision_needed": item.get("decision_needed"),
                "safe_default": item.get("safe_default"),
                "go_phrase": item.get("go_phrase"),
            }
        )
    return packets


def _compact_audit(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": audit.get("schema_version"),
        "files_scanned": audit.get("files_scanned"),
        "safe_open_count": audit.get("safe_open_count"),
        "unique_live_gate_count": audit.get("unique_live_gate_count"),
        "unique_design_gate_count": audit.get("unique_design_gate_count"),
        "other_open_count": audit.get("other_open_count"),
        "queue_exhausted": audit.get("queue_exhausted"),
        "mvp": _safe_dict(audit.get("mvp") or {}),
        "recommended_decisions": audit.get("recommended_decisions") or [],
    }


def _safe_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _safe_value(val) for key, val in value.items()}


def _safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return _safe_dict(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return str(value)


def _safe_repo_paths(paths: list[Any]) -> list[str]:
    safe: list[str] = []
    for item in paths:
        text = str(item).replace("\\", "/")
        if ":" in text or text.startswith("/"):
            continue
        safe.append(text)
    return safe


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception:
        return path.name
