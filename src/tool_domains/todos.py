"""Agent-facing Todo facade backed exclusively by owner-scoped Notes."""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional

from sqlalchemy.exc import IntegrityError

from core.database import Note, SessionLocal
from src.todo_domain_service import (
    TodoDomainError,
    TodoDomainService,
    TodoListSnapshot,
    make_list_ref,
)
from src.todo_intent import normalize_todo_match_text
from src.tool_domains.common import _parse_tool_args


_SESSION_FACTORY = SessionLocal
_DEFAULT_LIST_TITLE = "Todos"


def _error(message: str, *, status: str = "rejected", **extra) -> Dict:
    return {
        "error": message,
        "status": status,
        "domain": "todos",
        "exit_code": 1,
        **extra,
    }


def _active_list_refs(owner: str) -> list[str]:
    db = _SESSION_FACTORY()
    try:
        notes = (
            db.query(Note)
            .filter(
                Note.owner == owner,
                Note.note_type == "checklist",
                Note.archived.is_(False),
            )
            .order_by(Note.created_at.asc(), Note.id.asc())
            .all()
        )
        return [make_list_ref(owner, note.id) for note in notes]
    finally:
        db.close()


def _default_note_id(owner: str) -> str:
    digest = hashlib.sha256(f"todo-default-list:v1\0{owner}".encode("utf-8")).hexdigest()[:32]
    return f"todo-default-{digest}"


def _ensure_default_list(owner: str) -> str:
    note_id = _default_note_id(owner)
    db = _SESSION_FACTORY()
    try:
        existing = (
            db.query(Note)
            .filter(Note.id == note_id, Note.owner == owner)
            .first()
        )
        if existing is None:
            db.add(
                Note(
                    id=note_id,
                    owner=owner,
                    title=_DEFAULT_LIST_TITLE,
                    items="[]",
                    note_type="checklist",
                    archived=False,
                    source="agent",
                )
            )
            try:
                db.commit()
            except IntegrityError:
                # A concurrent first add may have created the same deterministic
                # owner-scoped list. Re-read and accept only an exact owner match.
                db.rollback()
                existing = (
                    db.query(Note)
                    .filter(Note.id == note_id, Note.owner == owner)
                    .first()
                )
                if existing is None:
                    raise
        return make_list_ref(owner, note_id)
    finally:
        db.close()


def _snapshots(service: TodoDomainService, owner: str) -> list[TodoListSnapshot]:
    return [
        service.list_items(owner=owner, list_ref=list_ref)
        for list_ref in _active_list_refs(owner)
    ]


def _resolve_list_by_title(
    snapshots: list[TodoListSnapshot], list_title: str
) -> tuple[str | None, Dict | None]:
    normalized = normalize_todo_match_text(list_title)
    matches = [
        snapshot for snapshot in snapshots
        if normalize_todo_match_text(snapshot.title) == normalized
    ]
    if len(matches) == 1:
        return matches[0].list_ref, None
    if len(matches) > 1:
        return None, _error(
            "Todo list title is ambiguous; retry with a stable list_ref",
            status="ambiguous",
            candidate_refs=[snapshot.list_ref for snapshot in matches],
        )
    return None, _error("Todo list not found", status="not_found")


def _resolve_target_list(
    *,
    snapshots: list[TodoListSnapshot],
    item_ref: str | None,
    text: str | None,
) -> tuple[str | None, Dict | None]:
    if (item_ref is None) == (text is None):
        return None, _error("Provide exactly one of item_ref or text")
    if item_ref is not None:
        matches = [
            (snapshot, item.item_ref)
            for snapshot in snapshots
            for item in snapshot.items
            if item.item_ref == item_ref
        ]
    else:
        normalized = normalize_todo_match_text(text or "")
        matches = [
            (snapshot, item.item_ref)
            for snapshot in snapshots
            for item in snapshot.items
            if normalize_todo_match_text(item.text) == normalized
        ]
    if len(matches) == 1:
        return matches[0][0].list_ref, None
    if len(matches) > 1:
        candidate_refs = sorted({item_ref for _snapshot, item_ref in matches})
        return None, _error(
            "Todo item is ambiguous; retry with stable list_ref and item_ref",
            status="ambiguous",
            candidate_refs=candidate_refs,
        )
    if len(snapshots) == 1:
        # Let the canonical service return a content-free not_found outcome.
        return snapshots[0].list_ref, None
    return None, _error("Todo item not found", status="not_found")


