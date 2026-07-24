from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET_ROOT = ROOT / "ops" / "homeserver" / "gemma3-maintenance-activation"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(relative_path: str) -> dict:
    return json.loads((PACKET_ROOT / relative_path).read_text(encoding="utf-8"))


def test_preflight_marks_packet_ready_without_authorizing_live_execution() -> None:
    preflight = _load_module("gmi15_preflight_test", PACKET_ROOT / "preflight.py")
    report = preflight.evaluate_preflight()

    assert report["status"] == "packet_ready_awaiting_gro_live_validation_and_gmi_live_go"
    assert report["packet_ready"] is True
    assert report["live_execution_eligible"] is False
    assert report["offline_blockers"] == ()
    assert report["live_blockers"] == (
        "gmi_live_go_not_recorded",
        "gro_live_validation_not_recorded",
    )
    assert report["gates"]["gmi_hash_manifest_matches"] is True
    assert report["gates"]["gro_packet_repo_eligible"] is True
    for key in (
        "live_actions_performed",
        "host_reads_performed",
        "network_io_performed",
        "model_calls_performed",
        "deploy_performed",
        "secrets_created",
        "services_changed",
        "runtime_setting_changed",
    ):
        assert report[key] is False
    assert preflight.main(["--require-packet-ready"]) == 0
    assert preflight.main(["--require-live-eligible"]) == preflight.BLOCKED_EXIT


def test_plan_freezes_exact_default_off_config_slos_and_phase_order() -> None:
    validator = _load_module("gmi15_validator_plan", PACKET_ROOT / "validate_packet.py")
    plan = _json("activation-plan.json")

    assert plan["status"] == "packet_ready_live_prerequisites_missing"
    assert plan["current_execution_authorized"] is False
    assert plan["safe_default"] == "gemma3_4b_maintenance_runtime_disabled"
    assert plan["single_live_gate"] == "GMI-LIVE-ACTIVATION"
    assert plan["single_go_phrase"] == "GO GMI-LIVE-ACTIVATION"
    assert plan["configuration_contract"] == validator.EXPECTED_CONFIG
    assert tuple(plan["phase_order"]) == validator.EXPECTED_PHASES
    assert tuple(phase["id"] for phase in plan["phases"]) == validator.EXPECTED_PHASES
    assert tuple(
        phase["id"] for phase in plan["phases"] if phase["mutates"]
    ) == validator.EXPECTED_MUTATING_PHASES

    invariants = plan["invariants"]
    assert invariants["model_ref"] == "gemma3:4b"
    assert invariants["provider_scope"] == "local_ollama"
    assert invariants["role_scope"] == "maintenance"
    assert invariants["max_queue_concurrency"] == 1
    assert invariants["canary_warmup_calls"] == 1
    assert invariants["canary_measured_calls"] == 20
    assert invariants["canary_success_calls_required"] == 20
    assert invariants["warm_latency_p95_seconds_lt"] == 30
    assert invariants["warm_latency_max_seconds_lt"] == 45
    assert invariants["event_loop_max_gap_seconds_lt"] == 0.1
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
        assert invariants[key] is False


def test_gro_live_dependency_is_separate_absent_and_fail_closed() -> None:
    plan = _json("activation-plan.json")

    assert plan["offline_barriers"]["gmi_observed_verdict"] == "offline_go"
    assert plan["offline_barriers"]["gro_observed_verdict"] == "offline_go"
    assert plan["offline_barriers"]["gro_packet_required_status"] == "eligible_awaiting_live_go"
    assert plan["offline_barriers"]["on_mismatch"] == "stop_before_live_read_or_mutation"
    assert plan["live_dependency"] == {
        "required_evidence_schema": "odysseus.memory_observability_live_soak.v1",
        "required_verdict": "go",
        "current_evidence_recorded": False,
        "reason": "GMI activation needs the separately authorized GRO target and dashboard service; this packet never activates GRO implicitly.",
    }
    assert plan["invariants"]["gro_activation_allowed_by_this_packet"] is False


