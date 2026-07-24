"""Durable clarification run store for ask_user v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any, Callable, Mapping

from src.clarification_privacy import (
    build_clarification_privacy_boundary,
    build_memory_candidate_from_answer,
    build_secure_handoff_intent,
    contains_private_path_material,
    contains_secret_material,
)


CLARIFICATION_REQUEST_SCHEMA = "odysseus.clarification_request.v2"
CLARIFICATION_RUN_SCHEMA = "odysseus.clarification_run.v1"
CLARIFICATION_EVENT_SCHEMA = "odysseus.clarification_event.v1"

RUN_STATUSES = {
    "clarifying",
    "understanding_review",
    "ready_for_plan",
    "planning",
    "paused",
    "cancelled",
    "blocked",
    "expired",
}
EVENT_TYPES = {
    "request_created",
    "question_answered",
    "answer_revised",
    "question_skipped",
    "default_approved",
    "batch_completed",
    "understanding_confirmed",
    "ready_for_plan",
    "run_reopened",
    "run_paused",
    "run_cancelled",
    "run_expired",
    "run_blocked",
}
QUESTION_TYPES = {
    "single_select",
    "multi_select",
    "boolean",
    "short_text",
    "long_text",
    "number",
    "date",
    "resource_ref",
}
_SCOPES = {"conversation", "project", "coding_task"}
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")
_PRIVATE_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/users/|/opt/|\\\\)")
_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")


class ClarificationStoreError(ValueError):
    """Raised when a clarification operation is invalid or unsafe."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        current_version: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.current_version = current_version
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class ClarificationWriteResult:
    run: dict[str, Any]
    event: dict[str, Any]
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.clarification_write_result.v1",
            "run": self.run,
            "event": self.event,
            "idempotent_replay": self.idempotent_replay,
        }


