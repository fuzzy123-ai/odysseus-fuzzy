"""Redacted review/write gate status routes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.constants import DATA_DIR
from src.memory_triage_contract import normalize_memory_write_intent_status


REVIEW_GATE_SCHEMA = "odysseus.review_gate_state.v1"


def setup_review_gate_routes(telegram_data_dir: str | Path | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/review-gates", tags=["review-gates"])

    @router.get("/status")
    async def review_gate_status(request: Request):
        require_admin(request)
        store = _load_telegram_store(telegram_data_dir)
        gates = _build_gate_list(store)
        pending = sum(1 for gate in gates if gate["state"] in {"pending_review", "ready_to_write", "ready_to_execute"})
        blocked = sum(1 for gate in gates if gate["state"] == "blocked")
        return {
            "schema": REVIEW_GATE_SCHEMA,
            "status": "pending" if pending else ("blocked" if blocked else "clear"),
            "pending_count": pending,
            "blocked_count": blocked,
            "gate_count": len(gates),
            "gates": gates,
            "raw_content_visible": False,
            "path_values_visible": False,
            "chat_id_value_visible": False,
            "token_value_visible": False,
        }

    return router


def _load_telegram_store(telegram_data_dir: str | Path | None):
    from plugins.telegram.stores import TelegramInboxStore

    root = Path(telegram_data_dir) if telegram_data_dir is not None else Path(DATA_DIR) / "plugins" / "telegram"
    return TelegramInboxStore(root)


def _build_gate_list(store: Any) -> list[dict[str, Any]]:
    inbox_review = _safe_call(lambda: store.latest_universal_inbox_review()) or {}
    memory_review = _safe_call(lambda: store.latest_universal_inbox_memory_review()) or {}
    history = _safe_call(lambda: store.history(limit=200)) or []
    export_event = _latest_kind(history, "universal_inbox_export_plan")
    return [
        _nextcloud_gate(inbox_review),
        _memory_gate(memory_review),
        _raptorgraph_gate(memory_review),
        _export_gate(export_event),
    ]


def _nextcloud_gate(review: Mapping[str, Any]) -> dict[str, Any]:
    if not review:
        return _gate("nextcloud_copy", "nextcloud", "no_pending", "no_recent_universal_inbox_review")

    status = _safe_token(review.get("status") or "")
    inbox_status = _safe_token(review.get("universal_inbox_status") or "")
    if status in {"blocked", "failed"}:
        state = "blocked"
        review_required = False
    else:
        state = "pending_review"
        review_required = True
    return _gate(
        "nextcloud_copy",
        "nextcloud",
        state,
        inbox_status or status or "review_required",
        review_required=review_required,
        approval_command="/review ok",
        source_ref=_source_ref(review, "nextcloud"),
        metadata={
            "universal_inbox_status": inbox_status,
            "attachment_family": _safe_token(review.get("attachment_family") or ""),
            "attachment_suffix": _safe_suffix(review.get("attachment_suffix") or ""),
            "review_reason_count": _safe_count(review.get("review_reason_count")),
            "maintenance_review_required": bool(review.get("maintenance_review_required")),
            "writes_performed": False,
        },
    )


def _memory_gate(review: Mapping[str, Any]) -> dict[str, Any]:
    if not review:
        return _gate("memory_write", "memory", "no_pending", "no_recent_memory_write_review")

    status = normalize_memory_write_intent_status(review.get("memory_write_intent_status") or "", fallback="")
    if status == "review":
        state = "pending_review"
        review_required = True
    elif status == "ready":
        state = "ready_to_write"
        review_required = False
    elif status == "blocked":
        state = "blocked"
        review_required = False
    else:
        state = "no_pending"
        review_required = False
    return _gate(
        "memory_write",
        "memory",
        state,
        status or "memory_write_intent_missing",
        review_required=review_required,
        approval_command="/review ok" if state in {"pending_review", "ready_to_write"} else "",
        source_ref=_source_ref(review, "memory"),
        metadata={
            "memory_write_intent_status": status,
            "memory_records_planned": _safe_count(review.get("memory_records_planned")),
            "memory_records_written": _safe_count(review.get("memory_records_written")),
            "review_reason_count": _safe_count(review.get("review_reason_count")),
            "maintenance_review_required": bool(review.get("maintenance_review_required")),
            "writes_performed": bool(review.get("writes_performed")),
        },
    )


def _raptorgraph_gate(review: Mapping[str, Any]) -> dict[str, Any]:
    if not review:
        return _gate("raptorgraph_write", "raptorgraph", "no_pending", "no_recent_raptorgraph_review")

    planned = _safe_count(review.get("raptorgraph_events_planned"))
    written = _safe_count(review.get("raptorgraph_events_written"))
    status = normalize_memory_write_intent_status(review.get("memory_write_intent_status") or "", fallback="")
    if planned <= 0:
        return _gate("raptorgraph_write", "raptorgraph", "no_pending", "no_raptorgraph_event_planned")
    if written >= planned:
        state = "done"
        review_required = False
    elif status == "review":
        state = "pending_review"
        review_required = True
    elif status == "ready":
        state = "ready_to_write"
        review_required = False
    elif status == "blocked":
        state = "blocked"
        review_required = False
    else:
        state = "pending_review"
        review_required = True
    return _gate(
        "raptorgraph_write",
        "raptorgraph",
        state,
        status or "raptorgraph_write_pending",
        review_required=review_required,
        approval_command="/review ok" if state in {"pending_review", "ready_to_write"} else "",
        source_ref=_source_ref(review, "raptorgraph"),
        metadata={
            "raptorgraph_events_planned": planned,
            "raptorgraph_events_written": written,
            "memory_write_intent_status": status,
            "writes_performed": bool(review.get("writes_performed")),
        },
    )


def _export_gate(event: Mapping[str, Any]) -> dict[str, Any]:
    if not event:
        return _gate("file_export", "export", "no_pending", "no_recent_export_plan")

    status = _safe_token(event.get("status") or "")
    if status in {"exported", "sent"}:
        state = "done"
        review_required = False
    elif status in {"ready", "planned", "needs_review"}:
        state = "ready_to_execute" if status == "ready" else "pending_review"
        review_required = True
    elif status in {"blocked", "unsupported", "failed"}:
        state = "blocked"
        review_required = False
    else:
        state = "pending_review"
        review_required = True
    return _gate(
        "file_export",
        "export",
        state,
        status or "export_plan_pending",
        review_required=review_required,
        approval_command="explicit export/live gate",
        source_ref=_source_ref(event, "export"),
        metadata={
            "target_format": _safe_token(event.get("target_format") or ""),
            "action": _safe_token(event.get("action") or ""),
            "required_tool": _safe_token(event.get("required_tool") or ""),
            "delivery_ready": bool(event.get("delivery_ready")),
            "bytes_written": _safe_count(event.get("bytes_written")),
            "writes_performed": status in {"exported", "sent"},
        },
    )


def _gate(
    gate_id: str,
    family: str,
    state: str,
    reason: str,
    *,
    review_required: bool = False,
    approval_command: str = "",
    source_ref: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "family": family,
        "state": state,
        "reason": _safe_token(reason or "unknown"),
        "review_required": bool(review_required),
        "approval_command": approval_command,
        "source_ref": source_ref,
        "metadata": _safe_metadata(metadata or {}),
        "raw_content_visible": False,
        "path_values_visible": False,
        "chat_id_value_visible": False,
        "token_value_visible": False,
    }


def _latest_kind(messages: Iterable[Any], kind: str) -> dict[str, Any]:
    for message in messages:
        if isinstance(message, Mapping) and message.get("kind") == kind:
            return dict(message)
    return {}


def _source_ref(message: Mapping[str, Any], family: str) -> str:
    basis = "|".join(
        str(message.get(key) or "")
        for key in ("kind", "status", "chat_handle", "message_id", "stored_at", "updated_at")
    )
    digest = hashlib.sha256(f"{family}:{basis}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"review-gate:{family}:{digest}"


def _safe_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        safe_key = _safe_token(key)
        if isinstance(value, bool):
            safe[safe_key] = value
        elif isinstance(value, int):
            safe[safe_key] = max(0, value)
        elif value is None:
            safe[safe_key] = None
        else:
            safe[safe_key] = _safe_token(value)
    return safe


def _safe_call(fn):
    try:
        return fn()
    except Exception:
        return None


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_suffix(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text.startswith("."):
        return ""
    if not all(ch.isalnum() or ch in "._-" for ch in text):
        return ""
    return text[:16]


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_")
    if not text:
        return ""
    if any(marker in text for marker in ("authorization", "bearer", "api_key", "password", "cookie", "token")):
        return "redacted"
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "._:")
    return (cleaned or "redacted")[:80]
