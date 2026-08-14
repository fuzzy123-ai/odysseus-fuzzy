"""Cookie-authenticated, owner-scoped routes for the local transcription API."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request

from core.auth import AuthSubjectIdentityError
from src.transcription_runtime import (
    TranscriptionDeletionUnavailable,
    TranscriptionRuntime,
    TranscriptionRuntimeDisabled,
    TranscriptionRuntimeError,
)
from src.transcription_store import IdempotencyConflictError, TranscriptionNotFoundError, TranscriptionStoreError


_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_OPAQUE_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_MEDIA_TYPES = frozenset({"audio/wav", "audio/mpeg", "audio/ogg", "audio/webm", "audio/mp4"})
_DELETE_CONFIRMATION = "delete-transcription"


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    try:
        parsed = urlsplit(origin or "")
        expected = f"{request.url.scheme}://{host}"
    except (TypeError, ValueError):
        raise HTTPException(403, "Same-origin request required")
    if not origin or not host or parsed.username or parsed.password or origin.rstrip("/") != expected.rstrip("/"):
        raise HTTPException(403, "Same-origin request required")


def _owner_id(request: Request) -> str:
    if getattr(request.state, "api_token", False) or request.headers.get("authorization"):
        raise HTTPException(403, "Bearer upload is not permitted")
    username = getattr(request.state, "current_user", None)
    if not isinstance(username, str) or not username or username == "api":
        raise HTTPException(401, "Authenticated browser session required")
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if auth_manager is None:
        raise HTTPException(503, "Authentication authority unavailable")
    try:
        owner_id = auth_manager.subject_id_for_username(username)
    except AuthSubjectIdentityError:
        raise HTTPException(503, "Authentication identity unavailable")
    if owner_id is None:
        raise HTTPException(401, "Authenticated browser session required")
    return owner_id


def _safe_record(record: Any) -> dict[str, Any]:
    """Serialize owner-visible output without the server's opaque owner/ref IDs."""
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if key not in {
                    "owner_id", "authorization_id", "retention_policy_id",
                    "storage_locator", "authorization", "retention_policy",
                    "receipt", "backup_receipt_id", "snapshot_ref",
                }
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    data = record.to_dict()
    return clean(data)


def _map_runtime_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, TranscriptionRuntimeDisabled):
        return HTTPException(404, "Transcription is disabled")
    if isinstance(exc, TranscriptionNotFoundError):
        return HTTPException(404, "Transcription not found")
    if isinstance(exc, IdempotencyConflictError):
        return HTTPException(409, "Idempotency conflict")
    if isinstance(exc, TranscriptionDeletionUnavailable):
        return HTTPException(409, "Deletion is not enabled")
    if isinstance(exc, (TranscriptionRuntimeError, TranscriptionStoreError)):
        return HTTPException(400, "Transcription request rejected")
    return HTTPException(500, "Transcription operation failed")


def setup_transcription_routes(runtime: TranscriptionRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/transcriptions", tags=["transcriptions"])

    @router.post("")
    async def upload_transcription(
        request: Request,
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
        content_length: str | None = Header(None, alias="Content-Length"),
        source_sha256: str | None = Header(None, alias="X-Content-SHA256"),
        audio_media_type: str | None = Header(None, alias="X-Audio-Media-Type"),
    ) -> dict[str, Any]:
        owner_id = _owner_id(request)
        _same_origin(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            raise HTTPException(415, "application/octet-stream required")
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise HTTPException(400, "Valid Idempotency-Key required")
        if not isinstance(source_sha256, str) or not _DIGEST.fullmatch(source_sha256):
            raise HTTPException(400, "Valid source digest required")
        if audio_media_type not in _MEDIA_TYPES:
            raise HTTPException(415, "Unsupported audio media type")
        try:
            expected_size = int(content_length or "")
        except ValueError:
            raise HTTPException(411, "Exact Content-Length required")
        if not 1 <= expected_size <= runtime.config.max_bytes:
            raise HTTPException(413, "Audio body exceeds the fixed limit")
        try:
            stored = await runtime.ingest_stream(
                owner_id, idempotency_key, request.stream(), audio_media_type,
                expected_sha256=source_sha256, expected_size=expected_size,
            )
        except Exception as exc:
            raise _map_runtime_error(exc)
        return {
            "artifact_id": stored.artifact.artifact_id,
            "job_id": stored.job.job_id,
            "state": stored.job.state,
            "source_sha256": stored.artifact.source_sha256,
            "byte_count": stored.artifact.byte_count,
            "media_type": stored.artifact.media_type,
            "idempotent_replay": bool(stored.idempotent_replay),
        }

    @router.get("/{job_id}")
    async def transcription_status(request: Request, job_id: str) -> dict[str, Any]:
        owner_id = _owner_id(request)
        if not _OPAQUE_ID.fullmatch(job_id):
            raise HTTPException(404, "Transcription not found")
        try:
            record = await asyncio.to_thread(runtime.read_record, owner_id, job_id)
        except Exception as exc:
            raise _map_runtime_error(exc)
        return {
            "artifact_id": record.artifact.artifact_id,
            "job_id": record.job.job_id,
            "state": record.job.state,
            "byte_count": record.artifact.byte_count,
            "media_type": record.artifact.media_type,
        }

    @router.get("/{job_id}/result")
    async def transcription_result(request: Request, job_id: str) -> dict[str, Any]:
        owner_id = _owner_id(request)
        if not _OPAQUE_ID.fullmatch(job_id):
            raise HTTPException(404, "Transcription not found")
        try:
            record = await asyncio.to_thread(runtime.read_record, owner_id, job_id)
        except Exception as exc:
            raise _map_runtime_error(exc)
        return _safe_record(record)

    @router.delete("/{job_id}")
    async def delete_transcription(
        request: Request,
        job_id: str,
        confirmation: str | None = Header(None, alias="X-Confirm-Deletion"),
    ) -> dict[str, str]:
        owner_id = _owner_id(request)
        _same_origin(request)
        if not _OPAQUE_ID.fullmatch(job_id):
            raise HTTPException(404, "Transcription not found")
        if confirmation != _DELETE_CONFIRMATION:
            raise HTTPException(409, "Separate deletion confirmation required")
        try:
            await asyncio.to_thread(
                runtime.request_deletion, owner_id, job_id, confirmed=True
            )
        except Exception as exc:
            raise _map_runtime_error(exc)
        return {"job_id": job_id, "state": "deletion_pending"}

    return router
