"""Deterministic offline validator for the GRO-14 activation packet."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
from typing import Any, Mapping


PACKET_ROOT = Path(__file__).resolve().parent
PLAN_PATH = PACKET_ROOT / "activation-plan.json"
RUNBOOK_PATH = PACKET_ROOT / "LIVE_RUNBOOK.md"
README_PATH = PACKET_ROOT / "README.md"
INPUTS_PATH = PACKET_ROOT / "templates" / "live-inputs.env.example"
SOAK_PATH = PACKET_ROOT / "templates" / "soak-evidence.template.json"
PREFLIGHT_PATH = PACKET_ROOT / "preflight.py"

SCHEMA = "odysseus.memory_observability_activation_packet_validation.v1"
EXPECTED_PHASES = (
    "offline_barrier",
    "live_identity_readback",
    "capacity_and_port_preflight",
    "backup_checkpoint",
    "default_off_staging",
    "scoped_secret_creation",
    "staged_validation",
    "private_activation",
    "functional_verification",
    "bounded_soak",
    "finalize_or_rollback",
)
EXPECTED_ROLLBACK = (
    "remove_activation_markers",
    "stop_grafana_user_unit",
    "stop_prometheus_user_unit",
    "verify_loopback_ports_closed",
    "revoke_scoped_api_token_by_recorded_id",
    "remove_untracked_secret_and_environment_files",
    "restore_or_remove_staged_user_units",
    "daemon_reload_user_systemd",
    "verify_odysseus_health_unchanged",
    "retain_versioned_volumes_for_forensics",
)
EXPECTED_INPUTS = (
    "EXPECTED_SSH_ALIAS",
    "EXPECTED_USER",
    "EXPECTED_HOSTNAME",
    "ODYSSEUS_ROOT",
    "EXPECTED_COMMIT",
    "ODYSSEUS_CONTAINER",
    "GRAFANA_ADMIN_USER",
    "PROMETHEUS_URL",
    "RESTIC_PASSWORD_FILE",
    "SOAK_HOURS",
)
PRIVATE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/](?:Users|Documents|Desktop)[\\/]", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_.-]+/"),
)
PRIVATE_NETWORK_PATTERN = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
SECRET_VALUE_PATTERN = re.compile(
    r"(?:ody_[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9_+/.=-]{12,}|"
    r"(?:PASSWORD|TOKEN|SECRET)\s*=\s*[^\s$<][^\s]{11,})",
    re.IGNORECASE,
)


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.is_file():
        errors.append(f"missing:{path.relative_to(PACKET_ROOT)}")
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


def _load_preflight():
    spec = importlib.util.spec_from_file_location("activation_packet_preflight", PREFLIGHT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("preflight import unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _validate_plan(plan: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    if plan.get("schema") != "odysseus.memory_observability_activation_plan.v1":
        errors.append("plan:schema_mismatch")
    if plan.get("status") != "eligible_awaiting_live_go":
        errors.append("plan:current_status_must_await_live_go")
    if plan.get("single_live_gate") != "GRO-LIVE-ACTIVATION":
        errors.append("plan:single_live_gate_mismatch")
    if plan.get("single_go_phrase") != "GO GRO-LIVE-ACTIVATION":
        errors.append("plan:single_go_phrase_mismatch")
    if plan.get("current_execution_authorized") is not False:
        errors.append("plan:execution_must_be_unauthorized")
    barrier = _mapping(plan.get("offline_barrier"))
    if barrier.get("required_verdict") != "offline_go":
        errors.append("plan:required_verdict_mismatch")
    if barrier.get("observed_verdict") != "offline_go":
        errors.append("plan:observed_verdict_must_be_offline_go")
    if barrier.get("on_mismatch") != "stop_before_live_read_or_mutation":
        errors.append("plan:no_go_barrier_not_fail_closed")
    if tuple(str(value) for value in _sequence(plan.get("required_live_inputs"))) != EXPECTED_INPUTS:
        errors.append("plan:required_live_inputs_mismatch")

    phase_order = tuple(str(value) for value in _sequence(plan.get("phase_order")))
    phases = _sequence(plan.get("phases"))
    phase_ids = tuple(str(_mapping(phase).get("id") or "") for phase in phases)
    if phase_order != EXPECTED_PHASES or phase_ids != EXPECTED_PHASES:
        errors.append("plan:phase_order_mismatch")
    for phase_value in phases:
        phase = _mapping(phase_value)
        if not _sequence(phase.get("required_evidence")):
            errors.append(f"plan:phase_without_evidence:{phase.get('id')}")
        if phase.get("mutates") is True and phase.get("id") not in {
            "backup_checkpoint",
            "default_off_staging",
            "scoped_secret_creation",
            "private_activation",
            "finalize_or_rollback",
        }:
            errors.append(f"plan:unexpected_mutating_phase:{phase.get('id')}")
        if phase.get("mutates") is True and not phase.get("rollback_ref"):
            errors.append(f"plan:mutating_phase_without_rollback:{phase.get('id')}")
        if phase.get("failure_action") not in {"stop", "rollback"}:
            errors.append(f"plan:failure_action_invalid:{phase.get('id')}")

    invariants = _mapping(plan.get("invariants"))
    required_false = (
        "odysseus_runtime_restart_allowed",
        "productive_rebuild_allowed",
        "public_binding_allowed",
        "host_network_allowed",
        "privileged_container_allowed",
        "remote_write_allowed",
        "external_alert_delivery_allowed",
        "destructive_volume_rollback_allowed",
    )
    for key in required_false:
        if invariants.get(key) is not False:
            errors.append(f"plan:unsafe_invariant:{key}")
    expected_scalars = {
        "prometheus_retention_days": 30,
        "prometheus_retention_size_gb": 5,
        "scrape_interval_seconds": 15,
        "scrape_timeout_seconds": 5,
        "soak_min_hours": 12,
        "soak_max_hours": 24,
    }
    for key, value in expected_scalars.items():
        if invariants.get(key) != value:
            errors.append(f"plan:invariant_mismatch:{key}")
    if _sequence(invariants.get("scrape_token_scopes")) != ["observability:read"]:
        errors.append("plan:token_scope_not_exact")

    rollback = _mapping(plan.get("rollback"))
    if tuple(str(value) for value in _sequence(rollback.get("ordered_steps"))) != EXPECTED_ROLLBACK:
        errors.append("plan:rollback_order_mismatch")
    if rollback.get("volume_policy") != "retain_unless_separately_approved_for_destruction":
        errors.append("plan:volume_policy_not_safe")
    if rollback.get("odysseus_restart") is not False:
        errors.append("plan:odysseus_restart_forbidden")
    if len(_sequence(rollback.get("automatic_triggers"))) < 8:
        errors.append("plan:rollback_triggers_incomplete")
    return {
        "phase_count": len(phases),
        "mutating_phase_count": sum(
            1 for phase in phases if _mapping(phase).get("mutates") is True
        ),
        "rollback_step_count": len(_sequence(rollback.get("ordered_steps"))),
        "automatic_trigger_count": len(_sequence(rollback.get("automatic_triggers"))),
    }


def _validate_templates(soak: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    if not INPUTS_PATH.is_file():
        errors.append("missing:templates/live-inputs.env.example")
        inputs: dict[str, str] = {}
    else:
        inputs = {}
        for line in INPUTS_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, separator, value = stripped.partition("=")
            if not separator:
                errors.append("inputs:invalid_line")
                continue
            inputs[key] = value
    if tuple(inputs) != EXPECTED_INPUTS:
        errors.append("inputs:key_order_or_set_mismatch")
    for key, value in inputs.items():
        if key == "SOAK_HOURS":
            if value != "12":
                errors.append("inputs:soak_default_mismatch")
        elif value:
            errors.append(f"inputs:live_value_must_be_blank:{key}")
    if any(key in inputs for key in ("TOKEN", "PASSWORD", "SECRET")):
        errors.append("inputs:secret_value_field_forbidden")

    if soak.get("schema") != "odysseus.memory_observability_live_soak.v1":
        errors.append("soak:schema_mismatch")
    if soak.get("duration_hours") != 12 or soak.get("sample_interval_seconds") != 15:
        errors.append("soak:duration_contract_mismatch")
    if soak.get("expected_sample_count") != 2880:
        errors.append("soak:sample_count_mismatch")
    if soak.get("verdict") != "not_run" or soak.get("rollback_performed") is not False:
        errors.append("soak:template_must_be_not_run")
    for key in (
        "secret_values_present",
        "raw_metric_payload_present",
        "raw_log_content_present",
    ):
        if soak.get(key) is not False:
            errors.append(f"soak:unsafe_template_flag:{key}")
    return {"input_count": len(inputs), "soak_expected_samples": soak.get("expected_sample_count")}


def _validate_runbook(errors: list[str]) -> dict[str, Any]:
    if not RUNBOOK_PATH.is_file():
        errors.append("missing:LIVE_RUNBOOK.md")
        return {}
    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    required = (
        "preflight.py --require-eligible --json",
        "offline_acceptance_verdict:offline_go",
        "GO GRO-LIVE-ACTIVATION",
        "pre-update-snapshot.sh",
        'profile=observability_readonly',
        "promtool test rules",
        "127.0.0.1:9090",
        "127.0.0.1:3000",
        "12–24-hour soak",
        "RB-01",
        "RB-02",
        "RB-03",
        "RB-04",
        "RB-ALL",
        "Retain both versioned volumes",
    )
    for value in required:
        if value not in text:
            errors.append(f"runbook:required_contract_missing:{value}")
    if text.find("preflight.py --require-eligible --json") > text.find("export GRO_LIVE_APPROVAL"):
        errors.append("runbook:go_materialized_before_offline_barrier")
    for forbidden in ("systemctl --user enable", "--privileged", "--network=host"):
        if forbidden in text:
            errors.append(f"runbook:forbidden_command:{forbidden}")
    if "never deletes a volume" not in text:
        errors.append("runbook:volume_retention_statement_missing")
    return {
        "has_single_go": text.count("GO GRO-LIVE-ACTIVATION") >= 1,
        "has_backup": "pre-update-snapshot.sh" in text,
        "has_soak": "12–24-hour soak" in text,
        "has_rollback": "RB-ALL" in text,
    }


def _validate_privacy(errors: list[str]) -> None:
    for path in (
        PLAN_PATH,
        RUNBOOK_PATH,
        README_PATH,
        INPUTS_PATH,
        SOAK_PATH,
    ):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(PACKET_ROOT)
        for pattern in PRIVATE_PATH_PATTERNS:
            if pattern.search(text):
                errors.append(f"privacy:private_path:{relative}")
        if PRIVATE_NETWORK_PATTERN.search(text):
            errors.append(f"privacy:private_network:{relative}")
        if SECRET_VALUE_PATTERN.search(text):
            errors.append(f"privacy:secret_value:{relative}")


def validate_packet() -> dict[str, Any]:
    errors: list[str] = []
    plan = _read_json(PLAN_PATH, errors)
    soak = _read_json(SOAK_PATH, errors)
    plan_summary = _validate_plan(plan, errors)
    template_summary = _validate_templates(soak, errors)
    runbook_summary = _validate_runbook(errors)
    try:
        preflight = _load_preflight().evaluate_preflight()
    except Exception as exc:
        preflight = {"activation_eligible": False, "blockers": (type(exc).__name__,)}
        errors.append("preflight:evaluation_failed")
    if preflight.get("activation_eligible") is not True:
        errors.append("preflight:offline_go_not_recognized")
    if tuple(preflight.get("blockers") or ()):
        errors.append("preflight:unexpected_current_blockers")
    if preflight.get("live_actions_performed") is not False:
        errors.append("preflight:live_action_detected")
    _validate_privacy(errors)
    return {
        "schema": SCHEMA,
        "valid": not errors,
        "errors": tuple(sorted(set(errors))),
        "plan": plan_summary,
        "templates": template_summary,
        "runbook": runbook_summary,
        "preflight_status": preflight.get("status"),
        "activation_eligible": preflight.get("activation_eligible"),
        "current_blockers": tuple(preflight.get("blockers") or ()),
        "live_actions_performed": False,
        "host_reads_performed": False,
        "network_io_performed": False,
        "secrets_created": False,
        "services_started": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    args = parser.parse_args(argv)
    report = validate_packet()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["valid"]:
        print("ACTIVATION_PACKET_VALID_AWAITING_LIVE_GO")
    else:
        for error in report["errors"]:
            print(f"ERROR {error}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
