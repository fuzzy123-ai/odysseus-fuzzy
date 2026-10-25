# Unified Source Index Domain Adapter Rollout Roadmap

Updated: 2026-09-07

Status: UDA-00 through UDA-03 and MEM-ELIG-00 accepted/released repo-only.
UDA-03 acceptance (2026-09-05) is bound to the new composite/occurrence
candidate and independent deep QA in
`docs/plans/evidence/abc-uda03-composite-acceptance-20260905.json`.
PDS-READ-00 is accepted repo-only (2026-09-07); exact candidate and independent
review evidence are in `handoffs/abc-open-work-5h-run-20260905.json`,
`pds_read00_acceptance`. This accepts the bounded owner-reader prerequisite only.
Productive domain indexing and private Memory reads remain gated.
PLN-SOURCE-00/01/02/03/04 and USI-EXACT-CHAR-CHUNK-00 are accepted/released repo-only;
see their prerequisite sections and canonical run acceptance records below.
Full UDA-06 and productive adapter activation remain unaccepted/default-off.

Historical status reconciliation (2026-09-05, before this run): Guidance and
Open Work retained the UDA-03 failure. Round-7 completed_slices contains no accepted UDA-03; the
registered Rev-89 R2 recovery has no frozen candidate and is not acceptance.

## Native Source Amendment 2026-08-01

Obsidian/ORCA vault is removed from the active domain rollout. The plugin and
its aliases are legacy and cannot be selected as a productive source adapter,
Wave-A dependency, exact reader, fallback or activation scope. A future
one-shot archival import would require its own bounded decision and would not
restore the plugin as runtime authority.

Parent: `OWM-15` / `0.28.x` Unified Source Index Foundation

Lane: `L26`

Slice prefix: `UDA`

Shared activation contract: `USI-LIVE-ACTIVATION`

## 1. Goal

Implement real, owner-scoped and policy-aware Source Adapters for Odysseus
domain systems without moving their writes or truth into USI.

The roadmap turns the generic `SourceAdapter` and `IndexJobStore` contracts from
USI-04 into a controlled rollout for Personal Memory, Personal Docs, native
Knowledge sources, Planning, Library/Research, Universal Inbox, Nextcloud and later communication
domains.

The adapter layer is source-neutral at its core but domain-specific at its
boundaries. It does not create a generic crawler, free filesystem scanner,
provider bypass or second domain store.

## 2. Current Domain Owners

| Domain | Canonical truth/write owner | Existing read/discovery surface |
| --- | --- | --- |
| Personal Memory | `MemoryManager`, memory lifecycle and review policy | `memory.py`, `memory_provider.py`, lifecycle adapters |
| Personal Docs | PersonalDocsManager and owner-scoped files | `personal_docs.py`, RAG manager/vector compatibility |
| Native Knowledge | first-party Knowledge/domain store | accepted native store and exact-reader APIs |
| Obsidian/ORCA vault | no current owner; legacy only | excluded from active adapter rollout |
| Planning | Planning stores/MCP and roadmap files | planning source inventory and service |
| Library/Documents | document/session stores and version routes | document helpers/routes/processors |
| Deep Research | owner-scoped research store | `research_handler_storage.py`, research routes |
| Universal Inbox | discovery/extraction/review/provenance pipeline | Universal Inbox modules and ledgers |
| Nextcloud | source provider, intake ledger and WebDAV policy | provider/scanner/policy modules |
| Email | account/cache/message/poller domain | email owner/cache/message helpers |
| Calendar | SQL calendar models and CalDAV boundary | calendar routes and CalDAV sync |
| Todos/Reminders | task/note domain and scheduler | task routes, note routes/reminders |
| Contacts | contacts store and vCard boundary | contacts routes/helpers |
| Sessions/Chats | SessionManager and session ownership | session routes/manager APIs |
| Forge Code | Local Forge / registered repository and immutable revision | Forge snapshot inventory and exact-reader contract; dedicated FCA roadmap |

No adapter may call a write route to discover data. Provider-backed domains use
their local accepted cache/ledger or an explicitly gated read adapter.

## 3. Ownership And No-Duplication Matrix

| Concern | Canonical owner | UDA responsibility | UDA must not do |
| --- | --- | --- | --- |
| Domain object writes | domain service/store | read immutable observations | mutate, review or approve objects |
| Source/version/chunk identity | USI | create records through accepted contracts | persist parallel IDs as truth |
| Source discovery cursor | USI JobStore plus bounded adapter checkpoint | enumerate changes idempotently | create query indexes in checkpoints |
| Generic text chunking | `rag_text_chunking`/USI profile | select profile and typed locators | fork another generic splitter |
| Domain parsing | domain adapter | produce typed entities/relations/evidence | reinterpret business state without owner contract |
| Semantic projection | USI/Chroma | submit accepted occurrences | write independent Chroma truth |
| RAPTOR | USI/RAPTOR derived run | expose eligible versioned inputs | write summaries or schedule models |
| Cross-domain links | USI Relation candidates/review | emit evidence-bound candidates | merge domain objects or owner scopes |
| Change signals | domain commit plus best-effort notifier | enqueue bounded rediscovery | make domain writes depend on index health |
| Deletion | domain truth plus ULO | emit unavailable/deleted observation | hard-delete audit evidence unsafely |
| Query | USI planner/UIR | provide source capabilities/read_exact | expose one tool per domain |

Forge Code is deliberately not a UDA Wave A/B/C domain. It has a distinct
revision, no-gap eligible-line and two-call exact-read contract in
`forge-usi-code-source-adapter-roadmap.md`. UDA must not register a generic
filesystem/Git crawler as a substitute, treat a branch/worktree as an immutable
source version, or let CBM/embeddings become the Forge exact reader.

