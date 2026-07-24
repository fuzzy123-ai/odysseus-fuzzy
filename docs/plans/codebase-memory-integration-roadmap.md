# Codebase Memory Integration Roadmap

Stand: 2026-07-17

Status: `CBM-08_accepted / CBM-09_TAX_USI_blocked / engine_default_off`

Master-Track: `0.29.x`, `OWM-16`, `L22`

## 1. Goal

Odysseus uses Codebase Memory (CBM) as the preferred rebuildable engine for
code symbols, calls, imports, inheritance, routes, data flow, communities and
impact queries while preserving USI as the source/version/provenance truth.

The track is done when an agent can locate any indexed code area in one bounded
`query_knowledge` call and use an existing exact reader as the optional second
call, while every result remains traceable to a USI source version and locator.

CBM supplies the structural data basis for `Lens > Code`. Rendering, navigation
and interaction remain owned by the existing Lens shell and the separate Lens
Code Graph roadmap.

## 2. Decision After Repository And Paper Review

CBM is no longer treated as only a parser experiment. The project already
contains a persistent typed code graph, incremental indexing, structural query
tools and a substantial Three.js graph UI. The arXiv v1 paper evaluated release
`v0.5.5` and provides credible architectural and scale evidence, but only
medium-strength agent-quality evidence.

Consequences:

1. CBM is the first implementation candidate for the complete derived code
   graph engine, not merely an AST extractor.
2. A local pinned acceptance spike is still mandatory because the paper used
   one model, author grading, tool-shaped questions and no edge precision/recall
   or ablation study.
3. The paper does not validate current post-`v0.5.5` semantic search,
   multi-repository, expanded LSP or UI behavior. Current features are tested as
   new product surface, not assumed from paper claims.
4. The expected winning retrieval condition is hybrid CBM plus exact
   `read_file`/`grep`, matching the paper's own conclusion.
5. CBM may keep an engine-native SQLite database only as a rebuildable
   projection. It never becomes repository, version, chunk or policy truth.
6. Operator decision on 2026-07-17: continue with CBM as the preferred engine.
   Graphify is not an active implementation dependency; it remains an optional
   benchmark/reference candidate if CBM fails a measured gate or a later
   decision explicitly reopens engine selection.
7. Neither CBM nor Graphify owns the product visualization. Lens reuses its
   existing shell/renderer and consumes bounded symbol, import and call data.

Primary references:

- `https://github.com/DeusData/codebase-memory-mcp`
- `https://arxiv.org/html/2603.27277v1`
- `docs/plans/unified-source-index-open-source-evaluation.md`

## 3. Canonical Ownership Boundary

| CBM capability | Odysseus treatment | Canonical owner |
| --- | --- | --- |
| Tree-sitter/LSP extraction | reuse behind pinned adapter | CBM engine implementation |
| Symbols and structural edges | rebuildable code projection with confidence | CBM projection, referenced by USI |
| Source/version/chunk IDs | map every result; never adopt engine IDs as truth | USI |
| Repository registration | disable or mirror from one-way adapter | Repo Registry |
| Commit/version history | never synthesize in CBM | Git/Project Version Store |
| Incremental file detection | driven by USI/repo change jobs | USI JobStore |
| Engine SQLite | disposable projection generation | CBM adapter |
| MCP tools | internal provider API only | TAX plus `query_knowledge` |
| ADR/project planning features | disabled/not imported | Planning system |
| Embeddings | optional derived provider, benchmarked against existing lane | USI EmbeddingStore/Chroma policy |
| Communities | code topology hint, not recursive RAPTOR | CBM projection |
| Hierarchical summaries | not CBM responsibility | USI/RAPTOR derived runs |
| Upstream graph UI | reference/fixtures only; no product dependency | Lens Code Graph roadmap |
| Metrics | emit into the GRO registry | GRO |
| Tool usage analytics | canonical external query invocation only | TUA |

## 4. Mode, Activation And Supply-Chain Policy

Planning uses `Standard ABC`. After explicit goal start, repository-only and
temporary local-fixture slices may run in `Overnight Backend Mode`.

Hard defaults:

- no installer script;
- no automatic edits to Codex, Claude, MCP, hook or instruction files;
- no network listener beyond an explicit loopback test process;
- no update check, telemetry or external network access;
- no direct public exposure of the upstream MCP server;
- exact release/commit, checksum, license, SBOM and build provenance pinned;
- downloaded binaries are not trusted solely because VirusTotal is green;
- source build or verified release artifact is preferred and sandboxed;
- project data remains local and owner-scoped;
- CBM failure falls back to USI lexical search plus existing `grep/read_file`;
- exactly one final gate, `CBM-LIVE-ACTIVATION`, controls productive indexing.

## 5. Acceptance Questions

The spike must answer these questions with evidence:

- Can CBM run without owning project registration, hooks or agent config?
- Can every node/edge result map to repo, source version, path, line/column and
  preferably symbol signature?
- Are node/edge IDs deterministic enough for a projection manifest, or must
  Odysseus maintain a separate mapping key?
