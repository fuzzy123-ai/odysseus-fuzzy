from __future__ import annotations

from typing import Dict, List


def normalize_chatgpt_subscription_url(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if base.endswith("/responses"):
        return base
    return base + "/responses"


def message_content_as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                if part:
                    parts.append(str(part))
                continue
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
                continue
            if isinstance(part.get("content"), str):
                parts.append(part["content"])
        return "\n".join(parts)
    return "" if content is None else str(content)


def chatgpt_subscription_instructions(messages: List[Dict]) -> str:
    instructions = [
        message_content_as_text(msg.get("content")).strip()
        for msg in messages or []
        if (msg.get("role") or "") == "system"
    ]
    instructions = [part for part in instructions if part]
    if instructions:
        return "\n\n".join(instructions)
    return "You are a helpful AI assistant."


def build_chatgpt_responses_payload(
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
    *,
    stream: bool = False,
    restricts_temperature,
) -> Dict:
    from src.chatgpt_subscription import build_responses_input

    conversation = [msg for msg in (messages or []) if (msg.get("role") or "") != "system"]
    payload: Dict = {
        "model": model,
        "instructions": chatgpt_subscription_instructions(messages),
        "input": build_responses_input(conversation),
        "stream": stream,
        "store": False,
    }
    if not restricts_temperature(model):
        payload["temperature"] = temperature
    # ChatGPT Subscription Codex API does not support max_output_tokens.
    # Passing it returns HTTP 400 "Unsupported parameter: max_output_tokens".
    return payload