## 4. Common Adapter Contract

Every adapter must expose the same bounded capabilities:

```text
describe_capability()
discover(scope, cursor, limit, time_budget)
observe_version(source_ref)
extract(source_version, extraction_profile, budgets)
read_exact(locator, policy_context)
observe_unavailable(source_ref, reason)
```

Required output:

- stable adapter ID and version;
- owner scope and domain kind;
- canonical source ref without secret-bearing provider URL;
- revision/content observation and source timestamps;
- explicit `inline`, `reference_only` or `metadata_only` policy;
- typed locators and exact-reader capability;
- classification and provider constraints;
- deterministic fingerprint, resume cursor and deletion semantics;
- bounded warnings and evidence method.

Adapters never return unrestricted absolute paths, credentials, raw provider
responses or content outside the accepted policy ceiling.

## 5. Default Content Policy By Wave

| Source wave | Default policy | Notes |
| --- | --- | --- |
| Personal Docs | local inline for explicitly indexed owner files | exact reader remains domain-owned |
| Approved Personal Memory | local inline for accepted records | no automatic Memory creation |
| Native Knowledge | local inline or reference-only per native policy | exact reader and owner policy remain authoritative |
| Obsidian/ORCA vault | disabled/excluded | no active adapter or activation scope |
| Planning/Roadmaps | local inline for accepted project sources | Planning writes remain MCP-owned |
| Library/Research | local inline for owner-approved local artifacts | citations and document versions preserved |
| Universal Inbox | metadata/reference until review allows extraction | no review bypass |
| Nextcloud | metadata/reference by default | local-only extracted content requires existing policy Go |
| Email | metadata/reference by default | body/attachments disabled until separately scoped |
| Calendar/Todos | metadata or minimal inline fields | private descriptions policy-controlled |
| Contacts | metadata-only by default | no communication action or enrichment |
| Sessions/Chats | reference-only and disabled by default | explicit retention/content decision required |

The strictest input classification propagates to chunks, entities, relations,
summaries and query results.

## 6. Rollout Waves

- Wave A: Personal Memory and Personal Docs. A native Knowledge adapter joins
  only after its first-party truth owner and exact-reader contract exist.
- Wave B: Planning, Library/Research, Universal Inbox and Nextcloud. These have
  established owner/provenance contracts and can integrate after Wave A core
  behavior is stable.
- Wave C: Email, Calendar, Todos/Reminders, Contacts and Sessions/Chats. These
  remain policy-deferred/default-off until explicit reprioritization. Planning
  their contracts does not authorize reading or indexing real data.

One wave may be `Partial` without blocking the core if its disabled state,
fallback and missing acceptance are explicit. The parent product may not claim
universal coverage while a domain is deferred.

## 7. Mode And Queue Policy

Planning mode is `Standard ABC`. New adapters and synthetic fixtures are
`repo_only`; private source reads and productive scans remain behind the parent
gate.

1. `UDA-03` is accepted repo-only against the fresh 2026-09-05 composite
   acceptance evidence. Historical rejected claims and correction budgets
   remain preserved. Select the next dependency-ready scoped packet under
   current authority/path checks; productive behavior stays off.
2. Adapter contract/fixtures may run in parallel on disjoint new files after
   `UDA-01`.
3. Existing domain hotfiles are touched only by UDA-14/15/16 after adapter
   parity and an explicit owner handoff.
4. Wave C remains blocked by current operator priority even if synthetic tests
   are prepared.
5. UDA creates no product activation gate of its own.

## 8. Slice Queue

### UDA-00 - Domain Truth, Reader And Mutation Inventory

- Class: `safe_offline`
- Owner: Charlie
- Status: `accepted_released_wave5r`
- Evidence: commit `35026ae7ced88fda4cacd159c1dbd2d48d8050f0`;
  canonical audits and the 251-test USI regression passed; independent Wave-5R
  QA and Root deep review passed.
- Dependencies: explicit goal; USI runtime impact map accepted
- Allowed paths:
  - `docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md`
  - `docs/plans/unified-source-index-domain-inventory.json`
  - `scripts/audit_unified_source_index_domains.py`
  - `tests/test_audit_unified_source_index_domains.py`
- Work:
  - enumerate canonical stores, readers, writes, deletes, owner fields and
    current derived indexes for every domain;
  - classify provider/network, local cache, review and content-policy seams;
  - identify existing source refs, timestamps, revisions and tombstones;
  - record dirty/hotfile ownership and current domain deferrals;
  - fail on any proposed adapter without a canonical domain owner.
- Tests: `python -m pytest -q tests/test_audit_unified_source_index_domains.py`
- Done when: every domain has one truth owner, one accepted reader boundary and
  an explicit rollout/deferred status.

### UDA-01 - Adapter Registry And Capability Manifest

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_released_round6_b`
- Evidence: claim `ABC-R6B-UDA01-20260802T204823+0200` released after 77 Root
  integration tests and 91 independent broader USI tests passed.
- Dependencies: `UDA-00`, USI-01, USI-02 and USI-04
- Allowed paths:
  - `src/unified_source_index_sources/__init__.py`
  - `src/unified_source_index_source_registry.py`
  - `src/unified_source_index_source_capability.py`
  - `tests/test_unified_source_index_source_registry.py`
- Work:
  - deterministic adapter registration by domain/capability/version;
  - strict owner/content/provider/query capability manifest;
  - no import-time scan or provider connection;
  - duplicate adapter/domain IDs fail closed;
  - selected adapter generation is recorded in Projection/Job evidence.
- Tests: `python -m pytest -q tests/test_unified_source_index_source_registry.py`
- Done when: runtime can enumerate available adapters without loading source
  content and every capability has explicit policy and exact-reader semantics.

### UDA-02 - Best-Effort Source Change Signal Contract

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_released_round7`
- Evidence: claim `ABC-UDA02-R3-20260803T120700+0200` released after 48 focused
  tests, independent QA and Deep-Sol review passed; compile, diff, concurrent
  lease and safe-v1 migration checks passed.
