# Postgres + pgvector Migration Contract

Stand: 2026-06-16

Status: Alice product, ops, and Charlie contract for `MS4A-postgres-pgvector-migration-contract`

Related roadmap slices:

- `MS1-store-interfaces`
- `MS2-diagnostics-layer`
- `MS3-query-budgets`
- `MS4-postgres-pgvector-schema`

## Purpose

This contract defines how Odysseus should explain, review, and later approve a migration toward a Postgres plus pgvector memory foundation without claiming that the migration already exists.

The goal of `MS4A` is language discipline and migration readiness:

- Postgres becomes the planned truth store for structured memory, graph, jobs, review, and provenance data.
- pgvector is not a side accelerator in this phase. It is part of the same Postgres truth for embeddings.
- Charlie gets a clear Go/No-Go vocabulary for future migration gates.
- Bob gets a minimum schema-model handoff without forcing a runtime switch.

This slice does not introduce a database, perform a migration, or enable dual-write.

## Contract Terms

### Core identity

- `truth_store`: The future canonical persistence layer. For the migration target this means Postgres, including pgvector-backed embedding columns and indexes that belong to the same database truth.
- `migration_run_id`: Stable identifier for one migration rehearsal, dry review, or future execution attempt.
- `schema_version`: Version marker for the target Postgres schema contract that a migration run claims to satisfy.
- `index_run_id`: Identifier for embedding or secondary index creation work tied to a migration or rebuild attempt.
- `go_no_go_status`: Explicit readiness decision for a migration step. Allowed values are `draft`, `review`, `go`, `no_go`, `rolled_back`, `superseded`.

### Backup and restore

- `backup_ref`: Reference to the backup set that protects the pre-migration truth. This may later point to files, snapshots, or export bundles, but in this contract it is only a required evidence reference.
- `restore_ref`: Reference to the documented restore path that can bring the protected truth back into service.
- `rollback_plan`: Human-readable and machine-referenceable description of how Charlie or an operator aborts a migration attempt and returns to the protected truth.

### Count evidence

- `source_count`: Number of source records expected in the migration comparison scope.
- `chunk_count`: Number of chunk records expected in the migration comparison scope.
- `embedding_count`: Number of embedding records expected in the migration comparison scope.
- `entity_count`: Number of graph entity records expected in the migration comparison scope.
- `relation_count`: Number of graph relation records expected in the migration comparison scope.
- `provenance_count`: Number of provenance records expected in the migration comparison scope.

### Cache behavior

- `query_cache_policy`: Declares whether query caches are persisted, rebuildable, disabled during migration, or invalidated after cutover. Query cache data is never treated as primary truth.

## Truth Model

For the target architecture, Postgres is the single truth store for persistent application data.

That means:

- source, chunk, graph, job, review, provenance, and cache metadata live under one canonical Postgres persistence model
- embeddings are part of that same truth model
- pgvector is a Postgres extension-level capability used to store and query embedding vectors inside the canonical database
- no separate vector database is implied by this contract

This contract intentionally rejects a split wording where "Postgres is truth but vectors live somewhere else by default." In `MS4`, embeddings remain part of the same truth boundary.

## Derived and rebuildable data

Even when stored in Postgres, not all data has the same role:

- sources, chunks, graph facts, job records, review records, and provenance are protected truth data
- embeddings are truth once committed to the canonical Postgres target because retrieval quality depends on them
- secondary indexes are rebuildable structures, even when essential for performance
- query caches remain derived and disposable under `query_cache_policy`

Charlie should later require the contract or schema to declare which structures are truth, which are rebuildable, and which may be safely dropped and regenerated.

## User and Ops View

From a user and operator perspective, the migration explanation must answer three questions:

1. What data are we protecting before any cutover?
2. How would we restore service if the migration fails?
3. How do we compare old and new state without silently switching production behavior?

The protected scope must at minimum cover:

- source records
- chunk records
- embedding records
- entity and relation records
- provenance records
- job and review state that would matter for continuity
- schema metadata required to identify `schema_version`

## Backup expectations

