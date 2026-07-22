"""Fail-closed, pre-consumer projection for maintenance diagnostics.

The boundary accepts only caller-declared fixed keys and already-narrow scalar
types. Raw text is never returned, logged, persisted, or included in errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


SECRET_SAFE_DIAGNOSTIC_SCHEMA = "odysseus.secret_safe_diagnostic.v1"
DEFAULT_MAX_BOUNDED_COUNT = 1_000_000
MAX_REGISTERED_COMMAND_SOURCES = 16
MAX_COMMAND_SOURCE_LENGTH = 256

_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SAFE_COMMAND_SOURCE = re.compile(r"^[\x20-\x7e]+$")
_FORBIDDEN_COUNT_OR_STATE_PARTS = frozenset(
    {
        "content",
        "credential",
        "hash",
        "key",
        "length",
        "message",
        "password",
        "prefix",
        "private",
        "raw",
        "secret",
        "stderr",
        "stdout",
        "suffix",
        "token",
        "value",
    }
)
_FORBIDDEN_PRESENCE_PARTS = frozenset(
    {
        "content",
        "hash",
        "length",
        "message",
        "prefix",
        "private",
        "raw",
        "stderr",
        "stdout",
        "suffix",
        "value",
    }
)
_SENSITIVE_PRESENCE_PARTS = frozenset(
    {"credential", "key", "password", "secret", "token"}
)
_FORBIDDEN_COMMAND_PATTERNS = (
    re.compile(
        r"(?<![a-z0-9_])(?:/usr/bin/)?(?:env|printenv)(?![a-z0-9_])"
    ),
    re.compile(r"(?<![a-z0-9_])(?:set|export)(?![a-z0-9_])"),
    re.compile(r"(?<![a-z0-9_])(?:declare|typeset)\s+(?:-x|-p)(?:\s|$)"),
    re.compile(r"(?<![a-z0-9_])compgen\s+-e(?:\s|$)"),
    re.compile(
        r"(?:get-childitem|gci|dir|get-item|gi|get-content|gc|type|ls)"
        r"[^\r\n]*\senv:"
    ),
    re.compile(r"\$\{?env:"),
    re.compile(
        r"(?:\[(?:system\.)?environment\]|(?:system\.)?environment)"
        r"\s*::\s*getenvironmentvariables?"
    ),
    re.compile(r"(?:os\.environ|os\.getenv|process\.env)"),
    re.compile(r"/proc/(?:self|\d+)/environ"),
    re.compile(
        r"(?:^|[\s\x22\x27=/\\])\.env(?:[.a-z0-9_-]*)"
        r"(?:[\s\x22\x27=/\\]|$)"
    ),
    re.compile(r"(?:docker|podman)\b[^\r\n]*\binspect\b"),
    re.compile(r"systemctl\b[^\r\n]*\b(?:show|cat)\b[^\r\n]*\benvironment\b"),
    re.compile(r"(?:docker|podman)\b[^\r\n]*\bcompose\s+config\b"),
    re.compile(r"(?<![a-z0-9_])compose\s+config\b"),
)


class DiagnosticProjectionStatus(StrEnum):
    ACCEPTED = "accepted"
    REFUSED = "refused"


class DiagnosticRefusalCode(StrEnum):
    RAW_SOURCE_FORBIDDEN = "raw_source_forbidden"
    UNKNOWN_SOURCE = "unknown_source"
    NARROWER_EVIDENCE_REQUIRED = "narrower_evidence_required"
    PAYLOAD_NOT_ALLOWLISTED = "payload_not_allowlisted"
    INVALID_SAFE_TYPE = "invalid_safe_type"
    COMMAND_SOURCE_NOT_ALLOWLISTED = "command_source_not_allowlisted"
    COUNT_OUT_OF_BOUNDS = "count_out_of_bounds"
    STATE_NOT_ALLOWLISTED = "state_not_allowlisted"
    DIAGNOSTIC_FAILED = "diagnostic_failed"


@dataclass(frozen=True, slots=True)
class DiagnosticContract:
    """Fixed output keys and types for one diagnostic source."""

    source_id: str
    presence_fields: tuple[str, ...] = ()
    count_fields: tuple[str, ...] = ()
    state_values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    command_sources: tuple[str, ...] = ()
    max_count: int = DEFAULT_MAX_BOUNDED_COUNT

    def __post_init__(self) -> None:
        source_id = _require_safe_token(self.source_id, field_name="source_id")
        presence = _normalize_presence_fields(self.presence_fields)
        counts = _normalize_fields(
            self.count_fields,
            field_name="count_fields",
            reject_sensitive_parts=True,
        )
        normalized_states: dict[str, tuple[str, ...]] = {}
        for raw_key, raw_values in dict(self.state_values).items():
            key = _require_safe_token(
                raw_key,
                field_name="state_values",
                reject_sensitive_parts=True,
            )
            values = tuple(
                _require_safe_token(value, field_name=f"state_values.{key}")
                for value in raw_values
            )
            if not values or len(set(values)) != len(values):
                raise ValueError("state values must be non-empty and unique")
            normalized_states[key] = values

        all_fields = presence + counts + tuple(normalized_states)
        if len(all_fields) > 32 or len(set(all_fields)) != len(all_fields):
            raise ValueError("diagnostic fields must be unique and bounded")
        if isinstance(self.max_count, bool) or not isinstance(self.max_count, int):
            raise ValueError("max_count must be an integer")
        if not 1 <= self.max_count <= DEFAULT_MAX_BOUNDED_COUNT:
            raise ValueError("max_count is outside the bounded range")
        command_sources = _normalize_command_sources(self.command_sources)

        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "presence_fields", presence)
        object.__setattr__(self, "count_fields", counts)
        object.__setattr__(self, "command_sources", command_sources)
        object.__setattr__(
            self,
            "state_values",
            MappingProxyType(normalized_states),
        )

    @property
    def allowed_fields(self) -> frozenset[str]:
        return frozenset(
            self.presence_fields + self.count_fields + tuple(self.state_values)
        )


@dataclass(frozen=True, slots=True)
class SecretSafeDiagnosticResult:
    source_id: str
    status: DiagnosticProjectionStatus
    presence: Mapping[str, bool] = field(default_factory=dict)
    counts: Mapping[str, int] = field(default_factory=dict)
    states: Mapping[str, str] = field(default_factory=dict)
    refusal_code: DiagnosticRefusalCode | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SECRET_SAFE_DIAGNOSTIC_SCHEMA,
            "source_id": self.source_id,
            "status": self.status.value,
            "presence": dict(sorted(self.presence.items())),
            "counts": dict(sorted(self.counts.items())),
            "states": dict(sorted(self.states.items())),
            "refusal_code": (
                self.refusal_code.value if self.refusal_code is not None else None
            ),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def diagnostic_source_is_forbidden(command_source: object) -> bool:
    """Return True for commands that serialize raw secret-bearing sources."""

    if command_source is None:
        return False
    if not isinstance(command_source, str):
        return True
    normalized = " ".join(command_source.strip().lower().replace("_", " ").split())
    return any(pattern.search(normalized) for pattern in _FORBIDDEN_COMMAND_PATTERNS)


def project_diagnostic(
    contract: DiagnosticContract,
    payload: object,
    *,
    command_source: object = None,
) -> SecretSafeDiagnosticResult:
    """Project one already-narrow mapping before any downstream consumer."""

    command_refusal = _command_source_refusal(contract, command_source)
    if command_refusal is not None:
        return _refused(contract.source_id, command_refusal)
    if type(payload) is not dict:
        return _refused(
            contract.source_id,
            DiagnosticRefusalCode.NARROWER_EVIDENCE_REQUIRED,
        )
    if len(payload) > len(contract.allowed_fields):
        return _refused(
            contract.source_id,
            DiagnosticRefusalCode.PAYLOAD_NOT_ALLOWLISTED,
        )
    if any(type(key) is not str for key in payload):
        return _refused(
            contract.source_id,
            DiagnosticRefusalCode.PAYLOAD_NOT_ALLOWLISTED,
        )
    if not set(payload).issubset(contract.allowed_fields):
        return _refused(
            contract.source_id,
            DiagnosticRefusalCode.PAYLOAD_NOT_ALLOWLISTED,
        )

    presence: dict[str, bool] = {}
    counts: dict[str, int] = {}
    states: dict[str, str] = {}
    for key in contract.presence_fields:
        if key not in payload:
            continue
        value = payload[key]
        if type(value) is not bool:
            return _refused(
                contract.source_id,
                DiagnosticRefusalCode.INVALID_SAFE_TYPE,
            )
        presence[key] = value

    for key in contract.count_fields:
        if key not in payload:
            continue
        value = payload[key]
        if type(value) is not int:
            return _refused(
                contract.source_id,
                DiagnosticRefusalCode.INVALID_SAFE_TYPE,
            )
        if not 0 <= value <= contract.max_count:
            return _refused(
                contract.source_id,
                DiagnosticRefusalCode.COUNT_OUT_OF_BOUNDS,
            )
        counts[key] = value

    for key, allowed_values in contract.state_values.items():
        if key not in payload:
            continue
        value = payload[key]
        if type(value) is not str or value not in allowed_values:
            return _refused(
                contract.source_id,
                DiagnosticRefusalCode.STATE_NOT_ALLOWLISTED,
            )
        states[key] = value

    return SecretSafeDiagnosticResult(
        source_id=contract.source_id,
        status=DiagnosticProjectionStatus.ACCEPTED,
        presence=MappingProxyType(presence),
        counts=MappingProxyType(counts),
        states=MappingProxyType(states),
    )


def project_registered_diagnostic(
    source_id: object,
    payload: object,
    *,
    registry: Mapping[str, DiagnosticContract],
    command_source: object = None,
) -> SecretSafeDiagnosticResult:
    """Fail closed when no exact diagnostic source contract is registered."""

    if type(source_id) is not str or source_id not in registry:
        return _refused("unknown", DiagnosticRefusalCode.UNKNOWN_SOURCE)
    contract = registry[source_id]
    if not isinstance(contract, DiagnosticContract) or contract.source_id != source_id:
        return _refused("unknown", DiagnosticRefusalCode.UNKNOWN_SOURCE)
    return project_diagnostic(contract, payload, command_source=command_source)


def project_subprocess_diagnostic(
    source_id: object,
    payload: object,
    *,
    returncode: object,
    registry: Mapping[str, DiagnosticContract],
    command_source: object = None,
    stdout: object = None,
    stderr: object = None,
) -> SecretSafeDiagnosticResult:
    """Project structured subprocess evidence while dropping both raw streams."""

    del stdout, stderr
    contract = _registered_contract(source_id, registry)
    if contract is None:
        return _refused("unknown", DiagnosticRefusalCode.UNKNOWN_SOURCE)
    if command_source is None:
        return _refused(
            contract.source_id,
            DiagnosticRefusalCode.COMMAND_SOURCE_NOT_ALLOWLISTED,
        )
    command_refusal = _command_source_refusal(contract, command_source)
    if command_refusal is not None:
        return _refused(contract.source_id, command_refusal)
    if type(returncode) is not int:
        return _refused(
            contract.source_id,
            DiagnosticRefusalCode.NARROWER_EVIDENCE_REQUIRED,
        )
    if returncode != 0:
        return _refused(
            contract.source_id,
            DiagnosticRefusalCode.DIAGNOSTIC_FAILED,
        )
    return project_registered_diagnostic(
        contract.source_id,
        payload,
        registry=registry,
        command_source=command_source,
    )


def project_exception_diagnostic(
    source_id: object,
    error: BaseException,
    *,
    registry: Mapping[str, DiagnosticContract],
) -> SecretSafeDiagnosticResult:
    """Return a fixed refusal without serializing exception type or message."""

    del error
    contract = _registered_contract(source_id, registry)
    if contract is None:
        return _refused("unknown", DiagnosticRefusalCode.UNKNOWN_SOURCE)
    return _refused(contract.source_id, DiagnosticRefusalCode.DIAGNOSTIC_FAILED)


def _refused(
    source_id: object,
    code: DiagnosticRefusalCode,
) -> SecretSafeDiagnosticResult:
    return SecretSafeDiagnosticResult(
        source_id=_safe_source_id(source_id),
        status=DiagnosticProjectionStatus.REFUSED,
        refusal_code=code,
    )


def _safe_source_id(value: object) -> str:
    if type(value) is str and _SAFE_TOKEN.fullmatch(value):
        return value
    return "unknown"


def _normalize_fields(
    values: tuple[str, ...],
    *,
    field_name: str,
    reject_sensitive_parts: bool = False,
) -> tuple[str, ...]:
    normalized = tuple(
        _require_safe_token(
            value,
            field_name=field_name,
            reject_sensitive_parts=reject_sensitive_parts,
        )
        for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must contain unique values")
    return normalized


def _normalize_presence_fields(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(
        _require_safe_token(value, field_name="presence_fields")
        for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError("presence_fields must contain unique values")
    for value in normalized:
        parts = frozenset(value.split("_"))
        if parts & _FORBIDDEN_PRESENCE_PARTS:
            raise ValueError("presence_fields contains a forbidden semantic")
        if parts & _SENSITIVE_PRESENCE_PARTS and not value.endswith("_present"):
            raise ValueError(
                "sensitive presence fields must use an explicit _present suffix"
            )
    return normalized


def _registered_contract(
    source_id: object,
    registry: Mapping[str, DiagnosticContract],
) -> DiagnosticContract | None:
    if type(source_id) is not str or source_id not in registry:
        return None
    contract = registry[source_id]
    if not isinstance(contract, DiagnosticContract) or contract.source_id != source_id:
        return None
    return contract


def _require_safe_token(
    value: object,
    *,
    field_name: str,
    reject_sensitive_parts: bool = False,
) -> str:
    if type(value) is not str or not _SAFE_TOKEN.fullmatch(value):
        raise ValueError(f"{field_name} must contain safe fixed tokens")
    if reject_sensitive_parts:
        parts = frozenset(value.split("_"))
        if parts & _FORBIDDEN_COUNT_OR_STATE_PARTS:
            raise ValueError(f"{field_name} contains a forbidden semantic")
    return value


def _normalize_command_source(value: object) -> str | None:
    if type(value) is not str:
        return None
    if any(ord(character) < 32 or ord(character) > 126 for character in value):
        return None
    normalized = " ".join(value.strip().split())
    if (
        not normalized
        or len(normalized) > MAX_COMMAND_SOURCE_LENGTH
        or _SAFE_COMMAND_SOURCE.fullmatch(normalized) is None
    ):
        return None
    return normalized


def _normalize_command_sources(values: tuple[str, ...]) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) > MAX_REGISTERED_COMMAND_SOURCES:
        raise ValueError("command_sources must be a bounded tuple")
    normalized: list[str] = []
    for value in values:
        source = _normalize_command_source(value)
        if source is None:
            raise ValueError("command_sources contains an invalid source")
        if diagnostic_source_is_forbidden(source):
            raise ValueError("command_sources contains a forbidden source")
        normalized.append(source)
    if len(set(normalized)) != len(normalized):
        raise ValueError("command_sources must contain unique values")
    return tuple(normalized)


def _command_source_refusal(
    contract: DiagnosticContract,
    command_source: object,
) -> DiagnosticRefusalCode | None:
    if command_source is None:
        return None
    if diagnostic_source_is_forbidden(command_source):
        return DiagnosticRefusalCode.RAW_SOURCE_FORBIDDEN
    normalized = _normalize_command_source(command_source)
    if normalized is None or normalized not in contract.command_sources:
        return DiagnosticRefusalCode.COMMAND_SOURCE_NOT_ALLOWLISTED
    return None


__all__ = [
    "DEFAULT_MAX_BOUNDED_COUNT",
    "MAX_COMMAND_SOURCE_LENGTH",
    "MAX_REGISTERED_COMMAND_SOURCES",
    "DiagnosticContract",
    "DiagnosticProjectionStatus",
    "DiagnosticRefusalCode",
    "SECRET_SAFE_DIAGNOSTIC_SCHEMA",
    "SecretSafeDiagnosticResult",
    "diagnostic_source_is_forbidden",
    "project_diagnostic",
    "project_exception_diagnostic",
    "project_registered_diagnostic",
    "project_subprocess_diagnostic",
]
