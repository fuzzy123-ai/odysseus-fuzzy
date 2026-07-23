"""Authoritative, side-effect-free Memory category policy."""
from __future__ import annotations

ALLOWED_MEMORY_CATEGORIES = (
    "fact",
    "event",
    "contact",
    "preference",
    "identity",
    "project",
    "goal",
)
TODO_ALIASES = frozenset(
    {
        "task",
        "tasks",
        "todo",
        "todos",
        "to-do",
        "to_do",
        "to do",
        "checklist",
        "checklists",
        "aufgabe",
        "aufgaben",
        "todo-list",
        "to-do-list",
        "todo_list",
        "todo list",
    }
)


class MemoryCategoryPolicyError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_memory_category(category: object) -> str:
    if category is None:
        raise MemoryCategoryPolicyError("memory_category_invalid")
    if not isinstance(category, str):
        raise MemoryCategoryPolicyError("memory_category_invalid")
    normalized = category.strip().lower()
    if normalized in TODO_ALIASES:
        raise MemoryCategoryPolicyError("todo_storage_forbidden")
    if normalized not in ALLOWED_MEMORY_CATEGORIES:
        raise MemoryCategoryPolicyError("memory_category_invalid")
    return normalized
