# Unified Source Index Data Lifecycle And Operations Roadmap

Updated: 2026-07-13

Status: planned child track; productive lifecycle actions default-off

Parent: `OWM-15` / `0.28.x` Unified Source Index Foundation

Lane: `L27`

Slice prefix: `ULO`

Shared activation contract: `USI-LIVE-ACTIVATION`

## 1. Goal

Integrate USI with Odysseus owner rename, deletion, access revocation, export,
backup, restore, wipe, retention and disaster-recovery behavior without making
the index the owner of user data or creating a second operations plane.

USI-13 owns the engine-level consistent SQLite backup and projection rebuild
primitives. Existing account, domain, backup and wipe flows own the user action
and canonical data mutation. This roadmap owns the contracts and orchestration
that keep USI records and rebuildable projections correct when those actions
occur.

It does not authorize a live rename, deletion, wipe, restore or backup. It does
not export the shared USI database as a portable user artifact, and it does not
turn derived index records into domain truth.

## 2. Current Code Evidence

- `routes/auth_user_rename.py` migrates SQL owner fields and owner-scoped
  Memory, Research, Personal RAG, uploads, skills and sessions artifacts.
- `routes/backup_routes.py` exports and imports selected user-owned JSON data,
  including memories, presets, skills, settings, features and preferences.
- `routes/admin_wipe_routes.py` owns category-specific destructive operations.
- `src/constants.py` and `src/config.py` define existing data roots and runtime
  configuration boundaries.
- system backup/update routes already own broader operational workflows.
- `src/memory.py`, Personal Docs, ORCA, Planning, Inbox and provider domains
  retain their own delete, archive, access and retention semantics.
- USI-03/04 define transactional stores, tombstones, jobs and projection
  manifests; USI-13 defines consistent SQLite backup/restore/rebuild
  primitives.
- UDA adapters observe domain deletion/access changes; UIR owns runtime state,
  workers, generation pinning and rollback.

The complete integration inventory is frozen in
`docs/plans/unified-source-index-integration-impact-map.md`.

## 3. Canonical Ownership And No-Duplication Matrix

| Concern | Canonical owner | ULO responsibility | ULO must not do |
| --- | --- | --- | --- |
| Username/account mutation | Auth/account service | map accepted owner lifecycle to USI scope | rename accounts or credentials |
| Domain record deletion | domain service/store | consume committed unavailable/deleted facts | delete domain truth |
| USI records/tombstones/jobs | USI core stores | orchestrate lifecycle operations transactionally | fork store or job schemas |
| SQLite backup primitive | USI-13 | call and verify it from system operations | implement a second SQLite copier |
| User portability export | existing backup/export owner | define index manifest and rebuild semantics | export shared raw index databases |
| System backup/restore | system backup owner | include consistent USI artifact and verification | replace system backup workflows |
| Projection purge/rebuild | USI manifests plus provider owners | coordinate generation-safe purge/rebuild | treat Chroma/CBM/RAPTOR as truth |
| Runtime stop/swap/rollback | UIR | request quiesce, generation swap and resume | create another worker controller |
| Source observation | UDA/domain adapters | request rediscovery after lifecycle event | bypass domain policy or providers |
| Metrics/diagnostics | GRO/UIR status | emit accepted content-free lifecycle state | create exporter/dashboard stack |
| Operator confirmation | existing action security | preserve action-specific authorization | reuse the product activation gate as consent |

## 4. Lifecycle Invariants

1. A display-name or username change must not change the logical owner scope of
   indexed records.
2. Owner scope is opaque and stable. If a current domain lacks a stable owner
   key, a versioned alias mapping bridges the migration; raw usernames never
   become permanent source IDs.
3. Domain truth commits first. USI receives an idempotent lifecycle request or
   converges through bounded rediscovery if the notification is lost.
4. Access revocation makes affected occurrences query-ineligible before or in
   the same committed generation that exposes the lifecycle success.
