# SEC132 published-blob stdin observation transport packet

Run: `ABC-SEC132-20260729-OBSERVER-TRANSPORT-RECOVERY`

Status: `repo_only_pending_publication`. This packet grants nothing and no SSH,
network, deploy, build, pull, container, backup, restore, or delivery action
has occurred while producing it.

## SEC131 terminal record and strategy change

The single SEC131 grant was used and ended as
`transport_or_schema_validation_failed`. No raw cause is claimed or retained in
this packet. Its one-call authority is spent and cannot be retried.

SEC132 removes the failed remote-checkout dependency. The future workstation
runner reads the exact published observer blob from the local Git object,
verifies its immutable SHA-256, and sends only those bytes to Python stdin over
one fixed SSH command. The host need not have the observer file at its checked
out revision.

## Immutable bindings

- Published revision: `0c61af3ce4d59fabed82dc87594e135527d726a8`
- Published object:
  `0c61af3ce4d59fabed82dc87594e135527d726a8:ops/homeserver/redacted_podman_compose_capability_observation.py`
- Git blob object ID: `329a542e5e0e0174d47f068ad99292e665efc12f`
- Observer byte SHA-256:
  `31af417c21acb00cdc7c9050e8d9f2e7c38784e518234b84fe659a0c51b696bc`

The replacement runner is
`ops/homeserver/redacted_podman_compose_capability_transport.py`. It accepts no
caller arguments, uses no shell, attempts no retry, discards SSH stderr, and
never emits SSH stdout, stderr, or exception text. It validates exact
allowlisted observer schemas and canonical evidence digests before
reserializing them. Blob, transport, timeout, schema, or digest failures yield
only the separate fixed transport `blocked` envelope with
`retry_permitted=false` and its own canonical digest.

## Future live action (not yet authorized)

SEC132 artifacts must first be reviewed and published. A later publication
record must bind their exact published revision and byte hashes; no draft,
placeholder, local file, or this packet itself is authority. Only then this
exact phrase may be requested:

```text
GO ABC-SEC132 PODMAN COMPOSE PUBLISHED BLOB STDIN TRANSPORT READ-ONLY OBSERVATION ONCE <=30S EXPIRES RUN_END
```

That phrase, if separately granted, permits exactly one invocation from the
Windows repository root with no arguments and no retry:

```text
venv\Scripts\python.exe ops/homeserver/redacted_podman_compose_capability_transport.py
```

It makes one SSH call with exact remote command:

```text
cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 15s /usr/bin/python3 -
```

The local immutable Git read is bounded to five seconds and the one SSH call to
20 seconds: this is a 25-second aggregate subprocess budget. The GO/operator
window is 30 seconds to cover bounded local process overhead; the remote
observation itself remains bounded to 15 seconds. Used, expired, revoked, or
replayed authority is invalid. A transport or schema terminal record is not a
permission to retry.

This phrase grants no deploy, build, pull, restart, container mutation,
checkout, backup, restore, restic check, cleanup, prune, delivery, send, or
other gate. Even a valid observer `ok` result creates no deploy authority.

## Handoff

Next action: publish this exact four-path SEC132 artifact set through a
separately reviewed publication packet:

- `docs/plans/security-incident-response-transport-recovery-packet.md`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`
- `docs/plans/security-incident-response-production-completion-roadmap.json`

Only then request the exact SEC132 phrase above. Until then, the OPS-Alert
live frontier remains blocked.
