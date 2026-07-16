# Tool Usage Analytics Acceptance Report

Date: 2026-07-16
Roadmap slice: TUA11
Overall result: `GO`

## Scope

This acceptance uses only deterministic synthetic events and an in-memory
SQLite store. Capture remains default-off. No production database, legacy
inventory, network service, provider, external exporter, deployment, or live
write was used.

## Aggregate results

| Check | Aggregate or technical result |
|---|---:|
| Synthetic invocations | 10,000 |
| Start events | 10,000 |
| Terminal events | 10,000 |
| Succeeded | 2,000 |
| Failed | 2,000 |
| Blocked | 2,000 |
| Cancelled | 2,000 |
| Rejected | 2,000 |
| Retry invocations | 1,000 |
| Incomplete invocations | 0 |
| Duplicate events rejected | 0 |
| Unknown identities | 0 |
| Writer failures in scale run | 0 |
| Coverage | 100% |
| Distinct-owner aggregate count | 10,000 |
| Distinct-session aggregate count | 10,000 |
| Duration p50 | 50 ms |
| Duration p95 | 100 ms |

Repeated aggregation produced the same bounded result and did not add counts.
The writer budget is enforced at p95 below 5 ms per invocation without a
simulated disk stall. The focused local in-memory run observed 2.765 ms p95 per
invocation across 100 batches of 100 invocations.

## Coverage matrix

| Lane | Synthetic invocations | Status |
|---|---:|---|
| Built-in / Agent | 2,000 | covered |
| Plugin / Agent | 2,000 | covered |
| MCP / MCP | 2,000 | covered |
| Built-in / Scheduler | 2,000 | covered |
| Built-in / API | 2,000 | covered |

Every lane contains all five terminal statuses. Retry is represented by the
bounded retry ordinal and remains part of the same aggregate contract.

## Privacy and isolation

| Contract | Technical status |
|---|---|
| Allowlist-only event fields | pass |
| Forbidden content fields | rejected |
| Direct owner/session references | rejected |
| High-cardinality owner/session inputs | aggregate counts only |
| Incognito persistence | zero writes |
| Writer failure isolation | pass |
| Database failure isolation | pass |
| Exporter failure isolation | pass |
| Exception details in diagnostics | absent |
| Raw content in aggregate output | absent |
| Direct identifiers in aggregate output | absent |

Writer, database, and exporter failures leave the original tool result object
unchanged. Incognito short-circuits before the writer. The report contains only
aggregate counts, bounded lane names, thresholds, and technical statuses.

## Gates and defaults

- Analytics capture remains default-off.
- Real legacy backfill remains unavailable; only the separate synthetic dry-run
  contract exists.
- Feature activation, capture, production persistence, external export, and
  deployment require their later action-specific authorization.
