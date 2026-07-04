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
    "resolved_with_live_evidence",
    "resolved_with_synthetic_live_evidence",
    "done_in_this_slice",
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
META_ROUTER_STATUSES = {"waiting_for_operator_choice"}

DECISION_FAMILIES = (
    (
        "version_release",
        10,
        (
            "version-1",
            "release",
            "deploy",
            "cloudflare",
            "r8-release",
        ),
    ),
    (
        "calendar_reminders",
        20,
        (
            "telegram-reminder",
            "caldav",
            "calendar",
            "reminder",
            "cal-mcp",
        ),
    ),
    (
        "autonomous_coding",
        30,
        (
            "autonomous-coding",
            "workstation",
            "network-allowlist",
            "mcp-service",
            "acpr",
        ),
    ),
    (
        "observability_ops",
        40,
        (
            "observability",
            "debian-observability",
            "grafana",
            "log-retention",
            "mcp-debug-server",
            "ulog",
            "obs-",
        ),
    ),
    (
        "security_ops",
        50,
        (
            "security-incident",
            "crowdsec",
            "lockdown",
            "remediation",
            "sir-",
        ),
    ),
    (
        "ui_design",
        60,
        (
            "ui",
            "design",
            "placement",
            "r6-lens",
            "r7-browser",
        ),
    ),
)
DEFAULT_DECISION_FAMILY = "other_gate"
DEFAULT_DECISION_PRIORITY = 90

