# Unified Source Index Runtime And Consumer Integration Roadmap

Status reconciliation (2026-09-05): retain accepted UIR-03A Go-A R2 and
UIR-03B R2, plus UIR-04A/B/C and UIR-05A/B from the existing Round-7
`long_run_state.completed_slices` ledger and guidance. Generic UIR-03 is
only a superseded historical aggregate; its current dependency meaning is
both UIR-03A and UIR-03B. UIR-04/05 sections below retain broader original
requirements; accepted sub-slices do not certify unimplemented optional
health aggregation or productive activation. Start only still-open work
after a fresh scope/dependency check. Evidence: `docs/plans/evidence/harbour-one-plan-reconciliation-20260905.json`.

Updated: 2026-08-10

Status: `UIR-00/01/02 accepted / UIR-03A Go A R2 accepted-released / UIR-03B R2 accepted-released`; the generic `UIR-03` aggregate is superseded by these two exact units, while runtime and productive query paths remain default-off

## Native Runtime Amendment 2026-08-01

Obsidian is a legacy compatibility subsystem, not a target runtime provider.
In this roadmap, `legacy fallback` means the current first-party Personal
Docs/RAG or exact native domain reader only. It never means an Obsidian route,
tool, context provider, vault service, worker, cache or RAPTOR artifact.

UIR must remove the remaining core/runtime couplings only after equivalent
first-party behavior exists. Until then the roadmap reports those couplings as
open migration evidence; it must not describe technical decoupling as already
complete. No productive activation scope may include `plugins/obsidian/**`.

Parent: `OWM-15` / `0.28.x` Unified Source Index Foundation

Lane: `L25`

Slice prefix: `UIR`

Shared activation contract: `USI-LIVE-ACTIVATION`

## 1. Goal

Compose the completed USI store/query core into the real Odysseus process and
move direct Personal Docs, Chat and Agent retrieval consumers behind one
bounded runtime service without breaking existing behavior.

This roadmap closes the application-wiring gap deliberately excluded from
`USI-00` through `USI-15`. It owns dependency injection, disabled-by-default
runtime lifecycle, health/status, compatibility facades, shadow comparison,
consumer cutover and rollback.

It does not implement USI records, stores, query algorithms, domain adapters,
CBM, Lineage, Lens rendering or data lifecycle.

## 2. Current Code Evidence

- `app.py` initializes Chroma RAG through `get_rag_manager`, passes it into
  managers, routes and diagnostics, and owns application lifespan tasks.
- `src/app_initializer.py` creates `MemoryManager`, Personal Docs,
  `MemoryVectorStore` and context-provider registry state.
- `src/chat_processor.py` directly calls
  `personal_docs_manager.rag_manager.search` when RAG is enabled.
- `src/personal_docs.py` combines an in-memory keyword index with direct
  `RAGManager` index/search/remove/rebuild calls.
- `src/ai_interaction.py` stores global RAG references and directly starts
  personal-document indexing.
- `src/context_orchestrator.py` is already the generic bounded Chat/Agent
  context-provider boundary and must remain the only prompt injection path.
- `src/rag_manager.py` is explicitly a compatibility wrapper around
  `VectorRAG` and is suitable as a temporary fallback facade.
- `src/service_health.py` and `routes/diagnostics_routes.py` expose current
  Chroma health and can extend the same bounded read-only surface.
- `src/bg_jobs.py` is a chat-scoped detached shell job system and is not an
  index-job scheduler.
- `src/task_scheduler.py` owns recurring actions, not index cursor or retry
  truth.

The complete code inventory is frozen in
`docs/plans/unified-source-index-integration-impact-map.md`.

## 3. Canonical Ownership And No-Duplication Matrix

| Concern | Canonical owner | UIR responsibility | UIR must not do |
| --- | --- | --- | --- |
| USI records/stores/query | USI core | instantiate and bind accepted services | fork contracts or query algorithms |
| Source extraction | UDA/domain adapters | invoke registered adapters/jobs | read arbitrary domain internals |
| Index job state | USI JobStore | run bounded workers against JobStore | reuse chat shell jobs as index truth |
| Personal Docs writes | PersonalDocsManager | shadow/cut over reads and enqueue discovery | own files or upload/delete policy |
| Personal Memory writes | MemoryManager/lifecycle | bind query/context provider | create or edit Memory facts |
| Chroma | existing semantic lane | expose health and fallback through USI manifest | treat collection IDs as source IDs |
| Chat context | Context Orchestrator | register one core knowledge provider | inject parallel prompt text |
| Tool identity | TAX/USI-09 | bind runtime provider to `query_knowledge` | edit descriptors before TAX handoff |
| Usage telemetry | TUA | one external invocation plus internal spans | create another invocation ledger |
| Runtime metrics | GRO | emit accepted low-cardinality samples | create exporter or dashboard stack |
| Code snapshot and exact read | Forge / canonical `read_file` | consume only a revision-bound locator after query planning | serve projection text, add a public tool, read current HEAD or accept a substituted path/revision |
| Code coverage and locator truth | FCA/USI | consume accepted coverage state and locator capability | infer completeness from CBM/embedding health |
| Code provider | CBM | register an optional structural/semantic projection | expose upstream MCP/tools/config or claim an exact-read authority |
| Owner/data lifecycle | ULO | call accepted lifecycle hooks | implement rename/delete/backup logic |

