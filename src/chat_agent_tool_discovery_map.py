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

DELEGATE_TOOLSET = frozenset({
    "delegate",
})

DURABLE_SUBAGENT_TOOLSET = frozenset({
    "spawn_subagent",
    "manage_subagents",
    "create_session",
    "send_to_session",
    "list_sessions",
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
    "parallel agents",
    "parallel work",
    "alice and bob",
    "multi agent support",
    "mehragenten",
    "mehr-agenten",
    "agenten",
    "unteragent",
    "unter-agent",
    "aufgaben verteilen",
    "aufgabe verteilen",
    "parallel arbeiten",
    "starte einen worker",
    "worker starten",
    "alice und bob",
    "charlie koordiniert",
})

DELEGATE_INTENT_KEYWORDS = frozenset({
    "delegate",
    "delegate to",
    "delegieren",
    "focused worker",
    "focused subtask",
    "lightweight analysis",
    "read-only investigation",
    "bounded investigation",
    "kurze analyse",
    "fokussierte analyse",
})

MULTI_AGENT_TOOLSET = DURABLE_SUBAGENT_TOOLSET

DISCOVERY_INTENTS = (
    ToolDiscoveryIntent(
        name="chat_session",
        keywords=SESSION_INTENT_KEYWORDS,
        tools=SESSION_TOOLSET,
    ),
    ToolDiscoveryIntent(
        name="multi_agent",
        keywords=MULTI_AGENT_INTENT_KEYWORDS,
        tools=DURABLE_SUBAGENT_TOOLSET,
    ),
    ToolDiscoveryIntent(
        name="delegate_lightweight",
        keywords=DELEGATE_INTENT_KEYWORDS,
        tools=DELEGATE_TOOLSET,
    ),
)


def keyword_hint_pairs() -> Iterable[tuple[FrozenSet[str], FrozenSet[str]]]:
    """Return keyword/tool pairs in the format expected by ToolIndex."""

    for intent in DISCOVERY_INTENTS:
        yield intent.keywords, intent.tools
