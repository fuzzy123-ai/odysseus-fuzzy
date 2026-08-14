# Forge-USI Code Source Adapter And Two-Call Retrieval Roadmap

Stand: 2026-08-03

Status: `centrally_registered / FCA-00_FCA-01_and_FCA-POL-00_accepted_released_repo_only / FCA-02-R2_architecture_frozen_claim_registration_pending / productive_indexing_gated`

Parent tracks: Local Forge / Unified Source Index / Codebase Memory / Lens Code

## Roadmap-Authoring Authority

```yaml
run_id: ABC-FCA-ROADMAP-20260802T230214+0200
thread_id: 019fc434-3b97-7291-bf68-6daf3cad6fd7
phase: roadmap_authoring
supervision: interactive
queue_scope: open_work_post_mvp
mutation_authority: repo_only_roadmap_files
master: root; Sol route requested, concrete runtime SKU not exposed
state: released_after_independent_deep_review
acquired_at: 2026-08-02T23:02:14+02:00
lease_expires_at: 2026-08-03T01:02:14+02:00
released_at: 2026-08-02T23:21:03+02:00
allowed_paths:
  - docs/plans/forge-usi-code-source-adapter-roadmap.md
  - docs/plans/project-versioning-forge-provider-roadmap.md
  - docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md
  - docs/plans/code-intelligence-evaluation-protocol.md
  - docs/plans/unified-source-index-runtime-integration-roadmap.md
  - docs/plans/codebase-memory-integration-roadmap.md
  - docs/plans/lens-code-graph-roadmap.md
excluded_hotfiles:
  - docs/plans/open-work-completion-master-roadmap.json
  - docs/plans/multi-agent-execution-guidance.json
  - docs/plans/parallel-roadmap-execution-round6.json
  - all implementation, test, runtime, provider, source and live-system paths
handoff_required: true
live_actions: false
```

Route: ABC is the sole orchestrator. Three disjoint Terra roadmap-authoring
workers may edit only their assigned path sets. Root/Sol performs the required
deep cross-track review. Central Open-Work registration remains a separate
Root-owned handoff because another active same-checkout master owns those
shared files.

Worker claims:

```yaml
- claim_id: ABC-FCA-A-20260802T230214+0200
  owner: Alice/Terra
  state: released_after_independent_deep_review
  allowed_paths:
    - docs/plans/forge-usi-code-source-adapter-roadmap.md
    - docs/plans/project-versioning-forge-provider-roadmap.md
    - docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md
  objective: author the Forge-USI code adapter, coverage and ownership roadmap
- claim_id: ABC-FCA-B-20260802T230214+0200
  owner: Bob/Terra
  state: released_after_independent_deep_review
  allowed_paths:
    - docs/plans/code-intelligence-evaluation-protocol.md
    - docs/plans/unified-source-index-runtime-integration-roadmap.md
  objective: author the hard two-tool-call retrieval certification and runtime handoff
- claim_id: ABC-FCA-C-20260802T230214+0200
  owner: Charlie/Terra
  state: released_after_independent_deep_review
  allowed_paths:
    - docs/plans/codebase-memory-integration-roadmap.md
    - docs/plans/lens-code-graph-roadmap.md
  objective: author generation-safe CBM sync and optional explicit index controls
```

## Authoring Objective

Freeze the missing Forge-to-USI code-source boundary, the measurable two-tool-
call retrieval guarantee, the Forge/USI/CBM generation handoff and the optional
operator-facing index controls without implementing or activating any runtime.

The completed roadmap must keep these owners distinct:

- Forge owns repository identity, immutable revision evidence, file snapshots
  and exact source reads.
- USI owns index truth, chunk occurrence identity, coverage, jobs, provenance
  and bounded query planning.
- CBM, embeddings and other Memory retrieval lanes are rebuildable projections.
- Lens exposes status, evidence and explicitly confirmed operations; it owns no
  source, index or projection truth.

## 1. Frozen Boundary Contract

This track is the only planned code-source route from an approved Forge
repository revision into USI. It does not replace Project Versioning, USI
stores, Codebase Memory, Code Lineage or the code-intelligence query surface.

| Concern | Owner | Required behavior | Must not do |
| --- | --- | --- | --- |
| repository registration, authorization and policy | Forge | admits an approved repository and reader scope | delegate scope decisions to USI or CBM |
| revision, branch, commit and immutable snapshot | Forge | names and resolves the exact repository revision | let an index, graph or embedding assert freshness |
| file inventory, blob/text bytes and exact line read | Forge | supplies authoritative snapshot evidence and exact reads | expose unbounded filesystem reads |
| source occurrence, chunk identity, coverage and index jobs | USI | records what was observed from one Forge snapshot | change Forge content or create a second repository truth |
| lexical/vector/symbol/graph projections and ranking | CBM, embeddings and Memory lanes | rebuild from USI occurrences and declare their generation | become an exact reader or assert source completeness |
| status and explicit operator controls | Lens/UIR | shows evidence and creates only confirmed, bounded jobs | infer a productive scan or own index truth |

