import json

import src.agent_task_ledger as agent_task_ledger

from plugins.telegram.control_service import (
    handle_agent_task_control_command,
    handle_calendar_control_command,
    handle_dsgvo_control_command,
    handle_new_chat_control_command,
    handle_project_intake_control_command,
    handle_universal_inbox_control_command,
    public_agent_task_record,
)


class FakeUniversalInboxStore:
    def __init__(self, data_dir, *, review=None, memory_review=None):
        self.data_dir = data_dir
        self.review = review
        self.memory_review = memory_review
        self.events = []

    def latest_universal_inbox_review(self, *, chat_id=None):
        return self.review if chat_id == "chat_safe" else None

    def latest_universal_inbox_memory_review(self, *, chat_id=None):
        return self.memory_review if chat_id == "chat_safe" else None

    def append_event(self, **event):
        self.events.append(event)


class FakeProjectIntakeStore:
    def __init__(self, data_dir, *, review=None):
        self.data_dir = data_dir
        self.review = review
        self.events = []

    def latest_project_intake_review(self, *, chat_id=None):
        return self.review if chat_id == "chat_safe" else None

    def append_event(self, **event):
        self.events.append(event)


class FakeSessionStore:
    def __init__(self, binding):
        self.binding = binding
        self.calls = []

    def rebind_chat(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.binding)


def test_public_agent_task_record_is_redacted_and_typed():
    record = public_agent_task_record(
        {
            "task_id": "task-1",
            "task_type": "coding_agent_task",
            "status": "running",
            "target_ref": "repo:demo",
            "progress_percent": "42",
            "gates_waiting": ["operator_go"],
            "chat_id": "raw-chat-id",
        }
    )

    assert record == {
        "task_id": "task-1",
        "task_type": "coding_agent_task",
        "status": "running",
        "target_ref": "repo:demo",
        "progress_percent": 42,
        "gates_waiting": ("operator_go",),
        "raw_content_visible": False,
    }
    assert "raw-chat-id" not in json.dumps(record, sort_keys=True)


def test_handle_agent_task_control_command_status_and_pause_use_redacted_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path / "task-ledger"))

    agent_task_ledger.record_task_event(
        task_id="tg_task_abc",
        task_type="website_research_to_memory",
        status="running",
        correlation_id="tg_task_abc",
        target_ref="https://www.asv-bw.de/",
        progress_percent=42,
        gates_waiting=("memory_write_policy",),
    )

    status = handle_agent_task_control_command("agent_task_status")
    assert status["status"] == "agent_task_status"
    assert status["agent_task"]["raw_content_visible"] is False
    assert "tg_task_abc" in status["reply_text"]
    assert "42%" in status["reply_text"]

    pause = handle_agent_task_control_command("agent_task_pause")
    assert pause["status"] == "pause_requested"
    assert pause["agent_task"]["status"] == "pause_requested"
    assert "raw_content_visible" in pause["agent_task"]
    assert "raw-chat-id" not in json.dumps(pause["agent_task"], sort_keys=True)


def test_handle_agent_task_control_command_handles_empty_and_unknown_ledgers(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path / "task-ledger"))

    missing = handle_agent_task_control_command("agent_task_status")
    assert missing == {
        "status": "agent_task_missing",
        "reply_text": "Ich finde aktuell keinen laufenden Agent-Task.",
        "agent_task": {"raw_content_visible": False},
    }

    unknown = handle_agent_task_control_command("agent_task_surprise")
    assert unknown["status"] == "agent_task_unknown_command"
    assert unknown["agent_task"]["raw_content_visible"] is False


def test_handle_dsgvo_control_command_enables_replies_and_syncs_pin():
    set_calls = []
    replies = []
    pin_calls = []

    result = handle_dsgvo_control_command(
        "dsgvo_enable",
        message={"kind": "text", "message_id": 34},
        raw_chat_id="raw-chat-id",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"exit_code": 0, "sent": {"telegram_message_id": 88}},
        store="store",
        pin_store="pins",
        set_dsgvo_mode=lambda enabled: set_calls.append(enabled) or {"after": enabled},
        dsgvo_mode_active=lambda: False,
        dsgvo_reply_text=lambda command, state: f"reply:{command}:{state['after']}",
        sync_dsgvo_pin_state=lambda **kwargs: pin_calls.append(kwargs) or {"status": "pinned"},
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
        },
    )

    assert result["status"] == "dsgvo_enabled"
    assert result["dsgvo_mode"] is True
    assert result["pin_status"] == "pinned"
    assert set_calls == [True]
    assert replies == [("chat_safe", "reply:dsgvo_enable:True", 34)]
    assert pin_calls[0]["chat_id"] == "chat_safe"
    assert pin_calls[0]["store"] == "store"
    assert pin_calls[0]["pin_store"] == "pins"
    assert pin_calls[0]["reply_result"]["sent"]["telegram_message_id"] == 88


