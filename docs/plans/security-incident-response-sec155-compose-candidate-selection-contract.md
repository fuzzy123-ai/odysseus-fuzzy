# SEC155 - Compose Candidate Selection and Evidence Contract

## Status and scope

This is a **repo-only decision contract** for the later, supported Compose
replacement path identified by SEC154. It deliberately records no candidate
implementation, version, distribution channel, repository, package, artifact,
provider, or recommendation.

The candidate slot is fixed as `unselected`. Filling it, researching sources,
or evaluating a real artifact is outside this slice and requires the separate
read-only authority defined below.

| Contract property | Current value |
| --- | --- |
| Candidate selection | `unselected` |
| Mutation authority | `repo_only` |
| Network, package, host, runtime, provider, deployment, send, and Git authority | `false` |
| Runtime evidence | not claimed |

## Non-goals

- Selecting, naming, researching, or recommending an implementation, version,
  vendor, channel, package, or artifact.
- Querying a registry, package manager, vendor site, host, container runtime,
  public-IP service, or provider.
- Installing, upgrading, removing, configuring, or invoking any package or
  Compose implementation.
- Retrying SEC153 or changing the fail-closed observer/transport behavior.
- Staging, committing, pushing, deploying, or sending notifications.

## Candidate record schema (for a future, separately authorized evaluation)

A later evaluation must produce one fixed-key record. Unknown values remain
`null`; they must never be inferred from a partial source.

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

`candidate_status` may be only `unselected`, `eligible`, `rejected`, or
`blocked`. `eligible` is prohibited until a future, separately authorized,
read-only provenance exercise has produced complete evidence for every required
field below. A missing, conflicting, mutable, or unverifiable field makes the
record `blocked` or `rejected`; it never permits a best-effort substitution.

## Required evidence fields and predicates

For a proposed candidate, the later record must contain all of the following:

1. **Exact implementation identity and supported distribution channel.** State
   the executable or implementation identity together with the maintained,
   supported way it is distributed. The full user-facing `compose_up` entry
   point and every delegated provider in its resolution chain must be explicit;
   an opaque wrapper or unknown delegation fails the contract.
2. **Exact artifact identity.** Record package or artifact identity, version,
   and target architecture when applicable. If a field is not applicable, the
   authoritative source must say why; it cannot be silently omitted.
3. **Immutable provenance.** Bind the artifact to either an immutable digest or
   checksum, or to a documented signed-identity mechanism. Record the approved
   repository/channel and the signature/key verification mechanism. Do not
   store keys, fingerprints, raw provider responses, credentials, paths that
   expose private data, or unredacted command output.
4. **Bounded installed-identity predicates.** Define fixed boolean or enum
   predicates sufficient to confirm the selected identity after a later
   installation, for example `entrypoint_identity_matches`,
   `provider_chain_matches`, `version_matches`, and
   `immutable_identity_matches`. Their readback must be redacted and bounded;
   no raw environment, package-manager, provider, or subprocess output is an
   acceptable evidence format.
5. **Offline synthetic-source fixture.** Supply a synthetic, non-production
   source fixture representing the resolved provider behavior. It must be
   self-contained, non-networked, and contain no secrets, real credentials, or
   copied raw provider output. It must exercise the exact source or AST shape
   selected by the evidence record rather than a semantic look-alike.
6. **Deterministic AST proof.** An offline test must prove that
   `compose_up --no-deps` reaches a service-only branch and that the opposite
   dependency-enabled branch expands dependencies. The proof must assert both
   branch predicates and the corresponding service/dependency call paths; a
   text search, successful import, or command return code is insufficient.
7. **Operational intent proof.** The same offline proof must establish service
   selection, no-build behavior, and force-recreate behavior for the selected
   branch. Each predicate is independently required; no aggregate 'compose
   works' assertion is acceptable.
8. **Adverse compatibility fixture.** Retain the Debian `podman-compose`
   `1.3.0` synthetic adverse fixture. Its expected result remains
   `needs_live_observation` with the exact missing proof
   `source_up_no_deps_guard_missing`. The candidate evidence must not relabel
   that known unsupported provider as a successful replacement.

## Result and evidence envelope

All future provenance and fixture results must be reserialized through a
fixed-key, redacted envelope before they enter the repository or an operator
handoff:

```yaml
schema: odysseus.compose_candidate_selection_result.v1
candidate_status: unselected # unselected | eligible | rejected | blocked
required_field_status: complete # complete | incomplete | conflicting | not_run
provider_chain_status: complete # complete | unknown | conflicting | not_run
immutable_identity_status: verified # verified | missing | mutable | not_run
signature_verification_status: verified # verified | unavailable | failed | not_run
offline_fixture_status: pass # pass | fail | not_run
ast_no_deps_service_only_status: pass # pass | fail | not_run
ast_dependency_expansion_status: pass # pass | fail | not_run
service_selection_status: pass # pass | fail | not_run
no_build_status: pass # pass | fail | not_run
force_recreate_status: pass # pass | fail | not_run
debian_1_3_0_adverse_status: needs_live_observation # needs_live_observation | fail | not_run
debian_1_3_0_missing_proof: source_up_no_deps_guard_missing
evidence_reference: null
stop_reason: null
```

Values illustrated as `complete`, `verified`, or `pass` describe the required
shape for a later successful result; this slice has not produced such a result.
Until a future evaluation runs, the current contract has
`candidate_status: unselected`; it makes no fixture or provenance result claim.

## Stop conditions

Stop the future evaluation and set the record to `blocked` when any of these
conditions occurs:

- implementation identity, provider chain, distribution support, architecture,
  or artifact/version mapping is ambiguous or conflicts across authoritative
  sources;
- evidence would require persisting or forwarding raw provider, package-manager,
  subprocess, environment, credential, or key material;
- any delegated provider or `compose_up` entry-point link cannot be identified
  exactly;
- provenance is unpinned, mutable-only, unsigned without an approved
  verification mechanism, or cannot be bound to a digest/checksum or signed
  identity;
- the offline fixture cannot prove all required behavioral predicates above; or
- the adverse Debian `1.3.0` fixture does not retain its exact fail-closed
  outcome.

Stopping is evidence of a blocked candidate, not permission to fall back to
direct host actions, a whole-stack workaround, or an in-place system mutation.

## Gates and authority boundaries

### Gate A - candidate provenance and owner decision

Before any candidate can become `eligible`, obtain a new, explicit, read-only
authority for a bounded official-provenance exercise. That authority must name
the approved source class, permitted read-only network operations, one
candidate-evaluation limit, redaction boundary, time window, and the exact
result envelope above. It must not authorize installation, package changes,
host access, runtime observation, provider mutation, deployment, sending, or
Git publication.

After redacted provenance evidence is complete, a durable owner decision must
select exactly one candidate record. No worker may infer the selection from the
evidence.

### Gate B - offline implementation and test scope

Only after the durable owner selection decision may a separate repo-only claim
modify these paths:

- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`

That claim must implement the synthetic fixture and deterministic AST proofs
above, preserve the existing fail-closed behavior, and keep the Debian `1.3.0`
negative fixture. It grants no package, host, network, live-observation,
deployment, notification, or Git authority.

### Subsequent gates

Publication, package/host change, fresh one-use observation, C2 readiness,
and any notification each remain separately gated. A successful offline proof
does not authorize any of them.

## Acceptance criteria for this SEC155 slice

- The document remains candidate-neutral with selection fixed to `unselected`.
- The future evidence record has fixed keys and only the four allowed statuses.
- Every provenance, provider-chain, immutable-identity, bounded-readback,
  synthetic-fixture, AST, operational-intent, and adverse-fixture requirement
  above is explicit.
- `compose_up --no-deps` service-only behavior and the opposite dependency
  expansion are independently testable offline.
- Service selection, no-build, and force-recreate are independently testable
  offline.
- Debian `podman-compose` `1.3.0` remains
  `needs_live_observation/source_up_no_deps_guard_missing`.
- All external, host, runtime, package, provider, notification, deployment,
  and Git authorities remain `false`.

## Handoff

Path/Slice: `SEC155-compose-candidate-selection-contract`; status: drafted for
Sol deep review. Claim: remains held by Alice until parent integration releases
it. Changed files: this document only. Commit/push: not done; no publication
authority. Tests/evidence: static document self-review only; no runtime,
provenance, package, host, or live evidence claimed. Route: ABC Alice/Terra;
repo-only contract authoring. Completion: contract drafted, but candidate
selection, implementation, publication, host change, and live validation are
not complete. Next claimable action: a parent-owned review and, only after that,
a separately authorized read-only provenance decision packet. Blockers: owner
selection and future read-only provenance authority are intentionally absent.
