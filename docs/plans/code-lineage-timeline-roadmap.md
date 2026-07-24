# Code Lineage And Timeline Roadmap

Stand: 2026-07-13

Status: `CLT-00_accepted / CLT-01_accepted / CLT-02_accepted / runtime_default_off`

Master-Track: `0.30.x`, `OWM-17`, `L23`

## 1. Goal

Odysseus can explain when a current file, symbol or code chunk first became
observable, how it moved or changed across revisions, which evidence supports
that conclusion and where uncertainty begins.

The core user outcome is a trustworthy answer to requests such as:

- sort the current project code by first observable creation time;
- show which subsystem was introduced first;
- follow a function across rename, move and refactor;
- show the commit that introduced or removed a current chunk;
- distinguish imported old code from code actually created in this repository;
- animate project growth in `Lens > Code` without inventing history.

## 2. Truth And Language Rules

Git and the existing Project Version Store are the only revision authorities.
This track creates evidence-bound USI lineage records and bounded queries; it
does not create commits, a second repository registry or a parallel project
version store.

The phrase `created at` is never emitted without qualification. The canonical
time fields are:

| Field | Meaning |
| --- | --- |
| `first_seen_at` | first observation by Odysseus |
| `history_first_observed_at` | earliest reachable revision containing the lineage |
| `authored_at` | Git author timestamp of the supporting commit |
| `committed_at` | Git committer timestamp of the supporting commit |
| `indexed_at` | time the evidence was indexed |
| `valid_from` / `valid_to` | validity window of one lineage fact |

User-facing default wording is `first observable in available history`.
Absolute creation is unknown when history is shallow, rewritten, imported,
vendored, generated or missing.

## 3. Ownership And No-Duplication Boundary

| Concern | Owner | This roadmap does |
| --- | --- | --- |
| Repo identity/remotes | Repo Registry | reference canonical repo ID only |
| Commits/version manifests | Git Adapter and Project Version Store | read immutable revision evidence |
| Source/chunk/entity identity | USI | attach lineage to USI occurrences |
| Current structural graph | CBM projection | use symbols/locators as matching evidence |
| Semantic similarity | existing embedding lane | optional candidate generation only |
| Timeline visualization | Lens Code Graph | consume bounded timeline payload |
| Project modifications | `commit_project`/Local Forge | no writes or commits |
| Runtime metrics | GRO | emit bounded operation spans after handoff |

## 4. Confidence Model

Lineage never collapses guesses into facts. Every link has a method and
confidence:

| Method | Default interpretation |
| --- | --- |
| `same_blob_same_path` | exact continuation |
| `same_blob_renamed_path` | high-confidence rename/move |
| `git_rename_detection` | Git-supported rename candidate |
| `stable_symbol_signature` | strong symbol continuation when unique |
| `ast_normalized_match` | structural continuation, reviewable |
| `bounded_diff_overlap` | probable modified continuation |
| `copy_candidate` | one-to-many candidate, never silently a rename |
| `semantic_candidate` | discovery hint only, not accepted lineage alone |
| `manual_assertion` | explicit reviewed assertion with evidence |

Ambiguous one-to-many and many-to-one relations are represented explicitly.
A lineage may branch, merge, disappear and reappear. One synthetic stable ID is
not forced through an ambiguous refactor.

## 5. Mode And Gate Policy

Planning uses `Standard ABC`; repository-only fixture work may later run in
`Overnight Backend Mode` after explicit goal start.

- Only `CLT-00` is claimable at start.
- No real repository history scan, author-identity export or persistent
  backfill occurs before the final activation gate.
- Temporary synthetic Git repositories and content-free aggregate reports need
  no user gate.
- Author names/emails are not required for timeline functionality and are
  excluded by default from API, metrics, reports and Lens payloads.
- Exactly one final gate, `CLT-LIVE-ACTIVATION`, selects repositories, revision
  bounds, retention, privacy and rollback.

