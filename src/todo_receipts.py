"""Redacted semantic receipts for canonical Todo domain outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


TODO_RECEIPT_SCHEMA = "odysseus.todo_receipt.v1"

_CLAIM_BY_OPERATION = {
    "add": "todo_item_created",
    "complete": "todo_item_completed",
    "reopen": "todo_item_reopened",
    "remove": "todo_item_removed",
    "list": "todo_list_read",
}
_LIST_REF_RE = re.compile(r"^todo-list:v1:[A-Fa-f0-9]{16}:.{1,1024}$")
_ITEM_REF_RE = re.compile(r"^todo-item:v1:[A-Za-z0-9_-]{8,128}$")
_EVIDENCE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,180}$")
_SECRET_RE = re.compile(
    r"(?i)(authorization|cookie|api[_-]?key|password|passwd|secret|bearer\s+[A-Za-z0-9._-]{8,})"
)


class TodoReceiptError(ValueError):
    """Raised when a Todo receipt is malformed or privacy-bearing."""


@dataclass(frozen=True, slots=True)
class TodoReceipt:
    claim_type: str
    operation: str
    list_ref: str
    item_ref: str | None
    previous_state: dict[str, bool | None]
    current_state: dict[str, bool | None]
    open_count: int
    transaction_status: str
    verified: bool
    evidence_refs: tuple[str, ...]
    schema: str = TODO_RECEIPT_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        operation: Any,
        list_ref: Any,
        item_ref: Any,
        previous_state: Any,
        current_state: Any,
        open_count: Any,
        transaction_status: Any,
        verified: Any,
        evidence_refs: Iterable[Any],
    ) -> "TodoReceipt":
        operation_text = str(operation or "").strip().lower()
        claim_type = _CLAIM_BY_OPERATION.get(operation_text)
        if not claim_type:
            raise TodoReceiptError("unknown Todo receipt operation")
        safe_list_ref = _safe_list_ref(list_ref)
        safe_item_ref = _safe_item_ref(item_ref, required=operation_text != "list")
        previous = _safe_state(previous_state)
        current = _safe_state(current_state)
        count = _safe_open_count(open_count)
        status = _safe_status(transaction_status)
        refs = _safe_evidence_refs(evidence_refs)
        semantic_match = _semantic_postcondition_matches(operation_text, current)
        readback_present = any(ref.startswith("notes-readback:v1:") for ref in refs)
        terminal_ok = status == "read" if operation_text == "list" else status in {
            "committed",
            "idempotent",
        }
        is_verified = bool(verified is True and semantic_match and readback_present and terminal_ok)
        return cls(
            claim_type=claim_type,
            operation=operation_text,
            list_ref=safe_list_ref,
            item_ref=safe_item_ref,
            previous_state=previous,
            current_state=current,
            open_count=count,
            transaction_status=status,
            verified=is_verified,
            evidence_refs=refs,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TodoReceipt":
        if payload.get("schema") not in (None, TODO_RECEIPT_SCHEMA):
            raise TodoReceiptError("unsupported Todo receipt schema")
        return cls.create(
            operation=payload.get("operation"),
            list_ref=payload.get("list_ref"),
            item_ref=payload.get("item_ref"),
            previous_state=payload.get("previous_state"),
            current_state=payload.get("current_state"),
            open_count=payload.get("open_count"),
            transaction_status=payload.get("transaction_status"),
            verified=payload.get("verified"),
            evidence_refs=payload.get("evidence_refs") or (),
        )

    @property
    def receipt_ref(self) -> str:
        payload = json.dumps(
            {
                "schema": self.schema,
                "claim_type": self.claim_type,
                "operation": self.operation,
                "list_ref": self.list_ref,
                "item_ref": self.item_ref,
                "previous_state": self.previous_state,
                "current_state": self.current_state,
                "open_count": self.open_count,
                "transaction_status": self.transaction_status,
                "verified": self.verified,
                "evidence_refs": self.evidence_refs,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"todo-receipt:v1:{digest}"

    @property
    def ledger_evidence(self) -> tuple[str, ...]:
        refs = [self.receipt_ref, *self.evidence_refs]
        refs.append(f"list-ref:sha256:{_hash_ref(self.list_ref)}")
        if self.item_ref:
            refs.append(f"item-ref:sha256:{_hash_ref(self.item_ref)}")
        return tuple(dict.fromkeys(refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "claim_type": self.claim_type,
            "operation": self.operation,
            "list_ref": self.list_ref,
            "item_ref": self.item_ref,
            "previous_state": dict(self.previous_state),
            "current_state": dict(self.current_state),
            "open_count": self.open_count,
            "transaction_status": self.transaction_status,
            "verified": self.verified,
            "evidence_refs": list(self.evidence_refs),
            "receipt_ref": self.receipt_ref,
            "raw_content_visible": False,
        }


def todo_receipts_from_tool_result(result: Mapping[str, Any]) -> tuple[TodoReceipt, ...]:
    """Build receipts from a ``manage_todos`` result without copying item text."""
    action = str(result.get("action") or result.get("operation") or "").strip().lower()
    if action == "list":
        if result.get("exit_code") not in (0, "0", None):
            return ()
        receipts: list[TodoReceipt] = []
        for snapshot in result.get("lists") or ():
            if not isinstance(snapshot, Mapping):
                continue
            version = str(snapshot.get("version") or "").strip()
            if not version or not re.fullmatch(r"[A-Fa-f0-9]{8,64}", version):
                continue
            receipts.append(
                TodoReceipt.create(
                    operation="list",
                    list_ref=snapshot.get("list_ref"),
                    item_ref=None,
                    previous_state={"exists": None, "done": None},
                    current_state={"exists": True, "done": None},
                    open_count=snapshot.get("open_count"),
                    transaction_status="read",
                    verified=True,
                    evidence_refs=(f"notes-readback:v1:{version}",),
                )
            )
        return tuple(receipts)

    if action not in _CLAIM_BY_OPERATION or action == "list":
        return ()
    try:
        return (
            TodoReceipt.create(
                operation=action,
                list_ref=result.get("list_ref"),
                item_ref=result.get("item_ref"),
                previous_state=result.get("previous_state"),
                current_state=result.get("current_state"),
                open_count=result.get("open_count"),
                transaction_status=result.get("transaction_status"),
                verified=(
                    result.get("verified") is True
                    and result.get("exit_code") in (0, "0", None)
                ),
                evidence_refs=result.get("evidence_refs") or (),
            ),
        )
    except TodoReceiptError:
        return ()


def todo_receipts_from_tool_events(
    tool_events: Iterable[Mapping[str, Any]],
) -> tuple[TodoReceipt, ...]:
    receipts: list[TodoReceipt] = []
    seen: set[str] = set()
    for event in tool_events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("tool") or "").strip() != "manage_todos":
            continue
        payloads: list[Any] = []
        if isinstance(event.get("todo_receipt"), Mapping):
            payloads.append(event["todo_receipt"])
        if isinstance(event.get("todo_receipts"), (list, tuple)):
            payloads.extend(event["todo_receipts"])
        for payload in payloads:
            if not isinstance(payload, Mapping):
                continue
            try:
                receipt = TodoReceipt.from_mapping(payload)
            except TodoReceiptError:
                continue
            if event.get("exit_code") not in (0, "0", None) and receipt.verified:
                receipt = TodoReceipt.create(
                    operation=receipt.operation,
                    list_ref=receipt.list_ref,
                    item_ref=receipt.item_ref,
                    previous_state=receipt.previous_state,
                    current_state=receipt.current_state,
                    open_count=receipt.open_count,
                    transaction_status=receipt.transaction_status,
                    verified=False,
                    evidence_refs=receipt.evidence_refs,
                )
            if receipt.receipt_ref not in seen:
                seen.add(receipt.receipt_ref)
                receipts.append(receipt)
    return tuple(receipts)


def todo_receipt_evidence_for_claim(
    receipts: Iterable[TodoReceipt], claim_type: str
) -> tuple[str, ...]:
    refs: list[str] = []
    for receipt in receipts:
        if receipt.claim_type != claim_type or not receipt.verified:
            continue
        refs.extend((receipt.receipt_ref, receipt.list_ref))
        if receipt.item_ref:
            refs.append(receipt.item_ref)
        refs.extend(receipt.evidence_refs)
    return tuple(dict.fromkeys(refs))


def render_todo_receipt_response(receipts: Iterable[TodoReceipt]) -> str:
    """Render a content-free final status from semantic receipts only."""
    items = tuple(receipts)
    if not items:
        return ""
    verified = tuple(receipt for receipt in items if receipt.verified)
    if not verified:
        return "Todo-Aktion nicht verifiziert; es wird kein Erfolgsstatus aus freier Modellprosa uebernommen."
    latest = verified[-1]
    if latest.claim_type == "todo_list_read":
        list_receipts = tuple(item for item in verified if item.claim_type == "todo_list_read")
        open_count = sum(item.open_count for item in list_receipts)
        return f"Todo-Liste verifiziert gelesen. Offene Todos: {open_count}."
    labels = {
        "todo_item_created": "Todo verifiziert gespeichert",
        "todo_item_completed": "Todo verifiziert erledigt",
        "todo_item_reopened": "Todo verifiziert wieder geoeffnet",
        "todo_item_removed": "Todo verifiziert entfernt",
    }
    return f"{labels[latest.claim_type]}. Referenz: `{latest.item_ref}`. Offene Todos: {latest.open_count}."


def _safe_list_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not _LIST_REF_RE.fullmatch(text) or _SECRET_RE.search(text):
        raise TodoReceiptError("invalid Todo list reference")
    return text


def _safe_item_ref(value: Any, *, required: bool) -> str | None:
    if value in (None, "") and not required:
        return None
    text = str(value or "").strip()
    if not _ITEM_REF_RE.fullmatch(text):
        raise TodoReceiptError("invalid Todo item reference")
    return text


def _safe_state(value: Any) -> dict[str, bool | None]:
    if not isinstance(value, Mapping) or set(value) != {"exists", "done"}:
        raise TodoReceiptError("Todo state must contain only exists and done")
    exists = value.get("exists")
    done = value.get("done")
    if not (exists is True or exists is False or exists is None):
        raise TodoReceiptError("Todo state values must be bool or null")
    if not (done is True or done is False or done is None):
        raise TodoReceiptError("Todo state values must be bool or null")
    if exists is False and done is not None:
        raise TodoReceiptError("non-existing Todo state cannot carry done")
    return {"exists": exists, "done": done}


def _safe_open_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        raise TodoReceiptError("open_count must be a bounded non-negative integer")
    return value


def _safe_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in {"read", "committed", "idempotent", "ambiguous", "not_found", "rejected", "failed", "blocked"}:
        raise TodoReceiptError("invalid Todo transaction status")
    return text


def _safe_evidence_refs(values: Iterable[Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or not _EVIDENCE_REF_RE.fullmatch(text) or _SECRET_RE.search(text):
            raise TodoReceiptError("unsafe Todo evidence reference")
        refs.append(text)
    return tuple(dict.fromkeys(refs))


def _semantic_postcondition_matches(
    operation: str, current_state: Mapping[str, bool | None]
) -> bool:
    if operation == "list":
        return current_state == {"exists": True, "done": None}
    if operation == "add":
        return current_state.get("exists") is True and current_state.get("done") is False
    if operation == "complete":
        return current_state == {"exists": True, "done": True}
    if operation == "reopen":
        return current_state == {"exists": True, "done": False}
    if operation == "remove":
        return current_state == {"exists": False, "done": None}
    return False


def _hash_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "TODO_RECEIPT_SCHEMA",
    "TodoReceipt",
    "TodoReceiptError",
    "render_todo_receipt_response",
    "todo_receipt_evidence_for_claim",
    "todo_receipts_from_tool_events",
    "todo_receipts_from_tool_result",
]
