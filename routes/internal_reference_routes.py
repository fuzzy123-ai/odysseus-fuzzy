"""Redacted internal-reference resolution routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.auth_helpers import effective_user
from src.constants import DATA_DIR
from src.internal_references import (
    InternalReference,
    InternalReferenceError,
    build_internal_reference,
    parse_chat_href,
    parse_internal_uri,
)


RESOLUTION_SCHEMA = "odysseus.internal_ref_resolution.v1"


def setup_internal_reference_routes(memory_manager: Any = None) -> APIRouter:
    router = APIRouter(prefix="/api/internal-refs", tags=["internal-refs"])

    @router.get("/resolve")
    async def resolve_internal_ref(request: Request, ref: str = Query(..., min_length=1, max_length=240)):
        parsed = _parse_ref(ref)
        if parsed.kind == "memory":
            return _resolve_memory_ref(request, parsed, memory_manager)
        if parsed.kind in {"raptor_node", "raptor_edge"}:
            return _resolve_raptor_ref(parsed)
        return _base_resolution(parsed, status="unsupported", exists=False, reason="unsupported_ref_kind")

    return router


def _parse_ref(ref: str) -> InternalReference:
    value = str(ref or "").strip()
    try:
        if value.startswith("odysseus://"):
            return parse_internal_uri(value)
        if value.startswith("#"):
            return parse_chat_href(value)
        if ":" in value:
            kind, entity_id = value.split(":", 1)
            return build_internal_reference(_normalize_shorthand_kind(kind), entity_id)
    except InternalReferenceError as exc:
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(400, "Unsupported internal reference format")


def _normalize_shorthand_kind(kind: str) -> str:
    value = str(kind or "").strip().lower().replace("-", "_")
    aliases = {
        "memory": "memory",
        "mem": "memory",
        "raptor": "raptor_node",
        "raptor_node": "raptor_node",
        "raptor_edge": "raptor_edge",
    }
    if value not in aliases:
        raise InternalReferenceError("unsupported internal reference kind")
    return aliases[value]


def _resolve_memory_ref(request: Request, ref: InternalReference, memory_manager: Any) -> dict[str, Any]:
    if memory_manager is None or not hasattr(memory_manager, "load"):
        return _base_resolution(ref, status="unavailable", exists=False, reason="memory_manager_unavailable")

    auth_manager = getattr(request.app.state, "auth_manager", None)
    auth_configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
    owner = effective_user(request)
    if auth_configured and not owner:
        raise HTTPException(403, "Not authenticated")

    memories = memory_manager.load(owner=owner)
    for memory in memories:
        if isinstance(memory, dict) and str(memory.get("id") or "") == ref.entity_id:
            return {
                **_base_resolution(ref, status="resolved", exists=True, reason="memory_found"),
                "target": {
                    "kind": "memory",
                    "read_route": f"/api/memory/{ref.entity_id}",
                    "open_mode": "memory_modal",
                    "category": _safe_optional_label(memory.get("category")),
                    "source": _safe_optional_label(memory.get("source")),
                    "timestamp": memory.get("timestamp"),
                    "pinned": bool(memory.get("pinned")),
                    "has_text": bool(memory.get("text")),
                    "text_redacted": True,
                },
            }
    return _base_resolution(ref, status="not_found", exists=False, reason="memory_not_found")


def _resolve_raptor_ref(ref: InternalReference) -> dict[str, Any]:
    event = _read_raptor_event(ref.entity_id)
    if event:
        return {
            **_base_resolution(ref, status="resolved", exists=True, reason="raptor_event_found"),
            "target": _redacted_raptor_event_target(event),
        }

    return {
        **_base_resolution(ref, status="diagnostics_fallback", exists=False, reason="raptor_event_not_found"),
        "target": {
            "kind": "raptorgraph_provenance",
            "read_route": "/api/diagnostics/memory-provenance?event_type=raptorgraph_mutation",
            "open_mode": "diagnostics_summary",
            "raw_content_visible": False,
            "event_id": ref.entity_id if ref.kind == "raptor_edge" else "",
            "node_id": ref.entity_id if ref.kind == "raptor_node" else "",
        },
    }


def _read_raptor_event(entity_id: str) -> dict[str, Any] | None:
    try:
        from src.universal_inbox_raptorgraph_store import read_universal_inbox_raptorgraph_event

        root = Path(DATA_DIR) / "universal_inbox_raptorgraph"
        return read_universal_inbox_raptorgraph_event(root, entity_id)
    except Exception:
        return None


def _redacted_raptor_event_target(event: dict[str, Any]) -> dict[str, Any]:
    memory_ids = tuple(str(value or "") for value in (event.get("memory_record_ids") or ()) if value)
    return {
        "kind": "raptorgraph_event",
        "read_route": "/api/diagnostics/memory-provenance?event_type=raptorgraph_mutation",
        "open_mode": "diagnostics_summary",
        "event_id": _safe_optional_label(event.get("event_id")),
        "source_provider": _safe_optional_label(event.get("source_provider")),
        "classification": _safe_optional_label(event.get("classification")),
        "document_type": _safe_optional_label(event.get("document_type")),
        "domain": _safe_optional_label(event.get("domain")),
        "memory_record_count": len(memory_ids),
        "memory_record_ids": memory_ids,
        "dsgvo_mode": bool(event.get("dsgvo_mode")),
        "local_only": bool(event.get("local_only")),
        "raw_content_visible": False,
    }


def _base_resolution(ref: InternalReference, *, status: str, exists: bool, reason: str) -> dict[str, Any]:
    return {
        "schema": RESOLUTION_SCHEMA,
        "status": status,
        "exists": bool(exists),
        "reason": reason,
        "ref": ref.to_dict(),
        "raw_content_visible": False,
        "content_redacted": True,
        "path_redacted": True,
        "chat_id_redacted": True,
    }


def _safe_optional_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "password", "cookie")):
        return "redacted"
    if any(ord(ch) < 32 for ch in text):
        return "redacted"
    return text[:120]
