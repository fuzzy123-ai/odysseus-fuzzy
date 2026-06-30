"""Response formatting helpers for the built-in email MCP server."""

from __future__ import annotations

from mcp.types import TextContent


def format_email_accounts_response(
    rows: list[dict],
    all_db_accounts: list[dict],
    owner: str,
) -> TextContent:
    if not rows:
        if all_db_accounts and owner:
            return TextContent(type="text", text="No email accounts configured for this owner.")
        return TextContent(type="text", text="No email accounts configured. Legacy single-account mode active.")
    lines = [f"Found {len(rows)} email account(s):\n"]
    for row in rows:
        star = " (default)" if row.get("is_default") else ""
        lines.append(
            f"- **{row['name']}**{star}\n"
            f"  email: {row.get('imap_user') or row.get('from_address') or '(unknown)'}\n"
            f"  id: {row['id']}"
        )
    return TextContent(type="text", text="\n".join(lines))


def apply_active_account_context(results: list[dict], active_cfg: dict) -> None:
    if active_cfg.get("account_name") or active_cfg.get("imap_user"):
        for item in results:
            item["_account"] = active_cfg.get("account_name") or active_cfg.get("imap_user") or "default"
            item["_account_email"] = active_cfg.get("imap_user") or ""


def merged_account_context_header(all_accounts: list[dict]) -> str:
    account_names = [
        f"{account.get('name') or account.get('imap_user')} <{account.get('imap_user') or account.get('from_address') or '?'}>"
        for account in all_accounts
    ]
    return (
        "[EMAIL ACCOUNT CONTEXT: No `account` was provided, so this result is merged across configured accounts: "
        f"{', '.join(account_names)}. Each row includes its source account.]\n"
    )


def selected_account_context_header(all_accounts: list[dict], active_cfg: dict) -> str:
    active_name = active_cfg.get("account_name") or "default"
    active_email = active_cfg.get("imap_user") or ""
    other = [
        f"{account['name']} <{account.get('imap_user') or account.get('from_address') or '?'}>"
        for account in all_accounts
        if account["id"] != active_cfg.get("account_id")
    ]
    return (
        f"[EMAIL ACCOUNT CONTEXT: This result is ONLY from account `{active_name}` ({active_email}). "
        f"Other configured accounts: {', '.join(other)}. "
        "If the user asks for Gmail/another inbox, call list_emails again with `account` set to that account name or email.]\n"
    )


def format_list_emails_response(results: list[dict], header_lines: list[str]) -> TextContent:
    if not results:
        msg = "No unread/unresponded emails found."
        if header_lines:
            msg = "\n".join(header_lines) + msg
        return TextContent(type="text", text=msg)

    lines = header_lines + [f"Found {len(results)} email(s):\n"]
    for i, em in enumerate(results, 1):
        line = (
            f"{i}. **{em['subject']}**\n"
            f"   From: {em['from']} ({em['from_address']})\n"
            f"   Date: {em['date']}\n"
            f"   UID: {em['uid']}"
        )
        if em.get("_account"):
            account_label = em.get("_account")
            if em.get("_account_email"):
                account_label += f" <{em['_account_email']}>"
            line += f"\n   Account: {account_label}"
        if em.get("summary"):
            line += f"\n   Summary: {em['summary']}"
        lines.append(line)
    return TextContent(type="text", text="\n\n".join(lines))


def format_download_attachment_response(result: dict) -> TextContent:
    text = (
        f"Attachment downloaded to: `{result['path']}`\n"
        f"Filename: {result['filename']}\n"
        f"Size: {result['size']} bytes\n\n"
        "You can now read this file using the read_file tool."
    )
    return TextContent(type="text", text=text)


def format_search_emails_response(query: str, hits: list[dict]) -> TextContent:
    if not hits:
        return TextContent(type="text", text=f'No emails matched "{query}".')
    lines = [f'Found {len(hits)} email(s) matching "{query}":\n']
    for i, em in enumerate(hits, 1):
        lines.append(
            f"{i}. **{em['subject']}**\n"
            f"   From: {em['from']} ({em['from_address']})\n"
            f"   Date: {em['date']}\n"
            f"   Folder: {em.get('_folder', 'INBOX')}\n"
            f"   UID: {em['uid']}"
        )
        if em.get("to"):
            lines.append(f"   To: {em['to']}")
        if em.get("summary"):
            lines.append(f"   Summary: {em['summary']}")
    return TextContent(type="text", text="\n".join(lines))


def format_read_email_response(result: dict) -> TextContent:
    text = (
        f"**Subject:** {result['subject']}\n"
        f"**From:** {result['from']} ({result['from_address']})\n"
        f"**Date:** {result['date']}\n"
        f"**UID:** {result['uid']}\n"
        f"**Account:** {result.get('account', 'default')} ({result.get('account_email', '')})\n"
        f"**Message-ID:** {result['message_id']}\n"
    )
    if result.get("attachments"):
        text += f"\n**Attachments ({len(result['attachments'])}):**\n"
        for attachment in result["attachments"]:
            size_kb = attachment["size"] // 1024
            text += (
                f"  - [{attachment['index']}] {attachment['filename']} "
                f"({attachment['content_type']}, {size_kb}KB)\n"
            )
        text += "\n_Use `download_attachment` with the UID and index to download._\n"
    text += f"\n---\n\n{result['body']}"
    return TextContent(type="text", text=text)


def format_sent_email_response(result: dict) -> TextContent:
    acct_note = f" (from {result['account']})" if result.get("account") else ""
    return TextContent(type="text", text=f"Sent email to {result['to']} with subject '{result['subject']}'{acct_note}.")


def format_draft_email_response(result: dict) -> TextContent:
    acct_note = f" from {result['account']}" if result.get("account") else ""
    return TextContent(
        type="text",
        text=(
            f"Created Odysseus email draft `{result['title']}` "
            f"(document ID: {result['doc_id']}){acct_note}. "
            "It has not been sent; open the document in Odysseus to review and send."
        ),
    )


def format_draft_reply_response(result: dict, uid) -> TextContent:
    acct_note = f" from {result['account']}" if result.get("account") else ""
    return TextContent(
        type="text",
        text=(
            f"Created Odysseus reply draft `{result['title']}` for UID {uid} "
            f"(document ID: {result['doc_id']}){acct_note}. "
            "It has not been sent; open the document in Odysseus to review and send."
        ),
    )


def format_ai_draft_reply_response(result: dict, uid) -> TextContent:
    acct_note = f" from {result['account']}" if result.get("account") else ""
    return TextContent(
        type="text",
        text=(
            "Generated AI reply and created Odysseus compose draft "
            f"`{result['title']}` for UID {uid} (document ID: {result['doc_id']}){acct_note}. "
            "It has not been sent; open the document in Odysseus to review and send."
        ),
    )


def format_bulk_result(changed_n: int, requested_n: int, verb: str, folder: str) -> TextContent:
    if changed_n <= 0:
        return TextContent(
            type="text",
            text=f"No matching UIDs found in {folder}; 0 of {requested_n} email(s) {verb}.",
        )
    suffix = "" if changed_n == requested_n else f" ({changed_n} of {requested_n} requested UIDs matched)"
    return TextContent(type="text", text=f"Done — {changed_n} email(s) {verb}{suffix}.")
