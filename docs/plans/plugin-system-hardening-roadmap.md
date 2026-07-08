# Plugin System Hardening Roadmap

Status: done for local schema, capability, lifecycle, metadata and runbook;
remote registry and breaking-schema cleanup deferred behind gates

ABC mode: Standard ABC

## Goal

Harden the plugin platform so Telegram, MCP, System Health, ORCA/Lens and
future plugins share one manifest, capability, lifecycle, audit and release
policy.

## Current Evidence

- `src/plugin_system.py` supports drop-in plugins, routes, services, tools,
  context providers and consolidation jobs.
- `src/plugin_manifest_policy.py`, `src/plugin_capability_boundary.py`,
  `src/plugin_local_audit.py`, `src/plugin_registry.py` and
  `src/plugin_release_gate.py` already model much of the safety surface.
- Installed reference plugins include Telegram, MCP Server, System Health
  Checker and ORCA/Lens/Obsidian.
- Current rework need: permission tiers, import side effects, compatibility
  versions and plugin lifecycle are not yet one strict product contract.

## Mode

Standard ABC. Repo-only until an operator explicitly approves plugin install,
runtime enablement or external registry work.

## Non-goals

- Do not install remote plugins.
- Do not enable disabled plugins as part of roadmap work.
- Do not run plugin live actions.
- Do not remove legacy plugin compatibility without a migration gate.

## What Must Be Done

- Freeze a plugin manifest schema with version, permission, capability,
  route/tool/provider/job declarations and compatibility range.
- Add permission tiers: read-only, owner-scoped write, admin, host-adjacent,
  networked, live-action.
- Add import-time side-effect audit expectations.
- Add lifecycle states: discovered, audited, loadable, loaded, degraded,
  disabled, quarantined, uninstallable.
- Add plugin health/readiness summary.
- Make reference plugins declare capabilities consistently.
- Define a release gate for plugin updates.

## PLG1 Manifest Inventory

Existing hardening blocks:
- Manifest policy: validates plugin metadata and should become the additive
  contract for schema version, declared surfaces and compatibility range.
- Capability boundary: maps declared permissions to routes, tools, context
  providers, jobs and host-adjacent operations.
- Local audit: checks import-time and local side-effect risks before runtime
  enablement.
- Registry: remains local-first unless `PLG-REMOTE-REGISTRY-GO` explicitly
  approves remote lookup or install behavior.
- Release gate: decides whether a plugin update is loadable, degraded,
  quarantined or blocked before it reaches normal runtime use.

Compatibility rule:
- Existing plugin manifests must remain loadable during PLG1-PLG5. New manifest
  fields are warnings and policy additions only until `PLG-BREAKING-SCHEMA-GO`
  decides the compatibility window and any hard schema requirement.

Permission tier vocabulary:
- `read_only`: inspect local plugin-owned or declared app state only.
- `owner_scoped_write`: mutate only plugin-owned resources or explicitly
  delegated owner-scoped records.
- `admin`: manage plugin configuration, readiness and local operator controls.
- `host_adjacent`: touch filesystem, process, environment or host health
  surfaces without broad host control.
- `networked`: call declared network endpoints without live mutation.
- `live_action`: perform external write, send, install, deploy, provider,
  Telegram, Nextcloud or similar irreversible actions; always gated.

Lifecycle vocabulary:
- `discovered`: present on disk or in a local registry index.
- `audited`: local manifest, import and capability checks completed.
- `loadable`: safe to load under current compatibility policy.
- `loaded`: active in the plugin system without granting new live permissions.
- `degraded`: available with reduced capability due to warnings or missing
  optional dependencies.
- `disabled`: intentionally unavailable by operator or policy.
- `quarantined`: blocked due to audit, release gate or safety failure.
- `uninstallable`: removable by a local operator flow after dependency checks.

## Execution Log

2026-07-05:
- PLG1 manifest inventory: done. Added the local hardening-block inventory,
  additive compatibility rule, permission-tier vocabulary and lifecycle
  vocabulary without approving remote registry, install or live behavior.
- PLG2 schema model: done. `src/plugin_manifest_policy.py` now validates
  additive permission tiers, capabilities, compatibility range, lifecycle and
  schema/manifest version fields while preserving legacy `user` and `admin`
  manifests.
- Verification: focused plugin policy plus dependent audit/release/capability
  and registry tests -> 47 passed, 1 known SQLAlchemy deprecation warning.
  Scoped `git diff --check` -> pass.
- PLG3 capability tiers: done. `src/plugin_capability_boundary.py` now
  classifies required permission tiers from declared capabilities, blocks
  explicit too-low new tiers, and warns only for explicit legacy `user`
  manifests that need migration.
