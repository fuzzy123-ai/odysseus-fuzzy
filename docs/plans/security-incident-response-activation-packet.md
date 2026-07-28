# Security Incident Response Activation Packets

Date: 2026-07-28

Status: SIRP-11 offline preparation only. Every gate named below is open or
independent in the canonical roadmap. This document grants no Go, does not
authorize a live action, and must not be used as a reusable approval.

## Operating rule

One future operator decision may approve **one** completed packet for **one**
named action. It must identify the incident/action version and opaque target
reference, be fresh for that action, and expire at the recorded time. A new
target, scope, policy/action version, channel, time window, or retry requires
a new packet and a later action-specific user decision. "Prepare", a prior
packet, a related gate, a receipt, or an executor acknowledgement is not Go.

Packets and their gates are independent. Observe never authorizes delivery;
delivery never authorizes CrowdSec, session invalidation, remediation tools or
deployment; CrowdSec never authorizes session invalidation; and no canary
authorizes temporal closure or optional lockdown. Credential, SSH-key and
authentication-configuration changes are out of scope.

## Packet completion contract

Before a packet can be presented for a later decision, record only redacted
references and complete every field below. Missing, ambiguous, stale or
unbounded information is `blocked`, not an invitation to infer it.

| Required field | Required record |
| --- | --- |
| Target class and bounded scope | One named class and one opaque target/action reference; no wildcard, cohort expansion or free text target. |
| Timeout and grant expiry | Exact action timeout and exact single-use approval expiry. Check expiry immediately before execution; execution must start before expiry; stale or replayed grants are rejected. |
| Grant status and revocation | Record only `unused`, `used`, `expired` or `revoked`. An unused grant may be revoked; revocation immediately blocks execution and revoked authority is never reusable. |
| Required evidence | Redacted preflight evidence reference, policy/action version, and the minimum acceptance facts for this packet. |
| Rollback/recovery | One pre-agreed reversible stop, expiry, recovery or terminal-failure path, including its owner. |
| Independent readback | A separately sourced, redacted effect/status readback; executor acknowledgement alone is insufficient. |
| Abort/stop conditions | The packet-specific conditions plus any raw/private output, secret risk, scope drift, missing gate, unavailable readback, timeout, retry expansion, lockout risk or operator withdrawal/revocation. |
| Operator decision and post-action status | A later explicit `Go`, `No-Go`, `Partial`, `Deferred` or `Blocked` decision and a final `succeeded`, `failed`, `rolled_back`, `closed`, `expired`, `unknown` or `not_run` status. |

No packet may include credential values, target values, raw logs, provider
responses, host paths, chat identifiers, cookies or session material. The
handoff records fixed redacted facts and bounded counts only.

Revocation is a fail-closed status transition, not a Go: the handoff records a
redacted revocation-decision/time reference without target or credential data.
It cannot be renewed, reused or converted into authority by this document.

## Observe packet — read-only only

Required gates: `observability-live-smoke-go`,
`debian-observability-live-go`, and `log-retention-policy-go`. These gates do
not authorize delivery, deployment, remediation or any mutation.

- Target class: exactly one approved read-only observability source or one
  Debian readiness probe projection.
- Bounded scope: one approved query/fixture or one fixed probe projection;
  record an exact time window and maximum result count where a query applies.
- Timeout: one exact read-only timeout; no follow-on query or broad ingestion.
- Evidence: redacted preflight policy/retention confirmation and source
  reference. For Debian readiness, the only permitted diagnostic reference is
  `ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe` and its fixed redacted JSON projection.
- Recovery/readback: stop the read; independently compare the bounded
  redacted projection or query metadata with the approved expectation.
- Abort: any request for a caller-supplied remote command, raw output,
  retention uncertainty, broader time window/result count, or mutation.
- Decision/status: later operator decision records `not_run`, `succeeded`,
  `failed`, `expired` or `unknown`; it grants no delivery or remediation.

## Delivery packet — one redacted operator notification

Required gate: `OPS-ALERT-DELIVERY-GO`. It is separate from the observe,
CrowdSec, session, deployment and temporal gates.

- Target class: one approved delivery channel with server-side target
  readiness confirmed without exposing the target.
- Bounded scope: one redacted body, one incident/action version and one send;
  no broadcast, recipient expansion or retry campaign.
- Timeout: one exact delivery attempt timeout and one grant expiry.
- Evidence: redacted preview reference, server-side readiness attestation and
  policy decision.
- Recovery/readback: failure records a redacted status/correlation reference;
  recovery is no send, `failed`/`unknown`, or a newly approved replacement
  packet—not an automatic retry.
