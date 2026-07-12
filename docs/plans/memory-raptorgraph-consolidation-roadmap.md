# Memory RaptorGraph Consolidation Roadmap

Status: in progress under Standard ABC

ABC mode: Standard ABC

## Goal

Make Memory, RAG, RaptorGraph, ORCA/Lens and Universal Inbox writes follow one
canonical source-to-memory lifecycle: source metadata, extraction abstraction,
review, memory write intent, provenance, graph event, diagnostics and rebuild.

## Current Evidence

- Memory surfaces exist in `src/memory_store_interfaces.py`,
  `src/memory_provenance_ledger.py`, `src/memory_write_policy.py`,
  `src/memory_provider.py`, `src/memory_diagnostics.py` and memory perf suite
  modules.
- RAG surfaces exist in `src/rag_text_chunking.py`, `src/rag_chunk_quality.py`,
  `src/rag_reindex_dry_run.py`, `src/rag_vector.py` and related tests.
- Raptor/graph surfaces exist in `plugins/obsidian/backend/raptor_cache.py`,
  `plugins/obsidian/backend/raptor_rebuild.py`,
  `src/raptorgraph_candidate_mapping.py`,
  `src/progressive_graph_api.py` and `src/large_graph_budget_proof.py`.
- Universal Inbox already has Memory Write Intent and RaptorGraph provenance
  modules.
- MEM1 now provides `docs/plans/memory-lifecycle-contract.md`, defining the
  canonical source metadata -> extracted abstraction -> policy review ->
  memory write intent -> memory record -> provenance event -> graph event ->
  diagnostics budget -> rebuild dry-run lifecycle.
- MEM2 now provides `src/memory_lifecycle.py`, a canonical redacted lifecycle
  state model and validator for already-produced Memory/RAG/RaptorGraph dry-run
  payloads, with tests in `tests/test_memory_lifecycle.py`.
- MEM3 now provides `src/memory_lifecycle_adapters.py`, additive adapters from
  Universal Inbox write intent, RAG reindex dry-run plans, manual memory
  candidates and RaptorGraph candidate mappings into the canonical lifecycle.
- MEM4 now provides `src/memory_provenance_alignment.py`, a read-only alignment
  plan for source hashes, RAG chunk refs, memory record IDs, provenance records
  and RaptorGraph event IDs.
- MEM5 now provides `src/memory_diagnostics_consolidation.py`, a read-only
  diagnostics summary that consolidates lifecycle, provenance alignment, store
  budgets and rebuild gates into existing diagnostic metric snapshots.
- MEM6 now provides `docs/plans/memory-legacy-naming-migration-map.md`, mapping
  Obsidian, RAPTOR, RAG, ORCA/Lens and Universal Inbox terms to the canonical
  lifecycle vocabulary while preserving compatibility surfaces.
- Current rework need: old Obsidian-compatible surfaces, ORCA naming,
  Universal Inbox writes and general Memory/RAG paths should converge.

## Mode

Standard ABC. Repo-only until live reindex, migration or real corpus rebuild
gets a bounded operator Go.

## Non-goals

- Do not live-reindex production collections.
- Do not migrate storage backend in this roadmap without a separate migration
  plan.
- Do not remove legacy ORCA/Obsidian compatibility in the first pass.
- Do not persist raw source text in graph, memory, ledger or evidence.

## What Must Be Done

- Define canonical memory lifecycle states.
- Make Memory Write Intent the only durable write entry for derived memories.
- Add adapters for ORCA/Lens, Universal Inbox, RAG import and manual memory
  flows.
- Align chunking, provenance and graph event IDs.
- Define rebuild/reindex dry-run envelope with rollback metadata.
- Consolidate memory diagnostics around source, extraction, intent, write,
  graph and query budget stages.
- Create deprecation map for legacy Obsidian-specific terms and paths.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| MEM1 lifecycle inventory | safe_offline | Alice | roadmap and memory lifecycle doc | Done: `docs/plans/memory-lifecycle-contract.md` |
| MEM2 canonical lifecycle model | repo_only | Bob | `src/memory_lifecycle.py`, tests | Done: `tests/test_memory_lifecycle.py` |
| MEM3 write-intent adapters | repo_only | Bob | memory/universal inbox adapter modules | Done: `tests/test_memory_lifecycle_adapters.py` |
| MEM4 provenance alignment | repo_only | Bob | provenance/chunk/graph modules | Done: `tests/test_memory_provenance_alignment.py` |
| MEM5 diagnostics consolidation | repo_only | Bob | diagnostics modules/routes | Done: `tests/test_memory_diagnostics_consolidation.py` |
| MEM6 legacy naming migration map | safe_offline | Alice | docs/plans | Done: `docs/plans/memory-legacy-naming-migration-map.md` |
| MEM7 live reindex packet | needs_live_go | Charlie | docs only until approved | no live run |

## Execution Progress

2026-07-06:
- MEM1 lifecycle inventory done as a docs-only safe_offline slice.
  `docs/plans/memory-lifecycle-contract.md` defines canonical lifecycle stages,
  status tokens, required IDs, write-boundary rules, redaction rules and a
  compatibility map for Obsidian Raptor cache, RAG import, Universal Inbox
  abstraction, ORCA/Lens graph mutation and memory diagnostics vocabulary.
