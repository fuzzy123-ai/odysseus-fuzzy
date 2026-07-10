"""Redacted audit event model for MCP Workbench access."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.mcp_policy_preview import GATES_BY_CATEGORY
from src.mcp_server_tool_policy import McpToolPolicyOptions, classify_mcp_tool
from src.runtime_event_envelope import stable_payload_hash


MCP_AUDIT_EVENT_SCHEMA = "odysseus.mcp.audit_event.v1"
PLANNING_SECTION_AUDIT_SCHEMA = "odysseus.planning.section_context_audit_descriptor.v1"

ALLOWED_AUDIT_STATUSES = frozenset({
    "ok",
    "error",
    "blocked",
    "preview",
    "disabled",
})


class McpAuditEventError(ValueError):
    """Raised when an MCP audit event cannot be safely constructed."""


def _safe_token(value: Any, *, fallback: str, max_chars: int = 120) -> str:
    text = str(value if value is not None else fallback).strip()
    if not text:
        text = fallback
    token = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", text).strip("._-")
    return (token or fallback)[:max_chars]


def _safe_text(value: Any, *, max_chars: int = 180) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(
        r"(?i)(token|secret|password|api[_-]?key|authorization|chat[_-]?id)\s*[:=]\s*[^\s,}]+",
        "[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\b[A-Z]:[\\/][^\s\"'<>]+|\\\\[^\s\"'<>]+|/(?:home|Users|private|var|tmp)/[^\s\"'<>]+",
        "[redacted]",
        text,
    )
    return text[:max_chars]


def _safe_client_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if (
        re.search(r"[A-Za-z]:[\\/]|^/|^\\\\|/(?:home|Users|private|var|tmp)/", text, re.IGNORECASE)
        or any(marker in lowered for marker in ("token=", "secret=", "password=", "authorization"))
    ):
        return "client:" + stable_payload_hash(text).split(":", 1)[-1][:16]
    return _safe_token(text, fallback="external-mcp", max_chars=80)


def _argument_summary(arguments: Any) -> tuple[tuple[str, ...], int, str]:
    if not isinstance(arguments, Mapping):
        shape: dict[str, str] = {}
        return (), 0, stable_payload_hash(shape)
    fields: list[str] = []
    shape: dict[str, str] = {}
    for key, value in sorted(arguments.items(), key=lambda item: str(item[0]))[:24]:
        field = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(key or "argument")).strip("._-")[:60] or "argument"
        if field in fields:
            continue
        fields.append(field)
        if isinstance(value, Mapping):
            value_type = "object"
        elif isinstance(value, (list, tuple, set)):
            value_type = "array"
        elif value is None:
            value_type = "null"
        elif isinstance(value, bool):
            value_type = "boolean"
        elif isinstance(value, (int, float)):
            value_type = "number"
        else:
            value_type = "string"
        shape[field] = value_type
    return tuple(fields), min(len(arguments), 999), stable_payload_hash({"fields": fields, "shape": shape})


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value in (None, ""):
        parsed = datetime.now(timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise McpAuditEventError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _metadata_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return "[redacted-structured-value]"
    return _safe_text(value, max_chars=120)


def _safe_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, item in list(value.items())[:12]:
        safe_key = _safe_token(key, fallback="metadata", max_chars=40)
        normalized = re.sub(r"[^a-z0-9]", "", safe_key.lower())
        if normalized in {"token", "apikey", "authorization", "secret", "password", "chatid"}:
            out[safe_key] = "[redacted]"
        else:
            out[safe_key] = _metadata_value(item)
    return out


@dataclass(frozen=True)
class McpAuditEvent:
    method: str
    status: str
    tool_name: str = ""
    client_id: str = ""
    category: str = ""
    reason: str = ""
    required_gate: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int = 0
    metadata: Mapping[str, str] = field(default_factory=dict)
    argument_fields: tuple[str, ...] = field(default_factory=tuple)
    argument_count: int = 0
    argument_hash: str = ""

    @classmethod
    def create(
        cls,
        *,
        method: Any,
        status: Any,
        tool_name: Any = "",
        client_id: Any = "",
        reason: Any = "",
        timestamp: Any = None,
        duration_ms: Any = 0,
        metadata: Any = None,
        arguments: Any = None,
        options: McpToolPolicyOptions | None = None,
    ) -> "McpAuditEvent":
        safe_status = _safe_token(status, fallback="preview", max_chars=40).lower()
        if safe_status not in ALLOWED_AUDIT_STATUSES:
            safe_status = "error"
        safe_tool = _safe_token(tool_name, fallback="", max_chars=120) if tool_name else ""
        category = ""
        required_gate = ""
        if safe_tool:
            decision = classify_mcp_tool(safe_tool, options)
            category = decision.category
            if not decision.exposed:
                required_gate = GATES_BY_CATEGORY.get(decision.category, "")
                if decision.category == "planning_readonly":
                    required_gate = "PLANNING-MCP-READONLY-GO"
        try:
            duration = int(duration_ms or 0)
        except (TypeError, ValueError):
            duration = 0
        argument_fields, argument_count, argument_hash = _argument_summary(arguments)
        return cls(
            method=_safe_token(method, fallback="unknown_method", max_chars=120),
            status=safe_status,
            tool_name=safe_tool,
            client_id=_safe_client_ref(client_id),
            category=category,
            reason=_safe_text(reason, max_chars=160),
            required_gate=required_gate,
            timestamp=_parse_time(timestamp),
            duration_ms=max(0, duration),
            metadata=_safe_metadata(metadata),
            argument_fields=argument_fields,
            argument_count=argument_count,
            argument_hash=argument_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MCP_AUDIT_EVENT_SCHEMA,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "method": self.method,
            "status": self.status,
            "tool_name": self.tool_name,
            "client_id": self.client_id,
            "category": self.category,
            "reason": self.reason,
            "required_gate": self.required_gate,
            "duration_ms": self.duration_ms,
            "metadata": dict(self.metadata),
            "argument_fields": self.argument_fields,
            "argument_count": self.argument_count,
            "argument_hash": self.argument_hash,
            "raw_arguments_visible": False,
            "token_value_visible": False,
            "secret_value_visible": False,
            "live_client_connection_allowed": False,
        }


def build_mcp_audit_event(payload: Mapping[str, Any]) -> McpAuditEvent:
    if not isinstance(payload, Mapping):
        raise McpAuditEventError("audit payload must be an object")
    return McpAuditEvent.create(
        method=payload.get("method"),
        status=payload.get("status"),
        tool_name=payload.get("tool") or payload.get("tool_name") or "",
        client_id=payload.get("client_id") or "",
        reason=payload.get("reason") or "",
        timestamp=payload.get("timestamp"),
        duration_ms=payload.get("duration_ms") or 0,
        metadata=payload.get("metadata") or {},
        arguments=payload.get("arguments") or {},
        options=payload.get("options") if isinstance(payload.get("options"), McpToolPolicyOptions) else None,
    )


def build_planning_section_audit_descriptor(
    *,
    client_id: Any = "",
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a value-free descriptor for one section-context read."""

    allowed_fields = {
        "project_id", "roadmap_id", "section_id", "item_id", "task_id", "gate_id",
        "max_items", "include_memory",
    }
    source = arguments if isinstance(arguments, Mapping) else {}
    shape_source = {key: source[key] for key in source if key in allowed_fields}
    argument_fields, argument_count, argument_hash = _argument_summary(shape_source)
    safe_client = _safe_client_ref(client_id)
    descriptor_material = {
        "client_id": safe_client,
        "tool_name": "planning_get_section_context_pack",
        "argument_fields": argument_fields,
        "argument_hash": argument_hash,
    }
    return {
        "schema": PLANNING_SECTION_AUDIT_SCHEMA,
        "descriptor_id": "planning-section-audit-" + stable_payload_hash(descriptor_material).split(":", 1)[-1][:20],
        "client_id": safe_client,
        "tool_name": "planning_get_section_context_pack",
        "argument_fields": argument_fields,
        "argument_count": argument_count,
        "argument_hash": argument_hash,
        "persisted": False,
        "events_emitted": False,
        "raw_arguments_visible": False,
        "section_values_visible": False,
        "private_paths_visible": False,
        "token_value_visible": False,
        "secret_value_visible": False,
    }
