# Product Planning Skills Integration Roadmap

Status: proposed, execution-ready, waiting for operator activation

Created: 2026-07-12

Mode: Standard ABC

Upstream inspiration: `chitransh-cj/kimchi` at commit
`3dedd38aef7cd0f3c868844b058ffbd519f067f0` (MIT)

## Goal

Odysseus can pressure-test a product, feature, startup, or app before coding by
combining Clarification v2, a small curated set of product-planning skills,
read-only decision audits, and PlanGraph-compatible evidence without creating a
second source of truth or allowing prompt personas to mutate plans directly.

## Authority And Activation

This is a new operator-requested follow-up roadmap. It does not reopen the
exhausted MVP runner queue and it does not add a safe slice to
`open-work-completion-master-roadmap.json`.

Before every implementation claim, reconcile this roadmap with:

1. `docs/plans/multi-agent-execution-guidance.json`
2. `docs/plans/open-work-completion-master-roadmap.json`
3. `specs/roadmaps/odysseus-multiagent-roadmap.v1.json`
4. `git status --short --branch`

Planning this track is authorized. Implementation is not active until the
operator says exactly:

```text
GO PPS repo-only implementation
```

That phrase authorizes only the `safe_offline` and `repo_only` slices below. It
does not authorize UI/design work, live research, provider calls, deployment,
external writes, skill auto-publication, or a push with unrelated files.

## Current Evidence

- The current skill runtime already provides owner isolation, progressive
  disclosure, audit/necessity metadata, relevance retrieval, trusted workflow
  bindings, reference loading, and `requires_toolsets` propagation.
- Clarification v2 already owns question ids, versions, answer correlation,
  privacy boundaries, understanding confirmation, and `ready_for_plan`.
- AgentReport validation/reduction already prevents read-only agents from
  claiming work done or making nodes claimable.
- PlanRuntime already owns dependencies, gates, sources, allowed paths, and
  claimability.
- The focused baseline passed on 2026-07-12:
  `59 passed, 1 warning` across workflow skills, AgentReport, PlanRuntime,
  Clarification, and coding runner state tests.
- The inspected Kimchi repository is a single orchestration skill with a shared
  doctrine, document templates, and thirteen persona references. It is a prompt
  package, not a runtime orchestration implementation.
- Direct URL import is not yet suitable: folded YAML such as
  `description: >-` is parsed as the literal string `>-`, and a nested
  `plugin/SKILL.md` bundle keeps the `plugin/` prefix although the skill refers
  to `references/...` from its own root.
- The current worktree contains extensive unrelated modified and untracked
  work, including active Clarification and frontend work. Implementation must
  use disjoint paths or wait for those hot files to settle.

## Target Architecture

```text
trusted product-planning intent or explicit skill invocation
    -> context inspection
    -> Clarification v2, one load-bearing question thread at a time
    -> curated specialist lenses loaded as references
    -> proposed decisions + reasons + open unknowns + evidence refs
    -> read-only product decision audit
    -> AgentReport validation and PlanEvent reduction
    -> operator acceptance where a decision becomes binding
    -> PlanRuntime gate/claimability evaluation
    -> optional human-readable product document export
```

Hard architecture decisions:

- PlanRuntime plus accepted PlanEvents remain canonical. Generated product
  documents and trackers are projections only.
- Skills and their references remain untrusted context data. They never become
  system authority by being imported or published.
- A specialist lens may propose a decision, risk, gate, or follow-up. It may
  not directly mark a decision accepted, a node claimable, or work verified.
- Kimchi's subjective `9/10` clarity heuristic is not a plan-unlock rule.
  Odysseus unlocks planning only through the existing deterministic
  Clarification v2 invariant.
- Business impact, delivery cost, dependency risk, reversibility, and security
  risk stay separate dimensions. Development effort is not discarded.
- Specialist lenses are selected by relevance and risk. The default is not to
  run thirteen personas serially or in parallel.

## Skill Pack Shape

The initial pack contains three skills:

1. `product-pressure-test`
   - Interactive discovery and clarification workflow.
   - Uses a compact lens registry instead of thirteen always-on personas.
   - Maintains decisions, recommendations, open unknowns, and next handoff.
2. `lean-implementation-review`
   - Checks whether a capability is needed, already available from the
     platform/stdlib/current dependencies, or can be implemented more simply.
   - Never relaxes validation, security, accessibility, data integrity, or
     explicit acceptance criteria.
