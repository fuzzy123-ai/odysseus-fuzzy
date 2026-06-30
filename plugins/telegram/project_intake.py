"""Telegram project-intake helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plugins.telegram.stores import TelegramInboxStore, TelegramSessionBridgeStore


_PROJECT_REGISTRY_FILE = "server_project_registry.json"
_PROJECT_INTAKE_HINT_RE = re.compile(
    r"(#project:|#projekt:|project:|projekt:|\broadmap\b|\bmvp\b|\btodo\b|\baufgabe\b|\bplan\b|\bslice\b)",
    re.IGNORECASE,
)


def _looks_like_project_intake(text: str) -> bool:
    prompt = str(text or "").strip()
    if not prompt or prompt.startswith("/"):
        return False
    if not _PROJECT_INTAKE_HINT_RE.search(prompt):
        return False
    if re.search(r"(mach|mache|wandle|konvertier|export|schick).{0,40}\b(pdf|png|jpg|docx|mp3|wav)\b", prompt, re.IGNORECASE):
        return False
    return bool(re.search(r"(#project:|#projekt:|project:|projekt:|\broadmap\b|\bmvp\b|\btodo\b|\baufgabe\b|\bslice\b)", prompt, re.IGNORECASE))


def build_telegram_project_intake_preview(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    sessions: TelegramSessionBridgeStore,
    chat_id: str,
    text: str,
    source_message_id: int | None = None,
    project_registry_path: str | Path | None = None,
) -> dict[str, Any] | None:
    if not _looks_like_project_intake(text):
        return None
    try:
        from src.project_intake import ProjectIntakeError, build_project_intake_preview
        from src.server_project_registry import ServerProjectRegistry
    except Exception as exc:
        return {"status": "blocked", "reason": f"project_intake_unavailable:{str(exc)[:80]}", "raw_content_visible": False}

    registry_path = Path(project_registry_path) if project_registry_path is not None else Path(data_dir) / _PROJECT_REGISTRY_FILE
    try:
        registry = ServerProjectRegistry.load_json(registry_path) if registry_path.exists() else ServerProjectRegistry()
    except Exception:
        return {"status": "blocked", "reason": "project_registry_unreadable", "raw_content_visible": False}

    session = sessions.get(chat_id) or {}
    try:
        proposal = build_project_intake_preview(
            registry=registry,
            text=text,
            source_channel="telegram",
            chat_session_id=str(session.get("session_id") or ""),
        ).to_dict()
    except ProjectIntakeError as exc:
        return {"status": "blocked", "reason": str(exc)[:120], "raw_content_visible": False}

    candidate = proposal.get("candidate_project") if isinstance(proposal.get("candidate_project"), dict) else {}
    tasks = tuple(proposal.get("tasks") or ())
    decisions = tuple(proposal.get("decisions") or ())
    risks = tuple(proposal.get("risks") or ())
    roadmap_updates = tuple(proposal.get("roadmap_updates") or ())
    result = {
        "status": str(proposal.get("status") or "blocked"),
        "reason": str(proposal.get("reason") or ""),
        "project_slug": str(candidate.get("project_slug") or ""),
        "project_title": str(candidate.get("project_title") or ""),
        "confidence": float(candidate.get("confidence") or 0),
        "task_count": len(tasks),
        "decision_count": len(decisions),
        "risk_count": len(risks),
        "roadmap_update_count": len(roadmap_updates),
        "tasks": tasks,
        "decisions": decisions,
        "risks": risks,
        "roadmap_updates": roadmap_updates,
        "recommended_next_action": str(proposal.get("recommended_next_action") or "review_project_intake"),
        "requires_review": bool(proposal.get("requires_review", True)),
        "proposal": proposal,
        "source_message_id": source_message_id,
        "raw_content_visible": False,
        "raw_content_persisted": False,
        "host_paths_visible": False,
    }
    if store is not None:
        store.append_event(
            kind="project_intake_review",
            status=str(result.get("status") or "blocked"),
            chat_id=chat_id,
            source_message_id=source_message_id,
            project_slug=str(result.get("project_slug") or ""),
            confidence=float(result.get("confidence") or 0),
            task_count=int(result.get("task_count") or 0),
            decision_count=int(result.get("decision_count") or 0),
            risk_count=int(result.get("risk_count") or 0),
            roadmap_update_count=int(result.get("roadmap_update_count") or 0),
            raw_content_visible=False,
            raw_content_persisted=False,
            raw_identifiers_visible=False,
            host_paths_visible=False,
            project_intake_apply_performed=False,
            project_intake_proposal=proposal,
        )
    return result


def format_telegram_project_intake_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "blocked")
    if status == "blocked":
        reason = str(result.get("reason") or "project_choice_required")
        if reason == "project_choice_required":
            return "Project-Intake erkannt, aber ich konnte kein Zielprojekt sicher bestimmen. Bitte sende z.B. #project:projekt-slug dazu."
        return f"Project-Intake blockiert: {reason}."
    project = str(result.get("project_slug") or "unknown")
    confidence = float(result.get("confidence") or 0)
    lines = [
        f"Project-Intake erkannt fuer {project} ({round(confidence * 100)}%).",
        f"Tasks: {int(result.get('task_count') or 0)}, Decisions: {int(result.get('decision_count') or 0)}, Risiken: {int(result.get('risk_count') or 0)}, Roadmap-Updates: {int(result.get('roadmap_update_count') or 0)}.",
    ]
    task_titles = []
    for task in tuple(result.get("tasks") or ())[:3]:
        if isinstance(task, dict) and task.get("title"):
            task_titles.append(str(task.get("title")))
    if task_titles:
        lines.append("Vorschlag:")
        lines.extend(f"- {title}" for title in task_titles)
    lines.append("Antwort: /project ok uebernimmt ins Intake-Ledger, /project hold pausiert. Projektdateien bleiben noch gesperrt.")
    return "\n".join(lines)


def _apply_telegram_project_intake_review(
    *,
    data_dir: str | Path,
    review: dict[str, Any],
    project_registry_path: str | Path | None = None,
) -> dict[str, Any]:
    proposal = review.get("project_intake_proposal")
    if not isinstance(proposal, dict):
        return {"status": "blocked", "applied": False, "blockers": ("proposal_missing_from_review",)}
    project_slug = str(review.get("project_slug") or "")
    if not project_slug:
        return {"status": "blocked", "applied": False, "blockers": ("project_slug_missing",)}
    try:
        from src.project_intake import apply_project_intake_proposal
        from src.server_project_intake_state import merge_project_intake_ledger
        from src.server_project_registry import ServerProjectRegistry
    except Exception as exc:
        return {"status": "blocked", "applied": False, "blockers": (f"project_intake_unavailable:{str(exc)[:80]}",)}

    registry_path = Path(project_registry_path) if project_registry_path is not None else Path(data_dir) / _PROJECT_REGISTRY_FILE
    try:
        registry = ServerProjectRegistry.load_json(registry_path) if registry_path.exists() else ServerProjectRegistry()
        record = registry.get(project_slug)
        ledger_path = Path(data_dir) / "server_projects" / project_slug / ".odysseus" / "project_intake_ledger.json"
        state_path = Path(data_dir) / "server_projects" / project_slug / ".odysseus" / "project_state.json"
        report = apply_project_intake_proposal(
            registry=registry,
            project_slug=project_slug,
            proposal=proposal,
            ledger_path=ledger_path,
            applied_by="telegram",
            review_confirmed=True,
        )
        payload = report.to_dict()
        if report.applied:
            merge_report = merge_project_intake_ledger(
                record=record,
                ledger_path=ledger_path,
                state_path=state_path,
                merged_at=_utc_now_iso(),
                source_event_id=report.event_id,
            )
            payload["intake_merge"] = merge_report.to_dict()
    except Exception as exc:
        return {"status": "blocked", "applied": False, "blockers": (str(exc)[:120],)}
    return payload


def _format_project_intake_review_status(review: dict[str, Any] | None) -> str:
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


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
