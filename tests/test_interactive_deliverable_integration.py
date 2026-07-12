from pathlib import Path

import src.agent_tools  # initialize the facade before importing its schema module
from src.tool_schemas import function_call_to_tool_block, get_function_tool_schemas


_ROOT = Path(__file__).resolve().parents[1]


def test_new_artifact_tools_are_in_native_schema_and_convert_to_json_blocks():
    schemas = get_function_tool_schemas()
    names = {item["function"]["name"] for item in schemas}

    assert {"verify_pygame_headless", "publish_artifact"} <= names
    verify = function_call_to_tool_block(
        "verify_pygame_headless",
        '{"path":"game.py","max_frames":10}',
    )
    publish = function_call_to_tool_block(
        "publish_artifact",
        '{"path":"game.py","inspect_image":false}',
    )
    assert verify.tool_type == "verify_pygame_headless"
    assert '"path": "game.py"' in verify.content
    assert publish.tool_type == "publish_artifact"
    assert '"inspect_image": false' in publish.content


def test_agent_loop_injects_deliverable_policy_and_required_tools():
    loop = (_ROOT / "src/agent_loop.py").read_text(encoding="utf-8")
    prompts = (_ROOT / "src/agent_loop_prompts.py").read_text(encoding="utf-8")

    assert "decide_interactive_deliverable" in loop
    assert '"verify_pygame_headless"' in loop
    assert '"publish_artifact"' in loop
    assert "INTERACTIVE DELIVERABLE POLICY — RUNTIME ENFORCED" in loop
    assert '"verify_pygame_headless":' in prompts
    assert '"publish_artifact":' in prompts