Forge is therefore the authority for the answer to “what exists at revision
`R` and what are its exact lines?” USI is the authority for the answer to
“which eligible lines of `R` have a current indexed occurrence, why not, and
which job established that fact?” A projection may answer “which indexed
occurrences are useful for this question?” only.

No component may materialize a source snippet as authoritative merely because
it is stored in a USI chunk, vector store or graph. A retrieval result carries
the immutable Forge revision and a Forge exact-read locator; Forge supplies the
text for any answer that requires exact source content.

## 2. Admission, Snapshot And Locator Contract

The adapter accepts only an already registered, authorized Forge repository and
one resolved immutable revision. Branch names, a moving worktree and an
unverified provider ref are discovery inputs, never index identities.

```text
ForgeRepositoryScope + ForgeRevisionEvidence
  -> ForgeSnapshotManifest
  -> USI CodeSourceVersion + CoverageManifest + IndexJob
  -> rebuildable lexical / embedding / CBM projections
```

Required Forge snapshot evidence:

- opaque `repo_id`, owner/project scope and the policy generation that admitted
  it;
- immutable `forge_revision_id` and canonical commit/object identity where the
  Forge supports one; a branch ref is informational only;
- deterministic file inventory with repository-relative normalized paths,
  file/blob digest, byte count, line count, detected kind and rename/deletion
  facts when a predecessor is supplied;
- an opaque snapshot digest over the admitted inventory and relevant adapter
  policy/version; and
- a bounded exact-reader reference. It must resolve only this repository,
  revision, path and requested line range after Forge repeats authorization.

USI source and chunk identities bind `repo_id`, `forge_revision_id`, normalized
path, source/blob digest, locator range, extraction-profile version and owner
scope. Identical text in two files, two revisions or two repositories remains
two source occurrences. A rename is evidence relating two occurrences, not an
identity shortcut; a delete is an unavailable/tombstone observation, not a
silent disappearance.

The adapter never accepts an absolute path as a public locator and never puts
raw repository bodies, provider output, credentials or local private paths into
jobs, metrics, error receipts or coverage summaries.

## 3. Coverage Manifest And No-Gap Rule

USI persists one versioned Coverage Manifest for each attempted
`(repo_id, forge_revision_id, adapter_version, indexing-policy-generation)`.
It is index truth, not a Forge replacement. A new scan may supersede a manifest
only for the same immutable snapshot and policy binding; it must retain the
prior job/evidence reference for audit.

Minimal redacted shape:

```yaml
schema: odysseus.usi.forge_code_coverage.v1
coverage_id: cov_...
repo_id: repo_...
owner_scope_ref: scope_...
forge_revision_id: rev_...
forge_snapshot_digest: sha256:...
adapter_version: forge-code-adapter.v1
indexing_policy_generation: policy_...
status: complete | partial | stale | failed
established_by_job: job_...
files:
  - path_ref: repo-relative-redacted-or-authorized-path-ref
    blob_digest: sha256:...
    source_kind: code | text | generated | binary | other
    total_line_count: 0
    direct_line_denominator: included | policy_out_of_scope | non_text
    eligible_line_count: 0
    covered_line_ranges: [[1, 34], [40, 96]]
    uncovered_eligible_ranges: []
    exclusion: null | {code: policy_excluded, evidence_ref: ...}
    occurrence_count: 0
totals:
  discovered_files: 0
  eligible_files: 0
  excluded_files: 0
  unreadable_files: 0
  eligible_lines: 0
  covered_eligible_lines: 0
  uncovered_eligible_lines: 0
  deleted_sources_observed: 0
whole_codebase_claim: allowed | forbidden_due_to_policy_excluded_source_text
```

`complete` has a strict meaning: every file in the immutable Forge inventory is
either an explicit `policy_out_of_scope` or `non_text` inventory record, or is
readable textual source with every physical line covered by at least one
accepted USI occurrence for the same snapshot. Multiple overlapping chunks may
cover a line; their union, clipped to the direct-line denominator, must contain
no gap. Comments, blank lines, generated/minified text and syntax-adjacent
lines are eligible whenever their file is in scope. This prevents semantic
chunk boundaries from turning a request for “all code” into an unreported line
hole. Empty in-scope text files prove zero eligible lines explicitly.

Only a policy-out-of-scope file or a genuinely binary/non-text blob may stay
outside the direct-line denominator, and each must be a visible, per-file
evidence record. If a policy excludes a source-text file, the selected-scope
manifest can be `complete` only for its stated in-scope denominator, with
`whole_codebase_claim: forbidden_due_to_policy_excluded_source_text`; it must
never be presented as universal or whole-codebase coverage. Generated,
minified, dependency, cache or vendor *text* stays in the denominator whenever
it is in scope; classifications may influence ranking or a separate retrieval
policy but cannot manufacture a coverage gap.

Unsupported language, parser or extraction profile does not permit exclusion:
the adapter must fall back to generic revision-bound line chunks. File size or
job budget does not permit exclusion either: bounded/streaming jobs must resume
until the textual file is covered. Forge-read failure, decoding/encoding
failure, exhausted job budget, failed fallback extraction, unclassified source
text or any uncovered denominator line makes the manifest `partial` or
`failed`. None may be collapsed into a global count.

