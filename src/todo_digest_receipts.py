"""Closed, content-free postconditions for default Todo digest membership."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from datetime import date
from typing import Any, Mapping


TODO_DIGEST_RECEIPT_SCHEMA = "odysseus.todo_digest_membership_receipt.v1"
TODO_DIGEST_RECEIPT_FIELD = "todo_digest_receipt"
_ACTIONS = frozenset({"add", "complete", "reopen", "remove"})
_REF_RE = re.compile(r"^(?:owner|list|item):[a-f0-9]{16}$|^operation:(?:add|complete|reopen|remove)$")
_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_COUNT = 1_000_000
_MAX_LIMIT = 100


def redact_ref(kind: str, value: Any) -> str | None:
    """Return the established 16-hex redaction without retaining the input."""
    if kind not in {"owner", "list", "item"} or not isinstance(value, str) or not value:
        return None
    return f"{kind}:{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def build_todo_digest_membership_receipt(
    *,
    action: Any,
    evidence_refs: Any,
    current_state: Any,
    included: Any,
    selection_position: Any,
    open_item_count: Any,
    selected_open_item_count: Any,
    limit: Any,
    label_filter_active: Any,
    list_filter_active: Any,
    builder_date: Any,
    snapshot_manifest: Any,
) -> dict[str, Any] | None:
    """Build one receipt from a content-free selection projection.

    Database access and raw Todo text/identifiers deliberately live outside this
    module.  The manifest is canonicalised here only after it has been reduced
    to redacted refs, positions, booleans, and ordering metadata.
    """
    refs = _refs(evidence_refs, action)
    state = _state(current_state)
    if refs is None or state is None or not _counts(open_item_count, selected_open_item_count, limit):
        return None
    if type(included) is not bool or label_filter_active is not False or list_filter_active is not False:
        return None
    if not _valid_date(builder_date):
        return None
    position = _position(selection_position, limit, included)
    if position is _INVALID:
        return None
    expected = _expected(action, state, included)
    if expected is None:
        return None
    digest = _snapshot_hash(
        snapshot_manifest, refs=refs, action=action, state=state, included=included,
        selection_position=position, open_item_count=open_item_count,
        selected_open_item_count=selected_open_item_count, limit=limit,
        builder_date=builder_date, label_filter_active=label_filter_active,
        list_filter_active=list_filter_active,
    )
    if digest is None:
        return None
    receipt = {
        "schema": TODO_DIGEST_RECEIPT_SCHEMA,
        "claim_type": "todo_digest_contains" if included else "todo_digest_excludes",
        "action": str(action),
        "transaction_status": "projected",
        "verified": True,
        "evidence_refs": refs,
        "current_state": {"exists": state[0], "done": state[1]},
        "included": included,
        "selection_position": position,
        "open_item_count": open_item_count,
        "selected_open_item_count": selected_open_item_count,
        "limit": limit,
        "label_filter_active": label_filter_active,
        "list_filter_active": list_filter_active,
        "builder_date": builder_date,
        "builder_clock": "naive_local",
        "snapshot_hash": digest,
        "raw_content_visible": False,
    }
    receipt_ref = _receipt_ref(receipt)
    if receipt_ref is None:
        return None
    receipt["receipt_ref"] = receipt_ref
    return receipt


def validate_todo_digest_receipt(receipt: Any, *, semantic_receipt: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Strictly validate the closed digest receipt and optional mutation bind."""
    try:
        return _validate_todo_digest_receipt_strict(receipt, semantic_receipt=semantic_receipt)
    except Exception:
        return None


