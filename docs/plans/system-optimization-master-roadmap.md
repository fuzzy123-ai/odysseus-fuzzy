# System Optimization Master Roadmap

Date: 2026-07-05

Status: repo-only complete under Standard ABC; live/design/breaking gates deferred

ABC mode: Standard ABC

## Goal

Turn the completed Odysseus backend roadmaps into a simpler, safer and more
operable platform by consolidating duplicated gate, evidence, plugin, memory,
inbox, Telegram, coding, MCP, ops and architecture surfaces.

## Why This Exists

The previous roadmap wave made Odysseus much more capable than legacy
Odysseus: Plugin Runtime, PlanRuntime, Universal Inbox, Secure Data Mode,
ORCA/Lens, Telegram, MCP, Coding Agent, System Health, Release Evidence and
many live gates now exist. The next risk is not missing capability. The risk is
fragmentation: many subsystems solve similar status, gate, evidence, review,
redaction and readiness problems in slightly different ways.

This master roadmap is a consolidation plan. It does not claim that the
existing systems are broken. It identifies the places where mature product
operation needs shared contracts, fewer duplicated patterns and clearer
operator surfaces.

## Current Evidence Reviewed

- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/open-work-completion-master-roadmap.json`
- `docs/plans/legacy-chat-new-functions-integration-roadmap.md`
- `docs/plans/mvp-master-roadmap.md`
- `src/plugin_system.py`
- `src/plan_runtime.py`
- `src/secure_policy_gate.py`
- `src/universal_inbox_pipeline.py`
- `src/orchestration_runtime_loop.py`
- `src/coding_agent_backend.py`
- `src/mcp_server_tool_policy.py`
- `src/nextcloud_webdav_client.py`
- `plugins/telegram/plugin.py`
- `plugins/system_health_checker/plugin.py`
- `src/image_tools_worker.py`
- `docs/plans/system-optimization-repo-only-integration-review.md`
- `src/github_issue_sync.py`
- `src/browser_devtools_evidence.py`
- `src/visual_observer_evidence.py`
- `src/observability_diagnostics_bridge.py`
- `src/security_incident_model.py`
- `routes/roadmap_routes.py`
- `routes/universal_inbox_routes.py`
- `routes/legacy_chat_contract_routes.py`
- `routes/version_one_readiness_routes.py`

Test inventory signals found broad coverage around Agent/Orchestration,
Release/Evidence, Memory/RAG, Security/Policy, Plugin/MCP, Nextcloud/Universal
Inbox, Observability/System Health, Calendar/Tasks, Telegram, Browser/Visual
and GitHub Issue Intelligence. This plan should preserve those guarantees.

## Master Principles

- Prefer one shared contract over ten local variants.
- Keep all live/provider/Telegram/Nextcloud/host/deploy/write actions gated.
- Keep raw private content, tokens, chat ids, private paths and provider output
  out of docs, tests, ledgers and evidence.
- Refactor in characterization-first slices. Do not rewrite hot paths before
  route, model and redaction behavior is pinned down.
- UI work is allowed only as `needs_design` unless a slice explicitly creates a
  small backend contract or static manifest for a UI agent.
- Telegram plugin simplification is not optional. It is part of the dedicated
  Telegram rework roadmap and must avoid behavior loss.

## Roadmap Set

| # | Roadmap | Priority | Class Bias | Why |
| -: | --- | ---: | --- | --- |
| 1 | `gate-evidence-core-rework-roadmap.md` | P0 | repo_only | Gates, evidence and readiness are duplicated across many systems. |
| 2 | `operator-dashboard-review-queue-roadmap.md` | P0 | needs_design + repo_only | Operators need one place to see status, gates, review and next actions. |
| 3 | `plugin-system-hardening-roadmap.md` | P0 | repo_only | Plugins are now foundational and need stronger lifecycle and capability policy. |
| 4 | `memory-raptorgraph-consolidation-roadmap.md` | P1 | repo_only | Memory, RAG, RaptorGraph and ORCA/Lens need one canonical write/read model. |
| 5 | `universal-inbox-nextcloud-flow-rework-roadmap.md` | P1 | repo_only + needs_live_go | Inbox, Nextcloud and Memory Write Intent need to feel like one workflow. |
| 6 | `telegram-plugin-refactor-roadmap.md` | P1 | repo_only | The Telegram plugin is powerful but too broad; split and characterize it. |
| 7 | `coding-agent-orchestration-consolidation-roadmap.md` | P1 | repo_only | Coding Agent, Server Project Runner and Orchestration overlap. |
| 8 | `ops-security-console-roadmap.md` | P2 | repo_only + needs_live_go | Observability, System Health and Security Ops should share an ops console model. |
| 9 | `mcp-workbench-productization-roadmap.md` | P2 | repo_only | MCP needs per-client scopes, policy previews and audit before broader exposure. |
| 10 | `codebase-architecture-cleanup-roadmap.md` | P2 | repo_only | Module sprawl should be reduced after contracts are stabilized. |

## Execution Order

1. Gate/Evidence Core first. It becomes the vocabulary used by later roadmaps.
2. Plugin System Hardening second. Refactors and new work should know plugin
   permission and capability boundaries.
3. Telegram Plugin Refactor third, because Telegram touches Inbox, reminders,
   notifications, screenshots, voice and live actions.
4. Universal Inbox/Nextcloud Flow and Memory/RaptorGraph can proceed in
   parallel only after shared gate vocabulary is stable.
5. Coding/Orchestration consolidation follows once Gate Core and Tool Truth
   are aligned.
6. MCP Workbench and Ops/Security Console can then consume the shared policy
   and evidence surfaces.
7. Codebase Architecture Cleanup is last; it should move proven boundaries,
   not invent them.

## Execution Progress

2026-07-05:
- Roadmap 2, Operator Dashboard / Review Queue: ODR2 snapshot model is done
  additively. `src/operator_dashboard_snapshot.py` normalizes already-produced
  review gate, live affordance, task, diagnostics, version-readiness and
  orchestration payloads into the
  `odysseus.operator_dashboard.snapshot.v1` read-only dashboard contract. It
  emits safe section counts, statuses, schemas, evidence hashes and read-only
  next actions, keeps approve/execute controls policy-gated, and does not call
  providers, routes, live systems or write APIs. ODR3 review queue model is
  also done additively: `src/operator_review_queue.py` converts existing
  review-gate, live-affordance, coding-approval and security-review payloads
  into the `odysseus.operator_review_queue.v1` read-only queue contract for
  Nextcloud copy, Memory write, RaptorGraph write, file export, security
  action, Telegram delivery and coding approval items. ODR4 route is done
  additively: `routes/operator_dashboard_routes.py` exposes the admin-gated
  `GET /api/operator-dashboard/snapshot` route and `app.py` wires it next to
  the existing review-gate/readiness routes. The route now gathers local
  read-only review gates, live affordance readiness, task summaries, operator
  quick status diagnostics and version readiness by default, while preserving
  provider injection for tests. ODR1 contract and ODR6 integration review are
  done in `docs/plans/operator-dashboard-review-queue-contract.md` and
  `docs/plans/operator-dashboard-review-queue-integration-review.md`. Roadmap 2
  is repo-only backend complete; UI placement and live action buttons remain
  deferred behind gates.
- Roadmap 1, Gate/Evidence Core: additive core, adapters, route compatibility,
  migration map and integration review are done. Breaking route-shape cleanup
  remains deferred behind `GEC-ROUTE-SHAPE-GO`.
- Roadmap 3, Plugin System Hardening: local schema, permission tiers,
  capability tiers, lifecycle/readiness, reference plugin metadata, operator
  runbook and integration review are done. Remote registry/install and breaking
  schema cleanup remain deferred behind `PLG-REMOTE-REGISTRY-GO` and
  `PLG-BREAKING-SCHEMA-GO`.
- Roadmap 4, Memory/RaptorGraph Consolidation: MEM1 lifecycle inventory is done
  as a docs-only safe_offline slice. `docs/plans/memory-lifecycle-contract.md`
  defines the canonical source metadata -> extracted abstraction -> policy
  review -> memory write intent -> memory record -> provenance event -> graph
  event -> diagnostics budget -> rebuild dry-run lifecycle, with redaction
  rules and a compatibility map for Obsidian Raptor cache, RAG import,
  Universal Inbox abstraction, ORCA/Lens graph mutation and memory diagnostics.
  MEM2 canonical lifecycle model is also done additively:
  `src/memory_lifecycle.py` exposes the
  `odysseus.memory_lifecycle.v1` state model and validator, normalizing
  already-produced Memory/RAG/RaptorGraph dry-run/status payloads without
  performing writes, graph mutation, reindex, rebuild or migration. Sensitive
  fields are stripped or hashed and runtime events record
  `side_effects=("none",)`.
  MEM3 write-intent adapters are done additively:
  `src/memory_lifecycle_adapters.py` maps Universal Inbox memory write intents,
  read-only RAG reindex dry-run plans, manual/web-research memory candidates
  and ORCA/Lens-style RaptorGraph candidate mappings into the canonical
  lifecycle without persistence or live mutation.
  MEM4 provenance alignment is done additively:
  `src/memory_provenance_alignment.py` links source hashes, deterministic RAG
  chunk refs, memory record IDs, redacted provenance records and RaptorGraph
  event IDs in a read-only alignment plan while rejecting mismatches and
  raw/private path markers.
  MEM5 diagnostics consolidation is done additively:
  `src/memory_diagnostics_consolidation.py` translates lifecycle state,
  provenance alignment and store budget summaries into existing
  `DiagnosticSnapshot`/`DiagnosticMetric` contracts plus readiness-by-family
  and readiness-gate summaries, without live checks.
  MEM6 legacy naming migration map is done as a docs-only safe_offline slice:
  `docs/plans/memory-legacy-naming-migration-map.md` maps Obsidian,
  RAPTOR/RaptorGraph, RAG, ORCA/Lens and Universal Inbox vocabulary to the
  canonical Memory lifecycle terms, with compatibility classes and rename order.
  Legacy route/tool/data-path removal remains behind `MEM-LEGACY-REMOVAL-GO`.
- Roadmap 5, Universal Inbox/Nextcloud Flow: UIX2 canonical state model is done
  additively. `src/universal_inbox_flow_state.py` defines the metadata-only
  received -> classified -> extracted -> abstracted -> reviewed -> routed ->
  copied/exported -> memory-intent -> graph-provenance flow, hashes source
  references, redacts paths, filenames, WebDAV/URL fields, chat ids, secrets
  and raw content, and keeps live writes disabled by default.
  UIX3 Nextcloud adapter is also done additively:
  `src/nextcloud_universal_inbox_flow_adapter.py` maps existing Nextcloud import
  dry-run reports, transfer-readiness plans, live-readiness checks and optional
  transfer results into the canonical flow state without scanning Nextcloud,
  calling WebDAV, copying files, starting workers or writing Memory/RaptorGraph
  data. `allow_live_write` remains opt-in and default-off.
  UIX4 review reason unification is done additively:
  `src/universal_inbox_review_reasons.py` normalizes legacy and surface-specific
  reason aliases into canonical codes and classifies review/no-go reasons by
  category, severity and canonical flow stage. The flow state now exposes
  `review_reason_details` and `no_go_reason_details` while preserving existing
  tuple reason fields.
  UIX5 route contract is done additively:
  `routes/universal_inbox_routes.py` now exposes
  `GET /api/universal-inbox/items/{source_ref}/flow-state` next to the existing
  status route. It reuses the upload backend, auth, owner/admin and source-ref
  validation checks, then returns the redacted canonical flow-state payload with
  live writes disabled.
  UIX8 integration review is done in
  `docs/plans/universal-inbox-nextcloud-flow-integration-review.md`, tying
  UIX2-UIX5 together under one repo-only evidence packet while leaving
  safe-area policy, live Nextcloud copy/write and Memory/RaptorGraph writes
  explicitly deferred behind gates.
- Roadmap 7, Coding Agent / Orchestration Consolidation: CAO1 lifecycle
  inventory is done as a docs-only safe_offline slice.
  `docs/plans/coding-orchestration-lifecycle-contract.md` defines the canonical
  intake -> scoped-task -> worktree-plan -> patch-plan -> checks-plan ->
  checks-result -> review-gate -> handoff -> publish-plan -> verified-done
  lifecycle, maps task/repo/node/check/gate/handoff/publish identifiers and
  keeps git/PR/deploy/thread dispatch behind explicit live gates.
  CAO2 canonical lifecycle model is done additively:
  `src/coding_lifecycle.py` exposes the side-effect-free
  `odysseus.coding_lifecycle.v1` lifecycle state over existing Coding Agent
  plan, runner state, sandbox dispatch, quality gate, handoff and publish-plan
  payloads. It redacts objective/raw output/host path/secret material, derives
  gates and next actions, and keeps publish-ready git writes behind
  `CAO-GIT-WRITE-GO`.
  CAO3 identifier adapters are done additively:
  `src/coding_lifecycle_adapters.py` maps Coding Agent task IDs, Server Project
  project/task IDs, Orchestration node IDs, agent runs, check jobs, gate IDs,
  handoff refs and publish-plan refs into
  `odysseus.coding_lifecycle.identifier_map.v1` without dispatching work,
  touching git, or exposing raw objectives/output/private paths.
  CAO4 quality/sandbox alignment is done additively:
  `src/coding_quality_alignment.py` maps Coding quality reports and sandbox
  dispatch statuses into Gate Evidence Core `CanonicalGate` payloads, reusable
  redacted `ResultEvidenceBundle` artifacts and `what_can_safely_happen_now`
  summaries, without preserving stdout/stderr previews, secrets or private
  paths.
  CAO5 route compatibility is done additively:
  `routes/coding_agent_routes.py` now returns canonical `coding_lifecycle`,
  `coding_lifecycle_identifiers` and `coding_quality_alignment` payloads next
  to existing Coding Agent response keys, and `routes/server_project_routes.py`
  returns `coding_lifecycle_identifiers` next to Server Project project/task/
  commit/push responses without breaking existing route shapes.
  CAO6 publish/live gates is done as a docs-only safe_offline slice:
  `docs/plans/coding-publish-live-gates-runbook.md` documents exact operator
  input and evidence requirements for git writes, live thread dispatch, sandbox
  live execution and route cleanup, preserving preview-only defaults and
  requiring explicit bounded Go before any live action.
  CAO7 integration review is done as a repo_only tests/docs slice:
  `docs/plans/coding-orchestration-integration-review.md` maps lifecycle,
  identifier, quality, route, runner, sandbox, orchestration and workspace
  evidence to the focused integration suite and keeps the remaining live or
  breaking cleanup actions behind explicit gates.
- Roadmap 9, MCP Workbench Productization: MCP1 policy inventory is done as a
  docs-only safe_offline slice. `docs/plans/mcp-workbench-policy-inventory.md`
  documents existing MCP tool exposure categories, disabled/read-only defaults,
  plugin boundaries, productization gaps and live-client/private-read/
  filesystem-read/owner-write/generic-API gates without enabling the MCP server
  or connecting a client.
  MCP2 client profile model is done additively: `src/mcp_client_profiles.py`
  defines the side-effect-free `odysseus.mcp.client_profile.v1` profile model
  and maps active per-client scopes into `McpToolPolicyOptions` while requiring
  owner, reason and expiry evidence for enabled sensitive access.
  MCP3 policy preview is done additively: `src/mcp_policy_preview.py` produces
  `odysseus.mcp.policy_preview.v1` previews with exposed/hidden counts,
  per-tool category/reason/gate details, required gate IDs and redacted
  client-profile context while leaving live client connection disabled.
  MCP4 audit events is done additively: `src/mcp_audit_events.py` defines the
  `odysseus.mcp.audit_event.v1` redacted audit model for MCP method/tool/
  resource access, gate attribution and safe metadata summaries.
  MCP5 config compatibility is done additively:
  `src/mcp_config_compatibility.py` defines
  `odysseus.mcp.config_compatibility.v1`, preserving disabled/read-only
  defaults, migrating legacy scope aliases and keeping `expose_all`
  unsupported without writing plugin config or enabling MCP.
  MCP6 setup runbook is done as a docs-only safe_offline slice:
  `docs/plans/mcp-workbench-setup-runbook.md` documents the safe setup order,
  decision language, required review packet, sensitive-scope gate mapping, stop
  rules and live smoke handoff card.
- Roadmap 10, Codebase Architecture Cleanup: ARC1 dependency inventory is done
  as a safe_offline docs slice:
  `docs/plans/codebase-architecture-dependency-inventory.md` records candidate
  boundaries for agent, orchestration, memory, inbox, integrations, ops,
  security, release, plugins, tools, workspace and visual domains while keeping
  route paths, plugin manifests and public API schemas stable. ARC2 import map
  generator is done additively: `scripts/architecture_import_map.py` parses
  Python files with `ast`, never imports project modules, classifies modules
  into candidate domains and emits `odysseus.architecture_import_map.v1` with
  `side_effects=("none",)`, `files_moved=False` and
  `imports_executed=False`. ARC3 boundary contract is done in
  `docs/plans/codebase-architecture-boundary-contract.md`, requiring import-map
  evidence, stable public routes/schemas, compatibility aliases, separate
  behavior-vs-move slices and explicit gates before alias removal or broad
  moves. ARC4 first package move and ARC5 compatibility aliases are done for
  the operator dashboard backend models: implementations now live under
  `src/operator_dashboard/`, `routes/operator_dashboard_routes.py` imports from
  the package facade and the old `src/operator_dashboard_snapshot.py` plus
  `src/operator_review_queue.py` paths remain thin compatibility aliases.
  ARC6 integration review is done in
  `docs/plans/codebase-architecture-integration-review.md`, mapping inventory,
  import-map tooling, boundary rules, the first package move, aliases and route
  consumer update to focused verification. Roadmap 10 is repo-only complete;
  broad moves remain blocked behind `ARC-BROAD-MOVE-GO`; compatibility alias
  removal remains gated by `ARC-COMPAT-REMOVAL-GO`.
- Roadmap 8, Ops Security Console: OPS1 surface inventory is done as a
  docs-only safe_offline slice. `docs/plans/ops-security-console-contract.md`
  maps System Health, Observability diagnostics, alert routing, Security
  Incident, Security Response Policy and prepare-only remediation planning into
  one read-only-first timeline, status, redaction and gate vocabulary.
  OPS2 timeline model is done additively: `src/ops_timeline.py` exposes the
  `odysseus.ops_timeline.v1` packet and `odysseus.ops_timeline.event.v1`
  event model for signal, triage, evidence, decision, action-plan,
  operator-gate and handoff stages across System Health, Observability,
  Diagnostics, Security and Remediation surfaces. It sorts read-only ops
  events, hashes sensitive evidence/correlation references, rejects unsafe
  summaries and requires explicit operator gates for containment/lockdown.
  OPS3 incident/action adapters are done additively:
  `src/ops_timeline_adapters.py` maps existing System Health dashboard
  summaries, Observability diagnostic packets, alert routes, Security Incident
  payloads, Security Response Policy decisions and prepare-only Remediation
  plans into canonical timeline events without live queries, host commands or
  writes. It preserves remediation/operator-gate requirements and hashes
  sensitive legacy evidence references.
  OPS4 route snapshot is done additively: `src/ops_console_snapshot.py` builds
  a conservative read-only Ops Console snapshot over the canonical timeline,
  source states, operator gates and Security/Remediation readiness packets, and
  `routes/ops_console_routes.py` exposes `GET /api/ops-console/snapshot` behind
  the existing admin gate. `app.py` registers the route without enabling live
  host, observability or remediation actions.
  OPS5 tabletop packet is done additively: `src/ops_tabletop_packet.py` builds
  and validates deterministic synthetic `odysseus.ops_tabletop_packet.v1`
  packets over Security Incident, Response Policy, prepare-only Remediation and
  Ops Console Snapshot contracts. It records expected operator steps,
  assertions, policy/remediation decisions and live-gate requirements while
  keeping raw content/logs/host paths/tokens/chat targets/live actions/host
  commands/writes/remediation disabled.
  OPS6 live ops runbook is done as docs-only preparation:
  `docs/plans/ops-security-console-live-runbook.md` defines exact
  Go/Partial/Deferred/No-Go/Blocked language, gate-specific required inputs,
  stop rules and handoff cards for host-agent, observability live query, alert
  delivery and remediation gates. It does not grant live permission and no live
  action was performed.
  OPS7 integration review is done as a repo_only tests/docs slice:
  `docs/plans/ops-security-console-integration-review.md` maps OPS1-OPS6
  artifacts to the focused integration suite, records the read-only/redaction
  guarantees, confirms the admin-gated snapshot route contract and leaves
  host-agent, observability live query, alert delivery and remediation actions
  deferred behind explicit operator gates.
- Roadmap 6, Telegram Plugin Refactor: TGR1 inventory, TGR2 route/tool/command
  characterization and TGR3 shared redacted status/gate model are done
  additively. TGR4 route registration split is done: admin/status/history/app
  routes live in `plugins/telegram/routes_admin.py`, polling in
  `plugins/telegram/routes_polling.py`, outbound/document delivery in
  `plugins/telegram/routes_outbound.py`, and webhook registration in
  `plugins/telegram/routes_webhook.py`. TGR5 has started with
  `plugins/telegram/webhook_service.py` handling webhook parse/store intake,
  invalid-update redaction, media-pipeline delegation, the shared public
  webhook response envelope and the redacted Universal Inbox attachment event
  payload. Export-plan and export-delivery event payloads are now also
  redacted service helpers, and export/project-intake response summaries now
  live in the webhook service. Control-command event payloads and public
  summaries are now service-owned as well, and agent-turn event payloads no
  longer live inline in the route. Project-intake preview/reply branch execution,
  attachment branch execution, recent attachment export branch execution,
  webhook control-command branch execution and webhook agent-turn branch
  execution are now service-owned behind injected helpers. Agent-Task control
  command execution and its redacted public task-record view now live in
  `plugins/telegram/control_service.py` behind compatibility wrappers. DSGVO
  control command orchestration is now also service-owned behind injected
  settings, reply, pin and bridge helpers. Kalender control command
  orchestration, command-tail parsing, reminder/todo digest argument parsing and
  Telegram calendar reply formatting are now service-owned behind injected
  Calendar Capability helpers. TGR6 has also moved Agent-Task control reply
  wording for status, help and pause/resume/cancel acknowledgements plus
  Project-Intake review apply/hold wording and Universal-Inbox review confirm/
  memory-write wording into the shared formatting module while leaving
  ledger/apply/event/transfer orchestration in
  `plugins/telegram/control_service.py`. TGR5/TGR6/TGR8 integration review is
  complete in `docs/plans/telegram-plugin-refactor-integration-review.md`,
  mapping route modules, status gates, webhook service, control service,
  polling and compatibility wrappers to the focused Telegram suite. Roadmap 6
  is repo-only complete; live send and voice download smokes remain deferred
  behind Telegram live gates.
- System Optimization repo-only integration review is done in
  `docs/plans/system-optimization-repo-only-integration-review.md`. It maps
  all ten roadmap repo-only statuses, remaining live/design/breaking gates and
  recent focused verification evidence. The master track is now repo-only
  complete; remaining work requires explicit operator decisions rather than
  another safe repo-only slice.
- Current verification: focused Gate Evidence plus Plugin Hardening suite
  passed with 211 tests and 1 known SQLAlchemy deprecation warning. Roadmap 2
  ODR2 verification passed with focused snapshot coverage:
  `tests/test_operator_dashboard_snapshot.py` passed with 3 tests and the same
  known warning. Roadmap 2 ODR3 verification passed with focused review queue
  coverage: `tests/test_operator_review_queue.py` passed with 3 tests and the
  same known warning. Roadmap 2 ODR4 verification passed with combined
  snapshot, review queue and route coverage:
  `tests/test_operator_dashboard_snapshot.py`,
  `tests/test_operator_review_queue.py` and
  `tests/test_operator_dashboard_routes.py` passed with 9 tests and the same
  known warning. Roadmap 2 ODR6 verification passed with focused compile plus
  the same ODR model/route suite: 9 tests passed with the same known warning.
  Roadmap 10 ARC2 verification passed with
  `tests/test_architecture_import_map.py`: 3 tests passed with the same known
  warning. A repo import-map smoke returned
  `odysseus.architecture_import_map.v1` with 845 Python files scanned, 842
  modules parsed, 3 parse errors recorded and 1333 local cross-domain edges.
  Roadmap 10 ARC4/ARC5 verification passed with focused compile plus
  operator-dashboard and architecture coverage:
  `tests/test_operator_dashboard_snapshot.py`,
  `tests/test_operator_review_queue.py`, `tests/test_operator_dashboard_routes.py`
  and `tests/test_architecture_import_map.py` passed with 14 tests and the same
  known warning. A post-move repo import-map smoke returned 848 Python files
  scanned, 845 modules parsed, 3 parse errors recorded and 1333 local
  cross-domain edges. Roadmap 10 ARC6 integration review verification passed
  with the same focused compile, model/route/architecture suite and post-move
  import-map smoke evidence.
  Focused
  Memory/RaptorGraph MEM1 verification passed as docs-only scoped
  `git diff --check`. Roadmap 4 MEM2 verification passed with compile plus
  focused lifecycle coverage: 4 tests passed. Broader Memory/RAG/RaptorGraph
  contract coverage across lifecycle, write policy, provenance ledger, store
  interfaces, Universal Inbox memory write intent, Nextcloud RaptorGraph
  provenance, RAG text/chunk quality and progressive graph API passed with 52
  tests and the same known warning; scoped `git diff --check` passed. Roadmap 4
  MEM3 verification passed with compile plus focused lifecycle/adapter coverage:
  8 tests passed. Broader Memory/RAG/RaptorGraph contract coverage across
  lifecycle, adapters, write policy, provenance ledger, store interfaces,
  Universal Inbox memory write intent, Nextcloud RaptorGraph provenance, RAG
  text/chunk quality, RAG reindex dry-run, RaptorGraph candidate mapping,
  progressive graph API and memory candidate schema passed with 63 tests and
  the same known warning; scoped `git diff --check` passed. Roadmap 4 MEM4
  verification passed with compile plus focused provenance/chunk/graph coverage:
  29 tests passed. Broader Memory/RAG/RaptorGraph contract coverage across
  lifecycle, adapters, provenance alignment, write policy, provenance ledger,
  store interfaces, Universal Inbox memory write intent, Nextcloud RaptorGraph
  provenance, RAG text/chunk quality, RAG reindex dry-run, RaptorGraph
  candidate mapping, progressive graph API and memory candidate schema passed
  with 66 tests and the same known warning; scoped `git diff --check` passed.
  Roadmap 4 MEM5 verification passed with compile plus focused
  diagnostics/lifecycle/alignment coverage: 18 tests passed. Broader
  Memory/RAG/RaptorGraph contract coverage across diagnostics consolidation,
  diagnostics snapshots, lifecycle, adapters, provenance alignment, write
  policy, provenance ledger, store interfaces, Universal Inbox memory write
  intent, Nextcloud RaptorGraph provenance, RAG text/chunk quality, RAG reindex
  dry-run, RaptorGraph candidate mapping, progressive graph API and memory
  candidate schema passed with 77 tests and the same known warning; scoped
  `git diff --check` passed. Roadmap 4 MEM6 verification passed as docs-only
  scoped `git diff --check`. Roadmap 7 CAO1 verification passed as docs-only
  scoped `git diff --check`. Roadmap 7 CAO2 verification passed with compile
  plus focused lifecycle coverage: 5 tests passed. Broader
  coding/server/orchestration/quality/workspace coverage passed with 86 tests
  and the same known warning. Roadmap 7 CAO3 verification passed with compile
  plus focused identifier-adapter coverage: 5 tests passed. Broader
  lifecycle/coding/server/orchestration/quality/workspace coverage passed with
  91 tests and the same known warning. Roadmap 7 CAO4 verification passed with
  compile plus focused quality/sandbox alignment coverage: 7 tests passed.
  Broader lifecycle/coding/sandbox/result-observer/gate-evidence/server/
  orchestration/quality/workspace coverage passed with 117 tests and the same
  known warning. Roadmap 7 CAO5 verification passed with compile plus focused
  route-compatibility coverage: 3 tests passed. Existing coding and
  server-project route compatibility coverage passed with 35 tests; broader
  lifecycle/coding/sandbox/result-observer/gate-evidence/server/
  orchestration/quality/workspace coverage passed with 135 tests and the same
  known warning. Roadmap 7 CAO6 verification passed as docs-only scoped
  whitespace/diff checks. Roadmap 7 CAO7 verification passed with the focused
  coding/server/orchestration/quality/workspace integration suite: 101 tests
  passed with the same known SQLAlchemy deprecation warning, plus
  docs-only whitespace/diff checks. Roadmap 9 MCP1 verification passed as
  docs-only scoped whitespace/diff checks. Roadmap 9 MCP2 verification passed
  with compile plus focused client-profile/policy coverage: 14 tests passed
  with the same known SQLAlchemy deprecation warning. Roadmap 9 MCP3
  verification passed with compile plus focused policy-preview/client-profile/
  policy coverage: 18 tests passed with the same known SQLAlchemy deprecation
  warning. Roadmap 9 MCP4 verification passed with compile plus focused
  audit/preview/client-profile/policy coverage: 22 tests passed with the same
  known SQLAlchemy deprecation warning. Roadmap 9 MCP5 verification passed
  with compile plus focused config/audit/preview/client-profile/policy
  coverage: 26 tests passed with the same known SQLAlchemy deprecation warning.
  Roadmap 9 MCP6 verification passed as docs-only scoped whitespace/diff
  checks. Roadmap 8 OPS1 verification passed as docs-only scoped
  whitespace/diff checks. Roadmap 8 OPS2 verification passed with compile plus
  focused timeline coverage: 5 tests passed with the same known SQLAlchemy
  deprecation warning. Roadmap 8 OPS3 verification passed with compile plus
  focused timeline/adapter coverage: 10 tests passed with the same known
  SQLAlchemy deprecation warning. Roadmap 8 OPS4 verification passed with
  compile plus focused snapshot/route coverage: 5 tests passed with the same
  known SQLAlchemy deprecation warning. Roadmap 8 OPS5 verification passed with
  compile plus focused tabletop-packet coverage: 5 tests passed with the same
  known SQLAlchemy deprecation warning. Roadmap 8 OPS6 verification passed as
  docs-only scoped whitespace/diff checks. Roadmap 8 OPS7 verification passed
  with the focused OPS integration suite across tabletop, snapshot, timeline,
  adapters, System Health, Observability and Security contracts: 63 tests
  passed with the same known SQLAlchemy deprecation warning, plus docs-only
  whitespace/diff checks. Focused Telegram contract/status/readiness/voice/formatting suite passed with 26
  tests and the same known warning. After the completed TGR4 route-registration
  split, the broader Telegram route/plugin/text/voice/image/screenshot/task/truth
  suite passed with 145 tests and the same known warning. After the first TGR5
  webhook-intake service extraction, the broader Telegram suite plus service
  tests passed with 147 tests and the same known warning.
  After the webhook response-envelope service extraction, the focused
  webhook-service/route/plugin suite passed with 16 tests and the same known
  warning.
  After the webhook attachment-event service extraction, the focused
  webhook-service/route/plugin suite passed with 18 tests and the same known
  warning.
  After the webhook export-event service extraction, the focused
  webhook-service/route/plugin/document suite passed with 22 tests and the
  same known warning.
  After the webhook summary service extraction, the focused
  webhook-service/route/plugin/document suite passed with 24 tests and the same
  known warning.
  After the webhook control-command service extraction, the focused
  webhook-service/route/plugin suite passed with 23 tests and the same known
  warning.
  Broader post-TGR5-service-extraction verification passed across route,
  status, webhook service, plugin, text, voice, image, screenshot, task and
  truth suites with 157 tests and the same known warning.
  After the webhook agent-turn event service extraction, the focused
  webhook-service/route/plugin suite passed with 23 tests and the same known
  warning.
  After the webhook project-intake branch service extraction, the focused
  webhook-service/route/plugin suite passed with 25 tests and the same known
  warning.
  Broader post-project-intake-service verification passed across route, status,
  webhook service, plugin, text, voice, image, screenshot, task and truth suites
  with 161 tests and the same known warning.
  After the webhook attachment branch service extraction, the focused
  webhook-service/route/plugin suite passed with 28 tests and the same known
  warning.
  The Telegram redaction regression test for reply history was hardened against
  incidental timestamp digit matches while still asserting no raw chat-id JSON
  value is persisted. Broader post-attachment-service verification passed across
  route, status, webhook service, plugin, text, voice, image, screenshot, task
  and truth suites with 163 tests and the same known warning.
  After the webhook export branch service extraction, the focused
  webhook-service/route/plugin/document suite passed with 34 tests and the same
  known warning. Broader post-export-branch-service verification passed across
  route, status, webhook service, plugin, text, voice, image, screenshot, task
  and truth suites with 168 tests and the same known warning.
  After the webhook control-command branch service extraction, the focused
  webhook-service/route/plugin suite passed with 34 tests and the same known
  warning. Broader post-control-branch-service verification passed across route,
  status, webhook service, plugin, text, voice, image, screenshot, task and truth
  suites with 170 tests and the same known warning.
  After the webhook agent-turn branch service extraction, the focused
  webhook-service/route/plugin suite passed with 37 tests and the same known
  warning. Broader post-agent-turn-branch-service verification passed across
  route, status, webhook service, plugin, text, voice, image, screenshot, task
  and truth suites with 172 tests and the same known warning.
  After the Agent-Task control service split, the focused
  control-service/route/plugin/ops-smoke suite passed with 14 tests and the same
  known warning. Broader post-agent-task-control-service verification passed
  across control service, route, status, webhook service, plugin, text, voice,
  image, screenshot, task, truth and ops-smoke suites with 178 tests and the same
  known warning.
  After the DSGVO control service split, the focused control-service/route/plugin
  suite passed with 16 tests and the same known warning. Broader
  post-dsgvo-control-service verification passed across control service, route,
  status, webhook service, plugin, text, voice, image, screenshot, task, truth
  and ops-smoke suites with 181 tests and the same known warning.
  After the Kalender control service split, the focused
  control-service/route/plugin suite passed with 18 tests and the same known
  warning. Broader post-calendar-control-service verification passed across
  control service, route, status, webhook service, plugin, text, voice, image,
  screenshot, task, truth and ops-smoke suites with 184 tests and the same known
  warning.
  After the Universal Inbox control service split, focused compile plus
  control-service/route/plugin review suites passed with 24 tests and the same
  known warning. The slice moved Universal Inbox status, Nextcloud review
  confirm/status and Memory/Raptor review confirm/status orchestration behind
  injected helpers in `plugins/telegram/control_service.py`; `plugin.py`
  now delegates this branch while keeping concrete repo/local helpers and live
  gates. Broader post-universal-inbox-control-service verification passed across
  control service, route, status, webhook service, plugin, text, voice, image,
  screenshot, task, truth and ops-smoke suites with 188 tests and the same known
  warning. The scheduled todo-digest notification defect was also fixed and
  hardened in the shared notification contract: Telegram todo-digest deliveries,
  including legacy tasks identified by name/body instead of an `action` field,
  render as plain multiline text without the generic `[Odysseus]` prefix or
  metadata footer, and the digest/notification/delivery suite passed with 18
  tests and the same known warning.
  After the Project-Intake control service split, focused compile plus
  control-service/route/plugin Project-Intake suites passed with 23 tests and
  the same known warning. The slice moved Project-Intake review status,
  confirm and hold orchestration behind injected helpers in
  `plugins/telegram/control_service.py`; `plugin.py` now delegates this branch
  while keeping concrete Project-Intake apply/format helpers and registry paths
  local. Broader post-project-intake-control-service verification passed across
  control service, route, status, webhook service, plugin, text, voice, image,
  screenshot, task, truth and ops-smoke suites with 191 tests and the same known
  warning.
  After the New-Chat control service split, focused compile plus
  control-service/route/plugin `/new` suites passed with 26 tests and the same
  known warning. The slice moved `/new` session rebinding, reply text and
  bound/pending status selection behind injected helpers in
  `plugins/telegram/control_service.py`; `plugin.py` now delegates the branch.
  Broader post-new-chat-control-service verification passed across control
  service, route, status, webhook service, plugin, text, voice, image,
  screenshot, task, truth and ops-smoke suites with 193 tests and the same known
  warning.
  TGR6 formatting normalization started with deterministic calendar control
  reply formatters moved into `plugins/telegram/formatting.py`; the control
  service now imports shared formatting helpers instead of carrying local reply
  wording. Focused compile plus formatting/control/route/calendar suites passed
  with 40 tests and the same known warning. Broader
  post-calendar-formatting-normalization verification passed across formatting,
  control service, route, status, webhook service, plugin, text, voice, image,
  screenshot, task, truth and ops-smoke suites with 207 tests and the same known
  warning.
  TGR6 formatting normalization continued with deterministic DSGVO control
  reply wording moved into `plugins/telegram/formatting.py`; `plugin.py` keeps
  only a thin `_dsgvo_reply_text` wrapper to provide current runtime mode.
  Focused compile plus formatting/control/route/DSGVO suites passed with 43
  tests and the same known warning. Broader post-dsgvo-formatting-normalization
  verification passed across formatting, control service, route, status,
  webhook service, plugin, text, voice, image, screenshot, task, truth and
  ops-smoke suites with 208 tests and the same known warning.
  TGR6 formatting normalization continued with Universal Inbox review and
  Memory/Raptor review status wording moved into
  `plugins/telegram/formatting.py`; `plugins/telegram/attachments.py` keeps
  compatibility wrappers for the previous underscored helper names. Focused
  compile plus formatting/attachment/control/Universal-Inbox suites passed with
  43 tests and the same known warning. Broader
  post-universal-inbox-review-formatting-normalization verification passed
  across formatting, attachment OCR, control service, route, status, webhook
  service, plugin, text, voice, image, screenshot, task, truth and ops-smoke
  suites with 215 tests and the same known warning.
  TGR6 formatting normalization continued with Project-Intake preview and
  review-status wording moved into `plugins/telegram/formatting.py`;
  `plugins/telegram/project_intake.py` keeps compatibility wrappers for the
  previous helper names. Focused compile plus formatting/control/Project-Intake
  suites passed with 37 tests and the same known warning. Broader
  post-project-intake-formatting-normalization verification passed across
  formatting, attachment OCR, control service, route, status, webhook service,
  plugin, text, voice, image, screenshot, task, truth and ops-smoke suites with
  216 tests and the same known warning.
  TGR6 formatting normalization continued with recent attachment-export reply
  wording moved into `plugins/telegram/formatting.py`;
  `plugins/telegram/export.py` keeps a compatibility wrapper for the previous
  public helper name. Focused compile plus formatting/webhook/export polling
  suites passed with 22 tests and the same known warning. Broader
  post-attachment-export-formatting-normalization verification passed across
  formatting, attachment OCR, control service, route, status, webhook service,
  plugin, text, voice, image, screenshot, task, truth and ops-smoke suites with
  217 tests and the same known warning.
  TGR6 formatting normalization continued with attachment-inbox reply and OCR
  warning wording moved into `plugins/telegram/formatting.py`;
  `plugins/telegram/attachments.py` delegates the active helper names to the
  shared formatting module. Focused compile plus formatting/OCR/attachment
  polling suites passed with 29 tests and the same known warning. Broader
  post-attachment-inbox-formatting-normalization verification passed across
  formatting, attachment OCR, control service, route, status, webhook service,
  plugin, text, voice, image, screenshot, task, truth and ops-smoke suites with
  219 tests and the same known warning. The pre-existing mojibake-heavy
  unreachable compatibility body after the early wrapper return in
  `plugins/telegram/attachments.py` was removed in the TGR8 cleanup pass.
  TGR6 formatting normalization continued with Nextcloud-transfer blocker
  wording moved into `plugins/telegram/formatting.py`;
  `plugins/telegram/plugin.py` keeps a compatibility wrapper for the previous
  underscored helper name. Focused compile plus formatting/control/Nextcloud
  review suites passed with 42 tests and the same known warning. Broader
  post-nextcloud-transfer-blocked-formatting-normalization verification passed
  across formatting, attachment OCR, control service, route, status, webhook
  service, plugin, text, voice, image, screenshot, task, truth and ops-smoke
  suites with 220 tests and the same known warning.
  TGR6 formatting normalization continued with agent-failure fallback wording
  moved into `plugins/telegram/formatting.py`; `plugins/telegram/polling.py`
  delegates the active `_agent_failure_reply` helper to the shared formatter.
  Focused compile plus formatting/webhook-agent-turn suites passed with 24
  tests and the same known warning; polling-focused verification passed with 2
  tests and the same known warning. Broader
  post-agent-failure-formatting-normalization verification passed across
  formatting, attachment OCR, control service, route, status, webhook service,
  plugin, text, voice, image, screenshot, task, truth and ops-smoke suites with
  221 tests and the same known warning. The mojibake-heavy unreachable
  compatibility body after the early wrapper return in
  `plugins/telegram/polling.py` was removed in the TGR8 cleanup pass.
  TGR6 formatting normalization continued with Agent-Task control reply wording
  moved into `plugins/telegram/formatting.py`; focused compile plus
  formatting/control-service verification passed with 41 tests and the same
  known warning.
  TGR6 formatting normalization continued with Project-Intake review apply and
  hold wording moved into `plugins/telegram/formatting.py`; focused compile
  plus formatting/control-service verification passed with 41 tests and the
  same known warning.
  TGR6 formatting normalization continued with Universal-Inbox review missing,
  Nextcloud transfer confirm/dry-run and memory review/write wording moved into
  `plugins/telegram/formatting.py`; focused compile plus formatting/control
  service verification passed with 41 tests and the same known warning.
  TGR6 formatting normalization continued with Calendar unknown-command and
  command-error wording moved into `plugins/telegram/formatting.py`; focused
  compile plus formatting/control service verification passed with 41 tests and
  the same known warning.
  TGR6 formatting normalization continued with Project-Intake missing-review
  and new-chat success/pending wording moved into
  `plugins/telegram/formatting.py`; focused compile plus formatting/control
  service verification passed with 41 tests and the same known warning.
  TGR6 formatting normalization continued with shared agent-turn reply
  selection moved into `plugins/telegram/formatting.py` and reused by polling
  and webhook service; focused compile plus formatting/webhook/polling
  verification passed with 28 tests and the same known warning.
  TGR8 cleanup completed for the known unreachable compatibility bodies:
  `plugins/telegram/attachments.py` keeps only the active attachment-inbox/OCR
  wrappers, and `plugins/telegram/polling.py` keeps only the active
  agent-failure wrapper. Focused compile plus formatting/attachment/polling
  suites passed with 33 tests and the same known warning; broader Telegram
  verification passed across formatting, attachment OCR, control service,
  route, status, webhook service, plugin, text, voice, image, screenshot, task,
  truth and ops-smoke suites with 221 tests and the same known warning.
  Roadmap 6 TGR9 integration review verification passed with focused compile
  plus the broad repo-only Telegram suite across formatting, attachment OCR,
  control service, route contract, status, webhook service, plugin, text,
  voice, image, screenshot, task, truth and ops-smoke coverage: 223 tests
  passed with the same known warning.
  Roadmap 5 UIX2 verification passed with compile plus
  `tests/test_universal_inbox_flow_state.py`,
  `tests/test_universal_inbox_pipeline.py`,
  `tests/test_universal_inbox_status_routes.py`,
  `tests/test_nextcloud_import_report.py` and
  `tests/test_universal_inbox_memory_write_intent.py`: 20 tests passed with the
  same known warning; scoped `git diff --check` passed.
  Roadmap 5 UIX3 verification passed with compile plus Nextcloud/UIX dry-run
  coverage across flow adapter, flow state, pipeline, policy, status routes,
  import report, transfer readiness, live-readiness summary, transfer executor,
  local extraction review, WebDAV client and memory write intent: 70 tests
  passed with the same known warning; scoped `git diff --check` passed.
  Roadmap 5 UIX4 verification passed with compile plus UIX/Nextcloud review
  reason, flow state, adapter, pipeline, policy, status route, import report,
  transfer readiness, live-readiness, transfer executor, local extraction
  review, WebDAV, memory intent, routing and placement coverage: 92 tests
  passed with the same known warning; scoped `git diff --check` passed.
  Roadmap 5 UIX5 verification passed with compile plus focused route, flow-state
  and review-reason coverage: 14 tests passed. Broader UIX/Nextcloud dry-run
  coverage across status route, review reasons, flow state, adapter, pipeline,
  policy, import report, transfer readiness, live-readiness, transfer executor,
  local extraction review, WebDAV, memory intent, routing and placement passed
  with 96 tests and the same known SQLAlchemy warning; scoped `git diff --check`
  passed.
  Roadmap 5 UIX8 integration review verification passed with focused compile
  plus broad repo-only UIX/Nextcloud dry-run coverage across status routes,
  review reasons, flow state, Nextcloud adapter, pipeline, policy, import
  report, transfer readiness, live-readiness, transfer executor, local
  extraction review, WebDAV, memory intent, routing and placement: 96 tests
  passed with the same known SQLAlchemy warning. Live writes and durable memory
  writes remain deferred behind explicit gates.

## Global Stop Rules

- Stop if a slice would require live Telegram, Nextcloud, provider, host,
  deploy, backup, restore, Cloudflare, GitHub write or remediation action
  without explicit bounded operator Go.
- Stop if a doc, test, log or fixture would persist raw private content,
  private paths, tokens, chat ids, secrets or provider outputs.
- Stop if existing dirty files outside the selected slice must be changed.
- Stop if a refactor would mix behavior changes with file moves before
  characterization tests exist.
- Stop if legacy UI or v2 UI hotfiles become required without a `needs_design`
  decision.

## Gate Queue

Gate: `OPT-UI-PLACEMENT`
Class: needs_design
Blocks: operator dashboard, review queue, final UI placement for readiness
Decision needed: choose whether UI agent wires legacy chat, Lens, dashboard, or
new v2 surface first
Safe preparation done: backend contracts and roadmaps can be created without UI
Risk if bypassed: duplicated dashboards and confusing operator state
Next safe slice: Gate/Evidence Core

Gate: `OPT-LIVE-SMOKE`
Class: needs_live_go
Blocks: live validation for Telegram, Nextcloud, MCP, host-agent, observability
Decision needed: approve one bounded live smoke at a time
Safe preparation done: dry-run contracts and redacted evidence models
Risk if bypassed: live mutation or secret exposure without audit boundary
Next safe slice: repo-only characterization tests

Gate: `OPT-ARCH-MOVE-GO`
Class: blocked
Blocks: broad module moves and package re-layout
Decision needed: allow only after public import map and characterization suite
Safe preparation done: architecture cleanup roadmap defines discovery first
Risk if bypassed: accidental import breakage across a large codebase
Next safe slice: module dependency inventory

## Verification

- Docs-only slices: `git diff --check`.
- JSON-affecting slices: `python -m json.tool <file>`.
- Model/contract slices: focused tests named in each roadmap.
- Refactor slices: characterization tests before and after file moves.
- Final consolidation: route contract smoke, plugin load tests, tool policy
  tests, redaction tests and MVP/readiness summary checks.

## Go Language

- Go: shared contract exists, focused tests pass, no live action implied.
- Partial: contract exists but one consumer still uses legacy local shape.
- Deferred: UI/live/operator decision is intentionally parked.
- Blocked: proceeding would require unsafe live, secret, destructive or broad
  refactor action.
- No-Go: raw private data, secrets, host paths or unapproved live actions would
  be exposed.
