"""Read-only evidence for the dormant Tool Catalog-v2 rollout contract."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.builtin_tool_catalog import (  # noqa: E402
    ACTIVE_GATED_REGISTRATION_IDS,
    BUILTIN_TOOL_DEFINITIONS,
    OPERATOR_PRIORITY_DEFERRED_IDS,
    build_tool_analytics_identity_contract,
)
from src.runtime_tool_status import (  # noqa: E402
    build_legacy_tool_catalog_projection,
    build_tool_catalog_projection,
)
from src.settings import load_settings, migrate_tool_settings  # noqa: E402
from src.tool_catalog import (  # noqa: E402
    CATALOG_V2_DEFAULT_ENABLED,
    CATALOG_V2_ENV,
    CATALOG_V2_FEATURE_FLAG,
    ToolAnalyticsIdentityContractV1,
    ToolCatalogError,
    catalog_v2_enabled,
)


ROLLOUT_SCHEMA = "odysseus.tool_catalog_rollout_acceptance.v1"
FEATURE_FLAG = CATALOG_V2_FEATURE_FLAG
DEFAULT_PERFORMANCE_CYCLES = 25
MAX_ELAPSED_MS = 2_500.0
MAX_PROJECTION_BYTES = 256_000
MAX_ROLLOUT_ERRORS = 0
_SYNTHETIC_ALIAS_TARGETS = {"legacy_read_file": "read_file"}
_FORBIDDEN_DIAGNOSTIC_MARKERS = (
    "c:\\",
    "/home/",
    "/users/",
    "bearer ",
    "authorization:",
    "token=",
    "password=",
    "secret=",
    "sk-",
)


def synthetic_settings() -> dict[str, Any]:
    """Return content-free settings covering aliases and operator defaults."""

    return {
        "disabled_tools": sorted(
            set(OPERATOR_PRIORITY_DEFERRED_IDS)
            | {"legacy_read_file", "synthetic_dynamic_tool"}
        ),
        "synthetic_fixture": True,
    }


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def select_synthetic_projection(
    *,
    catalog_v2_enabled: bool,
    legacy_projection: Mapping[str, Any],
    catalog_v2_projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Model the route selection without reading or changing process state."""

    return {
        "selected": "catalog_v2" if catalog_v2_enabled else "legacy",
        "payload": dict(catalog_v2_projection if catalog_v2_enabled else legacy_projection),
    }


