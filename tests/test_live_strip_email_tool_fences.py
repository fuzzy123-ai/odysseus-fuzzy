import re
from pathlib import Path


def test_chat_renderer_fetches_tool_tags_for_exec_fence_regex():
    source = Path("static/js/chatRenderer.js").read_text(encoding="utf-8")
    assert "/api/tools" in source
    assert "EXEC_FENCE_NON_TOOL" in source
    assert "EXEC_TOOL_TAGS" not in source


def test_api_tools_endpoint_uses_backend_tool_tags():
    source = Path("routes/model_routes.py").read_text(encoding="utf-8")
    assert re.search(r"for\s+tag\s+in\s+sorted\(\s*TOOL_TAGS\s*\)", source)


def test_python_equivalent_strips_email_tool_fence():
    from src.agent_tools import TOOL_TAGS

    tags = sorted(t for t in TOOL_TAGS if t not in {"bash", "python"})
    rx = re.compile(r"```(?:" + "|".join(tags) + r")\s*\n[\s\S]*?```", re.IGNORECASE)
    text = 'Here\n```list_emails\n{"max_results":10}\n```'
    assert rx.sub("", text).strip() == "Here"
