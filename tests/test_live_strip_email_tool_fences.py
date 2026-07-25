import re
from pathlib import Path


def test_chat_renderer_fetches_tool_tags_for_exec_fence_regex():
    source = Path("static/js/chatRenderer.js").read_text(encoding="utf-8")
    assert "/api/tools" in source
    assert "EXEC_FENCE_NON_TOOL" in source
    assert "EXEC_TOOL_TAGS" not in source


def test_api_tools_legacy_projection_supplies_backend_tool_tags():
    from src.agent_tools import TOOL_TAGS
    from src.runtime_tool_status import (
        build_legacy_tool_catalog_projection,
        build_tool_catalog_projection,
    )

    legacy_ids = {row["id"] for row in build_legacy_tool_catalog_projection()["tools"]}
    v2_ids = {row["id"] for row in build_tool_catalog_projection()["tools"]}

    assert set(TOOL_TAGS) <= legacy_ids
    assert legacy_ids <= v2_ids


def test_python_equivalent_strips_email_tool_fence():
    from src.agent_tools import TOOL_TAGS

    tags = sorted(t for t in TOOL_TAGS if t not in {"bash", "python"})
    rx = re.compile(r"```(?:" + "|".join(tags) + r")\s*\n[\s\S]*?```", re.IGNORECASE)
    text = 'Here\n```list_emails\n{"max_results":10}\n```'
    assert rx.sub("", text).strip() == "Here"