3. `product-decision-audit`
   - Read-only pre-build and post-build review.
   - Emits `holds`, `revise`, or `blocked` findings with evidence and a concrete
     remediation; it does not edit the plan.

The reference registry uses neutral domain labels:

- product and user value
- market evidence and economics
- architecture and reversibility
- UX and accessibility
- security, privacy, and legal constraints
- operations, reliability, and deployment
- lean scope and execution

All three skills start as `status: draft`, `source: admin`. Publication happens
only after the audit slice passes and duplicate/necessity checks are reviewed.

## Non-Goals

- No installation or execution of Kimchi's `install.sh`.
- No verbatim wholesale copy of the upstream skill or persona roster.
- No unreviewed names, tone, claims of expertise, or professional legal/finance
  guarantees copied from upstream.
- No thirteen-agent runtime, background-agent fanout, or new agent identity
  model.
- No new product-planning UI, frontpage work, dashboard placement, or design
  decision in this track.
- No live market research, live web/provider call, or external data write.
- No automatic writes to Memory, PlanGraph, roadmap files, or
  `docs/product/context.md` from skill instructions.
- No second canonical EPIC tracker beside PlanRuntime.
- No change to the current MVP percentages or Version-1 UI-live gate.

## Stop Rules

Stop the active slice when any of these is true:

- An allowed path overlaps unrelated modified, staged, claimed, or leased work.
- The slice would need to edit current Clarification/chat/frontend hot files
  before their owner has handed them off.
- Imported content, document text, or user text would become a trusted workflow
  trigger or system message.
- A skill import would escape its bundle root, preserve unsafe links, exceed
  existing limits, execute a bundled script, or silently publish the skill.
- A specialist output would accept its own decision, mark a node claimable, or
  claim verified completion.
- Secrets, tokens, chat ids, private paths, raw provider output, or private raw
  content would enter a skill, test fixture, report, plan, log, or export.
- Focused tests fail and the fix would leave the slice scope.
- A live action, design decision, deployment, destructive Git action, or broad
  dependency installation becomes necessary.
- The branch/push target is not clearly `dev` -> `fuzzy/dev`, or staging would
  include unrelated files.

## Progress Model

`PPS-0` is planning evidence and does not count as product implementation
progress. Product progress is weighted as follows:

| Milestone | Slices | Weight |
| --- | --- | ---: |
| M1 Safe skill compatibility and useful draft pack | PPS-1 to PPS-3 | 45% |
| M2 Structured audit and PlanGraph-safe handoff | PPS-4 to PPS-5 | 45% |
| M3 Optional projection and final acceptance | PPS-6 to PPS-7 | 10% |

A milestone reaches 100% only when its focused tests pass and its handoff is
accepted. Repo tests cannot satisfy a live or UI gate.

## Slice Queue

### PPS-0-roadmap-contract

- Status: done
- Class: `repo_only`
- Owner: Charlie
- Execution mode: `worker`
- Recommended model: GPT-5.6 Sol or best available lead model
- Reason: authority reconciliation, architecture, safety boundaries, and final
  acceptance need lead-level judgement.
- Dependencies: none
- Allowed paths:
  - `docs/plans/product-planning-skills-integration-roadmap.md`
  - `docs/plans/multi-agent-execution-guidance.json`
  - `tests/test_roadmap_multi_agent_guidance.py`
- Objective: publish this execution-ready roadmap without activating its
  implementation queue.
- Tests:
  - parse `multi-agent-execution-guidance.json`
  - run `scripts/roadmap_multi_agent_guidance.py` for this roadmap
  - `tests/test_roadmap_multi_agent_guidance.py`
- Done when:
  - the roadmap has goal, evidence, non-goals, stop rules, slices, gates,
    allowed paths, tests, dependencies, models, and completion language;
  - the guidance index classifies it as planned and non-claimable until Go;
  - no current open-work or MVP queue state is changed.

### PPS-1-skill-import-compatibility

- Status: planned, waiting for `PPS-G0`
- Class: `repo_only`
- Owner: Bob
- Execution mode: `worker`
- Recommended model: GPT-5.6 Terra or best available implementation model
- Reason: bounded parser/importer work with deterministic fixture coverage.
- Dependencies: PPS-0, `PPS-G0`
- Allowed paths:
  - `services/memory/skill_format.py`
  - `services/memory/skill_importer.py`
  - `services/memory/skills.py`
  - `tests/test_skill_importer.py`
  - `tests/test_skill_format.py`
  - `tests/test_skills_manager_owner_isolation.py`