DECISION_PACKET_TEXT = {
    "version_release": {
        "decision_needed": "Decide whether to run bounded release/deploy readiness evidence or defer Version 1.0 until UI live.",
        "safe_default": "defer_release_live_actions",
        "go_phrase": "GO version_release bounded evidence",
    },
    "calendar_reminders": {
        "decision_needed": "Choose one bounded live reminder path: Telegram reminder smoke or CalDAV writeback smoke.",
        "safe_default": "keep_reminders_repo_ready_no_live_send",
        "go_phrase": "GO calendar_reminders bounded smoke",
    },
    "autonomous_coding": {
        "decision_needed": "Choose one bounded autonomous-coding live control path: workstation-to-Telegram smoke, MCP service availability, or network allowlist.",
        "safe_default": "keep_autonomous_coding_dry_run",
        "go_phrase": "GO autonomous_coding bounded smoke",
    },
    "observability_ops": {
        "decision_needed": "Choose whether to run Debian observability inventory/setup, Grafana exposure, or log-retention evidence.",
        "safe_default": "keep_observability_contracts_repo_only",
        "go_phrase": "GO observability_ops bounded inventory",
    },
    "security_ops": {
        "decision_needed": "Choose whether to run tabletop evidence or prepare explicit remediation/lockdown actions.",
        "safe_default": "keep_security_actions_prepare_only",
        "go_phrase": "GO security_ops bounded tabletop",
    },
    "ui_design": {
        "decision_needed": "Hand UI placement and Version 1.0 UI-live evidence to the UI owner.",
        "safe_default": "do_not_edit_ui_from_backend_abc",
        "go_phrase": "GO ui_design handoff",
    },
    "other_gate": {
        "decision_needed": "Review uncategorized gates and either classify them or approve a bounded next action.",
        "safe_default": "defer_uncategorized_gate",
        "go_phrase": "GO other_gate bounded review",
    },
}


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
    recommended_decisions = _recommended_decisions(live_gates, design_gates)
    return {
        "schema_version": 1,
        "files_scanned": files_scanned,
        "safe_open_count": len(safe_open),
        "live_gate_count": len(live_gates),
        "unique_live_gate_count": len(_gate_groups(live_gates)),
        "design_gate_count": len(design_gates),
        "unique_design_gate_count": len(_gate_groups(design_gates)),
        "other_open_count": len(other_open),
        "mvp": mvp,
        "queue_exhausted": len(safe_open) == 0 and mvp.get("active_slice") in (None, "none", ""),
        "safe_open_slices": [item.to_dict() for item in safe_open],
        "live_gates": [item.to_dict() for item in live_gates],
        "live_gate_groups": _gate_groups(live_gates),
        "design_gates": [item.to_dict() for item in design_gates],
        "design_gate_groups": _gate_groups(design_gates),
        "recommended_decisions": recommended_decisions,
        "decision_packets": _decision_packets(recommended_decisions),
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
        f"Unique live gate ids: {report['unique_live_gate_count']}",
        f"Design gates: {report['design_gate_count']}",
        f"Unique design gate ids: {report['unique_design_gate_count']}",
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
    if report["live_gate_groups"] or report["design_gate_groups"]:
        lines.extend(["## Unique Gate Decisions", "", "| Type | ID | Entries | Files |", "| - | - | -: | - |"])
        for item in report["live_gate_groups"]:
            lines.append(
                f"| live | {item['id']} | {item['entry_count']} | {', '.join(item['files'])} |"
            )
        for item in report["design_gate_groups"]:
            lines.append(
                f"| design | {item['id']} | {item['entry_count']} | {', '.join(item['files'])} |"
            )
        lines.append("")
    if report.get("recommended_decisions"):
        lines.extend(
            [
                "## Recommended Next Decisions",
                "",
                "| Priority | Family | Unique Gates | Entry Count | Gate IDs |",
                "| -: | - | -: | -: | - |",
            ]
        )
        for item in report["recommended_decisions"]:
            lines.append(
                f"| {item['priority']} | {item['family']} | {item['unique_gate_count']} | "
                f"{item['entry_count']} | {', '.join(item['gate_ids'])} |"
            )
        lines.append("")
    if report.get("decision_packets"):
        lines.extend(
            [
                "## Operator Decision Packets",
                "",
                "| Priority | Family | Decision Needed | Safe Default | Go Phrase |",
                "| -: | - | - | - | - |",
            ]
        )
        for item in report["decision_packets"]:
            lines.append(
                f"| {item['priority']} | {item['family']} | {item['decision_needed']} | "
                f"{item['safe_default']} | {item['go_phrase']} |"
            )
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
    if _is_meta_router_item(status=status, path=path):
        return "closed"
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


def _is_meta_router_item(*, status: str, path: str) -> bool:
    """Ignore roadmap-internal router waits that merely point to real gates."""

    return status in META_ROUTER_STATUSES and "/abc_execution_queue" in path


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
        "mcp-debug",
        "observability",
        "security-incident",
        "tabletop",
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


def _gate_groups(items: Iterable[RoadmapAuditItem]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        group = grouped.setdefault(
            item.item_id,
            {"id": item.item_id, "entry_count": 0, "files": set(), "statuses": set()},
        )
        group["entry_count"] += 1
        group["files"].add(item.file)
        if item.status:
            group["statuses"].add(item.status)
    result: list[dict[str, Any]] = []
    for item_id in sorted(grouped):
        group = grouped[item_id]
        result.append(
            {
                "id": group["id"],
                "entry_count": group["entry_count"],
                "files": sorted(group["files"]),
                "statuses": sorted(group["statuses"]),
            }
        )
    return result


def _decision_family(gate_id: str) -> tuple[str, int]:
    lowered = gate_id.lower()
    for family, priority, markers in DECISION_FAMILIES:
        if any(marker in lowered for marker in markers):
            return family, priority
    return DEFAULT_DECISION_FAMILY, DEFAULT_DECISION_PRIORITY


def _recommended_decisions(
    live_gates: Iterable[RoadmapAuditItem], design_gates: Iterable[RoadmapAuditItem]
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    all_groups = _gate_groups(live_gates) + _gate_groups(design_gates)
    for gate in all_groups:
        family, priority = _decision_family(gate["id"])
        item = grouped.setdefault(
            family,
            {
                "family": family,
                "priority": priority,
                "unique_gate_count": 0,
                "entry_count": 0,
                "gate_ids": [],
                "files": set(),
            },
        )
        item["priority"] = min(item["priority"], priority)
        item["unique_gate_count"] += 1
        item["entry_count"] += gate["entry_count"]
        item["gate_ids"].append(gate["id"])
        item["files"].update(gate["files"])

    result: list[dict[str, Any]] = []
    for item in grouped.values():
        result.append(
            {
                "family": item["family"],
                "priority": item["priority"],
                "unique_gate_count": item["unique_gate_count"],
                "entry_count": item["entry_count"],
                "gate_ids": sorted(item["gate_ids"]),
                "files": sorted(item["files"]),
            }
        )
    return sorted(result, key=lambda item: (item["priority"], item["family"]))


def _decision_packets(recommended_decisions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for decision in recommended_decisions:
        family = decision["family"]
        text = DECISION_PACKET_TEXT.get(family, DECISION_PACKET_TEXT[DEFAULT_DECISION_FAMILY])
        packets.append(
            {
                "family": family,
                "priority": decision["priority"],
                "unique_gate_count": decision["unique_gate_count"],
                "entry_count": decision["entry_count"],
                "gate_ids": decision["gate_ids"],
                "decision_needed": text["decision_needed"],
                "safe_default": text["safe_default"],
                "go_phrase": text["go_phrase"],
            }
        )
    return packets


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
