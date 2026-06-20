# RAPTOR Dynamic Cache ABC Roadmap

Stand: 2026-06-20

Status: implementation completed with focused tests; disk cache, UI controls and background warming remain deferred

## Goal

Add a safe in-process dynamic cache for RAPTOR memory status and bounded derived graph views, with deterministic invalidation from vault source signatures, RAPTOR artifact metadata, feature flags and lock/write events.

## Current Evidence

- `plugins/obsidian/backend/raptor_rebuild.py` now writes derived RAPTOR artifacts atomically:
  - `.obsidian/odysseus/raptor/index.json`
  - `.obsidian/odysseus/raptor/summaries.json`
  - `.obsidian/odysseus/raptor/rebuild_report.json`
- `plugins/obsidian/backend/hybrid_retrieval.py` exposes `raptor_status(vault_dir)`, but it loads JSON, scans lineage and runs Freshness Gate audit on every call.
- `plugins/obsidian/backend/context_provider.py` computes stable `cache_key` values after building payloads, but it does not cache the RAPTOR status/view computation itself.
- `plugins/obsidian/backend/routes.py` has `_vault_watch_signature(vault_dir)`, but the helper is route-local and currently not used by RAPTOR.
- `plugins/obsidian/tests/test_raptor_rebuild_backend.py` covers dirty lineage after source changes and rebuild clearing dirty state.
- `src/memory_perf_suite_raptor.py` simulates large graph-memory cardinality and bounded output, but does not yet model cache hit/miss behavior.
- `plugins/obsidian/backend/raptor_cache.py` implements in-process status and graph-view caching.
- `plugins/obsidian/tests/test_raptor_cache_backend.py` covers hit/miss/stale/eviction behavior, source invalidation, rebuild clear and bounded graph views.
- `docs/plans/raptor-dynamic-cache-operator-contract.md` defines cache privacy, invalidation, diagnostics and rollback expectations.

## Design Decision

RAPTOR Dynamic Cache v1 is an **in-process read-through cache** for derived metadata only.

It must never cache raw note bodies, provider output, secrets, absolute host paths, or unbounded graph payloads. Cache entries are disposable and must be safe to drop at any time. Persistent RAPTOR JSON artifacts remain the durable source for derived RAPTOR state.

## Non-Goals

- No Redis, SQLite, disk cache, Qdrant, Kuzu, Postgres or external service.
- No cross-process cache coherence.
- No source-note writes.
- No provider or network calls.
- No UI work in v1.
- No full graph dump cache.
- No automatic live private-vault stress run.

## Cache Scope

Allowed cached objects:

- RAPTOR status payloads from `raptor_status(vault_dir)`
- bounded graph view payloads read from RAPTOR derived artifacts
- compact metadata: source counts, edge counts, clipped flags, cursors, cache diagnostics

Forbidden cached objects:

- markdown body text
- raw extracted document text
- provider or LLM output
- secrets/tokens/passwords/chat IDs
- absolute host paths
- unbounded source/node/edge arrays

## Invalidation Contract

A cache key must include:

- vault source signature for Markdown source files
- RAPTOR artifact signature for index/summaries/report metadata
- relevant feature flags
- view/query parameters
- cache schema version

Cache must invalidate when:

- any Markdown source file changes, appears or disappears
- RAPTOR artifact mtime/size changes
- RAPTOR rebuild completes
- relevant feature flags change
- explicit cache clear is called
- the process restarts

Locked vaults must not be inspected by cache warmers or routes. Existing locked-vault gates remain authoritative.

## Paths

### Path A: Operator Contract And Roadmap

Owner: Alice

Goal:

- Define operator-facing cache semantics, Go/No-Go language, invalidation expectations and privacy boundaries.

Path completion:

- Roadmap and optional operator note state what the cache may hold, when it invalidates and how operators should interpret cache diagnostics.

### Path B: Cache Core And Tests

Owner: Bob

Goal:

- Implement a small RAPTOR dynamic cache module with read-through status caching, bounded graph view caching, invalidation helpers and focused unit tests.

Path completion:

- Cache hit/miss/stale behavior is covered by tests without raw content persistence.

### Path C: Integration And Diagnostics

Owner: Charlie

Goal:

- Wire cache into RAPTOR status/rebuild surfaces and expose compact diagnostics while preserving auth, lock and write gates.

Path completion:

- Focused suites are green, roadmap status is updated, changes are committed and pushed.

## Slices

### RDC-ABC0 Roadmap

Owner: Charlie

Allowed files:

- `docs/plans/raptor-dynamic-cache-abc-roadmap.md`

Tests:

- `git diff --check`

### RDC-ABC1 Operator Cache Contract

Owner: Alice

Status: completed by Charlie after Alice timeout.

Allowed files:

- `docs/plans/raptor-dynamic-cache-abc-roadmap.md`
- optional `docs/plans/raptor-dynamic-cache-operator-contract.md`

Requirements:

