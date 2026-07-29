# SEC137 runtime-shape diagnostic packet

Run: `ABC-SEC137-20260729-RUNTIME-SHAPE-DIAGNOSTIC`

Status: `exact_publication_and_single_read_only_observation_authorized`. Deploy,
delivery, package upgrade, container, host, provider, backup and restore remain
unauthorized.

## Terminal predecessor evidence

SEC136 was published as commit
`4f0383f79fa75b167872d4e34c62b90e16831007` with tree
`aeb2af69dceb57582a9b76f42cbc337c830aa373`. Its separately authorized
published-blob observation ran exactly once and returned the validated terminal
envelope `status=needs_live_observation`,
`reason_code=semantic_proof_insufficient`, `retry_permitted=false`, with
evidence digest
`d4bce89cd5f58eb465a5e232e12cb423bab9f517bffbcf4e80c425b0eafdc5bf`.
It retained exactly four missing proofs:

- `build_service_argument_missing`
- `up_service_argument_missing`
- `source_up_service_selection_missing`
- `source_up_no_deps_guard_missing`

The SEC136 live grant is spent and must not be retried.

## Diagnostic contract

SEC137 may extend only the existing `needs_live_observation` envelope with one
fixed-schema, boolean-only runtime-shape profile. The profile must distinguish
the conservative recognizer boundaries without exposing raw help, source,
paths, names, values, fragments, prefixes, suffixes, lengths, hashes, counts,
environment data, exceptions or stderr.

The profile must cover:

- whether a usage line exists for each selected build/up subcommand;
- whether that usage line contains the exact existing uppercase positional
  grammar, the exact bracketed lowercase `[services ...]` grammar, or a bounded
  bare lowercase `services` positional token;
- whether top-level `compose_build`, `compose_up` and `get_excluded` handlers
  exist in the inspected AST;
- each individual link of the bounded exclusion-helper proof: exact signature,
  empty-set initialization, `args.services` branch, `compose.services` set,
  requested-service loop, dependency lookup/subtraction, selected-service
  discard, exact `compose_up` helper assignment, `compose.containers` loop and
  excluded-service `continue` guard;
- whether `compose_up` contains a proven no-deps dependency-control branch.

Every key and nesting level must be allowlisted and exact. All leaves must be
literal booleans. The canonical digest must cover the profile. The transport
must reject missing, extra, reordered where order is contractual, non-boolean,
unknown, oversized, malformed or digest-inconsistent variants without
forwarding raw subprocess output.

The existing `ok`, `blocked`, visibility and capability semantics remain
unchanged. The diagnostic profile cannot make
`deployment_capability_supported` true and cannot convert missing semantic
proof into a pass.

## Repo-only acceptance

Acceptance requires positive and near-miss tests for every fixed boolean,
strict transport validation, unchanged success and blocked envelopes,
canonical digest verification, Python compilation, focused tests,
`git diff --check`, and independent root/Sol deep review.

Worker implementation is limited to:

- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`

Root integration is limited to this packet and
`docs/plans/security-incident-response-production-completion-roadmap.json`.

No publication or later live observation is authorized by this packet.

## Accepted deep review

The Bob/Terra handoff changed only the four claimed implementation and test
paths. Root/Sol independently verified the exact nested keysets, literal
Boolean leaves, digest binding, fixed-size transport validation, unchanged
`ok` and `blocked` envelopes, and the no-false-pass boundary.

Python compilation and `git diff --check` passed. The two focused test files
passed 28 tests. An independent exhaustive mutation check covered every one of
the 22 profile leaves and confirmed observer and transport rejection for both
a non-Boolean replacement and a missing key: 44 mutation cases passed. The
observer SHA-256 is
`19be7e373b58ef55928bd2139b9963b96f46352a07f742a5ed7a2235970917e6`;
the transport pin matches it. Both SEC137 claims are released.

SEC137 is locally implemented and deep-reviewed, but it is not committed or
published. No live action occurred. Publication needs separate exact
authority; any later observation needs a new action-specific, single-use,
no-retry live Go.

## Current bounded authority

The operator's immediate instruction `dann mach weiter` authorizes only the
previously stated next sequence:

1. publish the exact seven-path SEC137 candidate once to `fuzzy/dev`;
2. verify the published revision and observer SHA-256;
3. run the no-argument redacted published-blob transport exactly once, with a
   30-second outer limit, no follow-on query and no retry.

The one-use live ledger entry is
`SEC137-RUNTIME-SHAPE-OBSERVATION-20260729` and expires at `RUN_END`. It grants
no deploy, delivery, send, package or host action.
