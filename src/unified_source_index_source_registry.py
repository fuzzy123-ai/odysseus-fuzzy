"""Pure, deterministic registry of default-off USI domain adapter manifests.

The registry stores declarations and optional lazy factories only.  It never
imports domain modules, invokes a factory, reads source content, connects to a
provider, or mutates a USI/domain store while it is constructed or enumerated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from src.unified_source_index_source_capability import (
    MAX_ADAPTER_MANIFESTS,
    SourceAdapterCapabilityManifest,
    SourceAdapterManifestError,
)


class SourceAdapterRegistryError(ValueError):
    """Raised when a registry lookup or declaration is ambiguous or unsafe."""


@dataclass(frozen=True, slots=True)
class SourceAdapterRegistration:
    """One manifest plus an optional factory retained without being invoked."""

    manifest: SourceAdapterCapabilityManifest
    factory: Callable[[], object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SourceAdapterCapabilityManifest):
            raise SourceAdapterRegistryError("registration requires a typed manifest")
        if self.factory is not None and not callable(self.factory):
            raise SourceAdapterRegistryError("adapter factory must be callable")


@dataclass(frozen=True, slots=True)
class SelectedSourceAdapter:
    """Content-free selected-adapter evidence for later Job/Projection writers."""

    manifest: SourceAdapterCapabilityManifest

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    @property
    def generation_ref(self) -> str:
        return self.manifest.generation_ref

    @property
    def job_profile_ref(self) -> str:
        return self.manifest.job_profile_ref

    def projection_evidence(self) -> dict[str, str]:
        return self.manifest.projection_evidence()


class SourceAdapterRegistry:
    """Immutable lookup registry; no source adapter is instantiated here."""

    def __init__(self, registrations: Iterable[SourceAdapterRegistration]) -> None:
        entries = tuple(registrations)
        if len(entries) > MAX_ADAPTER_MANIFESTS:
            raise SourceAdapterRegistryError("adapter registry exceeds its bounded capacity")
        if not all(isinstance(entry, SourceAdapterRegistration) for entry in entries):
            raise SourceAdapterRegistryError("registry entries must be typed registrations")

        by_adapter: dict[str, SourceAdapterRegistration] = {}
        by_domain: dict[str, SourceAdapterRegistration] = {}
        for entry in entries:
            manifest = entry.manifest
            if manifest.adapter_id in by_adapter:
                raise SourceAdapterRegistryError("duplicate adapter ID")
            if manifest.domain_id in by_domain:
                raise SourceAdapterRegistryError("duplicate domain ID")
            by_adapter[manifest.adapter_id] = entry
            by_domain[manifest.domain_id] = entry
        self._by_adapter = by_adapter
        self._by_domain = by_domain

    def manifests(self) -> tuple[SourceAdapterCapabilityManifest, ...]:
        """Enumerate descriptors deterministically without loading an adapter."""

        return tuple(
            self._by_adapter[adapter_id].manifest for adapter_id in sorted(self._by_adapter)
        )

    def registration(self, adapter_id: str) -> SourceAdapterRegistration:
        entry = self._by_adapter.get(adapter_id)
        if entry is None:
            raise SourceAdapterRegistryError("unknown adapter ID")
        return entry

    def for_domain(self, domain_id: str) -> SourceAdapterCapabilityManifest:
        entry = self._by_domain.get(domain_id)
        if entry is None:
            raise SourceAdapterRegistryError("unknown domain ID")
        return entry.manifest

    def select(self, adapter_id: str) -> SelectedSourceAdapter:
        """Select only evidence; this is never productive runtime activation."""

        return SelectedSourceAdapter(self.registration(adapter_id).manifest)


__all__ = [
    "SelectedSourceAdapter",
    "SourceAdapterRegistration",
    "SourceAdapterRegistry",
    "SourceAdapterRegistryError",
    "SourceAdapterManifestError",
]