def test_handle_dsgvo_control_command_status_does_not_mutate_mode():
    set_calls = []

    result = handle_dsgvo_control_command(
        "dsgvo_status",
        message={"kind": "text"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=None,
        pin_store=None,
        set_dsgvo_mode=lambda enabled: set_calls.append(enabled) or {"after": enabled},
        dsgvo_mode_active=lambda: True,
        dsgvo_reply_text=lambda command, state: f"reply:{command}:{state}",
        sync_dsgvo_pin_state=lambda **_kwargs: {"status": "pin_store_missing"},
        build_agent_bridge_request=lambda _message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": None,
        },
    )

    assert result["status"] == "dsgvo_status"
    assert result["dsgvo_mode"] is True
    assert result["pin_status"] == "pin_store_missing"
    assert set_calls == []


def test_handle_dsgvo_control_command_ignores_other_commands():
    result = handle_dsgvo_control_command(
        "agent_task_status",
        message={"kind": "text"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=None,
        pin_store=None,
        set_dsgvo_mode=lambda _enabled: {"after": True},
        dsgvo_mode_active=lambda: False,
        dsgvo_reply_text=lambda _command, _state: "reply",
        sync_dsgvo_pin_state=lambda **_kwargs: {"status": "unused"},
        build_agent_bridge_request=lambda _message, raw_chat_id: {"chat_id": raw_chat_id},
    )

    assert result is None


def test_handle_calendar_control_command_readiness_replies_with_injected_helpers():
    replies = []
    readiness_calls = []

    result = handle_calendar_control_command(
        "calendar_readiness",
        message={"kind": "text", "message_id": 34, "text": "/calendar"},
        raw_chat_id="raw-chat-id",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"exit_code": 0},
        memory_owner=" alice ",
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
        },
        build_calendar_readiness=lambda **kwargs: readiness_calls.append(kwargs) or {
            "calendars": 1,
            "events": 2,
            "due_notes": 3,
            "active_telegram_tasks": 4,
            "pending_caldav_writebacks": 5,
        },
        build_agenda_packet=lambda **_kwargs: {"counts": {}},
        write_reminder_note=lambda **_kwargs: {"status": "created"},
        write_todo_digest_schedule=lambda **_kwargs: {"status": "created"},
    )

    assert result["status"] == "calendar_ready"
    assert result["calendar"]["calendars"] == 1
    assert "Kalender-Status" in result["reply_text"]
    assert replies == [("chat_safe", result["reply_text"], 34)]
    assert readiness_calls == [{"owner": "alice"}]


def test_handle_calendar_control_command_reminder_create_parses_tail():
    writes = []

    result = handle_calendar_control_command(
        "calendar_reminder_create",
        message={"kind": "text", "message_id": 35, "text": "/remind 2026-07-04T09:00 OctoGate pruefen"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        memory_owner=None,
        build_agent_bridge_request=lambda _message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": 35,
        },
        build_calendar_readiness=lambda **_kwargs: {},
        build_agenda_packet=lambda **_kwargs: {"counts": {}},
        write_reminder_note=lambda **kwargs: writes.append(kwargs) or {
            "status": "created",
            "note_id": "note-123456",
        },
        write_todo_digest_schedule=lambda **_kwargs: {"status": "created"},
    )

    assert result["status"] == "calendar_reminder_created"
    assert "Erinnerung erstellt" in result["reply_text"]
    assert writes == [
        {
            "owner": None,
            "action": "add",
            "title": "OctoGate pruefen",
            "due_date": "2026-07-04T09:00",
        }
    ]


