import json

import pytest

from src.agent_tools import TOOL_TAGS
from src.builtin_tool_catalog import (
    CONFIRMED_ROUTE_REGISTRATION_GAPS,
    DEFERRED_REGISTRATION_GAPS,
    REGISTRATION_GAPS,
    SECURITY_BLOCKED_REGISTRATION_GAPS,
    BuiltInRegistrationDisposition,
    builtin_spec,
    catalog_call_allowed,
    catalog_fenced_tool_names,
)
from src.tool_index import ALWAYS_AVAILABLE, ASSISTANT_ALWAYS_AVAILABLE
from src.tool_parsing import parse_tool_blocks
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block


def test_all_six_baseline_gaps_have_one_explicit_catalog_disposition():
    dispositions = {
        tool_id: builtin_spec(tool_id).registration_disposition
        for tool_id in REGISTRATION_GAPS
    }

    assert CONFIRMED_ROUTE_REGISTRATION_GAPS == {
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
    }
    assert DEFERRED_REGISTRATION_GAPS == {"manage_assistant", "manage_presets"}
    assert SECURITY_BLOCKED_REGISTRATION_GAPS == {"tail_serve_output"}
    assert set(dispositions) == REGISTRATION_GAPS
    assert {
        tool_id
        for tool_id, disposition in dispositions.items()
        if disposition == BuiltInRegistrationDisposition.CONFIRMED_ROUTE_ONLY
    } == CONFIRMED_ROUTE_REGISTRATION_GAPS
    assert {
        tool_id
        for tool_id, disposition in dispositions.items()
        if disposition == BuiltInRegistrationDisposition.DEFERRED
    } == DEFERRED_REGISTRATION_GAPS
    assert {
        tool_id
        for tool_id, disposition in dispositions.items()
        if disposition == BuiltInRegistrationDisposition.SECURITY_BLOCKED
    } == SECURITY_BLOCKED_REGISTRATION_GAPS


def test_catalog_admission_extends_legacy_runtime_tags_only_for_confirmed_routes():
    catalog_names = catalog_fenced_tool_names()

    assert len(TOOL_TAGS) == 78
    assert len(catalog_names) == 81
    assert catalog_names - set(TOOL_TAGS) == CONFIRMED_ROUTE_REGISTRATION_GAPS
    assert all(catalog_call_allowed(tool_id) for tool_id in TOOL_TAGS)
    assert not any(catalog_call_allowed(tool_id) for tool_id in DEFERRED_REGISTRATION_GAPS)
    assert not catalog_call_allowed("tail_serve_output")


@pytest.mark.parametrize("tool_id", sorted(CONFIRMED_ROUTE_REGISTRATION_GAPS))
def test_native_calls_for_confirmed_route_tools_are_not_rejected_as_unknown(tool_id):
    block = function_call_to_tool_block(tool_id, json.dumps({"action": "list"}))

    assert block is not None
    assert block.tool_type == tool_id
    assert json.loads(block.content) == {"action": "list"}


@pytest.mark.parametrize("tool_id", sorted(CONFIRMED_ROUTE_REGISTRATION_GAPS))
def test_fenced_calls_for_confirmed_route_tools_use_the_same_json_contract(tool_id):
    blocks = parse_tool_blocks(f"```{tool_id}\n{{\"action\": \"list\"}}\n```")

    assert len(blocks) == 1
    assert blocks[0].tool_type == tool_id
    assert json.loads(blocks[0].content) == {"action": "list"}


@pytest.mark.parametrize(
    "tool_id",
    sorted(DEFERRED_REGISTRATION_GAPS | SECURITY_BLOCKED_REGISTRATION_GAPS),
)
def test_deferred_and_security_blocked_gaps_remain_unselectable(tool_id):
    assert function_call_to_tool_block(tool_id, json.dumps({"action": "list"})) is None
    assert parse_tool_blocks(f"```{tool_id}\n{{\"action\": \"list\"}}\n```") == []


def test_all_six_dispositions_keep_native_schema_but_none_is_default_prompt_content():
    schema_names = {
        schema["function"]["name"] for schema in FUNCTION_TOOL_SCHEMAS
    }

    assert REGISTRATION_GAPS <= schema_names
    assert REGISTRATION_GAPS.isdisjoint(ALWAYS_AVAILABLE)
    assert REGISTRATION_GAPS.isdisjoint(ASSISTANT_ALWAYS_AVAILABLE)


def test_unknown_tool_still_fails_closed():
    assert function_call_to_tool_block("unregistered_tool", "{}") is None
