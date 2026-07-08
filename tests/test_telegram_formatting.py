import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.telegram.plugin import (
    TelegramInboxStore,
    build_telegram_draft_id,
    build_telegram_readiness,
    send_telegram_photo,
    send_telegram_rich_draft,
    send_telegram_rich_message,
    send_telegram_text,
    setup,
)
from plugins.telegram.formatting import (
    format_agent_failure_reply,
    format_agent_task_action_reply,
    format_agent_task_help_reply,
    format_agent_task_missing_reply,
    format_agent_task_status_reply,
    format_agent_task_unknown_command_reply,
    format_agent_turn_reply,
    format_agenda_for_telegram,
    format_calendar_command_error_reply,
    format_calendar_readiness_for_telegram,
    format_calendar_unknown_command_reply,
    format_calendar_write_for_telegram,
    format_dsgvo_reply_text,
    format_new_chat_reply,
    format_nextcloud_transfer_blocked_reply,
    format_project_intake_apply_result,
    format_project_intake_hold_reply,
    format_project_intake_review_status,
    format_telegram_attachment_export_reply,
    format_telegram_attachment_inbox_reply,
    format_telegram_project_intake_reply,
    format_universal_inbox_memory_review_missing_reply,
    format_universal_inbox_memory_review_status,
    format_universal_inbox_memory_write_reply,
    format_universal_inbox_review_missing_reply,
    format_universal_inbox_review_status,
    format_universal_inbox_transfer_confirm_reply,
    telegram_attachment_ocr_note,
)
from src.telegram_formatting import chunk_telegram_html, render_telegram_markdown, validate_telegram_html
from tests.test_telegram_plugin import _PluginContext


def test_telegram_markdown_renderer_outputs_safe_html():
    rendered = render_telegram_markdown(
        "# Title\n\n**Bold** *italic* __under__ ~~strike~~ ||secret|| `code`\n"
        "[OpenAI](https://openai.com)\n> quote\n<script>alert(1)</script>"
    )

    assert rendered.parse_mode == "HTML"
    assert rendered.formatting_mode == "html"
    assert "<b>Title</b>" in rendered.html
    assert "<b>Bold</b>" in rendered.html
    assert "<i>italic</i>" in rendered.html
    assert "<u>under</u>" in rendered.html
    assert "<s>strike</s>" in rendered.html
    assert "<tg-spoiler>secret</tg-spoiler>" in rendered.html
    assert '<a href="https://openai.com">OpenAI</a>' in rendered.html
    assert "<blockquote>quote</blockquote>" in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert validate_telegram_html(rendered.html) is True


def test_agent_failure_formatter_only_replies_for_failed_turns():
    assert format_agent_failure_reply(None) == ""
    assert format_agent_failure_reply({"status": "accepted"}) == ""

    reply = format_agent_failure_reply({"status": "failed"})

    assert reply == (
        "Ich habe deine Nachricht erhalten und arbeite, aber das Sprachmodell "
        "konnte gerade nicht antworten. Bitte prüfe den Modell-Zugang in Odysseus."
    )


def test_agent_turn_reply_formatter_prefers_reply_text_then_failure_fallback():
    assert format_agent_turn_reply(None) == ""
    assert format_agent_turn_reply({"status": "accepted", "reply_text": "Hallo"}) == "Hallo"
    assert format_agent_turn_reply(
        {"status": "failed", "reply_text": ""},
        failure_reply=lambda turn: f"fallback:{turn['status']}",
    ) == "fallback:failed"

    fallback = format_agent_turn_reply({"status": "failed", "reply_text": ""})

    assert "Sprachmodell" in fallback