def test_handle_calendar_control_command_todo_digest_parses_time_weekdays_and_handles_errors():
    todos = []

    result = handle_calendar_control_command(
        "calendar_todo_digest_create",
        message={"kind": "text", "message_id": 36, "text": "/todo 08:30 mo-fr"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        memory_owner="alice",
        build_agent_bridge_request=lambda _message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": 36,
        },
        build_calendar_readiness=lambda **_kwargs: {},
        build_agenda_packet=lambda **_kwargs: {"counts": {}},
        write_reminder_note=lambda **_kwargs: {"status": "created"},
        write_todo_digest_schedule=lambda **kwargs: todos.append(kwargs) or {
            "status": "duplicate",
            "task_id": "task-123456",
        },
    )

    assert result["status"] == "calendar_todo_digest_duplicate"
    assert "Todo-Digest existiert bereits" in result["reply_text"]
    assert todos == [
        {
            "owner": "alice",
            "scheduled_time": "08:30",
            "weekdays": "mo-fr",
            "output_target": "telegram",
        }
    ]

    error = handle_calendar_control_command(
        "calendar_agenda",
        message={"kind": "text", "message_id": 37, "text": "/agenda"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        memory_owner="alice",
        build_agent_bridge_request=lambda _message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": 37,
        },
        build_calendar_readiness=lambda **_kwargs: {},
        build_agenda_packet=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private detail")),
        write_reminder_note=lambda **_kwargs: {"status": "created"},
        write_todo_digest_schedule=lambda **_kwargs: {"status": "created"},
    )

    assert error["status"] == "calendar_command_error"
    assert error["calendar"] == {"status": "error", "error": "RuntimeError", "raw_content_visible": False}
    assert "private detail" not in json.dumps(error, ensure_ascii=False)


def test_handle_universal_inbox_control_command_status_replies_with_injected_readiness():
    replies = []

    result = handle_universal_inbox_control_command(
        "universal_inbox_status",
        message={"kind": "text", "message_id": 40, "text": "/inbox"},
        raw_chat_id="raw-chat-id",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"ok": True},
        store=None,
        memory_manager=None,
        memory_vector=None,
        memory_owner=None,
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
        },
        build_universal_inbox_readiness=lambda: {"status": "partial", "raw_content_visible": False},
        format_universal_inbox_readiness=lambda snapshot: f"Universal Inbox: {snapshot['status']}",
        format_universal_inbox_review_status=lambda _review: "unused",
        build_nextcloud_transfer_dry_run=lambda **_kwargs: {"status": "unused"},
        format_nextcloud_transfer_blocked_reply=lambda _transfer: "unused",
        format_universal_inbox_memory_review_status=lambda _review: "unused",
        execute_memory_review_write=lambda **_kwargs: {"status": "unused"},
    )

    assert result["status"] == "universal_inbox_partial"
    assert result["universal_inbox"]["raw_content_visible"] is False
    assert replies == [("chat_safe", "Universal Inbox: partial", 40)]


def test_handle_universal_inbox_control_command_review_confirm_appends_redacted_transfer(tmp_path):
    store = FakeUniversalInboxStore(
        tmp_path,
        review={"message_id": 41, "universal_inbox_status": "partial", "file_id": "secret-file-id"},
    )

    result = handle_universal_inbox_control_command(
        "universal_inbox_review_confirm",
        message={"kind": "text", "message_id": 42, "text": "/review ok"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=store,
        memory_manager=None,
        memory_vector=None,
        memory_owner=None,
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
        },
        build_universal_inbox_readiness=lambda: {},
        format_universal_inbox_readiness=lambda _snapshot: "unused",
        format_universal_inbox_review_status=lambda _review: "unused",
        build_nextcloud_transfer_dry_run=lambda **kwargs: {
            "status": "dry_run_ready",
            "dry_run": True,
            "writes_performed": False,
            "verified": False,
            "review_approved": True,
            "saw_data_dir": kwargs["data_dir"] == tmp_path,
        },
        format_nextcloud_transfer_blocked_reply=lambda _transfer: "unused",
        format_universal_inbox_memory_review_status=lambda _review: "unused",
        execute_memory_review_write=lambda **_kwargs: {"status": "unused"},
    )

    assert result["status"] == "universal_inbox_review_confirmed"
    assert result["nextcloud_transfer"]["status"] == "dry_run_ready"
    assert "Operator-Go" in result["reply_text"]
    assert [event["kind"] for event in store.events] == [
        "universal_inbox_review",
        "universal_inbox_nextcloud_transfer",
    ]
    assert store.events[1]["target_path_visible"] is False
    assert store.events[1]["raw_identifiers_visible"] is False
    assert "secret-file-id" not in json.dumps(store.events, sort_keys=True)


