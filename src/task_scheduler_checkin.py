"""Assistant check-in execution for scheduled tasks."""

from __future__ import annotations

import json
import logging

from src.task_scheduler_helpers import (
    _cached,
    _checkin_calendar_events,
    _digest_windows,
    _resolve_task_timezone,
    _utcnow,
)

logger = logging.getLogger(__name__)


CHECKIN_MCP_PATTERNS = [
    {"detect": "list_emails",   "section": "Email",    "tool": "list_emails",
     "args": {"mailbox": "INBOX", "limit": 10, "unread_only": True},
     "label_from_identity": True,
     "formatter": "_format_email_output"},
    {"detect": "search_emails", "section": "Email",    "tool": "search_emails",
     "args": {"query": "is:unread", "limit": 10},
     "label_from_identity": True,
     "formatter": "_format_email_output"},
    {"detect": "get_feed",      "section": "RSS",      "tool": "get_feed",
     "args": {},
     "label_from_identity": False},
    {"detect": "list_feeds",    "section": "RSS",      "tool": "list_feeds",
     "args": {},
     "label_from_identity": False},
    {"detect": "list_messages", "section": "Messages", "tool": "list_messages",
     "args": {"limit": 10},
     "label_from_identity": True},
]


async def execute_checkin(scheduler, task, crew, db, session_id: str,
                           endpoint_url: str, model: str,
                           run_id: str | None = None,
                           tool_usage_instrumentation=None) -> str:
    """Gather raw data from all integrations, hand it to the LLM to write the check-in."""
    from src.tool_implementations import do_manage_notes
    from src.tool_utils import get_mcp_manager

    tz_name = _resolve_task_timezone(db, task)
    try:
        if tz_name:
            from zoneinfo import ZoneInfo
            from datetime import timezone, timedelta
            now = _utcnow().replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
        else:
            from datetime import timedelta
            now = _utcnow()
        time_str = now.strftime("%A, %B %d %Y, %H:%M")
    except Exception:
        from datetime import timedelta
        now = _utcnow()
        time_str = now.strftime("%H:%M UTC")

    raw = {}

    # Calendar: today+tomorrow, this week, month ahead
    # Pull directly from DB so we can include event_type and importance.
    try:
        from core.database import SessionLocal as _SL, CalendarEvent as _CE
        _db = _SL()
        try:
            for label, start, end in _digest_windows(now):
                # Strip timezone for naive DB comparison
                _s = start.replace(tzinfo=None) if start.tzinfo else start
                _e = end.replace(tzinfo=None) if end.tzinfo else end
                evs = _checkin_calendar_events(_db, task.owner, _s, _e)
                if not evs:
                    continue
                # Group by importance for richer output
                by_imp = {"critical": [], "high": [], "normal": [], "low": []}
                for ev in evs:
                    imp = (ev.importance or "normal").lower()
                    by_imp.setdefault(imp, []).append(ev)
                lines = []
                for tier in ("critical", "high", "normal", "low"):
                    items = by_imp.get(tier, [])
                    if not items:
                        continue
                    marker = {"critical": "[!!]", "high": "[!]", "normal": "  ", "low": " Â·"}[tier]
                    for ev in items:
                        t = ev.dtstart.strftime("%a %b %d %H:%M")
                        tag = f" ({ev.event_type})" if ev.event_type else ""
                        loc = f" @ {ev.location}" if ev.location else ""
                        lines.append(f"{marker} {t} â€” {ev.summary}{tag}{loc}")
                if lines:
                    raw[f"calendar_{label}"] = "\n".join(lines)
        finally:
            _db.close()
    except Exception as e:
        raw["calendar"] = f"Error: {e}"

    # Notes/Tasks
    try:
        _notes_argument = json.dumps({"action": "list"})
        if tool_usage_instrumentation is None:
            r = await do_manage_notes(_notes_argument, owner=task.owner)
        else:
            from src.tool_usage_instrumentation import execute_instrumented_bypass

            r = await execute_instrumented_bypass(
                tool_usage_instrumentation,
                tool_name="manage_notes",
                argument=_notes_argument,
                operation=lambda: do_manage_notes(_notes_argument, owner=task.owner),
                trusted_source="builtin",
                retry_ordinal=0,
            )
        raw["notes_tasks"] = r.get("results") or r.get("response") or "No notes"
    except Exception as e:
        raw["notes_tasks"] = f"Error: {e}"

    # Auto-discover API integrations (Miniflux RSS, etc.).
    try:
        import httpx
        from src.integrations import load_integrations
        for integ in load_integrations():
            if not integ.get("enabled"):
                continue
            preset = integ.get("preset", "")
            base_url = integ.get("base_url", "").rstrip("/")
            api_key = integ.get("api_key", "")
            if not base_url:
                continue

            # Build auth headers
            headers = {}
            if integ.get("auth_type") == "header" and api_key:
                headers[integ.get("auth_header", "X-Auth-Token")] = api_key
            elif integ.get("auth_type") == "bearer" and api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            # Miniflux: fetch unread entries (cached 3 min across tasks)
            if preset == "miniflux":
                async def _fetch_miniflux(_base=base_url, _headers=dict(headers)):
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.get(
                            f"{_base}/v1/entries",
                            params={"status": "unread", "limit": 15, "order": "published_at", "direction": "desc"},
                            headers=_headers,
                        )
                        if resp.status_code != 200:
                            return None
                        entries = resp.json().get("entries", []) or []
                        if not entries:
                            return None
                        lines = []
                        for e in entries[:15]:
                            title = e.get("title", "?")
                            feed = (e.get("feed") or {}).get("title", "?")
                            url = e.get("url", "")
                            lines.append(f"- [{feed}] {title} â€” {url}")
                        return "\n".join(lines)
                try:
                    val = await _cached(("miniflux_unread", base_url), 180, _fetch_miniflux)
                    if val:
                        raw["rss_miniflux_unread"] = val
                except Exception as e:
                    logger.warning(f"Miniflux fetch failed: {e}")
    except Exception as e:
        logger.warning(f"Integrations discovery failed: {e}")

    # Auto-discover MCP sources
    mcp = get_mcp_manager()
    if mcp:
        discovered = set()
        for server_id, tools in mcp._tools.items():
            if mcp.is_builtin(server_id):
                continue
            conn = mcp._connections.get(server_id, {})
            if conn.get("status") != "connected":
                continue
            identity = conn.get("identity", "")
            tool_names = {t["name"] for t in tools}
            for pattern in CHECKIN_MCP_PATTERNS:
                if pattern["detect"] not in tool_names:
                    continue
                key = f"{pattern['section']}_{server_id}"
                if key in discovered:
                    continue
                discovered.add(key)
                label = f"{pattern['section']} ({identity})" if identity else pattern["section"]
                qualified = f"mcp__{server_id}__{pattern['tool']}"
                args = dict(pattern.get("args", {}))
                args["account"] = "default"
                try:
                    # Cache 3 min: different scheduled tasks firing at the
                    # same minute share the same MCP snapshot.
                    async def _call_mcp(_q=qualified, _args=args):
                        if tool_usage_instrumentation is None:
                            return await mcp.call_tool(_q, _args)
                        from src.tool_usage_instrumentation import execute_instrumented_bypass

                        return await execute_instrumented_bypass(
                            tool_usage_instrumentation,
                            tool_name=_q,
                            argument=_args,
                            operation=lambda: mcp.call_tool(_q, _args),
                            trusted_source="mcp",
                            retry_ordinal=0,
                        )
                    cache_key = ("mcp_snapshot", qualified, json.dumps(args, sort_keys=True))
                    result = await _cached(cache_key, 180, _call_mcp)
                    if result.get("exit_code", 0) != 0:
                        continue
                    content = result.get("stdout") or result.get("output") or ""
                    if content.strip():
                        raw[label] = content[:3000]
                except Exception:
                    pass

    # Build the data dump and hand it to the LLM
    data_dump = f"Current time: {time_str}\n\n"
    for key, val in raw.items():
        data_dump += f"--- {key} ---\n{val}\n\n"

    context = (
        data_dump +
        f"---\n\n{task.prompt}\n\n"
        "Write the check-in. YOU decide what matters, what to skip, how to format. "
        "Only show future events. Calendar events are pre-tagged with importance: "
        "[!!] critical, [!] high, plain = normal, ' Â·' = low. "
        "GROUP your output by importance â€” lead with critical/high, then normal, "
        "skip low entirely unless explicitly relevant. Mention event type (work/health/travel/etc) "
        "where it adds context (e.g. 'leave 1h early for travel'). "
        "Flag anything coming up that needs prep (birthdays, deadlines, holidays). "
        "Use tools to take action if needed. Keep it concise â€” no raw data dumps."
    )

    return await scheduler._run_agent_loop(
        endpoint_url, model, task, session_id,
        system_prompt=(crew.personality or "").strip() if crew else None,
        disabled_tools=None, relevant_tools=None,
        override_user_message=context,
        run_id=run_id,
    )