- Dependencies: `UDA-01`, USI-04
- Allowed paths:
  - `src/unified_source_index_change_signal.py`
  - `src/unified_source_index_change_outbox.py`
  - `tests/test_unified_source_index_change_signal.py`
- Work:
  - content-free source-created/changed/deleted/access-changed signals;
  - idempotency key, owner scope, domain, source ref and revision hint only;
  - bounded durable outbox/retry with no raw body, path or provider response;
  - notifier failure never rolls back a domain commit;
  - periodic discovery remains the correctness fallback.
- Tests: `python -m pytest -q tests/test_unified_source_index_change_signal.py`
- Done when: duplicate/lost/delayed signals converge through discovery and no
  domain write depends on USI availability.

### MEM-ELIG-00 - Canonical Owner Eligibility Snapshot

- Class: `repo_only`
- Owner: Charlie
- Status: `accepted_released_round7`
- Evidence: claim `ABC-MEM-ELIG00-20260803T150700+0200` released with blobs
  `65fe9627` and `6c944734`; 88 focused and 111 compatibility tests,
  independent re-QA and Root Deep-Sol passed with 9/9 pins.
- Dependencies: accepted `UDA-00`
- Allowed paths:
  - `src/memory_owner_eligibility.py`
  - `tests/test_memory_owner_eligibility.py`
- Work:
  - read exactly one explicitly supplied synthetic `memory.json` byte snapshot
    without constructing `MemoryManager`, importing a provider or triggering
    migration, save, recall, vector or network behavior;
  - accept only closed `odysseus.memory_eligibility.v1` stamps that prove exact
    owner, active source, accepted content, non-incognito state, go policy and
    accepted/not-required review with content-free evidence references;
  - exclude legacy, unstamped, deleted, rejected, incognito, review-pending,
    policy-blocked, unknown, coerced or contradictory records without fallback;
  - emit immutable detached eligible records plus content-free bounded counts,
    reason counts and digests; never emit text, owner, host path or raw errors;
  - reject symlinks, duplicate keys/ids, non-finite JSON, oversized/deep inputs
    and file-replacement ambiguity without creating or changing a file.
- Tests: `python -m pytest -q tests/test_memory_owner_eligibility.py`
- Done when: synthetic owner fixtures prove deterministic eligibility and
  exclusion while static and runtime checks prove zero Memory/provider/write or
  productive-data side effects.
- User gates: productive/private Memory scan, eligibility migration/backfill,
  writer adoption, provider/runtime/host activation, push and deploy.

### UDA-03 - Personal Memory Source Adapter

- Class: `repo_only`
- Owner: Bob
- Status: `accepted_repo_only_2026-09-05`
- Evidence: `docs/plans/evidence/abc-uda03-composite-acceptance-20260905.json`
- Dependencies: `UDA-01`, accepted `MEM-ELIG-00`
- Allowed paths:
  - `src/unified_source_index_sources/memory.py`
  - `tests/test_unified_source_index_memory_source.py`
- Work:
  - read accepted owner-scoped Memory records through the canonical
    `MEM-ELIG-00` snapshot contract;
  - stable source/version refs and record-field locators;
  - preserve source/category/lifecycle/review evidence;
  - exclude rejected, deleted, incognito or policy-blocked records;
  - no remember/edit/delete or automatic Memory promotion.
- Tests: `python -m pytest -q tests/test_unified_source_index_memory_source.py`
- Done when: synthetic Memory changes yield deterministic versions and all
  retrieval evidence maps back to the accepted domain record.

### PDS-READ-00 - Bounded Personal Docs Owner Reader Prerequisite

- Class: `repo_only`
- Owner: Bob under an exact two-file claim; Root owns planning integration
- Status: `accepted_repo_only_2026-09-07`
- Evidence: `handoffs/abc-open-work-5h-run-20260905.json`,
  `pds_read00_acceptance`; exact candidate hashes, independent deep PASS,
  32 focused tests passed (2 platform skips), 22 compatibility tests passed,
  and guidance/round6/integration-spine validators passed. Prior rejected
  candidates and correction budgets remain recorded in the same run.
- Dependencies: accepted `UDA-00`, `UDA-01`, and the existing
  `native-knowledge-personal-docs-boundary-contract.md` exact-reader seam
- Allowed paths (new files only):
  - `src/personal_docs_owner_reader.py`
  - `tests/test_personal_docs_owner_reader.py`
- Frozen contract: `handoffs/abc-open-work-5h-run-20260905.json`,
  `pds_read00_contract` version `v1`; independently reviewed before dispatch.
- Work: implement a default-unwired, bounded, read-only domain companion for
  one explicit owner/root binding and strict UTF-8 TXT/Markdown/JSON files.
  Capture immutable descriptors, repeat confined byte-version exact reads,
  and expose only bounded unavailable observations. Constructor performs no
  filesystem work. Latest-snapshot capabilities are process-local; recapture
  replaces them and failed capture revokes them. No persistence or history.
