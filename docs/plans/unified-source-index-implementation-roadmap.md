# Unified Source Index Implementation Roadmap

Stand: 2026-07-13

Status: `USI-09_claimed_after_TAX_handoff / USI-13_dependency_blocked / runtime_default_off`

Master-Track: `0.28.x`, `OWM-15`, `L21`

## 1. Goal

Odysseus receives one persistent, source-neutral index control plane for code,
documents, personal memory, planning, inbox items and later communication
sources. Every indexed occurrence can be traced to an owner-scoped source,
an immutable source version, an exact locator and the extraction evidence that
created it.

The track is done when:

- stable source, version, chunk, entity, relation, lineage and derived-run IDs
  exist independently of SQLite, Chroma or a code engine;
- SQLite stores those records transactionally with bounded FTS5 queries;
- ingestion is incremental, resumable and deletion-aware;
- Chroma and later accelerators contain only rebuildable projections keyed by
  USI references;
- one bounded query service combines lexical, semantic, symbol, graph and
  timeline retrieval without adding parallel tool families;
- Context Provider, Context Transparency and answer provenance receive exact
  USI references;
- RAPTOR reads immutable USI snapshots and writes only evidence-bound derived
  runs;
- existing RAG and memory indexes are compared read-only before any cutover;
- no source domain loses its existing truth, policy or write workflow;
- productive indexing remains disabled until one final activation packet is
  reviewed.

## 2. Current Evidence

- `docs/plans/unified-source-index-architecture-contract.md` freezes the USI1
  truth model, IDs, policies and no-duplication rules.
- `docs/plans/unified-source-index-open-source-evaluation.md` freezes the USI1B
  sourcing boundary: Odysseus owns identity and provenance while engines stay
  replaceable.
- `docs/plans/memory-store-interface-contract.md` already names `SourceStore`,
  `ChunkStore`, `EmbeddingStore`, `GraphStore`, `JobStore`, `ReviewStore` and
  `QueryCacheStore`, but no common runtime implementation exists yet.
- `src/rag_text_chunking.py` and the completed RMT roadmap provide the generic
  structure-aware text splitter and chunking evidence.
- Chroma/FastEmbed, Personal Docs, Memory, Obsidian/Lens and RaptorGraph each
  have useful data, but currently use different identities and lifecycle
  boundaries.
- Project Versioning and the Local Forge already own project/revision facts.
- TAX/TUA own tool identity and usage telemetry. GRO owns the Prometheus and
  Grafana runtime observation plane. GMI owns local Gemma maintenance runtime.
- `docs/plans/unified-source-index-integration-impact-map.md` classifies the
  concrete startup, consumer, domain and data-lifecycle code paths.
- UIR, UDA and ULO are child closure tracks for runtime wiring, real source
  adapters and operational lifecycle. They share this track's activation gate
  and do not create new truth systems or product gates.
- The fixed ten-roadmap MVP remains complete; this is a post-MVP product track.

## 3. Mode And Queue Policy

Planning mode is `Standard ABC`. After an explicit implementation goal, all
repository-only slices may run in `Overnight Backend Mode` under their stated
dependencies and paths.

Queue rules:

1. This roadmap does not displace the active TAX/TUA queue or the separately
   planned GMI/GRO tracks.
2. On goal start, only `USI-00` becomes claimable. Successors stay
   `blocked_by_dependency`.
3. Charlie is the only writer for master roadmaps, migration state and shared
   schema decisions.
4. Bob owns store/query implementation. Alice owns contracts, operator wording
   and comparison reports. Their write paths must remain disjoint.
5. There is no user gate for contracts, fixtures, local temporary databases,
   tests, benchmarks, dry-run migration plans or synthetic staging.
6. Exactly one user gate, `USI-LIVE-ACTIVATION`, may materialize after
   `USI-00` through `USI-15`, `UIR-00` through `UIR-14`, `ULO-00` through
   `ULO-14` and `UDA-18` for the selected source scopes are accepted.

## 4. Canonical Ownership And No-Duplication Matrix