`stale` means the manifest was complete or partial for a different Forge
revision than the requested target, or that its policy/adapter binding is no
longer eligible for the requested query. It is not a synonym for “probably
unchanged.” `failed` records a bounded job failure without pretending that the
last successful manifest covered the new target.

## 4. Extraction And Delta Rules

USI may choose syntax-aware chunks, but each accepted chunk must retain a
contiguous file line locator and must participate in the coverage-union check.
If the selected parser cannot create locators that meet the no-gap rule, it
must fall back to generic revision-bound line chunks for the same textual file;
the file is not partially blessed or excluded because syntax is unknown. Large
textual files use bounded/streaming job pages with resumable opaque checkpoints
until the direct-line denominator is covered. A decode/read/budget/fallback
failure produces `partial` or `failed`, never a size/profile exclusion. Generic
line chunking may not weaken locator, digest or coverage checks.

Indexing starts from a requested Forge revision or from a bounded, content-free
Forge change signal. There is no watcher, ambient worktree crawl, import-time
scan or automatic productive reindex. The initial implementation uses only
synthetic repositories and fake Forge readers; production scanning remains
disabled until the parent activation gate names a concrete repository scope.

For a successor revision, Forge supplies inventory/delta evidence against one
predecessor. The adapter classifies add, modify, delete, rename and unchanged
paths deterministically:

- unchanged blobs may reuse immutable extraction artifacts, but USI writes a
  new revision-bound occurrence/coverage record rather than claiming the old
  occurrence covers the successor;
- modified/new paths receive new extraction and no-gap verification;
- a rename retains predecessor evidence but receives a new path-bound locator;
- deleted paths receive a tombstone/unavailable observation and become
  ineligible for the successor; and
- ambiguous inventory, missing predecessor evidence or a lost delta falls back
  to a bounded full inventory of the selected Forge revision, never to a silent
  mixed-revision index.

USI jobs are idempotent by repository scope, immutable revision, snapshot
digest, adapter version and policy generation. Retry/resume checkpoints contain
only opaque cursor/evidence references. A Forge commit or external provider
sync never waits for USI job success and is never rolled back because indexing
failed.

## 5. Query, Staleness And Generation Safety

The code retrieval surface consumes one selected USI Coverage Manifest. It may
rank lexical, semantic, symbol and graph candidates, but all candidates used to
make a coverage assertion must bind to that one manifest and Forge revision.
It must not combine `main@R1` chunks with `main@R2` chunks to manufacture a
complete result.

Every query response exposes at least:

- requested repository/revision selector and resolved `forge_revision_id`;
- `coverage_id`, coverage status, eligible/covered/uncovered totals and any
  matching exclusion reasons;
- the index/lexical generation and each optional embedding/CBM graph generation;
- per candidate: USI occurrence ID, snapshot-bound Forge locator, digest and
  projection generation(s); and
- whether exact source text is required and the only valid Forge exact-read
  reference.

When USI is complete but an embedding or CBM generation is behind, a query may
use current lexical/USI candidates and mark the projection stale. It may not
return graph/semantic absence as a completeness result. When USI itself is
partial, stale or failed for the requested revision, a query returns that state
and known gaps/exclusions; it cannot promise a two-call complete-code answer.
Rebuilding or deleting CBM/embedding/lexical projections must not change Forge
content, USI source occurrences, coverage evidence or the exact-reader route.

The hard two-tool-call certification and exact answer envelope are specified in
the Code Intelligence and UIR roadmaps. This roadmap supplies their mandatory
precondition: a complete, snapshot-bound manifest plus the accepted canonical
`read_file` Forge-locator bridge from `FCA-06A`.

## 6. Dependency-Ready Slice Queue

All slices are `repo_only`, use synthetic repositories/fake Forge readers and
remain default-off. A later implementation must acquire one path-scoped claim;
the listed paths are the complete allowed future implementation set, not
authorization to claim them together.

Current Round-7 frontier:

- `FCA-00` is accepted/released under claim
  `ABC-FCA00-R3-20260803T122000+0200`: 61 focused tests, independent QA and
  final Deep-Sol passed with six exact pins and the authority/tamper matrix.
- `FCA-01` is accepted/released under claim
  `ABC-FCA01-R5-20260803T150000+0200`: final blobs `b675ddf2`, `38f86696` and
  `91daaeac`; 73 focused and 158 seven-lane tests passed in independent re-QA
  and Root Deep-Sol with eight exact pins.
- `FCA-POL-00` R2 is accepted/released, evidence-only and default-off under
  claim `ABC-FCA-POL00-R2-REV103`. Exact blobs `84b32e05` and `4db2931e`
  passed 42 focused, 189 dependency, independent hostile QA and final Deep-Sol.
  This status grants no productive repository indexing, provider, issuer,
  runtime activation or live authority.

### FCA-00 - Forge Capability And Snapshot Inventory

- Status: `accepted_released_round7`
- Dependencies: explicit future goal; accepted PVF repository/reader handoff;
  USI source registry/store contracts available; no collision with active Forge
  or USI runtime claims.
