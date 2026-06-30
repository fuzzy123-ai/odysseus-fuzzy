"""
email_server.py

MCP server exposing email tools: list unread/unresponded emails,
read email content, and draft replies as email documents.
Connects to local Dovecot IMAP and reads from the AI summary cache.
"""

import asyncio
import imaplib
import smtplib
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
    download_attachment_to_disk,
    _extract_attachment_to_disk,
    _list_attachments_from_msg,
)
from mcp_servers.email_bulk_tool_utils import handle_bulk_email_tool
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
from mcp_servers.email_imap_mutation_utils import (
    archive_email as archive_email_via_helper,
    bulk_move as bulk_move_via_helper,
    bulk_set_flag as bulk_set_flag_via_helper,
    delete_email as delete_email_via_helper,
    move_message as move_message_via_helper,
    search_uids as search_uids_via_helper,
    set_flag as set_flag_via_helper,
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
from mcp_servers.email_read_operations import (
    list_emails as list_emails_via_helper,
    list_emails_across_accounts as list_emails_across_accounts_via_helper,
    read_email as read_email_via_helper,
    read_email_across_accounts as read_email_across_accounts_via_helper,
    search_emails as search_emails_via_helper,
)
from mcp_servers.email_reply_utils import (
    ai_draft_reply_to_email as ai_draft_reply_to_email_via_helper,
    draft_reply_to_email as draft_reply_to_email_via_helper,
    reply_to_email as reply_to_email_via_helper,
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
    return list_emails_via_helper(
        folder=folder,
        max_results=max_results,
        unresponded_only=unresponded_only,
        unread_only=unread_only,
        account=account,
        imap_connect=_imap_connect,
        quote_folder=_q,
        decode_header=_decode_header,
        get_cached_summaries=_get_cached_summaries,
    )


def _list_emails_across_accounts(folder="INBOX", max_results=20,
                                 unresponded_only=False, unread_only=False):
    return list_emails_across_accounts_via_helper(
        folder=folder,
        max_results=max_results,
        unresponded_only=unresponded_only,
        unread_only=unread_only,
        list_accounts_raw=_list_accounts_raw,
        list_emails_func=_list_emails,
        result_sort_time=_result_sort_time,
    )


def _search_emails(query, folders=None, max_results=20, account=None):
    """IMAP-search emails by free-text query. Matches FROM, SUBJECT, and
    body TEXT. Walks multiple folders so older threads outside INBOX
    (Sent/Archive) are still findable. Returns the same shape as
    _list_emails plus an `_folder` tag."""
    return search_emails_via_helper(
        query=query,
        folders=folders,
        max_results=max_results,
        account=account,
        imap_connect=_imap_connect,
        quote_folder=_q,
        decode_header=_decode_header,
        get_cached_summaries=_get_cached_summaries,
    )


def _read_email(uid=None, message_id=None, folder="INBOX", account=None):
    """Read full email content by UID or message-ID. account = mailbox selector."""
    return read_email_via_helper(
        uid=uid,
        message_id=message_id,
        folder=folder,
        account=account,
        load_config=_load_config,
        imap_connect=_imap_connect,
        quote_folder=_q,
        bytes_func=_b,
        decode_header=_decode_header,
        extract_text=_extract_text,
        list_attachments_from_msg=_list_attachments_from_msg,
    )


def _read_email_across_accounts(uid=None, message_id=None, folder="INBOX"):
    return read_email_across_accounts_via_helper(
        uid=uid,
        message_id=message_id,
        folder=folder,
        list_accounts_raw=_list_accounts_raw,
        read_email_func=_read_email,
    )


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
    return draft_reply_to_email_via_helper(
        uid,
        body,
        folder=folder,
        reply_all=reply_all,
        account=account,
        title=title,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
        bytes_func=_b,
        decode_header_func=_decode_header,
        load_config_func=_load_config,
        create_draft_document_func=_create_email_draft_document,
    )


async def _ai_draft_reply_to_email(uid, folder="INBOX", reply_all=False, account=None, title=None):
    """Generate a reply with Odysseus' AI-reply prompt/style, then create a compose doc."""
    return await ai_draft_reply_to_email_via_helper(
        uid=uid,
        folder=folder,
        reply_all=reply_all,
        account=account,
        title=title,
        read_email_func=_read_email,
        draft_reply_func=_draft_reply_to_email,
    )


def _reply_to_email(uid, body, folder="INBOX", reply_all=False, account=None):
    """Reply to an existing email by UID. Threads via In-Reply-To/References."""
    return reply_to_email_via_helper(
        uid,
        body,
        folder=folder,
        reply_all=reply_all,
        account=account,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
        bytes_func=_b,
        decode_header_func=_decode_header,
        send_email_func=_send_email,
    )


def _set_flag(uid, folder, flag, add=True, account=None):
    """Add or remove an IMAP flag (e.g. \\Seen, \\Answered, \\Deleted)."""
    return set_flag_via_helper(
        uid,
        folder,
        flag,
        add=add,
        account=account,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
        bytes_func=_b,
    )


def _bulk_set_flag(uids, folder, flag, add=True, account=None):
    """Add/remove an IMAP flag on MANY messages in one connection.
    `uids` is a list; we issue a single STORE over the comma-joined set
    (IMAP supports message-set syntax). Returns count attempted."""
    return bulk_set_flag_via_helper(
        uids,
        folder,
        flag,
        add=add,
        account=account,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
        bytes_func=_b,
        uid_fetch_rows_func=_uid_fetch_rows,
    )


def _bulk_move(uids, source_folder, dest_folder, account=None, role: str = ""):
    """Move MANY messages between folders in one connection."""
    return bulk_move_via_helper(
        uids,
        source_folder,
        dest_folder,
        account=account,
        role=role,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
        bytes_func=_b,
        resolve_folder_func=_resolve_folder,
        folder_role_from_name_func=_folder_role_from_name,
        uid_fetch_rows_func=_uid_fetch_rows,
    )


def _search_uids(folder="INBOX", criteria="UNSEEN", account=None):
    """Return a list of UIDs matching an IMAP search (e.g. UNSEEN,
    ALL, ANSWERED). Used to resolve selectors like all_unread → uids."""
    return search_uids_via_helper(
        folder=folder,
        criteria=criteria,
        account=account,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
    )


def _move_message(uid, source_folder, dest_folder, account=None, role: str = ""):
    """Move a message between folders. Tries IMAP MOVE, falls back to copy+delete."""
    return move_message_via_helper(
        uid,
        source_folder,
        dest_folder,
        account=account,
        role=role,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
        bytes_func=_b,
        resolve_folder_func=_resolve_folder,
        folder_role_from_name_func=_folder_role_from_name,
        uid_fetch_rows_func=_uid_fetch_rows,
    )


def _delete_email(uid, folder="INBOX", permanent=False, account=None):
    """Delete an email. By default moves to Trash; permanent=True expunges."""
    return delete_email_via_helper(
        uid,
        folder=folder,
        permanent=permanent,
        account=account,
        load_config_func=_load_config,
        set_flag_func=_set_flag,
        move_message_func=_move_message,
    )


def _archive_email(uid, folder="INBOX", account=None):
    """Move an email to the archive folder."""
    return archive_email_via_helper(
        uid,
        folder=folder,
        account=account,
        load_config_func=_load_config,
        move_message_func=_move_message,
    )


def _download_attachment(uid, index, folder="INBOX", account=None):
    """Extract a specific attachment to disk and return its local path."""
    return download_attachment_to_disk(
        uid,
        index,
        folder=folder,
        account=account,
        imap_connect_func=_imap_connect,
        quote_folder_func=_q,
        bytes_func=_b,
        attachments_dir=MAIL_ATTACHMENTS_DIR,
    )


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
            return handle_bulk_email_tool(
                arguments,
                account=acct,
                confirmed_func=_confirmed,
                confirmation_required_func=_email_delete_confirmation_required,
                search_uids_func=_search_uids,
                bulk_set_flag_func=_bulk_set_flag,
                bulk_move_func=_bulk_move,
                load_config_func=_load_config,
                format_bulk_result_func=format_bulk_result,
            )

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
