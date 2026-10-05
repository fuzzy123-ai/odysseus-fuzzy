"""Real component construction; global app startup is a separate joined lane."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from core.auth import AuthManager
from core.database import ChatMessage
from routes.project_versioning_routes import _DefaultForgeContextService
from src.context_source_app import install_context_sources
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_durable_context_source import (
    DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    DurablePlanningContextSource,
)
from src.private_context_confirmation import Challenge, ConfirmationError
from src.private_context_confirmation_app import ContextSelection, CookieConfirmationAuthority
from tests.test_context_manifest_producer import real_three_domain_context
from tests.test_planning_durable_context_source import _append_clean_descendants, _append_revision


def _routers(context):
    forge = APIRouter()
    provider = context["forge"]
    forge._forge_context_local_forge = context["local_forge"]
    forge._forge_context_registry_path = provider._registry_path
    forge._forge_context_workspace_root, forge._forge_context_server_projects_root = provider._roots
    forge._forge_context_service = _DefaultForgeContextService(
        local_forge=context["local_forge"], registry_path=provider._registry_path,
        workspace_root=provider._roots[0], server_projects_root=provider._roots[1],
    )
    planning = APIRouter()
    native = context["planning_producer"]
    service = native._approved_source_service
    runtime = native._runtime
    planning._planning_mcp_runtime = AppOwnedPlanningRuntime(
        repo_root=str(service.repo_root), legacy_store=service._definition_store,
        durable_store=runtime.durable_store, issuer=runtime.issuer,
    )
    planning._planning_mcp_service = service
    planning._planning_definition_store = service._definition_store
    planning._planning_definition_owner = service._definition_owner
    return forge, planning


def _app(context):
    app = FastAPI()
    app.state.auth_manager = context["auth"]
    return app


def _selection_json(selection):
    result = []
    for item in selection.items:
        row = {
            "source": item.source,
            "selection_ref": item.selection_ref,
            "revision_ref": item.revision_ref,
        }
        if item.forge_locator is not None:
            locator = item.forge_locator
            row["forge_locator"] = {
                "repo_id": locator.repo_id,
                "version_id": locator.version_id,
                "path": locator.path,
            }
        result.append(row)
    return result


def _native_composition(context):
    forge, planning = _routers(context)
    runtime = planning._planning_mcp_runtime
    owner_sid = context["auth"].subject_id_for_username("fuzzy")
    principal = runtime.issuer.issue(
        subject_id=owner_sid,
        owner_id="fuzzy",
        auth_context_digest="sha256:" + "4" * 64,
        ttl_seconds=3600,
        nonce="context-source-app-native-clean",
    )
    authority = CookieConfirmationAuthority(context["auth"])
    with authority.hold(context["cookie"], context["chat_id"]) as binding:
        _append_clean_descendants(
            {"store": runtime.durable_store, "principal": principal}, binding,
        )
    source = DurablePlanningContextSource(
        runtime=runtime,
        auth_manager=context["auth"],
        project_id="harbour-one",
        policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    )
    return forge, planning, source, principal


def test_real_borrowed_context_services_install_routes_and_preserve_lifecycle(real_three_domain_context):
    context = real_three_domain_context
    app = _app(context)
    forge, planning = _routers(context)
    bundle = install_context_sources(
        app, auth_manager=context["auth"], session_factory=context["session_factory"],
        forge_router=forge, planning_router=planning, knowledge_composition=context["knowledge_composition"],
    )
    assert bundle is not None
    assert repr(bundle) == "ContextSourceServices(<redacted>)"
    assert bundle.confirmation is app.state.private_context_confirmation
    assert bundle.confirmation._producer is bundle.producer
    assert bundle.forge._local_forge is context["local_forge"]
    assert bundle.knowledge._composition is context["knowledge_composition"]
    assert bundle.planning._runtime is planning._planning_mcp_runtime
    family = bundle.confirmation._authority
    assert bundle.forge_pages._authority is not family
    assert bundle.planning_pages._authority is not family
    assert bundle.knowledge_pages._authority is not family
    assert all(page._authority._key == family._key for page in (
        bundle.forge_pages, bundle.planning_pages, bundle.knowledge_pages,
    ))
    assert bundle.planning._auth_manager is context["auth"]
    assert bundle.planning._runtime.durable_store is context["planning_producer"]._runtime.durable_store
    assert bundle.planning._approved_source_service is planning._planning_mcp_service
    assert bundle.planning._durable_source is None
    assert app.state.context_source_status == {"configured": True, "runtime_verified": False, "reason": None}
    locator = context["forge_selection"].forge_locator
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", context["cookie"])
        response = client.post("/api/context/metadata", headers={"Origin": "http://testserver"}, json={
            "chat_session_id": context["chat_id"], "forge_locators": [{"repo_id": locator.repo_id, "version_id": locator.version_id, "path": locator.path}],
        })
        assert response.status_code == 200
        metadata = response.json()
        target = metadata["selection"][0]
        assert target["selection_ref"] == context["forge_selection"].selection_ref
        page = client.post("/api/context/page", headers={"Origin": "http://testserver"}, json={
            "chat_session_id": context["chat_id"], "selection": metadata["selection"], "target_index": 0,
            "expected_manifest_digest": metadata["targets"][0]["manifest_digest"], "offset": 0, "limit": 17,
        })
        assert page.status_code == 200
        assert page.headers["x-context-byteform"] == "git_blob"
        assert 0 < len(page.content) <= 17
    # The borrowed lifecycle remains usable after the small ASGI app exits.
    with CookieConfirmationAuthority(context["auth"]).hold(context["cookie"], context["chat_id"]) as binding:
        assert {item.source for item in bundle.producer.capture_metadata(binding, context["selection"])} == {"forge", "knowledge", "planning"}


def test_require_durable_planning_rejects_ready_legacy_context(real_three_domain_context):
    context = real_three_domain_context
    app = _app(context)
    forge, planning = _routers(context)
    bundle = install_context_sources(
        app, auth_manager=context["auth"], session_factory=context["session_factory"],
        forge_router=forge, planning_router=planning,
        knowledge_composition=context["knowledge_composition"],
        require_durable_planning=True,
    )
    assert bundle is None
    assert app.state.private_context_confirmation._producer is None
    assert app.state.context_source_status["configured"] is False
    assert app.state.context_source_status["runtime_verified"] is False
    assert not any(route.path.startswith("/api/context/") for route in app.routes)


@pytest.mark.parametrize("requirement", (None, 1, "true"))
def test_require_durable_planning_requires_exact_bool(real_three_domain_context, requirement):
    context = real_three_domain_context
    app = _app(context)
    forge, planning = _routers(context)
    bundle = install_context_sources(
        app, auth_manager=context["auth"], session_factory=context["session_factory"],
        forge_router=forge, planning_router=planning,
        knowledge_composition=context["knowledge_composition"],
        require_durable_planning=requirement,
    )
    assert bundle is None
    assert app.state.private_context_confirmation._producer is None
    assert app.state.context_source_status["configured"] is False
    assert app.state.context_source_status["runtime_verified"] is False
    assert not any(route.path.startswith("/api/context/") for route in app.routes)


def test_missing_or_mismatched_prerequisites_install_final_unavailable_confirmation(real_three_domain_context):
    context = real_three_domain_context
    for mismatch in ("knowledge", "planning_store", "planning_owner", "forge_service", "forge_local", "forge_registry", "forge_roots", "planning_root"):
        app = _app(context)
        forge, planning = _routers(context)
        composition = context["knowledge_composition"]
        if mismatch == "knowledge":
            composition = None
        elif mismatch == "planning_store":
            planning._planning_definition_store = object()
        elif mismatch == "planning_owner":
            planning._planning_definition_owner = "foreign-owner"
        elif mismatch == "forge_service":
            forge._forge_context_service = object()
        elif mismatch == "forge_local":
            forge._forge_context_service._local_forge = object()
        elif mismatch == "forge_registry":
            forge._forge_context_registry_path = Path(forge._forge_context_registry_path).with_name("foreign-registry.json")
        elif mismatch == "forge_roots":
            forge._forge_context_workspace_root = forge._forge_context_workspace_root / "foreign-root"
        elif mismatch == "planning_root":
            runtime = planning._planning_mcp_runtime
            planning._planning_mcp_runtime = AppOwnedPlanningRuntime(
                repo_root=str(Path(runtime.repo_root) / "foreign-root"), legacy_store=runtime.legacy_store,
                durable_store=runtime.durable_store, issuer=runtime.issuer,
            )
        bundle = install_context_sources(
            app, auth_manager=context["auth"], session_factory=context["session_factory"],
            forge_router=forge, planning_router=planning, knowledge_composition=composition,
        )
        assert bundle is None
        assert app.state.private_context_confirmation._producer is None
        assert app.state.context_source_status["configured"] is False
        assert app.state.context_source_status["runtime_verified"] is False
        assert not any(route.path.startswith("/api/context/") for route in app.routes)
        with pytest.raises(ConfirmationError) as failure:
            app.state.private_context_confirmation.issue_for_selection(context["cookie"], context["chat_id"], context["selection"])
        assert failure.value.code == "invalid_context"


def test_explicit_native_source_installs_real_mixed_routes_and_preserves_strong_pages(real_three_domain_context):
    context = real_three_domain_context
    app = _app(context)
    forge, planning, source, _principal = _native_composition(context)
    bundle = install_context_sources(
        app, auth_manager=context["auth"], session_factory=context["session_factory"],
        forge_router=forge, planning_router=planning,
        knowledge_composition=context["knowledge_composition"],
        durable_planning_source=source,
        require_durable_planning=True,
    )
    assert bundle is not None
    assert bundle.planning._runtime is planning._planning_mcp_runtime
    assert bundle.planning._auth_manager is context["auth"]
    assert bundle.planning._approved_source_service is None
    assert bundle.planning._durable_source is source
    assert bundle.forge._local_forge is context["local_forge"]
    assert bundle.knowledge._composition is context["knowledge_composition"]
    assert app.state.context_source_status == {
        "configured": True, "runtime_verified": False, "reason": None,
    }
    family = bundle.confirmation._authority
    assert bundle.forge_pages._authority is not family
    assert bundle.planning_pages._authority is not family
    assert bundle.knowledge_pages._authority is not family
    assert all(page._authority._key == family._key for page in (
        bundle.forge_pages, bundle.planning_pages, bundle.knowledge_pages,
    ))

    with bundle.confirmation._authority.hold(context["cookie"], context["chat_id"]) as binding:
        native_items = source.discover(binding)
        assert len(native_items) == 6
        selection = ContextSelection(tuple(sorted(
            (context["forge_selection"], context["knowledge_target"], *native_items[:2]),
            key=lambda item: (item.source, item.selection_ref, item.revision_ref),
        )))
        target = next(item for item in selection.items if item.source == "planning")
        payload = source.read_document(binding, target)

    body = {"chat_session_id": context["chat_id"], "selection": _selection_json(selection)}
    headers = {"Origin": "http://testserver"}
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", context["cookie"])
        response = client.post("/api/context/metadata", headers=headers, json=body)
        assert response.status_code == 200
        metadata = response.json()
        assert metadata["selection"] == body["selection"]
        assert len(metadata["items"]) == len(metadata["targets"]) == 4
        assert [item["source"] for item in metadata["items"]].count("planning") == 2
        assert all(len(item["boundary_sha256"].removeprefix("sha256:")) == 64 for item in metadata["items"])
        assert all(len(item["record_sha256"].removeprefix("sha256:")) == 64 for item in metadata["items"])
        assert all(len(item["payload_sha256"].removeprefix("sha256:")) == 64 for item in metadata["items"])
        assert all(len(item["manifest_digest"].removeprefix("sha256:")) == 64 for item in metadata["targets"])

        challenge_response = client.post("/api/context/challenge", headers=headers, json=body)
        assert challenge_response.status_code == 200
        challenge_body = challenge_response.json()
        expires = datetime.fromisoformat(challenge_body["expires_at"])
        verified = bundle.confirmation.consume_challenge(
            context["cookie"], context["chat_id"],
            Challenge(challenge_body["challenge_token"], expires - timedelta(seconds=300), expires),
        )
        target_index = selection.items.index(target)
        page_body = None
        pages_by_source = {}
        for index, item in enumerate(selection.items):
            candidate = dict(
                body,
                target_index=index,
                expected_manifest_digest=metadata["targets"][index]["manifest_digest"],
                offset=0,
                limit=1_000_000,
                disclosure_message_id=verified.disclosure_message_id,
            )
            page = client.post("/api/context/page", headers=headers, json=candidate)
            assert page.status_code == 200
            assert page.content
            pages_by_source.setdefault(item.source, []).append(page)
            if item is target:
                page_body = candidate
                assert page.content == payload
                expected_sha = hashlib.sha256(payload).hexdigest()
                assert page.headers["x-context-byteform"] == "durable_revision_document"
                assert int(page.headers["x-context-total"]) == len(payload)
                assert int(page.headers["x-context-offset"]) == 0
                assert int(page.headers["x-context-end"]) == len(payload)
                assert page.headers["x-context-page-sha256"] == expected_sha
                assert page.headers["x-context-whole-sha256"] == expected_sha
                assert page.headers["x-context-manifest-sha256"] == metadata["targets"][target_index]["manifest_digest"].removeprefix("sha256:")
        assert set(pages_by_source) == {"planning", "forge", "knowledge"}
        assert page_body is not None

        with context["session_factory"]() as db:
            row = db.query(ChatMessage).filter(
                ChatMessage.id == str(verified.disclosure_message_id),
                ChatMessage.session_id == context["chat_id"],
            ).one()
            row.content = "tampered native app confirmation"
            db.commit()
        denied = client.post("/api/context/page", headers=headers, json=page_body)
        assert denied.status_code == 409
        assert denied.json() == {"error": {"code": "context_unavailable"}}


def test_explicit_native_source_actual_head_drift_denies_old_grant(real_three_domain_context):
    context = real_three_domain_context
    app = _app(context)
    forge, planning, source, principal = _native_composition(context)
    bundle = install_context_sources(
        app, auth_manager=context["auth"], session_factory=context["session_factory"],
        forge_router=forge, planning_router=planning,
        knowledge_composition=context["knowledge_composition"],
        durable_planning_source=source,
        require_durable_planning=True,
    )
    assert bundle is not None
    authority = CookieConfirmationAuthority(context["auth"])
    with authority.hold(context["cookie"], context["chat_id"]) as binding:
        target = source.discover(binding)[0]
    selection = ContextSelection((target,))
    body = {"chat_session_id": context["chat_id"], "selection": _selection_json(selection)}
    headers = {"Origin": "http://testserver"}
    with TestClient(app) as client:
        client.cookies.set("odysseus_session", context["cookie"])
        metadata_response = client.post("/api/context/metadata", headers=headers, json=body)
        assert metadata_response.status_code == 200
        metadata = metadata_response.json()
        challenge_response = client.post("/api/context/challenge", headers=headers, json=body)
        assert challenge_response.status_code == 200
        challenge_body = challenge_response.json()
        expires = datetime.fromisoformat(challenge_body["expires_at"])
        verified = bundle.confirmation.consume_challenge(
            context["cookie"], context["chat_id"],
            Challenge(challenge_body["challenge_token"], expires - timedelta(seconds=300), expires),
        )
        with authority.hold(context["cookie"], context["chat_id"]) as binding:
            _append_revision(
                {"source": source, "binding": binding, "store": source._store, "principal": principal},
                target, title="Actual unapproved app composition revision",
                key="context-source-app-native-draft",
            )
        denied = client.post("/api/context/page", headers=headers, json=dict(
            body,
            target_index=0,
            expected_manifest_digest=metadata["targets"][0]["manifest_digest"],
            offset=0,
            limit=128,
            disclosure_message_id=verified.disclosure_message_id,
        ))
        assert denied.status_code == 409
        assert denied.json() == {"error": {"code": "context_unavailable"}}


@pytest.mark.parametrize("mismatch", ("auth", "runtime", "project", "store", "issuer"))
def test_explicit_malformed_native_source_fails_closed_without_legacy_fallback(
    real_three_domain_context, tmp_path: Path, mismatch: str,
):
    context = real_three_domain_context
    forge, planning, source, _principal = _native_composition(context)
    runtime = planning._planning_mcp_runtime
    if mismatch == "auth":
        source = DurablePlanningContextSource(
            runtime=runtime,
            auth_manager=AuthManager(str(tmp_path / "foreign-auth.json")),
            project_id="harbour-one",
            policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
        )
    elif mismatch == "runtime":
        foreign_runtime = AppOwnedPlanningRuntime(
            repo_root=runtime.repo_root,
            legacy_store=runtime.legacy_store,
            durable_store=runtime.durable_store,
            issuer=runtime.issuer,
        )
        source = DurablePlanningContextSource(
            runtime=foreign_runtime,
            auth_manager=context["auth"],
            project_id="harbour-one",
            policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
        )
    elif mismatch == "project":
        source = DurablePlanningContextSource(
            runtime=runtime,
            auth_manager=context["auth"],
            project_id="foreign-project",
            policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
        )
    elif mismatch == "store":
        source._store = object()
    else:
        source._issuer = object()

    app = _app(context)
    bundle = install_context_sources(
        app, auth_manager=context["auth"], session_factory=context["session_factory"],
        forge_router=forge, planning_router=planning,
        knowledge_composition=context["knowledge_composition"],
        durable_planning_source=source,
        require_durable_planning=True,
    )
    assert bundle is None
    assert planning._planning_mcp_service is not None
    assert app.state.private_context_confirmation._producer is None
    assert app.state.context_sources is None
    assert app.state.context_source_status["configured"] is False
    assert app.state.context_source_status["runtime_verified"] is False
    assert not any(route.path.startswith("/api/context/") for route in app.routes)
