"""
email_server.py

MCP server exposing email tools: list unread/unresponded emails,
read email content, and draft replies as email documents.
Connects to local Dovecot IMAP and reads from the AI summary cache.
"""

import asyncio
import imaplib
import smtplib
import email
import email.utils
import json
import sys
import os
import os.path
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcp_servers import email_account_config as _email_account_config
from mcp_servers.email_account_config import (
    _ACCOUNT_CACHE,
    _CURRENT_OWNER,
    _MCP_OWNER_ARG,
    _current_owner,
    _default_document_owner,
    _filter_accounts_for_owner,
    _list_accounts_raw,
    _load_config as _load_config_from_account_config,
    _mcp_owner_required,
    _read_accounts_from_db,
    _resolve_account_from_rows,
    _writing_style_guidance,
)
from mcp_servers.email_attachment_utils import (
    _extract_attachment_to_disk,
    _list_attachments_from_msg,
)
from mcp_servers.email_agent_draft_utils import (
    read_agent_email_confirm_setting,
    stash_agent_draft,
)
from mcp_servers.email_cache_utils import (
    _result_sort_time,
    load_cached_summaries,
)
from mcp_servers.email_draft_document_utils import (
    build_email_document_content,
    create_email_draft_document,
    merge_email_reply_body,
)
from mcp_servers.email_imap_utils import (
    _b,
    _clean_header_value,
    _confirmed,
    _email_delete_confirmation_required,
    _q,
    _uid_fetch_rows,
)
from mcp_servers.email_folder_utils import (
    _detect_sent_folder,
    _folder_name_from_list_line,
    _folder_role_from_name,
    _list_folder_lines,
    _resolve_folder,
)
from mcp_servers.email_message_utils import (
    _decode_header,
    _extract_text,
)
from mcp_servers.email_send_utils import send_email as send_email_via_helper
from mcp_servers.email_smtp_connection_utils import (
    connect_smtp,
    resolve_send_config,
    smtp_ready as _smtp_ready,
)
from mcp_servers.email_tool_formatting import (
    apply_active_account_context,
    format_ai_draft_reply_response,
    format_bulk_result,
    format_download_attachment_response,
    format_draft_email_response,
    format_draft_reply_response,
    format_email_accounts_response,
    format_list_emails_response,
    format_read_email_response,
    format_search_emails_response,
    format_sent_email_response,
    merged_account_context_header,
    selected_account_context_header,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("email")
EMAIL_SOCKET_TIMEOUT = float(os.environ.get("EMAIL_SOCKET_TIMEOUT", "20"))
from src.constants import DATA_DIR as _DATA_DIR, APP_DB, EMAIL_CACHE_DB, SETTINGS_FILE as _SETTINGS_FILE, MAIL_ATTACHMENTS_DIR
DATA_DIR = Path(_DATA_DIR)


# ── Config ──
# Multi-account aware. Accounts live in data/app.db :: email_accounts.
# Callers can pass `account=` (match by name, user, or id) to pick a specific
# inbox; None resolves to the default row. Falls back to env vars / settings.json
# flat keys when no DB row matches (legacy single-account behaviour).

def _sync_email_account_config_paths() -> None:
    _email_account_config.APP_DB = APP_DB
    _email_account_config._SETTINGS_FILE = _SETTINGS_FILE
    _email_account_config.DATA_DIR = DATA_DIR


def _read_accounts_from_db() -> list:
    _sync_email_account_config_paths()
    return _email_account_config._read_accounts_from_db()


def _list_accounts_raw() -> list:
    _sync_email_account_config_paths()
    return _email_account_config._list_accounts_raw()


def _filter_accounts_for_owner(rows: list[dict]) -> list[dict]:
    return _email_account_config._filter_accounts_for_owner(rows)


def _mcp_owner_required(rows: list[dict] | None = None) -> bool:
    _sync_email_account_config_paths()
    return _email_account_config._mcp_owner_required(rows)


def _writing_style_guidance() -> str:
    _sync_email_account_config_paths()
    return _email_account_config._writing_style_guidance()


def _default_document_owner() -> str | None:
    _sync_email_account_config_paths()
    return _email_account_config._default_document_owner()


def _load_config(account: str | None = None) -> dict:
    _sync_email_account_config_paths()
    return _load_config_from_account_config(account)

# ── IMAP helpers ──


def _imap_connect(account: str | None = None):
    """Connect to IMAP server, returns logged-in connection. account selects
    the mailbox (None = default)."""
    cfg = _load_config(account)
    if cfg["imap_ssl"]:
        conn = imaplib.IMAP4_SSL(
            cfg["imap_host"],
            cfg["imap_port"],
            timeout=EMAIL_SOCKET_TIMEOUT,
        )
    else:
        conn = imaplib.IMAP4(
            cfg["imap_host"],
            cfg["imap_port"],
            timeout=EMAIL_SOCKET_TIMEOUT,
        )
        if cfg["imap_starttls"]:
            try:
                conn.starttls()
            except Exception:
                # Don't leak the open plain socket on a rejected STARTTLS. (#3174)
                try:
                    conn.shutdown()
                except Exception:
                    pass
                raise
    if getattr(conn, "sock", None):
        conn.sock.settimeout(EMAIL_SOCKET_TIMEOUT)
    try:
        conn.login(cfg["imap_user"], cfg["imap_password"])
    except Exception:
        # A failed login otherwise orphans the connected socket; close it
        # before propagating (shutdown() is the pre-auth low-level close). (#3174)
        try:
            conn.shutdown()
        except Exception:
            pass
        raise
    return conn


def _get_cached_summaries():
    return load_cached_summaries(_load_config)


# ── Tool implementations ──


def _list_emails(folder="INBOX", max_results=20, unresponded_only=False,
                 unread_only=False, account=None):
    """List emails newest-first. By default returns the latest messages,
    including read mail, so it matches normal inbox UI expectations.
    Pass unread_only=True and/or unresponded_only=True for attention scans.
    account selects mailbox (None = default).
    """
    conn = None
    try:
        conn = _imap_connect(account)
        select_status, _ = conn.select(_q(folder), readonly=True)
        if select_status != "OK":
            raise ValueError(f"IMAP folder not found: {folder}")

        if unread_only and unresponded_only:
            status, data = conn.uid("SEARCH", None, "(UNSEEN UNANSWERED)")
        elif unread_only:
            status, data = conn.uid("SEARCH", None, "(UNSEEN)")
        elif unresponded_only:
            # Was missing — unresponded_only=True (without unread_only) fell through
            # to "ALL" and returned answered mail too, despite the documented
            # "emails without replies" behaviour.
            status, data = conn.uid("SEARCH", None, "(UNANSWERED)")
        else:
            # Include read too — IMAP search "ALL" returns the entire folder
            status, data = conn.uid("SEARCH", None, "ALL")

        if status != "OK" or not data[0]:
            return []

        uid_list = list(reversed(data[0].split()))[:max_results]
        cache = _get_cached_summaries()
        results = []

        for uid in uid_list:
            try:
                status, msg_data = conn.uid("FETCH", uid, "(RFC822.HEADER)")
                if status != "OK":
                    continue
                raw_header = msg_data[0][1]
                msg = email.message_from_bytes(raw_header)

                subject = _decode_header(msg.get("Subject", "(no subject)"))
                sender = _decode_header(msg.get("From", "unknown"))
                date_str = msg.get("Date", "")
                message_id = msg.get("Message-ID", "")

                # Parse sender name
                sender_name, sender_addr = email.utils.parseaddr(sender)
                sender_display = sender_name or sender_addr

                # Check cache for summary
                cached = cache.get(subject, {})
                summary = cached.get("summary", "")

                results.append({
                    "uid": uid.decode(),
                    "message_id": message_id,
                    "subject": subject,
                    "from": sender_display,
                    "from_address": sender_addr,
                    "date": date_str,
                    "summary": summary,
                })
            except Exception:
                continue

        return results
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass


def _list_emails_across_accounts(folder="INBOX", max_results=20,
                                 unresponded_only=False, unread_only=False):
    rows = _list_accounts_raw()
    combined = []
    errors = []
    for row in rows:
        account_selector = row.get("id") or row.get("name") or row.get("imap_user")
        account_name = row.get("name") or row.get("imap_user") or row.get("id") or "unknown"
        account_email = row.get("imap_user") or row.get("from_address") or ""
        try:
            account_results = _list_emails(
                folder=folder,
                max_results=max_results,
                unresponded_only=unresponded_only,
                unread_only=unread_only,
                account=account_selector,
            )
            for item in account_results:
                item["_account"] = account_name
                item["_account_email"] = account_email
                item["_account_id"] = row.get("id")
            combined.extend(account_results)
        except Exception as exc:
            errors.append(f"{account_name} ({account_email}): {exc}")
    combined.sort(key=_result_sort_time, reverse=True)
    return combined[:max_results], errors


def _search_emails(query, folders=None, max_results=20, account=None):
    """IMAP-search emails by free-text query. Matches FROM, SUBJECT, and
    body TEXT. Walks multiple folders so older threads outside INBOX
    (Sent/Archive) are still findable. Returns the same shape as
    _list_emails plus an `_folder` tag."""
    if not query or not str(query).strip():
        return []
    q = str(query).replace("\\", "\\\\").replace('"', '\\"')
    # Mail clients commonly use OR FROM/SUBJECT/TEXT to match either field.
    # IMAP SEARCH OR is binary, so we nest it.
    search_cmd = f'(OR OR FROM "{q}" SUBJECT "{q}" TEXT "{q}")'
    if folders is None:
        folders = ["INBOX", "Sent", "Archive"]
    cache = _get_cached_summaries()
    out = []
    conn = _imap_connect(account)
    touched = []
    try:
        for folder in folders:
            try:
                status, _ = conn.select(_q(folder), readonly=True)
                if status != "OK":
                    continue
                status, data = conn.uid("SEARCH", None, search_cmd)
                if status != "OK" or not data or not data[0]:
                    continue
                uid_list = list(reversed(data[0].split()))[:max_results]
                for uid in uid_list:
                    try:
                        status, msg_data = conn.uid("FETCH", uid, "(RFC822.HEADER)")
                        if status != "OK":
                            continue
                        raw_header = msg_data[0][1]
                        msg = email.message_from_bytes(raw_header)
                        subject = _decode_header(msg.get("Subject", "(no subject)"))
                        sender = _decode_header(msg.get("From", "unknown"))
                        date_str = msg.get("Date", "")
                        message_id = msg.get("Message-ID", "")
                        to_str = _decode_header(msg.get("To", ""))
                        cc_str = _decode_header(msg.get("Cc", ""))
                        sender_name, sender_addr = email.utils.parseaddr(sender)
                        sender_display = sender_name or sender_addr
                        cached = cache.get(subject, {})
                        out.append({
                            "uid": uid.decode(),
                            "message_id": message_id,
                            "subject": subject,
                            "from": sender_display,
                            "from_address": sender_addr,
                            "to": to_str,
                            "cc": cc_str,
                            "date": date_str,
                            "_folder": folder,
                            "summary": cached.get("summary", ""),
                        })
                    except Exception:
                        continue
            except Exception:
                continue
    finally:
        try: conn.logout()
        except Exception: pass
    # Cap total across folders.
    return out[: max_results * len(folders)]


def _read_email(uid=None, message_id=None, folder="INBOX", account=None):
    """Read full email content by UID or message-ID. account = mailbox selector."""
    cfg = _load_config(account)
    conn = None
    try:
        conn = _imap_connect(account)
        conn.select(_q(folder), readonly=True)

        if message_id and not uid:
            status, data = conn.uid("SEARCH", None, f'(HEADER Message-ID "{message_id}")')
            if status != "OK" or not data[0]:
                return {"error": f"Email not found with Message-ID: {message_id}"}
            uid = data[0].split()[-1]

        if not uid:
            return {"error": "No UID or Message-ID provided"}

        status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
        if status != "OK":
            return {"error": f"Failed to fetch email UID {uid}"}
        if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
            return {"error": f"Email not found with UID {uid}"}

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = _decode_header(msg.get("Subject", "(no subject)"))
        sender = _decode_header(msg.get("From", "unknown"))
        date_str = msg.get("Date", "")
        message_id_header = msg.get("Message-ID", "")
        body = _extract_text(msg)
        attachments = _list_attachments_from_msg(msg)

        sender_name, sender_addr = email.utils.parseaddr(sender)

        return {
            "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
            "account": cfg.get("account_name") or cfg.get("imap_user") or "default",
            "account_email": cfg.get("imap_user") or cfg.get("from_address") or "",
            "account_id": cfg.get("account_id"),
            "message_id": message_id_header,
            "subject": subject,
            "from": sender_name or sender_addr,
            "from_address": sender_addr,
            "date": date_str,
            "body": body[:8000],
            "attachments": attachments,
        }
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass


def _read_email_across_accounts(uid=None, message_id=None, folder="INBOX"):
    rows = _list_accounts_raw()
    matches = []
    errors = []
    for row in rows:
        account_selector = row.get("id") or row.get("name") or row.get("imap_user")
        account_name = row.get("name") or row.get("imap_user") or row.get("id") or "unknown"
        account_email = row.get("imap_user") or row.get("from_address") or ""
        result = _read_email(
            uid=uid,
            message_id=message_id,
            folder=folder,
            account=account_selector,
        )
        if "error" in result:
            errors.append(f"{account_name} <{account_email}>: {result['error']}")
            continue
        matches.append(result)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        accounts = ", ".join(
            f"{m.get('account')} <{m.get('account_email')}>" for m in matches
        )
        return {
            "error": (
                f"UID {uid or message_id} exists in multiple accounts: {accounts}. "
                "Call read_email again with the account name/email."
            )
        }
    return {"error": f"Email not found in any configured account. Checked: {'; '.join(errors)}"}


def _resolve_send_config(account=None):
    return resolve_send_config(
        account,
        load_config=_load_config,
        list_accounts_raw=_list_accounts_raw,
        smtp_ready_func=_smtp_ready,
    )


def _smtp_connect(account=None, cfg=None):
    return connect_smtp(
        account,
        cfg=cfg,
        load_config=_load_config,
        smtp_ready_func=_smtp_ready,
        smtp_module=smtplib,
        timeout=EMAIL_SOCKET_TIMEOUT,
    )


def _read_agent_email_confirm_setting() -> bool:
    """True if the user wants agent send_email/reply_to_email calls to be
    queued for manual approval instead of SMTPed immediately. Defaults to
    True so a fresh install is safe — agents have been observed inventing
    signatures and sending to real recipients without the user's review."""
    return read_agent_email_confirm_setting()


def _stash_agent_draft(*, to, subject, body, in_reply_to=None, references=None,
                      cc=None, bcc=None, account=None) -> dict:
    """Insert the composed email into scheduled_emails with status
    'agent_draft' and a far-future send_at so the scheduled-send poller
    never picks it up. Returns the pending payload the model surfaces to
    the user (and that the chat UI can render as an approval card)."""
    return stash_agent_draft(
        to=to,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
        cc=cc,
        bcc=bcc,
        account=account,
        current_owner_func=_current_owner,
    )


def _send_email(to, subject, body, in_reply_to=None, references=None, cc=None, bcc=None, account=None):
    """Send an email via SMTP. Returns dict with status.

    When the `agent_email_confirm` setting is on (the default), the email
    is NOT SMTPed — instead it lands in scheduled_emails as an
    `agent_draft` row and the user reviews + approves it from the chat
    UI. This closes the auto-send hole that let earlier models invent
    signatures and ship them to real recipients without confirmation."""
    return send_email_via_helper(
        to=to,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
        cc=cc,
        bcc=bcc,
        account=account,
        read_confirm_setting_func=_read_agent_email_confirm_setting,
        stash_draft_func=_stash_agent_draft,
        resolve_send_config_func=_resolve_send_config,
        smtp_connect_func=_smtp_connect,
        clean_header_value_func=_clean_header_value,
        imap_connect_func=_imap_connect,
        detect_sent_folder_func=_detect_sent_folder,
        quote_folder_func=_q,
    )


def _build_email_document_content(
    to,
    subject,
    body,
    *,
    cc=None,
    bcc=None,
    in_reply_to=None,
    references=None,
    source_uid=None,
    source_folder=None,
):
    return build_email_document_content(
        to,
        subject,
        body,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
        source_uid=source_uid,
        source_folder=source_folder,
    )


def _merge_email_reply_body(existing_content: str, reply_body: str) -> str:
    """Preserve email headers and quoted chain while replacing the editable reply body."""
    return merge_email_reply_body(existing_content, reply_body)


def _create_email_draft_document(
    *,
    to,
    subject,
    body,
    title=None,
    cc=None,
    bcc=None,
    in_reply_to=None,
    references=None,
    source_uid=None,
    source_folder=None,
    account=None,
    source_message_id=None,
):
    """Create an Odysseus email compose document for user review. Does not send."""
    return create_email_draft_document(
        to=to,
        subject=subject,
        body=body,
        title=title,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
        source_uid=source_uid,
        source_folder=source_folder,
        account=account,
        source_message_id=source_message_id,
        load_config_func=_load_config,
        current_owner_func=_current_owner,
        default_document_owner_func=_default_document_owner,
    )


def _draft_reply_to_email(uid, body, folder="INBOX", reply_all=False, account=None, title=None):
    """Create a threaded Odysseus reply draft document. Does not send."""
    conn = _imap_connect(account)
    conn.select(_q(folder), readonly=True)
    status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    conn.logout()
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    orig = email.message_from_bytes(raw)

    orig_subject = _decode_header(orig.get("Subject", ""))
    reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    orig_message_id = orig.get("Message-ID", "")
    orig_references = orig.get("References", "")
    new_references = (orig_references + " " + orig_message_id).strip() if orig_references else orig_message_id

    sender = _decode_header(orig.get("From", ""))
    _, sender_addr = email.utils.parseaddr(sender)
    to_addrs = sender_addr

    cc = None
    if reply_all:
        cc_addrs = []
        cfg = _load_config(account)
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

    return _create_email_draft_document(
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


async def _ai_draft_reply_to_email(uid, folder="INBOX", reply_all=False, account=None, title=None):
    """Generate a reply with Odysseus' AI-reply prompt/style, then create a compose doc."""
    read_result = _read_email(uid=uid, folder=folder, account=account)
    if "error" in read_result:
        return read_result

    to_addr = read_result.get("from_address") or email.utils.parseaddr(read_result.get("from") or "")[1]
    subject = read_result.get("subject") or ""
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    original_body = read_result.get("body") or ""
    message_id = read_result.get("message_id") or ""

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

    return _draft_reply_to_email(
        uid=uid,
        body=reply,
        folder=folder,
        reply_all=reply_all,
        account=account,
        title=title or reply_subject,
    )


def _reply_to_email(uid, body, folder="INBOX", reply_all=False, account=None):
    """Reply to an existing email by UID. Threads via In-Reply-To/References."""
    conn = None
    try:
        conn = _imap_connect(account)
        conn.select(_q(folder), readonly=True)
        status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    orig = email.message_from_bytes(raw)

    orig_subject = _decode_header(orig.get("Subject", ""))
    reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    orig_message_id = orig.get("Message-ID", "")
    orig_references = orig.get("References", "")
    new_references = (orig_references + " " + orig_message_id).strip() if orig_references else orig_message_id

    sender = _decode_header(orig.get("From", ""))
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

    return _send_email(
        to=to_addrs,
        subject=reply_subject,
        body=body,
        in_reply_to=orig_message_id,
        references=new_references,
        cc=cc,
        account=account,
    )


def _set_flag(uid, folder, flag, add=True, account=None):
    """Add or remove an IMAP flag (e.g. \\Seen, \\Answered, \\Deleted)."""
    conn = _imap_connect(account)
    conn.select(_q(folder))
    op = "+FLAGS" if add else "-FLAGS"
    try:
        status, data = conn.uid("STORE", _b(uid), op, flag)
        if add and flag == "\\Deleted":
            conn.expunge()
        return status == "OK" and bool(data and data[0])
    except Exception:
        return False
    finally:
        conn.logout()


def _bulk_set_flag(uids, folder, flag, add=True, account=None):
    """Add/remove an IMAP flag on MANY messages in one connection.
    `uids` is a list; we issue a single STORE over the comma-joined set
    (IMAP supports message-set syntax). Returns count attempted."""
    if not uids:
        return 0
    conn = _imap_connect(account)
    touched = []
    try:
        conn.select(_q(folder))
        op = "+FLAGS" if add else "-FLAGS"
        msg_set = ",".join(str(u) for u in uids)
        try:
            status, data = conn.uid("FETCH", _b(msg_set), "(UID)")
        except Exception:
            return 0
        touched = _uid_fetch_rows(data)
        if status != "OK" or not touched:
            return 0
        status, data = conn.uid("STORE", _b(msg_set), op, flag)
        if add and flag == "\\Deleted":
            conn.expunge()
        if status != "OK":
            return 0
    finally:
        conn.logout()
    return len(touched)


def _bulk_move(uids, source_folder, dest_folder, account=None, role: str = ""):
    """Move MANY messages between folders in one connection."""
    if not uids:
        return 0
    conn = _imap_connect(account)
    moved = 0
    try:
        conn.select(_q(source_folder))
        dest_folder = _resolve_folder(conn, dest_folder, role or _folder_role_from_name(dest_folder))
        msg_set = ",".join(str(u) for u in uids)
        try:
            status, data = conn.uid("FETCH", _b(msg_set), "(UID)")
        except Exception:
            return 0
        existing = _uid_fetch_rows(data)
        if not existing:
            return 0
        moved = len(existing)
        dest_arg = _q(dest_folder)
        status, _ = conn.uid("MOVE", _b(msg_set), dest_arg)
        if status != "OK":
            # Fallback: UID copy + flag-delete + expunge
            status, _ = conn.uid("COPY", _b(msg_set), dest_arg)
            if status != "OK":
                return 0
            status, _ = conn.uid("STORE", _b(msg_set), "+FLAGS", "\\Deleted")
            if status != "OK":
                return 0
            conn.expunge()
    finally:
        conn.logout()
    return moved


def _search_uids(folder="INBOX", criteria="UNSEEN", account=None):
    """Return a list of UIDs matching an IMAP search (e.g. UNSEEN,
    ALL, ANSWERED). Used to resolve selectors like all_unread → uids."""
    conn = _imap_connect(account)
    try:
        conn.select(_q(folder), readonly=True)
        status, data = conn.uid("SEARCH", None, criteria)
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()
    finally:
        conn.logout()


def _move_message(uid, source_folder, dest_folder, account=None, role: str = ""):
    """Move a message between folders. Tries IMAP MOVE, falls back to copy+delete."""
    conn = _imap_connect(account)
    conn.select(_q(source_folder))
    try:
        dest_folder = _resolve_folder(conn, dest_folder, role or _folder_role_from_name(dest_folder))
        try:
            status, data = conn.uid("FETCH", _b(uid), "(UID)")
        except Exception:
            return False
        existing = _uid_fetch_rows(data)
        if status != "OK" or not existing:
            return False
        dest_arg = _q(dest_folder)
        status, _ = conn.uid("MOVE", _b(uid), dest_arg)
        if status == "OK":
            return True
        # Fallback: UID copy + delete
        status, _ = conn.uid("COPY", _b(uid), dest_arg)
        if status != "OK":
            return False
        status, _ = conn.uid("STORE", _b(uid), "+FLAGS", "\\Deleted")
        if status != "OK":
            return False
        conn.expunge()
        ok = True
    finally:
        conn.logout()
    return ok


def _delete_email(uid, folder="INBOX", permanent=False, account=None):
    """Delete an email. By default moves to Trash; permanent=True expunges."""
    cfg = _load_config(account)
    if permanent:
        return _set_flag(uid, folder, "\\Deleted", add=True, account=account)
    return _move_message(uid, folder, cfg["trash_folder"], account=account, role="trash")


def _archive_email(uid, folder="INBOX", account=None):
    """Move an email to the archive folder."""
    cfg = _load_config(account)
    return _move_message(uid, folder, cfg["archive_folder"], account=account, role="archive")


def _download_attachment(uid, index, folder="INBOX", account=None):
    """Extract a specific attachment to disk and return its local path."""
    conn = None
    try:
        conn = _imap_connect(account)
        conn.select(_q(folder), readonly=True)
        status, msg_data = conn.uid("FETCH", _b(uid), "(BODY.PEEK[])")
    finally:
        if conn:
            try: conn.logout()
            except Exception: pass
    if status != "OK":
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    target_dir = Path(MAIL_ATTACHMENTS_DIR) / f"{folder}_{uid}"
    filepath = _extract_attachment_to_disk(msg, index, target_dir)
    if not filepath:
        return {"error": f"Attachment index {index} not found"}
    size = os.path.getsize(filepath)
    return {"path": filepath, "filename": os.path.basename(filepath), "size": size}


# ── MCP Tool Registration ──


@server.list_tools()
async def list_tools() -> list[Tool]:
    from mcp_servers.email_tool_schemas import build_email_tools

    return build_email_tools(_writing_style_guidance)

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    arguments = dict(arguments) if isinstance(arguments, dict) else {}
    owner = str(arguments.pop(_MCP_OWNER_ARG, "") or "").strip()
    owner_token = _CURRENT_OWNER.set(owner or None)
    try:
        all_db_accounts = _read_accounts_from_db()
        if _mcp_owner_required(all_db_accounts):
            return [TextContent(
                type="text",
                text="Error: email MCP requires an authenticated owner when multiple email account owners are configured.",
            )]

        if name == "list_email_accounts":
            rows = _filter_accounts_for_owner(all_db_accounts)
            return [format_email_accounts_response(rows, all_db_accounts, owner)]

        acct = arguments.get("account")  # consumed by all email ops

        if name == "list_emails":
            max_results = arguments.get("max_results", arguments.get("limit", 20))
            unresponded_only = arguments.get("unresponded_only", False)
            unread_only = arguments.get("unread_only", False)
            # Build a header note so the LLM always knows which account was hit
            # AND what other accounts exist. Prevents "I can see emails" →
            # user: "I have 2 inboxes" → "which one?" loop.
            all_accounts = _list_accounts_raw()
            header_lines = []
            errors = []
            if len(all_accounts) >= 2 and not acct:
                results, errors = _list_emails_across_accounts(
                    folder=arguments.get("folder", "INBOX"),
                    max_results=max_results,
                    unresponded_only=unresponded_only,
                    unread_only=unread_only,
                )
                header_lines.append(merged_account_context_header(all_accounts))
            else:
                results = _list_emails(
                    folder=arguments.get("folder", "INBOX"),
                    max_results=max_results,
                    unresponded_only=unresponded_only,
                    unread_only=unread_only,
                    account=acct,
                )
                active_cfg = _load_config(acct)
                apply_active_account_context(results, active_cfg)

            if len(all_accounts) >= 2 and acct:
                active_cfg = _load_config(acct)
                header_lines.append(selected_account_context_header(all_accounts, active_cfg))
            if errors:
                header_lines.append("[EMAIL ACCOUNT ERRORS: " + "; ".join(errors) + "]\n")

            return [format_list_emails_response(results, header_lines)]

        elif name == "download_attachment":
            uid = arguments.get("uid")
            index = arguments.get("index")
            folder = arguments.get("folder", "INBOX")
            if uid is None or index is None:
                return [TextContent(type="text", text="Error: uid and index are required")]
            result = _download_attachment(uid, index, folder, account=acct)
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [format_download_attachment_response(result)]

        elif name == "search_emails":
            q = arguments.get("query", "")
            folders = arguments.get("folders") or None
            max_results = arguments.get("max_results", 20)
            try:
                hits = _search_emails(q, folders=folders, max_results=max_results, account=acct)
            except Exception as e:
                return [TextContent(type="text", text=f"Search failed: {e}")]
            return [format_search_emails_response(q, hits)]

        elif name == "read_email":
            all_accounts = _list_accounts_raw()
            if len(all_accounts) >= 2 and not acct:
                result = _read_email_across_accounts(
                    uid=arguments.get("uid"),
                    message_id=arguments.get("message_id"),
                    folder=arguments.get("folder", "INBOX"),
                )
            else:
                result = _read_email(
                    uid=arguments.get("uid"),
                    message_id=arguments.get("message_id"),
                    folder=arguments.get("folder", "INBOX"),
                    account=acct,
                )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]

            return [format_read_email_response(result)]

        elif name == "send_email":
            to = arguments.get("to")
            subject = arguments.get("subject")
            body = arguments.get("body")
            if not to or not subject or body is None:
                return [TextContent(type="text", text="Error: to, subject, and body are required")]
            result = _send_email(
                to=to,
                subject=subject,
                body=body,
                cc=arguments.get("cc"),
                bcc=arguments.get("bcc"),
                account=acct,
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            if result.get("pending"):
                return [TextContent(
                    type="text",
                    text=(
                        f"Draft staged for approval (pending id: {result.get('pending_id')}). "
                        "Nothing has been sent yet. Review and approve it in Odysseus before delivery."
                    ),
                )]
            return [format_sent_email_response(result)]

        elif name == "draft_email":
            to = arguments.get("to")
            subject = arguments.get("subject")
            body = arguments.get("body")
            if not to or not subject or body is None:
                return [TextContent(type="text", text="Error: to, subject, and body are required")]
            result = _create_email_draft_document(
                to=to,
                subject=subject,
                body=body,
                title=arguments.get("title"),
                cc=arguments.get("cc"),
                bcc=arguments.get("bcc"),
                account=acct,
            )
            return [format_draft_email_response(result)]

        elif name == "reply_to_email":
            uid = arguments.get("uid")
            body = arguments.get("body")
            if not uid or body is None:
                return [TextContent(type="text", text="Error: uid and body are required")]
            result = _reply_to_email(
                uid=uid,
                body=body,
                folder=arguments.get("folder", "INBOX"),
                reply_all=bool(arguments.get("reply_all", False)),
                account=acct,
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            # Mark original as answered
            try:
                _set_flag(uid, arguments.get("folder", "INBOX"), "\\Answered", add=True, account=acct)
            except Exception:
                pass
            return [TextContent(type="text", text=f"Replied to UID {uid}: '{result['subject']}' → {result['to']}")]

        elif name == "draft_email_reply":
            uid = arguments.get("uid")
            body = arguments.get("body")
            if not uid or body is None:
                return [TextContent(type="text", text="Error: uid and body are required")]
            result = _draft_reply_to_email(
                uid=uid,
                body=body,
                folder=arguments.get("folder", "INBOX"),
                reply_all=bool(arguments.get("reply_all", False)),
                account=acct,
                title=arguments.get("title"),
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [format_draft_reply_response(result, uid)]

        elif name == "ai_draft_email_reply":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            result = await _ai_draft_reply_to_email(
                uid=uid,
                folder=arguments.get("folder", "INBOX"),
                reply_all=bool(arguments.get("reply_all", False)),
                account=acct,
                title=arguments.get("title"),
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            return [format_ai_draft_reply_response(result, uid)]

        elif name == "archive_email":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            ok = _archive_email(uid, arguments.get("folder", "INBOX"), account=acct)
            return [TextContent(type="text", text=f"{'Archived' if ok else 'Failed to archive'} UID {uid}")]

        elif name == "delete_email":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            permanent = bool(arguments.get("permanent", False))
            if permanent and not _confirmed(arguments.get("confirmed", False)):
                return [_email_delete_confirmation_required(
                    "permanent email deletion requires explicit confirmation",
                    uid=str(uid),
                )]
            ok = _delete_email(
                uid,
                arguments.get("folder", "INBOX"),
                permanent=permanent,
                account=acct,
            )
            return [TextContent(type="text", text=f"{'Deleted' if ok else 'Failed to delete'} UID {uid}")]

        elif name == "mark_email_read":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            read = bool(arguments.get("read", True))
            ok = _set_flag(uid, arguments.get("folder", "INBOX"), "\\Seen", add=read, account=acct)
            state = "read" if read else "unread"
            return [TextContent(type="text", text=f"{'Marked' if ok else 'Failed to mark'} UID {uid} as {state}")]

        elif name == "bulk_email":
            action = arguments.get("action", "")
            folder = arguments.get("folder", "INBOX")
            all_unread = bool(arguments.get("all_unread", False))
            uids = arguments.get("uids") or []
            if action == "delete" and (uids or all_unread) and not _confirmed(arguments.get("confirmed", False)):
                return [_email_delete_confirmation_required(
                    "bulk email deletion requires explicit confirmation",
                    count=(len(uids) if not all_unread else None),
                )]
            if all_unread:
                uids = _search_uids(folder, "UNSEEN", account=acct)
            if not uids:
                return [TextContent(type="text", text="No messages selected (pass uids or all_unread=true).")]
            requested_n = len(uids)
            changed_n = 0
            try:
                if action == "mark_read":
                    changed_n = _bulk_set_flag(uids, folder, "\\Seen", add=True, account=acct)
                    verb = "marked read"
                elif action == "mark_unread":
                    changed_n = _bulk_set_flag(uids, folder, "\\Seen", add=False, account=acct)
                    verb = "marked unread"
                elif action == "archive":
                    cfg = _load_config(acct)
                    changed_n = _bulk_move(uids, folder, cfg["archive_folder"], account=acct, role="archive")
                    verb = "archived"
                elif action == "junk":
                    cfg = _load_config(acct)
                    junk_folder = cfg.get("junk_folder") or "Junk"
                    changed_n = _bulk_move(uids, folder, junk_folder, account=acct, role="junk")
                    verb = "moved to Junk"
                elif action == "delete":
                    permanent = bool(arguments.get("permanent", False))
                    if permanent:
                        changed_n = _bulk_set_flag(uids, folder, "\\Deleted", add=True, account=acct)
                        verb = "permanently deleted"
                    else:
                        cfg = _load_config(acct)
                        changed_n = _bulk_move(uids, folder, cfg["trash_folder"], account=acct, role="trash")
                        verb = "moved to Trash"
                else:
                    return [TextContent(type="text", text=f"Unknown bulk action: {action!r}. Use mark_read/mark_unread/archive/delete/junk.")]
            except Exception as e:
                return [TextContent(type="text", text=f"Bulk {action} failed after partial work: {e}")]
            return [format_bulk_result(changed_n, requested_n, verb, folder)]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    finally:
        _CURRENT_OWNER.reset(owner_token)


# ── Main ──

async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(run())
