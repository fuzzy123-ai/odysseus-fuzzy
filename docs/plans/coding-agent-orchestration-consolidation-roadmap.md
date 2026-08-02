# Coding Agent Orchestration Consolidation Roadmap

Status: CAO1-CAO7 plus CAO-08/CAO-08J roadmap authoring and CAO-08A/CAO-08B/CAO-08B1/CAO-08C implementation accepted; CAO-08C1 is waiting on a later Terra writer, while CAO-08D is selected but implementation-gated on REPO-CLEANUP-ONE-WORKTREE and CAO-MASTER-ROUTE-VERIFICATION under Standard ABC

ABC mode: Standard ABC

## Goal

Unify Coding Agent, Server Project Runner, Repo Control, Sandbox Worker and
Orchestration Runtime around one lifecycle from intake to scoped task, worktree,
patch, test, review, handoff, publish plan and verified done.

## Current Evidence

- Coding agent contracts live in `src/coding_agent_backend.py`,
  `src/coding_agent_runner_state.py`, `src/coding_agent_sandbox_bridge.py`,
  `routes/coding_agent_routes.py` and related tests.
- Server project surfaces live in `src/server_project_*` modules and
  `routes/server_project_routes.py`.
- Orchestration surfaces live in PlanRuntime, handoff mailbox, heartbeat,
  thread lifecycle, quality gates and runtime loop modules.
- Workspace/sandbox policies exist in `src/workspace_policy.py`,
  `src/agent_sandbox_contract.py`, `src/sandbox_*`.
- CAO1 now provides `docs/plans/coding-orchestration-lifecycle-contract.md`,
  defining canonical lifecycle stages, status tokens, identifier mapping and
  live gates across Coding Agent, Server Project Runner, Sandbox Worker and
  Orchestration Runtime.
- CAO2 now provides `src/coding_lifecycle.py` and
  `tests/test_coding_lifecycle.py`, defining the side-effect-free
  `odysseus.coding_lifecycle.v1` view over existing Coding Agent plan, runner
  state, sandbox dispatch, quality gate, handoff and publish-plan payloads.
- CAO3 now provides `src/coding_lifecycle_adapters.py` and
  `tests/test_coding_lifecycle_adapters.py`, mapping Coding Agent task IDs,
  Server Project project/task IDs, Orchestration node IDs, agent run IDs,
  check job IDs, gate IDs, handoff refs and publish-plan refs into the shared
  identifier map without dispatching work or exposing raw objectives/output.
- CAO4 now provides `src/coding_quality_alignment.py` and
  `tests/test_coding_quality_alignment.py`, aligning Coding quality reports and
  sandbox dispatch statuses with Gate Evidence Core and reusable redacted
  `ResultEvidenceBundle` payloads.
- CAO5 now adds route compatibility fields to `routes/coding_agent_routes.py`
  and `routes/server_project_routes.py`, exposing canonical lifecycle,
  identifier-map and quality-alignment payloads next to existing response keys.
- CAO6 now provides `docs/plans/coding-publish-live-gates-runbook.md`,
  documenting publish/live gate IDs, required operator inputs, stop rules,
  decision language and handoff cards for git writes, live thread dispatch,
  sandbox live execution and route cleanup.
- CAO7 now provides `docs/plans/coding-orchestration-integration-review.md`,
  mapping lifecycle, identifier, quality, route, runner, sandbox, orchestration
  and workspace evidence to the focused integration suite and remaining gates.
- Current frontier: CAO-08A provides the accepted Planning-bound resumable
  lifecycle authority, CAO-08B provides the accepted content-free,
  revision-bound RAPTOR/GraphRAG context boundary for all four checkpoints,
  and CAO-08B1 provides accepted immutable role-scoped implementer, tester and
  independent-reviewer subagent capsules. CAO-08C provides the accepted pure
  Planning-bound controller, canonical user-gate resume and local/fake model
  intent boundary. CAO-08C1 remains the fully specified fenced bounded
  delegation slice but is `waiting_on_peer` after the permitted agent-channel
  recovery was exhausted without any claimed file being created. CAO-08D is
  independently dependency-ready, but implementation must wait for a verified
  Sol master route or an explicit user authorization of the observed
  `surface_default/root` route. Even after that gate closes, CAO-08D may not
  activate a real worker adapter or external dispatch.

