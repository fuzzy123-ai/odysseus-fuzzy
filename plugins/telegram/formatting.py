"""Shared Telegram reply formatting helpers.

These functions are deterministic and local-only: they do not inspect secrets,
call Telegram, or mutate repo/runtime state.
"""

from __future__ import annotations

from typing import Any

from src.memory_triage_contract import normalize_memory_write_intent_status


def format_agent_task_help_reply() -> str:
    return "Task-Kommandos: /task status, /task pause, /task resume, /task cancel."


def format_agent_task_missing_reply(*, for_action: bool = False) -> str:
    if for_action:
        return "Ich finde keinen Agent-Task, auf den ich das anwenden kann."
    return "Ich finde aktuell keinen laufenden Agent-Task."


def format_agent_task_unknown_command_reply() -> str:
    return "Task-Kommando nicht erkannt. Nutze /task status."


def format_agent_task_status_reply(record: dict[str, Any]) -> str:
    task_id = str(record.get("task_id") or "")
    task_type = str(record.get("task_type") or "unknown")
    status = str(record.get("status") or "unknown")
    progress = int(record.get("progress_percent") or 0)
    gates = tuple(str(item) for item in record.get("gates_waiting") or ())
    gate_text = f" Gates: {', '.join(gates[:3])}." if gates else ""
    return f"Letzter Task {task_id}: {task_type}, Status {status}, Fortschritt {progress}%.{gate_text}"


def format_agent_task_action_reply(action_text: str, record: dict[str, Any]) -> str:
    return f"{action_text} fuer Task {record.get('task_id')}."


def format_agent_failure_reply(agent_turn: dict[str, Any] | None) -> str:
    if not agent_turn or str(agent_turn.get("status") or "").lower() != "failed":
        return ""
    return (
        "Ich habe deine Nachricht erhalten und arbeite, aber das Sprachmodell "
        "konnte gerade nicht antworten. Bitte prüfe den Modell-Zugang in Odysseus."
    )


def format_agent_turn_reply(
    agent_turn: dict[str, Any] | None,
    *,
    failure_reply: Any | None = None,
) -> str:
    if not agent_turn:
        return ""
    reply_text = str(agent_turn.get("reply_text") or "")
    if reply_text:
        return reply_text
    if callable(failure_reply):
        return str(failure_reply(agent_turn) or "")
    return format_agent_failure_reply(agent_turn)


def format_telegram_project_intake_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "blocked")
    if status == "blocked":
        reason = str(result.get("reason") or "project_choice_required")
        if reason == "project_choice_required":
            return (
                "Project-Intake erkannt, aber ich konnte kein Zielprojekt sicher bestimmen. "
                "Bitte sende z.B. #project:projekt-slug dazu."
            )
        return f"Project-Intake blockiert: {reason}."
    project = str(result.get("project_slug") or "unknown")
    confidence = float(result.get("confidence") or 0)
    lines = [
        f"Project-Intake erkannt fuer {project} ({round(confidence * 100)}%).",
        (
            f"Tasks: {int(result.get('task_count') or 0)}, "
            f"Decisions: {int(result.get('decision_count') or 0)}, "
            f"Risiken: {int(result.get('risk_count') or 0)}, "
            f"Roadmap-Updates: {int(result.get('roadmap_update_count') or 0)}."
        ),
    ]
    task_titles = []
    for task in tuple(result.get("tasks") or ())[:3]:
        if isinstance(task, dict) and task.get("title"):
            task_titles.append(str(task.get("title")))
    if task_titles:
        lines.append("Vorschlag:")
        lines.extend(f"- {title}" for title in task_titles)
    lines.append(
        "Antwort: /project ok uebernimmt ins Intake-Ledger, /project hold pausiert. "
        "Projektdateien bleiben noch gesperrt."
    )
    return "\n".join(lines)


