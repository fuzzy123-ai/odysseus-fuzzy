"""Privacy-safe value contract for tool usage events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import hmac
import re
import secrets
from typing import Any

from src.tool_catalog import ToolFamily, ToolSource


SCHEMA_VERSION = "odysseus.tool_usage_event.v1"
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_ANALYTICS_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SAFE_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")
_HMAC_REF_RE = re.compile(r"^h1_(owner|session|run|correlation)_[a-f0-9]{32}$")
_MAX_DURATION_MS = 7 * 24 * 60 * 60 * 1000
_MAX_RETRY_ORDINAL = 100


class ToolUsageEventError(ValueError):
    """Raised when an event would violate the persistent allowlist contract."""


class ToolUsageEventKind(StrEnum):
    STARTED = "started"
    TERMINAL = "terminal"


class ToolUsageStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ToolUsageErrorClass(StrEnum):
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    DEPENDENCY_ERROR = "dependency_error"
    VALIDATION_ERROR = "validation_error"
    POLICY_ERROR = "policy_error"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ToolUsageBlockedReason(StrEnum):
    POLICY = "policy"
    PERMISSION = "permission"
    DISABLED = "disabled"
    UNKNOWN_TOOL = "unknown_tool"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"


class ToolUsageSizeBucket(StrEnum):
    NONE = "none"
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


class ToolUsageResultShape(StrEnum):
    NONE = "none"
    SCALAR = "scalar"
    MAPPING = "mapping"
    SEQUENCE = "sequence"
    BINARY = "binary"
    UNKNOWN = "unknown"


class ToolUsageSurface(StrEnum):
    CHAT = "chat"
    AGENT = "agent"
    SCHEDULER = "scheduler"
    API = "api"
    MCP = "mcp"
    SYSTEM = "system"


class ToolUsageModelScope(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ToolUsageAgentMode(StrEnum):
    CHAT = "chat"
    AGENT = "agent"
    BACKGROUND = "background"
    SYSTEM = "system"


class ToolUsageReferenceKind(StrEnum):
    OWNER = "owner"
    SESSION = "session"
    RUN = "run"
    CORRELATION = "correlation"


class ToolUsageSuppressionReason(StrEnum):
    INCOGNITO = "incognito"
    NOBODY = "nobody"


def _enum_value(enum_type: type[StrEnum], value: Any, *, field_name: str) -> StrEnum:
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ToolUsageEventError(f"{field_name} must be one of: {allowed}") from exc


def _strict_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ToolUsageEventError(f"{field_name} must be a boolean")
    return value


def _opaque_id(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_ID_RE.fullmatch(value):
        raise ToolUsageEventError(f"{field_name} must be an opaque identifier")
    return value


def _analytics_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 80
        or not _ANALYTICS_ID_RE.fullmatch(value)
    ):
        raise ToolUsageEventError("tool_analytics_id must be a canonical lowercase slug")
    return value


def _safe_version(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_VERSION_RE.fullmatch(value):
        raise ToolUsageEventError("app_version must be a bounded path-free version")
    return value


def _bounded_int(value: Any, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolUsageEventError(f"{field_name} must be an integer")
    if value < minimum or value > maximum:
        raise ToolUsageEventError(f"{field_name} is outside the bounded range")
    return value


def _occurred_at(value: datetime | None) -> datetime:
    normalized = value if value is not None else datetime.now(timezone.utc)
    if not isinstance(normalized, datetime) or normalized.tzinfo is None:
        raise ToolUsageEventError("occurred_at must be a timezone-aware timestamp")
    return normalized.astimezone(timezone.utc)


def _optional_hmac_ref(value: Any, *, kind: ToolUsageReferenceKind) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _HMAC_REF_RE.fullmatch(value):
        raise ToolUsageEventError(f"{kind.value}_ref must be an HMAC reference")
    if not value.startswith(f"h1_{kind.value}_"):
        raise ToolUsageEventError(f"{kind.value}_ref has the wrong reference namespace")
    return value


def _require_enum_instance(value: Any, enum_type: type[StrEnum], *, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise ToolUsageEventError(f"{field_name} must be a normalized {enum_type.__name__}")


def new_event_id() -> str:
    return f"evt_{secrets.token_urlsafe(18)}"


def new_invocation_id() -> str:
    return f"inv_{secrets.token_urlsafe(18)}"


def size_bucket_for_count(value: int) -> ToolUsageSizeBucket:
    count = _bounded_int(value, field_name="size", minimum=0, maximum=2**31 - 1)
    if count == 0:
        return ToolUsageSizeBucket.NONE
    if count <= 128:
        return ToolUsageSizeBucket.XS
    if count <= 1024:
        return ToolUsageSizeBucket.S
    if count <= 8192:
        return ToolUsageSizeBucket.M
    if count <= 65536:
        return ToolUsageSizeBucket.L
    return ToolUsageSizeBucket.XL


def pseudonymize_reference(
    value: str | None,
    *,
    hmac_key: bytes | None,
    kind: ToolUsageReferenceKind | str,
) -> str | None:
    """Return a namespaced HMAC reference or no reference when no key exists."""

    normalized_kind = _enum_value(ToolUsageReferenceKind, kind, field_name="kind")
    if hmac_key is None:
        return None
    if not isinstance(hmac_key, bytes) or len(hmac_key) < 16:
        raise ToolUsageEventError("hmac_key must contain at least 16 bytes")
    if not isinstance(value, str) or not value:
        return None
    digest = hmac.new(
        hmac_key,
        f"{normalized_kind.value}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"h1_{normalized_kind.value}_{digest}"


@dataclass(frozen=True, slots=True)
class ToolUsageEvent:
    schema_version: str
    event_id: str
    invocation_id: str
    event_kind: ToolUsageEventKind
    occurred_at: datetime
    duration_ms: int | None
    tool_analytics_id: str
    tool_family: ToolFamily
    tool_source: ToolSource
    surface: ToolUsageSurface
    status: ToolUsageStatus | None
    error_class: ToolUsageErrorClass | None
    blocked_reason_code: ToolUsageBlockedReason | None
    retry_ordinal: int
    argument_size_bucket: ToolUsageSizeBucket
    result_size_bucket: ToolUsageSizeBucket
    result_shape_bucket: ToolUsageResultShape
    owner_ref: str | None
    session_ref: str | None
    run_ref: str | None
    correlation_ref: str | None
    model_scope: ToolUsageModelScope
    agent_mode: ToolUsageAgentMode
    app_version: str

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ToolUsageEventError("schema_version is immutable")
        _opaque_id(self.event_id, field_name="event_id")
        _opaque_id(self.invocation_id, field_name="invocation_id")
        _analytics_id(self.tool_analytics_id)
        _safe_version(self.app_version)
        if not isinstance(self.occurred_at, datetime) or self.occurred_at.tzinfo is None:
            raise ToolUsageEventError("occurred_at must be a timezone-aware timestamp")
        if self.occurred_at.utcoffset() != timezone.utc.utcoffset(self.occurred_at):
            raise ToolUsageEventError("occurred_at must be normalized to UTC")
        if self.duration_ms is not None:
            _bounded_int(
                self.duration_ms,
                field_name="duration_ms",
                minimum=0,
                maximum=_MAX_DURATION_MS,
            )
        _bounded_int(
            self.retry_ordinal,
            field_name="retry_ordinal",
            minimum=0,
            maximum=_MAX_RETRY_ORDINAL,
        )
        for field_name, value, enum_type in (
            ("event_kind", self.event_kind, ToolUsageEventKind),
            ("tool_family", self.tool_family, ToolFamily),
            ("tool_source", self.tool_source, ToolSource),
            ("surface", self.surface, ToolUsageSurface),
            ("argument_size_bucket", self.argument_size_bucket, ToolUsageSizeBucket),
            ("result_size_bucket", self.result_size_bucket, ToolUsageSizeBucket),
            ("result_shape_bucket", self.result_shape_bucket, ToolUsageResultShape),
            ("model_scope", self.model_scope, ToolUsageModelScope),
            ("agent_mode", self.agent_mode, ToolUsageAgentMode),
        ):
            _require_enum_instance(value, enum_type, field_name=field_name)
        if self.status is not None:
            _require_enum_instance(self.status, ToolUsageStatus, field_name="status")
        if self.error_class is not None:
            _require_enum_instance(
                self.error_class,
                ToolUsageErrorClass,
                field_name="error_class",
            )
        if self.blocked_reason_code is not None:
            _require_enum_instance(
                self.blocked_reason_code,
                ToolUsageBlockedReason,
                field_name="blocked_reason_code",
            )
        _optional_hmac_ref(self.owner_ref, kind=ToolUsageReferenceKind.OWNER)
        _optional_hmac_ref(self.session_ref, kind=ToolUsageReferenceKind.SESSION)
        _optional_hmac_ref(self.run_ref, kind=ToolUsageReferenceKind.RUN)
        _optional_hmac_ref(
            self.correlation_ref,
            kind=ToolUsageReferenceKind.CORRELATION,
        )

        if self.event_kind == ToolUsageEventKind.STARTED:
            if any(
                value is not None
                for value in (
                    self.status,
                    self.error_class,
                    self.blocked_reason_code,
                    self.duration_ms,
                )
            ):
                raise ToolUsageEventError("started event contains terminal metadata")
            if self.result_size_bucket != ToolUsageSizeBucket.NONE:
                raise ToolUsageEventError("started event contains a result size")
            if self.result_shape_bucket != ToolUsageResultShape.NONE:
                raise ToolUsageEventError("started event contains a result shape")
        else:
            if self.status is None or self.duration_ms is None:
                raise ToolUsageEventError("terminal event is missing status or duration")
            if self.status == ToolUsageStatus.SUCCEEDED:
                valid = self.error_class is None and self.blocked_reason_code is None
            elif self.status == ToolUsageStatus.FAILED:
                valid = self.error_class is not None and self.blocked_reason_code is None
            elif self.status in {ToolUsageStatus.BLOCKED, ToolUsageStatus.REJECTED}:
                valid = self.error_class is None and self.blocked_reason_code is not None
            else:
                valid = self.error_class is None and self.blocked_reason_code is None
            if not valid:
                raise ToolUsageEventError("terminal event status metadata is inconsistent")

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "agent_mode": self.agent_mode.value,
            "app_version": self.app_version,
            "argument_size_bucket": self.argument_size_bucket.value,
            "blocked_reason_code": (
                self.blocked_reason_code.value if self.blocked_reason_code else None
            ),
            "correlation_ref": self.correlation_ref,
            "duration_ms": self.duration_ms,
            "error_class": self.error_class.value if self.error_class else None,
            "event_id": self.event_id,
            "event_kind": self.event_kind.value,
            "invocation_id": self.invocation_id,
            "model_scope": self.model_scope.value,
            "occurred_at": self.occurred_at.isoformat().replace("+00:00", "Z"),
            "owner_ref": self.owner_ref,
            "raw_content_visible": False,
            "result_shape_bucket": self.result_shape_bucket.value,
            "result_size_bucket": self.result_size_bucket.value,
            "retry_ordinal": self.retry_ordinal,
            "run_ref": self.run_ref,
            "schema_version": self.schema_version,
            "session_ref": self.session_ref,
            "status": self.status.value if self.status else None,
            "surface": self.surface.value,
            "tool_analytics_id": self.tool_analytics_id,
            "tool_family": self.tool_family.value,
            "tool_source": self.tool_source.value,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageBuildResult:
    event: ToolUsageEvent | None
    persistence_allowed: bool
    suppression_reason: ToolUsageSuppressionReason | None

    def __post_init__(self) -> None:
        _strict_bool(self.persistence_allowed, field_name="persistence_allowed")
        if self.persistence_allowed:
            if not isinstance(self.event, ToolUsageEvent) or self.suppression_reason is not None:
                raise ToolUsageEventError("persistable results require exactly one event")
        else:
            if self.event is not None or not isinstance(
                self.suppression_reason,
                ToolUsageSuppressionReason,
            ):
                raise ToolUsageEventError("suppressed results require a bounded reason and no event")

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_safe_dict() if self.event else None,
            "persistence_allowed": self.persistence_allowed,
            "raw_content_visible": False,
            "suppression_reason": (
                self.suppression_reason.value if self.suppression_reason else None
            ),
        }


class ToolUsageEventBuilder:
    """Allowlist-only builder for one started or terminal usage event."""

    @staticmethod
    def build(
        *,
        event_kind: ToolUsageEventKind | str,
        invocation_id: Any,
        tool_analytics_id: Any,
        tool_family: ToolFamily | str,
        tool_source: ToolSource | str,
        surface: ToolUsageSurface | str,
        argument_size_bucket: ToolUsageSizeBucket | str,
        result_size_bucket: ToolUsageSizeBucket | str,
        result_shape_bucket: ToolUsageResultShape | str,
        model_scope: ToolUsageModelScope | str,
        agent_mode: ToolUsageAgentMode | str,
        app_version: Any,
        event_id: Any = None,
        occurred_at: datetime | None = None,
        duration_ms: Any = None,
        status: ToolUsageStatus | str | None = None,
        error_class: ToolUsageErrorClass | str | None = None,
        blocked_reason_code: ToolUsageBlockedReason | str | None = None,
        retry_ordinal: Any = 0,
        owner_ref: Any = None,
        session_ref: Any = None,
        run_ref: Any = None,
        correlation_ref: Any = None,
        incognito: bool = False,
        owner_is_nobody: bool = False,
    ) -> ToolUsageBuildResult:
        normalized_incognito = _strict_bool(incognito, field_name="incognito")
        normalized_nobody = _strict_bool(owner_is_nobody, field_name="owner_is_nobody")
        if normalized_incognito:
            return ToolUsageBuildResult(
                event=None,
                persistence_allowed=False,
                suppression_reason=ToolUsageSuppressionReason.INCOGNITO,
            )
        if normalized_nobody:
            return ToolUsageBuildResult(
                event=None,
                persistence_allowed=False,
                suppression_reason=ToolUsageSuppressionReason.NOBODY,
            )

        normalized_kind = _enum_value(
            ToolUsageEventKind,
            event_kind,
            field_name="event_kind",
        )
        normalized_status = (
            _enum_value(ToolUsageStatus, status, field_name="status")
            if status is not None
            else None
        )
        normalized_error = (
            _enum_value(ToolUsageErrorClass, error_class, field_name="error_class")
            if error_class is not None
            else None
        )
        normalized_blocked = (
            _enum_value(
                ToolUsageBlockedReason,
                blocked_reason_code,
                field_name="blocked_reason_code",
            )
            if blocked_reason_code is not None
            else None
        )
        normalized_duration = (
            _bounded_int(
                duration_ms,
                field_name="duration_ms",
                minimum=0,
                maximum=_MAX_DURATION_MS,
            )
            if duration_ms is not None
            else None
        )

        if normalized_kind == ToolUsageEventKind.STARTED:
            if any(
                value is not None
                for value in (
                    normalized_status,
                    normalized_error,
                    normalized_blocked,
                    normalized_duration,
                )
            ):
                raise ToolUsageEventError(
                    "started events cannot contain terminal status, error, reason or duration"
                )
            if result_size_bucket != ToolUsageSizeBucket.NONE and str(result_size_bucket) != "none":
                raise ToolUsageEventError("started events require result_size_bucket=none")
            if result_shape_bucket != ToolUsageResultShape.NONE and str(result_shape_bucket) != "none":
                raise ToolUsageEventError("started events require result_shape_bucket=none")
        else:
            if normalized_status is None or normalized_duration is None:
                raise ToolUsageEventError("terminal events require status and duration_ms")
            if normalized_status == ToolUsageStatus.SUCCEEDED:
                if normalized_error is not None or normalized_blocked is not None:
                    raise ToolUsageEventError("succeeded events cannot contain error metadata")
            elif normalized_status == ToolUsageStatus.FAILED:
                if normalized_error is None or normalized_blocked is not None:
                    raise ToolUsageEventError(
                        "failed events require a bounded error_class and no blocked reason"
                    )
            elif normalized_status in {ToolUsageStatus.BLOCKED, ToolUsageStatus.REJECTED}:
                if normalized_blocked is None or normalized_error is not None:
                    raise ToolUsageEventError(
                        "blocked and rejected events require a reason and no error class"
                    )
            elif normalized_status == ToolUsageStatus.CANCELLED:
                if normalized_error is not None or normalized_blocked is not None:
                    raise ToolUsageEventError("cancelled events cannot contain error metadata")

        normalized_event_id = new_event_id() if event_id is None else _opaque_id(
            event_id,
            field_name="event_id",
        )
        event = ToolUsageEvent(
            schema_version=SCHEMA_VERSION,
            event_id=normalized_event_id,
            invocation_id=_opaque_id(invocation_id, field_name="invocation_id"),
            event_kind=normalized_kind,
            occurred_at=_occurred_at(occurred_at),
            duration_ms=normalized_duration,
            tool_analytics_id=_analytics_id(tool_analytics_id),
            tool_family=_enum_value(ToolFamily, tool_family, field_name="tool_family"),
            tool_source=_enum_value(ToolSource, tool_source, field_name="tool_source"),
            surface=_enum_value(ToolUsageSurface, surface, field_name="surface"),
            status=normalized_status,
            error_class=normalized_error,
            blocked_reason_code=normalized_blocked,
            retry_ordinal=_bounded_int(
                retry_ordinal,
                field_name="retry_ordinal",
                minimum=0,
                maximum=_MAX_RETRY_ORDINAL,
            ),
            argument_size_bucket=_enum_value(
                ToolUsageSizeBucket,
                argument_size_bucket,
                field_name="argument_size_bucket",
            ),
            result_size_bucket=_enum_value(
                ToolUsageSizeBucket,
                result_size_bucket,
                field_name="result_size_bucket",
            ),
            result_shape_bucket=_enum_value(
                ToolUsageResultShape,
                result_shape_bucket,
                field_name="result_shape_bucket",
            ),
            owner_ref=_optional_hmac_ref(owner_ref, kind=ToolUsageReferenceKind.OWNER),
            session_ref=_optional_hmac_ref(
                session_ref,
                kind=ToolUsageReferenceKind.SESSION,
            ),
            run_ref=_optional_hmac_ref(run_ref, kind=ToolUsageReferenceKind.RUN),
            correlation_ref=_optional_hmac_ref(
                correlation_ref,
                kind=ToolUsageReferenceKind.CORRELATION,
            ),
            model_scope=_enum_value(
                ToolUsageModelScope,
                model_scope,
                field_name="model_scope",
            ),
            agent_mode=_enum_value(
                ToolUsageAgentMode,
                agent_mode,
                field_name="agent_mode",
            ),
            app_version=_safe_version(app_version),
        )
        return ToolUsageBuildResult(
            event=event,
            persistence_allowed=True,
            suppression_reason=None,
        )