def test_agent_task_control_formatters_keep_reply_wording_stable():
    record = {
        "task_id": "tg_task_abc",
        "task_type": "website_research_to_memory",
        "status": "running",
        "progress_percent": 42,
        "gates_waiting": ("memory_write_policy", "live_go", "review", "ignored"),
    }

    assert format_agent_task_help_reply() == (
        "Task-Kommandos: /task status, /task pause, /task resume, /task cancel."
    )
    assert format_agent_task_missing_reply() == "Ich finde aktuell keinen laufenden Agent-Task."
    assert format_agent_task_missing_reply(for_action=True) == (
        "Ich finde keinen Agent-Task, auf den ich das anwenden kann."
    )
    assert format_agent_task_unknown_command_reply() == "Task-Kommando nicht erkannt. Nutze /task status."
    assert format_agent_task_status_reply(record) == (
        "Letzter Task tg_task_abc: website_research_to_memory, Status running, "
        "Fortschritt 42%. Gates: memory_write_policy, live_go, review."
    )
    assert format_agent_task_action_reply("Pause angefordert", record) == (
        "Pause angefordert fuer Task tg_task_abc."
    )


def test_calendar_control_formatters_render_compact_operator_replies():
    readiness = format_calendar_readiness_for_telegram({
        "calendars": 1,
        "events": 2,
        "due_notes": 3,
        "active_telegram_tasks": 4,
        "pending_caldav_writebacks": 5,
    })
    assert readiness == (
        "Kalender-Status: bereit. 1 Kalender, 2 Termine, 3 Erinnerungen, "
        "4 aktive Telegram-Tasks. CalDAV Writebacks offen: 5."
    )

    agenda = format_agenda_for_telegram({
        "counts": {"events": 1, "due_notes": 1, "scheduled_tasks": 0},
        "events": [{"summary": "Patchday", "dtstart": "2026-08-04T09:00"}],
        "due_notes": [{"title": "ASV pruefen", "due_date": "2026-07-07"}],
    })
    assert agenda.splitlines() == [
        "Agenda: 1 Termine, 1 Erinnerungen, 0 Tasks.",
        "- Patchday: 2026-08-04T09:00",
        "- ASV pruefen: 2026-07-07",
    ]

    reminders = format_agenda_for_telegram({
        "counts": {"due_notes": 1, "scheduled_tasks": 1},
        "due_notes": [{"title": "WLAN APs", "due_date": "today"}],
        "scheduled_tasks": [{"name": "Todo digest", "next_run": "09:00"}],
    }, reminders_only=True)
    assert reminders.splitlines() == [
        "Erinnerungen: 1 due notes, 1 geplante Tasks.",
        "- WLAN APs: today",
        "- Todo digest: 09:00",
    ]

    assert format_calendar_write_for_telegram(
        {"status": "duplicate", "task_id": "task-123456789"},
        noun="Todo-Digest",
    ) == "Todo-Digest existiert bereits. ID task-123."
    assert format_calendar_unknown_command_reply() == (
        "Kalender-Kommando nicht erkannt. Nutze /calendar, /agenda, /reminders oder /todo 09:00 mo-fr."
    )
    assert format_calendar_command_error_reply("RuntimeError") == "Kalender-Kommando blockiert: RuntimeError."


def test_dsgvo_control_formatter_keeps_privacy_wording_stable():
    assert format_dsgvo_reply_text("dsgvo_help") == (
        "Nutze /dsgvo zum Umschalten, oder /dsgvo status fuer den aktuellen Zustand."
    )
    assert format_dsgvo_reply_text("dsgvo_enable", {"after": True}) == (
        "DSGVO-Modus ist jetzt aktiv. Telegram laeuft local-only; "
        "externe Web-, Provider- und Tool-I/O ist gesperrt."
    )
    assert format_dsgvo_reply_text("dsgvo_disable", {"after": False}) == (
        "DSGVO-Modus ist jetzt aus. Normale Provider- und Tool-Regeln gelten wieder."
    )
    assert format_dsgvo_reply_text("dsgvo_disable", {"after": True, "forced_active": True}) == (
        "DSGVO-Modus bleibt aktiv, weil ein Server- oder Kompatibilitaets-Gate ihn erzwingt."
    )
    assert format_dsgvo_reply_text("dsgvo_toggle", {"after": False}) == (
        "DSGVO-Modus ist jetzt aus. Normale Provider- und Tool-Regeln gelten wieder."
    )
    assert format_dsgvo_reply_text("dsgvo_status", active=True) == (
        "DSGVO-Modus ist aktiv. Telegram nutzt local-only Verarbeitung."
    )


