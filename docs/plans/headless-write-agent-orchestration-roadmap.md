# Headless Write Agent Orchestration Roadmap

Date: 2026-07-13

Status: **active under Standard ABC; HWA0-HWA1 complete, durable state/fencing is next; Temporal Light is the sole durable execution coordinator; all real worker, provider, merge and deploy actions remain separately gated**

## Master Integration

This roadmap is a normalized follow-up to:

- `docs/plans/automated-agent-handoff-orchestration-mvp.md`
- `docs/plans/subagent-runtime-v1-roadmap.md`
- `docs/plans/automated-agent-n-scaling-design.md`
- `docs/plans/coding-agent-orchestration-consolidation-roadmap.md`
- `docs/plans/project-versioning-forge-provider-roadmap.md`

It is routed by `docs/plans/central-abc-masterplan-2026-06-29.md` and
`docs/plans/unified-odysseus-roadmap.md`. Current open-work and live-gate state
still comes from `docs/plans/open-work-completion-master-roadmap.json`.

The user explicitly opened this follow-up on 2026-07-13. That approval allows
repo-only contracts, validators, fake adapters, dry-run workers and tests. It
does not approve a real provider write, branch mutation, merge, deployment,
service restart, host change or external thread send.

## Goal

Odysseus can run bounded headless coding agents that write within leased file
scopes and, after independently verified evidence, request the same project
publication workflow as an interactive agent:

```text
approved slice
  -> leased headless subagent run
  -> scoped edits and checks
  -> verified handoff
  -> canonical commit_project
  -> Local Forge version
  -> durable provider outbox
  -> policy-selected Nextcloud/GitHub delivery worker
  -> optional reviewed merge promotion
  -> optional reviewed deploy promotion
```

Commit, push, merge and deploy are not four free-form agent tools. The public
commit authority remains `commit_project`; all later stages consume verified,
durable evidence produced by the preceding stage.

## Frozen Architecture Decisions

1. **One commit authority.** `commit_project` remains the only public
   commit/provider action. Subagents submit a verified commit intent through a
   server-side bridge; they do not receive raw `git commit` or `git push`.
2. **Provider choice is policy-owned.** A headless run cannot name GitHub,
   Nextcloud, a remote, URL or credential. Owner-scoped Project Forge policy
   selects delivery targets after the local commit.
3. **Local first.** A successful local Git commit and Local Forge version are
   required before a provider operation is claimable.
4. **Push is delivery, not another agent tool.** A leased outbox worker may
   deliver only already-enqueued operations. GitHub native push and Nextcloud
   readable project mirroring keep provider-specific adapters behind the same
   coordinator contract.
5. **Merge is promotion.** Merge consumes a reviewed commit/provider receipt
   and a protected-target policy. It cannot be implied by commit or push.
6. **Deploy is promotion.** Deploy consumes an immutable artifact or commit,
   environment policy, health gate and rollback target. It cannot be implied
   by merge.
7. **Identity is inherited, never supplied by the model.** Owner, project,
   agent run, plan, slice and lease identity come from trusted runtime state.
8. **Verified scope equality.** The reviewed paths must equal the handoff
   change set and stay inside the run capsule. Partial or hidden change sets
   fail closed.
9. **At-least-once coordination, idempotent effects.** Heartbeats and workers
   may retry after expiry; operation IDs and lease revision tokens prevent
   duplicate or stale completion.
10. **Fail closed.** Ambiguous identity, stale leases, red gates, unknown
    agents, overlapping paths, dirty unrelated files, provider divergence,
    missing rollback evidence or disabled live gates stop the stage.

## Temporal Light Adoption Decision

This section is normative and supersedes every earlier phrase that could be
read as permission to build a second persistent scheduler or effect queue.
The executable child contract is
`docs/plans/temporal-light-agent-execution-roadmap.json`; the Planning boundary
is `docs/plans/planning-definition-editor-roadmap.json`.

