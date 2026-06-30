"""Email MCP bulk action dispatch helpers."""

from __future__ import annotations

from mcp.types import TextContent


def email_text(text: str) -> TextContent:
    return TextContent(type="text", text=text)


def handle_bulk_email_tool(
    arguments: dict,
    *,
    account,
    confirmed_func,
    confirmation_required_func,
    search_uids_func,
    bulk_set_flag_func,
    bulk_move_func,
    load_config_func,
    format_bulk_result_func,
) -> list[TextContent]:
    action = arguments.get("action", "")
    folder = arguments.get("folder", "INBOX")
    all_unread = bool(arguments.get("all_unread", False))
    uids = arguments.get("uids") or []
    if action == "delete" and (uids or all_unread) and not confirmed_func(arguments.get("confirmed", False)):
        return [confirmation_required_func(
            "bulk email deletion requires explicit confirmation",
            count=(len(uids) if not all_unread else None),
        )]
    if all_unread:
        uids = search_uids_func(folder, "UNSEEN", account=account)
    if not uids:
        return [email_text("No messages selected (pass uids or all_unread=true).")]
    requested_n = len(uids)
    changed_n = 0
    try:
        if action == "mark_read":
            changed_n = bulk_set_flag_func(uids, folder, "\\Seen", add=True, account=account)
            verb = "marked read"
        elif action == "mark_unread":
            changed_n = bulk_set_flag_func(uids, folder, "\\Seen", add=False, account=account)
            verb = "marked unread"
        elif action == "archive":
            cfg = load_config_func(account)
            changed_n = bulk_move_func(uids, folder, cfg["archive_folder"], account=account, role="archive")
            verb = "archived"
        elif action == "junk":
            cfg = load_config_func(account)
            junk_folder = cfg.get("junk_folder") or "Junk"
            changed_n = bulk_move_func(uids, folder, junk_folder, account=account, role="junk")
            verb = "moved to Junk"
        elif action == "delete":
            permanent = bool(arguments.get("permanent", False))
            if permanent:
                changed_n = bulk_set_flag_func(uids, folder, "\\Deleted", add=True, account=account)
                verb = "permanently deleted"
            else:
                cfg = load_config_func(account)
                changed_n = bulk_move_func(uids, folder, cfg["trash_folder"], account=account, role="trash")
                verb = "moved to Trash"
        else:
            return [email_text(f"Unknown bulk action: {action!r}. Use mark_read/mark_unread/archive/delete/junk.")]
    except Exception as e:
        return [email_text(f"Bulk {action} failed after partial work: {e}")]
    return [format_bulk_result_func(changed_n, requested_n, verb, folder)]
