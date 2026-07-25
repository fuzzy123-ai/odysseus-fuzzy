from contextlib import contextmanager
import importlib
import sys
from unittest.mock import MagicMock

import pytest

from tests.helpers.import_state import (
    clear_module,
    preserve_import_state,
    preserve_module_tree,
)

# This module needs the real agent-tool stack; importing it pulls in heavy
# DB/auth deps, so we stub those just long enough to import, then restore them.
# We deliberately do NOT pop src.tool_execution: popping and re-importing it
# rebinds the `src` package's `tool_execution` attribute, so a later
# `import src.tool_execution as te` resolves to a different module object than
# the one its functions live in - which silently breaks tests that monkeypatch
# it (e.g. test_edit_file's admin gate).
_AGENT_MODULES = ["src.tool_parsing", "src.tool_schemas"]
_STUBBED = [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext", "sqlalchemy.ext.declarative",
    "sqlalchemy.ext.hybrid", "sqlalchemy.sql", "sqlalchemy.sql.expression",
    "src.database", "core.models", "core.database", "core.auth",
]

@contextmanager
def _isolated_agent_stack():
    with preserve_module_tree("src.agent_tools"), preserve_import_state(
        *_AGENT_MODULES, *_STUBBED
    ):
        clear_module("src.agent_tools")
        for _mod in _AGENT_MODULES:
            clear_module(_mod)
        for _mod in _STUBBED:
            if _mod not in sys.modules:
                sys.modules[_mod] = MagicMock()
        importlib.import_module("src.agent_tools")
        yield


def parse_tool_blocks(*args, **kwargs):
    with _isolated_agent_stack():
        parser = importlib.import_module("src.tool_parsing")
        return parser.parse_tool_blocks(*args, **kwargs)


def function_call_to_tool_block(*args, **kwargs):
    with _isolated_agent_stack():
        schemas = importlib.import_module("src.tool_schemas")
        return schemas.function_call_to_tool_block(*args, **kwargs)


def test_parse_xml_unknown_tool_returns_none():
    """XML-style <invoke> tags with truly unknown tools should be filtered out (return None)."""
    text = '<invoke name="super_secret_tool"><parameter name="arg1">value1</parameter></invoke>'
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 0


def test_parse_tool_call_unknown_tool_returns_none():
    """[TOOL_CALL] blocks with truly unknown tools should be filtered out (return None)."""
    text = '[TOOL_CALL] {tool => "mega_blast", command => "run energy"} [/TOOL_CALL]'
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 0


def test_function_call_to_tool_block_unknown_tool_returns_none():
    """Native function calls of truly unknown tools should return None."""
    block = function_call_to_tool_block("ultra_zap", '{"power": 9000}')
    assert block is None


def test_legacy_obsidian_mcp_function_call_returns_feedback_block():
    block = function_call_to_tool_block(
        "obsidian_mcp__obsidian_file_create",
        '{"path": "Test MCP Notiz.md", "content": "# Hi"}',
    )
    assert block is not None
    assert block.tool_type == "invalid_tool_call"
    assert "mcp__vault__obsidian_write_note" in block.content


def test_direct_legacy_obsidian_mcp_xml_returns_feedback_block():
    text = (
        "<obsidian_mcp__obsidian_file_create>"
        "<path>Test MCP Notiz.md</path>"
        "<content># Test MCP Notiz</content>"
        "</obsidian_mcp__obsidian_file_create>"
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "invalid_tool_call"
    assert "mcp__vault__obsidian_write_note" in blocks[0].content


def test_function_call_to_tool_block_invalid_json_returns_none():
    """Unparseable JSON arguments should result in returning None."""
    block = function_call_to_tool_block("web_search", '{"query": "valid json')  # invalid JSON
    assert block is None


def test_google_search_mapping():
    """google_search should map to web_search and extract the first query from queries list or string."""
    # List of queries case
    block = function_call_to_tool_block("google_search", '{"queries": ["testing google search"]}')
    assert block is not None
    assert block.tool_type == "web_search"
    assert block.content == "testing google search"

    # Single string query case
    block = function_call_to_tool_block("google_search_retrieval", '{"queries": "testing google search string"}')
    assert block is not None
    assert block.tool_type == "web_search"
    assert block.content == "testing google search string"
