"""Bounded composition of the retained Forge, Planning, and Knowledge readers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from typing import Iterator

from core.auth import AuthManager
from src.forge_context_metadata import ForgeContextMetadata, ForgeContextMetadataError, ForgeMetadataManifest
from src.harbour_knowledge_active_runtime import KnowledgeContextManifest, KnowledgeContextManifestProducer
from src.planning_context_reader import PlanningContextManifestProducer, PlanningContextReaderError, PlanningMetadata
from src.private_context_confirmation import ContextManifest, DisclosureBinding, ManifestItem
from src.private_context_confirmation_app import ContextSelection, ForgeSelectionLocator, SourceSelection
from src.mcp_context_authority import McpContextAuthority, McpContextAuthorityError, McpContextReadLease
from src.context_metadata_binding import McpContextMetadataAdmissionError, McpContextMetadataBinding, _open_mcp_metadata_admission, _require_mcp_metadata_admission


_ERROR_CODES = frozenset({
    "invalid_provider",
    "invalid_binding",
    "invalid_context",
    "capture_unavailable",
    "context_stale",
    "foundation_unavailable",
})


class ContextManifestProducerError(ValueError):
    """Fixed, content-free errors for the three-domain composition boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else "capture_unavailable"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"ContextManifestProducerError(code={self.code!r})"


def _fail(code: str) -> None:
    raise ContextManifestProducerError(code) from None


@dataclass(frozen=True, slots=True)
class _NativeCapture:
    items: tuple[ManifestItem, ...]
    forge: tuple[tuple[SourceSelection, ForgeMetadataManifest], ...]
    planning: tuple[object, ...]
    knowledge: object | None
    target_manifest_digests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CombinedContextSnapshot:
    """Immutable full-selection identity for content pagination."""

    items: tuple[ManifestItem, ...]
    target_manifest_digests: tuple[str, ...]
    mcp_metadata_binding: McpContextMetadataBinding | None = None

    def __post_init__(self) -> None:
        if (
            type(self.items) is not tuple
            or any(type(item) is not ManifestItem for item in self.items)
            or not 1 <= len(self.items) <= 300
            or type(self.target_manifest_digests) is not tuple
            or len(self.target_manifest_digests) != len(self.items)
            or any(type(value) is not str or len(value) not in {64, 71} for value in self.target_manifest_digests)
            or (self.mcp_metadata_binding is not None and type(self.mcp_metadata_binding) is not McpContextMetadataBinding)
        ):
            _fail("invalid_context")


