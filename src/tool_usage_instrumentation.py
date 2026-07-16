"""Fail-open central instrumentation for privacy-safe tool usage events."""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import secrets
from threading import Lock
import time
from typing import Any, Callable

from src.builtin_tool_catalog import builtin_spec
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageBlockedReason,
    ToolUsageErrorClass,
    ToolUsageEvent,
    ToolUsageEventBuilder,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
    new_invocation_id,
    pseudonymize_reference,
    size_bucket_for_count,
)


MAX_DIAGNOSTIC_COUNT = 1_000_000
MAX_ARGUMENT_BYTES = 10_000_000
_SAFE_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")
_ANALYTICS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


@dataclass(frozen=True, slots=True)
class ToolUsageCallMetadata:
    """Content-free metadata shared by independent usage and AI-Lens consumers."""

    tool_analytics_id: str
    tool_family: ToolFamily
    tool_source: ToolSource
    argument_size_bucket: ToolUsageSizeBucket
    argument_present: bool
    argument_bytes: int

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "tool_analytics_id": self.tool_analytics_id,
            "tool_family": self.tool_family.value,
            "tool_source": self.tool_source.value,
            "argument_size_bucket": self.argument_size_bucket.value,
            "argument_present": self.argument_present,
            "argument_bytes": self.argument_bytes,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageTerminalOutcome:
    status: ToolUsageStatus
    error_class: ToolUsageErrorClass | None = None
    blocked_reason_code: ToolUsageBlockedReason | None = None


@dataclass(frozen=True, slots=True)
class ToolUsageSourceIdentity:
    """Server-owned source adapter identity for proven non-wrapper paths."""

    tool_analytics_id: str
    tool_family: ToolFamily
    tool_source: ToolSource

    def __post_init__(self) -> None:
        if not isinstance(self.tool_analytics_id, str) or not _ANALYTICS_ID_RE.fullmatch(
            self.tool_analytics_id
        ):
            raise ValueError("tool_analytics_id must be a canonical lowercase slug")
        if not isinstance(self.tool_family, ToolFamily):
            raise ValueError("tool_family must be normalized")
        if not isinstance(self.tool_source, ToolSource):
            raise ValueError("tool_source must be normalized")


@dataclass(frozen=True, slots=True)
class ToolUsageTrustedContext:
    """Server-owned invocation identity and privacy policy.

    Tool payloads never participate in this object. Raw references remain
    process-local and are converted to HMAC references only at the persistence
    boundary.
    """

    surface: ToolUsageSurface
    model_scope: ToolUsageModelScope
    agent_mode: ToolUsageAgentMode
    owner: str | None
    session_id: str | None
    run_id: str | None
    incognito: bool
    correlation_id: str | None = None
    retry_ordinal: int = 0
    owner_is_nobody: bool = False

    def __post_init__(self) -> None:
        for field_name, value, enum_type in (
            ("surface", self.surface, ToolUsageSurface),
            ("model_scope", self.model_scope, ToolUsageModelScope),
            ("agent_mode", self.agent_mode, ToolUsageAgentMode),
        ):
            if not isinstance(value, enum_type):
                raise ValueError(f"{field_name} must be normalized")
        for field_name, value in (
            ("owner", self.owner),
            ("session_id", self.session_id),
            ("run_id", self.run_id),
            ("correlation_id", self.correlation_id),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field_name} must be a non-empty string or null")
        if not isinstance(self.incognito, bool) or not isinstance(
            self.owner_is_nobody,
            bool,
        ):
            raise ValueError("privacy flags must be booleans")
        if (
            isinstance(self.retry_ordinal, bool)
            or not isinstance(self.retry_ordinal, int)
            or not 0 <= self.retry_ordinal <= 100
        ):
            raise ValueError("retry_ordinal must be an integer from 0 through 100")


_BYPASS_INSTRUMENTATION: ContextVar["ToolUsageInstrumentation | None"] = ContextVar(
    "tool_usage_bypass_instrumentation",
    default=None,
)