## Mode

Standard ABC. Repo-only for contracts and dry-run execution. Real git push,
deploy, external thread sends or host actions require gates.

## Non-goals

- Do not start autonomous coding jobs live.
- Do not push, deploy or create PRs as part of this roadmap.
- Do not weaken repo registry, sandbox or quality gates.
- Do not merge server-project and coding-agent routes in one breaking change.

## What Must Be Done

- Define one canonical coding lifecycle.
- Map Coding Agent task ids, Server Project task runs, Agent Runs and
  Orchestration nodes to shared identifiers.
- Align quality gate result shape with Gate Evidence Core.
- Make sandbox result and artifact policy reusable.
- Define publish plan as preview-only until explicit Git/PR Go.
- Add one quick-status/readiness model that legacy chat and dashboard can use.
- Create migration adapters before route cleanup.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| CAO1 lifecycle inventory | safe_offline | Alice | roadmap and lifecycle doc | Done: `docs/plans/coding-orchestration-lifecycle-contract.md` |
| CAO2 canonical lifecycle model | repo_only | Bob | `src/coding_lifecycle.py`, tests | Done: focused and broader coding/orchestration tests |
| CAO3 identifier adapters | repo_only | Bob | coding/server/orchestration adapter modules | Done: focused and broader adapter tests |
| CAO4 quality/sandbox alignment | repo_only | Bob | quality and sandbox modules | Done: focused and broader quality/sandbox tests |
| CAO5 route compatibility | repo_only | Bob | coding/server route additive fields | Done: focused and broader route tests |
| CAO6 publish/live gates | safe_offline | Alice | docs/runbook | Done: `docs/plans/coding-publish-live-gates-runbook.md` |
| CAO7 integration | repo_only | Charlie | tests/docs | Done: integration review plus focused suite |

## Execution Progress

2026-07-06:
- CAO1 lifecycle inventory done as a docs-only safe_offline slice.
  `docs/plans/coding-orchestration-lifecycle-contract.md` maps Coding Agent
  backend plans, runner state, sandbox dispatch, Server Project Runner,
  Orchestration Runtime, workspace/sandbox policy and quality gates into a
  canonical intake -> scoped-task -> worktree-plan -> patch-plan ->
  checks-plan -> checks-result -> review-gate -> handoff -> publish-plan ->
  verified-done lifecycle.
- CAO1 verification passed: docs-only scoped `git diff --check`.
- CAO2 canonical lifecycle model done as a repo_only slice.
  `src/coding_lifecycle.py` exposes canonical intake -> scoped-task ->
  worktree-plan -> patch-plan -> checks-plan -> checks-result -> review-gate
  -> handoff -> publish-plan -> verified-done stage ordering, normalized
  lifecycle status tokens, redacted IDs/evidence refs, gate aggregation,
  next-action derivation and a runtime event with `side_effects=("none",)`.
  `CAO-GIT-WRITE-GO` remains required when a publish preview is ready and live
  git writes are not explicitly allowed.
- CAO2 verification passed: compile check, focused
  `tests/test_coding_lifecycle.py` with 5 tests, and broader
  coding/server/orchestration/quality/workspace coverage with 86 tests.
- CAO3 identifier adapters done as a repo_only slice.
  `src/coding_lifecycle_adapters.py` exposes the
  `odysseus.coding_lifecycle.identifier_map.v1` adapter layer for Coding Agent,
  Server Project and Orchestration surfaces. It derives deterministic
  server-project task IDs, check-job IDs, handoff refs and publish-plan refs,
  merges compatible maps, rejects conflicting canonical IDs and emits runtime
  events with `side_effects=("none",)`.
- CAO3 verification passed: compile check, focused
  `tests/test_coding_lifecycle_adapters.py` with 5 tests, and broader
  lifecycle/coding/server/orchestration/quality/workspace coverage with 91
  tests.