- Abort: unknown target readiness, body/scope change, private content,
  retry expansion, timeout or operator withdrawal.
- Decision/status: the later operator decision and independently observed
  delivery status are recorded; delivery receipt is not proof of CrowdSec,
  session or deployment effect.

## CrowdSec packet — one temporary reversible action

Required gates: `crowdsec-remediation-go`, `OPS-REMEDIATION-GO`, and
`mcp-remediation-tools-go`. All three must be independently granted later;
none substitutes for the others.

- Target class: exactly one `crowdsec_temp_block` or `crowdsec_unblock` action
  version against one opaque approved scope.
- Bounded scope: one action ID, exact TTL/expiry or one known originating ban;
  no firewall rule, deploy, service restart or session change.
- Timeout: exact execute/readback timeout and single-use grant expiry.
- Evidence: redacted preflight, false-positive and operator-lockout review,
  TTL/unban plan and action/policy version.
- Recovery/readback: independent redacted effect readback plus unban/expiry
  proof; terminal recovery is explicit unban/expiry or failed/unknown status.
- Abort: broad/unlimited scope, missing TTL, lockout risk, active uncertainty,
  unavailable independent readback, any effectful MCP exposure without its
  separate gate, or scope/action-version drift.
- Decision/status: later decision applies once; record effect and recovery
  status separately. No result grants session invalidation or temporal close.

## Session packet — one non-operator test session

Required gates: `security-incident-session-invalidation-go` and
`mcp-remediation-tools-go`, independently granted later.

- Target class: one approved non-operator test-session scope only.
- Bounded scope: one opaque session reference and one invalidation action
  version; all-user invalidation, credential reset and authentication changes
  are excluded.
- Timeout: exact action/readback timeout and one single-use grant expiry.
- Evidence: redacted preflight proving test/non-operator classification,
  policy/action version and operator-lockout assessment.
- Recovery/readback: independently read back redacted session-state facts and
  the approved recovery path; executor acknowledgement is not sufficient.
- Abort: operator/approval-session target, scope drift, lockout risk,
  credential/SSH/authentication change, missing recovery or missing readback.
- Decision/status: later decision records one outcome; it never authorizes
  CrowdSec, delivery, deployment or temporal closure.

## Temporal-closure packet — observation window only

Required gate: `security-incident-temporal-closure-go`. It may be considered
only after the prerequisite canaries have their own durable outcomes; it does
not create a new action family or authorize lockdown.

- Target class: one approved incident lifecycle/audit reconstruction window.
- Bounded scope: named canary evidence references, exact observation-window
  start/end, and stated closure criteria.
- Timeout: exact window/end-time and grant expiry; no indefinite monitoring.
- Evidence: redacted expiry, retry/idempotency, delayed-readback, rollback or
  expiry and audit-reconstruction references.
- Recovery/readback: independently review the bounded evidence chain and
  post-incident status; unresolved critical lifecycle evidence remains open.
- Abort: missing canary outcome, stale approval, evidence gap, new action
  family, automatic lockdown, or any attempt to claim production closure early.
- Decision/status: a later operator decision closes only this window; record
  `closed`, `failed`, `expired`, `unknown` or `not_run` with residual risk.

## Deployment boundary

`deploy-live-go` is independent. No SIRP packet above permits deployment,
deploy rollback or a deploy-adjacent remediation. Any such request needs its
own later, single-action deploy packet with target, rollback and independent
readback; this activation document supplies no Go for it.

## D0 predeploy read-only observation packet

Status: `WAITING_PATH_SCOPED_PUBLISH_AND_EXACT_GO`. This is one future predeploy
**read-only** observation, not a deployment packet and not a delivery packet.
It grants no Go, cannot enable delivery, and cannot be promoted into
`deploy-live-go` or `OPS-ALERT-DELIVERY-GO` authority.

### Required later decision

The only acceptable future user phrase is:

```text
GO ABC-SEC123 D0 PREDEPLOY READ-ONLY OBSERVATION ONCE <=30S EXPIRES RUN_END
```

That phrase is effective only when an operator has completed this packet with
one exact expected full revision, approved branch, upstream-relation allowlist,
expiry, and outer timeout. It authorizes at most one invocation of the exact
fixed command below. It does not authorize a deploy, send, provider action,
backup, restore, runtime mutation, retry, follow-on command, or caller-supplied
remote command.

### Fixed target and command contract

