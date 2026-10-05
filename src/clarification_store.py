"""SID-bound durable clarification storage."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json, re, uuid
from typing import Any, Callable, Mapping
from sqlalchemy.exc import IntegrityError
from src.auth_helpers import SessionPrincipal
from core.auth import SUBJECT_ID_PATTERN
from src.clarification_privacy import build_clarification_privacy_boundary, build_memory_candidate_from_answer, build_secure_handoff_intent, contains_private_path_material, contains_secret_material

CLARIFICATION_REQUEST_SCHEMA = "odysseus.clarification_request.v2"
CLARIFICATION_RUN_SCHEMA = "odysseus.clarification_run.v1"
CLARIFICATION_EVENT_SCHEMA = "odysseus.clarification_event.v1"
_PRIVATE = "__odysseus_clarification_private_v1"
_OPEN = ("clarifying", "understanding_review", "ready_for_plan", "paused", "blocked")
RUN_STATUSES = set(_OPEN) | {"planning", "cancelled", "expired"}
_INBOX_REQUIRED_ACTION = {"clarifying":"answer_required_questions","understanding_review":"review_understanding","ready_for_plan":"review_plan","paused":"reopen_or_cancel","blocked":"resolve_blocker","planning":"wait_for_plan","cancelled":"none","expired":"none"}
_SCOPES = {"conversation", "project", "coding_task"}
_TYPES = {"single_select", "multi_select", "boolean", "short_text", "long_text", "number", "date", "resource_ref"}
QUESTION_TYPES = _TYPES
EVENT_TYPES = {"request_created", "question_answered", "answer_revised", "question_skipped", "default_approved", "batch_completed", "understanding_confirmed", "ready_for_plan", "run_reopened", "run_paused", "run_cancelled", "run_expired", "run_blocked"}
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_SECRET = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")
_PATH = re.compile(r"(?i)([a-z]:[\\/]|/home/|/users/|/opt/|\\\\)")


def _inbox_digest(domain: str, value: Any) -> str:
    """Domain-separate private Inbox bindings without retaining raw identity."""
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise ClarificationStoreError("inbox_failed", "inbox digest failed") from None
    domain_bytes = b"odysseus.harbour.inbox.clarification.v1\0" + domain.encode("ascii")
    return hashlib.sha256(
        len(domain_bytes).to_bytes(4, "big") + domain_bytes
        + len(encoded).to_bytes(4, "big") + encoded
    ).hexdigest()


@contextmanager
def _sqlite_inbox_read_transaction(db: Any):
    """Start and observe a real SQLite read transaction before descriptor IO."""
    connection = db.connection()
    if connection.dialect.name != "sqlite":
        raise ClarificationStoreError("inbox_failed", "inbox store is unavailable")
    connection.exec_driver_sql("BEGIN")
    wrapped = connection.connection
    driver = getattr(wrapped, "driver_connection", wrapped)
    if getattr(driver, "in_transaction", None) is not True:
        raise ClarificationStoreError("inbox_failed", "inbox transaction is unavailable")
    try:
        yield connection
    finally:
        db.rollback()

class ClarificationStoreError(ValueError):
    def __init__(self, code: str, message: str, *, current_version: int | None = None, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message); self.code, self.current_version, self.details = code, current_version, dict(details or {})

@dataclass(frozen=True, slots=True)
class ClarificationWriteResult:
    run: dict[str, Any]; event: dict[str, Any]; idempotent_replay: bool = False
    def to_dict(self) -> dict[str, Any]:
        return {"schema":"odysseus.clarification_write_result.v1","run":self.run,"event":self.event,"idempotent_replay":self.idempotent_replay}


@dataclass(frozen=True, slots=True, repr=False)
class ClarificationInboxItem:
    clarification_id: str
    status: str
    expected_version: int
    ready_for_plan: bool
    updated_at: str
    binding_digest: str

    def __repr__(self) -> str:
        return "ClarificationInboxItem(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ClarificationInboxSnapshot:
    owner_binding_digest: str
    boundary_digest: str
    items: tuple[ClarificationInboxItem, ...]
    clipped: bool
    binding_digest: str

    def __repr__(self) -> str:
        return "ClarificationInboxSnapshot(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ClarificationInboxActionReceipt:
    """A detached provenance receipt for one current Inbox lifecycle action."""

    receipt_handle: str
    current_binding_digest: str
    operation: str
    occurred_at: str
    event_version: int
    committed_status: str
    envelope_bytes: int

    def __repr__(self) -> str:
        return "ClarificationInboxActionReceipt(<redacted>)"

class ClarificationStore:
    def __init__(self, *, session_factory: Callable[[], Any] | None = None) -> None:
        if session_factory is None:
            from core.database import SessionLocal
            session_factory = SessionLocal
        self._session_factory = session_factory

    def create_run(self, *, principal: SessionPrincipal, session_id: Any, request: Mapping[str, Any], project_slug: Any = "", coding_task_id: Any = "", clarification_id: Any = "") -> ClarificationWriteResult:
        from core.database import ClarificationEvent, ClarificationRun
        p, sid, req = _principal(principal), _ref(session_id,"session_id"), _request(request)
        supplied_id = str(clarification_id or "").strip()
        if supplied_id and not _safe_id(supplied_id):
            raise ClarificationStoreError("invalid_clarification_id", "clarification_id is invalid")
        run_id, unresolved = _safe_id(supplied_id) or "clar-"+uuid.uuid4().hex[:20], _unresolved(req)
        db=self._session_factory()
        try:
            if db.query(ClarificationRun.id).filter(ClarificationRun.id==run_id).first() is not None:
                raise ClarificationStoreError("run_exists","clarification run already exists")
            run=ClarificationRun(id=run_id,owner=p.owner,creator_subject_id=p.creator_subject_id,session_id=sid,scope=req["scope"],status="clarifying" if unresolved else "understanding_review",version=1,project_slug=_optional(project_slug),coding_task_id=_optional(coding_task_id),intent_summary=req["intent_summary"],request_json=_json(req),answers_json="{}",unresolved_required_json=_json(unresolved),understanding_summary="",ready_for_plan=False,raw_content_visible=False)
            event=ClarificationEvent(id=_event_id(run_id,1,"request_created"),clarification_id=run_id,owner=p.owner,event_type="request_created",version=1,payload_json=_json({"request":req,"created_at":_now()}))
            db.add_all((run,event)); db.flush()
            result=ClarificationWriteResult(_run(run,p),_event(event,p)); event.payload_json=_receipt(event.payload_json,_fingerprint(p,"create",{"session_id":sid,"request":req,"clarification_id":run_id},0),result)
            db.commit(); return result
        except IntegrityError:
            db.rollback()
            raise ClarificationStoreError("run_exists","clarification run already exists") from None
        except Exception: db.rollback(); raise
        finally: db.close()

    def read_run(self, *, principal: SessionPrincipal, clarification_id: Any) -> dict[str, Any] | None:
        from core.database import ClarificationRun
        p,rid=_principal(principal),_run_id(clarification_id); db=self._session_factory()
        try:
            row=db.query(ClarificationRun).filter(ClarificationRun.id==rid,_scope(ClarificationRun,p)).first(); return _run(row,p) if row else None
        finally: db.close()

    def read_events(self, *, principal: SessionPrincipal, clarification_id: Any) -> tuple[dict[str, Any],...]:
        from core.database import ClarificationEvent, ClarificationRun
        p,rid=_principal(principal),_run_id(clarification_id); db=self._session_factory()
        try:
            rows=db.query(ClarificationEvent).join(ClarificationRun,ClarificationEvent.clarification_id==ClarificationRun.id).filter(ClarificationEvent.clarification_id==rid,_scope(ClarificationRun,p)).order_by(ClarificationEvent.version,ClarificationEvent.created_at).all(); return tuple(_event(row,p) for row in rows)
        finally: db.close()

    def read_active_run_for_session(self, *, principal: SessionPrincipal, session_id: Any) -> dict[str, Any] | None:
        from core.database import ClarificationRun
        p,sid=_principal(principal),_ref(session_id,"session_id"); db=self._session_factory()
        try:
            row=db.query(ClarificationRun).filter(_scope(ClarificationRun,p),ClarificationRun.session_id==sid,ClarificationRun.status.in_(_OPEN)).order_by(ClarificationRun.updated_at.desc(),ClarificationRun.version.desc()).first(); return _run(row,p) if row else None
        finally: db.close()

    def list_active_metadata(self, *, principal: SessionPrincipal, limit: Any=25) -> tuple[dict[str,Any],...]:
        """Closed SQL projection for attention: no request, answer, project, or owner loading."""
        from core.database import ClarificationRun
        p,n=_principal(principal),_bounded(limit,"limit",1,100); db=self._session_factory()
        try:
            rows=db.query(ClarificationRun.id,ClarificationRun.session_id,ClarificationRun.status,ClarificationRun.version,ClarificationRun.ready_for_plan,ClarificationRun.unresolved_required_json).filter(_scope(ClarificationRun,p),ClarificationRun.status.in_(_OPEN)).order_by(ClarificationRun.updated_at.desc(),ClarificationRun.version.desc()).limit(n).all()
            return tuple({"clarification_id":x.id,"session_id":x.session_id,"status":x.status,"version":int(x.version or 0),"ready_for_plan":bool(x.ready_for_plan),"unresolved_required_count":len(_list(x.unresolved_required_json))} for x in rows)
        finally: db.close()

    def list_inbox_metadata(self, *, principal: SessionPrincipal, limit: Any=25) -> tuple[dict[str,Any],...]:
        """C-safe inbox projection with no session or JSON-content columns.

        The projection is intentionally distinct from attention.  It cannot
        accidentally grow into a run DTO because it does not select owner,
        session, request, answers, summary, project, or event payload fields.
        """
        from core.database import ClarificationRun, ClarificationEvent
        p,n=_principal(principal),_bounded(limit,"limit",1,100); db=self._session_factory()
        try:
            rows=db.query(ClarificationRun.id,ClarificationRun.status,ClarificationRun.version,ClarificationRun.ready_for_plan,ClarificationRun.updated_at,ClarificationEvent.id.label("latest_event_id")).outerjoin(ClarificationEvent,(ClarificationEvent.clarification_id==ClarificationRun.id)&(ClarificationEvent.version==ClarificationRun.version)).filter(_scope(ClarificationRun,p),ClarificationRun.status.in_(_OPEN)).order_by(ClarificationRun.updated_at.desc(),ClarificationRun.version.desc()).limit(n).all()
            if p.authority != "mcp_subject":
                return tuple({
                    "clarification_id": _run_id(x.id),
                    "status": str(x.status or ""),
                    "version": _bounded(x.version, "version", 1, 1_000_001),
                    "ready_for_plan": bool(x.ready_for_plan),
                    "owner": "self",
                    "authority": "session_principal",
                    "source": "clarification_store",
                    "freshness": "current",
                    "required_action": "review_clarification",
                    "due": None,
                    "latest_event_receipt": bool(x.latest_event_id),
                    "updated_at": _iso(x.updated_at),
                } for x in rows)
            return tuple(_inbox_metadata(
                clarification_id=x.id,
                status=x.status,
                version=x.version,
                ready_for_plan=x.ready_for_plan,
                authority=p.authority,
                event_receipt=bool(x.latest_event_id),
                updated_at=x.updated_at,
            ) for x in rows)
        finally: db.close()

    def capture_inbox_snapshot(self, *, principal: SessionPrincipal, limit: Any=25,
                               expected_boundary_digest: str | None = None) -> ClarificationInboxSnapshot:
        """Capture a content-free, SID-owned Inbox boundary.

        This is intentionally separate from the legacy list projection: it
        reads only scalar run metadata, validates the complete bounded owner
        boundary before filtering open states, and never carries owner or
        session identity in its returned value objects.
        """
        from core.database import ClarificationRun
        p, n = _principal(principal), _bounded(limit, "limit", 1, 100)
        if p.authority != "mcp_subject":
            raise ClarificationStoreError("inbox_failed", "inbox principal is unavailable")
        db = self._session_factory()
        try:
            rows = db.query(
                ClarificationRun.id, ClarificationRun.status, ClarificationRun.version,
                ClarificationRun.ready_for_plan, ClarificationRun.updated_at,
            ).filter(_scope(ClarificationRun, p)).order_by(
                ClarificationRun.updated_at.desc(), ClarificationRun.version.desc(), ClarificationRun.id.asc(),
            ).limit(10_001).all()
            if len(rows) > 10_000:
                raise ClarificationStoreError("inbox_failed", "inbox owner boundary exceeded")
            boundary_rows: list[dict[str, Any]] = []
            open_rows: list[tuple[Any, str, int, bool, str]] = []
            for row in rows:
                rid = _run_id(row.id)
                status = row.status
                version = row.version
                ready = row.ready_for_plan
                updated_at = row.updated_at
                if (type(status) is not str or status not in RUN_STATUSES
                        or type(version) is not int or not 1 <= version <= 1_000_001
                        or type(ready) is not bool or type(updated_at) is not datetime):
                    raise ClarificationStoreError("inbox_failed", "inbox run state is invalid")
                updated = _iso(updated_at)
                if not updated:
                    raise ClarificationStoreError("inbox_failed", "inbox timestamp is invalid")
                boundary_rows.append({"id": rid, "status": status, "version": version,
                                      "ready": ready, "updated": updated})
                if status in _OPEN:
                    open_rows.append((row, status, version, ready, updated))
            owner_binding = _inbox_digest("owner", p.creator_subject_id)
            boundary = _inbox_digest("boundary", {"owner": owner_binding, "rows": boundary_rows})
            if expected_boundary_digest is not None and expected_boundary_digest != boundary:
                raise ClarificationStoreError("inbox_stale", "inbox boundary changed")
            captured = open_rows[:n + 1]
            items = tuple(
                ClarificationInboxItem(
                    clarification_id=_run_id(row.id), status=status, expected_version=version,
                    ready_for_plan=ready, updated_at=updated,
                    binding_digest=_inbox_digest("item", {"owner": owner_binding, "id": _run_id(row.id),
                        "status": status, "version": version, "ready": ready, "updated": updated}),
                ) for row, status, version, ready, updated in captured[:n]
            )
            clipped = len(captured) > n
            binding = _inbox_digest("snapshot", {"owner": owner_binding, "boundary": boundary,
                "items": [item.binding_digest for item in items], "clipped": clipped})
            return ClarificationInboxSnapshot(owner_binding, boundary, items, clipped, binding)
        except ClarificationStoreError:
            raise
        except Exception:
            raise ClarificationStoreError("inbox_failed", "inbox capture failed") from None
        finally:
            db.close()

    def read_inbox_action_receipt(
        self, *, principal: SessionPrincipal, clarification_id: Any,
        event_version: Any, expected_current_version: Any,
        expected_receipt_handle: Any,
    ) -> ClarificationInboxActionReceipt | None:
        """Read one SID-bound lifecycle receipt without exposing event payloads.

        The exact event/version lookup is deliberately separate from the
        general event APIs.  It has no fallback parser or historic alias
        lookup, so a malformed private envelope or duplicate row fails closed.
        """
        from core.database import ClarificationEvent, ClarificationRun

        p = _principal(principal)
        if p.authority != "mcp_subject":
            raise ClarificationStoreError("inbox_failed", "inbox principal is unavailable")
        rid = _run_id(clarification_id)
        version = _bounded(event_version, "event_version", 1, 1_000_001)
        current = _bounded(expected_current_version, "expected_current_version", 1, 1_000_001)
        if expected_receipt_handle is not None and (
            type(expected_receipt_handle) is not str
            or not re.fullmatch(r"hir1:c:[0-9a-f]{64}", expected_receipt_handle)
        ):
            raise ClarificationStoreError("inbox_failed", "inbox receipt handle is invalid")
        db = self._session_factory()
        try:
            # Keep scalar ownership/version descriptors and the payload load in
            # one real read transaction.  The payload column is intentionally
            # absent until the exact lifecycle row has passed every descriptor
            # check below.
            with _sqlite_inbox_read_transaction(db) as connection:
                run_rows = db.query(
                ClarificationRun.id, ClarificationRun.owner,
                ClarificationRun.creator_subject_id, ClarificationRun.status,
                ClarificationRun.version,
                ).filter(
                ClarificationRun.id == rid,
                ClarificationRun.creator_subject_id == p.creator_subject_id,
                ).limit(2).all()
                if len(run_rows) != 1:
                    raise ClarificationStoreError("run_not_found", "clarification run not found")
                run = run_rows[0]
                if (
                type(run.creator_subject_id) is not str
                or run.creator_subject_id != p.creator_subject_id
                or type(run.owner) is not str
                or run.owner != p.owner
                or type(run.status) is not str or run.status not in RUN_STATUSES
                or type(run.version) is not int or not 1 <= run.version <= 1_000_001 or run.version != current
                ):
                    raise ClarificationStoreError("inbox_failed", "inbox receipt binding changed")
                rows = db.query(
                ClarificationEvent.id, ClarificationEvent.owner,
                ClarificationEvent.event_type, ClarificationEvent.version,
                ClarificationEvent.created_at,
                ).filter(
                ClarificationEvent.clarification_id == rid,
                ClarificationEvent.version == version,
                ).order_by(ClarificationEvent.id.asc()).limit(2).all()
                if len(rows) > 1:
                    raise ClarificationStoreError("inbox_failed", "inbox receipt is ambiguous")
                if not rows:
                    return None
                event = rows[0]
                if type(event.owner) is not str or event.owner != p.owner:
                    raise ClarificationStoreError("inbox_failed", "inbox event owner binding changed")
                operation_by_type = {
                "run_paused": "pause", "run_reopened": "reopen", "run_cancelled": "cancel",
                }
                operation = operation_by_type.get(event.event_type)
                if operation is None:
                    if type(event.event_type) is not str or event.event_type.startswith("run_"):
                        raise ClarificationStoreError("inbox_failed", "inbox lifecycle event is invalid")
                    if version > current:
                        raise ClarificationStoreError("event_version_out_of_range", "event version is ahead of current run")
                    return None
                if version < 2 or version > current:
                    raise ClarificationStoreError("event_version_out_of_range", "event version is out of range")
                if type(event.id) is not str or _safe_id(event.id) != event.id:
                    raise ClarificationStoreError("inbox_failed", "inbox lifecycle event is invalid")
                if type(event.version) is not int or event.version != version:
                    raise ClarificationStoreError("inbox_failed", "inbox event version is invalid")
                occurred_at = _iso(event.created_at)
                if not occurred_at:
                    raise ClarificationStoreError("inbox_failed", "inbox event is invalid")
                payload_rows = connection.exec_driver_sql(
                    "SELECT payload_json FROM clarification_events "
                    "WHERE id=? AND clarification_id=? AND version=? "
                    "AND typeof(payload_json)='text' "
                    "AND length(CAST(payload_json AS BLOB)) BETWEEN 1 AND 65536",
                    (event.id, rid, version),
                ).fetchall()
                if len(payload_rows) != 1 or type(payload_rows[0][0]) is not str:
                    raise ClarificationStoreError("inbox_failed", "inbox receipt descriptor is invalid")
                raw_payload = payload_rows[0][0]
                payload, envelope_bytes = _receipt_envelope(raw_payload)
                fingerprint = _fingerprint(p, operation, {}, version - 1)
                receipt = payload["receipt"]
                if (
                payload["fingerprint"] != fingerprint
                or not _receipt_matches_event(
                    receipt, rid=rid, event_id=event.id, version=version,
                    operation=operation, event_owner=event.owner, occurred_at=occurred_at,
                    lifecycle_status=payload["lifecycle_status"],
                )
                ):
                    raise ClarificationStoreError("inbox_failed", "inbox receipt verification failed")
                handle = "hir1:c:" + _inbox_digest("receipt-handle", {
                "sid": p.creator_subject_id, "clarification_id": rid,
                "event_id": event.id, "event_version": version, "operation": operation,
                })
                if expected_receipt_handle is not None and expected_receipt_handle != handle:
                    raise ClarificationStoreError("inbox_failed", "inbox receipt binding changed")
                binding = _inbox_digest("receipt-binding", {
                "owner": _inbox_digest("owner", p.creator_subject_id), "clarification_id": rid,
                "event_version": version, "status": run.status, "current_version": current,
                })
                return ClarificationInboxActionReceipt(
                    handle, binding, operation, occurred_at, version,
                    receipt["run"]["status"], envelope_bytes,
                )
        except ClarificationStoreError:
            raise
        except Exception:
            raise ClarificationStoreError("inbox_failed", "inbox receipt failed") from None
        finally:
            db.close()

    def read_mcp_inbox_action_result(
        self, *, principal: SessionPrincipal, clarification_id: Any,
        operation: Any, expected_version: Any, idempotency_key: Any,
    ) -> ClarificationWriteResult | None:
        """Recover one MCP action receipt without mutating the run.

        Recovery is deliberately narrower than ``_mutate``.  It accepts only
        the already-namespaced MCP key and verifies the current SID/owner,
        exact action event, and detached receipt envelope in one SQLite read
        transaction.  A valid owned run with no matching event is the sole
        ``None`` result; every malformed or tampered durable record fails
        closed.
        """
        from core.database import ClarificationEvent, ClarificationRun

        p = _principal(principal)
        if p.authority != "mcp_subject":
            raise ClarificationStoreError("inbox_failed", "inbox principal is unavailable")
        rid = _run_id(clarification_id)
        if type(operation) is not str or operation not in {"pause", "reopen", "cancel"}:
            raise ClarificationStoreError("inbox_failed", "inbox operation is invalid")
        expected = _bounded(expected_version, "expected_version", 1, 1_000_000)
        if type(idempotency_key) is not str or re.fullmatch(
            r"mcp-hi-v1:[0-9a-f]{64}", idempotency_key
        ) is None:
            raise ClarificationStoreError("inbox_failed", "inbox idempotency key is invalid")
        fingerprint = _fingerprint(p, operation, {}, expected)
        db = self._session_factory()
        try:
            with _sqlite_inbox_read_transaction(db):
                run_rows = db.query(
                    ClarificationRun.id, ClarificationRun.owner,
                    ClarificationRun.creator_subject_id, ClarificationRun.status,
                    ClarificationRun.version,
                ).filter(
                    ClarificationRun.id == rid,
                    ClarificationRun.creator_subject_id == p.creator_subject_id,
                ).limit(2).all()
                if len(run_rows) != 1:
                    raise ClarificationStoreError("inbox_failed", "clarification run is unavailable")
                run = run_rows[0]
                if (
                    type(run.creator_subject_id) is not str
                    or run.creator_subject_id != p.creator_subject_id
                    or type(run.owner) is not str
                    or run.owner != p.owner
                    or type(run.status) is not str
                    or run.status not in RUN_STATUSES
                    or type(run.version) is not int
                    or not 1 <= run.version <= 1_000_001
                ):
                    raise ClarificationStoreError("inbox_failed", "inbox run binding is invalid")
                expected_event_type = {
                    "pause": "run_paused", "reopen": "run_reopened", "cancel": "run_cancelled",
                }[operation]
                rows = db.query(
                    ClarificationEvent.id, ClarificationEvent.owner,
                    ClarificationEvent.event_type, ClarificationEvent.version,
                    ClarificationEvent.question_id, ClarificationEvent.idempotency_key,
                    ClarificationEvent.created_at,
                ).filter(
                    ClarificationEvent.clarification_id == rid,
                    ClarificationEvent.idempotency_key == idempotency_key,
                ).order_by(ClarificationEvent.id.asc()).limit(2).all()
                if len(rows) > 1:
                    raise ClarificationStoreError("inbox_failed", "inbox action receipt is ambiguous")
                if not rows:
                    return None
                event = rows[0]
                if (
                    type(event.id) is not str
                    or _safe_id(event.id) != event.id
                    or type(event.owner) is not str
                    or event.owner != p.owner
                    or type(event.event_type) is not str
                    or type(event.version) is not int
                    or type(event.idempotency_key) is not str
                    or event.idempotency_key != idempotency_key
                    or event.question_id is not None
                ):
                    raise ClarificationStoreError("inbox_failed", "inbox action event is invalid")
                if event.event_type != expected_event_type:
                    raise ClarificationStoreError(
                        "idempotency_conflict", "idempotency key was used for a different operation"
                    )
                if event.version != expected + 1 or event.version > run.version:
                    raise ClarificationStoreError(
                        "idempotency_conflict", "idempotency key was used for a different version"
                    )
                if event.version < 2 or event.version > 1_000_001:
                    raise ClarificationStoreError("inbox_failed", "inbox action event version is invalid")
                occurred_at = _iso(event.created_at)
                if not occurred_at:
                    raise ClarificationStoreError("inbox_failed", "inbox action event timestamp is invalid")
                payload_rows = db.connection().exec_driver_sql(
                    "SELECT payload_json FROM clarification_events "
                    "WHERE id=? AND clarification_id=? AND idempotency_key=? "
                    "AND typeof(payload_json)='text' "
                    "AND length(CAST(payload_json AS BLOB)) BETWEEN 1 AND 65536",
                    (event.id, rid, idempotency_key),
                ).fetchall()
                if len(payload_rows) != 1 or type(payload_rows[0][0]) is not str:
                    raise ClarificationStoreError("inbox_failed", "inbox action receipt descriptor is invalid")
                payload, _envelope_bytes = _receipt_envelope(payload_rows[0][0])
                if payload["fingerprint"] != fingerprint:
                    raise ClarificationStoreError(
                        "idempotency_conflict", "idempotency key was used for a different request"
                    )
                receipt = payload["receipt"]
                if not _receipt_matches_event(
                    receipt, rid=rid, event_id=event.id, version=event.version,
                    operation=operation, event_owner=event.owner, occurred_at=occurred_at,
                    lifecycle_status=payload["lifecycle_status"],
                ):
                    raise ClarificationStoreError("inbox_failed", "inbox action receipt verification failed")
                return _detached_replay(receipt)
        except ClarificationStoreError:
            raise
        except Exception:
            raise ClarificationStoreError("inbox_failed", "inbox action recovery failed") from None
        finally:
            db.close()

    def list_active_runs(self, *, principal: SessionPrincipal, limit: Any=25) -> tuple[dict[str,Any],...]:
        from core.database import ClarificationRun
        p,n=_principal(principal),_bounded(limit,"limit",1,100); db=self._session_factory()
        try:
            return tuple(_run(x,p) for x in db.query(ClarificationRun).filter(_scope(ClarificationRun,p),ClarificationRun.status.in_(_OPEN)).order_by(ClarificationRun.updated_at.desc(),ClarificationRun.version.desc()).limit(n).all())
        finally: db.close()

    def answer_question(self, *, principal: SessionPrincipal, clarification_id: Any, question_id: Any, answer: Any, expected_version: Any, idempotency_key: Any) -> ClarificationWriteResult:
        q=_question(question_id); params={"question_id":q,"answer":{"answer":answer}}
        if contains_private_path_material(params): raise ClarificationStoreError("unsafe_content","clarification answer contains private path material")
        return self._mutate(principal,clarification_id,expected_version,idempotency_key,"answer",params,"question_answered")
    def confirm_understanding(self, *, principal: SessionPrincipal, clarification_id: Any, understanding_summary: Any, expected_version: Any, idempotency_key: Any) -> ClarificationWriteResult:
        return self._mutate(principal,clarification_id,expected_version,idempotency_key,"confirm",{"understanding_summary":_text(understanding_summary,"understanding_summary",max_len=1200)},"ready_for_plan")
    def pause_run(self, *, principal: SessionPrincipal, clarification_id: Any, expected_version: Any, idempotency_key: Any) -> ClarificationWriteResult: return self._mutate(principal,clarification_id,expected_version,idempotency_key,"pause",{},"run_paused")
    def reopen_run(self, *, principal: SessionPrincipal, clarification_id: Any, expected_version: Any, idempotency_key: Any) -> ClarificationWriteResult: return self._mutate(principal,clarification_id,expected_version,idempotency_key,"reopen",{},"run_reopened")
    def cancel_run(self, *, principal: SessionPrincipal, clarification_id: Any, expected_version: Any, idempotency_key: Any) -> ClarificationWriteResult: return self._mutate(principal,clarification_id,expected_version,idempotency_key,"cancel",{},"run_cancelled")

    def _mutate(self, principal: SessionPrincipal, clarification_id: Any, expected_version: Any, idempotency_key: Any, operation: str, params: Mapping[str,Any], event_type: str) -> ClarificationWriteResult:
        from core.database import ClarificationEvent, ClarificationRun
        p,rid,key,expected=_principal(principal),_run_id(clarification_id),_ref(idempotency_key,"idempotency_key"),_bounded(expected_version,"expected_version",1,1_000_000); fp=_fingerprint(p,operation,params,expected); db=self._session_factory()
        try:
            run=db.query(ClarificationRun).filter(ClarificationRun.id==rid,_scope(ClarificationRun,p)).first()
            if not run: raise ClarificationStoreError("run_not_found","clarification run not found")
            old=_idem(db,ClarificationEvent,rid,key)
            if old: return _replay(old,fp)
            if run.status in {"cancelled","expired"}: raise ClarificationStoreError("run_closed","clarification run is closed",current_version=int(run.version))
            if int(run.version)!=expected: raise ClarificationStoreError("version_conflict","clarification version conflict",current_version=int(run.version))
            if operation=="answer" and contains_secret_material(params): raise ClarificationStoreError("secure_handoff_required","secret-bearing clarification answers must use secure handoff",current_version=int(run.version),details={"secure_handoff":build_secure_handoff_intent(_run(run,p),question_id=params["question_id"])})
            _reject(params)
            if operation=="answer" and params["question_id"] in _obj(run.answers_json): event_type="answer_revised"
            changes,payload,qid=_changes(run,p,operation,params); changes["version"]=expected+1
            changed=db.query(ClarificationRun).filter(ClarificationRun.id==rid,_scope(ClarificationRun,p),ClarificationRun.version==expected).update(changes,synchronize_session=False)
            if changed!=1:
                db.rollback()
                raced=self._race_receipt(p,rid,key,fp)
                if raced is not None: return raced
                current=self._current_version(p,rid)
                raise ClarificationStoreError("version_conflict" if current is not None else "run_not_found","clarification version conflict" if current is not None else "clarification run not found",current_version=current)
            db.expire_all(); updated=db.query(ClarificationRun).filter(ClarificationRun.id==rid,_scope(ClarificationRun,p)).one()
            event=ClarificationEvent(id=_event_id(rid,expected+1,event_type,qid),clarification_id=rid,owner=p.owner,event_type=event_type,version=expected+1,question_id=qid or None,idempotency_key=key,payload_json=_json(payload)); db.add(event); db.flush()
            result=ClarificationWriteResult(_run(updated,p),_event(event,p)); event.payload_json=_receipt(event.payload_json,fp,result); db.commit(); return result
        except IntegrityError:
            db.rollback()
            raced=self._race_receipt(p,rid,key,fp)
            if raced is not None: return raced
            raise ClarificationStoreError("idempotency_conflict","idempotency key was used for a different request") from None
        except Exception: db.rollback(); raise
        finally: db.close()

    def _race_receipt(self,p: SessionPrincipal,rid: str,key: str,fp: str)->ClarificationWriteResult|None:
        from core.database import ClarificationEvent
        db=self._session_factory()
        try:
            event=_idem(db,ClarificationEvent,rid,key)
            return _replay(event,fp) if event is not None else None
        finally: db.close()

    def _current_version(self,p: SessionPrincipal,rid: str)->int|None:
        from core.database import ClarificationRun
        db=self._session_factory()
        try:
            value=db.query(ClarificationRun.version).filter(ClarificationRun.id==rid,_scope(ClarificationRun,p)).scalar()
            return int(value) if value is not None else None
        finally: db.close()

def _changes(run: Any,p: SessionPrincipal,op: str,params: Mapping[str,Any]) -> tuple[dict[str,Any],dict[str,Any],str]:
    unresolved=list(_list(run.unresolved_required_json))
    if op=="answer":
        qid=str(params["question_id"]); req=_obj(run.request_json); questions={str(x.get("key")):x for x in req.get("questions",()) if isinstance(x,Mapping)}
        if qid not in questions: raise ClarificationStoreError("unknown_question","question_id is not part of this clarification run",current_version=int(run.version))
        answers=_obj(run.answers_json); revised=qid in answers; answers[qid]=dict(params["answer"]); unresolved=[x for x in unresolved if x!=qid]; status="understanding_review" if not unresolved else "clarifying"; preview=_preview(run,p,answers,unresolved,status,False)
        preview["version"] = int(run.version) + 1
        payload={"question_id":qid,**dict(params["answer"]),"privacy_boundary":build_clarification_privacy_boundary(preview,question_id=qid)}; candidate=build_memory_candidate_from_answer(preview,question=questions[qid],question_id=qid,answer=params["answer"])
        if candidate is not None: payload["memory_candidate"]=candidate
        return {"answers_json":_json(answers),"unresolved_required_json":_json(unresolved),"status":status,"ready_for_plan":False},payload,qid
    if op=="confirm":
        if unresolved: raise ClarificationStoreError("required_questions_unresolved","required clarification questions remain",current_version=int(run.version))
        return {"status":"ready_for_plan","ready_for_plan":True,"understanding_summary":params["understanding_summary"]},{"understanding_summary":params["understanding_summary"]},""
    if op=="pause": return {"status":"paused","ready_for_plan":False},{"status":"paused"},""
    if op=="reopen": return {"status":"clarifying" if unresolved else "understanding_review","ready_for_plan":False},{"status":"reopened"},""
    if op=="cancel": return {"status":"cancelled","ready_for_plan":False},{"status":"cancelled"},""
    raise ClarificationStoreError("invalid_event_type","clarification event type is invalid")

def _principal(x: Any) -> SessionPrincipal:
    if type(x) is SessionPrincipal and x.authority=="browser_session" and isinstance(x.owner,str) and x.owner==x.owner.strip().lower() and isinstance(x.creator_subject_id,str) and SUBJECT_ID_PATTERN.fullmatch(x.creator_subject_id): return x
    if type(x) is SessionPrincipal and x.authority=="mcp_subject" and isinstance(x.owner,str) and x.owner==x.owner.strip().lower() and isinstance(x.creator_subject_id,str) and SUBJECT_ID_PATTERN.fullmatch(x.creator_subject_id): return x
    if type(x) is SessionPrincipal and x.authority=="local" and x.owner=="local" and x.creator_subject_id is None: return x
    raise ClarificationStoreError("invalid_principal","a trusted session principal is required")
def _scope(model: Any,p: SessionPrincipal) -> Any: return (model.owner=="local") & model.creator_subject_id.is_(None) if p.authority=="local" else model.creator_subject_id==p.creator_subject_id
def _replay(event: Any,fp: str) -> ClarificationWriteResult:
    box=_obj(event.payload_json).get(_PRIVATE,{})
    if not isinstance(box,Mapping) or box.get("format_version")!=1 or box.get("fingerprint")!=fp: raise ClarificationStoreError("idempotency_conflict","idempotency key was used for a different request")
    receipt=box.get("receipt")
    if not isinstance(receipt,Mapping) or not isinstance(receipt.get("run"),Mapping) or not isinstance(receipt.get("event"),Mapping): raise ClarificationStoreError("idempotency_conflict","idempotency receipt is unavailable")
    return _detached_replay(receipt)
def _detached_replay(receipt: Mapping[str, Any]) -> ClarificationWriteResult:
    if not isinstance(receipt,Mapping) or not isinstance(receipt.get("run"),Mapping) or not isinstance(receipt.get("event"),Mapping):
        raise ClarificationStoreError("inbox_failed","inbox receipt is unavailable")
    run, public_event = dict(receipt["run"]), dict(receipt["event"])
    # JSON persistence turns the public tuple into a list; restore the API
    # contract before returning the original receipt.
    if isinstance(run.get("unresolved_required_question_ids"), list):
        run["unresolved_required_question_ids"] = tuple(run["unresolved_required_question_ids"])
    return ClarificationWriteResult(run,public_event,True)
def _receipt(payload: str,fp: str,result: ClarificationWriteResult) -> str:
    value=_obj(payload); value[_PRIVATE]={"format_version":1,"fingerprint":fp,"receipt":{"run":result.run,"event":result.event}}; return _json(value)


def _receipt_envelope(payload: Any) -> tuple[dict[str, Any], int]:
    """Strict bounded parser used only by the receipt reader.

    The legacy ``_obj`` helper intentionally has forgiving behavior for old
    public APIs.  Receipt provenance needs the opposite contract.
    """
    if type(payload) is not str:
        raise ClarificationStoreError("inbox_failed", "inbox receipt payload is invalid")
    try:
        raw = payload.encode("utf-8", "strict")
    except UnicodeError:
        raise ClarificationStoreError("inbox_failed", "inbox receipt payload is invalid") from None
    if len(raw) > 65_536:
        raise ClarificationStoreError("inbox_failed", "inbox receipt payload is invalid")
    def reject_pairs(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if type(key) is not str or key in value:
                raise ValueError
            value[key] = item
        return value
    try:
        value = json.loads(payload, object_pairs_hook=reject_pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, UnicodeError):
        raise ClarificationStoreError("inbox_failed", "inbox receipt payload is invalid") from None
    if type(value) is not dict or set(value) != {"status", _PRIVATE} or type(value.get(_PRIVATE)) is not dict:
        raise ClarificationStoreError("inbox_failed", "inbox receipt envelope is invalid")
    box = value[_PRIVATE]
    if set(box) != {"format_version", "fingerprint", "receipt"}:
        raise ClarificationStoreError("inbox_failed", "inbox receipt envelope is invalid")
    receipt = box["receipt"]
    if (
        type(box["format_version"]) is not int or box["format_version"] != 1
        or type(box["fingerprint"]) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", box["fingerprint"])
        or type(receipt) is not dict or set(receipt) != {"run", "event"}
        or type(receipt["run"]) is not dict or type(receipt["event"]) is not dict
    ):
        raise ClarificationStoreError("inbox_failed", "inbox receipt envelope is invalid")
    lifecycle_status = value.get("status")
    if type(lifecycle_status) is not str or lifecycle_status not in {"paused", "reopened", "cancelled"}:
        raise ClarificationStoreError("inbox_failed", "inbox receipt lifecycle is invalid")
    return {"fingerprint": box["fingerprint"], "receipt": receipt,
            "lifecycle_status": lifecycle_status}, len(raw)


def _receipt_matches_event(
    receipt: Mapping[str, Any], *, rid: str, event_id: str, version: int,
    operation: str, event_owner: str, occurred_at: str, lifecycle_status: str,
) -> bool:
    """Validate scalar correspondence without returning any private fields."""
    run, event = receipt.get("run"), receipt.get("event")
    expected_type = {"pause": "run_paused", "reopen": "run_reopened", "cancel": "run_cancelled"}[operation]
    return (
        type(run) is dict and type(event) is dict
        and run.get("schema") == CLARIFICATION_RUN_SCHEMA
        and event.get("schema") == CLARIFICATION_EVENT_SCHEMA
        and type(run.get("owner")) is str and bool(run.get("owner"))
        and type(event.get("owner")) is str and bool(event.get("owner"))
        and run.get("owner") == event.get("owner")
        and run.get("clarification_id") == rid and type(run.get("version")) is int and run.get("version") == version
        and type(run.get("status")) is str
        and event.get("clarification_id") == rid and event.get("event_id") == event_id
        and event.get("question_id") in {"", None}
        and event.get("event_type") == expected_type and type(event.get("version")) is int and event.get("version") == version
        and event.get("created_at") == occurred_at
        and event.get("payload") == {"status": lifecycle_status}
        and lifecycle_status == {"pause": "paused", "reopen": "reopened", "cancel": "cancelled"}[operation]
        and (operation != "pause" or run.get("status") == "paused")
        and (operation != "cancel" or run.get("status") == "cancelled")
        and (operation != "reopen" or run.get("status") in {"clarifying", "understanding_review"})
    )
def _fingerprint(p: SessionPrincipal,op: str,params: Mapping[str,Any],expected: int) -> str:
    """Canonical private idempotency input.

    It is deliberately strict about JSON types, but does not pass through the
    public persistence/redaction boundary: a secret answer must reach the
    redacted secure-handoff branch before any public payload validation.
    """
    try:
        value={"sid":p.creator_subject_id,"local":p.authority=="local","operation":op,"params":params,"expected_version":expected}
        # Preserve the accepted browser/local fingerprint byte-for-byte.  MCP
        # adds a channel only for its distinct authority so an intentional key
        # namespace collision fails closed instead of replaying across channels.
        if p.authority=="mcp_subject": value["channel"]="mcp_harbour_inbox_v1"
        canonical=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True)
    except (TypeError,ValueError):
        raise ClarificationStoreError("invalid_json","clarification payload must be JSON") from None
    return hashlib.sha256(canonical.encode()).hexdigest()
def _request(x: Mapping[str,Any]) -> dict[str,Any]:
    if not isinstance(x,Mapping): raise ClarificationStoreError("invalid_request","clarification request must be an object")
    if x.get("schema")!=CLARIFICATION_REQUEST_SCHEMA: raise ClarificationStoreError("invalid_schema","clarification request schema is invalid")
    scope=str(x.get("scope") or "").strip()
    if scope not in _SCOPES: raise ClarificationStoreError("invalid_scope","clarification scope is invalid")
    batch=x.get("batch") if isinstance(x.get("batch"),Mapping) else {}; value={"schema":CLARIFICATION_REQUEST_SCHEMA,"scope":scope,"intent_summary":_text(x.get("intent_summary"),"intent_summary",max_len=1000),"questions":tuple(_normalize_question(q) for q in x.get("questions") or ()),"batch":{"label":_text(batch.get("label") or "Clarification","batch.label",max_len=120),"index":_bounded(batch.get("index"),"batch.index",1,1000),"total":_bounded(batch.get("total"),"batch.total",1,1000),"max_visible_questions":_bounded(batch.get("max_visible_questions"),"batch.max_visible_questions",1,10)},"defaults_visible":bool(x.get("defaults_visible"))}; _reject(value); return value
def _normalize_question(x: Any) -> dict[str,Any]:
    if not isinstance(x,Mapping): raise ClarificationStoreError("invalid_question","question must be an object")
    kind=str(x.get("type") or "").strip()
    if kind not in _TYPES: raise ClarificationStoreError("invalid_question_type","question type is invalid")
    v={"key":_question(x.get("key")),"type":kind,"prompt":_text(x.get("prompt"),"question.prompt",max_len=1000),"required":bool(x.get("required")),"reason":_text(x.get("reason"),"question.reason",max_len=500),"category":_text(x.get("category") or "","question.category",max_len=80,empty=True)}
    if x.get("options") is not None: v["options"]=tuple(_option(q) for q in x.get("options") or ())[:20]
    if x.get("default") is not None: _reject({"value":x["default"]}); v["default"]=x["default"]
    if x.get("depends_on"): v["depends_on"]=_question(x["depends_on"])
    return v
def _option(x: Any) -> dict[str,Any]:
    if not isinstance(x,Mapping): raise ClarificationStoreError("invalid_option","question option must be an object")
    v={"label":_text(x.get("label"),"option.label",max_len=80)}; d=_text(x.get("description") or "","option.description",max_len=240,empty=True)
    if d:v["description"]=d
    if x.get("recommended") is not None:v["recommended"]=bool(x["recommended"])
    return v
def _run(row: Any,p: SessionPrincipal) -> dict[str,Any]: return _preview(row,p,_obj(row.answers_json),_list(row.unresolved_required_json),row.status,bool(row.ready_for_plan),True)
def _preview(row: Any,p: SessionPrincipal,answers: Mapping[str,Any],unresolved: Any,status: str,ready: bool,content: bool=False) -> dict[str,Any]:
    if status not in RUN_STATUSES: raise ClarificationStoreError("invalid_status","clarification status is invalid")
    v={"schema":CLARIFICATION_RUN_SCHEMA,"clarification_id":row.id,"owner":p.owner,"session_id":row.session_id,"scope":row.scope,"status":status,"version":int(row.version or 0),"project_slug":row.project_slug or "","coding_task_id":row.coding_task_id or "","intent_summary":row.intent_summary or "","answers":dict(answers),"unresolved_required_question_ids":tuple(unresolved),"unresolved_required_count":len(unresolved),"understanding_summary":row.understanding_summary or "","ready_for_plan":ready,"created_at":_iso(row.created_at),"updated_at":_iso(row.updated_at),"raw_content_visible":False}
    if content:v["request"]=_obj(row.request_json)
    _reject(v); return v
def _event(row: Any,p: SessionPrincipal) -> dict[str,Any]:
    payload=_obj(row.payload_json);payload.pop(_PRIVATE,None);v={"schema":CLARIFICATION_EVENT_SCHEMA,"event_id":row.id,"clarification_id":row.clarification_id,"owner":p.owner,"event_type":row.event_type,"version":int(row.version or 0),"question_id":row.question_id or "","payload":payload,"created_at":_iso(row.created_at),"raw_content_visible":False};_reject(v);return v
def _text(x: Any,field: str,*,max_len:int,empty:bool=False)->str:
    v=" ".join(str(x or "").strip().split())
    if not v and not empty:raise ClarificationStoreError("invalid_"+field,field+" must not be empty")
    if len(v)>max_len:raise ClarificationStoreError(field+"_too_long",field+" is too long")
    if _SECRET.search(v) or _PATH.search(v):raise ClarificationStoreError("unsafe_content",field+" contains unsafe content")
    return v
def _ref(x: Any,field: str)->str:
    v=_text(x,field,max_len=180)
    return v if re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,180}",v) else field+"-"+hashlib.sha256(v.encode()).hexdigest()[:16]
def _optional(x:Any)->str:return _ref(x,"ref") if str(x or "").strip() else ""
def _question(x:Any)->str:
    v=re.sub(r"[^a-z0-9_.-]+", "_", str(x or "").strip().lower().replace(" ", "_")).strip("_.-")
    if not _KEY.fullmatch(v):raise ClarificationStoreError("invalid_question_id","question key/id is invalid")
    return v
def _safe_id(x:Any)->str:
    if type(x) is not str:
        return ""
    v=x.strip();return v if re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}",v) else ""
def _run_id(x:Any)->str:
    v=_safe_id(x)
    if not v:raise ClarificationStoreError("invalid_clarification_id","clarification_id is invalid")
    return v
def _unresolved(request:Mapping[str,Any])->tuple[str,...]:return tuple(str(x["key"]) for x in request.get("questions") or () if x.get("required"))
def _bounded(x:Any,field:str,lo:int,hi:int)->int:
    if type(x) is not int:
        raise ClarificationStoreError("invalid_"+field,field+" must be an integer")
    v=x
    if not lo<=v<=hi:raise ClarificationStoreError(field+"_out_of_range",field+" is out of range")
    return v
def _json(x:Any)->str:
    _reject(x)
    try:return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    except (TypeError,ValueError):raise ClarificationStoreError("invalid_json","clarification payload must be JSON") from None
def _obj(x:Any)->dict[str,Any]:
    try:v=json.loads(x or "{}")
    except (TypeError,ValueError):v={}
    return dict(v) if isinstance(v,Mapping) else {}
def _list(x:Any)->tuple[str,...]:
    try:v=json.loads(x or "[]")
    except (TypeError,ValueError):v=[]
    return tuple(str(i) for i in v) if isinstance(v,list) else ()
def _idem(db:Any,model:Any,rid:str,key:str)->Any|None:return db.query(model).filter(model.clarification_id==rid,model.idempotency_key==key).first()
def _event_id(rid:str,v:int,kind:str,qid:str="")->str:return "evt-"+hashlib.sha256(f"{rid}:{v}:{kind}:{qid}:{uuid.uuid4().hex}".encode()).hexdigest()[:24]
def _now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
def _iso(x:Any)->str:return x.replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z") if isinstance(x,datetime) else ""
def _inbox_metadata(*,clarification_id:Any,status:Any,version:Any,ready_for_plan:Any,authority:str,event_receipt:bool,updated_at:Any)->dict[str,Any]:
    if authority not in {"browser_session","mcp_subject","local"}: raise ClarificationStoreError("invalid_principal","a trusted session principal is required")
    state=str(status or "")
    if state not in RUN_STATUSES: raise ClarificationStoreError("invalid_status","clarification status is invalid")
    return {"clarification_id":_run_id(clarification_id),"source":"clarification_store","owner":"self","status":state,"version":_bounded(version,"version",1,1_000_001),"ready_for_plan":bool(ready_for_plan),"required_action":_INBOX_REQUIRED_ACTION[state],"due":None,"authority":authority,"event_receipt_present":bool(event_receipt),"freshness":"current","updated_at":_iso(updated_at),"redaction_flags":{"content":True,"identity":True,"events":True}}
def _reject(x:Any)->None:
    if contains_secret_material(x):raise ClarificationStoreError("unsafe_content","clarification payload contains secret material")
    if contains_private_path_material(x):raise ClarificationStoreError("unsafe_content","clarification payload contains private path material")
