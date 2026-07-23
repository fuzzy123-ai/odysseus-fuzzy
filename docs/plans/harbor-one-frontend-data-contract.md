# Harbor One Frontend Data Contract

Status: OAW-10 repo-only contract
Roadmap: `docs/plans/odysseus-harbor-one-autonomous-workspace-roadmap.md`
Scope: Harbor One `static/frontpage-v3` data wiring before live UI cutover

## Goal

Harbor One consumes one bounded Odysseus workspace snapshot plus one active
clarification read model. Static fixture data remains an explicit fallback for
prototype and offline visual work; it must never become a second source of
truth.

## Authority

Runtime truth is owned by backend services and projected into frontend-safe
payloads. The intended canonical payloads are:

- `odysseus.workspace_snapshot.v1`
- `odysseus.clarification_request.v2`
- `odysseus.clarification_run_summary.v1`

`static/frontpage-v3/data.js` is a fixture pack only. It may contain synthetic
examples for visual development, empty states and offline demos. It may not be
used to infer current project, memory, planning, sandbox, model, runner or gate
state once a live snapshot is available.

## Frontend Data Sources

Harbor One resolves data in this order:

1. Live snapshot: owner-scoped, redacted, bounded API payload.
2. Active clarification run: owner-scoped structured intake state.
3. Explicit fixture fallback: synthetic `data.js` values labeled as fallback.
4. Empty/unavailable state: no hidden substitution from stale local state.

`localStorage` may store presentation preferences such as active workspace,
menu state or dismissed local UI hints. It must not store backend truth, plan
unlock state, answers, gate decisions, runner phases, memory summaries or
project-scoped source content.

## Required Workspace Snapshot Sections

The first live-capable Harbor One client should expect these top-level
sections. Every section carries `state`, `freshness`, `updated_at` and
`source_ref` or `reason_unavailable`.

| Section | Purpose | Minimum display payload |
| --- | --- | --- |
| `operator` | Current runtime health and policy posture | mode, gates, warnings, version-one readiness |
| `projects` | Current project registry and active project | stable ids, titles, status, repo/workspace refs |
| `clarification` | Active intake or ready-for-plan state | run id, status, progress, current batch summary |
| `planning` | Roadmaps and plan gates | roadmap refs, active proposal refs, blocked gates |
| `coding` | Autonomous coding lifecycle | runner phase, allowed paths, checks, quality/done/publish gates |
| `sandbox` | Test capability and evidence state | profiles, network mode, latest check summary, blocked reasons |
| `knowledge` | Memory and graph readiness | stats, provenance summaries, graph budgets, redaction state |
| `local_model` | Local model and maintenance status | warm model, queue, foreground marker, benchmark class |
| `inbox` | Recent source/import state | source refs, pending review counts, blocked privacy gates |
| `release` | Version 1.0 and deployment gates | MVP percent, UI-live gate, release target readiness |

Allowed section states:

- `live`: current and usable.
- `partial`: usable but missing named optional fields.
- `stale`: old data shown with timestamp and no hidden refresh claim.
- `unavailable`: no data; show reason and safe next action.
- `fixture`: synthetic fallback; never styled as live evidence.

## Clarification Read Model

The frontend does not interpret plain chat text as clarification state. It
renders the server-issued run.

Minimum fields:

- `clarification_id`
- `session_id`
- optional `project_slug` and `coding_task_id`
- `status`: `context_inspection`, `clarifying`, `understanding_review`,
  `ready_for_plan`, `paused`, `cancelled`, `blocked` or `expired`
- `version`
- `intent_summary`
- `progress`: total, answered, unresolved_required, current_batch
- `questions`: current visible batch only
- `answers`: answer summaries for visible/review state only
- `assumptions`: visible proposed defaults, never hidden accepted facts
- `plan_gate`: `locked`, `ready`, `blocked` or `not_applicable`

Answer writes must use `clarification_id`, `question_id`, `expected_version`
and an idempotency key. A conflicting write returns a visible conflict state;
the UI must not overwrite silently.

## Fixture Mapping

Current fixture families in `static/frontpage-v3/data.js` map to future
snapshot sections as follows:

| Fixture family | Future section | Rule |
| --- | --- | --- |
| `modelProfiles` | `local_model` | Synthetic only; live queue/warm state comes from snapshot. |
| `documentSamples` | `inbox`, `planning`, `knowledge` | Use for offline viewer samples only; live docs use source refs. |
| `historicalChats` | `operator` or session list | Fixture history must not imply real unread/waiting state. |
| planning/project fixture data | `planning`, `projects` | One service-backed roadmap payload must drive graph, list and viewer. |
| memory/knowledge fixture data | `knowledge` | Graph nodes need budgets, provenance and stale/fixture labels. |
| agent/tool demo rows | `coding`, `sandbox` | Live tool evidence comes from coding runner and sandbox sections. |

Any new fixture exported from `data.js` should include one of:

- `origin: "synthetic_fixture"`
- `origin: "fallback_fixture"`
- `origin: "demo_only"`

Live payloads should use:

- `origin: "runtime_snapshot"`
- `origin: "clarification_state"`
- `origin: "runner_state"`

## Rendering Rules

- If a live section is missing, render `unavailable`; do not borrow a fixture
  silently.
- If a live section is stale, show the stale timestamp and keep actions
  conservative.
- If a section is fixture-backed, visible labels and ARIA text must expose that
  it is a fixture.
- Required clarification questions block planning until the server says
  `plan_gate: "ready"`.
- Frontend controls may request actions, but backend gates decide whether an
  action is legal.
- Secret-like values, tokens, chat ids, private host paths and raw private
  document bodies never appear in fixtures, snapshot payloads, logs or UI
  debug panels.

## Client Handoff

`OAW-11` should implement a small client layer before adding more screen logic:

```text
static/frontpage-v3/api.js
  getWorkspaceSnapshot()
  getActiveClarification(sessionId)
  submitClarificationAnswers(clarificationId, expectedVersion, answers)
  actOnClarification(clarificationId, expectedVersion, action)
```

The client should return normalized states to the UI:

- `loading`
- `live`
- `fixture`
- `stale`
- `conflict`
- `unavailable`
- `error`

It should not mutate `data.js`, should not write runtime truth into
`localStorage`, and should not generate a plan-unlock state locally.

## Done Criteria

This contract slice is complete when:

- the snapshot and clarification authorities are explicit;
- fixture fallback is named and bounded;
- `data.js` fixture families map to future live sections;
- localStorage boundaries are explicit;
- clarification answer correlation and plan-gate rules are stated;
- OAW-11 has a concrete client handoff;
- no live network, provider, private corpus or UI cutover action is implied.