- SSH alias: exactly `odysseus-homeserver`.
- Repository path: exactly `/opt/odysseus`.
- Maximum invocations/results/retries: `1` / `1` / `0`.
- Outer timeout: an integer number of seconds from `1` through `30` inclusive.
- Total outer budget is at most 30 seconds. The composed SEC128 observer gets
  one invocation and the observer gets at most one fixed restic invocation;
  their component budgets must fit inside the D0 outer budget. No duplicate
  restic invocation, fallback observer, retry, or follow-on command is allowed.
  The fixed base budget is nine commands at one second each plus one SEC128
  observation bounded at 20 seconds: at most 29 seconds inside the 30-second
  outer budget.
- Exact command, with no appended arguments, shell fragment, redirection or
  alternate alias:

```text
ssh -F ops/homeserver/ssh_config odysseus-homeserver 'cd /opt/odysseus && exec python3 ops/homeserver/redacted_predeploy_observation.py'
```

The wrapper is exactly
`/opt/odysseus/ops/homeserver/redacted_predeploy_observation.py`. Repository
inspection initially found no corresponding wrapper. The earlier conservative
D0A backup projection is obsolete: accepted SEC128 now composes the fixed
source-redacted backup snapshot observation in-process. A separately
authorized path-scoped publish remains a prerequisite so the target can
observe the reviewed full revision. Neither publication nor observation is
authorized by this packet.

`ops/homeserver/check-backup-health.sh` is excluded because it emits raw
listings. `ops/homeserver/run-backup-gate-evidence.sh` is excluded because it
mutates state. Neither is an allowed fallback, input, wrapper, or command for
D0.

### Fixed redacted wrapper schema

The wrapper must emit exactly one UTF-8 JSON object and nothing else. It must
collect source facts internally, reject raw/unbounded data before output, and
return only this exact-key allowlist. It must not forward raw stdout, stderr,
journals, environment or secret metadata, provider responses, paths beyond
the fixed packet identity, or backup listings.

Successful `ok` output must contain exactly these keys:

```text
schema_id
status
identity
repository_revision
branch
worktree_clean
dirty_entry_count
upstream_relation
odysseus_podman_service_active
odysseus_podman_service_status
odysseus_container_running
odysseus_container_status
api_version_revision_matches
backup_ready
rollback_snapshot_available
rollback_snapshot_id
rollback_snapshot_source_identity
rollback_snapshot_age_seconds
rollback_snapshot_fresh
rollback_snapshot_observation_evidence_sha256
raw_environment_visible
secret_values_visible
evidence_sha256
```

Field constraints:

- `schema_id` is exactly `odysseus.redacted_predeploy_observation.v1` and
  `status` is exactly `ok`.
- `identity` is exactly
  `odysseus-homeserver:/opt/odysseus:odysseus-podman.service:odysseus_odysseus_1`;
  it is a fixed projection, not discovered host metadata. Before emitting it, the wrapper internally verifies expected principal `homebase` and expected hostname `debian`; a mismatch emits only `identity_mismatch`.
- `repository_revision` is exactly 40 lowercase hexadecimal characters and
  equals the full revision bound in the later decision. `branch` equals the
  decision's approved branch and matches the wrapper's fixed safe branch-name
  grammar; no branch path or remote URL is emitted.
- `worktree_clean` is boolean. `dirty_entry_count` is an integer from `0`
  through `4096` only; no dirty path, filename, diff or status text is allowed.
  D0 accepts only `worktree_clean=true` and `dirty_entry_count=0`.
- `upstream_relation` is exactly one of `upstream_equal`, `local_ahead`,
  `remote_ahead`, `diverged`, or `no_upstream`. The later packet supplies an
  allowlist; it must contain `upstream_equal` only unless a separate owner
  decision explicitly records another enum value and its risk.
- `odysseus_podman_service_active` and `odysseus_container_running` are
  booleans. Service status is one of `active`, `inactive`, `failed`,
  `activating`, `deactivating`, or `unknown`; container status is one of
  `running`, `created`, `exited`, `paused`, or `unknown`. D0 accepts only
  active/running with both booleans true.
- `api_version_revision_matches` is boolean only. The current local
  `/api/version` contract exposes exactly the first eight lowercase hexadecimal
  characters of the full revision as `commit`; the wrapper must require that
  fixed grammar and compare it with `repository_revision[0:8]` without emitting
  the endpoint response. D0 accepts only `true`. The full 40-character
  `repository_revision` remains independently required and is never replaced
  by the endpoint prefix.