def test_handle_universal_inbox_control_command_memory_confirm_writes_redacted_event(tmp_path):
    store = FakeUniversalInboxStore(
        tmp_path,
        memory_review={
            "message_id": 43,
            "memory_write_intent_status": "ready",
            "universal_inbox_status": "go",
            "filename": "private-reference.txt",
        },
    )

    result = handle_universal_inbox_control_command(
        "universal_inbox_memory_review_confirm",
        message={"kind": "text", "message_id": 44, "text": "/review memory ok"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=store,
        memory_manager="memory-manager",
        memory_vector="memory-vector",
        memory_owner="homebase",
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
        },
        build_universal_inbox_readiness=lambda: {},
        format_universal_inbox_readiness=lambda _snapshot: "unused",
        format_universal_inbox_review_status=lambda _review: "unused",
        build_nextcloud_transfer_dry_run=lambda **_kwargs: {"status": "unused"},
        format_nextcloud_transfer_blocked_reply=lambda _transfer: "unused",
        format_universal_inbox_memory_review_status=lambda _review: "unused",
        execute_memory_review_write=lambda **kwargs: {
            "status": "written",
            "writes_performed": True,
            "memory_records_written": 1,
            "raptorgraph_events_written": 1,
            "owner": kwargs["memory_owner"],
        },
    )

    assert result["status"] == "universal_inbox_memory_review_confirmed"
    assert result["memory_write"]["status"] == "written"
    assert "Langzeitgedaechtnis geschrieben" in result["reply_text"]
    assert [event["kind"] for event in store.events] == [
        "universal_inbox_memory_review",
        "universal_inbox_memory_write",
    ]
    assert store.events[1]["memory_records_written"] == 1
    assert store.events[1]["raw_content_visible"] is False
    assert "private-reference.txt" not in json.dumps(store.events, sort_keys=True)


