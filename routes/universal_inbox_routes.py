"""Browser-safe Universal Inbox status routes."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import effective_user
from src.universal_inbox_file_types import classify_universal_inbox_file


def setup_universal_inbox_routes(upload_handler: Any = None) -> APIRouter:
    router = APIRouter(prefix="/api/universal-inbox", tags=["universal-inbox"])

    @router.get("/items/{source_ref:path}/status")
    async def get_universal_inbox_item_status(request: Request, source_ref: str):
        source_kind, upload_id = _normalize_source_ref(source_ref)
        if source_kind != "upload":
            raise HTTPException(404, "Universal Inbox source not found")

        if upload_handler is None or not hasattr(upload_handler, "resolve_upload"):
            raise HTTPException(503, "Upload status backend is not available")

        auth_manager = getattr(request.app.state, "auth_manager", None)
        auth_configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
        owner = effective_user(request)
        if auth_configured and not owner:
            raise HTTPException(403, "Not authenticated")

        info = upload_handler.resolve_upload(
            upload_id,
            owner=owner,
            auth_manager=auth_manager,
            allow_admin=True,
        )
        if not info:
            raise HTTPException(404, "Universal Inbox source not found")

        return _build_redacted_upload_status(info, source_ref=source_ref)

    return router


def _normalize_source_ref(source_ref: str) -> tuple[str, str]:
    raw = (source_ref or "").strip()
    if not raw:
        raise HTTPException(400, "Invalid Universal Inbox source reference")
    if "/" in raw or "\\" in raw or raw in {".", ".."}:
        raise HTTPException(400, "Invalid Universal Inbox source reference")

    if raw.startswith("upload:"):
        return "upload", raw.removeprefix("upload:").strip()
    if raw.startswith("inbox:upload:"):
        return "upload", raw.removeprefix("inbox:upload:").strip()
    if ":" in raw:
        kind, value = raw.split(":", 1)
        return kind.strip(), value.strip()
    return "upload", raw


def _build_redacted_upload_status(info: dict[str, Any], *, source_ref: str) -> dict[str, Any]:
    upload_id = str(info.get("id") or "").strip()
    filename = str(info.get("original_name") or info.get("name") or upload_id)
    mime_type = str(info.get("mime") or "")
    decision = classify_universal_inbox_file(filename, mime_type=mime_type)

    status = _status_from_decision(decision)
    size = info.get("size")
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None

    return {
        "schema": "odysseus.universal_inbox.item_status.v1",
        "source_ref": _redacted_source_ref(source_ref, upload_id),
        "source_kind": "upload",
        "status": status,
        "processing_state": "metadata_only",
        "pipeline_status_available": False,
        "display_name_redacted": True,
        "path_redacted": True,
        "content_redacted": True,
        "chat_id_redacted": True,
        "extension": decision.suffix or os.path.splitext(upload_id)[1].lower(),
        "mime_type": decision.mime_type,
        "family": decision.family,
        "category": decision.category,
        "extractable_now": decision.extractable_now,
        "review_required": decision.review_required,
        "blocked": decision.blocked,
        "reason_codes": list(decision.reason_codes),
        "size_bytes": size,
        "uploaded_at": info.get("uploaded_at"),
        "live_write_allowed": False,
        "next_action": _next_action_from_decision(decision),
    }


def _status_from_decision(decision: Any) -> str:
    if decision.blocked:
        return "blocked"
    if decision.category == "unsupported":
        return "unsupported"
    if decision.review_required:
        return "needs_review"
    return "uploaded"


def _next_action_from_decision(decision: Any) -> str:
    if decision.blocked:
        return "reject"
    if decision.extractable_now:
        return "extract"
    if decision.review_required:
        return "review"
    return "none"


def _redacted_source_ref(source_ref: str, upload_id: str) -> str:
    raw = (source_ref or "").strip()
    if raw.startswith("inbox:upload:"):
        return f"inbox:upload:{upload_id}"
    return f"upload:{upload_id}"
