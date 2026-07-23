# Unified Source Index Domain Adapter Rollout Roadmap

Updated: 2026-07-13

Status: planned child track; productive domain indexing default-off

Parent: `OWM-15` / `0.28.x` Unified Source Index Foundation

Lane: `L26`

Slice prefix: `UDA`

Shared activation contract: `USI-LIVE-ACTIVATION`

## 1. Goal

Implement real, owner-scoped and policy-aware Source Adapters for Odysseus
domain systems without moving their writes or truth into USI.

The roadmap turns the generic `SourceAdapter` and `IndexJobStore` contracts from
USI-04 into a controlled rollout for Personal Memory, Personal Docs, ORCA/Vault,
Planning, Library/Research, Universal Inbox, Nextcloud and later communication
domains.

The adapter layer is source-neutral at its core but domain-specific at its
boundaries. It does not create a generic crawler, free filesystem scanner,
provider bypass or second domain store.

## 2. Current Domain Owners

| Domain | Canonical truth/write owner | Existing read/discovery surface |
| --- | --- | --- |
| Personal Memory | `MemoryManager`, memory lifecycle and review policy | `memory.py`, `memory_provider.py`, lifecycle adapters |
| Personal Docs | PersonalDocsManager and owner-scoped files | `personal_docs.py`, RAG manager/vector compatibility |
| ORCA/Vault | vault files, vault policy, ledger/history | Obsidian backend vault/query/context modules |
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
| ORCA/Vault | local inline or reference-only per vault rule | locked/private rules remain authoritative |
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

- Wave A: Personal Memory, Personal Docs and ORCA/Vault. These already have
  direct retrieval paths and are required for initial query parity.
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

1. Only `UDA-00` is claimable after a future explicit goal.
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
- Status: `ready_after_goal_start`
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

### UDA-03 - Personal Memory Source Adapter

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UDA-01`, Memory owner handoff
- Allowed paths:
  - `src/unified_source_index_sources/memory.py`
  - `tests/test_unified_source_index_memory_source.py`
- Work:
  - read accepted owner-scoped Memory records through canonical APIs;
  - stable source/version refs and record-field locators;
  - preserve source/category/lifecycle/review evidence;
  - exclude rejected, deleted, incognito or policy-blocked records;
  - no remember/edit/delete or automatic Memory promotion.
- Tests: `python -m pytest -q tests/test_unified_source_index_memory_source.py`
- Done when: synthetic Memory changes yield deterministic versions and all
  retrieval evidence maps back to the accepted domain record.

### UDA-04 - Personal Docs And Current RAG Source Adapter

- Class: `repo_only`
- Owner: Bob
- Dependencies: `UDA-01`, USI generic chunk profile, Personal Docs handoff
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

### UDA-05 - ORCA And Vault Source Adapter

- Class: `repo_only`
- Owner: Bob with plugin owner handoff
- Dependencies: `UDA-01`, vault security/rules contract
- Allowed paths:
  - `src/unified_source_index_sources/orca_vault.py`
  - `tests/test_unified_source_index_orca_vault_source.py`
- Work:
  - read vault files through accepted vault service/security boundaries;
  - map ledger/history refs to source versions and discovery checkpoints;
  - preserve links, headings, tags and file locators as evidence;
  - locked or denied vault content fails closed;
  - no write to vault, derived index or RAPTOR artifacts.
- Tests: `python -m pytest -q tests/test_unified_source_index_orca_vault_source.py`
- Done when: fixture vault retrieval has owner/policy/locator parity with the
  plugin path and does not require `derived_index.json` as truth.

### UDA-06 - Planning And Roadmap Source Adapter

- Class: `repo_only`
- Owner: Bob with Planning owner handoff
- Dependencies: `UDA-01`, Planning storage contract
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
- Dependencies: `UDA-02` through `UDA-05`, adapter parity evidence
- Allowed paths, serialized by domain:
  - `src/memory.py` or `src/memory_provider.py` only after Memory handoff
  - `src/personal_docs.py` and `routes/personal_routes.py` only after Personal
    Docs handoff
  - selected `plugins/obsidian/backend/` commit/service boundary only after
    plugin handoff
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
- Dependencies: `UDA-00` through `UDA-17` for the selected wave; deferred
  slices remain explicitly blocked
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