This contract does not prescribe a backup technology. It does require that every future migration plan produce a `backup_ref` with enough evidence to answer:

- what was backed up
- when it was backed up
- which schema or source state it corresponds to
- who verified that the backup is usable

If those answers are missing, the migration remains `no_go`.

## Restore expectations

`restore_ref` must point to a restore procedure that explains:

- how to recover the protected truth
- whether recovery is full or scoped
- what evidence proves recovery completed
- which counts or validations confirm the restored state

Restore is not optional readiness theater. If restore is undefined or unverifiable, Charlie must block migration approval.

## Read-only comparison

This contract uses "read-only comparison" for the stage where Odysseus compares source truth and target truth evidence without routing live writes or queries through the target path.

Read-only comparison means:

- the target dataset may be inspected
- counts, schema shape, and sample validations may be compared
- no runtime switch is implied
- no dual-write promise is implied
- mismatches must stay visible as evidence, not patched away by wording

## Charlie Go/No-Go Rules

Charlie should treat future migration readiness as a gate, not as a hopeful implementation milestone.

Charlie may only move a migration step toward `go` when all of the following are true:

- `truth_store` is explicitly declared as Postgres including pgvector embeddings
- `schema_version` is identified
- `backup_ref` exists and is reviewable
- `restore_ref` exists and is reviewable
- `rollback_plan` exists and is concrete
- comparison counts are present for sources, chunks, embeddings, entities, relations, and provenance
- `query_cache_policy` is explicit
- a read-only comparison path exists for review before any runtime switch

Charlie must stop and mark `no_go` when any of the following are true:

- truth ownership is ambiguous between Postgres and another store
- embeddings are described as authoritative but live outside the declared truth boundary
- backup or restore evidence is missing
- count evidence is absent, contradictory, or unexplained
- rollback is vague, manual only in name, or depends on hidden state
- the plan assumes dual-write without a separately approved contract
- migration wording implies production cutover before comparison gates are green

## Failure and Evidence Language

The migration contract should align with earlier tool-truth, diagnostics, and query-budget work:

- no silent success claims
- no "done" language without evidence references
- no performance claims without bounded diagnostics
- no completeness claims if counts are partial, clipped, stale, or unverified

Recommended evidence bundle for a future migration review:

- schema contract reference
- `backup_ref`
- `restore_ref`
- count comparison table
- index or rebuild evidence via `index_run_id`
- cache handling statement via `query_cache_policy`
- explicit `go_no_go_status`

## Non-Goals

This slice does not:

- introduce a live Postgres runtime
- create or run a migration
- define dual-write behavior
- switch Odysseus reads or writes to a new database
- add Docker Compose or deployment files
- implement import or export logic
- implement Qdrant, Kuzu, UMAP, or GMM paths

## Handoff to Bob

Bob's `MS4B-postgres-pgvector-schema-model-spike` should minimally model or validate the following fields:

- `truth_store`
- `schema_version`
- `migration_run_id`
- `backup_ref`
- `restore_ref`
- `rollback_plan`
- `index_run_id`
- `query_cache_policy`
- `go_no_go_status`
- `source_count`
- `chunk_count`
- `embedding_count`
- `entity_count`
- `relation_count`
- `provenance_count`

Bob's schema model should also answer these minimum structure questions:

- Which tables are canonical for sources, chunks, embeddings, entities, relations, provenance, jobs, reviews, and cache metadata?
- Which vector-bearing structures belong to the same Postgres truth boundary?
- Which indexes are required for correctness review versus performance only?
- Which records are protected truth, and which are rebuildable?
- Which validations can compare old and target counts without requiring runtime cutover?

## Handoff to Charlie

When Charlie later slices `MS5-import-export-migration-proof`, this contract should be used to require:

- a concrete backup and restore evidence chain
- a count comparison proof across the declared truth entities
- explicit `go_no_go_status` transitions
- a clear separation between contract readiness, dry review, and real cutover approval

`MS4A` is complete when migration language stays honest: prepared, reviewable, and bounded, but not falsely presented as already implemented.