## 6. Slice Queue

### CLT-00 - Semantics, Limits And Existing Git Capability Audit

- Class: `safe_offline`
- Owner: Charlie
- Status: `accepted_2026-07-18`
- Active claim:
  - run_id: `post-mvp-clt-20260718T195611+0200`
  - owner: `root` acting as Charlie; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T19:56:11+02:00`
  - lease_expires_at: `2026-07-18T23:56:11+02:00`
  - released_at: `2026-07-18T20:13:00+02:00`
  - allowed_paths: this roadmap,
    `docs/plans/code-lineage-capability-audit.md`,
    `scripts/audit_code_lineage_capabilities.py`,
    `tests/test_audit_code_lineage_capabilities.py` and the Open-Work master
  - preserved_foreign_hunks: existing Repo/Git/Project-Version adapters and
    stores, Git state/history, repository content, identities, config, host and
    live system
  - runtime_scope: static Python AST/text inventory and content-free synthetic
    fixture roots only; no Git command is executed by the audit
  - live_actions: `false`
- Dependencies: explicit goal; Project Versioning status loaded
- Allowed paths:
  - `docs/plans/code-lineage-timeline-roadmap.md`
  - `docs/plans/code-lineage-capability-audit.md`
  - `scripts/audit_code_lineage_capabilities.py`
  - `tests/test_audit_code_lineage_capabilities.py`
- Work:
  - inventory current Git log/diff/change-history/ProjectVersion APIs;
  - freeze time, confidence, shallow-history and privacy language;
  - identify direct subprocess duplication and required adapter extensions;
  - define current-code versus historical-code query scopes.
- Tests: `python -m pytest -q tests/test_audit_code_lineage_capabilities.py`
- Done when: no new Git command path is planned where a canonical adapter
  already provides the fact.
- Acceptance evidence:
  - static report: `go_clt_01_contract_only`, digest
    `sha256:09e4d6ee4d7584e8f119ccf67639657cdbf67d1d9b953d77ff87f430f37126db`;
  - inventory: 684 Python source files, 14/14 required canonical APIs,
    10 direct Git-process boundaries and 6 uncatalogued review boundaries;
  - focused tests: 15 passed, 1 non-blocking SQLAlchemy deprecation warning;
  - integrated Git Adapter, Project Version, Local Forge and roadmap tests:
    83 passed, 1 non-blocking SQLAlchemy deprecation warning;
  - executed by the audit: 0 Git commands, 0 subprocesses, 0 live actions;
  - artifacts:
    - `scripts/audit_code_lineage_capabilities.py` SHA-256
      `C4472A1164909AB774CDE15683CB366E27FCCD65A347051BB7D37E05D99FB436`;
    - `tests/test_audit_code_lineage_capabilities.py` SHA-256
      `654AE731B9A900E7EFEE12263BAB88D464F08A29139170A67DCD1EA77C46B2BA`;
    - `docs/plans/code-lineage-capability-audit.md` SHA-256
      `957ACC3989CEF8BFAEF2F622342906B23CC04E96551AE01014408A62A18A95D3`.

### CLT-01 - Timeline And Lineage Record Contract

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Active claim:
  - run_id: `post-mvp-clt-20260718T201301+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T20:13:01+02:00`
  - lease_expires_at: `2026-07-19T00:13:01+02:00`
  - released_at: `2026-07-18T21:23:51+02:00`
  - allowed_paths: this roadmap, `src/code_lineage_contract.py`,
    `tests/test_code_lineage_contract.py` and the Open-Work master
  - preserved_foreign_hunks: Git/Project Version/Repo Registry/USI owners,
    source content, repository history and identity, config, host and live
    system
  - runtime_scope: immutable typed records, deterministic serialization and
    in-memory synthetic fixtures only; no Git or source access
  - live_actions: `false`
- Dependencies: `CLT-00`, USI-01
- Allowed paths:
  - `src/code_lineage_contract.py`
  - `tests/test_code_lineage_contract.py`
- Work:
  - commit evidence refs, file events, lineage links and uncertainty reasons;
  - branch/merge/copy/delete/resurrection support;
  - separate author/committer/index/validity timestamps;
  - canonical ordering and deterministic serialization;
  - no direct person identity in default payload.
- Tests: `python -m pytest -q tests/test_code_lineage_contract.py`
- Done when: ambiguous branch/merge fixtures remain representable without
  false single-parent claims.
- Acceptance evidence:
  - `tests/test_code_lineage_contract.py`: 26 passed, 1 known SQLAlchemy
    deprecation warning.
  - Integrated contract suite:
    `tests/test_code_lineage_contract.py`,
    `tests/test_unified_source_index_contract.py`,
    `tests/test_audit_code_lineage_capabilities.py`: 62 passed, 1 known
    SQLAlchemy deprecation warning.
  - No Git command, source history scan, filesystem, process, network, config
    hook or live action is required or performed by the contract.
  - `src/code_lineage_contract.py` SHA-256
    `F4E5858CA9216FD4A21EBD548B1B77CE6891A4477D832DED7EF88CC066206A97`.
  - `tests/test_code_lineage_contract.py` SHA-256
    `39709C44F2D565B13C1CEE0A4FC7008854C4F845018A57D16FCCAD826E8D4892`.

### CLT-02 - Canonical Git History Read Adapter

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_2026-07-18`
- Active claim:
  - run_id: `post-mvp-clt-20260718T213319+0200`
  - owner: `root` acting as Bob; Sol acceptance
  - state: `released`
  - acquired_at: `2026-07-18T21:33:19+02:00`
  - lease_expires_at: `2026-07-19T01:33:19+02:00`
  - released_at: `2026-07-18T21:38:51+02:00`
  - allowed_paths: this roadmap, `src/code_lineage_git_adapter.py`,
    `tests/test_code_lineage_git_adapter.py` and the Open-Work master
  - preserved_foreign_hunks: existing repo/Git adapter owners, Project
    Version store, USI owners, source content, productive repository history,
    config, host and live system
  - runtime_scope: bounded read-only Git commands against explicit contained
    local repositories and synthetic test repositories only
  - live_actions: `false`
