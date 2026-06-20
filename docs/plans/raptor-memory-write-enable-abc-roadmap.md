# RAPTOR Memory Write Enable ABC Roadmap

Stand: 2026-06-20

Status: roadmap-only, implementation not started

## Goal

Enable a safe, gated RAPTOR memory rebuild/write path for the Obsidian plugin, with synthetic scale tests for deprecated/superseded source handling, purge behavior, and large graph-memory performance before live operator use.

## Current Evidence

- `plugins/obsidian/backend/hybrid_retrieval.py` exposes `raptor_status(vault_dir)` and currently reports RAPTOR as read-only.
- RAPTOR metadata paths already exist:
  - `.obsidian/odysseus/raptor/index.json`
  - `.obsidian/odysseus/raptor/summaries.json`
- Current RAPTOR status detects:
  - missing index
  - invalid index/summaries
  - dirty source hash lineage
  - missing source paths
  - tainted sources from Freshness Gate audit
- `plugins/obsidian/tests/test_memory_readiness_layers.py` already covers read-only RAPTOR readiness, dirty/missing lineage, tainted metadata, invalid metadata, and write-gate blocking.
- `plugins/obsidian/backend/derived_index.py` already has a write-enabled rebuild pattern for derived memory JSON artifacts.
- `src/large_graph_budget_proof.py` and `tests/test_large_graph_budget_proof.py` already model large graph budget evidence, but not a live RAPTOR rebuild simulation.
- `src/memory_perf_suite_*` now provides synthetic durability/performance infrastructure for event logs, metrics, performance gates and reports.
- `plugins/obsidian/backend/vault_service.py` has trash purge functions, but large purge stress behavior is not yet covered for RAPTOR/deprecated memory paths.

## Design Decision

RAPTOR writes must become enabled only as **derived-data writes**, never source-note writes.

The first implementation should rebuild compact, reproducible RAPTOR artifacts from current vault sources and existing freshness status. It should not generate LLM summaries, call providers, mutate notes, delete files, move files, write tags, or promote personal memory.

The write gate should move from:

```text
feature flag on/off + rebuild disabled
```

to:

```text
feature flag enabled + rebuild mode enabled + source lineage safe + derived-only write path + budget gate passed
```

## Non-Goals

- No live private-data stress run in the first implementation.
- No LLM/provider summary generation.
- No network, SSH, Nextcloud, WebDAV, Telegram, or external service dependency.
- No source note writes.
- No deletion outside `.trash/` or explicit derived RAPTOR artifact replacement.
- No automatic purge of user files.
- No full graph payload dump to UI, report, log, or test fixtures.
- No Qdrant, Kuzu, Postgres migration, or accelerator runtime in this track.
- No plugin UI expansion until the backend gate is green.

## Safety Boundaries

Allowed writes:

- `.obsidian/odysseus/raptor/index.json`
- `.obsidian/odysseus/raptor/summaries.json`
- optional `.obsidian/odysseus/raptor/rebuild_report.json`
- temp files next to those artifacts, atomically replaced with `os.replace`

Forbidden writes:

- markdown source notes
- `AI Memory/Canonical/`
- `AI Memory/Review Queue/`
- `AI Memory/Quarantine/`
- `.trash/` except dedicated purge tests for existing trash retention
- any path outside the unlocked vault

Durable RAPTOR artifacts may contain:

- source relative paths
- source hashes
- source status class
- graph node ids
- graph edge summaries
- cluster/branch ids
- bounded synthetic or derived labels
- counts, budgets, timestamps, lineage flags

Durable RAPTOR artifacts must not contain:

- full note body
- raw extracted document text
- provider output
- secrets, tokens, passwords, chat IDs
- absolute host paths
- private process details
- unbounded edge/node arrays in reports

## Paths

### Path A: Operator Contract And Gates

Owner: Alice

Goal:

- Define operator-facing gate language for RAPTOR write enablement, deprecated/superseded source behavior, purge boundaries, and large graph-memory performance.

Path completion:

- Roadmap and operator contract document exactly what is safe, what is blocked, and what counts as Go/Partial/No-Go.

### Path B: RAPTOR Derived Write Runtime

Owner: Bob

