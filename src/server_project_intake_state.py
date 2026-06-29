"""Project-local state merge for reviewed project intake ledger events."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from core.atomic_io import atomic_write_json
from src.project_intake import PROJECT_INTAKE_LEDGER_SCHEMA, ProjectIntakeError
from src.server_project_registry import ServerProjectRecord


PROJECT_INTAKE_STATE_SCHEMA = "odysseus.project_intake.state.v1"
PROJECT_INTAKE_MERGE_SCHEMA = "odysseus.project_intake.merge.v1"

_MAX_TEXT = 260
_SECRET_RE = re.compile(
    r"(api[_-]?key\s*[:=]|token\s*[:=]|secret\s*[:=]|password\s*[:=]|bearer\s+[a-z0-9._-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(?:home|root|var|etc|opt)/)")


class ServerProjectIntakeStateError(ValueError):
    """Raised when a project intake state merge cannot be performed safely."""


@dataclass(frozen=True, slots=True)
class ProjectIntakeStateMergeReport:
    status: str
    project_slug: str
    merged: bool
    state_path: str
    ledger_path: str
    processed_event_count: int
    added_task_count: int
    existing_task_count: int
    added_decision_count: int
    added_risk_count: int
    added_roadmap_update_count: int
    blockers: tuple[str, ...] = ()
    schema: str = PROJECT_INTAKE_MERGE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "project_slug": self.project_slug,
            "merged": self.merged,
            "state_path": self.state_path,
            "ledger_path": self.ledger_path,
            "processed_event_count": self.processed_event_count,
            "added_task_count": self.added_task_count,
            "existing_task_count": self.existing_task_count,
            "added_decision_count": self.added_decision_count,
            "added_risk_count": self.added_risk_count,
            "added_roadmap_update_count": self.added_roadmap_update_count,
            "blockers": self.blockers,
            "raw_content_visible": False,
            "raw_content_persisted": False,
        }


def merge_project_intake_ledger(
    *,
    record: ServerProjectRecord,
    ledger_path: str | Path,
    state_path: str | Path,
    merged_at: str,
    source_event_id: str | None = None,
) -> ProjectIntakeStateMergeReport:
    """Merge applied intake ledger events into project-local state."""

    safe_record = _validate_record(record)
    timestamp = _safe_text(merged_at, field_name="merged_at", max_len=40)
    ledger_file = Path(ledger_path)
    state_file = Path(state_path)
    if ledger_file.suffix.lower() != ".json" or state_file.suffix.lower() != ".json":
        raise ServerProjectIntakeStateError("ledger_path and state_path must be json files")
    ledger = _load_ledger(ledger_file, project_slug=safe_record.project_slug)
    state = _load_state(state_file, safe_record)
    event_filter = _safe_event_id(source_event_id) if source_event_id else ""
    events = tuple(_iter_applied_events(ledger, event_filter=event_filter))
    if event_filter and not events:
        return _blocked_report(
            record=safe_record,
            ledger_file=ledger_file,
            state_file=state_file,
            blocker="source_event_id_not_found",
        )
    if not events:
        return _blocked_report(
            record=safe_record,
            ledger_file=ledger_file,
            state_file=state_file,
            blocker="no_applied_intake_events",
        )

    task_index = {_task_key(task): index for index, task in enumerate(state["tasks"])}
    decision_index = set(_text_key(item["text"]) for item in state["decisions"])
    risk_index = set(_text_key(item["text"]) for item in state["risks"])
    roadmap_index = set(_text_key(item["text"]) for item in state["roadmap_updates"])
    processed_ids = set(state["processed_event_ids"])

    added_tasks = 0
    existing_tasks = 0
    added_decisions = 0
    added_risks = 0
    added_roadmaps = 0
    processed_now = 0

    for event in events:
        event_id = _safe_event_id(event.get("event_id"))
        if event_id in processed_ids:
            continue
        processed_now += 1
        for task in _list(event.get("tasks"), field_name="tasks"):
            normalized_task = _normalize_task(task, event_id=event_id, merged_at=timestamp)
            key = _task_key(normalized_task)
            if key in task_index:
                existing = state["tasks"][task_index[key]]
                sources = list(existing.get("source_event_ids") or [])
                if event_id not in sources:
                    sources.append(event_id)
                existing["source_event_ids"] = sources
                existing["updated_at"] = timestamp
                existing_tasks += 1
                continue
            state["tasks"].append(normalized_task)
            task_index[key] = len(state["tasks"]) - 1
            added_tasks += 1
        for text in _safe_string_items(event.get("decisions"), field_name="decisions"):
            key = _text_key(text)
            if key not in decision_index:
                state["decisions"].append(_note_item(text, event_id=event_id, merged_at=timestamp))
                decision_index.add(key)
                added_decisions += 1
        for text in _safe_string_items(event.get("risks"), field_name="risks"):
            key = _text_key(text)
            if key not in risk_index:
                state["risks"].append(_note_item(text, event_id=event_id, merged_at=timestamp))
                risk_index.add(key)
                added_risks += 1
        for text in _safe_string_items(event.get("roadmap_updates"), field_name="roadmap_updates"):
            key = _text_key(text)
            if key not in roadmap_index:
                state["roadmap_updates"].append(_note_item(text, event_id=event_id, merged_at=timestamp))
                roadmap_index.add(key)
                added_roadmaps += 1
        processed_ids.add(event_id)

    state["processed_event_ids"] = sorted(processed_ids)
    state["updated_at"] = timestamp
    atomic_write_json(str(state_file), state, indent=2)
    return ProjectIntakeStateMergeReport(
        status="merged",
        project_slug=safe_record.project_slug,
        merged=True,
        state_path=state_file.name,
        ledger_path=ledger_file.name,
        processed_event_count=processed_now,
        added_task_count=added_tasks,
        existing_task_count=existing_tasks,
        added_decision_count=added_decisions,
        added_risk_count=added_risks,
        added_roadmap_update_count=added_roadmaps,
    )


def load_project_intake_state(*, record: ServerProjectRecord, state_path: str | Path) -> dict[str, Any]:
    """Return a Project Manager-ready state snapshot, or an empty state."""

    return _load_state(Path(state_path), _validate_record(record))


def _blocked_report(
    *,
    record: ServerProjectRecord,
    ledger_file: Path,
    state_file: Path,
    blocker: str,
) -> ProjectIntakeStateMergeReport:
    return ProjectIntakeStateMergeReport(
        status="blocked",
        project_slug=record.project_slug,
        merged=False,
        state_path=state_file.name,
        ledger_path=ledger_file.name,
        processed_event_count=0,
        added_task_count=0,
        existing_task_count=0,
        added_decision_count=0,
        added_risk_count=0,
        added_roadmap_update_count=0,
        blockers=(blocker,),
    )


def _validate_record(record: ServerProjectRecord) -> ServerProjectRecord:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectIntakeStateError("record must be a ServerProjectRecord")
    return record


def _load_ledger(path: Path, *, project_slug: str) -> dict[str, Any]:
    if not path.exists():
        raise ServerProjectIntakeStateError("project intake ledger does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ServerProjectIntakeStateError("project intake ledger is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != PROJECT_INTAKE_LEDGER_SCHEMA:
        raise ServerProjectIntakeStateError("project intake ledger schema is unsupported")
    if str(payload.get("project_slug") or "") != project_slug:
        raise ServerProjectIntakeStateError("project intake ledger project mismatch")
    if not isinstance(payload.get("events"), list):
        raise ServerProjectIntakeStateError("project intake ledger events must be a list")
    return payload


def _load_state(path: Path, record: ServerProjectRecord) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": PROJECT_INTAKE_STATE_SCHEMA,
            "project_slug": record.project_slug,
            "project_title": record.project_spec.project_title,
            "tasks": [],
            "decisions": [],
            "risks": [],
            "roadmap_updates": [],
            "processed_event_ids": [],
            "raw_content_visible": False,
            "raw_content_persisted": False,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ServerProjectIntakeStateError("project intake state is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != PROJECT_INTAKE_STATE_SCHEMA:
        raise ServerProjectIntakeStateError("project intake state schema is unsupported")
    if str(payload.get("project_slug") or "") != record.project_slug:
        raise ServerProjectIntakeStateError("project intake state project mismatch")
    for key in ("tasks", "decisions", "risks", "roadmap_updates", "processed_event_ids"):
        if not isinstance(payload.get(key), list):
            raise ServerProjectIntakeStateError(f"project intake state {key} must be a list")
    for task in payload["tasks"]:
        if not isinstance(task, Mapping):
            raise ServerProjectIntakeStateError("project intake state task must be a mapping")
        _safe_text(task.get("title") or "", field_name="task.title", max_len=_MAX_TEXT)
        _safe_token(task.get("kind") or "task", field_name="task.kind")
        _safe_token(task.get("priority") or "normal", field_name="task.priority")
        for event_id in _list(task.get("source_event_ids"), field_name="task.source_event_ids"):
            _safe_event_id(event_id)
    for key in ("decisions", "risks", "roadmap_updates"):
        for item in payload[key]:
            if not isinstance(item, Mapping):
                raise ServerProjectIntakeStateError(f"project intake state {key} item must be a mapping")
            _safe_text(item.get("text") or "", field_name=f"{key}.text", max_len=_MAX_TEXT)
            for event_id in _list(item.get("source_event_ids"), field_name=f"{key}.source_event_ids"):
                _safe_event_id(event_id)
    for event_id in payload["processed_event_ids"]:
        _safe_event_id(event_id)
    payload["raw_content_visible"] = False
    payload["raw_content_persisted"] = False
    return payload


def _iter_applied_events(ledger: Mapping[str, Any], *, event_filter: str = "") -> Iterable[Mapping[str, Any]]:
    for event in ledger.get("events") or ():
        if not isinstance(event, Mapping):
            continue
        event_id = _safe_event_id(event.get("event_id"))
        if event_filter and event_id != event_filter:
            continue
        if str(event.get("status") or "") != "applied":
            continue
        yield event


def _normalize_task(value: Any, *, event_id: str, merged_at: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ServerProjectIntakeStateError("task must be a mapping")
    title = _safe_text(value.get("title") or "", field_name="task.title", max_len=_MAX_TEXT)
    kind = _safe_token(value.get("kind") or "task", field_name="task.kind")
    priority = _safe_token(value.get("priority") or "normal", field_name="task.priority")
    return {
        "id": f"task-{_text_key(kind + ':' + title)[:16]}",
        "title": title,
        "kind": kind,
        "priority": priority,
        "status": "planned",
        "source": "telegram_project_intake",
        "source_event_ids": [event_id],
        "created_at": merged_at,
        "updated_at": merged_at,
    }


def _note_item(text: str, *, event_id: str, merged_at: str) -> dict[str, Any]:
    return {
        "id": f"note-{_text_key(text)[:16]}",
        "text": text,
        "source": "telegram_project_intake",
        "source_event_ids": [event_id],
        "created_at": merged_at,
        "updated_at": merged_at,
    }


def _safe_string_items(value: Any, *, field_name: str) -> tuple[str, ...]:
    return tuple(_safe_text(item, field_name=field_name, max_len=_MAX_TEXT) for item in _list(value, field_name=field_name))


def _list(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    raise ServerProjectIntakeStateError(f"{field_name} must be a list")


def _task_key(task: Mapping[str, Any]) -> str:
    return _text_key(f"{task.get('kind') or 'task'}:{task.get('title') or ''}")


def _text_key(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text[:120] or "item"


def _safe_event_id(value: Any) -> str:
    event_id = re.sub(r"[^a-z0-9._-]+", "", str(value or "").strip().lower())
    if not event_id or len(event_id) > 80:
        raise ServerProjectIntakeStateError("event_id is invalid")
    return event_id


def _safe_token(value: Any, *, field_name: str) -> str:
    token = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    if not token:
        raise ServerProjectIntakeStateError(f"{field_name} must not be empty")
    if len(token) > 64:
        raise ServerProjectIntakeStateError(f"{field_name} exceeds max length")
    return token


def _safe_text(value: Any, *, field_name: str, max_len: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ServerProjectIntakeStateError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectIntakeStateError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectIntakeStateError(f"{field_name} appears to contain secret material")
    if _HOST_PATH_RE.search(text):
        raise ServerProjectIntakeStateError(f"{field_name} must not contain host-local absolute paths")
    return text