def test_universal_inbox_review_formatters_keep_operator_prompts_stable():
    assert format_universal_inbox_review_missing_reply() == "Keine offene Universal-Inbox-Review gefunden."

    assert format_universal_inbox_review_status({
        "universal_inbox_status": "go",
        "processable_count": 2,
    }) == "Universal Inbox: verarbeitet. Items: 2. Keine Review nötig."

    review = format_universal_inbox_review_status({
        "universal_inbox_status": "partial",
        "processable_count": 1,
    })
    assert review.splitlines() == [
        "Universal Inbox: Review nötig.",
        "Status: partial",
        "Items: 1",
        "Zum Bestätigen antworte mit /review ok.",
    ]

    ready = format_universal_inbox_memory_review_status({
        "memory_write_intent_status": "ready",
        "universal_inbox_status": "go",
    })
    assert ready.splitlines() == [
        "Universal Inbox Memory: bereit und automatisch uebernommen.",
        "Inbox-Status: go",
        "Es wird nur eine redaktierte Abstraktion geschrieben, kein Rohinhalt.",
    ]

    memory_review = format_universal_inbox_memory_review_status({
        "memory_write_intent_status": "needs_review",
        "universal_inbox_status": "partial",
    })
    assert memory_review.splitlines() == [
        "Universal Inbox Memory: Review nötig.",
        "Memory-Status: review",
        "Inbox-Status: partial",
        "Zum Bestätigen antworte mit /review memory ok.",
    ]


    assert format_universal_inbox_transfer_confirm_reply({"status": "completed"}) == (
        "Review bestaetigt. Nextcloud-Ablage wurde kopiert und verifiziert."
    )
    assert format_universal_inbox_transfer_confirm_reply({"status": "copied_unverified"}) == (
        "Review bestaetigt. Nextcloud-Ablage wurde kopiert, braucht aber Verifikation."
    )
    assert format_universal_inbox_transfer_confirm_reply({"status": "dry_run_ready"}) == (
        "Review bestaetigt. Nextcloud-Ablage ist vorbereitet, aber noch Dry-run. "
        "Live-Copy wartet auf Operator-Go."
    )
    assert format_universal_inbox_transfer_confirm_reply({
        "status": "blocked",
        "reason": "operator_live_go_missing",
    }) == "Review bestaetigt. Nextcloud-Ablage ist noch blockiert: operator_live_go_missing."
    assert format_universal_inbox_memory_review_missing_reply() == (
        "Keine offene Universal-Inbox-Memory-Review gefunden."
    )
    assert format_universal_inbox_memory_write_reply({"status": "written"}) == (
        "Memory-Review bestaetigt. Die redaktierte Abstraktion wurde ins Langzeitgedaechtnis geschrieben."
    )
    assert format_universal_inbox_memory_write_reply({
        "status": "blocked",
        "reason": "memory_writer_missing",
    }) == "Memory-Review bestaetigt, aber der Memory-Write wurde blockiert: memory_writer_missing."


