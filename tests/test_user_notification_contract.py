import pytest

from src.user_notification_contract import (
    NotificationContractError,
    PLANNING_NOTIFICATION_EVENTS,
    PLANNING_SILENT_EVENTS,
    build_planning_notification_candidate,
    build_user_notification_decision,
    build_user_notification_request,
    classify_planning_notification_event,
    render_user_notification_text,
)


NOW = "2026-07-10T08:00:00Z"


def planning_payload(event_type="roadmap_created", **overrides):
    value = {
        "event_type": event_type,
        "project_id": "project-001",
        "roadmap_id": "roadmap-001" if event_type.startswith("roadmap_") else None,
        "gate_id": "gate-001" if event_type.startswith("gate_") or event_type == "human_decision_required" else None,
        "severity": None,
        "reason": "A bounded structural Planning change is ready for review.",
        "created_at": NOW,
        "ui_target": {},
    }
    value.update(overrides)
    return value


def test_notification_request_defaults_to_dry_run_and_safe_auto_channel():
    request = build_user_notification_request({
        "event": "Roadmap Completed!",
        "message": "ABC roadmap finished.",
        "severity": "success",
        "requested_channel_class": "completion_notice",
        "metadata": {"commit": "abc123", "tests": "passed"},
    })

    assert request.event == "roadmap_completed"
    assert request.severity == "success"
    assert request.channel == "auto"
    assert request.dry_run is True
    assert request.metadata == {"commit": "abc123", "tests": "passed"}


def test_notification_contract_rejects_secret_or_target_keys_recursively():
    with pytest.raises(NotificationContractError):
        build_user_notification_request({
            "message": "Do not send this.",
            "metadata": {"token": "redacted"},
        })

    with pytest.raises(NotificationContractError):
        build_user_notification_request({
            "message": "Do not route this.",
            "chat_id": "synthetic-test-target",
        })


def test_notification_decision_blocks_live_without_server_gates():
    decision = build_user_notification_decision({
        "event": "backup_done",
        "message": "Backup completed.",
        "dry_run": False,
    })

    assert decision.status == "blocked"
    assert decision.dispatch_allowed is False
    assert decision.reason == "live_dispatch_disabled"
    public = decision.as_public_dict()
    assert public["token_value_visible"] is False
    assert public["chat_target_value_visible"] is False


def test_notification_decision_accepts_only_when_server_target_and_gate_exist():
    decision = build_user_notification_decision(
        {
            "event": "abc_done",
            "message": "Roadmap completed.",
            "severity": "success",
            "dry_run": False,
        },
        live_dispatch_enabled=True,
        target_configured=True,
    )

    assert decision.status == "accepted"
    assert decision.dispatch_allowed is True
    assert decision.reason == "ready_for_server_side_dispatch"
    assert decision.resolved_channel == "telegram"


def test_render_notification_text_contains_only_public_fields():
    request = build_user_notification_request({
        "event": "release_check",
        "message": "Release bundle ready.",
        "metadata": {"branch": "dev"},
    })

    text = render_user_notification_text(request)

    assert "[Odysseus][info] release_check" in text
    assert "Release bundle ready." in text
    assert "branch=dev" in text


def test_plain_notification_preserves_digest_lines_without_prefix_or_metadata():
    request = build_user_notification_request({
        "event": "scheduled_task",
        "message": "Todo digest\n\nOpen items:\n- One\n- Two",
        "severity": "success",
        "render_mode": "plain",
        "metadata": {"task_id": "sha256:abc"},
    })

    text = render_user_notification_text(request)

    assert text == "Todo digest\n\nOpen items:\n- One\n- Two"
    assert "[Odysseus]" not in text
    assert "task_id" not in text


