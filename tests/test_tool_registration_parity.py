from __future__ import annotations

import json

import pytest

from src.agent_tools import TOOL_TAGS
from src.builtin_tool_catalog import (
    ACTIVE_GATED_REGISTRATION_IDS,
    BLOCKED_REGISTRATION_IDS,
    DEFERRED_REGISTRATION_IDS,
    PARSER_REGISTERED_TOOL_IDS,
    RUNTIME_REGISTRATION_GAPS,
    build_builtin_descriptor_catalog,
    definitions_by_id,
)
from src.tool_parsing import parse_tool_blocks
from src.tool_schema_definitions import FUNCTION_TOOL_SCHEMAS
from src.tool_schemas import function_call_to_tool_block


def test_all_six_former_gaps_have_one_explicit_registration_disposition():
    assert ACTIVE_GATED_REGISTRATION_IDS == {
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
    }
    assert DEFERRED_REGISTRATION_IDS == {"manage_assistant", "manage_presets"}
    assert BLOCKED_REGISTRATION_IDS == {"tail_serve_output"}
    assert (
        ACTIVE_GATED_REGISTRATION_IDS
        | DEFERRED_REGISTRATION_IDS
        | BLOCKED_REGISTRATION_IDS
    ) == RUNTIME_REGISTRATION_GAPS
    assert not (
        ACTIVE_GATED_REGISTRATION_IDS & DEFERRED_REGISTRATION_IDS
        or ACTIVE_GATED_REGISTRATION_IDS & BLOCKED_REGISTRATION_IDS
        or DEFERRED_REGISTRATION_IDS & BLOCKED_REGISTRATION_IDS
    )


def test_parser_registration_adds_only_the_three_active_gated_tools():
    assert PARSER_REGISTERED_TOOL_IDS == frozenset(TOOL_TAGS) | ACTIVE_GATED_REGISTRATION_IDS
    assert len(PARSER_REGISTERED_TOOL_IDS) == 82
    assert PARSER_REGISTERED_TOOL_IDS.isdisjoint(
        DEFERRED_REGISTRATION_IDS | BLOCKED_REGISTRATION_IDS
    )


@pytest.mark.parametrize(
    ("tool_id", "arguments"),
    [
        ("manage_embeddings", {"action": "list"}),
        ("manage_personal_docs", {"action": "list"}),
        ("manage_plugins", {"action": "list"}),
    ],
)
def test_native_calls_for_active_gated_tools_are_not_rejected_as_unknown(
    tool_id: str,
    arguments: dict,
):
    block = function_call_to_tool_block(tool_id, json.dumps(arguments))
    assert block is not None
    assert block.tool_type == tool_id
    assert json.loads(block.content) == arguments


@pytest.mark.parametrize("tool_id", sorted(ACTIVE_GATED_REGISTRATION_IDS))
def test_fenced_calls_for_active_gated_tools_are_registered(tool_id: str):
    blocks = parse_tool_blocks(f'```{tool_id}\n{{"action":"list"}}\n```')
    assert len(blocks) == 1
    assert blocks[0].tool_type == tool_id
    assert json.loads(blocks[0].content) == {"action": "list"}


@pytest.mark.parametrize(
    "tool_id",
    sorted(DEFERRED_REGISTRATION_IDS | BLOCKED_REGISTRATION_IDS),
)
def test_deferred_and_blocked_tools_are_not_implicitly_parser_selected(tool_id: str):
    assert function_call_to_tool_block(tool_id, '{"action":"list"}') is None
    assert parse_tool_blocks(f'```{tool_id}\n{{"action":"list"}}\n```') == []


def test_all_six_keep_native_schemas_but_safe_descriptor_defaults():
    schema_ids = {
        item["function"]["name"]
        for item in FUNCTION_TOOL_SCHEMAS
    }
    assert RUNTIME_REGISTRATION_GAPS <= schema_ids

    definitions = definitions_by_id()
    catalog = build_builtin_descriptor_catalog()
    for tool_id in RUNTIME_REGISTRATION_GAPS:
        definition = definitions[tool_id]
        descriptor = catalog.resolve(tool_id)
        assert definition.registration_disposition in {
            "active-gated",
            "deferred",
            "blocked-until-tax5",
        }
        assert descriptor.default_enabled is False
        assert descriptor.default_visibility.value == "hidden"
        assert descriptor.availability.value == "disabled"

    for tool_id in ACTIVE_GATED_REGISTRATION_IDS:
        descriptor = catalog.resolve(tool_id)
        assert descriptor.permission.value == "admin"
        assert descriptor.requires_confirmation is True


def test_tail_serve_output_remains_blocked_until_tax5():
    definition = definitions_by_id()["tail_serve_output"]
    assert definition.registration_disposition == "blocked-until-tax5"
    assert definition.parser_registered is False
    assert "tail_serve_output" not in PARSER_REGISTERED_TOOL_IDS

