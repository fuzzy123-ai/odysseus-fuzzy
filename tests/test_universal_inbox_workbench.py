from __future__ import annotations

import json

import pytest

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
