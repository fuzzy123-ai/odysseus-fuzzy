# Large File Refactoring Overview

Date: 2026-06-29

## Scope

This overview prepares a refactoring pass for oversized files in the Odysseus
repo. The scan used `rg --files` and excluded dependency/vendor/runtime trees:
`venv`, `node_modules`, `.git`, caches, `dist`, `build`, `coverage`, `output`,
`data`, `logs`, `vault`, `backups`, and minified files.

Working thresholds:

- `<600` lines: normal.
- `600-800` lines: acceptable, monitor only.
- `801-2000` lines: warning zone.
- `>2000` lines: refactoring candidate.

Two views were counted:

| View | <600 | 600-800 | 801-2000 | >2000 |
| --- | ---: | ---: | ---: | ---: |
| Source-like files, including docs/specs/tests/mockups | 1963 | 38 | 62 | 40 |
| Production/runtime view, excluding docs/specs/tests/mockups | 720 | 25 | 42 | 37 |

Production/runtime files at or above 600 lines cluster like this:

| Area | Files >=600 lines |
| --- | ---: |
| `static` | 41 |
| `routes` | 22 |
| `src` | 22 |
| `plugins` | 8 |
| `services` | 4 |
| `core` | 3 |
| `scripts` | 2 |
| `app.py` | 1 |
| `mcp_servers` | 1 |

## Top Refactoring Candidates

### P0: Largest / Highest Leverage

| Lines | File | Refactoring direction |
| ---: | --- | --- |
| 37219 | `static/style.css` | Split global CSS into base tokens, app shell/layout, reusable components, and feature styles. Add an ownership map before moving rules. |
| 19279 | `services/hwfit/data/hf_models.json` | Treat as data, not code. Consider generated/compressed artifact policy, not function-level refactor. |
| 9248 | `static/js/document.js` | Split document API, state, renderers, editor actions, export/import, and event wiring. Keep a compatibility facade initially. |
| 6402 | `plugins/obsidian/frontend/main.js` | Split shell/window lifecycle, vault tree, note editor, memory workspace, project planner, graph rendering, and API client. |
| 6162 | `static/js/emailLibrary.js` | Continue the existing `static/js/emailLibrary/` extraction. Split list state, account/folder API, compose/reply, signatures, search, and UI rendering. |
| 6085 | `static/js/slashCommands.js` | Separate command registry, parsing, execution, UI rendering, and per-domain commands. |
| 6028 | `static/frontpage-v2/styles.css` | Split by shell/layout, cards/panels, chat surface, toolbar/actions, responsive rules. |
| 5946 | `static/js/settings.js` | Split settings domains: providers/endpoints, preferences, plugins/tools, UI sections, persistence/API. |
| 5938 | `src/tool_implementations.py` | Move tool implementations into domain modules. Leave dispatch/import compatibility in this file until callers are updated. |

### P1: Clear Monoliths

| Lines | File | Likely split boundary |
| ---: | --- | --- |
| 4900 | `static/js/notes.js` | notes API, list/filter state, editor pane, actions, render helpers |
| 4708 | `static/js/chat.js` | conversation state, send/stream lifecycle, composer, message actions |
| 4526 | `static/frontpage-v2/app.js` | shell state, views, actions, renderers, API client |
| 3804 | `static/app.js` | bootstrap/import orchestration, global wiring, feature initialization |
| 3779 | `plugins/telegram/plugin.py` | stores, parsing, polling cycle, attachment pipelines, outbound Telegram API, admin routes |
| 3606 | `static/js/cookbookRunning.js` | running jobs state, controls, renderers, API calls |
| 3602 | `static/js/galleryEditor.js` | canvas state, history/persistence, tools, layer UI, export/save |
| 3447 | `static/js/calendar.js` | API/sync, state/cache, month/week/agenda/year views, forms/settings |
| 3373 | `src/agent_loop.py` | prompt assembly, tool loop, retrieval/context injection, verifier/orchestrator helpers |
| 3259 | `routes/email_routes.py` | IMAP helpers, SMTP/drafts, route handlers, sanitization, event logging |
| 3230 | `static/js/documentLibrary.js` | library API, filters/state, list rendering, document actions |
| 3140 | `static/js/sessions.js` | session API, sidebar/list rendering, rename/delete actions, state |
| 3127 | `static/js/admin.js` | admin API client, panels, plugin/tools views, mutations |
| 2993 | `routes/cookbook_routes.py` | route layer, job/session service calls, validation, output formatting |