@contextmanager
def bind_bypass_tool_usage_instrumentation(
    instrumentation: "ToolUsageInstrumentation | None",
):
    """Bind telemetry only around a proven direct execution bypass."""

    token = _BYPASS_INSTRUMENTATION.set(instrumentation)
    try:
        yield
    finally:
        _BYPASS_INSTRUMENTATION.reset(token)


def current_bypass_tool_usage_instrumentation() -> "ToolUsageInstrumentation | None":
    return _BYPASS_INSTRUMENTATION.get()


def new_tool_usage_run_id() -> str:
    """Create a process-local run identifier for later HMAC conversion."""

    return f"run_{secrets.token_urlsafe(18)}"


def _source_identity_for_tool(tool_name: str) -> ToolUsageSourceIdentity:
    spec = builtin_spec(tool_name)
    if spec is not None:
        return ToolUsageSourceIdentity(
            tool_analytics_id=spec.tool_id.replace("_", "-"),
            tool_family=spec.family,
            tool_source=ToolSource.BUILTIN,
        )
    try:
        from src.mcp_manager import parse_qualified_mcp_tool_name

        if parse_qualified_mcp_tool_name(tool_name) is not None:
            return ToolUsageSourceIdentity(
                tool_analytics_id="dynamic-mcp",
                tool_family=ToolFamily.PLUGINS_MCP,
                tool_source=ToolSource.MCP,
            )
    except Exception:
        pass
    try:
        from src.tool_registry import usage_identity_for_tool

        plugin_identity = usage_identity_for_tool(tool_name)
        if plugin_identity is not None:
            analytics_id, family = plugin_identity
            return ToolUsageSourceIdentity(
                tool_analytics_id=analytics_id,
                tool_family=family,
                tool_source=ToolSource.PLUGIN,
            )
    except Exception:
        pass
    return ToolUsageSourceIdentity(
        tool_analytics_id="dynamic-unclassified",
        tool_family=ToolFamily.UNCLASSIFIED_DYNAMIC,
        tool_source=ToolSource.DYNAMIC,
    )


def build_bypass_tool_usage_call_metadata(
    identity: ToolUsageSourceIdentity,
    *,
    argument_bytes: int = 0,
) -> ToolUsageCallMetadata:
    """Build content-free metadata for an explicitly bound execution bypass."""

    if not isinstance(identity, ToolUsageSourceIdentity):
        raise ValueError("identity must be a ToolUsageSourceIdentity")
    if isinstance(argument_bytes, bool) or not isinstance(argument_bytes, int):
        raise ValueError("argument_bytes must be an integer")
    bounded_bytes = min(max(argument_bytes, 0), MAX_ARGUMENT_BYTES)
    return ToolUsageCallMetadata(
        tool_analytics_id=identity.tool_analytics_id,
        tool_family=identity.tool_family,
        tool_source=identity.tool_source,
        argument_size_bucket=size_bucket_for_count(bounded_bytes),
        argument_present=bounded_bytes > 0,
        argument_bytes=bounded_bytes,
    )


def build_tool_usage_call_metadata_for_name(
    tool_name: str,
    *,
    argument_bytes: int = 0,
) -> ToolUsageCallMetadata:
    """Resolve a runtime tool name through catalogs without reading arguments."""

    return build_bypass_tool_usage_call_metadata(
        _source_identity_for_tool(str(tool_name or "").strip()),
        argument_bytes=argument_bytes,
    )


def build_tool_usage_call_metadata(block: Any) -> ToolUsageCallMetadata:
    tool_name = str(getattr(block, "tool_type", "") or "").strip()
    content = str(getattr(block, "content", "") or "")
    argument_bytes = len(content.encode("utf-8", errors="replace"))
    return build_tool_usage_call_metadata_for_name(
        tool_name,
        argument_bytes=argument_bytes,
    )