- Objective: make standard nested Agent Skill bundles import predictably without
  weakening URL, path, size, ownership, or publication safety.
- Requirements:
  - Parse safe folded/literal YAML string scalars used by standard skill
    descriptions, including `>`, `>-`, `|`, and `|-`, without adding a broad
    YAML object-construction surface.
  - Normalize imported bundle paths relative to the directory containing the
    selected `SKILL.md`; `references/x.md` must land at
    `<skill-root>/references/x.md`.
  - Keep path traversal rejection, GitHub-only redirects, file/depth/byte
    limits, and text-file allowlists intact.
  - Import remains `source: imported` and `status: draft`; imported content is
    never eligible as a required workflow merely because parsing succeeded.
  - Do not execute `install.sh` or any bundled script.
  - Add network-free Kimchi-shaped fixtures and regression tests for existing
    skills.
- Tests:
  - `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_skill_format.py tests\test_skill_importer.py tests\test_skills_manager_owner_isolation.py`
- Done when:
  - a Kimchi-shaped fixture exposes a useful description;
  - `view_ref(name, "references/...")` resolves after nested import;
  - unsafe paths and cross-host redirects still fail closed;
  - existing skill serialization round-trips.

### PPS-2-curated-product-planning-skill-pack

- Status: planned, waiting for `PPS-G0`
- Class: `repo_only`
- Owner: Alice
- Execution mode: `worker`
- Recommended model: GPT-5.6 Terra or best available content/contract model
- Reason: bounded skill authoring, operator wording, neutral domain language,
  and attribution.
- Dependencies: PPS-0, `PPS-G0`
- Allowed paths:
  - `data/skills/product-planning/**`
  - `tests/test_product_planning_skills.py`
  - `ACKNOWLEDGMENTS.md`
- Objective: author the three Odysseus-native draft skills and their compact
  specialist-lens references.
- Requirements:
  - Use Odysseus frontmatter and structured `When to Use`, `Procedure`,
    `Pitfalls`, and `Verification` sections.
  - Descriptions and tags cover precise German and English product-planning
    intents without matching ordinary coding/debugging requests.
  - Use one load-bearing clarification thread at a time and keep a visible open
    unknown list.
  - Replace subjective completion ratios with Clarification v2 readiness.
  - Decisions are proposed with reasons, evidence, confidence, reversibility,
    and unresolved risk; they are not silently locked.
  - Include upstream attribution and MIT notice for any substantively adapted
    material.
  - All skills remain draft until PPS-3 and PPS-4 evidence is accepted.
- Tests:
  - `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_product_planning_skills.py`
- Done when:
  - all references are reachable through `view_ref`;
  - no broken relative paths or orphan required references remain;
  - skills contain no raw external prompts, secrets, host paths, or direct
    mutation instructions;
  - duplicate/necessity review finds no existing skill that should be extended
    instead.

### PPS-3-retrieval-and-skill-audit

- Status: planned
- Class: `safe_offline`
- Owner: Charlie
- Execution mode: `worker`
- Recommended model: GPT-5.6 Sol or best available review model
- Reason: retrieval precision, false-positive policy, and publication are
  cross-cutting acceptance decisions.
- Dependencies: PPS-1, PPS-2
- Allowed paths:
  - `data/skills/product-planning/**`
  - `tests/test_product_planning_skills.py`
  - `tests/test_skills_tag_token_match.py`
  - `tests/test_skill_index_prompt_injection.py`
- Objective: prove the draft pack is discoverable, narrow, safe, and useful
  before any publication or trusted binding.
- Requirements:
  - Test German and English positive queries for product ideation, feature
    scoping, architecture pressure tests, lean review, and pre-build audits.
  - Test negative queries for ordinary bug fixes, deployment status, document
    reading, and unrelated chat.
  - Verify skill text remains an untrusted context message.
  - Run existing skill test/audit logic with deterministic fixtures where
    possible; do not make a live model/provider call.
  - Review whether the existing low-confidence
    `audit-implementation-against-a-plan-checklist` skill should stay separate,
    be superseded, or be marked redundant. Do not delete it without explicit
    confirmation.
  - Publication is allowed only after audit verdict, necessity review, and
    retrieval tests are green. Otherwise leave the skills draft.