## 4. Target Runtime Architecture

```text
app composition root
  -> UnifiedSourceIndexRuntimeConfig (disabled by default)
  -> USI store/query service
  -> SourceAdapterRegistry from UDA
  -> IndexJobRunner backed by USI JobStore
  -> optional projection providers: Chroma / CBM / RAPTOR / Lineage

read-only consumers
  -> KnowledgeQueryRuntime facade
  -> bounded USI QueryPlanner
  -> ContextItem / AnswerProvenance
  -> existing Context Orchestrator
  -> Chat / Agent / query_knowledge

code direct/exact follow-up (only for an accepted, covered Forge revision)
  -> query_knowledge returns one Forge-bound locator, no source text
  -> existing read_file in Forge-locator mode consumes that locator and revision
  -> authoritative bytes/lines with explicit coverage/stale/partial state

fallback
  -> current first-party Personal Docs/RAG search or exact native domain reader
  -> explicit degraded/partial evidence
```

The runtime facade is dependency-injected. No module import may start scanning,
create an engine process, open a network listener or mutate a source.

## 5. Runtime States And Feature Flags

Minimum states:

- `disabled`: no database open, worker or query routing;
- `read_only`: database/status available, no discovery or writes;
- `shadow`: selected legacy requests also query USI for comparison, but legacy
  output remains authoritative and shadow results never enter the prompt;
- `canary`: selected owner/source/request scope uses USI with automatic legacy
  fallback and explicit generation evidence;
- `active`: selected scope uses USI as query authority;
- `degraded`: core or optional provider failed; policy remains fail-closed and
  exact/legacy fallback is explicit;
- `rollback`: new requests use the prior generation while in-flight jobs stop
  at safe checkpoints.

Required configuration is server-side and bounded:

- runtime state and selected generation;
- owner/source/domain allowlist;
- query and worker budgets;
- SQLite data path beneath `DATA_DIR`;
- optional provider enable flags;
- shadow sample rate with no raw query logging;
- stale/fallback/circuit-breaker thresholds.

## 6. Mode And Queue Policy

Planning mode is `Standard ABC`; implementation is `repo_only` until the
shared parent gate.

1. On a future explicit goal, only `UIR-00` is claimable.
2. Runtime/core files are single-writer hotfiles.
3. Shadow tests use synthetic fixtures and temporary databases only.
4. No productive scan, source read, Chroma migration, CBM process or prompt
   cutover occurs before `USI-LIVE-ACTIVATION`.
5. This roadmap never materializes a second product gate.
6. A source domain cannot enter canary until its UDA adapter and ULO lifecycle
   matrix are green for that scope.

## 7. Slice Queue

### UIR-00 - Runtime Caller And Composition Audit

- Class: `safe_offline`
- Owner: Charlie
- Status: `accepted_2026-07-23`
- Dependency audit: `Active Open-Work goal; USI-00 through USI-14 accepted;
  read-only recon found no active writer on the three new worker paths`
- Serialized claim:
  - run_id: `abc-uir00-20260723T201424+0200`
  - thread_id: `/root`
  - owner: `Charlie`
  - state: `released`
  - acquired_at: `2026-07-23T20:14:24+02:00`
  - lease_expires_at: `2026-07-24T00:14:24+02:00`
  - released_at: `2026-07-23T20:36:32+02:00`
  - worktree: `C:\tmp\odysseus-abc-usi09-20260723`
  - allowed_paths: `docs/plans/unified-source-index-runtime-caller-inventory.json`,
    `scripts/audit_unified_source_index_runtime.py`, and
    `tests/test_audit_unified_source_index_runtime.py`
  - excluded_paths: the accepted USI-00 overlap inventory and generator, every
    existing app/route/runtime/RAG/MemoryVector/Personal Docs/context/tool/
    provider/config implementation, productive database/source/data directory,
    runtime/worker/process, provider/network/deploy and live path
  - collision_resolution: `The old declared runtime-inventory path belongs to
    USI-00 and has a full-document deterministic generator. UIR-00 now owns a
    dedicated new runtime-caller inventory so both audits remain single-writer.`
  - evidence: `Three focused tests passed with one unrelated SQLAlchemy
    deprecation warning. The static audit scanned 805 tracked Python files,
    parsed 191 AST candidates, retained 21 relevant file hashes, classified 55
    direct caller candidates plus five reasoned exclusions, and left zero
    unclassified candidates. Every caller has a keep/adapt/fallback/retire/
    exclude decision and owner track; unknown routes and dynamic manager
    handles fail closed. The audit check completed in about 2.7 seconds. The
    accepted USI-00 overlap audit remains clean at 35 components, 103 writers
    and 17 tool identities. No runtime module, private source, provider,
    network, worker or live action was used.`
  - implementation_commit:
    `bfc00d6b16ae96701c7f25015974f558b9cca1e9`
  - artifact_hashes:
    - caller_inventory:
      `B64E170D08F1829B374E3D17DD9CDCFF676102E8FA144A3F400CA5B92F4579E6`
    - audit:
      `C2139A86D5B145E9E4531BB4C53C85967FBFFF012F66588DE0722554A4541957`
    - focused_tests:
      `81106C69C9A63B002E29A570DD7B01A61CA28D19F9B41906111C64ED3194E528`
  - sol_review:
    `approved_after_single-writer path correction, true unknown-caller
    rejection, explicit current route/dynamic rules and per-caller owner/
    decision hardening; zero blocking findings`
  - live_actions: `false`
