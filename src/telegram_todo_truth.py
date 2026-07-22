"""Redacted Todo evidence envelope for Telegram agent-turn bridges."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.todo_digest_receipts import (
    TodoDigestReceipt,
    TodoDigestReceiptError,
    todo_digest_receipts_from_tool_events,
)
from src.todo_receipts import TodoReceipt, TodoReceiptError, todo_receipts_from_tool_events
from src.tool_transaction_ledger import transactions_from_tool_events


TELEGRAM_TODO_TRUTH_ENVELOPE_SCHEMA = "odysseus.telegram_todo_truth_envelope.v1"


def build_telegram_todo_truth_envelope(
    tool_events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    events = tuple(
        event
        for event in tool_events
        if isinstance(event, Mapping)
        and str(event.get("tool") or "").strip() == "manage_todos"
    )
    postconditions = todo_receipts_from_tool_events(events)
    digest_postconditions = todo_digest_receipts_from_tool_events(events)
    transactions = tuple(
        tx
        for tx in transactions_from_tool_events(events, surface="telegram_bridge")
        if tx.claim_type.startswith("todo_")
    )
    starts: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for sequence, event in enumerate(events):
        event_receipts = todo_receipts_from_tool_events((event,))
        event_digest_receipts = todo_digest_receipts_from_tool_events((event,))
        starts.append(
            {
                "sequence": sequence,
                "tool": "manage_todos",
                "observed": True,
            }
        )
        outputs.append(
            {
                "sequence": sequence,
                "tool": "manage_todos",
                "exit_code": _safe_exit_code(event.get("exit_code")),
                "receipt_refs": [receipt.receipt_ref for receipt in event_receipts],
                "receipt_count": len(event_receipts),
                "digest_receipt_refs": [
                    receipt.receipt_ref for receipt in event_digest_receipts
                ],
                "digest_receipt_count": len(event_digest_receipts),
            }
        )
    envelope = {
        "schema": TELEGRAM_TODO_TRUTH_ENVELOPE_SCHEMA,
        "tool_starts": starts,
        "tool_outputs": outputs,
        "transactions": [tx.to_dict() for tx in transactions],
        "postconditions": [receipt.to_dict() for receipt in postconditions],
        "counts": {
            "tool_starts": len(starts),
            "tool_outputs": len(outputs),
            "transactions": len(transactions),
            "postconditions": len(postconditions),
        },
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
    }
    if digest_postconditions:
        envelope["digest_postconditions"] = [
            receipt.to_dict() for receipt in digest_postconditions
        ]
        envelope["counts"]["digest_postconditions"] = len(digest_postconditions)
    return envelope


def telegram_todo_truth_envelope_has_evidence(envelope: Any) -> bool:
    if not isinstance(envelope, Mapping):
        return False
    if envelope.get("schema") != TELEGRAM_TODO_TRUTH_ENVELOPE_SCHEMA:
        return False
    counts = envelope.get("counts")
    return bool(
        isinstance(counts, Mapping)
        and _bounded_count(counts.get("postconditions")) > 0
        and envelope.get("raw_content_visible") is False
        and envelope.get("raw_identifiers_visible") is False
    )


def tool_events_from_telegram_todo_truth_envelope(
    envelope: Any,
) -> tuple[dict[str, Any], ...]:
    """Validate an envelope and reconstruct only redacted gate events."""
    if not telegram_todo_truth_envelope_has_evidence(envelope):
        return ()
    payloads = envelope.get("postconditions")
    outputs = envelope.get("tool_outputs")
    starts = envelope.get("tool_starts")
    transactions = envelope.get("transactions")
    digest_payloads = envelope.get("digest_postconditions") or ()
    counts = envelope.get("counts")
    if not isinstance(payloads, (list, tuple)) or not isinstance(outputs, (list, tuple)):
        return ()
    if not isinstance(starts, (list, tuple)) or not isinstance(transactions, (list, tuple)):
        return ()
    if not isinstance(digest_payloads, (list, tuple)):
        return ()
    if not isinstance(counts, Mapping) or len(starts) != len(outputs):
        return ()
    if (
        _bounded_count(counts.get("tool_starts")) != len(starts)
        or _bounded_count(counts.get("tool_outputs")) != len(outputs)
        or _bounded_count(counts.get("transactions")) != len(transactions)
        or _bounded_count(counts.get("postconditions")) != len(payloads)
    ):
        return ()
    if _bounded_count(counts.get("digest_postconditions")) != len(digest_payloads):
        return ()

    receipts_by_ref: dict[str, TodoReceipt] = {}
    for payload in payloads:
        if not isinstance(payload, Mapping):
            return ()
        try:
            receipt = TodoReceipt.from_mapping(payload)
        except TodoReceiptError:
            return ()
        receipts_by_ref[receipt.receipt_ref] = receipt

    transaction_receipt_refs: set[str] = set()
    for transaction in transactions:
        if not isinstance(transaction, Mapping):
            return ()
        claim_type = str(transaction.get("claim_type") or "")
        if (
            transaction.get("tool") != "manage_todos"
            or not claim_type.startswith("todo_")
            or transaction.get("raw_content_visible") is not False
        ):
            return ()
        evidence_refs = transaction.get("evidence_refs")
        if not isinstance(evidence_refs, (list, tuple)):
            return ()
        matching_receipts = [
            receipt
            for receipt in receipts_by_ref.values()
            if receipt.receipt_ref in evidence_refs
        ]
        if len(matching_receipts) != 1:
            return ()
        receipt = matching_receipts[0]
        if claim_type != receipt.claim_type or bool(transaction.get("verified_done")) != receipt.verified:
            return ()
        transaction_receipt_refs.add(receipt.receipt_ref)
    if transaction_receipt_refs != set(receipts_by_ref):
        return ()

    digest_receipts_by_ref: dict[str, TodoDigestReceipt] = {}
    for payload in digest_payloads:
        if not isinstance(payload, Mapping):
            return ()
        try:
            receipt = TodoDigestReceipt.from_mapping(payload)
        except TodoDigestReceiptError:
            return ()
        digest_receipts_by_ref[receipt.receipt_ref] = receipt

    reconstructed: list[dict[str, Any]] = []
    seen_sequences: set[int] = set()
    for output in outputs:
        if not isinstance(output, Mapping) or output.get("tool") != "manage_todos":
            return ()
        sequence = output.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence in seen_sequences:
            return ()
        seen_sequences.add(sequence)
        start = next(
            (
                item
                for item in starts
                if isinstance(item, Mapping) and item.get("sequence") == sequence
            ),
            None,
        )
        if not isinstance(start, Mapping) or start.get("tool") != "manage_todos" or start.get("observed") is not True:
            return ()
        refs = output.get("receipt_refs")
        if not isinstance(refs, (list, tuple)):
            return ()
        receipts: list[TodoReceipt] = []
        for ref in refs:
            receipt = receipts_by_ref.get(str(ref or ""))
            if receipt is None:
                return ()
            receipts.append(receipt)
        if output.get("receipt_count") != len(receipts):
            return ()
        digest_refs = output.get("digest_receipt_refs") or ()
        if not isinstance(digest_refs, (list, tuple)):
            return ()
        digest_receipts: list[TodoDigestReceipt] = []
        for ref in digest_refs:
            digest_receipt = digest_receipts_by_ref.get(str(ref or ""))
            if digest_receipt is None:
                return ()
            digest_receipts.append(digest_receipt)
        if output.get("digest_receipt_count", 0) != len(digest_receipts):
            return ()
        reconstructed.append(
            {
                "tool": "manage_todos",
                "exit_code": _safe_exit_code(output.get("exit_code")),
                "todo_receipts": [receipt.to_dict() for receipt in receipts],
                "todo_digest_receipts": [
                    receipt.to_dict() for receipt in digest_receipts
                ],
            }
        )

    covered = {
        receipt.receipt_ref
        for event in reconstructed
        for receipt in todo_receipts_from_tool_events((event,))
    }
    if covered != set(receipts_by_ref):
        return ()
    digest_covered = {
        receipt.receipt_ref
        for event in reconstructed
        for receipt in todo_digest_receipts_from_tool_events((event,))
    }
    if digest_covered != set(digest_receipts_by_ref):
        return ()
    return tuple(reconstructed)


def telegram_todo_truth_envelope_public_summary(envelope: Any) -> dict[str, Any]:
    valid = telegram_todo_truth_envelope_has_evidence(envelope)
    counts = envelope.get("counts") if valid and isinstance(envelope, Mapping) else {}
    return {
        "schema": TELEGRAM_TODO_TRUTH_ENVELOPE_SCHEMA,
        "present": valid,
        "transaction_count": _bounded_count(counts.get("transactions")) if isinstance(counts, Mapping) else 0,
        "postcondition_count": _bounded_count(counts.get("postconditions")) if isinstance(counts, Mapping) else 0,
        "digest_postcondition_count": _bounded_count(counts.get("digest_postconditions")) if isinstance(counts, Mapping) else 0,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
    }


def _safe_exit_code(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return 1
    try:
        return max(0, min(int(value), 255))
    except (TypeError, ValueError):
        return 1


def _bounded_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if 0 <= parsed <= 10_000 else 0


__all__ = [
    "TELEGRAM_TODO_TRUTH_ENVELOPE_SCHEMA",
    "build_telegram_todo_truth_envelope",
    "telegram_todo_truth_envelope_has_evidence",
    "telegram_todo_truth_envelope_public_summary",
    "tool_events_from_telegram_todo_truth_envelope",
]
