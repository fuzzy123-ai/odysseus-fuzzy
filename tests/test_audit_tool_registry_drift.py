from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_tool_registry_drift.py"
SNAPSHOT_PATH = ROOT / "docs" / "plans" / "tool-taxonomy-inventory.json"

SPEC = importlib.util.spec_from_file_location("audit_tool_registry_drift", SCRIPT_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_inventory_reproduces_tax0_baseline() -> None:
    inventory = audit.build_inventory(ROOT)

    assert inventory["baseline"]["status"] == "matches"
    assert inventory["baseline"]["errors"] == []
    expected_counts = {
        "runtime_tags": 79,
        "function_schemas": 84,
        "schema_without_runtime_tag": 6,
        "runtime_without_function_schema": 1,
        "admin_metadata": 85,
        "runtime_without_admin_metadata": 0,
        "admin_catalog_without_runtime_tag": 6,
        "stale_admin_metadata": 0,
    }
    assert {key: inventory["counts"][key] for key in expected_counts} == expected_counts

    assert [item["tool_id"] for item in inventory["drift"]["schema_without_runtime_tag"]] == [
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    ]
    assert [item["tool_id"] for item in inventory["drift"]["runtime_without_function_schema"]] == [
        "generate_image"
    ]
    assert inventory["drift"]["stale_admin_metadata"] == []
    assert [
        item["tool_id"]
        for item in inventory["drift"]["admin_catalog_without_runtime_tag"]
    ] == [
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    ]
    assert inventory["admin_projection"] == {
        "mode": "descriptor_v2_api",
        "endpoint": "/api/tools",
        "legacy_static_metadata": False,
    }
    assert "manage_rag" not in inventory["projections"]["admin_metadata"]
    assert "const TOOL_META" not in (ROOT / "static" / "js" / "admin.js").read_text(
        encoding="utf-8"
    )


def test_inventory_is_deterministic_and_content_free() -> None:
    first = audit.build_inventory(ROOT)
    second = audit.build_inventory(ROOT)

    assert audit.render_inventory(first) == audit.render_inventory(second)
    assert first["privacy"] == {
        "private_paths_visible": False,
        "prompt_text_visible": False,
        "provider_output_visible": False,
        "raw_content_visible": False,
        "secret_values_visible": False,
        "tool_arguments_visible": False,
        "tool_results_visible": False,
    }
    serialized = audit.render_inventory(first)
    assert str(ROOT).casefold() not in serialized.casefold()
    assert "C:\\" not in serialized
    assert "sk-" not in serialized.casefold()
    assert "bearer " not in serialized.casefold()
    assert "telegram_bot_token=" not in serialized.casefold()


def test_source_hashes_are_relative_sorted_sha256_metadata() -> None:
    inventory = audit.build_inventory(ROOT)
    source_hashes = inventory["source_hashes_sha256"]

    assert list(source_hashes) == sorted(source_hashes)
    assert set(source_hashes) == set(audit.SOURCE_PATHS)
    assert all(not Path(path).is_absolute() and ":" not in path for path in source_hashes)
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in source_hashes.values())
    assert re.fullmatch(r"[0-9a-f]{64}", inventory["source_digest_sha256"])


def test_every_difference_has_a_bounded_classification() -> None:
    inventory = audit.build_inventory(ROOT)
    records = [item for items in inventory["drift"].values() for item in items]

    assert records
    assert {item["classification"] for item in records} <= {
        "intentional",
        "missing",
        "stale",
        "dynamic",
    }
    assert all(set(item) == {"tool_id", "classification", "reason_code"} for item in records)


def test_dynamic_registry_mcp_and_plugin_sources_are_inventory_only() -> None:
    dynamic = audit.build_inventory(ROOT)["dynamic_sources"]

    assert dynamic["registry"] == {
        "path": "src/tool_registry.py",
        "classification": "dynamic",
        "registration_mode": "runtime_ToolSpec_registry",
        "default_permission": "admin",
        "static_runtime_count": None,
    }
    assert dynamic["mcp"]["qualified_prefix"] == "mcp__"
    assert dynamic["mcp"]["classification"] == "dynamic"
    assert dynamic["plugin_registration_sources"]
    assert all(item["classification"] == "dynamic" for item in dynamic["plugin_registration_sources"])
    assert all(isinstance(item["registration_call_count"], int) for item in dynamic["plugin_registration_sources"])


def test_baseline_validator_rejects_unknown_drift() -> None:
    inventory = audit.build_inventory(ROOT)
    changed = deepcopy(inventory)
    changed["counts"]["runtime_tags"] += 1

    assert audit.validate_baseline(changed) == ["runtime_tags: expected 79, found 80"]


def test_snapshot_matches_current_checkout() -> None:
    inventory = audit.build_inventory(ROOT)

    assert audit.snapshot_errors(inventory, SNAPSHOT_PATH) == []


def test_snapshot_check_rejects_stale_or_missing_file(tmp_path: Path) -> None:
    inventory = audit.build_inventory(ROOT)
    missing = tmp_path / "missing.json"
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({"schema_version": "stale"}) + "\n", encoding="utf-8")

    assert audit.snapshot_errors(inventory, missing) == ["snapshot is missing"]
    assert audit.snapshot_errors(inventory, stale) == [
        "snapshot differs from deterministic repository inventory"
    ]
