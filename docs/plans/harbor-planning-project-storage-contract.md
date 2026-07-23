# Odysseus Planning Project Storage Contract

Status: repository contract, schema version 1
Scope: project-local Planning state shared by Odysseus/Harbor One, Planning MCP and derived Raptor Memory context
Sources: [Harbor Planning Integration Master Roadmap](harbor-planning-integration-master-roadmap.json) and [Planning MCP Roadmap](planning-mcp-roadmap.json)

## Purpose

This contract defines the canonical repository-relative storage and identity model for Odysseus Planning. JSON is the planning source of truth. Markdown is a rendered view or handoff only, and Raptor Memory is a derived, rebuildable projection rather than authoritative state.

Naming boundary: `odysseus.planning.*` is the durable canonical namespace. Existing `harbor.planning.*` records are accepted as legacy-compatible aliases during the Harbor One frontend transition, but new repository contracts and service projections should use `odysseus.planning.*`.

The contract covers projects, roadmaps, gates, todos, events and the Planning memory index. It does not approve live MCP exposure, direct writes, deletion, notification delivery or provider access.

## Canonical relative layout

All paths are resolved below one configured, allowlisted Planning root. The logical layout is:

```text
projects/
  <project_id>/
    project.json
    roadmaps/
      <roadmap_id>.roadmap.json
    gates/
      <gate_id>.gate.json
    todos/
      <todo_id>.todo.json
    events/
      <event_stream_id>.jsonl
    memory/
      index.json
    undo/
      <undo_ref>.undo.json
```

During the repo-only transition, an existing roadmap under `docs/plans/*.json` may remain the canonical roadmap source. The Planning service must expose it through the same logical project and roadmap identifiers and record its repository-relative `source_ref`. Migration into `projects/<project_id>/roadmaps/` must be explicit, validated and auditable; copying a roadmap must not create a second authoritative version.

The `undo/` directory contains bounded recovery metadata and, when policy permits, a validated recovery snapshot. It is not a general history store.

## Stable identifiers

- Identifiers are opaque strings, not user-visible titles and not filesystem paths.
- Canonical identifiers use lower-case ASCII letters, digits, `_` and `-`, start with a letter or digit, and are at most 64 characters.
- `project_id` is stable for the lifetime of a project. Renaming a project changes its title, not its ID.
- `roadmap_id`, `gate_id`, `todo_id`, `event_id` and `undo_ref` are unique within their project and are never silently reused after deletion.
- References use typed ID fields such as `project_id`, `roadmap_id` or `gate_id`; consumers must not infer IDs from display text.
- A file name must exactly match its entity ID plus the contract suffix. A mismatch is invalid.
- Tombstoned IDs remain reserved. Restoration reuses the original ID and advances the revision.

Examples in fixtures and documentation must use synthetic placeholders only.

## Common JSON envelope

Every JSON entity except an event-stream line contains these fields:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | integer | Required; currently `1`. Unknown major versions fail closed. |
| `kind` | string | Required typed discriminator. |
| `project_id` | string | Required and equal to the containing project directory. |
| `revision` | integer | Required, starts at `1`, increases by exactly one per committed mutation. |
| `created_at` | RFC 3339 string | Required, UTC, immutable after creation. |
| `updated_at` | RFC 3339 string | Required, UTC, monotonic for committed revisions. |
| `status` | string | Required, from the entity-specific allowlist. |
| `source_refs` | array of strings | Required, possibly empty; bounded repository-relative refs or typed entity refs only. |

Unknown fields may be preserved for forward compatibility, but public MCP and Harbor One payloads must be projected through an explicit allowlist. Unknown fields are never forwarded automatically.

## Entity contracts

### Project: `project.json`

Required fields in addition to the common envelope:

- `kind`: `odysseus.planning.project`
- `project_id`: stable project identifier
- `title`: non-empty display title
- `status`: `planned`, `active`, `paused`, `done` or `tombstoned`
- `roadmap_refs`, `gate_refs`, `todo_refs`: bounded arrays of typed IDs

The reference arrays are indexes for discovery, not substitutes for the child JSON files. They must be repairable by scanning validated child artifacts.

### Roadmap: `roadmaps/<roadmap_id>.roadmap.json`

Required fields:

- `kind`: `odysseus.planning.roadmap`
- `roadmap_id`, `title`, `goal`
- `status`: `planned`, `running`, `blocked`, `deferred`, `done` or `tombstoned`
- `slices`: bounded array of structured slice records
- `gate_refs`: bounded array of gate IDs
- `dependency_refs`: bounded array of roadmap or slice refs
- `verification`: bounded array of non-secret verification descriptions or commands

Roadmap adapters may retain a more specific existing `kind` or legacy `harbor.planning.roadmap` alias, but must produce the common logical fields without rewriting the source merely to satisfy a read operation.

### Gate: `gates/<gate_id>.gate.json`

Required fields:

- `kind`: `odysseus.planning.gate`
- `gate_id`
- `class`: `safe_offline`, `repo_only`, `needs_live_go`, `needs_design`, `needs_operator_go` or `blocked`
- `status`: `open`, `go`, `no_go`, `deferred`, `blocked` or `resolved`
- `decision_needed`, `blocks`, `safe_preparation_done`, `risk_if_bypassed`
- `decision`: null until decided, otherwise a bounded record with decision type, actor reference, reason and timestamp

A gate record documents authority; its presence does not grant authority. Live or write actions require the relevant explicit Go at execution time.

### Todo: `todos/<todo_id>.todo.json`

Required fields:

- `kind`: `odysseus.planning.todo`
- `todo_id`, `summary`
- `status`: `open`, `running`, `blocked`, `deferred`, `done` or `tombstoned`
- `roadmap_id`: nullable typed reference
- `owner_ref`: nullable non-secret actor or role reference
- `due_at`: nullable RFC 3339 timestamp
- `dependency_refs`: bounded typed references

Todos must not embed tokens, chat IDs, private document bodies or raw provider output.

### Event stream: `events/<event_stream_id>.jsonl`

Each non-empty line is one independently valid JSON object with:

- `schema_version`: `1`
- `kind`: `odysseus.planning.event`
- `event_id`, `project_id`, `event_type`, `occurred_at`
- `classification`: `silent` or `notification`
- `actor_ref`, `target_refs`, `reason`
- `revision_refs`: affected entity revisions
- `metadata`: bounded, redacted, allowlisted scalar metadata

Event streams are append-only. A malformed line invalidates the append operation and must never be skipped silently.

Classification rules:

| Event | Default classification |
| --- | --- |
| Read, search, context-pack creation, validation, dry-run proposal | `silent` |
| Routine progress, health or derived-memory refresh | `silent` |
| Project or roadmap creation | `notification` |
| Project or roadmap deletion/tombstone and restore | `notification` |
| Gate decision that blocks or unlocks user-visible work | `notification` |
| Conflict requiring operator action | `notification` |

`notification` means eligible for one sparse Harbor notification payload. It does not authorize Telegram, email, network delivery or any other live side effect. Repeated updates to the same structural event must be deduplicated.

### Derived memory index: `memory/index.json`

Required fields:

- `kind`: `odysseus.planning.memory_index`
- `generated_at`
- `source_revision_refs`: revisions from which the index was derived
- `entries`: bounded array containing stable `memory_ref`, source refs, safe summary, classification and content hash
- `rebuild_required`: boolean

The memory index may contain safe summaries, dependencies, gates and provenance. It must not contain raw private bodies, credentials, raw prompts or provider output. Deleting the index must not lose planning truth: it is always rebuildable from validated canonical JSON.

## Proposal-first write boundary

Reads, validation and proposal generation are repository-safe. A mutation crosses a separate apply boundary.

1. Resolve the requested target against the configured Planning root and entity allowlist.
2. Load and validate the current entity, revision and content hash when present.
3. Build the candidate in memory; do not mutate the canonical file.
4. Validate schema, IDs, references, field budgets, privacy rules and event classification.
5. Return a dry-run proposal containing target ref, base revision/hash, candidate revision/hash, bounded diff, warnings and required gate. A proposal is not planning truth.
6. Apply only after the relevant explicit write/operator gate and an exact target are supplied.
7. Require optimistic concurrency using the proposal base revision/hash. A mismatch returns a conflict; it is never overwritten silently.
8. Write a temporary file in the target directory, flush it, validate it again, then replace the target atomically.
9. Read back and validate the committed file before reporting success.
10. Append the redacted event only after the entity commit succeeds. If event append fails, report partial failure and keep enough non-secret recovery evidence for reconciliation.

