"""Backend route contracts for GitHub Issue Intelligence."""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import GitHubIssueRecord, SessionLocal
from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.github_issue_duplicates import GitHubIssueDraft, find_duplicate_candidates
from src.github_issue_fields import build_issue_field_projection, projection_to_write_report, validate_issue_fields
from src.github_issue_index import InMemoryGitHubIssueIndexBackend, reindex_github_issues
from src.tool_domains.github_issues import build_github_issue_sync_readiness, do_manage_github_issues


SessionFactory = Callable[[], Session]
AdminGuard = Callable[[Request], Any]


class GitHubIssueDuplicateRequest(BaseModel):
    repository: str = Field(min_length=1, max_length=260)
    title: str = Field(min_length=1, max_length=500)
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    external_id: str = ""
    top_k: int = 3
    include_closed: bool = True


class GitHubIssueSyncRequest(BaseModel):
    repository: str = Field(min_length=1, max_length=260)
    max_items: int = Field(default=50, ge=1, le=500)
    confirmed: bool = False


class GitHubIssueWritePlanRequest(BaseModel):
    action: str = Field(pattern="^(create_triaged|set_fields)$")
    repository: str = Field(min_length=1, max_length=260)
    title: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    external_id: str = ""
    issue_ref: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    top_k: int = 3
    include_closed: bool = True
    duplicate_confirmed: bool = False
    confirmed: bool = False


def setup_github_issue_routes(
    *,
    session_factory: SessionFactory = SessionLocal,
    require_admin_fn: AdminGuard = require_admin,
) -> APIRouter:
    router = APIRouter(prefix="/api/github-issues", tags=["github-issues"])

    @router.get("/readiness")
    def github_issue_readiness(request: Request, repository: str) -> dict[str, Any]:
        require_admin_fn(request)
        owner = _owner(request)
        with session_factory() as db:
            total = (
                db.query(GitHubIssueRecord)
                .filter(GitHubIssueRecord.owner == owner, GitHubIssueRecord.repository == repository)
                .count()
            )
            open_count = (
                db.query(GitHubIssueRecord)
                .filter(
                    GitHubIssueRecord.owner == owner,
                    GitHubIssueRecord.repository == repository,
                    GitHubIssueRecord.state != "closed",
                )
                .count()
            )
        return {
            "repository": repository,
            "owner": owner,
            "local_issue_count": total,
            "local_open_issue_count": open_count,
            "sync": build_github_issue_sync_readiness(repository),
            "writes": _write_gate(repository),
        }

    @router.post("/sync")
    async def github_issue_sync(request: Request, body: GitHubIssueSyncRequest) -> dict[str, Any]:
        require_admin_fn(request)
        owner = _owner(request)
        return await do_manage_github_issues(
            json.dumps(
                {
                    "action": "sync",
                    "repository": body.repository,
                    "max_items": body.max_items,
                    "confirmed": body.confirmed,
                }
            ),
            owner=owner,
        )

    @router.post("/duplicates")
    def github_issue_duplicates(request: Request, body: GitHubIssueDuplicateRequest) -> dict[str, Any]:
        require_admin_fn(request)
        owner = _owner(request)
        with session_factory() as db:
            backend = InMemoryGitHubIssueIndexBackend()
            reindex = reindex_github_issues(
                db,
                backend,
                owner=owner,
                repository=body.repository,
                include_closed=body.include_closed,
            )
            report = find_duplicate_candidates(
                backend,
                GitHubIssueDraft(
                    title=body.title,
                    body=body.body,
                    labels=tuple(body.labels),
                    external_id=body.external_id,
                ),
                owner=owner,
                repository=body.repository,
                top_k=body.top_k,
                include_closed=body.include_closed,
            )
        return {
            "success": True,
            "index": reindex.to_dict(),
            "github_issue_duplicates": report.to_dict(),
        }

    @router.post("/write-plan")
    def github_issue_write_plan(request: Request, body: GitHubIssueWritePlanRequest) -> dict[str, Any]:
        require_admin_fn(request)
        owner = _owner(request)
        try:
            fields = validate_issue_fields(body.fields) if body.fields else {}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if body.action == "set_fields":
            if not body.issue_ref and not body.external_id:
                raise HTTPException(status_code=400, detail="issue_ref or external_id is required")
            projections = build_issue_field_projection(fields) if fields else ()
            status = "needs_live_go" if body.confirmed else "confirmation_required"
            return {
                "success": False,
                "status": status,
                "requires_confirmation": True,
                "requires_live_go": bool(body.confirmed),
                "owner": owner,
                "repository": body.repository,
                "issue_ref": body.issue_ref or body.external_id,
                "fields": fields,
                "write_report": projection_to_write_report(projections),
                "next_action": _write_next_action(),
            }

        with session_factory() as db:
            backend = InMemoryGitHubIssueIndexBackend()
            reindex = reindex_github_issues(
                db,
                backend,
                owner=owner,
                repository=body.repository,
                include_closed=body.include_closed,
            )
            duplicate_report = find_duplicate_candidates(
                backend,
                GitHubIssueDraft(
                    title=body.title,
                    body=body.body,
                    labels=tuple(body.labels),
                    external_id=body.external_id,
                ),
                owner=owner,
                repository=body.repository,
                top_k=body.top_k,
                include_closed=body.include_closed,
            )
        duplicate_payload = duplicate_report.to_dict()
        if duplicate_report.blocks_auto_create and not body.duplicate_confirmed:
            return {
                "success": False,
                "status": "blocked_by_duplicate_candidate",
                "requires_confirmation": True,
                "requires_live_go": False,
                "owner": owner,
                "repository": body.repository,
                "fields": fields,
                "index": reindex.to_dict(),
                "github_issue_duplicates": duplicate_payload,
            }
        status = "needs_live_go" if body.confirmed else "confirmation_required"
        return {
            "success": False,
            "status": status,
            "requires_confirmation": True,
            "requires_live_go": bool(body.confirmed),
            "owner": owner,
            "repository": body.repository,
            "fields": fields,
            "index": reindex.to_dict(),
            "github_issue_duplicates": duplicate_payload,
            "next_action": _write_next_action(),
        }

    return router


def _owner(request: Request) -> str:
    return get_current_user(request) or "default"


def _write_gate(repository: str) -> dict[str, Any]:
    return {
        "status": "needs_live_go",
        "repository": repository,
        "requires_live_go": True,
        "requires_confirmation": True,
        "next_action": _write_next_action(),
    }


def _write_next_action() -> str:
    return "Provider writes need confirmed=true, operator_go, live_enabled, auth_ready and bounded repository scope."
