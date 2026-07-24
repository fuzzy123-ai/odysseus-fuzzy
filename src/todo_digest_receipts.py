"""Content-free semantic receipts for Todo digest membership and scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


TODO_DIGEST_RECEIPT_SCHEMA = "odysseus.todo_digest_receipt.v1"
_CLAIM_TYPES = {
    "todo_digest_contains",
    "todo_digest_excludes",
    "todo_digest_schedule_active",
}
_LIST_REF_RE = re.compile(r"^todo-list:v1:[A-Fa-f0-9]{16}:.{1,1024}$")
_ITEM_REF_RE = re.compile(r"^todo-item:v1:[A-Za-z0-9_-]{8,128}$")
_PROJECTION_REF_RE = re.compile(r"^todo-digest-projection:v1:[a-f0-9]{32}$")
_SCHEDULE_REF_RE = re.compile(r"^todo-digest-schedule:v1:[a-f0-9]{12}$")
_EVIDENCE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,180}$")


class TodoDigestReceiptError(ValueError):
    """Raised when a digest receipt is malformed or semantically inconsistent."""


@dataclass(frozen=True, slots=True)
class TodoDigestReceipt:
    claim_type: str
    verified: bool
    transaction_status: str
    evidence_refs: tuple[str, ...]
    list_ref: str | None = None
    item_ref: str | None = None
    included: bool | None = None
    current_state: dict[str, bool | None] | None = None
    projection_ref: str | None = None
    schedule_ref: str | None = None
    next_run: str | None = None
    schema: str = TODO_DIGEST_RECEIPT_SCHEMA

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TodoDigestReceipt":
        if payload.get("schema") not in (None, TODO_DIGEST_RECEIPT_SCHEMA):
            raise TodoDigestReceiptError("unsupported Todo digest receipt schema")
        claim_type = str(payload.get("claim_type") or "").strip()
        if claim_type not in _CLAIM_TYPES:
            raise TodoDigestReceiptError("unknown Todo digest claim type")
        status = str(payload.get("transaction_status") or payload.get("status") or "").strip().lower()
        refs = _safe_evidence_refs(payload.get("evidence_refs") or ())
        requested_verified = payload.get("verified") is True

        if claim_type == "todo_digest_schedule_active":
            schedule_ref = _safe_optional_ref(payload.get("schedule_ref"), _SCHEDULE_REF_RE)
            next_run = _safe_next_run(payload.get("next_run"))
            semantic_match = bool(
                status == "active"
                and schedule_ref
                and next_run
                and any(ref.startswith("scheduled-task-readback:v1:") for ref in refs)
            )
            return cls(
                claim_type=claim_type,
                verified=bool(requested_verified and semantic_match),
                transaction_status=status,
                evidence_refs=refs,
                schedule_ref=schedule_ref,
                next_run=next_run,
            )

        list_ref = str(payload.get("list_ref") or "").strip()
        item_ref = str(payload.get("item_ref") or "").strip()
        if not _LIST_REF_RE.fullmatch(list_ref) or not _ITEM_REF_RE.fullmatch(item_ref):
            raise TodoDigestReceiptError("invalid Todo digest item reference")
        projection_ref = _safe_optional_ref(payload.get("projection_ref"), _PROJECTION_REF_RE)
        included = payload.get("included")
        if included is not True and included is not False:
            raise TodoDigestReceiptError("included must be boolean")
        current_state = _safe_state(payload.get("current_state"))
        if claim_type == "todo_digest_contains":
            semantic_match = bool(
                included is True
                and current_state == {"exists": True, "done": False}
            )
        else:
            semantic_match = bool(
                included is False
                and current_state in (
                    {"exists": True, "done": True},
                    {"exists": False, "done": None},
                )
            )
        readback = any(ref.startswith("notes-digest-readback:v1:") for ref in refs)
        verified = bool(
            requested_verified
            and status == "projected"
            and projection_ref
            and readback
            and semantic_match
        )
        return cls(
            claim_type=claim_type,
            verified=verified,
            transaction_status=status,
            evidence_refs=refs,
            list_ref=list_ref,
            item_ref=item_ref,
            included=included,
            current_state=current_state,
            projection_ref=projection_ref,
        )

    @property
    def receipt_ref(self) -> str:
        payload = json.dumps(
            {
                "schema": self.schema,
                "claim_type": self.claim_type,
                "verified": self.verified,
                "transaction_status": self.transaction_status,
                "evidence_refs": self.evidence_refs,
                "list_ref": self.list_ref,
                "item_ref": self.item_ref,
                "included": self.included,
                "current_state": self.current_state,
                "projection_ref": self.projection_ref,
                "schedule_ref": self.schedule_ref,
                "next_run": self.next_run,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"todo-digest-receipt:v1:{digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "claim_type": self.claim_type,
            "verified": self.verified,
            "transaction_status": self.transaction_status,
            "evidence_refs": list(self.evidence_refs),
            "list_ref": self.list_ref,
            "item_ref": self.item_ref,
            "included": self.included,
            "current_state": dict(self.current_state) if self.current_state is not None else None,
            "projection_ref": self.projection_ref,
            "schedule_ref": self.schedule_ref,
            "next_run": self.next_run,
            "receipt_ref": self.receipt_ref,
            "raw_content_visible": False,
        }


def todo_digest_receipts_from_postconditions(
    projection: Mapping[str, Any] | None,
    schedule: Mapping[str, Any] | None,
) -> tuple[TodoDigestReceipt, ...]:
    payloads: list[Mapping[str, Any]] = []
    if isinstance(projection, Mapping):
        payloads.append({
            **projection,
            "schema": TODO_DIGEST_RECEIPT_SCHEMA,
        })
    if isinstance(schedule, Mapping):
        payloads.append({
            "schema": TODO_DIGEST_RECEIPT_SCHEMA,
            "claim_type": schedule.get("claim_type"),
            "transaction_status": schedule.get("status"),
            "schedule_ref": schedule.get("schedule_ref"),
            "next_run": schedule.get("next_run"),
            "verified": schedule.get("verified"),
            "evidence_refs": schedule.get("evidence_refs") or (),
        })
    receipts: list[TodoDigestReceipt] = []
    for payload in payloads:
        try:
            receipts.append(TodoDigestReceipt.from_mapping(payload))
        except TodoDigestReceiptError:
            continue
    return tuple(receipts)


def todo_digest_receipts_from_tool_result(
    result: Mapping[str, Any],
) -> tuple[TodoDigestReceipt, ...]:
    return _receipts_from_payloads(result.get("todo_digest_receipts") or ())


def todo_digest_receipts_from_tool_events(
    tool_events: Iterable[Mapping[str, Any]],
) -> tuple[TodoDigestReceipt, ...]:
    receipts: list[TodoDigestReceipt] = []
    seen: set[str] = set()
    for event in tool_events:
        if not isinstance(event, Mapping) or event.get("tool") != "manage_todos":
            continue
        event_receipts = _receipts_from_payloads(event.get("todo_digest_receipts") or ())
        exit_ok = event.get("exit_code") in (0, "0", None)
        for receipt in event_receipts:
            if not exit_ok and receipt.verified:
                payload = receipt.to_dict()
                payload["verified"] = False
                receipt = TodoDigestReceipt.from_mapping(payload)
            if receipt.receipt_ref not in seen:
                seen.add(receipt.receipt_ref)
                receipts.append(receipt)
    return tuple(receipts)


def todo_digest_evidence_for_claim(
    receipts: Iterable[TodoDigestReceipt],
    claim_type: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    for receipt in receipts:
        if receipt.claim_type != claim_type or not receipt.verified:
            continue
        refs.append(receipt.receipt_ref)
        refs.extend(receipt.evidence_refs)
        if receipt.projection_ref:
            refs.append(receipt.projection_ref)
        if receipt.schedule_ref:
            refs.append(receipt.schedule_ref)
    return tuple(dict.fromkeys(refs))


def _receipts_from_payloads(payloads: Any) -> tuple[TodoDigestReceipt, ...]:
    if not isinstance(payloads, (list, tuple)):
        return ()
    receipts: list[TodoDigestReceipt] = []
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        try:
            receipts.append(TodoDigestReceipt.from_mapping(payload))
        except TodoDigestReceiptError:
            continue
    return tuple(receipts)


def _safe_optional_ref(value: Any, pattern: re.Pattern[str]) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not pattern.fullmatch(text):
        raise TodoDigestReceiptError("invalid Todo digest evidence reference")
    return text


def _safe_state(value: Any) -> dict[str, bool | None]:
    if not isinstance(value, Mapping) or set(value) != {"exists", "done"}:
        raise TodoDigestReceiptError("invalid Todo digest item state")
    exists = value.get("exists")
    done = value.get("done")
    if not (exists is True or exists is False):
        raise TodoDigestReceiptError("invalid Todo digest item state")
    if not (done is True or done is False or done is None):
        raise TodoDigestReceiptError("invalid Todo digest item state")
    if exists is False and done is not None:
        raise TodoDigestReceiptError("removed Todo cannot retain done state")
    return {"exists": exists, "done": done}


def _safe_next_run(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TodoDigestReceiptError("invalid next_run") from exc
    return text


def _safe_evidence_refs(values: Iterable[Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or not _EVIDENCE_REF_RE.fullmatch(text):
            raise TodoDigestReceiptError("unsafe Todo digest evidence reference")
        refs.append(text)
    return tuple(dict.fromkeys(refs))


__all__ = [
    "TODO_DIGEST_RECEIPT_SCHEMA",
    "TodoDigestReceipt",
    "TodoDigestReceiptError",
    "todo_digest_evidence_for_claim",
    "todo_digest_receipts_from_postconditions",
    "todo_digest_receipts_from_tool_events",
    "todo_digest_receipts_from_tool_result",
]
