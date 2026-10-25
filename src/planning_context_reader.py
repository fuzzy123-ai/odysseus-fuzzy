"""Approved, subject-bound Planning context metadata and page reader.

The reader composes the app-owned durable Planning runtime with the existing
approved Planning source adapter.  It does not consult the legacy Planning
store as authority and does not expose raw repository/provider failures.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterator

from src.harbour_authority import TrustedPrincipal
from src.mcp_authenticated_dispatch import AppOwnedPlanningRuntime
from src.planning_revision_store import DurablePlanningRevisionStore
from src.planning_mcp_service import PlanningMcpService
from src.planning_durable_context_source import (
    DurablePlanningContextError, DurablePlanningContextSource,
    DurablePlanningSourceDescriptor,
)
from src.private_context_confirmation import (
    ContextManifest,
    DisclosureBinding,
    ManifestItem,
    VerifiedDisclosure,
    ConfirmationError,
)
from src.context_metadata_binding import McpContextMetadataAdmissionError, _mcp_recipient_digest, _require_mcp_metadata_admission
from src.mcp_context_authority import McpContextAuthorityError
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
    ChatConfirmationRepository,
    PrivateContextConfirmationApp,
    SourceSelection,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.planning import (
    PlanningSourceAdapter,
    PlanningSourceAdapterError,
)


_MAX_JSON_INT = (1 << 53) - 1
_MAX_PAGE_BYTES = 1_048_576
_MAX_SELECTIONS = 300
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$\Z")
_RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$\Z")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@~-]{0,255}\Z")

_ERROR_CODES = frozenset(
    {
        "invalid_context",
        "invalid_binding",
        "planning_unavailable",
        "planning_stale",
        "planning_policy_failed",
        "planning_payload_failed",
        "invalid_page",
        "disclosure_unverified",
    }
)

_HEAD_KEYS = frozenset(
    {
        "owner_id",
        "project_id",
        "roadmap_id",
        "accepted_revision_id",
        "accepted_revision",
        "accepted_content_sha256",
        "current_revision_id",
        "current_revision",
        "current_content_sha256",
        "approved_revision_id",
        "approved_revision",
        "approved_content_sha256",
        "generation",
    }
)


class PlanningContextReaderError(ValueError):
    """Fixed-code error boundary for the Planning context reader."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "planning_unavailable"
        self.code = safe
        super().__init__(safe)

    def __repr__(self) -> str:
        return f"PlanningContextReaderError(code={self.code!r})"


def _fail(code: str) -> None:
    raise PlanningContextReaderError(code) from None


def _sha(value: object, code: str = "invalid_context") -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _manifest_digest(value: object, code: str = "invalid_page") -> str:
    if type(value) is not str:
        _fail(code)
    raw = value[7:] if value.startswith("sha256:") else value
    if _RAW_SHA256_RE.fullmatch(raw) is None:
        _fail(code)
    return raw


def _opaque(value: object, *, code: str = "invalid_context") -> str:
    if type(value) is not str or _OPAQUE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        _fail("planning_unavailable")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_digest(value: bytes) -> str:
    if type(value) is not bytes:
        _fail("planning_payload_failed")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _closed_string(value: object, *, code: str = "planning_unavailable") -> str:
    if type(value) is not str or not value or len(value) > 4096 or not value.isascii():
        _fail(code)
    return value


def _fixed_exception(code: str) -> PlanningContextReaderError:
    return PlanningContextReaderError(code)


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMetadata:
    """Content-free, digest-pinned metadata for one approved Planning item."""

    selection: SourceSelection = field(repr=False)
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
        if type(self.selection) is not SourceSelection:
            _fail("invalid_context")
        if type(self.approved_revision) is not int or isinstance(self.approved_revision, bool) or self.approved_revision < 1:
            _fail("planning_unavailable")
        for value in (
            self.durable_revision_id,
            self.durable_content_sha256,
            self.durable_payload_sha256,
            self.canonical_projection_sha256,
            self.provenance_ref,
            self.boundary_sha256,
            self.record_sha256,
        ):
            _opaque(value, code="planning_unavailable")
        for value in (self.durable_content_sha256, self.durable_payload_sha256, self.canonical_projection_sha256,
                      self.boundary_sha256, self.record_sha256):
            _sha(value, "planning_unavailable")

    def canonical(self) -> dict[str, object]:
        return {
            "approved_revision": self.approved_revision,
            "boundary_sha256": self.boundary_sha256,
            "canonical_projection_sha256": self.canonical_projection_sha256,
            "durable_content_sha256": self.durable_content_sha256,
            "durable_payload_sha256": self.durable_payload_sha256,
            "durable_revision_id": self.durable_revision_id,
            "project_id": self.project_id,
            "provenance_ref": self.provenance_ref,
            "record_sha256": self.record_sha256,
            "roadmap_id": self.roadmap_id,
            "selection_ref": self.selection.selection_ref,
            "revision_ref": self.selection.revision_ref,
        }