def test_plain_notification_normalizes_legacy_flat_todo_digest():
    request = build_user_notification_request({
        "event": "todo_digest",
        "message": (
            "[Odysseus][success] scheduled_task: Todo digest Open items: "
            "- Zentrale To-Do-Liste: Termin mit Herr Assel und Macro koordinieren per E-Mail "
            "- Zentrale To-Do-Liste: ASV Noten ueberpruefen "
            "task_id=sha256:eb50733f9dcd20d40431eeb07ba5ea317adf9d7f205425b6419e2fe6b12ae7f0"
        ),
        "severity": "success",
        "render_mode": "plain",
        "metadata": {"task_id": "sha256:abc"},
    })

    text = render_user_notification_text(request)

    assert text == (
        "Todo digest\n\n"
        "Open items:\n"
        "Zentrale To-Do-Liste:\n"
        "- Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
        "- ASV Noten ueberpruefen"
    )


def test_standard_scheduled_task_notification_normalizes_legacy_todo_digest():
    request = build_user_notification_request({
        "event": "scheduled_task",
        "message": (
            "[Odysseus][success] scheduled_task: Todo digest Open items: "
            "- Zentrale To-Do-Liste: Termin mit Herr Assel und Macro koordinieren per E-Mail "
            "- Zentrale To-Do-Liste: ASV Noten ueberpruefen"
        ),
        "severity": "success",
        "metadata": {
            "task_id": "sha256:eb50733f9dcd20d40431eeb07ba5ea317adf9d7f205425b6419e2fe6b12ae7f0"
        },
    })

    text = render_user_notification_text(request)

    assert text == (
        "Todo digest\n\n"
        "Open items:\n"
        "Zentrale To-Do-Liste:\n"
        "- Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
        "- ASV Noten ueberpruefen"
    )
    assert "[Odysseus]" not in text
    assert "scheduled_task" not in text
    assert "task_id" not in text


def test_planning_roadmap_candidate_has_logical_overview_target_and_no_delivery_authority():
    candidate = build_planning_notification_candidate(planning_payload())

    assert candidate.event_type == "roadmap_created"
    assert candidate.project_id == "project-001"
    assert candidate.roadmap_id == "roadmap-001"
    assert candidate.gate_id is None
    assert candidate.severity == "success"
    assert candidate.classification == "notification_candidate"
    assert candidate.delivery_authorized is False
    assert candidate.live_delivery_performed is False
    assert candidate.dedupe_key.startswith("sha256:")
    assert candidate.ui_target.to_dict() == {
        "workspace": "planning",
        "view": "overview",
        "highlight_kind": "roadmap",
        "highlight_id": "roadmap-001",
        "highlight_mode": "expand_summary",
        "document_view_intent": "none",
    }
    public = candidate.to_dict()
    assert public["delivery_authorized"] is False
    assert public["live_delivery_performed"] is False
    assert "url" not in public["ui_target"]
    assert "path" not in public["ui_target"]
    assert "query" not in public["ui_target"]


def test_planning_gate_candidate_defaults_to_typed_gate_highlight():
    candidate = build_planning_notification_candidate(planning_payload(
        "gate_blocked",
        roadmap_id="roadmap-001",
    ))
    assert candidate.severity == "warning"
    assert candidate.gate_id == "gate-001"
    assert candidate.ui_target.highlight_kind == "gate"
    assert candidate.ui_target.highlight_id == "gate-001"


def test_planning_candidate_supports_bounded_document_view_intent():
    candidate = build_planning_notification_candidate(planning_payload(
        ui_target={
            "workspace": "planning",
            "view": "overview",
            "highlight_kind": "roadmap",
            "highlight_mode": "expand_summary",
            "document_view_intent": "open_roadmap_document",
        },
    ))
    assert candidate.ui_target.document_view_intent == "open_roadmap_document"
    assert candidate.ui_target.highlight_id == "roadmap-001"


@pytest.mark.parametrize("event_type", sorted(PLANNING_SILENT_EVENTS))
def test_routine_planning_events_are_quiet(event_type):
    payload = planning_payload(event_type, roadmap_id=None, gate_id=None, reason=None, created_at=None)
    assert classify_planning_notification_event(event_type) == "silent"
    assert build_planning_notification_candidate(payload) is None


@pytest.mark.parametrize("event_type", sorted(PLANNING_NOTIFICATION_EVENTS))
def test_structural_planning_events_classify_as_sparse_candidates(event_type):
    assert classify_planning_notification_event(event_type) == "notification_candidate"