| Concern | Canonical owner | USI responsibility | Explicit non-responsibility |
| --- | --- | --- | --- |
| Personal facts and memories | `MemoryManager` plus memory lifecycle | index approved records and preserve source refs | no automatic memory creation |
| Planning projects and roadmaps | Planning stores/MCP | read-only source adapter and derived retrieval | no second plan store |
| Repository identity and commits | Repo Registry, Git Adapter, Project Version Store, Local Forge | reference repo/version facts | no repo registration or commit workflow |
| Generic chunks | `rag_text_chunking` plus USI `ChunkStore` | stable occurrence identity and locator | no second generic splitter family |
| Code graph | CBM integration roadmap | consume/query a rebuildable code projection through USI refs | no in-core parser fork in this track |
| Code history | Git and Code Lineage roadmap | persist evidence-bound lineage records | no invented commit history |
| Semantic vectors | existing Chroma/FastEmbed lane | projection manifest and rebuild mapping | Chroma IDs are not chunk IDs |
| RAPTOR/GraphRAG | existing derived-run and maintenance contracts | immutable input snapshots and evidence refs | no second maintenance scheduler |
| Runtime metrics | GRO registry and exporter | emit bounded spans into that registry | no second Prometheus exporter |
| Model maintenance | GMI | submit bounded derived tasks after later integration | no model routing or scheduler |
| Tool identity | TAX Descriptor v2 | one `query_knowledge` projection | no direct engine-specific tool family |
| Usage analytics | TUA | one canonical invocation plus internal spans | no duplicate per-engine invocation ledger |
| Runtime explanation | AI Lens/Context Transparency | source-linked selection events | no reuse of index graph as runtime trace |
| User code visualization | Lens Code Graph roadmap | bounded graph API and source refs | no separate top-level UI shell |

## 5. Target Architecture

```text
Domain truth / provider
  -> SourceAdapter discovery and immutable SourceVersion
  -> policy-aware extraction
  -> USI SQLite truth
       Source / Version / Chunk / Entity / Relation / Lineage / Job
       FTS5 / manifests / tombstones / derived-run metadata
  -> rebuildable projections
       Chroma embeddings
       CBM code graph
       RAPTOR clusters and summaries
  -> bounded QueryPlanner
  -> ContextItem + AnswerProvenance + AI Lens observation
  -> read_file or domain reader for exact raw evidence
```

The first SQLite implementation may use one database with isolated tables or
one USI-owned database file. Engine-owned databases are allowed only as
rebuildable projections with explicit manifests. A projection failure must not
invalidate source/version/chunk truth.

## 6. Frozen Data And Query Contracts

Minimum record families:

- `SourceRecord`: stable logical source, owner scope, kind, canonical ref,
  classification and content policy;
- `SourceVersionRecord`: immutable revision/content observation with source,
  provider and observed timestamps;
- `ChunkRecord`: one occurrence in one source version with exact typed locator;
- `EntityRecord`: symbol, person, task, document section or domain entity tied
  to evidence;
- `RelationRecord`: typed edge with method, confidence and evidence refs;
- `LineageRecord`: prior/current occurrence relation with reason and
  confidence;
- `ProjectionManifest`: engine/version/config/input snapshot/output generation;
- `DerivedRunRecord`: RAPTOR/cluster/summary run over immutable inputs;
- `IndexJobRecord`: resumable discovery, extraction, projection, deletion or
  rebuild state;
- `QueryResultItem`: score contributions, source/version/locator refs,
  clipping and stale state.

Every list/query contract is bounded. Depending on mode it accepts `limit`,
`cursor`, `time_budget_ms`, `token_budget`, `max_nodes`, `max_edges`, `depth`,
`source_scope`, `owner_scope`, `classification_ceiling` and `stale_after`.

## 7. Slice Queue

### USI-00 - Reconciliation Baseline And Ownership Freeze

- Class: `safe_offline`
- Owner: Charlie
- Status: `accepted_2026-07-13`
- Dependencies: explicit goal; current worktree and active claims inspected
- Allowed paths:
  - `docs/plans/unified-source-index-implementation-roadmap.md`
  - `docs/plans/unified-source-index-runtime-inventory.json`
  - `scripts/audit_unified_source_index_overlap.py`
  - `tests/test_audit_unified_source_index_overlap.py`
- Work:
  - inventory current source/chunk/vector/graph/job stores and direct writers;
  - classify each as truth, index truth, projection, observation or legacy;
  - fail on an ownerless store, duplicate tool identity or undocumented writer;
  - record current TAX/TUA/GMI/GRO/Project-Versioning hotfiles.
- Tests:
  - `python -m pytest -q tests/test_audit_unified_source_index_overlap.py`
  - `python scripts/audit_unified_source_index_overlap.py --check`
- Done when: one content-free inventory proves the ownership matrix and names
  every migration candidate without reading private corpus content.

### USI-01 - Backend-Neutral Records, IDs And Policy Propagation

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-13`
- Acceptance: `21 focused tests; combined USI/roadmap suite 45 passed`
- Dependencies: `USI-00`
- Allowed paths:
  - `src/unified_source_index_contract.py`
  - `tests/test_unified_source_index_contract.py`
- Work:
  - strict dataclasses/enums and canonical serialization;
  - occurrence IDs distinct from content hashes;
  - owner/classification/content-policy propagation;
  - typed locators for text, page, row, message and code ranges;
  - invalid or incomplete evidence fails closed.
- Tests: `python -m pytest -q tests/test_unified_source_index_contract.py`
- Done when: deterministic fixtures cover identical content in different
  sources/positions, invalid scopes and stable round trips.

### USI-02 - Store Protocols And Transaction Boundary

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `9 focused tests; combined USI/overlap/routing suite 43 passed`
- Released claim:
  - run_id: `usi-02-20260717t075209`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T07:52:09+02:00`
  - lease_expires_at: `2026-07-17T09:52:09+02:00`
  - released_at: `2026-07-17T08:30:47+02:00`
  - allowed_paths: `src/unified_source_index_stores.py`,
    `tests/test_unified_source_index_stores.py`, and this status block
  - handoff_required: `false`
  - evidence: snapshot isolation, atomic explicit writes, global and per-record
    optimistic concurrency, owner-scoped bounded cursors, content-free
    tombstones, explicit restore and all USI-01 record families tested
  - next_frontier: `USI-03`
