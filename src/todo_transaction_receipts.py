"""Content-free semantic receipts for accepted ``manage_todos`` operations."""

from __future__ import annotations

from hashlib import sha256
import re
from typing import Any, Mapping


TODO_SEMANTIC_RECEIPT_SCHEMA = "odysseus.todo_semantic_receipt.v1"
TODO_RECEIPT_FIELD = "todo_semantic_receipt"
TODO_TOOL_NAME = "manage_todos"

_ACTIONS = frozenset({"list", "add", "complete", "reopen", "remove"})
_MUTATION_CLAIMS = {
    "add": "todo_item_created",
    "complete": "todo_item_completed",
    "reopen": "todo_item_reopened",
    "remove": "todo_item_removed",
}
_MAX_OPEN_COUNT = 1_000_000
_REDACTED_REF_RE = re.compile(
    r"^(?:owner|list|item):[a-f0-9]{16}$|^operation:(?:add|complete|reopen|remove|list)$"
)
_MAX_RAW_IDENTIFIER_CHARS = 256
_HISTORY_STATUSES = frozenset({"rejected", "confirmation_required", "invalid"})


def project_todo_semantic_receipt(
    action: Any, result: Mapping[str, Any] | Any
) -> dict[str, Any] | None:
    """Project a service result into the one closed Todo receipt shape.

    The projection intentionally omits owner IDs, list/item IDs, item text,
    exceptions, and any other raw result values.
    """
    canonical_action = _canonical_action(action)
    if canonical_action is None or not isinstance(result, Mapping):
        return None
    if result.get("status") != "ok" or result.get("action") != canonical_action:
        return None
    if type(result.get("exit_code")) is not int or result["exit_code"] != 0:
        return None

    open_count = _open_count(result.get("open_count"))
    if open_count is None:
        return None
    if canonical_action == "list":
        evidence_refs = _list_evidence_refs(result.get("evidence_refs_redacted"))
        if evidence_refs is None:
            return None
        return {
            "schema": TODO_SEMANTIC_RECEIPT_SCHEMA,
            "action": "list",
            "operation": "list",
            "claim_type": "todo_list_read",
            "verified": True,
            "transaction_status": "read_verified",
            "open_count": open_count,
            "previous_state": None,
            "current_state": None,
            "evidence_refs": evidence_refs,
        }

    if result.get("operation") != canonical_action or result.get("verified") is not True:
        return None
    previous_state = _state(result.get("previous_state"))
    current_state = _state(result.get("current_state"))
    if previous_state is _INVALID_STATE or current_state is _INVALID_STATE:
        return None
    if not _status_and_states_match(
        canonical_action, result.get("transaction_status"), previous_state, current_state
    ):
        return None
    evidence_refs = _redacted_evidence_refs(result.get("evidence_refs_redacted"), canonical_action)
    if evidence_refs is None:
        return None
    return {
        "schema": TODO_SEMANTIC_RECEIPT_SCHEMA,
        "action": canonical_action,
        "operation": canonical_action,
        "claim_type": _MUTATION_CLAIMS[canonical_action],
        "verified": True,
        "transaction_status": result["transaction_status"],
        "open_count": open_count,
        "previous_state": previous_state,
        "current_state": current_state,
        "evidence_refs": evidence_refs,
    }


def attach_todo_semantic_receipt(
    result: Mapping[str, Any],
    action: Any,
    *,
    owner: Any = None,
    list_ref: Any = None,
) -> dict[str, Any]:
    """Return the facade result with a receipt only when projection is valid."""
    attached = dict(result)
    if _canonical_action(action) == "list":
        snapshot_list_ref = attached.get("list_ref")
        evidence_refs = (
            _facade_list_evidence_refs(owner=owner, list_ref=snapshot_list_ref)
            if snapshot_list_ref == list_ref
            else None
        )
        if evidence_refs is not None:
            attached["evidence_refs_redacted"] = evidence_refs
    receipt = project_todo_semantic_receipt(action, attached)
    if receipt is not None:
        attached[TODO_RECEIPT_FIELD] = receipt
    return attached


def validated_todo_semantic_receipt_from_event(
    event: Mapping[str, Any] | Any
) -> dict[str, Any] | None:
    """Accept only a narrow, action-matching Todo semantic event envelope."""
    if not isinstance(event, Mapping) or event.get("tool") != TODO_TOOL_NAME:
        return None
    action = _canonical_action(event.get("action"))
    receipt = event.get(TODO_RECEIPT_FIELD)
    if action is None or not isinstance(receipt, Mapping):
        return None
    return _validate_semantic_receipt(action, receipt)


def todo_semantic_event(result: Mapping[str, Any] | Any) -> dict[str, Any] | None:
    """Build the only Todo event forwarded by the agent loop."""
    if not isinstance(result, Mapping):
        return None
    action = _canonical_action(result.get("action"))
    receipt = result.get(TODO_RECEIPT_FIELD)
    if action is None or not isinstance(receipt, Mapping):
        return None
    validated = _validate_semantic_receipt(action, receipt)
    if validated is None:
        return None
    event = {
        "tool": TODO_TOOL_NAME,
        "action": action,
        TODO_RECEIPT_FIELD: validated,
    }
    # Digest membership is an optional, separately validated postcondition.
    # Invalid extras are omitted so the established semantic event remains
    # backwards-compatible and content-free.
    if action in _MUTATION_CLAIMS:
        try:
            from src.todo_digest_receipts import TODO_DIGEST_RECEIPT_FIELD, validate_todo_digest_receipt
            digest = validate_todo_digest_receipt(
                result.get(TODO_DIGEST_RECEIPT_FIELD), semantic_receipt=validated
            )
            if digest is not None:
                event[TODO_DIGEST_RECEIPT_FIELD] = digest
        except Exception:
            pass
    return event


