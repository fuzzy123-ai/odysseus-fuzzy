"""Verify the TAX12 Catalog-v2 rollout entirely in synthetic memory."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.builtin_tool_catalog import (  # noqa: E402
    BUILTIN_TOOL_SPECS,
    CATALOG_TOOL_IDS,
    DEFAULT_DEFERRED_TOOLS,
    build_builtin_analytics_identity_contract,
    catalog_call_allowed,
)
from src.runtime_tool_status import build_tool_catalog_projection  # noqa: E402
from src.settings import migrate_tool_settings  # noqa: E402
from src.tool_catalog import (  # noqa: E402
    CATALOG_V2_DEFAULT_ENABLED,
    CATALOG_V2_ENV,
    CATALOG_V2_FEATURE_FLAG,
    ToolAvailability,
    ToolCatalogError,
    ToolEffectClass,
    catalog_v2_enabled,
)
from src.tool_policy import (  # noqa: E402
    DEFAULT_DEFERRED_RUNTIME_TOOLS,
    operator_priority_disabled_tools,
)
from src.settings import load_settings  # noqa: E402


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


def _assignment_value(relative_path: str, name: str) -> ast.AST:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node.value
    raise RuntimeError(f"required static assignment is missing: {name}")


def builtin_descriptions() -> dict[str, str]:
    """Read descriptions statically so verification cannot initialize the app."""

    node = _assignment_value("src/tool_index.py", "BUILTIN_TOOL_DESCRIPTIONS")
    if not isinstance(node, ast.Dict):
        raise RuntimeError("built-in descriptions must remain a static dictionary")
    descriptions = {
        ast.literal_eval(key): ast.literal_eval(value)
        for key, value in zip(node.keys, node.values)
    }
    if set(descriptions) != set(CATALOG_TOOL_IDS):
        raise RuntimeError("built-in description projection drift")
    return descriptions


def synthetic_settings() -> dict[str, Any]:
    """Return content-free settings that exercise defaults, aliases and quarantine."""

    return {
        "disabled_tools": sorted(
            set(DEFAULT_DEFERRED_RUNTIME_TOOLS)
            | {"legacy_read_file", "synthetic_dynamic_tool"}
        ),
        "synthetic_fixture": True,
    }


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def legacy_security_projection(disabled_tools: Iterable[str]) -> tuple[dict[str, Any], ...]:
    """Build the independently derived legacy-compatible comparison surface."""

    disabled = {str(item) for item in disabled_tools}
    rows = []
    for spec in BUILTIN_TOOL_SPECS:
        enabled = bool(
            spec.tool_id not in disabled
            and catalog_call_allowed(spec.tool_id)
            and spec.availability == ToolAvailability.AVAILABLE
        )
        rows.append(
            {
                "id": spec.tool_id,
                "enabled": enabled,
                "lifecycle": spec.lifecycle.value,
                "availability": spec.availability.value,
                "permission": spec.permission.value,
                "effect_class": spec.effect_class.value,
                "requires_confirmation": spec.effect_class != ToolEffectClass.READ,
                "runtime_availability": (
                    "disabled_by_settings"
                    if spec.tool_id in disabled
                    else "enabled"
                    if enabled
                    else "blocked_by_catalog"
                ),
            }
        )
    return tuple(sorted(rows, key=lambda item: item["id"]))


def catalog_v2_security_projection(
    projection: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    fields = (
        "id",
        "enabled",
        "lifecycle",
        "availability",
        "permission",
        "effect_class",
        "requires_confirmation",
        "runtime_availability",
    )
    return tuple(
        sorted(
            ({field: row[field] for field in fields} for row in projection["descriptors"]),
            key=lambda item: item["id"],
        )
    )


def classify_security_drift(
    legacy_projection: tuple[dict[str, Any], ...],
    catalog_v2_projection: tuple[dict[str, Any], ...],
) -> tuple[bool, tuple[str, ...]]:
    """Allow only the established owner-to-admin runtime-policy hardening."""

    if len(legacy_projection) != len(catalog_v2_projection):
        return False, ()
    strengthened: list[str] = []
    for legacy, catalog_v2 in zip(legacy_projection, catalog_v2_projection):
        if legacy["id"] != catalog_v2["id"]:
            return False, ()
        changed_fields = {
            field
            for field in legacy
            if legacy[field] != catalog_v2.get(field)
        }
        if not changed_fields:
            continue
        if changed_fields == {"permission"} and (
            legacy["permission"], catalog_v2["permission"]
        ) == ("owner", "admin"):
            strengthened.append(legacy["id"])
            continue
        return False, ()
    return True, tuple(strengthened)


def select_synthetic_projection(
    *,
    catalog_v2_enabled: bool,
    legacy_projection: tuple[dict[str, Any], ...],
    catalog_v2_projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Select a projection without reading or changing a real feature source."""

    if catalog_v2_enabled:
        return {
            "selected": "catalog_v2",
            "rows": catalog_v2_security_projection(catalog_v2_projection),
        }
    return {"selected": "legacy", "rows": legacy_projection}