- Dependencies: `USI-01`
- Allowed paths:
  - `src/unified_source_index_stores.py`
  - `tests/test_unified_source_index_stores.py`
- Work:
  - implement the existing Store Interface vocabulary as Python protocols;
  - define snapshot/read/write transaction boundaries and optimistic versions;
  - define tombstone, cursor and compare-and-swap semantics;
  - keep backend types out of domain records.
- Tests: `python -m pytest -q tests/test_unified_source_index_stores.py`
- Done when: an in-memory fake proves all protocols and failure semantics.

### USI-03 - SQLite Schema, Migrations And Store Implementation

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `14 focused migration/SQLite tests; combined USI/overlap/routing suite 57 passed`
- Released claim:
  - run_id: `usi-03-20260717t113405`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T11:34:05+02:00`
  - lease_expires_at: `2026-07-17T14:34:05+02:00`
  - released_at: `2026-07-17T11:49:34+02:00`
  - allowed_paths: `src/unified_source_index_sqlite.py`,
    `src/unified_source_index_migrations.py`,
    `tests/test_unified_source_index_sqlite.py`,
    `tests/test_unified_source_index_migrations.py`, and this status block
  - handoff_required: `false`
  - evidence: isolated versioned schema, WAL/foreign-key/busy-timeout gates,
    typed indexed truth tables, append-only snapshot history, atomic optimistic
    writes, rollback/reopen proof, tombstone restore, owner-scoped cursors and
    FTS5 trigger/rebuild coverage without a real user database
  - next_frontier: `USI-04` and `USI-05`
- Dependencies: `USI-02`
- Allowed paths:
  - `src/unified_source_index_sqlite.py`
  - `src/unified_source_index_migrations.py`
  - `tests/test_unified_source_index_sqlite.py`
  - `tests/test_unified_source_index_migrations.py`
- Work:
  - WAL, foreign keys, busy timeout, schema version and bounded transactions;
  - indexed source/version/chunk/entity/relation/lineage/job tables;
  - FTS5 shadow tables and trigger/rebuild strategy;
  - migration upgrade/downgrade proof on temporary databases;
  - no changes to global app migrations in this slice.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_sqlite.py`
  - `python -m pytest -q tests/test_unified_source_index_migrations.py`
- Done when: crash/reopen, rollback, FK, duplicate, cursor and migration tests
  pass without a real user database.

### USI-04 - Adapter And Resumable Index Job Runtime

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `13 focused adapter/job tests; combined USI/overlap/routing suite 70 passed; overlap inventory clean`
- Released claim:
  - run_id: `usi-04-20260717t115033`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T11:50:33+02:00`
  - lease_expires_at: `2026-07-17T14:50:33+02:00`
  - released_at: `2026-07-17T12:06:15+02:00`
  - allowed_paths: `src/unified_source_index_adapters.py`,
    `src/unified_source_index_jobs.py`,
    `tests/test_unified_source_index_adapters.py`,
    `tests/test_unified_source_index_jobs.py`, and this status block
  - handoff_required: `false`
  - evidence: bounded adapter capability/discovery/extraction/exact-read
    contracts, owner/classification/content-policy gates, content-free
    fingerprints and unavailable plans, durable checksum checkpoints, lease
    expiry/crash recovery, cancellation, retry and idempotent truth commits;
    projection failure retains truth and records explicit stale state
  - next_frontier: `USI-05` and `USI-06`
- Dependencies: `USI-02`, `USI-03`
- Allowed paths:
  - `src/unified_source_index_adapters.py`
  - `src/unified_source_index_jobs.py`
  - `tests/test_unified_source_index_adapters.py`
  - `tests/test_unified_source_index_jobs.py`
- Work:
  - discovery, fingerprint, extract, commit and projection stages;
  - leases, checkpoints, cancellation, retry and idempotency;
  - content-policy checks before reads and writes;
  - tombstone/delete propagation plan;
  - projection failures leave index truth committed and marked stale.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_adapters.py tests/test_unified_source_index_jobs.py`
- Done when: fake adapters survive retry/crash/cancel and never cross owner or
  classification boundaries.

