# TTD-06 Todo State Drift Audit - Operator Runbook

Status: repo-only accepted on `2026-07-24`; implementation commit
`d4255827f79f867b40f96cc3594036ad21037ff8`.

## Purpose and hard boundary

The TTD-06 command compares one exact owner's active Notes-backed Todo state,
legacy prohibited Todo-like Memory records, and the accepted default Todo
Digest projection. It emits a bounded audit plus a non-applying repair preview.

It never repairs, archives, deletes, migrates, backs up, deploys, contacts a
provider, or opens a network connection. `TTD-LIVE-DATA-REPAIR`,
`TTD-LIVE-DEPLOY`, Telegram smoke, and productive rollover remain dormant.

## Inputs

Prepare two explicit offline snapshot files:

1. A self-contained SQLite copy containing the `notes` table.
2. A JSON copy of `memory.json`.

The SQLite copy must be at most 50 MiB and have no sibling `-wal` or `-shm`
file. The Memory copy must be at most 50 MiB and contain a JSON list of mapping
records. Missing, unreadable, malformed, over-budget, WAL-backed, or ambiguous
inputs fail closed.

Do not point the command at an application default path, a running database, a
Debian production path, or a provider-mounted file. The CLI deliberately has
no defaults for owner or either source path.

## Content-free standard audit

Run from the repository root with local placeholders replaced in the terminal:

```powershell
.\venv\Scripts\python.exe scripts\audit_todo_state_drift.py `
  --owner "OWNER_EXACT" `
  --database "OFFLINE_NOTES_SNAPSHOT.sqlite" `
  --memory-file "OFFLINE_MEMORY_SNAPSHOT.json"
```

The owner is exact: blank values, surrounding whitespace, and values longer
than 256 characters are rejected. The SQLite connection uses URI `mode=ro`
plus `PRAGMA query_only = ON`; Memory JSON is read directly without constructing
`MemoryManager`.

Standard JSON output contains only bounded counts, status enums, booleans,
domain-separated hashes, redacted references, and snapshot/audit/preview refs.
It must not be persisted together with raw source content or enriched with raw
owner, Note, Memory, identifier, path, exception, or provider values.

Exit codes:

- `0`: the complete observed snapshot is consistent.
- `1`: drift or review candidates were detected.
- `2`: the audit was blocked or an input failed closed.

`complete=false`, `truncated=true`, `projection_valid=false`, a blocked status,
or any malformed/legacy/duplicate-ID count means the result is not eligible
for an exact repair decision. Digest limit exclusions are normal projection
behavior and are not repair actions.

## Ephemeral exact review

Raw review exists only for a present operator and requires both flags:

```powershell
.\venv\Scripts\python.exe scripts\audit_todo_state_drift.py `
  --owner "OWNER_EXACT" `
  --database "OFFLINE_NOTES_SNAPSHOT.sqlite" `
  --memory-file "OFFLINE_MEMORY_SNAPSHOT.json" `
  --review-details `
  --operator-authorized
```

The `operator_review` object is marked `ephemeral=true`,
`not_for_persistence=true`, and `raw_content_visible=true`. Never redirect,
pipe, copy, screenshot, log, attach, or paste this output. The command provides
no output-file option.

## Repair gate

Every preview action is `preview_only`, `review_required=true`,
`apply_supported=false`, `mutations_performed=false`, and names
`TTD-LIVE-DATA-REPAIR`. TTD-06 contains no apply code.

A future repair is a separate action-specific operation and requires all of:

1. A complete content-free audit bound to the exact `source_snapshot_ref`.
2. An ephemeral exact operator review of the same `preview_ref`.
3. A separately created and verified backup.
4. A written post-repair Notes and Digest readback plan.
5. The exact operator grant `GO TTD-LIVE-DATA-REPAIR` naming the approved
   preview and backup refs.

Any changed snapshot, ref mismatch, incomplete report, unexpected action,
missing backup, or absent grant stops the operation. This runbook does not
authorize such an operation.
