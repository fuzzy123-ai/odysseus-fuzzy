from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from routes.universal_inbox_routes import setup_universal_inbox_routes
from src.universal_inbox_file_types import classify_universal_inbox_file
from src.universal_inbox_workbench import (
    WorkbenchAction,
    WorkbenchActionDecision,
    WorkbenchActionState,
    WorkbenchContractError,
    build_universal_inbox_workbench_capability,
)


def _capability(
    filename: str,
    *,
    has_working_copy: bool = True,
    browser_download_allowed: bool = True,
    provider_write_requested: bool = False,
    browser_family_hint: str | None = None,
):
    return build_universal_inbox_workbench_capability(
        classify_universal_inbox_file(filename),
        owner_authorized=True,
        has_working_copy=has_working_copy,
        browser_download_allowed=browser_download_allowed,
        provider_write_requested=provider_write_requested,
        browser_family_hint=browser_family_hint,
    )


def _state(capability, action: WorkbenchAction) -> WorkbenchActionState:
    return capability.action(action).state


@pytest.mark.parametrize("filename", ("note.md", "note.txt", "scan.pdf", "letter.docx"))
def test_p0_documents_allow_the_complete_safe_local_workbench_path(filename: str):
    capability = _capability(filename)

    assert capability.mvp_tier == "p0"
    assert {
        decision.action: decision.state for decision in capability.actions
    } == {action: WorkbenchActionState.ALLOWED for action in WorkbenchAction}
    assert capability.original_immutable is True
    assert capability.working_copy_versioned is True
    assert all(decision.mutates_original is False for decision in capability.actions)
    assert all(decision.performs_live_write is False for decision in capability.actions)


def test_working_copy_actions_require_an_explicit_copy_but_creation_stays_available():
    capability = _capability("note.md", has_working_copy=False)

    assert _state(capability, WorkbenchAction.CREATE_WORKING_COPY) == WorkbenchActionState.ALLOWED
    assert _state(capability, WorkbenchAction.EDIT_WORKING_COPY) == WorkbenchActionState.BLOCKED
    assert _state(capability, WorkbenchAction.EXPORT_WORKING_COPY) == WorkbenchActionState.BLOCKED
    assert capability.action(WorkbenchAction.EDIT_WORKING_COPY).reason_codes == (
        "working_copy_required",
    )


@pytest.mark.parametrize("filename", ("page.html", "diagram.svg", "feed.xml", "table.csv"))
def test_p1_text_like_formats_are_review_only_for_working_copy_operations(
    filename: str,
):
    capability = _capability(filename)

    assert capability.mvp_tier == "p1"
    assert _state(capability, WorkbenchAction.INSPECT) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.ROUTE_DRY_RUN) == WorkbenchActionState.ALLOWED
    assert _state(capability, WorkbenchAction.CREATE_WORKING_COPY) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.EDIT_WORKING_COPY) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.DOWNLOAD_ORIGINAL) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.EXPORT_WORKING_COPY) == WorkbenchActionState.REVIEW


@pytest.mark.parametrize("filename", ("sheet.xls", "sheet.xlsx"))
def test_p1_spreadsheets_remain_original_only(filename: str):
    capability = _capability(filename)

    assert capability.mvp_tier == "p1"
    assert _state(capability, WorkbenchAction.ROUTE_DRY_RUN) == WorkbenchActionState.ALLOWED
    assert _state(capability, WorkbenchAction.DOWNLOAD_ORIGINAL) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.CREATE_WORKING_COPY) == WorkbenchActionState.NOT_SUPPORTED
    assert _state(capability, WorkbenchAction.EDIT_WORKING_COPY) == WorkbenchActionState.NOT_SUPPORTED
    assert _state(capability, WorkbenchAction.EXPORT_WORKING_COPY) == WorkbenchActionState.NOT_SUPPORTED


