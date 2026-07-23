"""Offline fail-closed preflight for the Memory observability live packet."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


PACKET_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKET_ROOT.parents[3]
PLAN_PATH = PACKET_ROOT / "activation-plan.json"
ACCEPTANCE_PATH = (
    REPO_ROOT
    / "docs"
    / "plans"
    / "graphrag-raptor-observability-offline-acceptance.json"
)
PROMETHEUS_ROOT = PACKET_ROOT.parent / "prometheus"
GRAFANA_ROOT = PACKET_ROOT.parent / "grafana"

SCHEMA = "odysseus.memory_observability_activation_preflight.v1"
ELIGIBLE_VERDICT = "offline_go"
BLOCKED_EXIT = 3


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.is_file():
        errors.append(f"missing:{path.name}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"invalid_json:{path.name}:{type(exc).__name__}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"json_root_not_object:{path.name}")
        return {}
    return payload


def _load_validator(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"validator import unavailable: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate_preflight() -> dict[str, Any]:
    errors: list[str] = []
    plan = _read_json(PLAN_PATH, errors)
    acceptance = _read_json(ACCEPTANCE_PATH, errors)
    try:
        prometheus = _load_validator(
            "activation_prometheus_validator", PROMETHEUS_ROOT / "validate_assets.py"
        ).validate_assets()
    except Exception as exc:  # fail closed without serializing exception content
        prometheus = {"valid": False, "errors": (type(exc).__name__,)}
    try:
        grafana = _load_validator(
            "activation_grafana_validator", GRAFANA_ROOT / "validate_assets.py"
        ).validate_assets()
    except Exception as exc:  # fail closed without serializing exception content
        grafana = {"valid": False, "errors": (type(exc).__name__,)}

    verdict = str(acceptance.get("verdict") or "missing")
    acceptance_safe = acceptance.get("safe_state")
    safe_state_closed = isinstance(acceptance_safe, dict) and all(
        value is False for value in acceptance_safe.values()
    )
    packet_shape_valid = (
        plan.get("schema") == "odysseus.memory_observability_activation_plan.v1"
        and plan.get("single_live_gate") == "GRO-LIVE-ACTIVATION"
        and plan.get("current_execution_authorized") is False
        and plan.get("safe_default")
        == "prometheus_grafana_and_productive_scrape_disabled"
    )
    gates = {
        "packet_shape_valid": packet_shape_valid,
        "offline_acceptance_present": bool(acceptance),
        "offline_acceptance_is_go": verdict == ELIGIBLE_VERDICT,
        "offline_safe_state_closed": safe_state_closed,
        "prometheus_assets_valid": prometheus.get("valid") is True,
        "grafana_assets_valid": grafana.get("valid") is True,
        "live_go_recorded": False,
        "live_identity_verified": False,
        "backup_checkpoint_created": False,
    }
    activation_eligible = all(
        gates[name]
        for name in (
            "packet_shape_valid",
            "offline_acceptance_present",
            "offline_acceptance_is_go",
            "offline_safe_state_closed",
            "prometheus_assets_valid",
            "grafana_assets_valid",
        )
    )
    blockers = []
    if errors:
        blockers.append("offline_packet_files_invalid")
    if verdict != ELIGIBLE_VERDICT:
        blockers.append(f"offline_acceptance_verdict:{verdict}")
    if not safe_state_closed:
        blockers.append("offline_safe_state_not_closed")
    if prometheus.get("valid") is not True:
        blockers.append("prometheus_assets_invalid")
    if grafana.get("valid") is not True:
        blockers.append("grafana_assets_invalid")
    if not packet_shape_valid:
        blockers.append("activation_packet_invalid")
    return {
        "schema": SCHEMA,
        "status": "eligible_for_future_live_gate" if activation_eligible else "blocked",
        "activation_eligible": activation_eligible,
        "offline_verdict": verdict,
        "gates": gates,
        "blockers": tuple(sorted(set(blockers))),
        "prometheus_asset_errors": tuple(prometheus.get("errors") or ()),
        "grafana_asset_errors": tuple(grafana.get("errors") or ()),
        "live_actions_performed": False,
        "host_reads_performed": False,
        "network_io_performed": False,
        "secrets_created": False,
        "services_started": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    parser.add_argument(
        "--require-eligible",
        action="store_true",
        help="exit 3 unless offline evidence permits the future live gate",
    )
    args = parser.parse_args(argv)
    report = evaluate_preflight()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["activation_eligible"]:
        print("ACTIVATION_PREFLIGHT_ELIGIBLE")
    else:
        print("ACTIVATION_PREFLIGHT_BLOCKED " + " ".join(report["blockers"]))
    if args.require_eligible and not report["activation_eligible"]:
        return BLOCKED_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
