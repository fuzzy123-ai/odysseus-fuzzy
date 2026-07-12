"""Read-only operator dashboard snapshot package contract.

This module normalizes already-produced status payloads. It does not call live
providers, execute actions, inspect private content, or perform writes.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping


OPERATOR_DASHBOARD_SNAPSHOT_SCHEMA = "odysseus.operator_dashboard.snapshot.v1"

SECTION_ORDER = (
    "review_gates",
    "live_affordances",
    "tasks",
    "diagnostics",
    "version_readiness",
    "orchestration",
)

_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9._:-]+")
_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "chat_id",
    "raw",
    "content",
    "body",
    "prompt",
    "reply",
    "path",
    "url",
    "webdav",
    "command",
    "env",
    "filename",
    "file_name",
)


def build_operator_dashboard_snapshot(
    *,
    review_gates: Mapping[str, Any] | None = None,
    live_affordances: Mapping[str, Any] | None = None,
    tasks_summary: Mapping[str, Any] | None = None,
    diagnostics_summary: Mapping[str, Any] | None = None,
    version_readiness: Mapping[str, Any] | None = None,
    orchestration_status: Mapping[str, Any] | None = None,
    last_updated_at: str = "",
) -> dict[str, Any]:
    """Build one redacted operator snapshot from existing status payloads."""

    sections = (
        _review_gates_section(_mapping(review_gates)),
        _live_affordances_section(_mapping(live_affordances)),
        _tasks_section(_mapping(tasks_summary)),
        _diagnostics_section(_mapping(diagnostics_summary)),
        _version_readiness_section(_mapping(version_readiness)),
        _orchestration_section(_mapping(orchestration_status)),
    )
    section_statuses = {section["id"]: section["status"] for section in sections}
    aggregate = _aggregate_counts(sections)
    status = _overall_status(section_statuses, aggregate)
    next_actions = _next_actions(sections)
    evidence_refs = _evidence_refs(sections)
    snapshot_id = _hash(
        {
            "schema": OPERATOR_DASHBOARD_SNAPSHOT_SCHEMA,
            "status": status,
            "sections": section_statuses,
            "counts": aggregate,
        }
    )
    return {
        "schema": OPERATOR_DASHBOARD_SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "status": status,
        "last_updated_at": str(last_updated_at or "").strip(),
        "sections": sections,
        "counts": aggregate,
        "next_actions": next_actions,
        "evidence_refs": evidence_refs,
        "controls": _controls(),
        "raw_content_visible": False,
        "private_content_visible": False,
        "path_values_visible": False,
        "url_values_visible": False,
        "command_values_visible": False,
        "env_values_visible": False,
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "live_probe_performed": False,
        "live_mutation_performed": False,
        "write_action_available": False,
    }


def _review_gates_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    status = _status(payload, fallback="unknown")
    pending = _safe_count(payload.get("pending_count"))
    blocked = _safe_count(payload.get("blocked_count"))
    gate_count = _safe_count(payload.get("gate_count"))
    if not gate_count and isinstance(payload.get("gates"), list):
        gate_count = len(payload["gates"])
    return _section(
        "review_gates",
        _normalize_status(status, blocked=blocked, pending=pending),
        item_count=gate_count,
        pending_count=pending,
        blocked_count=blocked,
        summary="review gates require attention" if pending or blocked else "review gates clear",
        next_action="review_pending_gates" if pending else ("inspect_blocked_gates" if blocked else "none"),
        evidence_source=payload,
    )


def _live_affordances_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    affordances = payload.get("affordances") if isinstance(payload.get("affordances"), list) else []
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), list) else []
    item_count = len(affordances) or len(readiness) or _safe_count(payload.get("affordance_count"))
    blocked = _safe_count(payload.get("blocked_count"))
    pending = _safe_count(payload.get("needs_go_count") or payload.get("pending_count"))
    status = _normalize_status(_status(payload, fallback="unknown"), blocked=blocked, pending=pending)
    return _section(
        "live_affordances",
        status,
        item_count=item_count,
        pending_count=pending,
        blocked_count=blocked,
        summary="live affordances remain gated" if pending or blocked else "live affordances read-only",
        next_action="request_bounded_live_go" if pending else ("inspect_blocked_live_gates" if blocked else "none"),
        evidence_source=payload,
    )


def _tasks_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    open_count = _safe_count(payload.get("open_count") or payload.get("pending_count"))
    overdue_count = _safe_count(payload.get("overdue_count"))
    due_count = _safe_count(payload.get("due_count") or payload.get("due_today_count"))
    item_count = _safe_count(payload.get("total_count")) or open_count + overdue_count + due_count
    status = "blocked" if overdue_count else ("pending" if open_count or due_count else _status(payload, fallback="clear"))
    return _section(
        "tasks",
        status,
        item_count=item_count,
        pending_count=open_count + due_count,
        blocked_count=overdue_count,
        summary="tasks need operator attention" if open_count or due_count or overdue_count else "no task attention needed",
        next_action="review_due_tasks" if open_count or due_count or overdue_count else "none",
        evidence_source=payload,
    )


def _diagnostics_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    alerts = _safe_count(payload.get("alert_count") or payload.get("active_alert_count"))
    endpoint_count = _safe_count(payload.get("endpoint_count"))
    item_count = endpoint_count or _safe_count(payload.get("collector_count"))
    status = _normalize_status(_status(payload, fallback="unknown"), blocked=0, pending=alerts)
    return _section(
        "diagnostics",
        status,
        item_count=item_count,
        pending_count=alerts,
        blocked_count=0,
        summary="diagnostics have warnings" if alerts else "diagnostics available",
        next_action="inspect_diagnostics" if alerts or status in {"warn", "unknown"} else "none",
        evidence_source=payload,
    )


def _version_readiness_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    blockers = _safe_count(payload.get("blocker_count") or payload.get("blocking_count"))
    pending = _safe_count(payload.get("pending_count") or payload.get("remaining_count"))
    percent = _safe_count(payload.get("overall_percent") or payload.get("progress_percent"))
    status = _normalize_status(_status(payload, fallback="unknown"), blocked=blockers, pending=pending)
    return _section(
        "version_readiness",
        status,
        item_count=_safe_count(payload.get("roadmap_count")) or (1 if payload else 0),
        pending_count=pending,
        blocked_count=blockers,
        summary=f"version readiness {percent} percent" if percent else "version readiness unknown",
        next_action="clear_version_readiness_blockers" if blockers else ("continue_readiness_work" if pending else "none"),
        evidence_source=payload,
        extra={"progress_percent": percent},
    )


def _orchestration_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    blocking = _safe_count(payload.get("blocking_item_count"))
    if not blocking and isinstance(payload.get("blocking_items"), list):
        blocking = len(payload["blocking_items"])
    pending = _safe_count(payload.get("next_action_count"))
    if not pending and isinstance(payload.get("next_actions"), list):
        pending = len(payload["next_actions"])
    progress = _safe_count(payload.get("progress_percent") or payload.get("overall_progress_percent"))
    return _section(
        "orchestration",
        _normalize_status(_status(payload, fallback="unknown"), blocked=blocking, pending=pending),
        item_count=_safe_count(payload.get("agent_path_count")) or _safe_count(payload.get("count")),
        pending_count=pending,
        blocked_count=blocking,
        summary=f"orchestration progress {progress} percent" if progress else "orchestration status unknown",
        next_action="resolve_orchestration_blocker" if blocking else ("continue_next_orchestration_action" if pending else "none"),
        evidence_source=payload,
        extra={"progress_percent": progress},
    )


def _section(
    section_id: str,
    status: str,
    *,
    item_count: int,
    pending_count: int,
    blocked_count: int,
    summary: str,
    next_action: str,
    evidence_source: Mapping[str, Any],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "id": section_id,
        "status": _safe_token(status) or "unknown",
        "item_count": _safe_count(item_count),
        "pending_count": _safe_count(pending_count),
        "blocked_count": _safe_count(blocked_count),
        "summary": summary,
        "next_action": _safe_token(next_action) or "none",
        "source_schema": _safe_schema(evidence_source.get("schema")),
        "raw_content_visible": False,
        "private_content_visible": False,
        "path_values_visible": False,
        "url_values_visible": False,
        "command_values_visible": False,
        "token_value_visible": False,
        "chat_id_value_visible": False,
    }
    if extra:
        payload.update({key: value for key, value in extra.items() if not _sensitive_key(key)})
    return payload


def _aggregate_counts(sections: tuple[dict[str, Any], ...]) -> dict[str, int]:
    return {
        "section_count": len(sections),
        "pending_count": sum(_safe_count(section.get("pending_count")) for section in sections),
        "blocked_count": sum(_safe_count(section.get("blocked_count")) for section in sections),
        "attention_count": sum(
            1
            for section in sections
            if section.get("status") in {"blocked", "pending", "review", "warn", "unknown"}
        ),
    }


def _overall_status(section_statuses: Mapping[str, str], counts: Mapping[str, int]) -> str:
    statuses = set(section_statuses.values())
    if _safe_count(counts.get("blocked_count")) or "blocked" in statuses:
        return "blocked"
    if _safe_count(counts.get("pending_count")) or statuses & {"pending", "review", "warn"}:
        return "attention"
    if "unknown" in statuses:
        return "partial"
    return "ok"


def _next_actions(sections: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    actions: list[dict[str, Any]] = []
    for section in sections:
        action = str(section.get("next_action") or "none")
        if action == "none":
            continue
        actions.append(
            {
                "rank": len(actions) + 1,
                "section_id": section["id"],
                "action": action,
                "mode": "read_only",
                "requires_live_go": action in {"request_bounded_live_go", "inspect_blocked_live_gates"},
                "write_action_enabled": False,
            }
        )
    return tuple(actions)


def _evidence_refs(sections: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for section in sections:
        refs.append(
            {
                "section_id": section["id"],
                "source_schema": section.get("source_schema") or "",
                "status": section["status"],
                "ref_hash": _hash(
                    {
                        "section_id": section["id"],
                        "source_schema": section.get("source_schema") or "",
                        "status": section["status"],
                    }
                ),
            }
        )
    return tuple(refs)


def _controls() -> dict[str, dict[str, Any]]:
    gated = {"state": "disabled", "reason": "operator dashboard snapshot is read-only"}
    live = {"state": "policy_gated", "reason": "requires explicit bounded live Go"}
    return {
        "approve": dict(live),
        "execute": dict(live),
        "retry": dict(gated),
        "dismiss": dict(gated),
    }


def _normalize_status(status: str, *, blocked: int, pending: int) -> str:
    token = _safe_token(status)
    if blocked:
        return "blocked"
    if pending:
        return "pending"
    if token in {"ok", "clear", "healthy", "ready", "completed", "available"}:
        return "ok"
    if token in {"blocked", "failed", "critical", "error"}:
        return "blocked"
    if token in {"pending", "review", "needs_review", "warn", "warning", "waiting", "partial"}:
        return "pending" if token != "warn" else "warn"
    return token or "unknown"


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _status(payload: Mapping[str, Any], *, fallback: str) -> str:
    return _safe_token(payload.get("status") or payload.get("state") or payload.get("plan_status") or fallback)


def _safe_schema(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.startswith("odysseus.") and not _sensitive_text(text) else ""


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _SAFE_TOKEN_RE.sub("_", text).strip("_")
    return text[:80]


def _sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sensitive_text(value: Any) -> bool:
    text = str(value or "").lower()
    return any(part in text for part in ("secret", "token", "password", "credential"))


def _hash(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8", errors="replace")).hexdigest()[:24]