### USI-05 - Lexical Retrieval And Query Result Contract

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `11 focused query/lexical tests; combined USI/overlap/routing suite 81 passed; overlap inventory clean`
- Released claim:
  - run_id: `usi-05-20260717t120737`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T12:07:37+02:00`
  - lease_expires_at: `2026-07-17T15:07:37+02:00`
  - released_at: `2026-07-17T12:14:08+02:00`
  - allowed_paths: `src/unified_source_index_lexical.py`,
    `src/unified_source_index_query_contract.py`,
    `tests/test_unified_source_index_lexical.py`,
    `tests/test_unified_source_index_query_contract.py`, and this status block
  - handoff_required: `false`
  - evidence: operator-free exact/token/prefix FTS compilation, bounded
    candidate reads without table scans, stable score/tie-break behavior,
    independently bounded snippets, owner/source/classification filtering,
    exact source-version-chunk-locator refs and explicit partial/clipped/stale
    result states with snapshot/query-bound cursors
  - next_frontier: `USI-06`
- Dependencies: `USI-03`
- Allowed paths:
  - `src/unified_source_index_lexical.py`
  - `src/unified_source_index_query_contract.py`
  - `tests/test_unified_source_index_lexical.py`
  - `tests/test_unified_source_index_query_contract.py`
- Work:
  - FTS5 exact/token/prefix query with safe parameterization;
  - stable score components and deterministic tie-breaks;
  - snippets bounded independently of stored content;
  - explicit partial/clipped/stale states and cursors.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_lexical.py tests/test_unified_source_index_query_contract.py`
- Done when: no query path performs an unbounded table scan and every result
  contains exact source/version/locator refs.

### USI-06 - Semantic Projection Manifest And Chroma Bridge

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `7 focused embedding tests; combined USI/overlap/routing suite 88 passed; overlap inventory clean`
- Released claim:
  - run_id: `usi-06-20260717t121449`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T12:14:49+02:00`
  - lease_expires_at: `2026-07-17T15:14:49+02:00`
  - released_at: `2026-07-17T12:21:52+02:00`
  - allowed_paths: `src/unified_source_index_embeddings.py`,
    `tests/test_unified_source_index_embeddings.py`, and this status block
  - handoff_required: `false`
  - evidence: occurrence-to-vector metadata mapping without raw content,
    model/config/generation separation, bounded batching and retry, atomic
    generation cutover, persisted projection manifests, explicit
    partial/stale/missing/count/unavailable health and lexical fail-open;
    fake Chroma delete/rebuild preserves USI truth and no live collection is
    read or mutated
  - next_frontier: `USI-07`
- Dependencies: `USI-01`, `USI-04`
- Allowed paths:
  - `src/unified_source_index_embeddings.py`
  - `tests/test_unified_source_index_embeddings.py`
- Work:
  - map USI chunk occurrences to current embedding lanes;
  - separate embedding generation/config from source identity;
  - batch, retry, stale generation and rebuild contracts;
  - fail-open to lexical/structural retrieval when Chroma is unavailable;
  - no live Chroma collection migration.
- Tests: `python -m pytest -q tests/test_unified_source_index_embeddings.py`
- Done when: fake Chroma deletion/rebuild leaves USI truth intact and generation
  drift is explicit.

### USI-07 - Federated Bounded Query Planner

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `9 focused planner tests; combined USI/overlap/routing suite 97 passed; overlap inventory clean`
- Released claim:
  - run_id: `usi-07-20260717t130952`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T13:09:52+02:00`
  - lease_expires_at: `2026-07-17T16:09:52+02:00`
  - released_at: `2026-07-17T13:23:01+02:00`
  - allowed_paths: `src/unified_source_index_query.py`,
    `tests/test_unified_source_index_query.py`, and this status block
  - handoff_required: `false`
  - evidence: fixed-order lexical/semantic/symbol/graph/timeline planning
    without an LLM, per-provider and total candidate/time budgets,
    occurrence-level fusion and dedupe with exact EvidenceRef preservation,
    transactional owner/classification/source filtering, content-free failure
    codes and honest partial/fallback states for missing, failed, late and
    budget-skipped providers; real SQLite lexical adapter exercised
  - next_frontier: `USI-08`
- Dependencies: `USI-05`, `USI-06`; structural provider may be a fake until
  the CBM roadmap reaches `CBM-05`
- Allowed paths:
  - `src/unified_source_index_query.py`
  - `tests/test_unified_source_index_query.py`
- Work:
  - lexical, semantic, symbol, graph and timeline provider interfaces;
  - plan selection, budgets, fusion, dedupe and evidence preservation;
  - deterministic fallback when one provider fails;
  - no LLM required for baseline planning.
- Tests: `python -m pytest -q tests/test_unified_source_index_query.py`
- Done when: provider failures and timeouts return honest partial results and
  never erase exact evidence refs.