- MEM1 verification passed: docs-only scoped `git diff --check`.
- MEM2 canonical lifecycle model done additively.
  `src/memory_lifecycle.py` exposes
  `build_memory_lifecycle_state(...)`, `MemoryLifecycleState`,
  `MemoryLifecycleStage`, `CANONICAL_MEMORY_LIFECYCLE_STAGES` and the
  `odysseus.memory_lifecycle.v1` payload. It normalizes source metadata,
  extracted abstractions, policy review, memory write intent, memory record,
  provenance event, graph event, diagnostics budget and rebuild dry-run stages
  without performing writes, graph mutation, reindex, rebuild or migration.
  Sensitive keys and sensitive string values are stripped or hashed, and
  runtime events record `side_effects=("none",)`.
- MEM2 verification passed:
  `py_compile src/memory_lifecycle.py tests/test_memory_lifecycle.py`;
  focused lifecycle coverage passed with 4 tests; broader Memory/RAG/RaptorGraph
  contract coverage passed with 52 tests and the known SQLAlchemy/cache
  warnings. Scoped `git diff --check` passed.
- MEM3 write-intent adapters done additively.
  `src/memory_lifecycle_adapters.py` now exposes adapters for Universal Inbox
  memory write intent, read-only RAG reindex dry-run plans, manual/web-research
  memory candidates and ORCA/Lens-style RaptorGraph candidate mappings. The
  adapters only translate already-produced payloads into
  `odysseus.memory_lifecycle.v1`; they do not persist memories, mutate graph
  stores, reindex, rebuild or migrate.
- MEM3 verification passed:
  `py_compile src/memory_lifecycle.py src/memory_lifecycle_adapters.py tests/test_memory_lifecycle.py tests/test_memory_lifecycle_adapters.py`;
  focused lifecycle/adapter coverage passed with 8 tests; broader
  Memory/RAG/RaptorGraph contract coverage passed with 63 tests and the known
  SQLAlchemy/cache warnings. Scoped `git diff --check` passed.
- MEM4 provenance alignment done additively.
  `src/memory_provenance_alignment.py` now exposes
  `build_memory_provenance_alignment_plan(...)`, producing the
  `odysseus.memory_provenance_alignment.v1` payload that links one canonical
  `source_hash` to deterministic RAG chunk refs, memory record IDs, a redacted
  provenance record and a deterministic RaptorGraph event ID. It validates
  mismatched source hashes and memory IDs, hashes section paths, rejects raw
  path/secret markers and performs no writes.
- MEM4 verification passed:
  `py_compile src/memory_provenance_alignment.py tests/test_memory_provenance_alignment.py`;
  focused provenance/chunk/graph coverage passed with 29 tests; broader
  Memory/RAG/RaptorGraph contract coverage passed with 66 tests and the known
  SQLAlchemy/cache warnings. Scoped `git diff --check` passed.
- MEM5 diagnostics consolidation done additively.
  `src/memory_diagnostics_consolidation.py` now exposes
  `build_memory_diagnostics_consolidation(...)`, translating existing
  lifecycle state, provenance alignment and store budget summaries into the
  established `DiagnosticSnapshot`/`DiagnosticMetric` contracts plus
  `readiness_by_family` and `readiness_gate` summaries. It performs no live
  checks and rejects raw paths/secret markers.
- MEM5 verification passed:
  `py_compile src/memory_diagnostics_consolidation.py tests/test_memory_diagnostics_consolidation.py`;
  focused diagnostics/lifecycle/alignment coverage passed with 18 tests;
  broader Memory/RAG/RaptorGraph contract coverage passed with 77 tests and the
  known SQLAlchemy/cache warnings. Scoped `git diff --check` passed.
- MEM6 legacy naming migration map done as a docs-only safe_offline slice.
  `docs/plans/memory-legacy-naming-migration-map.md` maps current/legacy
  `obsidian_*`, RAPTOR/RaptorGraph, RAG, ORCA/Lens and Universal Inbox terms to
  canonical Memory lifecycle terms, defines compatibility classes, rename order
  and stop rules, and keeps route/tool/data-path removal behind
  `MEM-LEGACY-REMOVAL-GO`.
- MEM6 verification passed: docs-only scoped `git diff --check`.

## Gate Queue

Gate: `MEM-LIVE-REINDEX-GO`
Class: needs_live_go
Blocks: real collection reindex/rebuild/migration
Decision needed: approve bounded reindex target, rollback and evidence path
Safe preparation done: dry-run envelope and adapters
Risk if bypassed: data loss, duplicate memories or stale graph state
Next safe slice: dry-run and fixture tests

Gate: `MEM-LEGACY-REMOVAL-GO`
Class: needs_design
Blocks: removal of Obsidian legacy vocabulary/routes/paths
Decision needed: compatibility period and UI wording
Safe preparation done: ORCA/Lens aliasing and deprecation map
Risk if bypassed: broken plugin compatibility and user confusion
Next safe slice: additive aliases and docs

## Paths

Alice path:
- define lifecycle and operator wording
- map legacy vocabulary to ORCA/Lens terms

Bob path:
- implement lifecycle model and adapters
- align provenance and diagnostics

Charlie path:
- sequence migrations after tests
- keep live reindex gated

## Verification

- `pytest tests/test_memory_write_policy.py`
- `pytest tests/test_memory_provenance_ledger.py`
- `pytest tests/test_memory_store_interfaces.py`
- `pytest tests/test_universal_inbox_memory_write_intent.py`
- `pytest tests/test_nextcloud_raptorgraph_provenance.py`
- `pytest tests/test_rag_text_chunking.py tests/test_rag_chunk_quality.py`
- `pytest tests/test_progressive_graph_api.py`
- `git diff --check`

## Go Language

- Go: canonical lifecycle and adapters exist, with no raw content persistence.
- Partial: some legacy consumers still use compatibility adapters.
- Deferred: live reindex and storage migration require operator Go.
- No-Go: raw source text or private paths enter memory/graph evidence.