- Existing `PersonalDocsManager` remains the sole truth/write/registry owner;
  this prerequisite does not alter the existing-domain hotfile rule in §7.
- Tests: `python -m pytest -q tests/test_personal_docs_owner_reader.py`,
  existing Personal Docs compatibility and confirmed-route/removal suites,
  hostile filesystem/descriptor lifecycle tests, independent deep QA.
- Done when: synthetic fixtures prove deterministic bounded discovery,
  confinement/replacement rejection, same-byte exact reads, content-free errors
  and zero manager/provider/index/runtime writes or automatic scans.
- Remaining UDA-04 limits: productive owner authorization, registry exclusions,
  extra-root ownership, PDF/Office extraction and legacy Chroma parity remain
  unproven. This prerequisite cannot accept UDA-04 or activate private reads.

### UDA-04 - Personal Docs And Current RAG Source Adapter

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UDA-01`, accepted `PDS-READ-00`, USI generic chunk profile,
  Personal Docs handoff including the remaining format/registry parity scope
- Allowed paths:
  - `src/unified_source_index_sources/personal_docs.py`
  - `tests/test_unified_source_index_personal_docs_source.py`
- Work:
  - owner-scoped file discovery through PersonalDocsManager boundaries;
  - file/content revisions, MIME/type and typed page/section/range locators;
  - reuse `rag_text_chunking` for generic text and accepted document extractors;
  - map legacy Chroma metadata without adopting Chroma IDs;
  - deletion/access loss becomes an observation, not silent stale content.
- Tests: `python -m pytest -q tests/test_unified_source_index_personal_docs_source.py`
- Done when: identical content in two files remains two occurrences and exact
  source reads resolve through the existing Personal Docs reader.

### UDA-05 - Native Knowledge Source Adapter

- Class: `repo_only`
- Owner: Bob with native Knowledge owner handoff
- Dependencies: `UDA-01`, `NMG-02-native-knowledge-store`
- Allowed paths:
  - `src/unified_source_index_sources/knowledge.py`
  - `tests/test_unified_source_index_knowledge_source.py`
- Work:
  - read only through accepted first-party Knowledge APIs;
  - preserve native revisions, typed locators, relations and policy evidence;
  - denied, deleted or unreviewed records fail closed;
  - no write to domain truth or derived projections;
  - no import from `plugins/obsidian` and no plugin artifact compatibility.
- Tests: `python -m pytest -q tests/test_unified_source_index_knowledge_source.py`
- Done when: synthetic native Knowledge records have deterministic
  owner/policy/version/locator parity without a plugin or vault dependency.

### PLN-SOURCE-00 - Approved Planning Projection Reader (Accepted Prerequisite)

- Status: accepted/released repo-only on 2026-09-07; full UDA-06 remains open.
- Allowed paths: `src/planning_revision_store.py`,
  `tests/test_planning_definition_projection.py`, `src/planning_mcp_service.py`,
  `tests/test_planning_mcp_service.py`.
- Public boundary: `snapshot_owner_sources`, `capture_owner_source_snapshot`,
  and `read_owner_source_projection`; select the exact latest approved revision.
  Draft-only rows are explicitly ineligible and cause no source-file read.
- Bounded owner enumeration, selected JSON reads, strict projection validation,
  and Windows-safe path validation preserve PRIVATE/INLINE_LOCAL policy.
  Markdown references carry no Markdown-body read authority.
- Evidence: `handoffs/abc-open-work-5h-run-20260905.json`,
  `pln_source00_acceptance`: independent deep PASS, 675 tests passed and one
  existing Windows skip; central planning checks passed. Historical rejected
  candidates and consumed correction budgets remain preserved.

### USI-EXACT-CHAR-CHUNK-00 - Shared Exact Character Ranges (Accepted Prerequisite)

- Status: accepted/released repo-only on 2026-09-07.
- Allowed paths: `src/rag_text_chunking.py`, `tests/test_rag_text_chunking.py`.
- `split_text_into_exact_character_ranges` supplies bounded contiguous ranges
  without normalizing source text. Existing chunking behavior remains unchanged.
- Evidence: `handoffs/abc-open-work-5h-run-20260905.json`,
  `exact_char_chunk_acceptance`: independent standard PASS, 45 tests passed,
  707 additional boundary checks, and exact reassembly of a 750,079-character
  canonical JSON projection.
- Consumer: accepted PLN-SOURCE-01 uses both prerequisites for the synthetic,
  default-off approved-JSON adapter. Full UDA-06, Markdown adapter consumption, productive
  owner mapping and activation remain open.

### PLN-SOURCE-01 - Approved Planning JSON Adapter (Accepted Prerequisite)

- Status: accepted/released repo-only on 2026-09-07; full UDA-06 remains open.
- Allowed paths: `src/unified_source_index_sources/planning.py` and
  `tests/test_unified_source_index_planning_source.py`.
- Reads only the public approved Planning projection boundary with explicit
  owner/snapshot binding, exact character ranges and deterministic graph records.
- Exported DTOs bind source/version evidence, exact text and the complete
  projection-derived graph; invalid requests fail before unnecessary reads.
- The manifest-only registration is default-off and has no productive factory.
- Evidence: `handoffs/abc-open-work-5h-run-20260905.json`,
  `pln_source01_acceptance`: 577 tests passed and one existing Windows skip,
  central-three PASS, fresh independent whole-candidate Sol/high PASS, and
  exact candidate/pin readback. Prior rejected candidates and budgets remain.
- Remaining UDA-06 scope: composite/runtime integration, productive owner mapping and activation.

### PLN-SOURCE-02 - Explicit Planning Markdown Body Reader (Accepted Prerequisite)

- Status: accepted repo-only prerequisite after G1 corrections and independent deep PASS.
- Target runtime: Debian 13 / Python 3.11. Original Windows acceptance: 612
  passing tests and one symlink skip. Supplementary Ubuntu 24.04.1 / Python 3.12.3
  verification: 613 passing tests, no skips, real symlinks and an independent
  unprivileged POSIX permission-denial probe. Evidence: canonical run
  `pln_source02_linux_verification`. Debian runtime verification remains open.
- Dependencies: accepted `PLN-SOURCE-00`, `PLN-SOURCE-01` and
  `USI-EXACT-CHAR-CHUNK-00`.
- Planning owner scope: `src/planning_mcp_service.py` and
  `tests/test_planning_mcp_service.py`; exact current baselines under own claim.
- Provides a default-empty, explicitly selected Markdown body reader through public
  Planning service methods. Every selected owner/project/roadmap/ref must also
  match the current latest-approved JSON supporting ref and expected raw-byte hash.
- Preserve strict UTF-8 bytes, fixed bounded errors, immutable selection binding,
  exact source versions and existing JSON/stable-filesystem behavior.
- Acceptance: 612 passing tests, one existing Windows symlink skip, central-three
  PASS and independent whole-boundary deep PASS on the corrected frozen candidate.
  Exact hashes and Root/independent evidence: canonical run `pln_source02_acceptance`.
  The initial rejected candidate and all G1 correction counts remain preserved.
- Frozen contract and claim: `handoffs/abc-open-work-5h-run-20260905.json`,
  `pln_source02_contract`; no productive selection, adapter/registry write,
  Markdown truth promotion, deletion inference or full UDA-06 acceptance.

### PLN-SOURCE-03 - Explicit Planning Markdown Adapter (Accepted Prerequisite)

- Status: accepted repo-only internal prerequisite after independent deep PASS.
- Dependencies: accepted `PLN-SOURCE-00`, `PLN-SOURCE-01`, `PLN-SOURCE-02`
  and `USI-EXACT-CHAR-CHUNK-00`.
- Implemented in `src/unified_source_index_sources/planning_markdown.py` and
  `tests/test_unified_source_index_planning_markdown_source.py`.
- Uses only public explicit Markdown capture/read APIs. Detached immutable evidence
  binds owner/project/roadmap/exact ref, approved metadata, raw UTF-8 hash and all
  selection/version/snapshot digests. Exact character ranges and empty bodies
  (zero chunks) are preserved; no inferred graph records or registry entry.
- Acceptance: 50 focused tests; Root 17 and independent review 6 hostile probes;
  required eight-file Windows lane 662 PASS / one symlink skip; central-three PASS;
  independent whole-candidate deep PASS. Same frozen candidate on Ubuntu 24.04.1 /
  Python 3.12.3: 663 PASS / zero skips. Python 3.11 syntax check passed; actual
  Debian 13 / Python 3.11 execution remains unverified.
- Exact hashes, rejected-candidate history, retained correction budgets and the
  unrelated OpenWork pin handoff are recorded in canonical run
  `pln_source03_acceptance`, `pln_source03_candidate` and `pln_source03_final_deep_qa`.
- Productive selections, owner mapping and full UDA-06 remain open/default-off.

### PLN-SOURCE-04 - Single Planning Domain Composite (Accepted Prerequisite)

- Status: accepted/released repo-only on 2026-09-07 after fresh independent deep PASS.
- Dependencies: accepted `UDA-01`, `USI-01`, `USI-03`, `USI-04`, `USI-05`,
  `PLN-SOURCE-00/01/02/03` and `USI-EXACT-CHAR-CHUNK-00`.
- Implemented only in `src/unified_source_index_sources/planning_composite.py`
  and `tests/test_unified_source_index_planning_composite_source.py`.
- One default-off `planning.approved_sources/v1` manifest with `factory=None`
  combines accepted public child APIs and preserves child DTO/source/version/
  provider identities. It replaces the legacy Planning registration; selecting
  both fails the existing duplicate-domain guard.
- Detached bindings and both-child discovery fences reject selected/nonselected
  source drift before and after routed operations. Child bindings are validated
  before comparison. No registry/core/package changes or productive activation.
- The public DTO discovery envelope is 100 JSON plus 24 Markdown sources. The
  real service separately limits metadata to 65,536 bytes; not every 100+24
  selection fits. Existing child graph/text budgets remain unchanged.
- Synthetic per-child jobs share the composite profile and one temporary SQLite
  store; direct lexical retrieval preserves exact refs and routed reads. This
  proves small-fixture compatibility, not the productive job bridge.
- Acceptance: focused23, Root7, central-three and fresh whole-candidate deep PASS.
  Required15 Windows: 743 PASS, one symlink skip, two unchanged ULO02 baseline
  failures. Same candidate Ubuntu24.04.1/Python3.12.3: 744 PASS, zero skips, the
  same two failures. Neither required lane is globally green. Python3.11 syntax
  passed; Debian13/Python3.11 runtime remains unverified.
- Canonical run `pln_source04_acceptance` links exact hashes, rejected first
  candidate, G1 correction and all evidence. The incomplete57-file Linux snapshot
  was repaired to a verified65-file import closure; prior failures are retained.
- Full UDA-06, productive owner mapping and activation remain open/default-off.

### USI-PAGED-EXTRACTION-00 - Complete Graph Extraction Pages (Registered Prerequisite)

- Status: registered repo-only prerequisite for `PLN-SOURCE-05`; requires a fresh
  critical contract and four-file writer claim after Source04 read-pin release.
- Dependencies: accepted generic `USI-04` JobStore/store contracts and Source04.
- Modify only `src/unified_source_index_adapters.py`,
  `src/unified_source_index_jobs.py`, `tests/test_unified_source_index_adapters.py`
  and `tests/test_unified_source_index_jobs.py`; no store/SQLite/schema changes.
- Add opt-in pages of at most100 records, at most12,296 aggregate records and
  123 pages; retain legacy extraction/projection and separate discovered-source,
  page and aggregate budgets. Validate detached exact typed data before behavior,
  stable source/version, sequence, bounded unique cursors, unique record kind/ID,
  and terminal count/canonical digest. Initial terminal empty pages are valid;
  empty advancing pages are rejected. Bind paged jobs to the exact adapter profile.
- Gather the complete source graph under one deadline with lease/cancellation
  checks between pages and immediately precommit. Publish graph and lease CAS
  in one existing transaction; failure preserves prior indexed truth. Use existing
  TRUTH_COMMIT checkpoint recovery for idempotent postcommit finalization without
  source rereads or a self-referential snapshot. No schema expansion.
- Reject paged plus legacy projection before source I/O with fixed
  `paged_projection_unsupported`; no graph truncation or hidden activation.
- Acceptance: full12,296-record memory/SQLite roundtrip, empty graph, excessive
  page/aggregate/character limits, malformed/duplicate/loop/drift/digest inputs,
  timeout/cancellation/lease race, atomic failure, postcommit crash/retry,
  profile mismatch before I/O, legacy regressions, central-three and fresh deep
  PASS. Retain the unrelated two ULO02 baseline failures without reopening them.

### PLN-SOURCE-05 - Planning Job Bridge (Registered Dependent Prerequisite)

- Status: dependency-blocked repo-only prerequisite; no implementation claim yet.
- Dependencies: accepted Source04 and `USI-PAGED-EXTRACTION-00`.
- Create only `src/unified_source_index_sources/planning_job_bridge.py` and
  `tests/test_unified_source_index_planning_job_bridge.py` under a later claim.
- Two internal per-child generic adapters preserve child provider/source/version
  IDs, share the composite-binding-derived required profile and page the entire
  validated graph. Resolve generic evidence through the exact chunk and typed
  child-bound request for composite routed reads; never truncate or widen generic
  exact-read DTOs to bypass child snapshot/locator authority.
- Acceptance requires both-child full indexing and exact-read evidence. UIR
  generation-aware query/read routing remains a separate integration gate; this
  bridge alone does not accept full UDA-06 or activate productive indexing.

### UDA-06 - Planning And Roadmap Source Adapter

- Class: `repo_only`
- Owner: Bob with Planning owner handoff
- Dependencies: `UDA-01`, Planning storage contract, accepted `PLN-SOURCE-00`
  and `USI-EXACT-CHAR-CHUNK-00` prerequisites, plus accepted `PLN-SOURCE-01`,
  `PLN-SOURCE-02`, `PLN-SOURCE-03` and `PLN-SOURCE-04`; registered
  `USI-PAGED-EXTRACTION-00` and `PLN-SOURCE-05` remain prerequisites for direct
  search, followed by UIR generation-aware query/read integration.
- Allowed paths:
  - `src/unified_source_index_sources/planning.py`
  - `tests/test_unified_source_index_planning_source.py`
- Work:
  - consume Planning inventory and canonical JSON/Markdown sources read-only;
  - stable plan/project/slice/gate entities and dependency relations;
  - use source file/version evidence without becoming Plan Graph truth;
  - preserve owner/project scope and exclude unsafe raw previews;
  - identify Planning-to-Memory projections that can later remain derived.
- Tests: `python -m pytest -q tests/test_unified_source_index_planning_source.py`
- Done when: plans are searchable directly with exact source refs and no
  Planning write/apply/delete behavior moves into USI.

### UDA-07 - Document Library And Deep Research Source Adapters

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UDA-01`, document/research owner handoff
- Allowed paths:
  - `src/unified_source_index_sources/document_library.py`
  - `src/unified_source_index_sources/research.py`
  - `tests/test_unified_source_index_document_library_source.py`
  - `tests/test_unified_source_index_research_source.py`
