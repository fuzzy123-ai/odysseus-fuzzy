"""Tool schema declarations for the built-in email MCP server."""

from __future__ import annotations

from collections.abc import Callable

from mcp.types import Tool


def build_email_tools(_writing_style_guidance: Callable[[], str]) -> list[Tool]:
    # The user may have multiple IMAP accounts configured. Every tool accepts an
    # optional `account` param — match by name (e.g. "work"), email address,
    # or account id. Leave it out to use the default account.
    ACCOUNT_PROP = {
        "account": {
            "type": "string",
            "description": "Which email account to use (name, email, or id). "
                           "Omit to use the default account. Use list_email_accounts to discover available accounts.",
        },
    }
    return [
        Tool(
            name="list_email_accounts",
            description=(
                "List the email accounts configured in Odysseus. Returns each account's "
                "name, email address, and whether it's the default. Use this first when "
                "the user asks about a specific inbox by name (e.g. 'check work')."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_emails",
            description=(
                "List unread or unresponded emails from the inbox. "
                "Returns subject, sender, date, and cached AI summary for each. "
                "Use this to check what emails need attention. "
                "Pass `account` to scan a non-default mailbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "IMAP folder to check (default: INBOX)",
                        "default": "INBOX",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default: 20)",
                        "default": 20,
                    },
                    "unresponded_only": {
                        "type": "boolean",
                        "description": "Only show emails without replies (default: false)",
                        "default": False,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only show unread emails. Default false so latest/all inbox requests match normal mail clients.",
                        "default": False,
                    },
                    **ACCOUNT_PROP,
                },
                "required": [],
            },
        ),
        Tool(
            name="download_attachment",
            description=(
                "Download an email attachment to the local disk so you can read it. "
                "Returns the local file path which you can then read with read_file. "
                "Use this when you need to review a document, spreadsheet, or other "
                "file attached to an email."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "index": {"type": "integer", "description": "Attachment index (from read_email's attachments list)"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "index"],
            },
        ),
        Tool(
            name="send_email",
            description=(
                "Send a new email via SMTP. Provide recipient(s), subject, and body. "
                "This sends immediately; for normal assistant-written email, prefer "
                "draft_email so the user can review and send from Odysseus. "
                "For replying to an existing thread, use reply_to_email instead. "
                "Pass `account` to send from a non-default mailbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Plain text body"},
                    "cc": {"type": "string", "description": "CC address(es), comma-separated (optional)"},
                    "bcc": {"type": "string", "description": "BCC address(es), comma-separated (optional)"},
                    **ACCOUNT_PROP,
                },
                "required": ["to", "subject", "body"],
            },
        ),
        Tool(
            name="draft_email",
            description=(
                "Create a new Odysseus email compose draft document. This DOES NOT send. "
                "Use this as the default way to write an email for the user: it opens "
                "a reviewable email document with To/Cc/Bcc/Subject/body, and the user "
                "can edit or press Send in Odysseus. "
                f"{_writing_style_guidance()}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Draft body"},
                    "cc": {"type": "string", "description": "CC address(es), comma-separated (optional)"},
                    "bcc": {"type": "string", "description": "BCC address(es), comma-separated (optional)"},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    **ACCOUNT_PROP,
                },
                "required": ["to", "subject", "body"],
            },
        ),
        Tool(
            name="reply_to_email",
            description=(
                "Reply to an existing email by UID. This sends immediately; for normal "
                "assistant-written replies, prefer draft_email_reply so the user can "
                "review and send from Odysseus. Automatically threads the reply with "
                "In-Reply-To and References headers, prefixes 'Re:' on the subject, and "
                "uses the original sender as the recipient. Set reply_all=true to also CC "
                "the original To/Cc recipients. For follow-up 'reply ...' requests, use "
                "the exact UID from the latest list_emails/read_email result; never invent UID 1."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    "reply_all": {"type": "boolean", "description": "Reply to all recipients (default: false)", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "body"],
            },
        ),
        Tool(
            name="draft_email_reply",
            description=(
                "Create an Odysseus email reply draft document for an existing email UID. "
                "This DOES NOT send. It threads the draft with In-Reply-To/References, "
                "prefills the recipient and subject, and stores source email metadata so "
                "the user can review and send from the normal email composer. "
                f"{_writing_style_guidance()}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Draft reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    "reply_all": {"type": "boolean", "description": "Reply to all recipients (default: false)", "default": False},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "body"],
            },
        ),
        Tool(
            name="ai_draft_email_reply",
            description=(
                "Generate an AI reply using Odysseus' existing AI Reply behavior, "
                "including Settings > Email > Writing Style, then create an email "
                "compose document for review. This DOES NOT send and does NOT save "
                "to the mailbox Drafts folder. Use this when the user asks you to "
                "write or draft a reply to an email without dictating the exact body."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    "reply_all": {"type": "boolean", "description": "Reply to all recipients (default: false)", "default": False},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="archive_email",
            description="Move an email out of the inbox into the Archive folder. Use after handling an email you want to keep but no longer need in the inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "folder": {"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="delete_email",
            description=(
                "Delete an email. By default moves it to the Trash folder; pass "
                "permanent=true to expunge immediately. permanent=true requires "
                "explicit user confirmation and confirmed=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "folder": {"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"},
                    "permanent": {"type": "boolean", "description": "Hard-delete instead of move to Trash", "default": False},
                    "confirmed": {"type": "boolean", "description": "Required only for permanent=true after explicit user confirmation", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="mark_email_read",
            description="Mark an email as read (\\Seen flag) or unread (read=false).",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID"},
                    "folder": {"type": "string", "description": "IMAP folder", "default": "INBOX"},
                    "read": {"type": "boolean", "description": "True to mark read, false to mark unread", "default": True},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="bulk_email",
            description=(
                "Perform one action on MANY emails at once — the efficient way to "
                "'mark all as read', 'archive these', 'delete all spam', etc. Select "
                "messages either by an explicit `uids` list OR by `all_unread: true` "
                "(operates on every unread message in the folder). Far better than "
                "calling mark_email_read / archive_email once per message."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"],
                        "description": "What to do to every selected message.",
                    },
                    "uids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit list of UIDs. Omit if using all_unread.",
                    },
                    "all_unread": {
                        "type": "boolean",
                        "description": "Operate on ALL unread messages in the folder (ignores uids).",
                        "default": False,
                    },
                    "folder": {"type": "string", "description": "IMAP folder", "default": "INBOX"},
                    "permanent": {"type": "boolean", "description": "For delete: expunge instead of moving to Trash.", "default": False},
                    "confirmed": {"type": "boolean", "description": "Required for action=delete after explicit user confirmation.", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="search_emails",
            description=(
                "Search emails by free-text query (sender, subject, or body). "
                "Walks INBOX + Sent + Archive by default so older threads are findable, "
                "not just recent unread. Use this whenever the user names a person or "
                "topic that isn't in the most recent inbox slice — e.g. 'Sara Sotheby's', "
                "'invoice from EY', 'last email about the property'. Returns matching "
                "emails with their UIDs so you can read_email or reply_to_email."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text query. Matches FROM, SUBJECT, and body TEXT.",
                    },
                    "folders": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Folders to search (default: INBOX, Sent, Archive)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results per folder (default: 20)",
                        "default": 20,
                    },
                    **ACCOUNT_PROP,
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="read_email",
            description=(
                "Read the full content of a specific email. "
                "Provide either the UID (from list_emails) or a Message-ID. "
                "Returns the subject, sender, date, and full body text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {
                        "type": "string",
                        "description": "Email UID from list_emails results",
                    },
                    "message_id": {
                        "type": "string",
                        "description": "RFC Message-ID header value",
                    },
                    "folder": {
                        "type": "string",
                        "description": "IMAP folder (default: INBOX)",
                        "default": "INBOX",
                    },
                    **ACCOUNT_PROP,
                },
                "required": [],
            },
        ),
    ]