5. Tombstones retain the minimum evidence required for convergence and audit;
   they never preserve source content that policy requires erased.
6. Chroma, CBM, RAPTOR, FTS mirrors and query caches are purgeable projections.
7. Backup and restore pin one database generation and one projection manifest.
   A restore never serves a mixed pre/post-restore generation.
8. User export carries domain truth plus a redacted index manifest sufficient
   to explain or rebuild coverage. It does not carry another user's records,
   engine credentials, absolute paths or a shared USI database.
9. Failed index cleanup cannot falsely report complete erasure. The owning
   action returns an explicit pending/degraded lifecycle state and retries from
   durable USI JobStore truth.
10. No cleanup, rename, export, restore or wipe is triggered by a query.

## 5. Lifecycle State Model

Minimum operation states:

- `requested`: authorized owner action exists, but no USI mutation is claimed;
- `quiescing`: affected writers stop at bounded JobStore checkpoints;
- `applying`: core source eligibility/tombstone/alias transaction is active;
- `projecting`: rebuildable provider data and caches are purged or rebuilt;
- `verifying`: owner, count, hash, policy and generation invariants are checked;
- `complete`: canonical action and required USI effects are verified;
- `degraded`: domain action committed but one or more derived cleanup tasks are
  pending with an honest reason and retry state;
- `failed`: no success is claimed and a bounded recovery instruction exists;
- `rolled_back`: only actions that are domain-reversible may restore the prior
  generation; erasure is never reversed from derived data.

Every state transition records operation ID, action kind, opaque owner scope,
affected source scopes, generation, timestamps, actor class, policy version and
content-free error evidence. Raw source content, query text, credentials and
secret-bearing provider references are forbidden.

## 6. Mode And Queue Policy

Planning mode is `Standard ABC`; implementation and verification use only
synthetic owners, temporary databases and redacted fixtures until separately
authorized operational actions occur.

1. On a future explicit goal, only `ULO-00` is claimable.
2. Contract and new-file work may overlap disjoint UIR/UDA work after USI
   identity/store contracts are stable.
3. Auth, backup, wipe, constants, app and provider hotfiles are single-writer.
4. Repo-only tests may simulate rename/delete/export/restore/wipe. They may not
   touch real user roots, productive databases or provider accounts.
5. ULO creates no second product gate. `USI-LIVE-ACTIVATION` selects whether
   USI is live; each later destructive or external operation retains its
   existing action-specific authorization.

## 7. Slice Queue

### ULO-00 - Lifecycle And Operations Inventory

- Class: `safe_offline`
- Owner: Charlie
- Status: `ready_after_goal_start`
- Dependencies: explicit goal; USI integration impact map accepted
- Allowed paths:
  - `docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md`
  - `docs/plans/unified-source-index-lifecycle-inventory.json`
  - `scripts/audit_unified_source_index_lifecycle.py`
  - `tests/test_audit_unified_source_index_lifecycle.py`
- Work:
  - enumerate owner rename/delete, domain delete/archive/access, export/import,
    backup/restore, category wipe, factory reset and retention entrypoints;
  - identify canonical transaction owner, authorization, data roots, failure
    behavior and current rollback for each action;
  - classify every USI table, FTS index, projection, cache, job and artifact by
    owner scope, rebuildability and erase/retain policy;
  - record active hotfile claims and current deferred domains;
  - fail when a persisted USI artifact has no lifecycle owner.
- Tests: `python -m pytest -q tests/test_audit_unified_source_index_lifecycle.py`
- Done when: every persisted artifact maps to exactly one lifecycle policy and
  every action maps to one canonical domain/system owner.

### ULO-01 - Stable Owner Scope And Alias Contract

- Class: `repo_only`
- Owner: Bob
- Dependencies: `ULO-00`, USI-01 and USI-02
- Allowed paths:
  - `src/unified_source_index_owner_scope.py`
  - `tests/test_unified_source_index_owner_scope.py`
