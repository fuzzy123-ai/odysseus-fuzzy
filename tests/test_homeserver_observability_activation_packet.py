from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET_ROOT = ROOT / "ops" / "homeserver" / "observability-podman" / "activation"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(name: str):
    return json.loads((PACKET_ROOT / name).read_text(encoding="utf-8"))


def test_preflight_recognizes_offline_go_without_any_live_action():
    preflight = _load_module("gro14_preflight", PACKET_ROOT / "preflight.py")
    report = preflight.evaluate_preflight()

    assert report["status"] == "eligible_for_future_live_gate"
    assert report["activation_eligible"] is True
    assert report["offline_verdict"] == "offline_go"
    assert report["blockers"] == ()
    assert report["gates"]["offline_acceptance_is_go"] is True
    assert report["gates"]["prometheus_assets_valid"] is True
    assert report["gates"]["grafana_assets_valid"] is True
    assert report["live_actions_performed"] is False
    assert report["host_reads_performed"] is False
    assert report["network_io_performed"] is False
    assert report["secrets_created"] is False
    assert report["services_started"] is False
    assert preflight.main(["--require-eligible"]) == 0


def test_activation_plan_has_exact_transaction_and_fail_closed_phase_order():
    validator = _load_module("gro14_validator_plan", PACKET_ROOT / "validate_packet.py")
    plan = _json("activation-plan.json")

    assert plan["status"] == "eligible_awaiting_live_go"
    assert plan["current_execution_authorized"] is False
    assert plan["offline_barrier"] == {
        "evidence": "docs/plans/graphrag-raptor-observability-offline-acceptance.json",
        "required_verdict": "offline_go",
        "observed_verdict": "offline_go",
        "on_mismatch": "stop_before_live_read_or_mutation",
    }
    assert tuple(plan["phase_order"]) == validator.EXPECTED_PHASES
    assert tuple(phase["id"] for phase in plan["phases"]) == validator.EXPECTED_PHASES
    assert all(phase["required_evidence"] for phase in plan["phases"])
    mutating = [phase["id"] for phase in plan["phases"] if phase["mutates"]]
    assert mutating == [
        "backup_checkpoint",
        "default_off_staging",
        "scoped_secret_creation",
        "private_activation",
        "finalize_or_rollback",
    ]
    assert all(phase.get("rollback_ref") for phase in plan["phases"] if phase["mutates"])


def test_plan_freezes_private_security_retention_and_soak_invariants():
    plan = _json("activation-plan.json")
    invariants = plan["invariants"]

    assert invariants["scrape_token_scopes"] == ["observability:read"]
    assert invariants["prometheus_binding"] == "loopback_only"
    assert invariants["grafana_binding"] == "loopback_only"
    assert invariants["prometheus_retention_days"] == 30
    assert invariants["prometheus_retention_size_gb"] == 5
    assert invariants["scrape_interval_seconds"] == 15
    assert invariants["scrape_timeout_seconds"] == 5
    assert invariants["soak_min_hours"] == 12
    assert invariants["soak_max_hours"] == 24
    for key in (
        "odysseus_runtime_restart_allowed",
        "productive_rebuild_allowed",
        "public_binding_allowed",
        "host_network_allowed",
        "privileged_container_allowed",
        "remote_write_allowed",
        "external_alert_delivery_allowed",
        "destructive_volume_rollback_allowed",
    ):
        assert invariants[key] is False


def test_rollback_is_ordered_automatic_non_destructive_and_no_restart():
    validator = _load_module("gro14_validator_rollback", PACKET_ROOT / "validate_packet.py")
    rollback = _json("activation-plan.json")["rollback"]

    assert tuple(rollback["ordered_steps"]) == validator.EXPECTED_ROLLBACK
    assert len(rollback["automatic_triggers"]) == 8
    assert rollback["volume_policy"] == "retain_unless_separately_approved_for_destruction"
    assert rollback["odysseus_restart"] is False
    assert "retain_versioned_volumes_for_forensics" in rollback["ordered_steps"]


def test_live_input_template_has_no_identity_url_or_secret_value():
    lines = [
        line.strip()
        for line in (PACKET_ROOT / "templates" / "live-inputs.env.example").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    values = dict(line.split("=", 1) for line in lines)

    assert tuple(values) == (
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
    assert values["SOAK_HOURS"] == "12"
    assert all(not value for key, value in values.items() if key != "SOAK_HOURS")
    assert not any(key in values for key in ("TOKEN", "PASSWORD", "SECRET"))


def test_soak_template_is_content_free_bounded_and_not_run():
    soak = _json("templates/soak-evidence.template.json")

    assert soak["duration_hours"] == 12
    assert soak["sample_interval_seconds"] == 15
    assert soak["expected_sample_count"] == 2880
    assert soak["observed_sample_count"] == 0
    assert soak["verdict"] == "not_run"
    assert soak["rollback_performed"] is False
    assert soak["secret_values_present"] is False
    assert soak["raw_metric_payload_present"] is False
    assert soak["raw_log_content_present"] is False


def test_runbook_places_barrier_before_go_and_has_backup_activation_soak_rollback():
    text = (PACKET_ROOT / "LIVE_RUNBOOK.md").read_text(encoding="utf-8")

    assert text.index("preflight.py --require-eligible --json") < text.index(
        "export GRO_LIVE_APPROVAL"
    )
    assert "offline_acceptance_verdict:offline_go" in text
    assert "pre-update-snapshot.sh" in text
    assert 'profile=observability_readonly' in text
    assert "promtool test rules" in text
    assert "127.0.0.1:9090" in text
    assert "127.0.0.1:3000" in text
    assert "12–24-hour soak" in text
    assert all(name in text for name in ("RB-01", "RB-02", "RB-03", "RB-04", "RB-ALL"))
    assert "systemctl --user enable" not in text
    assert "--privileged" not in text
    assert "--network=host" not in text
    assert "never restarts Odysseus and never deletes a volume" in text


def test_packet_validator_is_green_while_live_activation_remains_unauthorized():
    validator = _load_module("gro14_validator", PACKET_ROOT / "validate_packet.py")
    report = validator.validate_packet()

    assert report["valid"] is True, report["errors"]
    assert report["errors"] == ()
    assert report["plan"] == {
        "phase_count": 11,
        "mutating_phase_count": 5,
        "rollback_step_count": 10,
        "automatic_trigger_count": 8,
    }
    assert report["activation_eligible"] is True
    assert report["current_blockers"] == ()
    assert report["live_actions_performed"] is False
    assert report["host_reads_performed"] is False
    assert report["network_io_performed"] is False
    assert report["secrets_created"] is False
    assert report["services_started"] is False
