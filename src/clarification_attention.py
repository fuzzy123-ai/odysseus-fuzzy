"""Canonical read models for pending clarification attention."""

from __future__ import annotations

from typing import Any, Mapping


CLARIFICATION_ATTENTION_SCHEMA = "odysseus.clarification_attention.v1"
CLARIFICATION_WORKSPACE_STATUS_SCHEMA = "odysseus.clarification_workspace_status.v1"
_OPEN_STATUSES = {"clarifying", "understanding_review", "paused", "blocked"}


def build_session_clarification_attention(
    *,
    owner: str | None,
    session_id: str | None,
    store: Any = None,
) -> dict[str, Any]:
    if not session_id:
        return _empty_attention()
    try:
        if store is None:
            from src.clarification_store import ClarificationStore

            store = ClarificationStore()
        run = store.read_active_run_for_session(owner=owner or "local", session_id=session_id)
    except Exception:
        return {**_empty_attention(), "state": "unknown", "provider_failed": True}
    if not isinstance(run, Mapping):
        return _empty_attention()
    return _attention_from_run(run)


def build_workspace_clarification_status(
    *,
    owner: str | None = None,
    store: Any = None,
    limit: int = 25,
) -> dict[str, Any]:
    try:
        if store is None:
            from src.clarification_store import ClarificationStore

            store = ClarificationStore()
        runs = tuple(store.list_active_runs(owner=owner, limit=limit))
    except Exception:
        return {
            "schema": CLARIFICATION_WORKSPACE_STATUS_SCHEMA,
            "state": "partial",
            "status": "unknown",
            "summary": "clarification status unavailable",
            "provider_failed": True,
            "raw_content_visible": False,
        }
    attentions = tuple(_attention_from_run(run) for run in runs)
    open_items = tuple(item for item in attentions if item["active"])
    unresolved = sum(int(item.get("unresolved_required_count") or 0) for item in open_items)
    status = "pending" if open_items else "clear"
    return {
        "schema": CLARIFICATION_WORKSPACE_STATUS_SCHEMA,
        "state": "live",
        "status": status,
        "summary": f"{len(open_items)} active clarification run(s), {unresolved} required answer(s) pending.",
        "pending_count": unresolved,
        "active_run_count": len(open_items),
        "attention_items": [
            {
                "clarification_id": item["clarification_id"],
                "session_id": item["session_id"],
                "status": item["status"],
                "unresolved_required_count": item["unresolved_required_count"],
                "ready_for_plan": item["ready_for_plan"],
            }
            for item in open_items[:10]
        ],
        "raw_content_visible": False,
        "private_content_visible": False,
    }


def _attention_from_run(run: Mapping[str, Any]) -> dict[str, Any]:
    status = str(run.get("status") or "")
    unresolved = int(run.get("unresolved_required_count") or 0)
    ready = bool(run.get("ready_for_plan"))
    active = status in _OPEN_STATUSES and (unresolved > 0 or not ready)
    return {
        "schema": CLARIFICATION_ATTENTION_SCHEMA,
        "active": active,
        "state": "attention" if active else "clear",
        "reason": "clarification_required" if active else "",
        "message": "Waiting for required clarification input" if active else "",
        "clarification_id": str(run.get("clarification_id") or ""),
        "session_id": str(run.get("session_id") or ""),
        "status": status,
        "unresolved_required_count": unresolved,
        "ready_for_plan": ready,
        "raw_content_visible": False,
    }


def _empty_attention() -> dict[str, Any]:
    return {
        "schema": CLARIFICATION_ATTENTION_SCHEMA,
        "active": False,
        "state": "clear",
        "reason": "",
        "message": "",
        "clarification_id": "",
        "session_id": "",
        "status": "",
        "unresolved_required_count": 0,
        "ready_for_plan": False,
        "raw_content_visible": False,
    }