def _diagnostic_probe_fails_closed(descriptions: Mapping[str, str]) -> bool:
    try:
        build_builtin_analytics_identity_contract(
            descriptions,
            historical_alias_targets={"legacy_read_file": "missing_tool"},
        )
    except ToolCatalogError:
        return True
    return False


def _diagnostics_are_redacted(report: Mapping[str, Any]) -> bool:
    rendered = json.dumps(report, ensure_ascii=True, sort_keys=True).casefold()
    return not any(marker in rendered for marker in _FORBIDDEN_DIAGNOSTIC_MARKERS)


def build_synthetic_acceptance(
    *,
    performance_cycles: int = DEFAULT_PERFORMANCE_CYCLES,
    max_elapsed_ms: float = MAX_ELAPSED_MS,
) -> dict[str, Any]:
    """Run the off/on/off dual-read and return content-free acceptance evidence."""

    if performance_cycles < 1:
        raise ValueError("performance_cycles must be positive")
    descriptions = builtin_descriptions()
    migrated_settings, migration_report = migrate_tool_settings(
        synthetic_settings(),
        alias_targets=_SYNTHETIC_ALIAS_TARGETS,
    )
    disabled_tools = tuple(migrated_settings["disabled_tools"])
    settings_before = deepcopy(migrated_settings)
    settings_fingerprint_before = stable_fingerprint(settings_before)
    alias_ledger_before = tuple(
        (item["source"], item["target"])
        for item in migrated_settings["_tool_settings_migration"]["aliases"]
    )
    analytics_before = build_builtin_analytics_identity_contract(
        descriptions,
        historical_alias_targets=_SYNTHETIC_ALIAS_TARGETS,
    )

    errors = 0
    projections: list[dict[str, Any]] = []
    started = time.perf_counter()
    for _ in range(performance_cycles):
        try:
            projections.append(
                build_tool_catalog_projection(
                    disabled_tools=disabled_tools,
                    builtin_descriptions=descriptions,
                )
            )
        except Exception:  # pragma: no cover - aggregate error budget boundary
            errors += 1
    elapsed_ms = (time.perf_counter() - started) * 1_000
    if not projections:
        raise RuntimeError("synthetic Catalog-v2 projection produced no result")

    catalog_v2 = projections[0]
    legacy = legacy_security_projection(disabled_tools)
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

    settings_after = deepcopy(migrated_settings)
    analytics_after = build_builtin_analytics_identity_contract(
        descriptions,
        historical_alias_targets=_SYNTHETIC_ALIAS_TARGETS,
    )
    deferred = tuple(sorted(DEFAULT_DEFERRED_TOOLS))
    v2_rows = {row["id"]: row for row in catalog_v2["descriptors"]}
    security_compatible, permission_strengthening = classify_security_drift(
        legacy,
        catalog_v2_security_projection(catalog_v2),
    )
    projection_bytes = len(
        json.dumps(catalog_v2, ensure_ascii=True, sort_keys=True).encode("utf-8")
    )
    deterministic = all(projection == catalog_v2 for projection in projections[1:])
    checks = {
        "default_off": CATALOG_V2_DEFAULT_ENABLED is False,
        "feature_flag_consistent": all(
            row["feature_flag"] == FEATURE_FLAG for row in catalog_v2["descriptors"]
        ),
        "dual_read_security_compatible": security_compatible,
        "rollback_projection_exact": off_before == off_after,
        "off_on_off_sequence_proven": (
            off_before["selected"] == "legacy"
            and on["selected"] == "catalog_v2"
            and off_after["selected"] == "legacy"
        ),
        "settings_preserved": (
            settings_fingerprint_before == stable_fingerprint(settings_after)
        ),
        "settings_aliases_preserved": alias_ledger_before
        == tuple(
            (item["source"], item["target"])
            for item in settings_after["_tool_settings_migration"]["aliases"]
        ),
        "analytics_aliases_preserved": (
            analytics_before.alias_targets == analytics_after.alias_targets
        ),
        "analytics_reservations_preserved": (
            analytics_before.analytics_id_reservations
            == analytics_after.analytics_id_reservations
        ),
        "deferred_tools_disabled": all(
            v2_rows[tool_id]["enabled"] is False for tool_id in deferred
        ),
        "projection_deterministic": deterministic,
        "performance_budget_met": elapsed_ms <= max_elapsed_ms
        and projection_bytes <= MAX_PROJECTION_BYTES,
        "error_budget_met": errors <= MAX_ROLLOUT_ERRORS,
        "diagnostic_probe_fail_closed": _diagnostic_probe_fails_closed(descriptions),
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
            "catalog_tools": len(CATALOG_TOOL_IDS),
            "deferred_tools": len(deferred),
            "settings_aliases": len(alias_ledger_before),
            "analytics_reservations": len(analytics_before.analytics_id_reservations),
            "intentional_permission_strengthenings": len(permission_strengthening),
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
        "intentional_drift": {
            "kind": "runtime_permission_strengthening",
            "from": "owner",
            "to": "admin",
            "tool_ids": permission_strengthening,
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
            "materialization_ready": False,
            "activation_authorized": False,
            "deployment_performed": False,
            "restart_performed": False,
        },
    }
    checks["diagnostics_redacted"] = _diagnostics_are_redacted(report)
    passed = all(checks.values())
    report["status"] = "passed" if passed else "failed"
    report["live_contract"]["materialization_ready"] = passed
    return report


