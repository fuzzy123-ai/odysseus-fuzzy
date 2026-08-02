# Memory RaptorGraph Consolidation Roadmap

Status: `native_successor_in_progress / NMG-00 accepted / MEM1-MEM6 historical evidence / Obsidian legacy excluded`

ABC mode: Standard ABC

## Superseding Native-System Amendment 2026-08-01

The original Obsidian/ORCA consolidation direction is superseded. Obsidian is
not a current product architecture, source provider, graph owner, query path or
activation target. The dated MEM1-MEM6 results below remain historical evidence
for lifecycle, provenance, diagnostics and privacy contracts; their plugin
paths and compatibility language are not current execution authority.

The current system builds on native domain truth plus USI. Repository code still
contains legacy runtime couplings, so removal is an explicit verified migration,
not an assumed completed fact. New work uses the `NMG` queue below and may not
add dependencies on `plugins/obsidian/**`.

## Current Native Goal

Build one first-party Knowledge/Memory system on native domain truth and USI:
versioned sources, bounded retrieval, rebuildable graph/RAPTOR projections,
reviewable causal claims, native runtime/query integration and verified legacy
retirement.

## Dual-Lane Model Amendment 2026-08-02

Native RAPTOR trennt die semantische und die generative Modellrolle:

```text
immutable source -> extraction + normalization -> exact token preflight
  -> immutable source chunks
  -> semantic lane: retrieval / clustering / similarity / dedupe candidates
  -> generative lane: evidence summaries / cluster labels / entity-edge JSON
  -> schema + provenance + review gates
  -> rebuildable derived artifacts, never source replacement
```

Der erste Offline-Vergleichskandidat fuer die semantische Lane ist
`embeddinggemma:300m-qat-q4_0`. Das bestehende `all-MiniLM-L6-v2` bleibt
Baseline; `paraphrase-multilingual-MiniLM-L12-v2` dient als kleine
mehrsprachige Vergleichsbasis und `BAAI/bge-m3` optional als Quality Ceiling.
Das exakte `gemma3:4b`-Maintenance-Profil bleibt zunaechst die generative Lane.
Keines dieser Modelle ist durch diese Roadmap produktiv ausgewaehlt oder fuer
Download, Re-Embedding, Fine-Tuning oder Live-Aktivierung freigegeben.

NMG-04 konsumiert den abgenommenen RMT8/RMT9-Preflight-Vertrag. Er zaehlt den
normalisierten Inhalt mit einem revisions- und digest-gepinnten lokalen
Tokenizer vor dem Chunking, getrennt von task-spezifischem Prompt-/Special-
Token-Overhead. Projekte werden als Summe einzeln gezaehlter Dateien berichtet;
Rohinhalt und Klartextpfade duerfen den inhaltsfreien Report nicht verlassen.
Fehlende Assets oder Sicherheitsgrenzen blockieren den exakten Count, statt
eine Schaetzung als exakt auszugeben.

NMG-04 muss die Auswahl anhand eines eingefrorenen, source-/zeitgetrennten
Corpus entscheiden. Pflichtmetriken sind `Recall@4`, `nDCG@10`, Duplicate-F1,
Negations-/Zeit-/Entity-Hard-Negatives, Clusterqualitaet, Summary-Faithfulness,
Entity-/Edge-Praezision, JSON-Schema-Passrate, CPU-p95, Peak-RAM,
Vektorspeicher sowie Rebuild-/Rollback-Dauer. Fine-Tuning ist ein nachgelagerter
Schritt und nur bei belegter Baseline-Luecke zulaessig: contrastive
`anchor/positive/hard-negative`-Daten fuer die semantische Lane, separates
QLoRA/SFT fuer generatives Schema- und Evidence-Verhalten, jeweils mit
unangetastetem Holdout und ohne neue Truth-Write-Autoritaet.

## Historical Goal

Make Memory, RAG, RaptorGraph, ORCA/Lens and Universal Inbox writes follow one
canonical source-to-memory lifecycle: source metadata, extraction abstraction,
review, memory write intent, provenance, graph event, diagnostics and rebuild.

