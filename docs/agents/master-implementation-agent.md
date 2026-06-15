# Master Implementation Agent

This repo already has the core primitives for a top-level orchestrator:

- `orchestrator_mode` in [src/agent_loop.py](C:\Users\nkatz\odysseus\src\agent_loop.py)
- worker delegation via [src/delegate_tool.py](C:\Users\nkatz\odysseus\src\delegate_tool.py)
- reflector reviews via [src/reflector_agent.py](C:\Users\nkatz\odysseus\src\reflector_agent.py)
- durable run tracking via [plugins/obsidian/backend/state_doc.py](C:\Users\nkatz\odysseus\plugins\obsidian\backend\state_doc.py)

This document defines how to use them as a single master-agent pattern for implementation work.

## Purpose

The master agent does not implement code directly.

It should:

- split work into small slices
- avoid overlapping files when another agent is already active
- delegate one focused task per worker
- review summaries and risks
- decide the next slice or handoff
- keep the active run state current

## Hard Rules

When another agent is already working in the project:

- treat that agent as an active owner until proven otherwise
- do not assign a new worker to the same files
- prefer slices that touch different files or different layers
- if overlap is unavoidable, delegate only read-only inspection first
- use the state doc to record suspected ownership, conflicts, and blocked slices

The master agent should not:

- edit host files itself
- run shell commands itself
- bypass worker delegation for implementation work
- fan out broad overlapping tasks to multiple workers

## Recommended Slice Board

Use these slices as the default implementation lanes for the current Obsidian RC work:

| Slice | Scope | Likely Files | Safe In Parallel |
| --- | --- | --- | --- |
| `S1-auth-routing` | auth, route gating, API auth tests | `app.py`, `plugins/obsidian/backend/routes.py`, `tests/test_obsidian_sidebar_static.py` | yes |
| `S2-security-ui-docs` | password UX warning, README, SECURITY, RC wording | `plugins/obsidian/frontend/main.js`, `plugins/obsidian/README.md`, `plugins/obsidian/SECURITY.md` | yes |
| `S3-release-docs` | release notes, install/upgrade path, version sync | `README.md`, `plugins/obsidian/README.md`, `plugin.py`, `plugin.json` | yes |
| `S4-graph-focus` | tree-to-graph focus contract | `plugins/obsidian/frontend/main.js`, `tests/test_obsidian_sidebar_static.py` | only with explicit ownership |
| `S5-graph-filter-state` | filter-state consolidation, legacy globals removal | `plugins/obsidian/frontend/main.js`, `plugins/obsidian/backend/vault_model.py`, `tests/test_obsidian_sidebar_static.py` | only with explicit ownership |
| `S6-performance-gate` | measure and document large-vault thresholds | `tests/`, `docs/obsidian/00-priorisierte-roadmap.md` | yes if measurement-only |
| `S7-project-plan-conflicts` | merge/overwrite flow after P0 | `plugins/obsidian/backend/project_planning.py`, `routes.py`, `frontend/main.js` | yes after P0 stability |
| `S8-memory-review-productization` | queue, dedupe, UX hardening | `plugins/obsidian/backend/memory_review.py`, `frontend/main.js` | yes after P0 stability |

## Suggested Execution Order

Batch 1:

- `S1-auth-routing`
- `S2-security-ui-docs`
- `S3-release-docs`
- `S6-performance-gate`

Batch 2:

- `S4-graph-focus`
- `S5-graph-filter-state`

Batch 3:

- `S7-project-plan-conflicts`
- `S8-memory-review-productization`

## Delegation Contract

Each worker should receive:

- exactly one slice
- explicit non-goals
- explicit file ownership boundaries
- expected output as summary plus findings

Recommended delegate payload:

```json
{
  "task": "Own slice S2-security-ui-docs. Update only the password-protection UI wording and related docs. Do not touch graph code or backend auth. If you see overlap with another active agent, stop and report the overlap instead of editing.",
  "context_query": "obsidian password protection at-rest warning release candidate",
  "budget": 1200
}
```

## State Doc Expectations

The active run note at `_state/active_run.md` should reflect:

- goal
- current slice ownership assumptions
- delegation log
- reflection log
- open questions about overlap, blockers, or unclear ownership

Recommended manual conventions inside the note:

- prefix slice names consistently, for example `S2-security-ui-docs`
- include likely file ownership in delegation summaries
- mark overlap risk explicitly when another agent may be active

Example delegation summary line:

```text
Summary: Assigned S2-security-ui-docs, owning frontend password dialog and plugin docs only; no graph files.
```

## Reflector Use

Use the reflector to catch:

- worker drift outside the assigned slice
- overlapping file ownership
- too-broad delegation scopes
- stalled orchestration loops
- repeated delegation without integration decisions

If the reflector reports risk, the master agent should narrow the next worker task instead of broadening it.

## Success Criteria

The master agent is working correctly when:

- active slices are clearly separated
- another active agent is treated as a real coordination constraint
- workers stay narrow and file-bounded
- state-doc history is readable enough for handoff
- no two workers are sent into the same hot files without an explicit decision
