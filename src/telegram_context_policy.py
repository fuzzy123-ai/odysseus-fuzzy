"""Bounded, non-persisting context assembly for Telegram agent turns."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from src.prompt_security import untrusted_context_message


TELEGRAM_CONTEXT_POLICY_SCHEMA = "odysseus.telegram_context_window.v1"
DEFAULT_MAX_HISTORY_MESSAGES = 24
DEFAULT_MAX_HISTORY_CHARACTERS = 12_000

TELEGRAM_DOMAIN_POLICY = """## TELEGRAM TODO DOMAIN-STATE POLICY — RUNTIME ENFORCED
Chat prose, assistant claims, persisted summaries, task state, Memory, and RAG
are continuity hints only. They never prove current Todo state or a successful
Todo mutation.

- Current Todo reads and listings require `manage_todos` canonical Notes readback.
- Todo mutation success requires a matching validated receipt and postcondition.
- Never infer, reconstruct, or claim Todo state from chat prose, assistant claims,
  Summary, Memory, or RAG. If canonical readback or the required evidence is
  absent, say that the Todo state or mutation is not verified.

This policy applies only to this copied model-turn context; it does not mutate
or replace the persisted session."""


@dataclass(frozen=True)
class TelegramContextWindow:
    """A deterministic context copy and content-free assembly evidence."""

    messages: tuple[dict[str, Any], ...]
    evidence: Mapping[str, Any]


def build_telegram_turn_context(
    history: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    current_user_message: str,
    *,
    trusted_system_messages: Sequence[Mapping[str, Any]] | None = None,
    supplemental_messages: Sequence[Mapping[str, Any]] | None = None,
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    max_history_characters: int = DEFAULT_MAX_HISTORY_CHARACTERS,
) -> TelegramContextWindow:
    """Build one bounded Telegram turn without changing the supplied history.

    Persisted ``system`` messages are omitted because they may contain compacted
    summary or task-state prose. Only the handler's explicit trusted-runtime
    channel may supply copied system messages. Supplemental messages are always
    re-enveloped as untrusted user data, never promoted to the system role.
    """

    message_limit = _positive_int(
        "max_history_messages", max_history_messages, DEFAULT_MAX_HISTORY_MESSAGES
    )
    character_limit = _positive_int(
        "max_history_characters", max_history_characters, DEFAULT_MAX_HISTORY_CHARACTERS
    )
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
    retained_indexes: set[int] = set()
    retained_characters = 0
    for index in range(len(normalized_history) - 1, -1, -1):
        if len(retained_reversed) >= message_limit:
            break
        message = normalized_history[index]
        content_length = len(message["content"])
        if content_length > character_limit - retained_characters:
            # Do not skip a newer over-budget message to admit older history:
            # that would make an older turn appear more recent than it is.
            break
        retained_reversed.append(dict(message))
        retained_indexes.add(index)
        retained_characters += content_length
    retained = list(reversed(retained_reversed))

    trusted_system = [
        copied
        for item in (trusted_system_messages or ())
        if isinstance(item, Mapping)
        for copied in (_copy_trusted_system_message(item),)
        if copied is not None
    ]
    supplemental = [
        copied
        for item in (supplemental_messages or ())
        if isinstance(item, Mapping)
        for copied in (_copy_supplemental_message(item),)
        if copied is not None
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": TELEGRAM_DOMAIN_POLICY,
            "_protected": True,
        },
        *trusted_system,
        *retained,
        *supplemental,
        # This stays an ordinary final user message. The downstream trimmer's
        # latest-dialog preservation retains it in final position; marking it
        # protected would move it ahead of the retained conversation.
        {"role": "user", "content": prompt},
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
        "trusted_runtime_system_message_count": len(trusted_system),
        "history_message_limit": message_limit,
        "history_character_limit": character_limit,
        "history_structure_fingerprint": _history_fingerprint(
            normalized_history,
            retained_indexes,
        ),
        "todo_state_authority": "manage_todos",
        "summary_authoritative": False,
        "memory_authoritative": False,
        "rag_authoritative": False,
        "session_mutated": False,
        "domain_policy_protected": True,
        "current_user_turn_final": True,
    }
    return TelegramContextWindow(messages=tuple(messages), evidence=evidence)


def _copy_supplemental_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Copy supplemental text into a fixed untrusted user-message envelope."""

    content = _content_as_text(message.get("content")).strip()
    if not content:
        return None
    # Ignore caller-provided role, protection flags, and metadata. RAG/source
    # material remains data, and metadata may contain private provider details.
    return untrusted_context_message("telegram supplemental context", content)


def _copy_trusted_system_message(message: Mapping[str, Any]) -> dict[str, str] | None:
    """Copy only an explicitly routed runtime system message's text content."""

    if str(message.get("role") or "").strip().lower() != "system":
        return None
    content = _content_as_text(message.get("content")).strip()
    if not content:
        return None
    return {"role": "system", "content": content}


def _content_as_text(content: Any) -> str:
    """Accept plain strings and explicit text blocks; reject other structures."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, Mapping) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _history_fingerprint(
    messages: Sequence[Mapping[str, str]], retained_indexes: set[int]
) -> str:
    """Hash only a structural projection, never recoverable message contents."""

    projection = [
        {
            "role": item["role"],
            "characters": len(item["content"]),
            "retained": index in retained_indexes,
        }
        for index, item in enumerate(messages)
    ]
    canonical = json.dumps(projection, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(
        b"odysseus.telegram-context-structure.v1\0" + canonical.encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _positive_int(name: str, value: Any, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > maximum
    ):
        raise ValueError(f"{name} must be a positive integer")
    return value
