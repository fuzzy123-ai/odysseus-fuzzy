# Codebase Architecture Dependency Inventory

Date: 2026-07-06

Status: ARC1 safe-offline inventory under Standard ABC

## Scope

This inventory covers the architecture-cleanup planning surface for `src/`,
`routes/`, `plugins/`, `core/` and `services/`. It is intentionally static and
repo-only: no modules are imported, no files are moved and no routes or plugins
are renamed.

## Current Shape

The repo currently has a large flat `src/` namespace plus route and plugin
surfaces that directly import many domain modules. Several optimization
roadmaps have already produced stable contracts for gate evidence, plugin
policy, memory lifecycle, Universal Inbox/Nextcloud flow, Telegram service
splits, coding orchestration, MCP policy and ops/security console snapshots.

Architecture cleanup should move only boundaries that are already proven by
those contracts.

## Candidate Domains

| Domain | Current signals | Cleanup posture |
| --- | --- | --- |
| `agent` | `src.agent_*`, coding agent routes, server project routes | inventory first; avoid merging with orchestration until publish gates are stable |
| `orchestration` | `src.orchestration_*`, `src.plan_*`, heartbeat and quality gate modules | good package candidate after import map confirms consumers |
| `memory` | `src.memory_*`, `src.rag_*`, RaptorGraph and memory routes | keep compatibility aliases; do not remove legacy names before gate |
| `inbox` | `src.universal_inbox_*`, `src.nextcloud_*`, Universal Inbox routes | package candidate after UIX live/write gates remain deferred |
| `integrations` | email, calendar, contacts, webhook and external service adapters | split by provider only after route contracts are characterized |
| `ops` | ops console, system health and observability modules | package candidate once host-agent live gates stay external |
| `security` | gate evidence, review gates, live affordances and security routes | stable shared vocabulary; avoid behavior changes during moves |
| `release` | version-one readiness, local release and system update modules | keep deploy/tag actions gated |
| `plugins` | plugin lifecycle, manifests and plugin directories | plugin-facing imports require compatibility aliases |
| `tools` | MCP and tool capability modules | do not broaden exposure while reorganizing |
| `workspace` | workspace, mount, backup and vault routes | no live filesystem mutation during cleanup |
| `visual` | gallery, editor, document and visual modules/routes | UI/design decisions stay separate from package moves |

## Inventory Rules

- Build import maps before any move.
- Move one domain at a time.
- Keep public route paths stable.
- Keep old import paths behind aliases until consumers and tests are migrated.
- Run characterization tests before and after each move.
- Do not use broad cleanup to change behavior.

## Safe Next Step

ARC2 provides `scripts/architecture_import_map.py`, a static AST import-map
generator. ARC3 defines the boundary contract and compatibility rules. ARC4
may choose one low-risk package move only after the import map and tests are
reviewed.