- Allowed paths:
  - `src/unified_source_index_sources/forge_code.py`
  - `src/unified_source_index_source_capability.py`
  - `src/repo_git_adapter.py`
  - `tests/test_unified_source_index_forge_code_source.py`
  - `tests/test_repo_git_adapter.py`
- Work: define an authenticated Forge-only code capability, immutable snapshot
  inventory and bounded exact-reader reference; reject moving/foreign
  revisions, absolute paths and unregistered repositories.
- Evidence: synthetic fixture revision inventory, exact-reader authorization
  negatives and deterministic snapshot digest tests.
- Done when: USI can observe one authorized Forge snapshot without scanning a
  live worktree or retaining source bodies as Forge truth.

### FCA-01 - Version-Bound Code Occurrences And Locator Validation

- Status: `accepted_released_round7`
- Dependencies: `FCA-00`, accepted USI source/chunk identity contracts.
- Allowed paths:
  - `src/unified_source_index_contract.py`
  - `src/unified_source_index_stores.py`
  - `src/unified_source_index_sources/forge_code.py`
  - `tests/test_unified_source_index_contract.py`
  - `tests/test_unified_source_index_forge_code_source.py`
- Work: bind occurrence identity, file digest and contiguous line locator to
  one Forge revision; validate no path escape, digest mismatch, foreign scope
  or locator ambiguity.
- Evidence: same text across files/revisions, rename and bad locator fixtures.
- Done when: neither dedupe nor query can treat a chunk from another path or
  revision as the requested source occurrence.

### FCA-POL-00 - Typed Immutable Code Index PolicyObservation

- Status: `accepted_released_r2_private_snapshot_evidence_only_default_off`
- Dependencies: `FCA-00` R3 accepted/released; `FCA-01` R5 accepted/released;
  accepted Round-7 Claim/Pin/Baseline control compatibility.
- Accepted claim: `ABC-FCA-POL00-R2-REV103`; Evidence
  `docs/plans/evidence/abc-fca-pol00-r2-private-snapshot-rev103.json` at blob
  `8db0d14fc4a99a3de5cf7821ada70a8db6ce48d0`.
- Accepted representation: one validated private tuple snapshot, fresh detached
  decisions, exact five-parameter `PolicyFileDecision` construction and a fixed
  read-only schema property; private tamper fails closed and content-free.
- Allowed paths:
  - `src/unified_source_index_code_policy.py`
  - `tests/test_unified_source_index_code_policy.py`
- Work: define one content-free immutable `PolicyObservation` over an exact
  `ForgeSnapshotInventory`; represent a canonical per-file `in_scope` or
  `policy_out_of_scope` decision bijection; bind Forge authority, snapshot,
  indexing-policy generation and evidence into one semantic digest; provide
  detached semantic construction and bounded canonical-byte decoding.
- Evidence: every inventory file has exactly one decision; arbitrary exclusion
  strings cannot authorize `policy_out_of_scope`; mutation, rehash, alias,
  hostile-container and callback attacks fail closed; construction and decode
  perform no file, Git, provider, network or runtime action.
- Authority boundary: this slice is injected structural evidence only. It has
  no productive exclusion authority, selects no repository and starts no
  indexing. A later issuer decision remains separately gated.
- Done when: focused tests, the accepted FCA-00/FCA-01 dependency corridor,
  independent hostile QA and Root/Sol review pass on one frozen candidate.
- Forbidden: changes to rejected FCA-02 source/test, Forge reader, stores, USI
  contracts or registry; productive policy selection/indexing; provider,
  network, runtime, live, commit, push or deploy.

#### Frozen Sol product architecture packet

Packet `ABC-FCA-POL00-PRODUCT-SOL-ARCH-v1` is frozen. Its digest is computed
over the UTF-8 RFC-8259 JSON object below with keys recursively sorted and
separators `,` and `:`, excluding no fields. The exact digest is
`sha256:986ddc8973d5d9a3509df388de4ee6536b68d78f678a1b7b6ef82590b01868a7`.

