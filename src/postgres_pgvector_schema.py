"""Small backend contract for a future Postgres+pgvector schema plan."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TABLE_NAMES = {
    "source_providers",
    "sources",
    "source_versions",
    "chunks",
    "chunk_embeddings",
    "entities",
    "relations",
    "provenance",
    "index_runs",
    "automation_runs",
    "review_items",
    "query_cache",
    "graph_snapshots",
}


class PostgresSchemaPlanError(ValueError):
    """Raised when a Postgres schema planning payload is invalid or unsafe."""


class SchemaRole(StrEnum):
    TRUTH = "truth"
    DERIVED = "derived"
    CACHE = "cache"


class GoNoGoStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    GO = "go"
    NO_GO = "no_go"
    ROLLED_BACK = "rolled_back"
    SUPERSEDED = "superseded"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise PostgresSchemaPlanError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise PostgresSchemaPlanError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise PostgresSchemaPlanError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_name(value: Any, *, field_name: str) -> str:
    normalized = _normalize_slug(value, field_name=field_name).replace("-", "_")
    if field_name == "table_name" and normalized not in _TABLE_NAMES:
        raise PostgresSchemaPlanError("table_name is not a supported schema table")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise PostgresSchemaPlanError(f"{field_name} must not be empty")
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


def _normalize_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    raise PostgresSchemaPlanError(f"{field_name} must be a bool")


def _normalize_role(value: Any) -> SchemaRole:
    if isinstance(value, SchemaRole):
        return value
    try:
        return SchemaRole(_normalize_slug(value, field_name="schema_role"))
    except ValueError as exc:
        raise PostgresSchemaPlanError("schema_role is not supported") from exc


def _normalize_go_no_go_status(value: Any) -> GoNoGoStatus:
    if isinstance(value, GoNoGoStatus):
        return value
    normalized = _normalize_slug(value, field_name="go_no_go_status").replace("-", "_")
    try:
        return GoNoGoStatus(normalized)
    except ValueError as exc:
        raise PostgresSchemaPlanError("go_no_go_status is not supported") from exc


def _normalize_count(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise PostgresSchemaPlanError(f"{field_name} must be an int") from None
    if normalized < 0:
        raise PostgresSchemaPlanError(f"{field_name} must be >= 0")
    return normalized


@dataclass(frozen=True, slots=True)
class MigrationCountEvidence:
    source_count: int
    chunk_count: int
    embedding_count: int
    entity_count: int
    relation_count: int
    provenance_count: int

    @classmethod
    def create(
        cls,
        *,
        source_count: Any,
        chunk_count: Any,
        embedding_count: Any,
        entity_count: Any,
        relation_count: Any,
        provenance_count: Any,
    ) -> "MigrationCountEvidence":
        return cls(
            source_count=_normalize_count(source_count, field_name="source_count"),
            chunk_count=_normalize_count(chunk_count, field_name="chunk_count"),
            embedding_count=_normalize_count(embedding_count, field_name="embedding_count"),
            entity_count=_normalize_count(entity_count, field_name="entity_count"),
            relation_count=_normalize_count(relation_count, field_name="relation_count"),
            provenance_count=_normalize_count(provenance_count, field_name="provenance_count"),
        )


@dataclass(frozen=True, slots=True)
class PgVectorColumn:
    column_name: str
    dimensions: int
    distance_metric: str

    @classmethod
    def create(
        cls,
        *,
        column_name: Any,
        dimensions: Any,
        distance_metric: Any,
    ) -> "PgVectorColumn":
        try:
            normalized_dimensions = int(dimensions)
        except (TypeError, ValueError):
            raise PostgresSchemaPlanError("dimensions must be an int") from None
        if normalized_dimensions <= 0:
            raise PostgresSchemaPlanError("dimensions must be > 0")
        return cls(
            column_name=_normalize_name(column_name, field_name="column_name"),
            dimensions=normalized_dimensions,
            distance_metric=_normalize_text(distance_metric, field_name="distance_metric", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class SchemaIndex:
    index_id: str
    table_name: str
    columns: tuple[str, ...]
    index_kind: str
    vector_index: bool

    @classmethod
    def create(
        cls,
        *,
        index_id: Any,
        table_name: Any,
        columns: Iterable[Any],
        index_kind: Any,
        vector_index: Any = False,
    ) -> "SchemaIndex":
        normalized_columns = tuple(
            sorted({_normalize_name(column, field_name="column_name") for column in columns})
        )
        if not normalized_columns:
            raise PostgresSchemaPlanError("index columns must not be empty")
        return cls(
            index_id=_normalize_slug(index_id, field_name="index_id"),
            table_name=_normalize_name(table_name, field_name="table_name"),
            columns=normalized_columns,
            index_kind=_normalize_text(index_kind, field_name="index_kind", allow_empty=False),
            vector_index=_normalize_bool(vector_index, field_name="vector_index"),
        )


@dataclass(frozen=True, slots=True)
class SchemaTable:
    table_name: str
    schema_role: SchemaRole
    primary_key: str
    columns: tuple[str, ...]
    vector_columns: tuple[PgVectorColumn, ...]
    rebuild_required: bool
    backup_required: bool
    ttl_seconds: int
    invalidation_policy: str

    @classmethod
    def create(
        cls,
        *,
        table_name: Any,
        schema_role: SchemaRole | str,
        primary_key: Any,
        columns: Iterable[Any],
        vector_columns: Iterable[PgVectorColumn] = (),
        rebuild_required: Any = False,
        backup_required: Any = False,
        ttl_seconds: Any = 0,
        invalidation_policy: Any = "",
    ) -> "SchemaTable":
        normalized_columns = tuple(sorted({_normalize_name(column, field_name="column_name") for column in columns}))
        if not normalized_columns:
            raise PostgresSchemaPlanError("table columns must not be empty")
        normalized_primary_key = _normalize_name(primary_key, field_name="primary_key")
        if normalized_primary_key not in normalized_columns:
            raise PostgresSchemaPlanError("primary_key must be present in columns")
        normalized_vector_columns = tuple(vector_columns)
        if any(not isinstance(column, PgVectorColumn) for column in normalized_vector_columns):
            raise PostgresSchemaPlanError("vector_columns must contain PgVectorColumn items")
        try:
            normalized_ttl = int(ttl_seconds)
        except (TypeError, ValueError):
            raise PostgresSchemaPlanError("ttl_seconds must be an int") from None
        if normalized_ttl < 0:
            raise PostgresSchemaPlanError("ttl_seconds must be >= 0")
        return cls(
            table_name=_normalize_name(table_name, field_name="table_name"),
            schema_role=_normalize_role(schema_role),
            primary_key=normalized_primary_key,
            columns=normalized_columns,
            vector_columns=normalized_vector_columns,
            rebuild_required=_normalize_bool(rebuild_required, field_name="rebuild_required"),
            backup_required=_normalize_bool(backup_required, field_name="backup_required"),
            ttl_seconds=normalized_ttl,
            invalidation_policy=_normalize_text(
                invalidation_policy,
                field_name="invalidation_policy",
                allow_empty=True,
                limit=_MAX_LONG_TEXT,
            ),
        )


@dataclass(frozen=True, slots=True)
class MigrationReadiness:
    truth_store: str
    migration_run_id: str
    schema_version: str
    backup_ref: str
    restore_ref: str
    rollback_plan: str
    index_run_id: str
    query_cache_policy: str
    go_no_go_status: GoNoGoStatus
    count_evidence: MigrationCountEvidence
    read_only_compare: bool
    go: bool

    @classmethod
    def create(
        cls,
        *,
        truth_store: Any,
        migration_run_id: Any,
        schema_version: Any,
        backup_ref: Any,
        restore_ref: Any,
        rollback_plan: Any,
        index_run_id: Any,
        query_cache_policy: Any,
        go_no_go_status: GoNoGoStatus | str,
        count_evidence: MigrationCountEvidence,
        read_only_compare: Any,
        go: Any,
    ) -> "MigrationReadiness":
        normalized_truth_store = _normalize_text(
            truth_store,
            field_name="truth_store",
            allow_empty=False,
            limit=_MAX_TEXT,
        )
        if "postgres" not in normalized_truth_store.lower() or "pgvector" not in normalized_truth_store.lower():
            raise PostgresSchemaPlanError("truth_store must declare Postgres plus pgvector")
        normalized_backup = _normalize_text(backup_ref, field_name="backup_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_restore = _normalize_text(restore_ref, field_name="restore_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_rollback = _normalize_text(
            rollback_plan,
            field_name="rollback_plan",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        if not isinstance(count_evidence, MigrationCountEvidence):
            raise PostgresSchemaPlanError("count_evidence must be MigrationCountEvidence")
        normalized_status = _normalize_go_no_go_status(go_no_go_status)
        normalized_compare = _normalize_bool(read_only_compare, field_name="read_only_compare")
        normalized_go = _normalize_bool(go, field_name="go")
        if normalized_go and normalized_status != GoNoGoStatus.GO:
            raise PostgresSchemaPlanError("migration go requires go_no_go_status=go")
        if normalized_go and not (normalized_backup and normalized_restore and normalized_rollback and normalized_compare):
            raise PostgresSchemaPlanError(
                "migration go requires backup_ref, restore_ref, rollback_plan, and read_only_compare=True"
            )
        return cls(
            truth_store=normalized_truth_store,
            migration_run_id=_normalize_slug(migration_run_id, field_name="migration_run_id"),
            schema_version=_normalize_slug(schema_version, field_name="schema_version"),
            backup_ref=normalized_backup,
            restore_ref=normalized_restore,
            rollback_plan=normalized_rollback,
            index_run_id=_normalize_slug(index_run_id, field_name="index_run_id"),
            query_cache_policy=_normalize_text(
                query_cache_policy,
                field_name="query_cache_policy",
                allow_empty=False,
                limit=_MAX_LONG_TEXT,
            ),
            go_no_go_status=normalized_status,
            count_evidence=count_evidence,
            read_only_compare=normalized_compare,
            go=normalized_go,
        )


@dataclass(frozen=True, slots=True)
class PostgresSchemaPlan:
    plan_id: str
    tables: tuple[SchemaTable, ...]
    indexes: tuple[SchemaIndex, ...]
    readiness: MigrationReadiness
    rebuild_flags: tuple[str, ...]
    backup_flags: tuple[str, ...]
    summary: str

    @classmethod
    def create(
        cls,
        *,
        plan_id: Any,
        tables: Iterable[SchemaTable],
        indexes: Iterable[SchemaIndex],
        readiness: MigrationReadiness,
        rebuild_flags: Iterable[Any] = (),
        backup_flags: Iterable[Any] = (),
        summary: Any = "",
    ) -> "PostgresSchemaPlan":
        normalized_tables = tuple(tables)
        normalized_indexes = tuple(indexes)
        if not normalized_tables:
            raise PostgresSchemaPlanError("tables must not be empty")
        if any(not isinstance(table, SchemaTable) for table in normalized_tables):
            raise PostgresSchemaPlanError("tables must contain SchemaTable items")
        if any(not isinstance(index_item, SchemaIndex) for index_item in normalized_indexes):
            raise PostgresSchemaPlanError("indexes must contain SchemaIndex items")
        if not isinstance(readiness, MigrationReadiness):
            raise PostgresSchemaPlanError("readiness must be a MigrationReadiness")

        table_map = {table.table_name: table for table in normalized_tables}
        if len(table_map) != len(normalized_tables):
            raise PostgresSchemaPlanError("table_name must be unique")
        index_map = {index_item.index_id: index_item for index_item in normalized_indexes}
        if len(index_map) != len(normalized_indexes):
            raise PostgresSchemaPlanError("index_id must be unique")

        for index_item in normalized_indexes:
            if index_item.table_name not in table_map:
                raise PostgresSchemaPlanError("indexes must target known tables")

        for table in normalized_tables:
            if table.schema_role not in {SchemaRole.TRUTH, SchemaRole.DERIVED, SchemaRole.CACHE}:
                raise PostgresSchemaPlanError("tables must use truth, derived, or cache roles")

        chunk_embeddings = table_map.get("chunk_embeddings")
        if chunk_embeddings is None:
            raise PostgresSchemaPlanError("chunk_embeddings table is required")
        if not chunk_embeddings.vector_columns:
            raise PostgresSchemaPlanError("chunk_embeddings requires a pgvector column")
        if not any(
            index_item.table_name == "chunk_embeddings" and index_item.vector_index
            for index_item in normalized_indexes
        ):
            raise PostgresSchemaPlanError("chunk_embeddings requires a vector index")

        if not any(
            index_item.table_name == "relations"
            and set(index_item.columns) >= {"source_id", "target_id", "relation_type"}
            for index_item in normalized_indexes
        ):
            raise PostgresSchemaPlanError("relations requires a source/target/type traversal index")

        provenance = table_map.get("provenance")
        if provenance is None:
            raise PostgresSchemaPlanError("provenance table is required")
        if not ({"source_id", "chunk_id"} & set(provenance.columns)):
            raise PostgresSchemaPlanError("provenance requires source or chunk reference columns")

        query_cache = table_map.get("query_cache")
        if query_cache is None:
            raise PostgresSchemaPlanError("query_cache table is required")
        if query_cache.ttl_seconds <= 0 and not query_cache.invalidation_policy:
            raise PostgresSchemaPlanError("query_cache requires TTL or invalidation policy")

        return cls(
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            tables=tuple(sorted(normalized_tables, key=lambda table: table.table_name)),
            indexes=tuple(sorted(normalized_indexes, key=lambda index_item: index_item.index_id)),
            readiness=readiness,
            rebuild_flags=_normalize_text_list(rebuild_flags, field_name="rebuild_flag"),
            backup_flags=_normalize_text_list(backup_flags, field_name="backup_flag"),
            summary=_normalize_text(summary, field_name="summary", allow_empty=True, limit=_MAX_LONG_TEXT),
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "table_count": len(self.tables),
            "index_count": len(self.indexes),
            "vector_table_count": sum(1 for table in self.tables if table.vector_columns),
            "readiness": {
                "truth_store": self.readiness.truth_store,
                "go_no_go_status": self.readiness.go_no_go_status.value,
                "go": self.readiness.go,
                "read_only_compare": self.readiness.read_only_compare,
                "has_backup_ref": bool(self.readiness.backup_ref),
                "has_restore_ref": bool(self.readiness.restore_ref),
                "has_rollback_plan": bool(self.readiness.rollback_plan),
            },
            "migration_counts": {
                "sources": self.readiness.count_evidence.source_count,
                "chunks": self.readiness.count_evidence.chunk_count,
                "embeddings": self.readiness.count_evidence.embedding_count,
                "entities": self.readiness.count_evidence.entity_count,
                "relations": self.readiness.count_evidence.relation_count,
                "provenance": self.readiness.count_evidence.provenance_count,
            },
            "role_counts": {
                role.value: sum(1 for table in self.tables if table.schema_role == role)
                for role in SchemaRole
            },
            "vector_indexes": sum(1 for index_item in self.indexes if index_item.vector_index),
        }