- `backup_ready` and `rollback_snapshot_available` are exactly `true`.
  `rollback_snapshot_id` is exactly 64 lowercase hexadecimal characters.
  `rollback_snapshot_source_identity`, `rollback_snapshot_age_seconds`,
  `rollback_snapshot_fresh`, and
  `rollback_snapshot_observation_evidence_sha256` bind SEC128's validated
  source identity, fixed freshness fact, and canonical evidence digest into D0
  provenance. They never render a path,
  hostname, listing, credential, provider response, or raw observation. D0
  accepts the snapshot only when SEC128 `status=ok`, source inclusion is true,
  visibility flags are all false, and its canonical digest validates.
- `rollback_snapshot_source_identity` is exactly
  `odysseus_protected_source_v1`. `rollback_snapshot_age_seconds` is an
  integer, never boolean, from `0` through `86400`; `rollback_snapshot_fresh`
  is exactly `true`. `rollback_snapshot_observation_evidence_sha256` is
  exactly 64 lowercase hexadecimal characters and equals the independently
  recomputed SEC128 canonical digest. `rollback_snapshot_id` is exactly the
  validated SEC128 snapshot ID, not a substituted or newly derived value.
- `raw_environment_visible` and `secret_values_visible` are booleans and both
  are exactly `false`. Their absence, a non-boolean value, or `true` is a
  terminal `source_redaction_failure`.
- `evidence_sha256` is lowercase SHA-256 of the canonical evidence payload:
  serialize this `ok` object excluding `evidence_sha256` with UTF-8,
  `ensure_ascii=true`, lexicographically sorted keys, and separators `,` and
  `:`; hash those exact bytes. The handoff records the resulting
  `predeploy-observation:sha256:<digest>` reference only.

Blocked output must contain exactly `schema_id`, `status`, `error_code`, and
`evidence_sha256`; `status` is `blocked`, and no partial observation fields
are permitted. Its canonical hash uses the same serialization rule excluding
`evidence_sha256`.

The complete fail-closed `error_code` enum is:

```text
wrapper_missing
wrapper_integrity_unverified
identity_mismatch
repository_unavailable
revision_unavailable
branch_unallowed
worktree_dirty
dirty_count_out_of_range
upstream_relation_unallowed
service_status_unallowed
container_status_unallowed
api_version_unavailable
api_revision_mismatch
backup_readiness_unavailable
rollback_snapshot_unavailable
rollback_snapshot_unsafe
rollback_snapshot_invalid
timeout
malformed_output
unexpected_field
source_redaction_failure
internal_error
```

Malformed output, unknown fields, unknown enum values, a timeout, missing
hash, hash mismatch, unavailable source, or any non-`ok` status terminates D0
as `blocked` with no retry. The command's transport exit code or an SSH error
is never copied into evidence; the only retained result is the validated fixed
schema or a local content-free `blocked` record.

SEC128 blocked, unknown, malformed, stale, source-not-included, hash-mismatch,
visibility-true, timeout, or exception evidence also terminates D0 as fixed
`blocked`, with no partial D0 fields and no silent false-success.

### Rollback readiness and handoff

This packet does not execute or validate a rollback. It only observes whether
a source-safe rollback snapshot identifier is available for a future separate
deployment packet. SEC128 supplies the required snapshot-readiness provenance;
a false or invalid SEC128 result blocks D0 with no partial fields. SEC129
backup creation remains a separate later one-use action. D0/SEC128 evidence
does not authorize SEC129, deployment, restore, restic check, or delivery.

The later D0 handoff contains packet/reference ID, expected revision and
branch, timeout, expiry, grant status (`unused|used|expired|revoked`), the
canonical evidence reference, accepted enum facts, rollback-snapshot
availability, stop reason, and final status. An unused grant may be revoked;
revocation immediately blocks the one invocation and never creates reusable
authority. Expiry is checked immediately before invocation and invocation
must begin before expiry.

## Redacted handoff card

Use one card per packet after a future decision. Do not include raw evidence
or target values.

```text
Packet kind / canonical gate(s):
Incident and action/policy version reference:
Target class / opaque bounded scope reference:
Timeout / single-use grant expiry:
Grant status: unused | used | expired | revoked
Later operator decision and decision time:
Redacted revocation-decision/time reference (if revoked):
Required redacted preflight evidence references:
Rollback or recovery owner/path:
Independent redacted readback source/reference:
Abort/stop conditions encountered:
Post-action status: not_run | succeeded | failed | rolled_back | closed | expired | unknown
Mutation performed: yes | no
Residual risk / next safe action:
```

The card reports no Go by itself. A `Partial` decision records read-only
metadata only and cannot be promoted into delivery or execution. Revocation
blocks an unused grant immediately and never creates reusable authority.
