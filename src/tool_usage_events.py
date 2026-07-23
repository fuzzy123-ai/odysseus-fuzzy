"""Privacy-safe, allowlist-only tool usage event contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import hmac
import json
import re
import secrets
from typing import Any, ClassVar, Mapping

from src.tool_catalog import ToolDescriptorV2, ToolFamily, ToolSource


class ToolUsageEventError(ValueError):
    """Raised when a usage event cannot be represented without raw content."""


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
    EXECUTION = "execution"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    POLICY = "policy"
    PERMISSION = "permission"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ToolUsageBlockedReason(StrEnum):
    POLICY = "policy"
    PERMISSION = "permission"
    DISABLED = "disabled"
    UNKNOWN_TOOL = "unknown_tool"
    UNAVAILABLE = "unavailable"
    CONFIRMATION_REQUIRED = "confirmation_required"


class ToolUsageSurface(StrEnum):
    CHAT = "chat"
    AGENT = "agent"
    SCHEDULER = "scheduler"
    API = "api"
    MCP = "mcp"
    SYSTEM = "system"


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


class ToolUsageModelScope(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ToolUsageAgentMode(StrEnum):
    CHAT = "chat"
    AGENT = "agent"
    BACKGROUND_SYSTEM = "background_system"


class ToolUsageReferenceState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_REQUESTED = "not_requested"


class ToolUsagePersistenceReason(StrEnum):
    ALLOWED = "allowed"
    INCOGNITO = "incognito"
    NOBODY = "nobody"


_OPAQUE_ID_RE = re.compile(r"^(?:tue|tui)_[0-9a-f]{32}$")
_ANALYTICS_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
_HMAC_REF_RE = re.compile(r"^h1_[0-9a-f]{32}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.(?:[0-9]+|x)){1,2}(?:[-+][A-Za-z0-9.-]+)?$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_MAX_DURATION_MS = 86_400_000
_MAX_SIZE_BYTES = 1 << 40
_MAX_RETRY_ORDINAL = 100


def _enum(enum_type, value: Any, *, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except (TypeError, ValueError) as exc:
        raise ToolUsageEventError(f"{field_name} is not a controlled value") from exc


def _opaque_id(value: Any, *, prefix: str, field_name: str) -> str:
    if value is None or value == "":
        return f"{prefix}_{secrets.token_hex(16)}"
    if callable(value):
        raise ToolUsageEventError(f"{field_name} must not be callable")
    text = str(value)
    if not _OPAQUE_ID_RE.fullmatch(text) or not text.startswith(prefix + "_"):
        raise ToolUsageEventError(f"{field_name} must be an opaque {prefix} identifier")
    return text


def _analytics_id(value: Any) -> str:
    if callable(value):
        raise ToolUsageEventError("tool_analytics_id must not be callable")
    text = str(value or "")
    if not _ANALYTICS_ID_RE.fullmatch(text):
        raise ToolUsageEventError("tool_analytics_id must be a canonical TAX identity")
    return text


def _app_version(value: Any) -> str:
    if callable(value):
        raise ToolUsageEventError("app_version must not be callable")
    text = str(value or "")
    if not _VERSION_RE.fullmatch(text):
        raise ToolUsageEventError("app_version must be machine-readable")
    return text


def _timestamp(value: datetime | str | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value)
        if not _TIMESTAMP_RE.fullmatch(text):
            raise ToolUsageEventError("occurred_at must be a normalized UTC timestamp")
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00")
        except ValueError as exc:
            raise ToolUsageEventError("occurred_at is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ToolUsageEventError("occurred_at must be timezone-aware")
    normalized = parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return normalized.replace("+00:00", "Z")


def _bounded_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
    allow_none: bool,
) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolUsageEventError(f"{field_name} must be an integer")
    if value < minimum or value > maximum:
        raise ToolUsageEventError(f"{field_name} is outside the bounded range")
    return value


def size_bucket(size_bytes: int | None) -> ToolUsageSizeBucket:
    value = _bounded_int(
        0 if size_bytes is None else size_bytes,
        field_name="size_bytes",
        minimum=0,
        maximum=_MAX_SIZE_BYTES,
        allow_none=False,
    )
    if value == 0:
        return ToolUsageSizeBucket.NONE
    if value <= 256:
        return ToolUsageSizeBucket.XS
    if value <= 1024:
        return ToolUsageSizeBucket.S
    if value <= 4096:
        return ToolUsageSizeBucket.M
    if value <= 16384:
        return ToolUsageSizeBucket.L
    return ToolUsageSizeBucket.XL


def pseudonymize_reference(kind: str, value: Any, *, key: bytes | None) -> str | None:
    """Return a domain-separated HMAC reference or no reference without a key."""
    if value is None or value == "":
        return None
    if callable(value):
        raise ToolUsageEventError("reference values must not be callable")
    raw = str(value)
    if len(raw) > 512:
        raise ToolUsageEventError("reference value exceeds the bounded input length")
    if key is None:
        return None
    if not isinstance(key, bytes) or len(key) < 32:
        raise ToolUsageEventError("HMAC key must contain at least 32 bytes")
    if kind not in {"owner", "session", "run", "correlation"}:
        raise ToolUsageEventError("reference kind is not allowlisted")
    digest = hmac.new(key, f"{kind}\0{raw}".encode("utf-8"), hashlib.sha256).hexdigest()
    return "h1_" + digest[:32]


def _hmac_ref(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if callable(value):
        raise ToolUsageEventError(f"{field_name} must not be callable")
    text = str(value)
    if not _HMAC_REF_RE.fullmatch(text):
        raise ToolUsageEventError(f"{field_name} must be a keyed HMAC reference")
    return text


@dataclass(frozen=True, slots=True)
class ToolUsageEventV1:
    SCHEMA_ID: ClassVar[str] = "odysseus.tool_usage_event.v1"

    event_id: str
    invocation_id: str
    event_kind: ToolUsageEventKind
    occurred_at: str
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
    reference_state: ToolUsageReferenceState
    model_scope: ToolUsageModelScope
    agent_mode: ToolUsageAgentMode
    app_version: str
    persistence_allowed: bool
    persistence_reason: ToolUsagePersistenceReason

    SERIALIZED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "event_id",
            "invocation_id",
            "event_kind",
            "occurred_at",
            "duration_ms",
            "tool_analytics_id",
            "tool_family",
            "tool_source",
            "surface",
            "status",
            "error_class",
            "blocked_reason_code",
            "retry_ordinal",
            "argument_size_bucket",
            "result_size_bucket",
            "result_shape_bucket",
            "owner_ref",
            "session_ref",
            "run_ref",
            "correlation_ref",
            "reference_state",
            "model_scope",
            "agent_mode",
            "app_version",
            "persistence_allowed",
            "persistence_reason",
            "raw_content_visible",
        }
    )

    def __post_init__(self) -> None:
        if _opaque_id(self.event_id, prefix="tue", field_name="event_id") != self.event_id:
            raise ToolUsageEventError("event_id is not canonical")
        if _opaque_id(self.invocation_id, prefix="tui", field_name="invocation_id") != self.invocation_id:
            raise ToolUsageEventError("invocation_id is not canonical")
        for field_name, enum_type in (
            ("event_kind", ToolUsageEventKind),
            ("tool_family", ToolFamily),
            ("tool_source", ToolSource),
            ("surface", ToolUsageSurface),
            ("argument_size_bucket", ToolUsageSizeBucket),
            ("result_size_bucket", ToolUsageSizeBucket),
            ("result_shape_bucket", ToolUsageResultShape),
            ("reference_state", ToolUsageReferenceState),
            ("model_scope", ToolUsageModelScope),
            ("agent_mode", ToolUsageAgentMode),
            ("persistence_reason", ToolUsagePersistenceReason),
        ):
            if not isinstance(getattr(self, field_name), enum_type):
                raise ToolUsageEventError(f"{field_name} must use its controlled enum")
        for field_name, enum_type in (
            ("status", ToolUsageStatus),
            ("error_class", ToolUsageErrorClass),
            ("blocked_reason_code", ToolUsageBlockedReason),
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, enum_type):
                raise ToolUsageEventError(f"{field_name} must use its controlled enum")
        if _timestamp(self.occurred_at) != self.occurred_at:
            raise ToolUsageEventError("occurred_at is not canonical")
        if _analytics_id(self.tool_analytics_id) != self.tool_analytics_id:
            raise ToolUsageEventError("tool_analytics_id is not canonical")
        if _app_version(self.app_version) != self.app_version:
            raise ToolUsageEventError("app_version is not canonical")
        for field_name in ("owner_ref", "session_ref", "run_ref", "correlation_ref"):
            if _hmac_ref(getattr(self, field_name), field_name=field_name) != getattr(self, field_name):
                raise ToolUsageEventError(f"{field_name} is not canonical")
        _bounded_int(
            self.retry_ordinal,
            field_name="retry_ordinal",
            minimum=0,
            maximum=_MAX_RETRY_ORDINAL,
            allow_none=False,
        )
        _bounded_int(
            self.duration_ms,
            field_name="duration_ms",
            minimum=0,
            maximum=_MAX_DURATION_MS,
            allow_none=True,
        )
        if not isinstance(self.persistence_allowed, bool):
            raise ToolUsageEventError("persistence_allowed must be boolean")
        if self.persistence_allowed != (self.persistence_reason == ToolUsagePersistenceReason.ALLOWED):
            raise ToolUsageEventError("persistence decision and reason disagree")

        refs = (self.owner_ref, self.session_ref, self.run_ref, self.correlation_ref)
        if self.reference_state == ToolUsageReferenceState.AVAILABLE and not any(refs):
            raise ToolUsageEventError("available reference state requires at least one HMAC ref")
        if self.reference_state != ToolUsageReferenceState.AVAILABLE and any(refs):
            raise ToolUsageEventError("unavailable/not-requested reference state must not carry refs")

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
                raise ToolUsageEventError("started events cannot contain terminal fields")
            if self.result_size_bucket != ToolUsageSizeBucket.NONE:
                raise ToolUsageEventError("started events cannot contain a result size")
            if self.result_shape_bucket != ToolUsageResultShape.NONE:
                raise ToolUsageEventError("started events cannot contain a result shape")
        else:
            if self.status is None:
                raise ToolUsageEventError("terminal events require a status")
            if self.status == ToolUsageStatus.SUCCEEDED and (
                self.error_class is not None or self.blocked_reason_code is not None
            ):
                raise ToolUsageEventError("succeeded events cannot contain error or blocked classes")
            if self.status == ToolUsageStatus.FAILED and self.error_class is None:
                raise ToolUsageEventError("failed events require a bounded error_class")
            if self.status in {ToolUsageStatus.BLOCKED, ToolUsageStatus.REJECTED} and self.blocked_reason_code is None:
                raise ToolUsageEventError("blocked/rejected events require a bounded reason code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_ID,
            "event_id": self.event_id,
            "invocation_id": self.invocation_id,
            "event_kind": self.event_kind.value,
            "occurred_at": self.occurred_at,
            "duration_ms": self.duration_ms,
            "tool_analytics_id": self.tool_analytics_id,
            "tool_family": self.tool_family.value,
            "tool_source": self.tool_source.value,
            "surface": self.surface.value,
            "status": self.status.value if self.status else None,
            "error_class": self.error_class.value if self.error_class else None,
            "blocked_reason_code": self.blocked_reason_code.value if self.blocked_reason_code else None,
            "retry_ordinal": self.retry_ordinal,
            "argument_size_bucket": self.argument_size_bucket.value,
            "result_size_bucket": self.result_size_bucket.value,
            "result_shape_bucket": self.result_shape_bucket.value,
            "owner_ref": self.owner_ref,
            "session_ref": self.session_ref,
            "run_ref": self.run_ref,
            "correlation_ref": self.correlation_ref,
            "reference_state": self.reference_state.value,
            "model_scope": self.model_scope.value,
            "agent_mode": self.agent_mode.value,
            "app_version": self.app_version,
            "persistence_allowed": self.persistence_allowed,
            "persistence_reason": self.persistence_reason.value,
            "raw_content_visible": False,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ToolUsageEventV1":
        if not isinstance(value, Mapping):
            raise ToolUsageEventError("event must be a mapping")
        unknown = set(value) - cls.SERIALIZED_FIELDS
        missing = cls.SERIALIZED_FIELDS - set(value)
        if unknown:
            raise ToolUsageEventError("event contains non-allowlisted fields")
        if missing:
            raise ToolUsageEventError("event is missing required serialized fields")
        if value.get("schema_version") != cls.SCHEMA_ID:
            raise ToolUsageEventError("schema_version is not supported")
        if value.get("raw_content_visible") is not False:
            raise ToolUsageEventError("raw_content_visible must be false")
        return cls(
            event_id=_opaque_id(value.get("event_id"), prefix="tue", field_name="event_id"),
            invocation_id=_opaque_id(value.get("invocation_id"), prefix="tui", field_name="invocation_id"),
            event_kind=_enum(ToolUsageEventKind, value.get("event_kind"), field_name="event_kind"),
            occurred_at=_timestamp(value.get("occurred_at")),
            duration_ms=_bounded_int(
                value.get("duration_ms"),
                field_name="duration_ms",
                minimum=0,
                maximum=_MAX_DURATION_MS,
                allow_none=True,
            ),
            tool_analytics_id=_analytics_id(value.get("tool_analytics_id")),
            tool_family=_enum(ToolFamily, value.get("tool_family"), field_name="tool_family"),
            tool_source=_enum(ToolSource, value.get("tool_source"), field_name="tool_source"),
            surface=_enum(ToolUsageSurface, value.get("surface"), field_name="surface"),
            status=(
                _enum(ToolUsageStatus, value.get("status"), field_name="status")
                if value.get("status") is not None
                else None
            ),
            error_class=(
                _enum(ToolUsageErrorClass, value.get("error_class"), field_name="error_class")
                if value.get("error_class") is not None
                else None
            ),
            blocked_reason_code=(
                _enum(
                    ToolUsageBlockedReason,
                    value.get("blocked_reason_code"),
                    field_name="blocked_reason_code",
                )
                if value.get("blocked_reason_code") is not None
                else None
            ),
            retry_ordinal=_bounded_int(
                value.get("retry_ordinal"),
                field_name="retry_ordinal",
                minimum=0,
                maximum=_MAX_RETRY_ORDINAL,
                allow_none=False,
            ),
            argument_size_bucket=_enum(
                ToolUsageSizeBucket,
                value.get("argument_size_bucket"),
                field_name="argument_size_bucket",
            ),
            result_size_bucket=_enum(
                ToolUsageSizeBucket,
                value.get("result_size_bucket"),
                field_name="result_size_bucket",
            ),
            result_shape_bucket=_enum(
                ToolUsageResultShape,
                value.get("result_shape_bucket"),
                field_name="result_shape_bucket",
            ),
            owner_ref=_hmac_ref(value.get("owner_ref"), field_name="owner_ref"),
            session_ref=_hmac_ref(value.get("session_ref"), field_name="session_ref"),
            run_ref=_hmac_ref(value.get("run_ref"), field_name="run_ref"),
            correlation_ref=_hmac_ref(value.get("correlation_ref"), field_name="correlation_ref"),
            reference_state=_enum(
                ToolUsageReferenceState,
                value.get("reference_state"),
                field_name="reference_state",
            ),
            model_scope=_enum(
                ToolUsageModelScope, value.get("model_scope"), field_name="model_scope"
            ),
            agent_mode=_enum(
                ToolUsageAgentMode, value.get("agent_mode"), field_name="agent_mode"
            ),
            app_version=_app_version(value.get("app_version")),
            persistence_allowed=value.get("persistence_allowed"),
            persistence_reason=_enum(
                ToolUsagePersistenceReason,
                value.get("persistence_reason"),
                field_name="persistence_reason",
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolUsageEventBuilder:
    app_version: str
    hmac_key: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if _app_version(self.app_version) != self.app_version:
            raise ToolUsageEventError("app_version is not canonical")
        if self.hmac_key is not None and (
            not isinstance(self.hmac_key, bytes) or len(self.hmac_key) < 32
        ):
            raise ToolUsageEventError("HMAC key must contain at least 32 bytes")

    def build(
        self,
        *,
        descriptor: ToolDescriptorV2,
        event_kind: ToolUsageEventKind | str,
        surface: ToolUsageSurface | str,
        agent_mode: ToolUsageAgentMode | str,
        model_scope: ToolUsageModelScope | str = ToolUsageModelScope.UNKNOWN,
        status: ToolUsageStatus | str | None = None,
        error_class: ToolUsageErrorClass | str | None = None,
        blocked_reason_code: ToolUsageBlockedReason | str | None = None,
        duration_ms: int | None = None,
        retry_ordinal: int = 0,
        argument_size_bytes: int | None = 0,
        result_size_bytes: int | None = 0,
        result_shape: ToolUsageResultShape | str = ToolUsageResultShape.NONE,
        owner_identity: Any = None,
        session_identity: Any = None,
        run_identity: Any = None,
        correlation_identity: Any = None,
        incognito: bool = False,
        is_nobody: bool = False,
        event_id: str | None = None,
        invocation_id: str | None = None,
        occurred_at: datetime | str | None = None,
    ) -> ToolUsageEventV1:
        if not isinstance(descriptor, ToolDescriptorV2):
            raise ToolUsageEventError("descriptor must be a TAX ToolDescriptorV2")
        if not isinstance(incognito, bool) or not isinstance(is_nobody, bool):
            raise ToolUsageEventError("incognito and is_nobody must be boolean")
        reference_inputs = {
            "owner": owner_identity,
            "session": session_identity,
            "run": run_identity,
            "correlation": correlation_identity,
        }
        refs = {
            kind: pseudonymize_reference(kind, raw, key=self.hmac_key)
            for kind, raw in reference_inputs.items()
        }
        requested_refs = any(value not in (None, "") for value in reference_inputs.values())
        reference_state = (
            ToolUsageReferenceState.AVAILABLE
            if any(refs.values())
            else ToolUsageReferenceState.UNAVAILABLE
            if requested_refs
            else ToolUsageReferenceState.NOT_REQUESTED
        )
        persistence_reason = (
            ToolUsagePersistenceReason.INCOGNITO
            if incognito
            else ToolUsagePersistenceReason.NOBODY
            if is_nobody
            else ToolUsagePersistenceReason.ALLOWED
        )
        kind = _enum(ToolUsageEventKind, event_kind, field_name="event_kind")
        status_value = (
            _enum(ToolUsageStatus, status, field_name="status")
            if status is not None
            else None
        )
        error_value = (
            _enum(ToolUsageErrorClass, error_class, field_name="error_class")
            if error_class is not None
            else None
        )
        blocked_value = (
            _enum(
                ToolUsageBlockedReason,
                blocked_reason_code,
                field_name="blocked_reason_code",
            )
            if blocked_reason_code is not None
            else None
        )
        return ToolUsageEventV1(
            event_id=_opaque_id(event_id, prefix="tue", field_name="event_id"),
            invocation_id=_opaque_id(
                invocation_id, prefix="tui", field_name="invocation_id"
            ),
            event_kind=kind,
            occurred_at=_timestamp(occurred_at),
            duration_ms=_bounded_int(
                duration_ms,
                field_name="duration_ms",
                minimum=0,
                maximum=_MAX_DURATION_MS,
                allow_none=True,
            ),
            tool_analytics_id=_analytics_id(descriptor.analytics_id),
            tool_family=descriptor.family,
            tool_source=descriptor.source,
            surface=_enum(ToolUsageSurface, surface, field_name="surface"),
            status=status_value,
            error_class=error_value,
            blocked_reason_code=blocked_value,
            retry_ordinal=_bounded_int(
                retry_ordinal,
                field_name="retry_ordinal",
                minimum=0,
                maximum=_MAX_RETRY_ORDINAL,
                allow_none=False,
            ),
            argument_size_bucket=size_bucket(argument_size_bytes),
            result_size_bucket=(
                ToolUsageSizeBucket.NONE
                if kind == ToolUsageEventKind.STARTED
                else size_bucket(result_size_bytes)
            ),
            result_shape_bucket=(
                ToolUsageResultShape.NONE
                if kind == ToolUsageEventKind.STARTED
                else _enum(
                    ToolUsageResultShape, result_shape, field_name="result_shape"
                )
            ),
            owner_ref=refs["owner"],
            session_ref=refs["session"],
            run_ref=refs["run"],
            correlation_ref=refs["correlation"],
            reference_state=reference_state,
            model_scope=_enum(
                ToolUsageModelScope, model_scope, field_name="model_scope"
            ),
            agent_mode=_enum(
                ToolUsageAgentMode, agent_mode, field_name="agent_mode"
            ),
            app_version=self.app_version,
            persistence_allowed=persistence_reason == ToolUsagePersistenceReason.ALLOWED,
            persistence_reason=persistence_reason,
        )
