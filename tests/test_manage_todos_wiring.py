import inspect

from src.agent_loop import stream_agent_loop
from src.agent_loop_intent import _classify_agent_request, remove_memory_for_todo_domain
from src.agent_loop_prompts import _DOMAIN_RULES, _DOMAIN_TOOL_MAP, TOOL_SECTIONS
from src.agent_tools import TOOL_TAGS
from src.builtin_tool_catalog import build_builtin_descriptor_catalog
from src.tool_execution import _execute_tool_block_impl
from src.tool_implementations import do_manage_todos
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS, ToolIndex
from src.tool_parsing import _TOOL_NAME_MAP
from src.tool_policy import _COMMON_TOOL_NAMES
from src.tool_schema_definitions import FUNCTION_TOOL_SCHEMAS


def _todo_schema():
    return next(
        schema["function"]
        for schema in FUNCTION_TOOL_SCHEMAS
        if schema["function"]["name"] == "manage_todos"
    )


def _index_with_empty_retrieval():
    index = ToolIndex.__new__(ToolIndex)
    index.index_plugin_tools = lambda: None
    index.retrieve = lambda *_args, **_kwargs: set()
    return index


def test_todo_registration_surfaces_are_aligned():
    schema = _todo_schema()
    descriptor = build_builtin_descriptor_catalog().resolve("manage_todos")

    assert "manage_todos" in TOOL_TAGS
    assert "manage_todos" in _COMMON_TOOL_NAMES
    assert "manage_todos" in BUILTIN_TOOL_DESCRIPTIONS
    assert "todo mutations" in schema["description"].lower()
    assert "todo mutations" in BUILTIN_TOOL_DESCRIPTIONS["manage_todos"].lower()
    assert set(schema["parameters"]["properties"]) == {
        "action",
        "list_ref",
        "item_ref",
        "text",
        "idempotency_key",
        "confirmed",
    }
    assert schema["parameters"]["required"] == ["action", "list_ref"]
    assert schema["parameters"]["properties"]["action"]["enum"] == [
        "list",
        "add",
        "complete",
        "reopen",
        "remove",
    ]
    assert descriptor.native_schema
    assert descriptor.schema_ref == "function:manage_todos"
    assert descriptor.handler_ref == "dispatcher:manage_todos"
    assert descriptor.prompt_ref == "prompt:manage_todos"


def test_todo_parser_aliases_and_execution_delegate_are_wired():
    assert _TOOL_NAME_MAP["manage_todos"] == "manage_todos"
    assert _TOOL_NAME_MAP["todo"] == "manage_todos"
    assert _TOOL_NAME_MAP["todos"] == "manage_todos"
    assert _TOOL_NAME_MAP["checklist"] == "manage_todos"

    implementation_source = inspect.getsource(_execute_tool_block_impl)
    assert do_manage_todos.__module__ == "src.tool_domains.todos"
    assert 'elif tool == "manage_todos"' in implementation_source
    assert "await do_manage_todos(content, owner=owner)" in implementation_source


def test_todo_intent_removes_memory_but_leaves_other_domains_untouched():
    assert remove_memory_for_todo_domain(
        {"manage_memory", "manage_notes", "manage_todos"}, {"todos"}
    ) == {"manage_notes", "manage_todos"}
    assert remove_memory_for_todo_domain({"manage_memory"}, {"notes"}) == {
        "manage_memory"
    }
    assert remove_memory_for_todo_domain({"manage_memory"}, set()) == {
        "manage_memory"
    }

    assert "todos" in _classify_agent_request([], "complete this todo item")["domains"]
    assert "todos" in _classify_agent_request([], "erledige diese Aufgabe")["domains"]
    assert "todos" not in _classify_agent_request(
        [], "create a todo digest every morning"
    )["domains"]


def test_tool_index_direct_gate_routes_broad_todo_terms_without_memory():
    index = _index_with_empty_retrieval()

    for query in (
        "complete my todo",
        "mark todos done",
        "reopen the checklist item",
        "lösche die Aufgaben",
        "checkliste aktualisieren",
        "fix this cheklist",
        "add toodo item",
    ):
        selected = index.get_tools_for_query(query)
        assert "manage_todos" in selected, query
        assert "manage_memory" not in selected, query


def test_notes_description_does_not_claim_todo_mutations():
    assert "todo" not in BUILTIN_TOOL_DESCRIPTIONS["manage_notes"].lower()
    assert "todo mutations" in BUILTIN_TOOL_DESCRIPTIONS["manage_todos"].lower()


def test_prompt_and_final_selection_boundaries_reserve_item_mutations_for_todos():
    notes_prompt = TOOL_SECTIONS["manage_notes"]
    todo_prompt = TOOL_SECTIONS["manage_todos"]
    agent_loop_source = inspect.getsource(stream_agent_loop)

    assert "manage_todos" in _DOMAIN_TOOL_MAP["todos"]
    assert "manage_memory" not in _DOMAIN_TOOL_MAP["todos"]
    assert "manage_todos" in _DOMAIN_RULES["todos"]
    assert "never `manage_memory` or `manage_notes`" in _DOMAIN_RULES["todos"]
    assert "checklist-item mutations" in notes_prompt
    assert "toggle_item" not in notes_prompt
    assert "items:[{text,done}]" not in notes_prompt
    assert "remove requires confirmed=true" in todo_prompt
    assert "remove_memory_for_todo_domain" in agent_loop_source