- Dependencies: `CLT-00`, `CLT-01`
- Allowed paths:
  - `src/code_lineage_git_adapter.py`
  - existing repo/Git adapter only after its owner handoff
  - `tests/test_code_lineage_git_adapter.py`
- Work:
  - bounded commit ranges, path changes, blob IDs and parent relations;
  - detect shallow repositories, missing objects and rewritten ranges;
  - use argument arrays, timeouts and repo-root containment;
  - no network fetch and no modification of Git state;
  - normalize rename/copy scores without exposing command output.
- Tests: `python -m pytest -q tests/test_code_lineage_git_adapter.py`
- Done when: synthetic repositories with merge, rename, delete and shallow
  boundaries produce deterministic evidence.
- Acceptance evidence:
  - `tests/test_code_lineage_git_adapter.py`: 7 passed, 1 known SQLAlchemy
    deprecation warning.
  - Integrated CLT contract/adapter suite:
    `tests/test_code_lineage_contract.py`,
    `tests/test_code_lineage_git_adapter.py`: 33 passed, 1 known SQLAlchemy
    deprecation warning.
  - Integrated CLT audit/contract/adapter chain:
    `tests/test_audit_code_lineage_capabilities.py`,
    `tests/test_code_lineage_contract.py`,
    `tests/test_code_lineage_git_adapter.py`: 48 passed, 1 known SQLAlchemy
    deprecation warning.
  - Synthetic local Git repositories covered merge parents, rename, delete,
    missing/rewrite range handling and local shallow-clone boundary detection.
  - Adapter allowlist rejects fetch, reset, unbounded rev-list forms and
    shell-like ref escalation; commands use argument arrays, timeouts and a
    pinned repository cwd.
  - No productive repository mutation, network fetch, config hook or live action
    is required or performed by CLT-02.
  - `src/code_lineage_git_adapter.py` SHA-256
    `F8A8FC3655A7AD5DDD0EB56ADF5051C4657C6E50F02B43B559CCD6C8736F1F58`.
  - `tests/test_code_lineage_git_adapter.py` SHA-256
    `0ACBD97C4DC54E72EF25BCC018C0854E1C68D865C1191C8279C7A9241677D1CC`.

