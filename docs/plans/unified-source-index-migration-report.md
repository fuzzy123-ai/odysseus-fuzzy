# Unified Source Index Legacy Migration Comparison

Status: `synthetic_baseline_ready_2026-07-17`

Scope: USI-11 repository-only comparison evidence. This report does not
authorize live cutover, send shadow requests, dual-write data, inspect a
private corpus, or modify an active reader.

## Synthetic baseline

The deterministic `complete` fixture contains one content-free observation for
each legacy lane. All four lanes achieve:

- count coverage `1.0`;
- locator parity `1.0`;
- owner/classification/content-policy parity `1.0`;
- content-hash parity `1.0`;
- zero missing and zero orphan observations.

Negative fixtures independently prove that missing records, locator drift and
policy drift fail their respective gates.

## Decisions and cutover gates

| Legacy lane | Decision | Target role | Measurable gate before active-path change |
| --- | --- | --- | --- |
| Personal Docs | `adapt` | Keep discovery and domain reads behind a USI SourceAdapter. | At least one compared record, 100% count/locator/policy/hash parity, zero orphans, then the separate UIR cutover gate. |
| Current RAG / Chroma chunks | `adapt` | Keep Chroma only as a rebuildable semantic projection over USI occurrence refs. | 100% count/locator/policy/hash parity and query parity before retiring legacy chunk identity or direct retrieval. |
| Personal Memory | `keep` | MemoryManager and its lifecycle remain canonical personal-memory truth; USI is read-only indexing. | 100% index coverage and policy parity; indexing must never create memory or replace the reviewed memory write path. |
| Obsidian derived index / Lens knowledge reader | `retire` | USI/Derived Runs serve knowledge; Context Transparency and AI Lens remain runtime observation. | 100% count/locator/policy/hash parity, zero orphans and a green UIR cutover before removing the knowledge-reader fallback. Source-local checkpoints remain allowed. |

All thresholds are encoded in
`src/unified_source_index_legacy_comparison.py` and emitted in the JSON report,
so a failed gate is machine-readable rather than an operator judgement hidden
in prose.

## Reproduction

```powershell
venv\Scripts\python.exe -m pytest -q tests\test_unified_source_index_legacy_comparison.py tests\test_compare_unified_source_index.py
venv\Scripts\python.exe scripts\compare_unified_source_index.py --fixture complete --check
```

Expected CLI summary:

```text
USI synthetic comparison: profile=complete lanes=4 ready=true live_cutover_authorized=false
```

## Remaining live evidence

Synthetic readiness is not live parity. UIR-07 owns bounded shadow-request
wiring and the later activation packet must provide real aggregate counts,
query parity, rollback and source-scope approval. Until then:

- no legacy reader is removed;
- no persistent dual write is introduced;
- no real backfill or private-corpus comparison is performed;
- `USI-LIVE-ACTIVATION` remains dormant.