Direct write tools must default to `dry_run=true`. Patch apply, deletion and restoration are separate capabilities and gates.

## Delete, tombstone and undo

- Hard delete is not a default operation.
- A delete proposal first creates an `undo_ref` and validated recovery metadata.
- The entity is then marked `tombstoned` or moved only through an atomic, policy-approved operation. Its ID remains reserved.
- Tombstone metadata includes `deleted_at`, `actor_ref`, `reason`, `prior_revision`, `prior_content_hash`, `undo_ref` and affected typed references.
- Undo metadata records the original repository-relative target, schema version, expected tombstone revision, recovery hash and expiry/retention policy. Events contain the `undo_ref`, never the raw recovery body.
- Restore validates the recovery snapshot, target path and current tombstone revision, restores atomically, advances the revision and emits one `notification` event.
- Retention expiry may remove recovery snapshots only under an explicit policy. It must not erase the tombstone ID reservation or audit event.

## Path and reference safety

All storage operations must fail closed when any of these checks fails:

- Input path is absolute, drive-qualified, UNC, URI-like or contains a NUL byte.
- Normalization contains `..`, escapes the configured Planning root or crosses an allowlisted entity directory.
- A symlink or junction resolves outside the configured root.
- Percent-decoding or Unicode normalization changes the path into a traversal path.
- The file suffix, containing project ID or entity ID does not match the validated payload.
- A `source_ref` is absolute, private, outside an allowlisted repository area or includes query credentials.

Callers select entities by typed IDs. Arbitrary filesystem paths, globbing, shell expansion and generic read/write tools are outside this contract.

## Privacy and redaction

- Never persist tokens, passwords, chat IDs, cookies, authorization headers, private absolute paths or raw provider output.
- Private document bodies and raw prompts are excluded from Planning JSON, events, diffs, audit metadata and memory summaries.
- Public service payloads use bounded previews, typed refs, counts, hashes and redaction labels.
- Error responses state field/path categories, not rejected secret values.
- Actor and client information uses stable role/profile references, not credentials or transport identifiers.
- Logs and notification payloads contain the minimum metadata required for diagnosis and navigation.

## Invariants

1. Exactly one canonical JSON source exists for each logical planning entity.
2. Every entity belongs to one stable `project_id`; directory, file name and payload IDs agree.
3. Every committed mutation validates before and after an atomic write and advances `revision` exactly once.
4. No proposal, validation result, event or derived memory entry becomes a hidden second source of truth.
5. Dry-run proposals never mutate canonical files or emit user-visible notifications.
6. Routine reads and progress remain silent; structural changes and actionable conflicts produce at most one sparse notification candidate.
7. Deletes are reversible while retained, preserve ID reservation and never bypass the explicit delete gate.
8. Raptor Memory data is derived, source-linked, privacy-bounded and rebuildable.
9. Path resolution cannot escape the configured repository-relative Planning root.
10. Unknown or invalid schema versions, IDs, revisions, refs or privacy fields fail closed.

## Done and verification criteria

This contract slice is complete when:

- the canonical relative layout covers project, roadmap, gate, todo, event, memory and undo artifacts;
- stable identifier and minimum-field rules are explicit for every artifact;
- proposal-first, optimistic concurrency, atomic apply and post-write validation boundaries are defined;
- silent versus notification event policy and notification non-authority are explicit;
- tombstone, undo and restore metadata are reversible and revision-safe;
- derived Raptor Memory cannot become planning truth;
- privacy, redaction and path-traversal protections fail closed;
- both source roadmap links resolve inside `docs/plans`;
- no live, UI, MCP exposure, provider action or operator decision is required to adopt the repository contract.

## Handoff to PMCP-1

`PMCP-1-service-contract` should implement pure read/list/search/validate/context-pack operations against this logical model. Its tests should begin with synthetic fixtures for every entity type, ID/path mismatch rejection, traversal rejection, bounded/redacted payloads, proposal non-mutation and optimistic-concurrency conflicts. Apply, delete, restoration, external MCP exposure and live notification delivery remain separately gated.
