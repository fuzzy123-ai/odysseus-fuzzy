"""Fail-open central instrumentation for privacy-safe tool usage events."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import re
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


def build_tool_usage_call_metadata(block: Any) -> ToolUsageCallMetadata:
    tool_name = str(getattr(block, "tool_type", "") or "").strip()
    content = str(getattr(block, "content", "") or "")
    spec = builtin_spec(tool_name)
    if spec is not None:
        analytics_id = spec.tool_id.replace("_", "-")
        family = spec.family
        source = ToolSource.BUILTIN
    elif tool_name.startswith("mcp__"):
        analytics_id = "dynamic-mcp"
        family = ToolFamily.PLUGINS_MCP
        source = ToolSource.MCP
    else:
        analytics_id = "dynamic-unclassified"
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
        source = ToolSource.DYNAMIC
    argument_bytes = min(
        len(content.encode("utf-8", errors="replace")),
        MAX_ARGUMENT_BYTES,
    )
    return ToolUsageCallMetadata(
        tool_analytics_id=analytics_id,
        tool_family=family,
        tool_source=source,
        argument_size_bucket=size_bucket_for_count(argument_bytes),
        argument_present=bool(content),
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
        owner_ref: str | None,
        session_ref: str | None,
        active: bool,
    ) -> None:
        self._instrumentation = instrumentation
        self._metadata = metadata
        self._invocation_id = invocation_id
        self._started_monotonic = started_monotonic
        self._owner_ref = owner_ref
        self._session_ref = session_ref
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
                owner_ref=self._owner_ref,
                session_ref=self._session_ref,
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
        monotonic: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
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

    def begin(
        self,
        metadata: ToolUsageCallMetadata,
        *,
        owner: str | None = None,
        session_id: str | None = None,
    ) -> ToolUsageInvocationSpan:
        invocation_id = new_invocation_id()
        started_monotonic = self._monotonic()
        owner_ref = None
        session_ref = None
        try:
            owner_ref = pseudonymize_reference(
                owner,
                hmac_key=self.hmac_key,
                kind="owner",
            )
            session_ref = pseudonymize_reference(
                session_id,
                hmac_key=self.hmac_key,
                kind="session",
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
                owner_ref=owner_ref,
                session_ref=session_ref,
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
                owner_ref=owner_ref,
                session_ref=session_ref,
                active=active,
            )
        except Exception:
            self._increment("start_build_failures")
            return ToolUsageInvocationSpan(
                self,
                metadata=metadata,
                invocation_id=invocation_id,
                started_monotonic=started_monotonic,
                owner_ref=None,
                session_ref=None,
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
