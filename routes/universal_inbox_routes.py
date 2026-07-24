"""Browser-safe Universal Inbox status routes."""

from __future__ import annotations

import os
import json
import math
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from core.database import SessionLocal
from routes.document_helpers import WorkingCopyCreate, _doc_to_dict
from src.auth_helpers import require_privilege
from src.auth_helpers import effective_user
from src.universal_inbox_file_types import classify_universal_inbox_file
from src.universal_inbox_flow_state import build_universal_inbox_flow_state
from src.universal_inbox_items import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    UniversalInboxAuthenticationRequired,
    UniversalInboxBrowseError,
    UniversalInboxIndexUnavailable,
    UniversalInboxOwnerScopeDenied,
    UploadHandlerMetadataSource,
    browse_universal_inbox_items,
    load_owner_scoped_universal_inbox_items,
    resolve_universal_inbox_owner_scope,
)
from src.universal_inbox_source_access import (
    UniversalInboxSourceAccessError,
    read_selected_universal_inbox_source,
)
from src.universal_inbox_workspace_snapshot import (
    build_universal_inbox_workspace_snapshot,
)
from src.universal_inbox_working_copy import (
    UniversalInboxWorkingCopyError,
    create_or_get_universal_inbox_working_copy,
)
from src.universal_inbox_workbench import (
    WorkbenchAction,
    WorkbenchActionState,
    build_universal_inbox_workbench_capability,
)
from src.universal_inbox_routing import plan_universal_inbox_route
from src.universal_inbox_placement import build_universal_inbox_placement_plan


ROUTE_DRY_RUN_SCHEMA = "odysseus.universal_inbox.route_dry_run.v1"
_ROUTE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROUTE_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}(?:\.[a-z0-9]{1,12})?$", re.I)
_ROUTE_DRY_RUN_MAX_BYTES = 1024
_ROUTE_RISK_SIGNALS = frozenset(
    {"duplicate", "partial_extraction", "secret_detected", "sensitive", "target_conflict"}
)