- Work:
  - owner-scoped document/version and archived-state observations;
  - research report, citation and source-link locators;
  - no direct route call, export, restore or research job start;
  - archived/deleted/access-changed states propagate explicitly;
  - preserve exact document/report readers.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_document_library_source.py tests/test_unified_source_index_research_source.py`
- Done when: document/report fixtures can be queried with version/citation
  evidence and no duplicate library or research store exists.

### UDA-08 - Universal Inbox Source Adapter

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UDA-01`, Inbox provenance/review contract
- Allowed paths:
  - `src/unified_source_index_sources/universal_inbox.py`
  - `tests/test_unified_source_index_universal_inbox_source.py`
- Work:
  - ingest accepted discovery/provenance/review state, not arbitrary inbox paths;
  - metadata/reference-only before extraction approval;
  - version refs follow content hash and accepted extraction generation;
  - review-blocked/quarantined/failed items remain query-ineligible;
  - RaptorGraph events remain evidence signals, not source truth.
- Tests: `python -m pytest -q tests/test_unified_source_index_universal_inbox_source.py`
- Done when: fixture intake transitions index only policy-eligible versions and
  preserve every review/provenance decision.

### UDA-09 - Nextcloud Source Adapter

- Class: `repo_only` for local fixtures and accepted local ledgers
- Owner: Bob
- Dependencies: `UDA-01`, Nextcloud source policy and UDA-08
- Allowed paths:
  - `src/unified_source_index_sources/nextcloud.py`
  - `tests/test_unified_source_index_nextcloud_source.py`