class ClarificationStore:
    def __init__(self, *, session_factory: Callable[[], Any] | None = None) -> None:
        if session_factory is None:
            from core.database import SessionLocal

            session_factory = SessionLocal
        self._session_factory = session_factory

    def create_run(
        self,
        *,
        owner: Any,
        session_id: Any,
        request: Mapping[str, Any],
        project_slug: Any = "",
        coding_task_id: Any = "",
        clarification_id: Any = "",
    ) -> ClarificationWriteResult:
        from core.database import ClarificationEvent, ClarificationRun

        safe_owner = _required_ref(owner, "owner")
        safe_session = _required_ref(session_id, "session_id")
        normalized_request = _normalize_request(request)
        run_id = _safe_run_id(clarification_id) or _new_run_id(safe_owner, safe_session, normalized_request)
        unresolved = _unresolved_required(normalized_request)
        now = _now_iso()
        db = self._session_factory()
        try:
            existing = db.query(ClarificationRun).filter(ClarificationRun.id == run_id).first()
            if existing is not None:
                raise ClarificationStoreError("run_exists", "clarification run already exists")
            row = ClarificationRun(
                id=run_id,
                owner=safe_owner,
                session_id=safe_session,
                scope=normalized_request["scope"],
                status="clarifying" if unresolved else "understanding_review",
                version=1,
                project_slug=_optional_ref(project_slug),
                coding_task_id=_optional_ref(coding_task_id),
                intent_summary=normalized_request["intent_summary"],
                request_json=_json(normalized_request),
                answers_json=_json({}),
                unresolved_required_json=_json(unresolved),
                understanding_summary="",
                ready_for_plan=False,
                raw_content_visible=False,
            )
            event = ClarificationEvent(
                id=_event_id(run_id, 1, "request_created"),
                clarification_id=run_id,
                owner=safe_owner,
                event_type="request_created",
                version=1,
                payload_json=_json({"request": normalized_request, "created_at": now}),
            )
            db.add(row)
            db.add(event)
            db.commit()
            db.refresh(row)
            db.refresh(event)
            return ClarificationWriteResult(run=_run_to_dict(row), event=_event_to_dict(event))
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def read_run(self, *, owner: Any, clarification_id: Any) -> dict[str, Any] | None:
        from core.database import ClarificationRun

        safe_owner = _required_ref(owner, "owner")
        run_id = _safe_run_id(clarification_id)
        if not run_id:
            raise ClarificationStoreError("invalid_clarification_id", "clarification_id is invalid")
        db = self._session_factory()
        try:
            row = db.query(ClarificationRun).filter(
                ClarificationRun.id == run_id,
                ClarificationRun.owner == safe_owner,
            ).first()
            return _run_to_dict(row) if row is not None else None
        finally:
            db.close()

    def read_events(self, *, owner: Any, clarification_id: Any) -> tuple[dict[str, Any], ...]:
        from core.database import ClarificationEvent

        safe_owner = _required_ref(owner, "owner")
        run_id = _safe_run_id(clarification_id)
        if not run_id:
            raise ClarificationStoreError("invalid_clarification_id", "clarification_id is invalid")
        db = self._session_factory()
        try:
            rows = db.query(ClarificationEvent).filter(
                ClarificationEvent.clarification_id == run_id,
                ClarificationEvent.owner == safe_owner,
            ).order_by(ClarificationEvent.version.asc(), ClarificationEvent.created_at.asc()).all()
            return tuple(_event_to_dict(row) for row in rows)
        finally:
            db.close()

    def read_active_run_for_session(self, *, owner: Any, session_id: Any) -> dict[str, Any] | None:
        from core.database import ClarificationRun

        safe_owner = _required_ref(owner, "owner")
        safe_session = _required_ref(session_id, "session_id")
        db = self._session_factory()
        try:
            row = db.query(ClarificationRun).filter(
                ClarificationRun.owner == safe_owner,
                ClarificationRun.session_id == safe_session,
                ClarificationRun.status.in_(("clarifying", "understanding_review", "ready_for_plan", "paused", "blocked")),
            ).order_by(ClarificationRun.updated_at.desc(), ClarificationRun.version.desc()).first()
            return _run_to_dict(row) if row is not None else None
        finally:
            db.close()

    def list_active_runs(self, *, owner: Any | None = None, limit: Any = 25) -> tuple[dict[str, Any], ...]:
        from core.database import ClarificationRun

        max_rows = _bounded_int(limit, "limit", 1, 100)
        safe_owner = _required_ref(owner, "owner") if owner is not None else ""
        db = self._session_factory()
        try:
            query = db.query(ClarificationRun).filter(
                ClarificationRun.status.in_(("clarifying", "understanding_review", "ready_for_plan", "paused", "blocked")),
            )
            if safe_owner:
                query = query.filter(ClarificationRun.owner == safe_owner)
            rows = query.order_by(ClarificationRun.updated_at.desc(), ClarificationRun.version.desc()).limit(max_rows).all()
            return tuple(_run_to_dict(row) for row in rows)
        finally:
            db.close()

    def answer_question(
        self,
        *,
        owner: Any,
        clarification_id: Any,
        question_id: Any,
        answer: Any,
        expected_version: Any,
        idempotency_key: Any,
    ) -> ClarificationWriteResult:
        payload = {"answer": answer}
        return self._append_answer_event(
            owner=owner,
            clarification_id=clarification_id,
            question_id=question_id,
            answer_payload=payload,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            event_type="question_answered",
        )

    def confirm_understanding(
        self,
        *,
        owner: Any,
        clarification_id: Any,
        understanding_summary: Any,
        expected_version: Any,
        idempotency_key: Any,
    ) -> ClarificationWriteResult:
        from core.database import ClarificationEvent, ClarificationRun

        safe_owner = _required_ref(owner, "owner")
        run_id = _safe_run_id(clarification_id)
        key = _required_ref(idempotency_key, "idempotency_key")
        summary = _safe_text(understanding_summary, "understanding_summary", max_len=1200)
        db = self._session_factory()
        try:
            if not run_id:
                raise ClarificationStoreError("invalid_clarification_id", "clarification_id is invalid")
            run = db.query(ClarificationRun).filter(ClarificationRun.id == run_id, ClarificationRun.owner == safe_owner).first()
            if run is None:
                raise ClarificationStoreError("run_not_found", "clarification run not found")
            replay = _find_idempotent_event(db, ClarificationEvent, run_id, key)
            if replay is not None:
                return ClarificationWriteResult(run=_run_to_dict(run), event=_event_to_dict(replay), idempotent_replay=True)
            if run.status in {"cancelled", "expired"}:
                raise ClarificationStoreError("run_closed", "clarification run is closed", current_version=run.version)
            _assert_expected_version(run, expected_version)
            unresolved = _json_list(run.unresolved_required_json)
            if unresolved:
                raise ClarificationStoreError("required_questions_unresolved", "required clarification questions remain", current_version=run.version)
            next_version = int(run.version) + 1
            run.version = next_version
            run.status = "ready_for_plan"
            run.ready_for_plan = True
            run.understanding_summary = summary
            event = ClarificationEvent(
                id=_event_id(run_id, next_version, "ready_for_plan"),
                clarification_id=run_id,
                owner=safe_owner,
                event_type="ready_for_plan",
                version=next_version,
                idempotency_key=key,
                payload_json=_json({"understanding_summary": summary}),
            )
            db.add(event)
            db.commit()
            db.refresh(run)
            db.refresh(event)
            return ClarificationWriteResult(run=_run_to_dict(run), event=_event_to_dict(event))
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def pause_run(
        self,
        *,
        owner: Any,
        clarification_id: Any,
        expected_version: Any,
        idempotency_key: Any,
    ) -> ClarificationWriteResult:
        return self._append_lifecycle_event(
            owner=owner,
            clarification_id=clarification_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            event_type="run_paused",
            next_status="paused",
            ready_for_plan=False,
            payload={"status": "paused"},
        )

    def reopen_run(
        self,
        *,
        owner: Any,
        clarification_id: Any,
        expected_version: Any,
        idempotency_key: Any,
    ) -> ClarificationWriteResult:
        return self._append_lifecycle_event(
            owner=owner,
            clarification_id=clarification_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            event_type="run_reopened",
            next_status=None,
            ready_for_plan=False,
            payload={"status": "reopened"},
        )

    def cancel_run(
        self,
        *,
        owner: Any,
        clarification_id: Any,
        expected_version: Any,
        idempotency_key: Any,
    ) -> ClarificationWriteResult:
        return self._append_lifecycle_event(
            owner=owner,
            clarification_id=clarification_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            event_type="run_cancelled",
            next_status="cancelled",
            ready_for_plan=False,
            payload={"status": "cancelled"},
        )

    def _append_answer_event(
        self,
        *,
        owner: Any,
        clarification_id: Any,
        question_id: Any,
        answer_payload: Mapping[str, Any],
        expected_version: Any,
        idempotency_key: Any,
        event_type: str,
    ) -> ClarificationWriteResult:
        from core.database import ClarificationEvent, ClarificationRun

        if event_type not in EVENT_TYPES:
            raise ClarificationStoreError("invalid_event_type", "event_type is invalid")
        safe_owner = _required_ref(owner, "owner")
        run_id = _safe_run_id(clarification_id)
        qid = _question_key(question_id)
        key = _required_ref(idempotency_key, "idempotency_key")
        if contains_private_path_material(answer_payload):
            raise ClarificationStoreError("unsafe_content", "clarification answer contains private path material")
        answer_contains_secret = contains_secret_material(answer_payload)
        db = self._session_factory()
        try:
            run = _load_run_for_update(db, ClarificationRun, run_id, safe_owner)
            replay = _find_idempotent_event(db, ClarificationEvent, run_id, key)
            if replay is not None:
                return ClarificationWriteResult(run=_run_to_dict(run), event=_event_to_dict(replay), idempotent_replay=True)
            run_context = _run_to_dict(run)
            if answer_contains_secret:
                raise ClarificationStoreError(
                    "secure_handoff_required",
                    "secret-bearing clarification answers must use secure handoff",
                    current_version=run.version,
                    details={"secure_handoff": build_secure_handoff_intent(run_context, question_id=qid)},
                )
            _reject_unsafe_payload(answer_payload)
            _assert_expected_version(run, expected_version)
            request = _json_obj(run.request_json)
            question_by_key = {
                str(item.get("key")): item
                for item in request.get("questions") or ()
                if isinstance(item, Mapping)
            }
            question_keys = set(question_by_key)
            if qid not in question_keys:
                raise ClarificationStoreError("unknown_question", "question_id is not part of this clarification run", current_version=run.version)
            answers = _json_obj(run.answers_json)
            actual_event_type = "answer_revised" if qid in answers else event_type
            answers[qid] = dict(answer_payload)
            unresolved = tuple(item for item in _json_list(run.unresolved_required_json) if item != qid)
            next_version = int(run.version) + 1
            run.version = next_version
            run.answers_json = _json(answers)
            run.unresolved_required_json = _json(unresolved)
            run.status = "understanding_review" if not unresolved else "clarifying"
            run.ready_for_plan = False
            run_context_after = _run_to_dict(run)
            event_payload = {
                "question_id": qid,
                **dict(answer_payload),
                "privacy_boundary": build_clarification_privacy_boundary(run_context_after, question_id=qid),
            }
            memory_candidate = build_memory_candidate_from_answer(
                run_context_after,
                question=question_by_key.get(qid),
                question_id=qid,
                answer=answer_payload,
            )
            if memory_candidate is not None:
                event_payload["memory_candidate"] = memory_candidate
            event = ClarificationEvent(
                id=_event_id(run_id, next_version, actual_event_type, qid),
                clarification_id=run_id,
                owner=safe_owner,
                event_type=actual_event_type,
                version=next_version,
                question_id=qid,
                idempotency_key=key,
                payload_json=_json(event_payload),
            )
            db.add(event)
            db.commit()
            db.refresh(run)
            db.refresh(event)
            return ClarificationWriteResult(run=_run_to_dict(run), event=_event_to_dict(event))
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _append_lifecycle_event(
        self,
        *,
        owner: Any,
        clarification_id: Any,
        expected_version: Any,
        idempotency_key: Any,
        event_type: str,
        next_status: str | None,
        ready_for_plan: bool,
        payload: Mapping[str, Any],
    ) -> ClarificationWriteResult:
        from core.database import ClarificationEvent, ClarificationRun

        if event_type not in EVENT_TYPES:
            raise ClarificationStoreError("invalid_event_type", "event_type is invalid")
        safe_owner = _required_ref(owner, "owner")
        run_id = _safe_run_id(clarification_id)
        key = _required_ref(idempotency_key, "idempotency_key")
        _reject_unsafe_payload(payload)
        db = self._session_factory()
        try:
            if not run_id:
                raise ClarificationStoreError("invalid_clarification_id", "clarification_id is invalid")
            run = db.query(ClarificationRun).filter(ClarificationRun.id == run_id, ClarificationRun.owner == safe_owner).first()
            if run is None:
                raise ClarificationStoreError("run_not_found", "clarification run not found")
            replay = _find_idempotent_event(db, ClarificationEvent, run_id, key)
            if replay is not None:
                return ClarificationWriteResult(run=_run_to_dict(run), event=_event_to_dict(replay), idempotent_replay=True)
            if run.status in {"cancelled", "expired"}:
                raise ClarificationStoreError("run_closed", "clarification run is closed", current_version=run.version)
            _assert_expected_version(run, expected_version)
            unresolved = _json_list(run.unresolved_required_json)
            status = next_status
            if status is None:
                status = "clarifying" if unresolved else "understanding_review"
            if status not in RUN_STATUSES:
                raise ClarificationStoreError("invalid_status", "clarification status is invalid")
            next_version = int(run.version) + 1
            run.version = next_version
            run.status = status
            run.ready_for_plan = bool(ready_for_plan)
            event = ClarificationEvent(
                id=_event_id(run_id, next_version, event_type),
                clarification_id=run_id,
                owner=safe_owner,
                event_type=event_type,
                version=next_version,
                idempotency_key=key,
                payload_json=_json(payload),
            )
            db.add(event)
            db.commit()
            db.refresh(run)
            db.refresh(event)
            return ClarificationWriteResult(run=_run_to_dict(run), event=_event_to_dict(event))
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _normalize_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ClarificationStoreError("invalid_request", "clarification request must be an object")
    if payload.get("schema") != CLARIFICATION_REQUEST_SCHEMA:
        raise ClarificationStoreError("invalid_schema", "clarification request schema is invalid")
    scope = str(payload.get("scope") or "").strip()
    if scope not in _SCOPES:
        raise ClarificationStoreError("invalid_scope", "clarification scope is invalid")
    questions = tuple(_normalize_question(item) for item in payload.get("questions") or ())
    batch = payload.get("batch") if isinstance(payload.get("batch"), Mapping) else {}
    normalized = {
        "schema": CLARIFICATION_REQUEST_SCHEMA,
        "scope": scope,
        "intent_summary": _safe_text(payload.get("intent_summary"), "intent_summary", max_len=1000),
        "questions": questions,
        "batch": {
            "label": _safe_text(batch.get("label") or "Clarification", "batch.label", max_len=120),
            "index": _bounded_int(batch.get("index"), "batch.index", 1, 1000),
            "total": _bounded_int(batch.get("total"), "batch.total", 1, 1000),
            "max_visible_questions": _bounded_int(batch.get("max_visible_questions"), "batch.max_visible_questions", 1, 10),
        },
        "defaults_visible": bool(payload.get("defaults_visible")),
    }
    _reject_unsafe_payload(normalized)
    return normalized