```json
{
  "architect": "root/Sol",
  "authority_limits": [
    "structural_evidence_only",
    "no_policy_issuer_or_exclusion_authority",
    "no_repository_selection_or_index_activation",
    "default_off_zero_io_git_provider_network_runtime"
  ],
  "budgets": {
    "canonical_payload_bytes_max": 1000000,
    "decision_count_max": 512,
    "decision_count_min": 0,
    "file_byte_count_max": 16777216,
    "json_depth_max": 6,
    "json_node_count_max": 8192,
    "path_chars_max": 1024,
    "policy_generation_chars_max": 128,
    "total_string_chars_max": 750000
  },
  "canonicalization": [
    "UTF-8 RFC-8259 JSON; recursively sorted keys; ensure_ascii false; separators comma-colon; no BOM whitespace or trailing data",
    "decoder rejects duplicate keys non-finite numbers non-canonical bytes unknown or missing keys bool-as-int and every numeric or structural budget overflow",
    "each path is exact NFC and its identity key is NFC(path).casefold(); the exact path content_sha256 and byte_count must equal one detached inventory file",
    "decisions are sorted by exact path and zip-equal the complete canonical inventory file tuple; missing extra duplicate and casefold-alias decisions fail closed"
  ],
  "closed_values": {
    "decision": [
      "in_scope",
      "policy_out_of_scope"
    ],
    "error_code": [
      "budget_exceeded",
      "invalid_decision",
      "invalid_observation",
      "invalid_payload"
    ],
    "evidence_ref_pattern": "^sha256:[0-9a-f]{64}$",
    "indexing_policy_generation_pattern": "^[a-z][a-z0-9_.:-]{0,127}$"
  },
  "detach_algorithm": [
    "exact-type gate ForgeSnapshotInventory ForgeSnapshotAuthorityBinding ForgeSnapshotFile tuple PolicyFileDecision and all scalar fields before attribute protocol equality hashing encoding iteration serialization or repr",
    "capture every exact input field once into exact built-in locals; reconstruct a fresh Forge authority binding file tuple and inventory; never retain or reread caller aliases after capture",
    "reconstruct every decision from captured exact scalars and validate only detached inventory and decision copies",
    "store only detached exact scalars authority binding and decision tuple; returned dicts and bytes are fresh projections and post-construction caller mutation cannot change semantic state or digest"
  ],
  "digest_projection": [
    "schema",
    "owner_scope",
    "repo_id",
    "version_id",
    "commit_sha",
    "manifest_sha256",
    "snapshot_digest",
    "authority_binding.adapter_id",
    "authority_binding.adapter_version",
    "authority_binding.adapter_generation",
    "authority_binding.admission_policy_generation",
    "indexing_policy_generation",
    "policy_evidence_ref",
    "decisions[].schema",
    "decisions[].path",
    "decisions[].content_sha256",
    "decisions[].byte_count",
    "decisions[].decision",
    "decisions[].evidence_ref"
  ],
  "error_contract": {
    "exception": "CodePolicyObservationError(ValueError)",
    "rules": [
      "every public boundary emits a fresh exception whose args equal the one allowlisted code",
      "raise from None suppresses cause and context display; raw callback exception text repr path owner content or private marker never crosses the boundary",
      "PolicyFileDecision maps failure to invalid_decision; PolicyObservation construction and encoding map failure to invalid_observation; canonical-byte decode maps malformed input to invalid_payload and a numeric budget overflow to budget_exceeded"
    ]
  },
  "hostile_acceptance": [
    "foreign Mapping Sequence tuple list bytes str and iterator subclasses plus hostile __class__ properties are rejected before dispatch",
    "tampered exact nested values with hostile property equality hash encode repr to_dict serializer or mutable CodePolicyObservationError callbacks yield only fresh allowlisted errors",
    "single-capture TOCTOU and mutation-after-validation probes preserve identities canonical bytes digest and bounded repr",
    "empty 512-file and 513-file inventory boundaries plus payload byte depth node string path and file-byte limits are exact",
    "construction import encode decode and projection make zero filesystem Git subprocess provider network runtime registry store reader or indexing calls"
  ],
  "packet_id": "ABC-FCA-POL00-PRODUCT-SOL-ARCH-v1",
  "public_api": [
    "PolicyFileDecision(path, content_sha256, byte_count, decision, evidence_ref)",
    "PolicyFileDecision.to_dict()",
    "PolicyObservation.from_inventory(inventory, indexing_policy_generation, policy_evidence_ref, decisions)",
    "PolicyObservation.from_canonical_bytes(payload)",
    "PolicyObservation.to_dict()",
    "PolicyObservation.to_canonical_bytes()"
  ],
  "schemas": {
    "PolicyFileDecision": [
      "schema=odysseus.usi.code_policy_file_decision.v1",
      "path:str",
      "content_sha256:str",
      "byte_count:int",
      "decision:closed-enum",
      "evidence_ref:sha256-ref"
    ],
    "PolicyObservation": [
      "schema=odysseus.usi.code_policy_observation.v1",
      "owner_scope:str",
      "repo_id:str",
      "version_id:str",
      "commit_sha:str",
      "manifest_sha256:str",
      "snapshot_digest:str",
      "authority_binding:ForgeSnapshotAuthorityBinding",
      "indexing_policy_generation:str",
      "policy_evidence_ref:sha256-ref",
      "decisions:tuple[PolicyFileDecision,...]",
      "observation_digest:sha256-ref"
    ]
  },
  "slice_id": "FCA-POL-00-code-index-policy-observation",
  "state": "frozen",
  "verification": [
    "focused exact schema detach bijection digest decoder error and zero-effect tests",
    "dependency FCA-00 FCA-01 Forge adapter contract store owner-scope and registry corridor",
    "independent hostile QA on exact frozen blobs",
    "Root/Sol final review and central Round7 Round6 Guidance validators"
  ]
}
```

`PolicyObservation` deliberately exposes no select, exclude, admit, index,
activate, read or issuer method. A later slice may consume this value only after
binding a separately reviewed policy issuer and productive gate; FCA-POL-00 by
itself can never turn `policy_out_of_scope` into exclusion authority.

### FCA-02 - Coverage Manifest And No-Gap Verifier