### USI-08 - Context Provider And Answer Provenance Bridge

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `9 focused context/provenance tests; combined USI/context/Lens/overlap/routing suite 195 passed; overlap inventory clean`
- Released claim:
  - run_id: `usi-08-20260717t132402`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T13:24:02+02:00`
  - lease_expires_at: `2026-07-17T16:24:02+02:00`
  - released_at: `2026-07-17T13:33:02+02:00`
  - allowed_paths: `src/unified_source_index_context.py`,
    `tests/test_unified_source_index_context.py`, and this status block
  - handoff_required: `false`
  - evidence: bounded deterministic projection into existing ContextItem and
    AnswerPackSummary contracts, exact included-occurrence provenance pointers
    covering source/version/record/locator, normal-mode policy blocking and
    secure-local inclusion, explicit stale/excluded/clipped/truncated states,
    actual bounded AI Lens context/pack events and snippet-free diagnostics;
    no store read or source-content resolution occurs in the bridge
  - next_frontier: `USI-09` dependency audit, otherwise `USI-10`
- Dependencies: `USI-07`
- Allowed paths:
  - `src/unified_source_index_context.py`
  - `tests/test_unified_source_index_context.py`
- Work:
  - convert selected results to existing bounded Context Items;
  - project include/exclude/clip/stale events into Context Transparency and AI
    Lens observation contracts;
  - keep raw content out of diagnostic payloads;
  - ensure exact readers remain separate follow-up calls.
- Tests: `python -m pytest -q tests/test_unified_source_index_context.py`
- Done when: an answer pack can name every supporting source version and
  locator without exposing disallowed content.

### USI-09 - Canonical `query_knowledge` Tool Projection

- Class: `repo_only`
- Owner: Bob
- Status: `claimed_2026-07-23`
- Dependency audit: `USI-07 accepted; canonical Open-Work evidence marks TAX1/TAX5/TAX8 complete; four focused Descriptor-V2, security, dynamic-provider and USI-query dependency checks passed on the hydrated dev checkpoint`
- Serialized claim:
  - run_id: `abc-usi09-20260723T185258+0200`
  - thread_id: `/root`
  - owner: `Bob`
  - state: `claimed`
  - acquired_at: `2026-07-23T18:52:58+02:00`
  - lease_expires_at: `2026-07-23T22:52:58+02:00`
  - amended_at: `2026-07-23T18:57:04+02:00`
  - amendment_reason: `Adding the one canonical identity necessarily updates
    the frozen TAX0 catalog projections and deterministic inventory; the four
    added paths close only that bounded parity surface and were discovered
    before any product edit.`
  - worktree: `C:\tmp\odysseus-abc-usi09-20260723`
  - allowed_paths: `src/builtin_tool_catalog.py`, `src/tool_index.py`,
    `src/tool_schema_definitions.py`, `src/tool_execution.py`,
    `src/agent_tools/__init__.py`, new
    `src/agent_tools/knowledge_tools.py`, `src/tool_security.py`, and new
    `tests/test_query_knowledge_tool.py`,
    `scripts/audit_tool_registry_drift.py`,
    `docs/plans/tool-taxonomy-inventory.json`,
    `tests/test_audit_tool_registry_drift.py`, and
    `tests/test_builtin_tool_catalog.py`
  - excluded_paths: `src/tool_catalog.py`, `src/tool_registry.py`,
    `src/unified_source_index_query.py`, every app/runtime initializer,
    provider, MCP, database, migration, live and external path
  - handoff_required: `false`
  - evidence: `The UIX branch intentionally lacks USI dependencies; a clean
    dev-based worktree supplies the accepted USI-07 planner and TAX contracts
    without a broad cross-branch merge. Bob recon added builtin catalog and
    plan-mode security as required fail-closed projections.`
- Dependencies: `USI-07`, TAX1 identity, TAX5 security, TAX8 dynamic provider
  normalization
- Allowed paths:
  - `src/builtin_tool_catalog.py`
  - `src/tool_index.py`
  - `src/tool_schema_definitions.py`
  - `src/tool_execution.py`
  - `src/agent_tools/__init__.py`
  - `src/agent_tools/knowledge_tools.py`
  - `src/tool_security.py`
  - `tests/test_query_knowledge_tool.py`
  - `scripts/audit_tool_registry_drift.py`
  - `docs/plans/tool-taxonomy-inventory.json`
  - `tests/test_audit_tool_registry_drift.py`
  - `tests/test_builtin_tool_catalog.py`
- Work:
  - expose one read-only descriptor with domain/mode/scope/budget fields;
  - no direct CBM MCP tool registration;
  - exact source reads continue through `read_file` or a domain reader;
  - TUA sees one canonical invocation; internal provider spans remain metrics.
- Tests: `python -m pytest -q tests/test_query_knowledge_tool.py`; focused
  builtin-registration/security/audit parity; deterministic registry-drift
  snapshot check
- Done when: code, document and memory fixtures route through one descriptor
  with policy parity, no `Other` fallback and no TAX inventory drift.

These shared TAX paths are a single serialized integration claim after TAX
handoff; no USI worker may edit them independently.

### USI-10 - RAPTOR Derived-Run Adapter

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-17`
- Acceptance: `9 focused RAPTOR adapter tests; combined USI/context/Lens/overlap/routing suite 204 passed; overlap inventory clean`
- Released claim:
  - run_id: `usi-10-20260717t133422`
  - owner: `root` acting as Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T13:34:22+02:00`
  - lease_expires_at: `2026-07-17T16:34:22+02:00`
  - released_at: `2026-07-17T13:43:36+02:00`
  - allowed_paths: `src/unified_source_index_raptor.py`,
    `tests/test_unified_source_index_raptor.py`, and this status block
  - handoff_required: `false`
  - evidence: input-set-derived immutable snapshot identity, exact bounded
    EvidenceRef inputs, metadata-only DerivedRunRecord persistence, injected
    existing-worker submission with one bounded task and no scheduler,
    validated cluster/node/membership/summary lineage and quality evidence,
    added/changed/removed-only invalidation with unrelated commits ignored;
    delete/tombstone/rebuild restores the same run identity while preserving
    all non-derived in-memory and SQLite truth
  - next_frontier: `USI-11`
- Dependencies: `USI-04`, `USI-07`
- Allowed paths:
  - `src/unified_source_index_raptor.py`
  - `tests/test_unified_source_index_raptor.py`
- Work:
  - immutable input snapshots and derived run manifests;
  - cluster/node/membership/summary evidence refs;
  - invalidate by changed input set, not global full rebuild;
  - submit only bounded maintenance tasks to existing workers;
  - no new clustering or model scheduler.
- Tests: `python -m pytest -q tests/test_unified_source_index_raptor.py`
- Done when: deleting all derived runs and rebuilding from USI inputs changes
  no domain or index truth.

### USI-11 - Existing Source Compatibility Comparison

- Class: `repo_only`
- Owner: Alice for reports, Bob for comparison fixtures; serialized by Charlie
- Status: `accepted_2026-07-17`
- Acceptance: `15 focused comparison/CLI tests; combined USI/context/Lens/overlap/routing suite 219 passed; overlap inventory clean`
- Released claim:
  - run_id: `usi-11-20260717t134419`
  - owner: `root` acting as Alice/Bob, integrated by root/Charlie
  - state: `released`
  - acquired_at: `2026-07-17T13:44:19+02:00`
  - lease_expires_at: `2026-07-17T16:44:19+02:00`
  - released_at: `2026-07-17T13:50:57+02:00`
  - allowed_paths: `src/unified_source_index_legacy_comparison.py`,
    `scripts/compare_unified_source_index.py`,
    `tests/test_unified_source_index_legacy_comparison.py`,
    `tests/test_compare_unified_source_index.py`,
    `docs/plans/unified-source-index-migration-report.md`, and this status block
  - handoff_required: `false`
  - evidence: deterministic bounded synthetic observations for Personal Docs,
    current RAG, Memory and Obsidian/Lens; separate count/missing/orphan,
    locator, owner/classification/content-policy and content-hash parity gates;
    explicit adapt/adapt/keep/retire decisions, positive and independent
    negative fixtures, content-free JSON CLI/report and hard-coded proof that
    no corpus read, shadow request, dual write, active-path mutation or live
    cutover authorization occurs
  - next_frontier: `USI-12` dependency audit
- Dependencies: `USI-04`, `USI-07`
- Allowed paths:
  - `src/unified_source_index_legacy_comparison.py`
  - `scripts/compare_unified_source_index.py`
  - `tests/test_unified_source_index_legacy_comparison.py`
  - `tests/test_compare_unified_source_index.py`
  - `docs/plans/unified-source-index-migration-report.md`
- Work:
  - compare Personal Docs, current RAG chunks, Memory and Obsidian/Lens legacy
    reader output with synthetic UDA observations or bounded fake records;
  - counts, missing records, locator parity and policy parity;
  - comparison code is not a reusable production SourceAdapter and UIR-07 owns
    actual shadow-request wiring;
  - no dual-write and no active-path removal.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_legacy_comparison.py tests/test_compare_unified_source_index.py`