- Work:
  - define opaque immutable owner-scope IDs and versioned display aliases;
  - deterministic migration for legacy username-keyed source refs;
  - reject cross-owner alias reuse, ambiguous lookup and unsafe normalization;
  - keep aliases out of chunk/entity identity and low-cardinality metrics;
  - support owner-scope lookup without scanning source content.
- Tests: `python -m pytest -q tests/test_unified_source_index_owner_scope.py`
- Done when: repeated rename chains preserve IDs and locators, while collisions
  and stale aliases cannot cross owner boundaries.

### ULO-02 - Deletion, Access Revocation And Tombstone Contract

- Class: `repo_only`
- Owner: Bob
- Dependencies: `ULO-01`, USI-03/04 and UDA change-observation contract
- Allowed paths:
  - `src/unified_source_index_lifecycle_contract.py`
  - `src/unified_source_index_tombstone_policy.py`
  - `tests/test_unified_source_index_lifecycle_contract.py`
  - `tests/test_unified_source_index_tombstone_policy.py`
- Work:
  - typed source/version/owner deletion, archive and access-loss operations;
  - idempotency, generation fence, retry and stale-notification ordering;
  - query ineligibility before projection cleanup completes;
  - content erasure with minimum content-free tombstone evidence;
  - explicit distinction between unavailable, superseded, deleted and erased.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_lifecycle_contract.py tests/test_unified_source_index_tombstone_policy.py`
- Done when: out-of-order/replayed lifecycle events cannot resurrect source
  content or make another owner eligible.

### ULO-03 - Projection Purge And Rebuild Orchestration

- Class: `repo_only`
- Owner: Bob
- Dependencies: `ULO-02`, USI-05/06/10/13 provider manifests
- Allowed paths:
  - `src/unified_source_index_projection_lifecycle.py`
  - `tests/test_unified_source_index_projection_lifecycle.py`
- Work:
  - provider-neutral purge/rebuild requests keyed by USI refs and generation;
  - Chroma, CBM, RAPTOR, Lineage and query-cache capabilities remain optional;
  - missing/unavailable providers become durable pending work, not false Go;
  - stale generation completion cannot overwrite the selected manifest;
  - projection deletion never calls a domain source delete.
- Tests: `python -m pytest -q tests/test_unified_source_index_projection_lifecycle.py`
- Done when: each projection can be removed and reconstructed from accepted
  truth, and an absent optional engine cannot preserve query eligibility.

### ULO-04 - Owner Rename Integration

- Class: `repo_only`
- Owner: Charlie with Auth owner handoff
- Dependencies: `ULO-01` through `ULO-03`, UIR runtime quiesce contract
- Allowed paths:
  - `routes/auth_user_rename.py`
  - `src/unified_source_index_owner_lifecycle.py`
  - `tests/test_unified_source_index_owner_rename.py`
- Work:
  - integrate USI as one participant in the existing rename workflow;
  - prefer stable owner scope so only aliases/display references change;
  - define compensation when legacy username-keyed domain migration fails;
  - preserve source/version/chunk IDs, jobs and projection ownership;
  - no full corpus rewrite or provider scan during the request.
- Tests: `python -m pytest -q tests/test_unified_source_index_owner_rename.py`
- Done when: synthetic multi-step rename succeeds or compensates honestly with
  no orphaned owner records, cross-owner visibility or ID churn.

### ULO-05 - Account Deletion And Right-To-Erasure Integration

- Class: `repo_only` for synthetic fixtures; productive action retains the
  existing destructive-action authorization
- Owner: Charlie with account/domain owners
- Dependencies: `ULO-02`, `ULO-03`, UDA selected-source deletion semantics
- Allowed paths:
  - accepted account-deletion service boundary after owner handoff
  - `src/unified_source_index_owner_erasure.py`
  - `tests/test_unified_source_index_owner_erasure.py`
- Work:
  - owner-wide eligibility fence followed by bounded core/projection cleanup;
  - erase content and sensitive locators according to source policy;
  - retain only legally/operationally accepted content-free completion proof;
  - durable retries and explicit pending provider cleanup;
  - prove queries, status, caches and backups do not expose erased owner data.
- Tests: `python -m pytest -q tests/test_unified_source_index_owner_erasure.py`
- Done when: interrupted/replayed erasure converges, never restores content and
  reports complete only after all required stores are verified.

### ULO-06 - Category Wipe And Factory-Reset Integration

- Class: `repo_only` for synthetic fixtures; productive action retains the
  existing admin confirmation
- Owner: Charlie with Admin Wipe owner handoff
- Dependencies: `ULO-02`, `ULO-03` and domain wipe matrix from `ULO-00`
- Allowed paths:
  - `routes/admin_wipe_routes.py`
  - `src/unified_source_index_wipe_adapter.py`
  - `tests/test_unified_source_index_wipe.py`
- Work:
  - map each existing wipe category to exact source/projection/job scopes;
  - reject unknown categories and over-broad owner/global expansion;
  - quiesce affected workers and invalidate query caches before completion;
  - preserve unaffected owners/domains and canonical domain action results;
  - distinguish reset-to-rebuild from permanent erase.
- Tests: `python -m pytest -q tests/test_unified_source_index_wipe.py`
- Done when: every synthetic category changes only its declared scope and no
  wipe reports success while eligible stale results remain.

### ULO-07 - User Portability Export And Import Semantics

- Class: `repo_only`
- Owner: Bob with Backup route owner handoff
- Dependencies: `ULO-01`, `ULO-02`, domain export/import contracts
- Allowed paths:
  - `src/unified_source_index_portability.py`
  - `routes/backup_routes.py` only in serialized integration claim
  - `tests/test_unified_source_index_portability.py`
- Work:
  - export domain truth through existing owners plus a redacted USI coverage
    manifest containing schema/profile versions and rebuild hints;
  - never export a shared SQLite/Chroma/CBM database as user portability data;
  - import creates/reuses destination domain records first, then rediscovers;
  - source IDs are deterministically rebound to the destination owner scope;
  - reject traversal, absolute paths, credentials and cross-owner refs.
- Tests: `python -m pytest -q tests/test_unified_source_index_portability.py`
- Done when: round-trip fixtures rebuild equivalent eligible knowledge from
  domain truth without copying derived truth or another owner's artifacts.

### ULO-08 - System Backup Integration And Consistency Fence

- Class: `repo_only` for temporary artifacts; productive backup retains its
  existing operator action and storage policy
- Owner: Charlie with system backup owner handoff
- Dependencies: USI-13, `ULO-03`, UIR generation/quiesce contract
- Allowed paths:
  - `src/unified_source_index_system_backup.py`
  - accepted system backup route/service only after handoff
  - `tests/test_unified_source_index_system_backup.py`
- Work:
  - invoke the USI-13 SQLite online-backup primitive at a pinned generation;
  - include schema/profile/projection manifests and checksum inventory;
  - quiesce only when the selected backup mode requires it;
  - exclude transient locks, raw credentials and independently rebuildable
    engine data unless policy explicitly includes a verified snapshot;
  - partial backup cannot be labeled restorable.
- Tests: `python -m pytest -q tests/test_unified_source_index_system_backup.py`
- Done when: concurrent synthetic writes yield one internally consistent,
  checksummed artifact with explicit included/excluded projections.

### ULO-09 - Restore, Validation And Generation Swap

- Class: `repo_only` for temporary targets; productive restore retains its
  existing operator authorization
- Owner: Charlie
- Dependencies: `ULO-03`, `ULO-08`, UIR rollback/generation contract
- Allowed paths:
  - `src/unified_source_index_system_restore.py`
  - accepted system restore route/service only after handoff
  - `tests/test_unified_source_index_system_restore.py`
- Work:
  - restore into an isolated target and validate schema, checksums, ownership,
    policy and source/domain prerequisites before selection;
  - migrate only through accepted versioned migrations;
  - rebuild omitted/stale projections into a new generation;
  - atomic runtime generation swap with prior generation rollback;
  - never merge a restored owner into the wrong account implicitly.
- Tests: `python -m pytest -q tests/test_unified_source_index_system_restore.py`
- Done when: corrupt/incompatible backups fail before selection and a valid
  restore never serves mixed generations or stale projection data.

### ULO-10 - Retention And Garbage Collection

- Class: `repo_only`
- Owner: Bob
- Dependencies: `ULO-02`, `ULO-03`, USI JobStore/QueryCacheStore contracts
- Allowed paths:
  - `src/unified_source_index_retention.py`
  - `tests/test_unified_source_index_retention.py`
- Work:
  - bounded policy for completed/failed jobs, query cache, stale versions,
    superseded generations, tombstones and orphaned projection manifests;
  - source/domain retention and legal holds override generic index cleanup;
  - mark/sweep uses generation and owner fences with dry-run counts;
  - interrupted GC is idempotent and never follows arbitrary filesystem refs;
  - no raw content in retention logs or metrics.
- Tests: `python -m pytest -q tests/test_unified_source_index_retention.py`
- Done when: synthetic long-lived stores remain bounded without deleting live,
  held or rollback-required evidence.

### ULO-11 - Storage Failure And Corruption Recovery

- Class: `repo_only`
- Owner: Bob
- Dependencies: `ULO-08` through `ULO-10`, USI-03/13
- Allowed paths:
  - `src/unified_source_index_storage_recovery.py`
  - `tests/test_unified_source_index_storage_recovery.py`
- Work:
  - WAL/busy/locked, disk-full, read-only, permissions, checksum and corruption
    failure classification;
  - bounded retry versus fail-closed/quarantine decisions;
  - integrity check and restore/rebuild decision tree;
  - no destructive repair of domain truth or unverified database replacement;
  - content-free operator diagnostics with exact recovery artifact IDs.
- Tests: `python -m pytest -q tests/test_unified_source_index_storage_recovery.py`
- Done when: each injected storage failure produces deterministic state,
  preserves accepted truth and cannot falsely return healthy/complete.

### ULO-12 - Lifecycle Diagnostics And Operations Readback

- Class: `repo_only`
- Owner: Bob with GRO/UIR owner handoff
- Dependencies: `ULO-02` through `ULO-11`, USI-12 and UIR status contract
- Allowed paths:
  - `src/unified_source_index_lifecycle_status.py`
  - `routes/unified_source_index_routes.py` only after UIR route handoff
  - GRO-owned metric files only after GRO handoff
  - `tests/test_unified_source_index_lifecycle_status.py`
- Work:
  - bounded authorized status for pending erasure, purge, restore, rebuild and
    GC operations;
  - counts/age/state only, with no username, path, source content or query;
  - distinguish domain action, core eligibility and projection completion;
  - stable low-cardinality metrics through GRO;
  - diagnostics never scan a corpus or wake a provider.
- Tests: `python -m pytest -q tests/test_unified_source_index_lifecycle_status.py`
- Done when: an operator can prove lifecycle completion or pending work without
  reading private source data or opening engine internals.

### ULO-13 - Disaster-Recovery And Lifecycle Drill

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `ULO-04` through `ULO-12`, UIR failure suite
- Allowed paths:
  - `tests/test_unified_source_index_lifecycle_failure_matrix.py`
  - `scripts/drill_unified_source_index_recovery.py`
  - `docs/plans/unified-source-index-lifecycle-acceptance.md`
- Work:
  - synthetic rename interruption, delete replay, projection outage, disk full,
    corrupt backup, failed restore, concurrent query and stale worker cases;
  - verify owner isolation, eligibility fences, hashes and generation swaps;
  - measure bounded recovery time, pending-work visibility and storage growth;
  - exercise rebuild without productive source/provider reads;
  - produce redacted evidence and explicit Partial/No-Go outcomes.
- Tests: `python -m pytest -q tests/test_unified_source_index_lifecycle_failure_matrix.py`
- Done when: every declared lifecycle action has an executable recovery path
  and no failure can silently retain or resurrect queryable private content.

### ULO-14 - Lifecycle Closure Packet

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `ULO-00` through `ULO-13`; selected UDA source lifecycle and
  UIR generation/rollback contracts green
- Allowed paths:
  - `docs/plans/unified-source-index-lifecycle-closure-packet.md`
  - `docs/plans/unified-source-index-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - owner-scope, delete/access, export/import, backup/restore, wipe, retention
    and recovery matrix for the proposed source scopes;
  - exact artifact checksums, generations, projection rebuild and rollback;
  - name deferred domains and pending provider cleanup without overclaim;
  - distinguish product activation from later destructive-action approvals;
  - contribute evidence to the existing parent gate only.