Goal:

- Implement the backend rebuild/write path for RAPTOR derived artifacts with atomic writes and tests.

Path completion:

- RAPTOR can rebuild compact derived artifacts from synthetic/local vault sources under explicit flags and write scope.

### Path C: Scale, Deprecated, Purge, Performance

Owner: Bob

Goal:

- Add synthetic scale simulation and performance gates for deprecated/superseded source isolation, purge planning, and large graph-memory budgets.

Path completion:

- Large synthetic graph-memory runs can prove bounded outputs, no full dumps, and safe purge/deprecated behavior.

### Path D: Integration, Routes, Tools, Git

Owner: Charlie

Goal:

- Integrate routes/tools, run focused suites, stage only in-scope files, commit and push.

Path completion:

- Backend API/tool path is available with correct auth scopes, tests are green, and the branch is pushed.

## Slices

### RMW-ABC0 Roadmap

Owner: Charlie

Execution mode: worker

Goal:

- Store this roadmap as the durable planning artifact.

Allowed files:

- `docs/plans/raptor-memory-write-enable-abc-roadmap.md`

Tests:

- `git diff --check`

### RMW-ABC1 Operator Contract

Owner: Alice

Execution mode: worker

Goal:

- Create or refine operator wording for RAPTOR write gates, derived-only write promises, deprecated source handling, purge limits, performance budgets and Go/No-Go language.

Allowed files:

- `docs/plans/raptor-memory-write-enable-abc-roadmap.md`
- optional `docs/plans/raptor-memory-write-operator-contract.md`

Tests:

- Docs-only. Run `git diff --check`.

### RMW-ABC2 Write Gate Flags And Status

Owner: Bob

Execution mode: worker

Goal:

- Replace the hardcoded read-only RAPTOR write gate with a real gate that can become ready only when explicit flags and safety conditions are satisfied.

Allowed files:

- `plugins/obsidian/backend/feature_flags.py`
- `plugins/obsidian/backend/hybrid_retrieval.py`
- `plugins/obsidian/tests/test_memory_readiness_layers.py`

Requirements:

- Add explicit flag for RAPTOR rebuild/write enablement, separate from generic `obsidian_raptor_enabled`.
- Keep default blocked.
- `writes_supported` can become true only when both feature and rebuild-write flags are enabled.
- Status must still report gaps when disabled.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-gate plugins\obsidian\tests\test_memory_readiness_layers.py`

### RMW-ABC3 Derived Artifact Builder

Owner: Bob

Execution mode: worker

Goal:

- Implement a compact RAPTOR rebuild function that writes only derived metadata artifacts atomically.

Allowed files:

- `plugins/obsidian/backend/hybrid_retrieval.py`
- optional `plugins/obsidian/backend/raptor_rebuild.py`
- `plugins/obsidian/tests/test_raptor_rebuild_backend.py`

Requirements:

- Build `index.json`, `summaries.json`, and optional `rebuild_report.json`.
- Use source relative paths and hashes.
- Include compact graph/cluster/branch metadata.
- Exclude raw note body and full extracted content.
- Use temp file plus `os.replace`.
- Re-running after a source change must clear dirty status when artifacts are rebuilt from current source hashes.
- Missing sources from old artifacts must disappear after rebuild from current vault.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-rebuild plugins\obsidian\tests\test_raptor_rebuild_backend.py plugins\obsidian\tests\test_memory_readiness_layers.py`

### RMW-ABC4 Route And Tool Exposure

Owner: Charlie

Execution mode: worker

Goal:

- Expose RAPTOR rebuild via authenticated route and optional MCP/tool spec while preserving write-scope gates.

Allowed files:

- `plugins/obsidian/backend/routes.py`
- `plugins/obsidian/backend/tool_specs.py`
- `plugins/obsidian/tests/test_plugin_obsidian.py`
- `plugins/obsidian/tests/test_locked_vault_surfaces.py`
- `plugins/obsidian/tests/test_memory_readiness_layers.py`

Requirements:

- Add `POST /raptor/rebuild` or equivalent route.
- Require `vault:write`.
- Tool spec, if added, must be access `write`.
- Locked vault tests must block the route/tool.
- Read-only status route remains read-only.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-route plugins\obsidian\tests\test_memory_readiness_layers.py plugins\obsidian\tests\test_plugin_obsidian.py plugins\obsidian\tests\test_locked_vault_surfaces.py`

### RMW-ABC5 Deprecated And Superseded Source Semantics

Owner: Bob

Execution mode: worker

Goal:

- Prove deprecated/obsolete/superseded/archived sources are excluded from default retrieval but retained in audit/rebuild lineage when needed.

Allowed files:

- `plugins/obsidian/backend/freshness.py`
- `plugins/obsidian/backend/knowledge_status.py`
- `plugins/obsidian/backend/hybrid_retrieval.py`
- `plugins/obsidian/tests/test_memory_readiness_layers.py`
- `plugins/obsidian/tests/test_raptor_rebuild_backend.py`

Requirements:

- Deprecated aliases must normalize to isolated statuses.
- RAPTOR rebuild must not promote deprecated/superseded sources into ready/default retrieval.
- Rebuild report must count isolated/deprecated sources.
- Status must surface tainted/deprecated lineage compactly.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-deprecated plugins\obsidian\tests\test_memory_readiness_layers.py plugins\obsidian\tests\test_raptor_rebuild_backend.py`

### RMW-ABC6 Purge Safety And Scale

Owner: Bob

Execution mode: worker

Goal:

- Prove purge behavior is bounded, scoped and performant with many obsolete/trash entries.

Allowed files:

- `plugins/obsidian/backend/vault_service.py`
- `plugins/obsidian/tests/test_raptor_purge_scale_backend.py`

Requirements:

- Purge may delete only expired directories under `.trash/`.
- Purge must not follow or delete outside-vault paths.
- Purge report must include counts and errors only, not private paths by default.
- Large synthetic trash layouts must complete within a local budget.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-purge plugins\obsidian\tests\test_raptor_purge_scale_backend.py`

### RMW-ABC7 Large Graph Memory Simulation

Owner: Bob

Execution mode: worker

Goal:

- Simulate large graph-memory workloads without writing full payload dumps.

Allowed files:

- `src/memory_perf_suite_raptor.py`
- `tests/test_memory_perf_suite_raptor.py`
- `src/large_graph_budget_proof.py`
- `tests/test_large_graph_budget_proof.py`
- `docs/plans/raptor-memory-write-enable-abc-roadmap.md`

Requirements:

- Generate synthetic graph-memory datasets with configurable source count, edge count, dirty/missing/deprecated ratios and cluster count.
- Use deterministic seeds.
- Enforce node/edge/page/payload/runtime/memory budgets.
- Prove clipped output and cursor/aggregate behavior.
- No full graph dump in JSON/Markdown reports.

Tests:

- `venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-scale tests\test_memory_perf_suite_raptor.py tests\test_large_graph_budget_proof.py`

### RMW-ABC8 Final Integration

Owner: Charlie

Execution mode: worker

Goal:

- Run the final focused suite, update roadmap status, commit and push.

Allowed files:

- All files touched by RMW-ABC1 through RMW-ABC7.

Tests:

```text
venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-final plugins\obsidian\tests\test_memory_readiness_layers.py plugins\obsidian\tests\test_raptor_rebuild_backend.py plugins\obsidian\tests\test_raptor_purge_scale_backend.py plugins\obsidian\tests\test_plugin_obsidian.py plugins\obsidian\tests\test_locked_vault_surfaces.py tests\test_memory_perf_suite_raptor.py tests\test_large_graph_budget_proof.py
git diff --check
```

## Go / Partial / No-Go

Go:

- RAPTOR rebuild/write is available only behind explicit flags and `vault:write`.
- Rebuild writes only derived RAPTOR artifacts.
- Rebuild uses atomic writes.
- Rebuild clears dirty/missing state for current source lineage.
- Deprecated/superseded/archived sources remain isolated from default retrieval.
- Purge is scoped to expired `.trash/` content only.
- Large graph-memory simulation proves bounded output and performance gates.
- Reports contain no raw content, secrets or absolute host paths.

Partial:

- Backend rebuild works, but tool exposure is deferred.
- Deprecated/source-isolation tests pass, but large graph simulation is deferred.
- Large graph simulation exists, but purge scale remains separate.

No-Go:

- RAPTOR writes source notes.
- RAPTOR write path can run without explicit flag and `vault:write`.
- Rebuild artifacts contain raw note bodies, secrets or absolute host paths.
- Deprecated/superseded sources enter default retrieval.
- Purge can delete outside `.trash/`.
- Large graph query/report emits full graph payload.
- Performance budgets can be exceeded while reporting Go.

Deferred:

- Plugin UI controls for rebuild.
- Provider/LLM-generated RAPTOR summaries.
- Real private vault stress run.
- Accelerator runtime with Qdrant/Kuzu/Postgres.
- CI stress matrix.

## Stop Rules

- Stop if live private vault data is required.
- Stop if network/provider/API calls become necessary.
- Stop if a source-note write is needed for RAPTOR rebuild.
- Stop if a destructive delete outside `.trash/` is proposed.
- Stop if artifacts would persist raw content, secrets, tokens, chat IDs or absolute host paths.
- Stop on unrelated dirty files, staged files outside scope, or hotfile conflict.
- Stop if tests need writes outside test temp directories or unlocked vault fixture roots.
- Stop if write enablement cannot be gated by explicit flags and `vault:write`.

## Agent Prompt Packets

Do not start these packets until the operator approves implementation.

### Alice Prompt

```text
Alice-Slice: RMW-ABC1 Operator Contract