### P2: Over 2000 Lines, But More Contained

| Lines | File |
| ---: | --- |
| 2822 | `plugins/obsidian/frontend/style.css` |
| 2692 | `static/js/gallery.js` |
| 2539 | `static/js/cookbookServe.js` |
| 2536 | `src/llm_core.py` |
| 2531 | `static/js/tasks.js` |
| 2524 | `static/index.html` |
| 2500 | `static/js/cookbook.js` |
| 2434 | `static/js/chatRenderer.js` |
| 2322 | `static/js/cookbook-hwfit.js` |
| 2282 | `src/task_scheduler.py` |
| 2250 | `routes/model_routes.py` |
| 2206 | `mcp_servers/email_server.py` |
| 2116 | `core/database.py` |
| 2090 | `src/builtin_actions.py` |

## Recommended Refactoring Order

1. Establish guardrails before moving code.
   Add a line-count report or CI warning with an allowlist for generated data,
   specs, and unavoidable compatibility facades. Do not fail files in the
   `600-800` range.

2. Split CSS first, but with visual regression checks.
   `static/style.css` is by far the largest file. Splitting it by feature and
   keeping load order stable should reduce risk without touching behavior.

3. Tackle one frontend monolith at a time.
   Good first targets are `static/js/document.js`, `static/js/emailLibrary.js`,
   and `static/js/settings.js`, because each has visible domain boundaries.
   Extract pure helpers and API clients before render/event wiring.

4. Split backend dispatch files by domain.
   `src/tool_implementations.py`, `src/agent_loop.py`, `routes/email_routes.py`,
   and `routes/model_routes.py` should keep thin public facades while domain
   modules are introduced underneath.

5. Treat plugins as separate refactoring tracks.
   `plugins/telegram/plugin.py` and `plugins/obsidian/frontend/main.js` mix
   storage, transport, UI, and workflow logic. They should be split inside their
   plugin boundaries first, before changing shared platform APIs.

## Suggested Target Modules

Frontend:

- `static/css/base.css`, `layout.css`, `components.css`, `chat.css`,
  `documents.css`, `gallery.css`, `calendar.css`, `settings.css`.
- `static/js/document/api.js`, `state.js`, `render.js`, `actions.js`,
  `export.js`.
- `static/js/settings/api.js`, `providers.js`, `sections.js`, `render.js`.
- `static/js/emailLibrary/api.js`, `state.js`, `list.js`, `compose.js`,
  `search.js`.

Backend:

- `src/tool_domains/repos.py`, `tasks.py`, `settings.py`, `cookbook.py`,
  `vault.py`, `research.py`, with `src/tool_implementations.py` as facade.
- `src/llm/providers.py`, `payloads.py`, `streaming.py`, `fallbacks.py`,
  `activity.py`.
- `routes/email/imap.py`, `smtp.py`, `drafts.py`, `sanitizer.py`, `routes.py`.
- `routes/models/discovery.py`, `endpoints.py`, `probes.py`, `routes.py`.

Plugins:

- `plugins/telegram/stores.py`, `parser.py`, `polling.py`, `attachments.py`,
  `outbound.py`, `admin.py`.
- `plugins/obsidian/frontend/shell.js`, `vault.js`, `memory.js`,
  `project-planner.js`, `graph.js`, `api.js`.

## Refactoring Rules

- Keep public imports and route names stable during the first extraction pass.
- Move pure helpers first, then API/service functions, then rendering/event
  orchestration.
- Add or update focused tests around the moved boundaries before behavior edits.
- Do not spend refactoring budget on `600-800` line files unless they are
  already being touched for adjacent work.
- Keep generated/data artifacts on an explicit allowlist rather than forcing
  them into arbitrary line limits.

## Immediate Next Step

Start with one vertical slice:

1. Create the line-count allowlist/report.
2. Split `static/style.css` into CSS bundles while preserving load order.
3. Pick either `static/js/document.js` or `src/tool_implementations.py` for the
   first code extraction, depending on whether the next milestone is UI polish
   or backend/tool stability.
