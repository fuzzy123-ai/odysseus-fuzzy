"""Stable internal link targets for Memory and RaptorGraph objects."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from src.internal_references import build_internal_reference_dict, reference_markdown


class InternalReferenceLinksError(ValueError):
    """Raised when an internal link target would be unsafe."""


def build_knowledge_link_targets(records: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Return UI-ready internal links for Memory/RaptorGraph records."""

    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise InternalReferenceLinksError("record must be a mapping")
        _reject_unsafe_payload(record)
        record_id = _safe_id(record.get("candidate_id") or record.get("memory_id") or record.get("node_id") or record.get("edge_id"))
        kind = _kind_for_record(record)
        label = _safe_label(record.get("title") or record.get("label") or record.get("relation") or "Oeffnen")
        ref = build_internal_reference_dict(kind, record_id, label=label)
        result.append(
            {
                "record_id": record_id,
                "kind": kind,
                "label": label,
                "uri": ref["uri"],
                "chat_href": ref["chat_href"],
                "markdown": reference_markdown(ref),
                "raw_content_visible": False,
            }
        )
    return tuple(result)


def _kind_for_record(record: Mapping[str, Any]) -> str:
    if record.get("edge_id"):
        return "raptor_edge"
    if record.get("node_id"):
        return "raptor_node"
    return "memory"


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160:
        raise InternalReferenceLinksError("record id is invalid")
    if any(ord(ch) < 32 for ch in text):
        raise InternalReferenceLinksError("record id contains control characters")
    if re.search(r"[A-Za-z]:[\\/]|[\\/]{2,}|api[_-]?key|password|bearer\s+", text, re.IGNORECASE):
        raise InternalReferenceLinksError("record id looks unsafe")
    return text


def _safe_label(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())[:80]
    if not text:
        return "Oeffnen"
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "password", "cookie")):
        raise InternalReferenceLinksError("label contains forbidden marker")
    if re.search(r"[\r\n<>]", text):
        raise InternalReferenceLinksError("label contains unsafe characters")
    return text


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    forbidden_keys = {"html", "raw_html", "body", "payload", "bytes", "chat_id", "file_id", "token", "secret", "raw_text"}
    for key, value in payload.items():
        key_text = str(key).lower()
        if key_text in forbidden_keys:
            raise InternalReferenceLinksError(f"unsafe field: {key_text}")
        if isinstance(value, Mapping):
            _reject_unsafe_payload(value)
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise InternalReferenceLinksError("payload contains forbidden marker")