def format_project_intake_review_status(review: dict[str, Any] | None) -> str:
    if review is None:
        return "Keine offene Project-Intake-Review gefunden."
    project = str(review.get("project_slug") or "unbekannt")
    return (
        f"Offene Project-Intake-Review fuer {project}: "
        f"{int(review.get('task_count') or 0)} Tasks, "
        f"{int(review.get('decision_count') or 0)} Decisions, "
        f"{int(review.get('risk_count') or 0)} Risiken. "
        "Antwort: /project ok oder /project hold."
    )


def format_project_intake_apply_result(apply_report: dict[str, Any]) -> str:
    if bool(apply_report.get("applied")):
        merge_report = apply_report.get("intake_merge") if isinstance(apply_report.get("intake_merge"), dict) else {}
        return (
            "Project-Intake bestaetigt und ins Projekt-Intake-Ledger uebernommen. "
            f"Integriert: {int(merge_report.get('added_task_count') or 0)} neue Tasks, "
            f"{int(merge_report.get('added_risk_count') or 0)} Risiken, "
            f"{int(merge_report.get('added_roadmap_update_count') or 0)} Roadmap-Updates."
        )
    blockers = ", ".join(str(item) for item in apply_report.get("blockers") or ("apply_blocked",))
    return f"Project-Intake bestaetigt, aber Apply ist blockiert: {blockers}."


def format_project_intake_hold_reply() -> str:
    return "Project-Intake pausiert. Ich schreibe nichts in das Projekt."


def format_new_chat_reply(*, created: bool) -> str:
    return "Neuer Chat gestartet." if created else "Neuer Chat konnte nicht gestartet werden."


def format_telegram_attachment_export_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "blocked")
    target = str(result.get("target_format") or "unknown")
    tool = str(result.get("required_tool") or "")
    if status == "sent":
        return f"Export fertig: Ich habe dir die {target.upper()}-Datei geschickt."
    if status == "exported":
        return (
            f"Export fertig: {target.upper()} wurde lokal erzeugt.\n"
            "Die Datei ist bereit, aber der Telegram-Dokumentversand ist gerade nicht aktiv."
        )
    if status == "ready":
        return (
            f"Export erkannt: Ziel {target}.\n"
            f"Aktion: {result.get('action') or 'convert'}.\n"
            f"Konverter: {tool or 'builtin'}.\n"
            "Die Datei kann jetzt lokal erzeugt werden."
        )
    if status == "planned":
        return (
            f"Export erkannt: Ziel {target}.\n"
            f"Aktion: {result.get('action') or 'convert'}.\n"
            f"Benoetigtes lokales Tool: {tool or 'noch offen'}.\n"
            "Die echte Datei-Ausgabe ist noch nicht aktiviert; der sichere Export-Plan ist vorgemerkt."
        )
    return f"Export erkannt, aber blockiert: {result.get('reason') or 'policy_gate'}."