- Dependencies: explicit goal; current hotfile claims and dirty work inspected
- Allowed paths:
  - `docs/plans/unified-source-index-runtime-integration-roadmap.md`
  - `docs/plans/unified-source-index-runtime-caller-inventory.json`
  - `scripts/audit_unified_source_index_runtime.py`
  - `tests/test_audit_unified_source_index_runtime.py`
- Work:
  - enumerate every direct RAG, MemoryVector, Personal Docs and context caller;
  - record startup, shutdown, health, feature, route and owner-lifecycle seams;
  - distinguish active consumers from compatibility, test and admin paths;
  - record current writer claims for `app.py`, Chat, tool and route hotfiles;
  - fail when an unclassified direct query or indexing caller remains.
- Tests: `python -m pytest -q tests/test_audit_unified_source_index_runtime.py`
- Done when: the machine-readable inventory covers every current runtime caller
  and assigns keep, adapt, fallback or retire ownership.
- Acceptance:
  `offline_go_deterministic_static_runtime_caller_inventory_explicit_owner_decisions_zero_unclassified_preserved_usi00_overlap`
- Next frontier: `UIR-01` is dependency-ready; runtime and productive query
  paths remain disabled.

### UIR-01 - Runtime Service And State Contract

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-23`
- Dependency audit: `UIR-00, USI-01 and USI-02 accepted; both declared
  implementation paths are new and collision-free`
- Serialized claim:
  - run_id: `abc-uir01-20260723T203955+0200`
  - thread_id: `/root`
  - owner: `Bob`
  - state: `released`
  - acquired_at: `2026-07-23T20:39:55+02:00`
  - lease_expires_at: `2026-07-24T00:39:55+02:00`
  - released_at: `2026-07-23T20:53:57+02:00`
  - worktree: `C:\tmp\odysseus-abc-usi09-20260723`
  - allowed_paths: `src/unified_source_index_runtime_contract.py` and
    `tests/test_unified_source_index_runtime_contract.py`
  - excluded_paths: every existing app/route/runtime/USI query/context/RAG/
    provider/config/environment/composition implementation, productive
    database/source/data directory, runtime/worker/process, provider/network/
    deploy and live path
  - evidence: `Seventeen focused synthetic tests passed with one unrelated
    SQLAlchemy deprecation warning. Immutable records cover all seven modes,
    canonical round trips, generation/scope/provider/health/fallback state and
    fail closed on unsafe eligibility, lexical readiness, worker, fallback,
    duplicate, unbounded, secret/path/query/content and live-authorization
    combinations. Imports are standard-library-only; environment, filesystem,
    database, provider, network, runtime and live actions were zero. Canary and
    active remain representable values only, not runtime authorization.`
  - implementation_commit:
    `6416b997c336d1a2af7aad1f84002531c9dd3cc6`
  - artifact_hashes:
    - runtime_contract:
      `697F37B4AC1265BEB63EE251205F36E990AB001D0F654BD70B6D55C2EBEF9869`
    - focused_tests:
      `DB96F6BBDF1DECBBB3E945390AC6661196E1EFB1712B48E18B12BC57478F0D76`
  - sol_review:
    `approved after mode/health, selected-scope eligibility, mandatory lexical
    readiness, compatible degraded/rollback fallback and non-disabled
    runtime_disabled contradiction hardening; zero blocking findings`
  - live_actions: `false`
- Dependencies: `UIR-00`, USI-01 and USI-02
- Allowed paths:
  - `src/unified_source_index_runtime_contract.py`
  - `tests/test_unified_source_index_runtime_contract.py`
- Work:
  - immutable runtime state, generation, selected scopes and health records;
  - strict config normalization with secret/path-safe diagnostics;
  - provider capability and degraded/fallback reason contracts;
  - no environment reads outside the composition boundary;
  - no source content or raw query text in state records.
- Tests: `python -m pytest -q tests/test_unified_source_index_runtime_contract.py`
- Done when: disabled/read-only/shadow/canary/active/degraded/rollback states are
  deterministic and reject unsafe scope or policy combinations.
- Acceptance:
  `offline_go_immutable_deterministic_seven_mode_runtime_state_contract_fail_closed_and_never_live_authorizing`
- Next frontier: `UIR-02` is dependency-ready; runtime, workers, providers and
  productive query paths remain disabled.

### UIR-02 - Data Paths, Feature Flags And Configuration

- Class: `repo_only`
- Owner: Bob with Charlie integration
- Status: `accepted_2026-07-26`
- Dependency audit: `UIR-01 and USI-03 accepted; read-only collision/hotfile
  recon found no active writer or tracked diff in the four implementation paths`
- Serialized claim:
  - run_id: `abc-uir02-config-20260726T165219+0200`
  - thread_id: `/root`
  - owner: `Bob with Charlie integration`
  - worker: `/root/uir02_config`
  - worker_model:
    `requested_gpt-5.6-terra; concrete identifier unavailable on worker surface`
  - state: `released`
  - acquired_at: `2026-07-26T16:52:19+02:00`
  - lease_expires_at: `2026-07-26T20:52:19+02:00`
  - released_at: `2026-07-26T17:04:26+02:00`
  - worktree: `C:\Users\nkatz\odysseus`
  - implementation_commit:
    `960f21d2b7a2be696672870789a44c7c64ff87af`
  - allowed_paths: `src/unified_source_index_runtime_config.py`,
    `src/constants.py`, `src/config.py` and
    `tests/test_unified_source_index_runtime_config.py`
  - excluded_paths: every UI/static/template/browser path, application
    composition/runtime/worker/provider/query consumer, environment file,
    deployment, network and live host path
  - evidence: `Fifty-five focused tests passed with one existing SQLAlchemy
    deprecation warning. The immutable configuration is disabled by default,
    uses one canonical USI SQLite path beneath the selected data root, creates
    no path or database, strictly parses all productive booleans and bounded
    budgets, rejects wildcard/empty/duplicate owner-source-domain scopes,
    keeps CBM, Lineage, RAPTOR and Chroma flags independent, and gives tests
    one explicit absolute disabled fixture path. Disabled and fixture modes
    reject productive provider flags. Python compile and scoped diff checks
    passed.`
  - artifact_hashes:
    - runtime_config:
      `2658BD536843D7476C7227DC6D9190EB5F6BC022785ED21A164356BCCC30BC65`
    - constants:
      `7FDCCB19010401679083610E6DF06BABCFA3D6B3DB673D2145BE18AD7BDA953D`
    - config_accessor:
      `0DFCACC9B89DDCFCE9B1EEF7C842A06881F12A629772A1BEC6360D300A6C1E8D`
    - focused_tests:
      `F69F446DBA1F2243A0C299D506668CF4BBEF5119046D27E68C6C2FE2D7323E84`
  - sol_review:
    `approved after canonical SQLite path, fixture-provider isolation,
    side-effect coverage, all-boolean strict parsing, disabled-provider
    rejection and deterministic config-accessor corrections; zero blocking
    findings`
  - live_actions: `false`
- Dependencies: `UIR-01`, USI-03
- Allowed paths:
  - `src/unified_source_index_runtime_config.py`
  - `src/constants.py`
  - `src/config.py`
  - `tests/test_unified_source_index_runtime_config.py`
- Work:
  - define one USI-owned SQLite path and temporary/test override;
  - add default-off runtime/provider flags and bounded budgets;
  - validate path confinement beneath the selected data root;
  - separate productive runtime flags from test fixture flags;
  - keep CBM, Lineage, RAPTOR and Chroma provider flags independent.
- Tests: `python -m pytest -q tests/test_unified_source_index_runtime_config.py`
- Done when: a default installation starts with USI productive behavior off and
  no configuration can silently widen owner/source scope.
- Acceptance:
  `offline_go_default_off_single_confined_sqlite_path_strict_independent_provider_flags_bounded_scope_and_budgets_zero_runtime_side_effects`
- Next frontier: the former generic `UIR-03` aggregate is superseded. Exact
  `UIR-03A` Go-A R2 and `UIR-03B` R2 are accepted; continue only with a later
  dependency-ready UIR unit while runtime, workers, providers and productive
  query paths remain disabled.

### UIR-03A - Default-Off Knowledge Runtime Composition Boundary

- Class: `repo_only`
- Owner: Bob/Terra implementation; Root/Sol review
- Dependencies: `UIR-02`, USI-03, USI-04 and accepted caller inventory blob
  `9e87609d00691f1e4b0ca1a5c7b7c2ed2bcca244`
- Allowed paths:
  - `src/unified_source_index_runtime.py`
  - `tests/test_unified_source_index_runtime_composition.py`
- Work:
  - implement one default-off `KnowledgeRuntime` composition value around the
    accepted runtime config and query planner boundary;
  - disabled mode binds `None`; read-only mode may bind one already supplied,
    frozen planner proxy without executing it;
  - missing planners and uncomposed productive modes degrade with fixed,
    content-free status codes;
  - build, bind, snapshot and close are serialized, bounded and idempotent;
  - imports and all public operations perform no App, Obsidian, SQLite,
    provider, worker, network, filesystem or environment access.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_runtime_composition.py`
