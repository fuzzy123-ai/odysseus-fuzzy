# SEC133 malformed-version-output recovery packet

Run: `ABC-SEC133-20260729-MALFORMED-OUTPUT-RECOVERY`

Status: `repo_only_pending_publication`. This packet grants no SSH, network,
deploy, build, pull, checkout, container, backup, restore, send, or other
live action.

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

## Future action (not authorized)

The exact six-path SEC133 publication set must first be reviewed and published:

- `docs/plans/security-incident-response-malformed-output-recovery-packet.md`
- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`
- `docs/plans/security-incident-response-production-completion-roadmap.json`

Only after a publication record binds the resulting `fuzzy/dev` revision and
these byte hashes may the owner issue a separate plain-language,
action-specific approval for exactly one no-argument execution of the fixed
transport runner. That later approval must retain the 15-second remote limit,
the 25-second aggregate subprocess budget inside a 30-second operator window,
`RUN_END` expiry, and no retry. It grants neither deploy authority nor any
other gate, even when the observer returns `ok`.

## Handoff

Next safe action: deep review and an exact path-scoped publication packet. The
existing SEC132 observation authority is spent; this recovery packet is not a
replacement live GO.