class _RouteDryRunError(ValueError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code
        super().__init__(code)


def setup_universal_inbox_routes(upload_handler: Any = None) -> APIRouter:
    router = APIRouter(prefix="/api/universal-inbox", tags=["universal-inbox"])

    @router.get("/items")
    async def browse_inbox_items(
        request: Request,
        limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
        cursor: str | None = None,
        owner: str | None = None,
    ):
        auth_manager = getattr(request.app.state, "auth_manager", None)
        try:
            return browse_universal_inbox_items(
                upload_handler,
                caller_owner=effective_user(request),
                auth_manager=auth_manager,
                requested_owner=owner,
                limit=limit,
                cursor=cursor,
            )
        except Exception as exc:
            _raise_browse_http_error(exc)

    @router.get("/snapshot")
    async def get_inbox_snapshot(
        request: Request,
        owner: str | None = None,
    ):
        auth_manager = getattr(request.app.state, "auth_manager", None)
        try:
            target_owner, admin_override = resolve_universal_inbox_owner_scope(
                caller_owner=effective_user(request),
                auth_manager=auth_manager,
                requested_owner=owner,
            )
            items = load_owner_scoped_universal_inbox_items(
                UploadHandlerMetadataSource(upload_handler),
                target_owner=target_owner,
            )
            return build_universal_inbox_workspace_snapshot(
                items,
                admin_override=admin_override,
            )
        except Exception as exc:
            _raise_browse_http_error(exc)

    @router.get("/items/{source_ref:path}/status")
    async def get_universal_inbox_item_status(request: Request, source_ref: str):
        return _resolve_redacted_upload_status(request, source_ref, upload_handler)

    @router.post("/items/{source_ref:path}/route-dry-run")
    async def route_universal_inbox_item_dry_run(
        request: Request,
        source_ref: str,
    ):
        """Plan a bounded route without reading content or changing any state."""

        try:
            route_request = await _parse_route_dry_run_request(request)
            info, _ = _resolve_route_dry_run_upload(
                request,
                source_ref,
                upload_handler,
            )
            return JSONResponse(
                content=_build_route_dry_run_projection(
                    info,
                    route_request=route_request,
                ),
                headers=_route_dry_run_headers(),
            )
        except _RouteDryRunError as exc:
            return _route_dry_run_error(exc.status_code, exc.code)
        except Exception:
            return _route_dry_run_error(500, "route_dry_run_unavailable")

    @router.get("/items/{source_ref:path}/flow-state")
    async def get_universal_inbox_item_flow_state(request: Request, source_ref: str):
        status_payload = _resolve_redacted_upload_status(request, source_ref, upload_handler)
        return build_universal_inbox_flow_state(
            source_ref=str(status_payload["source_ref"]),
            item_status=status_payload,
            live_write_allowed=False,
        ).to_dict()

    @router.get("/items/{source_ref:path}/content")
    async def get_universal_inbox_item_content(
        request: Request,
        source_ref: str,
        download: bool = Query(False),
    ):
        auth_manager = getattr(request.app.state, "auth_manager", None)
        try:
            content = read_selected_universal_inbox_source(
                upload_handler,
                source_ref,
                owner=effective_user(request),
                auth_manager=auth_manager,
                range_header=request.headers.get("range"),
            )
        except UniversalInboxSourceAccessError as exc:
            headers = {
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                **exc.headers,
            }
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.to_dict(),
                headers=headers,
            )
        if download and (content.status_code != 200 or content.state != "complete"):
            error = UniversalInboxSourceAccessError(
                status_code=409,
                state="incomplete",
                reason_code="complete_source_required",
            )
            return JSONResponse(
                status_code=error.status_code,
                content=error.to_dict(),
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        headers = content.headers()
        if download:
            # The source reader has already generated an injection-safe RFC5987
            # filename.  Keep its bounded, owner-scoped bytes unchanged while
            # making this explicit browser-download branch an attachment.
            headers["Content-Disposition"] = re.sub(
                r"^inline\b", "attachment", content.disposition, count=1
            )
        return Response(
            content=content.body,
            status_code=content.status_code,
            media_type=content.media_type,
            headers=headers,
        )

    @router.post("/items/{source_ref:path}/working-copy")
    async def create_universal_inbox_working_copy(
        request: Request,
        source_ref: str,
        req: WorkingCopyCreate | None = None,
    ):
        owner = require_privilege(request, "can_use_documents")
        auth_manager = getattr(request.app.state, "auth_manager", None)
        db = SessionLocal()
        try:
            result = create_or_get_universal_inbox_working_copy(
                db,
                upload_handler,
                source_ref,
                owner=owner,
                auth_manager=auth_manager,
                new_revision=bool(req and req.new_revision),
            )
            payload = _doc_to_dict(result.document)
            payload["working_copy"] = result.status_dict()
            # The working-copy operation has already canonicalized and
            # owner-authorized this upload.  Derive the public classification
            # from that canonical id only: cached copies remain usable after a
            # source is deleted, and no filename/path needs to be re-read.
            _source_kind, upload_id = _normalize_source_ref(source_ref)
            suffix = os.path.splitext(upload_id)[1].lower()
            file_type = classify_universal_inbox_file(
                f"source{suffix}" if suffix else "source"
            )
            payload["workbench_capability"] = (
                build_universal_inbox_workbench_capability(
                    file_type,
                    owner_authorized=True,
                    has_working_copy=True,
                    browser_download_allowed=True,
                    provider_write_requested=False,
                ).to_dict()
            )
            return JSONResponse(
                status_code=201 if result.created else 200,
                content=payload,
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except UniversalInboxWorkingCopyError as exc:
            db.rollback()
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.to_dict(),
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except Exception:
            db.rollback()
            return JSONResponse(
                status_code=500,
                content=UniversalInboxWorkingCopyError(
                    500, "failed", "working_copy_failed"
                ).to_dict(),
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        finally:
            db.close()

    return router


def _raise_browse_http_error(exc: Exception) -> None:
    if isinstance(exc, UniversalInboxAuthenticationRequired):
        raise HTTPException(403, "Not authenticated") from exc
    if isinstance(exc, UniversalInboxOwnerScopeDenied):
        raise HTTPException(404, "Universal Inbox owner scope not found") from exc
    if isinstance(exc, UniversalInboxBrowseError):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, UniversalInboxIndexUnavailable):
        raise HTTPException(503, "Upload browse backend is not available") from exc
    raise exc


def _resolve_redacted_upload_status(
    request: Request,
    source_ref: str,
    upload_handler: Any,
) -> dict[str, Any]:
    source_kind, upload_id = _normalize_source_ref(source_ref)
    if source_kind != "upload":
        raise HTTPException(404, "Universal Inbox source not found")

    if upload_handler is None or not hasattr(upload_handler, "resolve_upload"):
        raise HTTPException(503, "Upload status backend is not available")

    auth_manager = getattr(request.app.state, "auth_manager", None)
    auth_configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
    owner = effective_user(request)
    if auth_configured and not owner:
        raise HTTPException(403, "Not authenticated")

    info = upload_handler.resolve_upload(
        upload_id,
        owner=owner,
        auth_manager=auth_manager,
        allow_admin=True,
    )
    if not info:
        raise HTTPException(404, "Universal Inbox source not found")

    return _build_redacted_upload_status(info, source_ref=source_ref)


def _normalize_source_ref(source_ref: str) -> tuple[str, str]:
    raw = (source_ref or "").strip()
    if not raw:
        raise HTTPException(400, "Invalid Universal Inbox source reference")
    if "/" in raw or "\\" in raw or raw in {".", ".."}:
        raise HTTPException(400, "Invalid Universal Inbox source reference")

    if raw.startswith("upload:"):
        return "upload", raw.removeprefix("upload:").strip()
    if raw.startswith("inbox:upload:"):
        return "upload", raw.removeprefix("inbox:upload:").strip()
    if ":" in raw:
        kind, value = raw.split(":", 1)
        return kind.strip(), value.strip()
    return "upload", raw


def _build_redacted_upload_status(info: dict[str, Any], *, source_ref: str) -> dict[str, Any]:
    upload_id = str(info.get("id") or "").strip()
    filename = str(info.get("original_name") or info.get("name") or upload_id)
    mime_type = str(info.get("mime") or "")
    decision = classify_universal_inbox_file(filename, mime_type=mime_type)

    status = _status_from_decision(decision)
    size = info.get("size")
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None

    return {
        "schema": "odysseus.universal_inbox.item_status.v1",
        "source_ref": _redacted_source_ref(source_ref, upload_id),
        "source_kind": "upload",
        "status": status,
        "processing_state": "metadata_only",
        "pipeline_status_available": False,
        "display_name_redacted": True,
        "path_redacted": True,
        "content_redacted": True,
        "chat_id_redacted": True,
        "extension": decision.suffix or os.path.splitext(upload_id)[1].lower(),
        "mime_type": decision.mime_type,
        "family": decision.family,
        "category": decision.category,
        "extractable_now": decision.extractable_now,
        "review_required": decision.review_required,
        "blocked": decision.blocked,
        "reason_codes": list(decision.reason_codes),
        "size_bytes": size,
        "uploaded_at": info.get("uploaded_at"),
        "live_write_allowed": False,
        "next_action": _next_action_from_decision(decision),
    }


def _status_from_decision(decision: Any) -> str:
    if decision.blocked:
        return "blocked"
    if decision.category == "unsupported":
        return "unsupported"
    if decision.review_required:
        return "needs_review"
    return "uploaded"


def _next_action_from_decision(decision: Any) -> str:
    if decision.blocked:
        return "reject"
    if decision.extractable_now:
        return "extract"
    if decision.review_required:
        return "review"
    return "none"


def _redacted_source_ref(source_ref: str, upload_id: str) -> str:
    raw = (source_ref or "").strip()
    if raw.startswith("inbox:upload:"):
        return f"inbox:upload:{upload_id}"
    return f"upload:{upload_id}"


async def _parse_route_dry_run_request(request: Request) -> dict[str, Any]:
    """Accept only the small, monotonic-risk routing request contract."""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise _RouteDryRunError(415, "route_dry_run_json_required")
    transfer_encoding = request.headers.get("transfer-encoding", "").strip().lower()
    content_length = request.headers.get("content-length", "").strip()
    if transfer_encoding or not content_length.isdigit():
        raise _RouteDryRunError(411, "route_dry_run_content_length_required")
    if int(content_length) > _ROUTE_DRY_RUN_MAX_BYTES:
        raise _RouteDryRunError(413, "route_dry_run_body_too_large")
    body = await request.body()
    if len(body) > _ROUTE_DRY_RUN_MAX_BYTES:
        raise _RouteDryRunError(413, "route_dry_run_body_too_large")
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise _RouteDryRunError(400, "invalid_route_dry_run_body") from exc
    if not isinstance(payload, dict) or set(payload) - {
        "domain", "document_type", "confidence", "risk_signals"
    }:
        raise _RouteDryRunError(400, "invalid_route_dry_run_body")

    domain = _route_token(payload.get("domain"), "domain")
    document_type = _route_token(payload.get("document_type"), "document_type")
    confidence = payload.get("confidence")
    if type(confidence) not in {int, float} or isinstance(confidence, bool):
        raise _RouteDryRunError(400, "invalid_route_dry_run_confidence")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise _RouteDryRunError(400, "invalid_route_dry_run_confidence")

    risk_signals = payload.get("risk_signals", {})
    if not isinstance(risk_signals, dict) or set(risk_signals) - _ROUTE_RISK_SIGNALS:
        raise _RouteDryRunError(400, "invalid_route_dry_run_risk_signals")
    if any(type(value) is not bool for value in risk_signals.values()):
        raise _RouteDryRunError(400, "invalid_route_dry_run_risk_signals")

    # A browser can only add a review signal; absence/false never asserts safety.
    return {
        "domain": domain,
        "document_type": document_type,
        "confidence": confidence,
        "risk_signals": {key: True for key, value in risk_signals.items() if value},
    }


def _route_token(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise _RouteDryRunError(400, f"invalid_route_dry_run_{field}")
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not _ROUTE_TOKEN_RE.fullmatch(token):
        raise _RouteDryRunError(400, f"invalid_route_dry_run_{field}")
    return token


def _resolve_route_dry_run_upload(
    request: Request,
    source_ref: str,
    upload_handler: Any,
) -> tuple[dict[str, Any], str]:
    try:
        source_kind, upload_id = _normalize_source_ref(source_ref)
    except HTTPException as exc:
        status_code = 400 if exc.status_code == 400 else 404
        raise _RouteDryRunError(status_code, "malformed_route_dry_run_source_ref") from exc
    if source_kind != "upload" or not _ROUTE_UPLOAD_ID_RE.fullmatch(upload_id):
        raise _RouteDryRunError(400, "malformed_route_dry_run_source_ref")
    if upload_handler is None or not hasattr(upload_handler, "resolve_upload"):
        raise _RouteDryRunError(503, "route_dry_run_source_unavailable")

    owner = effective_user(request)
    auth_manager = getattr(request.app.state, "auth_manager", None)
    auth_configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
    if auth_configured and (not isinstance(owner, str) or not owner.strip()):
        raise _RouteDryRunError(403, "route_dry_run_owner_required")
    try:
        info = upload_handler.resolve_upload(
            upload_id,
            owner=owner,
            auth_manager=auth_manager,
            allow_admin=True,
        )
    except Exception as exc:
        raise _RouteDryRunError(404, "route_dry_run_source_not_found") from exc
    if not isinstance(info, dict):
        # Foreign and missing sources deliberately share the same content-free response.
        raise _RouteDryRunError(404, "route_dry_run_source_not_found")
    return info, _redacted_source_ref(source_ref, upload_id)


def _build_route_dry_run_projection(
    info: dict[str, Any],
    *,
    route_request: dict[str, Any],
) -> dict[str, Any]:
    """Return a public route explanation, never the planner's path-bearing object."""

    filename = str(info.get("original_name") or info.get("name") or "document")
    mime_type = str(info.get("mime") or "")
    file_type = classify_universal_inbox_file(filename, mime_type=mime_type)
    capability = build_universal_inbox_workbench_capability(
        file_type,
        owner_authorized=True,
        # Browser detection and live/provider intent are intentionally not inputs here.
        has_working_copy=False,
        browser_download_allowed=False,
    )
    action = capability.action(WorkbenchAction.ROUTE_DRY_RUN)
    suffix = file_type.suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", file_type.suffix or "") else ".bin"
    planner_item = {
        # The planner requires a relative identity, but it never receives an upload path/name.
        "original_path": f"incoming/source{suffix}",
        "filename": f"source{suffix}",
        "title": "document",
        "domain": route_request["domain"],
        "document_type": route_request["document_type"],
        "confidence": route_request["confidence"],
        **route_request["risk_signals"],
    }
    routing_decision = plan_universal_inbox_route(planner_item)
    placement = build_universal_inbox_placement_plan(routing_decision)

    review_reasons = list(placement.review_reasons)
    no_go_reasons = list(placement.no_go_reasons)
    if action.state == WorkbenchActionState.REVIEW:
        review_reasons.append("route_capability_review")
    elif action.state != WorkbenchActionState.ALLOWED:
        no_go_reasons.append(f"route_capability_{action.state.value}")
    review_reasons = list(dict.fromkeys(review_reasons))
    no_go_reasons = list(dict.fromkeys(no_go_reasons))
    status = "no_go" if no_go_reasons else "review" if review_reasons else "go"

    return {
        "schema": ROUTE_DRY_RUN_SCHEMA,
        "status": status,
        "policy_status": status,
        "input_authority": "advisory",
        "suggestion": (
            "matched_policy_route" if status == "go"
            else "blocked_by_policy" if status == "no_go"
            else "review_required"
        ),
        "domain": route_request["domain"],
        "document_type": route_request["document_type"],
        "confidence": route_request["confidence"],
        "reason_codes": no_go_reasons + review_reasons,
        "review_reasons": review_reasons,
        "no_go_reasons": no_go_reasons,
        "route_capability": {
            "state": action.state.value,
            "reason_codes": list(action.reason_codes),
            "server_authoritative": True,
        },
        "owner_scope_verified": True,
        "source_ref_redacted": True,
        "path_redacted": True,
        "content_redacted": True,
        "raptorgraph_payload_visible": False,
        "dry_run": True,
        "copy_performed": False,
        "move_performed": False,
        "delete_performed": False,
        "overwrite_performed": False,
        "memory_writes_performed": False,
        "live_writes_performed": False,
        "writes_performed": False,
        "original_immutable": True,
        "live_apply": {
            "enabled": False,
            "gate": "UIX-NEXTCLOUD-LIVE-WRITE",
        },
    }


def _route_dry_run_headers() -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }


def _route_dry_run_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "schema": "odysseus.universal_inbox.route_dry_run_error.v1",
            "error": code,
            "content_redacted": True,
            "path_redacted": True,
            "copy_performed": False,
            "move_performed": False,
            "delete_performed": False,
            "overwrite_performed": False,
            "memory_writes_performed": False,
            "live_writes_performed": False,
            "writes_performed": False,
        },
        headers=_route_dry_run_headers(),
    )