| Existing HWA responsibility | Temporal-Light realization | Authority that does not move |
| --- | --- | --- |
| HWA3 durable claims, leases and fencing | Activities acquire, renew and release those records through owner-scoped Odysseus services | Odysseus remains the source of truth for ownership, overlap, lease revision and fencing |
| HWA4 headless execution backend | `TLR-04-activities-claims-heartbeats`; each registered backend invocation is one bounded Temporal Activity | Backend allowlist, path scope, tool/resource policy, effects and Runtime Quality Gates remain Odysseus-owned |
| HWA5A/HWA5B heartbeat automation | HWA5A is `TLR-03-deterministic-workflow`; after HWA4/TLR-04, HWA5B is `TLR-05-signals-updates-idempotency`; Temporal Event History, timers and retries replace a custom durable scheduler | `/abc` remains the sole run entrypoint; no workflow may broaden mutation or live authority |
| HWA9 status API | `TLR-06-history-agent-projection-api`; Agent consumes a bounded joined read model | Planning receives no run state and Temporal raw history is never sent to the browser |
| HWA10 fake pipeline | `TLR-08-replay-restart-recovery` and `TLR-09-24h-time-skipping-acceptance` | Claims, effects, evidence and live gates must still pass independently |

The runtime order is exact:

1. Planning prepares one approved revision/hash handoff with
   `launch_authorized=false`.
2. The user submits `/abc` in Agent; `/abc` selects bounded secondary skills
   and models and creates one immutable execution manifest.
3. A sandboxed deterministic Temporal Workflow evaluates only manifest state,
   recorded message state and Activity results.
4. Activities perform every filesystem, process, claim, evidence, Git,
   provider or other external interaction through existing Odysseus policy.
5. Agent projects the pinned plan reference, Temporal execution history and
   Odysseus authority receipts. Planning remains byte-stable for the run.
6. A structural steering request returns `requires_plan_revision`; it never
   rewrites the pinned definition.

Temporal Light must support a pinned deadline up to 24 hours, heartbeats at
most 30 seconds apart while an Activity is active, a 90-second heartbeat
timeout, bounded retries, Worker restart recovery and Continue-As-New at the
earliest configured 6-hour/history threshold. These values are owned by the
TLR roadmap and may not be silently redefined here.

## Capability Model

Headless capabilities are stage-specific and short-lived:

| Capability | Permits | Does not permit |
| --- | --- | --- |
| `workspace_write` | edit only leased capsule paths | git/provider/merge/deploy mutation |
| `project_commit_request` | submit verified intent to `commit_project` | raw Git commands or provider choice |
| `provider_delivery` | claim bounded due outbox operations | create commits, select targets or resolve divergence |
| `merge_promotion` | merge one reviewed immutable source into one protected target | force push, target guessing or deploy |
| `deploy_promotion` | promote one immutable approved artifact to one environment | build-from-dirty-worktree or implicit merge |

Every grant binds `owner_id`, `repo_id`, `plan_id`, `slice_id`,
`agent_run_id`, issued/expiry time, maximum attempts and an approval/evidence
reference. Capabilities are not transitive.

## Temporal Activity Heartbeat And Scaling Contract

The Temporal Workflow and Activities integrate the existing claim store and
N-agent admission policy without replacing their authority. There is no second
persistent work scheduler or HWA-owned effect queue:

- one Temporal Workflow Execution per immutable `/abc` manifest and one
  deterministic slice state per normalized DAG node;
- one Odysseus idempotency/effect receipt per external effect stage;
- one compare-and-set lease revision per claim;
- heartbeat renewal only by the current lease owner;
- an expired Activity may retry only after the stale fencing token is rejected
  and the stable effect ID is checked for an existing receipt;
- global, owner, project, agent, provider and environment concurrency limits;
- exact path-overlap checks, including parent/child roots and hot files;
- backpressure when token, worker, provider or deploy capacity is exhausted;
- no new agent creation merely because capacity is full;
- pause, resume, cancel, retry and gate decisions are versioned Temporal
  Updates; asynchronous non-structural steering is a Signal;
- every Activity records bounded heartbeat details and one evidence-bearing
  completion or failure result in Temporal history;
- no commit/delivery/merge/deploy Activity dispatch before the current run,
  claim, fence, gate and preceding-stage evidence are reconciled.

## Stop Rules

- Stop on missing authenticated owner or mismatched repo ownership.
- Stop when the subagent is not `verified_done`.
- Stop when handoff paths and requested reviewed paths differ.
- Stop on a changed path outside allowed files or inside blocked files.
- Stop on overlapping active file/root leases or unrelated staged changes.
- Stop when tests, content review or required evidence are not green.
- Stop on stale/expired lease revision or exhausted retry budget.
- Stop on missing adapter, disabled live gate, provider divergence or unknown
  remote state; create review evidence instead of overwriting.
