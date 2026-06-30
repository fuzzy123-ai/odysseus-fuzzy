"""Route-compatible helpers for email AI features.

The FastAPI route module owns HTTP dependencies and IMAP account checks; this
module owns prompt construction, endpoint fallback, output shaping and
owner-scoped AI cache persistence.
"""

from __future__ import annotations

import asyncio
import email as email_mod
import email.utils
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Callable

from src.llm_core import llm_call_async

logger = logging.getLogger(__name__)


def decode_plain_email_body(msg: email_mod.message.Message) -> str:
    """Return the first useful text/plain body from a message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
                break
        return body

    payload = msg.get_payload(decode=True)
    if payload:
        charset = msg.get_content_charset() or "utf-8"
        body = payload.decode(charset, errors="replace")
    return body


def gather_sent_style_samples(
    *,
    sample_count: int,
    owner: str,
    imap_factory: Callable[..., Any],
    quote_folder: Callable[[str], str],
    detect_sent_folder: Callable[[Any], str],
) -> tuple[list[str], str | None]:
    """Fetch recent sent-message plain-text samples for style extraction."""
    try:
        with imap_factory(owner=owner) as imap:
            imap.select(quote_folder(detect_sent_folder(imap)), readonly=True)
            status, data = imap.search(None, "ALL")
            if status != "OK" or not data[0]:
                return [], "No sent emails found"
            uid_list = data[0].split()[-sample_count:]

            out: list[str] = []
            for uid in uid_list:
                try:
                    status, msg_data = imap.fetch(uid, "(RFC822)")
                    if status != "OK":
                        continue
                    raw = msg_data[0][1]
                    body = decode_plain_email_body(email_mod.message_from_bytes(raw))
                    if body.strip() and len(body) > 20:
                        out.append(body[:1000])
                except Exception:
                    continue
            return out, None
    except Exception as exc:
        return [], str(exc)


async def extract_writing_style_response(
    req: Any,
    *,
    owner: str,
    imap_factory: Callable[..., Any],
    quote_folder: Callable[[str], str],
    detect_sent_folder: Callable[[Any], str],
    load_settings: Callable[[], dict],
    save_settings: Callable[[dict], None],
    strip_think: Callable[[str], str],
) -> dict[str, Any]:
    """Extract and persist writing style from recent sent emails."""
    try:
        samples, err = await asyncio.to_thread(
            gather_sent_style_samples,
            sample_count=req.sample_count,
            owner=owner,
            imap_factory=imap_factory,
            quote_folder=quote_folder,
            detect_sent_folder=detect_sent_folder,
        )
        if err and not samples:
            return {"success": False, "error": err}
        if len(samples) < 3:
            return {"success": False, "error": f"Only found {len(samples)} usable sent emails, need at least 3"}

        from src.endpoint_resolver import resolve_endpoint

        url, model, headers = resolve_endpoint("utility", owner=owner)
        if not url or not model:
            url, model, headers = resolve_endpoint("default", owner=owner)
        if not url or not model:
            return {
                "success": False,
                "error": "No LLM endpoint configured - set a Utility or Default Chat model in Settings -> AI Defaults.",
            }

        sample_text = "\n\n---EMAIL---\n\n".join(samples[:15])
        messages = [
            {
                "role": "system",
                "content": (
                    "You are analyzing a user's email writing style. Based on the sample emails below, "
                    "describe their writing style in 3-5 concise sentences. Cover: tone (formal/informal), "
                    "typical greeting and sign-off patterns, sentence structure (short/long), "
                    "any distinctive phrases or habits, and overall communication approach. "
                    "Write this as instructions for an AI to mimic this style. "
                    "Start with 'Write emails in this style:'"
                ),
            },
            {
                "role": "user",
                "content": f"Here are {len(samples)} recently sent emails:\n\n{sample_text}",
            },
        ]

        style = await llm_call_async(
            url,
            model,
            messages,
            headers=headers,
            max_tokens=2048,
            owner=owner,
            surface="email",
            prompt_type="email_writing_style_extract",
        )
        style = strip_think(style or "")
        if not style:
            return {"success": False, "error": "LLM failed to generate style description"}

        settings = load_settings()
        settings["email_writing_style"] = style
        save_settings(settings)

        logger.info("Writing style extracted and saved")
        return {"success": True, "style": style}
    except Exception as exc:
        logger.error(f"Failed to extract writing style: {exc}")
        return {"success": False, "error": "Mail operation failed"}


def summary_payload(
    *,
    model: str,
    sender: str,
    subject: str,
    body_for_llm: str,
    token_key: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an email summarizer. Format: 1-3 short bullet points (use '- '). "
                    "Cover: main point, action items, deadlines. If the email has attachments "
                    "(marked '--- ATTACHMENTS ---'), USE THEIR CONTENTS - pull invoice totals, "
                    "deadlines, key clauses, concrete numbers/dates from PDFs/docs into the bullets. "
                    "Be terse.\n\nOUTPUT FORMAT: Put ONLY the bullet points between these exact markers, "
                    "each on its own line:\n<<<SUMMARY>>>\n- ...\n<<<END>>>\nAny reasoning must come "
                    "BEFORE <<<SUMMARY>>> (ideally inside <think>...</think>). Only the text between "
                    "the markers is kept."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"From: {sender}\nSubject: {subject}\n\n{body_for_llm[:12000]}"
                    "\n\n---\n\nSummarize the email. Output the bullets between <<<SUMMARY>>> and <<<END>>>."
                ),
            },
        ],
        token_key: 8192,
        "temperature": 0.3,
        "stream": False,
    }


def extract_summary_content(message: dict[str, Any], *, extract_reply: Callable[[str], str]) -> str:
    content = (message.get("content") or "").strip()
    content = extract_reply(content)
    if content:
        return content

    reasoning = (message.get("reasoning_content") or "").strip()
    bullet_lines = []
    for line in reasoning.split("\n"):
        stripped = line.strip()
        if re.match(r"^[-*\u2022]\s+|^\d+[.)]\s+", stripped):
            bullet_lines.append(stripped)
    if bullet_lines:
        return "\n".join(bullet_lines)

    paragraphs = [p.strip() for p in reasoning.split("\n\n") if p.strip()]
    return paragraphs[-1] if paragraphs else reasoning[:500]


def cache_email_summary(
    *,
    db_path: str | os.PathLike[str],
    message_id: str,
    owner: str,
    uid: str,
    folder: str,
    subject: str,
    sender: str,
    summary: str,
    model: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO email_summaries
            (message_id, owner, uid, folder, subject, sender, summary, model_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, owner, uid, folder, subject, sender, summary, model, datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


async def summarize_email_response(
    data: dict[str, Any],
    *,
    owner: str,
    db_path: str | os.PathLike[str],
    assert_owns_account: Callable[[str, str], None],
    imap_factory: Callable[..., Any],
    quote_folder: Callable[[str], str],
    imap_uid_fetch: Callable[[Any, str, str], Any],
    extract_attachment_text: Callable[..., str],
    extract_reply: Callable[[str], str],
) -> dict[str, Any]:
    """Generate and optionally cache an on-demand email summary."""
    try:
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import _restricts_temperature, _uses_max_completion_tokens
        import requests as request_client

        body = data.get("body", "")
        subject = data.get("subject", "")
        sender = data.get("from", "")
        uid = data.get("uid", "")
        folder = data.get("folder", "INBOX") or "INBOX"
        account_id = data.get("account_id")
        if account_id:
            assert_owns_account(account_id, owner)
        if not body:
            return {"success": False, "error": "No body provided"}

        attachment_text = ""
        if uid:
            try:
                def _fetch_attachments() -> str:
                    with imap_factory(account_id, owner=owner) as conn:
                        conn.select(quote_folder(folder), readonly=True)
                        status, msg_data = imap_uid_fetch(conn, str(uid), "(BODY.PEEK[])")
                        if status != "OK" or not msg_data or not msg_data[0]:
                            return ""
                        raw = msg_data[0][1]
                        msg_obj = email_mod.message_from_bytes(raw)
                        return extract_attachment_text(msg_obj, max_chars=6000)

                attachment_text = await asyncio.to_thread(_fetch_attachments)
            except Exception as exc:
                logger.debug(f"on-demand summarize attachment fetch failed for uid={uid}: {exc}")

        body_for_llm = body
        if attachment_text:
            body_for_llm = body + "\n\n--- ATTACHMENTS ---\n\n" + attachment_text

        url, model, headers = resolve_endpoint("utility", owner=owner)
        if not url:
            url, model, headers = resolve_endpoint("default", owner=owner)
        if not url or not model:
            return {"success": False, "error": "No LLM endpoint configured"}

        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        token_key = "max_completion_tokens" if _uses_max_completion_tokens(model) else "max_tokens"
        payload = summary_payload(
            model=model,
            sender=sender,
            subject=subject,
            body_for_llm=body_for_llm,
            token_key=token_key,
        )
        if _restricts_temperature(model):
            payload.pop("temperature", None)

        resp = await asyncio.to_thread(
            request_client.post, url, json=payload, headers=request_headers, timeout=180
        )
        if not resp.ok:
            return {"success": False, "error": f"LLM HTTP {resp.status_code}"}
        response_data = resp.json()
        msg = (response_data.get("choices") or [{}])[0].get("message", {})
        content = extract_summary_content(msg, extract_reply=extract_reply)

        if not content:
            return {"success": False, "error": "Empty response from model"}

        message_id = data.get("message_id", "")
        if message_id:
            try:
                cache_email_summary(
                    db_path=db_path,
                    message_id=message_id,
                    owner=owner,
                    uid=data.get("uid", ""),
                    folder=data.get("folder", ""),
                    subject=subject,
                    sender=sender,
                    summary=content,
                    model=model,
                )
            except Exception as exc:
                logger.warning(f"Failed to cache summary: {exc}")

        return {"success": True, "summary": content, "model_used": model}
    except Exception as exc:
        logger.error(f"Failed to summarize: {exc}")
        return {"success": False, "error": "Mail operation failed"}


def cached_ai_reply(
    *,
    db_path: str | os.PathLike[str],
    message_id: str,
    owner: str,
    email_cache_owner_clause: Callable[[str], tuple[str, list[str]]],
    extract_reply: Callable[[str], str],
    apply_email_style_mechanics: Callable[[str], str],
) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    try:
        owner_clause, owner_params = email_cache_owner_clause(owner)
        row = conn.execute(
            f"SELECT reply, model_used FROM email_ai_replies WHERE message_id = ? AND {owner_clause}",
            (message_id, *owner_params),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    reply = apply_email_style_mechanics(extract_reply(row[0] or ""))
    if not reply:
        return None
    return {"success": True, "reply": reply, "model_used": row[1] or "cached", "cached": True}


def session_endpoint(session_id: str, requested_model: str, owner: str) -> tuple[str | None, str, dict | None]:
    url = None
    model = requested_model
    headers = None
    if not session_id:
        return url, model, headers

    from core.database import Session as ChatSession
    from core.database import SessionLocal

    db = SessionLocal()
    try:
        sess = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.owner == owner).first()
        if sess and sess.endpoint_url:
            url = sess.endpoint_url
            raw_headers = sess.headers
            for _ in range(3):
                if isinstance(raw_headers, str):
                    try:
                        raw_headers = json.loads(raw_headers)
                    except Exception:
                        raw_headers = None
                        break
                else:
                    break
            headers = raw_headers if isinstance(raw_headers, dict) and raw_headers else None
            if not requested_model:
                model = sess.model
    finally:
        db.close()
    return url, model, headers


def resolve_ai_reply_primary_endpoint(
    *,
    requested_model: str,
    session_id: str,
    owner: str,
) -> tuple[str | None, str | None, dict | None]:
    from src.endpoint_resolver import resolve_endpoint

    url = None
    model = requested_model
    headers = None
    if session_id:
        try:
            url, model, headers = session_endpoint(session_id, requested_model, owner)
        except Exception as exc:
            logger.warning(f"Failed to read session endpoint: {exc}")

    if not url:
        url, fallback_model, headers = resolve_endpoint("utility", owner=owner)
        if not url:
            url, fallback_model, headers = resolve_endpoint("default", owner=owner)
        if not model:
            model = fallback_model

    if url and model:
        try:
            from src.llm_core import list_model_ids

            available = list_model_ids(url, headers=headers)
            if available and model not in available:
                base = os.path.basename((model or "").rstrip("/"))
                match = next((item for item in available if os.path.basename(item.rstrip("/")) == base), None)
                model = match or available[0]
        except Exception as exc:
            logger.warning(f"AI reply model resolve failed: {exc}")
    return url, model, headers


def build_ai_reply_prompt(
    *,
    to: str,
    subject: str,
    original_body: str,
    user_hint: str,
    style: str,
    context_snippets: list[str],
    referenced: str,
    base_prompt: str,
) -> tuple[str, str]:
    system_prompt = base_prompt
    if style:
        system_prompt += f"\n\nWRITING STYLE TO MATCH:\n{style}"
    if context_snippets:
        system_prompt += (
            "\n\nRELEVANT CONTEXT FROM PAST EMAILS AND CONTACTS:\n"
            + "\n\n---\n\n".join(context_snippets[:5])
        )
    if referenced:
        system_prompt += (
            "\n\nREFERENCED MATERIAL - the last few emails from this sender, "
            "plus any text extracted from their attachments. Use this to "
            "answer numbered questions or refer to documents they previously "
            "sent. Do NOT cite this material verbatim unless the sender "
            "directly asked about something in it.\n\n"
            + referenced[:18000]
        )

    user_msg = (
        f"Recipient: {to}\nSubject: {subject}\n\n"
        f"Original email and any current draft:\n{original_body[:6000]}\n\n"
    )
    if user_hint:
        user_msg += (
            "User's instructions for THIS reply (follow these - they override "
            f"defaults like length/tone):\n{user_hint[:2000]}\n\n"
        )
    user_msg += "Draft a reply. Return only the reply body text."
    return system_prompt, user_msg


def ai_reply_candidates(
    *,
    primary_url: str | None,
    primary_model: str | None,
    primary_headers: dict | None,
    owner: str,
) -> list[tuple[str, str, dict | None]]:
    from src.endpoint_resolver import (
        resolve_chat_fallback_candidates,
        resolve_endpoint,
        resolve_utility_fallback_candidates,
    )

    seen: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str, dict | None]] = []

    def add(url: str | None, model: str | None, headers: dict | None):
        key = (url or "", model or "")
        if not url or not model or key in seen:
            return
        seen.add(key)
        candidates.append((url, model, headers))

    add(primary_url, primary_model, primary_headers)
    try:
        url, model, headers = resolve_endpoint("utility", owner=owner)
        add(url, model, headers)
    except Exception:
        pass
    try:
        url, model, headers = resolve_endpoint("default", owner=owner)
        add(url, model, headers)
    except Exception:
        pass
    for candidate in resolve_utility_fallback_candidates(owner=owner) or []:
        add(*candidate)
    for candidate in resolve_chat_fallback_candidates(owner=owner) or []:
        add(*candidate)
    return candidates


def format_attempted_candidates(candidates: list[tuple[str, str, dict | None]]) -> str:
    attempted = []
    for url, model, _headers in candidates:
        host = url.split("/")[2] if "/" in url else url
        attempted.append(f"{model}@{host}")
    return ", ".join(attempted) or "no candidates"


def cache_ai_reply(
    *,
    db_path: str | os.PathLike[str],
    message_id: str,
    owner: str,
    uid: str,
    folder: str,
    reply: str,
    model: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO email_ai_replies
            (message_id, owner, uid, folder, reply, model_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, owner, uid, folder, reply, model, datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


async def ai_reply_response(
    data: dict[str, Any],
    *,
    owner: str,
    db_path: str | os.PathLike[str],
    load_settings: Callable[[], dict],
    email_cache_owner_clause: Callable[[str], tuple[str, list[str]]],
    extract_reply: Callable[[str], str],
    apply_email_style_mechanics: Callable[[str], str],
    pre_retrieve_context: Callable[..., tuple[list[str], list[str]]],
    fetch_sender_thread_context: Callable[..., str],
    base_prompt: str,
) -> dict[str, Any]:
    """Generate an AI-drafted reply and cache it by owner/message."""
    try:
        to = data.get("to", "")
        subject = data.get("subject", "")
        original_body = data.get("original_body", "")
        requested_model = data.get("model", "").strip()
        session_id = data.get("session_id", "").strip()
        message_id = (data.get("message_id") or "").strip()
        source_uid = (data.get("uid") or "").strip()
        source_folder = (data.get("folder") or "INBOX").strip()
        fast_reply = bool(data.get("fast", False))
        user_hint = (data.get("user_hint") or "").strip()

        if not original_body:
            return {"success": False, "error": "No email body provided"}

        if message_id and not user_hint:
            try:
                cached = cached_ai_reply(
                    db_path=db_path,
                    message_id=message_id,
                    owner=owner,
                    email_cache_owner_clause=email_cache_owner_clause,
                    extract_reply=extract_reply,
                    apply_email_style_mechanics=apply_email_style_mechanics,
                )
                if cached:
                    return cached
            except Exception as exc:
                logger.warning(f"AI reply cache lookup failed: {exc}")

        settings = load_settings()
        style = settings.get("email_writing_style", "")
        url, model, headers = resolve_ai_reply_primary_endpoint(
            requested_model=requested_model,
            session_id=session_id,
            owner=owner,
        )
        if not url or not model:
            return {"success": False, "error": "No LLM endpoint configured"}

        logger.info(f"AI reply using model={model} url={url}")

        context_snippets, _terms = ([], [])
        if not fast_reply:
            context_snippets, _terms = pre_retrieve_context(original_body, to, owner=owner)

        referenced = ""
        if not fast_reply:
            try:
                from_addr_for_ctx = email.utils.parseaddr(to or "")[1]
                referenced = fetch_sender_thread_context(
                    sender_addr=from_addr_for_ctx,
                    exclude_uid=source_uid,
                    exclude_folder=source_folder,
                    limit=3,
                    owner=owner,
                )
            except Exception as exc:
                logger.warning(f"sender-thread-context failed: {exc}")

        system_prompt, user_msg = build_ai_reply_prompt(
            to=to,
            subject=subject,
            original_body=original_body,
            user_hint=user_hint,
            style=style,
            context_snippets=context_snippets,
            referenced=referenced,
            base_prompt=base_prompt,
        )

        from src.llm_core import llm_call_async_with_fallback

        candidates = ai_reply_candidates(
            primary_url=url,
            primary_model=model,
            primary_headers=headers,
            owner=owner,
        )
        try:
            reply = await llm_call_async_with_fallback(
                candidates,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.7,
                max_tokens=1024 if fast_reply else 6144,
                timeout=60 if fast_reply else 180,
                owner=owner,
                surface="email",
                correlation_id=str(source_uid or ""),
                prompt_type="email_ai_reply",
            )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            attempted = format_attempted_candidates(candidates)
            return {
                "success": False,
                "error": f"All endpoints failed ({attempted}): {detail}. Check your API keys in Settings -> Services.",
            }

        reply = apply_email_style_mechanics(extract_reply(reply or ""))
        if not reply:
            return {"success": False, "error": "LLM returned empty response"}

        if message_id:
            try:
                cache_ai_reply(
                    db_path=db_path,
                    message_id=message_id,
                    owner=owner,
                    uid=source_uid,
                    folder=source_folder,
                    reply=reply,
                    model=model,
                )
            except Exception as exc:
                logger.warning(f"Failed to cache ai_reply: {exc}")

        return {"success": True, "reply": reply, "model_used": model}
    except Exception as exc:
        logger.error(f"Failed to generate AI reply: {exc}")
        return {"success": False, "error": "Mail operation failed"}
