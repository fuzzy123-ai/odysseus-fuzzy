"""Telegram polling support helpers.

This module handles local agent-turn invocation and public result shaping only.
It must not call Telegram, mutate settings, or persist raw chat identifiers.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable


def _run_agent_turn(
    handler: Callable[[dict[str, Any]], Any] | None,
    bridge: dict[str, Any],
) -> dict[str, Any] | None:
    if not callable(handler) or not bridge.get("ready_for_agent"):
        return None
    try:
        result = handler(dict(bridge))
    except Exception as exc:
        return {
            "status": "failed",
            "reply_text": "",
            "reply_text_present": False,
            "error": str(exc)[:240],
        }
    if isinstance(result, dict):
        reply_text = str(result.get("reply_text") or result.get("text") or "")
        status = str(result.get("status") or "accepted")
    else:
        reply_text = str(result or "")
        status = "accepted"
    return {
        "status": status,
        "reply_text": reply_text,
        "reply_text_present": bool(reply_text.strip()),
    }


async def _run_agent_turn_async(
    handler: Callable[[dict[str, Any]], Any] | None,
    bridge: dict[str, Any],
) -> dict[str, Any] | None:
    if not callable(handler) or not bridge.get("ready_for_agent"):
        return None
    try:
        result = await asyncio.to_thread(handler, dict(bridge))
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        return {
            "status": "failed",
            "reply_text": "",
            "reply_text_present": False,
            "error": str(exc)[:240],
        }
    if isinstance(result, dict):
        reply_text = str(result.get("reply_text") or result.get("text") or "")
        status = str(result.get("status") or "accepted")
    else:
        reply_text = str(result or "")
        status = "accepted"
    return {
        "status": status,
        "reply_text": reply_text,
        "reply_text_present": bool(reply_text.strip()),
    }


def _public_agent_turn_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    public = {key: value for key, value in result.items() if key != "reply_text"}
    public["reply_text_value_visible"] = False
    return public


def _public_reply_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    output = result.get("output")
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return result


def _reply_result_telegram_message_id(result: dict[str, Any] | None) -> int | None:
    public = _public_reply_result(result)
    if not isinstance(public, dict):
        return None
    sent = public.get("sent")
    if not isinstance(sent, dict):
        sent = public
    candidate = sent.get("telegram_message_id")
    if candidate in ("", None):
        ids = sent.get("telegram_message_ids")
        if isinstance(ids, list) and ids:
            candidate = ids[0]
    try:
        value = int(candidate)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _agent_failure_reply(agent_turn: dict[str, Any] | None) -> str:
    if not agent_turn or str(agent_turn.get("status") or "").lower() != "failed":
        return ""
    return (
        "Ich habe deine Nachricht erhalten und arbeite, aber das Sprachmodell "
        "konnte gerade nicht antworten. Bitte prüfe den Modell-Zugang in Odysseus."
    )