- CAO4 quality/sandbox alignment done as a repo_only slice.
  `src/coding_quality_alignment.py` maps `CodingQualityGateReport` and
  `CodingSandboxDispatch`-style payloads into `CanonicalGate`,
  `ResultEvidenceBundle` and `what_can_safely_happen_now` summaries. Sandbox
  statuses become summary-only artifacts under `reports/sandbox/<job>.log`;
  stdout/stderr previews, secrets and private paths are not persisted in the
  canonical evidence.
- CAO4 verification passed: compile check, focused
  `tests/test_coding_quality_alignment.py` with 7 tests, and broader
  lifecycle/coding/sandbox/result-observer/gate-evidence/server/orchestration
  coverage with 117 tests.
- CAO5 route compatibility done as a repo_only slice.
  Coding Agent routes now return additive `coding_lifecycle`,
  `coding_lifecycle_identifiers` and `coding_quality_alignment` payloads where
  relevant while preserving legacy keys such as `coding_task`, `quality_gate`,
  `sandbox_dispatch`, `done_gate`, `handoff_plan`, `publish_plan` and
  `subagents_plan`. Server Project routes now expose
  `coding_lifecycle_identifiers` next to project/task/commit/push responses.
- CAO5 verification passed: compile check, focused
  `tests/test_coding_route_compatibility.py` with 3 tests, existing coding and
  server-project route compatibility coverage with 35 tests, and broader
  lifecycle/coding/sandbox/result-observer/gate-evidence/server/
  orchestration/quality/workspace coverage with 135 tests.
- CAO6 publish/live gates done as a docs-only safe_offline slice.
  `docs/plans/coding-publish-live-gates-runbook.md` defines the operator
  approval packet for `CAO-GIT-WRITE-GO`, `CAO-LIVE-THREAD-DISPATCH`,
  `CAO-SANDBOX-LIVE-EXECUTION-GO` and `CAO-ROUTE-CLEANUP-GO`, including
  Go/Partial/Deferred/No-Go/Blocked wording, required evidence, stop rules and
  handoff card templates.
- CAO6 verification passed: docs-only scoped whitespace/diff checks.
- CAO7 integration review done as a repo_only tests/docs slice.
  `docs/plans/coding-orchestration-integration-review.md` records the
  compatibility findings across lifecycle state, identifier maps, quality and
  sandbox alignment, Coding Agent routes, Server Project routes, runner state,
  orchestration runtime, quality gates and workspace policy. It keeps git
  writes, live thread dispatch, sandbox live execution and route cleanup behind
  explicit gates.
- CAO7 verification passed: focused coding/server/orchestration/quality/
  workspace suite with 101 tests and the same known SQLAlchemy deprecation
  warning, plus docs-only whitespace/diff checks.

## CAO-08 Production Coding-Loop Extension

### Authority and graph boundaries

Versioned Planning/Roadmap JSON, its dependency graph, gate queue, durable
claims and accepted evidence are the execution authority. A model, a chat
turn, a tool result, a skill, or a graph retrieval may propose work or provide
evidence; none may create authority, widen a claim, or mark an unaccepted
result true.

The production loop keeps three separate, linked graphs:

| Graph | Purpose | Authority boundary |
| --- | --- | --- |
| Planning graph | Roadmaps, slices, dependencies, claims, gates, owners, accepted evidence and resumable lifecycle state. | The only graph that controls what may run next. |
| Code graph | Commit-bound files, symbols, dependencies, tests, diagnostics and exact-read references. | Derived technical context only; it cannot approve scope, gates or writes. |
| Causal graph | Bounded hypotheses, actions, observations, failures, repairs and verification outcomes. | Learning and audit context only; it cannot turn a hypothesis or a passing command into acceptance. |

RAPTOR/GraphRAG and codebase memory are bounded retrieval, evidence and
learning systems. They may supply commit-bound context, source references and
reviewed learning candidates, but are never a truth controller, planning
authority, gate owner, or implicit permission to act. A stale, unavailable or
conflicting retrieval must yield a visible warning and fall back to exact source
reads and Planning-graph authority.

### Resumable lifecycle and required memory checkpoints

The canonical production state machine is:

```text
clarifying -> planning -> ready_for_claim -> claimed -> context_building
-> context_ready -> worktree_ready -> acting -> verifying
-> repair_planning -> acting | review_ready -> memory_review
-> publish_ready -> done

waiting | blocked may be entered from every state and retain the last accepted
state, claim, gate, evidence references, and explicit resume condition.
```

