# Harbor One Frontpage V3

V3 is the fixed-layout successor prototype for the Harbor One frontpage.

V2 stays intact as the interaction reference. V3 starts with a simpler rule:
each workspace owns a fixed screen layout; floating windows are no longer the
default structure.

## Open

```text
static/frontpage-v3/index.html
```

## Current scope

- V3 reuses the V2 visual system directly.
- Agent, Inbox, header menus, composer, workline, toolwheel and background
  animation should feel like V2.
- V3 owns a small fixed-layout override in `v3-fixed.css`.
- V3 does not load the V2 window manager, dragging, resizing, snapping or dock
  behavior.
- Planning is a definition-only editor for immutable roadmap revisions, DAGs,
  declared gates, verification and Agent handoff preparation.
- Agent owns the running operation: state, Activities, heartbeats, history,
  claims, evidence and server-projected controls.
- Planning and Agent use the same fixed V3 shell but never mirror each other's
  mutable truth.

## Data contract

The frontend data boundary is defined in
`docs/plans/harbor-one-frontend-data-contract.md`.

Runtime truth must come from a bounded `odysseus.workspace_snapshot.v1` payload
and active `odysseus.clarification_*` state. `data.js` is only an explicit
synthetic fixture fallback for prototype and offline visual work. Do not persist
backend truth, clarification answers, gate decisions, memory summaries or runner
phase state into `localStorage`.

`api.js` contains the bounded same-origin Planning and Agent clients. Planning
rejects runtime-shaped payloads; Agent accepts only the public operation
projection. `planning-fixtures.js` and `agent-operation-fixtures.js` are explicit
preview adapters and are never production truth.

The V3 baseline loads `planning.js` and `agent-operations.js` from `index.html`.
Root-route cutover and live execution remain deployment decisions outside this
static baseline handoff.

## File budget

- Keep every V3 source file below 1000 lines.
- Split by responsibility early: base tokens/reset, shell layout, screen CSS,
  screen behavior, and feature data should live in separate files.