Arbeite im Odysseus-Fork an einem docs-only Slice.

Ziel:
- Refine operator-facing language for RAPTOR write enablement, derived-only writes, deprecated source handling, purge limits, performance budgets and Go/No-Go language.

Erlaubte Dateien:
- docs/plans/raptor-memory-write-enable-abc-roadmap.md
- optional docs/plans/raptor-memory-write-operator-contract.md

Nicht anfassen:
- Code, tests, .env, data/, vault/, reports/.

Tests:
- git diff --check

Stop-Regeln:
- Scope drift, secrets, live data, destructive git, or private content.

Wenn fertig:
- Status, changed files, tests, risks, handoff.
```

### Bob Prompt

```text
Bob-Slice: RMW-ABC2/RMW-ABC3 RAPTOR Write Gate And Rebuild Foundation

Arbeite im Odysseus-Fork an einem scoped backend/test Slice.

Ziel:
- Implement explicit RAPTOR rebuild/write gates and a compact derived-artifact rebuild path.

Erlaubte Dateien:
- plugins/obsidian/backend/feature_flags.py
- plugins/obsidian/backend/hybrid_retrieval.py
- optional plugins/obsidian/backend/raptor_rebuild.py
- plugins/obsidian/tests/test_memory_readiness_layers.py
- plugins/obsidian/tests/test_raptor_rebuild_backend.py

Nicht anfassen:
- Source vault fixtures outside tmp dirs.
- UI files.
- Routes/tool specs; Charlie owns exposure.
- .env, data/, vault/, reports/.

Tests:
- venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp-raptor-rebuild plugins\obsidian\tests\test_raptor_rebuild_backend.py plugins\obsidian\tests\test_memory_readiness_layers.py

Stop-Regeln:
- Source-note writes, raw-content persistence, ungated writes, destructive deletes, unrelated dirty files.

Wenn fertig:
- Status, changed files, tests, risks, handoff.
```

### Charlie Prompt

```text
Charlie-Slice: RMW-ABC4/RMW-ABC8 Integration

Koordiniere die RAPTOR write-enable roadmap after Alice/Bob handoff.

Ziel:
- Expose gated RAPTOR rebuild through route/tool if backend is green, run final tests, stage only scope files, commit and push.

Scope:
- plugins/obsidian/backend/routes.py
- plugins/obsidian/backend/tool_specs.py
- plugins/obsidian/tests/test_plugin_obsidian.py
- plugins/obsidian/tests/test_locked_vault_surfaces.py
- roadmap/test files from RMW.

Stop:
- Any write path lacks vault:write or explicit flags.
- Locked vault route/tool is not blocked.
- Full payload dump or raw content appears.
- Tests fail outside clear scope.
```
