"""Native, authenticated Planning source backed by the durable store."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import re
from types import MemberDescriptorType
from typing import Iterator

from core.auth import AuthManager
from src.harbour_authority import (
    AuthenticatedPrincipalIssuer,
    PrincipalRef,
    TrustedPrincipal,
)
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    PlanningAcceptedHeadBinding,
    PlanningAcceptedMigrationBundle,
    PlanningContextRevisionMember,
    PlanningRevision,
)
from src.private_context_confirmation import DisclosureBinding
from src.context_metadata_binding import McpContextMetadataAdmissionError, _mcp_recipient_digest, _require_mcp_metadata_admission
from src.mcp_context_authority import McpContextAuthorityError
from src.private_context_confirmation_app import ContextSelection, SourceSelection
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
)
from src.unified_source_index_owner_scope import OwnerScope
import src.planning_mcp_service as planning_policy


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$\Z")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HEAD_KEYS = frozenset({
    "owner_id", "project_id", "roadmap_id", "accepted_revision_id",
    "accepted_revision", "accepted_content_sha256", "current_revision_id",
    "current_revision", "current_content_sha256", "approved_revision_id",
    "approved_revision", "approved_content_sha256", "generation",
})
_POLICY_REF = "planning.context_durable.private_inline_local.v1"
_NAMESPACE = "planning.context_durable.v1"
_PROVIDER_REF = "planning.context_durable.v1"
_PROJECTION_SCHEMA = "odysseus.planning.owner_source_projection.v1"
_ERROR_CODES = frozenset({
    "invalid_binding", "invalid_context", "planning_unavailable",
    "planning_stale", "policy_denied", "payload_unavailable",
    "capture_unavailable", "source_not_found",
})


class DurablePlanningContextError(ValueError):
    """Fixed-code, content-free failure from the native Planning boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else "capture_unavailable"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"DurablePlanningContextError(code={self.code!r})"


def _fail(code: str) -> None:
    raise DurablePlanningContextError(code) from None


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        _fail("planning_stale")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _object_digest(value: object) -> str:
    return _digest(_canonical(value))


def _sha(value: object, code: str = "planning_stale") -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _opaque(value: object, code: str = "planning_stale") -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


@dataclass(frozen=True, slots=True, repr=False)
class DurablePlanningContextPolicy:
    namespace: str = field(repr=False)
    policy_ref: str = field(repr=False)
    classification: str = field(repr=False)
    content_policy: str = field(repr=False)
    byteform: str = field(repr=False)
    reject_sensitive_content: bool = field(repr=False)

    def __post_init__(self) -> None:
        expected = (
            _NAMESPACE, _POLICY_REF, "private", "inline_local",
            "durable_revision_document", True,
        )
        if (
            self.namespace, self.policy_ref, self.classification,
            self.content_policy, self.byteform, self.reject_sensitive_content,
        ) != expected:
            _fail("invalid_context")

    def __repr__(self) -> str:
        return "DurablePlanningContextPolicy(<redacted>)"

    def canonical(self) -> dict[str, object]:
        return {
            "byteform": self.byteform,
            "classification": self.classification,
            "content_policy": self.content_policy,
            "namespace": self.namespace,
            "policy_ref": self.policy_ref,
            "reject_sensitive_content": self.reject_sensitive_content,
        }


@dataclass(frozen=True, slots=True, repr=False)
class DurablePlanningSourceDescriptor:
    source: SourceRecord = field(repr=False)
    source_version: SourceVersionRecord = field(repr=False)
    project_id: str = field(repr=False)
    roadmap_id: str = field(repr=False)
    approved_revision: int = field(repr=False)
    durable_revision_id: str = field(repr=False)
    durable_content_sha256: str = field(repr=False)
    durable_payload_sha256: str = field(repr=False)
    canonical_projection_sha256: str = field(repr=False)
    provenance_ref: str = field(repr=False)
    boundary_sha256: str = field(repr=False)
    record_sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.source) is not SourceRecord or type(self.source_version) is not SourceVersionRecord:
            _fail("planning_stale")
        if type(self.approved_revision) is not int or isinstance(self.approved_revision, bool) or self.approved_revision < 1:
            _fail("planning_stale")
        for value in (self.project_id, self.roadmap_id, self.durable_revision_id, self.provenance_ref):
            _opaque(value)
        for value in (
            self.durable_content_sha256, self.durable_payload_sha256,
            self.canonical_projection_sha256, self.boundary_sha256,
            self.record_sha256,
        ):
            _sha(value)
        if (
            self.source.source_kind is not SourceKind.PLANNING
            or self.source.classification is not Classification.PRIVATE
            or self.source.content_policy is not ContentPolicy.INLINE_LOCAL
            or self.source_version.classification is not Classification.PRIVATE
            or self.source_version.content_policy is not ContentPolicy.INLINE_LOCAL
            or self.source_version.source_id != self.source.source_id
            or self.source_version.source_version_id != self.provenance_ref
        ):
            _fail("planning_stale")

    def canonical(self) -> dict[str, object]:
        return {
            "approved_revision": self.approved_revision,
            "canonical_projection_sha256": self.canonical_projection_sha256,
            "durable_content_sha256": self.durable_content_sha256,
            "durable_payload_sha256": self.durable_payload_sha256,
            "durable_revision_id": self.durable_revision_id,
            "project_id": self.project_id,
            "provenance_ref": self.provenance_ref,
            "record_sha256": self.record_sha256,
            "roadmap_id": self.roadmap_id,
            "selection_ref": self.source.source_id,
            "revision_ref": self.source_version.source_version_id,
            "boundary_sha256": self.boundary_sha256,
        }

    def selection(self) -> SourceSelection:
        return SourceSelection(
            "planning", self.source.source_id, self.source_version.source_version_id,
        )


@dataclass(frozen=True, slots=True, repr=False)
class DurablePlanningSourceSnapshot:
    descriptors: tuple[DurablePlanningSourceDescriptor, ...] = field(repr=False)
    policy: DurablePlanningContextPolicy = field(repr=False)
    boundary_sha256: str = field(repr=False)
    metadata_sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.descriptors) is not tuple
            or any(type(item) is not DurablePlanningSourceDescriptor for item in self.descriptors)
            or type(self.policy) is not DurablePlanningContextPolicy
        ):
            _fail("planning_stale")
        _sha(self.boundary_sha256)
        _sha(self.metadata_sha256)
        keys = tuple((item.project_id, item.roadmap_id) for item in self.descriptors)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            _fail("planning_stale")

    @property
    def digest(self) -> str:
        return self.metadata_sha256

    @property
    def metadata_digest(self) -> str:
        return self.metadata_sha256

    def __repr__(self) -> str:
        return "DurablePlanningSourceSnapshot(<redacted>)"


@dataclass(frozen=True, slots=True)
class _Capture:
    snapshot: DurablePlanningSourceSnapshot
    payloads: tuple[tuple[str, bytes], ...]