- Tests:
  - `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_product_planning_skills.py tests\test_skills_tag_token_match.py tests\test_skill_index_prompt_injection.py`
- Done when:
  - the positive/negative corpus meets its declared precision expectations;
  - prompt-injection boundaries remain green;
  - an explicit publish-or-remain-draft decision is recorded for each skill.

### PPS-4-product-decision-audit-contract

- Status: planned
- Class: `repo_only`
- Owner: Bob
- Execution mode: `worker`
- Recommended model: GPT-5.6 Terra for implementation with Sol review
- Reason: a new structured safety contract needs deterministic implementation
  and lead review of its semantics.
- Dependencies: PPS-2
- Allowed paths:
  - `specs/product_decision_audit.v1.schema.json`
  - `src/product_decision_audit.py`
  - `tests/test_product_decision_audit.py`
- Objective: implement a bounded, read-only audit result contract that can
  represent cross-domain contradictions without editing plan state.
- Required fields:
  - audit id, plan id, scope, mode (`pre_build` or `post_build`)
  - finding id, domain, decision id, verdict (`holds`, `revise`, `blocked`)
  - severity, reason, evidence refs, confidence, concrete fix
  - redaction summary, created timestamp, schema version
- Requirements:
  - Validate bounded sizes and safe ids.
  - Reject secrets, private host paths, raw provider output, and unbounded raw
    transcripts.
  - Preserve disagreement and missing evidence; never smooth uncertainty into
    `holds`.
  - A `revise` or `blocked` finding must include a concrete fix or next decision.
  - The audit output cannot mark nodes claimable, accepted, complete, or
    verified.
- Tests:
  - `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_product_decision_audit.py`
- Done when:
  - valid fixtures round-trip;
  - unsafe and self-accepting payloads fail closed;
  - contradictory evidence produces an unresolved finding rather than an
    automatic winner.

### PPS-5-plan-graph-safe-handoff

- Status: planned, hot-file gated
- Class: `repo_only`
- Owner: Charlie
- Execution mode: `worker`
- Recommended model: GPT-5.6 Sol or best available lead model
- Reason: this is the security-sensitive integration boundary between untrusted
  planning output and canonical plan state.
- Dependencies: PPS-3, PPS-4, `PPS-G1`
- Allowed paths:
  - `src/product_planning_bridge.py`
  - `src/workflow_skills.py`
  - `src/agent_report_store.py`
  - `src/plan_runtime.py`
  - `tests/test_product_planning_bridge.py`
  - `tests/test_workflow_skills.py`
  - `tests/test_agent_report_store.py`
  - `tests/test_plan_runtime.py`
- Objective: reduce accepted product-planning output into existing observation,
  gate, blocker, and proposed-event shapes without bypassing operator acceptance
  or claimability.
- Requirements:
  - Explicit skill invocation works without a new binding.
  - Add a trusted exact intent binding only if PPS-3 proves fuzzy retrieval is
    insufficient; never inspect prompt/document body text as trusted trigger
    material.
  - Map open unknowns to clarification blockers or proposed follow-ups.
  - Map audit findings to `gate_observed`/`node_blocked` style proposals.
  - Map decisions to proposed events with source/evidence refs and confidence.
  - Reject any event that accepts its own report, marks verified done, or makes
    a node claimable.
  - Do not modify chat routes, Clarification files, or frontend hot files in
    this slice.
- Tests:
  - `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_product_planning_bridge.py tests\test_workflow_skills.py tests\test_agent_report_store.py tests\test_plan_runtime.py`
- Done when:
  - a complete pressure-test fixture reduces to safe proposed observations and
    events;
  - unresolved audit findings block a ready claim;
  - accepted events still require the existing orchestrator/operator path;
  - no raw skill/persona text becomes canonical future context.

### PPS-6-product-document-projection

- Status: deferred by default
- Class: `repo_only`
- Owner: Alice for contract, Bob for implementation, Charlie integrates
- Execution mode: `worker` only after a single owner is selected
- Recommended model: GPT-5.6 Terra or best available bounded worker model
- Reason: useful projection work, but not required for the safe core and it
  introduces a file-write surface.
- Dependencies: PPS-5, `PPS-G2`
- Allowed paths after owner selection:
  - `src/product_plan_export.py`
  - `tests/test_product_plan_export.py`
  - `docs/plans/product-planning-document-projection-contract.md`