def _validate_todo_digest_receipt_strict(receipt: Any, *, semantic_receipt: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if not isinstance(receipt, Mapping):
        return None
    expected_keys = {
        "schema", "claim_type", "action", "transaction_status", "verified", "evidence_refs",
        "current_state", "included", "selection_position", "open_item_count", "selected_open_item_count",
        "limit", "label_filter_active", "list_filter_active", "builder_date", "builder_clock", "snapshot_hash",
        "raw_content_visible", "receipt_ref",
    }
    if set(receipt) != expected_keys:
        return None
    action = receipt.get("action")
    refs = _refs(receipt.get("evidence_refs"), action)
    state = _state(receipt.get("current_state"))
    included = receipt.get("included")
    if refs is None or state is None or type(included) is not bool:
        return None
    if receipt.get("schema") != TODO_DIGEST_RECEIPT_SCHEMA or receipt.get("transaction_status") != "projected" or receipt.get("verified") is not True:
        return None
    if receipt.get("claim_type") != ("todo_digest_contains" if included else "todo_digest_excludes"):
        return None
    if not _counts(receipt.get("open_item_count"), receipt.get("selected_open_item_count"), receipt.get("limit")):
        return None
    if _position(receipt.get("selection_position"), receipt["limit"], included) is _INVALID:
        return None
    if receipt.get("label_filter_active") is not False or receipt.get("list_filter_active") is not False:
        return None
    if not _valid_date(receipt.get("builder_date")):
        return None
    if receipt.get("builder_clock") != "naive_local" or not isinstance(receipt.get("snapshot_hash"), str) or not _HASH_RE.fullmatch(receipt["snapshot_hash"]):
        return None
    expected_ref = _receipt_ref({key: receipt[key] for key in expected_keys if key != "receipt_ref"})
    if receipt.get("raw_content_visible") is not False or expected_ref is None or receipt.get("receipt_ref") != expected_ref:
        return None
    if _expected(action, state, included) is None:
        return None
    if semantic_receipt is not None and not _binds_semantic(receipt, semantic_receipt):
        return None
    return {key: receipt[key] for key in expected_keys}


def validated_todo_digest_receipt_from_event(event: Any) -> dict[str, Any] | None:
    """Accept a digest postcondition only inside a valid mutation event."""
    try:
        if not isinstance(event, Mapping) or event.get("tool") != "manage_todos":
            return None
        from src.todo_transaction_receipts import validated_todo_semantic_receipt_from_event
        semantic = validated_todo_semantic_receipt_from_event(event)
        if semantic is None:
            return None
        return validate_todo_digest_receipt(event.get(TODO_DIGEST_RECEIPT_FIELD), semantic_receipt=semantic)
    except Exception:
        return None


def digest_receipts_from_tool_events(events: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(events, (tuple, list)):
        return ()
    try:
        receipts = []
        for event in events[:64]:
            try:
                receipt = validated_todo_digest_receipt_from_event(event)
            except Exception:
                return ()
            if receipt is not None:
                receipts.append(receipt)
        return tuple(receipts)
    except Exception:
        return ()


def _refs(value: Any, action: Any) -> tuple[str, ...] | None:
    if action not in _ACTIONS or not isinstance(value, (tuple, list)):
        return None
    refs = tuple(value)
    if len(refs) != 4 or len(set(refs)) != 4 or any(not isinstance(ref, str) or not _REF_RE.fullmatch(ref) for ref in refs):
        return None
    if not refs[0].startswith("owner:") or not refs[1].startswith("list:") or not refs[2].startswith("item:") or refs[3] != f"operation:{action}":
        return None
    return refs


def _state(value: Any) -> tuple[bool, bool | None] | None:
    if not isinstance(value, Mapping) or set(value) != {"exists", "done"} or type(value.get("exists")) is not bool:
        return None
    done = value.get("done")
    if value["exists"]:
        return (True, done) if type(done) is bool else None
    return (False, None) if done is None else None


def _counts(open_count: Any, selected_count: Any, limit: Any) -> bool:
    return all(type(value) is int and 0 <= value <= _MAX_COUNT for value in (open_count, selected_count)) and type(limit) is int and 1 <= limit <= _MAX_LIMIT and selected_count <= min(open_count, limit)


def _position(value: Any, limit: Any, included: bool) -> int | None | object:
    if included:
        return value if type(value) is int and 0 <= value < limit else _INVALID
    return None if value is None else _INVALID


def _expected(action: Any, state: tuple[bool, bool | None], included: bool) -> bool | None:
    if action in {"add", "reopen"}:
        return True if state == (True, False) and included else None
    if action == "complete":
        return True if state == (True, True) and not included else None
    if action == "remove":
        return True if state == (False, None) and not included else None
    return None


def _snapshot_hash(manifest: Any, *, refs: tuple[str, ...], action: Any, state: tuple[bool, bool | None], included: bool, selection_position: int | None, open_item_count: int, selected_open_item_count: int, limit: int, builder_date: str, label_filter_active: bool, list_filter_active: bool) -> str | None:
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "schema", "builder_date", "builder_clock", "limit", "label_filter_active", "list_filter_active", "selected",
    }:
        return None
    if manifest.get("schema") != "odysseus.todo_digest_snapshot.v1" or manifest.get("builder_clock") != "naive_local":
        return None
    if not _valid_date(manifest.get("builder_date")) or manifest["builder_date"] != builder_date:
        return None
    if type(manifest.get("limit")) is not int or not 1 <= manifest["limit"] <= _MAX_LIMIT or manifest["limit"] != limit:
        return None
    if manifest.get("label_filter_active") is not label_filter_active or manifest.get("list_filter_active") is not list_filter_active:
        return None
    selected = manifest.get("selected")
    if not isinstance(selected, list) or len(selected) != selected_open_item_count or len(selected) > manifest["limit"]:
        return None
    seen: set[tuple[str, str]] = set()
    for position, item in enumerate(selected):
        if not isinstance(item, Mapping) or set(item) != {"list_ref", "item_ref", "position", "done"}:
            return None
        pair = (item.get("list_ref"), item.get("item_ref"))
        if not isinstance(pair[0], str) or not isinstance(pair[1], str) or not pair[0].startswith("list:") or not pair[1].startswith("item:") or not _REF_RE.fullmatch(pair[0]) or not _REF_RE.fullmatch(pair[1]):
            return None
        if pair in seen or item.get("position") != position or item.get("done") is not False:
            return None
        seen.add(pair)
    target = (refs[1], refs[2])
    if included:
        if selection_position is None or selection_position >= len(selected) or (selected[selection_position]["list_ref"], selected[selection_position]["item_ref"]) != target:
            return None
    elif target in seen:
        return None
    proof = {
        "manifest": manifest, "action": action, "evidence_refs": refs,
        "current_state": {"exists": state[0], "done": state[1]}, "included": included,
        "selection_position": selection_position, "open_item_count": open_item_count,
        "selected_open_item_count": selected_open_item_count, "limit": limit,
    }
    try:
        canonical = json.dumps(proof, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


def _binds_semantic(receipt: Mapping[str, Any], semantic: Mapping[str, Any]) -> bool:
    required = {"action", "operation", "verified", "evidence_refs"}
    if not required.issubset(semantic) or semantic.get("action") != receipt["action"] or semantic.get("operation") != receipt["action"] or semantic.get("verified") is not True:
        return False
    semantic_state = semantic.get("current_state")
    action = receipt["action"]
    expected = {"add": False, "reopen": False, "complete": True, "remove": None}[action]
    if semantic_state is not expected or tuple(semantic.get("evidence_refs", ())) != tuple(receipt["evidence_refs"]):
        return False
    return receipt["current_state"] == {"exists": action != "remove", "done": expected}


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _receipt_ref(receipt: Mapping[str, Any]) -> str | None:
    try:
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"
    except Exception:
        return None


_INVALID = object()