- Done when: the nine frozen `UIR-03A-SOL-ARCH-v1` regression families pass,
  independent QA and Deep-Sol accept one blob-bound candidate, and no live
  activation authority exists.
- Scope decision: this slice guarantees that the new Knowledge runtime itself
  has no Obsidian dependency. Removing existing App-level Obsidian imports is
  owned by `UIR-12`, not silently absorbed here.

### UIR-03B - Application State And Shutdown Wiring

- Class: `repo_only`
- Owner: Bob/Terra implementation; Root/Sol review
- Dependencies: accepted `UIR-03A` and a fresh exact active-file handoff
- Acceptance lineage: the initial Rev149 claim was blocked, then the explicit
  R2 transfer `ABC-UIR03B-R2-REV158` was released accepted. Later accepted
  `UIR-04C` and `UIR-05B` claims legitimately advanced `app.py` and
  `src/app_initializer.py`; the old claim-origin blocker is historical only.
- Allowed paths:
  - `src/app_initializer.py`
  - `app.py`
  - focused existing App initialization tests selected by the frozen claim
- Work:
  - construct exactly one accepted `KnowledgeRuntime` through the initializer;
  - place the same instance on `app.state`, bind the canonical
    `query_knowledge` planner seam and close/unbind it once at shutdown;
  - preserve existing App behavior and leave complete Obsidian retirement to
    `UIR-12`.
