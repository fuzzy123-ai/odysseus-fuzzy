"""Regression for issue #1707 — the agent tool-RAG force-included the entire
email toolset on any "tell me ..." query, crowding out the relevant tools so the
model believed it only had email tools and refused web/other tasks.

Root cause: `_KEYWORD_HINTS` in src/tool_index.py listed "tell" under the email
intent, and `get_tools_for_query` force-includes a hint's tools whenever any of
its keywords appears (word-boundary match). "tell" appears in a huge fraction of
requests (the reporter's was "visit <url> and tell me the title"), so email tools
were force-included for non-email queries.

These hints are deterministic string matching — no embeddings — so we can test
`get_tools_for_query` directly with retrieval stubbed out (no ChromaDB needed).
"""

from src.tool_index import ToolIndex, ALWAYS_AVAILABLE
from src.chat_agent_tool_discovery_map import MULTI_AGENT_TOOLSET, SESSION_TOOLSET

_EMAIL_TOOLS = {
    "list_emails", "read_email", "send_email", "reply_to_email",
    "bulk_email", "delete_email", "archive_email", "mark_email_read",
}


def _index_without_embeddings():
    """A ToolIndex whose retrieval returns nothing, so get_tools_for_query
    exercises only the deterministic base + keyword-hint logic."""
    ti = ToolIndex.__new__(ToolIndex)        # skip __init__ (no ChromaDB/fastembed)
    ti.retrieve = lambda query, k=8: []
    return ti


def test_tell_in_web_query_does_not_force_email_tools():
    """The #1707 repro: a web request that merely contains the word 'tell' must
    NOT drag in the email toolset."""
    ti = _index_without_embeddings()
    q = "visit https://www.youtube.com/user/PewDiePie and tell me the title of his latest video"
    tools = ti.get_tools_for_query(q)
    leaked = _EMAIL_TOOLS & tools
    assert not leaked, f"'tell me' must not force-include email tools, got {sorted(leaked)}"
    # web_search / web_fetch are always-available and must remain present.
    assert "web_search" in tools and "web_fetch" in tools


def test_explicit_web_search_query_gets_web_tools_without_retrieval():
    """Explicit web-search phrasing must surface web tools even if embeddings
    return nothing."""
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query("use web search and find a recipe for chocolate chip cookies")
    assert "web_search" in tools and "web_fetch" in tools


def test_genuine_email_query_still_gets_email_tools():
    """Removing 'tell' must not break real email intent — the actual email
    keywords still force-include the toolset."""
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query("reply to the unread email in my inbox")
    assert {"reply_to_email", "send_email", "read_email"} <= tools


def test_plain_tell_request_stays_minimal():
    """A bare 'tell me a joke' must not pull in email tools either."""
    ti = _index_without_embeddings()
    tools = ti.get_tools_for_query("tell me a joke")
    assert not (_EMAIL_TOOLS & tools)
    # Always-available baseline is still there.
    assert set(ALWAYS_AVAILABLE) <= tools


def test_german_chat_creation_queries_get_session_tools_without_retrieval():
    """German chat/session wording must surface the existing session tools."""
    ti = _index_without_embeddings()

    examples = [
        "Lege einen neuen Chat fuer Bob an",
        "Bitte eine neue Unterhaltung erstellen",
        "Thread starten fuer Alice",
        "Chat für Bob erstellen",
    ]

    for query in examples:
        tools = ti.get_tools_for_query(query)
        assert SESSION_TOOLSET <= tools, query


def test_english_chat_creation_queries_get_session_tools_without_retrieval():
    """English 'new chat' wording should not depend on embedding recall."""
    ti = _index_without_embeddings()

    tools = ti.get_tools_for_query("Create a new chat for Bob and send him the context")

    assert SESSION_TOOLSET <= tools


def test_multi_agent_queries_get_delegation_tools_without_retrieval():
    """Multi-agent/worker wording must surface orchestration-adjacent tools."""
    ti = _index_without_embeddings()

    examples = [
        "Verteile diese Aufgabe an Alice und Bob",
        "Starte einen Worker fuer die Analyse",
        "Nutze Multi-Agent-Support",
        "Charlie koordiniert, Alice und Bob arbeiten parallel",
    ]

    for query in examples:
        tools = ti.get_tools_for_query(query)
        assert MULTI_AGENT_TOOLSET <= tools, query