def test_handle_universal_inbox_control_command_ignores_other_commands():
    result = handle_universal_inbox_control_command(
        "project_intake_review_status",
        message={"kind": "text"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=None,
        memory_manager=None,
        memory_vector=None,
        memory_owner=None,
        build_agent_bridge_request=lambda _message, raw_chat_id: {"chat_id": raw_chat_id},
        build_universal_inbox_readiness=lambda: {},
        format_universal_inbox_readiness=lambda _snapshot: "unused",
        format_universal_inbox_review_status=lambda _review: "unused",
        build_nextcloud_transfer_dry_run=lambda **_kwargs: {"status": "unused"},
        format_nextcloud_transfer_blocked_reply=lambda _transfer: "unused",
        format_universal_inbox_memory_review_status=lambda _review: "unused",
        execute_memory_review_write=lambda **_kwargs: {"status": "unused"},
    )

    assert result is None


def test_handle_project_intake_control_command_confirm_appends_redacted_event(tmp_path):
    store = FakeProjectIntakeStore(
        tmp_path,
        review={
            "source_message_id": 70,
            "project_slug": "kundenportal-mvp",
            "task_count": 2,
            "decision_count": 1,
            "risk_count": 1,
            "roadmap_update_count": 1,
            "project_intake_proposal": {"raw_text": "private project note"},
        },
    )

    result = handle_project_intake_control_command(
        "project_intake_review_confirm",
        message={"kind": "text", "message_id": 71, "text": "/project ok"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=store,
        project_registry_path=tmp_path / "server_project_registry.json",
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
        },
        apply_project_intake_review=lambda **kwargs: {
            "status": "applied",
            "applied": True,
            "event_id": "evt-1",
            "saw_registry": kwargs["project_registry_path"] == tmp_path / "server_project_registry.json",
            "intake_merge": {
                "added_task_count": 2,
                "added_risk_count": 1,
                "added_roadmap_update_count": 1,
            },
        },
        format_project_intake_review_status=lambda _review: "unused",
    )

    assert result["status"] == "project_intake_review_confirmed"
    assert "Integriert: 2 neue Tasks" in result["reply_text"]
    assert len(store.events) == 1
    assert store.events[0]["status"] == "confirmed"
    assert store.events[0]["project_intake_apply_performed"] is True
    assert store.events[0]["raw_identifiers_visible"] is False
    assert "private project note" not in json.dumps(store.events, sort_keys=True)


def test_handle_project_intake_control_command_hold_and_missing_are_redacted(tmp_path):
    store = FakeProjectIntakeStore(
        tmp_path,
        review={"source_message_id": 72, "project_slug": "kundenportal-mvp"},
    )
    replies = []

    held = handle_project_intake_control_command(
        "project_intake_review_hold",
        message={"kind": "text", "message_id": 73, "text": "/project hold"},
        raw_chat_id="raw-chat-id",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"ok": True},
        store=store,
        project_registry_path=None,
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
        },
        apply_project_intake_review=lambda **_kwargs: {"status": "unused"},
        format_project_intake_review_status=lambda _review: "unused",
    )

    assert held["status"] == "project_intake_review_held"
    assert store.events == [{
        "kind": "project_intake_review",
        "status": "held",
        "chat_id": "chat_safe",
        "source_message_id": 72,
        "project_slug": "kundenportal-mvp",
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "project_intake_apply_performed": False,
    }]
    assert replies == [("chat_safe", held["reply_text"], 73)]

    missing = handle_project_intake_control_command(
        "project_intake_review_status",
        message={"kind": "text", "message_id": 74, "text": "/project status"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=None,
        project_registry_path=None,
        build_agent_bridge_request=lambda _message, raw_chat_id: {
            "chat_id": raw_chat_id,
            "source_message_id": None,
        },
        apply_project_intake_review=lambda **_kwargs: {"status": "unused"},
        format_project_intake_review_status=lambda review: "missing" if review is None else "status",
    )

    assert missing["status"] == "project_intake_review_missing"
    assert missing["reply_text"] == "missing"


def test_handle_project_intake_control_command_ignores_other_commands():
    result = handle_project_intake_control_command(
        "new_chat",
        message={"kind": "text"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        store=None,
        project_registry_path=None,
        build_agent_bridge_request=lambda _message, raw_chat_id: {"chat_id": raw_chat_id},
        apply_project_intake_review=lambda **_kwargs: {"status": "unused"},
        format_project_intake_review_status=lambda _review: "unused",
    )

    assert result is None


def test_handle_new_chat_control_command_rebinds_session_and_replies():
    replies = []
    sessions = FakeSessionStore({"session_id": "new-session", "last_selected_scope": "normal"})

    result = handle_new_chat_control_command(
        "new_chat",
        message={"kind": "text", "message_id": 80, "text": "/new"},
        raw_chat_id="raw-chat-id",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"ok": True},
        sessions=sessions,
        session_creator=lambda **_kwargs: {"session_id": "created"},
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
            "session_alias": "telegram:chat_safe",
            "recommended_session_name": "Telegram Chat Safe",
            "desired_session_scope": "normal",
        },
    )

    assert result["status"] == "new_chat_bound"
    assert result["binding"]["session_id"] == "new-session"
    assert result["reply_text"] == "Neuer Chat gestartet."
    assert replies == [("chat_safe", "Neuer Chat gestartet.", 80)]
    assert sessions.calls[0]["chat_id"] == "chat_safe"
    assert sessions.calls[0]["session_alias"] == "telegram:chat_safe"


def test_handle_new_chat_control_command_pending_and_ignores_other_commands():
    pending = handle_new_chat_control_command(
        "new_chat",
        message={"kind": "text", "message_id": 81, "text": "/new"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        sessions=FakeSessionStore({}),
        session_creator=lambda **_kwargs: {},
        build_agent_bridge_request=lambda message, raw_chat_id: {
            "chat_id": "chat_safe",
            "source_message_id": message["message_id"],
            "session_alias": "telegram:chat_safe",
            "recommended_session_name": "Telegram Chat Safe",
        },
    )

    assert pending["status"] == "new_chat_pending_bridge"
    assert pending["reply_text"] == "Neuer Chat konnte nicht gestartet werden."

    ignored = handle_new_chat_control_command(
        "project_intake_review_status",
        message={"kind": "text"},
        raw_chat_id="raw-chat-id",
        reply_handler=None,
        sessions=FakeSessionStore({}),
        session_creator=lambda **_kwargs: {},
        build_agent_bridge_request=lambda _message, raw_chat_id: {"chat_id": raw_chat_id},
    )

    assert ignored is None