- What is call-edge precision and recall for representative Python and
  JavaScript/TypeScript samples?
- Does incremental sync preserve correct locators after edit, rename and delete?
- Does hybrid CBM plus exact file read match or exceed grep/read quality?
- Are cold start, warm reopen and first-query-ready time acceptable on the
  development workstation and intended homeserver class?
- Do no-op, edit, add, delete, rename and bounded multi-file updates remain
  incremental without silently triggering a full-repository rebuild?
- Are incremental p50/p95 latency, CPU, peak RAM and database growth acceptable
  and reproducible on declared hardware?
- Can Lens consume symbol, import and call projections through a
  bounded/progressive API rather than requiring an unbounded whole-repository
  payload?
- Can upgrades rebuild the projection without changing USI identity?

## 6. Slice Queue

### CBM-00 - Pin, License, Security And Capability Freeze

- Class: `safe_offline`
- Owner: Charlie
- Status: `accepted_2026-07-18`
- Acceptance: `8 focused vendor-lock tests; 38 integrated vendor-lock and
  roadmap/master safety tests; strict JSON and primary-source readback green`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T174446+0200`
  - owner: `root` acting as Charlie; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T17:44:46+02:00`
  - lease_expires_at: `2026-07-18T21:44:46+02:00`
  - released_at: `2026-07-18T17:51:23+02:00`
  - allowed_paths: this roadmap,
    `docs/plans/codebase-memory-vendor-audit.md`,
    `config/codebase_memory.lock.json`,
    `tests/test_codebase_memory_vendor_lock.py`, and the Open-Work master
  - preserved_foreign_hunks: all runtime, engine, USI, TAX/TUA, hook, agent
    configuration, host and live-system changes
  - network_scope: primary-source read-only verification only; no download,
    clone, package install, update check or executable invocation
  - live_actions: `false`
  - evidence: release `v0.9.0` pinned to verified immutable commit
    `b637e3330c96cfe452da623db068c241aaa3ec01`; MIT license and release
    checksum-manifest digests frozen; installer/config/hook/write/update/watcher/
    UI/direct-tool/semantic-model surfaces default off; documentation tool-count
    and supported-version inconsistencies fail closed until an exact artifact
    probe; no clone, download, build, install, process, listener, index or vendor
    network call performed
  - artifact_hashes: lock
    `F24102C3039089951EC6314CAC96BB8466D4D0477371EF0537772D2F1862780F`,
    audit
    `1CA94EB2A356AA66340084562978277F14DB7D69C61A7A72C1838A0893712804`,
    tests
    `33EFF0FE25152B74150EB5758A076CB11B6580A1925A219FF9BCC47FE504BE60`
  - next_frontier: `CBM-01` repo-only fake-provider three-way harness
- Dependencies: explicit goal; active claims inspected
- Allowed paths:
  - `docs/plans/codebase-memory-integration-roadmap.md`
  - `docs/plans/codebase-memory-vendor-audit.md`
  - `config/codebase_memory.lock.json`
  - `tests/test_codebase_memory_vendor_lock.py`
- Work:
  - freeze exact source commit/release and distinguish paper-tested features
    from current unvalidated features;
  - capture license, dependency, binary, network, config-write and hook risks;
  - define source-build and verified-binary paths without executing installer;
  - list all upstream tools, ports, stores, watchers and UI assets;
  - define disabled-by-default flags and rollback.
- Tests: `python -m pytest -q tests/test_codebase_memory_vendor_lock.py`
- Done when: a deterministic lock and threat-oriented audit define exactly
  what may execute and what remains disabled.

