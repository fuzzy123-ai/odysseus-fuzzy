import json
from pathlib import Path
import subprocess
import sys

from scripts.verify_tool_catalog_rollout import (
    CATALOG_V2_DEFAULT_ENABLED,
    FEATURE_FLAG,
    ROLLOUT_SCHEMA,
    build_synthetic_acceptance,
    build_live_readback,
    builtin_descriptions,
    legacy_security_projection,
    select_synthetic_projection,
    synthetic_settings,
)
from src.builtin_tool_catalog import CATALOG_TOOL_IDS, DEFAULT_DEFERRED_TOOLS
from src.runtime_tool_status import build_tool_catalog_projection
from src.settings import migrate_tool_settings


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "docs" / "plans" / "tool-taxonomy-live-activation-packet.md"
SCRIPT = ROOT / "scripts" / "verify_tool_catalog_rollout.py"


def _acceptance():
    return build_synthetic_acceptance(performance_cycles=3, max_elapsed_ms=2_500.0)


def test_catalog_v2_feature_flag_remains_default_off():
    assert FEATURE_FLAG == "tool-catalog-v2"
    assert CATALOG_V2_DEFAULT_ENABLED is False
    assert {spec for spec in synthetic_settings()["disabled_tools"]} >= set(
        DEFAULT_DEFERRED_TOOLS
    )


def test_synthetic_off_on_off_rollout_is_exact_and_non_mutating():
    descriptions = builtin_descriptions()
    migrated, _report = migrate_tool_settings(
        synthetic_settings(),
        alias_targets={"legacy_read_file": "read_file"},
    )
    disabled = tuple(migrated["disabled_tools"])
    legacy = legacy_security_projection(disabled)
    catalog_v2 = build_tool_catalog_projection(
        disabled_tools=disabled,
        builtin_descriptions=descriptions,
    )

    off_before = select_synthetic_projection(
        catalog_v2_enabled=False,
        legacy_projection=legacy,
        catalog_v2_projection=catalog_v2,
    )
    on = select_synthetic_projection(
        catalog_v2_enabled=True,
        legacy_projection=legacy,
        catalog_v2_projection=catalog_v2,
    )
    off_after = select_synthetic_projection(
        catalog_v2_enabled=False,
        legacy_projection=legacy,
        catalog_v2_projection=catalog_v2,
    )

    assert off_before == off_after
    assert off_before["selected"] == "legacy"
    assert on["selected"] == "catalog_v2"
    assert len(on["rows"]) == len(CATALOG_TOOL_IDS) == 84


def test_machine_acceptance_proves_settings_alias_and_reservation_retention():
    report = _acceptance()

    assert report["schema_version"] == ROLLOUT_SCHEMA
    assert report["status"] == "passed"
    assert report["checks"]["settings_preserved"] is True
    assert report["checks"]["settings_aliases_preserved"] is True
    assert report["checks"]["analytics_aliases_preserved"] is True
    assert report["checks"]["analytics_reservations_preserved"] is True
    assert report["counts"]["settings_aliases"] == 1
    assert report["counts"]["analytics_reservations"] == 84


def test_only_documented_runtime_permission_strengthening_differs():
    report = _acceptance()

    assert report["checks"]["dual_read_security_compatible"] is True
    assert report["intentional_drift"] == {
        "kind": "runtime_permission_strengthening",
        "from": "owner",
        "to": "admin",
        "tool_ids": (
            "cancel_download",
            "download_model",
            "manage_embeddings",
            "manage_github_issues",
            "manage_personal_docs",
            "manage_presets",
            "manage_repos",
            "recent_changes",
            "serve_model",
            "serve_preset",
            "stop_served_model",
        ),
        "weakened_security_fields": 0,
    }


def test_final_deferred_list_stays_disabled_in_both_projections():
    report = _acceptance()

    assert set(report["deferred_tools"]) == set(DEFAULT_DEFERRED_TOOLS)
    assert len(report["deferred_tools"]) == 14
    assert report["checks"]["deferred_tools_disabled"] is True
    assert {"send_email", "manage_calendar", "manage_contact"} <= set(
        report["deferred_tools"]
    )


def test_performance_error_and_diagnostic_budgets_pass():
    report = _acceptance()

    assert report["checks"]["projection_deterministic"] is True
    assert report["checks"]["performance_budget_met"] is True
    assert report["checks"]["error_budget_met"] is True
    assert report["checks"]["diagnostic_probe_fail_closed"] is True
    assert report["counts"]["intentional_permission_strengthenings"] == 11
    assert report["budgets"]["rollout_errors"] == 0
    assert report["budgets"]["projection_bytes"] < 256_000


def test_acceptance_diagnostics_are_aggregate_and_redacted():
    report = _acceptance()
    rendered = json.dumps(report, sort_keys=True).casefold()

    assert report["checks"]["diagnostics_redacted"] is True
    assert report["diagnostics"] == {
        "aggregate_only": True,
        "raw_arguments_visible": False,
        "raw_results_visible": False,
        "raw_content_visible": False,
        "secret_values_visible": False,
        "private_paths_visible": False,
    }
    for marker in ("c:\\", "/home/", "bearer ", "token=", "password=", "sk-"):
        assert marker not in rendered


def test_cli_required_command_emits_machine_readable_pass():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "synthetic",
            "--assert-default-off",
            "--assert-rollback",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed"
    assert report["live_contract"] == {
        "activation_authorized": False,
        "deployment_performed": False,
        "gate_id": "TAX-LIVE-ACTIVATION",
        "materialization_ready": True,
        "restart_performed": False,
    }


def test_activation_packet_is_complete_default_off_and_secret_free():
    packet = PACKET.read_text(encoding="utf-8")
    lowered = packet.casefold()

    for heading in (
        "## Version And Environment Template",
        "## Pre-Activation Checks",
        "## Monitoring And Budgets",
        "## Abort Criteria",
        "## Rollback",
        "## Final Deferred Tools",
    ):
        assert heading in packet
    assert "default: `off`" in lowered
    assert "tax-live-activation" in lowered
    assert "activation_authorized=false" in lowered
    assert "c:\\" not in packet
    assert "/home/" not in lowered
    assert "bearer " not in lowered
    assert "token=" not in lowered
    assert "sk-" not in lowered


def test_live_readback_is_aggregate_and_requires_enabled_safe_projection(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
    monkeypatch.setattr(
        "scripts.verify_tool_catalog_rollout.load_settings",
        lambda: {"disabled_tools": sorted(DEFAULT_DEFERRED_TOOLS)},
    )

    report = build_live_readback()

    assert report["status"] == "passed"
    assert report["feature_flag"]["enabled"] is True
    assert report["checks"]["deferred_tools_disabled"] is True
    assert report["checks"]["email_calendar_contacts_disabled"] is True
    assert report["diagnostics"] == {
        "aggregate_only": True,
        "settings_values_visible": False,
        "raw_content_visible": False,
        "secret_values_visible": False,
    }


def test_live_readback_fails_when_deferred_tools_would_be_enabled(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
    monkeypatch.setattr(
        "scripts.verify_tool_catalog_rollout.load_settings",
        lambda: {"disabled_tools": []},
    )

    report = build_live_readback()

    assert report["status"] == "failed"
    assert report["checks"]["deferred_tools_disabled"] is False
