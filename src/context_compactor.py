"""
context_compactor.py

Auto-compacts conversation history when approaching context window limits.
Summarizes older messages via the same LLM, preserving key context.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from src.model_context import get_context_length, estimate_tokens
from src.token_estimator import estimate_character_capacity, estimate_text_tokens
from src.llm_core import llm_call_async
from src.endpoint_resolver import resolve_endpoint
from core.models import ChatMessage

logger = logging.getLogger(__name__)


def _content_as_text(content: Any) -> str:
    """Flatten a message's content to plain text.

    Handles the three shapes that flow through history: a plain string, a
    multimodal list of content blocks (vision/image attachments), and None
    (assistant turns that carried only native tool_calls persist content as
    None). Returns "" for anything without text so callers can safely slice
    the result.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("text")
        )
    return ""


COMPACT_THRESHOLD = 0.65  # Default trigger at 65% of context window
SUMMARY_MAX_TOKENS = 1024
SMALL_CONTEXT_LIMIT = 8192  # Models with context <= this get aggressive trimming
TASK_STATE_HEADER = "[Persistent task state]"


def get_compact_threshold() -> float:
    """Return the configured compaction threshold, clamped to a safe range."""
    try:
        from src.settings import get_setting
        value = float(get_setting("context_compact_threshold", COMPACT_THRESHOLD))
    except (TypeError, ValueError):
        value = COMPACT_THRESHOLD
    return min(max(value, 0.40), 0.90)

# Cursor-style self-summarization prompt — produces structured, dense summaries
SELF_SUMMARY_SYSTEM_PROMPT = """You are summarizing a conversation to preserve context after compaction. Produce a structured summary that lets the conversation continue seamlessly.

Use this format:

## Conversation Summary
**Turns summarized:** {count}  |  **Compactions so far:** {n}

### User Goal
One sentence describing what the user is trying to accomplish.

### What Was Done
- Bullet points of completed actions, decisions made, and key outputs
- Include specific file paths, function names, variable names, URLs, and config values
- Note any errors encountered and how they were resolved

### Current State
What is the system/code/task state right now? What was the last thing discussed?

### Pending / Next Steps
- What remains to be done
- Any open questions or blockers

### Key Context
- Important constraints, preferences, or decisions that must not be forgotten
- Specific values: model names, ports, paths, credentials references, versions

Keep the summary under 1000 tokens. Be dense — every token should carry information. Do not include pleasantries or meta-commentary."""


