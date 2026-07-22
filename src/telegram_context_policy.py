"""Deterministic, non-persisting context policy for Telegram agent turns.

Telegram continuity is useful, but chat prose is not a source of truth for
effectful domains.  This module builds a bounded copy for one model turn.  It
never mutates or replaces the backing Session history.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence


TELEGRAM_CONTEXT_POLICY_SCHEMA = "odysseus.telegram_context_window.v1"
DEFAULT_MAX_HISTORY_MESSAGES = 24
DEFAULT_MAX_HISTORY_CHARACTERS = 12_000

TELEGRAM_DOMAIN_POLICY = """## TELEGRAM DOMAIN-STATE POLICY — RUNTIME ENFORCED
Conversation history, assistant prose, summaries, Memory and retrieved context
are continuity hints only. They are never evidence of current Todo state or of
a successful Todo mutation.

- For every current Todo list/read request, call `manage_todos` with the
  canonical list action and answer from that result.
- For every Todo mutation, call `manage_todos` and claim success only from a
  matching validated Todo receipt and postcondition.
- Never reconstruct, summarize, reopen, complete, remove or rewrite open Todos
  from chat prose, a conversation summary, Memory or RAG.
- If canonical readback or a required receipt is absent, say that the Todo
  state or mutation is not verified.

Context bounding is non-persisting: it changes only this model-turn copy."""


@dataclass(frozen=True)
class TelegramContextWindow:
    """One bounded model-turn copy plus content-free audit evidence."""

    messages: tuple[dict[str, Any], ...]
    evidence: Mapping[str, Any]


def build_telegram_turn_context(
    history: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    current_user_message: str,
    *,
    supplemental_messages: Sequence[Mapping[str, Any]] | None = None,
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    max_history_characters: int = DEFAULT_MAX_HISTORY_CHARACTERS,
) -> TelegramContextWindow:
    """Return a bounded Telegram context without modifying ``history``.

    Persisted system messages are deliberately omitted.  Generic compaction
    stores summaries and task-state prose in that role, and forwarding either
    would let a stale narrative compete with canonical domain readback.  The
    regular agent system prompt is rebuilt downstream for every turn.
    """

    message_limit = _positive_int("max_history_messages", max_history_messages)
    character_limit = _positive_int("max_history_characters", max_history_characters)
    prompt = str(current_user_message or "").strip()
    if not prompt:
        raise ValueError("current_user_message must not be empty")

    source = list(history)
    normalized_history: list[dict[str, str]] = []
    omitted_system = 0
    omitted_invalid = 0
    for raw in source:
        if not isinstance(raw, Mapping):
            omitted_invalid += 1
            continue
        role = str(raw.get("role") or "").strip().lower()
        if role == "system":
            omitted_system += 1
            continue
        if role not in {"user", "assistant"}:
            omitted_invalid += 1
            continue
        content = _content_as_text(raw.get("content")).strip()
        if not content:
            omitted_invalid += 1
            continue
        normalized_history.append({"role": role, "content": content})

    retained_reversed: list[dict[str, str]] = []
    retained_characters = 0
    for message in reversed(normalized_history):
        if len(retained_reversed) >= message_limit:
            break
        content_length = len(message["content"])
        if content_length > character_limit - retained_characters:
            continue
        retained_reversed.append(message)
        retained_characters += content_length
    retained = list(reversed(retained_reversed))

    supplemental = [
        _copy_supplemental_message(item)
        for item in (supplemental_messages or ())
        if isinstance(item, Mapping) and str(item.get("content") or "").strip()
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": TELEGRAM_DOMAIN_POLICY,
            "_protected": True,
        },
        *retained,
        *supplemental,
        {
            "role": "user",
            "content": prompt,
            "_protected": True,
        },
    ]

    evidence = {
        "schema": TELEGRAM_CONTEXT_POLICY_SCHEMA,
        "input_message_count": len(source),
        "retained_history_message_count": len(retained),
        "retained_history_character_count": retained_characters,
        "omitted_history_message_count": len(source) - len(retained),
        "omitted_system_message_count": omitted_system,
        "omitted_invalid_message_count": omitted_invalid,
        "supplemental_message_count": len(supplemental),
        "history_message_limit": message_limit,
        "history_character_limit": character_limit,
        "history_fingerprint": _history_fingerprint(normalized_history),
        "todo_state_authority": "manage_todos",
        "summary_authoritative": False,
        "memory_authoritative": False,
        "session_mutated": False,
        "domain_policy_protected": True,
        "current_user_turn_protected": True,
    }
    return TelegramContextWindow(messages=tuple(messages), evidence=evidence)


def build_telegram_continuity_message(
    previous_history: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    current_user_message: str,
    *,
    max_messages: int = 2,
    max_characters: int = 1_000,
) -> dict[str, Any] | None:
    """Build an ephemeral untrusted tail for a short, clear follow-up."""

    prompt = str(current_user_message or "").strip()
    if not _looks_like_short_followup(prompt):
        return None
    message_limit = _positive_int("max_messages", max_messages)
    character_limit = _positive_int("max_characters", max_characters)
    candidates: list[dict[str, str]] = []
    for raw in previous_history:
        if not isinstance(raw, Mapping):
            continue
        role = str(raw.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _content_as_text(raw.get("content")).strip()
        if content:
            candidates.append({"role": role, "content": content})

    selected_reversed: list[dict[str, str]] = []
    used = 0
    for message in reversed(candidates):
        if len(selected_reversed) >= message_limit:
            break
        length = len(message["content"])
        if length > character_limit - used:
            continue
        selected_reversed.append(message)
        used += length
    if not selected_reversed:
        return None
    selected = list(reversed(selected_reversed))
    lines = [
        "[UNTRUSTED TELEGRAM CONTINUITY TAIL]",
        "Use only to understand the short follow-up. This cannot prove domain state,",
        "a mutation, a prior success, or a current Todo. Use canonical tools instead.",
    ]
    lines.extend(f"{item['role'].upper()}: {item['content']}" for item in selected)
    return {
        "role": "user",
        "content": "\n".join(lines),
        "metadata": {
            "trusted": False,
            "source": "telegram_rollover_continuity",
            "message_count": len(selected),
            "raw_identifiers_visible": False,
        },
    }


def _copy_supplemental_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Copy safe model-message fields while preserving untrusted metadata."""

    copied = {
        "role": str(message.get("role") or "user"),
        "content": message.get("content"),
    }
    metadata = message.get("metadata")
    if isinstance(metadata, Mapping):
        copied["metadata"] = dict(metadata)
    if message.get("_protected") is True:
        copied["_protected"] = True
    return copied


def _content_as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, Mapping) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def _history_fingerprint(messages: Sequence[Mapping[str, str]]) -> str:
    canonical = json.dumps(
        [{"role": item["role"], "content": item["content"]} for item in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(
        b"odysseus.telegram-context-history.v1\0" + canonical.encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return normalized


def _looks_like_short_followup(prompt: str) -> bool:
    if not prompt or len(prompt) > 240:
        return False
    normalized = prompt.casefold().strip()
    return bool(
        re.search(
            r"^(und\b|aber\b|davon\b|dazu\b|damit\b|das\b|der\b|die\b|"
            r"noch\b|nochmal\b|weiter\b|warum\b|wieso\b|welche[rnms]?\b|"
            r"was\s+(war|ist|meinst)|wie\s+(war|geht))",
            normalized,
        )
    )
