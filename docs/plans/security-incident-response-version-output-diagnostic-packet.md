# SEC134 bounded version-output diagnostic packet

Run: `ABC-SEC134-20260729-VERSION-OUTPUT-DIAGNOSTIC`

Status: `published_single_observation_terminal_needs_live_observation_no_retry`.
The packet grants no further SSH, network, probe, deploy, build, pull,
checkout, container, backup, restore, send, or other live action.

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

The accepted seven-path candidate was published as commit
`b7baf06bfe70b362c4cc6ef17cc0cf5ad587cb58` with tree
`85b7a15a021e55e0c319a063c19f35f7719853b4`. `fuzzy/dev` readback confirmed
the revision and observer SHA-256
`01e648a9a861cee1b3ff446e1807b8bc840d3ffc5338208fe80d1209e49fd82e`.

The separately authorized transport then ran exactly once. Its validated
terminal envelope was `status=needs_live_observation`,
`reason_code=semantic_proof_insufficient`, `retry_permitted=false`, with
evidence digest
`c57a19fbb46b8802d2f3298344026619ba0a2d3deab713d47dbd833b18d6de0d`.
The earlier version-output failure did not recur. This proves the fixed
short-version gate passed, but the envelope intentionally does not reveal which
downstream help-parser or source-audit proof remained false. No retry occurred.

Next safe action: first create and deep-review a repo-only fixed-enum
semantic-proof classifier that identifies the missing allowlisted proof without
emitting help text, source, values, or counts. Any later observation requires
new action-specific authority. Deploy and delivery remain independently gated.
