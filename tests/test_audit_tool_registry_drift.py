import copy
import json
import re
from pathlib import Path, PurePosixPath

from scripts.audit_tool_registry_drift import (
    CLASSIFICATIONS,
    EMAIL_SCHEMA_ADAPTER_TOOLS,
    EXPECTED_RUNTIME_WITHOUT_SCHEMA,
    EXPECTED_SCHEMA_WITHOUT_RUNTIME,
    EXPECTED_STALE_ADMIN_METADATA,
    _baseline_violations,
    _extract_admin_metadata_text,
    audit_inventory,
    main,
    render_inventory,
)


ROOT = Path(__file__).resolve().parents[1]


def _surface_map(report: dict) -> dict[str, set[str]]:
    return {
        item["name"]: set(item["tool_ids"])
        for item in report["surfaces"]
    }


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


def test_repository_inventory_reproduces_tax0_baseline():
    report = audit_inventory(ROOT)

    assert report["summary"]["clean"] is True
    assert report["summary"]["builtin_tag_count"] == 79
    assert report["summary"]["function_schema_count"] == 84
    assert report["summary"]["schema_without_runtime_count"] == 6
    assert report["summary"]["runtime_without_schema_count"] == 1
    assert report["summary"]["admin_metadata_count"] == 31
    assert report["summary"]["admin_fallback_count"] == 49
    assert report["violations"] == []


def test_known_runtime_schema_and_admin_differences_are_exact():
    surfaces = _surface_map(audit_inventory(ROOT))
    runtime = surfaces["builtin_tags"]
    schemas = surfaces["function_schemas"]
    admin = surfaces["admin_metadata"]

    assert schemas - runtime == EXPECTED_SCHEMA_WITHOUT_RUNTIME
    assert runtime - schemas == EXPECTED_RUNTIME_WITHOUT_SCHEMA
    assert admin - runtime == EXPECTED_STALE_ADMIN_METADATA
    assert len(runtime - admin) == 49


def test_every_difference_has_a_controlled_classification_and_explanation():
    report = audit_inventory(ROOT)

    assert report["differences"]
    assert {item["classification"] for item in report["differences"]} <= CLASSIFICATIONS
    assert all(item["explanation"] for item in report["differences"])
    assert any(item["classification"] == "intentional" for item in report["differences"])
    assert any(item["classification"] == "missing" for item in report["differences"])
    assert any(item["classification"] == "stale" for item in report["differences"])
    assert any(item["classification"] == "dynamic" for item in report["differences"])
    dispatcher_adapter_rows = {
        item["tool_id"]: item["classification"]
        for item in report["differences"]
        if item["surface"] == "dispatcher"
        and item["relation"] == "runtime_not_surface"
    }
    assert dispatcher_adapter_rows == {
        tool_id: "intentional" for tool_id in EMAIL_SCHEMA_ADAPTER_TOOLS
    }


def test_inventory_is_content_free_and_uses_only_repo_relative_paths():
    report = audit_inventory(ROOT)
    strings = list(_all_strings(report))

    assert report["summary"]["content_fields_recorded"] is False
    assert report["summary"]["private_paths_recorded"] is False
    assert report["summary"]["runtime_modules_imported"] is False
    assert not ({"arguments", "parameters", "prompt", "result", "raw_content"} & set(report))
    for item in report["sources"] + report["dynamic_sources"]:
        path = item["path"]
        assert not PurePosixPath(path).is_absolute()
        assert ".." not in PurePosixPath(path).parts
    assert all(str(ROOT).replace("\\", "/") not in value.replace("\\", "/") for value in strings)
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in report["sources"])


def test_rendering_is_byte_stable_and_sorted():
    first = audit_inventory(ROOT)
    second = audit_inventory(ROOT)

    assert first == second
    assert render_inventory(first).encode("utf-8") == render_inventory(second).encode("utf-8")
    assert first["surfaces"] == sorted(first["surfaces"], key=lambda item: item["name"])
    assert first["sources"] == sorted(
        first["sources"],
        key=lambda item: (item["path"], item["surface"], item["kind"]),
    )


def test_admin_metadata_parser_ignores_nested_fields():
    text = """
const TOOL_META = {
  alpha: { name: 'Alpha', desc: 'not a tool id' },
  'beta-tool': { name: 'Beta' },
};
"""

    assert _extract_admin_metadata_text(text) == {"alpha", "beta-tool"}


def test_unknown_baseline_drift_is_a_violation():
    report = audit_inventory(ROOT)
    surfaces = _surface_map(report)
    drifted = copy.deepcopy(surfaces)
    drifted["builtin_tags"].add("unexpected_tool")

    violations = _baseline_violations(drifted)

    assert {item["code"] for item in violations} == {
        "baseline_count_drift",
        "baseline_identity_drift",
    }


def test_check_mode_detects_persisted_snapshot_drift(tmp_path):
    output = tmp_path / "tool-taxonomy-inventory.json"
    assert main(["--root", str(ROOT), "--output", str(output)]) == 0
    assert main(["--root", str(ROOT), "--output", str(output), "--check"]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["summary"]["builtin_tag_count"] = 0
    output.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["--root", str(ROOT), "--output", str(output), "--check"]) == 1