- Stop before merge without protected-target and independent-review evidence.
- Stop before deploy without immutable artifact, environment target, health
  gate and rollback target.
- Stop all new dispatch after an owner/project/global pause or kill switch.

## ABC Slice Queue

### HWA0 Reconciliation And Master Routing

Status: `done` on 2026-07-13.

Owner: Charlie.

Allowed paths:

- `docs/plans/headless-write-agent-orchestration-roadmap.md`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/multi-agent-execution-guidance.json`

Evidence:

- Existing subagent runtime is fake/operator-gated and has no effect
  capabilities.
- Existing heartbeat/runtime loop queues dry-run messages but has no scheduler,
  durable renewal or effect-stage queue.
- Existing N-agent scaler is a pure planner with exact-file locks and no
  owner/project/provider budgets.
- Existing `commit_project` is the single public commit path and already
  enqueues provider operations into a durable leased outbox.
- Existing Coding Publish Plan is preview-only and must not become a second
  commit/push implementation.

Gate: repo-only documentation; satisfied by the explicit user request.

### HWA1 Verified Headless Mutation Contract

Status: `done` on 2026-07-13.

Owner: Bob, with Charlie integration review.

Allowed paths:

- `src/headless_write_agent_pipeline.py`
- `tests/test_headless_write_agent_pipeline.py`

Deliverables:

- immutable stage/capability/permit models;
- verified subagent commit intent validator;
- owner/run/slice/repo/path binding;
- fail-closed separation of commit, provider delivery, merge and deploy;
- fake-only tests proving raw provider, merge and deploy escalation is rejected.

Gate: `HWA-CONTRACT-GO`, satisfied for repo-only implementation.

Evidence:

- `src/headless_write_agent_pipeline.py` defines single-stage
  `ApprovalCapability`, immutable `PromotionEnvelope`, fingerprint-bound
  `HeadlessCommitEvidence` and a side-effect-free canonical call preparer.
- Commit intent cannot contain owner, provider, remote, branch, worktree,
  confirmation, merge or deploy authority.
- Owner, repo, task, plan, slice, agent run, diff digest, evidence reference,
  lease fence, approval window and path scope must match before the canonical
  `commit_project` call shape is produced.
- Focused verification:
  `venv\\Scripts\\python.exe -m pytest tests\\test_headless_write_agent_pipeline.py -q`
  returned `16 passed, 2 warnings`.
- A broader 101-test selection reached `62 passed` before 39 `tmp_path`
  fixture setup errors caused by the existing Windows ACL failure on both the
  default Pytest temp root and `C:\\tmp`; no assertion or implementation
  failure was reported.

### HWA2 Canonical Subagent Commit Bridge

Status: `pending` after HWA3A and HWA3B establish durable grants, evidence,
fencing and admission control.

Owner: Bob.

Allowed paths:

- `src/headless_write_agent_pipeline.py`
- `src/agent_tools/project_commit_tools.py`
- `src/tool_execution.py`
- `tests/test_headless_write_agent_pipeline.py`
- `tests/test_commit_project_tool.py`

Deliverables:

- trusted execution provenance in tool context;
- server-loaded and atomically reserved one-shot capability; no caller-supplied
  approval booleans;
- persisted worktree evidence binding base SHA, diff/check digest and exact
  task worktree before the bridge runs;
- server-derived idempotency for a verified subagent run;
- exact conversion to existing `CommitProjectToolHandler` arguments;
- one call to `commit_project`, never `RepoCommitRunner` or `RepoPushRunner`
  directly;
- result evidence ties Local Forge transaction/version/commit back to the run.

Gate: repo-only. Any actual commit outside an isolated fake/test repository is
`HWA-LOCAL-COMMIT-LIVE-GO`.

### HWA3A-HWA3B Persistent Authority Store And Scaling Governor

Status: `HWA3A pending after HWA1`; `HWA3B pending after HWA3A`.

Owner: Bob.

Allowed paths:

- **HWA3A only:** `src/headless_write_agent_state.py`,
  `tests/test_headless_write_agent_state.py` and exact evidence updates to this
  roadmap/handoff. It must not touch existing scaling, claim, runtime, route,
  UI, provider or scheduler files.
- **HWA3B only after HWA3A:** `src/agent_pool_scaling.py`,
  `src/claim_lease_store.py`, bounded integration modules under `src/` and
  focused matching tests under `tests/`.

Deliverables:

- **HWA3A:** durable compare-and-set owner/repo/task/plan/slice/run records;
  monotonic fences; acquire, renew, release and reclaim; one-shot capability
  nonce reservation/consumption; persisted worktree evidence; separate
  heartbeat/progress timestamps; owner/repo/run pause and kill; restart proof;
  rejection of stale fences and forbidden raw fields.
- **HWA3B:** integrate the HWA3A store with atomic assignment-to-claim,
  parent/child path-prefix and hot-file collision detection, global/owner/
  project/agent quotas, backpressure, fairness and bounded recovery metrics.

HWA3A is not allowed to start HWA3B opportunistically. HWA3B cannot replace or
fork the HWA3A authority store. Both must be complete before TLR-04 Activities
may acquire real Odysseus claims.

Gate: repo-only fake-clock/fake-store tests.

### HWA4 Temporal Activity Execution Backend

Status: `pending` after HWA3A, HWA3B and HWA5A/TLR-03.

Owner: Bob.

Allowed paths:

- `src/subagent_runtime.py`
- `src/coding_agent_sandbox_bridge.py`
- `src/temporal_runtime/activities.py`
- new bounded registered backend modules under `src/`
- focused matching tests under `tests/`

Deliverables:

- every backend call is registered as one bounded Temporal Activity; no
  arbitrary command, module, endpoint or Activity name is supplied by the
  model;
- per-run workspace, capsule, resource and tool policy;
- write tools limited to leased paths;
- network, credentials and provider tools absent by default;
- stable Activity/effect ID, fencing token, retry policy and idempotent receipt;
- heartbeat at most every 30 seconds during active work, 90-second heartbeat
  timeout, pause/cancel/timeout and redacted handoff evidence;
- no completion without Runtime Quality Gates and persisted evidence.

Roadmap mapping: HWA4 is complete only when
`TLR-04-activities-claims-heartbeats` is complete. A backend implementation
without its Activity wrapper, fencing/idempotency tests and heartbeat evidence
does not complete HWA4.

Gate: `HWA-HEADLESS-RUNNER-GO`; first implementation remains local fake or
isolated sandbox. External thread/process backends require their own target Go.

### HWA5A-HWA5B Temporal Workflow And Worker Coordinator

Status: `HWA5A/TLR-03 pending after HWA3A, HWA3B and TLR-02`;
`HWA5B/TLR-05 pending after HWA4/TLR-04`.

Owner: Bob, Alice reviews visible states.

Allowed paths:

- `src/heartbeat_coordinator.py`
- `src/orchestration_runtime_loop.py`
- `src/temporal_runtime/workflows.py`
- `src/temporal_runtime/worker.py`
- `src/temporal_runtime/messages.py`
- focused matching tests under `tests/`
- `docs/plans/temporal-light-agent-execution-roadmap.json`
- one dedicated local-runtime runbook under `docs/plans/`

Deliverables:

- **HWA5A / TLR-03:** sandboxed deterministic DAG Workflow with no filesystem, network, process,
  wall-clock, random or mutable-global access;
- **HWA5A / TLR-03:** Temporal Event History, durable timers, Activity scheduling, bounded retries
  and Continue-As-New replace the proposed custom tick/effect queue;
- registered HWA4 Activities for all external I/O and effects;
- per-stage concurrency and backpressure capped by the pinned manifest;
- **HWA5B / TLR-05:** versioned pause, resume, cancel, retry and gate-decision Updates;
- **HWA5B / TLR-05:** asynchronous non-structural steering Signals and read-only Queries;
- **HWA5B / TLR-05:** stable workflow, command, Activity and effect IDs with replay and duplicate
  tests;
- Worker restart, stale-heartbeat and retry-exhaustion recovery evidence.

Roadmap mapping and order are exact: HWA5A is TLR-03, then HWA4 is TLR-04,
then HWA5B is TLR-05. HWA5 is complete only when HWA5A, HWA4 and HWA5B are all
complete. No module named scheduler, queue or heartbeat loop may become an
independent durable dispatcher.

Gate: repository tests use the Temporal SDK test environment. Starting a real
localhost Temporal development service or Worker requires
`TLR-LOCAL-SERVICE-GO`. The legacy `HWA-SCHEDULER-ACTIVATION-GO` name is a
compatibility alias for that same decision and grants no additional authority.

### HWA6 Provider Delivery Worker

Status: `pending` after HWA2 and HWA5.

Owner: Bob.

Allowed paths:

- `src/project_forge_outbox.py`
- `src/project_forge_sync.py`
- `src/project_forge_github.py`
- `src/project_forge_nextcloud.py`
- new worker module under `src/`
- focused matching tests under `tests/`

Deliverables:

- consume only due operations already selected by owner-scoped policy;
- bounded claim limit, lease heartbeat and idempotent receipts;
- adapter/live-gate check at dispatch time;
- divergence becomes review evidence, never automatic overwrite;
- subagent receives redacted transaction status, not credentials or remote
  command access.

Gate: offline fake adapters first. Real actions remain behind
`HWA-GITHUB-PUSH-LIVE-GO` and `HWA-NEXTCLOUD-DELIVERY-LIVE-GO`.

### HWA7 Merge Promotion Queue

Status: `pending` after HWA6.

Owner: Bob, Charlie owns approval integration.

Deliverables:

- immutable source commit and exact protected target;
- independent reviewer/gate evidence;
- required green checks and current-target ancestry check;
- no force push, no guessed target, no conflict auto-resolution;
- merge receipt feeds the next stage.

Gate: fake forge first; real merge requires `HWA-MERGE-LIVE-GO` for one exact
repository, source commit and target branch.

### HWA8 Deploy Promotion Queue

Status: `pending` after HWA7.

Owner: Bob, Charlie owns activation and rollback review.

Deliverables:

- immutable artifact/commit, exact environment and deployment adapter;
- environment allowlist and concurrency lock;
- preflight/health checks, timeout, rollback target and receipt;
- no secrets in queue, logs or agent context;
- deploy never runs from a dirty worktree or an unreviewed merge.

Gate: dry-run/fake environment first; real deploy requires
`HWA-DEPLOY-LIVE-GO` for one exact artifact, environment and rollback target.

### HWA9 Agent Status Projection And Audit Evidence

Status: split into HWA9A after HWA5 and HWA9B after HWA6-HWA8.

Owner: Bob for backend projection; Alice owns Agent-screen placement under the
TLR design gate.

Deliverables:

- **HWA9A / TLR-06:** owner-scoped read-only Agent projection for run,
  Workflow, Activities, bounded history, attempts, retries, timers, heartbeats,
  claims, leases, commands, gates and evidence;
- **HWA9A / TLR-06:** one projection version and cursor contract derived from
  the pinned manifest, Temporal history and Odysseus authority stores;
- **HWA9B:** extend that same projection with delivery, merge, deploy and
  rollback stages only after HWA6-HWA8 exist;
- clear `claimed`, `verified`, `queued`, `delivered`, `merged`, `deployed`,
  `blocked`, `stale`, `failed` and `cancelled` semantics;
- no raw paths, secrets, provider payloads or command output;
- audit correlation from plan/revision/slice/run to Local Forge version and
  later receipts;
- no Planning endpoint or Planning payload returns this projection.

Gate: HWA9A backend is repo-only after the Temporal runtime contract gate.
Agent UI is TLR-07 and requires `HPA-AGENT-UX-ACCEPTANCE`. There is no separate
orchestration dashboard and Planning is not a fallback runtime surface.

### HWA10 End-To-End Fake Pipeline

Status: `pending` after HWA1-HWA9 repo slices.

Owner: Charlie.

Required proof:

```text
/abc pinned manifest -> deterministic replay -> leased Activity
-> heartbeat -> Worker restart -> idempotent Activity retry -> verified handoff
-> commit_project fake repository -> Local Forge -> outbox
-> fake provider receipt -> fake reviewed merge -> fake deploy receipt
-> bounded Agent history/evidence projection
```

Negative proof must cover owner mismatch, scope mismatch, lease expiry,
stale fencing token, duplicate start request, duplicate command, duplicate
Activity effect, nondeterministic Workflow code, lost Worker, stale heartbeat,
retry exhaustion, invalid Signal/Update, structural steering without a new plan
revision, provider divergence, merge conflict, missing rollback and kill switch.

Positive proof must cover deterministic replay from Event History, Worker and
Temporal service restart recovery, accepted/rejected Update readback,
non-structural Signal delivery, Continue-As-New state transfer and a 24-hour
time-skipping run with 2,880 expected 30-second heartbeat opportunities.

Roadmap mapping: replay/restart belongs to TLR-08 and the 24-hour synthetic
acceptance belongs to TLR-09. A separately gated 12-hour wall-clock soak is
TLR-10 and is not required to complete the repo-only fake pipeline. TLR-08 and
TLR-09 complete only the runtime-resilience portion of HWA10; HWA10 itself is
done only after the complete fake commit, Local Forge, provider, merge, deploy
and Agent-projection sequence above also passes.

Gate: all fake/local test infrastructure; no external action.

### HWA11-HWA13 Bounded Live Smokes

Status: `deferred`.

- `HWA11`: one exact provider delivery after GitHub or Nextcloud live Go.
- `HWA12`: one exact protected-branch merge after merge live Go.
- `HWA13`: one exact environment deployment with rollback after deploy live Go.

Each smoke is separately approved, observed, evidenced and stopped. Approval of
one stage never approves another.

## Gate Queue

| Gate | Safe default | Approval must name |
| --- | --- | --- |
| `HWA-CONTRACT-GO` | repo-only validators/tests | satisfied by 2026-07-13 user request |
| `HWA-LOCAL-COMMIT-LIVE-GO` | fake repo only | owner, repo, run, paths, commit message |
| `HWA-HEADLESS-RUNNER-GO` | fake/isolated runner only | backend, workspace, tools, resources, stop rules |
| `TLR-LOCAL-SERVICE-GO` (`HWA-SCHEDULER-ACTIVATION-GO` compatibility alias) | SDK test environment; no background process | localhost address/ports, persistent DB path, Worker task queue, exact time window, resources, pause/kill owner and cleanup |
| `HWA-GITHUB-PUSH-LIVE-GO` | outbox pending | owner, repo, exact policy/remote/branch, credential ref |
| `HWA-NEXTCLOUD-DELIVERY-LIVE-GO` | outbox pending | owner, repo, exact readable target, credential ref |
| `HWA-MERGE-LIVE-GO` | merge preview | repo, immutable source, protected target, reviewer/checks |
| `HWA-DEPLOY-LIVE-GO` | deploy preview | artifact, environment, adapter, health and rollback target |

## Verification Baseline

Repo-only slices must run their focused tests plus:

```powershell
venv\Scripts\python.exe -m pytest tests\test_subagent_runtime.py tests\test_agent_pool_scaling.py tests\test_claim_lease_store.py tests\test_heartbeat_coordinator.py tests\test_orchestration_runtime_loop.py tests\test_commit_project_tool.py tests\test_project_forge_outbox.py tests\test_project_forge_sync.py -q
```

Temporal-mapped HWA4, HWA5, HWA9 and HWA10 additionally require the exact test
commands and evidence listed by TLR-04, TLR-03/TLR-05, TLR-06 and TLR-08/TLR-09
respectively. Existing heartbeat-loop tests are compatibility coverage only;
they cannot prove a durable scheduler and must not be used to claim Temporal
replay or recovery complete.

`git diff --check` and a scoped dirty-file review are mandatory before any
integration claim. Green repo tests are not live provider, merge or deploy
evidence.

## Current Decision

Result: HWA0/HWA1 are repo-complete. `Go` for the next isolated durable
state/fencing slice HWA3A only; HWA3B follows after HWA3A evidence is accepted.
After HWA3A/HWA3B, execution work follows the TLR dependency order; no custom
scheduler/effect queue is authorized. `No-Go` for a real
commit bridge, localhost Temporal service/Worker, provider delivery, merge,
deploy or external headless execution until its exact prerequisite evidence
and bounded gate are satisfied.

Continuation handoff:
`docs/plans/headless-write-agent-orchestration-handoff.md`.
