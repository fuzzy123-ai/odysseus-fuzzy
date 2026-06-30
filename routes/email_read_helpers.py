"""Read-email cache and warm-read helpers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable, MutableSet
from pathlib import Path
from typing import Any


def cached_thread_turns_from_json(turns_json: str | None) -> list[dict[str, Any]] | None:
    if not turns_json:
        return None
    try:
        from src.email_thread_parser import THREAD_PARSER_VERSION

        parsed = json.loads(turns_json)
        if (
            isinstance(parsed, dict)
            and parsed.get("v") == THREAD_PARSER_VERSION
            and isinstance(parsed.get("turns"), list)
        ):
            return parsed["turns"]
    except Exception:
        return None
    return None


def load_read_cached_extras(
    db_path: str | Path,
    owner: str,
    message_id: str,
    sender_addr: str,
    body_html: str,
    body: str,
    *,
    email_cache_owner_clause: Callable[[str], tuple[str, list[str]]],
    apply_email_style_mechanics: Callable[[str], str],
    extract_reply: Callable[[str], str],
    thread_parser: Callable[[str, str], Any] | None = None,
    logger: Any = None,
) -> dict[str, Any]:
    cached_summary = None
    cached_ai_reply = None
    cached_boundaries = None
    cached_turns = None
    cached_sender_sig = None

    message_id = (message_id or "").strip()
    try:
        conn = sqlite3.connect(db_path)
        try:
            owner_clause, owner_params = email_cache_owner_clause(owner)
            row = conn.execute(
                f"SELECT summary FROM email_summaries WHERE message_id = ? AND {owner_clause}",
                (message_id, *owner_params),
            ).fetchone()
            if row:
                cached_summary = row[0]

            row = conn.execute(
                f"SELECT reply FROM email_ai_replies WHERE message_id = ? AND {owner_clause}",
                (message_id, *owner_params),
            ).fetchone()
            if row:
                cached_ai_reply = apply_email_style_mechanics(extract_reply(row[0] or ""))

            row = conn.execute(
                "SELECT sig_start, quote_start, turns_json FROM email_boundaries WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row:
                cached_boundaries = {"sig_start": row[0], "quote_start": row[1]}
                cached_turns = cached_thread_turns_from_json(row[2])

            sender_addr = (sender_addr or "").lower().strip()
            if sender_addr:
                try:
                    row = conn.execute(
                        f"SELECT signature_text FROM sender_signatures "
                        f"WHERE from_address = ? AND {owner_clause}",
                        (sender_addr, *owner_params),
                    ).fetchone()
                    if row and row[0]:
                        cached_sender_sig = row[0]
                except Exception:
                    pass
        finally:
            conn.close()
    except Exception:
        pass

    if cached_turns is None:
        try:
            parser = thread_parser
            if parser is None:
                from src.email_thread_parser import parse_thread

                parser = parse_thread
            cached_turns = parser(body_html, body)
        except Exception as exc:
            if logger is not None:
                try:
                    logger.debug(f"thread parse on read failed: {exc}")
                except Exception:
                    pass
            cached_turns = None

    return {
        "cached_summary": cached_summary,
        "cached_ai_reply": cached_ai_reply,
        "boundaries": cached_boundaries,
        "thread_turns": cached_turns,
        "sender_signature": cached_sender_sig,
    }


def select_recent_warm_reads(
    emails: list[dict[str, Any]],
    *,
    folder: str,
    account_id: str | None,
    owner: str,
    now: float,
    recent_seconds: int,
    max_bytes: int,
    read_limit: int,
    read_cache_key: Callable[..., str],
    read_cache_get: Callable[[str], Any],
    warming_reads: MutableSet[str],
) -> list[tuple[str, str]]:
    if not emails or folder == "__scheduled__":
        return []

    selected: list[tuple[str, str]] = []
    for em in emails:
        uid = str((em or {}).get("uid") or "").strip()
        if not uid:
            continue
        try:
            epoch = float((em or {}).get("date_epoch") or 0)
        except Exception:
            epoch = 0
        if epoch and now - epoch > recent_seconds:
            continue
        try:
            size = int((em or {}).get("size") or 0)
        except Exception:
            size = 0
        if size > max_bytes:
            continue
        ck = read_cache_key(account_id, folder, uid, owner=owner)
        if read_cache_get(ck) is not None or ck in warming_reads:
            continue
        warming_reads.add(ck)
        selected.append((uid, ck))
        if len(selected) >= read_limit:
            break
    return selected


def schedule_recent_email_warm(
    emails: list[dict[str, Any]],
    *,
    folder: str,
    account_id: str | None,
    owner: str,
    recent_seconds: int,
    max_bytes: int,
    read_limit: int,
    read_cache_key: Callable[..., str],
    read_cache_get: Callable[[str], Any],
    read_cache_put: Callable[[str, Any], Any],
    read_email_sync: Callable[[str, str, str | None, str, bool], dict[str, Any]],
    warming_reads: MutableSet[str],
    now: Callable[[], float] = time.time,
    create_task: Callable[[Any], Any] = asyncio.create_task,
    to_thread: Callable[..., Any] = asyncio.to_thread,
    sleep: Callable[[float], Any] = asyncio.sleep,
    logger: Any = None,
) -> bool:
    selected = select_recent_warm_reads(
        emails,
        folder=folder,
        account_id=account_id,
        owner=owner,
        now=now(),
        recent_seconds=recent_seconds,
        max_bytes=max_bytes,
        read_limit=read_limit,
        read_cache_key=read_cache_key,
        read_cache_get=read_cache_get,
        warming_reads=warming_reads,
    )
    if not selected:
        return False

    async def _warm() -> None:
        for uid, ck in selected:
            if read_cache_get(ck) is not None:
                warming_reads.discard(ck)
                continue
            try:
                result = await to_thread(read_email_sync, uid, folder, account_id, owner, False)
                if result and not result.get("error"):
                    read_cache_put(ck, result)
            except Exception as exc:
                if logger is not None:
                    try:
                        logger.debug(f"email read warm skipped uid={uid}: {exc}")
                    except Exception:
                        pass
            finally:
                warming_reads.discard(ck)
                await sleep(0.05)

    coro = _warm()
    try:
        create_task(coro)
        return True
    except RuntimeError:
        coro.close()
        return False