- Done when: each legacy path has an explicit keep/adapt/retire decision and a
  measurable cutover gate.

### USI-12 - Diagnostics And GRO Metrics Extension

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `12 focused diagnostics tests; 40 diagnostics/registry/exporter tests;
  15 Grafana/activation-packet tests; 8 overlap-inventory tests; final integrated
  USI/GRO/GMI readback 207 passed with one unrelated SQLAlchemy deprecation warning`
- Released claim:
  - run_id: `post-mvp-usi-20260718T172132+0200`
  - owner: `root` acting as Bob/Charlie; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T17:21:32+02:00`
  - lease_expires_at: `2026-07-18T21:21:32+02:00`
  - released_at: `2026-07-18T17:41:19+02:00`
  - dependency_handoff: `GRO-00 through GRO-15 accepted offline_go; shared
    registry/exporter/dashboard files serialized to this claim`
  - allowed_paths: `src/unified_source_index_diagnostics.py`, focused USI
    diagnostics tests, the frozen GRO metrics contract, shared runtime metric
    registry/exporter tests, Grafana generator/validator/dashboard/tests,
    affected GRO/GMI offline readbacks, the USI overlap audit/inventory regression,
    this roadmap and the Open-Work master
  - preserved_foreign_hunks: all unrelated runtime, TAX/TUA, source-adapter,
    query-tool, host and live-system changes
  - live_actions: `false`
  - evidence: one fail-soft content-free adapter over the existing process-local
    GRO registry; five closed USI metric families; no generic `record_kind`
    label expansion; one deterministic eight-panel dashboard added to the
    default-off six-dashboard bundle; exporter/packet/GMI hash readbacks green;
    overlap inventory clean at 35 components, 103 writers and 17 tool identities;
    no productive read, index, model, network, service, secret or live action
  - artifact_hashes: diagnostics
    `52DD7CD70FBFDF2BEE1F67B7192684DDC9B1E1BCA245E6D6C71418B20ADB2D26`,
    focused tests
    `D4B3DD01FD48C1FFAD1EC7CECC4EB90741BE2E8EEE30023A7A564D3F670CD781`,
    shared registry
    `9FBFCF41DF26E4F9340630FC6B661D1158989ECDB483BF15327775971DE9B0DD`,
    exporter
    `8008DAA6818B5551D73798EC951231384EC7029D2DBA87287A816CB829760B8E`,
    dashboard
    `5B610A2DCF284C89D85549232204FD1E6DB21C8DFAFF68F7277037E5F71EE098`
  - next_frontier: `USI-09` is claimed after the TAX1/TAX5/TAX8 dependency
    handoff; USI-13 remains dependency-blocked until USI-09 has focused
    acceptance and a released claim
- Dependency audit: `USI-04=accepted; GRO-00 through GRO-15=offline_go;
  serialized GRO handoff acquired; USI-09 remains independently TAX-blocked`
- Dependencies: `USI-04`, GRO-00 metric contract; serialize shared GRO files
- Allowed paths:
  - `src/unified_source_index_diagnostics.py`
  - `tests/test_unified_source_index_diagnostics.py`
  - GRO-owned metrics files only after explicit handoff
- Work:
  - content-free counts, queue depth, stale projections, latency and failures;
  - extend the GRO registry, exporter and dashboards instead of creating a
    second metrics stack;
  - no source/path/owner/query labels.
- Tests: `python -m pytest -q tests/test_unified_source_index_diagnostics.py`
- Done when: diagnostics are bounded and exporter failure never blocks
  indexing or query results.

### USI-13 - Backup, Restore, Rebuild And Scale Evidence

- Class: `repo_only`
- Owner: Bob
- Status: `blocked_by_USI-09_in_progress_2026-07-23`
- Dependencies: `USI-03` through `USI-12`
- Allowed paths:
  - `src/unified_source_index_backup.py`
  - `scripts/benchmark_unified_source_index.py`
  - `tests/test_unified_source_index_backup.py`
  - `tests/test_unified_source_index_scale.py`
- Work:
  - consistent SQLite backup and restore to temporary targets;
  - rebuild all projections from index truth;
  - 100k+ LOC and million-record synthetic scale profiles;
  - p50/p95, index size, RAM, writer contention and recovery evidence;
  - Postgres remains a later measured gate.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_backup.py tests/test_unified_source_index_scale.py`