- Tests: focused ULO suite plus JSON/guidance validation
- Done when: `USI-15` can prove lifecycle correctness for the exact source
  scopes in its activation packet and no separate ULO product gate exists.

## 8. Dependency And Hotfile Rules

- `ULO-00` precedes all auth/backup/wipe/system-operation edits.
- `ULO-01` and `ULO-02` are contract barriers for all lifecycle integrations.
- `ULO-04`, `ULO-05`, `ULO-06`, `ULO-07`, `ULO-08` and `ULO-09` serialize each
  existing owner hotfile with its current roadmap/thread.
- UIR alone owns runtime worker quiesce, generation selection and rollback.
- UDA alone owns domain readers and best-effort change observations.
- USI-13 alone owns SQLite backup/rebuild primitives; ULO owns their operational
  invocation, verification and system-workflow integration.
- GRO alone owns exporter/dashboard code; ULO emits accepted content-free
  samples after handoff.
- CBM, RAPTOR, Chroma and Lineage remain rebuildable providers and cannot veto
  core eligibility fencing when unavailable.

## 9. Acceptance Metrics

- rename causes zero USI source/version/chunk ID churn;
- delete/access revocation becomes query-ineligible before success is claimed;
- owner-wide erasure leaves zero eligible/core/projection/cache records beyond
  explicitly accepted content-free proof;