- Status: `dependency_ready_for_fresh_FCA-02-R2_architecture_and_claim`
- Dependencies: accepted `FCA-01` and accepted `FCA-POL-00` R2.
- Allowed paths:
  - `src/unified_source_index_code_coverage.py`
  - `src/unified_source_index_stores.py`
  - `src/unified_source_index_sources/forge_code.py`
  - `tests/test_unified_source_index_code_coverage.py`
  - `tests/test_unified_source_index_forge_code_source.py`
- Work: persist the manifest schema, eligible-line classification and range
  union verifier; distinguish visible inventory/accounted exclusions from the
  direct-line denominator; make partial, stale and failed states explicit.
- Evidence: blanks/comments, syntax boundary, empty file, uncovered-line,
  syntax-unknown generic-fallback, large streaming, parser-failure fallback,
  policy-out-of-scope text and genuine-binary fixtures.
- Done when: `complete` is mechanically impossible with one uncovered eligible
  line, and a policy-excluded source-text file forbids a universal/whole-
  codebase claim even when the selected in-scope denominator is complete.

#### FCA-02-R2 execution override (Round7 Revision 105)

- Status: `architecture_frozen / claim_registration_pending`.
- Frozen packet: `product::FCA-02-r2-detached-semantic-genesis-replay`,
  revision `ABC-FCA02-R2-DETACHED-REPLAY-SOL-ARCH-v1`, digest
  `sha256:926f49740e7608d85b861179c9e58934d821a9300bcf4fe9efb15c75a1daddd6`.
- Exact R2 writer paths only:
  - `src/unified_source_index_code_coverage.py`
  - `tests/test_unified_source_index_code_coverage.py`
- `src/unified_source_index_stores.py`,
  `src/unified_source_index_sources/forge_code.py` and their tests remain
  read-only dependency pins/test-corridor inputs. The historical five-path
  FCA-02 list above does not authorize an R2 writer on those paths.
- R2 rebuilds the rejected candidate around a detached private manifest
  snapshot, one semantic canonical decoder and O(n) ledger replay from genesis.
  It binds the accepted typed `PolicyObservation` structurally without granting
  exclusion, indexing or activation authority.
- One bounded correction applies only to
  `coverage_detached_semantic_decoder_genesis_replay_and_content_free_boundary`;
  recurrence releases the claim blocked and requires new Root/Sol architecture.
- Writer claim `ABC-FCA02-R2-REV106` may start only after exact Revision-105
  Round7/Open Work/Guidance/roadmap blob materialization and readback.

### FCA-03 - Bounded Extraction Profiles And Policy Exclusions

- Dependencies: `FCA-02`, accepted code content-policy handoff.
- Allowed paths:
  - `src/unified_source_index_sources/forge_code.py`
  - `src/unified_source_index_code_coverage.py`
  - `src/rag_text_chunking.py`
  - `tests/test_unified_source_index_forge_code_source.py`
  - `tests/test_unified_source_index_code_coverage.py`
  - `tests/test_rag_text_chunking.py`
- Work: add a versioned code extraction profile with syntax-first and generic
  revision-bound line-chunk fallback, plus bounded/streaming large-file jobs.
  Only policy-out-of-scope and genuine non-text classification may leave the
  direct-line denominator; generated/minified/vendor text, unsupported syntax
  and size are never exclusions. Encoding/read/budget failures remain partial
  or failed with policy evidence, never a successful coverage state.
- Evidence: deterministic profile-version, syntax-unknown fallback, generated/
  minified text, streaming-budget resume, policy-out-of-scope and failure
  fixtures.
- Done when: every in-scope textual file either closes direct-line coverage or
  surfaces a partial/failed reason; no parser/profile/size label can bypass it.

### FCA-04 - Forge Delta, Rename And Tombstone Reconciliation

- Dependencies: `FCA-02`, `FCA-03`.
- Allowed paths:
  - `src/unified_source_index_code_delta.py`
  - `src/unified_source_index_jobs.py`
  - `src/unified_source_index_sources/forge_code.py`
  - `tests/test_unified_source_index_code_delta.py`
  - `tests/test_unified_source_index_jobs.py`
- Work: reconcile add/modify/delete/rename/unchanged facts from a Forge
  predecessor pair, revision-bind reused artifacts and create explicit
  tombstones; fall back to bounded full snapshot reconciliation when needed.
- Evidence: retry/idempotency, lost-signal, rename, revert and delete fixtures.
- Done when: a successor can never inherit coverage from a different revision
  without a new manifest occurrence.

### FCA-05 - Job Admission, Status And Projection Handoff

- Dependencies: `FCA-04`, USI job/embedding lifecycle contracts.
- Allowed paths:
  - `src/unified_source_index_jobs.py`
  - `src/unified_source_index_diagnostics.py`
  - `src/unified_source_index_code_coverage.py`
  - `tests/test_unified_source_index_jobs.py`
  - `tests/test_unified_source_index_diagnostics.py`
- Work: enforce explicit requested jobs, idempotency keys, bounded redacted
  checkpoints and generation-state reporting to lexical/embedding/CBM consumers.
