"""Canonical internal references for Odysseus knowledge objects.

These references are UI/navigation metadata only. They must not carry raw
document text, host paths, tokens, chat ids, or provider output.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import re
from typing import Any, Mapping
from urllib.parse import quote, unquote


INTERNAL_REFERENCE_SCHEMA = "odysseus.internal_reference.v1"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$")
_SAFE_LABEL_RE = re.compile(r"^[^\r\n<>]{0,80}$")
_FORBIDDEN_ID_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\\/]{2,}|api[_-]?key|password|bearer\s+[A-Za-z0-9._-]{8,})",
    re.IGNORECASE,
)

_KINDS: dict[str, tuple[str, str]] = {
    "memory": ("memory", "memory"),
    "raptor_node": ("raptor/node", "raptor-node"),
    "raptor_edge": ("raptor/edge", "raptor-edge"),
    "rag_source": ("rag/source", "rag-source"),
    "rag_chunk": ("rag/chunk", "rag-chunk"),
    "graph_node": ("graph/node", "graph-node"),
    "graph_edge": ("graph/edge", "graph-edge"),
    "graph_query": ("graph/query", "graph-query"),
}


class InternalReferenceError(ValueError):
    """Raised when an Odysseus internal reference would be unsafe."""


@dataclass(frozen=True, slots=True)
class InternalReference:
    kind: str
    entity_id: str
    uri: str
    chat_href: str
    label: str
    raw_content_visible: bool = False
    schema: str = INTERNAL_REFERENCE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "kind": self.kind,
            "entity_id": self.entity_id,
            "uri": self.uri,
            "chat_href": self.chat_href,
            "label": self.label,
            "raw_content_visible": False,
        }


def build_internal_reference(kind: str, entity_id: Any, *, label: str | None = None) -> InternalReference:
    normalized_kind = _normalize_kind(kind)
    normalized_id = _normalize_id(entity_id)
    uri_path, href_prefix = _KINDS[normalized_kind]
    safe_label = _normalize_label(label or _default_label(normalized_kind))
    return InternalReference(
        kind=normalized_kind,
        entity_id=normalized_id,
        uri=f"odysseus://{uri_path}/{quote(normalized_id, safe='')}",
        chat_href=f"#{href_prefix}-{_anchor_id(normalized_id)}",
        label=safe_label,
    )


def build_internal_reference_dict(kind: str, entity_id: Any, *, label: str | None = None) -> dict[str, Any]:
    return build_internal_reference(kind, entity_id, label=label).to_dict()


def parse_internal_uri(uri: str) -> InternalReference:
    value = str(uri or "").strip()
    if not value.startswith("odysseus://"):
        raise InternalReferenceError("internal uri must start with odysseus://")
    body = value.removeprefix("odysseus://")
    for kind, (uri_path, _href_prefix) in _KINDS.items():
        prefix = f"{uri_path}/"
        if body.startswith(prefix):
            return build_internal_reference(kind, unquote(body.removeprefix(prefix)))
    raise InternalReferenceError("unknown internal uri kind")


def parse_chat_href(href: str) -> InternalReference:
    value = str(href or "").strip()
    if not value.startswith("#"):
        raise InternalReferenceError("chat href must start with #")
    body = value[1:]
    for kind, (_uri_path, href_prefix) in _KINDS.items():
        prefix = f"{href_prefix}-"
        if body.startswith(prefix):
            return build_internal_reference(kind, _decode_anchor_id(body.removeprefix(prefix)))
    raise InternalReferenceError("unknown chat href kind")


def reference_markdown(ref: InternalReference | Mapping[str, Any]) -> str:
    payload = ref.to_dict() if isinstance(ref, InternalReference) else dict(ref)
    label = _normalize_label(payload.get("label") or _default_label(str(payload.get("kind") or "")))
    href = str(payload.get("chat_href") or "")
    parsed = parse_chat_href(href)
    return f"[{label}]({parsed.chat_href})"


def _normalize_kind(kind: str) -> str:
    value = str(kind or "").strip().lower().replace("-", "_")
    if value not in _KINDS:
        raise InternalReferenceError(f"unsupported internal reference kind: {kind}")
    return value


def _normalize_id(entity_id: Any) -> str:
    value = str(entity_id or "").strip()
    if not value:
        raise InternalReferenceError("entity_id must not be empty")
    if len(value) > 160:
        raise InternalReferenceError("entity_id is too long")
    if any(ord(ch) < 32 for ch in value):
        raise InternalReferenceError("entity_id contains control characters")
    if _FORBIDDEN_ID_RE.search(value):
        raise InternalReferenceError("entity_id looks like a path or secret")
    return value


def _normalize_label(label: str) -> str:
    value = str(label or "").strip()[:80]
    if not _SAFE_LABEL_RE.fullmatch(value):
        raise InternalReferenceError("label contains unsafe characters")
    return value or "Open"


def _anchor_id(entity_id: str) -> str:
    if _SAFE_ID_RE.fullmatch(entity_id):
        return entity_id
    encoded = base64.urlsafe_b64encode(entity_id.encode("utf-8")).decode("ascii").rstrip("=")
    return f"b64-{encoded}"


def _decode_anchor_id(anchor_id: str) -> str:
    value = str(anchor_id or "")
    if value.startswith("b64-"):
        raw = value.removeprefix("b64-")
        padding = "=" * (-len(raw) % 4)
        try:
            return base64.urlsafe_b64decode((raw + padding).encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise InternalReferenceError("invalid encoded anchor id") from exc
    return value


def _default_label(kind: str) -> str:
    return {
        "memory": "Memory oeffnen",
        "raptor_node": "Raptor-Knoten oeffnen",
        "raptor_edge": "Raptor-Kante oeffnen",
        "rag_source": "RAG-Quelle oeffnen",
        "rag_chunk": "RAG-Chunk oeffnen",
        "graph_node": "Graph-Knoten oeffnen",
        "graph_edge": "Graph-Kante oeffnen",
        "graph_query": "Graph-Abfrage oeffnen",
    }.get(kind, "Oeffnen")