- Done when: disabled and read-only startup wiring is deterministic, import
  failure is controlled and exactly one runtime instance is app-owned.

### UIR-04 - Bounded Index Job Worker Lifecycle

- Class: `repo_only`
- Owner: Bob
- Dependencies: accepted `UIR-03A` and `UIR-03B`, USI-04
- Allowed paths:
  - `src/unified_source_index_job_runner.py`
  - `tests/test_unified_source_index_job_runner.py`
  - `app.py` only in Charlie's serialized integration claim
- Work:
  - cooperative worker over USI JobStore leases/cursors/retries;
  - bounded concurrency, time, batch and shutdown checkpoint behavior;
  - no detached shell, free command or TaskScheduler state duplication;
  - optional scheduler/startup wakeup only, with JobStore as truth;
  - disabled/read-only runtime starts no writer worker.
- Tests: `python -m pytest -q tests/test_unified_source_index_job_runner.py`
- Done when: restart, duplicate wakeup, cancellation and worker crash preserve a
  single resumable job state and never duplicate source writes.

### UIR-05 - Read-Only Status, Health And Diagnostics Routes

- Class: `repo_only`
- Owner: Bob with route-owner handoff
- Dependencies: accepted `UIR-03A` and `UIR-03B`, USI-12/GRO metric contract where available
- Allowed paths:
  - `src/unified_source_index_runtime_status.py`
  - `routes/unified_source_index_routes.py`
  - `src/service_health.py`
  - `routes/diagnostics_routes.py`
  - `app.py` only for serialized router registration
  - `tests/test_unified_source_index_routes.py`
  - `tests/test_unified_source_index_service_health.py`
- Work:
  - admin/owner-safe readiness, generation, queue and projection summaries;
  - bounded status reads with no corpus, vault or ledger scan;
  - route registration and authorization tests;
  - no source path, owner identifier, raw query or content in diagnostics;
  - diagnostics/exporter failure cannot block query or indexing.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_routes.py tests/test_unified_source_index_service_health.py`
- Done when: health distinguishes disabled, ready, degraded and stale without
  probing private sources or optional engines at scrape time.

### UIR-06 - Knowledge Query Runtime Facade And Compatibility Contract

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UIR-01`, USI-07 and USI-08
- Allowed paths:
  - `src/knowledge_query_runtime.py`
  - `src/knowledge_query_compat.py`
  - `tests/test_knowledge_query_runtime.py`
  - `tests/test_knowledge_query_compat.py`
- Work:
  - one async-safe bounded facade returning Context Items and exact refs;
  - generation pinning and cancellation per request;
  - explicit legacy adapter/fallback result with reason and confidence;
  - no raw engine object escapes to Chat, Agent or Tool code;
  - fail-closed policy and fail-soft optional-provider behavior.
- Tests:
  - `python -m pytest -q tests/test_knowledge_query_runtime.py tests/test_knowledge_query_compat.py`
- Done when: consumers can switch runtime generations without knowing SQLite,
  Chroma, CBM, RAPTOR or Lineage APIs.

### UIR-07 - Personal Docs And RAG Shadow Integration

- Class: `repo_only`
- Owner: Bob with Personal Docs owner handoff
- Dependencies: `UIR-06`, UDA Personal Docs adapter fixture, USI-11 comparison
- Allowed paths:
  - `src/personal_docs.py`
  - `src/rag_manager.py`
  - `routes/personal_routes.py`
  - `src/knowledge_query_shadow.py`
  - `tests/test_unified_source_index_personal_docs_shadow.py`
- Work:
  - preserve current uploads, exact reads, indexing controls and fallback;
  - route selected synthetic reads through a shadow comparator;
  - compare identities, locators, result relevance and policy without logging
    query/content or entering shadow results into prompts;
  - no active collection rebuild, delete or dual authoritative write;
  - expose a compatibility deprecation signal only after parity.
- Tests: `python -m pytest -q tests/test_unified_source_index_personal_docs_shadow.py`
- Done when: legacy remains authoritative in shadow mode and parity evidence can
  support a bounded canary without changing source behavior.

### UIR-08 - Chat Context Consumer Cutover

