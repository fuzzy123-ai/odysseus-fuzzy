# SEC136 Podman Compose 1.3.0 recognizer-repair packet

Run: `ABC-SEC136-20260729-COMPOSE-RECOGNIZER-REPAIR`

Status: `deep_reviewed_pending_exact_publication`. This packet grants no SSH,
network, probe, deploy, build, pull, checkout, container, backup, restore,
send, package upgrade, or other live action.

## Evidence-backed boundary

SEC135 published commit
`2c51fd349843d05e200c6d14e717e78a01e7e9f1` and consumed one separately
authorized read-only observation. The validated fixed-enum result identified:

- `build_service_argument_missing`
- `up_service_argument_missing`
- `source_up_service_selection_missing`
- `source_up_no_deps_guard_missing`

Public Debian Podman Compose 1.3.0 source establishes that:

- the shared build/up parser declares positional `services` with lowercase
  `metavar="services"` and `nargs="*"`;
- `compose_up` assigns `excluded = get_excluded(compose, args)` and skips
  containers whose service is in that exclusion set;
- `get_excluded` consumes `args.services` and includes selected services plus
  their declared dependencies;
- `compose_up` does not consume `args.no_deps`; the only 1.3.0 consumption of
  that flag is in `compose_run`.

Source:
`https://sources.debian.org/src/podman-compose/1.3.0-1/podman_compose.py/`

SEC136 may therefore repair the first three conservative recognizers. It must
not claim that `compose_up --no-deps` excludes dependencies. Under an official
1.3.0-shaped source fixture, the honest terminal result remains
`missing_proofs=["source_up_no_deps_guard_missing"]`.

## Required recognizers

The help recognizer may accept only explicit positional service grammar on the
usage line after the selected subcommand. It may support the existing uppercase
`SERVICE` grammar and the official lowercase `services` / `[services ...]`
grammar, but not descriptive occurrences.

The source audit may mark Up service selection proven only when all bounded AST
conditions hold:

- `compose_up` calls the fixed `get_excluded` helper with `compose` and `args`;
- the helper branches on `args.services` and constructs the selection/exclusion
  set using those requested services;
- `compose_up` uses the resulting exclusion value to guard service/container
  processing.

The no-deps proof remains false unless `args.no_deps` actually controls
dependency inclusion or exclusion in the `compose_up` call graph. Parser
presence alone is never semantic proof.

## Repo-only acceptance and publication scope

Acceptance requires official-shape positive tests for the three recognizer
repairs, near-miss negative tests for helper identity, call arguments, service
branching, exclusion guards and no-deps consumption, unchanged fixed schemas
and privacy boundaries, Python compilation, diff checking, focused tests, and
root/Sol deep review.

The exact publication candidate is limited to:

- `docs/plans/security-incident-response-production-completion-roadmap.json`
- `docs/plans/security-incident-response-semantic-proof-diagnostic-packet.md`
- `docs/plans/security-incident-response-compose-recognizer-repair-packet.md`
- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`

Publication and any later observation remain unavailable until deep review
binds the exact revision, tree, path inventory and observer SHA-256. A later
observation is limited to one no-argument redacted published-blob run, expires
at `RUN_END`, and permits no retry. It grants no deploy or delivery authority.

## Accepted deep review

The replacement Bob/Terra handoff changed only the four claimed implementation
and test paths. Root/Sol independently checked the official Debian 1.3.0 helper
and `compose_up` AST shape, the positional help grammar, every included
near-miss case, the fixed output schemas and the no-false-pass boundary.

Python compilation passed, `git diff --check` passed, and the two focused test
files passed 24 tests. The official-shape fixture terminates honestly with
`status=needs_live_observation`,
`missing_proofs=["source_up_no_deps_guard_missing"]`, and
`retry_permitted=false`. The observer worktree SHA-256 is
`2ceb8cede61732895f6da336b2762db3a7599d7adbbeeaf273c42a288b00a56b`;
the transport pin matches it. Both active SEC136 claims are released.

Next safe action: bind and publish only the exact seven-path candidate to
`fuzzy/dev`, verify the published revision and observer hash, and then consume
at most one no-argument redacted published-blob observation without retry.
Deploy, delivery, package upgrade, container and host mutation remain
unauthorized.

## Terminal publication and live outcome

The accepted seven-path candidate was published as commit
`4f0383f79fa75b167872d4e34c62b90e16831007` with tree
`aeb2af69dceb57582a9b76f42cbc337c830aa373`. `fuzzy/dev` readback confirmed
that revision and the observer SHA-256
`2ceb8cede61732895f6da336b2762db3a7599d7adbbeeaf273c42a288b00a56b`.

The separately authorized published-blob transport then ran exactly once. Its
validated terminal envelope was `status=needs_live_observation`,
`reason_code=semantic_proof_insufficient`, `retry_permitted=false`, with
evidence digest
`d4bce89cd5f58eb465a5e232e12cb423bab9f517bffbcf4e80c425b0eafdc5bf`.
It still identified exactly:

- `build_service_argument_missing`
- `up_service_argument_missing`
- `source_up_service_selection_missing`
- `source_up_no_deps_guard_missing`

No retry occurred. The observation grant is spent. The published code and its
offline tests remain valid evidence of the bounded recognizer implementation,
but the unchanged live envelope proves that the actual runtime help grammar
and/or inspected source AST still does not satisfy those recognizers. No
capability PASS, deploy readiness or delivery readiness is claimed.

Next safe action: do not retry SEC136. First create and deep-review a new
repo-only fixed-enum diagnostic contract that distinguishes the actual runtime
help-grammar and source-AST shape mismatches without exposing raw output,
source, paths, environment, exceptions or values. Any later observation needs
new action-specific authority. Package upgrade, deploy and delivery remain
independently gated.