- Objective: generate a human-readable overview, EPIC/story pages, and audit
  page from canonical plan state without making those files the truth source.
- Requirements:
  - Preview is read-only and deterministic.
  - Every output identifies its source plan version and projection timestamp.
  - Tracker status is derived from PlanRuntime; no independent writable status
    field exists.
  - Writing into a target project requires an exact target, preview, allowed
    paths, and operator confirmation.
  - No `execute.md` may override current ABC, scope, gate, Git, or evidence
    policies.
- Tests:
  - `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q tests\test_product_plan_export.py`
- Done when:
  - preview output is stable and source-linked;
  - stale projections are detectable;
  - no output can change canonical status or start an agent.

### PPS-7-final-verification-and-handoff

- Status: planned
- Class: `repo_only`
- Owner: Charlie
- Execution mode: `worker`
- Recommended model: GPT-5.6 Sol or best available lead model
- Reason: final integration, security review, scope verification, and publish
  decision cannot be delegated to a lightweight verifier alone.
- Dependencies: PPS-5; PPS-6 may be done or consciously deferred
- Allowed paths:
  - every file explicitly accepted from PPS-1 through PPS-6
  - `docs/plans/product-planning-skills-integration-roadmap.md`
- Objective: run the complete focused suite, confirm safety invariants, record
  final evidence, and prepare a scoped commit/push only if the worktree permits.
- Verification:
  - all slice-specific tests
  - `tests/test_clarification_contract.py`
  - `tests/test_clarification_policy.py`
  - `tests/test_skill_index_toolset_gating.py`
  - `tests/test_skill_index_prompt_injection.py`
  - `tests/test_claim_evidence_gate.py`
  - `git diff --check`
  - JSON schema and guidance parsing
- Done when:
  - published skills, if any, have green audit/necessity/retrieval evidence;
  - direct import regressions and path boundaries are green;
  - product decisions remain proposed until accepted;
  - PlanRuntime remains canonical;
  - unrelated dirty files are not staged or explained as PPS progress;
  - a handoff records changed files, tests, risks, gates, commit, and push state.

## Dependencies And Parallelism

```text
PPS-0
  +-- PPS-1 import compatibility --------+
  +-- PPS-2 curated skill pack ----------+--> PPS-3 retrieval/audit --+
  +-- PPS-2 -----------------------------> PPS-4 audit contract -------+--> PPS-5 bridge
                                                                           +--> PPS-6 export (optional)
                                                                           +--> PPS-7 final
```

- After `PPS-G0`, Alice may run PPS-2 while Bob runs PPS-1 because their write
  paths are disjoint.
- PPS-3 is serial after both PPS-1 and PPS-2 because it may tune skill metadata.
- PPS-4 may run in parallel with PPS-3 after PPS-2; it owns only new schema,
  module, and test paths.
- PPS-5 is serial and owned by Charlie because it touches shared runtime
  contracts.
- PPS-6 uses one selected writer. Alice and Bob do not edit its contract/code in
  parallel.
- Only Charlie edits this roadmap or the execution guidance during a run.

## Gate Queue

### PPS-G0-implementation-activation

- Class: `blocked`
- Blocks: PPS-1 through PPS-7
- Decision needed: operator says `GO PPS repo-only implementation`.
- Safe preparation done: analysis, target architecture, slice scopes, tests,
  and stop rules are documented.
- Risk if bypassed: a plan-only request would be misread as authorization to
  edit runtime and skill files.
- Next safe slice: none until Go.

### PPS-G1-runtime-hotfiles-stable

- Class: `blocked`
- Blocks: PPS-5
- Decision needed: Charlie confirms that Clarification, AgentReport,
  PlanRuntime, workflow-skill, and relevant chat/runtime owners have completed
  or handed off overlapping work.
- Safe preparation done: PPS-5 excludes current chat, Clarification, and UI
  hotfiles.
- Risk if bypassed: context/state changes could be overwritten or integrated
  against an unstable contract.
- Next safe slice: PPS-1, PPS-2, PPS-3, or PPS-4 when otherwise ready.

### PPS-G2-document-projection-go

- Class: `blocked`
- Blocks: PPS-6
- Decision needed: approve the projection contract and decide whether file
  export is needed in the first release or consciously deferred.
- Safe preparation done: canonical PlanRuntime remains sufficient without the
  export.