### CBM-01 - Reproducible Three-Way Evaluation Harness

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `14 focused synthetic-harness tests; 52 integrated CBM vendor,
  harness and roadmap/master safety tests; deterministic content-free report
  generation and negative validation green`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T175325+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T17:53:25+02:00`
  - lease_expires_at: `2026-07-18T21:53:25+02:00`
  - released_at: `2026-07-18T18:02:26+02:00`
  - allowed_paths: `scripts/benchmark_code_intelligence.py`,
    `tests/fixtures/code_intelligence/`,
    `tests/test_benchmark_code_intelligence.py`,
    `docs/plans/code-intelligence-evaluation-protocol.md`, this roadmap and
    the Open-Work master
  - preserved_foreign_hunks: all engine/runtime, source, USI, TAX/TUA, hook,
    config, host and live-system changes
  - live_actions: `false`
  - evidence: 36 opaque content-free cases, six per category; three identical-
    budget arms with two quality repeats per cell; nine performance scenarios
    with three repeats; complete 216-quality/27-performance receipt matrices;
    deterministic canonical digest; duplicate, incomplete, private, unsafe,
    budget-biased and exact-reader-invalid inputs fail closed; engine/model/
    productive-source/network/process/listener/live actions all zero
  - artifact_hashes: harness
    `3A5893B81131260FBD136E7B3F55D5D8E5F8B18E3EEDDD34DE28066E8B7AC627`,
    question matrix
    `664B84859905EF54F803421F558479A3033B407A80C0A9A69FF7E20DB9D17D89`,
    tests
    `1D9C5C2012FC797860072653E2D4BF3216411BAD2F98ADF0049281E79420A8BE`,
    protocol
    `98BA23376EF1CD6D8B81F4375A3BDF1E9CB53B0F7E6D3ED6FB1D938E2748C99E`
  - next_frontier: `CBM-02` USI identity and locator mapping contract
- Dependencies: `CBM-00`
- Allowed paths:
  - `scripts/benchmark_code_intelligence.py`
  - `tests/fixtures/code_intelligence/`
  - `tests/test_benchmark_code_intelligence.py`
  - `docs/plans/code-intelligence-evaluation-protocol.md`
- Work:
  - compare `grep/read`, CBM-only and CBM-plus-exact-read;
  - 30-50 questions split across structural, exact/exhaustive, semantic,
    architecture, impact and negative cases;
  - same model, prompt, tool/time budget and repository commit;
  - repeated runs with raw content excluded from committed reports;
  - ground truth from source, AST/LSP, Git and manually labelled edge samples;
  - report quality, calls, tokens, p50/p95 and failure categories;
  - define repeatable performance scenarios for empty-projection cold start,
    warm reopen, first query, no-op sync, edit, add, delete, rename and a bounded
    multi-file change burst;
  - record declared hardware, OS, exact repository/engine commit, configuration,
    run count, wall time, CPU, peak RAM, database size/growth and touched work;
  - keep performance receipts content-free and distinguish extraction/indexing,
    query-ready and query latency.
- Tests: `python -m pytest -q tests/test_benchmark_code_intelligence.py`
- Done when: the harness can run with fake providers and produces a content-free
  schema before any real engine invocation.

### CBM-02 - USI Identity And Locator Mapping Contract

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `32 focused mapping-contract tests; 105 integrated USI, CBM and
  roadmap/master safety tests; canonical identity recomputation and ambiguity
  negatives green`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T180530+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T18:05:30+02:00`
  - lease_expires_at: `2026-07-18T22:05:30+02:00`
  - released_at: `2026-07-18T18:15:00+02:00`
  - allowed_paths: `src/code_intelligence_contract.py`,
    `tests/test_code_intelligence_contract.py`, this roadmap and the Open-Work
    master
  - preserved_foreign_hunks: all engine/runtime, source store, USI canonical
    contract/store, TAX/TUA, hook, config, host and live-system changes
  - live_actions: `false`
  - evidence: typed file/symbol/edge records recompute exact USI source,
    version, entity and relation IDs; NFC repo-relative UTF-8 paths and half-open
    byte/line/column ranges; method, confidence, extractor version and
    incomplete-parse markers; engine-independent fallback keys; duplicate names,
    overloads, identical files and moved paths remain distinct; ambiguous engine
    refs, ancestry, endpoint, traversal, absolute host path, duplicate JSON and
    tampered canonical IDs fail closed; engine/source/network/process/listener/
    live actions all zero
  - artifact_hashes: contract
    `1527C64A7AF9129ED96E7F212930E6A9028A5FEF2F7511E0E32443725429A3C3`,
    tests
    `631C4E160E2A35F67D81E41E10F7A8C15C23580EAFE7E6DF57FE6EE72385322F`
  - next_frontier: `CBM-03` isolated fake-process adapter and health contract
- Dependencies: USI-01 contract, `CBM-00`
- Allowed paths:
  - `src/code_intelligence_contract.py`
  - `tests/test_code_intelligence_contract.py`
- Work:
  - engine project/file/symbol/edge refs mapped to USI source/version/entity;
  - normalized repo-relative UTF-8 paths and byte/line/column ranges;
  - method, confidence, extractor version and incomplete-parse markers;
  - stable fallback keys when upstream IDs change;
  - no raw absolute host paths in API or reports.
- Tests: `python -m pytest -q tests/test_code_intelligence_contract.py`
- Done when: duplicate names, overloads, identical files and moved paths map
  without ambiguous source identity.

