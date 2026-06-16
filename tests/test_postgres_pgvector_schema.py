from src.postgres_pgvector_schema import (
    GoNoGoStatus,
    MigrationCountEvidence,
    MigrationReadiness,
    PgVectorColumn,
    PostgresSchemaPlan,
    PostgresSchemaPlanError,
    SchemaIndex,
    SchemaRole,
    SchemaTable,
)


def _make_vector_column(**overrides) -> PgVectorColumn:
    payload = {
        "column_name": "embedding",
        "dimensions": 1536,
        "distance_metric": "cosine",
    }
    payload.update(overrides)
    return PgVectorColumn.create(**payload)


def _make_table(name: str, **overrides) -> SchemaTable:
    defaults = {
        "schema_role": "truth",
        "primary_key": "id",
        "columns": ["id", "name"],
        "vector_columns": (),
        "rebuild_required": False,
        "backup_required": False,
        "ttl_seconds": 0,
        "invalidation_policy": "",
    }
    if name == "chunk_embeddings":
        defaults["schema_role"] = "derived"
        defaults["columns"] = ["id", "chunk_id", "embedding"]
        defaults["vector_columns"] = (_make_vector_column(),)
        defaults["rebuild_required"] = True
    elif name == "relations":
        defaults["columns"] = ["id", "source_id", "target_id", "relation_type"]
    elif name == "provenance":
        defaults["columns"] = ["id", "source_id", "chunk_id", "note"]
    elif name == "query_cache":
        defaults["schema_role"] = "cache"
        defaults["columns"] = ["id", "query_hash", "payload"]
        defaults["ttl_seconds"] = 3600
    payload = {"table_name": name, **defaults}
    payload.update(overrides)
    return SchemaTable.create(**payload)


def _make_index(index_id: str, table_name: str, columns, **overrides) -> SchemaIndex:
    payload = {
        "index_id": index_id,
        "table_name": table_name,
        "columns": columns,
        "index_kind": "btree",
        "vector_index": False,
    }
    payload.update(overrides)
    return SchemaIndex.create(**payload)


def _make_readiness(**overrides) -> MigrationReadiness:
    payload = {
        "truth_store": "Postgres plus pgvector",
        "migration_run_id": "migration-2026-06-16",
        "schema_version": "schema-v1",
        "backup_ref": "backup-2026-06-16",
        "restore_ref": "restore-runbook-2026-06-16",
        "rollback_plan": "restore backup and compare reads",
        "index_run_id": "index-run-1",
        "query_cache_policy": "invalidate after cutover",
        "go_no_go_status": "go",
        "count_evidence": MigrationCountEvidence.create(
            source_count=10,
            chunk_count=20,
            embedding_count=20,
            entity_count=6,
            relation_count=4,
            provenance_count=20,
        ),
        "read_only_compare": True,
        "go": True,
    }
    payload.update(overrides)
    return MigrationReadiness.create(**payload)


def _make_plan(**overrides) -> PostgresSchemaPlan:
    tables = [
        _make_table("source_providers"),
        _make_table("sources"),
        _make_table("source_versions"),
        _make_table("chunks"),
        _make_table("chunk_embeddings"),
        _make_table("entities"),
        _make_table("relations"),
        _make_table("provenance"),
        _make_table("index_runs", schema_role="derived"),
        _make_table("automation_runs", schema_role="derived"),
        _make_table("review_items"),
        _make_table("query_cache"),
        _make_table("graph_snapshots", schema_role="derived"),
    ]
    indexes = [
        _make_index("provider-path", "source_providers", ["name", "path"]),
        _make_index("source-hash", "sources", ["content_hash"]),
        _make_index("source-status", "sources", ["status"]),
        _make_index("chunk-source", "chunks", ["source_id"]),
        _make_index("entity-name-type", "entities", ["name", "entity_type"]),
        _make_index("relation-traversal", "relations", ["source_id", "target_id", "relation_type"]),
        _make_index("provenance-source-chunk", "provenance", ["source_id", "chunk_id"]),
        _make_index("chunk-embedding-vector", "chunk_embeddings", ["embedding"], index_kind="ivfflat", vector_index=True),
    ]
    payload = {
        "plan_id": "pgvector-plan",
        "tables": tables,
        "indexes": indexes,
        "readiness": _make_readiness(),
        "rebuild_flags": ["chunk_embeddings rebuild"],
        "backup_flags": ["logical backup required"],
        "summary": "Future schema plan for Postgres and pgvector.",
    }
    payload.update(overrides)
    return PostgresSchemaPlan.create(**payload)


