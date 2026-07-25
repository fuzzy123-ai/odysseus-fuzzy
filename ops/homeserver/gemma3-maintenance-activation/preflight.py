"""Offline fail-closed preflight for the GMI-15 activation packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PACKET_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKET_ROOT.parents[2]
PLAN_PATH = PACKET_ROOT / "activation-plan.json"
GMI_ACCEPTANCE_PATH = (
    REPO_ROOT / "docs" / "plans" / "gemma3-memory-ops-offline-acceptance.json"
)
GRO_ACCEPTANCE_PATH = (
    REPO_ROOT
    / "docs"
    / "plans"
    / "graphrag-raptor-observability-offline-acceptance.json"
)
GRO_PLAN_PATH = (
    REPO_ROOT
    / "ops"
    / "homeserver"
    / "observability-podman"
    / "activation"
    / "activation-plan.json"
)

SCHEMA = "odysseus.gemma3_maintenance_activation_preflight.v1"
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


def _safe_state_closed(payload: dict[str, Any]) -> bool:
    safe_state = payload.get("safe_state")
    return isinstance(safe_state, dict) and bool(safe_state) and all(
        value is False for value in safe_state.values()
    )


def _canonical_text_sha256(content: bytes) -> str:
    """Hash versioned text identically on LF and Windows CRLF worktrees."""
    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest().upper()


def _hash_manifest_matches(payload: dict[str, Any], errors: list[str]) -> bool:
    manifest = payload.get("hash_manifest")
    if not isinstance(manifest, dict) or not manifest:
        errors.append("gmi_hash_manifest_missing")
        return False
    matches = True
    for relative_path, expected in manifest.items():
        if not isinstance(relative_path, str) or not isinstance(expected, str):
            matches = False
            continue
        path = REPO_ROOT / relative_path
        try:
            observed = _canonical_text_sha256(path.read_bytes())
        except OSError:
            observed = ""
        if observed != expected.upper():
            matches = False
    if not matches:
        errors.append("gmi_hash_manifest_mismatch")
    return matches


def evaluate_preflight() -> dict[str, Any]:
    errors: list[str] = []
    plan = _read_json(PLAN_PATH, errors)
    gmi = _read_json(GMI_ACCEPTANCE_PATH, errors)
    gro = _read_json(GRO_ACCEPTANCE_PATH, errors)
    gro_plan = _read_json(GRO_PLAN_PATH, errors)

    gmi_hashes_match = _hash_manifest_matches(gmi, errors) if gmi else False
    packet_shape_valid = (
        plan.get("schema") == "odysseus.gemma3_maintenance_activation_plan.v1"
        and plan.get("single_live_gate") == "GMI-LIVE-ACTIVATION"
        and plan.get("single_go_phrase") == "GO GMI-LIVE-ACTIVATION"
        and plan.get("current_execution_authorized") is False
        and plan.get("safe_default") == "gemma3_4b_maintenance_runtime_disabled"
    )
    gates = {
        "packet_shape_valid": packet_shape_valid,
        "gmi_offline_acceptance_is_go": gmi.get("verdict") == "offline_go",
        "gmi_safe_state_closed": _safe_state_closed(gmi),
        "gmi_hash_manifest_matches": gmi_hashes_match,
        "gro_offline_acceptance_is_go": gro.get("verdict") == "offline_go",
        "gro_safe_state_closed": _safe_state_closed(gro),
        "gro_packet_repo_eligible": (
            gro_plan.get("status") == "eligible_awaiting_live_go"
            and gro_plan.get("current_execution_authorized") is False
        ),
        "gro_live_validation_recorded": False,
        "gmi_live_go_recorded": False,
        "host_identity_verified": False,
        "backup_checkpoint_created": False,
        "real_model_canary_complete": False,
        "runtime_activation_applied": False,
    }
    offline_gate_names = (
        "packet_shape_valid",
        "gmi_offline_acceptance_is_go",
        "gmi_safe_state_closed",
        "gmi_hash_manifest_matches",
        "gro_offline_acceptance_is_go",
        "gro_safe_state_closed",
        "gro_packet_repo_eligible",
    )
    packet_ready = all(gates[name] for name in offline_gate_names) and not errors
    offline_blockers: list[str] = []
    if errors:
        offline_blockers.extend(errors)
    for name in offline_gate_names:
        if not gates[name]:
            offline_blockers.append(name)
    live_blockers = (
        "gmi_live_go_not_recorded",
        "gro_live_validation_not_recorded",
    )
    return {
        "schema": SCHEMA,
        "status": (
            "packet_ready_awaiting_gro_live_validation_and_gmi_live_go"
            if packet_ready
            else "offline_packet_blocked"
        ),
        "packet_ready": packet_ready,
        "live_execution_eligible": False,
        "gates": gates,
        "offline_blockers": tuple(sorted(set(offline_blockers))),
        "live_blockers": live_blockers,
        "live_actions_performed": False,
        "host_reads_performed": False,
        "network_io_performed": False,
        "model_calls_performed": False,
        "deploy_performed": False,
        "secrets_created": False,
        "services_changed": False,
        "runtime_setting_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--require-packet-ready",
        action="store_true",
        help="exit 3 unless all repository/offline barriers are green",
    )
    parser.add_argument(
        "--require-live-eligible",
        action="store_true",
        help="exit 3 unless live GRO evidence and the exact GMI Go are recorded",
    )
    args = parser.parse_args(argv)
    report = evaluate_preflight()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["packet_ready"]:
        print("GMI_ACTIVATION_PACKET_READY_LIVE_PREREQUISITES_MISSING")
    else:
        print("GMI_ACTIVATION_PACKET_BLOCKED " + " ".join(report["offline_blockers"]))
    if args.require_packet_ready and not report["packet_ready"]:
        return BLOCKED_EXIT
    if args.require_live_eligible and not report["live_execution_eligible"]:
        return BLOCKED_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