def format_telegram_attachment_inbox_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "failed")
    inbox_status = str(result.get("universal_inbox_status") or "")
    memory_status = normalize_memory_write_intent_status(
        result.get("memory_write_intent_status") or "",
        fallback="unknown",
    )
    maintenance_action = str(result.get("maintenance_action") or "").strip()
    ocr_note = telegram_attachment_ocr_note(result)
    processable = int(result.get("processable_count") or 0)
    nextcloud_note = _telegram_attachment_nextcloud_note(result)
    if _telegram_attachment_fully_done(result):
        return "✅ Datei abgelegt."
    if status == "processed" and inbox_status == "go" and memory_status:
        lines = [
            f"Anhang verarbeitet. Items: {processable}. Keine Inbox-Review noetig.",
            f"Memory/Raptor-Intent: {memory_status}.",
        ]
        if maintenance_action:
            lines.append(f"Maintenance: {maintenance_action}.")
        if memory_status == "ready":
            auto_status = str(result.get("memory_auto_write_status") or "").strip()
            if auto_status == "written":
                lines.append("Redigierte Abstraktion automatisch ins Memory/RaptorGraph geschrieben.")
            elif auto_status:
                reason = str(result.get("memory_auto_write_reason") or auto_status).strip()
                lines.append(f"Automatischer Memory-Write blockiert: {reason}.")
            else:
                lines.append("Redigierte Abstraktion wird automatisch uebernommen.")
        if nextcloud_note:
            lines.append(nextcloud_note)
        if ocr_note:
            lines.append(ocr_note)
        return "\n".join(lines)
    if status == "processed" and inbox_status == "go":
        lines = [f"Anhang verarbeitet. Items: {processable}. Keine Review noetig."]
        if nextcloud_note:
            lines.append(nextcloud_note)
        if ocr_note:
            lines.append(ocr_note)
        return "\n".join(lines)
    if status == "processed":
        lines = [
            "Anhang empfangen und geprüft. Review nötig.",
            f"Universal-Inbox-Status: {inbox_status or 'partial'}",
            f"Items: {processable}",
        ]
        if ocr_note:
            lines.append(ocr_note)
        lines.append("Zum Bestätigen antworte mit /review ok.")
        return "\n".join(lines)
    if status == "blocked":
        return f"Anhang empfangen, aber blockiert: {result.get('reason') or 'policy_gate'}."
    return f"Anhang empfangen, aber Verarbeitung fehlgeschlagen: {result.get('reason') or 'unknown'}."


def _telegram_attachment_fully_done(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "").strip()
    inbox_status = str(result.get("universal_inbox_status") or "").strip()
    memory_status = normalize_memory_write_intent_status(
        result.get("memory_write_intent_status") or "",
        fallback="unknown",
    )
    nextcloud_status = str(result.get("nextcloud_transfer_status") or "").strip()
    return (
        status == "processed"
        and inbox_status == "go"
        and memory_status not in {"review", "needs_review", "blocked"}
        and nextcloud_status == "completed"
        and bool(result.get("nextcloud_verified"))
    )


def _telegram_attachment_nextcloud_note(result: dict[str, Any]) -> str:
    status = str(result.get("nextcloud_transfer_status") or "").strip()
    if not status:
        return ""
    if status == "completed":
        return "Nextcloud-Ablage: kopiert und verifiziert."
    if status == "copied_unverified":
        return "Nextcloud-Ablage: kopiert, Verifikation offen."
    if status == "dry_run_ready":
        return "Nextcloud-Ablage: vorbereitet, Live-Copy wartet auf Operator-Go."
    reason = str(result.get("nextcloud_transfer_reason") or status).strip()
    return f"Nextcloud-Ablage blockiert: {reason}."


def format_nextcloud_transfer_blocked_reply(transfer: dict[str, Any]) -> str:
    reason = str(transfer.get("reason") or transfer.get("status") or "unknown")
    if reason == "nextcloud_server_config_missing":
        return (
            "Review bestaetigt. Nextcloud-Ablage ist blockiert: Die serverseitige "
            "Nextcloud-Konfiguration ist nicht verfuegbar. Bitte keine Zugangsdaten "
            "in Telegram senden; Nextcloud-Zugangsdaten werden nur serverseitig hinterlegt."
        )
    return f"Review bestaetigt. Nextcloud-Ablage ist noch blockiert: {reason}."


def format_universal_inbox_review_missing_reply() -> str:
    return "Keine offene Universal-Inbox-Review gefunden."


def format_universal_inbox_transfer_confirm_reply(transfer: dict[str, Any]) -> str:
    status = str(transfer.get("status") or "")
    if status == "completed":
        return "Review bestaetigt. Nextcloud-Ablage wurde kopiert und verifiziert."
    if status == "copied_unverified":
        return "Review bestaetigt. Nextcloud-Ablage wurde kopiert, braucht aber Verifikation."
    if status == "dry_run_ready":
        return (
            "Review bestaetigt. Nextcloud-Ablage ist vorbereitet, aber noch Dry-run. "
            "Live-Copy wartet auf Operator-Go."
        )
    return format_nextcloud_transfer_blocked_reply(transfer)