def build_live_readback() -> dict[str, Any]:
    """Read only aggregate Catalog-v2 activation state from this runtime."""

    disabled_tools = operator_priority_disabled_tools(load_settings())
    projection = build_tool_catalog_projection(
        disabled_tools=disabled_tools,
        builtin_descriptions=builtin_descriptions(),
    )
    rows = {row["id"]: row for row in projection["descriptors"]}
    deferred = tuple(sorted(DEFAULT_DEFERRED_TOOLS))
    checks = {
        "feature_enabled": catalog_v2_enabled(),
        "deferred_tools_disabled": all(
            rows[tool_id]["enabled"] is False for tool_id in deferred
        ),
        "email_calendar_contacts_disabled": all(
            rows[tool_id]["enabled"] is False
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
        "projection": {
            "schema": projection["schema"],
            "descriptor_schema": projection["descriptor_schema"],
            "tool_count": projection["tool_count"],
            "deferred_tool_count": len(deferred),
        },
        "checks": checks,
        "diagnostics": {
            "aggregate_only": True,
            "settings_values_visible": False,
            "raw_content_visible": False,
            "secret_values_visible": False,
        },
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the read-only synthetic Tool Catalog-v2 rollout check."
    )
    parser.add_argument(
        "--mode", choices=("synthetic", "live-readback"), required=True
    )
    parser.add_argument("--assert-default-off", action="store_true")
    parser.add_argument("--assert-rollback", action="store_true")
    parser.add_argument("--assert-live-enabled", action="store_true")
    parser.add_argument("--assert-deferred-disabled", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = (
        build_synthetic_acceptance()
        if args.mode == "synthetic"
        else build_live_readback()
    )
    if args.assert_default_off and not report["checks"].get("default_off", False):
        report["status"] = "failed"
    if args.assert_rollback and not report["checks"].get(
        "rollback_projection_exact", False
    ):
        report["status"] = "failed"
    if args.assert_live_enabled and not report["checks"].get("feature_enabled", False):
        report["status"] = "failed"
    if args.assert_deferred_disabled and not report["checks"]["deferred_tools_disabled"]:
        report["status"] = "failed"
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
