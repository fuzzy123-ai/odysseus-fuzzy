"""Summarize safe roadmap queue status without reading private runtime data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN_DIR = ROOT / "docs" / "plans"
DEFAULT_MVP_STATE = DEFAULT_PLAN_DIR / "mvp-roadmap-runner-state.json"

SAFE_CLASSES = {"safe_offline", "repo_only"}
GATED_CLASSES = {"needs_live_go", "needs_design", "live_gated", "blocked"}
DONE_STATUSES = {
    "done",
    "complete",
    "completed",
    "go",
    "resolved",
    "resolved_with_synthetic_live_evidence",
    "implemented",
    "repo_complete",
    "backend_complete",
    "backend contract consumed",
}
GATED_DONE_STATUSES = {
    "backend_ready_ui_gated",
    "backend_ready_live_gated",
    "backend_contracts_ready_ui_live_gated",
    "repo_slices_done_live_gated",
    "repo_contracts_done_live_gated",
    "foundation_done_live_gated",
    "repo_prepared_live_unverified",
}
OPEN_STATUSES = {"open", "planned", "running", "todo", "pending"}


@dataclass(frozen=True)
class RoadmapAuditItem:
    file: str
    path: str
    item_id: str
    item_class: str
    status: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "file": self.file,
            "path": self.path,
            "id": self.item_id,
            "class": self.item_class,
            "status": self.status,
            "reason": self.reason,
        }


def audit_plan_dir(plan_dir: Path = DEFAULT_PLAN_DIR, *, mvp_state_path: Path = DEFAULT_MVP_STATE) -> dict[str, Any]:
    safe_open: list[RoadmapAuditItem] = []
    live_gates: list[RoadmapAuditItem] = []
    design_gates: list[RoadmapAuditItem] = []
    other_open: list[RoadmapAuditItem] = []
    files_scanned = 0

    for path in sorted(plan_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        files_scanned += 1
        for item in _walk_items(data, file_name=path.name):
            bucket = _classify_item(item)
            if bucket == "safe_open":
                safe_open.append(item)
            elif bucket == "live_gate":
                live_gates.append(item)
            elif bucket == "design_gate":
                design_gates.append(item)
            elif bucket == "other_open":
                other_open.append(item)

    mvp = _mvp_summary(mvp_state_path)
    return {
        "schema_version": 1,
        "files_scanned": files_scanned,
        "safe_open_count": len(safe_open),
        "live_gate_count": len(live_gates),
        "design_gate_count": len(design_gates),
        "other_open_count": len(other_open),
        "mvp": mvp,
        "queue_exhausted": len(safe_open) == 0 and mvp.get("active_slice") in (None, "none", ""),
        "safe_open_slices": [item.to_dict() for item in safe_open],
        "live_gates": [item.to_dict() for item in live_gates],
        "design_gates": [item.to_dict() for item in design_gates],
        "other_open_items": [item.to_dict() for item in other_open],
    }


def render_markdown(report: dict[str, Any]) -> str:
    mvp = report.get("mvp") or {}
    lines = [
        "# Roadmap Safe Queue Audit",
        "",
        f"Files scanned: {report['files_scanned']}",
        f"Safe open slices: {report['safe_open_count']}",
        f"Live gates: {report['live_gate_count']}",
        f"Design gates: {report['design_gate_count']}",
        f"Queue exhausted: {'yes' if report['queue_exhausted'] else 'no'}",
        "",
        "## MVP Runner",
        "",
        f"- Progress: {mvp.get('progress_percent', 'unknown')}%",
        f"- UI live: {'yes' if mvp.get('ui_live') else 'no'}",
        f"- Active slice: {mvp.get('active_slice') or 'none'}",
        "",
    ]
    if report["safe_open_slices"]:
        lines.extend(["## Safe Open Slices", "", "| File | ID | Class | Status |", "| - | - | - | - |"])
        for item in report["safe_open_slices"]:
            lines.append(f"| {item['file']} | {item['id']} | {item['class']} | {item['status']} |")
        lines.append("")
    if report["live_gates"] or report["design_gates"]:
        lines.extend(["## Gates", "", "| Type | File | ID | Status |", "| - | - | - | - |"])
        for item in report["live_gates"]:
            lines.append(f"| live | {item['file']} | {item['id']} | {item['status']} |")
        for item in report["design_gates"]:
            lines.append(f"| design | {item['file']} | {item['id']} | {item['status']} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _walk_items(value: Any, *, file_name: str, path: str = "") -> Iterable[RoadmapAuditItem]:
    if isinstance(value, dict):
        item_class = str(value.get("class") or "").strip()
        status = str(value.get("status") or "").strip()
        if item_class or status:
            yield RoadmapAuditItem(
                file=file_name,
                path=path or "/",
                item_id=str(value.get("id") or value.get("slice") or value.get("title") or path or file_name),
                item_class=item_class,
                status=status,
                reason=str(value.get("reason") or value.get("blocker") or value.get("goal") or ""),
            )
        for key, child in value.items():
            yield from _walk_items(child, file_name=file_name, path=f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_items(child, file_name=file_name, path=f"{path}[{index}]")


def _classify_item(item: RoadmapAuditItem) -> str:
    status = item.status.lower()
    item_class = item.item_class.lower()
    item_id = item.item_id.lower()
    path = item.path.lower()
    if status in DONE_STATUSES or status in GATED_DONE_STATUSES:
        return "closed"
    if item_class in SAFE_CLASSES:
        return "safe_open" if status in OPEN_STATUSES or not status else "other_open"
    if item_class in {"needs_live_go", "live_gated"} or status == "gated":
        return "live_gate"
    if item_class == "needs_design":
        return "design_gate"
    if not item_class and _looks_like_live_gate(item_id=item_id, path=path):
        return "live_gate"
    if not item_class and _looks_like_design_gate(item_id=item_id, path=path):
        return "design_gate"
    if item_class == "blocked":
        return "other_open"
    if status in OPEN_STATUSES:
        return "other_open"
    return "closed"


def _looks_like_live_gate(*, item_id: str, path: str) -> bool:
    if "gate" not in path and "gate" not in item_id:
        return False
    live_markers = (
        "live",
        "telegram",
        "caldav",
        "nextcloud",
        "deploy",
        "cloudflare",
        "mcp-service",
        "observability",
        "crowdsec",
        "remediation",
        "lockdown",
        "retention",
    )
    return any(marker in item_id for marker in live_markers)


def _looks_like_design_gate(*, item_id: str, path: str) -> bool:
    if "gate" not in path and "gate" not in item_id:
        return False
    return any(marker in item_id for marker in ("ui", "design", "placement"))


def _mvp_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False, "progress_percent": None, "ui_live": False, "active_slice": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"present": True, "progress_percent": None, "ui_live": False, "active_slice": None, "error": "invalid_json"}
    roadmaps = data.get("roadmaps") or []
    progress = None
    if roadmaps:
        progress = round(sum(int(item.get("percent") or 0) for item in roadmaps) / len(roadmaps))
    active = (data.get("runner") or {}).get("active_slice")
    ui_live = bool((data.get("version_1_0_gate") or {}).get("ui_live"))
    return {"present": True, "progress_percent": progress, "ui_live": ui_live, "active_slice": active}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit open safe roadmap slices and live/design gates.")
    parser.add_argument("--plans-dir", default=str(DEFAULT_PLAN_DIR))
    parser.add_argument("--mvp-state", default=str(DEFAULT_MVP_STATE))
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    report = audit_plan_dir(Path(args.plans_dir), mvp_state_path=Path(args.mvp_state))
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
