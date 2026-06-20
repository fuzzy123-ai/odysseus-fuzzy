# RAPTOR Memory Write Operator Contract

Stand: 2026-06-20

Status: implementation contract for gated derived-artifact rebuilds

## Purpose

This contract defines when RAPTOR memory writes are allowed in the Obsidian plugin. RAPTOR writes are allowed only for reproducible derived artifacts built from current vault sources. They are not source-note writes, not memory promotion, not cleanup automation, and not provider-generated summaries.

## Required Gates

A RAPTOR rebuild is operator-approved only when all gates are true:

- `ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED=true`
- `ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED=true`
- caller has `vault:write`
- vault is unlocked
- rebuild writes only the RAPTOR derived artifact paths
- status is clean enough for `writes_supported=true` after rebuild
- tests prove no raw content, absolute host paths, secrets, or provider output are persisted
- large graph simulation proves bounded output instead of full graph dumps

Default state is blocked. Setting only `ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED=true` is not enough.

## Allowed Writes

The rebuild path may write only:

- `.obsidian/odysseus/raptor/index.json`
- `.obsidian/odysseus/raptor/summaries.json`
- `.obsidian/odysseus/raptor/rebuild_report.json`
- temporary files next to those artifacts, atomically replaced with `os.replace`

The route and tool are write surfaces. They must remain blocked for locked vaults and API tokens without `vault:write`.

## Forbidden Writes

The rebuild path must not write, delete, move, or mutate:

- markdown source notes
- `AI Memory/Canonical/`
- `AI Memory/Review Queue/`
- `AI Memory/Quarantine/`
- `.trash/` except independent trash-retention purge tests
- paths outside the unlocked vault
- external stores, provider endpoints, or network services

## Artifact Privacy

RAPTOR artifacts may contain compact derived metadata:

- relative source paths
- source hashes
- normalized source status
- default-retrieval booleans
- bounded graph edge records
- cluster ids and counts
- lineage flags, budgets, timestamps, and warning counts

RAPTOR artifacts must not contain:

- full note bodies
- raw extracted document text
- provider or LLM output
- tokens, passwords, chat IDs, or secrets
- absolute host paths
- unbounded graph/node/edge payloads

## Deprecated And Superseded Sources

Deprecated, superseded, archived, stale, conflict, review, draft, and quarantined sources remain isolated from default retrieval. Rebuild may retain their compact lineage metadata and counts so operators can audit why sources are excluded. Rebuild must not convert isolated statuses into active/default retrieval.

## Purge Boundary

Trash purge is separate from RAPTOR rebuild. Purge may delete only expired date directories under `.trash/`, must not follow symlinks, and should report counts rather than private paths. RAPTOR rebuild must not purge user files.

## Performance Boundary

Large graph evidence must be synthetic or temp-fixture based unless a real private vault stress run is explicitly approved. Performance tests must prove:

- node and edge counts are recorded
- returned output is clipped to configured budgets
- cursor or aggregate metadata exists for clipped output
- reports do not serialize full graph payloads
- resource gates can fail without reporting Go

## Go / Partial / No-Go

Go:

- both RAPTOR flags enabled, `vault:write` required, locked vault blocked
- derived artifacts rebuild atomically
- dirty/missing source lineage can be cleared by rebuilding current sources
- deprecated/superseded sources stay isolated
- purge scale and large graph simulation tests pass
- artifacts avoid raw content, secrets, provider output, and absolute paths

Partial:

- backend rebuild works but UI controls are not added
- scale simulation is synthetic only
- provider-generated summaries remain deferred

No-Go:

- source notes are written by RAPTOR rebuild
- rebuild runs with only the read/status flag enabled
- route/tool bypasses `vault:write`
- locked vault exposes or writes RAPTOR data
- artifacts include raw note bodies, secrets, absolute paths, or full graph dumps
- deprecated/superseded sources enter default retrieval
- purge deletes outside `.trash/`

## Rollback

Rollback is operationally simple because rebuild artifacts are derived. Disable `ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED`, delete or archive the RAPTOR artifact directory if needed, and rebuild later from source notes. Source notes are the system of record.
