# Unified Source Index Integration Impact Map

Updated: 2026-07-13

Status: planning evidence; no runtime activation

Parent program: `OWM-15` / Unified Source Index Foundation

## 1. Purpose

This document maps the current Odysseus code paths that must participate in a
complete Unified Source Index rollout. It closes the gap between the USI core
store/query roadmap and the real application composition, consumers, source
domains and data lifecycle.

It is not a fourth truth system and is not an implementation queue. The three
executable child roadmaps are:

- `docs/plans/unified-source-index-runtime-integration-roadmap.md` (`UIR`);
- `docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md` (`UDA`);
- `docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md`
  (`ULO`).

All three share the parent `USI-LIVE-ACTIVATION` contract. They do not create
independent product activation gates.

## 2. Executive Finding

The USI core roadmap is sufficient for records, SQLite stores, jobs, lexical
and federated query, Chroma manifests, context projection, RAPTOR manifests and
the canonical tool contract. It intentionally does not own several active
runtime and domain hotfiles.

A usable product therefore still needs four closure boundaries:

1. compose USI into application startup, shutdown, health and configuration;
2. move direct RAG/Memory consumers behind the bounded query/context service;
3. implement real read-only source adapters and incremental change signals;
4. integrate owner rename/delete, export, backup, restore, wipe and retention.

The current code already has canonical domain owners. The correct migration is
adapter and consumer integration, not replacement of those owners.

## 3. Current Runtime Flows

### 3.1 Personal Documents And Chroma

```text
app.py
  -> src.rag_singleton.get_rag_manager
  -> VectorRAG / Chroma
  -> RAGManager compatibility wrapper

ChatProcessor
  -> PersonalDocsManager.rag_manager.search
  -> prompt context

PersonalDocsManager
  -> in-memory keyword index
  -> RAGManager index/search/remove/rebuild
```

Current code elements:

- `app.py` creates the RAG manager and passes it to app components;
- `src/rag_singleton.py` owns lazy Chroma-backed initialization;
- `src/rag_vector.py` owns Chroma collections, document writes and search;
- `src/rag_manager.py` is a compatibility wrapper around `VectorRAG`;
- `src/personal_docs.py` owns file discovery, keyword search and direct RAG
  calls;
- `src/chat_processor.py` directly calls the personal RAG search path;
- `src/ai_interaction.py` directly starts personal-document indexing;
- `routes/personal_routes.py` and `routes/embedding_routes.py` expose current
  indexing and collection operations.

Target:

- Personal Docs remains the domain reader and file owner;
- USI owns source/version/chunk occurrence identity and index jobs;
- Chroma remains a rebuildable semantic projection;
- direct query consumers move to USI while exact file reads remain separate;
- `RAGManager` remains a compatibility facade until parity permits retirement.

### 3.2 Personal Memory

Current code elements:

- `src/memory.py` and `MemoryManager` own `memory.json` domain writes;
- `src/memory_provider.py` owns provider-facing remember/recall behavior;
- `src/memory_vector.py` owns a separate semantic Memory projection;
- `src/memory_lifecycle.py` and `src/memory_lifecycle_adapters.py` own lifecycle
  decisions and review states;
- `routes/memory_routes.py`, Telegram and planning paths call `add_entry`;
- Context and AI Lens instrumentation currently use memory-specific refs.

Target:

- Memory writes and lifecycle decisions do not move into USI;
- approved Memory records are exposed through a read-only Source Adapter;
- USI refs replace projection-local IDs in cross-domain retrieval evidence;
- MemoryVector remains a rebuildable projection until an explicit parity
  decision retires it.

### 3.3 ORCA, Vault And RAPTOR

Current code elements:

- `plugins/obsidian/backend/derived_index.py` persists a vault-local derived
  chunk index;
- `plugins/obsidian/backend/query_layer.py` and `context_provider.py` retrieve
  from plugin-owned paths;
- `plugins/obsidian/backend/hybrid_retrieval.py` reads RAPTOR metadata and
  summaries;
- `raptor_cache.py`, `raptor_rebuild.py` and `raptor_warming.py` own current
  maintenance behavior;
- vault ledger/history/security modules own source-local state and policy.

Target:

- vault files and vault policy remain domain truth;
- ledger state may remain a discovery/job checkpoint;
- `derived_index.json` becomes a temporary compatibility projection;
- RAPTOR consumes immutable USI input snapshots and emits versioned Derived
  Runs with evidence refs;
- no second universal graph or maintenance scheduler is introduced.

### 3.4 Chat, Agents, Context And Tools

Current code elements:

- `src/chat_processor.py` performs direct document retrieval;
- `src/ai_interaction.py` holds global RAG manager references and indexing
  actions;
- `src/coding_agent_memory_bridge.py` projects code work into Memory context;
- `src/agent_context_transparency.py` and AI Lens modules already model
  bounded observations;
- `src/tool_catalog.py`, `src/tool_index.py`, tool schemas and execution own
  public tool identity and dispatch;
- exact filesystem readers live under `src/agent_tools/filesystem_tools.py`.

Target:

- one runtime `KnowledgeQueryService` binds the USI planner;
- Chat and Agents receive only bounded Context Items from the existing context
  orchestration boundary;
- one public `query_knowledge` tool selects lexical, semantic, symbol, graph or
  timeline mode;
- `read_file`, `grep`, `glob` and domain readers remain exact verification;
- provider-internal calls are metrics, not duplicate TUA invocations.

### 3.5 Code, Git And Project Versions

Current code elements:

- `src/repo_registry.py` owns registered repository identity;
- `src/repo_git_adapter.py` owns bounded read-only Git facts;
- `src/project_version_store.py` owns immutable project version manifests;
- Local Forge and `commit_project` own version creation and commit workflow;
- current code navigation relies primarily on exact filesystem tools.

Target:

- CBM is an isolated, pinned and disposable structural projection;
- USI maps every graph result to repository, revision, source and locator;
- Code Lineage extends read-only history evidence without changing Git;
- repo registration, commits and Project Version records are never duplicated;
- CBM upstream project control, hooks and direct MCP tools remain disabled.

### 3.6 Planning, Inbox And Communication Domains

Current domain owners include:

- Planning: `src/planning_source_inventory.py`,
  `src/planning_source_memory.py`, `src/planning_mcp_service.py` and plan stores;
- Documents/Research: document routes, `src/research_handler_storage.py` and
  Deep Research files;
- Universal Inbox: discovery, extraction, provenance, routing, placement and
  review modules;
- Nextcloud: source provider, resumable scanner, intake ledger, policy and
  WebDAV adapter;
- Email: owner-scoped account/cache/message/poller modules and routes;
- Calendar/Todos: SQL domain models, CalDAV sync/writeback, task and note
  routes;
- Contacts: contacts JSON/route and vCard helpers;
- Sessions/Chats: SessionManager and session routes.

Target:

- each domain keeps its store, writes, review and provider policy;
- each domain receives a read-only Source Adapter with explicit content policy;
- best-effort change signals schedule discovery and never make a domain write
  fail;
- deletion and access loss propagate tombstones before stale results can be
  returned;
- private communication domains remain default-off until reprioritized and
  explicitly included in `USI-LIVE-ACTIVATION` scope.

## 4. Affected System Matrix

| System | Canonical owner after USI | Required update | Retirement candidate |
| --- | --- | --- | --- |
| Runtime composition | application composition root | initialize disabled USI services, routes, health and lifecycle | ad hoc global service lookup |
| Personal Docs | PersonalDocsManager/files | Source Adapter, index jobs, query facade and exact-reader refs | in-memory/query duplication after parity |
| Chroma document RAG | USI projection manifest plus Chroma | map USI occurrences to collection generations | Chroma IDs as chunk identity |
| Personal Memory | MemoryManager/lifecycle | approved-record adapter and evidence mapping | duplicate semantic query entrypoint |
| ORCA/Vault | vault files and vault policy | adapter, checkpoint mapping and USI/RAPTOR evidence | active `derived_index.json` query path |
| RAPTOR | existing derived-run worker | immutable USI snapshots and incremental invalidation | global corpus scans and unversioned summaries |
| Chat/Agents | Context Orchestrator | replace direct RAG search with bounded query service | parallel prompt injection |
| Tool runtime | TAX catalog/registry | one `query_knowledge` provider binding | engine-specific public tools |
| Code graph | CBM projection | isolated process, mapping, query and graph API | direct engine database/UI control |
| Git history | Git/Project Versioning | read-only lineage provider | synthetic commit/version truth |
| Planning | Planning stores/MCP | read-only adapter and direct retrieval | Planning-to-Memory duplication where unnecessary |
| Inbox/Nextcloud | Inbox/Nextcloud domain flows | adapters, checkpoints and tombstones | Raptor-only provenance as query truth |
| Email/Calendar/Todos/Contacts | domain DB/provider | policy-limited adapters after reprioritization | none; domain stores remain |
| Lens | Knowledge/Lens shell | Code mode, bounded routes and evidence inspector | second graph app shell |
| Observability | GRO | content-free USI/CBM/Lineage metrics | second exporter or metrics store |
| Data lifecycle | account/domain owners plus ULO | rename/delete/export/backup/restore/wipe integration | orphaned projection data |