def test_rollback_is_single_key_first_ordered_and_non_destructive() -> None:
    validator = _load_module("gmi15_validator_rollback", PACKET_ROOT / "validate_packet.py")
    rollback = _json("activation-plan.json")["rollback"]

    assert tuple(rollback["ordered_steps"]) == validator.EXPECTED_ROLLBACK
    assert rollback["ordered_steps"][0] == "set_maintenance_runtime_enabled_false"
    assert len(rollback["automatic_triggers"]) == 14
    assert rollback["settings_policy"] == "compare_and_set_single_key_never_restore_whole_settings_file"
    assert rollback["volume_policy"] == "never_delete_or_restore_productive_data_volumes_from_this_packet"
    assert rollback["git_policy"] == "never_reset_checkout_or_rewrite_history"
    assert rollback["gro_policy"] == "never_stop_or_reconfigure_preexisting_gro_services"


def test_templates_are_blank_bounded_content_free_and_not_run() -> None:
    lines = [
        line.strip()
        for line in (PACKET_ROOT / "templates" / "live-inputs.env.example").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    values = dict(line.split("=", 1) for line in lines)
    evidence = _json("templates/live-evidence.template.json")

    assert tuple(values) == (
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
    assert values["OBSERVATION_HOURS"] == "12"
    assert all(not value for key, value in values.items() if key != "OBSERVATION_HOURS")
    assert evidence["canary"]["warmup_calls"] == 1
    assert evidence["canary"]["measured_calls"] == 20
    assert evidence["canary"]["verdict"] == "not_run"
    assert evidence["observation"]["expected_sample_count"] == 2880
    assert evidence["observation"]["verdict"] == "not_run"
    assert evidence["final_verdict"] == "not_run"
    for key in (
        "credential_or_secret_values_present",
        "prompt_or_message_content_present",
        "model_output_present",
        "raw_metric_payload_present",
        "raw_log_content_present",
        "private_host_value_present",
    ):
        assert evidence[key] is False


def test_dashboard_has_only_fixed_low_cardinality_gmi_queries() -> None:
    dashboard = _json("grafana/gemma3-maintenance.json")

    assert dashboard["uid"] == "odysseus-gemma3-maintenance"
    assert dashboard["editable"] is False
    assert dashboard["templating"]["list"] == []
    assert len(dashboard["panels"]) == 6
    queries = [panel["targets"][0]["expr"] for panel in dashboard["panels"]]
    assert len(queries) == 6
    for expression in queries:
        assert "gemma_maintenance_" in expression
        assert 'model_scope="gemma3_4b"' in expression
        assert 'runtime="maintenance"' in expression
        assert "$" not in expression
    encoded = json.dumps(dashboard, sort_keys=True).lower()
    for forbidden in ("endpoint", "owner", "source_ref", "prompt", "output", "token"):
        assert forbidden not in encoded


def test_canary_defaults_to_refusal_before_any_call(monkeypatch, capsys) -> None:
    canary = _load_module("gmi15_canary_refusal", PACKET_ROOT / "run_canary.py")
    monkeypatch.setattr(canary, "load_settings", lambda: dict(canary.EXPECTED_SETTINGS))

    report = canary.evaluate_execution_gate(execute=False, approval=None)
    assert report["allowed"] is False
    assert report["live_model_calls_performed"] is False
    assert report["gates"]["execute_flag_present"] is False
    assert report["gates"]["exact_live_go_recorded"] is False
    assert canary.main([]) == canary.BLOCKED_EXIT
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "blocked"
    assert output["live_model_calls_performed"] is False


def test_canary_fake_transport_proves_exact_positive_path_without_network(monkeypatch) -> None:
    canary = _load_module("gmi15_canary_positive", PACKET_ROOT / "run_canary.py")
    calls = []

    async def fake_attempt(attempt):
        calls.append(attempt)
        return canary.MaintenanceLLMUpstreamResponse(
            status_code=200,
            payload={"message": {"content": "READY"}},
        )

    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    report = asyncio.run(
        canary.execute_canary(
            "http://127.0.0.1:11434",
            attempt=fake_attempt,
        )
    )

    assert len(calls) == 21
    assert report["warmup_calls"] == report["warmup_success"] == 1
    assert report["measured_calls"] == report["success_calls"] == 20
    assert report["failure_calls"] == 0
    assert report["verdict"] == "go"
    assert report["gates"]["p95_lt_30_seconds"] is True
    assert report["gates"]["max_lt_45_seconds"] is True
    assert report["gates"]["event_loop_gap_lt_100ms"] is True
    assert report["global_runtime_changed"] is False
    assert report["response_content_recorded"] is False
    assert report["prompt_or_message_content_recorded"] is False
    assert report["failure_codes"] == ()


def test_canary_fake_failure_is_content_free_no_go(monkeypatch) -> None:
    canary = _load_module("gmi15_canary_failure", PACKET_ROOT / "run_canary.py")

    async def fake_failure(_attempt):
        return canary.MaintenanceLLMUpstreamResponse(
            status_code=503,
            payload={"message": {"content": "must-not-be-recorded"}},
        )

    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    report = asyncio.run(
        canary.execute_canary(
            "http://127.0.0.1:11434",
            attempt=fake_failure,
        )
    )

    assert report["verdict"] == "no_go"
    assert report["success_calls"] == 0
    assert report["failure_codes"] == ("MaintenanceLLMCallError",)
    encoded = json.dumps(report, sort_keys=True)
    assert "must-not-be-recorded" not in encoded
    assert report["response_content_recorded"] is False
    assert report["prompt_or_message_content_recorded"] is False


def test_runbook_places_barriers_before_go_and_has_all_transaction_stages() -> None:
    text = (PACKET_ROOT / "LIVE_RUNBOOK.md").read_text(encoding="utf-8")

    assert text.index("preflight.py --require-packet-ready --json") < text.index(
        "export GMI_LIVE_APPROVAL"
    )
    assert "odysseus.memory_observability_live_soak.v1" in text
    assert "pre-update-snapshot.sh" in text
    assert "predeployment image archive" in text
    assert "run_canary.py --execute" in text
    assert "20/20" in text
    assert "p95 < 30 s" in text
    assert "max < 45 s" in text
    assert "event-loop gap < 100 ms" in text
    assert "single settings key" in text
    assert "12-24 hour observation" in text
    assert all(name in text for name in ("RB-01", "RB-02", "RB-03", "RB-04", "RB-ALL"))
    for forbidden in (
        "git reset",
        "git checkout --",
        "compose down -v",
        "podman volume rm",
        "systemctl --user enable",
        "--privileged",
        "--network=host",
    ):
        assert forbidden not in text


def test_packet_validator_is_green_while_both_live_barriers_remain_closed() -> None:
    validator = _load_module("gmi15_validator_final", PACKET_ROOT / "validate_packet.py")
    report = validator.validate_packet()

    assert report["valid"] is True, report["errors"]
    assert report["errors"] == ()
    assert report["plan"] == {
        "phase_count": 13,
        "mutating_phase_count": 5,
        "rollback_step_count": 10,
        "automatic_trigger_count": 14,
    }
    assert report["dashboard"] == {"panel_count": 6, "query_count": 6}
    assert report["templates"]["canary_measured_calls"] == 20
    assert report["packet_ready"] is True
    assert report["live_execution_eligible"] is False
    assert report["live_blockers"] == (
        "gmi_live_go_not_recorded",
        "gro_live_validation_not_recorded",
    )
    for key in (
        "live_actions_performed",
        "host_reads_performed",
        "network_io_performed",
        "model_calls_performed",
        "deploy_performed",
        "secrets_created",
        "services_changed",
    ):
        assert report[key] is False
