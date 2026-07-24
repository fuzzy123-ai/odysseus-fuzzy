import json
from pathlib import Path
import subprocess
import sys

from scripts.verify_tool_catalog_rollout import (
    CATALOG_V2_DEFAULT_ENABLED,
    FEATURE_FLAG,
    ROLLOUT_SCHEMA,
    build_live_readback,
    build_synthetic_acceptance,
    select_synthetic_projection,
)
from src.builtin_tool_catalog import BUILTIN_TOOL_DEFINITIONS, OPERATOR_PRIORITY_DEFERRED_IDS
from src.runtime_tool_status import (
    build_legacy_tool_catalog_projection,
    build_tool_catalog_projection,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_tool_catalog_rollout.py"


def test_synthetic_off_on_off_rollout_is_exact_and_non_mutating():
    disabled = tuple(sorted(OPERATOR_PRIORITY_DEFERRED_IDS))
    legacy = build_legacy_tool_catalog_projection(disabled_tools=disabled)
    catalog_v2 = build_tool_catalog_projection(disabled_tools=disabled)

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

    assert CATALOG_V2_DEFAULT_ENABLED is False
    assert FEATURE_FLAG == "tool-catalog-v2"
    assert off_before == off_after
    assert off_before["selected"] == "legacy"
    assert on["selected"] == "catalog_v2"
    assert len(on["payload"]["tools"]) == len(BUILTIN_TOOL_DEFINITIONS) == 86


def test_machine_acceptance_proves_current_catalog_deferred_and_rollback_contract():
    report = build_synthetic_acceptance(performance_cycles=2)

    assert report["schema_version"] == ROLLOUT_SCHEMA
    assert report["status"] == "passed"
    assert report["counts"] == {
        "catalog_tools": 86,
        "deferred_tools": 14,
        "settings_aliases": 1,
        "analytics_reservations": 90,
        "intentional_permission_strengthenings": 0,
    }
    for check in (
        "default_off",
        "feature_flag_consistent",
        "off_on_off_sequence_proven",
        "rollback_projection_exact",
        "settings_preserved",
        "settings_aliases_preserved",
        "analytics_aliases_preserved",
        "analytics_reservations_preserved",
        "query_knowledge_registered",
        "deferred_tools_disabled",
        "unavailable_tools_fail_closed",
        "v2_rows_ui_addressable",
        "diagnostic_probe_fail_closed",
        "migration_report_redacted",
        "diagnostics_redacted",
    ):
        assert report["checks"][check] is True


def test_v2_projection_is_redacted_and_unavailable_rows_are_never_enabled():
    projection = build_tool_catalog_projection()

    assert projection["schema"] == "odysseus.tool_catalog_projection.v2"
    assert projection["tool_count"] == 86
    assert all(row["id"] == row["runtime_tool_id"] for row in projection["tools"])
    assert all(
        row["enabled"] is False
        for row in projection["tools"]
        if row["source"] == "builtin" and row["availability"] != "available"
    )
    assert projection["raw_schema_visible"] is False
    assert projection["secret_values_visible"] is False


def test_cli_required_command_emits_machine_readable_pass():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "synthetic",
            "--assert-default-off",
            "--assert-rollback",
            "--assert-deferred-disabled",
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


def test_live_readback_requires_explicit_enablement_and_safe_projection(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
    monkeypatch.setattr(
        "scripts.verify_tool_catalog_rollout.load_settings",
        lambda: {"disabled_tools": sorted(OPERATOR_PRIORITY_DEFERRED_IDS)},
    )

    report = build_live_readback()

    assert report["status"] == "passed"
    assert report["feature_flag"]["enabled"] is True
    assert report["checks"]["deferred_tools_disabled"] is True
    assert report["checks"]["email_calendar_contacts_disabled"] is True


def test_live_readback_fails_when_deferred_tools_would_be_enabled(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
    monkeypatch.setattr(
        "scripts.verify_tool_catalog_rollout.load_settings",
        lambda: {"disabled_tools": []},
    )

    report = build_live_readback()

    assert report["status"] == "failed"
    assert report["checks"]["deferred_tools_disabled"] is False