def classify_tool_usage_outcome(
    description: Any,
    result: Any,
) -> ToolUsageTerminalOutcome:
    normalized_description = str(description or "").casefold()
    if normalized_description.startswith("unknown:"):
        return ToolUsageTerminalOutcome(
            status=ToolUsageStatus.REJECTED,
            blocked_reason_code=ToolUsageBlockedReason.UNKNOWN_TOOL,
        )
    if "blocked" in normalized_description:
        reason = ToolUsageBlockedReason.POLICY
        error_text = (
            str(result.get("error") or "").casefold()
            if isinstance(result, dict)
            else ""
        )
        if "disabled" in error_text:
            reason = ToolUsageBlockedReason.DISABLED
        elif "admin" in error_text or "permission" in error_text:
            reason = ToolUsageBlockedReason.PERMISSION
        elif "unavailable" in error_text or "not available" in error_text:
            reason = ToolUsageBlockedReason.UNAVAILABLE
        return ToolUsageTerminalOutcome(
            status=ToolUsageStatus.BLOCKED,
            blocked_reason_code=reason,
        )
    if _result_failed(result):
        return ToolUsageTerminalOutcome(
            status=ToolUsageStatus.FAILED,
            error_class=ToolUsageErrorClass.EXECUTION_ERROR,
        )
    return ToolUsageTerminalOutcome(status=ToolUsageStatus.SUCCEEDED)


