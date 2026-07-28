# Transactional Deploy Packet

Status: `needs_live_observation`. This is a contract and future template only.
It grants no deploy Go, defines no deploy executor, and performs no build,
container, Git, network, SSH, backup, restore, or delivery action.

## One exact compatibility observation

The later user phrase is exactly:

```text
GO ABC-SEC131 PODMAN COMPOSE CAPABILITY READ-ONLY OBSERVATION ONCE <=15S EXPIRES RUN_END
```

It authorizes one read-only, source-redacted capability observation only:

```text
cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 15s /usr/bin/python3 ops/homeserver/redacted_podman_compose_capability_observation.py
```

The exact future workstation invocation is:

```text
ssh -F ops/homeserver/ssh_config odysseus-homeserver 'cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 15s /usr/bin/python3 ops/homeserver/redacted_podman_compose_capability_observation.py'
```

No arguments, retry, follow-on command, mutation, deploy, image build, pull,
container switch, checkout, or service action is allowed. The grant expires at
`RUN_END`; expiry is checked before invocation; used, expired, revoked, or
replayed authority is invalid. The wrapper returns one fixed source-redacted
JSON result or content-free `blocked`; it must not emit raw command output,
version output, host/path/config data, environment, credentials, image names,
or provider response.

Repo-only evidence cannot prove the target host's Podman Compose 1.3.0
behavior for service-scoped `build`/`up` with `--no-deps --no-build`, whether
dependencies are recreated or pulled, or whether rollback `--force-recreate`
has the required effect. The observation must therefore establish only an
allowlisted compatibility capability before an owner can complete any deploy
packet.

Successful output has exactly these keys:

```text
schema_id
status
podman_compose_version
global_env_file_parser_present
global_project_name_parser_present
service_scoped_build_parser_present
service_scoped_up_parser_present
no_deps_parser_present
no_build_parser_present
rollback_force_recreate_parser_present
service_scoped_dependency_exclusion_proven
rollback_force_recreate_proven
deployment_capability_supported
raw_stdout_visible
raw_stderr_visible
exception_text_visible
environment_visible
source_text_visible
paths_visible
hostnames_visible
secret_values_visible
evidence_sha256
```

`status=ok`; every parser/proven capability is exactly `true`; all eight
visibility booleans are exactly `false`. A constant validated
`podman_compose_version` may be emitted, but raw version output cannot. A
`needs_live_observation` result has exactly `schema_id`, `status`, `reason_code`,
`retry_permitted=false`, and `evidence_sha256`. A `blocked` result has exactly
`schema_id`, `status`, `error_code`, `retry_permitted=false`, and
`evidence_sha256`. Any false/unknown, malformed, timeout, exception,
visibility true, digest mismatch, or unexpected field is terminal `blocked`
with no retry and no partial fields.

## Deferred transactional-deploy template

This template is intentionally incomplete until the compatibility observation
passes and an owner binds every value. Its later action-specific Go must bind:

- exact old and new revisions, each 40 lowercase hexadecimal characters;
- validated fresh SEC129 snapshot evidence and exact provenance/digest;
- one lock, clean development checkout, configured source validation, and one
  bounded fetch;
- exact revision validation plus fast-forward-only transition;
- detached release worktree and immutable manifest; captured image and rollback
  tag; allowlisted internal metadata preservation only; do not use the existing
  metadata updater because it performs extra network/remote-URL rewrite;
- app-only service build/switch using the observed compatible flags; no
  dependency recreation or pull;
- health, Chroma, version, and manifest readback; then one exact
  fast-forward-only development-checkout advance after health;
- at most one rollback after switch, using the captured rollback tag and
  `--force-recreate`, with independent readback; forensic artifacts preserved.

The owner-bound model is fixed project `odysseus`, a clean `dev` checkout, a
configured remote/ref and owner-bound safe source identity without URL or
userinfo output, exactly one bounded fetch of `dev`, and an old-ancestor/new
exact revision relation. The detached release worktree is fixed, must not
preexist, and must never be overwritten. Its manifest is revision-bound.
Internally capture and validate the old app image ID and fixed rollback tag;
read the fixed production environment file internally; then perform app-only
build/switch with no dependency-service pull or recreation, and no prune. The
app service itself is intentionally switched/recreated. Read back exact
app health, Chroma health, version, and manifest. Only after those pass may
the production checkout run `git merge --ff-only <exact-new>` and perform an
internal allowlisted metadata edit that preserves all other bytes and all
credential/authentication values.

Exact deploy command template, target, old/new revisions, worktree, image/tag,
lock, source, timeout, impact bound, rollback command, health criteria, and
readback source are all `INCOMPLETE_OWNER_BINDING`. No placeholder is a value
or authority. A later packet must record one invocation/result, expiry,
revocation, separate `effect_phase` (`not_run|preflight|runtime_switched|
post_health|rollback_attempted|rollback_verified`) and `outcome`
(`not_run|succeeded|rolled_back|failed|unknown`), and `retry_permitted=false`.
Failure before runtime switch leaves the old runtime in place and performs no
rollback. Any failure, timeout, or ambiguity after runtime switch must attempt
exactly one bounded rollback to the captured old image without data restore,
then independently verify the old revision. Verified rollback yields outcome
`rolled_back`; rollback failure, timeout, or ambiguity yields `unknown`; no
retry, cleanup, or prune occurs. Release worktree and rollback image are
preserved as forensic artifacts.

Backup creation remains separately gated by SEC129; restore, restic check, and
send remain separately gated; `deploy-live-go` is not satisfied by this packet.

## Handoff

Record only packet ID, exact later decision/time, grant status
(`unused|used|expired|revoked`), observation evidence reference, bound values,
phase/outcome, redacted readback, forensic-artifact references, residual risk,
and next safe action. Until every owner binding and capability result exists,
status is `not_run`.