def test_planning_event_constraints_require_typed_refs():
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload("roadmap_deleted", roadmap_id=None))
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload("gate_blocked", gate_id=None))
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload("human_decision_required", gate_id=None))

    project = build_planning_notification_candidate(planning_payload(
        "project_created", roadmap_id=None, gate_id=None,
    ))
    assert project.ui_target.highlight_kind == "project"
    assert project.ui_target.highlight_id == "project-001"
    undo = build_planning_notification_candidate(planning_payload(
        "undo_available_after_structural_delete", roadmap_id=None, gate_id=None,
    ))
    assert undo.severity == "info"


@pytest.mark.parametrize("event_type", ["roadmap_read", "progress", "ROADMAP_CREATED", None])
def test_unknown_or_noncanonical_planning_events_fail_closed(event_type):
    with pytest.raises(NotificationContractError):
        classify_planning_notification_event(event_type)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "Project-001"),
        ("project_id", "../project"),
        ("roadmap_id", "roadmap/001"),
        ("gate_id", "gate.001"),
    ],
)
def test_planning_candidate_rejects_noncanonical_ids(field, value):
    payload = planning_payload()
    payload[field] = value
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(payload)


@pytest.mark.parametrize(
    "ui_target",
    [
        {"url": "https://example.invalid/planning"},
        {"path": "planning/overview"},
        {"query": "project_id=project-001"},
        {"workspace": "agent"},
        {"view": "document"},
        {"highlight_kind": "todo"},
        {"highlight_kind": "gate"},
        {"highlight_mode": "flash"},
        {"document_view_intent": "open_url"},
    ],
)
def test_planning_ui_target_rejects_arbitrary_navigation_or_missing_ref(ui_target):
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload(ui_target=ui_target))


def test_document_intent_requires_a_roadmap_ref():
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload(
            "project_created",
            roadmap_id=None,
            ui_target={"document_view_intent": "open_roadmap_document"},
        ))


@pytest.mark.parametrize(
    "payload_update",
    [
        {"token": "synthetic-value"},
        {"delivery_authorized": True},
        {"reason": "See C:\\Users\\private\\plan.txt"},
        {"reason": "Open https://example.invalid/private"},
        {"reason": "api_key=synthetic-value"},
        {"ui_target": {"target": "synthetic-target"}},
    ],
)
def test_planning_candidate_rejects_delivery_secrets_paths_urls_and_authority(payload_update):
    payload = planning_payload()
    payload.update(payload_update)
    with pytest.raises(NotificationContractError) as caught:
        build_planning_notification_candidate(payload)
    assert "synthetic-value" not in str(caught.value)


def test_planning_candidate_rejects_invalid_reason_time_and_severity():
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload(reason="x" * 241))
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload(created_at="2026-07-10T09:00:00+01:00"))
    with pytest.raises(NotificationContractError):
        build_planning_notification_candidate(planning_payload(severity="critical"))


def test_planning_dedupe_key_is_deterministic_and_excludes_timestamp():
    first = build_planning_notification_candidate(planning_payload())
    second = build_planning_notification_candidate(planning_payload(created_at="2026-07-10T09:00:00Z"))
    changed = build_planning_notification_candidate(planning_payload(reason="A different bounded structural reason."))
    assert first.dedupe_key == second.dedupe_key
    assert first.dedupe_key != changed.dedupe_key


def test_pmcp_read_proposal_section_and_memory_events_are_all_explicitly_silent():
    required = {
        "planning_read",
        "planning_search",
        "planning_validate",
        "planning_draft_created",
        "planning_patch_proposed",
        "planning_context_pack_read",
        "planning_section_context_pack_read",
        "planning_progress_updated",
        "planning_todo_completed",
        "planning_derived_memory_lifecycle_planned",
        "planning_agent_checkpoint_written",
    }

    assert required.issubset(PLANNING_SILENT_EVENTS)
    for event_type in sorted(required):
        assert classify_planning_notification_event(event_type) == "silent"
        assert build_planning_notification_candidate(
            planning_payload(event_type, roadmap_id=None, gate_id=None, reason=None, created_at=None)
        ) is None