def test_project_intake_formatters_keep_operator_prompts_stable():
    reply = format_telegram_project_intake_reply({
        "status": "review",
        "project_slug": "kundenportal-mvp",
        "confidence": 0.88,
        "task_count": 2,
        "decision_count": 1,
        "risk_count": 1,
        "roadmap_update_count": 1,
        "tasks": [
            {"title": "Login als MVP Slice aufnehmen"},
            {"title": "Review-Gate pruefen"},
            {"title": "Ledger aktualisieren"},
            {"title": "Nicht mehr anzeigen"},
        ],
    })

    assert reply.splitlines() == [
        "Project-Intake erkannt fuer kundenportal-mvp (88%).",
        "Tasks: 2, Decisions: 1, Risiken: 1, Roadmap-Updates: 1.",
        "Vorschlag:",
        "- Login als MVP Slice aufnehmen",
        "- Review-Gate pruefen",
        "- Ledger aktualisieren",
        (
            "Antwort: /project ok uebernimmt ins Intake-Ledger, /project hold pausiert. "
            "Projektdateien bleiben noch gesperrt."
        ),
    ]

    assert format_telegram_project_intake_reply({
        "status": "blocked",
        "reason": "project_choice_required",
    }) == (
        "Project-Intake erkannt, aber ich konnte kein Zielprojekt sicher bestimmen. "
        "Bitte sende z.B. #project:projekt-slug dazu."
    )

    assert format_project_intake_review_status({
        "project_slug": "kundenportal-mvp",
        "task_count": 2,
        "decision_count": 1,
        "risk_count": 1,
    }) == (
        "Offene Project-Intake-Review fuer kundenportal-mvp: "
        "2 Tasks, 1 Decisions, 1 Risiken. Antwort: /project ok oder /project hold."
    )
    assert format_project_intake_review_status(None) == "Keine offene Project-Intake-Review gefunden."
    assert format_project_intake_apply_result({
        "applied": True,
        "intake_merge": {
            "added_task_count": 2,
            "added_risk_count": 1,
            "added_roadmap_update_count": 1,
        },
    }) == (
        "Project-Intake bestaetigt und ins Projekt-Intake-Ledger uebernommen. "
        "Integriert: 2 neue Tasks, 1 Risiken, 1 Roadmap-Updates."
    )
    assert format_project_intake_apply_result({
        "applied": False,
        "blockers": ("registry_missing", "gate_closed"),
    }) == "Project-Intake bestaetigt, aber Apply ist blockiert: registry_missing, gate_closed."
    assert format_project_intake_hold_reply() == "Project-Intake pausiert. Ich schreibe nichts in das Projekt."
    assert format_new_chat_reply(created=True) == "Neuer Chat gestartet."
    assert format_new_chat_reply(created=False) == "Neuer Chat konnte nicht gestartet werden."


def test_attachment_export_formatter_keeps_operator_prompts_stable():
    assert format_telegram_attachment_export_reply({
        "status": "sent",
        "target_format": "pdf",
    }) == "Export fertig: Ich habe dir die PDF-Datei geschickt."

    assert format_telegram_attachment_export_reply({
        "status": "exported",
        "target_format": "pdf",
    }).splitlines() == [
        "Export fertig: PDF wurde lokal erzeugt.",
        "Die Datei ist bereit, aber der Telegram-Dokumentversand ist gerade nicht aktiv.",
    ]

    assert format_telegram_attachment_export_reply({
        "status": "ready",
        "target_format": "pdf",
        "action": "export_recent_attachment",
        "required_tool": "builtin_text_pdf",
    }).splitlines() == [
        "Export erkannt: Ziel pdf.",
        "Aktion: export_recent_attachment.",
        "Konverter: builtin_text_pdf.",
        "Die Datei kann jetzt lokal erzeugt werden.",
    ]

    assert format_telegram_attachment_export_reply({
        "status": "planned",
        "target_format": "docx",
        "action": "convert",
    }).splitlines() == [
        "Export erkannt: Ziel docx.",
        "Aktion: convert.",
        "Benoetigtes lokales Tool: noch offen.",
        "Die echte Datei-Ausgabe ist noch nicht aktiviert; der sichere Export-Plan ist vorgemerkt.",
    ]

    assert format_telegram_attachment_export_reply({
        "status": "blocked",
        "reason": "recent_attachment_missing",
    }) == "Export erkannt, aber blockiert: recent_attachment_missing."