async def do_manage_todos(content: str, owner: Optional[str] = None) -> Dict:
    """List and mutate Todo items through ``TodoDomainService`` only."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return _error("Invalid JSON arguments")
    if not isinstance(owner, str) or not owner.strip():
        return _error("An authenticated owner scope is required")

    action = str(args.get("action") or "").replace("-", "_").strip().lower()
    action = {
        "create": "add",
        "new": "add",
        "done": "complete",
        "comlete": "complete",
        "complte": "complete",
        "uncomplete": "reopen",
        "reopn": "reopen",
        "delete": "remove",
        "remve": "remove",
    }.get(action, action)
    if action not in {"list", "add", "complete", "reopen", "remove"}:
        return _error("Unknown action; use list/add/complete/reopen/remove")

    service = TodoDomainService(_SESSION_FACTORY)
    try:
        explicit_list_ref = args.get("list_ref")
        list_title = args.get("list_title")
        snapshots = _snapshots(service, owner)

        if action == "list":
            if explicit_list_ref:
                snapshots = [service.list_items(owner=owner, list_ref=explicit_list_ref)]
            elif list_title:
                resolved, problem = _resolve_list_by_title(snapshots, str(list_title))
                if problem:
                    return problem
                snapshots = [service.list_items(owner=owner, list_ref=resolved or "")]
            return {
                "action": "list",
                "domain": "todos",
                "lists": [snapshot.as_dict() for snapshot in snapshots],
                "list_count": len(snapshots),
                "open_count": sum(snapshot.open_count for snapshot in snapshots),
                "exit_code": 0,
            }

        idempotency_key = args.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return _error("idempotency_key is required for Todo mutations")

        if explicit_list_ref:
            list_ref = str(explicit_list_ref)
            service.list_items(owner=owner, list_ref=list_ref)
        elif list_title:
            list_ref, problem = _resolve_list_by_title(snapshots, str(list_title))
            if problem:
                return problem
        elif action == "add":
            if len(snapshots) == 1:
                list_ref = snapshots[0].list_ref
            elif not snapshots:
                list_ref = _ensure_default_list(owner)
            else:
                return _error(
                    "Multiple Todo lists exist; retry with a stable list_ref",
                    status="ambiguous",
                    candidate_refs=[snapshot.list_ref for snapshot in snapshots],
                )
        else:
            list_ref, problem = _resolve_target_list(
                snapshots=snapshots,
                item_ref=args.get("item_ref"),
                text=args.get("text"),
            )
            if problem:
                return problem

        if action == "add":
            outcome = service.add_item(
                owner=owner,
                list_ref=list_ref or "",
                text=args.get("text") or "",
                idempotency_key=idempotency_key,
            )
        else:
            operation = {
                "complete": service.complete_item,
                "reopen": service.reopen_item,
                "remove": service.remove_item,
            }[action]
            outcome = operation(
                owner=owner,
                list_ref=list_ref or "",
                item_ref=args.get("item_ref"),
                text=args.get("text"),
                idempotency_key=idempotency_key,
            )
        result = outcome.as_dict()
        result.update(
            action=action,
            domain="todos",
            exit_code=(
                0 if outcome.transaction_status in {"committed", "idempotent"} else 1
            ),
        )
        if result["exit_code"]:
            result["error"] = f"Todo mutation {outcome.transaction_status}; no success claim is allowed"
        return result
    except TodoDomainError as exc:
        return _error(str(exc))


__all__ = ["do_manage_todos"]