### CBM-03 - Isolated Process Adapter And Health Contract

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `37 focused fake-process/client tests; 142 integrated USI, CBM
  and roadmap/master safety tests; child-process cleanup readback green`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T181626+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T18:16:26+02:00`
  - lease_expires_at: `2026-07-18T22:16:26+02:00`
  - released_at: `2026-07-18T18:36:47+02:00`
  - allowed_paths: `src/codebase_memory_process.py`,
    `src/codebase_memory_client.py`, `tests/test_codebase_memory_process.py`,
    `tests/test_codebase_memory_client.py`, this roadmap and the Open-Work
    master
  - preserved_foreign_hunks: all real engine/artifact, productive source,
    projection, USI, TAX/TUA, hook, config, host and live-system changes
  - runtime_scope: injected/fake launcher and local test child processes only;
    no real CBM executable
  - live_actions: `false`
  - evidence: default-off stdio-only settings with exact vendor lock and
    explicit executable/config/data/allowed-root paths; state directories
    excluded from source root; egress receipt required before launch; minimal
    allowlisted environment with watcher/auto-index/UI/update/network/installer/
    mutation/export/diagnostics/model controls false; bounded start, exchange,
    timeout, cancellation, terminate/kill escalation and response size; strict
    JSON-RPC IDs and duplicate/non-finite rejection; locked protocol/version/
    commit, capability and health validation; crash, malformed response,
    protocol drift, unsafe controls, successful network call and degraded state
    stop the client; 23 bounded local fake-child cases per full focused suite;
    real CBM/artifact starts, listeners, network calls, source reads and live
    actions zero; interrupted debug-run orphan removed by exact PID and final
    Python-process readback empty
  - artifact_hashes: process adapter
    `9490D8D2E1782533F640EBE803517FF13C2E64D18F1CBCFAD56B58B0567A68B4`,
    client
    `EF655BAD3D6AAB69AEAB5F3BFCF95193FC441579144493B5EA5AFEC8D0E681BE`,
    process tests
    `F8B6E2192306112166CDE24CF7EB21152123CE21AB6D760F34B2BC83B98A4133`,
    client tests
    `90EA9F57A6961287F88E574DBDF872DE40228A0989C4034FB68119A94685EF59`
  - next_frontier: `CBM-04` one-way projection manifest and repository bridge
- Dependencies: `CBM-00`, `CBM-02`
- Allowed paths:
  - `src/codebase_memory_process.py`
  - `src/codebase_memory_client.py`
  - `tests/test_codebase_memory_process.py`
  - `tests/test_codebase_memory_client.py`
- Work:
  - bounded start/stop/timeout/cancel and loopback-only transport;
  - explicit executable/config/data paths from application settings;
  - scrubbed environment and disabled auto-config/hook/update behavior;
  - structured capability/version/health response;
  - malformed output, crash and protocol-version mismatch handling;
  - no process starts at app import time.
- Tests:
  - `python -m pytest -q tests/test_codebase_memory_process.py tests/test_codebase_memory_client.py`
- Done when: fake executable tests prove lifecycle and failure isolation without
  a real downloaded binary.

### CBM-04 - Projection Manifest And One-Way Repository Bridge

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `20 focused projection-contract tests; 132 integrated Registry,
  Project-Version, USI, CBM mapping and roadmap/master tests`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T184228+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T18:42:28+02:00`
  - lease_expires_at: `2026-07-18T22:42:28+02:00`
  - released_at: `2026-07-18T18:54:56+02:00`
  - allowed_paths: `src/codebase_memory_projection.py`,
    `tests/test_codebase_memory_projection.py`, this roadmap and the Open-Work
    master
  - preserved_foreign_hunks: Repo Registry, Project Versioning, USI stores/jobs,
    engine/runtime, productive sources, TAX/TUA, hook, config, host and live
    system
  - live_actions: `false`
  - evidence: read-only RepoRegistry lookup plus canonical Project Version and
    CBM-02 file mappings drive one content-free engine config; every mapped
    source-version requires exact USI evidence; owner/repo/commit/snapshot/
    config/input authority digests and project/generation/plan IDs recompute;
    all watcher/index/UI/update/network/source-write/config-write/hook/export/
    semantic flags false; deterministic USI CODE_GRAPH ProjectionManifest;
    in-memory generation PREPARED/ACTIVE/STALE/FAILED states with transactional
    compare-and-switch; build failure preserves active generation; delete and
    identical rebuild reproduce the same identity with canonical_writes=0;
    explicit reverse engine registration rejects; module contains no filesystem,
    process, socket/network or canonical-store mutation path
  - artifact_hashes: projection
    `B0EEA30B667E3D2D6ECCD16F77248C444180CC4A4DEA020EEBCCBEA88E6357DC`,
    tests
    `38A0247E75C3CE8695D8766B9FCB94A30895FA594C1A490713499ACC4F86E842`
  - next_frontier: `CBM-05` structural query provider
- Dependencies: USI-04 jobs, `CBM-02`, `CBM-03`
- Allowed paths:
  - `src/codebase_memory_projection.py`
  - `tests/test_codebase_memory_projection.py`
- Work:
  - Repo Registry and USI snapshot drive engine project/config input;
  - projection manifest records engine/config/input generation;
  - engine project registration cannot mutate canonical registry;
  - transactional generation switch and stale/failed states;
  - projection database can be deleted and rebuilt independently.
- Tests: `python -m pytest -q tests/test_codebase_memory_projection.py`
- Done when: engine state is a one-way derivative and cannot create a new
  canonical repo/source/version.

