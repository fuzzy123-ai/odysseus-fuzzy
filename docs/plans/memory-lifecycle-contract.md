# Memory Lifecycle Contract

Date: 2026-07-06

Status: MEM1 inventory contract

## Goal

Define one repo-safe lifecycle vocabulary for Memory, RAG, RaptorGraph,
ORCA/Lens and Universal Inbox derived-memory flows.

## Scope

This contract is descriptive and additive. It does not run a reindex, migrate
storage, write memories, rebuild a graph, scan private corpora or remove legacy
Obsidian/ORCA compatibility.

## Canonical Lifecycle

| Stage | Meaning | Primary existing surfaces | Safe payload rule |
| --- | --- | --- | --- |
| source_metadata | A source item is known by provider, owner, class and hash. | Universal Inbox status, Nextcloud import reports, `src/memory_store_interfaces.py` | IDs, counts, hashes and classes only; no host path, WebDAV URL, filename, chat id or raw body. |
| extracted_abstraction | Source content has been converted into a bounded abstraction. | `src/universal_inbox_memory.py`, Universal Inbox pipeline abstraction events | Abstract summaries only; raw OCR, document text and provider output remain hidden. |
| policy_review | Policy decides ready, review or blocked. | `src/memory_write_policy.py`, Universal Inbox policy gate, UIX review reasons | Reasons use canonical safe tokens and no private evidence text. |
| memory_write_intent | A durable pre-write packet describes what would be written. | `src/universal_inbox_memory_write_intent.py` | `dry_run=True`, `writes_performed=False` by default; records contain derived text only after policy allows it. |
| memory_record | A bounded memory record is planned or written by an approved writer. | Memory routes, memory provider/store interfaces | Record IDs, category and metadata are durable; raw source content is never embedded. |
| provenance_event | The system can explain why the memory or graph event exists. | `src/memory_provenance_ledger.py` | Append-only redacted records; allowed event types only; `raw_content_visible=False`. |
| graph_event | RaptorGraph/ORCA/Lens receives a provenance mutation event. | `src/universal_inbox_raptorgraph_store.py`, `src/progressive_graph_api.py`, Obsidian Raptor adapters | Graph events carry source hashes, record IDs, counts and safe tokens only. |
| diagnostics_budget | Operators can inspect health, freshness and query bounds. | `src/memory_diagnostics.py`, diagnostics routes, store interface budgets | Diagnostics expose counts, gaps and bounded budgets, not private source payloads. |
| rebuild_dry_run | Reindex/rebuild/migration is previewed before live work. | RAG reindex dry-run, Raptor rebuild modules | Dry-run envelopes include rollback metadata and counts; live execution requires `MEM-LIVE-REINDEX-GO`. |

## Canonical Status Tokens

| Status | Meaning |
| --- | --- |
| pending | Stage has not started or lacks enough metadata. |
| completed | Stage finished without requiring operator review. |
| review | Operator or policy review is required before continuing. |
| blocked | A no-go reason prevents writes or graph mutation. |
| dry_run_ready | Safe dry-run evidence exists, but no live write occurred. |
| written | A bounded approved writer persisted a record or event. |
| duplicate | A writer found an existing equivalent event and did not duplicate it. |

## Required IDs

- `source_hash`: stable hash of the redacted source reference or source
  abstraction input.
- `memory_id`: durable memory record ID, usually derived from the source hash
  when created from Universal Inbox.
- `graph_event_id`: deterministic ID for a graph mutation or graph provenance
  event.
- `correlation_id`: runtime/event envelope ID linking status, intent,
  provenance and graph diagnostics.

## Write Boundary

Memory Write Intent is the only approved durable pre-write boundary for derived
memories. A component may prepare a memory record or graph event only after a
policy packet has produced one of these outcomes:

- `ready`: may prepare records, but live persistence still depends on the
  concrete writer and any roadmap gate.
- `review`: may expose redacted reasons and next action, but must not write.
- `blocked`: may expose no-go reasons and diagnostics, but must not write.

All live reindex, live graph rebuild, storage migration and production corpus
write actions remain behind `MEM-LIVE-REINDEX-GO` or a narrower explicit Go.

## Redaction Rules

The following must not appear in lifecycle docs, tests, ledgers, route payloads
or graph evidence:

- raw document text, OCR dumps, e-mail bodies or provider output
- local host paths, WebDAV URLs, private filenames or source directory names
- chat IDs, tokens, cookies, passwords, API keys or credentials
- unbounded graph traversals, unbounded scan names or full-corpus dumps

Allowed evidence is limited to safe tokens, booleans, counts, hashes, stable
IDs, classifications, reason codes and bounded budget metadata.

## Compatibility Map

| Legacy term | Canonical term | Rule |
| --- | --- | --- |
| Obsidian Raptor cache | graph_event store or graph diagnostics | Keep adapters additive until removal has operator/design Go. |
| RAG import write | memory_write_intent followed by approved memory_record | Imports must expose dry-run intent before persistence. |
| Universal Inbox memory abstraction | extracted_abstraction | Reuse the UIX flow-state abstraction stage. |
| ORCA/Lens graph mutation | graph_event | Align event IDs and source hashes before deprecating old names. |
| Memory diagnostics | diagnostics_budget | Report bounded query, freshness and graph gaps with redacted counts. |

## MEM1 Done Definition

- The lifecycle stages above are the vocabulary for MEM2 and later slices.
- No live write, reindex, rebuild or migration is implied.
- Later code models should preserve this stage order and the redaction rules.
