# SEC157 - Compose Candidate Official-Provenance Read-Only Instantiation

## Decision record

This is the repo-only instantiation required by SEC156. Its exact
`evaluation_subject` is `containers/podman-compose upstream project`.
It is an evidence-gathering nomination only. It is not an adoption decision,
candidate selection, implementation evaluation, recommendation, package
choice, version choice, distribution-channel choice, or provider change.

The candidate state remains `unselected`. One subject is permitted and zero
substitutions are permitted. No other project, fork, package, artifact,
release, version, or provider may be substituted or discovered under this
record.

```yaml
schema: odysseus.compose_candidate_provenance_readonly_instantiation.v1
candidate_status: unselected
evaluation_subject: containers/podman-compose upstream project
approved_origins:
  - https://github.com
  - https://raw.githubusercontent.com
  - https://pypi.org
approved_source_classes:
  - official project or vendor documentation
  - official signed release metadata or official public immutable source
  - official operating-system distribution metadata
  - official language package index project and release metadata
request_budget: 12
page_budget: 8
body_budget: 4
body_byte_cap: 524288
aggregate_inspected_body_byte_cap: 2097152
origin_budget: 3
time_budget_seconds: 600
attempt_budget: 1
retry_budget: 0
expires_at: null
```

## Exact origin and path boundary

The exact approved origins are, and only are:

1. `https://github.com`, exact path `/containers/podman-compose` or a
   descendant beginning `/containers/podman-compose/`;
2. `https://raw.githubusercontent.com`, path prefix
   `/containers/podman-compose/`, followed by an immutable full Git commit SHA
   path segment matching `^[0-9a-fA-F]{40}$` and then a non-empty source path;
   branches, tags, abbreviated SHAs, and generic refs are never accepted; and
3. `https://pypi.org`, path prefix `/project/podman-compose/`, and exact
   metadata path `/pypi/podman-compose/json` with no descendant path.

Path matching must occur before a response body is inspected. GitHub accepts
only the exact repository path or descendants beginning with its trailing slash;
lexical siblings such as `/containers/podman-compose-evil` are rejected. Raw
GitHub accepts source evidence only when its path contains the required full
commit SHA segment and a non-empty remaining source path. A redirect must
remain within one of these exact origin/path pairs. The source class must also
match the SEC156 allowlist: PyPI is limited to `official language package index
project and release metadata` at either the exact project-path boundary or the
exact `/pypi/podman-compose/json` metadata path and never permits a
package-manager invocation. A parent-domain page, wildcard, search result
outside the listed path boundaries, external redirect, mirror, login, cookie,
form, API write, or ambiguous official status stops the exercise.

## Future action and one-use binding

No request is authorized now. A separate future plain `go`, immediately
responding to this accepted instantiation, is the only action that may create a
one-use provenance ledger. Before its first request, that ledger must bind this
exact document, the exact subject, every origin and path prefix, source
classes, all budgets, an immutable non-null expiry, operator, owner, and the
SEC155 record/result envelopes below. It may then perform only unauthenticated,
read-only, domain-restricted HTTP(S) `search`, `open`, `GET`, or `HEAD` within
the exact allowlist. There is no open-web discovery.

That future ledger permits at most 12 requests, 8 searched or opened pages, 4
inspected bodies, 524288 bytes per body, and 2097152 inspected bytes in
aggregate, 3 origins, 600 seconds, one attempt, and zero retries. The
524288-byte per-body bound exists solely for a bounded immutable upstream-source
body within the exact allowlist; it is not a general-web expansion. It expires
after one use and cannot be renewed, broadened, or reused. An error, boundary
violation, budget exhaustion, expiry, ambiguous evidence, or required excluded
action consumes the attempt and stops it.

The current user `go` is consumed solely for this repo-only instantiation. It
is not reusable for a provenance request, candidate selection, package action,
host action, runtime observation, public-IP query, deployment, notification,
Git action, or any other external action.

## Redaction and evidence boundary

