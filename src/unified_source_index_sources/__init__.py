"""Namespace for domain-owned USI source adapters.

This package intentionally exports only the declarative registry seam.  It
does not import domain adapters, source readers, providers, or runtime wiring.
"""

from src.unified_source_index_source_capability import SourceAdapterCapabilityManifest
from src.unified_source_index_source_registry import (
    SelectedSourceAdapter,
    SourceAdapterRegistration,
    SourceAdapterRegistry,
)

__all__ = [
    "SelectedSourceAdapter",
    "SourceAdapterCapabilityManifest",
    "SourceAdapterRegistration",
    "SourceAdapterRegistry",
]
