"""Machine-readable contracts for legacy chat feature wiring."""

from __future__ import annotations

from typing import Any


LEGACY_CHAT_CONTRACTS_SCHEMA = "odysseus.legacy_chat.contracts.v1"


LEGACY_CHAT_CONTRACTS: tuple[dict[str, Any], ...] = (
    {
        "slice_id": "LC1",
        "title": "Secure mode chat indicator",
        "surface": "inline_plus_slash",
        "ui_hook": "chat status chip and slash reply",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/security/dsgvo/status", "action": "read"},
            {"method": "POST", "path": "/api/security/dsgvo/toggle", "action": "admin_mutation"},
            {"method": "POST", "path": "/api/security/dsgvo", "action": "admin_mutation"},
        ),
    },
    {
        "slice_id": "LC2",
        "title": "Attachment processing status",
        "surface": "attachment_status",
        "ui_hook": "attachment chips and user-message footer",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/universal-inbox/items/{source_ref}/status", "action": "read"},
        ),
    },
    {
        "slice_id": "LC3",
        "title": "Memory and RaptorGraph clickable refs",
        "surface": "internal_links",
        "ui_hook": "message renderer and memory modal",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/internal-refs/resolve?ref={internal_ref}", "action": "read"},
        ),
    },
    {
        "slice_id": "LC4",
        "title": "Review and write gates",
        "surface": "gate_action_row",
        "ui_hook": "tool-result block and inline action row",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/review-gates/status", "action": "read"},
        ),
    },
    {
        "slice_id": "LC5",
        "title": "Task and reminder feedback",
        "surface": "task_summary",
        "ui_hook": "slash command and task result block",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/tasks/summary", "action": "read"},
        ),
    },
    {
        "slice_id": "LC6",
        "title": "File export intent preview",
        "surface": "export_plan_preview",
        "ui_hook": "attachment follow-up result block",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/universal-file-io/capabilities", "action": "read"},
            {"method": "POST", "path": "/api/universal-file-io/export-plan", "action": "plan_only"},
        ),
    },
    {
        "slice_id": "LC7",
        "title": "MCP and system health quick status",
        "surface": "operator_quick_status",
        "ui_hook": "slash command result block",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/diagnostics/quick-status", "action": "admin_read"},
        ),
    },
    {
        "slice_id": "LC8",
        "title": "Coding-agent lightweight entry",
        "surface": "coding_task_card",
        "ui_hook": "slash command and compact chat task card",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/coding-agent/quick-entry", "action": "admin_read"},
        ),
    },
    {
        "slice_id": "LC9",
        "title": "Diagnostics surfaces",
        "surface": "diagnostics_summary",
        "ui_hook": "slash command summaries",
        "status": "backend_ready",
        "endpoints": (
            {"method": "GET", "path": "/api/diagnostics/quick-summary", "action": "admin_read"},
        ),
    },
    {
        "slice_id": "LC10",
        "title": "Live delivery and converter affordances",
        "surface": "live_affordance_gates",
        "ui_hook": "disabled or gated buttons",
        "status": "backend_ready_live_gated",
        "endpoints": (
            {"method": "GET", "path": "/api/live-affordances/readiness", "action": "admin_read"},
        ),
    },
)


def build_legacy_chat_contracts() -> dict[str, Any]:
    contracts = tuple(_safe_contract(item) for item in LEGACY_CHAT_CONTRACTS)
    return {
        "schema": LEGACY_CHAT_CONTRACTS_SCHEMA,
        "status": "backend_ready",
        "contract_count": len(contracts),
        "contracts": contracts,
        "ui_execution_required": True,
        "ui_code_included": False,
        "live_execution_performed": False,
        "raw_content_visible": False,
        "host_paths_visible": False,
        "token_values_visible": False,
        "chat_id_values_visible": False,
        "private_values_visible": False,
    }


def _safe_contract(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "slice_id": _safe_token(item.get("slice_id")),
        "title": _safe_label(item.get("title")),
        "surface": _safe_token(item.get("surface")),
        "ui_hook": _safe_label(item.get("ui_hook")),
        "status": _safe_token(item.get("status")),
        "endpoints": tuple(_safe_endpoint(endpoint) for endpoint in item.get("endpoints") or ()),
        "raw_values_visible": False,
    }


def _safe_endpoint(endpoint: dict[str, Any]) -> dict[str, str]:
    return {
        "method": _safe_method(endpoint.get("method")),
        "path": _safe_path(endpoint.get("path")),
        "action": _safe_token(endpoint.get("action")),
    }


def _safe_method(value: Any) -> str:
    token = _safe_token(value).upper()
    return token if token in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "GET"


def _safe_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path.startswith("/api/"):
        return "/api/redacted"
    return "".join(ch for ch in path if ch.isalnum() or ch in "/{}?=_-")[:220]


def _safe_label(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in " -_/()")[:160]


def _safe_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in "._:-")[:100]
