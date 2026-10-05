"""Pure, bounded lifecycle contracts for ULO-02.

The module deliberately contains no store, filesystem, provider, scheduler, or
runtime effects.  Domain truth and the existing IndexJobRecord remain owned by
their respective layers; these values only bind lifecycle intent and evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, ClassVar, Mapping

from src.unified_source_index_contract import (
    IndexJobKind,
    IndexJobRecord,
    RecordKind,
    RecordRef,
    SourceScope,
    _record_id,
)


LIFECYCLE_REQUEST_SCHEMA = "odysseus.unified_source_index.lifecycle_request.v1"
LIFECYCLE_CHECKPOINT_SCHEMA = "odysseus.unified_source_index.lifecycle_checkpoint.v1"
_MAX_CHECKPOINT_CHARS = 1024
_MAX_GENERATION = 9_223_372_036_854_775_807
_OWNER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._/@+-]{0,159}$")
_DOMAIN_RE = re.compile(r"^(?:usi_version_[0-9a-f]{64}|sha256:[0-9a-f]{64}|rev_[0-9a-f]{8,64})$")
_POLICY_RE = re.compile(r"^ulo_policy_[0-9a-f]{64}$")
_SIGNAL_RE = re.compile(r"^usi_change_[0-9a-f]{64}$")
_RESTORE_RE = re.compile(r"^usi_restore_[0-9a-f]{64}$")
_PURGE_RE = re.compile(r"^usi_purge_[0-9a-f]{64}$")
_SNAPSHOT_RE = re.compile(r"^usi_snapshot_[0-9a-f]{64}$")
_REQUEST_RE = re.compile(r"^usi_lifecycle_[0-9a-f]{64}$")


class LifecycleActionKind(StrEnum):
    ARCHIVE = "archive"
    ACCESS_REVOKE = "access_revoke"
    SUPERSEDE = "supersede"
    DELETE = "delete"
    ERASE = "erase"
    RESTORE = "restore"


class LifecycleDisposition(StrEnum):
    ELIGIBLE = "eligible"
    UNAVAILABLE = "unavailable"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    ERASED = "erased"


class LifecycleEffectKind(StrEnum):
    QUERY_FENCE = "query_fence"
    TRUTH_TOMBSTONE = "truth_tombstone"
    CONTENT_PURGE = "content_purge"
    PROJECTION_CLEANUP = "projection_cleanup"
    REDISCOVERY = "rediscovery"


class LifecycleScopeKind(StrEnum):
    OWNER = "owner"
    SOURCE = "source"
    SOURCE_VERSION = "source_version"


class LifecycleState(StrEnum):
    REQUESTED = "requested"
    QUIESCING = "quiescing"
    APPLYING = "applying"
    PROJECTING = "projecting"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class LifecycleErrorCode(StrEnum):
    NONE = "none"
    INVALID_REQUEST = "invalid_request"
    OWNER_MISMATCH = "owner_mismatch"
    REVISION_MISMATCH = "revision_mismatch"
    STALE_GENERATION = "stale_generation"
    GENERATION_COLLISION = "generation_collision"
    STATE_TRANSITION_INVALID = "state_transition_invalid"
    SIGNAL_NOT_AUTHORITY = "signal_not_authority"
    TOMBSTONE_MISMATCH = "tombstone_mismatch"
    RESTORE_FORBIDDEN = "restore_forbidden"
    CONTENT_PURGE_UNPROVEN = "content_purge_unproven"
    CLEANUP_PENDING = "cleanup_pending"
    EFFECT_FAILED = "effect_failed"


class LifecycleContractError(ValueError):
    """Fresh fixed-code error with no caller data in its public shape."""

    def __init__(self, code: LifecycleErrorCode = LifecycleErrorCode.INVALID_REQUEST):
        self.code = code if type(code) is LifecycleErrorCode else LifecycleErrorCode.INVALID_REQUEST
        super().__init__()


def _fail(code: LifecycleErrorCode = LifecycleErrorCode.INVALID_REQUEST) -> None:
    raise LifecycleContractError(code) from None


def _exact_text(value: Any, pattern: re.Pattern[str], *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value) or (value and pattern.fullmatch(value) is None):
        _fail()
    return value


def _owner(value: Any) -> str:
    return _exact_text(value, _OWNER_RE)


def _record_ref(value: Any, kind: RecordKind | None = None) -> RecordRef:
    if type(value) is not RecordRef or (kind is not None and value.record_kind is not kind):
        _fail(LifecycleErrorCode.OWNER_MISMATCH if kind is not None else LifecycleErrorCode.INVALID_REQUEST)
    return value


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        _fail()
    raise AssertionError


def _pairs(value: str | bytes) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                _fail()
            result[key] = item
        return result
    try:
        parsed = json.loads(value, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, json.JSONDecodeError):
        _fail()
    if type(parsed) is not dict:
        _fail()
    return parsed


def _timestamp(value: Any) -> str:
    if type(value) is not str or not value.endswith("Z"):
        _fail()
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail()
    if parsed.utcoffset() is None or parsed.isoformat().replace("+00:00", "Z") != value:
        _fail()
    return value


def _generation(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_GENERATION:
        _fail()
    return value


def _effects(value: Any) -> tuple[LifecycleEffectKind, ...]:
    if type(value) is not tuple or any(type(item) is not LifecycleEffectKind for item in value):
        _fail()
    if len(set(value)) != len(value):
        _fail()
    return value


@dataclass(frozen=True, slots=True)
class LifecycleTarget:
    owner_scope: str
    scope_kind: LifecycleScopeKind
    source_ref: RecordRef | None = None
    source_version_ref: RecordRef | None = None

    def __post_init__(self) -> None:
        owner = _owner(self.owner_scope)
        if type(self.scope_kind) is not LifecycleScopeKind:
            _fail()
        source = self.source_ref
        version = self.source_version_ref
        if source is not None:
            _record_ref(source, RecordKind.SOURCE)
        if version is not None:
            _record_ref(version, RecordKind.SOURCE_VERSION)
        if self.scope_kind is LifecycleScopeKind.OWNER and (source is not None or version is not None):
            _fail()
        if self.scope_kind is LifecycleScopeKind.SOURCE and (source is None or version is not None):
            _fail()
        if self.scope_kind is LifecycleScopeKind.SOURCE_VERSION and (source is None or version is None):
            _fail()
        if source is not None and version is not None and version.record_id == source.record_id:
            _fail()
        object.__setattr__(self, "owner_scope", owner)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_scope": self.owner_scope,
            "scope_kind": self.scope_kind.value,
            "source_ref": self.source_ref.to_dict() if self.source_ref else None,
            "source_version_ref": self.source_version_ref.to_dict() if self.source_version_ref else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LifecycleTarget":
        if type(value) is not dict or set(value) != {"owner_scope", "scope_kind", "source_ref", "source_version_ref"}:
            _fail()
        try:
            source = RecordRef.from_dict(value["source_ref"]) if value["source_ref"] is not None else None
            version = RecordRef.from_dict(value["source_version_ref"]) if value["source_version_ref"] is not None else None
            return cls(value["owner_scope"], LifecycleScopeKind(value["scope_kind"]), source, version)
        except (KeyError, TypeError, ValueError):
            _fail()
        raise AssertionError


_DISPOSITION = {
    LifecycleActionKind.ARCHIVE: LifecycleDisposition.UNAVAILABLE,
    LifecycleActionKind.ACCESS_REVOKE: LifecycleDisposition.UNAVAILABLE,
    LifecycleActionKind.SUPERSEDE: LifecycleDisposition.SUPERSEDED,
    LifecycleActionKind.DELETE: LifecycleDisposition.DELETED,
    LifecycleActionKind.ERASE: LifecycleDisposition.ERASED,
    LifecycleActionKind.RESTORE: LifecycleDisposition.ELIGIBLE,
}


@dataclass(frozen=True, slots=True)
class LifecycleRequest:
    action_kind: LifecycleActionKind
    disposition: LifecycleDisposition
    target: LifecycleTarget
    domain_revision_ref: str
    lifecycle_generation: int
    policy_version: str
    change_signal_ref: str
    restore_authority_ref: str
    request_id: str
    schema: ClassVar[str] = LIFECYCLE_REQUEST_SCHEMA

    @classmethod
    def create(cls, *, action_kind: LifecycleActionKind, target: LifecycleTarget, domain_revision_ref: str, lifecycle_generation: int, policy_version: str, change_signal_ref: str = "", restore_authority_ref: str = "") -> "LifecycleRequest":
        if type(action_kind) is not LifecycleActionKind or type(target) is not LifecycleTarget:
            _fail()
        if type(domain_revision_ref) is not str or _DOMAIN_RE.fullmatch(domain_revision_ref) is None:
            _fail()
        if type(policy_version) is not str or _POLICY_RE.fullmatch(policy_version) is None:
            _fail()
        generation = _generation(lifecycle_generation)
        if type(change_signal_ref) is not str or (change_signal_ref and _SIGNAL_RE.fullmatch(change_signal_ref) is None):
            _fail()
        if type(restore_authority_ref) is not str or (restore_authority_ref and _RESTORE_RE.fullmatch(restore_authority_ref) is None):
            _fail()
        if action_kind is LifecycleActionKind.RESTORE:
            if not restore_authority_ref:
                _fail(LifecycleErrorCode.RESTORE_FORBIDDEN)
            if change_signal_ref:
                _fail(LifecycleErrorCode.SIGNAL_NOT_AUTHORITY)
        elif restore_authority_ref:
            _fail()
        payload = {
            "schema": LIFECYCLE_REQUEST_SCHEMA,
            "action_kind": action_kind.value,
            "disposition": _DISPOSITION[action_kind].value,
            "target": target.to_dict(),
            "domain_revision_ref": domain_revision_ref,
            "lifecycle_generation": generation,
            "policy_version": policy_version,
            "change_signal_ref": change_signal_ref,
            "restore_authority_ref": restore_authority_ref,
        }
        request_id = "usi_lifecycle_" + hashlib.sha256(_canonical(payload).encode()).hexdigest()
        return cls(action_kind, _DISPOSITION[action_kind], target, domain_revision_ref, generation, policy_version, change_signal_ref, restore_authority_ref, request_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action_kind": self.action_kind.value,
            "disposition": self.disposition.value,
            "target": self.target.to_dict(),
            "domain_revision_ref": self.domain_revision_ref,
            "lifecycle_generation": self.lifecycle_generation,
            "policy_version": self.policy_version,
            "change_signal_ref": self.change_signal_ref,
            "restore_authority_ref": self.restore_authority_ref,
            "request_id": self.request_id,
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LifecycleRequest":
        if type(value) is not dict or set(value) != {"schema", "action_kind", "disposition", "target", "domain_revision_ref", "lifecycle_generation", "policy_version", "change_signal_ref", "restore_authority_ref", "request_id"}:
            _fail()
        try:
            result = cls.create(
                action_kind=LifecycleActionKind(value["action_kind"]),
                target=LifecycleTarget.from_dict(value["target"]),
                domain_revision_ref=value["domain_revision_ref"],
                lifecycle_generation=value["lifecycle_generation"],
                policy_version=value["policy_version"],
                change_signal_ref=value["change_signal_ref"],
                restore_authority_ref=value["restore_authority_ref"],
            )
        except (KeyError, TypeError, ValueError):
            _fail()
        if value["schema"] != LIFECYCLE_REQUEST_SCHEMA or value["disposition"] != result.disposition.value or value["request_id"] != result.request_id:
            _fail()
        return result

    @classmethod
    def from_json(cls, value: str | bytes) -> "LifecycleRequest":
        return cls.from_dict(_pairs(value))


def _lifecycle_request_projection(value: object) -> tuple[dict[str, object], str]:
    """Project a request from exact object leaves before canonical serialization."""
    if type(value) is not LifecycleRequest:
        _fail()
    try:
        action = object.__getattribute__(value, "action_kind")
        disposition = object.__getattribute__(value, "disposition")
        target = object.__getattribute__(value, "target")
        domain_revision = object.__getattribute__(value, "domain_revision_ref")
        generation = object.__getattribute__(value, "lifecycle_generation")
        policy = object.__getattribute__(value, "policy_version")
        signal = object.__getattribute__(value, "change_signal_ref")
        restore = object.__getattribute__(value, "restore_authority_ref")
        request_id = object.__getattribute__(value, "request_id")
    except Exception:
        _fail()
    if type(action) is not LifecycleActionKind or type(disposition) is not LifecycleDisposition or type(target) is not LifecycleTarget:
        _fail()
    try:
        owner = object.__getattribute__(target, "owner_scope")
        scope = object.__getattribute__(target, "scope_kind")
        source = object.__getattribute__(target, "source_ref")
        version = object.__getattribute__(target, "source_version_ref")
    except Exception:
        _fail()
    _owner(owner)
    if type(scope) is not LifecycleScopeKind:
        _fail()
    def ref_value(ref: object, expected: RecordKind) -> dict[str, str] | None:
        if ref is None:
            return None
        if type(ref) is not RecordRef:
            _fail()
        try:
            kind = object.__getattribute__(ref, "record_kind")
            record_id = object.__getattribute__(ref, "record_id")
        except Exception:
            _fail()
        if type(kind) is not RecordKind or kind is not expected:
            _fail()
        try:
            checked = _record_id(record_id, expected)
        except Exception:
            _fail()
        return {"schema": RecordRef.SCHEMA, "record_kind": kind.value, "record_id": checked}
    source_value = ref_value(source, RecordKind.SOURCE)
    version_value = ref_value(version, RecordKind.SOURCE_VERSION)
    if ((scope is LifecycleScopeKind.OWNER and (source_value is not None or version_value is not None))
            or (scope is LifecycleScopeKind.SOURCE and (source_value is None or version_value is not None))
            or (scope is LifecycleScopeKind.SOURCE_VERSION and (source_value is None or version_value is None))):
        _fail()
    if type(domain_revision) is not str or _DOMAIN_RE.fullmatch(domain_revision) is None:
        _fail()
    if type(generation) is not int or not 1 <= generation <= _MAX_GENERATION:
        _fail()
    if type(policy) is not str or _POLICY_RE.fullmatch(policy) is None:
        _fail()
    if type(signal) is not str or (signal and _SIGNAL_RE.fullmatch(signal) is None):
        _fail()
    if type(restore) is not str or (restore and _RESTORE_RE.fullmatch(restore) is None):
        _fail()
    if type(request_id) is not str or _REQUEST_RE.fullmatch(request_id) is None:
        _fail()
    expected_disposition = _DISPOSITION[action]
    if disposition is not expected_disposition or (action is LifecycleActionKind.RESTORE) != bool(restore):
        _fail()
    target_values = {"owner_scope": owner, "scope_kind": scope.value, "source_ref": source_value, "source_version_ref": version_value}
    payload: dict[str, object] = {
        "schema": LIFECYCLE_REQUEST_SCHEMA,
        "action_kind": action.value,
        "disposition": disposition.value,
        "target": target_values,
        "domain_revision_ref": domain_revision,
        "lifecycle_generation": generation,
        "policy_version": policy,
        "change_signal_ref": signal,
        "restore_authority_ref": restore,
    }
    request_payload = dict(payload)
    expected_id = "usi_lifecycle_" + hashlib.sha256(_canonical(request_payload).encode()).hexdigest()
    if request_id != expected_id:
        _fail()
    payload["request_id"] = request_id
    return payload, _canonical(payload)


_TRANSITIONS = {
    LifecycleState.REQUESTED: {LifecycleState.QUIESCING, LifecycleState.APPLYING, LifecycleState.FAILED},
    LifecycleState.QUIESCING: {LifecycleState.APPLYING, LifecycleState.DEGRADED, LifecycleState.FAILED, LifecycleState.ROLLED_BACK},
    LifecycleState.APPLYING: {LifecycleState.PROJECTING, LifecycleState.VERIFYING, LifecycleState.DEGRADED, LifecycleState.FAILED},
    LifecycleState.PROJECTING: {LifecycleState.VERIFYING, LifecycleState.DEGRADED, LifecycleState.FAILED},
    LifecycleState.VERIFYING: {LifecycleState.COMPLETE, LifecycleState.DEGRADED, LifecycleState.FAILED},
    LifecycleState.DEGRADED: {LifecycleState.APPLYING, LifecycleState.PROJECTING, LifecycleState.VERIFYING, LifecycleState.FAILED},
    LifecycleState.COMPLETE: set(),
    LifecycleState.FAILED: set(),
    LifecycleState.ROLLED_BACK: set(),
}


_COMPLETE_TOKEN = object()
_VERIFIED_COMPLETE_CHECKPOINTS: dict[int, "LifecycleCheckpoint"] = {}


@dataclass(frozen=True, slots=True)
class LifecycleCheckpoint:
    request_id: str
    state: LifecycleState
    lifecycle_generation: int
    domain_revision_ref: str
    store_snapshot_ref: str
    completed_effects: tuple[LifecycleEffectKind, ...]
    pending_effects: tuple[LifecycleEffectKind, ...]
    attempt_count: int
    error_code: LifecycleErrorCode
    recorded_at: str
    content_purge_proof_ref: str
    checkpoint_digest: str
    core_commit_ref: str = ""
    schema: ClassVar[str] = LIFECYCLE_CHECKPOINT_SCHEMA

    @classmethod
    def create(cls, request: LifecycleRequest, *, state: LifecycleState, recorded_at: str, store_snapshot_ref: str = "", completed_effects: tuple[LifecycleEffectKind, ...] = (), pending_effects: tuple[LifecycleEffectKind, ...] = (), attempt_count: int = 0, error_code: LifecycleErrorCode = LifecycleErrorCode.NONE, content_purge_proof_ref: str = "", core_commit_ref: str = "", _complete_token: object | None = None) -> "LifecycleCheckpoint":
        if type(request) is not LifecycleRequest or type(state) is not LifecycleState or type(error_code) is not LifecycleErrorCode:
            _fail()
        _timestamp(recorded_at)
        _generation(request.lifecycle_generation)
        _effects(completed_effects)
        _effects(pending_effects)
        if type(attempt_count) is not int or attempt_count < 0 or attempt_count > 10_000:
            _fail()
        if type(store_snapshot_ref) is not str or (store_snapshot_ref and _SNAPSHOT_RE.fullmatch(store_snapshot_ref) is None):
            _fail()
        if type(content_purge_proof_ref) is not str or (content_purge_proof_ref and _PURGE_RE.fullmatch(content_purge_proof_ref) is None):
            _fail()
        if type(core_commit_ref) is not str or (core_commit_ref and re.fullmatch(r"usi_commit_[0-9a-f]{64}", core_commit_ref) is None):
            _fail()
        if state is LifecycleState.COMPLETE:
            if _complete_token is not _COMPLETE_TOKEN:
                _fail(LifecycleErrorCode.EFFECT_FAILED)
            required = {LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE}
            if not store_snapshot_ref or not core_commit_ref or not required.issubset(completed_effects) or pending_effects or error_code is not LifecycleErrorCode.NONE:
                _fail(LifecycleErrorCode.EFFECT_FAILED)
            if request.action_kind is LifecycleActionKind.ERASE and (not content_purge_proof_ref or LifecycleEffectKind.CONTENT_PURGE not in completed_effects):
                _fail(LifecycleErrorCode.CONTENT_PURGE_UNPROVEN)
        if request.action_kind is LifecycleActionKind.ERASE and state is LifecycleState.COMPLETE and not content_purge_proof_ref:
            _fail(LifecycleErrorCode.CONTENT_PURGE_UNPROVEN)
        if state is LifecycleState.DEGRADED and (not pending_effects or error_code is LifecycleErrorCode.NONE):
            _fail(LifecycleErrorCode.CLEANUP_PENDING)
        data = {
            "schema": LIFECYCLE_CHECKPOINT_SCHEMA,
            "request_id": request.request_id,
            "state": state.value,
            "lifecycle_generation": request.lifecycle_generation,
            "domain_revision_ref": request.domain_revision_ref,
            "store_snapshot_ref": store_snapshot_ref,
            "completed_effects": [item.value for item in completed_effects],
            "pending_effects": [item.value for item in pending_effects],
            "attempt_count": attempt_count,
            "error_code": error_code.value,
            "recorded_at": recorded_at,
            "content_purge_proof_ref": content_purge_proof_ref,
        }
        if core_commit_ref:
            data["core_commit_ref"] = core_commit_ref
        digest = "sha256:" + hashlib.sha256(_canonical(data).encode()).hexdigest()
        result = cls(request.request_id, state, request.lifecycle_generation, request.domain_revision_ref, store_snapshot_ref, completed_effects, pending_effects, attempt_count, error_code, recorded_at, content_purge_proof_ref, digest, core_commit_ref)
        if state is LifecycleState.COMPLETE:
            _VERIFIED_COMPLETE_CHECKPOINTS[id(result)] = result
        if len(result.to_json()) > _MAX_CHECKPOINT_CHARS:
            _fail()
        return result

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema": self.schema,
            "request_id": self.request_id,
            "state": self.state.value,
            "lifecycle_generation": self.lifecycle_generation,
            "domain_revision_ref": self.domain_revision_ref,
            "store_snapshot_ref": self.store_snapshot_ref,
            "completed_effects": [item.value for item in self.completed_effects],
            "pending_effects": [item.value for item in self.pending_effects],
            "attempt_count": self.attempt_count,
            "error_code": self.error_code.value,
            "recorded_at": self.recorded_at,
            "content_purge_proof_ref": self.content_purge_proof_ref,
            "checkpoint_digest": self.checkpoint_digest,
        }
        if self.core_commit_ref:
            result["core_commit_ref"] = self.core_commit_ref
        return result

    def to_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LifecycleCheckpoint":
        required_fields = {"schema", "request_id", "state", "lifecycle_generation", "domain_revision_ref", "store_snapshot_ref", "completed_effects", "pending_effects", "attempt_count", "error_code", "recorded_at", "content_purge_proof_ref", "checkpoint_digest"}
        if type(value) is not dict or set(value) not in (required_fields, required_fields | {"core_commit_ref"}):
            _fail()
        try:
            state = LifecycleState(value["state"])
            completed = tuple(LifecycleEffectKind(item) for item in value["completed_effects"])
            pending = tuple(LifecycleEffectKind(item) for item in value["pending_effects"])
            error = LifecycleErrorCode(value["error_code"])
        except (TypeError, ValueError, KeyError):
            _fail()
        data = dict(value)
        digest = data.pop("checkpoint_digest")
        if type(digest) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            _fail()
        base = dict(data)
        expected = "sha256:" + hashlib.sha256(_canonical(base).encode()).hexdigest()
        if digest != expected or value["schema"] != LIFECYCLE_CHECKPOINT_SCHEMA:
            _fail()
        if state is LifecycleState.COMPLETE:
            _fail(LifecycleErrorCode.EFFECT_FAILED)
        # A detached checkpoint is decoded only after its request identity is checked
        # by lifecycle_checkpoint_from_job.
        result = object.__new__(cls)
        object.__setattr__(result, "request_id", value["request_id"])
        object.__setattr__(result, "state", state)
        object.__setattr__(result, "lifecycle_generation", value["lifecycle_generation"])
        object.__setattr__(result, "domain_revision_ref", value["domain_revision_ref"])
        object.__setattr__(result, "store_snapshot_ref", value["store_snapshot_ref"])
        object.__setattr__(result, "completed_effects", completed)
        object.__setattr__(result, "pending_effects", pending)
        object.__setattr__(result, "attempt_count", value["attempt_count"])
        object.__setattr__(result, "error_code", error)
        object.__setattr__(result, "recorded_at", value["recorded_at"])
        object.__setattr__(result, "content_purge_proof_ref", value["content_purge_proof_ref"])
        object.__setattr__(result, "checkpoint_digest", digest)
        object.__setattr__(result, "core_commit_ref", value.get("core_commit_ref", ""))
        return result

    @classmethod
    def from_json(cls, value: str | bytes) -> "LifecycleCheckpoint":
        return cls.from_dict(_pairs(value))


def advance_lifecycle(previous: LifecycleCheckpoint | None, request: LifecycleRequest, *, next_state: LifecycleState, recorded_at: str, store_snapshot_ref: str = "", completed_effects: tuple[LifecycleEffectKind, ...] = (), pending_effects: tuple[LifecycleEffectKind, ...] = (), error_code: LifecycleErrorCode = LifecycleErrorCode.NONE, content_purge_proof_ref: str = "") -> LifecycleCheckpoint:
    if type(request) is not LifecycleRequest or type(next_state) is not LifecycleState:
        _fail()
    if next_state is LifecycleState.COMPLETE:
        _fail(LifecycleErrorCode.EFFECT_FAILED)
    if previous is not None:
        if type(previous) is not LifecycleCheckpoint:
            _fail(LifecycleErrorCode.GENERATION_COLLISION)
        if request.lifecycle_generation < previous.lifecycle_generation:
            _fail(LifecycleErrorCode.STALE_GENERATION)
        if previous.request_id != request.request_id:
            _fail(LifecycleErrorCode.GENERATION_COLLISION)
        if next_state is not previous.state and next_state not in _TRANSITIONS[previous.state]:
            _fail(LifecycleErrorCode.STATE_TRANSITION_INVALID)
    return LifecycleCheckpoint.create(request, state=next_state, recorded_at=recorded_at, store_snapshot_ref=store_snapshot_ref, completed_effects=completed_effects, pending_effects=pending_effects, error_code=error_code, content_purge_proof_ref=content_purge_proof_ref, attempt_count=previous.attempt_count if previous else 0)


def make_lifecycle_job(request: LifecycleRequest, *, source_scope: SourceScope, checkpoint: LifecycleCheckpoint) -> IndexJobRecord:
    if type(request) is not LifecycleRequest or type(source_scope) is not SourceScope or type(checkpoint) is not LifecycleCheckpoint or checkpoint.request_id != request.request_id:
        _fail()
    status = {
        LifecycleState.REQUESTED: "pending", LifecycleState.QUIESCING: "running", LifecycleState.APPLYING: "running", LifecycleState.PROJECTING: "running", LifecycleState.VERIFYING: "running", LifecycleState.COMPLETE: "completed", LifecycleState.DEGRADED: "blocked", LifecycleState.FAILED: "failed", LifecycleState.ROLLED_BACK: "cancelled",
    }[checkpoint.state]
    return IndexJobRecord.create(job_kind=IndexJobKind.DELETION, source_scope=source_scope, request_ref=request.request_id, profile_ref="ulo02_v1", status=status, max_items=1, time_budget_ms=1_000, cursor=checkpoint.to_json(), attempt_count=checkpoint.attempt_count, started_at=checkpoint.recorded_at if checkpoint.state is not LifecycleState.REQUESTED else "", completed_at=checkpoint.recorded_at if checkpoint.state in {LifecycleState.COMPLETE, LifecycleState.FAILED, LifecycleState.ROLLED_BACK} else "")


def lifecycle_checkpoint_from_job(job: IndexJobRecord) -> LifecycleCheckpoint:
    if type(job) is not IndexJobRecord or job.job_kind is not IndexJobKind.DELETION or job.profile_ref != "ulo02_v1":
        _fail()
    checkpoint = LifecycleCheckpoint.from_json(job.cursor)
    if checkpoint.request_id != job.request_ref:
        _fail(LifecycleErrorCode.REVISION_MISMATCH)
    return checkpoint


__all__ = [
    "LIFECYCLE_CHECKPOINT_SCHEMA", "LIFECYCLE_REQUEST_SCHEMA", "LifecycleActionKind", "LifecycleCheckpoint", "LifecycleContractError", "LifecycleDisposition", "LifecycleEffectKind", "LifecycleErrorCode", "LifecycleRequest", "LifecycleScopeKind", "LifecycleState", "LifecycleTarget", "advance_lifecycle", "lifecycle_checkpoint_from_job", "make_lifecycle_job",
]


def complete_lifecycle(previous: LifecycleCheckpoint, request: LifecycleRequest, *, store: object, result: object, recorded_at: str, completed_effects: tuple[LifecycleEffectKind, ...], pending_effects: tuple[LifecycleEffectKind, ...] = (), error_code: LifecycleErrorCode = LifecycleErrorCode.NONE, content_purge_proof_ref: str = "") -> LifecycleCheckpoint:
    if type(previous) is not LifecycleCheckpoint or type(request) is not LifecycleRequest or type(completed_effects) is not tuple or type(pending_effects) is not tuple or type(error_code) is not LifecycleErrorCode:
        _fail(LifecycleErrorCode.EFFECT_FAILED)
    request_json = _lifecycle_request_projection(request)[1]
    try:
        request = LifecycleRequest.from_json(request_json)
    except Exception:
        _fail(LifecycleErrorCode.INVALID_REQUEST)
    if previous.request_id != request.request_id or previous.state is not LifecycleState.VERIFYING or pending_effects or error_code is not LifecycleErrorCode.NONE:
        _fail(LifecycleErrorCode.EFFECT_FAILED)
    required = {LifecycleEffectKind.QUERY_FENCE, LifecycleEffectKind.TRUTH_TOMBSTONE}
    if not required.issubset(completed_effects):
        _fail(LifecycleErrorCode.EFFECT_FAILED)
    from src.unified_source_index_tombstone_policy import (
        TombstoneCommitResult, TombstoneEvidence, _FACTORY_TOKEN, _is_factory_object, _verified_result_projection,
    )
    from src.unified_source_index_stores import StoreSnapshot, TombstoneCommitReceipt, verify_tombstone_commit
    if _verified_result_projection(result, request, store) is None:
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    try:
        result_token = object.__getattribute__(result, "_factory_token")
        result_binding = object.__getattribute__(result, "_store_binding")
        result_evidence = object.__getattribute__(result, "evidence")
        result_plan_digest = object.__getattribute__(result, "plan_digest")
    except Exception:
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    if (result_token is not _FACTORY_TOKEN or result_binding != id(store)
            or type(result_evidence) is not tuple or type(result_plan_digest) is not str or not result_plan_digest):
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    if type(result.receipt) is not TombstoneCommitReceipt or type(result.receipt_digest) is not str or type(result.evidence_digest) is not str or type(result.committed_snapshot) is not StoreSnapshot:
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    if result.receipt.commands and not result.evidence:
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    if result.receipt.committed_snapshot.snapshot_ref != result.committed_snapshot.snapshot_ref:
        _fail(LifecycleErrorCode.REVISION_MISMATCH)
    verified = verify_tombstone_commit(store, result.receipt, expected_authority_ref=result.receipt.authority_ref, expected_plan_digest=result.plan_digest, expected_commands=result.receipt.commands)
    if verified.receipt.receipt_digest != result.receipt_digest or result.receipt.committed_snapshot != result.committed_snapshot:
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    expected_evidence = []
    for evidence, command in zip(result.evidence, result.receipt.commands, strict=True):
        if (
            type(evidence) is not TombstoneEvidence
            or not _is_factory_object(evidence)
            or evidence._factory_token is not _FACTORY_TOKEN
            or evidence.request_id != request.request_id
            or evidence.plan_digest != result.plan_digest
            or evidence.base_snapshot_ref != result.receipt.base_snapshot.snapshot_ref
            or evidence.snapshot_ref != result.committed_snapshot.snapshot_ref
            or evidence.record_ref != command.record_ref
            or evidence.owner_scope != command.owner_scope
            or evidence.reason_code != command.reason
        ):
            _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
        expected_evidence.append(evidence.to_dict() | {"evidence_digest": ""})
    if len(expected_evidence) != len(result.receipt.commands):
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    expected_digest = "sha256:" + hashlib.sha256(_canonical(expected_evidence).encode("ascii")).hexdigest()
    if expected_digest != result.evidence_digest:
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    if request.action_kind is LifecycleActionKind.ERASE and (not content_purge_proof_ref or LifecycleEffectKind.CONTENT_PURGE not in completed_effects):
        _fail(LifecycleErrorCode.CONTENT_PURGE_UNPROVEN)
    core_commit_ref = "usi_commit_" + hashlib.sha256(_canonical({"request_id": request.request_id, "plan_digest": result.plan_digest, "receipt_digest": result.receipt_digest, "evidence_digest": result.evidence_digest, "snapshot_ref": result.committed_snapshot.snapshot_ref}).encode("ascii")).hexdigest()
    return LifecycleCheckpoint.create(request, state=LifecycleState.COMPLETE, recorded_at=recorded_at, store_snapshot_ref=result.committed_snapshot.snapshot_ref, completed_effects=completed_effects, pending_effects=pending_effects, error_code=error_code, content_purge_proof_ref=content_purge_proof_ref, core_commit_ref=core_commit_ref, _complete_token=_COMPLETE_TOKEN)


__all__ += ["complete_lifecycle"]
