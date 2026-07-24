from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.audit_tool_registry_drift import audit_inventory
from src.builtin_tool_catalog import (
    BUILTIN_TOOL_DEFINITIONS,
    EMAIL_ADAPTER_TOOL_IDS,
    INDEX_INJECTED_PROMPT_IDS,
    INTERNAL_DISPATCH_CONTROL_IDS,
    NON_NATIVE_SCHEMA_IDS,
    RUNTIME_REGISTRATION_GAPS,
    STATIC_PROMPT_BASELINE_CHARACTERS,
    BuiltinProjectionSnapshot,
    CatalogProjectionError,
    build_builtin_descriptor_catalog,
    catalog_audit_summary,
    definitions_by_id,
    expected_projection_sets,
    validate_builtin_projections,
)


ROOT = Path(__file__).resolve().parents[1]


def _inventory_surfaces() -> dict[str, frozenset[str]]:
    payload = audit_inventory(ROOT)
    assert payload["summary"]["clean"] is True
    return {
        row["name"]: frozenset(row["tool_ids"])
        for row in payload["surfaces"]
    }


def _current_snapshot() -> BuiltinProjectionSnapshot:
    surfaces = _inventory_surfaces()
    return BuiltinProjectionSnapshot.create(
        runtime_tags=surfaces["builtin_tags"],
        function_schemas=surfaces["function_schemas"],
        tool_index=surfaces["tool_index"],
        prompt_sections=surfaces["prompt_sections"],
        dispatcher=surfaces["dispatcher"],
    )


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found in {path}")


def test_catalog_is_sorted_unique_and_has_the_frozen_projection_counts():
    tool_ids = tuple(item.tool_id for item in BUILTIN_TOOL_DEFINITIONS)
    assert tool_ids == tuple(sorted(tool_ids))
    assert len(tool_ids) == len(set(tool_ids)) == 86
    assert {name: len(ids) for name, ids in expected_projection_sets().items()} == {
        "runtime_tags": 80, "function_schemas": 85, "tool_index": 86, "prompt_sections": 69, "dispatcher": 77,
    }


def test_every_current_static_projection_is_strictly_validated_by_the_catalog():
    report = validate_builtin_projections(_current_snapshot())
    assert report.clean is True
    assert dict(report.actual_counts) == {
        "dispatcher": 83, "function_schemas": 85, "prompt_sections": 69, "runtime_tags": 80, "tool_index": 86,
    }
    report.assert_valid()


def test_removing_one_projection_makes_the_focused_validator_red():
    snapshot = _current_snapshot()
    removed = replace(
        snapshot,
        runtime_tags=snapshot.runtime_tags - {"read_file"},
    )
    report = validate_builtin_projections(removed)
    assert report.clean is False
    assert any(
        issue.surface == "runtime_tags"
        and issue.relation == "missing"
        and issue.tool_ids == ("read_file",)
        for issue in report.issues
    )
    with pytest.raises(CatalogProjectionError, match="read_file"):
        report.assert_valid()


def test_unknown_projection_identity_is_rejected_but_control_sentinels_are_explicit():
    snapshot = _current_snapshot()
    assert INTERNAL_DISPATCH_CONTROL_IDS <= snapshot.dispatcher
    report = validate_builtin_projections(
        replace(snapshot, tool_index=snapshot.tool_index | {"unknown_builtin"})
    )
    assert any(
        issue.surface == "tool_index"
        and issue.relation == "unexpected"
        and issue.tool_ids == ("unknown_builtin",)
        for issue in report.issues
    )


def test_projection_exceptions_are_explicit_and_exact():
    definitions = definitions_by_id()
    assert {
        tool_id
        for tool_id, definition in definitions.items()
        if not definition.runtime_registered
    } == RUNTIME_REGISTRATION_GAPS
    assert {
        tool_id
        for tool_id, definition in definitions.items()
        if not definition.native_schema
    } == NON_NATIVE_SCHEMA_IDS
    assert {
        tool_id
        for tool_id, definition in definitions.items()
        if not definition.static_prompt_section
    } == INDEX_INJECTED_PROMPT_IDS
    assert {
        tool_id
        for tool_id, definition in definitions.items()
        if definition.handler_projection == "email_adapter"
    } == EMAIL_ADAPTER_TOOL_IDS
    assert definitions["generate_image"].projection_exceptions == (
        ("function_schemas", "non-native-image-schema-adapter"),
    )
    assert (
        "dispatcher",
        "qualified-email-schema-adapter",
    ) in definitions["send_email"].projection_exceptions


def test_descriptor_v2_projection_is_complete_safe_and_alias_stable():
    catalog = build_builtin_descriptor_catalog()
    assert len(catalog.descriptors) == 86
    assert catalog.resolve("manage_rag").tool_id == "manage_personal_docs"
    assert catalog.resolve("generate_image").native_schema is False
    assert (
        catalog.resolve("generate_image").projection_exception_reason
        == "non-native-image-schema-adapter"
    )
    for tool_id in RUNTIME_REGISTRATION_GAPS:
        descriptor = catalog.resolve(tool_id)
        assert descriptor.lifecycle.value == "deferred"
        assert descriptor.availability.value == "disabled"
        assert descriptor.default_enabled is False
        assert descriptor.default_visibility.value == "hidden"


def test_catalog_audit_summary_is_content_free():
    summary = catalog_audit_summary()
    assert summary["builtin_count"] == 86
    assert summary["raw_content_visible"] is False
    assert summary["schema_arguments_visible"] is False
    assert summary["secret_value_visible"] is False
    assert "description" not in summary


def test_catalog_import_does_not_pull_runtime_catalog_or_create_startup_cycle():
    source = (ROOT / "src" / "builtin_tool_catalog.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_internal_imports = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ImportFrom)
            and str(node.module or "").startswith("src")
        )
        or (
            isinstance(node, ast.Import)
            and any(alias.name.startswith("src") for alias in node.names)
        )
    ]
    assert top_level_internal_imports == []

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import src.builtin_tool_catalog; "
                "assert 'src.tool_catalog' not in sys.modules"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_static_prompt_projection_stays_inside_the_frozen_character_budget():
    sections = _literal_assignment(ROOT / "src" / "agent_loop_prompts.py", "TOOL_SECTIONS")
    prompt_characters = sum(len(tool_id) + len(text) for tool_id, text in sections.items())
    assert len(sections) == 69
    assert prompt_characters <= STATIC_PROMPT_BASELINE_CHARACTERS

