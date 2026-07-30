# SEC156 - Compose Candidate Official-Provenance Read-Only Authority Packet

## Purpose and current state

This is a repo-only authorization packet for a later, tightly bounded official-
provenance exercise required by SEC155. It performs no research and records no
candidate implementation, version, distribution channel, package, artifact,
provider, source, or recommendation.

The current candidate state is fixed as `unselected`. All current authorities
are false: network, package, host, runtime, provider, deployment, notification,
Git, public-IP, and external-service authority.

| Property | Current value |
| --- | --- |
| Candidate | `unselected` |
| Current action | repo-only packet authoring |
| Future exercise limit | at most one candidate |
| Current execution authority | none |
| Selection authority | absent; durable owner decision required |

## Future instantiation and exact authority to be issued separately

This unfilled template cannot be bound by `go`. Before an owner GO, an
authorized instantiation must set a non-null `evaluation_subject` and an exact
non-empty `approved_origins` allowlist. The subject is the exact implementation
identity nominated only for evidence gathering; it is explicitly not a durable
adoption selection. The instantiation must preserve `candidate_status:
unselected`, use one subject only, and prohibit substitution.

```yaml
schema: odysseus.compose_candidate_provenance_readonly_instantiation.v1
candidate_status: unselected
evaluation_subject: null
approved_origins: []
approved_source_classes: []
request_budget: null
page_budget: null
body_budget: 4
body_byte_cap: 524288
aggregate_inspected_body_byte_cap: 2097152
time_budget_seconds: null
expires_at: null
```

`evaluation_subject` must be an exact implementation identity, not a category,
family, feature request, or search term. `approved_origins` must be an exact
origin allowlist tied to the source classes below, not a wildcard, a parent
domain, or an open-web discovery instruction. Missing or ambiguous subject or
origins is `waiting_on_user` before any request; it cannot be resolved by a
worker search or substitution.

A future authority may be bound only by a plain `go` immediately responding to
the fully instantiated and accepted packet. Binding creates one durable,
one-use, expiring ledger entry before any request. The entry must include the
instantiation, operator, owner, source classes, request and time budgets,
expiry, redaction boundary, and the SEC155 result schema. This unfilled packet
and the current user message are not a GO for the exercise.

The bound exercise may gather evidence for that one, still-unselected subject.
It is limited to unauthenticated, read-only HTTP(S) `GET`, `HEAD`,
domain-restricted `search`, and `open` operations. `search` may query only
within the prelisted approved origins. Every `GET`, `HEAD`, `open`, and redirect
must remain within those same origins. No open-web candidate discovery, login,
cookies, form submission, API write, package manager invocation, binary
execution, clone, download-for-execution, installation, upgrade, removal,
configuration, host access, runtime access, observation, retry, deployment,
notification, public-IP query, staging, commit, push, or other Git action is
authorized.

Permitted source classes are only:

1. official project or vendor documentation;
2. official signed release metadata or official public immutable source; and
3. official operating-system distribution metadata; and
4. official language package index project and release metadata.

The fourth class is permitted only when an accepted instantiation prelists an
exact package-index origin and exact project/release or metadata path boundary.
It permits reading metadata only; it never permits a package-manager
invocation, package download for execution, installation, upgrade, removal,
configuration, or any host action. A package index is not an open discovery
source.

The bound ledger must copy the exact approved origins from the accepted
instantiation before the first request. Redirects may remain only within those
origins and source classes. A redirect outside them, a source whose official
status cannot be established, or a need to authenticate stops the exercise.

## Budgets, boundaries, and audit record

The future ledger must set all of these finite limits before execution:

- one candidate slot and no substitutions;
- at most 12 requests across at most 8 opened or searched pages;
- at most 4 inspected response bodies, with each body capped at 524288 bytes
  and a maximum inspected aggregate of 2097152 bytes;
- at most 3 approved origins;
- at most 10 minutes wall-clock duration; and
- one immutable, non-renewable expiry and one execution attempt.