- Class: `repo_only`
- Owner: Bob with Chat hotfile handoff
- Dependencies: `UIR-06`, `UIR-07`, USI-08
- Allowed paths:
  - `src/chat_processor.py`
  - `src/context_orchestrator.py`
  - `tests/test_unified_source_index_chat_context.py`
  - existing focused Chat RAG budget tests after explicit claim
- Work:
  - register USI as one bounded core context provider;
  - remove direct query selection from ChatProcessor in the new path;
  - preserve RAG-off, incognito, secure/local-only and owner policy behavior;
  - enforce provider/token/time budgets and exact evidence refs;
  - legacy fallback remains explicit and cannot double-inject context.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_chat_context.py tests/test_chat_processor_rag_budget.py`
- Done when: one request produces at most one authoritative knowledge context
  pack and disabling USI restores current Chat behavior.

### UIR-09 - Agent, AI Interaction And Coding Consumer Cutover

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UIR-06`, `UIR-08`
- Allowed paths:
  - `src/ai_interaction.py`
  - `src/coding_agent_memory_bridge.py`
  - `src/agent_context_transparency.py` only after contract-owner handoff
  - `tests/test_unified_source_index_agent_context.py`
  - `tests/test_coding_agent_memory_bridge.py`
- Work:
  - bind Agent retrieval to the same runtime facade and budgets;
  - replace global RAG indexing/query assumptions with injected capabilities;
  - code discovery can request structural mode but exact reads remain separate;
  - reserve the direct/exact two-call code path, public-call accounting and
    coverage certification for `UIR-09A`; this slice must not introduce a
    direct Forge/CBM/provider tool or source read;
  - Context Transparency receives include/exclude/clip/stale/source refs;
  - no autonomous Memory write or code mutation is added.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_agent_context.py tests/test_coding_agent_memory_bridge.py`
- Done when: Chat and Agent use the same evidence contract while retaining
  distinct budgets, policies and exact-reader follow-ups.

### UIR-10 - Canonical Tool Provider Binding

- Class: `repo_only`
- Owner: Bob after TAX/USI-09 handoff
- Dependencies: USI-09, `UIR-06`, TAX Descriptor and security readiness
- Allowed paths:
  - `src/knowledge_query_tool_provider.py`
  - `src/app_initializer.py` only in Charlie's serialized integration claim
  - `tests/test_knowledge_query_tool_provider.py`
- Work:
  - bind the accepted `query_knowledge` descriptor to the runtime facade;
  - one canonical invocation regardless of internal provider plan;
  - no direct CBM, Chroma, RAPTOR or timeline public tools;
  - exact readers retain current confirmations and path policy;
  - unavailable/deferred provider modes return structured partial evidence.
- Tests: `python -m pytest -q tests/test_knowledge_query_tool_provider.py`
- Done when: tool dispatch is provider-neutral and changing an internal engine
  cannot change public tool identity or security class.

### UIR-09A - Forge-Bound Two-Call Code Retrieval Certification

- Class: `repo_only`
- Owner: Bob with Forge/USI contract-owner handoff and Charlie test review
- Dependencies: `UIR-09`, `UIR-10`, `FCA-00`, `FCA-01`, `FCA-02`, `FCA-05`,
  `FCA-06` and `FCA-06A`; TAX descriptor/security readiness; accepted
  `code-intelligence-evaluation-protocol.md` two-call certification contract.
  `FCA-06A` is the final precondition: it bridges the accepted locator to the
  existing public `read_file` identity in Forge-locator mode. It is upstream
  capability work and must not depend on `UIR-09A`, avoiding a dependency
  cycle; this slice must not guess or recreate the bridge.
- Queue position: intentionally after `UIR-10` despite the suffix. The public
  `query_knowledge` identity and its default-off binding must exist before the
  two-call assertion can be meaningful.
- Allowed paths:
  - `src/knowledge_query_tool_provider.py` only after the released `UIR-10`
    hotfile claim;
  - `src/knowledge_query_runtime.py` only after a released `UIR-06` contract
    handoff;
  - `src/agent_tools/knowledge_tools.py` only after its current canonical
    `query_knowledge` tool-owner handoff;
  - `tests/test_knowledge_query_two_call_code_retrieval.py`;
  - `tests/test_knowledge_query_tool_provider.py` only within a serialized
    `UIR-10` follow-up claim;
  - `tests/test_query_knowledge_tool.py` only with the paired
    `knowledge_tools.py` owner claim when serializer/request coverage is
    required;
  - `src/code_retrieval_certification.py` only after a separate exact-path
    claim;
  - `tests/test_code_retrieval_certification.py` only with its paired source
    claim.
- Forbidden:
  - Forge adapter, repository snapshot, coverage-manifest or `read_file`
    Forge-locator bridge
    implementation paths owned by FCA;
  - direct CBM/embedding/graph/MCP public dispatches, filesystem reads, source
    scans, productive indexing, prompt cutover, source text logging or any
    default-on code provider.
- Work:
  - expose a direct/exact code request mode through the one canonical
    `query_knowledge` dispatch; it may return one revision-bound Forge locator
    plus coverage/generation/retrieval-mode evidence, never source text;
  - require a caller's optional second public dispatch to be the existing
    canonical `read_file` identity in Forge-locator mode, using precisely that
    returned locator and revision; record one `query_knowledge` plus one
    `read_file` as the maximum public-tool budget, with no new public tool;
    internal USI/lexical/CBM/embedding spans are separately observable but do
    not count as public tools;
  - permit positive certification only for FCA-declared `complete` coverage.
    Propagate `partial`, `stale`, `failed`, `not_indexed` and `out_of_scope`
    without substituting HEAD, a legacy reader, a semantic hit or a current
    projection generation;
  - reject a forged/substituted locator, path, range, manifest digest or Forge
    revision before a read; stop after call 1 for every honest non-success
    state; and make retries/extra dispatches visible and certification-failing;
  - bind synthetic positive and negative cases to the protocol-derived
    coverage corpus. Prove locator range coverage, Forge revision equality and
    content-free exact-read digest equality without persisting source/query
    text;
  - keep the runtime, the direct/exact mode and all code projections disabled
    by default. This provides fixture evidence only and does not authorize a
    productive index, provider, tool cutover or `USI-LIVE-ACTIVATION`.
- Tests:
  - `python -m pytest -q tests/test_knowledge_query_two_call_code_retrieval.py tests/test_knowledge_query_tool_provider.py tests/test_query_knowledge_tool.py`
  - fixture-only certification verifier over a complete synthetic manifest,
    a partial/stale manifest and the protocol's negative corpus; no Forge
    repository, source file, model, engine, provider process or network call.
- Done when: every eligible fixture line derived from a coverage-complete
  manifest reaches a Forge locator in call 1 and exactly reads from the same
  Forge revision through the existing `read_file` identity in Forge-locator
  mode in call 2; every positive receipt has exactly two public tool dispatches
  and introduces no public tool identity, and every negative/partial/stale case
  fails honestly with no unauthorized reader call. Semantic/structural ranking
  may remain available but cannot issue a line certificate.
- Acceptance:
  `offline_go_full_manifest_line_denominator_zero_false_certificates_one_query_knowledge_plus_one_existing_read_file_forge_locator_mode_max_no_new_or_hidden_public_tools_revision_locator_digest_bound_honest_partial_stale_and_negative_outcomes_default_off`

### UIR-11 - Generation Pinning, Circuit Breaker And Rollback

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UIR-04` through `UIR-10`
- Allowed paths:
  - `src/unified_source_index_runtime_control.py`
  - `tests/test_unified_source_index_runtime_control.py`
