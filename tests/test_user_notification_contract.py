import pytest

from src.user_notification_contract import (
    NotificationContractError,
    build_user_notification_decision,
    build_user_notification_request,
    render_user_notification_text,
)


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