The 524288-byte per-body cap exists only to inspect a bounded immutable
upstream-source body when that source is within the exact approved boundary;
it is not permission to expand into general-web content. The four-body cap and
2097152-byte aggregate remain hard limits. Only repository-owned code may
validate and reserialize observations into the fixed-key schemas below. Raw
responses, headers, URLs with sensitive query
parameters, command output, package-manager output, provider output, cookies,
credentials, keys, key fingerprints, environment data, and response bodies
must not be persisted, printed, forwarded, or placed in an operator handoff.
The audit/readback is limited to request-count status, origin-class status,
expiry status, fixed enum/boolean fields, and a local evidence reference.

## Required future candidate record

The future exercise must emit exactly this fixed-key record. `null` means not
established; fields must never be guessed from partial evidence.

```yaml
schema: odysseus.compose_candidate_selection.v1
candidate_status: unselected # unselected | eligible | rejected | blocked
implementation_identity: null
supported_distribution_channel: null
entrypoint_provider_chain: null
package_or_artifact_identity: null
version: null
architecture: null
immutable_identity: null
approved_repository_or_channel: null
signature_or_key_verification_mechanism: null
installed_identity_predicates: []
offline_fixture_contract: null
ast_proof_contract: null
decision_authority: null
provenance_evidence: null
rejection_or_block_reason: null
```

Required evidence has these exact semantics:

1. `implementation_identity` and `supported_distribution_channel` identify a
   maintained implementation and its supported distribution method.
2. `entrypoint_provider_chain` names the full `compose_up` entrypoint and every
   delegated provider through the selected implementation; an unknown link is
   a provider-chain gap.
3. `package_or_artifact_identity`, `version`, and `architecture` identify the
   exact target when applicable. An inapplicable field requires authoritative
   justification in the redacted evidence status.
4. `immutable_identity` is an immutable digest/checksum or documented signed
   identity. `approved_repository_or_channel` and
   `signature_or_key_verification_mechanism` state the approved source path and
   verification method without retaining key material.
5. `installed_identity_predicates` contains only bounded booleans or enums,
   including `entrypoint_identity_matches`, `provider_chain_matches`,
   `version_matches`, and `immutable_identity_matches`. It is a future
   post-install readback contract, not permission to install.
6. `offline_fixture_contract` and `ast_proof_contract` bind the selected source
   shape to synthetic offline proof: `compose_up --no-deps` must select the
   service-only branch, while the opposite branch expands dependencies. The
   proof separately establishes service selection, no-build, and
   force-recreate behavior.
7. The Debian `podman-compose` 1.3.0 adverse fixture remains
   `needs_live_observation` with
   `source_up_no_deps_guard_missing`; it cannot be relabeled as success.

## Required future result envelope

```yaml
schema: odysseus.compose_candidate_selection_result.v1
candidate_status: unselected # unselected | eligible | rejected | blocked
required_field_status: not_run # complete | incomplete | conflicting | not_run
provider_chain_status: not_run # complete | unknown | conflicting | not_run
immutable_identity_status: not_run # verified | missing | mutable | not_run
signature_verification_status: not_run # verified | unavailable | failed | not_run
offline_fixture_status: not_run # pass | fail | not_run
ast_no_deps_service_only_status: not_run # pass | fail | not_run
ast_dependency_expansion_status: not_run # pass | fail | not_run
service_selection_status: not_run # pass | fail | not_run
no_build_status: not_run # pass | fail | not_run
force_recreate_status: not_run # pass | fail | not_run
debian_1_3_0_adverse_status: needs_live_observation # needs_live_observation | fail | not_run
debian_1_3_0_missing_proof: source_up_no_deps_guard_missing
evidence_reference: null
stop_reason: null
```

The result must report only fixed-key, redacted status. A successful
provenance read does not perform an offline proof and leaves its proof fields
`not_run` until a later, separately authorized repo-only implementation slice.

