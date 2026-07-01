"""Provider message-format helpers for :mod:`src.llm_core`."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Dict, List

from src.text_helpers import strip_think

logger = logging.getLogger(__name__)


def _anthropic_rejects_temperature(model: str) -> bool:
    """Check if a native-Anthropic model rejects the temperature field (Opus 4.7+)."""
    if not isinstance(model, str) or not model:
        return False
    # `(?<![a-z])` anchors "opus" to a word boundary so a substring match like
    # `oct-opus`/`octopus-4-8` can't be read as Opus (it would otherwise strip
    # temperature). Cap the minor at 1-2 digits and forbid a trailing digit so a
    # dated id like `claude-opus-4-20250514` (Opus 4.0) parses as major-only (no
    # minor match, kept) instead of reading the date `20250514` as a giant minor
    # that would falsely test >= 4.7. Dated 4.7+ snapshots (`claude-opus-4-7-
    # 20260201`) keep their explicit minor and are still matched.
    match = re.search(r"(?<![a-z])opus[-_]?(\d+)[-_.](\d{1,2})(?!\d)", model.lower())
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (4, 7)

# Models that support structured thinking — may output </think> without opening tag
_MISTRAL_REASONING_EFFORT = os.getenv("ODYSSEUS_MISTRAL_REASONING_EFFORT", "high")

_THINKING_MODEL_PATTERNS = (
    "qwen3", "qwq", "deepseek-r1", "deepseek-reasoner", "minimax",
    "m2-reap", "gemma", "magistral", "mistral-small", "mistral-medium",
)

def _supports_thinking(model: str) -> bool:
    """Check if model supports structured thinking output."""
    if not model:
        return False
    m = model.lower()
    return any(p in m for p in _THINKING_MODEL_PATTERNS)

def _normalize_mistral_content(content):
    if isinstance(content, str):
        return content, ""
    if not isinstance(content, list):
        return "", ""
    text_parts = []
    thinking_parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text:
                text_parts.append(text)
        elif btype == "thinking":
            inner = block.get("thinking", [])
            if isinstance(inner, list):
                for tb in inner:
                    if isinstance(tb, dict) and tb.get("text"):
                        thinking_parts.append(tb["text"])
            elif isinstance(inner, str):
                thinking_parts.append(inner)
    return "".join(text_parts), "".join(thinking_parts)


_VISIBLE_REASONING_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"wir m(?:ü|ue)ssen|"
    r"ich m(?:u|ü)ss|"
    r"der benutzer|"
    r"der nutzer|"
    r"die anfrage|"
    r"we need to|"
    r"i need to|"
    r"the user|"
    r"the request"
    r")\b",
    re.IGNORECASE,
)
_ANSWER_START_RE = re.compile(
    r"(?m)^(?:"
    r"\s*(?:#{1,6}\s+|\*\*[^*\n]{2,80}\*\*|[-*]\s+|\d+[.)]\s+)|"
    r"\s*(?:Sofortcheck|Automatisierung|Human-Gate|Kurzantwort|Antwort|Ergebnis|Fazit)\s*:"
    r")"
)


def _model_needs_visible_reasoning_scrub(model: str) -> bool:
    if not model:
        return False
    m = model.lower()
    return "deepseek-v" in m or "deepseek-flash" in m


_FINAL_ONLY_GUARD = (
    "Return only the final user-facing answer. Do not include hidden reasoning, "
    "analysis, planning notes, or explanations about how you interpret the request."
)


def _apply_visible_reasoning_guard(messages: List[Dict], model: str) -> List[Dict]:
    """Add a narrow anti-reasoning instruction for models known to leak it."""
    if not _model_needs_visible_reasoning_scrub(model):
        return messages
    guarded = [dict(message) for message in messages]
    if guarded and guarded[0].get("role") == "system":
        first = dict(guarded[0])
        first["content"] = "\n\n".join(
            part for part in (str(first.get("content") or ""), _FINAL_ONLY_GUARD) if part
        )
        guarded[0] = first
    else:
        guarded.insert(0, {"role": "system", "content": _FINAL_ONLY_GUARD})
    return guarded


def _strip_visible_reasoning_preamble(text: str, model: str) -> str:
    if not text:
        return ""

    cleaned = strip_think(text, prose=False, prompt_echo=False)
    if cleaned != text:
        return cleaned.strip()

    if not _model_needs_visible_reasoning_scrub(model):
        return text.strip()

    stripped = text.strip()
    cleaned = strip_think(stripped, prose=True, prompt_echo=True)
    if cleaned != stripped:
        return cleaned.strip()

    if not _VISIBLE_REASONING_PREFIX_RE.match(stripped):
        return stripped

    matches = list(_ANSWER_START_RE.finditer(stripped))
    for match in matches:
        # Avoid treating a numbered reasoning sentence at the very beginning as
        # the final answer. A real answer marker after a visible preamble is
        # normally separated by at least one paragraph or several sentences.
        if match.start() > 40:
            return stripped[match.start():].strip()

    # If the model spent the whole budget on visible reasoning, suppress it
    # rather than showing chain-of-thought as the answer.
    return ""


def _parse_openai_compatible_message(
    message: dict,
    *,
    model: str,
    normalize_content_func: Callable[[Any], tuple[str, str]] = _normalize_mistral_content,
) -> str:
    """Extract user-facing text from an OpenAI-compatible chat message.

    Some gateways split thinking into structured fields (`reasoning_content`,
    `reasoning`, `thinking`). Others, notably weak DeepSeek-V/Flash variants,
    can leak a leading reasoning preamble inside `content`. Keep ordinary
    content first, preserve the existing reasoning fallback for reasoning-only
    responses, but scrub known visible preambles before returning text.
    """
    content = message.get("content")
    reasoning = (
        message.get("reasoning_content")
        or message.get("reasoning")
        or message.get("thinking")
        or ""
    )
    if isinstance(content, list):
        text_part, thinking_part = normalize_content_func(content)
        text = ((thinking_part + "\n\n") if thinking_part else "") + (text_part or "")
        if not text:
            text = reasoning or ""
    else:
        text = content or reasoning or ""
    return _strip_visible_reasoning_preamble(str(text), model)

def _convert_openai_content_to_anthropic(content):
    """Convert OpenAI multimodal content blocks to Anthropic format.

    Converts image_url blocks (data URI) → Anthropic image blocks.
    Passes text blocks through unchanged.
    """
    if not isinstance(content, list):
        return content
    converted = []
    for block in content:
        if not isinstance(block, dict):
            converted.append(block)
            continue
        if block.get("type") == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            # Parse data URI: data:image/<fmt>;base64,<data>
            if url.startswith("data:"):
                try:
                    header, b64_data = url.split(",", 1)
                    media_type = header.split(";")[0].replace("data:", "")
                except (ValueError, IndexError):
                    continue
                converted.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                })
            else:
                # External URL — use Anthropic's URL source
                converted.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
        elif block.get("type") == "text":
            converted.append(block)
        else:
            converted.append(block)
    return converted


def _build_anthropic_payload(model, messages, temperature, max_tokens, stream=False, tools=None):
    """Convert OpenAI-style messages to Anthropic format."""
    system_parts = []
    chat_messages = []
    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m.get("content") or "")
        elif m.get("role") == "tool":
            # Convert OpenAI tool result to Anthropic format
            chat_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }],
            })
        elif m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list):
            # Convert OpenAI assistant tool_calls to Anthropic format
            content = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                args_str = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except (json.JSONDecodeError, TypeError):
                    args = {}
                content.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args,
                })
            chat_messages.append({"role": "assistant", "content": content})
        else:
            # Convert multimodal content (image_url → image) for Anthropic
            content = _convert_openai_content_to_anthropic(m["content"])
            chat_messages.append({"role": m["role"], "content": content})
    # Anthropic only accepts temperature in [0.0, 1.0] and 400s on anything above
    # 1.0. Clamp here (in the Anthropic builder only) so presets/sliders that use
    # the wider OpenAI 0.0-2.0 range — e.g. the shipped "Nietzsche" preset at 1.2
    # — don't hard-break every Claude request. OpenAI's own path is left untouched.
    if temperature is not None:
        temperature = max(0.0, min(temperature, 1.0))
    payload = {
        "model": model,
        "messages": chat_messages,
        "max_tokens": max_tokens if max_tokens and max_tokens > 0 else 4096,
    }
    # Opus 4.7+ removed the sampling parameters — sending `temperature` (even 0.0)
    # returns HTTP 400. Omit it for those models; older Claude models still take it.
    if not _anthropic_rejects_temperature(model):
        payload["temperature"] = temperature
    if system_parts:
        system_text = "\n\n".join(system_parts)
        # Send `system` as a structured text block so we can attach a prompt-cache
        # breakpoint. The agent loop re-sends this same large prefix every round;
        # caching it makes Anthropic re-read it from cache (~90% cheaper, lower TTFB)
        # instead of re-billing it. Skip caching tiny one-off prompts, where the
        # cache-WRITE premium wouldn't pay back (no reuse). Presence of `tools`
        # means an agentic/multi-round call, where the prefix is always reused.
        system_block = {"type": "text", "text": system_text}
        if tools or len(system_text) > 4000:
            system_block["cache_control"] = {"type": "ephemeral"}
        payload["system"] = [system_block]
    if stream:
        payload["stream"] = True
    # Convert OpenAI-format tools to Anthropic format
    if tools:
        anthropic_tools = []
        for t in tools:
            if t.get("type") == "function":
                fn = t["function"]
                anthropic_tools.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
        if anthropic_tools:
            # Cache the tool schemas too — they're stable for the whole agent run.
            # The breakpoint caches all tool defs preceding it in the request.
            anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = anthropic_tools
    return payload

def _build_anthropic_headers(headers):
    """Convert Bearer auth to x-api-key for Anthropic."""
    h = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    if headers:
        for k, v in headers.items():
            if k.lower() == "authorization" and isinstance(v, str) and v.startswith("Bearer "):
                h["x-api-key"] = v[7:]
            else:
                h[k] = v
    return h

def _parse_anthropic_response(data: dict) -> str:
    """Extract text from an Anthropic response.

    The Messages API `content` is an array that can hold more than one text
    block (e.g. text split around a tool_use block, or citation-segmented
    text). Concatenate them all instead of returning only the first, which
    silently dropped the rest of the reply.
    """
    return "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _as_content_blocks(content) -> List[Dict]:
    """Coerce a message `content` into a list of content blocks.

    A list (multimodal: text + image parts) passes through; a non-empty string
    becomes a single text block; None/empty yields no blocks. Used when merging
    consecutive user messages so multimodal content isn't str()-ed away.
    """
    if isinstance(content, list):
        return content
    if content:
        return [{"type": "text", "text": str(content)}]
    return []


def _sanitize_llm_messages(messages: List[Dict]) -> List[Dict]:
    """Strip Odysseus-only metadata before sending messages to providers.

    Per the OpenAI chat format: user/system messages must have content; a tool
    message needs content + tool_call_id; an assistant message may carry content,
    tool_calls, or both. The old guard required content on every message, which
    dropped a valid assistant message that has only tool_calls — e.g. the
    follow-up message _append_tool_results builds for a no-prose native tool call
    (content=None, since Gemini/Ollama reject tool_calls alongside ""). Dropping
    it leaves the tool result dangling and breaks the next round.
    """
    allowed = {"role", "content", "name", "tool_call_id", "tool_calls", "function_call", "reasoning_content"}
    cleaned = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        item = {k: v for k, v in msg.items() if k in allowed and v is not None}
        role = item.get("role")
        if not role:
            continue
        if role == "assistant":
            # Re-add an explicit content=None when the message is tool-calls-only
            # (the None was stripped above) so the provider gets the spec-correct
            # `content: null`, not an omitted key.
            if "content" not in item and item.get("tool_calls"):
                item["content"] = None
            if "content" in item or item.get("tool_calls"):
                cleaned.append(item)
        elif role == "tool":
            if "content" in item and "tool_call_id" in item:
                cleaned.append(item)
        elif "content" in item:
            cleaned.append(item)

    # Repair tool-call adjacency before sending to any OpenAI-compatible
    # provider. Trimming/compaction/retries can leave `role:"tool"` messages
    # without their immediately-preceding assistant `tool_calls` parent, which
    # DeepSeek rejects with:
    # "Messages with role 'tool' must be a response to a preceding message with
    # 'tool_calls'". Also strip unanswered assistant tool_calls; some providers
    # reject those as incomplete conversations.
    repaired: List[Dict] = []
    i = 0
    while i < len(cleaned):
        msg = cleaned[i]
        role = msg.get("role")

        if role == "tool":
            # Orphan tool result. There is no valid assistant tool_calls parent
            # immediately before this batch, so it cannot be sent.
            logger.debug("Dropping orphan tool message before provider request")
            i += 1
            continue

        tool_calls = msg.get("tool_calls") if role == "assistant" else None
        if not tool_calls:
            repaired.append(msg)
            i += 1
            continue

        call_ids = [
            str(tc.get("id"))
            for tc in tool_calls
            if isinstance(tc, dict) and tc.get("id")
        ]
        expected = set(call_ids)
        answered_ids = []
        tool_batch = []
        j = i + 1
        while j < len(cleaned) and cleaned[j].get("role") == "tool":
            tid = str(cleaned[j].get("tool_call_id") or "")
            if tid in expected and tid not in answered_ids:
                answered_ids.append(tid)
                tool_batch.append(cleaned[j])
            else:
                logger.debug("Dropping unmatched/duplicate tool message before provider request")
            j += 1

        if not tool_batch:
            plain = {k: v for k, v in msg.items() if k != "tool_calls"}
            if (plain.get("content") or "").strip():
                repaired.append(plain)
            else:
                logger.debug("Dropping unanswered assistant tool_calls before provider request")
            i = j
            continue

        answered = set(answered_ids)
        pruned_calls = [
            tc for tc in tool_calls
            if isinstance(tc, dict) and str(tc.get("id")) in answered
        ]
        fixed = dict(msg)
        fixed["tool_calls"] = pruned_calls
        if "content" not in fixed:
            fixed["content"] = None
        repaired.append(fixed)
        repaired.extend(tool_batch)
        if len(pruned_calls) != len(tool_calls):
            logger.debug("Pruned unanswered assistant tool_calls before provider request")
        i = j

    # Merge consecutive user messages to satisfy strict role alternation
    # requirements after invalid tool-call fragments have been removed.
    merged: List[Dict] = []
    for item in repaired:
        if not merged:
            merged.append(item)
            continue

        last = merged[-1]
        if last.get("role") == "user" and item.get("role") == "user":
            last_copy = dict(last)
            lc = last_copy.get("content")
            ic = item.get("content")
            if isinstance(lc, list) or isinstance(ic, list):
                # Preserve multimodal content blocks (e.g. an image part) by
                # concatenating the block lists. str()-ing a list turned an
                # image message into its Python repr and dropped the image.
                merged_blocks = _as_content_blocks(lc) + _as_content_blocks(ic)
                if merged_blocks:
                    last_copy["content"] = merged_blocks
                else:
                    last_copy.pop("content", None)
            else:
                last_str = str(lc) if lc is not None else ""
                item_str = str(ic) if ic is not None else ""
                new_content = "\n\n".join(part for part in (last_str, item_str) if part)
                if new_content:
                    last_copy["content"] = new_content
                else:
                    last_copy.pop("content", None)
            merged[-1] = last_copy
        else:
            merged.append(item)

    return merged
