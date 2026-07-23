"""Admin-gated read-only Harbor One workspace snapshot route."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Request

from core.middleware import require_admin
from plugins.system_health_checker.health_model import build_agent_offline_snapshot
from src.agent_sandbox_contract import DEFAULT_SANDBOX_CAPABILITIES, SANDBOX_JOB_SCHEMA
from src.clarification_attention import build_workspace_clarification_status
from src.constants import APP_VERSION
from src.live_affordance_readiness import build_live_affordance_readiness
from src.local_model_memory_status import build_local_model_memory_status
from src.operator_dashboard import build_operator_dashboard_snapshot
from src.operator_quick_status import build_operator_quick_status
from src.version_one_readiness import load_version_one_readiness
from src.workspace_snapshot import WORKSPACE_SNAPSHOT_SCHEMA, build_workspace_snapshot


WORKSPACE_SNAPSHOT_ROUTE_SCHEMA = "odysseus.workspace_snapshot.route.v1"


def setup_workspace_snapshot_routes(
    *,
    mcp_manager: Any = None,
    operator_provider: Callable[[], Mapping[str, Any]] | None = None,
    projects_provider: Callable[[], Mapping[str, Any]] | None = None,
    clarification_provider: Callable[[], Mapping[str, Any]] | None = None,
    planning_provider: Callable[[], Mapping[str, Any]] | None = None,
    coding_provider: Callable[[], Mapping[str, Any]] | None = None,
    sandbox_provider: Callable[[], Mapping[str, Any]] | None = None,
    knowledge_provider: Callable[[], Mapping[str, Any]] | None = None,
    local_model_provider: Callable[[], Mapping[str, Any]] | None = None,
    inbox_provider: Callable[[], Mapping[str, Any]] | None = None,
    release_provider: Callable[[], Mapping[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["workspace-snapshot"])

    @router.get("/api/workspace/snapshot")
    def workspace_snapshot(request: Request):
        require_admin(request)
        snapshot = build_workspace_snapshot(
            operator_status=_safe_provider(operator_provider, fallback=lambda: _default_operator_status(mcp_manager)),
            projects_status=_safe_provider(projects_provider, fallback=_default_unavailable("projects", "project snapshot adapter not connected")),
            clarification_status=_safe_provider(
                clarification_provider,
                fallback=lambda: build_workspace_clarification_status(
                    owner=getattr(request.state, "current_user", None) or None,
                ),
            ),
            planning_status=_safe_provider(planning_provider, fallback=_default_unavailable("planning", "planning snapshot adapter not connected")),
            coding_status=_safe_provider(coding_provider, fallback=_default_unavailable("coding", "coding runner snapshot adapter not connected")),
            sandbox_status=_safe_provider(sandbox_provider, fallback=_default_sandbox_status),
            knowledge_status=_safe_provider(knowledge_provider, fallback=_default_unavailable("knowledge", "knowledge snapshot adapter not connected")),
            local_model_status=_safe_provider(local_model_provider, fallback=_default_local_model_status),
            inbox_status=_safe_provider(inbox_provider, fallback=_default_unavailable("inbox", "inbox snapshot adapter not connected")),
            release_status=_safe_provider(release_provider, fallback=_default_release_status),
            generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
        return {
            "schema": WORKSPACE_SNAPSHOT_ROUTE_SCHEMA,
            "snapshot": snapshot,
            "snapshot_schema": WORKSPACE_SNAPSHOT_SCHEMA,
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
    fallback: Callable[[], Mapping[str, Any]],
) -> Mapping[str, Any]:
    fn = provider or fallback
    try:
        value = fn()
    except Exception:
        return {"schema": "odysseus.workspace_section.v1", "state": "partial", "status": "unknown", "provider_failed": True}
    return value if isinstance(value, Mapping) else {}


def _default_unavailable(section_id: str, reason: str) -> Callable[[], Mapping[str, Any]]:
    def _provider() -> Mapping[str, Any]:
        return {
            "schema": "odysseus.workspace_section.v1",
            "state": "unavailable",
            "status": "unavailable",
            "reason_unavailable": reason,
            "source_ref": f"{section_id}:not_connected",
        }

    return _provider


def _default_operator_status(mcp_manager: Any = None) -> Mapping[str, Any]:
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    diagnostics = build_operator_quick_status(
        mcp_manager=mcp_manager,
        mcp_servers=[],
        system_health=build_agent_offline_snapshot(observed_at=observed_at).to_dict(),
        app_version=APP_VERSION,
    )
    return build_operator_dashboard_snapshot(
        diagnostics_summary=diagnostics,
        live_affordances=build_live_affordance_readiness(),
        version_readiness=load_version_one_readiness(),
        last_updated_at=observed_at,
    )


def _default_sandbox_status() -> Mapping[str, Any]:
    return {
        "schema": SANDBOX_JOB_SCHEMA,
        "state": "partial",
        "status": "ready",
        "summary": "sandbox capability contract available; execution remains policy gated",
        "item_count": len(DEFAULT_SANDBOX_CAPABILITIES),
        "source_ref": "sandbox:contract",
    }


def _default_local_model_status() -> Mapping[str, Any]:
    return build_local_model_memory_status()


def _default_release_status() -> Mapping[str, Any]:
    payload = load_version_one_readiness()
    return payload if isinstance(payload, Mapping) else {}