No transition may be inferred from narrative progress. Each transition records
the planning-node id, actor id, input commit/worktree ref, bounded evidence
refs, decision, and next owner. `repair_planning` may return to `acting` only
for the same accepted scope and bounded repair budget; it must enter `waiting`
or `blocked` when escalation is required.

Four checkpoint contracts are mandatory:

1. **Pre-plan retrieval:** retrieve only bounded Planning, Code and Causal
   graph context for the proposed objective; record retrieval freshness and
   source refs before planning.
2. **Pre-slice context envelope:** after claim and before acting, construct a
   commit-bound envelope containing allowed and blocked paths, exact objective,
   acceptance checks, current worktree/ref, selected code evidence, relevant
   prior causal evidence, tool capabilities, budgets and stop rules.
3. **Failure retrieval:** after each failed verification or rejected patch,
   query relevant causal/code evidence within the active envelope before a
   repair plan; do not carry raw logs or uncontrolled historical context.
4. **Post-acceptance writeback:** only after independent acceptance, create
   reviewable Code/Causal/Memory write intents with provenance. Persisted
   learning is a consequence of acceptance, never evidence that acceptance
   happened.

An exact code read is mandatory before every edit. The read receipt must name
the commit/worktree ref, normalized path, content hash, line/range or bounded
anchor, reader actor, and time. A patch/apply request must bind to that receipt;
any changed hash, ambiguous anchor, scope mismatch or stale worktree blocks the
edit and requires a new exact read. Semantic search, a symbol summary or a
GraphRAG snippet never substitutes for the exact-read receipt.

### Gate owners and escalation

Gate ownership is explicit and is not determined by whichever agent made the
last tool call:

| Owner class | May decide automatically | Must not decide |
| --- | --- | --- |
| `machine_auto` | Schema/path/ref validation, claim compatibility, exact-read freshness, deterministic policy checks, command allowlists, evidence shape, test exit status and repair-budget accounting. | Product acceptance, architecture choice, security/privacy policy, ambiguous scope, destructive or external effects. |
| `agent_auto` | Routine implementation within an accepted envelope, bounded test execution, repair planning, and independent evidence review. The reviewer identity must differ from the implementer and the implementer cannot self-approve. | User-only decisions or approving its own work, even when tests pass. |
| `user_acceptance` | UI/UX decisions; material product, architecture or scope decisions; auth/security/privacy policy; destructive, external or live actions; final acceptance when requested by the Planning graph. | Delegating these decisions to a model, retrieval system or automatic test gate. |

Routine implementation, focused tests and a bounded repair may proceed without
an additional user turn only while their claim, context envelope and
`machine_auto` checks remain valid and an independent `agent_auto` reviewer is
available. The loop escalates to `waiting_on_user` (or `waiting_on_peer` for a
missing reviewer) on: ambiguous acceptance criteria; requested scope expansion;
a material architecture or product decision; auth/security/privacy risk;
overlapping foreign work; or two failed approaches. "Two approaches" means two
distinct, evidence-recorded repair plans, not two retries of the same command.

### Defaults and non-goals

- Harbor One currently uses exactly one registered, clean canonical Git
  worktree. Concurrent lanes may share it only through disjoint exact-path
  claims; shared router/hotfiles remain single-writer. Additional Git worktrees
  are disabled until the user explicitly changes this policy. Execution
  isolation still comes from argv-only, no-network, no-secrets sandbox jobs and
  redacted evidence, not from accumulating Git worktrees.
- The generic host `bash`/`python` surface is not a coding-loop executor. A
  coding intent must use the scoped worktree and sandbox controller; any legacy
  shell use remains a separately policy-gated non-coding capability.
- A dry run, generated publish plan, self-review, GraphRAG result, or passing
  weak test is not a completed coding task.
- This extension does not implement UI placement, auto-commit/push/PR/deploy,
  arbitrary host shell, automatic architectural decisions, raw-log memory, or
  a claim that real code intelligence/browser/evaluation gates already exist.

### CAO-08D scoped execution-plane contract

