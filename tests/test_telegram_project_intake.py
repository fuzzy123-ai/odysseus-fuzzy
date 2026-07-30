import pytest

from plugins.telegram.project_intake import (
    _looks_like_project_intake,
    build_telegram_project_intake_preview,
)
from plugins.telegram.stores import TelegramInboxStore, TelegramSessionBridgeStore
from plugins.telegram.webhook_service import run_webhook_project_intake_branch


@pytest.mark.parametrize(
    "text",
    (
        "Todo für Freitag: Videos speichern und Tobi schicken.",
        "Todos für Freitag: Videos speichern und Tobi schicken.",
        "Aufgabe für Freitag: Videos speichern und Tobi schicken.",
        "Aufgaben für Freitag: Videos speichern und Tobi schicken.",
        "Zu erledigen bis Freitag: Videos speichern und Tobi schicken.",
    ),
)
def test_personal_todo_vocabulary_does_not_trigger_project_intake(text):
    assert not _looks_like_project_intake(text)


@pytest.mark.parametrize(
    "text",
    (
        "#project:kundenportal Todo: Login verbessern",
        "#projekt:kundenportal Aufgabe: Login verbessern",
        "Project: kundenportal Todo: Login verbessern",
        "Projekt: kundenportal Aufgabe: Login verbessern",
        "Roadmap für das Kundenportal aktualisieren",
        "MVP für das Kundenportal definieren",
        "Slice für den Login planen",
    ),
)
def test_explicit_project_vocabulary_still_triggers_project_intake(text):
    assert _looks_like_project_intake(text)


def test_webhook_personal_todo_bypasses_project_intake(tmp_path):
    store = TelegramInboxStore(tmp_path)
    sessions = TelegramSessionBridgeStore(tmp_path)
    reply_calls = []

    project_intake, reply = run_webhook_project_intake_branch(
        message={"chat_id": "todo-chat-1", "message_id": 71},
        stored_message={
            "kind": "text",
            "text": "Todo für Freitag: Videos speichern und Tobi schicken.",
        },
        data_dir=tmp_path,
        store=store,
        sessions=sessions,
        project_registry_path=tmp_path / "server_project_registry.json",
        build_project_intake_preview=build_telegram_project_intake_preview,
        format_project_intake_reply=lambda _project: "should not run",
        reply_with_gate=lambda *_args, **_kwargs: reply_calls.append("reply"),
    )

    assert project_intake is None
    assert reply is None
    assert reply_calls == []
    assert not any(
        item.get("kind") == "project_intake_review"
        for item in store.history(limit=10)
    )