## Historical Evidence

- Memory surfaces exist in `src/memory_store_interfaces.py`,
  `src/memory_provenance_ledger.py`, `src/memory_write_policy.py`,
  `src/memory_provider.py`, `src/memory_diagnostics.py` and memory perf suite
  modules.
- RAG surfaces exist in `src/rag_text_chunking.py`, `src/rag_chunk_quality.py`,
  `src/rag_reindex_dry_run.py`, `src/rag_vector.py` and related tests.
- Raptor/graph surfaces exist in `plugins/obsidian/backend/raptor_cache.py`,
  `plugins/obsidian/backend/raptor_rebuild.py`,
  `src/raptorgraph_candidate_mapping.py`,
  `src/progressive_graph_api.py` and `src/large_graph_budget_proof.py`.
- Universal Inbox already has Memory Write Intent and RaptorGraph provenance
  modules.
- MEM1 now provides `docs/plans/memory-lifecycle-contract.md`, defining the
  canonical source metadata -> extracted abstraction -> policy review ->
  memory write intent -> memory record -> provenance event -> graph event ->
  diagnostics budget -> rebuild dry-run lifecycle.
- MEM2 now provides `src/memory_lifecycle.py`, a canonical redacted lifecycle
  state model and validator for already-produced Memory/RAG/RaptorGraph dry-run
  payloads, with tests in `tests/test_memory_lifecycle.py`.
- MEM3 now provides `src/memory_lifecycle_adapters.py`, additive adapters from
  Universal Inbox write intent, RAG reindex dry-run plans, manual memory
  candidates and RaptorGraph candidate mappings into the canonical lifecycle.
- MEM4 now provides `src/memory_provenance_alignment.py`, a read-only alignment
  plan for source hashes, RAG chunk refs, memory record IDs, provenance records
  and RaptorGraph event IDs.
- MEM5 now provides `src/memory_diagnostics_consolidation.py`, a read-only
  diagnostics summary that consolidates lifecycle, provenance alignment, store
  budgets and rebuild gates into existing diagnostic metric snapshots.
- MEM6 now provides `docs/plans/memory-legacy-naming-migration-map.md`, mapping
  Obsidian, RAPTOR, RAG, ORCA/Lens and Universal Inbox terms to the canonical
  lifecycle vocabulary while preserving compatibility surfaces.
- Current rework need: old Obsidian-compatible surfaces, ORCA naming,
  Universal Inbox writes and general Memory/RAG paths should converge.

## Mode

Standard ABC. Repo-only until live reindex, migration or real corpus rebuild
gets a bounded operator Go.

## Non-goals

- Do not live-reindex production collections.
- Do not migrate storage backend in this roadmap without a separate migration
  plan.
- Do not remove legacy ORCA/Obsidian compatibility in the first pass.
- Do not persist raw source text in graph, memory, ledger or evidence.
- Do not replace canonical source chunks with paraphrases, summaries, labels or
  embeddings; all model outputs remain derived and rebuildable.
- Do not mix embedding models or dimensions inside one collection generation.
- Do not call a model/provider, download tokenizer assets at runtime, silently
  truncate input or label an estimated token count as exact.

## What Must Be Done

- Define canonical memory lifecycle states.
- Make Memory Write Intent the only durable write entry for derived memories.
- Add adapters for ORCA/Lens, Universal Inbox, RAG import and manual memory
  flows.
- Align chunking, provenance and graph event IDs.
- Define rebuild/reindex dry-run envelope with rollback metadata.
- Consolidate memory diagnostics around source, extraction, intent, write,
  graph and query budget stages.
- Create deprecation map for legacy Obsidian-specific terms and paths.