def _normalize_question(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ClarificationStoreError("invalid_question", "question must be an object")
    qtype = str(payload.get("type") or "").strip()
    if qtype not in QUESTION_TYPES:
        raise ClarificationStoreError("invalid_question_type", "question type is invalid")
    question = {
        "key": _question_key(payload.get("key")),
        "type": qtype,
        "prompt": _safe_text(payload.get("prompt"), "question.prompt", max_len=1000),
        "required": bool(payload.get("required")),
        "reason": _safe_text(payload.get("reason"), "question.reason", max_len=500),
        "category": _safe_text(payload.get("category") or "", "question.category", allow_empty=True, max_len=80),
    }
    if payload.get("options") is not None:
        question["options"] = tuple(_normalize_option(item) for item in payload.get("options") or ())[:20]
    if payload.get("default") is not None:
        question["default"] = _safe_answer_value(payload.get("default"))
    if payload.get("depends_on"):
        question["depends_on"] = _question_key(payload.get("depends_on"))
    return question


def _normalize_option(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ClarificationStoreError("invalid_option", "question option must be an object")
    option = {"label": _safe_text(payload.get("label"), "option.label", max_len=80)}
    description = _safe_text(payload.get("description") or "", "option.description", allow_empty=True, max_len=240)
    if description:
        option["description"] = description
    if payload.get("recommended") is not None:
        option["recommended"] = bool(payload.get("recommended"))
    return option


def _safe_answer_value(value: Any) -> Any:
    _reject_unsafe_payload({"value": value})
    return value


def _load_run_for_update(db: Any, model: Any, run_id: str, owner: str) -> Any:
    if not run_id:
        raise ClarificationStoreError("invalid_clarification_id", "clarification_id is invalid")
    run = db.query(model).filter(model.id == run_id, model.owner == owner).first()
    if run is None:
        raise ClarificationStoreError("run_not_found", "clarification run not found")
    if run.status in {"cancelled", "expired"}:
        raise ClarificationStoreError("run_closed", "clarification run is closed", current_version=run.version)
    return run


def _find_idempotent_event(db: Any, model: Any, run_id: str, key: str) -> Any | None:
    return db.query(model).filter(model.clarification_id == run_id, model.idempotency_key == key).first()


def _assert_expected_version(run: Any, expected_version: Any) -> None:
    expected = _bounded_int(expected_version, "expected_version", 1, 1_000_000)
    current = int(run.version or 0)
    if expected != current:
        raise ClarificationStoreError("version_conflict", "clarification version conflict", current_version=current)


def _unresolved_required(request: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item["key"]) for item in request.get("questions") or () if item.get("required"))


def _run_to_dict(row: Any) -> dict[str, Any]:
    request = _json_obj(row.request_json)
    answers = _json_obj(row.answers_json)
    unresolved = _json_list(row.unresolved_required_json)
    payload = {
        "schema": CLARIFICATION_RUN_SCHEMA,
        "clarification_id": row.id,
        "owner": row.owner,
        "session_id": row.session_id,
        "scope": row.scope,
        "status": row.status,
        "version": int(row.version or 0),
        "project_slug": row.project_slug or "",
        "coding_task_id": row.coding_task_id or "",
        "intent_summary": row.intent_summary or "",
        "request": request,
        "answers": answers,
        "unresolved_required_question_ids": unresolved,
        "unresolved_required_count": len(unresolved),
        "understanding_summary": row.understanding_summary or "",
        "ready_for_plan": bool(row.ready_for_plan),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "raw_content_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _event_to_dict(row: Any) -> dict[str, Any]:
    payload = {
        "schema": CLARIFICATION_EVENT_SCHEMA,
        "event_id": row.id,
        "clarification_id": row.clarification_id,
        "owner": row.owner,
        "event_type": row.event_type,
        "version": int(row.version or 0),
        "question_id": row.question_id or "",
        "payload": _json_obj(row.payload_json),
        "created_at": _iso(row.created_at),
        "raw_content_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _safe_text(value: Any, field: str, *, allow_empty: bool = False, max_len: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ClarificationStoreError(f"invalid_{field}", f"{field} must not be empty")
    if len(text) > max_len:
        raise ClarificationStoreError(f"{field}_too_long", f"{field} is too long")
    if _SECRET_RE.search(text) or _PRIVATE_PATH_RE.search(text):
        raise ClarificationStoreError("unsafe_content", f"{field} contains unsafe content")
    return text


def _required_ref(value: Any, field: str) -> str:
    text = _safe_text(value, field, max_len=180)
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,180}", text):
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{field}-{digest}"
    return text


def _optional_ref(value: Any) -> str:
    text = str(value or "").strip()
    return _required_ref(text, "ref") if text else ""


def _question_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_.-]+", "_", text).strip("_.-")
    if not _SAFE_ID_RE.fullmatch(text):
        raise ClarificationStoreError("invalid_question_id", "question key/id is invalid")
    return text


def _safe_run_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", text):
        raise ClarificationStoreError("invalid_clarification_id", "clarification_id is invalid")
    return text


def _new_run_id(owner: str, session_id: str, request: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(f"{owner}:{session_id}:{uuid.uuid4().hex}".encode("utf-8")).hexdigest()[:24]
    return f"clar-{digest}"


def _event_id(run_id: str, version: int, event_type: str, question_id: str = "") -> str:
    seed = f"{run_id}:{version}:{event_type}:{question_id}"
    return "clev-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ClarificationStoreError(f"invalid_{field}", f"{field} must be an integer") from None
    if parsed < minimum or parsed > maximum:
        raise ClarificationStoreError(f"{field}_out_of_range", f"{field} is out of range")
    return parsed


def _json(value: Any) -> str:
    _reject_unsafe_payload(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_obj(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> tuple[str, ...]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def _reject_unsafe_payload(payload: Any) -> None:
    encoded = repr(payload)
    if _SECRET_RE.search(encoded):
        raise ClarificationStoreError("unsafe_content", "clarification payload contains secret material")
    if _PRIVATE_PATH_RE.search(encoded):
        raise ClarificationStoreError("unsafe_content", "clarification payload contains private path material")


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
