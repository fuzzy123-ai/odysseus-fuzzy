# Operator Dashboard Review Queue Contract

Date: 2026-07-06

Status: backend contract under Standard ABC

## Purpose

Provide one read-only operator contract for status, gates, reviews, evidence,
live readiness and next safe actions without requiring a UI to inspect separate
Telegram, Nextcloud, Memory, MCP, Release, System Health, task and
orchestration surfaces.

## Route

`GET /api/operator-dashboard/snapshot`

Access: admin-gated.

Behavior: read-only. The route must not execute live actions, run provider
probes, dispatch Telegram messages, copy Nextcloud files, write Memory or
RaptorGraph records, publish code, remediate security items, mutate tasks or
start deployments.

## Payload Shape

The route returns:

- `schema`: `odysseus.operator_dashboard.route.v1`
- `snapshot`: `odysseus.operator_dashboard.snapshot.v1`
- `review_queue`: `odysseus.operator_review_queue.v1`
- route-level redaction and no-live/no-write flags

The snapshot contains these sections in stable order:

1. `review_gates`
2. `live_affordances`
3. `tasks`
4. `diagnostics`
5. `version_readiness`
6. `orchestration`

Each section contains only status, safe counts, source schema, a short safe
summary, a safe next-action token and redaction flags.

The review queue can contain these families:

- `nextcloud_copy`
- `memory_write`
- `raptorgraph_write`
- `file_export`
- `security_action`
- `telegram_delivery`
- `coding_approval`

Each review item explains:

- proposed action
- why review is needed
- risk if bypassed
- required gate
- safe default
- next action
- redacted source reference hash

## Default Sources

The route may use injected providers in tests. In the app wiring it gathers
local read-only sources:

| Contract section | Default source |
| --- | --- |
| `review_gates` | Existing review-gate store summary |
| `live_affordances` | `build_live_affordance_readiness()` |
| `tasks` | Existing task-summary model over local scheduled tasks |
| `diagnostics` | Existing operator quick-status model |
| `version_readiness` | `load_version_one_readiness()` |
| `orchestration` | Empty until an orchestration provider is wired |

Provider failures must produce a safe `unknown` section rather than leaking raw
errors.

## Redaction Invariants

The operator dashboard route, snapshot and review queue must keep:

- `raw_content_visible = false`
- `private_content_visible = false`
- `path_values_visible = false`
- `url_values_visible = false`
- `command_values_visible = false`
- `token_value_visible = false`
- `chat_id_value_visible = false`
- `live_probe_performed = false`
- `live_mutation_performed = false`
- `write_action_enabled = false`

Review queue items additionally keep `source_ref_visible = false`,
`live_action_enabled = false` and `write_action_enabled = false`.

## Gates

Gate: `ODR-UI-PLACEMENT`

Class: `needs_design`

Decision needed: choose whether the UI agent places the dashboard in legacy
chat, Lens, v2 dashboard or a plugin page.

Gate: `ODR-LIVE-ACTION-BUTTONS`

Class: `needs_live_go`

Decision needed: explicitly approve one bounded live action class before any
approve/execute button can do more than preview.

## Done Definition

Backend partial completion means:

- one admin-gated snapshot route exists;
- one read-only dashboard snapshot contract exists;
- one read-only review queue contract exists;
- default local providers populate the route without live actions;
- focused model and route tests prove redaction, admin gating and no-write
  behavior.

Full roadmap completion still requires the UI placement decision or an
explicit deferral accepted by the operator.