### CBM-05 - Structural Query Provider

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `45 focused query-contract tests; 148 integrated USI, CBM mapping,
  projection, query and roadmap/master tests`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T185646+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T18:56:46+02:00`
  - lease_expires_at: `2026-07-18T22:56:46+02:00`
  - released_at: `2026-07-18T19:09:32+02:00`
  - allowed_paths: `src/codebase_memory_query.py`,
    `tests/test_codebase_memory_query.py`, this roadmap and the Open-Work master
  - preserved_foreign_hunks: engine process/projection state, USI planner,
    public tools/TAX, productive source content, config, host and live system
  - runtime_scope: injected fake transport and typed CBM-02/04 fixtures only
  - live_actions: `false`
  - evidence: nine typed bounded operations; strict unknown-field and raw-query
    rejection; deterministic request IDs and opaque cursors; input, result,
    node, edge, depth and time budgets; exact immutable CBM-02 to USI file,
    symbol and relation mapping tied to one CBM-04 generation; operation-specific
    edge validation; explicit confidence and unresolved-edge receipts; catalog,
    scope, generation, stats and pagination fail-closed checks; stale/unavailable
    provider returns usable fallback metadata; no process, filesystem, network,
    config, hook, source-content or productive projection action
  - artifact_hashes: query
    `12E3FB1D3CAB8C6FA6F9C4D518DEA51DB5F7D5C1D57BDABDD73AD93AE6F5A7DC`,
    tests
    `63D83EAC0570D4A75A0FD44B96C7B4C993ACC61508DF1C2EB0DF25BD59DC208F`
  - next_frontier: `CBM-06` hybrid USI query planner integration
- Dependencies: `CBM-03`, `CBM-04`
- Allowed paths:
  - `src/codebase_memory_query.py`
  - `tests/test_codebase_memory_query.py`
- Work:
  - bounded symbol, caller, callee, import, inheritance, route, dataflow,
    community and impact operations;
  - typed query/result contract with pagination and budgets;
  - confidence and unresolved edges always visible;
  - output mapped to exact USI refs;
  - arbitrary engine query language remains internal/admin-only.
- Tests: `python -m pytest -q tests/test_codebase_memory_query.py`
- Done when: all supported operations return bounded deterministic fixtures
  and reject unknown/unbounded requests.

### CBM-06 - Hybrid USI Query Planner Integration

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `36 focused hybrid-retrieval tests; 198 integrated USI planner,
  CBM mapping/projection/query/retrieval and roadmap/master tests`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T191224+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T19:12:24+02:00`
  - lease_expires_at: `2026-07-18T23:12:24+02:00`
  - released_at: `2026-07-18T19:24:38+02:00`
  - allowed_paths: `src/code_intelligence_retrieval.py`,
    `tests/test_code_intelligence_retrieval.py`, this roadmap and the Open-Work
    master
  - preserved_foreign_hunks: USI query planner and provider registration,
    CBM process/projection/query modules, TAX/public tools, source content,
    configuration, host and live system
  - runtime_scope: injected USI provider fixtures and CBM-05 result fixtures
    only; no provider is registered into USI in this claim
  - live_actions: `false`
  - evidence: explicit bounded intent contract selects all nine CBM structural
    operations first with lexical/exact fallback; dynamic/exhaustive questions
    remain lexical-first and vocabulary mismatch alone may request an optional
    semantic lane; query-bound CBM adapter maps nodes and resolved/unresolved
    edge endpoints back to exact USI entity evidence; USI planner preserves
    policy/source filtering, bounded fusion, per-provider score contribution,
    stale/partial/missing/failed outcomes and clean lexical fallback; result
    recommends canonical `read_file` only when an exact follow-up is useful;
    duplicate/foreign registrations and ancestry/locator conflicts fail closed;
    no USI hotfile, public tool, filesystem, process, network, source-content,
    config, hook or live-system mutation
  - artifact_hashes: retrieval
    `E619AB6726F9D7CEBE6E0C6F9C113A52F4F72ADB74F601B69D39A1318F346166`,
    tests
    `3FDC3729631E3EA1B74FA5E8F0FE83A10D38C507FC8A0F2F06E5664DAF686A11`
  - handoff_pending: USI provider registration remains separately unclaimed
  - next_frontier: `CBM-07` incremental sync correctness and performance
- Dependencies: USI-07 planner, `CBM-05`
- Allowed paths:
  - `src/code_intelligence_retrieval.py`
  - `tests/test_code_intelligence_retrieval.py`
  - USI query provider registration only after USI handoff
- Work:
  - structural-first plans for relation/impact questions;
  - lexical/exact fallback for macros, dynamic code and exhaustive search;
  - optional semantic provider for vocabulary mismatch;
  - evidence-preserving fusion and score explanation;
  - recommend exact read as second call when source context is required.
- Tests: `python -m pytest -q tests/test_code_intelligence_retrieval.py`
- Done when: fixture questions select the correct provider mix and engine
  failure returns usable lexical/exact fallback.

