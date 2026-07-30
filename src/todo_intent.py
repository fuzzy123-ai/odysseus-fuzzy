"""Deterministic Todo-intent routing and Memory domain protection."""

from __future__ import annotations

import re
from typing import Iterable


TODO_DISCOVERY_KEYWORDS = frozenset({
    "todo",
    "aufgabe",
    "aufgaben",
    "zu erledigen",
    "add a todo",
    "add todo",
    "add task",
    "create a todo",
    "create todo",
    "complete todo",
    "complete task",
    "mark todo done",
    "reopen todo",
    "remove todo",
    "todo list",
    "todos",
    "to-dos",
    "checklist item",
    "neue aufgabe",
    "neues todo",
    "neue to-do",
    "todo hinzufuegen",
    "todo hinzufügen",
    "aufgabe hinzufuegen",
    "aufgabe hinzufügen",
    "aufgabe erledigen",
    "todo erledigen",
    "todo wieder oeffnen",
    "todo wieder öffnen",
    "todo entfernen",
    "aufgabe entfernen",
    "todo-liste",
    "aufgabenliste",
})

_TODO_NOUN = re.compile(
    r"\b(?:to[\s-]?dos?|tasks?|checklists?|checklist[\s-]?items?|"
    r"aufgaben?|aufgabenlisten?|todo[\s-]?listen?|zu\s+erledigen)\b",
    re.IGNORECASE,
)
_TODO_VERB = re.compile(
    r"\b(?:add|create|new|save|write|note|complete|finish|mark|reopen|remove|delete|list|show|"
    r"neu(?:e[snr]?)?|erstelle|erstellen|fuege|füge|hinzufuegen|hinzufügen|notiere|"
    r"schreibe|schreiben|setze|setzen|setz|packe|packen|speichere|speichern|"
    r"erledige|erledigen|abhaken|oeffne|öffne|wieder(?:\s+)?oeffnen|"
    r"wieder(?:\s+)?öffnen|loesche|lösche|entferne|entfernen|liste|zeige)\b",
    re.IGNORECASE,
)
_TODO_PREFIX = re.compile(
    r"^\s*(?:to[\s-]?dos?|tasks?|checklist[\s-]?items?|aufgaben?|zu\s+erledigen)\b"
    r"(?:\s+[^:\n]{1,80})?\s*[:#-]",
    re.IGNORECASE,
)

_TODO_NOUN_TOKENS = frozenset({
    "todo", "todos", "task", "tasks", "checklist", "aufgabe", "aufgaben",
})
_TODO_VERB_TOKENS = frozenset({
    "add", "create", "complete", "finish", "mark", "reopen", "remove", "delete",
    "erstelle", "fuege", "füge", "hinzufuegen", "hinzufügen", "erledige",
    "erledigen", "abhaken", "oeffne", "öffne", "loesche", "lösche", "entferne",
})


def normalize_todo_match_text(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _within_one_typo(value: str, expected: str) -> bool:
    """Accept one insertion/deletion/substitution or adjacent transposition."""
    if value == expected:
        return True
    if min(len(value), len(expected)) < 4 or abs(len(value) - len(expected)) > 1:
        return False
    if len(value) == len(expected):
        differences = [index for index, pair in enumerate(zip(value, expected)) if pair[0] != pair[1]]
        if len(differences) == 1:
            return True
        return (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and value[differences[0]] == expected[differences[1]]
            and value[differences[1]] == expected[differences[0]]
        )
    shorter, longer = (value, expected) if len(value) < len(expected) else (expected, value)
    short_index = long_index = edits = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
        else:
            edits += 1
            long_index += 1
            if edits > 1:
                return False
    return True


def _has_fuzzy_token(tokens: set[str], vocabulary: frozenset[str]) -> bool:
    return any(
        _within_one_typo(token, expected)
        for token in tokens
        for expected in vocabulary
    )


def is_clear_todo_intent(value: str) -> bool:
    """Return true only for explicit Todo-domain requests, not mere mentions."""
    text = normalize_todo_match_text(value)
    if not text:
        return False
    if _TODO_PREFIX.search(text):
        return True
    exact_noun = bool(_TODO_NOUN.search(text))
    exact_verb = bool(_TODO_VERB.search(text))
    if exact_noun and exact_verb:
        return True
    tokens = set(re.findall(r"[a-zäöüß]+", text))
    return bool(
        (exact_verb and _has_fuzzy_token(tokens, _TODO_NOUN_TOKENS))
        or (exact_noun and _has_fuzzy_token(tokens, _TODO_VERB_TOKENS))
    )


def is_todo_memory_payload(*, text: str, category: str | None) -> bool:
    """Detect payloads that must never be persisted as Memory."""
    normalized_category = normalize_todo_match_text(category or "")
    if normalized_category in {
        "todo", "todos", "to-do", "to-dos", "task", "tasks", "checklist",
    }:
        return True
    normalized_text = normalize_todo_match_text(text)
    if _TODO_PREFIX.search(normalized_text):
        return True
    # A bare "Task: ..." / "Aufgabe ..." payload has already lost the original
    # user turn by the time it reaches Memory, but remains explicit enough to
    # fail closed and redirect to the canonical Todo facade.
    if re.match(r"^(?:task|aufgabe|to[\s-]?do)\b", normalized_text, re.IGNORECASE):
        return True
    return is_clear_todo_intent(normalized_text)


def route_todo_toolset(tool_names: Iterable[str], user_text: str) -> set[str]:
    """Select manage_todos and remove manage_memory for a clear Todo turn."""
    selected = {str(name) for name in tool_names}
    if is_clear_todo_intent(user_text):
        selected.add("manage_todos")
        selected.discard("manage_memory")
        selected.discard("manage_notes")
    return selected


__all__ = [
    "TODO_DISCOVERY_KEYWORDS",
    "is_clear_todo_intent",
    "is_todo_memory_payload",
    "normalize_todo_match_text",
    "route_todo_toolset",
]