def test_attachment_inbox_formatter_keeps_operator_prompts_stable():
    assert format_telegram_attachment_inbox_reply({
        "status": "processed",
        "universal_inbox_status": "go",
        "memory_write_intent_status": "ready",
        "nextcloud_transfer_status": "completed",
        "nextcloud_verified": True,
    }) == "✅ Datei abgelegt."

    ready = format_telegram_attachment_inbox_reply({
        "status": "processed",
        "universal_inbox_status": "go",
        "memory_write_intent_status": "ready",
        "processable_count": 2,
        "memory_auto_write_status": "written",
        "maintenance_action": "dedupe",
    })
    assert ready.splitlines() == [
        "Anhang verarbeitet. Items: 2. Keine Inbox-Review noetig.",
        "Memory/Raptor-Intent: ready.",
        "Maintenance: dedupe.",
        "Redigierte Abstraktion automatisch ins Memory/RaptorGraph geschrieben.",
    ]

    review = format_telegram_attachment_inbox_reply({
        "status": "processed",
        "universal_inbox_status": "partial",
        "memory_write_intent_status": "review",
        "processable_count": 1,
        "extraction_warning_codes": ("pdf_ocr_required",),
    })
    assert review.splitlines() == [
        "Anhang empfangen und geprüft. Review nötig.",
        "Universal-Inbox-Status: partial",
        "Items: 1",
        "OCR: noetig, aber lokaler OCR-Adapter ist noch nicht aktiv.",
        "Zum Bestätigen antworte mit /review ok.",
    ]

    assert format_telegram_attachment_inbox_reply({
        "status": "blocked",
        "reason": "policy_gate",
    }) == "Anhang empfangen, aber blockiert: policy_gate."

    assert format_telegram_attachment_inbox_reply({
        "status": "failed",
        "reason": "parse_error",
    }) == "Anhang empfangen, aber Verarbeitung fehlgeschlagen: parse_error."


def test_attachment_ocr_note_formatter_maps_warning_codes():
    assert telegram_attachment_ocr_note({"extraction_warning_codes": ("image_ocr_required",)}) == (
        "OCR: noetig, aber lokaler OCR-Adapter ist noch nicht aktiv."
    )
    assert telegram_attachment_ocr_note({"extraction_warning_codes": ("pdf_ocr_blocked_by_policy",)}) == (
        "OCR: durch Datenschutz-/Policy-Gate blockiert."
    )
    assert telegram_attachment_ocr_note({"extraction_warning_codes": ("pdf_ocr_budget_exceeded",)}) == (
        "OCR: Budget erreicht; bitte mit hoeherem OCR-Budget erneut starten."
    )
    assert telegram_attachment_ocr_note({"extraction_warning_codes": ("image_ocr_unavailable",)}) == (
        "OCR: lokaler OCR-Adapter ist nicht verfuegbar."
    )
    assert telegram_attachment_ocr_note({"extraction_warning_codes": ("pdf_ocr_failed",)}) == (
        "OCR: lokaler OCR-Lauf ist fehlgeschlagen."
    )
    assert telegram_attachment_ocr_note({"extraction_warning_codes": ("image_ocr_empty",)}) == (
        "OCR: Bild geprueft, aber kein Text erkannt."
    )
    assert telegram_attachment_ocr_note({"extraction_warning_codes": ()}) == ""


def test_nextcloud_transfer_blocked_formatter_keeps_secret_boundary_wording_stable():
    missing_config = format_nextcloud_transfer_blocked_reply({
        "status": "blocked",
        "reason": "nextcloud_server_config_missing",
    })

    assert missing_config == (
        "Review bestaetigt. Nextcloud-Ablage ist blockiert: Die serverseitige "
        "Nextcloud-Konfiguration ist nicht verfuegbar. Bitte keine Zugangsdaten "
        "in Telegram senden; Nextcloud-Zugangsdaten werden nur serverseitig hinterlegt."
    )
    assert "NEXTCLOUD_WEBDAV" not in missing_config

    assert format_nextcloud_transfer_blocked_reply({
        "status": "blocked",
        "reason": "operator_live_go_missing",
    }) == "Review bestaetigt. Nextcloud-Ablage ist noch blockiert: operator_live_go_missing."