- export/import has zero cross-owner refs and rebuilds from domain truth;
- backup/restore checksum, record-count and selected-generation hashes match;
- corrupt/incompatible restore artifacts fail before runtime selection;
- wipe changes only the named owner/category/source scopes;
- retention is bounded, idempotent and respects legal/domain retention;
- optional provider outage remains visible as pending cleanup, never false Go;
- logs, metrics and closure evidence contain no raw content, usernames, paths,
  credentials or query text.

## 10. Shared Activation And Action Authorization

ULO has no independent product gate. Its closure is required by the parent
phrase for the selected source scopes:

`GO USI-LIVE-ACTIVATION: activate USI <version> for <source scopes> in <environment> using <policies/generation>; observe <window>; auto-rollback via <plan> on No-Go.`

That phrase permits only the named USI runtime/source activation. It is not
authorization to rename a user, delete an account, wipe data, export private
data, run a productive backup or restore an environment. Those operations keep
their existing endpoint security, operator confirmation and storage policy.

## 11. Definition Of Done

- every persisted USI artifact has an owner, rebuild and lifecycle policy;
- stable opaque owner scope survives rename without source identity churn;
- delete/access/erasure cannot leave silently eligible stale results;
- user portability exports domain truth and a redacted manifest, not shared
  derived databases;
- system backup/restore uses USI-13 primitives and generation-safe verification;
- wipe, retention and recovery behavior are bounded and test-proven;
- UIR, UDA and existing domain/operations owners retain their responsibilities;
- ULO contributes lifecycle evidence to one parent USI activation gate.
