# RAPTOR Dynamic Cache Operator Contract

Stand: 2026-06-20

Status: implementation contract for in-process derived metadata caching

## Purpose

RAPTOR Dynamic Cache v1 reduces repeated status and bounded graph-view work for derived RAPTOR metadata. It is an in-process acceleration layer only. It is not a durable store, not a source of truth, and not a replacement for RAPTOR artifact rebuilds.

## Cache Boundaries

The cache may contain:

- RAPTOR status payloads
- bounded graph edge view pages
- source and artifact signatures made from relative paths, mtimes and sizes
- feature-flag signatures
- compact diagnostics such as hits, misses, stale entries, evictions and entry count

The cache must not contain:

- markdown note bodies
- raw extracted document text
- provider or LLM output
- secrets, tokens, passwords or chat IDs
- absolute host paths
- unbounded graph payloads

## Invalidation

Operators should expect cache misses when any of these change:

- Markdown source files are created, changed or removed
- RAPTOR derived artifacts are rebuilt or edited
- relevant feature flags change
- the explicit cache clear helper is called
- the process restarts
- a cached entry exceeds its TTL

RAPTOR rebuild must clear cache entries for the affected vault after artifact writes. Source edits do not require manual clear because source signatures are part of cache keys.

## Locked Vaults

Locked-vault gates remain authoritative. Cache code must not warm, inspect or return derived data for locked vault surfaces that existing route/tool handlers block.

## Diagnostics

Cache diagnostics are operational metadata. They may show:

- namespace
- cache key hash
- hit/miss state
- hit, miss, stale and eviction counts
- entry count

Diagnostics should not include private source paths, raw query text beyond bounded params, note bodies or absolute host paths.

## Go / Partial / No-Go

Go:

- repeated RAPTOR status calls hit cache while signatures remain stable
- source edits produce a cache miss and correct dirty status
- rebuild clears stale cache and status becomes fresh
- graph views are bounded and cursor-aware
- tests prove no raw content in cache keys or diagnostics

Partial:

- status cache works but graph-view cache remains backend-only
- diagnostics are visible only in API/tool payloads
- cache metrics exist in synthetic performance tests but are not graphed in UI

No-Go:

- cache returns clean/ready status after a source edit
- rebuild leaves stale dirty payloads visible
- graph view returns unbounded edges
- cache stores raw content, secrets, provider output or absolute host paths
- locked vault bypasses existing route/tool protections

## Rollback

The cache is disposable. Operators can clear it in-process, restart the service, or disable dynamic cache integration without touching source notes or RAPTOR artifacts. Persistent RAPTOR artifacts remain rebuildable derived data.
