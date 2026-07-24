"""Closed, content-free proof that an owner's Todo digest is actively scheduled."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any, Mapping


TODO_DIGEST_SCHEDULE_RECEIPT_SCHEMA = "odysseus.todo_digest_schedule_receipt.v1"
TODO_DIGEST_SCHEDULE_RECEIPT_FIELD = "todo_digest_schedule_receipt"
_REF_RE = re.compile(r"^(?:owner|task|schedule):[a-f0-9]{16}$")
_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def build_todo_digest_schedule_receipt(*, owner: Any, candidates: Any, now_utc: Any) -> dict[str, Any] | None:
    """Evaluate injected rows without database access or content exposure."""
    try:
        return _build_strict(owner=owner, candidates=candidates, now_utc=now_utc)
    except Exception:
        return None


def _build_strict(*, owner: Any, candidates: Any, now_utc: Any) -> dict[str, Any] | None:
    if not _owner(owner) or not _naive_datetime(now_utc) or not isinstance(candidates, (list, tuple)) or len(candidates) != 1:
        return None
    task = candidates[0]
    values = {key: _value(task, key) for key in (
        "id", "owner", "task_type", "action", "trigger_type", "schedule", "status", "cron_expression", "scheduled_time", "next_run",
    )}
    if values["owner"] != owner or not _raw_id(values["id"]):
        return None
    if (values["task_type"], values["action"], values["trigger_type"], values["schedule"], values["status"]) != ("action", "todo_digest", "schedule", "cron", "active"):
        return None
    cron = _weekday_cron(values["cron_expression"])
    if cron is None or values["scheduled_time"] != f"{cron[1]:02d}:{cron[0]:02d}":
        return None
    next_run = values["next_run"]
    if not _naive_datetime(next_run) or not next_run > now_utc:
        return None
    refs = (
        _redact("owner", owner),
        _redact("task", values["id"]),
        _redact("schedule", "|".join((values["cron_expression"], values["scheduled_time"], next_run.isoformat(), now_utc.isoformat()))),
    )
    receipt = {
        "schema": TODO_DIGEST_SCHEDULE_RECEIPT_SCHEMA,
        "claim_type": "todo_digest_schedule_active",
        "transaction_status": "read_verified",
        "verified": True,
        "evidence_refs": refs,
        "single_task": True,
        "active": True,
        "schedule_kind": "cron",
        "next_run_state": "future",
        "clock": "naive_utc",
        "raw_content_visible": False,
    }
    receipt_ref = _receipt_ref(receipt)
    if receipt_ref is None:
        return None
    receipt["receipt_ref"] = receipt_ref
    return receipt


def validate_todo_digest_schedule_receipt(receipt: Any, *, owner_ref: Any = None) -> dict[str, Any] | None:
    try:
        if not isinstance(receipt, Mapping):
            return None
        keys = {"schema", "claim_type", "transaction_status", "verified", "evidence_refs", "single_task", "active", "schedule_kind", "next_run_state", "clock", "raw_content_visible", "receipt_ref"}
        if set(receipt) != keys or receipt.get("schema") != TODO_DIGEST_SCHEDULE_RECEIPT_SCHEMA or receipt.get("claim_type") != "todo_digest_schedule_active":
            return None
        if receipt.get("transaction_status") != "read_verified" or receipt.get("verified") is not True or receipt.get("single_task") is not True or receipt.get("active") is not True:
            return None
        if receipt.get("schedule_kind") != "cron" or receipt.get("next_run_state") != "future" or receipt.get("clock") != "naive_utc" or receipt.get("raw_content_visible") is not False:
            return None
        refs = receipt.get("evidence_refs")
        if not isinstance(refs, (tuple, list)) or len(refs) != 3 or len(set(refs)) != 3 or any(not isinstance(value, str) or not _REF_RE.fullmatch(value) for value in refs):
            return None
        if not refs[0].startswith("owner:") or not refs[1].startswith("task:") or not refs[2].startswith("schedule:"):
            return None
        if owner_ref is not None and refs[0] != owner_ref:
            return None
        expected = _receipt_ref({key: receipt[key] for key in keys if key != "receipt_ref"})
        if not isinstance(receipt.get("receipt_ref"), str) or not _HASH_RE.fullmatch(receipt["receipt_ref"]) or expected is None or receipt["receipt_ref"] != expected:
            return None
        return {key: receipt[key] for key in keys}
    except Exception:
        return None


def validated_todo_digest_schedule_receipt_from_event(event: Any) -> dict[str, Any] | None:
    try:
        if not isinstance(event, Mapping) or event.get("tool") != "manage_todos":
            return None
        from src.todo_transaction_receipts import validated_todo_semantic_receipt_from_event
        semantic = validated_todo_semantic_receipt_from_event(event)
        if semantic is None:
            return None
        return validate_todo_digest_schedule_receipt(event.get(TODO_DIGEST_SCHEDULE_RECEIPT_FIELD), owner_ref=semantic["evidence_refs"][0])
    except Exception:
        return None


def schedule_receipts_from_tool_events(events: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(events, (list, tuple)):
        return ()
    try:
        found = []
        for event in events[:64]:
            receipt = validated_todo_digest_schedule_receipt_from_event(event)
            if receipt is not None:
                found.append(receipt)
        return tuple(found)
    except Exception:
        return ()


def _weekday_cron(value: Any) -> tuple[int, int, tuple[int, ...]] | None:
    if not isinstance(value, str):
        return None
    fields = value.split()
    if len(fields) != 5 or fields[2:4] != ["*", "*"]:
        return None
    try:
        minute, hour = int(fields[0]), int(fields[1])
    except (ValueError, TypeError):
        return None
    weekdays = _cron_weekdays(fields[4])
    if not 0 <= minute <= 59 or not 0 <= hour <= 23 or weekdays is None:
        return None
    return minute, hour, weekdays


def _cron_weekdays(field: str) -> tuple[int, ...] | None:
    days: set[int] = set()
    for raw in field.split(","):
        part = raw.strip()
        if not part:
            return None
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = _cron_day(start_text), _cron_day(end_text)
            if start is None or end is None or end < start:
                return None
            days.update(range(start, end + 1))
        else:
            day = _cron_day(part)
            if day is None:
                return None
            days.add(day)
    return tuple(sorted(days)) if days else None


def _cron_day(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed in {0, 7}:
        return 6
    return parsed - 1 if 1 <= parsed <= 6 else None


def _owner(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value and len(value) <= 256


def _raw_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value and len(value) <= 256


def _naive_datetime(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is None


def _value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    mapping = getattr(row, "_mapping", None)
    if isinstance(mapping, Mapping):
        return mapping.get(key)
    return getattr(row, key, None)


def _redact(kind: str, value: str) -> str:
    return f"{kind}:{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _receipt_ref(value: Mapping[str, Any]) -> str | None:
    try:
        return "sha256:" + sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
    except Exception:
        return None
