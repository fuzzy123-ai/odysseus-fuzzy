# SEC133 malformed-version-output recovery packet

Run: `ABC-SEC133-20260729-MALFORMED-OUTPUT-RECOVERY`

Status: `published_single_observation_terminal_blocked_no_retry`. The exact
six-path candidate was published as commit
`6a0776cf577959d6aeff791efcd4ddb9f37c17ee` with tree
`65539b2cbde9f899818f51f01d721a7c5db21cd4`. This packet grants no further
SSH, network, deploy, build, pull, checkout, container, backup, restore, send,
or other live action.

## SEC132 terminal evidence and bounded recovery

SEC132 was published in commit
`9e2afe207438c6c2efa46ab8c5c0a71c7b3158ad`. Its single transport invocation
returned a validated observer terminal envelope with `status=blocked`,
`error_code=malformed_output`, `retry_permitted=false`, and a valid canonical
digest. No raw cause is claimed, retained, or retried.

Public upstream design evidence documents that Podman Compose 1.3.0 can render
its compose version line followed by a Podman version line. That evidence is
not treated as proof of the host's raw output. SEC133 therefore changes only
the fixed parser: it accepts exactly one allowed Compose 1.3.0 line and at
most one syntactically bounded Podman version line. Order changes, duplicate or
extra lines, empty lines, ANSI/control characters, malformed optional Podman
lines, and all other renderings remain terminal `malformed_output`; a
well-formed non-1.3.0 Compose line remains terminal `version_mismatch`. The
Podman version is never represented in the returned evidence.

## Publication-safe observer binding

The revised observer byte SHA-256 is:

`e534ec2e43c6b2d77245e3b2e1ad7f083bbf7e8200cb7dea1026dfaec3318509`

The transport no longer contains a self-referential commit pin. It reads only
`refs/remotes/fuzzy/dev:ops/homeserver/redacted_podman_compose_capability_observation.py`
and verifies that exact SHA-256 before opening SSH. Before the SEC133 artifact
set is published, or after any blob drift, it fails closed with its separate
redacted terminal envelope and makes no SSH call.

## Terminal live outcome

The separately approved transport was invoked exactly once after publication.
It returned the validated terminal envelope `status=blocked`,
`error_code=malformed_output`, `retry_permitted=false`, with evidence digest
`08bae6f55bcd382fb61c787fcde8c7a4d5f9ddf8ebae6655736d09ab189d8a47`.
No retry occurred. The digest covers the canonical terminal envelope only; it
does not fingerprint or distinguish the discarded raw command output, so no
raw cause is claimed.

## Completed publication

The exact six-path SEC133 publication set must first be reviewed and published:

- `docs/plans/security-incident-response-malformed-output-recovery-packet.md`
- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`
- `docs/plans/security-incident-response-production-completion-roadmap.json`

The publication record binds `fuzzy/dev` revision
`6a0776cf577959d6aeff791efcd4ddb9f37c17ee` and the hashes above. Its
single-use observation authority is spent and cannot be reused. It granted
neither deploy authority nor any other gate.

## Handoff

Next safe action: do not retry SEC133. If work resumes, first create and
deep-review a repo-only fixed-schema diagnostic classifier that distinguishes
bounded version-output shape classes without retaining raw output. A later
observation requires a new action-specific approval. Deploy remains
independently gated.