- Work:
  - consume the designated source provider/scanner/intake ledger boundaries;
  - metadata/reference-only default and redacted canonical refs;
  - no WebDAV network call in unit/synthetic staging;
  - local extracted content only when existing local-only/review policy allows;
  - moves/deletes/tag changes produce evidence without remote writes.
- Tests: `python -m pytest -q tests/test_unified_source_index_nextcloud_source.py`
- Done when: offline fixture/provider records map deterministically and no
  adapter bypasses Nextcloud permissions, review or live gates.

### UDA-10 - Email Source Adapter

- Class: `blocked` by current operator priority; becomes `repo_only` for
  synthetic fixtures after explicit reprioritization
- Owner: Bob
- Dependencies: `UDA-01`, Email owner/cache/privacy contract
- Allowed paths after reprioritization:
  - `src/unified_source_index_sources/email.py`
  - `tests/test_unified_source_index_email_source.py`
- Work:
  - local owner-scoped cache/message records only; no IMAP/SMTP action;
  - thread/message entities and metadata/reference locators;
  - body and attachment content disabled by default;
  - folder/delete/access state and account removal propagation;
  - no reply, send, mark, move or delete capability.
- Tests: `python -m pytest -q tests/test_unified_source_index_email_source.py`
- Done when: synthetic messages respect owner/account/folder/content policy and
  exact domain reads remain separate.

