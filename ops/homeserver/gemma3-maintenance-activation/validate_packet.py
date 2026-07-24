"""Deterministic offline validator for the GMI-15 activation packet."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
from typing import Any, Mapping


PACKET_ROOT = Path(__file__).resolve().parent
PLAN_PATH = PACKET_ROOT / "activation-plan.json"
PREFLIGHT_PATH = PACKET_ROOT / "preflight.py"
CANARY_PATH = PACKET_ROOT / "run_canary.py"
RUNBOOK_PATH = PACKET_ROOT / "LIVE_RUNBOOK.md"
README_PATH = PACKET_ROOT / "README.md"
INPUTS_PATH = PACKET_ROOT / "templates" / "live-inputs.env.example"
EVIDENCE_PATH = PACKET_ROOT / "templates" / "live-evidence.template.json"
DASHBOARD_PATH = PACKET_ROOT / "grafana" / "gemma3-maintenance.json"

SCHEMA = "odysseus.gemma3_maintenance_activation_packet_validation.v1"
EXPECTED_PHASES = (
    "offline_barrier",
    "gro_live_evidence_barrier",
    "live_identity_revision_readback",
    "capacity_model_and_health_preflight",
    "backup_and_image_checkpoint",
    "default_off_deploy",
    "exact_config_readback",
    "dashboard_stage_and_readback",
    "warm_canary_20",
    "slo_decision",
    "activate_exact_setting",
    "bounded_observation",
    "finalize_or_rollback",
)
EXPECTED_MUTATING_PHASES = (
    "backup_and_image_checkpoint",
    "default_off_deploy",
    "dashboard_stage_and_readback",
    "activate_exact_setting",
    "finalize_or_rollback",
)
EXPECTED_ROLLBACK = (
    "set_maintenance_runtime_enabled_false",
    "wait_for_settings_cache_expiry",
    "verify_in_process_runtime_setting_false",
    "remove_packet_dashboard_if_created_by_this_run",
    "verify_gro_services_remain_in_their_preexisting_state",
    "restore_predeployment_image_if_deploy_regressed",
    "recreate_only_odysseus_from_recorded_image_if_required",
    "verify_odysseus_and_chromadb_health",
    "verify_no_unrelated_setting_changed",
    "retain_redacted_evidence_and_image_archive_for_forensics",
)
EXPECTED_INPUTS = (
    "EXPECTED_SSH_ALIAS",
    "EXPECTED_USER",
    "EXPECTED_HOSTNAME",
    "ODYSSEUS_ROOT",
    "EXPECTED_COMMIT",
    "ODYSSEUS_CONTAINER",
    "OLLAMA_ENDPOINT",
    "PROMETHEUS_URL",
    "GRAFANA_URL",
    "GRAFANA_NETRC_FILE",
    "RESTIC_PASSWORD_FILE",
    "OBSERVATION_HOURS",
)
EXPECTED_CONFIG = {
    "maintenance_model_ref": "gemma3:4b",
    "maintenance_model_provider": "local_ollama",
    "maintenance_model_token_budget": 1200,
    "maintenance_model_max_input_chars": 6000,
    "maintenance_model_chunk_budget": 4,
    "maintenance_model_source_ref_budget": 4,
    "maintenance_model_latency_budget_ms": 45000,
    "maintenance_model_api_fallback_enabled": False,
    "maintenance_runtime_enabled_before_activation": False,
    "only_activation_diff": {"maintenance_runtime_enabled": True},
}
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
    r"(?:Bearer\s+[A-Za-z0-9_+/.=-]{12,}|"
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
    spec = importlib.util.spec_from_file_location("gmi15_packet_preflight", PREFLIGHT_PATH)
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
    if plan.get("schema") != "odysseus.gemma3_maintenance_activation_plan.v1":
        errors.append("plan:schema_mismatch")
    if plan.get("status") != "packet_ready_live_prerequisites_missing":
        errors.append("plan:status_mismatch")
    if plan.get("single_live_gate") != "GMI-LIVE-ACTIVATION":
        errors.append("plan:single_live_gate_mismatch")
    if plan.get("single_go_phrase") != "GO GMI-LIVE-ACTIVATION":
        errors.append("plan:single_go_phrase_mismatch")
    if plan.get("current_execution_authorized") is not False:
        errors.append("plan:current_execution_must_be_unauthorized")
    if plan.get("safe_default") != "gemma3_4b_maintenance_runtime_disabled":
        errors.append("plan:safe_default_mismatch")

    barriers = _mapping(plan.get("offline_barriers"))
    for name in ("gmi_required_verdict", "gmi_observed_verdict", "gro_required_verdict", "gro_observed_verdict"):
        if barriers.get(name) != "offline_go":
            errors.append(f"plan:offline_barrier_mismatch:{name}")
    if barriers.get("gro_packet_required_status") != "eligible_awaiting_live_go":
        errors.append("plan:gro_packet_barrier_mismatch")
    if barriers.get("on_mismatch") != "stop_before_live_read_or_mutation":
        errors.append("plan:offline_barrier_not_fail_closed")
    live_dependency = _mapping(plan.get("live_dependency"))
    if live_dependency.get("required_evidence_schema") != "odysseus.memory_observability_live_soak.v1":
        errors.append("plan:gro_live_schema_mismatch")
    if live_dependency.get("required_verdict") != "go":
        errors.append("plan:gro_live_verdict_mismatch")
    if live_dependency.get("current_evidence_recorded") is not False:
        errors.append("plan:gro_live_evidence_must_be_absent_now")

    if tuple(str(value) for value in _sequence(plan.get("required_live_inputs"))) != EXPECTED_INPUTS:
        errors.append("plan:required_live_inputs_mismatch")
    if dict(_mapping(plan.get("configuration_contract"))) != EXPECTED_CONFIG:
        errors.append("plan:configuration_contract_mismatch")

    invariants = _mapping(plan.get("invariants"))
    expected_invariants = {
        "model_ref": "gemma3:4b",
        "model_scope": "gemma3_4b",
        "provider_scope": "local_ollama",
        "role_scope": "maintenance",
        "max_queue_concurrency": 1,
        "canary_warmup_calls": 1,
        "canary_measured_calls": 20,
        "canary_success_calls_required": 20,
        "warm_latency_p95_seconds_lt": 30,
        "warm_latency_max_seconds_lt": 45,
        "event_loop_max_gap_seconds_lt": 0.1,
        "runtime_timeout_ms": 45000,
        "observation_min_hours": 12,
        "observation_max_hours": 24,
        "observation_sample_interval_seconds": 15,
    }
    for key, expected in expected_invariants.items():
        if invariants.get(key) != expected:
            errors.append(f"plan:invariant_mismatch:{key}")
    for key in (
        "fallback_allowed",
        "truth_write_allowed",
        "streaming_allowed",
        "productive_write_allowed",
        "agent_or_chat_role_allowed",
        "public_binding_allowed",
        "new_secret_creation_allowed",
        "gro_activation_allowed_by_this_packet",
        "destructive_git_operation_allowed",
        "destructive_volume_operation_allowed",
    ):
        if invariants.get(key) is not False:
            errors.append(f"plan:unsafe_invariant:{key}")

    phase_order = tuple(str(value) for value in _sequence(plan.get("phase_order")))
    phases = _sequence(plan.get("phases"))
    phase_ids = tuple(str(_mapping(phase).get("id") or "") for phase in phases)
    if phase_order != EXPECTED_PHASES or phase_ids != EXPECTED_PHASES:
        errors.append("plan:phase_order_mismatch")
    mutating = tuple(
        str(_mapping(phase).get("id") or "")
        for phase in phases
        if _mapping(phase).get("mutates") is True
    )
    if mutating != EXPECTED_MUTATING_PHASES:
        errors.append("plan:mutating_phase_set_mismatch")
    for phase_value in phases:
        phase = _mapping(phase_value)
        phase_id = str(phase.get("id") or "")
        if not _sequence(phase.get("required_evidence")):
            errors.append(f"plan:phase_without_evidence:{phase_id}")
        if phase.get("failure_action") not in {"stop", "rollback"}:
            errors.append(f"plan:failure_action_invalid:{phase_id}")
        if phase.get("failure_action") == "rollback" and not phase.get("rollback_ref"):
            errors.append(f"plan:rollback_reference_missing:{phase_id}")
        if phase_id in EXPECTED_PHASES[:2] and phase.get("live_action") is not False:
            errors.append(f"plan:offline_phase_marked_live:{phase_id}")
        if phase_id in EXPECTED_PHASES[2:] and phase.get("live_action") is not True:
            errors.append(f"plan:live_phase_not_marked:{phase_id}")

    rollback = _mapping(plan.get("rollback"))
    if tuple(str(value) for value in _sequence(rollback.get("ordered_steps"))) != EXPECTED_ROLLBACK:
        errors.append("plan:rollback_order_mismatch")
    if len(_sequence(rollback.get("automatic_triggers"))) < 14:
        errors.append("plan:rollback_triggers_incomplete")
    expected_policies = {
        "settings_policy": "compare_and_set_single_key_never_restore_whole_settings_file",
        "image_policy": "retain_predeployment_image_archive_until_final_go",
        "volume_policy": "never_delete_or_restore_productive_data_volumes_from_this_packet",
        "git_policy": "never_reset_checkout_or_rewrite_history",
        "gro_policy": "never_stop_or_reconfigure_preexisting_gro_services",
    }
    for key, expected in expected_policies.items():
        if rollback.get(key) != expected:
            errors.append(f"plan:rollback_policy_mismatch:{key}")
    return {
        "phase_count": len(phases),
        "mutating_phase_count": len(mutating),
        "rollback_step_count": len(_sequence(rollback.get("ordered_steps"))),
        "automatic_trigger_count": len(_sequence(rollback.get("automatic_triggers"))),
    }


def _validate_templates(evidence: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    inputs: dict[str, str] = {}
    if not INPUTS_PATH.is_file():
        errors.append("missing:templates/live-inputs.env.example")
    else:
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
        if key == "OBSERVATION_HOURS":
            if value != "12":
                errors.append("inputs:observation_default_mismatch")
        elif value:
            errors.append(f"inputs:live_value_must_be_blank:{key}")
    if any(key in inputs for key in ("TOKEN", "PASSWORD", "SECRET")):
        errors.append("inputs:secret_value_field_forbidden")

    if evidence.get("schema") != "odysseus.gemma3_maintenance_live_evidence.v1":
        errors.append("evidence:schema_mismatch")
    canary = _mapping(evidence.get("canary"))
    observation = _mapping(evidence.get("observation"))
    if (canary.get("warmup_calls"), canary.get("measured_calls")) != (1, 20):
        errors.append("evidence:canary_count_mismatch")
    if canary.get("verdict") != "not_run" or evidence.get("final_verdict") != "not_run":
        errors.append("evidence:template_must_be_not_run")
    if observation.get("duration_hours") != 12 or observation.get("sample_interval_seconds") != 15:
        errors.append("evidence:observation_contract_mismatch")
    if observation.get("expected_sample_count") != 2880:
        errors.append("evidence:observation_sample_count_mismatch")
    for key in (
        "credential_or_secret_values_present",
        "prompt_or_message_content_present",
        "model_output_present",
        "raw_metric_payload_present",
        "raw_log_content_present",
        "private_host_value_present",
    ):
        if evidence.get(key) is not False:
            errors.append(f"evidence:unsafe_template_flag:{key}")
    return {
        "input_count": len(inputs),
        "canary_measured_calls": canary.get("measured_calls"),
        "observation_expected_samples": observation.get("expected_sample_count"),
    }


def _validate_dashboard(dashboard: Mapping[str, Any], errors: list[str]) -> dict[str, Any]:
    if dashboard.get("uid") != "odysseus-gemma3-maintenance":
        errors.append("dashboard:uid_mismatch")
    if dashboard.get("editable") is not False:
        errors.append("dashboard:must_not_be_editable")
    if _sequence(_mapping(dashboard.get("templating")).get("list")):
        errors.append("dashboard:variables_forbidden")
    panels = _sequence(dashboard.get("panels"))
    if len(panels) != 6:
        errors.append("dashboard:panel_count_mismatch")
    queries: list[str] = []
    for panel_value in panels:
        panel = _mapping(panel_value)
        targets = _sequence(panel.get("targets"))
        if len(targets) != 1:
            errors.append(f"dashboard:target_count:{panel.get('id')}")
        for target_value in targets:
            target = _mapping(target_value)
            expression = str(target.get("expr") or "")
            queries.append(expression)
            if "gemma_maintenance_" not in expression:
                errors.append(f"dashboard:non_gmi_query:{panel.get('id')}")
            if "model_scope=\"gemma3_4b\"" not in expression or "runtime=\"maintenance\"" not in expression:
                errors.append(f"dashboard:scope_filter_missing:{panel.get('id')}")
            if "$" in expression:
                errors.append(f"dashboard:variable_query_forbidden:{panel.get('id')}")
    encoded = json.dumps(dashboard, sort_keys=True)
    for forbidden in ("endpoint", "owner", "source_ref", "prompt", "output", "token"):
        if forbidden in encoded.lower():
            errors.append(f"dashboard:forbidden_content:{forbidden}")
    return {"panel_count": len(panels), "query_count": len(queries)}


def _validate_canary_source(errors: list[str]) -> dict[str, Any]:
    if not CANARY_PATH.is_file():
        errors.append("missing:run_canary.py")
        return {}
    text = CANARY_PATH.read_text(encoding="utf-8")
    required = (
        'APPROVAL_VALUE = "GO GMI-LIVE-ACTIVATION"',
        "WARMUP_CALLS = 1",
        "MEASURED_CALLS = 20",
        "P95_LIMIT_SECONDS = 30.0",
        "MAX_LIMIT_SECONDS = 45.0",
        "EVENT_LOOP_GAP_LIMIT_SECONDS = 0.1",
        "MaintenanceModelProfile.create(runtime_enabled=True)",
        '"maintenance_runtime_enabled": False',
        "fallback_requested=False",
        "truth_write_requested=False",
        '"response_content_recorded": False',
    )
    for value in required:
        if value not in text:
            errors.append(f"canary:required_contract_missing:{value}")
    if text.find("evaluate_execution_gate(") > text.find("asyncio.run(execute_canary"):
        errors.append("canary:execution_before_gate")
    for forbidden in ("subprocess", "os.system", "shell=True", "verify=False"):
        if forbidden in text:
            errors.append(f"canary:forbidden_execution:{forbidden}")
    return {
        "default_refusal_present": "--execute" in text,
        "exact_go_present": "GO GMI-LIVE-ACTIVATION" in text,
        "content_free_output": '"response_content_recorded": False' in text,
    }


def _validate_runbook(errors: list[str]) -> dict[str, Any]:
    if not RUNBOOK_PATH.is_file():
        errors.append("missing:LIVE_RUNBOOK.md")
        return {}
    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    required = (
        "preflight.py --require-packet-ready --json",
        "odysseus.memory_observability_live_soak.v1",
        "GO GMI-LIVE-ACTIVATION",
        "pre-update-snapshot.sh",
        "maintenance_runtime_enabled",
        "run_canary.py --execute",
        "20/20",
        "p95 < 30 s",
        "max < 45 s",
        "event-loop gap < 100 ms",
        "odysseus-gemma3-maintenance",
        "12-24 hour observation",
        "RB-01",
        "RB-02",
        "RB-03",
        "RB-04",
        "RB-ALL",
        "single settings key",
        "predeployment image archive",
    )
    for value in required:
        if value not in text:
            errors.append(f"runbook:required_contract_missing:{value}")
    if text.find("preflight.py --require-packet-ready --json") > text.find("export GMI_LIVE_APPROVAL"):
        errors.append("runbook:go_materialized_before_offline_barrier")
    for forbidden in (
        "git reset",
        "git checkout --",
        "compose down -v",
        "podman volume rm",
        "systemctl --user enable",
        "--privileged",
        "--network=host",
    ):
        if forbidden in text:
            errors.append(f"runbook:forbidden_command:{forbidden}")
    return {
        "has_single_go": "GO GMI-LIVE-ACTIVATION" in text,
        "has_canary": "run_canary.py --execute" in text,
        "has_observation": "12-24 hour observation" in text,
        "has_rollback": "RB-ALL" in text,
    }


def _validate_privacy(errors: list[str]) -> None:
    paths = (
        PLAN_PATH,
        RUNBOOK_PATH,
        README_PATH,
        INPUTS_PATH,
        EVIDENCE_PATH,
        DASHBOARD_PATH,
        CANARY_PATH,
    )
    for path in paths:
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
    evidence = _read_json(EVIDENCE_PATH, errors)
    dashboard = _read_json(DASHBOARD_PATH, errors)
    plan_summary = _validate_plan(plan, errors)
    template_summary = _validate_templates(evidence, errors)
    dashboard_summary = _validate_dashboard(dashboard, errors)
    canary_summary = _validate_canary_source(errors)
    runbook_summary = _validate_runbook(errors)
    try:
        preflight = _load_preflight().evaluate_preflight()
    except Exception as exc:
        preflight = {"packet_ready": False, "live_blockers": (type(exc).__name__,)}
        errors.append("preflight:evaluation_failed")
    if preflight.get("packet_ready") is not True:
        errors.append("preflight:packet_not_ready")
    if preflight.get("live_execution_eligible") is not False:
        errors.append("preflight:live_execution_must_remain_ineligible")
    if tuple(preflight.get("offline_blockers") or ()):
        errors.append("preflight:unexpected_offline_blocker")
    if tuple(preflight.get("live_blockers") or ()) != (
        "gmi_live_go_not_recorded",
        "gro_live_validation_not_recorded",
    ):
        errors.append("preflight:live_blocker_mismatch")
    if preflight.get("live_actions_performed") is not False:
        errors.append("preflight:live_action_detected")
    _validate_privacy(errors)
    return {
        "schema": SCHEMA,
        "valid": not errors,
        "errors": tuple(sorted(set(errors))),
        "plan": plan_summary,
        "templates": template_summary,
        "dashboard": dashboard_summary,
        "canary": canary_summary,
        "runbook": runbook_summary,
        "preflight_status": preflight.get("status"),
        "packet_ready": preflight.get("packet_ready"),
        "live_execution_eligible": preflight.get("live_execution_eligible"),
        "live_blockers": tuple(preflight.get("live_blockers") or ()),
        "live_actions_performed": False,
        "host_reads_performed": False,
        "network_io_performed": False,
        "model_calls_performed": False,
        "deploy_performed": False,
        "secrets_created": False,
        "services_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = validate_packet()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["valid"]:
        print("GMI_ACTIVATION_PACKET_VALID_LIVE_PREREQUISITES_MISSING")
    else:
        for error in report["errors"]:
            print(f"ERROR {error}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