def _result_failed(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    return bool(result.get("error")) or result.get("exit_code") not in (None, 0, "0")


def _result_shape(result: Any) -> ToolUsageResultShape:
    if result is None:
        return ToolUsageResultShape.NONE
    if isinstance(result, dict):
        return ToolUsageResultShape.MAPPING
    if isinstance(result, (list, tuple, set, frozenset)):
        return ToolUsageResultShape.SEQUENCE
    if isinstance(result, (bytes, bytearray, memoryview)):
        return ToolUsageResultShape.BINARY
    if isinstance(result, (str, int, float, bool)):
        return ToolUsageResultShape.SCALAR
    return ToolUsageResultShape.UNKNOWN


def _result_count(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, (dict, list, tuple, set, frozenset, bytes, bytearray, memoryview)):
        return min(len(result), 2**31 - 1)
    return 1


class ToolUsageInvocationSpan:
    def __init__(
        self,
        instrumentation: "ToolUsageInstrumentation",
        *,
        metadata: ToolUsageCallMetadata,
        invocation_id: str,
        started_monotonic: float,
        owner: str | None,
        session_id: str | None,
        run_id: str | None,
        correlation_id: str | None,
        retry_ordinal: int,
        active: bool,
    ) -> None:
        self._instrumentation = instrumentation
        self._metadata = metadata
        self._invocation_id = invocation_id
        self._started_monotonic = started_monotonic
        self._owner = owner
        self._session_id = session_id
        self._run_id = run_id
        self._correlation_id = correlation_id
        self._retry_ordinal = retry_ordinal
        self._active = active
        self._closed = False
        self._lock = Lock()

    @property
    def invocation_id(self) -> str:
        return self._invocation_id

    def finish(
        self,
        outcome: ToolUsageTerminalOutcome,
        *,
        result: Any = None,
    ) -> None:
        with self._lock:
            if self._closed:
                self._instrumentation._increment("duplicate_terminal_attempts")
                return
            self._closed = True
        if not self._active:
            return
        try:
            duration_ms = max(
                0,
                int(
                    (self._instrumentation._monotonic() - self._started_monotonic)
                    * 1_000
                ),
            )
            owner_ref, session_ref, run_ref, correlation_ref = (
                self._instrumentation._pseudonymize_before_store(
                    owner=self._owner,
                    session_id=self._session_id,
                    run_id=self._run_id,
                    correlation_id=self._correlation_id,
                )
            )
            built = ToolUsageEventBuilder.build(
                event_kind=ToolUsageEventKind.TERMINAL,
                invocation_id=self._invocation_id,
                tool_analytics_id=self._metadata.tool_analytics_id,
                tool_family=self._metadata.tool_family,
                tool_source=self._metadata.tool_source,
                surface=self._instrumentation.surface,
                argument_size_bucket=self._metadata.argument_size_bucket,
                result_size_bucket=size_bucket_for_count(_result_count(result)),
                result_shape_bucket=_result_shape(result),
                model_scope=self._instrumentation.model_scope,
                agent_mode=self._instrumentation.agent_mode,
                app_version=self._instrumentation.app_version,
                occurred_at=self._instrumentation._wall_clock(),
                duration_ms=duration_ms,
                status=outcome.status,
                error_class=outcome.error_class,
                blocked_reason_code=outcome.blocked_reason_code,
                retry_ordinal=self._retry_ordinal,
                owner_ref=owner_ref,
                session_ref=session_ref,
                run_ref=run_ref,
                correlation_ref=correlation_ref,
            )
            if built.persistence_allowed and built.event is not None:
                self._instrumentation._emit(built.event, "terminal_events")
        except Exception:
            self._instrumentation._increment("terminal_build_failures")


class ToolUsageInstrumentation:
    """Explicit opt-in consumer; no instance is wired by default."""

    def __init__(
        self,
        *,
        sink: Any = None,
        hmac_key: bytes | None = None,
        surface: ToolUsageSurface = ToolUsageSurface.AGENT,
        model_scope: ToolUsageModelScope = ToolUsageModelScope.UNKNOWN,
        agent_mode: ToolUsageAgentMode = ToolUsageAgentMode.AGENT,
        app_version: str = "unknown",
        incognito: bool = False,
        owner_is_nobody: bool = False,
        trusted_context: ToolUsageTrustedContext | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if trusted_context is not None and not isinstance(
            trusted_context,
            ToolUsageTrustedContext,
        ):
            raise ValueError("trusted_context must be a ToolUsageTrustedContext")
        if trusted_context is not None:
            surface = trusted_context.surface
            model_scope = trusted_context.model_scope
            agent_mode = trusted_context.agent_mode
            incognito = trusted_context.incognito
            owner_is_nobody = trusted_context.owner_is_nobody
        if not isinstance(surface, ToolUsageSurface):
            raise ValueError("surface must be normalized")
        if not isinstance(model_scope, ToolUsageModelScope):
            raise ValueError("model_scope must be normalized")
        if not isinstance(agent_mode, ToolUsageAgentMode):
            raise ValueError("agent_mode must be normalized")
        if not isinstance(app_version, str) or not _SAFE_VERSION_RE.fullmatch(app_version):
            raise ValueError("app_version must be a bounded path-free version")
        if not isinstance(incognito, bool) or not isinstance(owner_is_nobody, bool):
            raise ValueError("privacy flags must be booleans")
        self.sink = sink
        self.hmac_key = hmac_key
        self.surface = surface
        self.model_scope = model_scope
        self.agent_mode = agent_mode
        self.app_version = app_version
        self.incognito = incognito
        self.owner_is_nobody = owner_is_nobody
        self.trusted_context = trusted_context
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._counts: Counter[str] = Counter()
        self._counts_lock = Lock()

    def _increment(self, name: str) -> None:
        with self._counts_lock:
            self._counts[name] = min(self._counts[name] + 1, MAX_DIAGNOSTIC_COUNT)

    def diagnostics(self) -> dict[str, Any]:
        with self._counts_lock:
            counts = dict(sorted(self._counts.items()))
        return {
            "schema_version": "odysseus.tool_usage_instrumentation_diagnostics.v1",
            "counts": counts,
            "raw_content_visible": False,
            "identifiers_visible": False,
            "exception_details_visible": False,
        }

    def _emit(self, event: ToolUsageEvent, counter: str) -> None:
        if self.incognito or self.owner_is_nobody:
            self._increment("suppressed_events")
            return
        if self.sink is None:
            self._increment("discarded_events")
            return
        try:
            if callable(self.sink):
                self.sink(event)
                result = None
            else:
                result = self.sink.write_events((event,))
            if result is not None and int(getattr(result, "failures", 0) or 0) > 0:
                self._increment("sink_failures")
                return
            self._increment(counter)
        except Exception:
            self._increment("sink_failures")

    def _pseudonymize_before_store(
        self,
        *,
        owner: str | None,
        session_id: str | None,
        run_id: str | None,
        correlation_id: str | None,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Convert trusted raw references immediately before event emission."""

        return (
            pseudonymize_reference(owner, hmac_key=self.hmac_key, kind="owner"),
            pseudonymize_reference(
                session_id,
                hmac_key=self.hmac_key,
                kind="session",
            ),
            pseudonymize_reference(run_id, hmac_key=self.hmac_key, kind="run"),
            pseudonymize_reference(
                correlation_id,
                hmac_key=self.hmac_key,
                kind="correlation",
            ),
        )

    def begin(
        self,
        metadata: ToolUsageCallMetadata,
        *,
        owner: str | None = None,
        session_id: str | None = None,
    ) -> ToolUsageInvocationSpan:
        invocation_id = new_invocation_id()
        started_monotonic = self._monotonic()
        run_id = None
        correlation_id = None
        retry_ordinal = 0
        if self.trusted_context is not None:
            owner = self.trusted_context.owner
            session_id = self.trusted_context.session_id
            run_id = self.trusted_context.run_id
            correlation_id = self.trusted_context.correlation_id
            retry_ordinal = self.trusted_context.retry_ordinal
        if self.incognito or self.owner_is_nobody:
            self._increment("suppressed_invocations")
            return ToolUsageInvocationSpan(
                self,
                metadata=metadata,
                invocation_id=invocation_id,
                started_monotonic=started_monotonic,
                owner=owner,
                session_id=session_id,
                run_id=run_id,
                correlation_id=correlation_id,
                retry_ordinal=retry_ordinal,
                active=False,
            )
        try:
            owner_ref, session_ref, run_ref, correlation_ref = (
                self._pseudonymize_before_store(
                    owner=owner,
                    session_id=session_id,
                    run_id=run_id,
                    correlation_id=correlation_id,
                )
            )
            built = ToolUsageEventBuilder.build(
                event_kind=ToolUsageEventKind.STARTED,
                invocation_id=invocation_id,
                tool_analytics_id=metadata.tool_analytics_id,
                tool_family=metadata.tool_family,
                tool_source=metadata.tool_source,
                surface=self.surface,
                argument_size_bucket=metadata.argument_size_bucket,
                result_size_bucket=ToolUsageSizeBucket.NONE,
                result_shape_bucket=ToolUsageResultShape.NONE,
                model_scope=self.model_scope,
                agent_mode=self.agent_mode,
                app_version=self.app_version,
                occurred_at=self._wall_clock(),
                retry_ordinal=retry_ordinal,
                owner_ref=owner_ref,
                session_ref=session_ref,
                run_ref=run_ref,
                correlation_ref=correlation_ref,
                incognito=self.incognito,
                owner_is_nobody=self.owner_is_nobody,
            )
            active = built.persistence_allowed and built.event is not None
            if active:
                self._emit(built.event, "started_events")
            else:
                self._increment("suppressed_invocations")
            return ToolUsageInvocationSpan(
                self,
                metadata=metadata,
                invocation_id=invocation_id,
                started_monotonic=started_monotonic,
                owner=owner,
                session_id=session_id,
                run_id=run_id,
                correlation_id=correlation_id,
                retry_ordinal=retry_ordinal,
                active=active,
            )
        except Exception:
            self._increment("start_build_failures")
            return ToolUsageInvocationSpan(
                self,
                metadata=metadata,
                invocation_id=invocation_id,
                started_monotonic=started_monotonic,
                owner=owner,
                session_id=session_id,
                run_id=run_id,
                correlation_id=correlation_id,
                retry_ordinal=retry_ordinal,
                active=False,
            )


def exception_outcome(exc: BaseException) -> ToolUsageTerminalOutcome:
    if isinstance(exc, asyncio.CancelledError):
        return ToolUsageTerminalOutcome(status=ToolUsageStatus.CANCELLED)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ToolUsageTerminalOutcome(
            status=ToolUsageStatus.FAILED,
            error_class=ToolUsageErrorClass.TIMEOUT,
        )
    return ToolUsageTerminalOutcome(
        status=ToolUsageStatus.FAILED,
        error_class=ToolUsageErrorClass.EXECUTION_ERROR,
    )
