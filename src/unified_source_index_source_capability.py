"""Declarative, default-off capability manifests for USI domain adapters.

This module describes adapter authority only.  It has no domain imports,
filesystem access, provider access, source reads, or runtime side effects.
Actual adapters remain owned by their respective domain slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from typing import Iterable

from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    canonical_json,
)


MAX_ADAPTER_MANIFESTS = 64
_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class SourceAdapterManifestError(ValueError):
    """Raised when adapter authority is incomplete, ambiguous, or unsafe."""


class OwnerScopeRequirement(StrEnum):
    """The only owner identity form a domain adapter may accept."""

    IMMUTABLE_OPAQUE = "immutable_opaque"


class ProviderConstraint(StrEnum):
    """Whether an adapter is allowed to use a provider boundary at all."""

    NONE = "none"
    LOCAL_ACCEPTED_BOUNDARY = "local_accepted_boundary"
    EXTERNAL_DISABLED = "external_disabled"


class QueryCapability(StrEnum):
    """The query surface advertised by one adapter, not a query authority."""

    DISABLED = "disabled"
    EXACT_READER = "exact_reader"


class SourceAdapterOperation(StrEnum):
    """The bounded common adapter operations, declared one by one."""

    DISCOVER = "discover"
    OBSERVE_VERSION = "observe_version"
    EXTRACT = "extract"
    READ_EXACT = "read_exact"
    OBSERVE_UNAVAILABLE = "observe_unavailable"


_BASE_OPERATIONS = frozenset(
    {
        SourceAdapterOperation.DISCOVER,
        SourceAdapterOperation.OBSERVE_VERSION,
        SourceAdapterOperation.EXTRACT,
        SourceAdapterOperation.OBSERVE_UNAVAILABLE,
    }
)


@dataclass(frozen=True, slots=True)
class SourceAdapterCapabilityManifest:
    """One deterministic, non-executing adapter capability declaration.

    ``productive_default_enabled`` intentionally cannot be true.  Runtime
    activation belongs to UIR and the parent USI live gate, not this registry.
    """

    adapter_id: str
    adapter_version: str
    domain_id: str
    source_kind: SourceKind
    content_policy: ContentPolicy
    classification_ceiling: Classification
    owner_scope_requirement: OwnerScopeRequirement
    provider_constraint: ProviderConstraint
    query_capability: QueryCapability
    operations: tuple[SourceAdapterOperation, ...]
    exact_reader_boundary: str = ""
    productive_default_enabled: bool = False

    def __post_init__(self) -> None:
        for field_name in ("adapter_id", "adapter_version", "domain_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name))
        object.__setattr__(self, "source_kind", _enum(self.source_kind, SourceKind, "source_kind"))
        object.__setattr__(self, "content_policy", _enum(self.content_policy, ContentPolicy, "content_policy"))
        object.__setattr__(self, "classification_ceiling", _enum(self.classification_ceiling, Classification, "classification_ceiling"))
        object.__setattr__(self, "owner_scope_requirement", _enum(self.owner_scope_requirement, OwnerScopeRequirement, "owner_scope_requirement"))
        object.__setattr__(self, "provider_constraint", _enum(self.provider_constraint, ProviderConstraint, "provider_constraint"))
        object.__setattr__(self, "query_capability", _enum(self.query_capability, QueryCapability, "query_capability"))
        if type(self.productive_default_enabled) is not bool or self.productive_default_enabled:
            raise SourceAdapterManifestError("productive adapters must remain default-off")

        operations = _operations(self.operations)
        if not _BASE_OPERATIONS.issubset(operations):
            raise SourceAdapterManifestError("manifest must declare every base adapter operation")
        object.__setattr__(self, "operations", tuple(sorted(operations, key=str)))

        boundary = _optional_identifier(self.exact_reader_boundary, "exact_reader_boundary")
        if self.query_capability is QueryCapability.EXACT_READER:
            if self.content_policy is ContentPolicy.METADATA_ONLY:
                raise SourceAdapterManifestError("metadata-only adapters cannot advertise an exact reader")
            if not boundary or SourceAdapterOperation.READ_EXACT not in operations:
                raise SourceAdapterManifestError("exact-reader query capability requires boundary and read_exact")
        elif boundary or SourceAdapterOperation.READ_EXACT in operations:
            raise SourceAdapterManifestError("disabled query capability cannot advertise exact-reader semantics")
        object.__setattr__(self, "exact_reader_boundary", boundary)

    @property
    def generation_ref(self) -> str:
        """Stable selection generation safe for USI Projection and Job evidence."""

        digest = hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()
        return f"usi_generation_{digest}"

    @property
    def job_profile_ref(self) -> str:
        """A bounded Job profile token that binds a job to this selection."""

        return f"usi.adapter_generation.{self.generation_ref.removeprefix('usi_generation_')}"

    def projection_evidence(self) -> dict[str, str]:
        """Fields callers must pass to ``ProjectionManifest.create`` unchanged."""

        return {
            "implementation_ref": f"adapter.{self.adapter_id}",
            "implementation_version": self.adapter_version,
            "output_generation_ref": self.generation_ref,
        }

    def to_dict(self) -> dict[str, object]:
        """Return the closed, content-free data used for a generation digest."""

        return {
            "schema": "odysseus.unified_source_index.adapter_manifest.v1",
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "domain_id": self.domain_id,
            "source_kind": self.source_kind.value,
            "content_policy": self.content_policy.value,
            "classification_ceiling": self.classification_ceiling.value,
            "owner_scope_requirement": self.owner_scope_requirement.value,
            "provider_constraint": self.provider_constraint.value,
            "query_capability": self.query_capability.value,
            "operations": [operation.value for operation in self.operations],
            "exact_reader_boundary": self.exact_reader_boundary,
            "productive_default_enabled": self.productive_default_enabled,
        }


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise SourceAdapterManifestError(f"{field_name} must be a bounded canonical identifier")
    return value


def _optional_identifier(value: object, field_name: str) -> str:
    if value == "":
        return ""
    return _identifier(value, field_name)


def _enum(value: object, enum_type, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise SourceAdapterManifestError(f"{field_name} is invalid") from exc


def _operations(values: Iterable[SourceAdapterOperation | str]) -> frozenset[SourceAdapterOperation]:
    if not isinstance(values, tuple) or not values:
        raise SourceAdapterManifestError("operations must be a non-empty bounded tuple")
    if len(values) > len(SourceAdapterOperation):
        raise SourceAdapterManifestError("operations are unbounded")
    normalized = frozenset(_enum(value, SourceAdapterOperation, "operation") for value in values)
    if len(normalized) != len(values):
        raise SourceAdapterManifestError("operations must not contain duplicates")
    return normalized


__all__ = [
    "MAX_ADAPTER_MANIFESTS",
    "OwnerScopeRequirement",
    "ProviderConstraint",
    "QueryCapability",
    "SourceAdapterCapabilityManifest",
    "SourceAdapterManifestError",
    "SourceAdapterOperation",
]