def format_universal_inbox_memory_review_missing_reply() -> str:
    return "Keine offene Universal-Inbox-Memory-Review gefunden."


def format_universal_inbox_memory_write_reply(execution: dict[str, Any]) -> str:
    if str(execution.get("status") or "") == "written":
        return "Memory-Review bestaetigt. Die redaktierte Abstraktion wurde ins Langzeitgedaechtnis geschrieben."
    reason = str(execution.get("reason") or execution.get("status") or "unknown")
    return f"Memory-Review bestaetigt, aber der Memory-Write wurde blockiert: {reason}."


def telegram_attachment_ocr_note(result: dict[str, Any]) -> str:
    warnings = tuple(str(value) for value in (result.get("extraction_warning_codes") or ()))
    warning_set = set(warnings)
    if "pdf_ocr_required" in warning_set or "image_ocr_required" in warning_set:
        return "OCR: noetig, aber lokaler OCR-Adapter ist noch nicht aktiv."
    if "pdf_ocr_blocked_by_policy" in warning_set:
        return "OCR: durch Datenschutz-/Policy-Gate blockiert."
    if "pdf_ocr_budget_exceeded" in warning_set:
        return "OCR: Budget erreicht; bitte mit hoeherem OCR-Budget erneut starten."
    if "image_ocr_unavailable" in warning_set:
        return "OCR: lokaler OCR-Adapter ist nicht verfuegbar."
    if "pdf_ocr_failed" in warning_set or "image_ocr_failed" in warning_set:
        return "OCR: lokaler OCR-Lauf ist fehlgeschlagen."
    if "image_ocr_empty" in warning_set:
        return "OCR: Bild geprueft, aber kein Text erkannt."
    return ""


def format_calendar_readiness_for_telegram(readiness: dict[str, Any]) -> str:
    return (
        "Kalender-Status: bereit. "
        f"{int(readiness.get('calendars') or 0)} Kalender, "
        f"{int(readiness.get('events') or 0)} Termine, "
        f"{int(readiness.get('due_notes') or 0)} Erinnerungen, "
        f"{int(readiness.get('active_telegram_tasks') or 0)} aktive Telegram-Tasks. "
        f"CalDAV Writebacks offen: {int(readiness.get('pending_caldav_writebacks') or 0)}."
    )


def format_agenda_for_telegram(packet: dict[str, Any], *, reminders_only: bool = False) -> str:
    counts = packet.get("counts") if isinstance(packet.get("counts"), dict) else {}
    if reminders_only:
        notes = packet.get("due_notes") if isinstance(packet.get("due_notes"), list) else []
        tasks = packet.get("scheduled_tasks") if isinstance(packet.get("scheduled_tasks"), list) else []
        lines = [
            f"Erinnerungen: {int(counts.get('due_notes') or 0)} due notes, {int(counts.get('scheduled_tasks') or 0)} geplante Tasks."
        ]
        for item in notes[:5]:
            lines.append(f"- {item.get('title') or 'Reminder'}: {item.get('due_date') or ''}")
        for item in tasks[:5]:
            lines.append(f"- {item.get('name') or 'Task'}: {item.get('next_run') or ''}")
        return "\n".join(lines)

    lines = [
        f"Agenda: {int(counts.get('events') or 0)} Termine, {int(counts.get('due_notes') or 0)} Erinnerungen, {int(counts.get('scheduled_tasks') or 0)} Tasks."
    ]
    for item in (packet.get("events") if isinstance(packet.get("events"), list) else [])[:5]:
        lines.append(f"- {item.get('summary') or 'Termin'}: {item.get('dtstart') or ''}")
    for item in (packet.get("due_notes") if isinstance(packet.get("due_notes"), list) else [])[:5]:
        lines.append(f"- {item.get('title') or 'Reminder'}: {item.get('due_date') or ''}")
    return "\n".join(lines)