### CBM-07 - Incremental Sync Correctness And Performance

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `26 focused incremental-sync tests; 174 integrated USI lineage,
  CBM mapping/projection/query/sync and roadmap/master tests`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T192900+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T19:29:00+02:00`
  - lease_expires_at: `2026-07-18T23:29:00+02:00`
  - released_at: `2026-07-18T19:39:17+02:00`
  - allowed_paths: `src/codebase_memory_sync.py`,
    `tests/test_codebase_memory_sync.py`, this roadmap and the Open-Work master
  - preserved_foreign_hunks: Repo/Git/USI stores and jobs, CBM process,
    projection and query modules, productive sources, watchers, config, host
    and live system
  - runtime_scope: typed in-memory file mappings, USI version/lineage evidence
    and synthetic bounded change sets only
  - lineage_dependency: existing USI `LineageRecord` and `RENAMED`/`MOVED`
    reasons are available; CLT history inference/backfill remains out of scope
  - live_actions: `false`
  - evidence: canonical bounded ADD/MODIFY/DELETE/RENAME changes and change-set
    identities; exact old/new source-version evidence; rename/move requires USI
    chunk `LineageRecord` ancestry; persistent path index updates one delta and
    digest without materializing the full projection; active and working state
    remain separate through interruption and atomically switch only after the
    contiguous ordered prefix completes; arbitrary input order is canonicalized;
    resume verifies the applied prefix; replay of an accepted change set is
    zero-work idempotent; delete and rename retain historical source-version and
    lineage evidence; 300-file fixture updates one file with examined_file_count=1,
    full_rebuild=false, canonical_writes=0 and watcher_events=0; no source crawl,
    watcher, filesystem, process, network, config, hook or live action
  - artifact_hashes: sync
    `5E7A90B28311F9280607A11C4638F5A49F38346E18DDFE769868C38B43724B81`,
    tests
    `9CAC2BFF626E56BAEC4D54425B2DDC2DCF2C29C48C709D4EBF0DF697524C82E4`
  - next_frontier: `CBM-08` bounded graph projection API
- Dependencies: `CBM-04`, `CBM-05`; lineage contract available for rename refs
- Allowed paths:
  - `src/codebase_memory_sync.py`
  - `tests/test_codebase_memory_sync.py`
- Work:
  - consume bounded Repo/Git/USI change sets instead of independent global
    watchers;
  - add/change/delete/rename generation updates;
  - preserve old source-version evidence while current projection advances;
  - detect missed events and schedule bounded reconcile;
  - no permanent dual watcher authority;
  - benchmark no-op, one-file edit/add/delete/rename and bounded multi-file
    changes with dispatch-to-queryable p50/p95, touched files/records, CPU, peak
    RAM and database growth;
  - fail the incremental gate when normal change handling silently falls back to
    a full-repository scan or rebuild; explicit missed-event reconcile remains a
    separately labelled recovery path.
- Tests: `python -m pytest -q tests/test_codebase_memory_sync.py`
- Done when: repeated, interrupted and out-of-order fixture updates converge to
  the same projection hash, and measured work stays proportional to the bounded
  change set without a hidden full rebuild.

### CBM-08 - Bounded Graph Projection API

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Acceptance: `31 focused bounded-graph tests; 166 integrated Progressive Graph,
  CBM mapping/projection/query/graph and roadmap/master tests`
- Released claim:
  - run_id: `post-mvp-cbm-20260718T194159+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T19:41:59+02:00`
  - lease_expires_at: `2026-07-18T23:41:59+02:00`
  - released_at: `2026-07-18T19:53:14+02:00`
  - allowed_paths: `src/codebase_memory_graph_projection.py`,
    `tests/test_codebase_memory_graph_projection.py`, this roadmap and the
    Open-Work master
  - preserved_foreign_hunks: Progressive Graph API base contract, CBM process,
    projection/query/sync, Lens UI, productive graph/source data, config, host
    and live system
  - runtime_scope: typed CBM-02/05 mappings, injected bounded graph stores and
    synthetic aggregate metadata only
  - graph_dependency: existing `src/progressive_graph_api.py` contract and its
    five query kinds are available; Lens production UI remains separately owned
  - live_actions: `false`
  - evidence: hard limits for visible nodes/edges, depth, path hops, time,
    payload bytes and continuation offsets; overview, neighborhood, path,
    community and query-subgraph contracts; deterministic cursor fingerprint;
    small catalog store preserves complete local neighborhoods and uses bounded
    traversal/examination work; persistent visual node/edge/aggregate IDs hash
    canonical USI entity/relation/community identity and never engine refs;
    every visible node and edge carries exact CBM-02 fallback, source/version,
    locator, method and confidence evidence; payload and aggregate overflow are
    explicitly clipped with next action; injected million-node/eight-million-edge
    metadata returns two aggregates and zero detailed nodes/edges; malicious
    over-budget, duplicate, dangling, aggregate/detail and time/work responses
    fail closed; no Lens/UI, filesystem, process, network, config, hook or live
    action
  - artifact_hashes: graph projection
    `B5114191E5BCB032303B7409345B03B642B4F61F8803DDA4880D1B610112F08E`,
    tests
    `5748F1FE90E4235C3E8528AF260FDAC97D50BC0C440A46CA1F563D809D836615`
  - next_frontier: `CBM-09` is dependency-blocked by USI-09 and
    TAX1/TAX5/TAX8/TAX10; next independent post-MVP frontier is `CLT-00`
- Dependencies: `CBM-05`; Progressive Graph API contract
- Allowed paths:
  - `src/codebase_memory_graph_projection.py`
  - `tests/test_codebase_memory_graph_projection.py`
- Work:
  - overview aggregates, communities, neighborhood, path and query subgraph;
  - cursor/max-node/max-edge/depth/time budgets;
  - deterministic stable visual refs independent of raw engine IDs;
  - aggregate/LOD responses for large repositories;
  - no unbounded whole-graph payload requirement.
- Tests: `python -m pytest -q tests/test_codebase_memory_graph_projection.py`
- Done when: million-node synthetic metadata remains bounded and small graphs
  preserve complete local neighborhoods.

### CBM-09 - Canonical Tool And Agent Routing

- Class: `repo_only`
- Owner: Bob
- Status: `blocked_dependencies_2026-07-18`
- Dependency audit: `CBM-06=accepted; USI-09=pending TAX1/TAX5/TAX8;
  TAX1/TAX5/TAX8/TAX10 and serialized TAX handoff are not accepted, so neither
  the provider handler nor any shared tool/catalog hotfile may be claimed`
- Next action: resume only after the TAX/USI roadmap owners record all four
  accepted dependencies and an explicit handoff; do not bypass into CBM-10,
  whose dependency is `CBM-03` through `CBM-09`
- Dependencies: `CBM-06`, USI-09, TAX1/TAX5/TAX8/TAX10
- Allowed paths:
  - `src/code_intelligence_tool_provider.py`
  - `tests/test_code_intelligence_tool_routing.py`
- Work:
  - CBM operations remain provider modes under `query_knowledge`;
  - do not register the 14 upstream tools as public Odysseus tools;
  - stable analytics identity and alias policy;
  - tool descriptions teach one-call search plus optional exact read;
  - internal provider failures remain content-free diagnostics.
- Integration rule: this slice produces the provider handler consumed by
  USI-09. It does not edit TAX catalog/schema/index/security files itself.
- Tests: `python -m pytest -q tests/test_code_intelligence_tool_routing.py`
- Done when: tool catalog parity sees one canonical query identity and agents
  can issue structural queries without knowing CBM internals.

### CBM-10 - Security, Sandbox And Adversarial Protocol Matrix

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `CBM-03` through `CBM-09`
- Allowed paths:
  - `tests/test_codebase_memory_security.py`
  - `tests/test_codebase_memory_protocol_adversarial.py`
  - `docs/plans/codebase-memory-security-acceptance.md`
- Work:
  - path traversal, symlink, malformed JSON-RPC, oversized output and timeout;
  - network egress and loopback binding checks;
  - no config/hook/instruction modifications;
  - source classification and owner scope enforcement;
  - executable tamper/version mismatch and projection poisoning;
  - fallback remains read-only and policy-equivalent.
- Tests:
  - `python -m pytest -q tests/test_codebase_memory_security.py tests/test_codebase_memory_protocol_adversarial.py`
- Done when: a compromised or broken engine cannot write repository files,
  alter agent configuration or bypass source policy.

### CBM-11 - Real Engine Quality And Scale Run

- Class: `repo_only`; use only a verified, pinned local artifact or a
  repository-approved temporary build. A missing artifact blocks this slice
  until normal dependency installation is available, but does not create a
  second product activation gate.
- Owner: Charlie
- Dependencies: `CBM-00` through `CBM-10`
- Allowed paths:
  - temporary benchmark output outside committed source
  - `docs/plans/codebase-memory-evaluation-result.md` with aggregate evidence
- Work:
  - index Odysseus at a fixed commit;
  - run the three-way evaluation and labelled edge sample;
  - measure an empty-projection cold start as process boot, extraction/indexing
    and first-query-ready phases;
  - measure warm process/reopen and first-query latency without reindexing;
  - repeat no-op, edit, add, delete, rename and bounded multi-file incremental
    runs and report p50/p95 dispatch-to-queryable latency;
  - record wall time, CPU, peak RAM, database size/growth, scanned/touched work
    and p50/p95 query latency so hidden full rebuilds are detectable;
  - run on the development workstation and a declared representative homeserver
    profile; unavailable hardware is reported as deferred evidence, not assumed;
  - test Python plus JavaScript/TypeScript and malformed fixtures;
  - report failures, not only averages.
- Tests: benchmark protocol plus focused adapter suites
- Done when: aggregate evidence includes per-scenario baselines and regression
  budgets, supports Go/Partial/No-Go and contains no raw private source or host
  path.

### CBM-12 - Packaging, Upgrade And Projection Rebuild

- Class: `repo_only`
- Owner: Bob
- Dependencies: accepted `CBM-11`
- Allowed paths:
  - `src/codebase_memory_install_plan.py`
  - `tests/test_codebase_memory_install_plan.py`
  - `docs/plans/codebase-memory-operator-runbook.md`
- Work:
  - plan-only install/upgrade/remove commands and checksums;
  - current/next projection generations and rollback;
  - rebuild on incompatible engine schema;
  - Windows and Debian path/config contracts;
  - no package download or host mutation in tests.
- Tests: `python -m pytest -q tests/test_codebase_memory_install_plan.py`
- Done when: upgrade failure returns to prior query behavior without changing
  USI identity or repository data.

### CBM-13 - Synthetic Staging And Activation Packet

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `CBM-00` through `CBM-12`; UIR provider binding and ULO
  projection lifecycle acceptance for the selected repositories
- Allowed paths:
  - `docs/plans/codebase-memory-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - exact version, checksum, feature flags, process/data paths and scopes;
  - initial bounded repository, observation window and rollback;
  - declare semantic/LSP/multi-repo features individually as Go/Partial/Deferred;
  - coordinate GRO metrics and Lens Code Graph readiness;
  - prove UIR generation/fallback binding and ULO purge/rebuild behavior for
    the selected repository scope;
  - materialize one live gate only after packet validation.