### CLT-03 - File Introduction, Rename And Move Lineage

- Class: `repo_only`
- Owner: Bob
- Dependencies: `CLT-02`, USI-03
- Allowed paths:
  - `src/code_file_lineage.py`
  - `tests/test_code_file_lineage.py`
- Work:
  - exact blob/path continuation and Git rename evidence;
  - earliest reachable introduction for current files;
  - directory moves and case-only rename handling;
  - delete/re-add as separate or linked lineage based on evidence;
  - generated/vendor status carried from source policy.
- Tests: `python -m pytest -q tests/test_code_file_lineage.py`
- Done when: file sort order cites exact commit/blob evidence and reports
  shallow/import uncertainty.

### CLT-04 - Copy And One-To-Many Lineage Candidates

- Class: `repo_only`
- Owner: Bob
- Dependencies: `CLT-03`
- Allowed paths:
  - `src/code_copy_lineage.py`
  - `tests/test_code_copy_lineage.py`
- Work:
  - exact-blob and bounded similarity copy candidates;
  - one source to multiple descendants without deleting source lineage;
  - candidate limits to prevent repository-wide quadratic comparison;
  - confidence/method and unresolved alternatives;
  - semantic similarity cannot accept a copy link alone.
- Tests: `python -m pytest -q tests/test_code_copy_lineage.py`
- Done when: copy, template duplication and coincidental common boilerplate are
  distinguished in labelled fixtures.

### CLT-05 - Symbol And Chunk Continuity Across Revisions

- Class: `repo_only`
- Owner: Bob
- Dependencies: `CLT-01`, `CLT-03`, CBM-02 mapping contract
- Allowed paths:
  - `src/code_symbol_lineage.py`
  - `tests/test_code_symbol_lineage.py`
- Work:
  - exact content, stable qualified signature and normalized AST matching;
  - bounded diff overlap for modified functions/classes/sections;
  - overload, nested symbol and same-name collision handling;
  - split/merge refactors represented as branching candidates;
  - every link references both source versions and exact locators.
- Tests: `python -m pytest -q tests/test_code_symbol_lineage.py`
- Done when: rename, move, body edit, extraction and merge fixtures produce
  evidence-bound links without name-only false positives.

### CLT-06 - First Observable Introduction Query

- Class: `repo_only`
- Owner: Bob
- Dependencies: `CLT-03`, `CLT-05`
- Allowed paths:
  - `src/code_introduction_query.py`
  - `tests/test_code_introduction_query.py`
- Work:
  - first reachable file/symbol/chunk occurrence by lineage;
  - choose author versus committer time only through explicit sort mode;
  - stable ordering with commit topology and timestamp tie-breaks;
  - unknown/incomplete history states;
  - current-source snapshot filter.
- Tests: `python -m pytest -q tests/test_code_introduction_query.py`
- Done when: `sort current code by first observable creation` is deterministic,
  bounded and never labels unknown history as absolute creation.

### CLT-07 - Removal, Resurrection And Historical Scope

- Class: `repo_only`
- Owner: Bob
- Dependencies: `CLT-03`, `CLT-05`
- Allowed paths:
  - `src/code_lifecycle_timeline.py`
  - `tests/test_code_lifecycle_timeline.py`