@dataclass(frozen=True, slots=True, repr=False)
class PlanningContextPageRequest:
    """One complete original selection and one bounded Planning page request."""

    selection: ContextSelection = field(repr=False)
    target: SourceSelection = field(repr=False)
    chat_session_id: str = field(repr=False)
    expected_manifest_digest: str = field(repr=False)
    offset: int = field(repr=False)
    limit: int = field(repr=False)
    disclosure_message_id: int = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.selection) is not ContextSelection or type(self.target) is not SourceSelection:
            _fail("invalid_page")
        if self.target not in self.selection.items or self.target.source != "planning":
            _fail("invalid_page")
        _opaque(self.chat_session_id, code="invalid_page")
        object.__setattr__(self, "expected_manifest_digest", _manifest_digest(self.expected_manifest_digest))
        if type(self.offset) is not int or isinstance(self.offset, bool) or not 0 <= self.offset <= _MAX_JSON_INT:
            _fail("invalid_page")
        if type(self.limit) is not int or isinstance(self.limit, bool) or not 1 <= self.limit <= _MAX_PAGE_BYTES:
            _fail("invalid_page")
        if type(self.disclosure_message_id) is not int or isinstance(self.disclosure_message_id, bool) or not 1 <= self.disclosure_message_id <= _MAX_JSON_INT:
            _fail("invalid_page")


@dataclass(frozen=True, slots=True, repr=False)
class PlanningContextPage:
    """Bounded bytes plus honest whole/page digests and offsets."""

    page_bytes: bytes = field(repr=False)
    total_bytes: int
    offset: int
    end: int
    whole_sha256: str = field(repr=False)
    page_sha256: str = field(repr=False)
    manifest_digest: str = field(repr=False)
    byteform: str = "durable_revision_document"

    def __post_init__(self) -> None:
        if type(self.page_bytes) is not bytes or type(self.total_bytes) is not int or type(self.offset) is not int or type(self.end) is not int:
            _fail("invalid_page")
        if not 0 <= self.offset <= self.end <= self.total_bytes:
            _fail("invalid_page")
        if len(self.page_bytes) != self.end - self.offset or len(self.page_bytes) > _MAX_PAGE_BYTES:
            _fail("invalid_page")
        _sha(self.whole_sha256, "invalid_page")
        _sha(self.page_sha256, "invalid_page")
        object.__setattr__(self, "manifest_digest", _manifest_digest(self.manifest_digest))
        if self.byteform != "durable_revision_document":
            _fail("invalid_page")


@dataclass(frozen=True, slots=True)
class _DurableRecord:
    revision_id: str
    document: dict[str, object]
    project_id: str
    roadmap_id: str
    revision: int
    revision_state: str
    content_sha256: str
    payload_sha256: str


