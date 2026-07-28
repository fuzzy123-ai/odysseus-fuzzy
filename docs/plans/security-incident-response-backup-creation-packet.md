# SEC129 Predeploy Backup Creation Packet

Status: contract only; no current Go and no action. This future packet is
single-use and cannot authorize deploy, send, restore, restic check, SSH,
network access, staging, commit, or push.

## Later user decision

The exact future phrase is:

```text
GO ABC-SEC129 PREDEPLOY BACKUP CREATE ONCE <=1860S EXPIRES RUN_END
```

It is requestable only after the reviewed revision is published and both the
D0 predeploy observation and SEC128 backup-snapshot read-only observation have
valid, redacted evidence references. It grants at most one invocation and one
result, with no retry. Expiry is checked immediately before invocation;
invocation must begin before `RUN_END`. An unused grant may be revoked;
revoked, expired, used, or replayed authority is invalid forever.

`deploy-live-go` and `OPS-ALERT-DELIVERY-GO` remain independent; neither is
granted or satisfied by backup creation.

## Exact bounded action

- Repository: exactly `/mnt/backup/restic/homeserver`.
- Protected source: exactly `/opt/odysseus`.
- Fixed action command, no appended arguments, environment overrides,
  redirection, alternate repository/source/binary, `--init-repo`, `--prune`,
  `check`, `restore`, `unlock`, or deletion:

```text
cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 1860s /usr/bin/python3 ops/homeserver/redacted_predeploy_backup_creation.py
```

- Outer timeout is exactly 1860 seconds; maximum invocation/result/retry is
  `1` / `1` / `0`.
- A concurrent-backup lock is required before invocation and must be retained
  through the fixed redacted post-backup readback. Existing or ambiguous lock
  ownership blocks before invocation. Partial-snapshot/readback ambiguity
  after dispatch is terminal `unknown`, not a pre-invocation block.

The wrapper alone invokes the fixed absolute
`/opt/odysseus/ops/homeserver/backup-homeserver.sh --mode pre-update`. It pins
and validates the reviewed script's fixed configuration, rejects
password-command use and all binary/repository/source/scope overrides, uses
only a source-safe password-file configuration, and emits no raw process
output. Its fixed environment pins the broader reviewed homeserver-script
scope, while inclusion of the exact protected source remains mandatory.
Without the published reviewed wrapper binding this packet remains blocked.

## Required preflight and provenance

Record only redacted references to:

- the published reviewed revision and exact deployment-diff digest;
- successful D0 predeploy observation evidence;
- successful SEC128 snapshot-readiness observation evidence;
- action/policy version, opaque scope fingerprint, fixed command digest, grant
  expiry, and lock-provenance reference.

Published revision, D0/SEC128 evidence, grant expiry, revocation, and replay
are packet-controller preconditions. If any is absent or fails, the action is
`not_run`; they are not wrapper error enums and the wrapper is not invoked.

The new snapshot must be provably created after the internally recorded action
start, have one exact lowercase 64-hex snapshot ID, include the protected
source, be fresh under the fixed 1860-second creation/readback window, and be
bound to the action provenance. No repository listing, path, host name,
credential/config/password detail, process output, exception, environment, or
raw restic response may leave the repository-owned redaction boundary.

## Fixed post-backup readback

The fixed published readback wrapper may emit exactly one canonical UTF-8 JSON
object. Successful output has exactly:

```text
schema_id
status
repository_identity
protected_source_identity
backup_effect
action_provenance_ref
snapshot_id
snapshot_created_after_start
source_included
snapshot_age_seconds
snapshot_fresh
concurrent_lock_held
partial_snapshot_detected
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

`schema_id` is exactly `odysseus.redacted_predeploy_backup_creation.v1`;
`status` is `ok`; `repository_identity` and `protected_source_identity` are
exactly `restic_homeserver_backup_v1` and `odysseus_protected_source_v1`;
`backup_effect` is exactly `created`; `action_provenance_ref` matches exactly
`^predeploy_backup_creation_v1:[0-9a-f]{64}$`; `snapshot_id` matches exactly
`^[0-9a-f]{64}$`; and `snapshot_age_seconds` is an integer from `0` through
`1860`. The four effect booleans (`snapshot_created_after_start`,
`source_included`, `snapshot_fresh`, `concurrent_lock_held`) are `true`;
`partial_snapshot_detected` and every visibility boolean are `false`.
`evidence_sha256` is SHA-256 of the object excluding that field, serialized as
UTF-8, `ensure_ascii=true`, sorted keys, and separators `,` and `:`.

Blocked-before-invocation output contains only `schema_id`, `status=blocked`,
`error_code`, `backup_invoked=false`, `retry_permitted=false`, and
`evidence_sha256`. Allowed error codes are:

```text
config_unavailable
config_invalid
mount_unavailable
repository_unsafe
password_file_unsafe
restic_unavailable
backup_script_unsafe
source_path_missing
lock_contended
lock_unavailable
internal_error
```

After invocation, timeout, nonzero result, exception, malformed/oversized
output, missing provenance, partial-snapshot signal, or ambiguous readback is
terminal `unknown`, not `blocked`: backup effect may have occurred and no retry
is permitted. Unknown-after-invocation output contains only `schema_id`,
`status=unknown`, `error_code`, `action_provenance_ref`,
`effect_may_have_occurred=true`, `retry_permitted=false`,
`manual_recovery_required=true`, and `evidence_sha256`.
Its allowed codes are `backup_timeout`, `backup_failed`, `backup_exception`,
`backup_result_invalid`, `readback_timeout`, `readback_failed`,
`readback_exception`, `readback_output_too_large`, `readback_malformed`,
`snapshot_missing`, `snapshot_id_invalid`, `snapshot_invalid`,
`snapshot_stale`, `snapshot_not_new`, and `internal_error`.

## Terminal recovery and handoff

Terminal recovery is stop only: no delete, prune, unlock, retry, restore, or
scope expansion. A later separately authorized read-only observation or manual
recovery may investigate terminal unknown state; neither is implied here.

Handoff fields: packet ID; later decision/time; grant status
(`unused|used|expired|revoked`); published revision/diff and wrapper digests;
D0 and SEC128 evidence references; action start/provenance reference; timeout;
canonical post-backup evidence reference; final status
(`not_run|succeeded|blocked|unknown|expired|revoked`); residual risk; and next
safe action. The handoff stores no snapshot listing, source path, host name,
password/config data, raw output, or secrets.
