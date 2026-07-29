# SEC134 bounded version-output diagnostic packet

Run: `ABC-SEC134-20260729-VERSION-OUTPUT-DIAGNOSTIC`

Status: `accepted_repo_only_deep_reviewed_pending_publication`. This packet
grants no SSH, network, probe, deploy, build, pull, checkout, container,
backup, restore, send, or other live action before the exact publication
binding and readback are complete.

## Bounded strategy change

SEC132 and SEC133 each consumed one separately approved read-only observation
and ended terminally with `malformed_output`; neither grant permits a retry.
Their canonical evidence digest covers the redacted terminal envelope only and
does not identify the discarded raw output.

The Debian `podman-compose` 1.3.0 source defines the fixed read-only
`version --short` command and emits only its `__version__` value on that path:

`https://sources.debian.org/src/podman-compose/1.3.0-1/podman_compose.py/`

SEC134 therefore replaces the ambiguous non-short version command, which also
invokes Podman version output, with exactly:

`podman-compose version --short`

The only accepted positive rendering is the single line `1.3.0`. A well-formed
different semantic version is terminal `version_mismatch`. Empty, control-
character, multiline, and other malformed renderings map to fixed allowlisted
terminal diagnostic codes. No raw value, substring, length, hash, suffix,
prefix, line content, stderr, exception, environment, path, hostname, source,
or secret-derived metadata may be emitted or retained.

## Repo-only acceptance

The implementation must:

- preserve fixed commands, bounded timeouts, `PATH=/usr/bin:/bin`, discarded
  stderr, and the existing source-audit and capability checks;
- validate only fixed-schema envelopes with canonical digests and
  `retry_permitted=false` on every blocked result;
- keep the transport pinned to the exact published observer bytes before SSH;
- cover every diagnostic enum, transport rejection, positive path, and
  well-formed version mismatch in focused tests;
- pass focused pytest, Python compilation, diff checking, and root/Sol deep
  review.

The exact publication candidate is limited to:

- `docs/plans/security-incident-response-production-completion-roadmap.json`
- `docs/plans/security-incident-response-malformed-output-recovery-packet.md`
- `docs/plans/security-incident-response-version-output-diagnostic-packet.md`
- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`

Publication, push, or a later observation requires a fresh exact binding after
deep review. A later observation must be a single no-argument execution through
the redacted published-blob transport, expire at `RUN_END`, and permit no
retry. It grants no deploy, delivery, backup, restore, or host mutation.

## Current frontier

The four-path Bob/Terra handoff passed root/Sol deep review. Independent
verification passed 19 focused tests, Python compilation, and diff checking.
The observer worktree SHA-256 is
`01e648a9a861cee1b3ff446e1807b8bc840d3ffc5338208fe80d1209e49fd82e`,
and the transport pin matches it. Both SEC134 claims are released.

Next safe action: build and verify the exact seven-path publication candidate.
Only after `fuzzy/dev` readback confirms its revision and observer hash may the
recorded single-use no-argument read-only observation be consumed once without
retry. Chat memory is not completion evidence.