## Historical Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| MEM1 lifecycle inventory | safe_offline | Alice | roadmap and memory lifecycle doc | Done: `docs/plans/memory-lifecycle-contract.md` |
| MEM2 canonical lifecycle model | repo_only | Bob | `src/memory_lifecycle.py`, tests | Done: `tests/test_memory_lifecycle.py` |
| MEM3 write-intent adapters | repo_only | Bob | memory/universal inbox adapter modules | Done: `tests/test_memory_lifecycle_adapters.py` |
| MEM4 provenance alignment | repo_only | Bob | provenance/chunk/graph modules | Done: `tests/test_memory_provenance_alignment.py` |
| MEM5 diagnostics consolidation | repo_only | Bob | diagnostics modules/routes | Done: `tests/test_memory_diagnostics_consolidation.py` |
| MEM6 legacy naming migration map | safe_offline | Alice | docs/plans | Done: `docs/plans/memory-legacy-naming-migration-map.md` |
| MEM7 live reindex packet | needs_live_go | Charlie | docs only until approved | no live run |

`MEM7` and the old live-reindex/removal gates are archived for the superseded
plugin direction and are not claimable by the current system.

## Native Memory/Graph Queue

| Slice | Class | Dependencies | Outcome |
| --- | --- | --- | --- |
| `NMG-00-legacy-boundary-audit` | safe_offline | explicit goal, `REPO-CLEANUP-ONE-WORKTREE`, `CAO-MASTER-ROUTE-VERIFICATION` | Inventory every remaining core/app/plugin coupling and freeze `retire/excluded` decisions without deleting code. The three-file claim remains unclaimable until both gates pass. |
| `NMG-01-native-knowledge-contract` | repo_only | `NMG-00-legacy-boundary-audit`, USI-01, USI-02 | Freeze the Personal Docs/Knowledge boundary and cutover rule: Personal Docs remains current truth until an explicitly selected migration; Knowledge must not create parallel truth or dual writes. |
| `NMG-02-native-knowledge-store` | repo_only | `NMG-01-native-knowledge-contract` | Implement the native domain store and exact reader with owner, policy, version, tombstone and export contracts. |
| `NMG-03-native-adapter-acceptance` | repo_only | `NMG-02-native-knowledge-store`, UDA-05 | Accept the single UDA-05 adapter against exact-read, version, deletion and no-fallback fixtures; NMG does not implement a second adapter. |
| `NMG-04-native-RAPTOR-worker-contract` | safe_offline | `NMG-03-native-adapter-acceptance`, USI-10, accepted RMT8 contract | Define separate provider-neutral semantic and generative lanes, immutable-source/derived-artifact ownership, consumption of the exact pre-chunk token report, model/algorithm injection, frozen-corpus comparison, bounded execution, migration-safe collection generations and failure isolation over immutable USI snapshots. |
| `NMG-04A-native-RAPTOR-worker` | repo_only | `NMG-04-native-RAPTOR-worker-contract`, accepted RMT9 preflight | Implement deterministic/injected clustering and summarization workers, the dual-lane offline comparison harness with separate source-token and processed-token accounting, and a rebuildable derived-artifact store without source writes; fine-tuning remains gated behind measured baseline gaps. |
| `NMG-04B-native-RAPTOR-runtime-projection` | repo_only | `NMG-04A-native-RAPTOR-worker`, USI-10, UIR-03 | Compose native RAPTOR runs only with the accepted semantic/generative pair, project generation-pinned manifests into USI and preserve rollback to the prior embedding generation; the existing adapter alone is not treated as a worker. |
| `NMG-05-causal-domain-contract` | safe_offline | `NMG-01-native-knowledge-contract` | Define `CausalClaimStore` as sole causal truth/review owner, with epistemic states, evidence, conflicts, retractions and forbidden automatic inference. |
| `NMG-05A-causal-store-and-review` | repo_only | `NMG-05-causal-domain-contract` | Persist evidence-bound claims and append-only review transitions with exact owner scope and no USI dual writes. |
| `NMG-05B-causal-candidate-generation` | repo_only | `NMG-05A-causal-store-and-review`, selected accepted UDA adapters | Generate bounded candidates for review; correlation, co-occurrence and model suggestions never become `supported` automatically. |
| `NMG-06-native-query-and-lens` | repo_only | `NMG-04B-native-RAPTOR-runtime-projection`, `NMG-05B-causal-candidate-generation`, `USI-C4-causal-query`, UIR-06 | Expose bounded retrieval, graph and causal explanations through the canonical runtime and AI Lens. |
| `NMG-07-legacy-runtime-retirement` | repo_only | `NMG-02-native-knowledge-store` through `NMG-06-native-query-and-lens`, UIR-12 | Remove direct Obsidian imports/routes/providers/jobs after native parity; any retained plugin code is inert archive outside the current product runtime. |
| `NMG-08-closure-packet` | repo_only | `NMG-00-legacy-boundary-audit` through `NMG-07-legacy-runtime-retirement`, UIR-12A, UIR-13, ULO-14 | Produce static, functional, privacy, backup, rollback and performance evidence for the parent USI activation packet. |