CAO-08D is the only bridge from a CAO-08C bounded-check intent to a sandbox job
plan. It consumes exactly one `request_bounded_check` intent already stored in
the supplied controller state; it does not accept a raw model prompt, objective,
shell command string, patch intent or exact-read request. The execution request
must repeat and match the controller state ID, intent ID, Planning item and
revision, claim ID and owner, scope digest, input revision, tester capsule ID,
acceptance-check ref, tool-capability ref and a bounded argv tuple. The
acceptance-check and capability refs must already exist in the tester capsule.

The reducer validates that the controller lifecycle is `verifying`, the intent
belongs to the tester capsule, all Planning/claim/scope/input bindings are
current, the claim scope is non-empty and repository-relative, and no claim
path intersects blocked scope. It reuses the canonical coding-check allowlist;
unknown executables, shell strings or operators, response-file escapes,
environment injection, private or absolute paths and arguments outside the
claim scope fail closed. A model cannot select an arbitrary image or expand a
runtime profile: the selected profile is a trusted mapping referenced by the
capsule, and its timeout cannot exceed the capsule time budget.

On success the reducer emits a deterministic, replay-safe plan containing
`SandboxJobRequest` values with read-only claim-scope mounts, `network_mode`
`none`, an empty network allowlist, no secrets, bounded resources and only the
minimal profile capabilities. The accompanying descriptor always reports
`dispatch_allowed: false`, `execution_performed: false`, `live_effect_allowed:
false`, content-free evidence refs and no raw output. An identical request is
idempotent; reuse of its request ref with different semantic facts is rejected.

This slice may validate and render jobs and may reduce injected, content-free
fake status receipts. It must not import or call `subprocess`, the generic tool
executor, host shell helpers, `SandboxWorker`, route handlers, graph/memory
writers, a real dispatch adapter, or any live process. Activating a worker,
running Podman, attaching writable mounts/network/secrets, or ingesting raw
stdout/stderr remains a separately claimed and action-specific gate. Scoped
patch application and exact-read receipts remain CAO-08E responsibilities.

After CAO-08D passes its Terra handoff, root/Sol deep review and focused
integration lane, the ABC run pauses before any CAO-08E claim. Root reports the
accepted D evidence and waits for the user's cleanup pass; no successor slice
is implicitly opened by CAO-08D acceptance.

### Cross-roadmap ownership boundary

CAO owns the canonical lifecycle/controller, Planning/Code/Causal graph
boundaries, exact-read-to-edit integration, and the adapters that attach
accepted external evidence to lifecycle transitions. ACPR owns verification and
readiness-lane implementations, their evidence policy, and their accepted
verification contracts. Harbor One owns read-only lifecycle projection, Gate
Inbox presentation, and user-decision UX. CAO consumes those bounded contracts;
it does not reimplement their engines or user interface.

No CAO, ACPR, Harbor One, tool, model, or retrieval track may introduce a
second Planning graph, claim authority, gate authority, or memory truth source.
All three tracks reference the same versioned Planning/Roadmap authority and
emit provenance-bound evidence or projections only. A missing or incompatible
cross-roadmap contract blocks the dependent transition rather than creating a
parallel ledger, gate queue, memory store, or implicit fallback.

### CAO-08J subagent context and bounded delegation

The parent Planning slice and its accepted revision remain the authority for
every child run. RAPTOR supplies bounded hierarchical summaries; GraphRAG
supplies only scope-filtered, commit-bound code/test/dependency relations; and
the Causal graph supplies only accepted failure/repair evidence. These inputs
can reduce discovery cost, but cannot widen a claim, change an acceptance
criterion, choose a gate owner, or close a gate. No child receives a raw memory
dump, and no retrieved summary substitutes for an exact source read before an
edit.

Each child receives a role-specific, immutable capsule derived from the parent
slice revision rather than a copy of the parent conversation:

| Role | Permitted capsule focus | Additional isolation rule |
| --- | --- | --- |
| Implementer | Accepted objective, allowed/blocked paths, exact source refs, worktree/diff state, bounded tools and repair budget. | May act only inside its path and tool policy; cannot approve itself. |
| Tester/diagnostician | Acceptance checks, relevant test/diagnostic evidence, read-only source relations and failure-retrieval envelope. | May diagnose and propose a repair plan; it cannot edit unless Planning explicitly grants a distinct implementer claim. |
| Independent reviewer | Acceptance criteria, independently retrieved Code/Causal evidence, exact source refs, diff/worktree state and verification receipts. | Excludes implementer persuasion, self-evaluation, private chain-of-thought and unreviewed success claims; reviewer identity must differ from implementer. |

Every capsule binds, at minimum: parent run and Planning slice/revision;
child identity and role; allowed and blocked paths; commit, worktree and diff
references; acceptance criteria; exact-source read receipts; retrieval
provenance, freshness and conflict state; tool/network/secrets policy;
token/time/repair budgets; and stop/escalation rules. The broker must redact
private/raw content, reject unavailable or conflicted mandatory context, and
record only bounded evidence references. Exact code reads remain mandatory
immediately before an edit, even when the same path was summarized in a
capsule.

The parent/child lifecycle is:

```text
parent planned -> capsule_ready -> child dispatched -> acknowledged -> running
-> verifying -> handoff_ready -> accepted | rejected

cancelled | expired may interrupt every child state; resume creates or resumes
a fenced child attempt from the last accepted parent state and valid capsule.
```

`planned` has no executable child; `capsule_ready` is a validated immutable
envelope; `dispatched/acknowledged` proves the intended child and capsule
revision; `running` and `verifying` consume budget; and `handoff_ready` carries
evidence rather than authority. Only the parent Planning state plus the
appropriate `machine_auto`, independent `agent_auto`, or `user_acceptance`
gate can mark a child accepted or rejected. A child cannot widen scope, replace
its reviewer, or recursively delegate unless the parent Planning revision
explicitly permits the exact child role and scope.

Delegation defaults are bounded: fanout is at most two children per parent
slice, delegation depth is one, and each dispatch has an idempotency key derived
from parent run/slice revision, role, scope and attempt. A monotonically
increasing fence token binds every tool/event/handoff to the current attempt;
older, duplicate, expired or cancelled attempts cannot write state or satisfy a
gate. Parent cancellation revokes active child leases, prevents new tool calls,
and records a bounded cancellation receipt; resume requires a fresh policy,
budget and exact-read freshness check. Larger fanout/depth or real external
thread dispatch requires an explicit Planning decision and retains the separate
`CAO-LIVE-THREAD-DISPATCH` live gate. OPS-Alert, security, homeserver and their
associated operational paths are excluded from child capsules and delegation
claims.

One-time activation of a bounded worker adapter is a `user_acceptance`
decision. Until that activation is recorded in accepted Planning policy, the
current runtime remains local/fake only and this contract grants no dispatch
authority. After activation, ordinary internal child dispatches are
`machine_auto`/`agent_auto`: they may proceed without interrupting the user per
child only when they remain inside the current slice/claim, capsule,
fanout/depth, tool/network/secrets and token/time/repair budgets, and retain an
independent reviewer where required. User acceptance remains mandatory for an
external or cross-workspace/thread dispatch, an expanded fanout/depth or scope,
a material product/architecture/security/privacy decision, and every
destructive or live effect.

This extension instantiates, rather than changes, the existing four checkpoint
contract: role capsules are the pre-slice context envelope; failed child
verification invokes the bounded failure retrieval checkpoint; and only a
parent-approved, independently accepted handoff may create post-acceptance
Code/Causal/Memory write intents. Pre-plan retrieval remains parent-owned.

### Dependency-ordered implementation queue