### UDA-11 - Calendar, Todo And Reminder Source Adapters

- Class: `blocked` by current operator priority; becomes `repo_only` for
  synthetic fixtures after explicit reprioritization
- Owner: Bob
- Dependencies: `UDA-01`, calendar/task domain contracts
- Allowed paths after reprioritization:
  - `src/unified_source_index_sources/calendar.py`
  - `src/unified_source_index_sources/todos.py`
  - `tests/test_unified_source_index_calendar_source.py`
  - `tests/test_unified_source_index_todo_source.py`
- Work:
  - owner-scoped local event/task/note observations;
  - recurrence, due/status and source timestamps with typed locators;
  - descriptions use metadata/minimal-inline policy by default;
  - tombstones and recurrence changes do not resurrect stale occurrences;
  - no CalDAV writeback, reminder dispatch or TaskScheduler mutation.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_calendar_source.py tests/test_unified_source_index_todo_source.py`
- Done when: synthetic recurrence/delete/update cases are deterministic and no
  query path can dispatch or mutate a reminder.

### UDA-12 - Contacts Source Adapter

- Class: `blocked` by current operator priority; becomes `repo_only` for
  synthetic fixtures after explicit reprioritization
- Owner: Bob
- Dependencies: `UDA-01`, Contacts owner/privacy contract
- Allowed paths after reprioritization:
  - `src/unified_source_index_sources/contacts.py`
  - `tests/test_unified_source_index_contacts_source.py`
- Work:
  - metadata-only contact entities and owner-scoped exact refs;
  - no enrichment, communication action or implicit relationship claim;
  - vCard/import provenance and delete/change observations;
  - sensitive fields excluded unless an explicit content policy allows them;
  - cross-domain person links remain reviewable relation candidates.
- Tests: `python -m pytest -q tests/test_unified_source_index_contacts_source.py`
- Done when: synthetic contacts are discoverable without exposing private
  fields or granting communication capability.

### UDA-13 - Sessions And Chat Reference Adapter

- Class: `repo_only` for metadata/reference fixtures; raw-content indexing is
  `blocked` until explicit retention and privacy policy exists
- Owner: Bob
- Dependencies: `UDA-01`, Session owner/retention contract
- Allowed paths:
  - `src/unified_source_index_sources/sessions.py`
  - `tests/test_unified_source_index_session_source.py`
- Work:
  - owner/session metadata and durable reference locators only by default;
  - exclude incognito, secure or expired sessions;
  - no raw prompt/completion indexing in the default profile;
  - approved Memory extraction remains separate and is not duplicated;
  - archive/delete/retention state propagates to query eligibility.
- Tests: `python -m pytest -q tests/test_unified_source_index_session_source.py`
- Done when: metadata fixtures are policy-safe and raw chat indexing remains
  impossible without a separate explicit policy decision.

### UDA-14 - Wave A Change-Signal Integration

- Class: `repo_only`
- Owner: Charlie with one domain writer at a time
- Dependencies: `UDA-02`, `UDA-03`, `UDA-04`, adapter parity evidence; add
  `UDA-05` only when native Knowledge is selected for the activation scope
- Allowed paths, serialized by domain:
  - `src/memory.py` or `src/memory_provider.py` only after Memory handoff
  - `src/personal_docs.py` and `routes/personal_routes.py` only after Personal
    Docs handoff
  - accepted native Knowledge commit/service boundary only after its owner
    handoff
  - `tests/test_unified_source_index_wave_a_signals.py`
- Work:
  - emit best-effort content-free change signals after successful domain commit;
  - notifier failure never changes domain success/failure;
  - dedupe/replay and periodic discovery convergence;
  - no source payload or path in outbox/metrics;
  - no signal starts a productive worker while runtime is disabled.
- Tests: `python -m pytest -q tests/test_unified_source_index_wave_a_signals.py`
- Done when: create/update/delete/access changes converge without dual domain
  writes or stale-query resurrection.

### UDA-15 - Wave B Change-Signal Integration

- Class: `repo_only`
- Owner: Charlie with one domain writer at a time
- Dependencies: `UDA-02`, `UDA-06` through `UDA-09`, adapter parity evidence
- Allowed paths, serialized by domain:
  - `src/planning_source_inventory.py` or accepted Planning commit boundary
  - accepted document/research storage boundary
  - accepted Universal Inbox provenance/commit boundary
  - accepted Nextcloud intake-ledger boundary
  - `tests/test_unified_source_index_wave_b_signals.py`
- Work: same best-effort, content-free, idempotent post-commit contract as
  UDA-14, with domain-specific delete/access semantics.
- Tests: `python -m pytest -q tests/test_unified_source_index_wave_b_signals.py`
- Done when: Wave B local truth changes converge without network writes,
  provider bypass, review bypass or second query index.

### UDA-16 - Wave C Change-Signal Integration

- Class: `blocked` until the relevant Wave C domains are explicitly
  reprioritized and their adapters accepted
- Owner: Charlie with one domain writer at a time
- Dependencies: accepted `UDA-10` through `UDA-13`
- Allowed paths after reprioritization:
  - accepted local Email owner/cache event boundary
  - accepted Calendar/Task/Contact/Session commit boundaries
  - `tests/test_unified_source_index_wave_c_signals.py`
- Work: content-free post-commit signals only; no provider poll/send/write,
  reminder dispatch, contact action or raw session capture.
- Tests: `python -m pytest -q tests/test_unified_source_index_wave_c_signals.py`
- Done when: each activated communication domain has tested local convergence
  and remains independently disabled until parent source-scope activation.

### UDA-17 - Cross-Domain Policy, Dedupe And Relation Candidates

- Class: `repo_only`
- Owner: Bob
- Dependencies: accepted adapters from the selected source waves
- Allowed paths:
  - `src/unified_source_index_domain_policy.py`
  - `src/unified_source_index_relation_candidates.py`
  - `tests/test_unified_source_index_domain_policy.py`
  - `tests/test_unified_source_index_relation_candidates.py`
- Work:
  - strict owner/provider/classification ceiling before fusion;
  - occurrence dedupe without collapsing distinct source records;
  - evidence-bound, typed relation candidates with method/confidence;
  - no automatic person/task/contact/source merge;
  - summaries inherit the strictest input policy.
- Tests:
  - `python -m pytest -q tests/test_unified_source_index_domain_policy.py tests/test_unified_source_index_relation_candidates.py`
- Done when: same content across domains remains separately attributable and no
  cross-owner/cross-policy query or relation can be produced.

### UDA-18 - Source Wave Acceptance And Parent Activation Contribution

- Class: `repo_only`
- Owner: Charlie
- Dependencies: every dependency-satisfied adapter, signal and policy slice
  required by the explicitly selected source scopes; `UDA-05` is required only
  when native Knowledge is selected; deferred/excluded slices remain blocked
- Allowed paths:
  - `docs/plans/unified-source-index-domain-adapter-acceptance.md`
  - `docs/plans/unified-source-index-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - per-domain truth owner, adapter version, policy and exact-reader matrix;
  - count/locator/policy/delete/query parity and known gaps;
  - wave-specific job, storage, rebuild and rollback budget;
  - declare disabled/deferred sources without universal-coverage claims;
  - contribute selected `source scopes` to the existing parent gate only.
