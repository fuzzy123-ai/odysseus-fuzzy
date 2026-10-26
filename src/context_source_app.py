"""Bind concrete context services while borrowing the app's existing stores."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, FastAPI

from core.auth import AuthManager
from routes.context_source_routes import create_context_source_router
from routes.project_versioning_routes import _DefaultForgeContextService
from src.context_manifest_producer import CombinedContextManifestProducer
from src.forge_context_metadata import ForgeContextMetadata
from src.forge_context_page import ForgeContextPageConsumer
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_active_runtime import (
    ActiveKnowledgeComposition, KnowledgeContextManifestProducer, KnowledgeContextPageConsumer,
)
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_durable_context_source import DurablePlanningContextSource
from src.planning_context_reader import PlanningContextManifestProducer, PlanningContextPageConsumer
from src.planning_mcp_service import PlanningMcpService
from src.planning_revision_store import DurablePlanningRevisionStore
from src.private_context_confirmation_app import PrivateContextConfirmationApp
from src.project_forge_local import LocalProjectForge


@dataclass(frozen=True, slots=True, repr=False)
class ContextSourceServices:
    """Constructed bindings, with no additional lifecycle ownership."""

    producer: CombinedContextManifestProducer = field(repr=False)
    confirmation: PrivateContextConfirmationApp = field(repr=False)
    forge: ForgeContextMetadata = field(repr=False)
    planning: PlanningContextManifestProducer = field(repr=False)
    knowledge: KnowledgeContextManifestProducer = field(repr=False)
    forge_pages: ForgeContextPageConsumer = field(repr=False)
    planning_pages: PlanningContextPageConsumer = field(repr=False)
    knowledge_pages: KnowledgeContextPageConsumer = field(repr=False)

    def __repr__(self) -> str:
        return "ContextSourceServices(<redacted>)"


def install_context_sources(
    app: FastAPI, *, auth_manager: AuthManager, session_factory: Callable,
    forge_router: APIRouter, planning_router: APIRouter,
    knowledge_composition: ActiveKnowledgeComposition | None,
    durable_planning_source: DurablePlanningContextSource | None = None,
    require_durable_planning: bool = False,
) -> ContextSourceServices | None:
    """Construct once; unavailable dependencies preserve the fail-closed chat."""
    if type(app) is not FastAPI or type(auth_manager) is not AuthManager or not callable(session_factory):
        raise ValueError("context composition unavailable") from None
    if getattr(app.state, "auth_manager", None) is not auth_manager:
        raise ValueError("context composition unavailable") from None
    if any(getattr(route, "path", "").startswith("/api/context/") for route in app.routes):
        raise ValueError("context composition already installed") from None
    bundle = None
    context_router = None
    reason = "missing_prerequisites"
    try:
        if type(require_durable_planning) is not bool:
            raise ValueError("context composition unavailable")
        if type(forge_router) is not APIRouter or type(planning_router) is not APIRouter:
            raise ValueError("context composition unavailable")
        local_forge = getattr(forge_router, "_forge_context_local_forge", None)
        forge_service = getattr(forge_router, "_forge_context_service", None)
        runtime = getattr(planning_router, "_planning_mcp_runtime", None)
        service = getattr(planning_router, "_planning_mcp_service", None)
        store = getattr(planning_router, "_planning_definition_store", None)
        owner = getattr(planning_router, "_planning_definition_owner", None)
        legacy_planning_ready = (
            durable_planning_source is None
            and type(service) is PlanningMcpService
            and Path(runtime.repo_root).resolve(strict=False) == Path(service.repo_root).resolve(strict=False)
            and store is not None and runtime.legacy_store is store
            and service._definition_store is store
            and type(owner) is str and bool(owner) and service._definition_owner == owner
        ) if type(runtime) is AppOwnedPlanningRuntime else False
        native_planning_ready = (
            type(durable_planning_source) is DurablePlanningContextSource
            and durable_planning_source._runtime is runtime
            and durable_planning_source._auth_manager is auth_manager
            and durable_planning_source._project_id == "harbour-one"
            and durable_planning_source._store is runtime.durable_store
            and durable_planning_source._issuer is runtime.issuer
        ) if type(runtime) is AppOwnedPlanningRuntime else False
        planning_ready = (
            native_planning_ready
            if require_durable_planning
            else legacy_planning_ready or native_planning_ready
        )
        ready = (
            type(local_forge) is LocalProjectForge
            and type(forge_service) is _DefaultForgeContextService
            and forge_service._local_forge is local_forge
            and Path(forge_service._registry_path).resolve(strict=False)
                == Path(forge_router._forge_context_registry_path).resolve(strict=False)
            and forge_service._roots == (
                Path(forge_router._forge_context_workspace_root).resolve(strict=False),
                Path(forge_router._forge_context_server_projects_root).resolve(strict=False),
            )
            and type(runtime) is AppOwnedPlanningRuntime
            and type(runtime.durable_store) is DurablePlanningRevisionStore
            and type(runtime.issuer) is AuthenticatedPrincipalIssuer
            and planning_ready
            and type(knowledge_composition) is ActiveKnowledgeComposition
            and knowledge_composition._auth is auth_manager
        )
        if ready:
            reason = "construction_unavailable"
            forge = ForgeContextMetadata(
                local_forge=local_forge, registry_path=forge_router._forge_context_registry_path,
                auth_manager=auth_manager, workspace_root=forge_router._forge_context_workspace_root,
                server_projects_root=forge_router._forge_context_server_projects_root,
            )
            if durable_planning_source is None:
                planning = PlanningContextManifestProducer(runtime, auth_manager, service, "harbour-one")
            else:
                planning = PlanningContextManifestProducer(
                    runtime, auth_manager, None, "harbour-one",
                    durable_source=durable_planning_source,
                )
            knowledge = KnowledgeContextManifestProducer(knowledge_composition)
            producer = CombinedContextManifestProducer(auth_manager=auth_manager, forge=forge, planning=planning, knowledge=knowledge)
            confirmation = PrivateContextConfirmationApp(session_factory, auth_manager, producer)
            forge_pages = ForgeContextPageConsumer(
                metadata_provider=forge, local_forge=local_forge, auth_manager=auth_manager,
                session_factory=session_factory, confirmation_app=confirmation, manifest_producer=producer,
            )
            planning_pages = PlanningContextPageConsumer(planning, auth_manager, confirmation, session_factory)
            knowledge_pages = KnowledgeContextPageConsumer(knowledge_composition, auth_manager, confirmation, session_factory)
            context_router = create_context_source_router(
                auth_manager=auth_manager, session_factory=session_factory, manifest_producer=producer,
                confirmation_app=confirmation, forge=forge, planning=planning, knowledge=knowledge,
                forge_pages=forge_pages, planning_pages=planning_pages, knowledge_pages=knowledge_pages,
            )
            bundle = ContextSourceServices(producer, confirmation, forge, planning, knowledge, forge_pages, planning_pages, knowledge_pages)
    except Exception:
        bundle = None
        context_router = None
    if bundle is None:
        confirmation = PrivateContextConfirmationApp(session_factory, auth_manager, manifest_producer=None)
    else:
        confirmation = bundle.confirmation
        app.include_router(context_router)
    app.state.private_context_confirmation = confirmation
    app.state.private_context_confirmation_auth_manager = auth_manager
    app.state.context_sources = bundle
    # Construction readiness never claims source capture or release validation.
    app.state.context_source_status = {"configured": bundle is not None, "runtime_verified": False,
                                      "reason": None if bundle is not None else reason}
    return bundle
