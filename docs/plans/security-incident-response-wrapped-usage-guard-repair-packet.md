# SEC138 wrapped-usage and exclusion-guard repair packet

Run: `ABC-SEC138-20260729-WRAPPED-USAGE-GUARD-REPAIR`

Status: `repo_only_deep_review_passed_publication_not_authorized`. This packet grants no publication,
network, SSH, probe, live observation, retry, deploy, delivery, package,
container, host, provider, backup or restore action.

## Terminal predecessor evidence

SEC137 was published as commit
`d91b2fb695b32c57235c971e47d4f50e5d7bbb86` with tree
`241fc79e8adf0383c065fbf9ee95dc880bbab107`. Its one-use redacted observation
ran exactly once and returned `status=needs_live_observation`,
`retry_permitted=false`, with evidence digest
`5c93b83f9c3c5ed7296d2eba245f9d3ab89ff0eaf80285a78b5c4a283ea8f555`.

The Boolean-only profile proves:

- build and up each have a usage line, but none of the three service grammars
  occurs on the currently inspected single line;
- `compose_build`, `compose_up`, `get_excluded`, all helper links, the exact
  helper assignment and the `compose.containers` loop exist;
- only the excluded-service continue-guard recognizer and the genuine no-deps
  dependency-control proof remain false.

The SEC137 live grant is spent and must not be retried.

## Bounded repair contract

The help recognizer may inspect one bounded usage block per subcommand: the
matching `usage:` line plus only its immediately following indented
continuation lines, with fixed line and character limits. It may recognize the
existing uppercase `SERVICE` grammar and exact lowercase `[services ...]`
grammar anywhere inside that bounded block. It must not consume descriptions,
option help, later headings, unrelated usage blocks or arbitrary occurrences of
`services`. Raw help must never be emitted.

The source recognizer may accept the exact excluded-service guard when:

- the `if` condition remains exactly the current
  `container["_service"] in excluded` comparison;
- there is no `else`;
- the final branch statement is an unconditional `continue`;
- every preceding branch statement is a non-control expression, matching the
  public 1.3.0 logging-before-continue shape.

It must reject nested, conditional or unreachable continue variants, wrong
container bindings, wrong excluded variables, `else` branches and any branch
without a final unconditional continue.

The real `compose_up --no-deps` semantic proof remains false. Neither repair may
change capability-pass semantics beyond the two evidence-backed recognizer
gaps.

## Repo-only acceptance

Acceptance requires wrapped positive fixtures for build and up, adversarial
boundary/description/heading tests, exact public-shape guard fixtures,
near-miss negative tests, unchanged Boolean-only profile and schemas, unchanged
no-deps blocker, Python compilation, focused tests, `git diff --check`, and
independent root/Sol deep review.

Worker paths:

- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`

Root paths:

- `docs/plans/security-incident-response-production-completion-roadmap.json`
- `docs/plans/security-incident-response-wrapped-usage-guard-repair-packet.md`

No commit, push or later observation is authorized by this packet.

## Durable SEC138 handoff

The Bob/Terra implementation was accepted after three review rounds. Root/Sol
found and returned two false-positive classes during deep review: indented prose
containing an otherwise valid service token, and an over-limit matching usage
header. Both now fail closed.

Changed implementation paths:

- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`

The claimed transport test path remained unchanged. Independent verification
passed Python compilation, `31` focused tests, `git diff --check`, and exact
observer-to-transport SHA-256 binding
`af8e688ae86e1406a55f51d521f746d15b9300d399caeaa4698e53bd133bd46c`.
The Boolean-only schemas are unchanged and the real `compose_up --no-deps`
semantic proof remains false.

Both SEC138 claims are released. The next frontier is an exact `fuzzy/dev`
publication candidate, but this packet contains no commit or push authority.
Any later redacted observation needs a separate action-specific live grant and
must not reuse the spent SEC137 grant.
