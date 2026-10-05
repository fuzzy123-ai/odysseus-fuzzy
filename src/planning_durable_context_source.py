"""Native, authenticated Planning source backed by the durable store."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Iterator

from core.auth import AuthManager
from src.harbour_authority import AuthenticatedPrincipalIssuer, TrustedPrincipal
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    PlanningAcceptedHeadBinding,
    PlanningAcceptedMigrationBundle,
    PlanningContextRevisionMember,
)
from src.private_context_confirmation import DisclosureBinding
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

    def _assert_runtime_pins(self) -> None:
        if (
            type(self._runtime) is not AppOwnedPlanningRuntime
            or self._runtime.durable_store is not self._store
            or self._runtime.issuer is not self._issuer
            or type(self._store) is not DurablePlanningRevisionStore
            or type(self._issuer) is not AuthenticatedPrincipalIssuer
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

    def _stable_capture(
        self, binding: DisclosureBinding, principal: TrustedPrincipal, alias: str,
    ) -> _Capture:
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
        self._assert_runtime_pins()
        manager = self._auth_manager
        guard = getattr(manager, "guard_current_admin_subject", None)
        identity_fn = getattr(manager, "active_admin_subject_identity", None)
        revision_fn = getattr(manager, "security_action_revision", None)
        if not callable(guard) or not callable(identity_fn) or not callable(revision_fn):
            _fail("invalid_binding")
        try:
            with guard(binding.owner_sid) as alias:
                identity = identity_fn(subject_id=binding.owner_sid)
                revision = revision_fn()
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
                try:
                    yield principal, alias
                finally:
                    final_identity = identity_fn(subject_id=binding.owner_sid)
                    final_revision = revision_fn()
                    self._assert_runtime_pins()
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
                "store": type(store).__qualname__,
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
                document = json.loads(payload.decode("utf-8"))
            except Exception:
                _fail("planning_stale")
            if type(document) is not dict:
                _fail("planning_stale")
            canonical = _canonical(document)
            if payload != canonical or _digest(payload) != revision.payload_sha256:
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
                or compute_roadmap_content_hash(roadmap) != revision.content_sha256
                or revision.owner_id != alias
                or revision.project_id != self._project_id
            ):
                _fail("planning_stale")
            revisions[revision_id] = revision
            documents[revision_id] = document
            payloads[revision_id] = payload
            boundary_records.append({
                "document_sha256": _digest(canonical),
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
    def _verify_payload(payload: object, descriptor: DurablePlanningSourceDescriptor) -> None:
        if type(payload) is not bytes or _digest(payload) != descriptor.durable_payload_sha256:
            _fail("payload_unavailable")
        try:
            document = json.loads(payload.decode("utf-8"))
        except Exception:
            _fail("payload_unavailable")
        if _canonical(document) != payload:
            _fail("planning_stale")
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
            or compute_roadmap_content_hash(roadmap) != descriptor.durable_content_sha256
        ):
            _fail("planning_stale")

    @staticmethod
    def _validate_binding(binding: object) -> None:
        if type(binding) is not DisclosureBinding:
            _fail("invalid_binding")


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
