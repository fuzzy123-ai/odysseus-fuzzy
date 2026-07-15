import ast
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.audit_tool_registry_drift import build_inventory
from src.builtin_tool_catalog import (
    BUILTIN_TOOL_SPECS,
    CATALOG_TOOL_IDS,
    NON_NATIVE_SCHEMA_TOOLS,
    REGISTRATION_GAPS,
    RAG_ONLY_PROMPT_TOOLS,
    BuiltInCatalogError,
    build_builtin_descriptors,
    builtin_catalog_audit_summary,
    validate_builtin_projections,
)
from src.tool_catalog import (
    ToolAvailability,
    ToolEffectClass,
    ToolLifecycle,
    ToolVisibility,
)


ROOT = Path(__file__).resolve().parents[1]


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found")


def _projections():
    return build_inventory(ROOT)["projections"]


def _projection_kwargs():
    projections = _projections()
    return {
        "runtime_tags": projections["runtime_tags"],
        "function_schemas": projections["function_schemas"],
        "tool_index_entries": projections["tool_index_entries"],
        "prompt_sections": projections["prompt_sections"],
        "agent_handlers": projections["agent_handlers"],
        "dispatcher_condition_ids": projections["dispatcher_condition_ids"],
    }


def _descriptions():
    return _literal_assignment(ROOT / "src" / "tool_index.py", "BUILTIN_TOOL_DESCRIPTIONS")


def test_catalog_is_the_complete_unique_union_of_current_builtin_projections():
    projections = _projections()
    projection_union = set().union(
        projections["runtime_tags"],
        projections["function_schemas"],
        projections["tool_index_entries"],
        projections["prompt_sections"],
    )

    assert len(CATALOG_TOOL_IDS) == 84
    assert len(set(CATALOG_TOOL_IDS)) == 84
    assert {spec.tool_id for spec in BUILTIN_TOOL_SPECS} == set(CATALOG_TOOL_IDS)
    assert projection_union == set(CATALOG_TOOL_IDS)


def test_all_consumer_projections_strictly_validate_against_the_catalog():
    validate_builtin_projections(**_projection_kwargs())


@pytest.mark.parametrize(
    ("projection", "tool_id"),
    [
        ("runtime_tags", "read_file"),
        ("function_schemas", "read_file"),
        ("tool_index_entries", "read_file"),
        ("prompt_sections", "read_file"),
    ],
)
def test_removing_any_required_consumer_projection_fails_closed(projection, tool_id):
    values = _projection_kwargs()
    values[projection] = set(values[projection]) - {tool_id}

    with pytest.raises(BuiltInCatalogError):
        validate_builtin_projections(**values)


def test_runtime_tool_without_handler_or_dispatcher_fails_closed():
    values = _projection_kwargs()
    values["agent_handlers"] = set(values["agent_handlers"]) - {"read_file"}
    values["dispatcher_condition_ids"] = set(values["dispatcher_condition_ids"]) - {
        "read_file"
    }

    with pytest.raises(BuiltInCatalogError):
        validate_builtin_projections(**values)


def test_registration_and_non_native_exceptions_are_explicit_and_default_off():
    by_id = {spec.tool_id: spec for spec in BUILTIN_TOOL_SPECS}

    assert REGISTRATION_GAPS == {
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    }
    assert NON_NATIVE_SCHEMA_TOOLS == {"generate_image"}
    for tool_id in REGISTRATION_GAPS:
        spec = by_id[tool_id]
        assert spec.runtime_registered is False
        assert spec.availability == ToolAvailability.BLOCKED
        assert spec.lifecycle in {ToolLifecycle.DEFERRED, ToolLifecycle.BLOCKED}
        assert "registration_gap_deferred_to_TAX3" in spec.projection_exceptions
    assert by_id["generate_image"].native_schema is False
    assert "text_only_no_native_schema" in by_id["generate_image"].projection_exceptions


def test_rag_only_prompt_exceptions_are_exact_and_still_searchable():
    by_id = {spec.tool_id: spec for spec in BUILTIN_TOOL_SPECS}

    assert len(RAG_ONLY_PROMPT_TOOLS) == 16
    for tool_id in RAG_ONLY_PROMPT_TOOLS:
        spec = by_id[tool_id]
        assert spec.searchable_index is True
        assert spec.dedicated_prompt_section is False
        assert "rag_index_only_no_dedicated_prompt_section" in spec.projection_exceptions


def test_descriptor_projection_is_complete_conservative_and_content_free_in_audit():
    index = build_builtin_descriptors(_descriptions())
    descriptors = index.descriptors

    assert len(descriptors) == 84
    assert {item.tool_id for item in descriptors} == set(CATALOG_TOOL_IDS)
    assert all(item.default_enabled is False for item in descriptors)
    assert all(len(item.description) <= 160 for item in descriptors)
    assert all("/" not in item.description and "\\" not in item.description for item in descriptors)
    assert index.resolve("api_call").display_name == "API call"
    assert index.resolve("manage_mcp").display_name == "Manage MCP"
    for descriptor in descriptors:
        if descriptor.tool_id in REGISTRATION_GAPS:
            assert descriptor.default_visibility == ToolVisibility.BLOCKED
        if descriptor.effect_class != ToolEffectClass.READ:
            assert descriptor.requires_confirmation is True

    audit = builtin_catalog_audit_summary()
    assert audit["counts"] == {
        "catalog": 84,
        "runtime_registered": 78,
        "native_schema": 83,
        "searchable_index": 84,
        "dedicated_prompt_section": 68,
        "registration_gaps": 6,
    }
    assert audit["raw_content_visible"] is False
    assert audit["callable_visible"] is False
    assert audit["tool_arguments_visible"] is False
    assert audit["tool_results_visible"] is False
    assert audit["secret_values_visible"] is False


def test_description_projection_drift_fails_closed():
    descriptions = _descriptions()
    descriptions.pop("read_file")
    with pytest.raises(BuiltInCatalogError):
        build_builtin_descriptors(descriptions)


def test_catalog_import_does_not_load_embedding_or_agent_startup_modules():
    code = (
        "import sys; import src.builtin_tool_catalog; "
        "assert 'src.tool_index' not in sys.modules; "
        "assert 'src.embedding_lanes' not in sys.modules; "
        "assert 'src.agent_tools' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
