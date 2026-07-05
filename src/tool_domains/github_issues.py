"""GitHub issue intelligence tool domain.

The tool surface is intentionally conservative: local duplicate search can run
from already-synced issue records, while provider sync and write-like actions
return explicit gates until future live adapters are approved.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from core.database import SessionLocal
from src.github_issue_duplicates import GitHubIssueDraft, find_duplicate_candidates, record_duplicate_candidates
from src.github_issue_fields import build_issue_field_projection, projection_to_write_report, validate_issue_fields
from src.github_issue_index import InMemoryGitHubIssueIndexBackend, reindex_github_issues
from src.github_issue_live_client import GitHubRestIssueReadClient
from src.github_issue_sync import GitHubIssueSyncError, sync_github_issues
from src.tool_domains.common import _parse_tool_args


_WRITE_ACTIONS = {"create_triaged", "set_fields"}


def build_github_issue_sync_readiness(repository: str) -> Dict[str, Any]:
    token_present = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    allow_public = _env_bool("GITHUB_ISSUE_SYNC_ALLOW_PUBLIC_UNAUTHENTICATED")
    if not _env_bool("GITHUB_ISSUE_SYNC_LIVE_ENABLED"):
        return _sync_gate(repository=repository, reason="server_live_sync_disabled")
    if not _repository_allowed(repository):
        return _sync_gate(repository=repository, reason="repository_not_allowlisted")
    if not token_present and not allow_public:
        return _sync_gate(repository=repository, reason="server_auth_not_ready")
    return {
        "status": "ready_for_confirmed_sync",
        "repository": repository,
        "requires_confirmation": True,
        "requires_live_go": True,
        "auth_ready": True,
        "auth_mode": "server_token" if token_present else "public_unauthenticated",
        "max_items": _bounded_int(os.environ.get("GITHUB_ISSUE_SYNC_MAX_ITEMS"), default=50, minimum=1, maximum=500),
        "provider_writes_performed": 0,
        "next_action": "Call sync with confirmed=true and a bounded max_items value. Never pass provider tokens in chat.",
        "exit_code": 0,
    }


async def do_manage_github_issues(content: str, owner: Optional[str] = None) -> Dict[str, Any]:
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action") or "").strip().lower()
    if action not in {"sync", "duplicate_search", "create_triaged", "set_fields"}:
        return {
            "error": "action must be one of: sync, duplicate_search, create_triaged, set_fields",
            "exit_code": 1,
        }

    caller_owner = str(owner or args.get("owner") or "default").strip() or "default"
    repository = _required_arg(args, "repository")
    if not repository:
        return {"error": "repository is required", "exit_code": 1}

    if action == "sync":
        return _sync(args, owner=caller_owner, repository=repository)
    if action == "duplicate_search":
        return _duplicate_search(args, owner=caller_owner, repository=repository)
    if action == "set_fields":
        return _set_fields(args, owner=caller_owner, repository=repository)
    return _create_triaged(args, owner=caller_owner, repository=repository)


def _sync_gate(*, repository: str, reason: str = "") -> Dict[str, Any]:
    return {
        "status": "needs_live_go",
        "requires_confirmation": True,
        "requires_live_go": True,
        "repository": repository,
        "reason": reason or "live_read_sync_not_confirmed_or_not_enabled",
        "next_action": (
            "Approve a bounded GitHub read-only sync with server-side credentials or "
            "an explicit public unauthenticated read gate. This tool will not accept "
            "provider tokens in chat."
        ),
        "exit_code": 0,
    }


def _sync(args: dict[str, Any], *, owner: str, repository: str) -> Dict[str, Any]:
    if not _confirmed(args):
        return _sync_gate(repository=repository, reason="confirmation_required")
    readiness = build_github_issue_sync_readiness(repository)
    if readiness.get("status") != "ready_for_confirmed_sync":
        return readiness

    max_items = _bounded_int(
        args.get("max_items") or args.get("limit") or os.environ.get("GITHUB_ISSUE_SYNC_MAX_ITEMS"),
        default=50,
        minimum=1,
        maximum=500,
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    allow_public = _env_bool("GITHUB_ISSUE_SYNC_ALLOW_PUBLIC_UNAUTHENTICATED")
    try:
        client = _make_live_read_client(token=token, allow_public=allow_public, max_items=max_items)
        with SessionLocal() as db:
            result = sync_github_issues(
                db,
                owner=owner,
                repository=repository,
                client=client,
            )
    except GitHubIssueSyncError as exc:
        return {
            "status": "blocked",
            "requires_confirmation": True,
            "requires_live_go": True,
            "repository": repository,
            "error": str(exc),
            "exit_code": 1,
        }

    return {
        "status": "synced",
        "repository": repository,
        "owner": owner,
        "sync": result.to_dict(),
        "auth_mode": getattr(client, "auth_mode", "server_token" if bool(token) else "public_unauthenticated"),
        "max_items": max_items,
        "provider_writes_performed": 0,
        "exit_code": 0,
    }


def _duplicate_search(args: dict[str, Any], *, owner: str, repository: str) -> Dict[str, Any]:
    draft = _draft_from_args(args)
    include_closed = bool(args.get("include_closed", True))
    top_k = int(args.get("top_k") or 3)
    source_issue_id = str(args.get("source_issue_id") or "").strip()
    persist = bool(args.get("persist") or args.get("record"))
    with SessionLocal() as db:
        backend = InMemoryGitHubIssueIndexBackend()
        reindex = reindex_github_issues(
            db,
            backend,
            owner=owner,
            repository=repository,
            include_closed=include_closed,
        )
        report = find_duplicate_candidates(
            backend,
            draft,
            owner=owner,
            repository=repository,
            top_k=top_k,
            include_closed=include_closed,
        )
        recorded = 0
        if persist and source_issue_id:
            recorded = record_duplicate_candidates(
                db,
                owner=owner,
                repository=repository,
                source_issue_id=source_issue_id,
                report=report,
            )
    return {
        "github_issue_duplicates": report.to_dict(),
        "index": reindex.to_dict(),
        "recorded_candidates": recorded,
        "exit_code": 0,
    }


def _set_fields(args: dict[str, Any], *, owner: str, repository: str) -> Dict[str, Any]:
    issue_ref = str(args.get("issue_ref") or args.get("external_id") or "").strip()
    if not issue_ref:
        return {"error": "issue_ref or external_id is required for set_fields", "exit_code": 1}
    fields = _fields_from_args(args)
    try:
        normalized = validate_issue_fields(fields)
        projections = build_issue_field_projection(normalized)
    except Exception as exc:
        return {"error": str(exc), "exit_code": 1}
    report = projection_to_write_report(projections)
    if not _confirmed(args):
        return {
            "status": "confirmation_required",
            "requires_confirmation": True,
            "owner": owner,
            "repository": repository,
            "issue_ref": issue_ref,
            "fields": normalized,
            "write_report": report,
            "exit_code": 0,
        }
    return _live_write_gate(
        action="set_fields",
        owner=owner,
        repository=repository,
        issue_ref=issue_ref,
        payload={"fields": normalized, "write_report": report},
    )


def _create_triaged(args: dict[str, Any], *, owner: str, repository: str) -> Dict[str, Any]:
    draft = _draft_from_args(args)
    fields = _fields_from_args(args)
    try:
        normalized_fields = validate_issue_fields(fields) if fields else {}
    except Exception as exc:
        return {"error": str(exc), "exit_code": 1}
    duplicate_result = _duplicate_search(
        {
            "repository": repository,
            "title": draft.title,
            "body": draft.body,
            "labels": list(draft.labels),
            "top_k": args.get("top_k", 3),
            "include_closed": args.get("include_closed", True),
        },
        owner=owner,
        repository=repository,
    )
    duplicate_report = duplicate_result["github_issue_duplicates"]
    if duplicate_report.get("blocks_auto_create") and not bool(args.get("duplicate_confirmed")):
        return {
            "status": "blocked_by_duplicate_candidate",
            "requires_confirmation": True,
            "owner": owner,
            "repository": repository,
            "draft": draft_to_dict(draft),
            "fields": normalized_fields,
            "github_issue_duplicates": duplicate_report,
            "exit_code": 0,
        }
    if not _confirmed(args):
        return {
            "status": "confirmation_required",
            "requires_confirmation": True,
            "owner": owner,
            "repository": repository,
            "draft": draft_to_dict(draft),
            "fields": normalized_fields,
            "github_issue_duplicates": duplicate_report,
            "exit_code": 0,
        }
    return _live_write_gate(
        action="create_triaged",
        owner=owner,
        repository=repository,
        issue_ref="new",
        payload={
            "draft": draft_to_dict(draft),
            "fields": normalized_fields,
            "github_issue_duplicates": duplicate_report,
        },
    )


def draft_to_dict(draft: GitHubIssueDraft) -> dict[str, Any]:
    return {
        "title": draft.title,
        "body_chars": len(draft.body),
        "labels": list(draft.labels),
        "external_id": draft.external_id,
    }


def _live_write_gate(
    *,
    action: str,
    owner: str,
    repository: str,
    issue_ref: str,
    payload: dict[str, Any],
) -> Dict[str, Any]:
    return {
        "status": "needs_live_go",
        "requires_confirmation": True,
        "requires_live_go": True,
        "action": action,
        "owner": owner,
        "repository": repository,
        "issue_ref": issue_ref,
        **payload,
        "next_action": "Provider write adapter is gated; require operator_go, live_enabled, auth_ready and bounded repository scope.",
        "exit_code": 0,
    }


def _draft_from_args(args: dict[str, Any]) -> GitHubIssueDraft:
    title = str(args.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    labels = args.get("labels") or ()
    if isinstance(labels, str):
        labels = [item.strip() for item in labels.split(",")]
    return GitHubIssueDraft(
        title=title,
        body=str(args.get("body") or ""),
        labels=tuple(str(label).strip() for label in labels if str(label).strip()),
        external_id=str(args.get("external_id") or "").strip(),
    )


def _fields_from_args(args: dict[str, Any]) -> dict[str, Any]:
    fields = args.get("fields")
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError:
            fields = {}
    if isinstance(fields, dict):
        return fields
    result: dict[str, Any] = {}
    for key in ("type", "priority", "effort", "area", "status", "start_date", "target_date", "duplicate_of"):
        if args.get(key) not in (None, ""):
            result[key] = args[key]
    return result


def _required_arg(args: dict[str, Any], name: str) -> str:
    return str(args.get(name) or "").strip()


def _confirmed(args: dict[str, Any]) -> bool:
    return bool(args.get("confirmed") or args.get("confirm"))


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _repository_allowed(repository: str) -> bool:
    raw = str(os.environ.get("GITHUB_ISSUE_SYNC_ALLOWED_REPOSITORIES") or "").strip()
    if not raw:
        return False
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    return "*" in allowed or repository in allowed


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _make_live_read_client(*, token: str, allow_public: bool, max_items: int) -> GitHubRestIssueReadClient:
    return GitHubRestIssueReadClient(
        token=token,
        allow_unauthenticated_public=allow_public,
        max_items=max_items,
    )