def test_valid_postgres_schema_plan_normalizes_stably() -> None:
    plan = _make_plan(plan_id=" PgVector Plan ")

    assert plan.plan_id == "pgvector-plan"
    assert plan.tables[0].table_name == "automation_runs"
    assert any(table.table_name == "chunk_embeddings" for table in plan.tables)
    assert plan.readiness.go is True
    assert plan.readiness.go_no_go_status is GoNoGoStatus.GO


def test_missing_primary_key_is_rejected() -> None:
    try:
        _make_table("sources", primary_key="missing_id", columns=["id", "name"])
    except PostgresSchemaPlanError as exc:
        assert "primary_key must be present" in str(exc)
    else:
        raise AssertionError("expected primary key validation to fail")


def test_chunk_embeddings_without_pgvector_or_vector_index_is_rejected() -> None:
    try:
        _make_plan(
            tables=[
                table if table.table_name != "chunk_embeddings" else _make_table("chunk_embeddings", vector_columns=())
                for table in _make_plan().tables
            ]
        )
    except PostgresSchemaPlanError as exc:
        assert "pgvector column" in str(exc)
    else:
        raise AssertionError("expected chunk_embeddings vector validation to fail")

    try:
        _make_plan(
            indexes=[index for index in _make_plan().indexes if index.table_name != "chunk_embeddings"]
        )
    except PostgresSchemaPlanError as exc:
        assert "vector index" in str(exc)
    else:
        raise AssertionError("expected vector index validation to fail")


def test_relations_without_traversal_index_is_rejected() -> None:
    try:
        _make_plan(
            indexes=[index for index in _make_plan().indexes if index.table_name != "relations"]
        )
    except PostgresSchemaPlanError as exc:
        assert "traversal index" in str(exc)
    else:
        raise AssertionError("expected relations index validation to fail")


def test_provenance_without_source_or_chunk_reference_is_rejected() -> None:
    try:
        _make_plan(
            tables=[
                table
                if table.table_name != "provenance"
                else _make_table("provenance", columns=["id", "note"])
                for table in _make_plan().tables
            ]
        )
    except PostgresSchemaPlanError as exc:
        assert "requires source or chunk reference" in str(exc)
    else:
        raise AssertionError("expected provenance reference validation to fail")


def test_query_cache_without_ttl_or_invalidation_is_rejected() -> None:
    try:
        _make_plan(
            tables=[
                table
                if table.table_name != "query_cache"
                else _make_table("query_cache", ttl_seconds=0, invalidation_policy="")
                for table in _make_plan().tables
            ]
        )
    except PostgresSchemaPlanError as exc:
        assert "requires TTL or invalidation policy" in str(exc)
    else:
        raise AssertionError("expected query_cache policy validation to fail")


def test_migration_readiness_without_backup_rollback_compare_blocks_go() -> None:
    try:
        _make_readiness(backup_ref="", restore_ref="", rollback_plan="", read_only_compare=False, go=True)
    except PostgresSchemaPlanError as exc:
        assert "migration go requires" in str(exc)
    else:
        raise AssertionError("expected readiness validation to fail")


def test_migration_readiness_requires_postgres_pgvector_truth_and_go_status() -> None:
    try:
        _make_readiness(truth_store="qdrant only")
    except PostgresSchemaPlanError as exc:
        assert "Postgres plus pgvector" in str(exc)
    else:
        raise AssertionError("expected truth_store validation to fail")

    try:
        _make_readiness(go_no_go_status="review", go=True)
    except PostgresSchemaPlanError as exc:
        assert "go_no_go_status=go" in str(exc)
    else:
        raise AssertionError("expected go status validation to fail")


def test_negative_migration_count_evidence_is_rejected() -> None:
    try:
        MigrationCountEvidence.create(
            source_count=1,
            chunk_count=1,
            embedding_count=-1,
            entity_count=1,
            relation_count=1,
            provenance_count=1,
        )
    except PostgresSchemaPlanError as exc:
        assert "embedding_count must be >= 0" in str(exc)
    else:
        raise AssertionError("expected count validation to fail")


def test_audit_summary_contains_table_index_counts_and_readiness_without_sql_dumps() -> None:
    plan = _make_plan(summary="schema sql " + ("x" * 500))

    summary = plan.audit_summary()

    assert summary["plan_id"] == "pgvector-plan"
    assert summary["table_count"] == 13
    assert summary["index_count"] == 8
    assert summary["vector_table_count"] == 1
    assert summary["readiness"]["go"] is True
    assert summary["readiness"]["go_no_go_status"] == "go"
    assert summary["readiness"]["has_restore_ref"] is True
    assert summary["migration_counts"]["embeddings"] == 20
    assert summary["vector_indexes"] == 1
    assert "sql" not in str(summary).lower()
    assert "x" * 200 not in str(summary)
