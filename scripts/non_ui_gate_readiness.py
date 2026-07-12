"""Build a redacted readiness view for non-UI roadmap gate packets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.roadmap_safe_queue_audit import DEFAULT_MVP_STATE, DEFAULT_PLAN_DIR, audit_plan_dir
from src.live_affordance_readiness import build_live_affordance_readiness


NON_UI_GATE_READINESS_SCHEMA = "odysseus.non_ui_gate_readiness.v1"

FAMILY_RUNTIME_ACTIONS: dict[str, tuple[str, ...]] = {
    "calendar_reminders": ("telegram_delivery",),
    "autonomous_coding": ("sandbox_execution", "telegram_delivery"),
}


def build_non_ui_gate_readiness(
    *,
    plan_dir: Path = DEFAULT_PLAN_DIR,
    mvp_state_path: Path = DEFAULT_MVP_STATE,
    env: Mapping[str, str] | None = None,
    tool_lookup: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    audit = audit_plan_dir(plan_dir, mvp_state_path=mvp_state_path)
    live = build_live_affordance_readiness(env=env, tool_lookup=tool_lookup)
    live_by_id = {action["action_id"]: action for action in live["actions"]}
    families = tuple(
        _family_readiness(packet, live_by_id)
        for packet in audit.get("non_ui_decision_packets", ())
    )
    next_operator_action = _next_operator_action(families)
    return {
        "schema": NON_UI_GATE_READINESS_SCHEMA,
        "status": "ready" if families and all(item["can_execute_now"] for item in families) else "blocked",
        "safe_open_count": audit["safe_open_count"],
        "other_open_count": audit["other_open_count"],
        "queue_exhausted": audit["queue_exhausted"],
        "non_ui_decision_packet_count": audit.get("non_ui_decision_packet_count", len(families)),
        "excluded_design_decision_packet_count": audit.get("excluded_design_decision_packet_count", 0),
        "recommended_next_operator_action": next_operator_action,
        "families": families,
        "live_affordance_status": live["status"],
        "live_execution_performed": live["live_execution_performed"],
        "network_probe_performed": live["network_probe_performed"],
        "telegram_send_performed": live["telegram_send_performed"],
        "nextcloud_write_performed": live["nextcloud_write_performed"],
        "sandbox_execution_performed": live["sandbox_execution_performed"],
        "converter_process_started": live["converter_process_started"],
        "tokens_visible": live["tokens_visible"],
        "chat_ids_visible": live["chat_ids_visible"],
        "host_paths_visible": live["host_paths_visible"],
        "raw_content_visible": live["raw_content_visible"],
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
    next_action = payload.get("recommended_next_operator_action") or {}
    unsafe_flags = [
        key
        for key in (
            "live_execution_performed",
            "network_probe_performed",
            "telegram_send_performed",
            "nextcloud_write_performed",
            "sandbox_execution_performed",
            "converter_process_started",
            "tokens_visible",
            "chat_ids_visible",
            "host_paths_visible",
            "raw_content_visible",
        )
        if payload.get(key)
    ]
    lines = [
        "# Non-UI Gate Readiness",
        "",
        f"Status: {payload.get('status')}",
        f"Queue exhausted: {'yes' if payload.get('queue_exhausted') else 'no'}",
        f"Non-UI packets: {payload.get('non_ui_decision_packet_count')}",
        f"Excluded UI/design packets: {payload.get('excluded_design_decision_packet_count')}",
        f"Live affordance status: {payload.get('live_affordance_status')}",
        f"Executable now: {sum(1 for item in payload.get('families') or () if item.get('can_execute_now'))}",
        f"Unsafe evidence flags: {', '.join(unsafe_flags) if unsafe_flags else 'none'}",
        "",
        "## Recommended Next Operator Action",
        "",
    ]
    if next_action:
        lines.extend(
            [
                f"- Family: {next_action.get('family')}",
                f"- Status: {next_action.get('status')}",
                f"- Reason: {next_action.get('reason')}",
                f"- Can execute now: {'yes' if next_action.get('can_execute_now') else 'no'}",
                f"- Safe default: `{next_action.get('safe_default')}`",
                f"- Go phrase: `{next_action.get('go_phrase')}`",
                f"- Next step: {next_action.get('next_step')}",
                "- Missing runtime gates:",
            ]
        )
        for value in tuple(next_action.get("missing_runtime_gates") or ()):
            lines.append(f"  - {value}")
        lines.append("- Required inputs:")
        for value in tuple(next_action.get("required_inputs") or ()):
            lines.append(f"  - {value}")
        lines.append("- Evidence required:")
        for value in tuple(next_action.get("evidence_required") or ()):
            lines.append(f"  - {value}")
        lines.append("- Forbidden until Go:")
        for value in tuple(next_action.get("forbidden_until_go") or ()):
            lines.append(f"  - {value}")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Non-UI Families",
            "",
            "| Family | Status | Can Execute Now | Missing Runtime Gates | Execution Blockers | Safe Default |",
            "| - | - | - | - | - | - |",
        ]
    )
    for item in tuple(payload.get("families") or ()):
        lines.append(
            f"| {item.get('family')} | {item.get('status')} | "
            f"{'yes' if item.get('can_execute_now') else 'no'} | "
            f"{'; '.join(tuple(item.get('missing_runtime_gates') or ())) or '-'} | "
            f"{'; '.join(tuple(item.get('execution_blockers') or ())) or '-'} | "
            f"{item.get('safe_default')} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _family_readiness(packet: Mapping[str, Any], live_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    family = str(packet["family"])
    runtime_action_ids = FAMILY_RUNTIME_ACTIONS.get(family, ())
    runtime_actions = tuple(live_by_id[action_id] for action_id in runtime_action_ids if action_id in live_by_id)
    missing_runtime_gates = _unique(
        gap
        for action in runtime_actions
        for gap in tuple(action.get("readiness_gap_names") or ())
    )
    blocked_live_actions = _unique(
        action_name
        for action in runtime_actions
        for action_name in tuple(action.get("blocked_live_actions") or ())
    )
    runtime_ready = bool(runtime_actions) and not missing_runtime_gates
    required_inputs = tuple(packet.get("required_inputs") or ())
    execution_blockers = []
    if required_inputs:
        execution_blockers.append("operator_go_package_inputs_required")
    if missing_runtime_gates:
        execution_blockers.append("runtime_readiness_gates_blocked")
    if not runtime_action_ids:
        execution_blockers.append("no_runtime_probe_for_family")
    return {
        "family": family,
        "status": "blocked" if execution_blockers else "ready",
        "can_execute_now": False if execution_blockers else runtime_ready,
        "gate_ids": tuple(packet.get("gate_ids") or ()),
        "go_phrase": packet.get("go_phrase"),
        "safe_default": packet.get("safe_default"),
        "required_inputs": required_inputs,
        "evidence_required": tuple(packet.get("evidence_required") or ()),
        "forbidden_until_go": tuple(packet.get("forbidden_until_go") or ()),
        "runtime_action_ids": runtime_action_ids,
        "runtime_ready": runtime_ready,
        "missing_runtime_gates": missing_runtime_gates,
        "blocked_live_actions": blocked_live_actions,
        "execution_blockers": tuple(execution_blockers),
        "values_visible": False,
    }


def _next_operator_action(families: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    if not families:
        return None
    candidates = [family for family in families if family["runtime_action_ids"]]
    family = next((item for item in candidates if not item["can_execute_now"]), None)
    if family is None:
        family = next((item for item in families if not item["can_execute_now"]), families[0])
    if family["missing_runtime_gates"]:
        reason = "runtime_readiness_gates_blocked"
        next_step = "Configure the missing runtime gates, then rerun non_ui_gate_readiness before any live action."
    elif family["required_inputs"]:
        reason = "operator_go_package_inputs_required"
        next_step = "Provide the required bounded inputs and evidence path, then rerun non_ui_gate_readiness."
    else:
        reason = "ready_for_bounded_execution"
        next_step = "Execute only the bounded action covered by the Go phrase and evidence requirements."
    return {
        "family": family["family"],
        "status": family["status"],
        "can_execute_now": family["can_execute_now"],
        "go_phrase": family["go_phrase"],
        "safe_default": family["safe_default"],
        "reason": reason,
        "next_step": next_step,
        "required_inputs": family["required_inputs"],
        "missing_runtime_gates": family["missing_runtime_gates"],
        "blocked_live_actions": family["blocked_live_actions"],
        "evidence_required": family["evidence_required"],
        "forbidden_until_go": family["forbidden_until_go"],
        "values_visible": False,
    }


def _unique(values) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = str(value)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _load_env_file(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    try:
        from dotenv import dotenv_values

        return {key: str(value or "") for key, value in dotenv_values(path).items()}
    except Exception:
        result: dict[str, str] = {}
        if not path.exists():
            return result
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build redacted non-UI gate readiness.")
    parser.add_argument("--plans-dir", default=str(DEFAULT_PLAN_DIR))
    parser.add_argument("--mvp-state", default=str(DEFAULT_MVP_STATE))
    parser.add_argument("--env-file", default="")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    env_path = Path(args.env_file) if args.env_file else None
    payload = build_non_ui_gate_readiness(
        plan_dir=Path(args.plans_dir),
        mvp_state_path=Path(args.mvp_state),
        env=_load_env_file(env_path),
    )
    if args.format == "markdown":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
