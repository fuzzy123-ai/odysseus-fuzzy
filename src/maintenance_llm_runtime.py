"""Typed, default-off call boundary for the local maintenance model.

This module intentionally does not reuse the generic LLM retry loop.  A
maintenance call has a narrower contract: one admission lease, timeout and
transport invocation per upstream attempt.  Generic chat, agent and cloud
calls remain untyped and therefore cannot enter this lane accidentally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import math
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse, urlunparse

import httpx

from src.local_model_scheduler import (
    LocalModelAdmissionRegistry,
    local_model_async_slot,
    local_model_sync_slot,
)
from src.maintenance_model_policy import (
    DEFAULT_LATENCY_BUDGET_MS,
    DEFAULT_MAINTENANCE_MODEL,
    DEFAULT_MAINTENANCE_PROVIDER,
    DEFAULT_TOKEN_BUDGET,
    MaintenanceModelProfile,
    MaintenanceModelRole,
    default_maintenance_model_profile,
    evaluate_maintenance_model_eligibility,
)


MAINTENANCE_LLM_REQUEST_SCHEMA = "odysseus.maintenance_llm_request.v1"
MAINTENANCE_LLM_RESULT_SCHEMA = "odysseus.maintenance_llm_result.v1"
MAX_MAINTENANCE_ATTEMPTS = 3
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_ALLOWED_MESSAGE_ROLES = frozenset({"system", "user", "assistant"})


class MaintenanceLLMRuntimeError(RuntimeError):
    """Base error for the isolated maintenance runtime."""


class MaintenanceLLMContractError(MaintenanceLLMRuntimeError, ValueError):
    """Raised when a request violates the typed maintenance contract."""


class MaintenanceLLMDisabledError(MaintenanceLLMRuntimeError):
    """Raised before admission or I/O while the runtime is disabled."""


class MaintenanceLLMAdmissionError(MaintenanceLLMRuntimeError):
    """Raised when the exact maintenance lane cannot issue a lease."""


class MaintenanceLLMCallError(MaintenanceLLMRuntimeError):
    """Content-free terminal failure for one maintenance call."""

    def __init__(self, reason: str, *, attempts: int, status_code: int | None = None) -> None:
        self.reason = reason
        self.attempts = attempts
        self.status_code = status_code
        status_scope = "http" if status_code is not None else "transport"
        super().__init__(
            f"maintenance LLM call failed: reason={reason}; "
            f"attempts={attempts}; status_scope={status_scope}"
        )

    def audit_dict(self) -> dict[str, Any]:
        return {
            "schema": MAINTENANCE_LLM_RESULT_SCHEMA,
            "outcome": "failed",
            "reason": self.reason,
            "attempts": self.attempts,
            "status_scope": "http" if self.status_code is not None else "transport",
            "retryable": False,
        }


class _MaintenanceAttemptFailure(RuntimeError):
    def __init__(self, reason: str, *, retryable: bool, status_code: int | None = None) -> None:
        self.reason = reason
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class MaintenanceLLMMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in _ALLOWED_MESSAGE_ROLES:
            raise MaintenanceLLMContractError("message role is not allowed")
        if not isinstance(self.content, str) or not self.content.strip():
            raise MaintenanceLLMContractError("message content must be a non-empty string")


@dataclass(frozen=True, slots=True)
class MaintenanceLLMRequest:
    endpoint: str
    messages: tuple[MaintenanceLLMMessage, ...]
    profile: MaintenanceModelProfile = field(default_factory=default_maintenance_model_profile)
    role: MaintenanceModelRole = MaintenanceModelRole.MAINTENANCE
    max_tokens: int = DEFAULT_TOKEN_BUDGET
    timeout_ms: int = DEFAULT_LATENCY_BUDGET_MS
    max_attempts: int = 1
    temperature: float = 0.0
    stream: bool = False
    fallback_requested: bool = False
    truth_write_requested: bool = False
    schema: str = MAINTENANCE_LLM_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MAINTENANCE_LLM_REQUEST_SCHEMA:
            raise MaintenanceLLMContractError("unsupported maintenance request schema")
        if not isinstance(self.profile, MaintenanceModelProfile):
            raise MaintenanceLLMContractError("profile must be a MaintenanceModelProfile")
        decision = evaluate_maintenance_model_eligibility(
            model_ref=self.profile.model_ref,
            provider=self.profile.provider,
            role=self.role,
            fallback_requested=self.fallback_requested,
            truth_write_requested=self.truth_write_requested,
        )
        if not decision.eligible or self.profile.role is not self.role:
            raise MaintenanceLLMContractError(
                f"maintenance eligibility rejected: {decision.reason.value}"
            )
        if not isinstance(self.stream, bool):
            raise MaintenanceLLMContractError("stream must be a boolean")
        if self.stream:
            raise MaintenanceLLMContractError("streaming is not supported by maintenance v1")
        _validate_endpoint(self.endpoint)
        _validate_positive_bounded_int(
            "max_tokens",
            self.max_tokens,
            min(self.profile.token_budget, DEFAULT_TOKEN_BUDGET),
        )
        _validate_positive_bounded_int(
            "timeout_ms",
            self.timeout_ms,
            min(self.profile.latency_budget_ms, DEFAULT_LATENCY_BUDGET_MS),
        )
        _validate_positive_bounded_int(
            "max_attempts", self.max_attempts, MAX_MAINTENANCE_ATTEMPTS
        )
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)):
            raise MaintenanceLLMContractError("temperature must be numeric")
        if not math.isfinite(float(self.temperature)) or not 0.0 <= float(self.temperature) <= 2.0:
            raise MaintenanceLLMContractError("temperature must be between 0 and 2")
        if not isinstance(self.messages, tuple) or not self.messages:
            raise MaintenanceLLMContractError("messages must be a non-empty typed tuple")
        if any(not isinstance(message, MaintenanceLLMMessage) for message in self.messages):
            raise MaintenanceLLMContractError("messages must contain MaintenanceLLMMessage values")
        input_chars = sum(len(message.content) for message in self.messages)
        if input_chars > self.profile.max_input_chars:
            raise MaintenanceLLMContractError("message input exceeds the maintenance character budget")

    @property
    def model(self) -> str:
        return DEFAULT_MAINTENANCE_MODEL

    @property
    def provider(self) -> str:
        return DEFAULT_MAINTENANCE_PROVIDER

    @property
    def target_url(self) -> str:
        return _ollama_chat_url(self.endpoint)

    @property
    def timeout_seconds(self) -> float:
        return self.timeout_ms / 1000.0

    def audit_dict(self) -> dict[str, Any]:
        """Return bounded diagnostics without endpoint or message content."""

        return {
            "schema": self.schema,
            "model_scope": "gemma3_4b",
            "provider_scope": "local_ollama",
            "role_scope": "maintenance",
            "runtime_enabled": self.profile.runtime_enabled,
            "message_count": len(self.messages),
            "input_chars": sum(len(message.content) for message in self.messages),
            "max_tokens": self.max_tokens,
            "timeout_ms": self.timeout_ms,
            "max_attempts": self.max_attempts,
            "streaming_allowed": False,
            "fallback_allowed": False,
            "truth_write_allowed": False,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceLLMUpstreamAttempt:
    number: int
    target_url: str
    payload: Mapping[str, Any]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class MaintenanceLLMUpstreamResponse:
    status_code: int
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceLLMResult:
    text: str
    attempts: int
    schema: str = MAINTENANCE_LLM_RESULT_SCHEMA

    def audit_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "outcome": "succeeded",
            "model_scope": "gemma3_4b",
            "provider_scope": "local_ollama",
            "attempts": self.attempts,
            "output_chars": len(self.text),
            "fallback_used": False,
            "truth_write_performed": False,
        }


SyncAttempt = Callable[[MaintenanceLLMUpstreamAttempt], MaintenanceLLMUpstreamResponse]
AsyncAttempt = Callable[
    [MaintenanceLLMUpstreamAttempt], Awaitable[MaintenanceLLMUpstreamResponse]
]


def call_maintenance_llm(
    request: MaintenanceLLMRequest,
    *,
    attempt: SyncAttempt | None = None,
    registry: LocalModelAdmissionRegistry | None = None,
) -> MaintenanceLLMResult:
    """Execute a typed sync call with one lease per upstream attempt."""

    _validate_runtime_request(request)
    invoke = attempt or _default_sync_attempt
    for attempt_number in range(1, request.max_attempts + 1):
        upstream = _build_upstream_attempt(request, attempt_number)
        try:
            with local_model_sync_slot(
                request.endpoint,
                request.model,
                provider=request.provider,
                role=request.role,
                fallback_requested=False,
                truth_write_requested=False,
                registry=registry,
            ) as lease:
                if lease is None:
                    raise MaintenanceLLMAdmissionError("maintenance admission unavailable")
                response = invoke(upstream)
            text = _parse_upstream_response(response)
            return MaintenanceLLMResult(text=text, attempts=attempt_number)
        except MaintenanceLLMAdmissionError:
            raise
        except _MaintenanceAttemptFailure as exc:
            if exc.retryable and attempt_number < request.max_attempts:
                continue
            raise MaintenanceLLMCallError(
                exc.reason,
                attempts=attempt_number,
                status_code=exc.status_code,
            ) from None
        except Exception:
            raise MaintenanceLLMCallError(
                "transport_exception", attempts=attempt_number
            ) from None
    raise AssertionError("maintenance attempt loop exhausted")


async def call_maintenance_llm_async(
    request: MaintenanceLLMRequest,
    *,
    attempt: AsyncAttempt | None = None,
    registry: LocalModelAdmissionRegistry | None = None,
) -> MaintenanceLLMResult:
    """Execute a typed async call with one lease and timeout per attempt."""

    _validate_runtime_request(request)
    invoke = attempt or _default_async_attempt
    for attempt_number in range(1, request.max_attempts + 1):
        upstream = _build_upstream_attempt(request, attempt_number)
        try:
            async with local_model_async_slot(
                request.endpoint,
                request.model,
                provider=request.provider,
                role=request.role,
                fallback_requested=False,
                truth_write_requested=False,
                registry=registry,
            ) as lease:
                if lease is None:
                    raise MaintenanceLLMAdmissionError("maintenance admission unavailable")
                try:
                    response = await asyncio.wait_for(
                        invoke(upstream), timeout=upstream.timeout_seconds
                    )
                except TimeoutError:
                    raise _MaintenanceAttemptFailure(
                        "timeout", retryable=True
                    ) from None
            text = _parse_upstream_response(response)
            return MaintenanceLLMResult(text=text, attempts=attempt_number)
        except MaintenanceLLMAdmissionError:
            raise
        except asyncio.CancelledError:
            raise
        except _MaintenanceAttemptFailure as exc:
            if exc.retryable and attempt_number < request.max_attempts:
                continue
            raise MaintenanceLLMCallError(
                exc.reason,
                attempts=attempt_number,
                status_code=exc.status_code,
            ) from None
        except Exception:
            raise MaintenanceLLMCallError(
                "transport_exception", attempts=attempt_number
            ) from None
    raise AssertionError("maintenance attempt loop exhausted")


def _validate_runtime_request(request: MaintenanceLLMRequest) -> None:
    if not isinstance(request, MaintenanceLLMRequest):
        raise MaintenanceLLMContractError("request must be a MaintenanceLLMRequest")
    if not request.profile.runtime_enabled:
        raise MaintenanceLLMDisabledError("maintenance LLM runtime is disabled")


def _validate_positive_bounded_int(name: str, value: Any, upper_bound: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MaintenanceLLMContractError(f"{name} must be an integer")
    if value <= 0 or value > upper_bound:
        raise MaintenanceLLMContractError(f"{name} must be between 1 and {upper_bound}")


def _validate_endpoint(endpoint: Any) -> None:
    if not isinstance(endpoint, str) or not endpoint:
        raise MaintenanceLLMContractError("endpoint must be a non-empty URL")
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise MaintenanceLLMContractError("endpoint is invalid") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"}:
        raise MaintenanceLLMContractError("endpoint scheme must be http or https")
    if host not in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "ollama"}:
        raise MaintenanceLLMContractError("endpoint must resolve to the local Ollama scope")
    if parsed.username is not None or parsed.password is not None:
        raise MaintenanceLLMContractError("endpoint credentials are forbidden")
    if parsed.query or parsed.fragment:
        raise MaintenanceLLMContractError("endpoint query and fragment are forbidden")
    if port is not None and not 1 <= port <= 65535:
        raise MaintenanceLLMContractError("endpoint port is invalid")
    path = (parsed.path or "").rstrip("/")
    if path not in {"", "/api", "/api/chat"}:
        raise MaintenanceLLMContractError("endpoint path must be the Ollama API root or chat route")


def _ollama_chat_url(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    return urlunparse((parsed.scheme, parsed.netloc, "/api/chat", "", "", ""))


def _build_upstream_attempt(
    request: MaintenanceLLMRequest, attempt_number: int
) -> MaintenanceLLMUpstreamAttempt:
    payload = {
        "model": request.model,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "stream": request.stream,
        "options": {
            "temperature": float(request.temperature),
            "num_predict": request.max_tokens,
        },
    }
    return MaintenanceLLMUpstreamAttempt(
        number=attempt_number,
        target_url=request.target_url,
        payload=payload,
        timeout_seconds=request.timeout_seconds,
    )


def _parse_upstream_response(response: Any) -> str:
    if not isinstance(response, MaintenanceLLMUpstreamResponse):
        raise _MaintenanceAttemptFailure("invalid_transport_response", retryable=False)
    status_code = response.status_code
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise _MaintenanceAttemptFailure("invalid_status", retryable=False)
    if not 200 <= status_code < 300:
        raise _MaintenanceAttemptFailure(
            "retryable_http_status" if status_code in _RETRYABLE_STATUS_CODES else "http_status",
            retryable=status_code in _RETRYABLE_STATUS_CODES,
            status_code=status_code,
        )
    payload = response.payload
    if not isinstance(payload, Mapping):
        raise _MaintenanceAttemptFailure("invalid_response_schema", retryable=False)
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise _MaintenanceAttemptFailure("invalid_response_schema", retryable=False)
    content = message.get("content")
    if not isinstance(content, str):
        raise _MaintenanceAttemptFailure("invalid_response_schema", retryable=False)
    return content


def _default_sync_attempt(
    attempt: MaintenanceLLMUpstreamAttempt,
) -> MaintenanceLLMUpstreamResponse:
    try:
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            response = client.post(
                attempt.target_url,
                json=attempt.payload,
                timeout=attempt.timeout_seconds,
            )
    except (httpx.TimeoutException, httpx.NetworkError):
        raise _MaintenanceAttemptFailure("transport_failure", retryable=True) from None
    except httpx.HTTPError:
        raise _MaintenanceAttemptFailure("transport_failure", retryable=False) from None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return MaintenanceLLMUpstreamResponse(response.status_code, payload)


async def _default_async_attempt(
    attempt: MaintenanceLLMUpstreamAttempt,
) -> MaintenanceLLMUpstreamResponse:
    try:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                attempt.target_url,
                json=attempt.payload,
                timeout=attempt.timeout_seconds,
            )
    except (httpx.TimeoutException, httpx.NetworkError):
        raise _MaintenanceAttemptFailure("transport_failure", retryable=True) from None
    except httpx.HTTPError:
        raise _MaintenanceAttemptFailure("transport_failure", retryable=False) from None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return MaintenanceLLMUpstreamResponse(response.status_code, payload)


__all__ = [
    "MAINTENANCE_LLM_REQUEST_SCHEMA",
    "MAINTENANCE_LLM_RESULT_SCHEMA",
    "MAX_MAINTENANCE_ATTEMPTS",
    "MaintenanceLLMAdmissionError",
    "MaintenanceLLMCallError",
    "MaintenanceLLMContractError",
    "MaintenanceLLMDisabledError",
    "MaintenanceLLMMessage",
    "MaintenanceLLMRequest",
    "MaintenanceLLMResult",
    "MaintenanceLLMRuntimeError",
    "MaintenanceLLMUpstreamAttempt",
    "MaintenanceLLMUpstreamResponse",
    "call_maintenance_llm",
    "call_maintenance_llm_async",
]