@pytest.mark.parametrize("filename", ("deck.pptx", "document.odt", "legacy.rtf", "book.epub"))
def test_p2_formats_support_review_routing_and_original_download_only(filename: str):
    capability = _capability(filename)

    assert capability.mvp_tier == "p2"
    assert _state(capability, WorkbenchAction.INSPECT) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.ROUTE_DRY_RUN) == WorkbenchActionState.ALLOWED
    assert _state(capability, WorkbenchAction.DOWNLOAD_ORIGINAL) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.CREATE_WORKING_COPY) == WorkbenchActionState.NOT_SUPPORTED
    assert _state(capability, WorkbenchAction.EDIT_WORKING_COPY) == WorkbenchActionState.NOT_SUPPORTED
    assert _state(capability, WorkbenchAction.EXPORT_WORKING_COPY) == WorkbenchActionState.NOT_SUPPORTED


def test_supporting_images_never_claim_document_editing():
    capability = _capability("photo.png")

    assert capability.mvp_tier == "supporting"
    assert _state(capability, WorkbenchAction.INSPECT) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.ROUTE_DRY_RUN) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.DOWNLOAD_ORIGINAL) == WorkbenchActionState.REVIEW
    assert _state(capability, WorkbenchAction.CREATE_WORKING_COPY) == WorkbenchActionState.NOT_SUPPORTED


def test_dangerous_source_is_metadata_review_only_and_other_actions_fail_closed():
    capability = _capability("setup.exe")

    assert capability.mvp_tier == "out_of_focus"
    assert _state(capability, WorkbenchAction.INSPECT) == WorkbenchActionState.REVIEW
    assert all(
        decision.state == WorkbenchActionState.BLOCKED
        for decision in capability.actions
        if decision.action != WorkbenchAction.INSPECT
    )
    assert _state(capability, WorkbenchAction.DOWNLOAD_ORIGINAL) == WorkbenchActionState.BLOCKED


def test_unknown_format_is_not_supported_for_every_action():
    capability = _capability("payload.unknown")

    assert capability.mvp_tier == "unsupported"
    assert all(
        decision.state == WorkbenchActionState.NOT_SUPPORTED
        for decision in capability.actions
    )


def test_missing_owner_authority_blocks_every_action_without_side_effect_authority():
    capability = build_universal_inbox_workbench_capability(
        classify_universal_inbox_file("note.md"),
        owner_authorized=False,
        has_working_copy=True,
        browser_download_allowed=True,
    )

    assert all(
        decision.state == WorkbenchActionState.BLOCKED
        for decision in capability.actions
    )
    assert {
        decision.reason_codes for decision in capability.actions
    } == {("owner_authorization_required",)}
    assert capability.live_write_authorized is False


def test_provider_target_requires_live_gate_without_granting_or_performing_a_write():
    capability = _capability("note.md", provider_write_requested=True)

    assert _state(capability, WorkbenchAction.ROUTE_DRY_RUN) == WorkbenchActionState.ALLOWED
    assert _state(capability, WorkbenchAction.DOWNLOAD_ORIGINAL) == WorkbenchActionState.LIVE_GATE_REQUIRED
    assert _state(capability, WorkbenchAction.EXPORT_WORKING_COPY) == WorkbenchActionState.LIVE_GATE_REQUIRED
    assert all(decision.performs_live_write is False for decision in capability.actions)
    assert capability.live_write_authorized is False


def test_browser_download_policy_is_authoritative():
    capability = _capability("note.md", browser_download_allowed=False)

    assert _state(capability, WorkbenchAction.DOWNLOAD_ORIGINAL) == WorkbenchActionState.BLOCKED
    assert _state(capability, WorkbenchAction.EXPORT_WORKING_COPY) == WorkbenchActionState.BLOCKED
    assert capability.action(WorkbenchAction.DOWNLOAD_ORIGINAL).reason_codes == (
        "browser_download_policy_required",
    )


def test_browser_detection_is_advisory_and_cannot_change_server_actions():
    baseline = _capability("note.md")
    mismatched = _capability("note.md", browser_family_hint="archive")
    invalid = _capability("note.md", browser_family_hint="../../private")

    assert mismatched.server_family == "text"
    assert mismatched.browser_hint_relation == "mismatch"
    assert invalid.browser_hint == "ignored"
    assert invalid.browser_hint_relation == "ignored_invalid"
    assert mismatched.actions == baseline.actions
    assert invalid.actions == baseline.actions
    assert mismatched.server_authoritative is True
    assert mismatched.browser_detection_advisory is True