- Done when: backup/restore/rebuild count hashes match and bounded query SLOs
  are reported without arbitrary LOC-based migration claims.

### USI-14 - Security, Privacy And Failure Matrix

- Class: `repo_only`
- Owner: Charlie
- Status: `blocked_by_USI-12_and_USI-13_2026-07-17`
- Dependencies: `USI-01` through `USI-13`
- Allowed paths:
  - `tests/test_unified_source_index_security.py`
  - `tests/test_unified_source_index_failure_matrix.py`
  - `docs/plans/unified-source-index-acceptance.md`
- Work:
  - owner/classification/content-policy negatives;
  - traversal, malformed locator, FTS injection and oversized payload tests;
  - unavailable SQLite/Chroma/CBM/RAPTOR provider behavior;
  - deletion/tombstone and stale-result checks;
  - no private content in logs, reports, metrics or Lens payloads.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_security.py tests/test_unified_source_index_failure_matrix.py`
- Done when: failures are fail-closed for policy and fail-soft for optional
  projections, with no false success state.

### USI-15 - Synthetic Staging And Activation Packet

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `USI-00` through `USI-14`, `UIR-14`, `ULO-14` and `UDA-18`
  for every selected source scope; CBM, lineage and Lens may remain separate
  Partial tracks if clearly declared
- Allowed paths:
  - `docs/plans/unified-source-index-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - exact feature flags, data paths, backup, rollback and cutover order;
  - synthetic end-to-end indexing/query/rebuild evidence;
  - runtime/consumer, source-adapter and lifecycle closure references;
  - declare which domains are ready, partial or deferred;
  - materialize one dormant live gate only after green acceptance.
