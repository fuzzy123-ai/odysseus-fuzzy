"""Bounded composition of the retained Forge, Planning, and Knowledge readers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from typing import Iterator

from core.auth import AuthManager
from src.forge_context_metadata import ForgeContextMetadata, ForgeMetadataManifest
from src.harbour_knowledge_active_runtime import KnowledgeContextManifest, KnowledgeContextManifestProducer
from src.planning_context_reader import PlanningContextManifestProducer, PlanningMetadata
from src.private_context_confirmation import ContextManifest, DisclosureBinding, ManifestItem
from src.private_context_confirmation_app import ContextSelection, ForgeSelectionLocator, SourceSelection


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

    def __post_init__(self) -> None:
        if (
            type(self.items) is not tuple
            or any(type(item) is not ManifestItem for item in self.items)
            or not 1 <= len(self.items) <= 300
            or type(self.target_manifest_digests) is not tuple
            or len(self.target_manifest_digests) != len(self.items)
            or any(type(value) is not str or len(value) not in {64, 71} for value in self.target_manifest_digests)
        ):
            _fail("invalid_context")


class CombinedContextManifestProducer:
    """Recapture and unite the three concrete private-context providers."""

    __slots__ = ("_auth_manager", "_forge", "_planning", "_knowledge")

    def __init__(
        self,
        *,
        auth_manager: AuthManager,
        forge: ForgeContextMetadata,
        planning: PlanningContextManifestProducer,
        knowledge: KnowledgeContextManifestProducer,
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


__all__ = ["CombinedContextManifestProducer", "CombinedContextSnapshot", "ContextManifestProducerError"]
