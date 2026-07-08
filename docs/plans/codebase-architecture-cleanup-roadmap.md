# Codebase Architecture Cleanup Roadmap

Status: repo-only complete under Standard ABC; broad moves and alias removal gated

ABC mode: Standard ABC

## Goal

Reduce module sprawl and overlapping domain boundaries after the optimization
contracts are stable, without breaking existing imports, routes, plugins or
tests.

## Current Evidence

- `src/` contains many mature but flat domain modules across agent,
  orchestration, memory, inbox, nextcloud, telegram, ops, security, release,
  plugin, MCP, visual, coding and tool systems.
- Large plugin surfaces exist, especially Telegram and ORCA/Lens.
- Several domains already have good tests, which makes characterization-first
  cleanup feasible.
- ARC1 dependency inventory is documented in
  `docs/plans/codebase-architecture-dependency-inventory.md`.
- ARC2 import map generator is implemented in
  `scripts/architecture_import_map.py` with focused tests in
  `tests/test_architecture_import_map.py`. A repo scan currently covers 848
  Python files, parses 845 modules, records 3 parse errors and finds 1333 local
  cross-domain edges.
- ARC3 boundary contract is documented in
  `docs/plans/codebase-architecture-boundary-contract.md`.
- ARC4/ARC5 first package move and compatibility aliases are complete for the
  operator dashboard backend models: implementation now lives under
  `src/operator_dashboard/`, while `src/operator_dashboard_snapshot.py` and
  `src/operator_review_queue.py` remain compatibility aliases.
- ARC6 integration review is documented in
  `docs/plans/codebase-architecture-integration-review.md`, closing the safe
  repo-only architecture track and deferring broad moves plus alias removal.
- Current rework need: package boundaries should eventually reflect product
  domains instead of a mostly flat module namespace.

## Mode

Standard ABC. Repo-only. Broad moves are blocked until import maps and
characterization tests exist.

## Non-goals

- Do not move files before dependency inventory exists.
- Do not rename public routes during architecture cleanup.
- Do not combine behavior changes with module moves.
- Do not remove compatibility aliases without migration gates.

## What Must Be Done

- Build dependency inventory for candidate domains.
- Identify stable package boundaries:
  `agent`, `orchestration`, `memory`, `inbox`, `integrations`, `ops`,
  `security`, `release`, `plugins`, `tools`, `workspace`, `visual`.
- Create import compatibility map before moves.
- Add package-level public API files only after consumers are known.
- Move one domain at a time behind compatibility aliases.
- Delete compatibility only after consumers and tests are migrated.
- Keep docs and route contracts stable during internal cleanup.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| ARC1 dependency inventory | safe_offline | Alice | roadmap and inventory doc | Done: `docs/plans/codebase-architecture-dependency-inventory.md` |
| ARC2 import map generator | repo_only | Bob | script/test files only | Done: `tests/test_architecture_import_map.py` |
| ARC3 domain boundary contract | safe_offline | Alice | docs | Done: `docs/plans/codebase-architecture-boundary-contract.md` |
| ARC4 first small package move | repo_only | Bob | one low-risk domain only | Done: operator dashboard package move |
| ARC5 compatibility alias check | repo_only | Bob | aliases/tests | Done: legacy import alias tests |
| ARC6 broad cleanup gate | blocked | Charlie | none until approved | Done: integration review, gates deferred |

## Execution Progress

2026-07-06:
- ARC1 dependency inventory done as a safe_offline docs slice. The inventory
  records candidate boundaries for agent, orchestration, memory, inbox,
  integrations, ops, security, release, plugins, tools, workspace and visual
  domains, and states that route paths, plugin manifests and user-facing API
  schemas must remain stable.
- ARC2 import map generator done additively. `scripts/architecture_import_map.py`
  parses Python files with `ast`, never imports project modules, classifies
  modules into candidate domains and emits the
  `odysseus.architecture_import_map.v1` report with side effects set to none,
  `files_moved=False` and `imports_executed=False`.
