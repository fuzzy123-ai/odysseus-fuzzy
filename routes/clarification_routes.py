"""Owner-scoped API routes for durable clarification runs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth_helpers import scoped_effective_user
from src.clarification_store import ClarificationStore, ClarificationStoreError


class ClarificationCreateRequest(BaseModel):
    session_id: str = Field(default="", max_length=180)
    request: dict[str, Any]
    project_slug: str = Field(default="", max_length=180)
    coding_task_id: str = Field(default="", max_length=180)
    clarification_id: str = Field(default="", max_length=120)


class ClarificationSessionCreateRequest(BaseModel):
    request: dict[str, Any]
    project_slug: str = Field(default="", max_length=180)
    coding_task_id: str = Field(default="", max_length=180)
    clarification_id: str = Field(default="", max_length=120)


class ClarificationAnswerRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=120)
    answer: Any
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=180)


class ClarificationActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=180)
    understanding_summary: str = Field(default="", max_length=1200)


def setup_clarification_routes(*, store: ClarificationStore | None = None) -> APIRouter:
    router = APIRouter(tags=["clarifications"])
    clarification_store = store or ClarificationStore()

    @router.post("/api/clarifications")
    def create_clarification(request: Request, body: ClarificationCreateRequest) -> dict[str, Any]:
        owner = _owner(request)
        if not body.session_id.strip():
            raise HTTPException(status_code=400, detail="session_id is required")
        try:
            result = clarification_store.create_run(
                owner=owner,
                session_id=body.session_id,
                request=body.request,
                project_slug=body.project_slug,
                coding_task_id=body.coding_task_id,
                clarification_id=body.clarification_id,
            )
        except ClarificationStoreError as exc:
            _raise_store_error(exc)
        return {"success": True, **result.to_dict()}

    @router.post("/api/sessions/{session_id}/clarification")
    def create_session_clarification(
        request: Request,
        session_id: str,
        body: ClarificationSessionCreateRequest,
    ) -> dict[str, Any]:
        owner = _owner(request)
        try:
            result = clarification_store.create_run(
                owner=owner,
                session_id=session_id,
                request=body.request,
                project_slug=body.project_slug,
                coding_task_id=body.coding_task_id,
                clarification_id=body.clarification_id,
            )
        except ClarificationStoreError as exc:
            _raise_store_error(exc)
        return {"success": True, **result.to_dict()}

    @router.get("/api/sessions/{session_id}/clarification")
    def get_session_clarification(request: Request, session_id: str) -> dict[str, Any]:
        owner = _owner(request)
        try:
            run = clarification_store.read_active_run_for_session(owner=owner, session_id=session_id)
        except ClarificationStoreError as exc:
            _raise_store_error(exc)
        return {
            "success": True,
            "active": run is not None,
            "clarification": run,
            "raw_content_visible": False,
        }

    @router.get("/api/clarifications/{clarification_id}")
    def get_clarification(request: Request, clarification_id: str) -> dict[str, Any]:
        owner = _owner(request)
        try:
            run = clarification_store.read_run(owner=owner, clarification_id=clarification_id)
        except ClarificationStoreError as exc:
            _raise_store_error(exc)
        if run is None:
            raise HTTPException(status_code=404, detail="clarification run not found")
        return {"success": True, "clarification": run, "raw_content_visible": False}

    @router.get("/api/clarifications/{clarification_id}/events")
    def get_clarification_events(request: Request, clarification_id: str) -> dict[str, Any]:
        owner = _owner(request)
        try:
            events = clarification_store.read_events(owner=owner, clarification_id=clarification_id)
        except ClarificationStoreError as exc:
            _raise_store_error(exc)
        if not clarification_store.read_run(owner=owner, clarification_id=clarification_id):
            raise HTTPException(status_code=404, detail="clarification run not found")
        return {"success": True, "events": list(events), "raw_content_visible": False}

    @router.post("/api/clarifications/{clarification_id}/answers")
    def answer_clarification(
        request: Request,
        clarification_id: str,
        body: ClarificationAnswerRequest,
    ) -> dict[str, Any]:
        owner = _owner(request)
        try:
            result = clarification_store.answer_question(
                owner=owner,
                clarification_id=clarification_id,
                question_id=body.question_id,
                answer=body.answer,
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
            )
        except ClarificationStoreError as exc:
            _raise_store_error(exc)
        return {"success": True, **result.to_dict()}

    @router.post("/api/clarifications/{clarification_id}/actions")
    def act_on_clarification(
        request: Request,
        clarification_id: str,
        body: ClarificationActionRequest,
    ) -> dict[str, Any]:
        owner = _owner(request)
        action = body.action.strip().lower()
        try:
            if action == "pause":
                result = clarification_store.pause_run(
                    owner=owner,
                    clarification_id=clarification_id,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key,
                )
            elif action == "reopen":
                result = clarification_store.reopen_run(
                    owner=owner,
                    clarification_id=clarification_id,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key,
                )
            elif action == "cancel":
                result = clarification_store.cancel_run(
                    owner=owner,
                    clarification_id=clarification_id,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key,
                )
            elif action in {"complete", "confirm_understanding"}:
                result = clarification_store.confirm_understanding(
                    owner=owner,
                    clarification_id=clarification_id,
                    understanding_summary=body.understanding_summary,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key,
                )
            else:
                raise HTTPException(status_code=400, detail="unsupported clarification action")
        except ClarificationStoreError as exc:
            _raise_store_error(exc)
        return {"success": True, **result.to_dict()}

    return router


def _owner(request: Request) -> str:
    return scoped_effective_user(request, "chat") or "local"


def _raise_store_error(exc: ClarificationStoreError) -> None:
    detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
    if exc.current_version is not None:
        detail["current_version"] = exc.current_version
    if exc.details:
        detail.update(exc.details)
    status = 400
    if exc.code in {"run_not_found"}:
        status = 404
    elif exc.code in {"version_conflict", "run_exists", "required_questions_unresolved", "run_closed", "secure_handoff_required"}:
        status = 409
    raise HTTPException(status_code=status, detail=detail)