- Tests: all CBM focused suites plus master JSON validation
- Done when: productive activation requires no hidden installer, hook, config or
  second tool-registry step.

### CBM-LIVE-ACTIVATION - Single User Gate

- Class: `needs_live_go`
- Status: `dormant`
- Blocks: persistent productive CBM process, real repository projection and
  automatic incremental sync
- Decision needed: pinned artifact, environment, repositories, process/data
  paths, security profile, observation window and rollback
- Go phrase:
  `GO CBM-LIVE-ACTIVATION: activate pinned CBM <version/sha> as a rebuildable code projection for <repos> in <environment>; keep hooks/config writes/network disabled; observe <window>; rollback via <plan> on No-Go.`

## 7. Quality Gates

Required Go evidence:

- hybrid quality is not lower than grep/read baseline on the labelled set;
- structural questions materially reduce median calls/tokens or latency;
- exact/exhaustive questions correctly fall back instead of returning false
  completeness;
- call-edge precision and recall are reported per sampled language;
- all returned nodes and edges carry exact USI locators and confidence/method;
- unchanged input produces a deterministic projection manifest;
- small edits produce bounded incremental updates;
- cold start and warm reopen have repeatable wall-time, CPU, peak-RAM, database
  and first-query-ready evidence on declared hardware;