class PlanningContextManifestProducer:
    """Capture approved Planning metadata from the app-owned durable runtime."""

    __slots__ = (
        "_runtime", "_auth_manager", "_approved_source_service", "_project_id",
        "_durable_source", "_dual_resolve_pin",
    )

    def __init__(
        self,
        runtime: AppOwnedPlanningRuntime,
        auth_manager: object,
        approved_source_service: object,
        project_id: str,
        *,
        durable_source: DurablePlanningContextSource | None = None,
    ) -> None:
        if type(runtime) is not AppOwnedPlanningRuntime or auth_manager is None:
            _fail("invalid_context")
        if type(project_id) is not str or not project_id or len(project_id) > 128 or not project_id.isascii():
            _fail("invalid_context")
        if durable_source is None:
            if type(approved_source_service) is not PlanningMcpService:
                _fail("invalid_context")
        elif (
            type(durable_source) is not DurablePlanningContextSource
            or approved_source_service is not None
            or durable_source._runtime is not runtime
            or durable_source._auth_manager is not auth_manager
            or durable_source._project_id != project_id
        ):
            _fail("invalid_context")
        if not isinstance(runtime.durable_store, DurablePlanningRevisionStore) or runtime.issuer is None:
            _fail("planning_unavailable")
        self._runtime = runtime
        self._auth_manager = auth_manager
        self._approved_source_service = approved_source_service
        self._project_id = project_id
        self._durable_source = durable_source
        self._dual_resolve_pin = (
            type(durable_source).__dict__.get("resolve_dual")
            if durable_source is not None else None
        )

    def capture(self, binding: DisclosureBinding, selection: ContextSelection) -> ContextManifest:
        metadata = self._capture_metadata(binding, selection)
        items = [
            ManifestItem(
                "planning",
                item.selection.selection_ref,
                item.selection.revision_ref,
                item.boundary_sha256,
                item.record_sha256,
                item.durable_payload_sha256,
                "private",
                item.provenance_ref,
            )
            for item in metadata
        ]
        items.sort(key=lambda item: _canonical(item._canonical()))
        return ContextManifest(tuple(items))

    def capture_metadata(self, binding: DisclosureBinding, selection: ContextSelection) -> tuple[PlanningMetadata, ...]:
        return self._capture_metadata(binding, selection)

    def capture_metadata_mcp(self, admission: object, metadata_binding: object,
                             selection: ContextSelection) -> tuple[tuple[PlanningMetadata, ...], str]:
        """Capture only native durable Planning metadata under an MCP lease."""
        if type(selection) is not ContextSelection or self._durable_source is None or self._approved_source_service is not None:
            _fail("invalid_context")
        selected = tuple(item for item in selection.items if item.source == "planning")
        if not selected or len(selected) != len(selection.items): _fail("invalid_context")
        _require_mcp_metadata_admission(admission, metadata_binding)
        try:
            descriptors = self._durable_source.resolve_mcp(admission, metadata_binding, selection)
            if type(descriptors) is not tuple or len(descriptors) != len(selected): _fail("planning_stale")
            result=[]
            for item, descriptor in zip(selected, descriptors):
                if type(descriptor) is not DurablePlanningSourceDescriptor or descriptor.selection() != item: _fail("planning_stale")
                result.append(PlanningMetadata(item,descriptor.project_id,descriptor.roadmap_id,descriptor.approved_revision,descriptor.durable_revision_id,descriptor.durable_content_sha256,descriptor.durable_payload_sha256,descriptor.canonical_projection_sha256,descriptor.provenance_ref,descriptor.boundary_sha256,descriptor.record_sha256))
            target=_mcp_recipient_digest("odysseus.context.mcp_metadata.planning.target.v1",metadata_binding,{"items":[item.canonical() for item in result]})
            _require_mcp_metadata_admission(admission,metadata_binding)
            return tuple(result),target
        except McpContextAuthorityError: raise
        except McpContextMetadataAdmissionError: raise
        except DurablePlanningContextError as error: self._native_error(error)
        except PlanningContextReaderError: raise
        except ValueError: raise
        except Exception: _fail("planning_unavailable")

    def capture_metadata_dual(
        self,
        binding: DisclosureBinding,
        admission: object,
        metadata_binding: object,
        selection: ContextSelection,
    ) -> tuple[
        tuple[PlanningMetadata, ...],
        tuple[PlanningMetadata, ...],
        str,
    ]:
        """Build cookie and MCP metadata from one dual-authenticated image."""
        if (
            type(binding) is not DisclosureBinding
            or type(selection) is not ContextSelection
            or self._durable_source is None
            or self._approved_source_service is not None
        ):
            _fail("invalid_context")
        selected = tuple(item for item in selection.items if item.source == "planning")
        if not selected or len(selected) != len(selection.items):
            _fail("invalid_context")
        _require_mcp_metadata_admission(admission, metadata_binding)
        try:
            resolve_dual = self._dual_resolve_pin
            if (
                type(self._durable_source).__dict__.get("resolve_dual")
                is not resolve_dual
            ):
                _fail("invalid_context")
            cookie_descriptors, mcp_descriptors = resolve_dual(
                self._durable_source, binding, admission, metadata_binding, selection,
            )
            if (
                type(cookie_descriptors) is not tuple
                or type(mcp_descriptors) is not tuple
                or len(cookie_descriptors) != len(selected)
                or len(mcp_descriptors) != len(selected)
            ):
                _fail("planning_stale")

            def project(descriptors: tuple[DurablePlanningSourceDescriptor, ...]):
                result = []
                for item, descriptor in zip(selected, descriptors):
                    if (
                        type(descriptor) is not DurablePlanningSourceDescriptor
                        or descriptor.selection() != item
                    ):
                        _fail("planning_stale")
                    result.append(PlanningMetadata(
                        item, descriptor.project_id, descriptor.roadmap_id,
                        descriptor.approved_revision, descriptor.durable_revision_id,
                        descriptor.durable_content_sha256,
                        descriptor.durable_payload_sha256,
                        descriptor.canonical_projection_sha256,
                        descriptor.provenance_ref, descriptor.boundary_sha256,
                        descriptor.record_sha256,
                    ))
                return tuple(result)

            cookie_result = project(cookie_descriptors)
            mcp_result = project(mcp_descriptors)
            mcp_target = _mcp_recipient_digest(
                "odysseus.context.mcp_metadata.planning.target.v1",
                metadata_binding,
                {"items": [item.canonical() for item in mcp_result]},
            )
            _require_mcp_metadata_admission(admission, metadata_binding)
            return cookie_result, mcp_result, mcp_target
        except McpContextAuthorityError:
            raise
        except McpContextMetadataAdmissionError:
            raise
        except DurablePlanningContextError as error:
            self._native_error(error)
        except PlanningContextReaderError:
            raise
        except ValueError:
            raise
        except Exception:
            _fail("planning_unavailable")

    def capture_metadata_dual_content(
        self,
        binding: DisclosureBinding,
        admission: object,
        metadata_binding: object,
        selection: ContextSelection,
        target: SourceSelection,
    ) -> tuple[
        tuple[PlanningMetadata, ...],
        tuple[PlanningMetadata, ...],
        str,
        bytes,
        bytes,
    ]:
        """Carry one verified Planning target only for the active dual sample."""
        if (
            type(binding) is not DisclosureBinding
            or type(selection) is not ContextSelection
            or type(target) is not SourceSelection
            or target.source != "planning"
            or target not in selection.items
            or self._durable_source is None
            or self._approved_source_service is not None
        ):
            _fail("invalid_context")
        selected = tuple(item for item in selection.items if item.source == "planning")
        if not selected or len(selected) != len(selection.items):
            _fail("invalid_context")
        _require_mcp_metadata_admission(admission, metadata_binding)
        try:
            resolve_dual = self._dual_resolve_pin
            if type(self._durable_source).__dict__.get("resolve_dual") is not resolve_dual:
                _fail("invalid_context")
            resolved = resolve_dual(
                self._durable_source, binding, admission, metadata_binding,
                selection, _content_target=target,
            )
            if type(self._durable_source).__dict__.get("resolve_dual") is not resolve_dual:
                _fail("invalid_context")
            if type(resolved) is not tuple or len(resolved) != 4:
                _fail("planning_stale")
            cookie_descriptors, mcp_descriptors, cookie_payload, mcp_payload = resolved
            if (
                type(cookie_descriptors) is not tuple
                or type(mcp_descriptors) is not tuple
                or len(cookie_descriptors) != len(selected)
                or len(mcp_descriptors) != len(selected)
                or type(cookie_payload) is not bytes
                or type(mcp_payload) is not bytes
            ):
                _fail("planning_stale")

            def project(descriptors: tuple[DurablePlanningSourceDescriptor, ...]):
                result = []
                for item, descriptor in zip(selected, descriptors):
                    if (
                        type(descriptor) is not DurablePlanningSourceDescriptor
                        or descriptor.selection() != item
                    ):
                        _fail("planning_stale")
                    result.append(PlanningMetadata(
                        item, descriptor.project_id, descriptor.roadmap_id,
                        descriptor.approved_revision, descriptor.durable_revision_id,
                        descriptor.durable_content_sha256,
                        descriptor.durable_payload_sha256,
                        descriptor.canonical_projection_sha256,
                        descriptor.provenance_ref, descriptor.boundary_sha256,
                        descriptor.record_sha256,
                    ))
                return tuple(result)

            cookie_result = project(cookie_descriptors)
            mcp_result = project(mcp_descriptors)
            mcp_target = _mcp_recipient_digest(
                "odysseus.context.mcp_metadata.planning.target.v1",
                metadata_binding,
                {"items": [item.canonical() for item in mcp_result]},
            )
            _require_mcp_metadata_admission(admission, metadata_binding)
            if type(self._durable_source).__dict__.get("resolve_dual") is not resolve_dual:
                _fail("invalid_context")
            return (
                cookie_result, mcp_result, mcp_target,
                cookie_payload, mcp_payload,
            )
        except McpContextAuthorityError:
            raise
        except McpContextMetadataAdmissionError:
            raise
        except DurablePlanningContextError as error:
            self._native_error(error)
        except PlanningContextReaderError:
            raise
        except ValueError:
            raise
        except Exception:
            _fail("planning_unavailable")

    def read_document(self, binding: DisclosureBinding, target: SourceSelection) -> bytes:
        if type(target) is not SourceSelection or target.source != "planning":
            _fail("invalid_context")
        if self._durable_source is not None:
            try:
                payload = self._durable_source.read_document(binding, target)
                if type(payload) is not bytes:
                    _fail("planning_payload_failed")
                return payload
            except DurablePlanningContextError as error:
                self._native_error(error)
            except PlanningContextReaderError:
                raise
            except Exception:
                _fail("planning_payload_failed")
        selection = ContextSelection((target,))
        with self._principal(binding) as (principal, alias):
            metadata = self._capture_metadata_with_principal(binding, selection, principal, alias)
            item = metadata[0]
            store = self._runtime.durable_store
            if not isinstance(store, DurablePlanningRevisionStore):
                _fail("planning_unavailable")
            try:
                payload = store.read_payload(principal, owner_id=alias, revision_id=item.durable_revision_id)
                revision = store.read_revision(principal, owner_id=alias, revision_id=item.durable_revision_id)
            except Exception:
                _fail("planning_payload_failed")
            if type(payload) is not bytes or _bytes_digest(payload) != item.durable_payload_sha256:
                _fail("planning_payload_failed")
            if revision.payload_sha256 != item.durable_payload_sha256 or revision.content_sha256 != item.durable_content_sha256:
                _fail("planning_stale")
            try:
                document = json.loads(payload.decode("utf-8"))
                canonical = _canonical(document)
            except Exception:
                _fail("planning_payload_failed")
            if canonical != payload or not self._document_matches(document, item):
                _fail("planning_stale")
            return bytes(payload)

    def _capture_metadata(self, binding: DisclosureBinding, selection: ContextSelection) -> tuple[PlanningMetadata, ...]:
        if type(binding) is not DisclosureBinding or type(selection) is not ContextSelection:
            _fail("invalid_context")
        planning_items = tuple(item for item in selection.items if item.source == "planning")
        if not planning_items:
            _fail("invalid_context")
        if self._durable_source is not None:
            return self._capture_native_metadata(binding, selection, planning_items)
        planning_selection = ContextSelection(planning_items)
        with self._principal(binding) as (principal, alias):
            return self._capture_metadata_with_principal(binding, planning_selection, principal, alias)

    @staticmethod
    def _native_error(error: DurablePlanningContextError) -> None:
        mapping = {
            "invalid_binding": "invalid_binding", "invalid_context": "invalid_context",
            "planning_unavailable": "planning_unavailable", "planning_stale": "planning_stale",
            "source_not_found": "planning_stale", "policy_denied": "planning_policy_failed",
            "payload_unavailable": "planning_payload_failed", "capture_unavailable": "planning_unavailable",
        }
        _fail(mapping.get(error.code, "planning_unavailable"))

    def _capture_native_metadata(
        self, binding: DisclosureBinding, selection: ContextSelection,
        planning_items: tuple[SourceSelection, ...],
    ) -> tuple[PlanningMetadata, ...]:
        try:
            descriptors = self._durable_source.resolve(binding, selection)
            if type(descriptors) is not tuple or len(descriptors) != len(planning_items):
                _fail("planning_stale")
            metadata = []
            for selected, descriptor in zip(planning_items, descriptors):
                if type(descriptor) is not DurablePlanningSourceDescriptor or descriptor.selection() != selected:
                    _fail("planning_stale")
                metadata.append(PlanningMetadata(
                    selection=selected, project_id=descriptor.project_id,
                    roadmap_id=descriptor.roadmap_id, approved_revision=descriptor.approved_revision,
                    durable_revision_id=descriptor.durable_revision_id,
                    durable_content_sha256=descriptor.durable_content_sha256,
                    durable_payload_sha256=descriptor.durable_payload_sha256,
                    canonical_projection_sha256=descriptor.canonical_projection_sha256,
                    provenance_ref=descriptor.provenance_ref, boundary_sha256=descriptor.boundary_sha256,
                    record_sha256=descriptor.record_sha256,
                ))
            return tuple(metadata)
        except DurablePlanningContextError as error:
            self._native_error(error)
        except PlanningContextReaderError:
            raise
        except Exception:
            _fail("planning_unavailable")

    def _capture_metadata_with_principal(
        self,
        binding: DisclosureBinding,
        selection: ContextSelection,
        principal: TrustedPrincipal,
        alias: str,
    ) -> tuple[PlanningMetadata, ...]:
        store = self._runtime.durable_store
        if not isinstance(store, DurablePlanningRevisionStore):
            _fail("planning_unavailable")
        try:
            owner, batch, records, heads = store.read_subject_bound_accepted_migration_records(
                principal, project_id=self._project_id
            )
        except Exception:
            _fail("planning_unavailable")
        if owner != alias or type(batch) is not dict or type(records) is not list or type(heads) is not list:
            _fail("planning_stale")
        head_map = self._validated_heads(heads, owner)
        if any(head["project_id"] != self._project_id for head in head_map.values()):
            _fail("planning_stale")
        record_map = self._validated_records(records, owner)
        adapter = self._adapter(alias, binding.owner_sid)
        try:
            adapter_binding = adapter.binding
            discovered = adapter.discover(
                expected_binding_digest=adapter_binding.binding_digest,
                expected_snapshot_digest=adapter_binding.snapshot_digest,
                cursor=0,
                limit=100,
            )
            descriptors = {}
            for item in discovered.items:
                key = (item.source.source_id, item.source_version.source_version_id)
                if key in descriptors:
                    _fail("planning_policy_failed")
                descriptors[key] = item
        except Exception:
            _fail("planning_policy_failed")
        if discovered.next_cursor is not None:
            _fail("planning_policy_failed")
        boundary = _digest({
            "batch": batch,
            "heads": [head_map[key] for key in sorted(head_map)],
            "owner_sid": binding.owner_sid,
            "project_id": self._project_id,
            "policy_snapshot": adapter_binding.snapshot_digest,
            "policy_binding": adapter_binding.binding_digest,
        })
        result: list[PlanningMetadata] = []
        for selected in selection.items:
            descriptor = descriptors.get((selected.selection_ref, selected.revision_ref))
            if descriptor is None:
                _fail("planning_policy_failed")
            evidence = descriptor.evidence
            project = evidence.project_id
            roadmap = evidence.roadmap_id
            head = head_map.get(roadmap)
            if head is None:
                _fail("planning_stale")
            revision_id = head["current_revision_id"]
            record = record_map.get(revision_id)
            if record is None:
                _fail("planning_stale")
            try:
                revision = store.read_revision(
                    principal,
                    owner_id=alias,
                    revision_id=revision_id,
                )
            except Exception:
                _fail("planning_stale")
            document = record.document
            if (
                selected.selection_ref != descriptor.source.source_id
                or selected.revision_ref != descriptor.source_version.source_version_id
                or descriptor.source.classification.value != "private"
                or descriptor.source.content_policy.value != "inline_local"
                or descriptor.source_version.classification.value != "private"
                or descriptor.source_version.content_policy.value != "inline_local"
                or descriptor.source_version.revision_ref
                != f"approved:{evidence.approved_revision}:{evidence.canonical_projection_hash}"
                or evidence.approved_revision != head["approved_revision"]
                or evidence.domain_content_hash != head["approved_content_sha256"]
                or descriptor.source_version.content_hash != evidence.canonical_projection_hash
                or revision.revision_id != revision_id
                or revision.revision != head["current_revision"]
                or revision.revision_state != "approved"
                or revision.content_sha256 != head["current_content_sha256"]
                or revision.payload_sha256 != record.payload_sha256
                or revision.owner_id != alias
                or revision.project_id != self._project_id
                or revision.roadmap_id != roadmap
                or project != self._project_id
                or roadmap != head["roadmap_id"]
                or not self._document_matches(document, revision, project, roadmap)
            ):
                _fail("planning_stale")
            provenance = descriptor.source_version.source_version_id
            record_sha = _digest({
                "boundary_sha256": boundary,
                "canonical_projection_sha256": evidence.canonical_projection_hash,
                "content_sha256": revision.content_sha256,
                "document_revision": revision.revision,
                "durable_revision_id": revision.revision_id,
                "payload_sha256": revision.payload_sha256,
                "provenance_ref": provenance,
                "selection_ref": selected.selection_ref,
            })
            result.append(
                PlanningMetadata(
                    selection=selected,
                    project_id=project,
                    roadmap_id=roadmap,
                    approved_revision=revision.revision,
                    durable_revision_id=revision.revision_id,
                    durable_content_sha256=revision.content_sha256,
                    durable_payload_sha256=revision.payload_sha256,
                    canonical_projection_sha256=evidence.canonical_projection_hash,
                    provenance_ref=provenance,
                    boundary_sha256=boundary,
                    record_sha256=record_sha,
                )
            )
        return tuple(result)

    @contextmanager
    def _principal(self, binding: DisclosureBinding) -> Iterator[tuple[TrustedPrincipal, str]]:
        if type(binding) is not DisclosureBinding:
            _fail("invalid_binding")
        manager = self._auth_manager
        guard = getattr(manager, "guard_current_admin_subject", None)
        if not callable(guard):
            _fail("invalid_binding")
        try:
            with guard(binding.owner_sid) as guarded_alias:
                identity = manager.active_admin_subject_identity(subject_id=binding.owner_sid)
                revision = manager.security_action_revision()
                if (
                    type(identity) is not tuple
                    or len(identity) != 2
                    or type(identity[0]) is not str
                    or identity[1] != binding.owner_sid
                    or type(guarded_alias) is not str
                    or guarded_alias != identity[0]
                ):
                    _fail("invalid_binding")
                if type(revision) is not int or revision != binding.security_revision:
                    _fail("invalid_binding")
                issuer = self._runtime.issuer
                if issuer is None:
                    _fail("planning_unavailable")
                context = _digest({
                    "auth_session_binding": binding.auth_session_binding,
                    "owner_sid": binding.owner_sid,
                    "project_id": self._project_id,
                    "security_revision": binding.security_revision,
                })
                try:
                    principal = issuer.issue(
                        subject_id=binding.owner_sid,
                        owner_id=identity[0],
                        auth_context_digest=context,
                        ttl_seconds=60,
                    )
                except Exception:
                    _fail("planning_unavailable")
                try:
                    yield principal, identity[0]
                finally:
                    final_identity = manager.active_admin_subject_identity(subject_id=binding.owner_sid)
                    final_revision = manager.security_action_revision()
                    if (
                        type(final_identity) is not tuple
                        or len(final_identity) != 2
                        or type(final_identity[0]) is not str
                        or type(final_identity[1]) is not str
                        or final_identity != (identity[0], binding.owner_sid)
                        or type(final_revision) is not int
                        or final_revision != binding.security_revision
                    ):
                        _fail("invalid_binding")
        except PlanningContextReaderError:
            raise
        except Exception:
            _fail("invalid_binding")

    def _adapter(self, owner_alias: str, owner_sid: str) -> PlanningSourceAdapter:
        try:
            return PlanningSourceAdapter(
                service=self._approved_source_service,
                owner_scope=OwnerScope.for_subject_id(owner_sid),
                expected_owner_ref=owner_alias,
                observed_at=_now_text(),
                max_sources=100,
            )
        except Exception:
            _fail("planning_policy_failed")

    @staticmethod
    def _validated_heads(heads: list[object], owner: str) -> dict[str, dict[str, object]]:
        if not heads or len(heads) > 10000:
            _fail("planning_stale")
        result: dict[str, dict[str, object]] = {}
        for raw in heads:
            if type(raw) is not dict or set(raw) != _HEAD_KEYS:
                _fail("planning_stale")
            head = dict(raw)
            if (
                head["owner_id"] != owner
                or type(head["project_id"]) is not str
                or type(head["roadmap_id"]) is not str
                or not head["project_id"]
                or not head["roadmap_id"]
                or type(head["generation"]) is not int
                or isinstance(head["generation"], bool)
                or not 1 <= head["generation"] <= (1 << 63) - 1
                or any(type(head[name]) is not int or isinstance(head[name], bool) or head[name] < 1 for name in ("accepted_revision", "current_revision", "approved_revision"))
                or head["current_revision"] != head["approved_revision"]
                or head["current_revision_id"] != head["approved_revision_id"]
                or head["current_content_sha256"] != head["approved_content_sha256"]
                or head["accepted_revision"] > head["approved_revision"]
                or any(_SHA256_RE.fullmatch(head[name]) is None for name in ("accepted_content_sha256", "current_content_sha256", "approved_content_sha256"))
                or any(type(head[name]) is not str or not head[name] for name in ("accepted_revision_id", "current_revision_id", "approved_revision_id"))
            ):
                _fail("planning_stale")
            roadmap_id = head["roadmap_id"]
            if roadmap_id in result:
                _fail("planning_stale")
            result[roadmap_id] = head
        return result

    @staticmethod
    def _validated_records(records: list[object], owner: str) -> dict[str, _DurableRecord]:
        if not records or len(records) > 10000:
            _fail("planning_stale")
        result: dict[str, _DurableRecord] = {}
        for raw in records:
            if type(raw) is not tuple or len(raw) != 3 or raw[0] != owner or type(raw[1]) is not dict or type(raw[2]) is not str or not raw[2].startswith("durable:"):
                _fail("planning_stale")
            revision_id = raw[2][len("durable:"):]
            if not revision_id or revision_id in result:
                _fail("planning_stale")
            document = dict(raw[1])
            roadmaps = document.get("roadmaps")
            project = document.get("project")
            if type(project) is not dict or type(roadmaps) is not list or len(roadmaps) != 1 or type(roadmaps[0]) is not dict:
                _fail("planning_stale")
            roadmap = roadmaps[0]
            project_id = project.get("project_id")
            roadmap_id = roadmap.get("roadmap_id")
            revision = roadmap.get("revision")
            state = roadmap.get("revision_state")
            content_sha256 = roadmap.get("content_hash")
            if (
                type(project_id) is not str
                or not project_id
                or type(roadmap_id) is not str
                or not roadmap_id
                or type(revision) is not int
                or isinstance(revision, bool)
                or revision < 1
                or type(state) is not str
                or not state
                or type(content_sha256) is not str
                or _SHA256_RE.fullmatch(content_sha256) is None
            ):
                _fail("planning_stale")
            payload_sha256 = _bytes_digest(_canonical(document))
            result[revision_id] = _DurableRecord(
                revision_id=revision_id,
                document=document,
                project_id=project_id,
                roadmap_id=roadmap_id,
                revision=revision,
                revision_state=state,
                content_sha256=content_sha256,
                payload_sha256=payload_sha256,
            )
        return result

    @staticmethod
    def _document_matches(document: object, revision: object, project_id: str | None = None, roadmap_id: str | None = None) -> bool:
        if not isinstance(document, dict) or type(document.get("project")) is not dict or type(document.get("roadmaps")) is not list or len(document["roadmaps"]) != 1 or type(document["roadmaps"][0]) is not dict:
            return False
        project = document["project"].get("project_id")
        roadmap = document["roadmaps"][0]
        if project_id is not None and project != project_id:
            return False
        if roadmap_id is not None and roadmap.get("roadmap_id") != roadmap_id:
            return False
        if hasattr(revision, "revision"):
            return roadmap.get("revision") == revision.revision and roadmap.get("revision_state") == revision.revision_state and roadmap.get("content_hash") == revision.content_sha256
        if isinstance(revision, PlanningMetadata):
            return (
                roadmap.get("revision") == revision.approved_revision
                and roadmap.get("revision_state") == "approved"
                and roadmap.get("content_hash") == revision.durable_content_sha256
            )
        return True