Dependency rule: `NMG-07` cannot start from naming alone. Native truth, exact
reads, query/context behavior, state/claim authority and rollback must be green
first. No live data import, reindex, delete or deploy is authorized here.

## Execution Progress

2026-07-06:
- MEM1 lifecycle inventory done as a docs-only safe_offline slice.
  `docs/plans/memory-lifecycle-contract.md` defines canonical lifecycle stages,
  status tokens, required IDs, write-boundary rules, redaction rules and a
  compatibility map for Obsidian Raptor cache, RAG import, Universal Inbox
  abstraction, ORCA/Lens graph mutation and memory diagnostics vocabulary.
- MEM1 verification passed: docs-only scoped `git diff --check`.
- MEM2 canonical lifecycle model done additively.
  `src/memory_lifecycle.py` exposes
  `build_memory_lifecycle_state(...)`, `MemoryLifecycleState`,
  `MemoryLifecycleStage`, `CANONICAL_MEMORY_LIFECYCLE_STAGES` and the
  `odysseus.memory_lifecycle.v1` payload. It normalizes source metadata,
  extracted abstractions, policy review, memory write intent, memory record,
  provenance event, graph event, diagnostics budget and rebuild dry-run stages
  without performing writes, graph mutation, reindex, rebuild or migration.
  Sensitive keys and sensitive string values are stripped or hashed, and
  runtime events record `side_effects=("none",)`.
- MEM2 verification passed:
  `py_compile src/memory_lifecycle.py tests/test_memory_lifecycle.py`;
  focused lifecycle coverage passed with 4 tests; broader Memory/RAG/RaptorGraph
  contract coverage passed with 52 tests and the known SQLAlchemy/cache
  warnings. Scoped `git diff --check` passed.
- MEM3 write-intent adapters done additively.
  `src/memory_lifecycle_adapters.py` now exposes adapters for Universal Inbox
  memory write intent, read-only RAG reindex dry-run plans, manual/web-research
  memory candidates and ORCA/Lens-style RaptorGraph candidate mappings. The
  adapters only translate already-produced payloads into
  `odysseus.memory_lifecycle.v1`; they do not persist memories, mutate graph
  stores, reindex, rebuild or migrate.
- MEM3 verification passed:
  `py_compile src/memory_lifecycle.py src/memory_lifecycle_adapters.py tests/test_memory_lifecycle.py tests/test_memory_lifecycle_adapters.py`;
  focused lifecycle/adapter coverage passed with 8 tests; broader
  Memory/RAG/RaptorGraph contract coverage passed with 63 tests and the known
  SQLAlchemy/cache warnings. Scoped `git diff --check` passed.
- MEM4 provenance alignment done additively.
  `src/memory_provenance_alignment.py` now exposes
  `build_memory_provenance_alignment_plan(...)`, producing the
  `odysseus.memory_provenance_alignment.v1` payload that links one canonical
  `source_hash` to deterministic RAG chunk refs, memory record IDs, a redacted
  provenance record and a deterministic RaptorGraph event ID. It validates
  mismatched source hashes and memory IDs, hashes section paths, rejects raw
  path/secret markers and performs no writes.
- MEM4 verification passed:
  `py_compile src/memory_provenance_alignment.py tests/test_memory_provenance_alignment.py`;
  focused provenance/chunk/graph coverage passed with 29 tests; broader
  Memory/RAG/RaptorGraph contract coverage passed with 66 tests and the known
  SQLAlchemy/cache warnings. Scoped `git diff --check` passed.
