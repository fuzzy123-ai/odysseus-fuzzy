"""Validate and summarize the MVP roadmap runner state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "docs" / "plans" / "mvp-roadmap-runner-state.json"


RUNNABLE_WITHOUT_LIVE = {"safe_offline", "repo_only"}


class RunnerStateError(ValueError):
    pass


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    validate_state(state)
    return state


def validate_state(state: dict[str, Any]) -> None:
    roadmaps = state.get("roadmaps")
    if not isinstance(roadmaps, list) or len(roadmaps) != 10:
        raise RunnerStateError("runner state must contain exactly ten roadmaps")

    numbers = [item.get("number") for item in roadmaps]
    if numbers != list(range(1, 11)):
        raise RunnerStateError("roadmaps must be numbered 1 through 10")

    for item in roadmaps:
        percent = item.get("percent")
        if not isinstance(percent, int) or not 0 <= percent <= 100:
            raise RunnerStateError(f"roadmap {item.get('number')} has invalid percent")
        if item.get("number") <= 10 and not item.get("why_not_100"):
            raise RunnerStateError(f"roadmap {item.get('number')} is missing why_not_100")

    if state.get("push_remote") == "origin":
        raise RunnerStateError("origin must not be the push remote")
    if "origin" not in state.get("forbidden_remotes", []):
        raise RunnerStateError("origin must be listed as a forbidden remote")


def overall_progress(state: dict[str, Any]) -> int:
    roadmaps = state["roadmaps"]
    return round(sum(item["percent"] for item in roadmaps) / len(roadmaps))


def slice_is_runnable(state: dict[str, Any], item: dict[str, Any]) -> bool:
    slice_class = item.get("class")
    if slice_class in RUNNABLE_WITHOUT_LIVE:
        return True
    if slice_class != "needs_live_go":
        return False

    ledger = state.get("live_go_ledger", {})
    required = item.get("required_live_go") or []
    return all(bool(ledger.get(flag)) for flag in required)


def select_next_step(state: dict[str, Any]) -> dict[str, Any]:
    first_blocked: dict[str, Any] | None = None
    for roadmap in state["roadmaps"]:
        if roadmap["percent"] >= 100:
            continue
        for item in roadmap.get("next_slices", []):
            if item.get("status") not in {"open", "running"}:
                if first_blocked is None and item.get("status") == "blocked":
                    first_blocked = {"roadmap": roadmap, "slice": item, "runnable": False}
                continue
            candidate = {"roadmap": roadmap, "slice": item, "runnable": slice_is_runnable(state, item)}
            if candidate["runnable"]:
                return candidate
            if first_blocked is None:
                first_blocked = candidate
    if first_blocked is not None:
        return first_blocked
    return {"roadmap": None, "slice": None, "runnable": False}


def render_report(state: dict[str, Any]) -> str:
    next_step = select_next_step(state)
    roadmap = next_step["roadmap"]
    item = next_step["slice"]
    ui_live = "ja" if state["version_1_0_gate"].get("ui_live") else "nein"

    if roadmap and item:
        active = f"R{roadmap['number']} {item['id']}"
        result = "ready" if next_step["runnable"] else item.get("status", "blocked")
        why = item.get("blocker") or roadmap.get("why_not_100") or "No blocker recorded."
        next_label = active if next_step["runnable"] else _next_open_label(state, after=roadmap["number"])
    else:
        active = "none"
        result = "queue_exhausted"
        why = "No open runner slice remains in the current state."
        next_label = "none"

    lines = [
        f"MVP-Gesamtfortschritt: {overall_progress(state)}%",
        f"Version-1.0-Gate: UI live? {ui_live}",
        f"Aktiver Runner-Schritt: {active}",
        f"Ergebnis: {result}",
        f"Warum: {why}",
        "Fortschritt geaendert: nein",
        f"Naechster Schritt: {next_label}",
        "",
        "Roadmap-Fortschritt:",
        "| # | Roadmap | % | Warum nicht 100% |",
        "| - | - | -: | - |",
    ]
    for roadmap_item in state["roadmaps"]:
        lines.append(
            f"| {roadmap_item['number']} | {roadmap_item['name']} | "
            f"{roadmap_item['percent']} | {roadmap_item['why_not_100']} |"
        )

    decision = _recommended_decision(state)
    lines.extend(["", "Recommended next human decision:", f"- {decision}"])
    return "\n".join(lines)


def _next_open_label(state: dict[str, Any], after: int) -> str:
    for roadmap in state["roadmaps"]:
        if roadmap["number"] < after or roadmap["percent"] >= 100:
            continue
        for item in roadmap.get("next_slices", []):
            if item.get("status") in {"open", "running"}:
                return f"R{roadmap['number']} {item['id']}"
    return "none"


def _recommended_decision(state: dict[str, Any]) -> str:
    gates = state.get("gate_queue") or []
    if gates:
        gate = gates[0]
        return f"{gate['id']}: {gate['decision_needed']}"
    return "No human decision is currently recorded."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--report", action="store_true", help="print the runner report")
    args = parser.parse_args()

    state = load_state(args.state)
    if args.report:
        print(render_report(state))
    else:
        print(f"valid: {overall_progress(state)}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