- Evidence: duplicate, stale target, retry, partial and failed job fixtures;
  no source body/path leakage checks.
- Done when: a consumer can distinguish current USI coverage from stale/missing
  projections without triggering a source read or auto-reindex.

### FCA-06 - Query Coverage And Exact-Read Precondition Integration

- Dependencies: `FCA-05`; accepted code-intelligence two-call certification
  protocol; accepted USI query contract; and, only for public-result-field
  handoff, the released UIR query-facade/tool-owner contract. This is a
  document/owner-contract dependency, not a dependency on `UIR-09A`; `FCA-06`
  and `FCA-06A` are upstream of that implementation slice.
- Allowed paths:
  - `src/unified_source_index_query.py`
  - `src/unified_source_index_query_contract.py`
  - `src/unified_source_index_diagnostics.py`
  - `tests/test_unified_source_index_query.py`
  - `tests/test_unified_source_index_query_contract.py`
  - `tests/test_code_intelligence_retrieval.py`
- Work: expose coverage state and only snapshot-bound Forge locators; reject
  mixed generations/revisions and surface partial/stale/failure rather than a
  false complete answer.
- Evidence: complete, partial, stale, mixed-generation and exact-read refusal
  matrix against synthetic repositories.
- Done when: UIR can certify a two-call candidate only when this roadmap's
  manifest precondition is present.

### FCA-06A - Canonical Public `read_file` Forge-Locator Bridge

- Dependencies: `FCA-06`; accepted Forge exact-reader capability from
  `FCA-00`; accepted UIR two-call protocol contract; exact owner handoff for
  the public filesystem-tool/schema and TAX tool-identity hotfiles. `UIR-09A`
  depends on this slice and is not a dependency of it.
- Allowed paths:
  - `src/forge_code_exact_reader.py`
  - `src/agent_tools/filesystem_tools.py` only after its owner handoff
  - `src/tool_schema_definitions.py` only after its owner handoff
  - `src/tool_schemas.py` only after its owner handoff
  - `src/tool_index.py` only after its owner handoff
  - `tests/test_forge_code_exact_reader.py`
  - `tests/test_code_nav_tools.py`
  - `tests/test_tool_index_schema_parity.py`
- Work: retain `read_file` as the sole public exact-read tool identity. Add one
  discriminated Forge-locator mode whose only source selector is the opaque,
  snapshot-bound locator issued by the first USI query call. Internally
  `forge_code_exact_reader` resolves the locator through Forge to its immutable
  repository, revision, path and line range and repeats owner/policy checks.
  The public schema must reject a caller-supplied path, offset/limit, range,
  revision/ref, `HEAD`/branch selector or ordinary-worktree flag whenever
  Forge-locator mode is selected. Ordinary path-based `read_file` behavior,
  including its current range semantics, remains a separate backward-compatible
  mode and cannot accept a Forge locator.
- Evidence: synthetic opaque-locator success plus substituted path, range,
  revision, owner, digest, expired/revoked locator, current-`HEAD`, mixed-mode
  and ordinary-read regression fixtures. Schema/catalog parity proves no
  `read_forge_file`, direct Forge reader or third public tool was exposed.
- Done when: the second public call is exactly `read_file` with the opaque
  locator returned by the first query call; it can return only the Forge-bound
  source range or a fail-closed error, while ordinary `read_file` behavior is
  unchanged and cannot be used to bypass the Forge revision binding.

### FCA-07 - Adapter Acceptance Packet And Parent Gate Contribution

- Dependencies: `FCA-00` through `FCA-06`, `FCA-06A`, selected USI/CBM/UIR integration
  evidence, and a fresh owner/path collision check.
