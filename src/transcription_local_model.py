"""Exact local Gemma transport for the transcription reviewer."""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

from src.maintenance_llm_runtime import (
    MaintenanceLLMMessage,
    MaintenanceLLMRequest,
    MaintenanceLLMResult,
    call_maintenance_llm,
)
from src.maintenance_model_policy import (
    DEFAULT_MAINTENANCE_MODEL,
    DEFAULT_MAINTENANCE_PROVIDER,
    MaintenanceModelProfile,
    MaintenanceModelRole,
)


_SYSTEM_PROMPT = """You review German ASR segments locally.
Return exactly one JSON object with schema odysseus.transcription_review_result.v1.
Use only segment_id values from the request. Never invent text, people, numbers,
dates, owners, hashes, or identifiers. Claims contain only claim_kind,
segment_ids, assignee, and critical_uncertainty; never claim prose. Corrections
contain only the exact declared fields. Use null for no assignee. No markdown."""


class LocalTranscriptionReviewTransportError(RuntimeError):
    """Content-free terminal local review error."""


ReviewCall = Callable[[MaintenanceLLMRequest], MaintenanceLLMResult]


class Gemma3LocalReviewTransport:
    """A one-attempt, non-streaming, no-fallback local maintenance call."""

    def __init__(
        self,
        endpoint: str,
        profile: MaintenanceModelProfile,
        *,
        invoke: ReviewCall = call_maintenance_llm,
        max_tokens: int = 1_200,
        timeout_ms: int = 45_000,
    ) -> None:
        if (
            not isinstance(profile, MaintenanceModelProfile)
            or profile.model_ref != DEFAULT_MAINTENANCE_MODEL
            or profile.provider != DEFAULT_MAINTENANCE_PROVIDER
            or profile.role is not MaintenanceModelRole.MAINTENANCE
            or profile.runtime_enabled is not True
            or profile.fallback_allowed
            or profile.truth_write_allowed
            or not callable(invoke)
        ):
            raise LocalTranscriptionReviewTransportError(
                "local transcription review unavailable"
            )
        try:
            parsed = urlparse(endpoint)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise LocalTranscriptionReviewTransportError(
                "local transcription review unavailable"
            ) from exc
        if (
            parsed.scheme != "http"
            or host not in {"localhost", "127.0.0.1", "::1", "ollama"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed.path or "").rstrip("/") not in {"", "/api", "/api/chat"}
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise LocalTranscriptionReviewTransportError(
                "local transcription review unavailable"
            )
        self._endpoint = endpoint
        self._profile = profile
        self._invoke = invoke
        self._max_tokens = max_tokens
        self._timeout_ms = timeout_ms

    def review(self, request_json: str) -> str:
        try:
            request = MaintenanceLLMRequest(
                endpoint=self._endpoint,
                messages=(
                    MaintenanceLLMMessage("system", _SYSTEM_PROMPT),
                    MaintenanceLLMMessage("user", request_json),
                ),
                profile=self._profile,
                role=MaintenanceModelRole.MAINTENANCE,
                max_tokens=self._max_tokens,
                timeout_ms=self._timeout_ms,
                max_attempts=1,
                temperature=0.0,
                stream=False,
                fallback_requested=False,
                truth_write_requested=False,
            )
            result = self._invoke(request)
            if not isinstance(result, MaintenanceLLMResult) or not isinstance(
                result.text, str
            ):
                raise TypeError("invalid local review result")
            return result.text
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise LocalTranscriptionReviewTransportError(
                "local transcription review failed"
            ) from None


__all__ = [
    "Gemma3LocalReviewTransport",
    "LocalTranscriptionReviewTransportError",
]