## Stop conditions

Stop immediately, consume the one use, and serialize only a bounded `blocked`
or `rejected` result when any of the following occurs:

- before any request, `evaluation_subject` or `approved_origins` is absent,
  empty, wildcarded, or ambiguous: record `waiting_on_user` and make no
  request;
- identity, version, architecture, channel, artifact mapping, or provenance is
  ambiguous, conflicting, mutable-only, or unverifiable;
- the full entrypoint/provider chain cannot be established exactly;
- an approved immutable or signed identity and its verification mechanism are
  unavailable;
- a redirect exits the approved origin, exact prelisted project/release or
  metadata path boundary, or source class;
- a body would exceed 524288 bytes, a fifth body would be inspected, or the
  inspected aggregate would exceed 2097152 bytes;
- source access requires login, cookies, a form, API write, package manager,
  binary execution, clone, installation, or any excluded action;
- a budget or expiry is reached; or
- recording the evidence would require raw output or data outside the redaction
  envelope.

Stopping never authorizes a fallback provider, direct host action, package
mutation, whole-stack workaround, or a second attempt.

## Selection, gates, and recovery

Future read-only evidence cannot select, recommend, install, or authorize a
candidate. Even a complete `eligible` evidence result requires a separate,
durable owner decision selecting exactly one candidate before Gate B can begin.
Gate B is a separately claimed repo-only observer, transport, and offline-test
slice. Publication, package or host change, fresh one-use live observation, C2
readiness, and notification each require their own later gate.

Rollback and recovery are N/A because the exercise is read-only. If it stops,
the recovery action is to preserve the bounded fixed-key audit record, leave
the candidate unselected or blocked, and request a new explicit authority; do
not retry under the expired ledger entry.

## Acceptance criteria

- No candidate, version, channel, provider, or source is named, selected,
  evaluated, or recommended by this packet.
- The unfilled template is not GO-bindable. A future one-use authority binds
  only to a fully instantiated, accepted packet with one exact non-null
  evidence-gathering subject, a non-empty exact origin allowlist, expiry,
  budgets, and audit/readback.
- The evidence-gathering subject is not an adoption selection; candidate status
  remains `unselected` until a separate durable owner decision.
- Only approved official public sources and unauthenticated read-only HTTP(S)
  operations are permitted. Search is restricted to the exact approved origins;
  an official language package index is metadata-only at its exact prelisted
  origin/path and no open-web discovery or package-manager invocation is
  permitted.
- Body inspection is limited to four bodies of at most 524288 bytes each and
  at most 2097152 bytes in aggregate. The larger per-body bound is solely for
  an immutable upstream-source body within the exact allowlist, never general
  web expansion.
- The two SEC155 schemas, all provenance fields, bounded predicates, offline
  proof requirements, and Debian 1.3.0 adverse outcome are preserved.
- Raw output is excluded by a repository-owned fixed-key redaction boundary.
- All current external, package, host, runtime, provider, deployment,
  notification, public-IP, and Git authorities remain false.

## Not verified

No network request, provenance research, candidate evaluation, package action,
host/runtime access, offline implementation, live observation, publication,
or notification ran. No subject or origin allowlist has been instantiated. No
candidate is selected.

## Handoff

Path/slice: `SEC156-compose-provenance-readonly-authority-packet`. Route:
ABC Alice/Terra. Changed path: this document only. Claim: retained for Sol
review. Tests/evidence: static document self-review, diff check, and ASCII
marker scan only. Commit/push: not done and not authorized. Next action: Sol
deep review, then an authorized instantiation that records one exact
evidence-gathering subject and exact approved origins without selecting a
candidate. Only a plain `go` immediately following that fully instantiated,
accepted packet may create the one-use, expiring read-only ledger entry.
Blockers: subject/origin instantiation, candidate adoption selection, and the
separately bound future read-only authority remain absent.