- Verification: capability boundary plus dependent local audit, release gate
  and manifest policy tests -> 37 passed, 1 known SQLAlchemy deprecation
  warning. Scoped `git diff --check` -> pass.
- PLG4 lifecycle/readiness: done. Added `src/plugin_lifecycle_readiness.py`
  and `tests/test_plugin_lifecycle_readiness.py`, then exposed the read-only
  lifecycle readiness payload additively through `src/local_release_readiness_bundle.py`.
  The model summarizes discovered/audited/loadable/loaded/degraded/disabled/
  quarantined states without plugin imports, registry network, install or live
  enablement.
- Verification: lifecycle readiness, local release bundle, local audit,
  manifest policy, capability boundary and release gate tests -> 47 passed,
  1 known SQLAlchemy deprecation warning. Scoped `git diff --check` -> pass.
- PLG5 reference plugin metadata: done. Telegram, MCP Server, System Health
  Checker and Obsidian now declare additive manifest/version, compatibility,
  lifecycle and capability metadata while preserving existing plugin load
  behavior and local-only safety gates.
- Verification: manifest/capability/local-audit/release/lifecycle/bundle plus
  MCP, System Health and Obsidian plugin load tests -> 68 passed, 1 known
  SQLAlchemy deprecation warning. Full Telegram plugin regression -> 101
  passed, 1 known SQLAlchemy deprecation warning. Scoped `git diff --check`
  -> pass.
- PLG6 operator runbook: done. Added
  `docs/plans/plugin-system-hardening-operator-runbook.md` with plugin
  decision states, permission decisions, local review flow, Go language and
  stop rules for registry/install/live gates.
- PLG7 integration: done. Gate Evidence route/core tests plus plugin manifest,
  capability, local audit, release gate, registry, lifecycle, local release
  bundle, MCP, System Health, Obsidian and Telegram tests were rerun together:
  211 passed, 1 known SQLAlchemy deprecation warning. No remote registry,
  plugin install, plugin enablement or live action was performed.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| PLG1 manifest inventory | safe_offline | Alice | docs/plans/plugin-system-hardening-roadmap.md | Docs-only |
| PLG2 schema model | repo_only | Bob | `src/plugin_manifest_policy.py`, tests | plugin manifest tests |
| PLG3 capability tiers | repo_only | Bob | `src/plugin_capability_boundary.py`, tests | capability tests |
| PLG4 lifecycle/readiness | repo_only | Bob | plugin readiness modules, tests | plugin readiness tests |
| PLG5 reference plugin metadata | repo_only | Bob | plugin manifests only | plugin load tests |
| PLG6 operator runbook | safe_offline | Alice | docs/plans | Docs-only |
| PLG7 integration | repo_only | Charlie | tests/docs | focused plugin tests |

## Gate Queue

Gate: `PLG-REMOTE-REGISTRY-GO`
Class: needs_live_go
Blocks: remote registry fetch/install
Decision needed: approve remote plugin registry/network access
Safe preparation done: local schema and audit
Risk if bypassed: untrusted code download/import
Next safe slice: local audit only

Gate: `PLG-BREAKING-SCHEMA-GO`
Class: needs_design
Blocks: requiring all existing plugins to update manifest immediately
Decision needed: compatibility period or hard cutover
Safe preparation done: additive schema fields
Risk if bypassed: reference plugins fail to load
Next safe slice: additive validation

## Paths

Alice path:
- write operator language for plugin states and permissions
- document install/update/disable/quarantine decisions

Bob path:
- implement schema/tier/lifecycle models
- add compatibility adapters
- update reference metadata incrementally

Charlie path:
- guard against plugin runtime activation
- run plugin load and local audit tests

## Verification

- `pytest tests/test_plugin_manifest_policy.py`
- `pytest tests/test_plugin_capability_boundary.py`
- `pytest tests/test_plugin_local_audit.py`
- `pytest tests/test_plugin_registry.py`
- `pytest tests/test_plugin_release_gate.py`
- `pytest tests/test_plugin_obsidian_load.py tests/test_telegram_plugin.py tests/test_mcp_server_plugin.py tests/test_system_health_checker_plugin.py`
- `git diff --check`

## Go Language

- Go: schema, tiers, lifecycle and local audit pass for installed reference
  plugins without changing live behavior.
- Partial: schema exists but some plugins remain compatibility-only.
- Deferred: remote registry and live install are gated.
- No-Go: plugin import can execute unreviewed host/network side effects.
