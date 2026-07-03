"""Redacted ledger for long-running Odysseus agent tasks."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from src.constants import DATA_DIR


AGENT_TASK_LEDGER_DIR = os.path.join(DATA_DIR, "agent_task_ledger")
AGENT_TASK_LEDGER_SCHEMA = "odysseus.agent_task_ledger.v1"
TASK_CONTROL_STATUSES = ("pause_requested", "resume_requested", "cancel_requested")

_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_SECRET_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "password",
    "cookie",
    "token=",
    "private raw text",
    "chat_id",
    "file_id",
)


class AgentTaskLedgerError(ValueError):
    """Raised when an agent task ledger record is unsafe."""


def build_task_record(
    *,
    task_id: str,
    task_type: str,
    status: str,
    surface: str = "telegram",
    owner: str = "operator",
    correlation_id: str = "",
    target_ref: str = "",
    progress_percent: int = 0,
    gates_waiting: Iterable[str] = (),
    summary: str = "",
    error_class: str = "",
) -> dict[str, Any]:
    """Build one metadata-only task ledger record."""

    record = {
        "schema": AGENT_TASK_LEDGER_SCHEMA,
        "ts": _now_iso(),
        "task_id": _safe_label(task_id, "task_id"),
        "task_type": _safe_label(task_type, "task_type"),
        "status": _safe_label(status, "status"),
        "surface": _safe_label(surface, "surface"),
        "owner_hash": _hash_owner(owner),
        "correlation_id": _safe_label(correlation_id, "correlation_id"),
        "target_ref": _safe_target_ref(target_ref),
        "progress_percent": _safe_progress(progress_percent),
        "gates_waiting": tuple(_safe_label(item, "gate") for item in gates_waiting),
        "summary": _safe_summary(summary),
        "error_class": _safe_label(error_class, "error_class"),
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(record)
    return record


def append_task_record(record: Mapping[str, Any], *, day: str | None = None) -> dict[str, Any]:
    payload = dict(record)
    _reject_forbidden_payload(payload)
    path = ledger_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def record_task_event(**kwargs: Any) -> dict[str, Any]:
    return append_task_record(build_task_record(**kwargs))


def read_task_records(*, day: str | None = None, task_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    path = ledger_path(day)
    capped_limit = max(1, min(int(limit or 100), 1000))
    task_filter = _safe_label(task_id, "task_id_filter") if task_id else ""
    if not path.exists():
        return {"status": "success", "count": 0, "records": [], "summary": summarize_task_records([])}
    records: list[dict[str, Any]] = []
    skipped = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise AgentTaskLedgerError("record must be mapping")
                _reject_forbidden_payload(record)
            except Exception:
                skipped += 1
                continue
            if task_filter and record.get("task_id") != task_filter:
                continue
            records.append(record)
    recent = records[-capped_limit:]
    recent.reverse()
    summary = summarize_task_records(records)
    summary["skipped"] = skipped
    return {"status": "success", "count": len(recent), "records": recent, "summary": summary}


def read_task_control_events(*, task_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Return recent metadata-only remote-control events for coding tasks."""

    result = read_task_records(task_id=task_id, limit=max(1, min(int(limit or 20), 200)))
    records = [
        record
        for record in result.get("records", [])
        if record.get("status") in TASK_CONTROL_STATUSES
        and record.get("task_type") == "coding_agent_task"
    ]
    return {
        "status": "success",
        "count": len(records),
        "records": records,
        "control_statuses": TASK_CONTROL_STATUSES,
        "raw_content_visible": False,
    }


def summarize_task_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    latest_progress: dict[str, int] = {}
    for record in records:
        status = str(record.get("status") or "unknown")
        task_type = str(record.get("task_type") or "unknown")
        task_id = str(record.get("task_id") or "")
        by_status[status] = by_status.get(status, 0) + 1
        by_type[task_type] = by_type.get(task_type, 0) + 1
        if task_id:
            latest_progress[task_id] = _safe_progress(record.get("progress_percent"))
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_type": by_type,
        "latest_progress": latest_progress,
    }


def ledger_path(day: str | None = None) -> Path:
    day_text = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_text):
        raise AgentTaskLedgerError("ledger day must be YYYY-MM-DD")
    return Path(AGENT_TASK_LEDGER_DIR) / f"{day_text}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash_owner(owner: Any) -> str:
    text = str(owner or "operator").strip()
    if not text:
        text = "operator"
    if any(marker in text.lower() for marker in _SECRET_MARKERS):
        raise AgentTaskLedgerError("owner contains forbidden marker")
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _safe_label(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise AgentTaskLedgerError(f"{field} contains forbidden marker")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise AgentTaskLedgerError(f"{field} must not contain host paths")
    if not _SAFE_LABEL_RE.fullmatch(text):
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text[:180]


def _safe_target_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise AgentTaskLedgerError("target_ref contains forbidden marker")
    if lowered.startswith("domain:"):
        domain = lowered.removeprefix("domain:")
        if re.fullmatch(r"[a-z0-9.-]{1,253}", domain):
            return f"domain:{domain}"
    if lowered.startswith("repo:"):
        repo = lowered.removeprefix("repo:")
        if re.fullmatch(r"[a-z0-9_.-]{2,80}", repo):
            return f"repo:{repo}"
    if lowered.startswith(("http://", "https://")):
        match = re.fullmatch(r"https?://[a-z0-9.-]{1,253}(:[0-9]{1,5})?/", lowered)
        if match:
            return lowered
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _safe_progress(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(parsed, 100))


def _safe_summary(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if any(marker in text.lower() for marker in _SECRET_MARKERS):
        raise AgentTaskLedgerError("summary contains forbidden marker")
    if len(text) > 240:
        text = text[:237] + "..."
    return text


def _reject_forbidden_payload(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).lower()
    if any(marker in encoded for marker in _SECRET_MARKERS):
        raise AgentTaskLedgerError("task ledger payload contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise AgentTaskLedgerError("task ledger payload contains host path")