| Slice | Class | Depends on | Allowed paths/types | Acceptance evidence and default | Non-goals |
| --- | --- | --- | --- | --- | --- |
| CAO-08A planning authority and lifecycle contract | `repo_only` | CAO-08 authoring acceptance | `src/coding_lifecycle.py`, lifecycle/adaptor contracts, focused tests, this roadmap | Tests reject transitions without planning/claim/evidence identity; preserve legacy compatibility fields; default is `waiting` on missing authority. | No route/tool execution or graph write. |
| CAO-08B context-envelope and graph-boundary adapter | `repo_only` | 08A | New bounded coding-context adapter, `context_capsule`/code-memory contracts, focused tests | Pre-plan and pre-slice checkpoints emit redacted, commit-bound envelopes; unavailable/stale retrieval is visible and fail-safe. | No graph becomes an execution authority; no raw source/log persistence. |
| CAO-08B1 role-scoped context broker | `repo_only` | 08A, 08B; CAO-08J authoring acceptance | New capsule broker/contracts, Code/Causal retrieval adapters, focused tests | Generates immutable implementer/tester/reviewer capsules bound to parent revision, exact refs, provenance/freshness/conflict, policies and budgets; reviewer retrieval is independent and excludes implementer self-evaluation. Default blocks on missing/conflicted mandatory context. | Raw memory dumps, authority changes, or edits without a fresh exact source read. |
| CAO-08C production coding-loop controller | `repo_only` | 08A, 08B, 08B1 | New controller, `agent_loop`/tool schema/dispatch integration, coding-route adapters, focused loop tests | Fake-model integration proves clarifying through review-ready state while every mutation request is routed through the controller; default denies an unscoped coding tool call. | No direct `app_api` bridge or generic shell fallback. |
| CAO-08C1 bounded delegation runtime | `repo_only` | 08B1, 08C | Parent/child lifecycle runtime, lease/fence/idempotency/cancel contracts, focused tests | Enforces role-specific capsules, max fanout/depth defaults, fenced/idempotent dispatch, cancel/expiry/resume and no recursive delegation without explicit Planning permission. Default is local/fake dispatch only; after a separately user-accepted adapter activation, ordinary in-slice children within the accepted envelope are machine/agent automatic. | Granting current runtime authority, per-child user interrupts, real external/cross-workspace/thread dispatch, OPS-Alert/security/homeserver paths, child scope widening or reviewer self-approval. |
| CAO-08D scoped execution-plane consolidation | `repo_only` | 08C | Coding backend/sandbox bridge, tool policy, subprocess boundaries, tests | Coding intents cannot invoke host-shell execution; allowed checks render argv-only sandbox jobs with no network/secrets by default. | Removing generic shell for unrelated approved workflows; live sandbox execution. |
| CAO-08E transactional worktree edit and exact-read receipts | `repo_only` | 08B, 08C, 08D | Coding backend patch/worktree service, edit contracts, tests | Atomic patch transaction binds every changed file to a fresh exact-read receipt, produces canonical diff/evidence, and rolls back on conflict or partial failure. | AST/LSP implementation, auto merge/rebase, delete/move without separate decision. |
| CAO-08F code-intelligence and failure-retrieval plane | `repo_only` | 08B, 08E | Code graph query adapters/tools, source-index contracts, tests/evaluation fixtures | Symbol/definition/reference/impact/diagnostic results are commit-bound, scoped and followed by exact reads before edit; failure checkpoint retrieves only envelope-bounded causal/code evidence. | Treating search ranking as truth or enabling an unvalidated external engine. |
| CAO-08G verification, repair and independent-review gates | `repo_only` | 08C1, 08D, 08E, 08F; accepted ACPR VCL/readiness lanes | Lifecycle evidence adapters, quality/review gate contracts, focused integration tests | Consumes ACPR typed static/lint/test/browser evidence and accepted VCL contracts, wires them into lifecycle transitions, escalates after two distinct failed repairs, and requires a capsule-isolated reviewer different from the implementer for `review_ready`. | Implementing duplicate browser/static/evaluation engines, user acceptance, live browser/network execution, or reviewer self-approval. |
| CAO-08H model/tool reliability evaluation and memory writeback | `repo_only` | 08C1, 08G; accepted ACPR offline-eval and failure-intake evidence | Coding model profile/routing, fake-model tool-loop benchmark, Causal/Memory write-intent adapters, tests | Consumes ACPR offline-eval/failure-intake evidence to measure scope compliance, exact-read-before-edit, tool recovery, verification and escalation; writeback occurs only after independent acceptance and is provenance-linked. | Selecting a production model by roadmap assertion alone, duplicate evaluation/failure engines, or unreviewed memory writes. |
| CAO-08I user-acceptance handoff integration | `needs_design` / `needs_live_go` where applicable | 08H; accepted Harbor One Gate Inbox/user-decision UX contract | Additive lifecycle-to-Harbor-One handoff/projection bindings only | Consumes Harbor One gate UX so user-only decisions show evidence, alternatives, impact and rollback; no action starts until explicit recorded acceptance. | Implementing Gate Inbox/UI placement here, commit/push/PR/deploy, auth-policy changes, or external sends. |