Only a repository-owned validator may transform allowed observations into the
fixed-key envelopes below. The four-body and 2097152-byte aggregate limits
remain mandatory even where the immutable-source body cap is used. Do not
print, persist, forward, or hand off raw
responses, response bodies, headers, URLs with query values, provider output,
package-manager output, command output, cookies, credentials, keys, key
fingerprints, environment data, or unbounded exception text. The only allowed
recorded values are fixed enums and booleans, bounded aggregate counters,
approved-origin and path-class status, expiry status, and an internal evidence
reference.

The later result must bind exactly to the SEC155 schemas
`odysseus.compose_candidate_selection.v1` and
`odysseus.compose_candidate_selection_result.v1`. Their field sets and enum
meanings may not be broadened. `null` means not established and is never a
license to infer a value.

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

The later evidence process must preserve the SEC155 offline-fixture and AST
contracts: `compose_up --no-deps` must take the service-only branch and the
opposite branch must expand dependencies; service selection, no-build, and
force-recreate are separate proof fields. Debian `podman-compose` 1.3.0 remains
an adverse `needs_live_observation` fixture with
`source_up_no_deps_guard_missing`; it cannot be called a success.

## Stop rules and authority status

Stop before any request if the future one-use ledger is absent, expired,
ambiguous, missing a required bound field, or not immediately authorized by the
next plain `go`. Stop during the exercise for any provider-chain gap,
unverifiable immutable or signed identity, mutable-only evidence, invalid raw
GitHub full-SHA path, GitHub lexical sibling, PyPI path outside the exact
project or `/pypi/podman-compose/json` metadata boundary, path or origin escape,
redaction-boundary violation, body cap, aggregate cap, other budget limit, or
need for an excluded action.
Serialize only the bounded fixed-key blocked or rejected result. Do not retry,
select a fallback, use direct Podman, alter a host, or attempt a whole-stack
workaround.

All current authority values are false: external, network, package, host,
runtime, provider, live observation, public-IP, deployment, notification/send,
and Git. No source was opened and no provenance was evaluated.

## Acceptance

- The subject is exactly `containers/podman-compose upstream project`.
- Candidate status remains `unselected`; nomination is not selection or
  recommendation.
- The three approved origins, their exact path prefixes, source classes, and
  all finite SEC156 budgets are recorded without expansion.
- GitHub accepts only `/containers/podman-compose` or descendants beginning
  `/containers/podman-compose/`; lexical siblings are rejected. Source evidence
  from raw GitHub requires a full immutable 40-hex Git commit SHA segment and a
  non-empty source path; branches, tags, abbreviated SHAs, and generic refs are
  rejected.
- PyPI is limited to the exact `/project/podman-compose/` path boundary and
  the exact `/pypi/podman-compose/json` metadata path, within the exact
  `official language package index project and release metadata` source class;
  no package-manager invocation is permitted.
- Body inspection is limited to four bodies of at most 524288 bytes each and
  at most 2097152 bytes in aggregate. The larger per-body bound is solely for
  a bounded immutable upstream-source body inside the exact allowlist, never
  general-web expansion.
- A future exercise requires a new one-use expiring ledger and immediate plain
  `go`; the current `go` is consumed and cannot authorize it.
- The SEC155 fixed-key record/result schemas, Debian adverse fixture, and
  redaction boundary are preserved.
- No external, package, host, runtime, provider, live, public-IP, deploy, send,
  or Git authority is granted.

## Not verified

No network request, search, open, HTTP request, provenance reading, candidate
evaluation, version determination, package action, host access, runtime access,
observation, deployment, notification, Git action, or selection ran. No
candidate fact has been established.

## Handoff

Path/slice: `SEC157-compose-provenance-readonly-instantiation`. Goal and
phase: bind the SEC156 evidence-gathering nomination and source-class boundary;
repo-only decision record. Claim: retained for Sol deep review. Changed paths:
this document and the SEC156 packet only. Tests/evidence: ASCII/marker/trailing
whitespace scan and untracked-aware path diff pending at handoff. Commit/push:
not done and not authorized. Actual worker route:
Alice/Terra. Next action: Sol deep review, then only a separately authorized
plain `go` immediately responding to this accepted record may create the
one-use read-only provenance ledger. Blockers: that future authority, evidence
collection, durable owner candidate selection, offline implementation, and all
later live gates remain absent.