- Work:
  - immutable query generation per request and projection generation checks;
  - bounded timeout/error/stale thresholds and circuit breaker;
  - canary scope selection without raw owner labels in metrics;
  - automatic fallback to prior generation with an honest partial result;
  - rollback stops new jobs and preserves safe in-flight checkpoints.
- Tests: `python -m pytest -q tests/test_unified_source_index_runtime_control.py`
- Done when: injected store/provider failures cannot produce mixed-generation or
  falsely successful answers and rollback needs no source mutation.

### UIR-12 - Direct Caller Elimination And Legacy Retirement Decisions

- Class: `repo_only`
- Owner: Alice for decisions, Charlie for audit integration
- Dependencies: `UIR-07` through `UIR-11`
- Allowed paths:
  - `scripts/audit_unified_source_index_direct_callers.py`
  - `tests/test_audit_unified_source_index_direct_callers.py`
  - `docs/plans/unified-source-index-consumer-migration-report.md`
- Work:
  - prove no active Chat/Agent path bypasses the runtime facade;
  - classify RAGManager and MemoryVector paths as keep/fallback/retire per
    source wave;
  - classify every Obsidian/ORCA-plugin query, context, state, auth, route,
    scheduler and maintenance caller as `retire/excluded`;
  - name the exact first-party replacement and owner for every retiring caller;
  - name exact parity evidence and rollback for each retirement;
  - prohibit removal while any active caller or source policy depends on it;
  - keep admin/domain exact operations separate from query consumers.
- Tests: `python -m pytest -q tests/test_audit_unified_source_index_direct_callers.py`
- Done when: every direct caller has one owner and no duplicate active prompt
  injection or public query surface remains unexplained.

### UIR-12A - Post-Retirement Native Runtime Verification

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `UIR-12`, `NMG-07-legacy-runtime-retirement`
- Allowed paths:
  - `scripts/audit_native_runtime_legacy_dependencies.py`
  - `tests/test_audit_native_runtime_legacy_dependencies.py`
  - `docs/plans/unified-source-index-consumer-migration-report.md`
- Work:
  - prove core/app code has no direct `plugins.obsidian` import;
  - prove disabled/native startup registers no Obsidian route, provider, tool,
    job, scheduler or maintenance task;
  - prove `query_knowledge` is composed through the first-party planner;
  - preserve any retained plugin code as inert archive outside product runtime.
- Tests:
  - `python -m pytest -q tests/test_audit_native_runtime_legacy_dependencies.py`
- Done when: static and startup evidence demonstrate native runtime independence
  without claiming deletion of historical repository evidence.