- Allowed paths:
  - `docs/plans/forge-usi-code-source-adapter-acceptance.md`
  - `docs/plans/unified-source-index-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work: record selected repository scope only as an activation proposal,
  manifest/job/generation/test matrix, no-gap results, direct-line denominator,
  per-file exclusions, whole-codebase-claim eligibility, rollback and known
  gaps. Root alone changes the Open Work registration.
- Evidence: all declared focused suites plus manifest fixture digest and
  redacted acceptance packet review.
- Done when: the packet contributes a scoped `Go`, `Partial` or `No-Go` to the
  existing parent gate and never describes unselected repositories as covered.

## 7. Parent Activation And Operator Controls

FCA has no independent productive activation. The only valid production phrase
is a parent-gated, action-specific authorization such as:

```text
GO USI-LIVE-ACTIVATION: activate Forge code adapter <version> for
<approved repo_id/revision-or-ref policy> in <environment>, with
<coverage policy and projection generations>; observe <window>; rollback by
disabling the adapter/query scope and retaining Forge/USI audit evidence.
```

This phrase must name the repository scope, read policy, target revision/ref
policy, adapter/profile versions, coverage status requirement, job limit,
projection-generation behavior, observation window, rollback owner and exact
Forge reader. It grants no repository write, provider sync, commit, branch
change, filesystem crawl, network call or broad reindex.

An optional Lens control is only a later UIR/Lens slice: display a revision,
coverage status, exclusions and generation staleness; submit a confirmed,
bounded index request; show the returned job state. It cannot fabricate a
`complete` badge, configure parser/provider paths, run a background watcher or
invoke direct CBM/Forge tools.

## 8. Required Evidence And Negative Matrix

Before any parent gate contribution, local synthetic evidence must prove:

- fixed revision inventory and exact-read authorization are deterministic;
- every eligible physical line is covered for a `complete` fixture, including
  comments/blank lines and chunk boundaries;
- syntax-unknown source falls back to revision-bound generic line chunks and a
  large textual file completes through bounded/streaming job pages;
- every policy-out-of-scope/non-text inventory item carries an individual
  reason; parser/profile/size failures are partial/failed rather than excluded;
- a policy-excluded source-text file visibly forbids a universal/whole-codebase
  coverage claim;
- modified, renamed, deleted and reverted files yield correct new occurrence,
  tombstone and manifest state;
- retries and lost/delayed signals converge without duplicate occurrences or
  version mixing;
- stale USI and stale embedding/CBM generations are distinguishable;
- a complete USI result can supply a Forge locator, while partial/stale/failed
  states cannot certify complete two-call retrieval; and
- no test starts a real scan, uses a private repository, calls a provider,
  commits, pushes, mutates Forge content or logs raw source unexpectedly.

Minimum planned commands, executed only by the future claimed slices:

```powershell
venv\Scripts\python.exe -m pytest tests\test_unified_source_index_forge_code_source.py tests\test_unified_source_index_code_coverage.py tests\test_unified_source_index_code_delta.py -q
venv\Scripts\python.exe -m pytest tests\test_unified_source_index_jobs.py tests\test_unified_source_index_query.py tests\test_code_intelligence_retrieval.py -q
git diff --check -- docs/plans/forge-usi-code-source-adapter-roadmap.md
```

## 9. Non-Goals And Stop Rules

- No general filesystem crawler, worktree watcher, import-time scan or direct
  Git/provider/network client in USI.
- No Forge repository/version history copied into CBM, Chroma, embeddings or a
  second code store.
- No claim that semantic, symbol or graph search is exhaustive.
- No source mutation, commit, branch, provider sync, restore, delete or
  automatic conflict resolution as part of indexing.
- No actual repository indexing, user data inspection, hidden reindex, live
  activation or Lens UI implementation under this roadmap-authoring claim.
- Stop on an unregistered repository, unresolved/moving revision, missing
  exact-reader authority, policy ambiguity, coverage gap, generation mix,
  hotfile conflict, raw-content leak or a red required synthetic test.

## 10. Current Registration Handoff

This post-MVP child track is centrally registered. `FCA-00`, `FCA-01` and
`FCA-POL-00` are accepted/released repo-only work. The active 12-hour goal has
selected `FCA-02-R2`; its detached-semantic/genesis-replay architecture and
current two-path collision audit are frozen at Round7 Revision 105. The slice
remains unclaimed until exact packet-only router blobs are materialized and
read back, after which Root may register only `ABC-FCA02-R2-REV106`.
Registration and predecessor acceptance grant no productive repository read or
index, provider/runtime activation, source mutation, live action, stage,
commit, push or deploy authority.

## 11. Historical Roadmap-Authoring Handoff

The following block is the time-bound 2026-08-02 authoring handoff. Its
`registration_pending` fields describe that released authoring claim and are
not the current execution frontier stated above.

```yaml
path_slice: FCA-roadmap-authoring
status: roadmap_authored_registration_pending
goal_phase: freeze_plan_points_1_through_4; roadmap_authoring
claim: released_after_independent_deep_review
changed_paths:
  - docs/plans/forge-usi-code-source-adapter-roadmap.md
  - docs/plans/project-versioning-forge-provider-roadmap.md
  - docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md
  - docs/plans/code-intelligence-evaluation-protocol.md
  - docs/plans/unified-source-index-runtime-integration-roadmap.md
  - docs/plans/codebase-memory-integration-roadmap.md
  - docs/plans/lens-code-graph-roadmap.md
evidence:
  target_environment: local
  observed_at: 2026-08-02T23:21:03+02:00
  authority: repo
  mutation_class: repo_only
  required: [static, scoped_diff, cross_track_dependency_review]
  positive_path_required: false
  results:
    - all Markdown fences balanced and no trailing whitespace found
    - scoped git diff --check passed for all tracked roadmap amendments
    - independent final deep review passed after three findings were corrected
    - FCA-to-CBM/UIR-to-LCG dependency readback is acyclic
route:
  entrypoint: abc
  workers: three requested gpt-5.6-terra routes; concrete runtime SKUs not exposed
  review: independent gpt-5.6-sol route requested; concrete runtime SKU not exposed
  actual_model_evidence: surface_default_unknown
completion:
  implemented: na
  tested: na
  independently_deep_reviewed: yes
  committed: no
  pushed: no
  deployed: na
  live_validated: na
  visual_validated: na
  temporal_validated: na
  product_complete: no
remaining_gates:
  - central Open Work and routing registration after the foreign master releases its hotfiles
  - a fresh dependency, ownership and path-collision audit before FCA-00 claim
  - implementation, focused tests, activation packets and action-specific live gates
```
