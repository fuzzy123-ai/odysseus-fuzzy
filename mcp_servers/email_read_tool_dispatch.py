"""Read-only MCP tool dispatch helpers for the email server."""

from __future__ import annotations

from mcp.types import TextContent

from mcp_servers.email_tool_formatting import (
    apply_active_account_context,
    format_download_attachment_response,
    format_email_accounts_response,
    format_list_emails_response,
    format_read_email_response,
    format_search_emails_response,
    merged_account_context_header,
    selected_account_context_header,
)


def handle_read_tool(
    name: str,
    arguments: dict,
    *,
    owner: str,
    all_db_accounts: list[dict],
    account,
    filter_accounts_for_owner,
    list_accounts_raw,
    load_config,
    list_emails,
    list_emails_across_accounts,
    download_attachment,
    search_emails,
    read_email,
    read_email_across_accounts,
) -> list[TextContent] | None:
    """Handle read-only email MCP tools, or return None for mutation tools."""
    if name == "list_email_accounts":
        rows = filter_accounts_for_owner(all_db_accounts)
        return [format_email_accounts_response(rows, all_db_accounts, owner)]

    if name == "list_emails":
        max_results = arguments.get("max_results", arguments.get("limit", 20))
        unresponded_only = arguments.get("unresponded_only", False)
        unread_only = arguments.get("unread_only", False)
        all_accounts = list_accounts_raw()
        header_lines = []
        errors = []
        if len(all_accounts) >= 2 and not account:
            results, errors = list_emails_across_accounts(
                folder=arguments.get("folder", "INBOX"),
                max_results=max_results,
                unresponded_only=unresponded_only,
                unread_only=unread_only,
            )
            header_lines.append(merged_account_context_header(all_accounts))
        else:
            results = list_emails(
                folder=arguments.get("folder", "INBOX"),
                max_results=max_results,
                unresponded_only=unresponded_only,
                unread_only=unread_only,
                account=account,
            )
            active_cfg = load_config(account)
            apply_active_account_context(results, active_cfg)

        if len(all_accounts) >= 2 and account:
            active_cfg = load_config(account)
            header_lines.append(selected_account_context_header(all_accounts, active_cfg))
        if errors:
            header_lines.append("[EMAIL ACCOUNT ERRORS: " + "; ".join(errors) + "]\n")

        return [format_list_emails_response(results, header_lines)]

    if name == "download_attachment":
        uid = arguments.get("uid")
        index = arguments.get("index")
        folder = arguments.get("folder", "INBOX")
        if uid is None or index is None:
            return [TextContent(type="text", text="Error: uid and index are required")]
        result = download_attachment(uid, index, folder, account=account)
        if "error" in result:
            return [TextContent(type="text", text=f"Error: {result['error']}")]
        return [format_download_attachment_response(result)]

    if name == "search_emails":
        query = arguments.get("query", "")
        folders = arguments.get("folders") or None
        max_results = arguments.get("max_results", 20)
        try:
            hits = search_emails(query, folders=folders, max_results=max_results, account=account)
        except Exception as exc:
            return [TextContent(type="text", text=f"Search failed: {exc}")]
        return [format_search_emails_response(query, hits)]

    if name == "read_email":
        all_accounts = list_accounts_raw()
        if len(all_accounts) >= 2 and not account:
            result = read_email_across_accounts(
                uid=arguments.get("uid"),
                message_id=arguments.get("message_id"),
                folder=arguments.get("folder", "INBOX"),
            )
        else:
            result = read_email(
                uid=arguments.get("uid"),
                message_id=arguments.get("message_id"),
                folder=arguments.get("folder", "INBOX"),
                account=account,
            )
        if "error" in result:
            return [TextContent(type="text", text=f"Error: {result['error']}")]
        return [format_read_email_response(result)]

    return None