### Known-gap closure map

The current generic host `bash` path versus safe coding backend split is closed
by CAO-08C/08D. The absence of a real production LLM/tool loop is CAO-08C; the
existing fake subagent surface is insufficient until CAO-08B1/CAO-08C1 supply
role capsules and bounded/fenced delegation, and CAO-08G supplies
independent-review identity and evidence gates. Missing transactional edits are
CAO-08E. Real code intelligence remains off until CAO-08F validates it.
Browser, static-analysis and model/tool reliability gates remain absent until
CAO-08G/08H; their future evidence must not be backdated into CAO1-CAO7.

Root/Sol deep review accepted CAO-08 on 2026-08-01 after separating CAO, ACPR
and Harbor One ownership and removing competing lifecycle, verification and UI
truth. CAO-08A was accepted on 2026-08-02 after one bounded fix cycle and a
92-test lifecycle/route/runner/Planning/orchestration integration lane. CAO-08B
was accepted on 2026-08-02 after a documented writer transfer, two bounded fix
cycles and a final 115-test lifecycle/context/Planning/workspace integration
lane. CAO-08B1 was accepted after one bounded root fix cycle and a final
122-test lifecycle/context/subagent-capsule integration lane. CAO-08C was
accepted after a documented writer transfer, two bounded root fix cycles, a
final 150-test CAO integration lane and a 9-test adjacent compatibility lane.
CAO-08C1 remains dependency-ready but `waiting_on_peer`; no implementation
evidence was manufactured. CAO-08D is the current repo-only planning frontier,
but its implementation is blocked by CAO-MASTER-ROUTE-VERIFICATION. The historical
CAO1-CAO7 evidence remains accepted and is not reinterpreted as evidence for
the new production-loop behaviors.

## Gate Queue

Gate: `CAO-GIT-WRITE-GO`
Class: needs_live_go
Blocks: real commit, push, PR, branch or remote mutation
Decision needed: approve exact repo, branch, remote and action
Safe preparation done: publish plan preview
Risk if bypassed: unintended source mutation
Next safe slice: dry-run publish plan

Gate: `CAO-LIVE-THREAD-DISPATCH`
Class: needs_live_go
Blocks: one-time bounded worker-adapter activation and sending work to real
external or cross-workspace threads/agents
Decision needed: approve exact adapter/target, workspace boundary, stop
criteria, fanout/depth and rollback/cancel path
Safe preparation done: dry-run ThreadDispatchRequest and local/fake bounded
delegation contract
Risk if bypassed: unreviewed cross-boundary work or runaway autonomous work
Next safe slice: fake thread lifecycle tests; after accepted activation,
ordinary in-slice child dispatch remains subject to its Planning envelope and
does not require a user interruption per child

## Paths

Alice path:
- define lifecycle language and operator gates
- document publish/live boundaries

Bob path:
- implement lifecycle and adapters
- align quality and sandbox results

Charlie path:
- prevent behavior-changing route cleanup until compatibility exists
- run coding/server/orchestration tests

## Verification

- `pytest tests/test_coding_agent_backend.py`
- `pytest tests/test_coding_agent_runner_state.py`
- `pytest tests/test_coding_agent_sandbox_bridge.py`
- `pytest tests/test_server_project_runner.py`
- `pytest tests/test_server_project_task_runner.py`
- `pytest tests/test_orchestration_runtime_loop.py`
- `pytest tests/test_quality_gates.py`
- `pytest tests/test_workspace_policy.py`
- `git diff --check`

## Go Language

- Go: one lifecycle model maps existing coding/server/orchestration states
  without weakening sandbox or quality gates.
- Partial: adapters exist but routes still expose old local names.
- Deferred: real git, deploy and thread dispatch wait for live Go.
- No-Go: autonomous execution starts without bounded approval.