def todo_safe_history_event(result: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Return the closed history envelope for every Todo tool outcome.

    Invalid, rejected, or confirmation-gated responses are intentionally not
    receipts.  Their event never carries command text, output, exception text,
    selectors, or raw identifiers.
    """
    semantic = todo_semantic_event(result)
    if semantic is not None:
        return semantic
    action = _canonical_action(result.get("action")) if isinstance(result, Mapping) else None
    status = str(result.get("status") or "").strip().lower() if isinstance(result, Mapping) else ""
    return {
        "tool": TODO_TOOL_NAME,
        "action": action or "",
        "semantic_status": status if status in _HISTORY_STATUSES else "invalid",
    }


def _validate_semantic_receipt(
    action: str, receipt: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Validate an already-redacted envelope without re-reading raw results."""
    expected_claim = "todo_list_read" if action == "list" else _MUTATION_CLAIMS[action]
    expected_keys = {
        "schema",
        "action",
        "operation",
        "claim_type",
        "verified",
        "transaction_status",
        "open_count",
        "previous_state",
        "current_state",
        "evidence_refs",
    }
    if set(receipt) != expected_keys:
        return None
    if (
        receipt.get("schema") != TODO_SEMANTIC_RECEIPT_SCHEMA
        or receipt.get("action") != action
        or receipt.get("operation") != action
        or receipt.get("claim_type") != expected_claim
        or receipt.get("verified") is not True
    ):
        return None
    open_count = _open_count(receipt.get("open_count"))
    if open_count is None:
        return None
    previous_state = _state(receipt.get("previous_state"))
    current_state = _state(receipt.get("current_state"))
    if previous_state is _INVALID_STATE or current_state is _INVALID_STATE:
        return None
    if action == "list":
        if (
            receipt.get("transaction_status") != "read_verified"
            or previous_state is not None
            or current_state is not None
            or _list_evidence_refs(receipt.get("evidence_refs")) is None
        ):
            return None
    else:
        if not _status_and_states_match(
            action, receipt.get("transaction_status"), previous_state, current_state
        ):
            return None
        if _redacted_evidence_refs(receipt.get("evidence_refs"), action) is None:
            return None
    return {
        "schema": TODO_SEMANTIC_RECEIPT_SCHEMA,
        "action": action,
        "operation": action,
        "claim_type": expected_claim,
        "verified": True,
        "transaction_status": receipt["transaction_status"],
        "open_count": open_count,
        "previous_state": previous_state,
        "current_state": current_state,
        "evidence_refs": tuple(receipt["evidence_refs"]),
    }


def _canonical_action(value: Any) -> str | None:
    action = str(value or "").strip().lower()
    return action if action in _ACTIONS else None


def _open_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= _MAX_OPEN_COUNT else None


_INVALID_STATE = object()


def _state(value: Any) -> bool | None | object:
    return value if value is None or isinstance(value, bool) else _INVALID_STATE


def _status_and_states_match(
    action: str, transaction_status: Any, previous: bool | None, current: bool | None
) -> bool:
    if action == "add":
        if transaction_status == "committed":
            return previous is None and current is False
        return transaction_status == "idempotent_noop" and previous is None and isinstance(current, bool)
    if transaction_status != "committed":
        return False
    if action == "complete":
        return isinstance(previous, bool) and current is True
    if action == "reopen":
        return isinstance(previous, bool) and current is False
    return action == "remove" and current is None and isinstance(previous, bool)


def _redacted_evidence_refs(value: Any, action: str) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    refs = tuple(value)
    if any(not isinstance(ref, str) or not _REDACTED_REF_RE.fullmatch(ref) for ref in refs):
        return None
    if len(set(refs)) != len(refs):
        return None
    if len(refs) != 4:
        return None
    if (
        not refs[0].startswith("owner:")
        or not refs[1].startswith("list:")
        or not refs[2].startswith("item:")
        or refs[3] != f"operation:{action}"
    ):
        return None
    return refs


def _list_evidence_refs(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    refs = tuple(value)
    if len(refs) != 3 or len(set(refs)) != 3:
        return None
    if any(not isinstance(ref, str) or not _REDACTED_REF_RE.fullmatch(ref) for ref in refs):
        return None
    if not refs[0].startswith("owner:") or not refs[1].startswith("list:") or refs[2] != "operation:list":
        return None
    return refs


def _facade_list_evidence_refs(*, owner: Any, list_ref: Any) -> tuple[str, ...] | None:
    if not _raw_owner_is_safe(owner) or not _raw_identifier_is_safe(list_ref):
        return None
    owner_text = owner if owner is not None else "<null>"
    return (
        _redact_ref("owner", owner_text),
        _redact_ref("list", list_ref),
        "operation:list",
    )


def _raw_owner_is_safe(value: Any) -> bool:
    return value is None or _raw_identifier_is_safe(value)


def _raw_identifier_is_safe(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_RAW_IDENTIFIER_CHARS
        and value.strip() == value
    )


def _redact_ref(kind: str, value: str) -> str:
    return f"{kind}:{sha256(value.encode('utf-8')).hexdigest()[:16]}"
