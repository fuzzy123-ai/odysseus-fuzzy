"""Shared knowledge-status normalization for derived Obsidian layers."""

from __future__ import annotations

STATUS_ALIASES = {
    "unresolved conflict": "conflict",
    "unresolved_conflict": "conflict",
    "unresolved-conflict": "conflict",
    "conflicted": "conflict",
}


def normalize_status(value: object, *, default: str = "active") -> str:
    raw = str(value or default).strip().lower()
    return STATUS_ALIASES.get(raw, raw)