class DurablePlanningContextSource:
    """Read exact approved durable Planning documents under app authority."""

    __slots__ = (
        "_runtime", "_auth_manager", "_project_id", "_policy", "_store", "_issuer",
        "_dual_identity", "_dual_identity_anchor",
    )

    def __init__(
        self,
        *,
        runtime: AppOwnedPlanningRuntime,
        auth_manager: AuthManager,
        project_id: str,
        policy: DurablePlanningContextPolicy,
    ) -> None:
        if (
            type(runtime) is not AppOwnedPlanningRuntime
            or type(auth_manager) is not AuthManager
            or type(policy) is not DurablePlanningContextPolicy
            or type(project_id) is not str
            or _ID_RE.fullmatch(project_id) is None
            or type(runtime.durable_store) is not DurablePlanningRevisionStore
            or type(runtime.issuer) is not AuthenticatedPrincipalIssuer
        ):
            _fail("invalid_context")
        self._runtime = runtime
        self._auth_manager = auth_manager
        self._project_id = project_id
        self._policy = policy
        self._store = runtime.durable_store
        self._issuer = runtime.issuer
        dual_identity = (
            type(self).__dict__.get("_capture_from_bundle"),
            type(self).__dict__.get("_capture_mcp_from_bundle"),
            type(self._store).__dict__.get(
                "_read_dual_subject_bound_accepted_migration_bundle",
            ),
            type(self).__dict__.get("_principal"),
            type(self).__dict__.get("_principal_mcp"),
            type(self).__dict__.get("_capture_dual_from_bundle"),
            type(self).__dict__.get("_heads"),
            type(self).__dict__.get("_records"),
            type(self).__dict__.get("_assert_runtime_pins"),
            planning_policy._contains_sensitive_value,
            _object_digest,
            _canonical,
            _mcp_recipient_digest,
            _require_mcp_metadata_admission,
            OwnerScope,
            SourceRecord,
            SourceVersionRecord,
            SourceVersionRecord.__dict__.get("create"),
            DurablePlanningContextPolicy.__dict__["canonical"],
            DisclosureBinding.__dict__["_canonical"],
            compute_roadmap_content_hash,
            OwnerScope.__dict__.get("for_subject_id"),
            SourceRecord.__dict__["__init__"],
            SourceRecord.__dict__["__post_init__"],
            SourceVersionRecord.__dict__["__init__"],
            SourceVersionRecord.__dict__["__post_init__"],
            DurablePlanningSourceDescriptor.__dict__["canonical"],
            self._runtime,
            self._auth_manager,
            self._project_id,
            self._policy,
            self._store,
            self._issuer,
            self._issuer.issuer_id,
            self._issuer.key_id,
            self._policy.namespace,
            self._policy.policy_ref,
            self._policy.classification,
            self._policy.content_policy,
            self._policy.byteform,
            self._policy.reject_sensitive_content,
            _digest,
            json,
            json.loads,
            json.dumps,
            hashlib,
            hashlib.sha256,
            DurablePlanningSourceDescriptor,
            DurablePlanningSourceDescriptor.__dict__["__init__"],
            DurablePlanningSourceDescriptor.__dict__["__post_init__"],
            DurablePlanningSourceSnapshot,
            DurablePlanningSourceSnapshot.__dict__["__init__"],
            DurablePlanningSourceSnapshot.__dict__["__post_init__"],
            _Capture,
            _Capture.__dict__["__init__"],
            _Capture.__dict__.get("__post_init__"),
            OwnerScope.__dict__.get("__new__"),
            OwnerScope.__dict__["__init__"],
            OwnerScope.__dict__["__post_init__"],
            SourceRecord.__dict__.get("__new__"),
            SourceVersionRecord.__dict__.get("__new__"),
            DurablePlanningSourceDescriptor.__dict__.get("__new__"),
            DurablePlanningSourceSnapshot.__dict__.get("__new__"),
            _Capture.__dict__.get("__new__"),
            type(self).__dict__.get("_verify_payload"),
        )
        self._dual_identity = dual_identity
        self._dual_identity_anchor = dual_identity

    def _assert_runtime_pins(self) -> None:
        source_type = type(self)
        if source_type is not DurablePlanningContextSource:
            _fail("invalid_context")
        anchor_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_ANCHOR_SLOT")
        identity_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_SLOT")
        anchor_descriptor = source_type.__dict__.get("_dual_identity_anchor")
        identity_descriptor = source_type.__dict__.get("_dual_identity")
        if (
            type(anchor_slot) is not MemberDescriptorType
            or anchor_slot.__objclass__ is not source_type
            or type(anchor_slot.__name__) is not str
            or anchor_slot.__name__ != "_dual_identity_anchor"
            or type(identity_slot) is not MemberDescriptorType
            or identity_slot.__objclass__ is not source_type
            or type(identity_slot.__name__) is not str
            or identity_slot.__name__ != "_dual_identity"
            or anchor_descriptor is not anchor_slot
            or identity_descriptor is not identity_slot
            or type(anchor_descriptor) is not MemberDescriptorType
            or anchor_descriptor.__objclass__ is not source_type
            or type(anchor_descriptor.__name__) is not str
            or anchor_descriptor.__name__ != "_dual_identity_anchor"
            or type(identity_descriptor) is not MemberDescriptorType
            or identity_descriptor.__objclass__ is not source_type
            or type(identity_descriptor.__name__) is not str
            or identity_descriptor.__name__ != "_dual_identity"
        ):
            _fail("invalid_context")
        identity = anchor_slot.__get__(self, source_type)
        if (
            type(identity) is not tuple
            or len(identity) != 65
            or identity_slot.__get__(self, source_type) is not identity
            or type(self._runtime) is not AppOwnedPlanningRuntime
            or self._runtime.durable_store is not self._store
            or self._runtime.issuer is not self._issuer
            or type(self._store) is not DurablePlanningRevisionStore
            or type(self._issuer) is not AuthenticatedPrincipalIssuer
            or type(self).__dict__.get("_capture_from_bundle") is not identity[0]
            or type(self).__dict__.get("_capture_mcp_from_bundle") is not identity[1]
            or type(self._store).__dict__.get(
                "_read_dual_subject_bound_accepted_migration_bundle",
            ) is not identity[2]
            or "_read_dual_subject_bound_accepted_migration_bundle"
                in getattr(self._store, "__dict__", {})
            or type(self).__dict__.get("_principal") is not identity[3]
            or type(self).__dict__.get("_principal_mcp") is not identity[4]
            or type(self).__dict__.get("_assert_runtime_pins") is not identity[8]
            or type(identity[64]) is not staticmethod
            or type(self).__dict__.get("_verify_payload") is not identity[64]
        ):
            _fail("invalid_context")

    def capture(self, binding: DisclosureBinding) -> DurablePlanningSourceSnapshot:
        self._assert_runtime_pins()
        self._validate_binding(binding)
        try:
            with self._principal(binding) as (principal, alias):
                return self._stable_capture(binding, principal, alias).snapshot
        except DurablePlanningContextError:
            raise
        except Exception:
            _fail("capture_unavailable")
        finally:
            self._assert_runtime_pins()

    def discover(self, binding: DisclosureBinding) -> tuple[SourceSelection, ...]:
        snapshot = self.capture(binding)
        return tuple(item.selection() for item in snapshot.descriptors)

    def resolve(
        self, binding: DisclosureBinding, selection: ContextSelection,
    ) -> tuple[DurablePlanningSourceDescriptor, ...]:
        self._assert_runtime_pins()
        if type(selection) is not ContextSelection:
            _fail("invalid_context")
        planning_items = tuple(item for item in selection.items if item.source == "planning")
        if not planning_items:
            _fail("invalid_context")
        self._validate_binding(binding)
        try:
            with self._principal(binding) as (principal, alias):
                captured = self._stable_capture(binding, principal, alias).snapshot
                by_key = {
                    (item.source.source_id, item.source_version.source_version_id): item
                    for item in captured.descriptors
                }
                result = []
                for selected in planning_items:
                    item = by_key.get((selected.selection_ref, selected.revision_ref))
                    if item is None:
                        _fail("source_not_found")
                    result.append(item)
                return tuple(result)
        except DurablePlanningContextError:
            raise
        except Exception:
            _fail("capture_unavailable")
        finally:
            self._assert_runtime_pins()

    def resolve_mcp(self, admission: object, metadata_binding: object, selection: ContextSelection) -> tuple[DurablePlanningSourceDescriptor, ...]:
        """Resolve durable records through the live MCP lease, never a cookie adapter."""
        self._assert_runtime_pins()
        if type(selection) is not ContextSelection:
            _fail("invalid_context")
        selected = tuple(item for item in selection.items if item.source == "planning")
        if not selected or len(selected) != len(selection.items): _fail("invalid_context")
        foundation = _require_mcp_metadata_admission(admission, metadata_binding)
        try:
            with self._principal_mcp(admission, metadata_binding) as (principal, alias, foundation):
                captured = self._stable_capture_mcp(admission, metadata_binding, principal, alias, foundation).snapshot
                by_key = {(item.source.source_id, item.source_version.source_version_id): item for item in captured.descriptors}
                result = tuple(by_key.get((item.selection_ref, item.revision_ref)) for item in selected)
                if any(item is None for item in result): _fail("source_not_found")
                _require_mcp_metadata_admission(admission, metadata_binding)
                return result
        except McpContextAuthorityError: raise
        except McpContextMetadataAdmissionError: raise
        except DurablePlanningContextError: raise
        except ValueError: raise
        except Exception: _fail("capture_unavailable")
        finally: self._assert_runtime_pins()

    def resolve_dual(
        self,
        binding: DisclosureBinding,
        admission: object,
        metadata_binding: object,
        selection: ContextSelection,
        *,
        _content_target: SourceSelection | None = None,
    ) -> tuple[
        tuple[DurablePlanningSourceDescriptor, ...],
        tuple[DurablePlanningSourceDescriptor, ...],
    ] | tuple[
        tuple[DurablePlanningSourceDescriptor, ...],
        tuple[DurablePlanningSourceDescriptor, ...],
        bytes,
        bytes,
    ]:
        """Project one freshly authenticated image into both recipient domains."""
        def read_identity_slots() -> tuple[object, object]:
            source_type = type(self)
            if source_type is not DurablePlanningContextSource:
                _fail("invalid_context")
            anchor_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_ANCHOR_SLOT")
            identity_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_SLOT")
            anchor_descriptor = source_type.__dict__.get("_dual_identity_anchor")
            identity_descriptor = source_type.__dict__.get("_dual_identity")
            if (
                type(anchor_slot) is not MemberDescriptorType
                or anchor_slot.__objclass__ is not source_type
                or type(anchor_slot.__name__) is not str
                or anchor_slot.__name__ != "_dual_identity_anchor"
                or type(identity_slot) is not MemberDescriptorType
                or identity_slot.__objclass__ is not source_type
                or type(identity_slot.__name__) is not str
                or identity_slot.__name__ != "_dual_identity"
                or anchor_descriptor is not anchor_slot
                or identity_descriptor is not identity_slot
                or type(anchor_descriptor) is not MemberDescriptorType
                or anchor_descriptor.__objclass__ is not source_type
                or type(anchor_descriptor.__name__) is not str
                or anchor_descriptor.__name__ != "_dual_identity_anchor"
                or type(identity_descriptor) is not MemberDescriptorType
                or identity_descriptor.__objclass__ is not source_type
                or type(identity_descriptor.__name__) is not str
                or identity_descriptor.__name__ != "_dual_identity"
            ):
                _fail("invalid_context")
            return (
                anchor_slot.__get__(self, source_type),
                identity_slot.__get__(self, source_type),
            )

        identity, stored_identity = read_identity_slots()
        if stored_identity is not identity:
            _fail("invalid_context")
        if type(binding) is not DisclosureBinding:
            _fail("invalid_binding")
        binding_ref = binding
        binding_entry = (
            binding.owner_sid,
            binding.chat_session_id,
            binding.auth_session_binding,
            binding.security_revision,
            binding.audience,
        )
        if (
            any(type(value) is not str for value in binding_entry[:3] + binding_entry[4:])
            or type(binding_entry[3]) is not int
        ):
            _fail("invalid_binding")

        def guard() -> None:
            anchor, current_identity = read_identity_slots()
            if (
                type(identity) is not tuple
                or len(identity) != 65
                or anchor is not identity
                or current_identity is not identity
                or type(self).__dict__.get("_capture_dual_from_bundle") is not identity[5]
                or type(identity[6]) is not staticmethod
                or type(self).__dict__.get("_heads") is not identity[6]
                or type(self).__dict__.get("_records") is not identity[7]
                or type(self).__dict__.get("_assert_runtime_pins") is not identity[8]
                or planning_policy._contains_sensitive_value is not identity[9]
                or globals().get("_object_digest") is not identity[10]
                or globals().get("_canonical") is not identity[11]
                or globals().get("_mcp_recipient_digest") is not identity[12]
                or globals().get("_require_mcp_metadata_admission") is not identity[13]
                or globals().get("OwnerScope") is not identity[14]
                or globals().get("SourceRecord") is not identity[15]
                or globals().get("SourceVersionRecord") is not identity[16]
                or type(identity[17]) is not classmethod
                or SourceVersionRecord.__dict__.get("create") is not identity[17]
                or DurablePlanningContextPolicy.__dict__.get("canonical") is not identity[18]
                or DisclosureBinding.__dict__.get("_canonical") is not identity[19]
                or globals().get("compute_roadmap_content_hash") is not identity[20]
                or type(identity[21]) is not classmethod
                or OwnerScope.__dict__.get("for_subject_id") is not identity[21]
                or SourceRecord.__dict__.get("__init__") is not identity[22]
                or SourceRecord.__dict__.get("__post_init__") is not identity[23]
                or SourceVersionRecord.__dict__.get("__init__") is not identity[24]
                or SourceVersionRecord.__dict__.get("__post_init__") is not identity[25]
                or DurablePlanningSourceDescriptor.__dict__.get("canonical") is not identity[26]
                or self._runtime is not identity[27]
                or self._auth_manager is not identity[28]
                or self._project_id is not identity[29]
                or self._policy is not identity[30]
                or self._store is not identity[31]
                or self._issuer is not identity[32]
                or self._issuer.issuer_id is not identity[33]
                or self._issuer.key_id is not identity[34]
                or self._policy.namespace is not identity[35]
                or self._policy.policy_ref is not identity[36]
                or self._policy.classification is not identity[37]
                or self._policy.content_policy is not identity[38]
                or self._policy.byteform is not identity[39]
                or self._policy.reject_sensitive_content is not identity[40]
                or globals().get("_digest") is not identity[41]
                or globals().get("json") is not identity[42]
                or json.loads is not identity[43]
                or json.dumps is not identity[44]
                or globals().get("hashlib") is not identity[45]
                or hashlib.sha256 is not identity[46]
                or globals().get("DurablePlanningSourceDescriptor") is not identity[47]
                or DurablePlanningSourceDescriptor.__dict__.get("__init__") is not identity[48]
                or DurablePlanningSourceDescriptor.__dict__.get("__post_init__") is not identity[49]
                or globals().get("DurablePlanningSourceSnapshot") is not identity[50]
                or DurablePlanningSourceSnapshot.__dict__.get("__init__") is not identity[51]
                or DurablePlanningSourceSnapshot.__dict__.get("__post_init__") is not identity[52]
                or globals().get("_Capture") is not identity[53]
                or _Capture.__dict__.get("__init__") is not identity[54]
                or _Capture.__dict__.get("__post_init__") is not identity[55]
                or OwnerScope.__dict__.get("__new__") is not identity[56]
                or OwnerScope.__dict__.get("__init__") is not identity[57]
                or OwnerScope.__dict__.get("__post_init__") is not identity[58]
                or SourceRecord.__dict__.get("__new__") is not identity[59]
                or SourceVersionRecord.__dict__.get("__new__") is not identity[60]
                or DurablePlanningSourceDescriptor.__dict__.get("__new__") is not identity[61]
                or DurablePlanningSourceSnapshot.__dict__.get("__new__") is not identity[62]
                or _Capture.__dict__.get("__new__") is not identity[63]
                or type(identity[64]) is not staticmethod
                or type(self).__dict__.get("_verify_payload") is not identity[64]
                or binding is not binding_ref
            ):
                _fail("invalid_context")
            current_binding = (
                binding.owner_sid,
                binding.chat_session_id,
                binding.auth_session_binding,
                binding.security_revision,
                binding.audience,
            )
            if (
                any(type(value) is not type(expected) for value, expected in zip(current_binding, binding_entry))
                or current_binding != binding_entry
            ):
                _fail("invalid_binding")
            identity[8](self)

        guard()
        if type(selection) is not ContextSelection:
            _fail("invalid_context")
        selected = tuple(item for item in selection.items if item.source == "planning")
        if not selected or len(selected) != len(selection.items):
            _fail("invalid_context")
        if (
            _content_target is not None
            and (
                type(_content_target) is not SourceSelection
                or _content_target.source != "planning"
                or _content_target not in selected
            )
        ):
            _fail("invalid_context")
        selection_keys = tuple(
            (item.selection_ref, item.revision_ref) for item in selected
        )
        native_probe = (_item for _item in ())
        native_generator_type = type(native_probe)
        native_next = native_generator_type.__dict__["__next__"]
        native_close = native_generator_type.__dict__["close"]
        if globals().get("StopIteration") is not None:
            _fail("invalid_context")
        try:
            native_next(native_probe)
        except StopIteration as native_completed:
            native_stop_type = type(native_completed)
        else:
            _fail("invalid_context")
        native_close(native_probe)
        validation_generator = None
        guard()
        foundation = identity[13](admission, metadata_binding)
        guard()
        try:
            with identity[3](self, binding) as (cookie_principal, cookie_alias):
                guard()
                with identity[4](
                    self, admission, metadata_binding,
                ) as (mcp_principal, mcp_alias, held_foundation):
                    guard()
                    if (
                        held_foundation is not foundation
                        or type(cookie_alias) is not str
                        or type(mcp_alias) is not str
                        or cookie_alias != mcp_alias
                    ):
                        _fail("invalid_binding")
                    bundle = identity[2](
                        self._store,
                        cookie_principal,
                        mcp_principal,
                        project_id=self._project_id,
                    )
                    guard()
                    validation_generator = identity[5](
                        self,
                        binding,
                        admission,
                        metadata_binding,
                        bundle,
                        cookie_alias,
                        foundation,
                        cookie_principal,
                        mcp_principal,
                        identity,
                    )
                    guard()
                    if type(validation_generator) is not native_generator_type:
                        _fail("invalid_context")
                    captures = native_next(validation_generator)
                    guard()
                    if (
                        type(captures) is not tuple
                        or len(captures) != 2
                        or type(captures[0]) is not identity[53]
                        or type(captures[1]) is not identity[53]
                    ):
                        _fail("invalid_context")
                    cookie_capture, mcp_capture = captures
                    cookie_by_key = {
                        (item.source.source_id, item.source_version.source_version_id): item
                        for item in cookie_capture.snapshot.descriptors
                    }
                    mcp_by_key = {
                        (item.source.source_id, item.source_version.source_version_id): item
                        for item in mcp_capture.snapshot.descriptors
                    }
                    cookie_result = tuple(cookie_by_key.get(key) for key in selection_keys)
                    mcp_result = tuple(mcp_by_key.get(key) for key in selection_keys)
                    if any(item is None for item in cookie_result + mcp_result):
                        _fail("source_not_found")
                    cookie_payload = mcp_payload = None
                    if _content_target is not None:
                        target_key = (
                            _content_target.selection_ref,
                            _content_target.revision_ref,
                        )
                        cookie_descriptor = cookie_by_key.get(target_key)
                        mcp_descriptor = mcp_by_key.get(target_key)
                        if (
                            type(cookie_descriptor) is not identity[47]
                            or type(mcp_descriptor) is not identity[47]
                        ):
                            _fail("source_not_found")
                        cookie_matches = tuple(
                            payload for revision_id, payload in cookie_capture.payloads
                            if revision_id == cookie_descriptor.durable_revision_id
                        )
                        mcp_matches = tuple(
                            payload for revision_id, payload in mcp_capture.payloads
                            if revision_id == mcp_descriptor.durable_revision_id
                        )
                        if (
                            len(cookie_matches) != 1
                            or len(mcp_matches) != 1
                            or type(cookie_matches[0]) is not bytes
                            or type(mcp_matches[0]) is not bytes
                        ):
                            _fail("payload_unavailable")
                        verify_operations = (
                            identity[41], identity[43], identity[11], identity[20],
                        )
                        identity[64].__func__(
                            cookie_matches[0], cookie_descriptor,
                            _operations=verify_operations, _guard=guard,
                        )
                        identity[64].__func__(
                            mcp_matches[0], mcp_descriptor,
                            _operations=verify_operations, _guard=guard,
                        )
                        cookie_payload = cookie_matches[0]
                        mcp_payload = mcp_matches[0]
                guard()
                if native_next(validation_generator) is not None:
                    _fail("invalid_context")
            guard()
            if native_next(validation_generator) is not None:
                _fail("invalid_context")
            guard()
            guard()
            current = identity[13](admission, metadata_binding)
            guard()
            if current is not foundation:
                _fail("invalid_binding")
            if _content_target is None:
                result = (cookie_result, mcp_result)
            else:
                if type(cookie_payload) is not bytes or type(mcp_payload) is not bytes:
                    _fail("payload_unavailable")
                result = (
                    cookie_result, mcp_result, cookie_payload, mcp_payload,
                )
            guard()
            try:
                native_next(validation_generator)
            except native_stop_type as completed:
                if type(completed) is not native_stop_type or completed.value is not None:
                    _fail("invalid_context")
            else:
                _fail("invalid_context")
            validation_generator = None
            return result
        except McpContextAuthorityError:
            raise
        except McpContextMetadataAdmissionError:
            raise
        except DurablePlanningContextError:
            raise
        except ValueError:
            raise
        except Exception:
            _fail("capture_unavailable")
        finally:
            if type(validation_generator) is native_generator_type:
                try:
                    native_close(validation_generator)
                except DurablePlanningContextError:
                    raise
                except Exception:
                    _fail("capture_unavailable")

    def _capture_dual_from_bundle(
        self,
        binding: DisclosureBinding,
        admission: object,
        metadata_binding: object,
        bundle: PlanningAcceptedMigrationBundle,
        alias: str,
        foundation: object,
        cookie_principal: TrustedPrincipal,
        mcp_principal: TrustedPrincipal,
        identity: tuple[object, ...],
    ) -> Iterator[tuple[_Capture, _Capture] | None]:
        """Validate one raw bundle once, then render isolated recipient graphs."""

        def read_identity_slots() -> tuple[object, object]:
            source_type = type(self)
            if source_type is not DurablePlanningContextSource:
                _fail("invalid_context")
            anchor_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_ANCHOR_SLOT")
            identity_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_SLOT")
            anchor_descriptor = source_type.__dict__.get("_dual_identity_anchor")
            identity_descriptor = source_type.__dict__.get("_dual_identity")
            if (
                type(anchor_slot) is not MemberDescriptorType
                or anchor_slot.__objclass__ is not source_type
                or type(anchor_slot.__name__) is not str
                or anchor_slot.__name__ != "_dual_identity_anchor"
                or type(identity_slot) is not MemberDescriptorType
                or identity_slot.__objclass__ is not source_type
                or type(identity_slot.__name__) is not str
                or identity_slot.__name__ != "_dual_identity"
                or anchor_descriptor is not anchor_slot
                or identity_descriptor is not identity_slot
                or type(anchor_descriptor) is not MemberDescriptorType
                or anchor_descriptor.__objclass__ is not source_type
                or type(anchor_descriptor.__name__) is not str
                or anchor_descriptor.__name__ != "_dual_identity_anchor"
                or type(identity_descriptor) is not MemberDescriptorType
                or identity_descriptor.__objclass__ is not source_type
                or type(identity_descriptor.__name__) is not str
                or identity_descriptor.__name__ != "_dual_identity"
            ):
                _fail("invalid_context")
            return (
                anchor_slot.__get__(self, source_type),
                identity_slot.__get__(self, source_type),
            )

        def guard() -> None:
            anchor, current_identity = read_identity_slots()
            if (
                type(identity) is not tuple
                or len(identity) != 65
                or anchor is not identity
                or current_identity is not identity
                or type(self).__dict__.get("_capture_dual_from_bundle") is not identity[5]
                or type(identity[6]) is not staticmethod
                or type(self).__dict__.get("_heads") is not identity[6]
                or type(self).__dict__.get("_records") is not identity[7]
                or type(self).__dict__.get("_assert_runtime_pins") is not identity[8]
                or planning_policy._contains_sensitive_value is not identity[9]
                or globals().get("_object_digest") is not identity[10]
                or globals().get("_canonical") is not identity[11]
                or globals().get("_mcp_recipient_digest") is not identity[12]
                or globals().get("_require_mcp_metadata_admission") is not identity[13]
                or globals().get("OwnerScope") is not identity[14]
                or globals().get("SourceRecord") is not identity[15]
                or globals().get("SourceVersionRecord") is not identity[16]
                or type(identity[17]) is not classmethod
                or SourceVersionRecord.__dict__.get("create") is not identity[17]
                or DurablePlanningContextPolicy.__dict__.get("canonical") is not identity[18]
                or DisclosureBinding.__dict__.get("_canonical") is not identity[19]
                or globals().get("compute_roadmap_content_hash") is not identity[20]
                or type(identity[21]) is not classmethod
                or OwnerScope.__dict__.get("for_subject_id") is not identity[21]
                or SourceRecord.__dict__.get("__init__") is not identity[22]
                or SourceRecord.__dict__.get("__post_init__") is not identity[23]
                or SourceVersionRecord.__dict__.get("__init__") is not identity[24]
                or SourceVersionRecord.__dict__.get("__post_init__") is not identity[25]
                or DurablePlanningSourceDescriptor.__dict__.get("canonical") is not identity[26]
                or self._runtime is not identity[27]
                or self._auth_manager is not identity[28]
                or self._project_id is not identity[29]
                or self._policy is not identity[30]
                or self._store is not identity[31]
                or self._issuer is not identity[32]
                or self._issuer.issuer_id is not identity[33]
                or self._issuer.key_id is not identity[34]
                or self._policy.namespace is not identity[35]
                or self._policy.policy_ref is not identity[36]
                or self._policy.classification is not identity[37]
                or self._policy.content_policy is not identity[38]
                or self._policy.byteform is not identity[39]
                or self._policy.reject_sensitive_content is not identity[40]
                or globals().get("_digest") is not identity[41]
                or globals().get("json") is not identity[42]
                or json.loads is not identity[43]
                or json.dumps is not identity[44]
                or globals().get("hashlib") is not identity[45]
                or hashlib.sha256 is not identity[46]
                or globals().get("DurablePlanningSourceDescriptor") is not identity[47]
                or DurablePlanningSourceDescriptor.__dict__.get("__init__") is not identity[48]
                or DurablePlanningSourceDescriptor.__dict__.get("__post_init__") is not identity[49]
                or globals().get("DurablePlanningSourceSnapshot") is not identity[50]
                or DurablePlanningSourceSnapshot.__dict__.get("__init__") is not identity[51]
                or DurablePlanningSourceSnapshot.__dict__.get("__post_init__") is not identity[52]
                or globals().get("_Capture") is not identity[53]
                or _Capture.__dict__.get("__init__") is not identity[54]
                or _Capture.__dict__.get("__post_init__") is not identity[55]
                or OwnerScope.__dict__.get("__new__") is not identity[56]
                or OwnerScope.__dict__.get("__init__") is not identity[57]
                or OwnerScope.__dict__.get("__post_init__") is not identity[58]
                or SourceRecord.__dict__.get("__new__") is not identity[59]
                or SourceVersionRecord.__dict__.get("__new__") is not identity[60]
                or DurablePlanningSourceDescriptor.__dict__.get("__new__") is not identity[61]
                or DurablePlanningSourceSnapshot.__dict__.get("__new__") is not identity[62]
                or _Capture.__dict__.get("__new__") is not identity[63]
                or type(identity[64]) is not staticmethod
                or type(self).__dict__.get("_verify_payload") is not identity[64]
            ):
                _fail("invalid_context")
            identity[8](self)

        def scalar(value: object, expected_type: type) -> object:
            if type(value) is not expected_type:
                _fail("planning_stale")
            return value

        def revision_stamp(revision: PlanningRevision) -> tuple[object, ...]:
            if type(revision) is not PlanningRevision:
                _fail("planning_stale")
            parent = revision.parent_revision_id
            if parent is not None and type(parent) is not str:
                _fail("planning_stale")
            principal_ref = revision.principal_ref
            if type(principal_ref) is not PrincipalRef:
                _fail("planning_stale")
            principal_values = (
                scalar(principal_ref.issuer_id, str),
                scalar(principal_ref.subject_id, str),
                scalar(principal_ref.owner_id, str),
                scalar(principal_ref.auth_context_digest, str),
                scalar(principal_ref.principal_digest, str),
            )
            return (
                revision,
                scalar(revision.revision_id, str),
                scalar(revision.owner_id, str),
                scalar(revision.project_id, str),
                scalar(revision.roadmap_id, str),
                scalar(revision.revision, int),
                parent,
                scalar(revision.payload_schema_id, str),
                scalar(revision.payload_sha256, str),
                scalar(revision.content_sha256, str),
                scalar(revision.immutable_locator, str),
                scalar(revision.revision_state, str),
                scalar(revision.created_at, str),
                principal_ref,
                principal_values,
            )

        def principal_stamp(principal: TrustedPrincipal) -> tuple[object, ...]:
            if type(principal) is not TrustedPrincipal:
                _fail("invalid_binding")
            return (
                principal,
                scalar(object.__getattribute__(principal, "_issuer_id"), str),
                scalar(object.__getattribute__(principal, "_key_id"), str),
                scalar(object.__getattribute__(principal, "_subject_id"), str),
                scalar(object.__getattribute__(principal, "_owner_id"), str),
                scalar(object.__getattribute__(principal, "_auth_context_digest"), str),
                scalar(object.__getattribute__(principal, "_issued_at"), str),
                scalar(object.__getattribute__(principal, "_expires_at"), str),
                scalar(object.__getattribute__(principal, "_nonce"), str),
                scalar(object.__getattribute__(principal, "_attestation"), str),
                scalar(object.__getattribute__(principal, "_sealed"), bool),
            )

        guard()
        if (
            type(bundle) is not PlanningAcceptedMigrationBundle
            or type(bundle.owner_id) is not str
            or type(bundle.project_id) is not str
            or type(bundle.state_digest) is not str
            or type(bundle.batch_bytes) is not bytes
            or type(bundle.heads) is not tuple
            or type(bundle.revisions) is not tuple
            or type(binding) is not DisclosureBinding
            or type(alias) is not str
        ):
            _fail("planning_stale")
        bundle_ref = bundle
        owner_ref = bundle.owner_id
        project_ref = bundle.project_id
        state_digest_ref = bundle.state_digest
        batch_bytes_ref = bundle.batch_bytes
        heads_ref = bundle.heads
        revisions_ref = bundle.revisions
        binding_ref = binding
        binding_values = (
            scalar(binding.owner_sid, str),
            scalar(binding.chat_session_id, str),
            scalar(binding.auth_session_binding, str),
            scalar(binding.security_revision, int),
            scalar(binding.audience, str),
        )
        source_refs = (
            self._runtime,
            self._auth_manager,
            self._project_id,
            self._policy,
            self._store,
            self._issuer,
            scalar(self._project_id, str),
            scalar(self._issuer.issuer_id, str),
            scalar(self._issuer.key_id, str),
            scalar(self._policy.namespace, str),
            scalar(self._policy.policy_ref, str),
            scalar(self._policy.classification, str),
            scalar(self._policy.content_policy, str),
            scalar(self._policy.byteform, str),
            scalar(self._policy.reject_sensitive_content, bool),
        )
        head_stamps = []
        for head in heads_ref:
            if type(head) is not PlanningAcceptedHeadBinding:
                _fail("planning_stale")
            values = (
                scalar(head.owner_id, str),
                scalar(head.project_id, str),
                scalar(head.roadmap_id, str),
                scalar(head.accepted_revision_id, str),
                scalar(head.accepted_revision, int),
                scalar(head.accepted_content_sha256, str),
                scalar(head.current_revision_id, str),
                scalar(head.current_revision, int),
                scalar(head.current_content_sha256, str),
                scalar(head.approved_revision_id, str),
                scalar(head.approved_revision, int),
                scalar(head.approved_content_sha256, str),
                scalar(head.generation, int),
            )
            head_stamps.append((head, values))
        member_stamps = []
        for member in revisions_ref:
            if type(member) is not PlanningContextRevisionMember:
                _fail("planning_stale")
            revision = member.revision
            payload = member.payload
            if type(payload) is not bytes:
                _fail("planning_stale")
            member_stamps.append((member, revision, payload, revision_stamp(revision)))
        head_stamps_tuple = tuple(head_stamps)
        member_stamps_tuple = tuple(member_stamps)
        cookie_principal_stamp = principal_stamp(cookie_principal)
        mcp_principal_stamp = principal_stamp(mcp_principal)

        def check_stamp() -> None:
            guard()
            if (
                bundle is not bundle_ref
                or type(bundle) is not PlanningAcceptedMigrationBundle
                or bundle.owner_id is not owner_ref
                or bundle.project_id is not project_ref
                or bundle.state_digest is not state_digest_ref
                or bundle.batch_bytes is not batch_bytes_ref
                or bundle.heads is not heads_ref
                or bundle.revisions is not revisions_ref
                or binding is not binding_ref
                or type(binding) is not DisclosureBinding
                or self._runtime is not source_refs[0]
                or self._auth_manager is not source_refs[1]
                or self._project_id is not source_refs[2]
                or self._policy is not source_refs[3]
                or self._store is not source_refs[4]
                or self._issuer is not source_refs[5]
            ):
                _fail("planning_stale")
            current_binding = (
                binding.owner_sid,
                binding.chat_session_id,
                binding.auth_session_binding,
                binding.security_revision,
                binding.audience,
            )
            if any(type(value) is not type(expected) for value, expected in zip(current_binding, binding_values)) or current_binding != binding_values:
                _fail("invalid_binding")
            current_source = (
                self._project_id,
                self._issuer.issuer_id,
                self._issuer.key_id,
                self._policy.namespace,
                self._policy.policy_ref,
                self._policy.classification,
                self._policy.content_policy,
                self._policy.byteform,
                self._policy.reject_sensitive_content,
            )
            expected_source = source_refs[6:]
            if any(type(value) is not type(expected) for value, expected in zip(current_source, expected_source)) or current_source != expected_source:
                _fail("planning_stale")
            if (
                type(heads_ref) is not tuple
                or len(heads_ref) != len(head_stamps_tuple)
                or type(revisions_ref) is not tuple
                or len(revisions_ref) != len(member_stamps_tuple)
            ):
                _fail("planning_stale")
            for index, (head, expected) in enumerate(head_stamps_tuple):
                if heads_ref[index] is not head or type(head) is not PlanningAcceptedHeadBinding:
                    _fail("planning_stale")
                current = (
                    head.owner_id, head.project_id, head.roadmap_id,
                    head.accepted_revision_id, head.accepted_revision,
                    head.accepted_content_sha256, head.current_revision_id,
                    head.current_revision, head.current_content_sha256,
                    head.approved_revision_id, head.approved_revision,
                    head.approved_content_sha256, head.generation,
                )
                if any(type(value) is not type(old) for value, old in zip(current, expected)) or current != expected:
                    _fail("planning_stale")
            for index, (member, revision, payload, expected) in enumerate(member_stamps_tuple):
                if (
                    revisions_ref[index] is not member
                    or type(member) is not PlanningContextRevisionMember
                    or member.revision is not revision
                    or member.payload is not payload
                    or type(payload) is not bytes
                    or type(revision) is not PlanningRevision
                    or expected[0] is not revision
                    or revision.principal_ref is not expected[13]
                    or type(revision.principal_ref) is not PrincipalRef
                ):
                    _fail("planning_stale")
                current = (
                    revision.revision_id, revision.owner_id, revision.project_id,
                    revision.roadmap_id, revision.revision,
                    revision.parent_revision_id, revision.payload_schema_id,
                    revision.payload_sha256, revision.content_sha256,
                    revision.immutable_locator, revision.revision_state,
                    revision.created_at,
                )
                old = expected[1:13]
                if any(type(value) is not type(previous) for value, previous in zip(current, old)) or current != old:
                    _fail("planning_stale")
                principal = revision.principal_ref
                principal_values = (
                    principal.issuer_id, principal.subject_id, principal.owner_id,
                    principal.auth_context_digest, principal.principal_digest,
                )
                if any(type(value) is not str for value in principal_values) or principal_values != expected[14]:
                    _fail("planning_stale")
            for stamp in (cookie_principal_stamp, mcp_principal_stamp):
                principal = stamp[0]
                if type(principal) is not TrustedPrincipal:
                    _fail("invalid_binding")
                current = tuple(
                    object.__getattribute__(principal, name)
                    for name in (
                        "_issuer_id", "_key_id", "_subject_id", "_owner_id",
                        "_auth_context_digest", "_issued_at", "_expires_at",
                        "_nonce", "_attestation", "_sealed",
                    )
                )
                if (
                    any(type(value) is not str for value in current[:-1])
                    or type(current[-1]) is not bool
                    or current != stamp[1:]
                ):
                    _fail("invalid_binding")

        def checked_operation(name: str, *args: object) -> object:
            check_stamp()
            if name == "loads":
                operation = identity[43]
            elif name == "canonical":
                operation = identity[11]
            elif name == "digest":
                operation = identity[41]
            elif name == "content_hash":
                operation = identity[20]
            else:
                _fail("invalid_context")
            result = operation(*args)
            check_stamp()
            expected_type = dict if name == "loads" else bytes if name == "canonical" else str
            if type(result) is not expected_type:
                _fail("planning_stale")
            return result

        def checked_object_digest(value: object) -> str:
            canonical = checked_operation("canonical", value)
            if type(canonical) is not bytes:
                _fail("planning_stale")
            digest = checked_operation("digest", canonical)
            if type(digest) is not str:
                _fail("planning_stale")
            return digest

        def seal_json(value: object) -> tuple[object, ...]:
            if type(value) is dict:
                items = []
                for key in sorted(value):
                    if type(key) is not str:
                        _fail("planning_stale")
                    items.append((key, seal_json(value[key])))
                return ("dict", tuple(items))
            if type(value) is list:
                return ("list", tuple(seal_json(item) for item in value))
            if type(value) is str:
                return ("str", value)
            if type(value) is int:
                return ("int", value)
            if type(value) is bool:
                return ("bool", value)
            if value is None:
                return ("none",)
            if type(value) is float:
                return ("float", value)
            _fail("planning_stale")

        def thaw_json(value: tuple[object, ...]) -> object:
            kind = value[0]
            if kind == "dict":
                return {key: thaw_json(item) for key, item in value[1]}
            if kind == "list":
                return [thaw_json(item) for item in value[1]]
            if kind in {"str", "int", "bool", "float"}:
                return value[1]
            if kind == "none":
                return None
            _fail("planning_stale")

        def policy_graph() -> dict[str, object]:
            check_stamp()
            value = identity[18](self._policy)
            check_stamp()
            if type(value) is not dict:
                _fail("planning_stale")
            return value

        def admission_check() -> None:
            check_stamp()
            current = identity[13](admission, metadata_binding)
            check_stamp()
            if current is not foundation:
                _fail("invalid_binding")

        check_stamp()
        if owner_ref != alias or project_ref != source_refs[6]:
            _fail("planning_stale")
        if _SHA256_RE.fullmatch(state_digest_ref) is None:
            _fail("planning_stale")
        try:
            batch = checked_operation("loads", batch_bytes_ref.decode("utf-8"))
        except Exception:
            _fail("planning_stale")
        if type(batch) is not dict:
            _fail("planning_stale")
        check_stamp()
        canonical_batch = checked_operation("canonical", batch)
        if canonical_batch != batch_bytes_ref:
            _fail("planning_stale")
        if (
            batch.get("state") != "accepted"
            or batch.get("owner_id") != alias
            or batch.get("project_id") != source_refs[6]
            or type(batch.get("source_items")) is not list
            or not batch["source_items"]
            or any(type(item) is not dict for item in batch["source_items"])
        ):
            _fail("planning_stale")
        check_stamp()
        head_map = identity[6].__func__(alias, heads_ref)
        check_stamp()
        record_map = identity[7](
            self, alias, revisions_ref, head_map,
            _checked_operation=checked_operation,
        )
        check_stamp()
        source_roadmaps = tuple(item.get("roadmap_id") for item in batch["source_items"])
        if (
            any(type(roadmap_id) is not str or not roadmap_id for roadmap_id in source_roadmaps)
            or len(set(source_roadmaps)) != len(source_roadmaps)
            or set(source_roadmaps) != set(head_map)
        ):
            _fail("planning_stale")
        batch_sealed = seal_json(batch)
        heads_sealed = tuple(seal_json(head_map[roadmap_id]) for roadmap_id in source_roadmaps)
        records_sealed = tuple(seal_json(item) for item in record_map["boundary_records"])
        neutral_rows = []
        for roadmap_id in sorted(head_map):
            head = head_map[roadmap_id]
            current = record_map["revisions"].get(head["current_revision_id"])
            if current is None:
                _fail("planning_stale")
            document = record_map["documents"][current.revision_id]
            payload = record_map["payloads"][current.revision_id]
            roadmap = document.get("roadmaps", [{}])[0]
            projection = {
                "schema_id": _PROJECTION_SCHEMA,
                "project_id": source_refs[6],
                "roadmap": roadmap,
            }
            check_stamp()
            projection_hash = checked_object_digest(projection)
            neutral_rows.append((
                roadmap_id,
                head["approved_revision"],
                current.revision_id,
                current.content_sha256,
                current.payload_sha256,
                current.created_at,
                projection_hash,
                payload,
            ))
        neutral_rows_tuple = tuple(neutral_rows)
        payloads_tuple = tuple(sorted(
            ((stamp[3][1], stamp[2]) for stamp in member_stamps_tuple),
            key=lambda item: item[0],
        ))
        del batch, head_map, record_map, projection, roadmap, document
        check_stamp()

        def cookie_render() -> _Capture:
            batch_graph = thaw_json(batch_sealed)
            heads_graph = [thaw_json(item) for item in heads_sealed]
            records_graph = tuple(thaw_json(item) for item in records_sealed)
            check_stamp()
            binding_graph = identity[19](binding)
            check_stamp()
            boundary_material = {
                "backend": {
                    "issuer_id": source_refs[7],
                    "key_id": source_refs[8],
                    "runtime": type(source_refs[0]).__qualname__,
                    "store": type(source_refs[4]).__qualname__,
                },
                "binding": binding_graph,
                "batch": batch_graph,
                "heads": heads_graph,
                "records": records_graph,
                "policy": policy_graph(),
                "project_id": source_refs[6],
            }
            check_stamp()
            boundary = checked_object_digest(boundary_material)
            del batch_graph, heads_graph, records_graph, binding_graph, boundary_material
            descriptors = []
            for row in neutral_rows_tuple:
                roadmap_id, approved_revision, revision_id, content_hash, payload_hash, created_at, projection_hash, payload = row
                check_stamp()
                try:
                    denied = identity[9](payload.decode("utf-8"))
                except UnicodeDecodeError:
                    _fail("planning_stale")
                check_stamp()
                if source_refs[14] and denied:
                    _fail("policy_denied")
                check_stamp()
                owner_scope = identity[21].__func__(identity[14], binding_values[0])
                check_stamp()
                source = identity[15](
                    owner_scope=owner_scope.value,
                    source_kind=SourceKind.PLANNING,
                    canonical_ref=f"{_NAMESPACE}:{source_refs[6]}:{roadmap_id}",
                    classification=Classification.PRIVATE,
                    content_policy=ContentPolicy.INLINE_LOCAL,
                    provider_ref=_PROVIDER_REF,
                )
                check_stamp()
                version = identity[17].__func__(
                    identity[16], source,
                    revision_ref=f"durable:{revision_id}:{payload_hash}",
                    content_hash=payload_hash,
                    version_observed_at=created_at,
                    provider_ref=_PROVIDER_REF,
                    classification=Classification.PRIVATE,
                    content_policy=ContentPolicy.INLINE_LOCAL,
                )
                check_stamp()
                record_material = {
                    "boundary_sha256": boundary,
                    "canonical_projection_sha256": projection_hash,
                    "content_sha256": content_hash,
                    "durable_revision_id": revision_id,
                    "payload_sha256": payload_hash,
                    "provenance_ref": version.source_version_id,
                    "project_id": source_refs[6],
                    "roadmap_id": roadmap_id,
                }
                record = checked_object_digest(record_material)
                check_stamp()
                descriptor = identity[47](
                    source=source,
                    source_version=version,
                    project_id=source_refs[6],
                    roadmap_id=roadmap_id,
                    approved_revision=approved_revision,
                    durable_revision_id=revision_id,
                    durable_content_sha256=content_hash,
                    durable_payload_sha256=payload_hash,
                    canonical_projection_sha256=projection_hash,
                    provenance_ref=version.source_version_id,
                    boundary_sha256=boundary,
                    record_sha256=record,
                )
                check_stamp()
                descriptors.append(descriptor)
            descriptors_tuple = tuple(descriptors)
            canonical_descriptors = []
            for item in descriptors_tuple:
                check_stamp()
                canonical_descriptors.append(identity[26](item))
                check_stamp()
            metadata_material = {
                "boundary_sha256": boundary,
                "descriptors": canonical_descriptors,
                "policy": policy_graph(),
            }
            check_stamp()
            metadata = checked_object_digest(metadata_material)
            check_stamp()
            snapshot = identity[50](
                descriptors_tuple, self._policy, boundary, metadata,
            )
            check_stamp()
            capture = identity[53](snapshot, payloads_tuple)
            check_stamp()
            return capture

        def mcp_render() -> _Capture:
            admission_check()
            batch_graph = thaw_json(batch_sealed)
            heads_graph = [thaw_json(item) for item in heads_sealed]
            records_graph = tuple(thaw_json(item) for item in records_sealed)
            native_material = {
                "backend": {
                    "issuer_id": source_refs[7],
                    "key_id": source_refs[8],
                    "runtime": type(source_refs[0]).__qualname__,
                    "store": type(source_refs[4]).__qualname__,
                },
                "batch": batch_graph,
                "heads": heads_graph,
                "records": records_graph,
                "policy": policy_graph(),
                "project_id": source_refs[6],
            }
            check_stamp()
            native_boundary = checked_object_digest(native_material)
            del batch_graph, heads_graph, records_graph, native_material
            check_stamp()
            boundary = "sha256:" + identity[12](
                "odysseus.context.mcp_metadata.planning.boundary.v1",
                metadata_binding,
                {"native": native_boundary, "project_id": source_refs[6]},
            )
            check_stamp()
            descriptors = []
            for row in neutral_rows_tuple:
                roadmap_id, approved_revision, revision_id, content_hash, payload_hash, created_at, projection_hash, payload = row
                check_stamp()
                try:
                    denied = identity[9](payload.decode("utf-8"))
                except UnicodeDecodeError:
                    _fail("planning_stale")
                check_stamp()
                if source_refs[14] and denied:
                    _fail("policy_denied")
                owner_subject_id = foundation.owner_subject_id
                if type(owner_subject_id) is not str:
                    _fail("invalid_binding")
                check_stamp()
                owner_scope = identity[21].__func__(identity[14], owner_subject_id)
                check_stamp()
                source = identity[15](
                    owner_scope=owner_scope.value,
                    source_kind=SourceKind.PLANNING,
                    canonical_ref=f"{_NAMESPACE}:{source_refs[6]}:{roadmap_id}",
                    classification=Classification.PRIVATE,
                    content_policy=ContentPolicy.INLINE_LOCAL,
                    provider_ref=_PROVIDER_REF,
                )
                check_stamp()
                version = identity[17].__func__(
                    identity[16], source,
                    revision_ref=f"durable:{revision_id}:{payload_hash}",
                    content_hash=payload_hash,
                    version_observed_at=created_at,
                    provider_ref=_PROVIDER_REF,
                    classification=Classification.PRIVATE,
                    content_policy=ContentPolicy.INLINE_LOCAL,
                )
                check_stamp()
                native_record_material = {
                    "boundary_sha256": native_boundary,
                    "canonical_projection_sha256": projection_hash,
                    "content_sha256": content_hash,
                    "durable_revision_id": revision_id,
                    "payload_sha256": payload_hash,
                    "provenance_ref": version.source_version_id,
                    "project_id": source_refs[6],
                    "roadmap_id": roadmap_id,
                }
                native_record = checked_object_digest(native_record_material)
                record = "sha256:" + identity[12](
                    "odysseus.context.mcp_metadata.planning.record.v1",
                    metadata_binding,
                    {
                        "native": native_record,
                        "boundary": boundary,
                        "source": version.source_version_id,
                    },
                )
                check_stamp()
                descriptor = identity[47](
                    source=source,
                    source_version=version,
                    project_id=source_refs[6],
                    roadmap_id=roadmap_id,
                    approved_revision=approved_revision,
                    durable_revision_id=revision_id,
                    durable_content_sha256=content_hash,
                    durable_payload_sha256=payload_hash,
                    canonical_projection_sha256=projection_hash,
                    provenance_ref=version.source_version_id,
                    boundary_sha256=boundary,
                    record_sha256=record,
                )
                check_stamp()
                descriptors.append(descriptor)
            descriptors_tuple = tuple(descriptors)
            check_stamp()
            canonical_descriptors = []
            for item in descriptors_tuple:
                check_stamp()
                canonical_descriptors.append(identity[26](item))
                check_stamp()
            metadata = "sha256:" + identity[12](
                "odysseus.context.mcp_metadata.planning.target.v1",
                metadata_binding,
                {
                    "boundary": boundary,
                    "descriptors": canonical_descriptors,
                },
            )
            check_stamp()
            admission_check()
            check_stamp()
            snapshot = identity[50](
                descriptors_tuple, self._policy, boundary, metadata,
            )
            check_stamp()
            capture = identity[53](snapshot, payloads_tuple)
            check_stamp()
            return capture

        cookie_capture = cookie_render()
        check_stamp()
        mcp_capture = mcp_render()
        check_stamp()
        try:
            yield cookie_capture, mcp_capture
            check_stamp()
            yield None
            check_stamp()
            yield None
        finally:
            check_stamp()

    @contextmanager
    def _principal_mcp(self, admission: object, metadata_binding: object):
        source_type = type(self)
        if source_type is not DurablePlanningContextSource:
            _fail("invalid_context")
        anchor_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_ANCHOR_SLOT")
        identity_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_SLOT")
        anchor_descriptor = source_type.__dict__.get("_dual_identity_anchor")
        identity_descriptor = source_type.__dict__.get("_dual_identity")
        if (
            type(anchor_slot) is not MemberDescriptorType
            or anchor_slot.__objclass__ is not source_type
            or type(anchor_slot.__name__) is not str
            or anchor_slot.__name__ != "_dual_identity_anchor"
            or type(identity_slot) is not MemberDescriptorType
            or identity_slot.__objclass__ is not source_type
            or type(identity_slot.__name__) is not str
            or identity_slot.__name__ != "_dual_identity"
            or anchor_descriptor is not anchor_slot
            or identity_descriptor is not identity_slot
        ):
            _fail("invalid_context")
        native_identity = anchor_slot.__get__(self, source_type)
        if (
            type(native_identity) is not tuple
            or len(native_identity) != 65
            or identity_slot.__get__(self, source_type) is not native_identity
            or source_type.__dict__.get("_assert_runtime_pins") is not native_identity[8]
        ):
            _fail("invalid_context")
        runtime_checker = native_identity[8]
        runtime_checker(self)
        foundation = _require_mcp_metadata_admission(admission, metadata_binding)
        runtime_checker(self)
        manager = self._auth_manager
        try:
            runtime_checker(self)
            with manager.guard_current_admin_subject(foundation.owner_subject_id) as alias:
                runtime_checker(self)
                identity = manager.active_admin_subject_identity(subject_id=foundation.owner_subject_id)
                revision = manager.security_action_revision()
                runtime_checker(self)
                if identity != (alias, foundation.owner_subject_id) or revision != foundation.security_action_revision: _fail("invalid_binding")
                principal = self._issuer.issue(subject_id=foundation.owner_subject_id, owner_id=alias,
                    auth_context_digest="sha256:" + _mcp_recipient_digest("odysseus.context.mcp_metadata.planning.principal.v1", metadata_binding, {"project_id": self._project_id, "policy": self._policy.canonical()}), ttl_seconds=60)
                runtime_checker(self)
                try:
                    yield principal, alias, foundation
                finally:
                    runtime_checker(self)
                    fresh = _require_mcp_metadata_admission(admission, metadata_binding)
                    final_identity = manager.active_admin_subject_identity(subject_id=fresh.owner_subject_id)
                    final_revision = manager.security_action_revision()
                    runtime_checker(self)
                    if fresh is not foundation or final_identity != (alias, fresh.owner_subject_id) or final_revision != fresh.security_action_revision: _fail("invalid_binding")
        except McpContextAuthorityError: raise
        except McpContextMetadataAdmissionError: raise
        except DurablePlanningContextError: raise
        except ValueError: raise
        except Exception: _fail("invalid_binding")
        finally:
            runtime_checker(self)

    def _stable_capture_mcp(self, admission: object, metadata_binding: object, principal: TrustedPrincipal, alias: str, foundation: object) -> _Capture:
        with self._store._planning_payload_validation_pair():
            first = self._capture_mcp(admission, metadata_binding, principal, alias, foundation)
            final = self._capture_mcp(admission, metadata_binding, principal, alias, foundation)
        if final.snapshot != first.snapshot: _fail("planning_stale")
        return final

    def _capture_mcp(self, admission: object, metadata_binding: object, principal: TrustedPrincipal, alias: str, foundation: object) -> _Capture:
        """Native durable-store capture with recipient-bound boundaries only."""
        self._assert_runtime_pins(); current = _require_mcp_metadata_admission(admission, metadata_binding)
        if current is not foundation: _fail("invalid_binding")
        try:
            bundle = self._store.read_subject_bound_accepted_migration_bundle(principal, project_id=self._project_id)
            return self._capture_mcp_from_bundle(
                admission, metadata_binding, bundle, alias, foundation,
            )
        except McpContextAuthorityError: raise
        except McpContextMetadataAdmissionError: raise
        except DurablePlanningContextError: raise
        except ValueError: raise
        except Exception: _fail("planning_unavailable")
        finally:
            self._assert_runtime_pins()

    def _capture_mcp_from_bundle(
        self,
        admission: object,
        metadata_binding: object,
        bundle: PlanningAcceptedMigrationBundle,
        alias: str,
        foundation: object,
    ) -> _Capture:
        current = _require_mcp_metadata_admission(admission, metadata_binding)
        if current is not foundation:
            _fail("invalid_binding")
        try:
            if type(bundle) is not PlanningAcceptedMigrationBundle or bundle.owner_id != alias or bundle.project_id != self._project_id: _fail("planning_stale")
            _sha(bundle.state_digest)
            batch = json.loads(bundle.batch_bytes.decode("utf-8"))
            if type(batch) is not dict or _canonical(batch) != bundle.batch_bytes or batch.get("state") != "accepted" or batch.get("owner_id") != alias or batch.get("project_id") != self._project_id: _fail("planning_stale")
            if type(batch.get("source_items")) is not list or not batch["source_items"] or any(type(item) is not dict for item in batch["source_items"]): _fail("planning_stale")
            head_map = self._heads(alias, bundle.heads); record_map = self._records(alias, bundle.revisions, head_map)
            source_roadmaps = tuple(item.get("roadmap_id") for item in batch["source_items"])
            if (any(type(roadmap_id) is not str or not roadmap_id for roadmap_id in source_roadmaps)
                    or len(set(source_roadmaps)) != len(source_roadmaps)
                    or set(source_roadmaps) != set(head_map)):
                _fail("planning_stale")
            heads = [head_map[roadmap_id] for roadmap_id in source_roadmaps]
            native_boundary = _object_digest({"backend": {"issuer_id": self._issuer.issuer_id, "key_id": self._issuer.key_id, "runtime": type(self._runtime).__qualname__, "store": type(self._store).__qualname__}, "batch": batch, "heads": heads, "records": record_map["boundary_records"], "policy": self._policy.canonical(), "project_id": self._project_id})
            boundary = "sha256:" + _mcp_recipient_digest("odysseus.context.mcp_metadata.planning.boundary.v1", metadata_binding, {"native": native_boundary, "project_id": self._project_id})
            descriptors=[]
            for roadmap_id in sorted(head_map):
                head=head_map[roadmap_id]; current_record=record_map["revisions"].get(head["current_revision_id"])
                if current_record is None: _fail("planning_stale")
                payload=record_map["payloads"][current_record.revision_id]; document=record_map["documents"][current_record.revision_id]
                if self._policy.reject_sensitive_content and planning_policy._contains_sensitive_value(payload.decode("utf-8")): _fail("policy_denied")
                projection={"schema_id": _PROJECTION_SCHEMA,"project_id":self._project_id,"roadmap":document.get("roadmaps",[{}])[0]}; projection_hash=_object_digest(projection)
                owner_scope=OwnerScope.for_subject_id(foundation.owner_subject_id); source=SourceRecord(owner_scope=owner_scope.value,source_kind=SourceKind.PLANNING,canonical_ref=f"{_NAMESPACE}:{self._project_id}:{roadmap_id}",classification=Classification.PRIVATE,content_policy=ContentPolicy.INLINE_LOCAL,provider_ref=_PROVIDER_REF)
                version=SourceVersionRecord.create(source,revision_ref=f"durable:{current_record.revision_id}:{current_record.payload_sha256}",content_hash=current_record.payload_sha256,version_observed_at=current_record.created_at,provider_ref=_PROVIDER_REF,classification=Classification.PRIVATE,content_policy=ContentPolicy.INLINE_LOCAL)
                native_record=_object_digest({"boundary_sha256":native_boundary,"canonical_projection_sha256":projection_hash,"content_sha256":current_record.content_sha256,"durable_revision_id":current_record.revision_id,"payload_sha256":current_record.payload_sha256,"provenance_ref":version.source_version_id,"project_id":self._project_id,"roadmap_id":roadmap_id})
                record="sha256:" + _mcp_recipient_digest("odysseus.context.mcp_metadata.planning.record.v1",metadata_binding,{"native":native_record,"boundary":boundary,"source":version.source_version_id})
                descriptors.append(DurablePlanningSourceDescriptor(source=source,source_version=version,project_id=self._project_id,roadmap_id=roadmap_id,approved_revision=head["approved_revision"],durable_revision_id=current_record.revision_id,durable_content_sha256=current_record.content_sha256,durable_payload_sha256=current_record.payload_sha256,canonical_projection_sha256=projection_hash,provenance_ref=version.source_version_id,boundary_sha256=boundary,record_sha256=record))
            snapshot=DurablePlanningSourceSnapshot(tuple(descriptors),self._policy,boundary,"sha256:" + _mcp_recipient_digest("odysseus.context.mcp_metadata.planning.target.v1",metadata_binding,{"boundary":boundary,"descriptors":[x.canonical() for x in descriptors]}))
            _require_mcp_metadata_admission(admission,metadata_binding)
            return _Capture(snapshot,tuple(sorted(((item.revision.revision_id,bytes(item.payload)) for item in bundle.revisions),key=lambda item:item[0])))
        except McpContextAuthorityError: raise
        except McpContextMetadataAdmissionError: raise
        except DurablePlanningContextError: raise
        except ValueError: raise
        except Exception: _fail("planning_unavailable")

    def _stable_capture(
        self, binding: DisclosureBinding, principal: TrustedPrincipal, alias: str,
    ) -> _Capture:
        with self._store._planning_payload_validation_pair():
            first = self._capture(binding, principal, alias)
            final = self._capture(binding, principal, alias)
        if final.snapshot != first.snapshot:
            _fail("planning_stale")
        return final

    def read_document(
        self, binding: DisclosureBinding, target: SourceSelection,
    ) -> bytes:
        self._assert_runtime_pins()
        if type(target) is not SourceSelection or target.source != "planning":
            _fail("invalid_context")
        self._validate_binding(binding)
        try:
            with self._principal(binding) as (principal, alias):
                first = self._capture(binding, principal, alias)
                descriptor = next(
                    (
                        item for item in first.snapshot.descriptors
                        if item.source.source_id == target.selection_ref
                        and item.source_version.source_version_id == target.revision_ref
                    ),
                    None,
                )
                if descriptor is None:
                    _fail("source_not_found")
                payload = next(
                    (
                        value for revision_id, value in first.payloads
                        if revision_id == descriptor.durable_revision_id
                    ),
                    None,
                )
                if type(payload) is not bytes:
                    _fail("payload_unavailable")
                self._verify_payload(payload, descriptor)
                final = self._capture(binding, principal, alias)
                if final.snapshot != first.snapshot:
                    _fail("planning_stale")
                return bytes(payload)
        except DurablePlanningContextError:
            raise
        except Exception:
            _fail("payload_unavailable")
        finally:
            self._assert_runtime_pins()

    @contextmanager
    def _principal(self, binding: DisclosureBinding) -> Iterator[tuple[TrustedPrincipal, str]]:
        source_type = type(self)
        if source_type is not DurablePlanningContextSource:
            _fail("invalid_context")
        anchor_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_ANCHOR_SLOT")
        identity_slot = globals().get("_DURABLE_PLANNING_DUAL_IDENTITY_SLOT")
        anchor_descriptor = source_type.__dict__.get("_dual_identity_anchor")
        identity_descriptor = source_type.__dict__.get("_dual_identity")
        if (
            type(anchor_slot) is not MemberDescriptorType
            or anchor_slot.__objclass__ is not source_type
            or type(anchor_slot.__name__) is not str
            or anchor_slot.__name__ != "_dual_identity_anchor"
            or type(identity_slot) is not MemberDescriptorType
            or identity_slot.__objclass__ is not source_type
            or type(identity_slot.__name__) is not str
            or identity_slot.__name__ != "_dual_identity"
            or anchor_descriptor is not anchor_slot
            or identity_descriptor is not identity_slot
        ):
            _fail("invalid_context")
        native_identity = anchor_slot.__get__(self, source_type)
        if (
            type(native_identity) is not tuple
            or len(native_identity) != 65
            or identity_slot.__get__(self, source_type) is not native_identity
            or source_type.__dict__.get("_assert_runtime_pins") is not native_identity[8]
        ):
            _fail("invalid_context")
        runtime_checker = native_identity[8]
        runtime_checker(self)
        manager = self._auth_manager
        guard = getattr(manager, "guard_current_admin_subject", None)
        identity_fn = getattr(manager, "active_admin_subject_identity", None)
        revision_fn = getattr(manager, "security_action_revision", None)
        if not callable(guard) or not callable(identity_fn) or not callable(revision_fn):
            _fail("invalid_binding")
        try:
            runtime_checker(self)
            with guard(binding.owner_sid) as alias:
                runtime_checker(self)
                identity = identity_fn(subject_id=binding.owner_sid)
                revision = revision_fn()
                runtime_checker(self)
                if (
                    type(alias) is not str
                    or type(identity) is not tuple
                    or len(identity) != 2
                    or type(identity[0]) is not str
                    or type(identity[1]) is not str
                    or type(revision) is not int
                    or identity != (alias, binding.owner_sid)
                    or revision != binding.security_revision
                ):
                    _fail("invalid_binding")
                context = _object_digest({
                    "binding": binding._canonical(),
                    "policy": self._policy.canonical(),
                    "project_id": self._project_id,
                })
                try:
                    principal = self._issuer.issue(
                        subject_id=binding.owner_sid, owner_id=alias,
                        auth_context_digest=context, ttl_seconds=60,
                    )
                except Exception:
                    _fail("planning_unavailable")
                runtime_checker(self)
                try:
                    yield principal, alias
                finally:
                    runtime_checker(self)
                    final_identity = identity_fn(subject_id=binding.owner_sid)
                    final_revision = revision_fn()
                    runtime_checker(self)
                    if (
                        type(final_identity) is not tuple
                        or len(final_identity) != 2
                        or type(final_identity[0]) is not str
                        or type(final_identity[1]) is not str
                        or type(final_revision) is not int
                        or final_identity != (alias, binding.owner_sid)
                        or final_revision != binding.security_revision
                    ):
                        _fail("invalid_binding")
        except DurablePlanningContextError:
            raise
        except Exception:
            _fail("invalid_binding")
        finally:
            runtime_checker(self)

    def _capture(
        self, binding: DisclosureBinding, principal: TrustedPrincipal, alias: str,
    ) -> _Capture:
        self._assert_runtime_pins()
        store = self._store
        try:
            bundle = store.read_subject_bound_accepted_migration_bundle(
                principal, project_id=self._project_id,
            )
        except Exception:
            _fail("planning_unavailable")
        return self._capture_from_bundle(binding, bundle, alias)

    def _capture_from_bundle(
        self,
        binding: DisclosureBinding,
        bundle: PlanningAcceptedMigrationBundle,
        alias: str,
    ) -> _Capture:
        if (
            type(bundle) is not PlanningAcceptedMigrationBundle
            or bundle.owner_id != alias
            or bundle.project_id != self._project_id
        ):
            _fail("planning_stale")
        _sha(bundle.state_digest)
        try:
            batch = json.loads(bundle.batch_bytes.decode("utf-8"))
        except Exception:
            _fail("planning_stale")
        if type(batch) is not dict or _canonical(batch) != bundle.batch_bytes:
            _fail("planning_stale")
        if batch.get("state") != "accepted" or batch.get("owner_id") != alias or batch.get("project_id") != self._project_id:
            _fail("planning_stale")
        if type(batch.get("source_items")) is not list or not batch["source_items"]:
            _fail("planning_stale")
        head_map = self._heads(alias, bundle.heads)
        record_map = self._records(alias, bundle.revisions, head_map)
        try:
            boundary_heads = [head_map[item["roadmap_id"]] for item in batch["source_items"]]
        except Exception:
            _fail("planning_stale")
        if len(boundary_heads) != len(head_map):
            _fail("planning_stale")
        descriptors: list[DurablePlanningSourceDescriptor] = []
        boundary_material = {
            "backend": {
                "issuer_id": self._issuer.issuer_id,
                "key_id": self._issuer.key_id,
                "runtime": type(self._runtime).__qualname__,
                "store": type(self._store).__qualname__,
            },
            "binding": binding._canonical(),
            "batch": batch,
            "heads": boundary_heads,
            "records": record_map["boundary_records"],
            "policy": self._policy.canonical(),
            "project_id": self._project_id,
        }
        boundary = _object_digest(boundary_material)
        for roadmap_id in sorted(head_map):
            head = head_map[roadmap_id]
            current_id = head["current_revision_id"]
            current = record_map["revisions"].get(current_id)
            if current is None:
                _fail("planning_stale")
            document = record_map["documents"][current_id]
            payload = record_map["payloads"][current_id]
            if self._policy.reject_sensitive_content:
                try:
                    if planning_policy._contains_sensitive_value(payload.decode("utf-8")):
                        _fail("policy_denied")
                except UnicodeDecodeError:
                    _fail("planning_stale")
            roadmap = document.get("roadmaps", [{}])[0]
            projection = {
                "schema_id": _PROJECTION_SCHEMA,
                "project_id": self._project_id,
                "roadmap": roadmap,
            }
            projection_hash = _object_digest(projection)
            owner_scope = OwnerScope.for_subject_id(binding.owner_sid)
            source = SourceRecord(
                owner_scope=owner_scope.value,
                source_kind=SourceKind.PLANNING,
                canonical_ref=f"{_NAMESPACE}:{self._project_id}:{roadmap_id}",
                classification=Classification.PRIVATE,
                content_policy=ContentPolicy.INLINE_LOCAL,
                provider_ref=_PROVIDER_REF,
            )
            version = SourceVersionRecord.create(
                source,
                revision_ref=f"durable:{current.revision_id}:{current.payload_sha256}",
                content_hash=current.payload_sha256,
                version_observed_at=current.created_at,
                provider_ref=_PROVIDER_REF,
                classification=Classification.PRIVATE,
                content_policy=ContentPolicy.INLINE_LOCAL,
            )
            record_sha = _object_digest({
                "boundary_sha256": boundary,
                "canonical_projection_sha256": projection_hash,
                "content_sha256": current.content_sha256,
                "durable_revision_id": current.revision_id,
                "payload_sha256": current.payload_sha256,
                "provenance_ref": version.source_version_id,
                "project_id": self._project_id,
                "roadmap_id": roadmap_id,
            })
            descriptors.append(DurablePlanningSourceDescriptor(
                source=source,
                source_version=version,
                project_id=self._project_id,
                roadmap_id=roadmap_id,
                approved_revision=head["approved_revision"],
                durable_revision_id=current.revision_id,
                durable_content_sha256=current.content_sha256,
                durable_payload_sha256=current.payload_sha256,
                canonical_projection_sha256=projection_hash,
                provenance_ref=version.source_version_id,
                boundary_sha256=boundary,
                record_sha256=record_sha,
            ))
        descriptors_tuple = tuple(descriptors)
        metadata = _object_digest({
            "boundary_sha256": boundary,
            "descriptors": [item.canonical() for item in descriptors_tuple],
            "policy": self._policy.canonical(),
        })
        snapshot = DurablePlanningSourceSnapshot(
            descriptors_tuple, self._policy, boundary, metadata,
        )
        payloads = tuple(
            sorted(
                (
                    (item.revision.revision_id, bytes(item.payload))
                    for item in bundle.revisions
                ),
                key=lambda item: item[0],
            )
        )
        return _Capture(snapshot, payloads)

    @staticmethod
    def _heads(
        alias: str, heads: tuple[PlanningAcceptedHeadBinding, ...],
    ) -> dict[str, dict[str, object]]:
        if not heads or any(type(item) is not PlanningAcceptedHeadBinding for item in heads):
            _fail("planning_stale")
        result: dict[str, dict[str, object]] = {}
        for item in heads:
            head = {
                "owner_id": item.owner_id,
                "project_id": item.project_id,
                "roadmap_id": item.roadmap_id,
                "accepted_revision_id": item.accepted_revision_id,
                "accepted_revision": item.accepted_revision,
                "accepted_content_sha256": item.accepted_content_sha256,
                "current_revision_id": item.current_revision_id,
                "current_revision": item.current_revision,
                "current_content_sha256": item.current_content_sha256,
                "approved_revision_id": item.approved_revision_id,
                "approved_revision": item.approved_revision,
                "approved_content_sha256": item.approved_content_sha256,
                "generation": item.generation,
            }
            if (
                set(head) != _HEAD_KEYS
                or head["owner_id"] != alias
                or type(head["project_id"]) is not str
                or type(head["roadmap_id"]) is not str
                or head["approved_revision_id"] != head["current_revision_id"]
                or head["approved_revision"] != head["current_revision"]
                or head["approved_content_sha256"] != head["current_content_sha256"]
                or not all(type(head[key]) is int and not isinstance(head[key], bool) and head[key] >= 1 for key in ("accepted_revision", "current_revision", "approved_revision", "generation"))
                or head["accepted_revision"] > head["approved_revision"]
                or any(_SHA256_RE.fullmatch(head[key]) is None for key in ("accepted_content_sha256", "current_content_sha256", "approved_content_sha256"))
                or any(type(head[key]) is not str or not head[key] for key in ("accepted_revision_id", "current_revision_id", "approved_revision_id"))
                or head["roadmap_id"] in result
            ):
                _fail("planning_stale")
            result[head["roadmap_id"]] = head
        return result

    def _records(
        self,
        alias: str, records: tuple[PlanningContextRevisionMember, ...],
        heads: dict[str, dict[str, object]],
        *, _checked_operation: object | None = None,
    ) -> dict[str, object]:
        if not records:
            _fail("planning_stale")
        revisions: dict[str, object] = {}
        documents: dict[str, dict[str, object]] = {}
        payloads: dict[str, bytes] = {}
        boundary_records: list[dict[str, object]] = []
        for member in records:
            if type(member) is not PlanningContextRevisionMember:
                _fail("planning_stale")
            revision = member.revision
            payload = member.payload
            revision_id = revision.revision_id
            if revision_id in revisions or not revision_id:
                _fail("planning_stale")
            try:
                encoded = payload.decode("utf-8")
                document = (
                    json.loads(encoded) if _checked_operation is None
                    else _checked_operation("loads", encoded)
                )
            except Exception:
                _fail("planning_stale")
            if type(document) is not dict:
                _fail("planning_stale")
            canonical = (
                _canonical(document) if _checked_operation is None
                else _checked_operation("canonical", document)
            )
            if payload != canonical:
                _fail("planning_stale")
            payload_digest = (
                _digest(payload) if _checked_operation is None
                else _checked_operation("digest", payload)
            )
            if payload_digest != revision.payload_sha256:
                _fail("planning_stale")
            roadmaps = document.get("roadmaps")
            project = document.get("project")
            if type(project) is not dict or type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict:
                _fail("planning_stale")
            roadmap = roadmaps[0]
            if (
                project.get("project_id") != revision.project_id
                or roadmap.get("roadmap_id") != revision.roadmap_id
                or roadmap.get("revision") != revision.revision
                or roadmap.get("revision_state") != revision.revision_state
                or roadmap.get("content_hash") != revision.content_sha256
                or (
                    compute_roadmap_content_hash(roadmap)
                    if _checked_operation is None
                    else _checked_operation("content_hash", roadmap)
                ) != revision.content_sha256
                or revision.owner_id != alias
                or revision.project_id != self._project_id
            ):
                _fail("planning_stale")
            revisions[revision_id] = revision
            documents[revision_id] = document
            payloads[revision_id] = payload
            boundary_records.append({
                "document_sha256": (
                    _digest(canonical) if _checked_operation is None
                    else _checked_operation("digest", canonical)
                ),
                "parent_revision_id": revision.parent_revision_id,
                "payload_sha256": revision.payload_sha256,
                "revision_id": revision.revision_id,
                "revision": revision.revision,
                "revision_state": revision.revision_state,
                "roadmap_id": revision.roadmap_id,
            })
        for roadmap_id, head in heads.items():
            current_id = head["current_revision_id"]
            accepted_id = head["accepted_revision_id"]
            current = revisions.get(current_id)
            accepted = revisions.get(accepted_id)
            if current is None or accepted is None or current.roadmap_id != roadmap_id or accepted.parent_revision_id is not None:
                _fail("planning_stale")
            if (
                current.revision_id != head["current_revision_id"]
                or current.revision != head["current_revision"]
                or current.content_sha256 != head["current_content_sha256"]
                or current.revision_id != head["approved_revision_id"]
                or current.revision != head["approved_revision"]
                or current.content_sha256 != head["approved_content_sha256"]
                or current.revision_state != "approved"
                or accepted.revision_id != head["accepted_revision_id"]
                or accepted.revision != head["accepted_revision"]
                or accepted.content_sha256 != head["accepted_content_sha256"]
            ):
                _fail("planning_stale")
            cursor = current
            seen: set[str] = set()
            while True:
                if cursor.revision_id in seen:
                    _fail("planning_stale")
                seen.add(cursor.revision_id)
                if cursor.revision_id == accepted_id:
                    break
                if cursor.parent_revision_id is None:
                    _fail("planning_stale")
                cursor = revisions.get(cursor.parent_revision_id)
                if cursor is None or cursor.roadmap_id != roadmap_id:
                    _fail("planning_stale")
            if len(seen) != sum(1 for item in revisions.values() if item.roadmap_id == roadmap_id):
                _fail("planning_stale")
        if set(item.roadmap_id for item in revisions.values()) != set(heads):
            _fail("planning_stale")
        return {
            "revisions": revisions,
            "documents": documents,
            "payloads": payloads,
            "boundary_records": tuple(sorted(boundary_records, key=lambda item: item["revision_id"])),
        }

    @staticmethod
    def _verify_payload(
        payload: object,
        descriptor: DurablePlanningSourceDescriptor,
        *,
        _operations: tuple[object, object, object, object] | None = None,
        _guard: object = None,
    ) -> None:
        if _operations is None:
            digest, loads, canonical, content_hash = (
                _digest, json.loads, _canonical, compute_roadmap_content_hash,
            )
        elif (
            type(_operations) is not tuple
            or len(_operations) != 4
            or not all(callable(operation) for operation in _operations)
            or not callable(_guard)
        ):
            _fail("invalid_context")
        else:
            digest, loads, canonical, content_hash = _operations
        if _guard is not None:
            _guard()
        if type(payload) is not bytes or digest(payload) != descriptor.durable_payload_sha256:
            _fail("payload_unavailable")
        if _guard is not None:
            _guard()
        try:
            document = loads(payload.decode("utf-8"))
        except Exception:
            _fail("payload_unavailable")
        if _guard is not None:
            _guard()
        if canonical(document) != payload:
            _fail("planning_stale")
        if _guard is not None:
            _guard()
        roadmaps = document.get("roadmaps") if type(document) is dict else None
        project = document.get("project") if type(document) is dict else None
        if type(project) is not dict or project.get("project_id") != descriptor.project_id or type(roadmaps) is not list or len(roadmaps) != 1:
            _fail("planning_stale")
        roadmap = roadmaps[0]
        if (
            type(roadmap) is not dict
            or roadmap.get("roadmap_id") != descriptor.roadmap_id
            or roadmap.get("revision") != descriptor.approved_revision
            or roadmap.get("revision_state") != "approved"
            or roadmap.get("content_hash") != descriptor.durable_content_sha256
            or content_hash(roadmap) != descriptor.durable_content_sha256
        ):
            _fail("planning_stale")
        if _guard is not None:
            _guard()

    @staticmethod
    def _validate_binding(binding: object) -> None:
        if type(binding) is not DisclosureBinding:
            _fail("invalid_binding")


_DURABLE_PLANNING_DUAL_IDENTITY_SLOT = (
    DurablePlanningContextSource.__dict__["_dual_identity"]
)
_DURABLE_PLANNING_DUAL_IDENTITY_ANCHOR_SLOT = (
    DurablePlanningContextSource.__dict__["_dual_identity_anchor"]
)


DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY = DurablePlanningContextPolicy(
    _NAMESPACE, _POLICY_REF, "private", "inline_local",
    "durable_revision_document", True,
)


__all__ = [
    "DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY",
    "DurablePlanningContextError",
    "DurablePlanningContextPolicy",
    "DurablePlanningContextSource",
    "DurablePlanningSourceDescriptor",
    "DurablePlanningSourceSnapshot",
]