def format_calendar_write_for_telegram(result: dict[str, Any], *, noun: str) -> str:
    status = str(result.get("status") or "error")
    if status == "clarification_required":
        return f"{noun}: Ich brauche noch genauere Angaben. {result.get('error') or ''}".strip()
    if status == "not_found":
        return f"{noun}: Ziel nicht gefunden. Nutze /reminders fuer die aktuelle Liste."
    if status in {"created", "updated", "duplicate"}:
        verb = {"created": "erstellt", "updated": "aktualisiert", "duplicate": "existiert bereits"}[status]
        ident = str(result.get("note_id") or result.get("task_id") or "")[:8]
        suffix = f" ID {ident}." if ident else "."
        return f"{noun} {verb}.{suffix}"
    return f"{noun}: blockiert ({result.get('error') or status})."


def format_calendar_unknown_command_reply() -> str:
    return "Kalender-Kommando nicht erkannt. Nutze /calendar, /agenda, /reminders oder /todo 09:00 mo-fr."


def format_calendar_command_error_reply(error_class: str) -> str:
    return f"Kalender-Kommando blockiert: {error_class}."


def format_dsgvo_reply_text(
    command: str,
    result: dict[str, Any] | None = None,
    *,
    active: bool = False,
) -> str:
    mode_active = bool((result or {}).get("after") if result is not None else active)
    if command == "dsgvo_help":
        return "Nutze /dsgvo zum Umschalten, oder /dsgvo status fuer den aktuellen Zustand."
    if command == "dsgvo_enable":
        return (
            "DSGVO-Modus ist jetzt aktiv. Telegram laeuft local-only; "
            "externe Web-, Provider- und Tool-I/O ist gesperrt."
        )
    if command == "dsgvo_disable" and (result or {}).get("forced_active"):
        return (
            "DSGVO-Modus bleibt aktiv, weil ein Server- oder Kompatibilitaets-Gate "
            "ihn erzwingt."
        )
    if command == "dsgvo_disable":
        return "DSGVO-Modus ist jetzt aus. Normale Provider- und Tool-Regeln gelten wieder."
    if command == "dsgvo_toggle":
        if (result or {}).get("forced_active"):
            return (
                "DSGVO-Modus bleibt aktiv, weil ein Server- oder Kompatibilitaets-Gate "
                "ihn erzwingt."
            )
        return (
            "DSGVO-Modus ist jetzt aktiv. Telegram laeuft local-only; "
            "externe Web-, Provider- und Tool-I/O ist gesperrt."
        ) if mode_active else "DSGVO-Modus ist jetzt aus. Normale Provider- und Tool-Regeln gelten wieder."
    return (
        "DSGVO-Modus ist aktiv. Telegram nutzt local-only Verarbeitung."
        if mode_active
        else "DSGVO-Modus ist aus."
    )


def format_universal_inbox_review_status(review: dict[str, Any]) -> str:
    status = str(review.get("universal_inbox_status") or review.get("status") or "unknown")
    processable = int(review.get("processable_count") or 0)
    if status == "go":
        return f"Universal Inbox: verarbeitet. Items: {processable}. Keine Review nötig."
    return (
        "Universal Inbox: Review nötig.\n"
        f"Status: {status}\n"
        f"Items: {processable}\n"
        "Zum Bestätigen antworte mit /review ok."
    )


def format_universal_inbox_memory_review_status(review: dict[str, Any]) -> str:
    status = normalize_memory_write_intent_status(
        review.get("memory_write_intent_status") or "",
        fallback="unknown",
    )
    inbox_status = str(review.get("universal_inbox_status") or "unknown")
    if status == "ready":
        return (
            "Universal Inbox Memory: bereit und automatisch uebernommen.\n"
            f"Inbox-Status: {inbox_status}\n"
            "Es wird nur eine redaktierte Abstraktion geschrieben, kein Rohinhalt."
        )
    return (
        "Universal Inbox Memory: Review nötig.\n"
        f"Memory-Status: {status}\n"
        f"Inbox-Status: {inbox_status}\n"
        "Zum Bestätigen antworte mit /review memory ok."
    )