def build_task_state_message(summary: str, recent_messages: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Build a compact persistent task-state system block from a summary."""
    recent_messages = recent_messages or []
    state = {
        "CURRENT_TASK": _first_section_line(summary, "User Goal") or _latest_user_text(recent_messages) or "unknown",
        "COMPLETED_STEPS": _section_bullets(summary, "What Was Done") or ["none recorded"],
        "KNOWN_CONSTRAINTS": _section_bullets(summary, "Key Context") or ["none recorded"],
        "OPEN_QUESTIONS": _section_bullets(summary, "Pending / Next Steps") or ["none recorded"],
    }
    content = (
        f"{TASK_STATE_HEADER}\n"
        "CURRENT_TASK: " + state["CURRENT_TASK"] + "\n"
        "COMPLETED_STEPS:\n" + _format_bullets(state["COMPLETED_STEPS"]) + "\n"
        "KNOWN_CONSTRAINTS:\n" + _format_bullets(state["KNOWN_CONSTRAINTS"]) + "\n"
        "OPEN_QUESTIONS:\n" + _format_bullets(state["OPEN_QUESTIONS"])
    )
    return {"role": "system", "content": content, "metadata": {"task_state": True}}


def _section_text(text: str, heading: str) -> str:
    marker = f"### {heading}"
    raw = str(text or "")
    start = raw.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = raw.find("\n### ", start)
    return raw[start:end if end != -1 else len(raw)].strip()


def _first_section_line(text: str, heading: str) -> str:
    for line in _section_text(text, heading).splitlines():
        stripped = line.strip().strip("-*").strip()
        if stripped:
            return stripped
    return ""


def _section_bullets(text: str, heading: str) -> List[str]:
    bullets = []
    for line in _section_text(text, heading).splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            value = stripped[1:].strip()
            if value:
                bullets.append(value)
    if not bullets:
        fallback = _first_section_line(text, heading)
        if fallback:
            bullets.append(fallback)
    return bullets[:8]


def _latest_user_text(messages: List[Dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _content_as_text(msg.get("content")).strip()[:500]
    return ""


def _format_bullets(items: List[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _sanitize_tool_messages(msgs: List[Dict]) -> List[Dict]:
    """Drop orphaned `tool` messages and dangling assistant `tool_calls`.

    OpenAI's API requires every `role:"tool"` message to immediately
    follow an assistant message that carries `tool_calls` (or another
    tool message in the same batch). Front-trimming the history can cut
    the assistant `tool_calls` parent while keeping its tool responses,
    which triggers: "messages with role 'tool' must be a response to a
    preceding message with 'tool_calls'". This pass repairs that:
      - drops `tool` messages with no valid preceding tool_calls
      - removes calls whose matching response was trimmed away
      - keeps only matching, unique tool results (providers reject unanswered
        calls, orphan results, and mismatched ``tool_call_id`` values)
    """
    out: List[Dict] = []
    i = 0
    while i < len(msgs):
        msg = msgs[i]
        if msg.get("role") == "tool":
            i += 1  # orphaned result
            continue
        calls = msg.get("tool_calls") if msg.get("role") == "assistant" else None
        if not isinstance(calls, list) or not calls:
            out.append(msg)
            i += 1
            continue

        valid_calls = {
            str(call.get("id")): call
            for call in calls
            if isinstance(call, dict) and call.get("id")
        }
        results: List[Dict] = []
        answered: set[str] = set()
        j = i + 1
        while j < len(msgs) and msgs[j].get("role") == "tool":
            result = msgs[j]
            call_id = str(result.get("tool_call_id") or "")
            if call_id in valid_calls and call_id not in answered:
                results.append(result)
                answered.add(call_id)
            j += 1

        if answered:
            repaired = dict(msg)
            repaired["tool_calls"] = [
                call for call in calls
                if isinstance(call, dict) and str(call.get("id") or "") in answered
            ]
            out.append(repaired)
            out.extend(results)
        else:
            # Keep accompanying assistant text as a normal turn, but never emit
            # an unanswered tool call after compaction.
            repaired = {k: v for k, v in msg.items() if k != "tool_calls"}
            content = repaired.get("content")
            if isinstance(content, str) and content.strip():
                out.append(repaired)
            elif isinstance(content, list) and content:
                out.append(repaired)
        i = j
    return out


def _message_text_token_estimate(text: str, model_hint: Optional[str] = None) -> int:
    if not isinstance(text, str):
        return 4
    if model_hint is not None and str(model_hint).strip():
        return estimate_text_tokens(text, model_hint=model_hint).count + 4
    return int(len(text) * 0.3) + 4


def _truncate_text_to_token_budget(
    text: str,
    token_budget: int,
    *,
    message_kind: str = "user",
    model_hint: Optional[str] = None,
) -> str:
    """Trim an oversized recent turn with an explicit, model-visible notice."""
    is_assistant = message_kind == "assistant"
    if token_budget <= 32:
        if is_assistant:
            return "[Previous assistant response omitted: it exceeded the model context window.]"
        return "[Current user message omitted: it exceeded the model context window.]"

    if not isinstance(text, str):
        # This helper is typed/used as text downstream, so return an empty
        # string rather than the raw non-string (which would move the crash
        # into the caller that concatenates/measures the result).
        return ""
    model_aware = model_hint is not None and str(model_hint).strip()
    if model_aware:
        if estimate_text_tokens(text, model_hint=model_hint).count <= token_budget:
            return text
        max_chars = max(
            1,
            estimate_character_capacity(max(1, token_budget - 16), model_hint=model_hint),
        )
    else:
        # Preserve the historical no-hint behavior exactly.
        max_chars = max(200, int((token_budget - 16) / 0.3))
    if not model_aware and len(text) <= max_chars:
        return text

    if is_assistant:
        notice = (
            "\n\n[Notice: the previous assistant response was too large for this "
            "model's context window, so Odysseus kept the beginning and end.]"
        )
    else:
        notice = (
            "\n\n[Notice: the pasted message was too large for this model's context "
            "window, so Odysseus kept the beginning and end.]"
        )
    keep_chars = max(200, max_chars - len(notice))
    head_len = max(100, int(keep_chars * 0.7))
    tail_len = max(80, keep_chars - head_len)
    truncated = text[:head_len].rstrip() + notice + "\n\n" + text[-tail_len:].lstrip()
    if not model_aware:
        return truncated

    # Character capacity is an initial bound. Verify the final notice-bearing
    # result with the selected estimator because Unicode byte length and the
    # notice itself can otherwise exceed the hard limit.
    while truncated and estimate_text_tokens(truncated, model_hint=model_hint).count > token_budget:
        keep_chars = max(0, int(keep_chars * 0.85))
        if keep_chars <= 0:
            truncated = ""
            break
        head_len = max(1, int(keep_chars * 0.7))
        tail_len = max(0, keep_chars - head_len)
        tail = text[-tail_len:].lstrip() if tail_len else ""
        truncated = text[:head_len].rstrip() + notice + ("\n\n" + tail if tail else "")
        if estimate_text_tokens(notice, model_hint=model_hint).count > token_budget:
            notice_chars = estimate_character_capacity(token_budget, model_hint=model_hint)
            truncated = notice[:notice_chars]
            while truncated and estimate_text_tokens(truncated, model_hint=model_hint).count > token_budget:
                truncated = truncated[:-1]
            break
    return truncated


def _truncate_tool_call_args(
    msg: Dict[str, Any],
    token_budget: int,
    *,
    model_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Shrink oversized assistant ``tool_calls`` arguments to fit ``token_budget``.

    A tool-only turn persists ``content=None`` with its whole payload in
    ``tool_calls[].function.arguments`` (e.g. a large create_document body), which
    the text-content truncation can't reach — so the message could stay over
    budget and the upstream call would 400. Replace each argument string that
    overflows its share of the budget with a small valid-JSON placeholder,
    preserving ``id``/``type``/``function.name`` so tool/result pairing and
    provider validation are unaffected. Returns msg unchanged when there is
    nothing oversized.
    """
    tool_calls = msg.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return msg
    # Budget left after whatever content survived (estimate_tokens counts tool
    # arguments too, so measure content alone here).
    content_tokens = estimate_tokens(
        [{"role": msg.get("role", "assistant"), "content": msg.get("content")}],
        model_hint=model_hint,
    )
    per_call = max(16, (max(0, token_budget - content_tokens)) // len(tool_calls))
    new_calls = []
    changed = False
    for tc in tool_calls:
        fn = tc.get("function") if isinstance(tc, dict) else None
        args = fn.get("arguments") if isinstance(fn, dict) else None
        args_tokens = (
            estimate_text_tokens(args, model_hint=model_hint).count
            if isinstance(args, str) and model_hint is not None and str(model_hint).strip()
            else int(len(args) * 0.3) if isinstance(args, str) else 0
        )
        if isinstance(args, str) and args_tokens > per_call:
            new_fn = dict(fn)
            new_fn["arguments"] = json.dumps({"_truncated_for_context": len(args)})
            new_tc = dict(tc)
            new_tc["function"] = new_fn
            new_calls.append(new_tc)
            changed = True
        else:
            new_calls.append(tc)
    if not changed:
        return msg
    out = dict(msg)
    out["tool_calls"] = new_calls
    return out


def _truncate_message_to_token_budget(
    msg: Dict[str, Any],
    token_budget: int,
    *,
    message_kind: str = "user",
    model_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a copy of msg whose text content (and tool-call args) fit token_budget."""
    out = dict(msg)
    content = out.get("content", "")
    if isinstance(content, str):
        out["content"] = _truncate_text_to_token_budget(
            content,
            token_budget,
            message_kind=message_kind,
            model_hint=model_hint,
        )
    elif isinstance(content, list):
        remaining = token_budget
        new_content = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                new_content.append(item)
                continue
            text = item.get("text", "")
            truncated = _truncate_text_to_token_budget(
                text,
                remaining,
                message_kind=message_kind,
                model_hint=model_hint,
            )
            cloned = dict(item)
            cloned["text"] = truncated
            new_content.append(cloned)
            remaining -= _message_text_token_estimate(truncated, model_hint=model_hint)
        out["content"] = new_content
    # A tool-only turn (content=None) carries its payload in tool_calls args,
    # which the branches above can't shrink — handle it so the message can fit.
    return _truncate_tool_call_args(out, token_budget, model_hint=model_hint)


def _latest_dialog_indices(messages: List[Dict]) -> tuple[Optional[int], Optional[int]]:
    """Return ``(previous_assistant, current_user)`` conversation indices."""
    user_idx = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if user_idx is None:
        return None, None
    assistant_idx = next(
        (i for i in range(user_idx - 1, -1, -1) if messages[i].get("role") == "assistant"),
        None,
    )
    return assistant_idx, user_idx


def latest_dialog_pair_preserved(
    original: List[Dict],
    trimmed: List[Dict],
    model_hint: Optional[str] = None,
) -> bool:
    """Return whether trimming retained (or visibly truncated) the latest pair."""
    original_assistant, original_user = _latest_dialog_indices(original)
    if original_user is None:
        return True
    trimmed_assistant, trimmed_user = _latest_dialog_indices(trimmed)
    if trimmed_user is None:
        return False

    def _matches(source: Dict, candidate: Dict, kind: str) -> bool:
        if source is candidate or source == candidate:
            return True
        if source.get("role") != candidate.get("role"):
            return False
        source_text = _content_as_text(source.get("content"))
        candidate_text = _content_as_text(candidate.get("content"))
        visible_markers = (
            "[Current user message omitted:",
            "[Previous assistant response omitted:",
            "[Previous assistant tool response omitted",
        )
        if model_hint is not None and str(model_hint).strip():
            visible_markers += (
                "previous assistant response was too large",
                "pasted message was too large",
            )
        if any(marker in candidate_text for marker in visible_markers):
            return True
        notice = "\n\n[Notice:"
        candidate_prefix = candidate_text.split(notice, 1)[0]
        if source_text and candidate_prefix and source_text.startswith(candidate_prefix):
            return True
        if not source_text and kind == "assistant":
            source_ids = {
                str(call.get("id")) for call in source.get("tool_calls", [])
                if isinstance(call, dict) and call.get("id")
            }
            candidate_ids = {
                str(call.get("id")) for call in candidate.get("tool_calls", [])
                if isinstance(call, dict) and call.get("id")
            }
            return bool(source_ids & candidate_ids)
        return False

    if not _matches(original[original_user], trimmed[trimmed_user], "user"):
        return False
    if original_assistant is None:
        return True
    return trimmed_assistant is not None and _matches(
        original[original_assistant], trimmed[trimmed_assistant], "assistant",
    )


def trim_for_context(
    messages: List[Dict],
    context_length: int,
    reserve_tokens: int = 512,
    model_hint: Optional[str] = None,
) -> List[Dict]:
    """Trim messages to a non-negative prompt budget.

    ``reserve_tokens`` is subtracted once for callers that pass a real model
    window. Callers passing an already computed input cap must pass zero.
    RAG/memory and older tool/history messages are reduced before the latest
    user message and its immediately preceding assistant response.
    """
    try:
        window = max(0, int(context_length or 0))
    except (TypeError, ValueError):
        window = 0
    try:
        reserve = max(0, int(reserve_tokens or 0))
    except (TypeError, ValueError):
        reserve = 0
    budget = max(1, window - reserve)
    used = estimate_tokens(messages, model_hint=model_hint)
    if used <= budget:
        return messages

    logger.info("Trimming messages: %s tokens > %s budget (ctx=%s)", used, budget, window)

    # Separate system messages from conversation.
    # Messages marked _protected (e.g. active document) are never trimmed.
    system_msgs = []
    protected_msgs = []
    convo_msgs = []
    for msg in messages:
        if msg.get("_protected"):
            protected_msgs.append(msg)
        elif msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            convo_msgs.append(msg)

    # Protected messages count toward budget but are never dropped
    protected_tokens = estimate_tokens(protected_msgs, model_hint=model_hint)
    available_budget = max(1, budget - protected_tokens)

    # Priority: keep first system msg (preset prompt), drop others (memory, RAG, memo).
    # Exception: a research-spinoff primer (the seeded report that grounds a
    # "Discuss" chat) must never be dropped — it is the conversation's whole
    # knowledge base. Treat any system message carrying research_spinoff_from
    # metadata as essential alongside the leading system prompt.
    def _is_research_primer(m):
        return bool((m.get("metadata") or {}).get("research_spinoff_from"))
    _primers = [m for m in system_msgs if _is_research_primer(m)]
    _non_primer = [m for m in system_msgs if not _is_research_primer(m)]
    essential_system = (_non_primer[:1] if _non_primer else []) + _primers
    extra_system = _non_primer[1:]

    # Try dropping extra system messages one by one (from the end)
    trimmed = essential_system + convo_msgs
    if estimate_tokens(trimmed, model_hint=model_hint) <= available_budget:
        # Dropping extras was enough — try adding back some
        result = list(essential_system)
        for msg in extra_system:
            candidate = result + [msg] + convo_msgs
            if estimate_tokens(candidate, model_hint=model_hint) <= available_budget:
                result.append(msg)
            else:
                break
        return _sanitize_tool_messages(result + protected_msgs + convo_msgs)

    # Still too big — truncate the first system message (but keep more than 500 chars)
    if essential_system:
        sys_text = essential_system[0].get("content", "")
        if len(sys_text) > 2000:
            essential_system[0] = {"role": "system", "content": sys_text[:2000] + "\n[System prompt truncated for context limits]"}
            trimmed = essential_system + convo_msgs
            if estimate_tokens(trimmed, model_hint=model_hint) <= available_budget:
                return _sanitize_tool_messages(essential_system + protected_msgs + convo_msgs)

    # Still too big: shed older/tool-heavy turns while protecting the latest
    # user/assistant dialog pair used by follow-up questions.
    latest_assistant_idx, latest_user_idx = _latest_dialog_indices(convo_msgs)
    mandatory = {i for i in (latest_assistant_idx, latest_user_idx) if i is not None}
    kept = set(range(len(convo_msgs)))
    tool_heavy = [
        i for i, msg in enumerate(convo_msgs)
        if i not in mandatory and (msg.get("role") == "tool" or msg.get("tool_calls"))
    ]
    remaining_old = [i for i in range(len(convo_msgs)) if i not in mandatory and i not in tool_heavy]
    for drop_idx in tool_heavy + remaining_old:
        current = [msg for i, msg in enumerate(convo_msgs) if i in kept]
        if estimate_tokens(essential_system + current, model_hint=model_hint) <= available_budget:
            break
        kept.discard(drop_idx)
    convo_msgs = [msg for i, msg in enumerate(convo_msgs) if i in kept]

    # If the direct pair alone is too large, retain both roles with visible
    # truncation/omission notices rather than silently severing the follow-up.
    latest_assistant_idx, latest_user_idx = _latest_dialog_indices(convo_msgs)
    if estimate_tokens(essential_system + convo_msgs, model_hint=model_hint) > available_budget and latest_user_idx is not None:
        pair_indices = [i for i in (latest_assistant_idx, latest_user_idx) if i is not None]
        non_pair = [msg for i, msg in enumerate(convo_msgs) if i not in pair_indices]
        pair_budget = max(
            1,
            available_budget
            - estimate_tokens(essential_system + non_pair, model_hint=model_hint)
            - (4 * len(pair_indices)),
        )
        assistant_budget = pair_budget // 2 if latest_assistant_idx is not None else 0
        user_budget = pair_budget - assistant_budget
        if latest_assistant_idx is not None:
            convo_msgs[latest_assistant_idx] = _truncate_message_to_token_budget(
                convo_msgs[latest_assistant_idx],
                assistant_budget,
                message_kind="assistant",
                model_hint=model_hint,
            )
        convo_msgs[latest_user_idx] = _truncate_message_to_token_budget(
            convo_msgs[latest_user_idx],
            user_budget,
            message_kind="user",
            model_hint=model_hint,
        )

    result = _sanitize_tool_messages(essential_system + protected_msgs + convo_msgs)
    if latest_assistant_idx is not None and not latest_dialog_pair_preserved(
        messages,
        result,
        model_hint=model_hint,
    ):
        user_pos = next(
            (i for i in range(len(result) - 1, -1, -1) if result[i].get("role") == "user"),
            len(result),
        )
        result.insert(user_pos, {
            "role": "assistant",
            "content": "[Previous assistant tool response omitted during context compaction.]",
        })
    logger.info(
        "Trimmed context: before=%s after=%s messages=%s latest_pair_preserved=%s",
        used,
        estimate_tokens(result, model_hint=model_hint),
        len(result),
        latest_dialog_pair_preserved(messages, result, model_hint=model_hint),
    )
    return result


async def maybe_compact(
    session,
    endpoint_url: str,
    model: str,
    messages: List[Dict],
    headers: Optional[Dict] = None,
    owner: Optional[str] = None,
    model_hint: Optional[str] = None,
) -> tuple:
    """Check context usage and compact if above threshold.

    Returns (messages, context_length, was_compacted).
    """
    context_length = get_context_length(endpoint_url, model)
    effective_model_hint = model_hint or model
    used = estimate_tokens(messages, model_hint=effective_model_hint)
    pct = (used / context_length) * 100 if context_length else 0

    threshold = get_compact_threshold()
    if pct < threshold * 100:
        return messages, context_length, False

    logger.info(
        f"Context at {pct:.1f}% ({used}/{context_length} tokens) — compacting"
    )

    # Split into system preface and conversation
    system_msgs = []
    convo_msgs = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            convo_msgs.append(msg)

    if len(convo_msgs) < 4:
        return messages, context_length, False

    # Split conversation: summarize older half, keep recent half
    split_point = len(convo_msgs) // 2
    older = convo_msgs[:split_point]
    recent = convo_msgs[split_point:]

    # Build the text to summarize
    convo_text = "\n".join(
        f"{msg.get('role', 'user').upper()}: {_content_as_text(msg.get('content'))[:2000]}"
        for msg in older
    )

    # Count prior compactions from existing summary messages
    compaction_count = sum(
        1 for m in system_msgs
        if "[Conversation summary" in m.get("content", "")
    )

    # Use utility model if configured, otherwise fall back to session model
    util_url, util_model, util_headers = resolve_endpoint("utility", owner=owner)
    compact_url = util_url or endpoint_url
    compact_model = util_model or model
    compact_headers = util_headers if util_url else headers

    prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace(
        "{count}", str(len(older))
    ).replace(
        "{n}", str(compaction_count + 1)
    )
    summary_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": convo_text},
    ]

    try:
        summary = await llm_call_async(
            compact_url,
            compact_model,
            summary_messages,
            temperature=0.2,
            max_tokens=SUMMARY_MAX_TOKENS,
            headers=compact_headers,
            timeout=30,
            owner=owner,
            surface="context_compactor",
            session_id=getattr(session, "id", None),
            prompt_type="context_auto_compact",
        )
    except Exception as e:
        logger.error(f"Compaction summary failed: {e}")
        # Degrade gracefully: keep the conversation intact rather than
        # silently dropping the older half. was_compacted=False signals the
        # caller nothing was summarized; trim_for_context handles length.
        return messages, context_length, False

    summary_msg = {
        "role": "system",
        "content": f"[Conversation summary — earlier messages were compacted]\n{summary}",
    }
    task_state_msg = build_task_state_message(summary, recent)

    compacted = system_msgs + [summary_msg, task_state_msg] + recent

    # Update session history to match. Pass len(system_msgs) so the
    # recent_history slice in _update_session_history uses the correct
    # offset — session.history INCLUDES the system messages, but
    # split_point is indexed against convo_msgs which does NOT. Without
    # this, the slice drops the leading system message(s).
    _update_session_history(
        session,
        split_point,
        summary,
        system_msg_count=len(system_msgs),
        recent_messages=recent,
    )

    new_used = estimate_tokens(compacted, model_hint=effective_model_hint)
    logger.info(
        f"Compacted: {used} -> {new_used} tokens "
        f"({len(older)} messages summarized, {len(recent)} kept)"
    )

    return compacted, context_length, True


def _update_session_history(session, split_point: int, summary: str,
                            system_msg_count: int = 0,
                            recent_messages: Optional[List[Dict]] = None):
    """Update the in-memory session history after compaction.

    `split_point` is the index in `convo_msgs` (system-stripped). The
    in-memory `session.history` includes leading system messages, so the
    actual recent-history slice starts at `system_msg_count + split_point`.
    Prepending `session.history[:system_msg_count]` to the new history
    preserves persona, preset, and RAG system messages that would
    otherwise be dropped.
    """
    if not session or not hasattr(session, "history"):
        return

    effective_split = system_msg_count + split_point
    if effective_split >= len(session.history):
        return

    # Keep the recent messages, prepend summary AND the leading system
    # messages so the system prompt survives compaction.
    system_prefix = list(session.history[:system_msg_count])
    recent_history = session.history[effective_split:]
    summary_msg = ChatMessage(
        role="system",
        content=f"[Conversation summary]\n{summary}",
        metadata={"compacted": True, "summarized_count": split_point},
    )
    task_state_payload = build_task_state_message(summary, recent_messages or recent_history)
    task_state_msg = ChatMessage(
        role="system",
        content=task_state_payload["content"],
        metadata={"task_state": True},
    )
    new_history = system_prefix + [summary_msg, task_state_msg] + recent_history
    try:
        from core.models import get_session_manager_instance
        manager = get_session_manager_instance()
    except Exception:
        manager = None
    if manager and getattr(session, "id", None):
        if manager.replace_messages(session.id, new_history):
            return
    session.history = new_history