def test_telegram_renderer_rejects_unsafe_link_targets():
    rendered = render_telegram_markdown("[bad](javascript:alert(1)) and <b raw>")

    assert "javascript:alert" in rendered.html
    assert "<a href" not in rendered.html
    assert "&lt;b raw&gt;" in rendered.html
    assert validate_telegram_html(rendered.html) is True


def test_telegram_tables_and_fenced_code_are_safe_pre_blocks():
    rendered = render_telegram_markdown("| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('<x>')\n```")

    assert "<pre>| A | B |" in rendered.html
    assert "<pre><code>print(&#x27;&lt;x&gt;&#x27;)</code></pre>" in rendered.html
    assert validate_telegram_html(rendered.html) is True


def test_telegram_chunking_respects_classic_message_limit():
    chunks = chunk_telegram_html(("word " * 1200).strip(), max_chars=4096)

    assert len(chunks) == 2
    assert all(len(chunk) <= 4096 for chunk in chunks)


def test_send_telegram_text_uses_html_parse_mode_for_single_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": len(calls)}}

    result = send_telegram_text("chat-1", "**Hello**", http_post=_post)

    assert result["ok"] is True
    assert result["telegram_message_id"] == 1
    assert result["message_count"] == 1
    assert result["delivery_mode"] == "classic_html"
    assert result["formatting_mode"] == "html"
    assert all(call[1]["parse_mode"] == "HTML" for call in calls)


def test_send_telegram_text_uses_plaintext_chunks_for_long_html(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": len(calls)}}

    long_code = "```python\n" + "\n".join(f"print({index})" for index in range(900)) + "\n```"
    result = send_telegram_text("chat-1", "**Plan**\n" + long_code, http_post=_post)

    assert result["ok"] is True
    assert result["message_count"] == len(calls)
    assert result["message_count"] > 1
    assert result["delivery_mode"] == "classic_plaintext_chunks"
    assert result["formatting_mode"] == "plaintext_chunk_fallback"
    assert result["parse_mode"] == ""
    assert result["truncated"] is False
    assert all("parse_mode" not in call[1] for call in calls)
    assert all(len(call[1]["text"]) <= 4096 for call in calls)
    assert calls[0][1]["text"].startswith("Teil 1/")


def test_send_telegram_text_caps_long_replies(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_MAX_REPLY_CHUNKS", "2")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": len(calls)}}

    huge_reply = "```python\n" + "\n".join(f"print({index})" for index in range(2000)) + "\n```"
    result = send_telegram_text("chat-1", huge_reply, http_post=_post)

    assert result["ok"] is True
    assert result["message_count"] == 2
    assert result["max_reply_chunks"] == 2
    assert result["truncated"] is True
    assert result["delivery_mode"] == "classic_plaintext_chunks_truncated"
    assert calls[0][1]["text"].startswith("Teil 1/2")
    assert calls[1][1]["text"].startswith("Teil 2/2")
    assert "Weitere Teile wurden gekuerzt" in calls[1][1]["text"]
    assert all("parse_mode" not in call[1] for call in calls)
    assert all(len(call[1]["text"]) <= 4096 for call in calls)


def test_readiness_exposes_rich_status_without_raw_payloads(tmp_path):
    store = TelegramInboxStore(tmp_path)
    store.append_outbound(
        "chat-1",
        "**Hello**",
        delivery_status="sent",
        delivery_mode="classic_html",
        formatting_mode="html",
    )

    readiness = build_telegram_readiness(tmp_path)
    encoded = json.dumps(readiness, ensure_ascii=False)

    assert readiness["rich_messages_enabled"] is False
    assert readiness["rich_drafts_enabled"] is False
    assert readiness["formatting_mode"] == "html"
    assert readiness["last_delivery_mode"] == "classic_html"
    assert readiness["last_delivery_status"] == "sent"
    assert readiness["raw_rich_payload_visible"] is False
    assert "chat-1" not in encoded


