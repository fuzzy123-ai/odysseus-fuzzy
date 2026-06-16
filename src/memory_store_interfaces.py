"""Small backend contract for memory store interface specifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_BOUND_REQUIRED = {"read", "search", "scan"}
_GRAPH_REQUIRED = {"graph"}
_DEPENDENCY_FORBIDDEN = {"accelerator"}
_UNBOUNDED_NAMES = {"load-all", "load_all", "full-scan", "full_scan", "scan-all", "scan_all"}


class MemoryStoreInterfaceError(ValueError):
    """Raised when a memory store interface specification is invalid or unsafe."""


class StoreKind(StrEnum):
    MEMORY = "memory"
    SOURCE = "source"
    CHUNK = "chunk"
    EMBEDDING = "embedding"
    GRAPH = "graph"
    JOB = "job"
    REVIEW = "review"
    QUERY_CACHE = "query_cache"


class StoreTruthRole(StrEnum):
    TRUTH = "truth"
    DERIVED = "derived"
    CACHE = "cache"
    ACCELERATOR = "accelerator"


class StoreCapability(StrEnum):
    READ = "read"
    WRITE = "write"
    SEARCH = "search"
    SCAN = "scan"
    EXPORT = "export"
    IMPORT = "import"
    REBUILD = "rebuild"
    DELETE = "delete"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise MemoryStoreInterfaceError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise MemoryStoreInterfaceError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise MemoryStoreInterfaceError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise MemoryStoreInterfaceError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_text_list(values: Iterable[Any], *, field_name: str, limit: int = _MAX_TEXT) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True, limit=limit)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_kind(value: Any) -> StoreKind:
    if isinstance(value, StoreKind):
        return value
    try:
        return StoreKind(_normalize_slug(value, field_name="store_kind").replace("-", "_"))
    except ValueError as exc:
        raise MemoryStoreInterfaceError("store_kind is not supported") from exc


def _normalize_truth_role(value: Any) -> StoreTruthRole:
    if isinstance(value, StoreTruthRole):
        return value
    try:
        return StoreTruthRole(_normalize_slug(value, field_name="truth_role").replace("-", "_"))
    except ValueError as exc:
        raise MemoryStoreInterfaceError("truth_role is not supported") from exc


def _normalize_capability(value: Any) -> StoreCapability:
    if isinstance(value, StoreCapability):
        return value
    try:
        return StoreCapability(_normalize_slug(value, field_name="capability").replace("-", "_"))
    except ValueError as exc:
        raise MemoryStoreInterfaceError("capability is not supported") from exc


@dataclass(frozen=True, slots=True)
class StoreBudget:
    default_limit: int
    max_limit: int
    max_bytes: int
    max_nodes: int
    max_edges: int

    @classmethod
    def create(
        cls,
        *,
        default_limit: Any = 0,
        max_limit: Any = 0,
        max_bytes: Any = 0,
        max_nodes: Any = 0,
        max_edges: Any = 0,
    ) -> "StoreBudget":
        def normalize_int(value: Any, field_name: str) -> int:
            try:
                normalized = int(value)
            except (TypeError, ValueError):
                raise MemoryStoreInterfaceError(f"{field_name} must be an int") from None
            if normalized < 0:
                raise MemoryStoreInterfaceError(f"{field_name} must be >= 0")
            return normalized

        normalized_default = normalize_int(default_limit, "default_limit")
        normalized_max = normalize_int(max_limit, "max_limit")
        normalized_bytes = normalize_int(max_bytes, "max_bytes")
        normalized_nodes = normalize_int(max_nodes, "max_nodes")
        normalized_edges = normalize_int(max_edges, "max_edges")
        if normalized_default and normalized_max and normalized_default > normalized_max:
            raise MemoryStoreInterfaceError("default_limit must not exceed max_limit")
        return cls(
            default_limit=normalized_default,
            max_limit=normalized_max,
            max_bytes=normalized_bytes,
            max_nodes=normalized_nodes,
            max_edges=normalized_edges,
        )

    def has_query_bound(self) -> bool:
        return any(value > 0 for value in (self.default_limit, self.max_limit, self.max_bytes))

    def has_graph_bound(self) -> bool:
        return self.max_nodes > 0 or self.max_edges > 0


@dataclass(frozen=True, slots=True)
class StoreOperationSpec:
    operation_id: str
    capability: StoreCapability
    bounded_default: bool
    budget: StoreBudget
    evidence_refs: tuple[str, ...]
    summary: str

    @classmethod
    def create(
        cls,
        *,
        operation_id: Any,
        capability: StoreCapability | str,
        bounded_default: bool,
        budget: StoreBudget | None = None,
        evidence_refs: Iterable[Any] = (),
        summary: Any = "",
    ) -> "StoreOperationSpec":
        normalized_id = _normalize_slug(operation_id, field_name="operation_id")
        if normalized_id in {name.replace("_", "-") for name in _UNBOUNDED_NAMES}:
            raise MemoryStoreInterfaceError("unbounded load_all style operations are not allowed")
        normalized_capability = _normalize_capability(capability)
        normalized_budget = budget if isinstance(budget, StoreBudget) else StoreBudget.create()
        normalized_evidence = _normalize_text_list(evidence_refs, field_name="evidence_ref")
        normalized_summary = _normalize_text(summary, field_name="summary", allow_empty=True, limit=_MAX_LONG_TEXT)
        if normalized_capability.value in _BOUND_REQUIRED and not (bounded_default or normalized_budget.has_query_bound()):
            raise MemoryStoreInterfaceError("read, search, and scan operations require bounded defaults or query budgets")
        return cls(
            operation_id=normalized_id,
            capability=normalized_capability,
            bounded_default=bool(bounded_default),
            budget=normalized_budget,
            evidence_refs=normalized_evidence,
            summary=normalized_summary,
        )


@dataclass(frozen=True, slots=True)
class StoreInterfaceSpec:
    store_id: str
    store_kind: StoreKind
    truth_role: StoreTruthRole
    capabilities: tuple[StoreCapability, ...]
    operations: tuple[StoreOperationSpec, ...]
    dependencies: tuple[StoreTruthRole, ...]
    rebuild_evidence: tuple[str, ...]
    summary: str

    @classmethod
    def create(
        cls,
        *,
        store_id: Any,
        store_kind: StoreKind | str,
        truth_role: StoreTruthRole | str,
        capabilities: Iterable[StoreCapability | str],
        operations: Iterable[StoreOperationSpec],
        dependencies: Iterable[StoreTruthRole | str] = (),
        rebuild_evidence: Iterable[Any] = (),
        summary: Any = "",
    ) -> "StoreInterfaceSpec":
        normalized_kind = _normalize_kind(store_kind)
        normalized_truth_role = _normalize_truth_role(truth_role)
        normalized_capabilities = tuple(
            sorted({_normalize_capability(capability) for capability in capabilities}, key=lambda item: item.value)
        )
        if not normalized_capabilities:
            raise MemoryStoreInterfaceError("capabilities must not be empty")
        normalized_operations = tuple(operations)
        if not normalized_operations:
            raise MemoryStoreInterfaceError("operations must not be empty")
        if any(not isinstance(operation, StoreOperationSpec) for operation in normalized_operations):
            raise MemoryStoreInterfaceError("operations must contain StoreOperationSpec items")
        operation_ids = {operation.operation_id for operation in normalized_operations}
        if len(operation_ids) != len(normalized_operations):
            raise MemoryStoreInterfaceError("operation_id must be unique within a store")

        normalized_dependencies = tuple(
            sorted({_normalize_truth_role(dependency) for dependency in dependencies}, key=lambda item: item.value)
        )
        normalized_rebuild_evidence = _normalize_text_list(rebuild_evidence, field_name="rebuild_evidence")

        if normalized_kind.value in _GRAPH_REQUIRED:
            for operation in normalized_operations:
                if operation.capability in {StoreCapability.READ, StoreCapability.SEARCH, StoreCapability.SCAN} and not (
                    operation.budget.has_graph_bound()
                ):
                    raise MemoryStoreInterfaceError("graph operations require max_nodes or max_edges budgets")

        if normalized_truth_role in {StoreTruthRole.ACCELERATOR, StoreTruthRole.DERIVED} and not (
            StoreCapability.REBUILD in normalized_capabilities or normalized_rebuild_evidence
        ):
            raise MemoryStoreInterfaceError("accelerator and derived stores require rebuild capability or rebuild evidence")

        if normalized_truth_role == StoreTruthRole.TRUTH and any(
            dependency.value in _DEPENDENCY_FORBIDDEN for dependency in normalized_dependencies
        ):
            raise MemoryStoreInterfaceError("truth stores must not depend on accelerator stores")

        for operation in normalized_operations:
            if operation.capability not in normalized_capabilities:
                raise MemoryStoreInterfaceError("operations must not declare capabilities absent from the store")

        return cls(
            store_id=_normalize_slug(store_id, field_name="store_id"),
            store_kind=normalized_kind,
            truth_role=normalized_truth_role,
            capabilities=normalized_capabilities,
            operations=tuple(sorted(normalized_operations, key=lambda item: item.operation_id)),
            dependencies=normalized_dependencies,
            rebuild_evidence=normalized_rebuild_evidence,
            summary=_normalize_text(summary, field_name="summary", allow_empty=True, limit=_MAX_LONG_TEXT),
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "store_kind": self.store_kind.value,
            "truth_role": self.truth_role.value,
            "capability_count": len(self.capabilities),
            "operation_count": len(self.operations),
            "budgeted_operation_count": sum(
                1
                for operation in self.operations
                if operation.budget.has_query_bound() or operation.budget.has_graph_bound()
            ),
            "dependency_count": len(self.dependencies),
            "rebuild_evidence_count": len(self.rebuild_evidence),
            "capabilities": tuple(capability.value for capability in self.capabilities),
            "operations": tuple(
                {
                    "operation_id": operation.operation_id,
                    "capability": operation.capability.value,
                    "bounded_default": operation.bounded_default,
                }
                for operation in self.operations
            ),
        }
