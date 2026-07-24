# Unified Source Index Runtime And Consumer Integration Roadmap

Updated: 2026-07-13

Status: planned child track; runtime and productive query path default-off

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
| Code provider | CBM | register an optional structural provider | expose upstream MCP/tools/config |
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

fallback
  -> legacy Personal Docs/RAG search or exact domain reader
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
- Status: `ready_after_goal_start`
- Dependencies: explicit goal; current hotfile claims and dirty work inspected
- Allowed paths:
  - `docs/plans/unified-source-index-runtime-integration-roadmap.md`
  - `docs/plans/unified-source-index-runtime-inventory.json`
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

### UIR-01 - Runtime Service And State Contract

- Class: `repo_only`
- Owner: Bob
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

### UIR-02 - Data Paths, Feature Flags And Configuration

- Class: `repo_only`
- Owner: Bob with Charlie integration
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

### UIR-03 - Composition Root And Dependency Injection

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `UIR-02`, USI-03, USI-04 and active-file handoff
- Allowed paths:
  - `src/unified_source_index_runtime.py`
  - `src/app_initializer.py`
  - `app.py`
  - `tests/test_unified_source_index_runtime_wiring.py`
  - `tests/test_app_router_initialization_order.py`
- Work:
  - build one runtime object from accepted store/query/adapter dependencies;
  - place it on application state and inject it into consumers/routes;
  - opening the app in disabled mode creates no scan, job or engine process;
  - startup failure is controlled and leaves legacy runtime available;
  - import order and optional dependencies remain safe.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_runtime_wiring.py tests/test_app_router_initialization_order.py`
- Done when: disabled and read-only startup are deterministic, dependency
  failures do not break application import and exactly one runtime exists.

### UIR-04 - Bounded Index Job Worker Lifecycle

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UIR-03`, USI-04
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
- Dependencies: `UIR-03`, USI-12/GRO metric contract where available
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
  - classify RAGManager, MemoryVector and plugin query paths as keep/fallback/
    compatibility/retire per source wave;
  - name exact parity evidence and rollback for each retirement;
  - prohibit removal while any active caller or source policy depends on it;
  - keep admin/domain exact operations separate from query consumers.
- Tests: `python -m pytest -q tests/test_audit_unified_source_index_direct_callers.py`
- Done when: every direct caller has one owner and no duplicate active prompt
  injection or public query surface remains unexplained.

### UIR-13 - Runtime Concurrency, Performance And Failure Suite

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `UIR-03` through `UIR-12`, GRO metric contract
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
- Dependencies: `UIR-00` through `UIR-13`; ULO closure and at least one green
  UDA source wave for any proposed activation scope
- Allowed paths:
  - `docs/plans/unified-source-index-runtime-closure-packet.md`
  - `docs/plans/unified-source-index-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - exact artifact, config, routes, source wave, generation and fallback;
  - startup/shutdown/health/authorization and package parity evidence;
  - legacy rollback path and direct-caller audit result;
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
- legacy fallback is explicit, bounded and owner/policy equivalent;
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
- shadow/canary/fallback/rollback behavior is test-proven.
- direct legacy consumers have explicit keep/retire decisions.
- UIR adds evidence to `USI-LIVE-ACTIVATION` and creates no second gate.