- Risk if bypassed: duplicate trackers and stale build documents become a
  second source of truth.
- Next safe slice: PPS-7 with PPS-6 deferred.

### PPS-G3-live-research-and-specialist-fanout

- Class: `needs_live_go`
- Blocks: any future live market research, finance data lookup, provider call,
  or background-agent fanout.
- Decision needed: exact research question, sources/regions, budget, privacy
  boundary, evidence format, and operator Go.
- Safe preparation done: the first release uses static lenses and existing
  user-supplied evidence only.
- Risk if bypassed: unbounded external calls, weak source provenance, private
  context leakage, and unsupported market/finance claims.
- Next safe slice: all repo-only core slices.

### PPS-G4-ui-and-design

- Class: `needs_design`
- Blocks: any product-planning screen, persona selector, audit dashboard, or
  frontpage placement.
- Decision needed: approved Harbor/UI information architecture and interaction
  contract.
- Safe preparation done: skills work through existing chat/skill surfaces.
- Risk if bypassed: collision with active frontend v2/v3 work and a new
  unapproved interaction model.
- Next safe slice: all repo-only core slices.

### PPS-G5-clean-integration-scope

- Class: `blocked`
- Blocks: PPS-7 commit/push
- Decision needed: clean dedicated worktree or an explicitly reviewed set of
  PPS-only changes with no unrelated staged files.
- Safe preparation done: every slice has exact allowed paths and focused tests.
- Risk if bypassed: unrelated user work could be committed, reverted, or
  misreported as PPS completion.
- Next safe slice: verification without staging, if paths remain readable.

## ABC Paths

| Path | Owner | Goal | Completion |
| --- | --- | --- | --- |
| `pps-import-compatibility` | Bob | Standard skill bundles parse and resolve references safely | PPS-1 done with regression tests |
| `pps-skill-content` | Alice, then Charlie review | Three narrow, neutral, attributed draft skills | PPS-2 and PPS-3 done; publish decision recorded |
| `pps-decision-audit` | Bob | Structured read-only decision audit contract | PPS-4 done with unsafe fixtures rejected |
| `pps-runtime-bridge` | Charlie | Safe proposed-event handoff into existing planning contracts | PPS-5 done; no self-acceptance or claim bypass |
| `pps-projection` | one selected owner | Optional human-readable projection | PPS-6 done or consciously deferred |
| `pps-integration` | Charlie | Full verification and scoped handoff | PPS-7 accepted |

## Required Handoff

Every slice reports:

```text
Roadmap: docs/plans/product-planning-skills-integration-roadmap.md
Slice: PPS-<n>
Owner: Alice | Bob | Charlie
Status: done | blocked | deferred | failed
Changed paths: ...
Tests and results: ...
Evidence refs: ...
Remaining gate or blocker: ...
Collision check: ...
Recommended next action: ...
```

Reports are evidence proposals. Charlie validates and accepts them before any
roadmap status changes.

## Go Language

- `Go`: the slice's dependencies and gates are satisfied, allowed paths are
  disjoint, and focused tests/evidence are green.
- `Partial`: a useful artifact exists but publication, bridge integration, or
  acceptance evidence is incomplete; do not claim the milestone done.
- `No-Go`: unsafe import behavior, trusted untrusted-content routing,
  self-accepting plan events, scope collision, or failed safety tests.
- `Deferred`: deliberately postponed without blocking the safe core, especially
  PPS-6, UI, live research, or agent fanout.
- `Blocked`: an explicit operator decision, unstable hotfile, dirty integration
  scope, or missing prerequisite prevents safe progress.

## Definition Of Done

The roadmap is complete when:

- standard nested skill bundles parse and reference-load safely;
- the three product-planning skills are audited, necessary, narrowly
  discoverable, and either intentionally published or intentionally left draft;
- product pressure tests use Clarification v2 readiness instead of subjective
  completion claims;
- decision audits are structured, bounded, read-only, and evidence-linked;
- all skill/persona outputs enter PlanGraph only as validated proposals;
- no skill can accept its own decisions, make a node claimable, or claim
  verified completion;
- PlanRuntime and accepted events remain the only canonical execution state;
- product document projection is implemented safely or consciously deferred;
- focused verification passes and the final handoff records remaining live/UI
  gates;
- any commit/push contains only accepted PPS scope and targets `fuzzy/dev`,
  never `origin`.
