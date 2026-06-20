# Master Implementation Agent

This repo already has the core primitives for a top-level orchestrator:

- `orchestrator_mode` in [src/agent_loop.py](C:\Users\nkatz\odysseus\src\agent_loop.py)
- worker delegation via [src/delegate_tool.py](C:\Users\nkatz\odysseus\src\delegate_tool.py)
- reflector reviews via [src/reflector_agent.py](C:\Users\nkatz\odysseus\src\reflector_agent.py)
- durable run tracking via [plugins/obsidian/backend/state_doc.py](C:\Users\nkatz\odysseus\plugins\obsidian\backend\state_doc.py)

This document defines how to use them as a single master-agent pattern for implementation work.

## Delegate vs Durable Subagents

`delegate` is not a durable implementation worker. It is a focused LLM call that
returns compact JSON. Its system prompt explicitly says that it must not claim
to have changed files or external state. Use it for lightweight analysis,
summaries, option checks, or read-only reasoning.

Durable subagents are a separate runtime concern. They need a
`SubagentRunSpec`, a scoped `ContextCapsule`, a persisted `AgentRun`, an
unambiguous `ThreadRef` or `JobRef`, an execution backend, a parsed handoff, and
quality gates before the master can call work verified.

Canonical runtime plan:

- `docs/plans/subagent-runtime-v1-roadmap.md`

Hard boundary:

- Do not treat `delegate` output as proof that files changed, tests ran, git was
  clean, or a worker thread completed.
- Do not start live thread execution until the Subagent Runtime track has a
  fake backend, tests, status visibility, and a separate explicit operator Go.
- Do not persist secrets, raw thread IDs, chat IDs, private source contents, or
  raw provider/tool output in prompts, docs, tests, evidence, or handoffs.

## Purpose

The master agent does not implement code directly.

It should:

- split work into small slices
- avoid overlapping files when another agent is already active
- delegate one focused task per worker
- keep test scope proportional to the implementation slice
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
- ask multiple workers to restate the same security or contract invariant at tool, route, and UI levels in parallel
- use `delegate` as a write-capable or durable subagent

## Test Layering Rules

For Obsidian RC work, test growth must follow a layering strategy instead of adding the same invariant everywhere.

- prefer one central policy/backend test over many near-identical surface tests
- keep only a small number of canonical route/tool contract tests per invariant
- reserve static/source-text tests for contracts that are hard to drive at runtime
- do not grow `plugins/obsidian/tests/test_plugin_obsidian.py` as the default destination for every new regression
- when a slice changes product logic and test structure, split that into separate slices unless the change is tiny

If an invariant is already proven directly in a backend policy module such as vault security, lock guards, or apply guards, the next worker should usually add:

- zero or one representative route/tool contract test
- not a full matrix of equivalent tests across every entry surface

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
| `S15-test-layering-refactor` | split oversized Obsidian tests and reduce duplicate surface coverage | `plugins/obsidian/tests/test_plugin_obsidian.py`, `tests/test_obsidian_sidebar_static.py`, new focused test files | only with explicit ownership |

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
- `S15-test-layering-refactor`

## Delegation Contract

Each worker should receive:

- exactly one slice
- explicit non-goals
- explicit file ownership boundaries
- explicit test-layer guidance for the slice
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
- record whether the slice adds policy tests, surface-contract tests, or only smoke coverage

Example delegation summary line:

```text
Summary: Assigned S2-security-ui-docs, owning frontend password dialog and plugin docs only; no graph files.
```

## Reflector Use

Use the reflector to catch:

- worker drift outside the assigned slice
- overlapping file ownership
- too-broad delegation scopes
- duplicate test coverage of the same invariant across multiple layers
- stalled orchestration loops
- repeated delegation without integration decisions

If the reflector reports risk, the master agent should narrow the next worker task instead of broadening it.

## Success Criteria

The master agent is working correctly when:

- active slices are clearly separated
- another active agent is treated as a real coordination constraint
- workers stay narrow and file-bounded
- workers do not inflate the same invariant across backend, route, tool, and static tests without a clear reason
- state-doc history is readable enough for handoff
- no two workers are sent into the same hot files without an explicit decision
