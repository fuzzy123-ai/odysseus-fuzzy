"""Email MCP reply orchestration helpers."""

from __future__ import annotations

import email
import email.utils


def reply_to_email(
    uid,
    body,
    *,
    folder="INBOX",
    reply_all=False,
    account=None,
    imap_connect_func,
    quote_folder_func,
    bytes_func,
    decode_header_func,
    send_email_func,
) -> dict:
    """Reply to an existing email by UID."""
    conn = None
    try:
        conn = imap_connect_func(account)
        conn.select(quote_folder_func(folder), readonly=True)
        status, msg_data = conn.uid("FETCH", bytes_func(uid), "(BODY.PEEK[])")
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    orig = email.message_from_bytes(raw)

    orig_subject = decode_header_func(orig.get("Subject", ""))
    reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    orig_message_id = orig.get("Message-ID", "")
    orig_references = orig.get("References", "")
    new_references = (orig_references + " " + orig_message_id).strip() if orig_references else orig_message_id

    sender = decode_header_func(orig.get("From", ""))
    _, sender_addr = email.utils.parseaddr(sender)
    to_addrs = sender_addr

    cc = None
    if reply_all:
        cc_addrs = []
        for header_name in ("To", "Cc"):
            for _, addr in email.utils.getaddresses([orig.get(header_name, "")]):
                if addr and addr != sender_addr:
                    cc_addrs.append(addr)
        if cc_addrs:
            cc = ", ".join(cc_addrs)

    return send_email_func(
        to=to_addrs,
        subject=reply_subject,
        body=body,
        in_reply_to=orig_message_id,
        references=new_references,
        cc=cc,
        account=account,
    )


def draft_reply_to_email(
    uid,
    body,
    *,
    folder="INBOX",
    reply_all=False,
    account=None,
    title=None,
    imap_connect_func,
    quote_folder_func,
    bytes_func,
    decode_header_func,
    load_config_func,
    create_draft_document_func,
) -> dict:
    """Create an Odysseus reply draft document for an existing email."""
    conn = imap_connect_func(account)
    conn.select(quote_folder_func(folder), readonly=True)
    status, msg_data = conn.uid("FETCH", bytes_func(uid), "(BODY.PEEK[])")
    conn.logout()
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    orig = email.message_from_bytes(raw)

    orig_subject = decode_header_func(orig.get("Subject", ""))
    reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    orig_message_id = orig.get("Message-ID", "")
    orig_references = orig.get("References", "")
    new_references = (orig_references + " " + orig_message_id).strip() if orig_references else orig_message_id

    sender = decode_header_func(orig.get("From", ""))
    _, sender_addr = email.utils.parseaddr(sender)
    to_addrs = sender_addr

    cc = None
    if reply_all:
        cc_addrs = []
        cfg = load_config_func(account)
        own_addrs = {
            (cfg.get("imap_user") or "").strip().lower(),
            (cfg.get("from_address") or "").strip().lower(),
        }
        for header_name in ("To", "Cc"):
            for _, addr in email.utils.getaddresses([orig.get(header_name, "")]):
                addr_l = (addr or "").strip().lower()
                if addr and addr != sender_addr and addr_l not in own_addrs:
                    cc_addrs.append(addr)
        if cc_addrs:
            cc = ", ".join(dict.fromkeys(cc_addrs))

    return create_draft_document_func(
        to=to_addrs,
        subject=reply_subject,
        body=body,
        title=title or reply_subject,
        cc=cc,
        in_reply_to=orig_message_id,
        references=new_references,
        source_uid=uid,
        source_folder=folder,
        account=account,
        source_message_id=orig_message_id,
    )


async def ai_draft_reply_to_email(
    uid,
    *,
    folder="INBOX",
    reply_all=False,
    account=None,
    title=None,
    read_email_func,
    draft_reply_func,
) -> dict:
    """Generate an AI reply body, then create a compose document."""
    read_result = read_email_func(uid=uid, folder=folder, account=account)
    if "error" in read_result:
        return read_result

    to_addr = read_result.get("from_address") or email.utils.parseaddr(read_result.get("from") or "")[1]
    subject = read_result.get("subject") or ""
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    original_body = read_result.get("body") or ""

    if not original_body.strip():
        return {"error": "No email body available for AI reply"}

    try:
        from routes.email_helpers import (
            _EMAIL_REPLY_SYS_PROMPT_BASE,
            _apply_email_style_mechanics,
            _extract_reply,
            _load_settings,
        )
        from src.endpoint_resolver import (
            resolve_endpoint,
            resolve_utility_fallback_candidates,
            resolve_chat_fallback_candidates,
        )
        from src.llm_core import llm_call_async_with_fallback
    except Exception as exc:
        return {"error": f"AI reply helpers unavailable: {exc}"}

    settings = _load_settings()
    style = settings.get("email_writing_style", "")
    system_prompt = _EMAIL_REPLY_SYS_PROMPT_BASE
    if style:
        system_prompt += f"\n\nWRITING STYLE TO MATCH:\n{style}"

    user_msg = (
        f"Recipient: {to_addr}\nSubject: {reply_subject}\n\n"
        f"Original email and any current draft:\n{original_body[:6000]}\n\n"
        "Draft a reply. Return only the reply body text."
    )

    candidates = []
    seen = set()

    def _add(url, model, headers):
        key = (url or "", model or "")
        if not url or not model or key in seen:
            return
        seen.add(key)
        candidates.append((url, model, headers))

    try:
        _add(*resolve_endpoint("utility", owner=None))
    except Exception:
        pass
    try:
        _add(*resolve_endpoint("default", owner=None))
    except Exception:
        pass
    try:
        utility_fallbacks = resolve_utility_fallback_candidates(owner=None) or []
    except TypeError:
        utility_fallbacks = resolve_utility_fallback_candidates() or []
    for cand in utility_fallbacks:
        _add(*cand)
    try:
        chat_fallbacks = resolve_chat_fallback_candidates(owner=None) or []
    except TypeError:
        chat_fallbacks = resolve_chat_fallback_candidates() or []
    for cand in chat_fallbacks:
        _add(*cand)

    if not candidates:
        return {"error": "No LLM endpoint configured for AI reply"}

    try:
        raw_reply = await llm_call_async_with_fallback(
            candidates,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=1024,
            timeout=60,
        )
    except Exception as exc:
        return {"error": f"AI reply generation failed: {exc}"}

    reply = _apply_email_style_mechanics(_extract_reply(raw_reply or ""))
    if not reply:
        return {"error": "AI reply generation returned an empty response"}

    return draft_reply_func(
        uid=uid,
        body=reply,
        folder=folder,
        reply_all=reply_all,
        account=account,
        title=title or reply_subject,
    )