- Work:
  - removal commits and validity windows;
  - resurrection with exact or candidate prior lineage;
  - current-only, deleted-only and all-history scopes;
  - no tombstone erasure of historical evidence;
  - source policy/deletion may hide content while preserving permitted metadata.
- Tests: `python -m pytest -q tests/test_code_lifecycle_timeline.py`
- Done when: deleted code is absent from current results but remains explainable
  where policy permits.

### CLT-08 - Bounded Timeline Store And Query Provider

- Class: `repo_only`
- Owner: Bob
- Dependencies: USI-03, `CLT-01` through `CLT-07`
- Allowed paths:
  - `src/code_lineage_store.py`
  - `src/code_timeline_query.py`
  - `tests/test_code_lineage_store.py`
  - `tests/test_code_timeline_query.py`
- Work:
  - persist evidence refs and materialized earliest-observed summaries;
  - cursor, source scope, revision range, time range and confidence filters;
  - indexed ordering without full-history scans at query time;
  - clipped/partial/stale state and projection generation;
  - USI timeline provider interface.
- Tests:
  - `python -m pytest -q tests/test_code_lineage_store.py tests/test_code_timeline_query.py`
- Done when: million-event synthetic fixtures return bounded pages with stable
  cursors and indexed plans.

### CLT-09 - Incremental History Backfill And Resume

- Class: `repo_only`
- Owner: Bob
- Dependencies: USI-04 jobs, `CLT-02`, `CLT-08`
- Allowed paths:
  - `src/code_lineage_jobs.py`
  - `tests/test_code_lineage_jobs.py`
- Work:
  - revision-range checkpoints and idempotent batches;
  - append-only new commit updates where history is unchanged;
  - rewrite detection invalidates only affected lineage generations;
  - cancellation, crash/restart, budget and lock handling;
  - no fetch or branch mutation.
- Tests: `python -m pytest -q tests/test_code_lineage_jobs.py`
- Done when: interrupted and repeated backfills converge without duplicate
  events or missing validity windows.

### CLT-10 - Hybrid Code Graph And Timeline Integration

- Class: `repo_only`
- Owner: Bob
- Dependencies: CBM-05/CBM-07, USI-07, `CLT-08`
- Allowed paths:
  - `src/code_graph_timeline_bridge.py`
  - `tests/test_code_graph_timeline_bridge.py`
- Work:
  - attach first-observed/current/removed summaries to code graph refs;
  - timeline filters for structural queries and impact results;
  - no history facts stored in CBM as canonical truth;
  - stale generation mismatch fails to partial, not false freshness;
  - exact read remains current/revision-aware through existing readers.
- Tests: `python -m pytest -q tests/test_code_graph_timeline_bridge.py`
- Done when: one symbol can be found structurally and followed through its
  evidence-backed history without ID confusion.

### CLT-11 - Privacy, Accuracy And Adversarial Fixture Suite

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `CLT-01` through `CLT-10`
- Allowed paths:
  - `tests/fixtures/code_lineage/`
  - `tests/test_code_lineage_accuracy.py`
  - `tests/test_code_lineage_privacy.py`
  - `docs/plans/code-lineage-acceptance.md`
- Work:
  - labelled rename/copy/split/merge/delete/resurrection/import fixtures;
  - precision/recall by method and confidence band;
  - timestamp manipulation and non-monotonic author-time cases;
  - shallow clone, missing object, generated/vendor and history rewrite cases;
  - no author email, absolute path or source body in reports/metrics.
- Tests:
  - `python -m pytest -q tests/test_code_lineage_accuracy.py tests/test_code_lineage_privacy.py`
- Done when: accepted confidence thresholds are evidence-based and uncertain
  cases remain visible as candidates.

### CLT-12 - GRO Metrics And Scale Evidence

- Class: `repo_only`
- Owner: Bob
- Dependencies: GRO-00 and `CLT-08` through `CLT-11`
- Allowed paths:
  - `src/code_lineage_diagnostics.py`
  - `tests/test_code_lineage_diagnostics.py`
  - GRO metrics files only after explicit handoff
