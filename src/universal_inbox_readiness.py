"""Redacted readiness snapshot for Universal Inbox Telegram control."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from src.universal_inbox_discovery import UniversalInboxDiscoveryError
from src.universal_inbox_worker import UniversalInboxWorkerConfig, run_universal_inbox_dry_run


DEFAULT_UNIVERSAL_INBOX_PATH = "/app/universal-inbox"


def resolve_universal_inbox_path() -> str:
    return (os.getenv("UNIVERSAL_INBOX_PATH") or DEFAULT_UNIVERSAL_INBOX_PATH).strip()


def build_universal_inbox_readiness(
    inbox_path: str | Path | None = None,
    *,
    max_items_probe: int | None = None,
) -> dict[str, Any]:
    """Return a content-free Universal Inbox readiness snapshot.

    The payload intentionally avoids absolute paths, file names, hashes, or
    extracted text so it can be sent back through Telegram.
    """

    configured_path = str(inbox_path or resolve_universal_inbox_path()).strip()
    if not configured_path:
        return _blocked(
            "path_missing",
            "UNIVERSAL_INBOX_PATH ist nicht gesetzt.",
            path_configured=False,
        )

    path = Path(configured_path)
    if not path.exists():
        return _blocked(
            "path_not_found",
            "Der Universal-Inbox-Pfad ist fuer Odysseus nicht erreichbar.",
            path_configured=True,
        )
    if not path.is_dir():
        return _blocked(
            "path_not_directory",
            "Der Universal-Inbox-Pfad ist kein Ordner.",
            path_configured=True,
        )

    try:
        report = run_universal_inbox_dry_run(
            path,
            config=UniversalInboxWorkerConfig(max_file_size_bytes=max_items_probe),
        )
    except UniversalInboxDiscoveryError as exc:
        return _blocked(
            "discovery_blocked",
            str(exc),
            path_configured=True,
        )
    except Exception:
        return _blocked(
            "dry_run_failed",
            "Universal-Inbox-Dry-Run ist fehlgeschlagen.",
            path_configured=True,
        )

    payload = report.to_dict()
    discovery = payload.get("discovery") or {}
    memory_write_intent_status = _memory_write_intent_status(payload)
    return {
        "feature": "universal_inbox",
        "status": str(payload.get("status") or "blocked"),
        "ready": str(payload.get("status") or "") in {"go", "partial"},
        "path_configured": True,
        "path_visible": False,
        "host_paths_visible": False,
        "raw_content_visible": False,
        "writes_performed": bool(payload.get("writes_performed")),
        "dry_run": bool(payload.get("dry_run")),
        "discovered_count": int(discovery.get("discovered_count") or 0),
        "processable_count": int(payload.get("item_count") or 0),
        "warning_count": len(tuple(discovery.get("warnings") or ())),
        "review_reason_count": len(tuple(payload.get("review_reasons") or ())),
        "no_go_reason_count": len(tuple(payload.get("no_go_reasons") or ())),
        "memory_write_intent_status": memory_write_intent_status,
        "reason": _status_reason(str(payload.get("status") or ""), int(payload.get("item_count") or 0)),
    }


def format_universal_inbox_readiness_for_telegram(snapshot: dict[str, Any]) -> str:
    status = str(snapshot.get("status") or "blocked")
    ready = bool(snapshot.get("ready"))
    lines = [
        f"Universal Inbox: {status}",
        f"Bereit fuer Read-only-Dry-Run: {'ja' if ready else 'nein'}",
        f"Gefundene Dateien: {int(snapshot.get('discovered_count') or 0)}",
        f"Verarbeitbare Dry-Run-Items: {int(snapshot.get('processable_count') or 0)}",
    ]
    reason = str(snapshot.get("reason") or "").strip()
    if reason:
        lines.append(f"Warum: {reason}")
    if int(snapshot.get("no_go_reason_count") or 0):
        lines.append(f"No-Go-Gruende: {int(snapshot.get('no_go_reason_count') or 0)}")
    if int(snapshot.get("review_reason_count") or 0):
        lines.append(f"Review-Gruende: {int(snapshot.get('review_reason_count') or 0)}")
    lines.append("Private Inhalte, Dateinamen und Host-Pfade wurden nicht ausgegeben.")
    return "\n".join(lines)


def _blocked(reason_code: str, reason: str, *, path_configured: bool) -> dict[str, Any]:
    return {
        "feature": "universal_inbox",
        "status": "blocked",
        "ready": False,
        "path_configured": path_configured,
        "path_visible": False,
        "host_paths_visible": False,
        "raw_content_visible": False,
        "writes_performed": False,
        "dry_run": True,
        "discovered_count": 0,
        "processable_count": 0,
        "warning_count": 0,
        "review_reason_count": 0,
        "no_go_reason_count": 1,
        "reason_code": reason_code,
        "reason": reason,
    }


def _status_reason(status: str, item_count: int) -> str:
    if status == "go" and item_count:
        return "Read-only-Pipeline kann Dateien ohne Schreibzugriff pruefen."
    if status == "go":
        return "Inbox erreichbar; aktuell keine Dateien im Dry-Run gefunden."
    if status == "partial":
        return "Inbox erreichbar; einige Items brauchen Review."
    if status == "no_go":
        return "Inbox erreichbar; mindestens ein No-Go-Gate blockiert Verarbeitung."
    return "Universal-Inbox-Status ist blockiert."


def _memory_write_intent_status(payload: dict[str, Any]) -> str:
    statuses: list[str] = []
    for item in tuple(payload.get("items") or ()):
        if not isinstance(item, dict):
            continue
        pipeline = item.get("pipeline_report") if isinstance(item.get("pipeline_report"), dict) else {}
        intent = pipeline.get("memory_write_intent") if isinstance(pipeline.get("memory_write_intent"), dict) else {}
        status = str(intent.get("status") or "").strip()
        if status:
            statuses.append(status)
    if not statuses:
        return ""
    if "blocked" in statuses:
        return "blocked"
    if "review" in statuses:
        return "review"
    if "ready" in statuses:
        return "ready"
    return statuses[0]