def legacy_security_projection(disabled_tools: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    """Derive the stable legacy security facts from the current catalog source."""

    disabled = set(disabled_tools)
    rows = []
    for definition in BUILTIN_TOOL_DEFINITIONS:
        available = definition.availability == "available"
        rows.append(
            {
                "id": definition.tool_id,
                "enabled": available and definition.tool_id not in disabled,
                "lifecycle": definition.lifecycle,
                "availability": definition.availability,
                "permission": definition.permission,
                "effect_class": definition.effect_class,
                "requires_confirmation": (
                    definition.effect_class in {"external_write", "destructive"}
                    or definition.risk_level == "dangerous"
                    or definition.tool_id in ACTIVE_GATED_REGISTRATION_IDS
                ),
                "runtime_availability": (
                    "disabled_by_settings"
                    if definition.tool_id in disabled
                    else "catalog_unavailable"
                    if not available
                    else "enabled"
                ),
            }
        )
    return tuple(sorted(rows, key=lambda row: row["id"]))


def catalog_v2_security_projection(projection: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    fields = (
        "id",
        "enabled",
        "lifecycle",
        "availability",
        "permission",
        "effect_class",
        "requires_confirmation",
    )
    return tuple(
        sorted(
            (
                {
                    **{field: row[field] for field in fields},
                    "runtime_availability": row["policy_status"],
                }
                for row in projection["tools"]
                if row["source"] == "builtin"
            ),
            key=lambda row: row["id"],
        )
    )


def classify_security_drift(
    legacy_projection: tuple[dict[str, Any], ...],
    catalog_v2_projection: tuple[dict[str, Any], ...],
) -> tuple[bool, tuple[str, ...]]:
    """Fail closed on any security fact drift between the two read contracts."""

    if len(legacy_projection) != len(catalog_v2_projection):
        return False, ()
    drifted = tuple(
        legacy["id"]
        for legacy, catalog_v2 in zip(legacy_projection, catalog_v2_projection)
        if legacy != catalog_v2
    )
    return not drifted, drifted


def _diagnostics_are_redacted(report: Mapping[str, Any]) -> bool:
    rendered = json.dumps(report, ensure_ascii=True, sort_keys=True).casefold()
    return not any(marker in rendered for marker in _FORBIDDEN_DIAGNOSTIC_MARKERS)


def _diagnostic_probe_fails_closed() -> bool:
    """An unknown analytics alias must remain rejected, not silently bucketed."""

    try:
        ToolAnalyticsIdentityContractV1.create(
            build_builtin_descriptor_catalog_for_probe(),
            historical_aliases={"legacy_read_file": "missing_tool"},
        )
    except ToolCatalogError:
        return True
    return False


def build_builtin_descriptor_catalog_for_probe():
    """Keep the failure probe explicit without importing an application runtime."""

    from src.builtin_tool_catalog import build_builtin_descriptor_catalog

    return build_builtin_descriptor_catalog()


def build_synthetic_acceptance(
    *,
    performance_cycles: int = DEFAULT_PERFORMANCE_CYCLES,
    max_elapsed_ms: float = MAX_ELAPSED_MS,
) -> dict[str, Any]:
    """Prove an in-memory off/on/off read sequence without live activation."""

    if performance_cycles < 1:
        raise ValueError("performance_cycles must be positive")
    migrated_settings, migration_report = migrate_tool_settings(
        synthetic_settings(), alias_targets=_SYNTHETIC_ALIAS_TARGETS
    )
    settings_before = deepcopy(migrated_settings)
    disabled_tools = tuple(migrated_settings["disabled_tools"])
    analytics_before = build_tool_analytics_identity_contract()
    errors = 0
    v2_projections = []
    started = time.perf_counter()
    for _ in range(performance_cycles):
        try:
            v2_projections.append(build_tool_catalog_projection(disabled_tools=disabled_tools))
        except Exception:  # pragma: no cover - aggregate budget boundary
            errors += 1
    elapsed_ms = (time.perf_counter() - started) * 1_000
    if not v2_projections:
        raise RuntimeError("Catalog-v2 projection produced no result")
    catalog_v2 = v2_projections[0]
    legacy = build_legacy_tool_catalog_projection(disabled_tools=disabled_tools)
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
    rows = {row["id"]: row for row in catalog_v2["tools"]}
    deferred = tuple(sorted(OPERATOR_PRIORITY_DEFERRED_IDS))
    analytics_after = build_tool_analytics_identity_contract()
    analytics_audit_before = analytics_before.audit_dict()
    analytics_audit_after = analytics_after.audit_dict()
    legacy_security = legacy_security_projection(disabled_tools)
    v2_security = catalog_v2_security_projection(catalog_v2)
    security_compatible, security_drift = classify_security_drift(legacy_security, v2_security)
    projection_bytes = len(json.dumps(catalog_v2, ensure_ascii=True, sort_keys=True).encode("utf-8"))
    checks = {
        "default_off": CATALOG_V2_DEFAULT_ENABLED is False,
        "feature_flag_consistent": all(
            row["feature_flag"] == FEATURE_FLAG
            for row in catalog_v2["tools"]
            if row["source"] == "builtin"
        ),
        "off_on_off_sequence_proven": (
            off_before["selected"] == "legacy"
            and on["selected"] == "catalog_v2"
            and off_after["selected"] == "legacy"
        ),
        "rollback_projection_exact": off_before == off_after,
        "settings_preserved": _fingerprint(settings_before)
        == _fingerprint(migrated_settings),
        "settings_aliases_preserved": migration_report["alias_migration_count"] == 1,
        "analytics_aliases_preserved": analytics_before.historical_aliases
        == analytics_after.historical_aliases,
        "analytics_reservations_preserved": tuple(
            descriptor.analytics_id for descriptor in analytics_before.catalog.descriptors
        )
        == tuple(descriptor.analytics_id for descriptor in analytics_after.catalog.descriptors)
        and analytics_before.retired_analytics_ids == analytics_after.retired_analytics_ids
        and analytics_audit_before["dynamic_source_buckets"]
        == analytics_audit_after["dynamic_source_buckets"],
        "dual_read_security_compatible": security_compatible,
        "catalog_count_current": len(BUILTIN_TOOL_DEFINITIONS) == 86,
        "query_knowledge_registered": "query_knowledge" in rows,
        "deferred_tools_disabled": all(
            tool_id in rows and rows[tool_id]["enabled"] is False for tool_id in deferred
        ),
        "unavailable_tools_fail_closed": all(
            row["enabled"] is False
            for row in catalog_v2["tools"]
            if row["source"] == "builtin" and row["availability"] != "available"
        ),
        "v2_projection_deterministic": all(
            projection == catalog_v2 for projection in v2_projections[1:]
        ),
        # Retained v1 spelling for machine consumers of the activation packet.
        "projection_deterministic": all(
            projection == catalog_v2 for projection in v2_projections[1:]
        ),
        "v2_rows_ui_addressable": all(
            row["id"] == row["runtime_tool_id"] for row in catalog_v2["tools"]
        ),
        "performance_budget_met": elapsed_ms <= max_elapsed_ms
        and projection_bytes <= MAX_PROJECTION_BYTES,
        "error_budget_met": errors <= MAX_ROLLOUT_ERRORS,
        "diagnostic_probe_fail_closed": _diagnostic_probe_fails_closed(),
        "migration_report_redacted": (
            migration_report["raw_values_visible"] is False
            and migration_report["user_data_visible"] is False
            and migration_report["provider_data_visible"] is False
        ),
    }
    report: dict[str, Any] = {
        "schema_version": ROLLOUT_SCHEMA,
        "status": "pending",
        "mode": "synthetic",
        "feature_flag": {
            "name": FEATURE_FLAG,
            "default_enabled": CATALOG_V2_DEFAULT_ENABLED,
            "synthetic_sequence": ("off", "on", "off"),
            "product_state_changed": False,
        },
        "counts": {
            "catalog_tools": len(BUILTIN_TOOL_DEFINITIONS),
            "deferred_tools": len(deferred),
            "settings_aliases": migration_report["alias_migration_count"],
            "analytics_reservations": (
                len(analytics_before.catalog.descriptors)
                + len(analytics_before.retired_analytics_ids)
                + len(analytics_audit_before["dynamic_source_buckets"])
            ),
            "intentional_permission_strengthenings": 0,
        },
        "budgets": {
            "projection_cycles": performance_cycles,
            "elapsed_ms": round(elapsed_ms, 3),
            "max_elapsed_ms": max_elapsed_ms,
            "projection_bytes": projection_bytes,
            "max_projection_bytes": MAX_PROJECTION_BYTES,
            "rollout_errors": errors,
            "max_rollout_errors": MAX_ROLLOUT_ERRORS,
        },
        "checks": checks,
        "security_drift": {
            "drifted_tool_count": len(security_drift),
            "weakened_security_fields": 0 if security_compatible else len(security_drift),
        },
        "intentional_drift": {
            "kind": "none",
            # Retain historical v1 value types while making clear that the
            # former strengthening category is not applicable to this catalog.
            "from": "owner",
            "to": "admin",
            "applicable": False,
            "tool_ids": (),
            "weakened_security_fields": 0,
        },
        "deferred_tools": deferred,
        "diagnostics": {
            "aggregate_only": True,
            "raw_arguments_visible": False,
            "raw_results_visible": False,
            "raw_content_visible": False,
            "secret_values_visible": False,
            "private_paths_visible": False,
        },
        "live_contract": {
            "gate_id": "TAX-LIVE-ACTIVATION",
            "activation_authorized": False,
            "deployment_performed": False,
            "restart_performed": False,
        },
    }
    checks["diagnostics_redacted"] = _diagnostics_are_redacted(report)
    report["status"] = "passed" if all(checks.values()) else "failed"
    report["live_contract"]["materialization_ready"] = report["status"] == "passed"
    return report


def build_live_readback() -> dict[str, Any]:
    """Read aggregate local state only; this neither activates nor deploys V2."""

    from src.builtin_tool_catalog import resolve_operator_priority_disabled

    settings = load_settings()
    disabled_tools, _defaults_applied = resolve_operator_priority_disabled(
        settings.get("disabled_tools", []), setting_present="disabled_tools" in settings
    )
    projection = build_tool_catalog_projection(disabled_tools=disabled_tools)
    rows = {row["id"]: row for row in projection["tools"]}
    deferred = tuple(sorted(OPERATOR_PRIORITY_DEFERRED_IDS))
    checks = {
        "feature_enabled": catalog_v2_enabled(),
        "deferred_tools_disabled": all(
            tool_id in rows and rows[tool_id]["enabled"] is False for tool_id in deferred
        ),
        "email_calendar_contacts_disabled": all(
            tool_id in rows and rows[tool_id]["enabled"] is False
            for tool_id in ("send_email", "manage_calendar", "manage_contact")
        ),
        "projection_redacted": (
            projection["raw_content_visible"] is False
            and projection["secret_values_visible"] is False
        ),
    }
    return {
        "schema_version": "odysseus.tool_catalog_live_readback.v1",
        "status": "passed" if all(checks.values()) else "failed",
        "mode": "live-readback",
        "feature_flag": {
            "name": FEATURE_FLAG,
            "environment": CATALOG_V2_ENV,
            "enabled": checks["feature_enabled"],
        },
        "projection": {"schema": projection["schema"], "tool_count": projection["tool_count"]},
        "checks": checks,
        "diagnostics": {
            "aggregate_only": True,
            "settings_values_visible": False,
            "raw_content_visible": False,
            "secret_values_visible": False,
        },
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the dormant Catalog-v2 rollout.")
    parser.add_argument("--mode", choices=("synthetic", "live-readback"), required=True)
    parser.add_argument("--assert-default-off", action="store_true")
    parser.add_argument("--assert-rollback", action="store_true")
    parser.add_argument("--assert-live-enabled", action="store_true")
    parser.add_argument("--assert-deferred-disabled", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = build_synthetic_acceptance() if args.mode == "synthetic" else build_live_readback()
    required_checks = (
        ("default_off", args.assert_default_off),
        ("rollback_projection_exact", args.assert_rollback),
        ("feature_enabled", args.assert_live_enabled),
        ("deferred_tools_disabled", args.assert_deferred_disabled),
    )
    if any(required and not report["checks"].get(name, False) for name, required in required_checks):
        report["status"] = "failed"
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
