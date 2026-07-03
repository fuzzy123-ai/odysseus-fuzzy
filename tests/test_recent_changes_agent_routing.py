"""Regression tests for Recent Changes / Patch Notes agent routing.

The recent_changes tool is the source of truth for local Odysseus
"what changed?" questions. These prompts must not be treated as low-signal or
fall back to web search, memory, release versions, or raw git commits alone.
"""

import pytest

agent_loop = pytest.importorskip("src.agent_loop")


def _classify(text: str):
    return agent_loop._classify_agent_request([{"role": "user", "content": text}], text)


def _selected_tools(domains):
    tools = set()
    for domain in domains:
        tools |= agent_loop._DOMAIN_TOOL_MAP.get(domain, set())
    return tools


@pytest.mark.parametrize(
    "prompt",
    [
        "Was gab es in den letzten 12h Neues?",
        "Zeig mir die Patch Notes von heute",
        "Was hat sich gestern geaendert?",
        "What changed in the last 12 hours?",
        "Welche Odysseus Updates gab es heute?",
    ],
)
def test_recent_change_questions_route_to_changes_domain(prompt):
    intent = _classify(prompt)

    assert intent["low_signal"] is False, intent
    assert "changes" in intent["domains"], intent


def test_changes_domain_seeds_recent_changes_tool_only():
    selected = _selected_tools({"changes"})

    assert selected == {"recent_changes"}


def test_recent_changes_schema_reaches_function_tool_filter():
    intent = _classify("Welche Odysseus Updates gab es heute?")
    selected = _selected_tools(intent["domains"])
    schema_names = {
        schema.get("function", {}).get("name")
        for schema in agent_loop.FUNCTION_TOOL_SCHEMAS
        if schema.get("function", {}).get("name") in selected
    }

    assert "recent_changes" in selected
    assert "recent_changes" in schema_names


def test_recent_changes_prompt_rules_block_web_or_commit_only_answers():
    rules = agent_loop._domain_rules_for_tools({"recent_changes"})
    combined = "\n".join(rules).lower()

    assert "recent_changes" in combined
    assert "do not use web search" in combined
    assert "git commits alone are insufficient" in combined


def test_generic_news_or_web_updates_do_not_use_local_changes_domain():
    for prompt in (
        "Search the web for current Python updates",
        "What are the latest weather updates?",
        "Look up Apple news updates",
    ):
        intent = _classify(prompt)
        assert "changes" not in intent["domains"], intent
