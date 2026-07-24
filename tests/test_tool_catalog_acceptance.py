import json
import subprocess
import sys
import time
from pathlib import Path

from src.builtin_tool_catalog import (
    BUILTIN_TOOL_DEFINITIONS,
    EMAIL_ADAPTER_TOOL_IDS,
    INDEX_INJECTED_PROMPT_IDS,
    NON_NATIVE_SCHEMA_IDS,
    OPERATOR_PRIORITY_DEFERRED_IDS,
    RUNTIME_REGISTRATION_GAPS,
    build_builtin_descriptor_catalog,
    build_tool_analytics_identity_contract,
    expected_projection_sets,
)
from src.runtime_tool_status import build_tool_catalog_projection


ROOT = Path(__file__).resolve().parents[1]


def test_current_builtin_catalog_is_complete_and_has_the_expected_projection_baseline():
    definitions = BUILTIN_TOOL_DEFINITIONS
    ids = {definition.tool_id for definition in definitions}

    assert len(definitions) == len(ids) == 86
    assert "query_knowledge" in ids
    expected = expected_projection_sets()
    assert {name: len(tool_ids) for name, tool_ids in expected.items()} == {
        "runtime_tags": 80,
        "function_schemas": 85,
        "tool_index": 86,
        "prompt_sections": 69,
        "dispatcher": 77,
    }
    assert RUNTIME_REGISTRATION_GAPS == {
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    }
    assert ids - set(expected["runtime_tags"]) == set(RUNTIME_REGISTRATION_GAPS)
    assert NON_NATIVE_SCHEMA_IDS == {"generate_image"}
    assert ids - set(expected["function_schemas"]) == set(NON_NATIVE_SCHEMA_IDS)
    assert len(INDEX_INJECTED_PROMPT_IDS) == 17
    assert ids - set(expected["prompt_sections"]) == set(INDEX_INJECTED_PROMPT_IDS)
    assert len(EMAIL_ADAPTER_TOOL_IDS) == 9
    assert ids - set(expected["dispatcher"]) == set(EMAIL_ADAPTER_TOOL_IDS)


def test_descriptor_projection_and_analytics_use_the_same_current_catalog():
    descriptors = build_builtin_descriptor_catalog()
    analytics = build_tool_analytics_identity_contract()
    projection = build_tool_catalog_projection(
        disabled_tools=OPERATOR_PRIORITY_DEFERRED_IDS
    )

    assert len(descriptors.descriptors) == 86
    assert len(analytics.catalog.descriptors) == 86
    assert tuple(descriptor.tool_id for descriptor in descriptors.descriptors) == tuple(
        descriptor.tool_id for descriptor in analytics.catalog.descriptors
    )
    assert projection["tool_count"] == 86
    assert {row["id"] for row in projection["tools"]} == {
        definition.tool_id for definition in BUILTIN_TOOL_DEFINITIONS
    }
    assert all(row["id"] == row["runtime_tool_id"] for row in projection["tools"])
    assert {
        row["feature_flag"]
        for row in projection["tools"]
        if row["source"] == "builtin"
    } == {"tool-catalog-v2"}


def test_operator_priority_deferred_tools_remain_disabled_and_unavailable_never_enable():
    projection = build_tool_catalog_projection(
        disabled_tools=OPERATOR_PRIORITY_DEFERRED_IDS
    )
    rows = {row["id"]: row for row in projection["tools"]}

    assert len(OPERATOR_PRIORITY_DEFERRED_IDS) == 14
    assert all(rows[tool_id]["enabled"] is False for tool_id in OPERATOR_PRIORITY_DEFERRED_IDS)
    assert all(
        row["enabled"] is False
        for row in rows.values()
        if row["availability"] != "available"
    )
    assert rows["tail_serve_output"]["settings_toggle_allowed"] is False
    assert rows["tail_serve_output"]["settings_mutable"] is False


def test_catalog_import_and_projection_are_bounded_and_redacted():
    code = (
        "import sys; import src.tool_catalog; import src.builtin_tool_catalog; "
        "import src.runtime_tool_status; "
        "assert 'src.tool_index' not in sys.modules; "
        "assert 'src.embedding_lanes' not in sys.modules; "
        "assert 'src.agent_tools' not in sys.modules; "
        "assert 'core.database' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    started = time.perf_counter()
    projections = [build_tool_catalog_projection() for _ in range(5)]
    elapsed = time.perf_counter() - started
    rendered = json.dumps(projections[0], sort_keys=True).casefold()

    assert elapsed < 2.5
    assert all(projection == projections[0] for projection in projections[1:])
    assert len(json.dumps(projections[0], sort_keys=True).encode("utf-8")) < 256_000
    for marker in ("c:\\", "/home/", "bearer ", "token=", "password=", "sk-"):
        assert marker not in rendered