class CombinedContextManifestProducer:
    """Recapture and unite the three concrete private-context providers."""

    __slots__ = ("_auth_manager", "_forge", "_planning", "_knowledge", "_mcp_authority", "_mcp_identity")

    def __init__(
        self,
        *,
        auth_manager: AuthManager,
        forge: ForgeContextMetadata,
        planning: PlanningContextManifestProducer,
        knowledge: KnowledgeContextManifestProducer,
        mcp_authority: McpContextAuthority | None = None,
    ) -> None:
        if (
            type(auth_manager) is not AuthManager
            or type(forge) is not ForgeContextMetadata
            or type(planning) is not PlanningContextManifestProducer
            or type(knowledge) is not KnowledgeContextManifestProducer
        ):
            _fail("invalid_provider")
        try:
            if (
                forge._auth_manager is not auth_manager
                or planning._auth_manager is not auth_manager
                or knowledge._composition._auth is not auth_manager
            ):
                _fail("invalid_provider")
        except ContextManifestProducerError:
            raise
        except Exception:
            _fail("invalid_provider")
        self._auth_manager = auth_manager
        self._forge = forge
        self._planning = planning
        self._knowledge = knowledge
        if mcp_authority is not None and (type(mcp_authority) is not McpContextAuthority or mcp_authority._auth_manager is not auth_manager):
            _fail("invalid_provider")
        self._mcp_authority = mcp_authority
        self._mcp_identity = (
            auth_manager, forge, planning, knowledge, mcp_authority,
            forge._auth_manager, planning._auth_manager, knowledge._composition._auth,
            knowledge._composition,
            planning._durable_source,
            getattr(planning._durable_source, "_runtime", None),
            getattr(planning._durable_source, "_store", None),
            getattr(planning._durable_source, "_issuer", None),
            getattr(planning._durable_source, "_project_id", None),
            getattr(mcp_authority, "_session_factory", None),
        )

    def capture_metadata(
        self,
        binding: DisclosureBinding,
        selection: ContextSelection,
    ) -> tuple[ManifestItem, ...]:
        self._validate_inputs(binding, selection)
        try:
            with self._admin_guard(binding):
                with self._knowledge_reservation(selection):
                    first = self._capture_native(binding, selection)
                    final = self._capture_native(binding, selection)
                    if first != final:
                        _fail("context_stale")
                    return first.items
        except ContextManifestProducerError:
            raise
        except Exception:
            _fail("capture_unavailable")

    def capture(self, binding: DisclosureBinding, selection: ContextSelection) -> ContextManifest:
        metadata = self.capture_metadata(binding, selection)
        try:
            return ContextManifest(metadata)
        except ContextManifestProducerError:
            raise
        except Exception:
            _fail("foundation_unavailable")

    def capture_snapshot(
        self,
        binding: DisclosureBinding,
        selection: ContextSelection,
    ) -> CombinedContextSnapshot:
        """Capture the canonical projection and native digest for every target."""
        self._validate_inputs(binding, selection)
        try:
            with self._admin_guard(binding):
                with self._knowledge_reservation(selection):
                    first = self._capture_native(binding, selection)
                    final = self._capture_native(binding, selection)
                    if first != final:
                        _fail("context_stale")
                    return CombinedContextSnapshot(
                        first.items, first.target_manifest_digests,
                    )
        except ContextManifestProducerError:
            raise
        except Exception:
            _fail("capture_unavailable")

    def capture_mcp_snapshot(self, lease: object, selection: ContextSelection) -> CombinedContextSnapshot:
        """Capture detached, recipient-bound native metadata with no cookie path."""
        self._assert_mcp_provider_pins()
        if type(lease) is not McpContextReadLease:
            _fail("invalid_binding")
        if type(selection) is not ContextSelection:
            _fail("invalid_context")
        try:
            with _open_mcp_metadata_admission(self._mcp_authority, lease) as (admission, metadata_binding):
                with self._mcp_knowledge_reservation(selection):
                    first = self._capture_native_mcp(admission, metadata_binding, selection)
                    _require_mcp_metadata_admission(admission, metadata_binding)
                    final = self._capture_native_mcp(admission, metadata_binding, selection)
                    if first != final: _fail("context_stale")
                    result = CombinedContextSnapshot(first.items, first.target_manifest_digests, metadata_binding)
            self._assert_mcp_provider_pins()
            return result
        except ContextManifestProducerError: raise
        except McpContextAuthorityError: raise
        except McpContextMetadataAdmissionError: _fail("invalid_binding")
        except ForgeContextMetadataError as error:
            if error.code in {"invalid_binding", "invalid_context"}:
                _fail(error.code)
            if error.code in {"forge_context_stale", "forge_context_integrity"}:
                _fail("context_stale")
            _fail("capture_unavailable")
        except PlanningContextReaderError as error:
            if error.code in {"invalid_binding", "invalid_context"}:
                _fail(error.code)
            if error.code in {"planning_stale", "source_not_found"}:
                _fail("context_stale")
            _fail("capture_unavailable")
        except Exception: _fail("capture_unavailable")

    def __repr__(self) -> str:
        return "CombinedContextManifestProducer(<redacted>)"

    @staticmethod
    def _validate_inputs(binding: DisclosureBinding, selection: ContextSelection) -> None:
        if type(binding) is not DisclosureBinding:
            _fail("invalid_binding")
        if type(selection) is not ContextSelection:
            _fail("invalid_context")
        if any(type(item) is not SourceSelection for item in selection.items):
            _fail("invalid_context")

    @contextmanager
    def _admin_guard(self, binding: DisclosureBinding) -> Iterator[str]:
        guard = getattr(self._auth_manager, "guard_current_admin_subject", None)
        identity_fn = getattr(self._auth_manager, "active_admin_subject_identity", None)
        revision_fn = getattr(self._auth_manager, "security_action_revision", None)
        if not callable(guard) or not callable(identity_fn) or not callable(revision_fn):
            _fail("invalid_binding")
        try:
            with guard(binding.owner_sid) as alias:
                identity = identity_fn(subject_id=binding.owner_sid)
                revision = revision_fn()
                if identity != (alias, binding.owner_sid) or revision != binding.security_revision:
                    _fail("invalid_binding")
                yield alias
                identity = identity_fn(subject_id=binding.owner_sid)
                revision = revision_fn()
                if identity != (alias, binding.owner_sid) or revision != binding.security_revision:
                    _fail("invalid_binding")
        except ContextManifestProducerError:
            raise
        except Exception:
            _fail("invalid_binding")

    @contextmanager
    def _knowledge_reservation(self, selection: ContextSelection) -> Iterator[None]:
        if not any(item.source == "knowledge" for item in selection.items):
            yield
            return
        composition = self._knowledge._composition
        operation = getattr(composition, "_context_operation", None)
        if not callable(operation):
            _fail("invalid_provider")
        try:
            with operation():
                yield
        except ContextManifestProducerError:
            raise
        except Exception:
            _fail("capture_unavailable")

    @contextmanager
    def _mcp_knowledge_reservation(self, selection: ContextSelection) -> Iterator[None]:
        if not any(item.source == "knowledge" for item in selection.items):
            yield
            return
        try:
            with self._knowledge._composition._context_operation():
                yield
        except McpContextAuthorityError:
            raise
        except McpContextMetadataAdmissionError:
            raise
        except ContextManifestProducerError:
            raise
        except ForgeContextMetadataError:
            raise
        except PlanningContextReaderError:
            raise
        except ValueError:
            _fail("context_stale")
        except KnowledgeContextError:
            _fail("capture_unavailable")
        except Exception:
            _fail("capture_unavailable")

    def _assert_mcp_provider_pins(self) -> None:
        configured = self._mcp_identity
        authority = configured[4]
        if authority is None or type(authority) is not McpContextAuthority or authority._auth_manager is not configured[0]:
            _fail("invalid_context")
        if authority._session_factory is not configured[14] or authority._session_factory is not authority._session_factory_pin:
            _fail("invalid_provider")
        if (
            type(self._auth_manager) is not AuthManager
            or type(self._forge) is not ForgeContextMetadata
            or type(self._planning) is not PlanningContextManifestProducer
            or type(self._knowledge) is not KnowledgeContextManifestProducer
            or any(current is not pinned for current, pinned in zip(
                (self._auth_manager, self._forge, self._planning, self._knowledge, self._mcp_authority), configured[:5]
            ))
            or self._forge._auth_manager is not configured[5]
            or self._planning._auth_manager is not configured[6]
            or self._knowledge._composition._auth is not configured[7]
            or self._knowledge._composition is not configured[8]
            or self._planning._durable_source is not configured[9]
        ):
            _fail("invalid_provider")
        durable = configured[9]
        if (durable is None or durable._runtime is not configured[10] or durable._store is not configured[11]
                or durable._issuer is not configured[12] or durable._project_id != configured[13]
                or durable._auth_manager is not configured[0]):
            _fail("invalid_provider")
        try:
            durable._assert_runtime_pins()
        except Exception:
            _fail("invalid_provider")

    def _capture_native(self, binding: DisclosureBinding, selection: ContextSelection) -> _NativeCapture:
        forge_selection = tuple(item for item in selection.items if item.source == "forge")
        planning_selection = tuple(item for item in selection.items if item.source == "planning")
        knowledge_selection = tuple(item for item in selection.items if item.source == "knowledge")
        forge_results: list[tuple[SourceSelection, ForgeMetadataManifest]] = []
        items: list[ManifestItem] = []
        target_digests: dict[tuple[str, str, str], str] = {}
        try:
            for selected in forge_selection:
                locator = selected.forge_locator
                if type(locator) is not ForgeSelectionLocator:
                    _fail("invalid_context")
                manifest = self._forge.capture_version_metadata(
                    binding,
                    locator.repo_id,
                    locator.version_id,
                    (locator.path,),
                )
                if (
                    type(manifest) is not ForgeMetadataManifest
                    or manifest.descriptor.source_id != selected.selection_ref
                    or manifest.descriptor.source_version_id != selected.revision_ref
                    or manifest.item.selection_ref != selected.selection_ref
                    or manifest.item.revision_ref != selected.revision_ref
                    or manifest.descriptor.selected_paths != (locator.path,)
                ):
                    _fail("context_stale")
                forge_results.append((selected, manifest))
                items.append(manifest.item)
                target_digests[(selected.source, selected.selection_ref, selected.revision_ref)] = manifest.digest

            planning_results = self._planning.capture_metadata(
                binding,
                ContextSelection(planning_selection),
            ) if planning_selection else ()
            if (
                type(planning_results) is not tuple
                or any(type(metadata) is not PlanningMetadata for metadata in planning_results)
                or len(planning_results) != len(planning_selection)
            ):
                _fail("context_stale")
            for selected, metadata in zip(planning_selection, planning_results):
                if (
                    metadata.selection != selected
                    or metadata.selection.selection_ref != selected.selection_ref
                    or metadata.selection.revision_ref != selected.revision_ref
                ):
                    _fail("context_stale")
                items.append(ManifestItem(
                    "planning",
                    selected.selection_ref,
                    selected.revision_ref,
                    metadata.boundary_sha256,
                    metadata.record_sha256,
                    metadata.durable_payload_sha256,
                    "private",
                    metadata.provenance_ref,
                ))
            if planning_selection:
                planning_manifest = ContextManifest(tuple(sorted(
                    items[-len(planning_selection):],
                    key=lambda item: json.dumps(item._canonical(), sort_keys=True, separators=(",", ":")),
                )))
                for selected in planning_selection:
                    target_digests[(selected.source, selected.selection_ref, selected.revision_ref)] = planning_manifest.digest

            knowledge_result = self._knowledge.capture_metadata(
                binding,
                ContextSelection(knowledge_selection),
            ) if knowledge_selection else None
            if knowledge_selection:
                if type(knowledge_result) is not KnowledgeContextManifest:
                    _fail("context_stale")
                native_items = getattr(knowledge_result, "items", None)
                if type(native_items) is not tuple or len(native_items) != len(knowledge_selection):
                    _fail("context_stale")
                by_key = {
                    (item.selection_ref, item.revision_ref): item
                    for item in native_items
                }
                if len(by_key) != len(native_items):
                    _fail("context_stale")
                for selected in knowledge_selection:
                    native = by_key.get((selected.selection_ref, selected.revision_ref))
                    if native is None:
                        _fail("context_stale")
                    items.append(ManifestItem(
                        "knowledge",
                        native.selection_ref,
                        native.revision_ref,
                        native.boundary_sha256,
                        native.record_sha256,
                        native.payload_sha256,
                        native.classification,
                        native.provenance_ref,
                    ))
                for selected in knowledge_selection:
                    target_digests[(selected.source, selected.selection_ref, selected.revision_ref)] = knowledge_result.digest
            result = tuple(sorted(
                items,
                key=lambda item: json.dumps(item._canonical(), sort_keys=True, separators=(",", ":")),
            ))
            if len(result) != len(selection.items):
                _fail("context_stale")
            ordered_digests = tuple(
                target_digests[(item.source, item.selection_ref, item.revision_ref)]
                for item in selection.items
            )
            return _NativeCapture(result, tuple(forge_results), tuple(planning_results), knowledge_result, ordered_digests)
        except ContextManifestProducerError:
            raise
        except Exception:
            _fail("capture_unavailable")

    def _capture_native_mcp(self, admission: object, metadata_binding: object, selection: ContextSelection) -> _NativeCapture:
        _require_mcp_metadata_admission(admission, metadata_binding)
        forge_selection=tuple(item for item in selection.items if item.source=="forge")
        planning_selection=tuple(item for item in selection.items if item.source=="planning")
        knowledge_selection=tuple(item for item in selection.items if item.source=="knowledge")
        items=[]; target_digests={}; forge_results=[]
        for selected in forge_selection:
            _require_mcp_metadata_admission(admission, metadata_binding)
            locator=selected.forge_locator
            if type(locator) is not ForgeSelectionLocator: _fail("invalid_context")
            manifest,target=self._forge.capture_version_metadata_mcp(admission,metadata_binding,locator.repo_id,locator.version_id,(locator.path,))
            if manifest.descriptor.source_id != selected.selection_ref or manifest.descriptor.source_version_id != selected.revision_ref: _fail("context_stale")
            forge_results.append((selected,manifest));items.append(manifest.item);target_digests[(selected.source,selected.selection_ref,selected.revision_ref)]=target
            _require_mcp_metadata_admission(admission, metadata_binding)
        planning_results=()
        if planning_selection:
            _require_mcp_metadata_admission(admission, metadata_binding)
            planning_results,planning_target=self._planning.capture_metadata_mcp(admission,metadata_binding,ContextSelection(planning_selection))
            if len(planning_results)!=len(planning_selection): _fail("context_stale")
            for selected,metadata in zip(planning_selection,planning_results):
                if metadata.selection != selected: _fail("context_stale")
                items.append(ManifestItem("planning",selected.selection_ref,selected.revision_ref,metadata.boundary_sha256,metadata.record_sha256,metadata.durable_payload_sha256,"private",metadata.provenance_ref));target_digests[(selected.source,selected.selection_ref,selected.revision_ref)]=planning_target
            _require_mcp_metadata_admission(admission, metadata_binding)
        knowledge_result=None
        if knowledge_selection:
            _require_mcp_metadata_admission(admission, metadata_binding)
            knowledge_result,knowledge_target=self._knowledge.capture_metadata_mcp(admission,metadata_binding,ContextSelection(knowledge_selection))
            if type(knowledge_result) is not KnowledgeContextManifest or len(knowledge_result.items)!=len(knowledge_selection): _fail("context_stale")
            by_key={(item.selection_ref,item.revision_ref):item for item in knowledge_result.items}
            for selected in knowledge_selection:
                native=by_key.get((selected.selection_ref,selected.revision_ref))
                if native is None: _fail("context_stale")
                items.append(ManifestItem("knowledge",native.selection_ref,native.revision_ref,native.boundary_sha256,native.record_sha256,native.payload_sha256,native.classification,native.provenance_ref));target_digests[(selected.source,selected.selection_ref,selected.revision_ref)]=knowledge_target
            _require_mcp_metadata_admission(admission, metadata_binding)
        result=tuple(sorted(items,key=lambda item: json.dumps(item._canonical(),sort_keys=True,separators=(",",":"))))
        if len(result)!=len(selection.items): _fail("context_stale")
        ordered=tuple(target_digests[(item.source,item.selection_ref,item.revision_ref)] for item in selection.items)
        _require_mcp_metadata_admission(admission,metadata_binding)
        return _NativeCapture(result,tuple(forge_results),tuple(planning_results),knowledge_result,ordered)


__all__ = ["CombinedContextManifestProducer", "CombinedContextSnapshot", "ContextManifestProducerError"]