def test_projection_is_bounded_content_free_and_json_serializable():
    capability = _capability("private-report.docx", browser_family_hint="document")
    payload = capability.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["browser_hint_relation"] == "match"
    assert payload["source_suffix"] == ".docx"
    assert payload["raw_content_visible"] is False
    assert payload["absolute_path_visible"] is False
    assert payload["live_write_authorized"] is False
    assert "private-report" not in encoded
    assert len(payload["actions"]) == 6

    unknown = _capability("private.secret-extension").to_dict()
    unknown_encoded = json.dumps(unknown, sort_keys=True)
    assert unknown["source_suffix"] == "other"
    assert "secret-extension" not in unknown_encoded


def test_contract_rejects_truthy_non_booleans_and_unknown_actions():
    decision = classify_universal_inbox_file("note.md")

    with pytest.raises(WorkbenchContractError, match="owner_authorized"):
        build_universal_inbox_workbench_capability(
            decision,
            owner_authorized=1,  # type: ignore[arg-type]
        )

    capability = build_universal_inbox_workbench_capability(
        decision,
        owner_authorized=True,
    )
    with pytest.raises(WorkbenchContractError, match="unknown"):
        capability.action("delete_original")

    with pytest.raises(TypeError):
        WorkbenchActionDecision(
            action=WorkbenchAction.INSPECT,
            state=WorkbenchActionState.ALLOWED,
            reason_codes=("forged",),
            mutates_original=True,  # type: ignore[call-arg]
        )


class _RouteDryRunUploadHandler:
    def __init__(self, owner: str = "alice", filename: str = "private-plan.pdf"):
        self.owner = owner
        self.filename = filename
        self.calls: list[tuple[str, str | None]] = []

    def resolve_upload(self, upload_id, *, owner=None, **_kwargs):
        self.calls.append((upload_id, owner))
        if owner != self.owner:
            return None
        return {
            "id": upload_id,
            "original_name": self.filename,
            "mime": "application/pdf",
        }


class _RouteDryRunAuthManager:
    def __init__(self, is_configured: bool):
        self.is_configured = is_configured


def _route_dry_run_client(
    owner: str | None,
    handler: _RouteDryRunUploadHandler,
    *,
    auth_configured: bool = False,
) -> TestClient:
    app = FastAPI()
    app.state.auth_manager = _RouteDryRunAuthManager(auth_configured)

    @app.middleware("http")
    async def owner_context(request: Request, call_next):
        if owner is not None:
            request.state.current_user = owner
        return await call_next(request)

    app.include_router(setup_universal_inbox_routes(handler))
    return TestClient(app)


def _route_ref() -> str:
    return f"upload:{'a' * 32}.pdf"


