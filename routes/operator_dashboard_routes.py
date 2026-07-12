"""Admin-gated read-only operator dashboard route."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Request

import core.database as cdb
from core.middleware import require_admin
from plugins.system_health_checker.health_model import build_agent_offline_snapshot
from src.constants import APP_VERSION, DATA_DIR
from src.live_affordance_readiness import build_live_affordance_readiness
from src.operator_dashboard import build_operator_dashboard_snapshot, build_operator_review_queue
from src.operator_quick_status import build_operator_quick_status
from src.task_summary import summarize_tasks
from src.version_one_readiness import load_version_one_readiness


OPERATOR_DASHBOARD_ROUTE_SCHEMA = "odysseus.operator_dashboard.route.v1"


def setup_operator_dashboard_routes(
    *,
    mcp_manager: Any = None,
    telegram_data_dir: str | Path | None = None,
    review_gate_provider: Callable[[], Mapping[str, Any]] | None = None,
    live_affordance_provider: Callable[[], Mapping[str, Any]] | None = None,
    tasks_summary_provider: Callable[[], Mapping[str, Any]] | None = None,
    diagnostics_summary_provider: Callable[[], Mapping[str, Any]] | None = None,
    version_readiness_provider: Callable[[], Mapping[str, Any]] | None = None,
    orchestration_status_provider: Callable[[], Mapping[str, Any]] | None = None,
    coding_approvals_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
    security_reviews_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["operator-dashboard"])

    @router.get("/api/operator-dashboard/snapshot")
    def operator_dashboard_snapshot(request: Request):
        require_admin(request)
        review_gates = _safe_provider(
            review_gate_provider,
            fallback=lambda: _default_review_gate_status(telegram_data_dir),
        )
        live_affordances = _safe_provider(
            live_affordance_provider,
            fallback=lambda: build_live_affordance_readiness(),
        )
        tasks_summary = _safe_provider(tasks_summary_provider, fallback=_default_tasks_summary)
        diagnostics_summary = _safe_provider(
            diagnostics_summary_provider,
            fallback=lambda: _default_diagnostics_summary(mcp_manager),
        )
        version_readiness = _safe_provider(
            version_readiness_provider,
            fallback=load_version_one_readiness,
        )
        orchestration_status = _safe_provider(orchestration_status_provider)
        coding_approvals = _safe_list_provider(coding_approvals_provider)
        security_reviews = _safe_list_provider(security_reviews_provider)

        snapshot = build_operator_dashboard_snapshot(
            review_gates=review_gates,
            live_affordances=live_affordances,
            tasks_summary=tasks_summary,
            diagnostics_summary=diagnostics_summary,
            version_readiness=version_readiness,
            orchestration_status=orchestration_status,
            last_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        review_queue = build_operator_review_queue(
            review_gate_status=review_gates,
            live_affordance_readiness=live_affordances,
            coding_approvals=coding_approvals,
            security_reviews=security_reviews,
        )
        return {
            "schema": OPERATOR_DASHBOARD_ROUTE_SCHEMA,
            "snapshot": snapshot,
            "review_queue": review_queue,
            "raw_content_visible": False,
            "private_content_visible": False,
            "path_values_visible": False,
            "url_values_visible": False,
            "command_values_visible": False,
            "token_value_visible": False,
            "chat_id_value_visible": False,
            "live_probe_performed": False,
            "live_mutation_performed": False,
            "write_action_enabled": False,
        }

    return router


def _safe_provider(
    provider: Callable[[], Mapping[str, Any]] | None,
    *,
    fallback: Callable[[], Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    fn = provider or fallback
    if fn is None:
        return {}
    try:
        value = fn()
    except Exception:
        return {"status": "unknown", "provider_failed": True}
    return value if isinstance(value, Mapping) else {}


def _safe_list_provider(
    provider: Callable[[], list[Mapping[str, Any]]] | None,
) -> list[Mapping[str, Any]]:
    if provider is None:
        return []
    try:
        values = provider()
    except Exception:
        return []
    return [value for value in values if isinstance(value, Mapping)] if isinstance(values, list) else []


def _default_review_gate_status(telegram_data_dir: str | Path | None = None) -> Mapping[str, Any]:
    from routes.review_gate_routes import REVIEW_GATE_SCHEMA, _build_gate_list, _load_telegram_store

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


def _default_tasks_summary() -> Mapping[str, Any]:
    db = cdb.SessionLocal()
    try:
        tasks = db.query(cdb.ScheduledTask).order_by(cdb.ScheduledTask.created_at.desc()).limit(200).all()
        payload = summarize_tasks(tasks)
        payload["owner_scoped"] = False
        payload["limit"] = 200
        return payload
    finally:
        db.close()


def _default_diagnostics_summary(mcp_manager: Any = None) -> Mapping[str, Any]:
    db = cdb.SessionLocal()
    try:
        servers = db.query(cdb.McpServer).all()
    finally:
        db.close()
    observed_at = datetime.now(timezone.utc).isoformat()
    system_health = build_agent_offline_snapshot(observed_at=observed_at).to_dict()
    return build_operator_quick_status(
        mcp_manager=mcp_manager,
        mcp_servers=servers,
        system_health=system_health,
        app_version=APP_VERSION,
    )
