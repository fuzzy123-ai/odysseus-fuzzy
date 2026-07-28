# Backup Snapshot Read-Only Observation Packet

Status: repo-only contract. This packet grants no Go, performs no observation,
and authorizes neither backup creation nor restore. It is a non-reusable future
one-use observation boundary only.

## Required later user decision

The exact future phrase is:

```text
GO ABC-SEC128 BACKUP SNAPSHOT READ-ONLY OBSERVATION ONCE <=30S EXPIRES RUN_END
```

The phrase is ineffective unless the fixed wrapper below exists in the
published reviewed revision, its wrapper digest is recorded, and the later
packet is complete. It authorizes at most one read-only invocation, one
validated result, no retry, and no follow-on command. It does not authorize
SSH, network access, repository mutation, backup creation, restore, restic
check, deploy, send, credential change, or any caller-supplied command.

## Fixed host-local wrapper contract

The only future host-local command is:

```text
cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 30s /usr/bin/python3 ops/homeserver/redacted_backup_snapshot_observation.py
```

It has no arguments, redirection, shell extension, environment override, or
alternate binary/repository/source/path. It is bounded by an outer timeout of
at most 30 seconds and expires at `RUN_END`; expiry is checked immediately
before invocation and the invocation must begin before expiry. An unused grant
may be revoked, which blocks it immediately; used, expired, revoked, or
replayed grants are never reusable.

The wrapper must use only these fixed internal identities:

- protected source: `/opt/odysseus`
- backup repository: `/mnt/backup/restic/homeserver`
- restic binary: `/usr/bin/restic`

The wrapper may use only its fixed source-safe password-file configuration.
It must reject any `RESTIC_PASSWORD_COMMAND`, any arbitrary binary, repository,
source, password-file, option, or path override before starting restic. It must
not inspect or emit an environment, credential metadata, password-file name,
file listing, host name, raw command output, stderr, exception text, or restic
provider output.

## Canonical redacted JSON

The wrapper emits exactly one UTF-8 JSON object and nothing else. Before
emission, it validates source facts and reserializes only the following exact
allowlist. No additional key, nested object, array, path, hostname, file name,
credential, raw stdout/stderr, exception, environment, or provider response is
accepted or retained.

Successful output has exactly these keys:

```text
schema_id
status
repository_identity
protected_source_identity
snapshot_id
source_included
snapshot_age_seconds
snapshot_fresh
raw_stdout_visible
raw_stderr_visible
exception_text_visible
environment_visible
file_contents_visible
paths_visible
hostnames_visible
secret_values_visible
evidence_sha256
```

Constraints:

- `schema_id` is exactly `odysseus.redacted_backup_snapshot_observation.v1`.
- `status` is exactly `ok`.
- `repository_identity` is exactly `restic_homeserver_backup_v1` and
  `protected_source_identity` is exactly `odysseus_protected_source_v1`; both
  are fixed projections, never discovered path or hostname strings.
- `snapshot_id` matches exactly `^[0-9a-f]{64}$`.
- `source_included` is boolean and must be `true` for
  acceptance; its derivation verifies inclusion of the fixed protected source
  without emitting a path or file name.
- `snapshot_age_seconds` is an integer from `0` through `86400` inclusive.
  `snapshot_fresh` is boolean and is accepted only when `true`, using the
  wrapper's fixed maximum freshness bound of 86400 seconds.
- `raw_stdout_visible`, `raw_stderr_visible`, `exception_text_visible`,
  `environment_visible`, `file_contents_visible`, `paths_visible`,
  `hostnames_visible`, and `secret_values_visible` are each exactly `false`.
- `evidence_sha256` is lowercase SHA-256 of the canonical object excluding
  that field: UTF-8, `ensure_ascii=true`, sorted keys, and separators `,` and
  `:`. The handoff stores only
  `backup-snapshot-observation:sha256:<digest>`.

Blocked output has exactly `schema_id`, `status`, `error_code`, and
`evidence_sha256`; `status` is `blocked`, contains no partial facts, and uses
the same canonical-digest rule.

## Fail-closed error enum

```text
config_unavailable
config_invalid
mount_unavailable
repository_unsafe
password_file_unsafe
restic_unavailable
snapshot_query_failed
output_too_large
malformed_output
snapshot_missing
snapshot_id_invalid
snapshot_invalid
snapshot_stale
source_path_missing
timeout
internal_error
```

Unknown or malformed output, unknown enum values, a digest mismatch, timeout,
or any non-`ok` status is terminal `blocked`; it creates no retry and no
fallback to another command or script.

## Separate action boundaries

This packet observes one existing snapshot only. Backup creation requires its
own later action-specific live Go and rollback plan. Restore requires a
separate later action-specific live Go, exact snapshot scope, access recovery,
and independent post-restore readback. A restic check requires a separate
later live Go. Deployment remains behind independent `deploy-live-go`; any
notification remains behind independent `OPS-ALERT-DELIVERY-GO`. None of those
actions, nor this observation, implies another.

## Future handoff and blockers

Record only packet ID, grant status (`unused|used|expired|revoked`), later
decision/time, timeout, expiry, wrapper digest, canonical evidence reference,
the accepted fixed schema facts, stop reason, and final status. Do not record
the snapshot source path, repository path, host identity, credential data, or
raw diagnostic content.

Current blockers: the fixed wrapper must be implemented, offline-tested,
reviewed, committed, and pushed under a separately authorized path-scoped
publish before any later Go can be considered. This document authorizes none
of those actions.