def test_rich_draft_helpers_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_RICH_MESSAGES_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_RICH_DRAFTS_ENABLED", raising=False)
    calls = []

    with pytest.raises(ValueError, match="rich messages"):
        send_telegram_rich_draft("chat-1", "partial", http_post=lambda url, payload: calls.append(payload))

    assert calls == []


def test_send_telegram_photo_uses_send_photo_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    image = tmp_path / "screen.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = []

    def _post(url, payload, file_field, file_path, *, filename, mime_type):
        calls.append((url, dict(payload), file_field, str(file_path), filename, mime_type))
        return {"ok": True, "result": {"message_id": 44}}

    result = send_telegram_photo(
        "chat-1",
        image,
        filename="program-screenshot.png",
        caption="Screenshot",
        http_post_multipart=_post,
    )

    assert result["ok"] is True
    assert result["delivery_mode"] == "photo"
    assert result["formatting_mode"] == "photo_caption"
    assert calls[0][0].endswith("/sendPhoto")
    assert calls[0][2] == "photo"
    assert calls[0][4] == "program-screenshot.png"
    assert calls[0][5] == "image/png"


def test_rich_draft_uses_stable_nonzero_draft_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_RICH_DRAFTS_ENABLED", "true")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": 7}}

    first = build_telegram_draft_id(chat_id="chat-1", source_message_id=42)
    second = build_telegram_draft_id(chat_id="chat-1", source_message_id=42)
    result = send_telegram_rich_draft(
        "chat-1",
        "<tg-thinking>draft only</tg-thinking>\n**Partial**",
        source_message_id=42,
        http_post=_post,
    )

    assert first == second
    assert first > 0
    assert result["delivery_mode"] == "rich_draft"
    assert result["draft_id"] == first
    assert result["draft_id_value_visible"] is False
    payload = calls[0][1]
    assert payload["draft_id"] == first
    assert "rich_message" in payload
    assert "Partial" in payload["rich_message"]
    assert result["raw_rich_payload_visible"] is False


def test_final_rich_message_strips_draft_thinking(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "true")
    calls = []

    def _post(url, payload):
        calls.append((url, dict(payload)))
        return {"ok": True, "result": {"message_id": 10}}

    result = send_telegram_rich_message(
        "chat-1",
        "<tg-thinking>draft only</tg-thinking>\n**Final**",
        http_post=_post,
    )

    assert result["delivery_mode"] == "rich_final"
    assert result["telegram_message_id"] == 10
    assert "Final" in calls[0][1]["rich_message"]
    assert "draft only" not in calls[0][1]["rich_message"]
    assert result["raw_rich_payload_visible"] is False


def test_reply_route_falls_back_to_classic_html_when_final_rich_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_RICH_MESSAGES_ENABLED", "true")

    def _rich_fail(*_args, **_kwargs):
        raise ValueError("rich transport offline")

    classic_calls = []

    def _classic(chat_id, text):
        classic_calls.append((chat_id, text))
        return {
            "ok": True,
            "telegram_message_id": 77,
            "delivery_mode": "classic_html",
            "formatting_mode": "html",
            "token_value_visible": False,
            "raw_rich_payload_visible": False,
        }

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_rich_message", _rich_fail)
    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", _classic)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/reply", json={"chat_id": "123", "text": "**Hallo**"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sent"]["delivery_mode"] == "classic_html_fallback"
    assert payload["sent"]["rich_fallback_reason"] == "rich transport offline"
    assert classic_calls == [("123", "**Hallo**")]
    history = TelegramInboxStore(tmp_path).history(chat_id="123")
    assert history[0]["delivery_mode"] == "classic_html_fallback"
    assert history[0]["formatting_mode"] == "html"
