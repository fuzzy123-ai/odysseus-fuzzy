"""Project Intake preview contracts for mobile plans and roadmaps.

The intake layer turns incoming notes, roadmap snippets, or extracted file
abstracts into a reviewable project merge proposal. It does not mutate project
state, call providers, write files, or persist raw source content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping

from core.atomic_io import atomic_write_json
from src.server_project_registry import ServerProjectRecord, ServerProjectRegistry


PROJECT_INTAKE_SCHEMA = "odysseus.project_intake.proposal.v1"
PROJECT_INTAKE_TASK_SCHEMA = "odysseus.project_intake.task.v1"
PROJECT_INTAKE_LEDGER_SCHEMA = "odysseus.project_intake.ledger.v1"
PROJECT_INTAKE_APPLY_SCHEMA = "odysseus.project_intake.apply.v1"

_MAX_TEXT = 8000
_MAX_ITEM = 260
_PROJECT_HINT_RE = re.compile(
    r"(?:#project:|project:|projekt:|projekt\s+)([a-zA-Z0-9][a-zA-Z0-9._-]{1,79})",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(api[_-]?key\s*[:=]|token\s*[:=]|secret\s*[:=]|password\s*[:=]|bearer\s+[a-z0-9._-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(?:home|root|var|etc|opt)/)")
_TASK_HINT_RE = re.compile(
    r"\b(todo|task|aufgabe|roadmap|plan|slice|mvp|implement|baue|bau|erstelle|fix|fehlt|brauchen|need|add|deploy|release|test)\b",
    re.IGNORECASE,
)


class ProjectIntakeError(ValueError):
    """Raised when an intake payload cannot be safely processed."""


@dataclass(frozen=True, slots=True)
class ProjectCandidate:
    project_slug: str
    project_title: str
    confidence: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "project_title": self.project_title,
            "confidence": round(float(self.confidence), 3),
            "reasons": self.reasons,
        }


@dataclass(frozen=True, slots=True)
class ProjectIntakeTask:
    title: str
    kind: str
    priority: str
    evidence: tuple[str, ...] = ()
    schema: str = PROJECT_INTAKE_TASK_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "title": self.title,
            "kind": self.kind,
            "priority": self.priority,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ProjectIntakeProposal:
    status: str
    reason: str
    source_channel: str
    candidate_project: ProjectCandidate | None
    candidates: tuple[ProjectCandidate, ...]
    tasks: tuple[ProjectIntakeTask, ...]
    decisions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    roadmap_updates: tuple[str, ...] = ()
    recommended_next_action: str = "review_project_intake"
    requires_review: bool = True
    ai_planner_used: bool = False
    raw_content_visible: bool = False
    raw_content_persisted: bool = False
    schema: str = PROJECT_INTAKE_SCHEMA

    @property
    def ready_for_apply(self) -> bool:
        return self.status == "ready" and self.candidate_project is not None and not self.requires_review

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reason": self.reason,
            "source_channel": self.source_channel,
            "candidate_project": self.candidate_project.to_dict() if self.candidate_project else None,
            "candidates": tuple(candidate.to_dict() for candidate in self.candidates),
            "tasks": tuple(task.to_dict() for task in self.tasks),
            "decisions": self.decisions,
            "risks": self.risks,
            "roadmap_updates": self.roadmap_updates,
            "recommended_next_action": self.recommended_next_action,
            "requires_review": self.requires_review,
            "ready_for_apply": self.ready_for_apply,
            "ai_planner_used": self.ai_planner_used,
            "raw_content_visible": False,
            "raw_content_persisted": False,
        }


@dataclass(frozen=True, slots=True)
class ProjectIntakeApplyReport:
    status: str
    project_slug: str
    applied: bool
    ledger_path: str
    event_id: str
    task_count: int
    decision_count: int
    risk_count: int
    roadmap_update_count: int
    blockers: tuple[str, ...] = ()
    schema: str = PROJECT_INTAKE_APPLY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "project_slug": self.project_slug,
            "applied": self.applied,
            "ledger_path": self.ledger_path,
            "event_id": self.event_id,
            "task_count": self.task_count,
            "decision_count": self.decision_count,
            "risk_count": self.risk_count,
            "roadmap_update_count": self.roadmap_update_count,
            "blockers": self.blockers,
            "raw_content_visible": False,
            "raw_content_persisted": False,
        }


def build_project_intake_preview(
    *,
    registry: ServerProjectRegistry,
    text: str,
    source_channel: str = "telegram",
    chat_session_id: str | None = None,
    forced_project_slug: str | None = None,
    ai_merge_planner: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
) -> ProjectIntakeProposal:
    """Build a review-only project merge proposal from incoming text."""

    if not isinstance(registry, ServerProjectRegistry):
        raise ProjectIntakeError("registry must be a ServerProjectRegistry")
    source_text = _safe_text(text, field_name="text", max_len=_MAX_TEXT)
    channel = _safe_token(source_channel or "unknown", field_name="source_channel")
    candidates = _match_projects(
        registry,
        source_text,
        chat_session_id=chat_session_id,
        forced_project_slug=forced_project_slug,
    )
    candidate = candidates[0] if candidates else None
    if candidate is None:
        return ProjectIntakeProposal(
            status="blocked",
            reason="project_choice_required",
            source_channel=channel,
            candidate_project=None,
            candidates=(),
            tasks=(),
            recommended_next_action="choose_project_before_merge",
        )

    planner_payload = _planner_payload(source_text, candidate=candidate, source_channel=channel)
    ai_payload = _call_ai_planner(ai_merge_planner, planner_payload) if ai_merge_planner else None
    tasks = _tasks_from_ai(ai_payload) if ai_payload else _extract_tasks(source_text)
    decisions = _tuple_from_payload(ai_payload, "decisions") if ai_payload else _extract_prefixed_items(source_text, ("decision", "entscheidung"))
    risks = _tuple_from_payload(ai_payload, "risks") if ai_payload else _extract_prefixed_items(source_text, ("risk", "risiko", "blocker"))
    roadmap_updates = (
        _tuple_from_payload(ai_payload, "roadmap_updates")
        if ai_payload
        else _extract_prefixed_items(source_text, ("roadmap", "plan", "mvp"))
    )

    if not tasks and not decisions and not risks and not roadmap_updates:
        reason = "no_project_merge_items_detected"
        status = "blocked"
        next_action = "send_more_specific_project_plan"
    elif candidate.confidence < 0.8:
        reason = "project_match_requires_review"
        status = "review"
        next_action = "confirm_project_before_apply"
    else:
        reason = "project_intake_preview_ready"
        status = "review"
        next_action = "review_project_merge_proposal"

    return ProjectIntakeProposal(
        status=status,
        reason=reason,
        source_channel=channel,
        candidate_project=candidate,
        candidates=candidates,
        tasks=tasks,
        decisions=decisions,
        risks=risks,
        roadmap_updates=roadmap_updates,
        recommended_next_action=next_action,
        requires_review=True,
        ai_planner_used=ai_payload is not None,
    )


def apply_project_intake_proposal(
    *,
    registry: ServerProjectRegistry,
    project_slug: str,
    proposal: Mapping[str, Any],
    ledger_path: str | Path,
    applied_at: str | None = None,
    applied_by: str = "operator",
    review_confirmed: bool = False,
) -> ProjectIntakeApplyReport:
    """Append a reviewed project intake proposal to the project intake ledger.

    This is intentionally a ledger apply, not a filesystem project merge. It
    records only extracted, validated proposal items so a later smart merge
    worker can update roadmap/task state with audit context.
    """

    if not isinstance(registry, ServerProjectRegistry):
        raise ProjectIntakeError("registry must be a ServerProjectRegistry")
    slug = _normalize_project_hint(project_slug)
    if not slug:
        raise ProjectIntakeError("project_slug must not be empty")
    record = registry.get(slug)
    if not isinstance(proposal, Mapping):
        raise ProjectIntakeError("proposal must be a mapping")
    actor = _safe_token(applied_by or "operator", field_name="applied_by")
    timestamp = _safe_timestamp(applied_at)
    path = Path(ledger_path)
    if path.suffix.lower() != ".json":
        raise ProjectIntakeError("ledger_path must be a json file")

    blockers = _apply_blockers(record, proposal, review_confirmed=review_confirmed)
    event = _apply_event(
        record=record,
        proposal=proposal,
        applied_at=timestamp,
        applied_by=actor,
        blockers=blockers,
    )
    if blockers:
        return ProjectIntakeApplyReport(
            status="blocked",
            project_slug=record.project_slug,
            applied=False,
            ledger_path=path.name,
            event_id=str(event["event_id"]),
            task_count=int(event["task_count"]),
            decision_count=int(event["decision_count"]),
            risk_count=int(event["risk_count"]),
            roadmap_update_count=int(event["roadmap_update_count"]),
            blockers=blockers,
        )

    ledger = _load_intake_ledger(path)
    events = list(ledger.get("events") or [])
    if not any(item.get("event_id") == event["event_id"] for item in events if isinstance(item, dict)):
        events.append(event)
    ledger = {
        "schema": PROJECT_INTAKE_LEDGER_SCHEMA,
        "project_slug": record.project_slug,
        "events": events,
    }
    atomic_write_json(str(path), ledger, indent=2)
    return ProjectIntakeApplyReport(
        status="applied",
        project_slug=record.project_slug,
        applied=True,
        ledger_path=path.name,
        event_id=str(event["event_id"]),
        task_count=int(event["task_count"]),
        decision_count=int(event["decision_count"]),
        risk_count=int(event["risk_count"]),
        roadmap_update_count=int(event["roadmap_update_count"]),
    )


def _match_projects(
    registry: ServerProjectRegistry,
    text: str,
    *,
    chat_session_id: str | None,
    forced_project_slug: str | None,
) -> tuple[ProjectCandidate, ...]:
    scored: dict[str, ProjectCandidate] = {}
    explicit_hint = _project_hint(text)
    forced = _normalize_project_hint(forced_project_slug or "")
    for record in registry.projects.values():
        reasons: list[str] = []
        score = 0.0
        if forced and forced == record.project_slug:
            score = max(score, 1.0)
            reasons.append("forced_project")
        if explicit_hint and explicit_hint in {record.project_slug, _normalize_project_hint(record.project_spec.project_title)}:
            score = max(score, 0.98)
            reasons.append("explicit_project_hint")
        if chat_session_id and chat_session_id in record.chat_session_ids:
            score = max(score, 0.9)
            reasons.append("chat_session_bound")
        keyword_score = _keyword_project_score(text, record)
        if keyword_score:
            score = max(score, keyword_score)
            reasons.append("keyword_overlap")
        if score > 0:
            scored[record.project_slug] = ProjectCandidate(
                project_slug=record.project_slug,
                project_title=record.project_spec.project_title,
                confidence=score,
                reasons=tuple(dict.fromkeys(reasons)),
            )
    return tuple(sorted(scored.values(), key=lambda item: (-item.confidence, item.project_slug)))


def _project_hint(text: str) -> str:
    match = _PROJECT_HINT_RE.search(text or "")
    return _normalize_project_hint(match.group(1)) if match else ""


def _normalize_project_hint(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _keyword_project_score(text: str, record: ServerProjectRecord) -> float:
    text_tokens = _tokens(text)
    project_tokens = _tokens(f"{record.project_spec.project_title} {record.project_slug} {record.project_spec.repo_name}")
    if not text_tokens or not project_tokens:
        return 0.0
    overlap = text_tokens & project_tokens
    if not overlap:
        return 0.0
    ratio = len(overlap) / max(1, len(project_tokens))
    if ratio >= 0.6:
        return 0.74
    if ratio >= 0.34:
        return 0.62
    return 0.52


def _tokens(text: str) -> set[str]:
    stop = {"the", "und", "oder", "ein", "eine", "das", "der", "die", "wir", "ich", "project", "projekt"}
    return {token for token in re.findall(r"[a-z0-9]{3,}", str(text or "").lower()) if token not in stop}


def _planner_payload(text: str, *, candidate: ProjectCandidate, source_channel: str) -> dict[str, Any]:
    return {
        "schema": "odysseus.project_intake.ai_merge_planner_input.v1",
        "source_channel": source_channel,
        "candidate_project": candidate.to_dict(),
        "input_text": text,
        "instructions": (
            "Return only structured project merge candidates: tasks, decisions, risks, roadmap_updates. "
            "Do not include secrets, raw private data, chat ids, or host paths."
        ),
    }


def _call_ai_planner(
    planner: Callable[[dict[str, Any]], Mapping[str, Any]],
    payload: dict[str, Any],
) -> Mapping[str, Any]:
    result = planner(dict(payload))
    if not isinstance(result, Mapping):
        raise ProjectIntakeError("ai_merge_planner must return a mapping")
    encoded = str(result)
    _reject_sensitive(encoded, field_name="ai_merge_planner_result")
    return result


def _extract_tasks(text: str) -> tuple[ProjectIntakeTask, ...]:
    tasks: list[ProjectIntakeTask] = []
    for line in _candidate_lines(text):
        if not _TASK_HINT_RE.search(line):
            continue
        title = _strip_marker(line)
        if not title:
            continue
        tasks.append(
            ProjectIntakeTask(
                title=title,
                kind=_task_kind(title),
                priority=_priority(title),
                evidence=("mobile_intake",),
            )
        )
    return tuple(_dedupe_tasks(tasks)[:12])


def _tasks_from_ai(payload: Mapping[str, Any] | None) -> tuple[ProjectIntakeTask, ...]:
    if not payload:
        return ()
    raw_tasks = payload.get("tasks") or ()
    if not isinstance(raw_tasks, Iterable) or isinstance(raw_tasks, (str, bytes, Mapping)):
        raise ProjectIntakeError("ai tasks must be a list")
    tasks: list[ProjectIntakeTask] = []
    for raw in raw_tasks:
        item = raw if isinstance(raw, Mapping) else {"title": raw}
        title = _safe_text(item.get("title") or "", field_name="task.title", max_len=_MAX_ITEM)
        tasks.append(
            ProjectIntakeTask(
                title=title,
                kind=_safe_token(item.get("kind") or _task_kind(title), field_name="task.kind"),
                priority=_safe_token(item.get("priority") or _priority(title), field_name="task.priority"),
                evidence=("ai_merge_planner",),
            )
        )
    return tuple(_dedupe_tasks(tasks)[:12])


def _candidate_lines(text: str) -> list[str]:
    lines = []
    for raw in re.split(r"[\r\n]+|(?<=[.!?])\s+", text):
        line = " ".join(raw.strip().split())
        if line:
            lines.append(line)
    return lines


def _strip_marker(line: str) -> str:
    cleaned = re.sub(r"^[-*+\d.)\s]+", "", line).strip()
    cleaned = _PROJECT_HINT_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"^(todo|task|aufgabe|roadmap|plan|mvp)\s*[:.-]\s*", "", cleaned, flags=re.IGNORECASE)
    return _safe_text(cleaned, field_name="task", max_len=_MAX_ITEM)


def _task_kind(title: str) -> str:
    lowered = title.lower()
    if any(word in lowered for word in ("test", "smoke", "qa")):
        return "test"
    if any(word in lowered for word in ("deploy", "cloudflare", "release", "live")):
        return "release"
    if any(word in lowered for word in ("entscheidung", "decision", "frage")):
        return "decision"
    if any(word in lowered for word in ("roadmap", "plan")):
        return "roadmap"
    return "task"


def _priority(title: str) -> str:
    lowered = title.lower()
    if any(word in lowered for word in ("p0", "urgent", "kritisch", "blocker", "mvp")):
        return "high"
    if any(word in lowered for word in ("später", "later", "nice")):
        return "low"
    return "normal"


def _extract_prefixed_items(text: str, prefixes: tuple[str, ...]) -> tuple[str, ...]:
    items: list[str] = []
    prefix_re = re.compile(r"^(?:" + "|".join(re.escape(prefix) for prefix in prefixes) + r")\s*[:.-]\s*(.+)$", re.IGNORECASE)
    for line in _candidate_lines(text):
        match = prefix_re.search(line)
        if match:
            items.append(_safe_text(match.group(1), field_name="item", max_len=_MAX_ITEM))
    return _dedupe_strings(items)[:12]


def _tuple_from_payload(payload: Mapping[str, Any] | None, key: str) -> tuple[str, ...]:
    if not payload:
        return ()
    values = payload.get(key) or ()
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, Iterable) or isinstance(values, Mapping):
        raise ProjectIntakeError(f"{key} must be a list")
    return _dedupe_strings(_safe_text(value, field_name=key, max_len=_MAX_ITEM) for value in values)[:12]


def _dedupe_tasks(tasks: Iterable[ProjectIntakeTask]) -> list[ProjectIntakeTask]:
    seen: set[str] = set()
    result: list[ProjectIntakeTask] = []
    for task in tasks:
        key = task.title.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(task)
    return result


def _dedupe_strings(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(text)
    return tuple(result)


def _safe_token(value: Any, *, field_name: str) -> str:
    token = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    if not token:
        raise ProjectIntakeError(f"{field_name} must not be empty")
    if len(token) > 64:
        raise ProjectIntakeError(f"{field_name} exceeds max length")
    return token


def _safe_text(value: Any, *, field_name: str, max_len: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ProjectIntakeError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ProjectIntakeError(f"{field_name} exceeds max length {max_len}")
    _reject_sensitive(text, field_name=field_name)
    return text


def _reject_sensitive(text: str, *, field_name: str) -> None:
    if _SECRET_RE.search(text):
        raise ProjectIntakeError(f"{field_name} appears to contain secret material")
    if _HOST_PATH_RE.search(text):
        raise ProjectIntakeError(f"{field_name} must not contain host-local absolute paths")


def _safe_timestamp(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    timestamp = _safe_text(value, field_name="applied_at", max_len=40)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        raise ProjectIntakeError("applied_at must be an ISO UTC timestamp")
    return timestamp


def _apply_blockers(
    record: ServerProjectRecord,
    proposal: Mapping[str, Any],
    *,
    review_confirmed: bool,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not review_confirmed:
        blockers.append("review_not_confirmed")
    status = str(proposal.get("status") or "")
    if status not in {"review", "ready"}:
        blockers.append("proposal_not_ready_for_apply")
    candidate = proposal.get("candidate_project")
    candidate_slug = ""
    if isinstance(candidate, Mapping):
        candidate_slug = _normalize_project_hint(str(candidate.get("project_slug") or ""))
    if candidate_slug != record.project_slug:
        blockers.append("proposal_project_mismatch")
    if not _proposal_item_count(proposal):
        blockers.append("proposal_has_no_merge_items")
    return tuple(dict.fromkeys(blockers))


def _apply_event(
    *,
    record: ServerProjectRecord,
    proposal: Mapping[str, Any],
    applied_at: str,
    applied_by: str,
    blockers: tuple[str, ...],
) -> dict[str, Any]:
    tasks = tuple(_safe_task_payload(item) for item in _payload_list(proposal.get("tasks"), field_name="tasks"))
    decisions = _safe_string_tuple(proposal.get("decisions"), field_name="decisions")
    risks = _safe_string_tuple(proposal.get("risks"), field_name="risks")
    roadmap_updates = _safe_string_tuple(proposal.get("roadmap_updates"), field_name="roadmap_updates")
    event_core = {
        "schema": PROJECT_INTAKE_APPLY_SCHEMA,
        "project_slug": record.project_slug,
        "project_title": record.project_spec.project_title,
        "applied_at": applied_at,
        "applied_by": applied_by,
        "source_channel": _safe_token(proposal.get("source_channel") or "unknown", field_name="source_channel"),
        "status": "blocked" if blockers else "applied",
        "blockers": blockers,
        "tasks": tasks,
        "decisions": decisions,
        "risks": risks,
        "roadmap_updates": roadmap_updates,
        "task_count": len(tasks),
        "decision_count": len(decisions),
        "risk_count": len(risks),
        "roadmap_update_count": len(roadmap_updates),
        "project_state_write_performed": False,
        "roadmap_file_write_performed": False,
        "raw_content_visible": False,
        "raw_content_persisted": False,
    }
    digest_payload = json.dumps(event_core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    event_core["event_id"] = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:24]
    return event_core


def _proposal_item_count(proposal: Mapping[str, Any]) -> int:
    total = 0
    for key in ("tasks", "decisions", "risks", "roadmap_updates"):
        value = proposal.get(key) or ()
        if isinstance(value, (list, tuple)):
            total += len(value)
    return total


def _safe_task_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        value = {"title": value}
    title = _safe_text(value.get("title") or "", field_name="task.title", max_len=_MAX_ITEM)
    return {
        "schema": PROJECT_INTAKE_TASK_SCHEMA,
        "title": title,
        "kind": _safe_token(value.get("kind") or _task_kind(title), field_name="task.kind"),
        "priority": _safe_token(value.get("priority") or _priority(title), field_name="task.priority"),
        "evidence": tuple(
            _safe_token(item, field_name="task.evidence")
            for item in _payload_list(value.get("evidence") or ("reviewed_project_intake",), field_name="task.evidence")
        )[:6],
    }


def _safe_string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    return tuple(
        _safe_text(item, field_name=field_name, max_len=_MAX_ITEM)
        for item in _payload_list(value, field_name=field_name)
    )[:12]


def _payload_list(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, str):
        return (value,)
    raise ProjectIntakeError(f"{field_name} must be a list")


def _load_intake_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": PROJECT_INTAKE_LEDGER_SCHEMA, "events": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProjectIntakeError("project intake ledger is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != PROJECT_INTAKE_LEDGER_SCHEMA:
        raise ProjectIntakeError("project intake ledger schema is unsupported")
    if not isinstance(payload.get("events"), list):
        raise ProjectIntakeError("project intake ledger events must be a list")
    return payload