### UIR-13 - Runtime Concurrency, Performance And Failure Suite

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `UIR-03A`, `UIR-03B`, `UIR-04` through `UIR-12`, `UIR-12A`, native observability contract
- Allowed paths:
  - `tests/test_unified_source_index_runtime_concurrency.py`
  - `tests/test_unified_source_index_runtime_failure_matrix.py`
  - `scripts/benchmark_unified_source_index_runtime.py`
  - `docs/plans/unified-source-index-runtime-acceptance.md`
- Work:
  - concurrent query/index, restart, shutdown, cancellation and SQLite busy
    tests;
  - optional provider timeout, Chroma/CBM absence and corrupted projection;
  - startup/import latency and disabled-mode overhead;
  - content-free p50/p95 and fallback/circuit metrics through GRO;
  - no private corpus or raw query in committed evidence.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_runtime_concurrency.py tests/test_unified_source_index_runtime_failure_matrix.py`
- Done when: bounded synthetic load meets declared SLOs and disabled/degraded
  runtime behavior is cheaper and safer than an unbounded fallback.

### UIR-14 - Runtime Closure Packet

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `UIR-00`, `UIR-01`, `UIR-02`, `UIR-03A`, `UIR-03B`, `UIR-04` through `UIR-13`, including `UIR-12A`; ULO closure and at least one green
  UDA source wave for any proposed activation scope
- Allowed paths:
  - `docs/plans/unified-source-index-runtime-closure-packet.md`
  - `docs/plans/unified-source-index-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - exact artifact, config, routes, source wave, generation and fallback;
  - startup/shutdown/health/authorization and package parity evidence;
  - rollback to the prior native query path and direct-caller audit result;
  - static proof that runtime, selected source scope and rollback do not require
    Obsidian plugin code or artifacts;
  - declare deferred domains/providers without overclaim;
  - contribute evidence to the existing parent gate only.
- Tests: focused UIR suite plus JSON/guidance validation
- Done when: `USI-15` can name a fully wired, disabled-by-default runtime and a
  one-command rollback for the selected bounded source scope.

## 8. Dependency And Hotfile Rules

- `UIR-00` precedes all active-file edits.
- `UIR-01`, UDA contract work and ULO contract work may proceed on disjoint new
  files after USI identity is stable.
- `UIR-03`, `UIR-04` and route registration serialize `app.py`.
- `UIR-07` serializes Personal Docs/RAG files with existing RAG work.
- `UIR-08` serializes Chat and Context Orchestrator files.
- `UIR-10` waits for TAX and USI-09; it does not edit catalog/schema files.
- `UIR-14` is a Charlie-only integration write to the parent activation packet.
- No UIR worker edits domain write routes, CBM/Lineage engines, Lens UI or GRO
  exporter files without the corresponding roadmap handoff.

## 9. Acceptance Metrics

- disabled startup performs zero USI source scans and starts zero index writers;
- shadow results never enter prompts or tool outputs;
- one Chat/Agent request has one authoritative knowledge context pack;
- every USI result has source/version/locator/generation evidence;
- a certified code line is reachable only through one `query_knowledge` call
  followed by the existing `read_file` identity in Forge-locator mode against
  the same immutable revision; no semantic/structural projection is treated as
  source authority and no new public tool is added;
- complete/partial/stale/failed/not-indexed/out-of-scope code coverage remains
  visible to the caller and a partial result never claims exhaustive line
  retrieval;
- legacy fallback is explicit, bounded and owner/policy equivalent;
- Obsidian is absent from the selected runtime, fallback and activation scope;
- optional provider failure never hides an exact/lexical result;
- rollback changes no domain truth and restores the prior query path;
- no active direct RAG consumer remains unclassified;
- status and metrics never scan private sources or expose raw labels/content.

## 10. Shared Activation Language

UIR does not have an independent live gate. Runtime activation is one required
part of the parent phrase:

`GO USI-LIVE-ACTIVATION: activate USI <version> for <source scopes> in <environment> using <policies/generation>; observe <window>; auto-rollback via <plan> on No-Go.`

A product Go is invalid unless `UIR-14` is green for the exact artifact,
environment, source wave and generation named in that phrase.

## 11. Definition Of Done

- USI is composed once through dependency injection and remains default-off.
- Job state is owned by USI JobStore, not shell jobs or TaskScheduler.
- health/status/routes are bounded, authorized and content-free.
- Personal Docs, Chat and Agent consumers use one feature-flagged runtime facade.
- `query_knowledge` binds to that same facade after TAX handoff.
- direct/exact code retrieval, when enabled by a later scope-specific decision,
  preserves the certified maximum of one `query_knowledge` dispatch plus one
  existing `read_file` dispatch in Forge-locator mode and remains
  revision/coverage bound.
- shadow/canary/fallback/rollback behavior is test-proven.
- direct legacy consumers have explicit keep/retire decisions.
- all Obsidian-backed callers are retired/excluded and no new dependency points
  at `plugins/obsidian/**`.
- UIR adds evidence to `USI-LIVE-ACTIVATION` and creates no second gate.