def test_route_dry_run_projects_owner_checked_policy_without_paths_or_writes():
    handler = _RouteDryRunUploadHandler()
    client = _route_dry_run_client("alice", handler)

    response = client.post(
        f"/api/universal-inbox/items/{_route_ref()}/route-dry-run",
        json={
            "domain": "private",
            "document_type": "invoice",
            "confidence": 0.91,
            "risk_signals": {"sensitive": True},
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    payload = response.json()
    assert handler.calls == [("a" * 32 + ".pdf", "alice")]
    assert payload["status"] == payload["policy_status"] == "review"
    assert payload["input_authority"] == "advisory"
    assert payload["confidence"] == 0.91
    assert payload["review_reasons"] == ["sensitive"]
    assert payload["reason_codes"] == ["sensitive"]
    assert payload["route_capability"]["server_authoritative"] is True
    assert payload["dry_run"] is True
    assert payload["path_redacted"] is True
    assert payload["content_redacted"] is True
    assert payload["raptorgraph_payload_visible"] is False
    assert payload["live_apply"] == {
        "enabled": False,
        "gate": "UIX-NEXTCLOUD-LIVE-WRITE",
    }
    assert all(payload[name] is False for name in (
        "copy_performed", "move_performed", "delete_performed", "overwrite_performed",
        "memory_writes_performed", "live_writes_performed", "writes_performed",
    ))
    encoded = json.dumps(payload)
    for forbidden in ("private-plan", "incoming/source", "Documents/", "raptorgraph_event"):
        assert forbidden not in encoded


def test_route_dry_run_projects_a_server_authoritative_go_when_policy_allows_it():
    client = _route_dry_run_client("alice", _RouteDryRunUploadHandler())
    response = client.post(
        f"/api/universal-inbox/items/{_route_ref()}/route-dry-run",
        json={"domain": "private", "document_type": "invoice", "confidence": 0.99},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == payload["policy_status"] == "go"
    assert payload["suggestion"] == "matched_policy_route"
    assert payload["reason_codes"] == payload["review_reasons"] == payload["no_go_reasons"] == []


def test_route_dry_run_projects_a_capability_no_go_without_exposing_the_source():
    client = _route_dry_run_client(
        "alice",
        _RouteDryRunUploadHandler(filename="private-installer.exe"),
    )
    response = client.post(
        f"/api/universal-inbox/items/{_route_ref()}/route-dry-run",
        json={"domain": "private", "document_type": "invoice", "confidence": 0.99},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == payload["policy_status"] == "no_go"
    assert payload["suggestion"] == "blocked_by_policy"
    assert payload["no_go_reasons"] == ["route_capability_blocked"]
    assert payload["reason_codes"] == ["route_capability_blocked"]
    assert "private-installer" not in json.dumps(payload)


@pytest.mark.parametrize(
    ("owner", "source_ref", "body", "status", "code"),
    [
        (None, _route_ref(), {"domain": "private", "document_type": "invoice", "confidence": 0.9}, 403, "route_dry_run_owner_required"),
        ("bob", _route_ref(), {"domain": "private", "document_type": "invoice", "confidence": 0.9}, 404, "route_dry_run_source_not_found"),
        ("alice", "upload:not-a-source", {"domain": "private", "document_type": "invoice", "confidence": 0.9}, 400, "malformed_route_dry_run_source_ref"),
        ("alice", _route_ref(), {"domain": "private", "document_type": "invoice", "confidence": "0.9"}, 400, "invalid_route_dry_run_confidence"),
        ("alice", _route_ref(), {"domain": "private", "document_type": "invoice", "confidence": 0.9, "owner": "alice"}, 400, "invalid_route_dry_run_body"),
    ],
)
def test_route_dry_run_fails_closed_for_owner_source_and_invalid_input(
    owner,
    source_ref,
    body,
    status,
    code,
):
    client = _route_dry_run_client(
        owner,
        _RouteDryRunUploadHandler(),
        auth_configured=owner is None,
    )
    response = client.post(
        f"/api/universal-inbox/items/{source_ref}/route-dry-run",
        json=body,
    )

    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json() == {
        "schema": "odysseus.universal_inbox.route_dry_run_error.v1",
        "error": code,
        "content_redacted": True,
        "path_redacted": True,
        "copy_performed": False,
        "move_performed": False,
        "delete_performed": False,
        "overwrite_performed": False,
        "memory_writes_performed": False,
        "live_writes_performed": False,
        "writes_performed": False,
    }


def test_route_dry_run_requires_a_small_json_body():
    client = _route_dry_run_client("alice", _RouteDryRunUploadHandler())

    wrong_type = client.post(
        f"/api/universal-inbox/items/{_route_ref()}/route-dry-run",
        content="{}",
        headers={"content-type": "text/plain"},
    )
    oversized = client.post(
        f"/api/universal-inbox/items/{_route_ref()}/route-dry-run",
        content="{" + "x" * 1100 + "}",
        headers={"content-type": "application/json"},
    )

    assert wrong_type.status_code == 415
    assert wrong_type.json()["error"] == "route_dry_run_json_required"
    assert oversized.status_code == 413
    assert oversized.json()["error"] == "route_dry_run_body_too_large"


def test_route_dry_run_keeps_unconfigured_single_user_ownerless_but_scoped():
    handler = _RouteDryRunUploadHandler(owner=None)
    client = _route_dry_run_client(None, handler, auth_configured=False)

    response = client.post(
        f"/api/universal-inbox/items/{_route_ref()}/route-dry-run",
        json={"domain": "private", "document_type": "invoice", "confidence": 0.9},
    )

    assert response.status_code == 200
    assert handler.calls == [("a" * 32 + ".pdf", None)]