- no-op, edit, add, delete, rename and bounded multi-file updates report p50/p95
  latency and touched work without a hidden full-repository scan/rebuild;
- activation fixes measured performance budgets; regressions beyond those
  budgets block Go instead of being accepted from headline upstream claims;
- engine crash, absence or stale projection leaves exact search usable;
- no public duplicate tools, project registry, ADR store or hooks exist;
- projection deletion/rebuild changes no source/version/lineage truth.

No single headline such as `10x`, `120x`, `1 ms` or language count is an
acceptance criterion. Odysseus-local evidence decides.

## 8. Parallelism And Hotfiles

- `CBM-00` and `CBM-01` may run in parallel only after lock ownership is clear.
- `CBM-02` is the barrier before process/query/projection work.
- `CBM-03` and fixture-only `CBM-01` can proceed on disjoint paths.
- `CBM-05` and `CBM-07` serialize engine query/projection fixtures.
- TAX owns tool catalog/index/security hotfiles until handoff.
- USI owns its query planner and source store; CBM contributes one provider.
- GRO owns observability registry/exporter files.
- Lens Code Graph owns all production UI; upstream engine UI is reference-only.
- Project Versioning owns repository and commit/version stores.

## 9. Verification Bundle

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q `
  tests\test_codebase_memory_vendor_lock.py `
  tests\test_benchmark_code_intelligence.py `
  tests\test_code_intelligence_contract.py `
  tests\test_codebase_memory_process.py `
  tests\test_codebase_memory_client.py `
  tests\test_codebase_memory_projection.py `
  tests\test_codebase_memory_query.py `
  tests\test_code_intelligence_retrieval.py `
  tests\test_codebase_memory_sync.py `
  tests\test_codebase_memory_graph_projection.py `
  tests\test_codebase_memory_security.py
```

## 10. Go Language

- `Go`: pinned engine, adapter, locator mapping, hybrid retrieval, security,
  incremental updates, scale and rollback meet all required gates.
- `Partial`: structural engine is useful but semantic, multi-repo or LSP
  subsets remain disabled with explicit fallbacks.
- `No-Go`: engine cannot be isolated, locators/edges are unreliable, upgrades
  require hidden config mutation, or hybrid quality regresses.
- `Deferred`: optional language, SCIP enrichment or semantic lane awaits its own
  evidence/dependency; Lens UI delivery is tracked separately.
- `Blocked`: required artifact, license/build provenance or safe local execution
  cannot be established.

## 11. Definition Of Done

- CBM is a replaceable, rebuildable code graph engine under Odysseus control.
- USI remains source, version, chunk, policy and provenance truth.
- Git/Project Versioning remains history truth.
- RAPTOR remains hierarchical summary/cluster truth for derived knowledge.
- One canonical knowledge query plus exact reader covers the agent workflow.
- CBM exposes bounded symbol, import and call projections for Lens without
  creating another app shell, renderer dependency or unbounded API requirement.
- Cold-start, warm-reopen and incremental performance gates pass against fixed,
  reproducible budgets on declared hardware.
- Missing/stale/broken CBM degrades to existing exact tools.
- CBM process generations are bound through UIR and can be purged/rebuilt
  through ULO without changing repository or USI truth.
- Productive use has one explicit activation gate and tested rollback.