- Define cache privacy boundaries.
- Define invalidation language.
- Define Go/Partial/No-Go.
- No implementation details that conflict with existing RAPTOR derived-only contract.

Tests:

- `git diff --check`

### RDC-ABC2 Cache Core

Owner: Bob

Status: completed by Charlie after Bob timeout.

Allowed files:

- `plugins/obsidian/backend/raptor_cache.py`
- `plugins/obsidian/tests/test_raptor_cache_backend.py`

Requirements:

- In-process bounded read-through cache.
- TTL and max-entry budget.
- Key based on source signature, artifact signature, feature flags, view parameters and schema.
- No raw note content in keys, entries or diagnostics.
- Explicit `clear_raptor_cache(vault_dir=None)`.
- Compact diagnostics: hits, misses, stale, evictions, entry count.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-cache plugins\obsidian\tests\test_raptor_cache_backend.py`

### RDC-ABC3 RAPTOR Status Integration

Owner: Charlie

Status: completed.

Allowed files:

- `plugins/obsidian/backend/hybrid_retrieval.py`
- `plugins/obsidian/backend/raptor_rebuild.py`
- `plugins/obsidian/backend/raptor_cache.py`
- `plugins/obsidian/tests/test_memory_readiness_layers.py`
- `plugins/obsidian/tests/test_raptor_rebuild_backend.py`
- `plugins/obsidian/tests/test_raptor_cache_backend.py`

Requirements:

- `raptor_status(vault_dir)` uses read-through cache.
- Source edits invalidate status.
- Rebuild clears or invalidates cache after artifact writes.
- Status exposes compact `cache` diagnostics.
- Dirty/missing semantics remain correct.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-cache-integration plugins\obsidian\tests\test_raptor_cache_backend.py plugins\obsidian\tests\test_memory_readiness_layers.py plugins\obsidian\tests\test_raptor_rebuild_backend.py`

### RDC-ABC4 Bounded Graph View Cache

Owner: Bob

Status: completed by Charlie after Bob timeout.

Allowed files:

- `plugins/obsidian/backend/raptor_cache.py`
- `plugins/obsidian/backend/tool_specs.py`
- `plugins/obsidian/backend/routes.py`
- `plugins/obsidian/tests/test_raptor_cache_backend.py`
- `plugins/obsidian/tests/test_plugin_obsidian.py`

Requirements:

- Add helper to read bounded RAPTOR graph views from `index.json`.
- Cache views by cursor/limit parameters and signatures.
- Return clipped/cursor metadata.
- Do not return full graph payload unless within caller budget.
- Keep route/tool read-only if exposed.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-view plugins\obsidian\tests\test_raptor_cache_backend.py plugins\obsidian\tests\test_plugin_obsidian.py`

### RDC-ABC5 Performance Simulation

Owner: Bob

Status: completed by Charlie.

Allowed files:

- `src/memory_perf_suite_raptor.py`
- `tests/test_memory_perf_suite_raptor.py`

Requirements:

- Extend simulation with cache hit/miss metrics.
- Prove cache reuse reduces repeated derived-view work without full payload dumps.
- Prove budget failure still reports No-Go.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-cache-perf tests\test_memory_perf_suite_raptor.py`

### RDC-ABC6 Final Integration

Owner: Charlie

Status: completed.

Allowed files:

- all files touched by RDC-ABC1 through RDC-ABC5

Tests:

```text
venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-cache-final plugins\obsidian\tests\test_raptor_cache_backend.py plugins\obsidian\tests\test_memory_readiness_layers.py plugins\obsidian\tests\test_raptor_rebuild_backend.py plugins\obsidian\tests\test_plugin_obsidian.py tests\test_memory_perf_suite_raptor.py
git diff --check
```

## Go / Partial / No-Go

Go:

- Cache is bounded, in-process and disposable.
- Status cache invalidates on source/artifact/flag changes.
- Rebuild clears or invalidates RAPTOR cache.
- Graph view cache is clipped and cursor-aware.
- Diagnostics show hit/miss/stale/eviction counts without private paths or raw content.
- Focused tests pass.

Partial:

- Status cache is complete, graph view cache deferred.
- Cache diagnostics are backend-only.
- Performance simulation records cache metrics, but no UI exposes them.

No-Go:

- Cache stores note bodies, secrets, provider output or absolute paths.
- Cache can return stale clean status after a source edit.
- Rebuild completes but stale cache remains visible.
- Graph view emits unbounded payload.
- Locked vault route/tool bypasses existing gates.
- Tests require real private vault data or network/provider calls.

Deferred:

- Disk-backed cache.
- Cross-process cache coherence.
- UI cache controls.
- Background cache warming.
- Live private vault stress run.

## Stop Rules

- Stop on unrelated dirty or staged files.
- Stop if cache keys or diagnostics include raw content, secrets or absolute host paths.
- Stop if source-note writes are proposed.
- Stop if a route/tool bypasses vault lock or auth scope.
- Stop if full graph payloads are cached or returned without budget.
- Stop if tests need live private data, network, provider calls or destructive git.
