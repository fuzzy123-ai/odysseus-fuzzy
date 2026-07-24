"""Safe tool facade for the owner-scoped Todo domain service."""
from __future__ import annotations

from typing import Any, Optional

from src.tool_domains.common import _parse_tool_args
from src.todo_transaction_receipts import attach_todo_semantic_receipt
from src.todo_domain_service import (
    TodoAmbiguousMatchError,
    TodoConflictError,
    TodoDataError,
    TodoDomainError,
    TodoIdempotencyConflictError,
    TodoNotFoundError,
    TodoValidationError,
    TodoDomainService,
)

_ACTION_ALIASES = {
    "create": "add",
    "delete": "remove",
    "done": "complete",
    "finish": "complete",
    "new": "add",
    "uncomplete": "reopen",
    "undo": "reopen",
}
_SUPPORTED_ACTIONS = frozenset({"list", "add", "complete", "reopen", "remove"})


def _service_factory():
    return TodoDomainService.from_core_database()


def _error(code: str, **extra: Any) -> dict[str, Any]:
    return {"status": "rejected", "error": code, "error_code": code, "exit_code": 1, **extra}


async def do_manage_todos(content: str, owner: Optional[str] = None) -> dict[str, Any]:
    """Call the Todo service without exposing checklist item text in receipts."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return _error("invalid_arguments")
    if not isinstance(args, dict):
        return _error("invalid_arguments")

    requested_action = str(args.get("action") or "").strip().lower().replace("-", "_")
    action = _ACTION_ALIASES.get(requested_action, requested_action)
    if action not in _SUPPORTED_ACTIONS:
        return _error("invalid_action")

    if action == "remove" and not _is_confirmed(args):
        return {
            "status": "confirmation_required",
            "requires_confirmation": True,
            "action": "remove",
            "exit_code": 0,
        }

    list_ref = args.get("list_ref")
    try:
        service = _service_factory()
        if action == "list":
            result = service.list(owner=owner, list_ref=list_ref)
        elif action == "add":
            result = service.add(
                owner=owner,
                list_ref=list_ref,
                text=args.get("text"),
                idempotency_key=args.get("idempotency_key"),
            )
        else:
            method = getattr(service, action)
            result = method(
                owner=owner,
                list_ref=list_ref,
                item_ref=args.get("item_ref"),
                text=args.get("text"),
            )
        payload = result.to_dict()
        return attach_todo_semantic_receipt(
            {"status": "ok", "action": action, "exit_code": 0, **payload},
            action,
            owner=owner,
            list_ref=list_ref,
        )
    except Exception as exc:
        return _public_error_for(exc)


def _is_confirmed(args: dict[str, Any]) -> bool:
    """Only a JSON boolean confirmation authorizes a destructive mutation."""
    return args.get("confirmed") is True or args.get("confirm") is True


def _public_error_for(exc: Exception) -> dict[str, Any]:
    """Map service failures to stable, content-free tool errors."""
    if isinstance(exc, TodoAmbiguousMatchError):
        return _error("ambiguous_item", candidate_refs=list(exc.candidate_refs))

    for error_type, code in (
        (TodoValidationError, "invalid_arguments"),
        (TodoNotFoundError, "not_found"),
        (TodoIdempotencyConflictError, "idempotency_conflict"),
        (TodoConflictError, "conflict"),
        (TodoDataError, "invalid_data"),
        (TodoDomainError, "todo_error"),
    ):
        if isinstance(exc, error_type):
            return _error(code)
    return _error("todo_error")