## 5. Runtime And Master Hotfiles

These paths require one Charlie/root integration writer and cannot be edited by
parallel domain workers:

- `app.py`;
- `src/app_initializer.py`;
- `src/constants.py` and `src/config.py`;
- `src/service_health.py` and `routes/diagnostics_routes.py`;
- `src/chat_processor.py` and active chat routes;
- `src/tool_catalog.py`, `src/tool_index.py`, tool schemas and execution;
- `routes/auth_user_rename.py`, `routes/backup_routes.py` and
  `routes/admin_wipe_routes.py`;
- `docs/plans/open-work-completion-master-roadmap.json`;
- `docs/plans/multi-agent-execution-guidance.json`.

Domain adapters should be implemented under
`src/unified_source_index_sources/` first. Existing domain files are touched
only by a later explicit change-signal or consumer handoff slice.

## 6. Job And Scheduler Boundary

USI `IndexJobRecord` is canonical for index discovery, extraction, projection,
delete propagation and rebuild progress.

- `src/bg_jobs.py` is a chat-scoped detached shell execution system and is not
  an index scheduler;
- `TaskScheduler` owns recurring user/system actions and must not become index
  truth;
- the USI runtime may use a bounded lifespan worker or a scheduler wakeup, but
  all cursors, leases, retries and completion state stay in `JobStore`;
- RAPTOR/GMI maintenance workers consume bounded jobs after their own contract
  gates and do not own source discovery.

## 7. Data Lifecycle Boundary

The complete rollout must cover:

- stable owner scope across username rename;
- owner/domain/source deletion and projection purge;
- access-revocation tombstones;
- user portability export without leaking other owners or engine internals;
- consistent SQLite system backup and verified restore;
- projection rebuild after restore without changing USI identity;
- category wipe and factory reset behavior;
- bounded retention for query cache, jobs, stale versions and tombstones;
- WAL/busy/corruption recovery and disk-full failure behavior.

System backup may preserve USI index truth. User export should prefer domain
truth plus a redacted source/version manifest; it must not export a raw shared
engine database. Chroma and CBM databases remain rebuildable and optional in
backups.

## 8. Required Migration Sequence

1. Implement and test USI contracts, stores, jobs and bounded query.
2. Compose a disabled runtime and expose read-only health/status.
3. Build domain adapters against synthetic and read-only fixtures.
4. Shadow-index selected sources and compare count, locator, policy and query
   parity without changing active answers.
5. Bind Chat, Agents and `query_knowledge` to a feature-flagged runtime facade.
6. Prove owner rename/delete/export/backup/restore and projection rebuild.
7. Activate one bounded source wave under `USI-LIVE-ACTIVATION`.
8. Observe quality, latency, stale results and fallback behavior.
9. Retire a legacy query path only after its explicit parity and rollback gate.

No step performs permanent dual write between equal truths. During shadow mode,
domain truth is read once into USI and legacy projections may be compared, but
only one query path is authoritative for a request.

## 9. Explicit Non-Changes

USI does not change:

- Memory creation, edit, review or lifecycle policy;
- Planning writes, applies, deletes or MCP mutation gates;
- Email send/delete, CalDAV writeback, task/reminder or contact mutation rules;
- repository registration, Git commits or Project Version creation;
- AI Lens event truth or model reasoning policy;
- GMI model eligibility or scheduler ownership;
- GRO exporter and dashboard ownership;
- exact filesystem/domain read confirmation behavior.

## 10. Closure Roadmap Assignment

| Gap | Executable owner | Parent dependency |
| --- | --- | --- |
| Runtime composition, feature flags, service health and job lifecycle | `UIR` | USI-01/02/03/04 |
| Direct RAG/Chat/Agent consumer cutover and fallback | `UIR` | USI-07/08/09 |
| Real source adapters and change signals | `UDA` | USI-01/04 plus domain handoff |
| Owner rename/delete, export, backup, restore, wipe and retention | `ULO` | USI-03/04 plus domain handoff |
| Code structural provider | `CBM` | USI identity and runtime provider binding |
| Code history | `CLT` | USI identity and read-only Git evidence |
| Code visualization | `LCG` | bounded CBM/USI APIs |

`USI-15` may prepare the final activation packet only when UIR and ULO are
green for the selected scope and at least one UDA source wave has complete
parity evidence. Deferred domains stay named and disabled.