- MEM5 diagnostics consolidation done additively.
  `src/memory_diagnostics_consolidation.py` now exposes
  `build_memory_diagnostics_consolidation(...)`, translating existing
  lifecycle state, provenance alignment and store budget summaries into the
  established `DiagnosticSnapshot`/`DiagnosticMetric` contracts plus
  `readiness_by_family` and `readiness_gate` summaries. It performs no live
  checks and rejects raw paths/secret markers.
- MEM5 verification passed:
  `py_compile src/memory_diagnostics_consolidation.py tests/test_memory_diagnostics_consolidation.py`;
  focused diagnostics/lifecycle/alignment coverage passed with 18 tests;
  broader Memory/RAG/RaptorGraph contract coverage passed with 77 tests and the
  known SQLAlchemy/cache warnings. Scoped `git diff --check` passed.
- MEM6 legacy naming migration map done as a docs-only safe_offline slice.
  `docs/plans/memory-legacy-naming-migration-map.md` maps current/legacy
  `obsidian_*`, RAPTOR/RaptorGraph, RAG, ORCA/Lens and Universal Inbox terms to
  canonical Memory lifecycle terms, defines compatibility classes, rename order
  and stop rules, and keeps route/tool/data-path removal behind
  `MEM-LEGACY-REMOVAL-GO`.
- MEM6 verification passed: docs-only scoped `git diff --check`.

2026-08-02:
- NMG-00 legacy-boundary audit accepted at local commit `1487c732` after one
  bounded Root review fix cycle. The committed identifiers-only inventory
  freezes ten canonical full boundary tuples with explicit `retire` or
  `excluded` disposition and no runtime, provider, productive-source, migration,
  deletion or live effect.
- NMG-00 verification passed: 8 focused tests, canonical inventory check,
  static import review, Root deep review and Charlie's independent 186-test
  combined QA lane. NMG-01 is next only after an exact claim registration;
  NMG-02 through NMG-08 remain dependency-blocked.

## Historical Gate Queue (preserved snapshot)

Gate: `MEM-LIVE-REINDEX-GO`
Class: needs_live_go
Blocks: real collection reindex/rebuild/migration
Decision needed: approve bounded reindex target, rollback and evidence path
Safe preparation done: dry-run envelope and adapters
Risk if bypassed: data loss, duplicate memories or stale graph state
Next safe slice: dry-run and fixture tests

Gate: `MEM-LEGACY-REMOVAL-GO`
Class: needs_design
Blocks: removal of Obsidian legacy vocabulary/routes/paths
Decision needed: compatibility period and UI wording
Safe preparation done: ORCA/Lens aliasing and deprecation map
Risk if bypassed: broken plugin compatibility and user confusion
Next safe slice: additive aliases and docs

Superseding disposition: both gates above are dated evidence only and are
nonclaimable. They block nothing in the native queue and authorize no current
action. `NMG-07` owns retirement after native parity; any live migration or
deletion requires a new action-specific gate bound to the native architecture.

## Historical Paths

Alice path:
- define lifecycle and operator wording
- map legacy vocabulary to ORCA/Lens terms

Bob path:
- implement lifecycle model and adapters
- align provenance and diagnostics

Charlie path:
- sequence migrations after tests
- keep live reindex gated

## Verification

- `pytest tests/test_memory_write_policy.py`
- `pytest tests/test_memory_provenance_ledger.py`
- `pytest tests/test_memory_store_interfaces.py`
- `pytest tests/test_universal_inbox_memory_write_intent.py`
- `pytest tests/test_nextcloud_raptorgraph_provenance.py`
- `pytest tests/test_rag_text_chunking.py tests/test_rag_chunk_quality.py`
- `pytest tests/test_progressive_graph_api.py`
- `git diff --check`

## Go Language

- Go: canonical lifecycle and adapters exist, with no raw content persistence.
- Partial: native runtime composition or legacy caller retirement remains open.
- Deferred: live reindex and storage migration require operator Go.
- No-Go: raw source text or private paths enter memory/graph evidence; an
  estimated, truncated or tokenizer-unpinned count is accepted as exact.