- Tests:
  - focused USI suite from prior slices
  - `python -m json.tool docs/plans/open-work-completion-master-roadmap.json`
- Done when: packet can activate one bounded source set and automatically
  restore the prior query path on No-Go.

### USI-LIVE-ACTIVATION - Single User Gate

- Class: `needs_live_go`
- Status: `dormant`
- Blocks: productive indexing, legacy cutover and persistent source scanning
- Decision needed: exact environment, source scopes, content policies, backup,
  cutover generation, observation window and rollback command
- Go phrase:
  `GO USI-LIVE-ACTIVATION: activate USI <version> for <source scopes> in <environment> using <policies/generation>; observe <window>; auto-rollback via <plan> on No-Go.`

## 8. Dependency And Hotfile Rules

- TAX owns `src/tool_catalog.py`, tool schemas, tool indexes and security
  projections until its handoff.
- TUA owns canonical invocation telemetry. USI does not write a second event
  ledger.
- GMI owns local-model eligibility, context cache and maintenance admission.
- GRO owns `src/observability_metrics.py`, Prometheus/Grafana assets and
  performance dashboards.
- CBM roadmap owns code-engine packaging, process adapter and code graph query.
- Lineage roadmap owns Git history algorithms and timeline semantics.
- Lens Code Graph roadmap owns UI and graph rendering.
- Project Versioning remains the only project revision and commit authority.
- UIR owns serialized changes to `app.py`, `src/app_initializer.py`, Personal
  Docs, Chat, Agent and read-only runtime routes; USI core does not imply them.
- UDA owns source adapters and best-effort domain change signals; domain writes
  remain authoritative and each existing hotfile requires owner handoff.
- ULO owns serialized Auth/backup/restore/wipe lifecycle integration and calls
  USI-13 primitives rather than duplicating backup or recovery code.
- Active UI files remain outside USI core and are owned by Lens/UI roadmaps.

## 9. Verification Bundle

Minimum final suite:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q `
  tests\test_unified_source_index_contract.py `
  tests\test_unified_source_index_stores.py `
  tests\test_unified_source_index_sqlite.py `
  tests\test_unified_source_index_jobs.py `
  tests\test_unified_source_index_lexical.py `
  tests\test_unified_source_index_embeddings.py `
  tests\test_unified_source_index_query.py `
  tests\test_unified_source_index_context.py `
  tests\test_unified_source_index_raptor.py `
  tests\test_unified_source_index_security.py
```

Also required:

- deterministic repeated-run hash;
- migration upgrade/reopen proof;
- backup/restore/rebuild count and content-hash proof;
- no-query-full-scan checks;
- synthetic owner/classification/deletion matrix;
- comparison report against each legacy source lane;
- green UIR runtime/consumer closure and ULO lifecycle closure;
- UDA acceptance for every source scope named by the activation packet;
- valid guidance and master-roadmap JSON.

## 10. Go Language

- `Go`: all required records, policies, stores, queries, provenance, rebuild and
  comparison gates are green for the selected source scopes.
- `Partial`: the core is sound but one source adapter/projection remains
  disabled with an exact fallback and no overclaim.
- `No-Go`: identity, policy propagation, deletion, locator accuracy, rebuild or
  rollback is not trustworthy.
- `Deferred`: optional domain, Postgres, accelerator or UI work is intentionally
  outside the selected activation.
- `Blocked`: required truth source, migration evidence or owner scope cannot be
  established without unsafe assumptions.

## 11. Definition Of Done

- Exactly one USI control plane owns index identity and provenance.
- Domain systems remain authoritative for their own facts and writes.
- SQLite handles the local workload; Postgres is evidence-triggered.
- Chroma, CBM and RAPTOR are independently deletable/rebuildable projections.
- Every result points to exact source/version/locator evidence.
- Queries and diagnostics are bounded and privacy-safe.
- One canonical query tool and existing exact readers cover the agent workflow.
- The accepted runtime is composed once, each active domain has one adapter,
  and owner rename/delete/backup/restore/wipe behavior is verified.
- No current roadmap or engine creates a duplicate registry, graph truth,
  metrics stack, scheduler, commit history or Lens shell.
- Productive activation has exactly one explicit user gate and a tested
  rollback.