class PlanningContextPageConsumer:
    """Verify a fresh grant and read one durable approved document page."""

    __slots__ = ("_producer", "_auth_manager", "_confirmation_app", "_authority", "_repository")

    def __init__(self, producer: PlanningContextManifestProducer, auth_manager: object, confirmation_app: PrivateContextConfirmationApp, session_factory: object) -> None:
        if not isinstance(producer, PlanningContextManifestProducer) or auth_manager is None or type(confirmation_app) is not PrivateContextConfirmationApp:
            _fail("invalid_context")
        try:
            confirmation_authority = getattr(confirmation_app, "_authority", None)
            if (
                type(confirmation_authority) is not CookieConfirmationAuthority
                or confirmation_authority._auth_manager is not auth_manager
            ):
                _fail("invalid_context")
            authority = confirmation_authority.fork()
            if (
                type(authority) is not CookieConfirmationAuthority
                or authority._auth_manager is not auth_manager
            ):
                _fail("invalid_context")
            repository = ChatConfirmationRepository(session_factory, authority)
        except ConfirmationError:
            raise
        except Exception:
            _fail("invalid_context")
        self._producer = producer
        self._auth_manager = auth_manager
        self._confirmation_app = confirmation_app
        self._authority = authority
        self._repository = repository

    def read_page(self, cookie: str, request: PlanningContextPageRequest) -> PlanningContextPage:
        if type(request) is not PlanningContextPageRequest:
            _fail("invalid_page")
        try:
            with self._authority.hold(cookie, request.chat_session_id) as binding:
                self._repository.assert_chat_owned(binding)
                manifest = self._producer.capture(binding, request.selection)
                if manifest.digest != request.expected_manifest_digest:
                    _fail("planning_stale")
                subset = self._producer.capture(binding, ContextSelection((request.target,)))
                self._confirmation_app.verify_disclosure(
                    cookie,
                    request.chat_session_id,
                    request.disclosure_message_id,
                    request.selection,
                    expected_subset=subset,
                )
                self._authority.recheck(binding)
                self._repository.assert_chat_owned(binding)
                payload = self._producer.read_document(binding, request.target)
                final_manifest = self._producer.capture(binding, request.selection)
                if final_manifest.digest != manifest.digest or final_manifest.digest != request.expected_manifest_digest:
                    _fail("planning_stale")
                final_subset = self._producer.capture(binding, ContextSelection((request.target,)))
                self._confirmation_app.verify_disclosure(
                    cookie,
                    request.chat_session_id,
                    request.disclosure_message_id,
                    request.selection,
                    expected_subset=final_subset,
                )
                self._authority.recheck(binding)
                self._repository.assert_chat_owned(binding)
                if request.offset > len(payload):
                    _fail("invalid_page")
                end = min(len(payload), request.offset + request.limit)
                page = bytes(payload[request.offset:end])
                return PlanningContextPage(
                    page_bytes=page,
                    total_bytes=len(payload),
                    offset=request.offset,
                    end=end,
                    whole_sha256=_bytes_digest(payload),
                    page_sha256=_bytes_digest(page),
                    manifest_digest=request.expected_manifest_digest,
                )
        except PlanningContextReaderError:
            raise
        except ConfirmationError as exc:
            if exc.code == "invalid_context":
                raise PlanningContextReaderError("invalid_context") from None
            raise PlanningContextReaderError("disclosure_unverified") from None
        except Exception:
            _fail("planning_payload_failed")


__all__ = [
    "PlanningContextManifestProducer",
    "PlanningContextPage",
    "PlanningContextPageConsumer",
    "PlanningContextPageRequest",
    "PlanningContextReaderError",
    "PlanningMetadata",
]