- ARC2 initial repo scan evidence: 845 Python files scanned, 842 modules parsed, 3
  parse errors recorded, 1333 local cross-domain edges found. The parse-error
  modules are `src.builtin_action_email_urgency`, `src.builtin_actions` and
  `src.task_scheduler_checkin`; they are inventory risks, not blockers for the
  static map generator.
- ARC3 boundary contract done as a safe_offline docs slice. The contract
  requires current import-map evidence, stable public routes/schemas,
  compatibility aliases before consumer migration, separate behavior and move
  slices, focused tests before edits and explicit gates before alias removal or
  broad moves.
- Verification passed:
  `pytest tests/test_architecture_import_map.py -q` with 3 tests passed and
  the known SQLAlchemy deprecation warning. A real repo import-map smoke
  returned schema `odysseus.architecture_import_map.v1` and the counts above.
- ARC4 first small package move done. `src/operator_dashboard/snapshot.py` and
  `src/operator_dashboard/review_queue.py` now hold the operator dashboard
  backend model implementations, and `src/operator_dashboard/__init__.py`
  exposes their public builders/constants. `routes/operator_dashboard_routes.py`
  imports from the package facade. No route paths, schemas or behavior were
  renamed.
- ARC5 compatibility alias check done. `src/operator_dashboard_snapshot.py` and
  `src/operator_review_queue.py` remain tiny compatibility alias modules that
  re-export the moved implementations for existing consumers.
- ARC4/ARC5 verification passed:
  `py_compile src/operator_dashboard/__init__.py src/operator_dashboard/snapshot.py src/operator_dashboard/review_queue.py src/operator_dashboard_snapshot.py src/operator_review_queue.py routes/operator_dashboard_routes.py tests/test_operator_dashboard_snapshot.py tests/test_operator_review_queue.py tests/test_operator_dashboard_routes.py scripts/architecture_import_map.py tests/test_architecture_import_map.py`;
  combined operator-dashboard and architecture suite passed with 14 tests and
  the known SQLAlchemy deprecation warning. A post-move repo import-map smoke
  returned 848 Python files scanned, 845 modules parsed, 3 parse errors
  recorded and 1333 local cross-domain edges.
- ARC6 integration review done. `docs/plans/codebase-architecture-integration-review.md`
  maps dependency inventory, import-map tooling, boundary contract, the first
  package move, compatibility aliases and route consumer update to focused
  verification. `ARC-BROAD-MOVE-GO` and `ARC-COMPAT-REMOVAL-GO` remain deferred
  gates for any larger cleanup.

## Gate Queue

Gate: `ARC-BROAD-MOVE-GO`
Class: blocked
Blocks: moving large domain families
Decision needed: approve one domain move after inventory and tests
Safe preparation done: dependency inventory and import map
Risk if bypassed: import breakage across app startup, plugins and tests
Next safe slice: inventory and import map

Gate: `ARC-COMPAT-REMOVAL-GO`
Class: needs_design
Blocks: removing old import paths or public aliases
Decision needed: compatibility duration and release notes
Safe preparation done: aliases and warnings
Risk if bypassed: third-party plugins/integrations break
Next safe slice: additive aliases

## Paths

Alice path:
- inventory domains and public contracts
- define migration language and release notes

Bob path:
- build import/dependency tooling
- perform tiny move after tests prove safety

Charlie path:
- prevent broad cleanup from overlapping feature work
- require focused tests before and after moves

## Verification

- Import map script/check.
- Startup/import smoke.
- Focused domain tests for any moved module.
- Plugin load tests after plugin-facing moves.
- `git diff --check`.

## Go Language

- Go: inventory and import map exist; small package move succeeds with aliases.
- Partial: inventory exists but moves are deferred.
- Deferred: compatibility removal waits for release/design gate.
- No-Go: broad file moves occur without characterization tests.