- Work:
  - batches, events, candidates, accepted links, unknowns, latency and failures;
  - bounded low-cardinality labels by operation/method/outcome;
  - no repository/path/commit/author labels;
  - benchmark backfill and incremental update without duplicating GRO stack.
- Tests: `python -m pytest -q tests/test_code_lineage_diagnostics.py`
- Done when: scale evidence identifies cost and uncertainty without leaking Git
  metadata.

### CLT-13 - Synthetic Staging And Activation Packet

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `CLT-00` through `CLT-12`
- Allowed paths:
  - `docs/plans/code-lineage-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - exact repositories, branches/revision bounds, history depth and policies;
  - storage estimate, backfill budget, pause/resume and rollback;
  - current-code sort and timeline API acceptance examples;
  - Lens integration remains separately gated;
  - materialize one live activation decision.
- Tests: focused lineage suite plus master JSON validation
- Done when: activation cannot fetch, mutate Git, expose identity or claim
  absolute creation beyond available evidence.

### CLT-LIVE-ACTIVATION - Single User Gate

- Class: `needs_live_go`
- Status: `dormant`
- Blocks: persistent scan/backfill of real repository history
- Decision needed: repositories, refs/ranges, current versus historical scope,
  identity policy, budget, retention, observation window and rollback
- Go phrase:
  `GO CLT-LIVE-ACTIVATION: index available Git lineage for <repos/refs> in <environment> with <history/privacy/budget policy>; label time as first-observable; observe <window>; rollback via <plan> on No-Go.`

## 7. Query Contract For Sorting Code By Time

The default product operation is conceptually:

```json
{
  "domain": "code",
  "mode": "timeline",
  "scope": "current_symbols",
  "sort": "history_first_observed_at",
  "order": "asc",
  "confidence_floor": 0.75,
  "limit": 100,
  "cursor": null
}
```

Each item returns:

- current USI source/version/entity/chunk refs;
- current path/range and symbol signature;
- earliest supporting commit ref and timestamps;
- lineage method/confidence;
- `history_complete`, `shallow_boundary`, `import_suspected` and
  `generated_or_vendor` flags;
- next cursor and clipping state.

## 8. Verification Bundle

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest -q `
  tests\test_code_lineage_contract.py `
  tests\test_code_lineage_git_adapter.py `
  tests\test_code_file_lineage.py `
  tests\test_code_copy_lineage.py `
  tests\test_code_symbol_lineage.py `
  tests\test_code_introduction_query.py `
  tests\test_code_lifecycle_timeline.py `
  tests\test_code_lineage_store.py `
  tests\test_code_timeline_query.py `
  tests\test_code_lineage_jobs.py `
  tests\test_code_lineage_accuracy.py `
  tests\test_code_lineage_privacy.py
```

## 9. Go Language

- `Go`: current files/symbols/chunks have bounded evidence-backed earliest
  observable history, accepted confidence accuracy and safe incremental update.
- `Partial`: file history is reliable but some symbol/copy/refactor lineage is
  candidate-only and clearly labelled.
- `No-Go`: name-only matching, false absolute creation claims, Git mutation,
  identity leaks or unbounded backfill remain possible.
- `Deferred`: author identity, remote history fetch, branch comparison or
  historical source-body retention is outside the approved policy.
- `Blocked`: required Git objects or canonical source/version identity are not
  available.

## 10. Definition Of Done

- Git and Project Versioning remain the sole history authority.
- USI stores bounded lineage facts with methods, confidence and evidence.
- CBM consumes timeline annotations but does not own them.
- Current code can be sorted by first observable history with honest caveats.
- Rename, move, copy, split, merge, delete and resurrection are representable.
- Queries do not scan full history at request time.
- No author/private/path content leaks into reports, metrics or Lens payloads.
- Productive history backfill has one explicit gate and tested rollback.
