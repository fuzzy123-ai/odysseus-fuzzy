"""Intent-to-tool maps for chat/session and multi-agent discovery.

This module keeps multilingual trigger phrases out of the retrieval plumbing so
tests and operator docs can refer to the same canonical tool groups.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable


@dataclass(frozen=True)
class ToolDiscoveryIntent:
    """A deterministic tool-discovery intent used as a retrieval backstop."""

    name: str
    keywords: FrozenSet[str]
    tools: FrozenSet[str]


SESSION_TOOLSET = frozenset({
    "create_session",
    "list_sessions",
    "manage_session",
    "send_to_session",
    "search_chats",
})

MULTI_AGENT_TOOLSET = frozenset({
    "delegate",
    "create_session",
    "send_to_session",
    "list_sessions",
    "pipeline",
    "chat_with_model",
    "ask_teacher",
})

SESSION_INTENT_KEYWORDS = frozenset({
    "new chat",
    "create chat",
    "create a chat",
    "start a chat",
    "new conversation",
    "new session",
    "create session",
    "start thread",
    "chat anlegen",
    "chat erstellen",
    "neuer chat",
    "neuen chat",
    "neue konversation",
    "neue unterhaltung",
    "unterhaltung erstellen",
    "session erstellen",
    "thread starten",
    "chat fuer",
    "chat für",
    "chat fuer alice",
    "chat für alice",
    "chat fuer bob",
    "chat für bob",
})

MULTI_AGENT_INTENT_KEYWORDS = frozenset({
    "multi agent",
    "multi-agent",
    "multiagent",
    "multiple agents",
    "subagent",
    "sub-agent",
    "worker",
    "delegate",
    "delegate to",
    "parallel agents",
    "parallel work",
    "alice and bob",
    "multi agent support",
    "mehragenten",
    "mehr-agenten",
    "agenten",
    "unteragent",
    "unter-agent",
    "delegieren",
    "aufgaben verteilen",
    "aufgabe verteilen",
    "parallel arbeiten",
    "starte einen worker",
    "worker starten",
    "alice und bob",
    "charlie koordiniert",
})

DISCOVERY_INTENTS = (
    ToolDiscoveryIntent(
        name="chat_session",
        keywords=SESSION_INTENT_KEYWORDS,
        tools=SESSION_TOOLSET,
    ),
    ToolDiscoveryIntent(
        name="multi_agent",
        keywords=MULTI_AGENT_INTENT_KEYWORDS,
        tools=MULTI_AGENT_TOOLSET,
    ),
)


def keyword_hint_pairs() -> Iterable[tuple[FrozenSet[str], FrozenSet[str]]]:
    """Return keyword/tool pairs in the format expected by ToolIndex."""

    for intent in DISCOVERY_INTENTS:
        yield intent.keywords, intent.tools