- Tests: selected adapter/signal/policy suites plus JSON validation
- Done when: each proposed source scope is independently Go/Partial/No-Go and
  no deferred domain is accidentally enabled or described as complete.

## 9. Dependency And Parallelism Rules

- `UDA-01` is the registry barrier; adapter implementations may then run on
  disjoint files with at most three writers.
- UDA adapter files are new and may not edit domain truth modules.
- UDA-14/15/16 are serialized hotfile integration slices after parity.
- UIR owns runtime composition and consumer cutover; UDA only registers
  capabilities and source change signals.
- ULO owns owner/delete/export/backup behavior; adapters only report source
  observations and unavailability.
- RAPTOR/CBM/Lineage are projection/query consumers, not source adapters for
  unrelated domains.
- Obsidian/ORCA plugin paths are legacy and never enter an active UDA wave.
- GRO owns exporter files; UDA emits only accepted content-free metric calls
  after handoff.

## 10. Acceptance Metrics

For every activated adapter:

- deterministic source/version/chunk IDs across repeated discovery;
- exact owner, policy and locator parity with the domain reader;
- bounded discovery/extraction with resumable cursor;
- create/change/delete/access-loss convergence;
- no provider/network mutation from indexing;
- no domain write failure caused by change-signal failure;
- no raw private content in jobs, logs, metrics or reports;
- query results open the exact accepted domain reader;
- rebuild can delete derived projections without changing domain truth.

## 11. Shared Activation Language

UDA has no independent product gate. A source wave can become productive only
when named in the parent phrase:

`GO USI-LIVE-ACTIVATION: activate USI <version> for <source scopes> in <environment> using <policies/generation>; observe <window>; auto-rollback via <plan> on No-Go.`

Wave C planning never implies permission to inspect real Email, Calendar,
Todos, Contacts or Chat data. Current operator deferrals remain authoritative
until explicitly changed.

## 12. Definition Of Done

- the registry contains no duplicate domain or adapter identity;
- Wave A has real adapter parity and best-effort change-signal evidence;
- selected Wave B domains have explicit Go/Partial/No-Go results;
- Wave C remains safely planned and default-off until reprioritized;
- domain writes, reviews, provider actions and exact readers remain canonical;
- deletion/access changes cannot leave silently eligible stale results;
- cross-domain retrieval preserves occurrence identity and strictest policy;
- UDA contributes source-scope evidence to one parent USI activation gate.
