"""Default-off, fail-open instrumentation for the central tool wrapper."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import secrets
import threading
import time
from typing import Any, Callable

from src.builtin_tool_catalog import build_tool_analytics_identity_contract
from src.tool_catalog import (
    ToolAnalyticsIdentityV1,
    ToolAvailability,
    ToolDescriptorV2,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageBlockedReason,
    ToolUsageErrorClass,
    ToolUsageEventBuilder,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageResultShape,
    ToolUsageStatus,
    ToolUsageSurface,
)
from src.tool_usage_context import TrustedToolUsageContext


_MAX_DURATION_MS = 86_400_000
_MAX_SIZE_BYTES = 1 << 40
_FAILURE_COUNT_MAX = 1_000_000_000


@dataclass(frozen=True, slots=True)
class NormalizedToolUsageOutcome:
    status: ToolUsageStatus
    error_class: ToolUsageErrorClass | None
    blocked_reason: ToolUsageBlockedReason | None
    result_size_bytes: int
    result_shape: ToolUsageResultShape
    ai_lens_status: str


@dataclass(slots=True)
class ToolUsageInvocation:
    invocation_id: str
    descriptor: ToolDescriptorV2
    argument_size_bytes: int
    retry_ordinal: int
    _terminal_emitted: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def claim_terminal(self) -> bool:
        with self._lock:
            if self._terminal_emitted:
                return False
            self._terminal_emitted = True
            return True


def normalize_tool_usage_outcome(
    *,
    description: str = "",
    result: Any = None,
    exception: bool = False,
    cancelled: bool = False,
) -> NormalizedToolUsageOutcome:
    """Map runtime outcomes to bounded status classes without retaining content."""

    result_size = _safe_size(result)
    result_shape = _result_shape(result)
    if cancelled:
        return NormalizedToolUsageOutcome(
            status=ToolUsageStatus.CANCELLED,
            error_class=ToolUsageErrorClass.CANCELLED,
            blocked_reason=None,
            result_size_bytes=0,
            result_shape=ToolUsageResultShape.NONE,
            ai_lens_status="failed",
        )
    if exception:
        return NormalizedToolUsageOutcome(
            status=ToolUsageStatus.FAILED,
            error_class=ToolUsageErrorClass.EXECUTION,
            blocked_reason=None,
            result_size_bytes=0,
            result_shape=ToolUsageResultShape.NONE,
            ai_lens_status="failed",
        )
    if not _result_failed(result):
        return NormalizedToolUsageOutcome(
            status=ToolUsageStatus.SUCCEEDED,
            error_class=None,
            blocked_reason=None,
            result_size_bytes=result_size,
            result_shape=result_shape,
            ai_lens_status="succeeded",
        )

    description_code = str(description or "").lower()
    error_code = ""
    if isinstance(result, Mapping):
        error_code = str(result.get("error") or "").lower()
    combined = f"{description_code} {error_code}"
    if (
        description_code.startswith("unknown:")
        or "unknown tool" in combined
        or "invalid mcp tool name" in combined
    ):
        return NormalizedToolUsageOutcome(
            status=ToolUsageStatus.REJECTED,
            error_class=None,
            blocked_reason=ToolUsageBlockedReason.UNKNOWN_TOOL,
            result_size_bytes=result_size,
            result_shape=result_shape,
            ai_lens_status="failed",
        )
    if "disabled" in combined:
        blocked_reason = ToolUsageBlockedReason.DISABLED
    elif any(marker in combined for marker in ("permission", "requires an admin", "restricted to admin")):
        blocked_reason = ToolUsageBlockedReason.PERMISSION
    elif "blocked" in combined:
        blocked_reason = ToolUsageBlockedReason.POLICY
    else:
        return NormalizedToolUsageOutcome(
            status=ToolUsageStatus.FAILED,
            error_class=ToolUsageErrorClass.EXECUTION,
            blocked_reason=None,
            result_size_bytes=result_size,
            result_shape=result_shape,
            ai_lens_status="failed",
        )
    return NormalizedToolUsageOutcome(
        status=ToolUsageStatus.BLOCKED,
        error_class=None,
        blocked_reason=blocked_reason,
        result_size_bytes=result_size,
        result_shape=result_shape,
        ai_lens_status="blocked",
    )


def normalize_bypass_tool_usage_outcome(
    result: Any,
    *,
    succeeded: bool,
    rejected_unknown: bool = False,
) -> NormalizedToolUsageOutcome:
    """Normalize a proven non-wrapper execution without persisting its content."""

    if not isinstance(succeeded, bool) or not isinstance(rejected_unknown, bool):
        raise TypeError("bypass outcome flags must be boolean")
    if rejected_unknown:
        return NormalizedToolUsageOutcome(
            status=ToolUsageStatus.REJECTED,
            error_class=None,
            blocked_reason=ToolUsageBlockedReason.UNKNOWN_TOOL,
            result_size_bytes=_safe_size(result),
            result_shape=_result_shape(result),
            ai_lens_status="failed",
        )
    if succeeded:
        return NormalizedToolUsageOutcome(
            status=ToolUsageStatus.SUCCEEDED,
            error_class=None,
            blocked_reason=None,
            result_size_bytes=_safe_size(result),
            result_shape=_result_shape(result),
            ai_lens_status="succeeded",
        )
    return NormalizedToolUsageOutcome(
        status=ToolUsageStatus.FAILED,
        error_class=ToolUsageErrorClass.EXECUTION,
        blocked_reason=None,
        result_size_bytes=_safe_size(result),
        result_shape=_result_shape(result),
        ai_lens_status="failed",
    )


class ToolUsageInstrumentation:
    """Synchronous best-effort event consumer injected into execute_tool_block."""

    def __init__(
        self,
        *,
        builder: ToolUsageEventBuilder,
        sink: Any,
        identity_contract: Any = None,
        surface: ToolUsageSurface | str = ToolUsageSurface.AGENT,
        agent_mode: ToolUsageAgentMode | str = ToolUsageAgentMode.AGENT,
        model_scope: ToolUsageModelScope | str = ToolUsageModelScope.UNKNOWN,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        context: TrustedToolUsageContext | None = None,
    ) -> None:
        if not isinstance(builder, ToolUsageEventBuilder):
            raise TypeError("builder must be ToolUsageEventBuilder")
        self._builder = builder
        self._sink = sink
        self._identity_contract = identity_contract or build_tool_analytics_identity_contract()
        self._context = context or TrustedToolUsageContext.create(
            surface=surface,
            agent_mode=agent_mode,
            model_scope=model_scope,
        )
        self._clock = clock
        self._failure_counts: Counter[str] = Counter()
        self._emitted_counts: Counter[str] = Counter()
        self._suppressed_counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def with_context(self, context: TrustedToolUsageContext) -> "ToolUsageInstrumentation":
        if not isinstance(context, TrustedToolUsageContext):
            raise TypeError("context must be TrustedToolUsageContext")
        return ToolUsageInstrumentation(
            builder=self._builder,
            sink=self._sink,
            identity_contract=self._identity_contract,
            clock=self._clock,
            context=context,
        )

    def begin(
        self,
        tool_name: Any,
        argument: Any,
        *,
        trusted_source: ToolSource | str | None = None,
        retry_ordinal: int = 0,
    ) -> ToolUsageInvocation | None:
        if not self._context.persistence_allowed:
            with self._lock:
                reason = "incognito" if self._context.incognito else "nobody"
                self._suppressed_counts[reason] = min(
                    _FAILURE_COUNT_MAX,
                    self._suppressed_counts[reason] + 1,
                )
            return None
        try:
            identity = self._resolve_identity(tool_name, trusted_source=trusted_source)
            invocation = ToolUsageInvocation(
                invocation_id="tui_" + secrets.token_hex(16),
                descriptor=self._descriptor_for(identity),
                argument_size_bytes=_safe_size(argument),
                retry_ordinal=retry_ordinal,
            )
            event = self._builder.build(
                descriptor=invocation.descriptor,
                event_kind=ToolUsageEventKind.STARTED,
                surface=self._context.surface,
                agent_mode=self._context.agent_mode,
                model_scope=self._context.model_scope,
                retry_ordinal=invocation.retry_ordinal,
                argument_size_bytes=invocation.argument_size_bytes,
                owner_identity=self._context.owner_identity,
                session_identity=self._context.session_identity,
                run_identity=self._context.run_identity,
                correlation_identity=self._context.correlation_identity,
                invocation_id=invocation.invocation_id,
                occurred_at=self._clock(),
            )
            self._emit(event, "started")
            return invocation
        except (Exception, asyncio.CancelledError):
            self._record_failure("begin_failure")
            return None

    def finish(
        self,
        invocation: ToolUsageInvocation | None,
        *,
        outcome: NormalizedToolUsageOutcome,
        duration_ms: int,
    ) -> None:
        if invocation is None or not invocation.claim_terminal():
            return
        try:
            event = self._builder.build(
                descriptor=invocation.descriptor,
                event_kind=ToolUsageEventKind.TERMINAL,
                surface=self._context.surface,
                agent_mode=self._context.agent_mode,
                model_scope=self._context.model_scope,
                status=outcome.status,
                error_class=outcome.error_class,
                blocked_reason_code=outcome.blocked_reason,
                retry_ordinal=invocation.retry_ordinal,
                duration_ms=max(0, min(int(duration_ms), _MAX_DURATION_MS)),
                argument_size_bytes=invocation.argument_size_bytes,
                result_size_bytes=outcome.result_size_bytes,
                result_shape=outcome.result_shape,
                owner_identity=self._context.owner_identity,
                session_identity=self._context.session_identity,
                run_identity=self._context.run_identity,
                correlation_identity=self._context.correlation_identity,
                invocation_id=invocation.invocation_id,
                occurred_at=self._clock(),
            )
            self._emit(event, "terminal")
        except (Exception, asyncio.CancelledError):
            self._record_failure("terminal_failure")

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": "odysseus.tool_usage_instrumentation.v1",
                "emitted": dict(sorted(self._emitted_counts.items())),
                "failures": dict(sorted(self._failure_counts.items())),
                "suppressed": dict(sorted(self._suppressed_counts.items())),
                "capture_default_enabled": False,
                "retry_semantics": "zero_based_logical_attempt",
                "correlation_semantics": "trusted_context_hmac_only",
                "raw_content_visible": False,
            }

    def _resolve_identity(
        self,
        tool_name: Any,
        *,
        trusted_source: ToolSource | str | None = None,
    ) -> ToolAnalyticsIdentityV1:
        name = str(tool_name or "")
        source: ToolSource | str | None = trusted_source
        if source is None and name.startswith("mcp__"):
            source = ToolSource.MCP
        elif source is None:
            try:
                from src.tool_registry import get_tool

                if get_tool(name) is not None:
                    source = ToolSource.PLUGIN
            except Exception:
                source = None
        return self._identity_contract.resolve(name, source=source)

    def _descriptor_for(self, identity: ToolAnalyticsIdentityV1) -> ToolDescriptorV2:
        if identity.canonical_tool_id is not None:
            for descriptor in self._identity_contract.catalog.descriptors:
                if descriptor.tool_id == identity.canonical_tool_id:
                    return descriptor
            raise ValueError("canonical analytics descriptor is missing")
        source_id = (
            "analytics-contract"
            if identity.source in {ToolSource.PLUGIN, ToolSource.MCP, ToolSource.PROVIDER}
            else None
        )
        return ToolDescriptorV2.create(
            tool_id=identity.analytics_id,
            analytics_id=identity.analytics_id,
            display_name="Unclassified Tool Source",
            description="Aggregate-only identity for an unreviewed tool source.",
            family=ToolFamily.UNCLASSIFIED_DYNAMIC,
            source=identity.source,
            source_id=source_id,
            lifecycle=ToolLifecycle.EXPERIMENTAL,
            availability=ToolAvailability.UNAVAILABLE,
            availability_reason="unclassified-dynamic-source",
            default_enabled=False,
            default_visibility=ToolVisibility.UNAVAILABLE,
            risk_level=ToolRiskLevel.DANGEROUS,
            permission=ToolPermission.ADMIN,
            effect_class=ToolEffectClass.CONTROL,
            requires_confirmation=True,
            schema_ref=f"analytics:{identity.analytics_id}",
            handler_ref=f"analytics:{identity.analytics_id}",
            prompt_ref=None,
            introduced_in="0.24.0",
        )

    def _emit(self, event: Any, event_kind: str) -> None:
        try:
            if hasattr(self._sink, "append_best_effort"):
                result = self._sink.append_best_effort((event,))
            elif callable(self._sink):
                result = self._sink(event)
            else:
                raise TypeError("tool usage sink is not callable")
            if inspect.isawaitable(result):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise TypeError("tool usage sink must be synchronous")
            if getattr(result, "failure_count", 0):
                self._record_failure("sink_rejected")
            with self._lock:
                self._emitted_counts[event_kind] = min(
                    _FAILURE_COUNT_MAX,
                    self._emitted_counts[event_kind] + 1,
                )
        except (Exception, asyncio.CancelledError):
            self._record_failure("sink_failure")

    def _record_failure(self, category: str) -> None:
        with self._lock:
            self._failure_counts[category] = min(
                _FAILURE_COUNT_MAX,
                self._failure_counts[category] + 1,
            )


async def execute_instrumented_bypass(
    instrumentation: Any,
    *,
    tool_name: Any,
    argument: Any,
    operation: Callable[[], Any],
    trusted_source: ToolSource | str | None = None,
    retry_ordinal: int = 0,
    outcome_factory: Callable[[Any], NormalizedToolUsageOutcome] | None = None,
) -> Any:
    """Instrument one proven non-wrapper execution while preserving its result.

    Callers invoke this only at an actual execution boundary. Discovery,
    planning, previews and cache hits therefore create no invocation. The retry
    ordinal is zero-based and describes a logical caller attempt; transport
    reconnects inside that attempt are deliberately not counted again.
    """

    started_at = time.perf_counter()
    invocation = None
    if instrumentation is not None:
        try:
            invocation = instrumentation.begin(
                tool_name,
                argument,
                trusted_source=trusted_source,
                retry_ordinal=retry_ordinal,
            )
        except (Exception, asyncio.CancelledError):
            invocation = None
    try:
        result = operation()
        if inspect.isawaitable(result):
            result = await result
    except asyncio.CancelledError:
        _finish_bypass_best_effort(
            instrumentation,
            invocation,
            normalize_tool_usage_outcome(cancelled=True),
            started_at,
        )
        raise
    except Exception:
        _finish_bypass_best_effort(
            instrumentation,
            invocation,
            normalize_tool_usage_outcome(exception=True),
            started_at,
        )
        raise

    try:
        outcome = (
            outcome_factory(result)
            if outcome_factory is not None
            else normalize_tool_usage_outcome(result=result)
        )
    except (Exception, asyncio.CancelledError):
        outcome = None
    _finish_bypass_best_effort(
        instrumentation,
        invocation,
        outcome,
        started_at,
    )
    return result


def _finish_bypass_best_effort(
    instrumentation: Any,
    invocation: Any,
    outcome: NormalizedToolUsageOutcome | None,
    started_at: float,
) -> None:
    if instrumentation is None or invocation is None or outcome is None:
        return
    try:
        duration_ms = max(1, int((time.perf_counter() - started_at) * 1000))
        instrumentation.finish(
            invocation,
            outcome=outcome,
            duration_ms=duration_ms,
        )
    except (Exception, asyncio.CancelledError):
        pass


def _result_failed(result: Any) -> bool:
    if not isinstance(result, Mapping):
        return True
    return bool(result.get("error")) or result.get("exit_code") not in (None, 0, "0")


def _safe_size(value: Any) -> int:
    if value is None:
        return 0
    try:
        if isinstance(value, bytes):
            size = len(value)
        elif isinstance(value, str):
            size = len(value.encode("utf-8", errors="replace"))
        else:
            size = len(repr(value).encode("utf-8", errors="replace"))
    except Exception:
        return 0
    return max(0, min(size, _MAX_SIZE_BYTES))


def _result_shape(result: Any) -> ToolUsageResultShape:
    if result is None:
        return ToolUsageResultShape.NONE
    if isinstance(result, bytes):
        return ToolUsageResultShape.BINARY
    if isinstance(result, Mapping):
        return ToolUsageResultShape.MAPPING
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        return ToolUsageResultShape.SEQUENCE
    if isinstance(result, (str, int, float, bool)):
        return ToolUsageResultShape.SCALAR
    return ToolUsageResultShape.UNKNOWN
