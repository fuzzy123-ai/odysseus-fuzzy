"""`ask_user` — the agent poses a multiple-choice question to the user.

The tool is a pure UI-control marker: it does no I/O. `execute_tool_block`
returns an `ask_user` payload that the agent loop turns into an `ask_user` SSE
event and then ends the turn so the chat waits for the user's selection.
"""
import asyncio
import json

from src.agent_tools import ToolBlock, TOOL_TAGS  # noqa: E402  (import first to avoid circular)
from src.tool_execution import execute_tool_block
from src.tool_index import ALWAYS_AVAILABLE, BUILTIN_TOOL_DESCRIPTIONS
from src.tool_security import is_public_blocked_tool


def _run(content):
    return asyncio.run(execute_tool_block(ToolBlock("ask_user", content)))


def test_valid_question_returns_ask_user_payload():
    content = json.dumps({
        "question": "Which database should I use?",
        "options": [
            {"label": "PostgreSQL", "description": "Relational, ACID"},
            {"label": "SQLite", "description": "Zero-config, file-based"},
        ],
    })
    desc, result = _run(content)
    assert result.get("exit_code") == 0
    assert "error" not in result
    payload = result["ask_user"]
    assert payload["question"] == "Which database should I use?"
    assert [o["label"] for o in payload["options"]] == ["PostgreSQL", "SQLite"]
    assert payload["options"][0]["description"] == "Relational, ACID"
    assert payload["multi"] is False
    assert result["clarification_request"]["schema"] == "odysseus.clarification_request.v2"
    assert result["clarification_request"]["questions"][0]["type"] == "single_select"
    assert "PostgreSQL" in result["output"]


def test_multi_flag_is_carried():
    content = json.dumps({
        "question": "Which features?",
        "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
        "multi": True,
    })
    _, result = _run(content)
    assert result["ask_user"]["multi"] is True
    assert len(result["ask_user"]["options"]) == 3


def test_string_options_are_accepted():
    content = json.dumps({"question": "Pick one", "options": ["Yes", "No"]})
    _, result = _run(content)
    labels = [o["label"] for o in result["ask_user"]["options"]]
    assert labels == ["Yes", "No"]


def test_options_are_capped_at_six():
    content = json.dumps({
        "question": "Pick",
        "options": [{"label": f"opt{i}"} for i in range(10)],
    })
    _, result = _run(content)
    assert len(result["ask_user"]["options"]) == 6
    assert len(result["clarification_request"]["questions"][0]["options"]) == 6


def test_fewer_than_two_options_is_rejected():
    content = json.dumps({"question": "Only one?", "options": [{"label": "A"}]})
    _, result = _run(content)
    assert "error" in result
    assert result.get("exit_code") == 1


def test_missing_question_is_rejected():
    content = json.dumps({"options": [{"label": "A"}, {"label": "B"}]})
    _, result = _run(content)
    assert "error" in result


def test_legacy_ask_user_rejects_secret_or_private_path():
    content = json.dumps({"question": "Use token=SECRET?", "options": ["Yes", "No"]})
    _, result = _run(content)
    assert result.get("exit_code") == 1
    assert "error" in result


def test_v2_clarification_request_is_accepted_and_carried():
    content = json.dumps({
        "schema": "odysseus.clarification_request.v2",
        "scope": "project",
        "intent_summary": "Build a document review workflow.",
        "questions": [
            {
                "key": "target_documents",
                "type": "short_text",
                "prompt": "Which document set should be reviewed first?",
                "required": True,
                "reason": "The source changes scope and privacy boundaries.",
                "category": "scope",
            },
            {
                "key": "review_tone",
                "type": "single_select",
                "prompt": "Which tone should the review use?",
                "required": False,
                "reason": "Tone changes the output style.",
                "options": [{"label": "Concise"}, {"label": "Detailed", "recommended": True}],
            },
        ],
        "batch": {"label": "Scope", "index": 1, "total": 1, "max_visible_questions": 5},
        "defaults_visible": False,
    })
    _, result = _run(content)

    assert result["exit_code"] == 0
    assert result["clarification_request"]["scope"] == "project"
    assert len(result["clarification_request"]["questions"]) == 2
    assert result["ask_user"]["question"] == "Which document set should be reviewed first?"
    assert result["ask_user"]["clarification_schema"] == "odysseus.clarification_request.v2"


def test_v2_clarification_request_rejects_bad_dependencies_and_budgets():
    bad_dependency = {
        "schema": "odysseus.clarification_request.v2",
        "scope": "project",
        "intent_summary": "Build a workflow.",
        "questions": [
            {
                "key": "review_tone",
                "type": "single_select",
                "prompt": "Which tone?",
                "required": True,
                "reason": "Tone changes output.",
                "depends_on": "missing_question",
                "options": [{"label": "Concise"}, {"label": "Detailed"}],
            }
        ],
        "batch": {"label": "Scope", "index": 1, "total": 1, "max_visible_questions": 5},
        "defaults_visible": False,
    }
    _, dependency_result = _run(json.dumps(bad_dependency))
    assert dependency_result.get("exit_code") == 1

    bad_dependency["questions"][0].pop("depends_on")
    bad_dependency["batch"]["max_visible_questions"] = 99
    _, budget_result = _run(json.dumps(bad_dependency))
    assert budget_result.get("exit_code") == 1


def test_serializer_round_trips_structured_args():
    from src.tool_schemas import function_call_to_tool_block
    args = {"question": "Q?", "options": [{"label": "A"}, {"label": "B"}], "multi": True}
    block = function_call_to_tool_block("ask_user", json.dumps(args))
    assert block is not None
    assert block.tool_type == "ask_user"
    assert json.loads(block.content) == args


def test_serializer_keeps_unicode_labels_readable():
    from src.tool_schemas import function_call_to_tool_block
    args = {"question": "Wählen?", "options": [{"label": "Mañana"}, {"label": "Übermorgen"}]}
    block = function_call_to_tool_block("ask_user", json.dumps(args))
    assert block is not None
    assert "\\u00" not in block.content
    assert json.loads(block.content) == args


def test_registered_everywhere():
    # TOOL_TAGS gate (serializer rejects unknown tools)
    assert "ask_user" in TOOL_TAGS
    # Always reachable + has a retrieval description
    assert "ask_user" in ALWAYS_AVAILABLE
    assert "ask_user" in BUILTIN_TOOL_DESCRIPTIONS
    # Function schema present
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    names = {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}
    assert "ask_user" in names
    # Not admin/public-gated — any user can be asked
    assert is_public_blocked_tool("ask_user") is False
