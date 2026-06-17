# Memory Cleanup Readiness Roadmap

Status: active planning slice for pre-1.0 memory clarity
Owner: Charlie coordinates, Alice documents roles, Bob builds read-only stats

## Goal

Make the memory layers understandable and measurable before 1.0 without
deleting, migrating, rebuilding, or reshaping production data.

The operator should be able to answer:

- What is the canonical personal memory store?
- What is only a rebuildable semantic index?
- What is the RAG / knowledge index?
- How many entries or indexed items are visible, and how large are the
  storage artifacts, without leaking memory contents?

## Current Evidence

- `data/memory.json` is the canonical personal memory store used by
  `MemoryManager`.
- `src/memory_vector.py` exposes `MemoryVectorStore.get_stats()` with
  `healthy`, `count`, and `lanes`.
- `/api/rag/stats` exists in `routes/diagnostics_routes.py` and is
  admin-gated.
- `/api/memory` currently returns memory contents; it is not a safe stats
  endpoint.
- Local size/count observations from operator context are evidence only and
  must not be hardcoded into code or tests.

## Storage Roles

| Layer | Role | Release meaning |
| --- | --- | --- |
| Personal Memory Store | `data/memory.json` | Canonical user memory data. Not just legacy. |
| Vector Memory Index | Chroma-backed memory vector collections | Derived semantic index over personal memory. Rebuildable from canonical memory. |
| Knowledge / RAG Index | RAG document, vault, and upload index | Knowledge index for documents. Not personal memory. |
| Graph / RAPTOR Memory | Relation and summary layer | Separate future/extended memory layer. Do not conflate with personal memory. |

## Non-Goals

- No deletion or cleanup-by-removal before 1.0.
- No data migration.
- No automatic rebuild.
- No Graph, RAPTOR, Postgres, plugin, Telegram, Nextcloud, or Obsidian work.
- No memory text in diagnostics, logs, tests, or route responses for stats.

## Slices

### MEM0A: memory-storage-roles-contract

Alice owns this docs-only slice.

Allowed files:
- `docs/plans/memory-storage-roles-contract.md`
- Optional short link in this roadmap.

Outcome:
- Operator contract that explains the four storage roles.
- Clear Go / Partial / No-Go language for pre-1.0 cleanup.
- Explicit statement that indexes are derived and rebuildable, while
  `memory.json` remains canonical.

### MEM0B: memory-stats-readonly-model

Bob owns this model/test slice.

Allowed files:
- `src/memory_store_stats.py`
- `tests/test_memory_store_stats.py`

Outcome:
- Read-only stats model that can be fed existing manager/vector/RAG inputs.
- No Chroma writes, no rebuild calls, no memory content exposure.
- Robust labels:
  - `canonical`
  - `derived_index`
  - `knowledge_index`
- Supported fields:
  - `personal_memory_entries`
  - `memory_json_bytes`
  - `memory_json_path`
  - `vector_index_healthy`
  - `vector_index_count`
  - `vector_lanes`
  - optional bounded `chroma_bytes`
  - optional `rag_document_count`

### MEM1: memory-stats-admin-route

Charlie integrates after MEM0A and MEM0B are stable.

Candidate route:
- `GET /api/memory/stats`

Outcome:
- Admin-gated read-only stats endpoint.
- No memory text, no rebuild, no write operations, no secrets.
- Tests prove counts, bytes, health, labels, and no content leakage.

### MEM2: memory-ui-labels-or-doc-link

Optional P2 only if tiny and low-risk.

Outcome:
- Align user-facing labels to "Personal Memory", "Vector Index", and
  "Knowledge / RAG Index".
- No UI redesign.

## Stop Rules

- Stop if any slice proposes deletion, migration, automatic rebuild, or data
  content exposure.
- Stop on dirty hotfile conflicts, foreign staged files, or scope drift.
- Stop if `.env`, secrets, tokens, or private memory contents would be read or
  copied.
- Stop if Graph/RAPTOR/Postgres/plugin/Telegram/Nextcloud/Obsidian scope appears.
- No destructive Git commands.

## Verification

MEM0A:
- Docs-only, no tests required.

MEM0B:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_memory_store_stats.py`

MEM1:
- Focused route tests for `/api/memory/stats`.
- Existing memory route tests should remain unaffected.

## Release Language

Go:
- Storage roles are documented.
- Read-only stats model returns counts and bytes without memory contents.
- Optional admin route is gated and content-safe.

Partial:
- Docs and model are complete, but admin route is deferred.

No-Go:
- Any cleanup-by-delete, migration, automatic rebuild, or memory content leakage
  is attempted before explicit operator approval.
