# SIRP-12 observe packet evidence

Status: `OBSERVE_SUCCEEDED` — the approved packet was invoked exactly once and is consumed. This document contains only the repository-owned privacy-minimized projection supplied after strict validation; it contains no raw subprocess output.

## Approved packet

- Run: `ABC-SEC120-20260728-SIRP12-OBSERVE`
- Live Go: `SIRP12-OBSERVE-PACKET-20260728`
- Granted by: `GO SIRP12-OBSERVE-PACKET wie beschrieben`
- Action: `other/read_only_observation`
- Target class: fixed redacted Debian readiness projection
- Exact command: `ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe`
- Limits: one invocation, one result, 30-second outer timeout, zero follow-on queries
- Retention: allowlisted boolean presence and bounded counts only; no raw output
- Recovery: stop with no mutation and no retry
- Readback: repository-owned strict validation and reserialization
- Expiry: run end
- Consumption: consumed; never reusable
- Invocation counter: `1`
- Result counter: `1`
- Retry counter: `0`
- Follow-on query counter: `0`
- External action executed: `true`
- Mutation performed: `false`

The authorization does not cover delivery, deployment, remediation, runtime database access, credential or authentication changes, caller-supplied commands, or scope expansion.

## Strict output contract

The only accepted schema identifier is `odysseus.homeserver.redacted_runtime_probe.v1`.

Before reserialization, the root wrapper reported strict exact-key, type and bounds validation against the repository probe contract. The retained evidence projection intentionally excludes container identity and contains only:

- `schema_id`: the exact identifier above
- `status`: `ok`
- `container_running`: boolean
- `environment_entry_count`: integer from 0 through 4096
- `credential_presence`: exactly the repository allowlist below, with boolean values only
- `unknown_sensitive_key_count`: integer from 0 through 4096
- `raw_environment_visible`: `false`
- `secret_values_visible`: `false`

The exact credential-presence key allowlist is:

`DATA_BRAVE_API_KEY`, `EMBEDDING_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`, `GOOGLE_API_KEY`, `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `NEXTCLOUD_WEBDAV_APP_PASSWORD`, `ODYSSEUS_ADMIN_PASSWORD`, `ODYSSEUS_INTERNAL_TOKEN`, `OPENAI_API_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY`, `TELEGRAM_BOT_TOKEN`.

A `blocked` projection must contain only:

- `schema_id`: the exact identifier above
- `status`: `blocked`
- `error_code`: one of `invalid_container_name`, `podman_unavailable`, `container_probe_timeout`, `container_probe_internal_error`, `container_probe_failed`, or `invalid_probe_payload`
- `raw_environment_visible`: `false`
- `secret_values_visible`: `false`

Any extra field, wrong type, out-of-range count, unexpected presence key, raw content, timeout, or schema mismatch was terminal for this packet. The wrapper reported validation pass and exit status 0. It suppressed raw stderr and did not retain raw output beyond the validated privacy-minimized projection. No original stdout, stderr, exception, journal, provider response, environment, credential material, container identity, secret value, key prefix, key suffix, key length, or hash is persisted here.

## Observation result

```json
{
  "schema_id": "odysseus.homeserver.redacted_runtime_probe.v1",
  "status": "ok",
  "container_running": true,
  "environment_entry_count": 102,
  "credential_presence": {
    "DATA_BRAVE_API_KEY": false,
    "EMBEDDING_API_KEY": false,
    "GH_TOKEN": true,
    "GITHUB_TOKEN": true,
    "GOOGLE_API_KEY": false,
    "HF_TOKEN": false,
    "HUGGING_FACE_HUB_TOKEN": false,
    "NEXTCLOUD_WEBDAV_APP_PASSWORD": true,
    "ODYSSEUS_ADMIN_PASSWORD": true,
    "ODYSSEUS_INTERNAL_TOKEN": true,
    "OPENAI_API_KEY": false,
    "SERPER_API_KEY": false,
    "TAVILY_API_KEY": false,
    "TELEGRAM_BOT_TOKEN": true
  },
  "unknown_sensitive_key_count": 1,
  "raw_environment_visible": false,
  "secret_values_visible": false
}
```

## Execution receipt

```json
{
  "status": "succeeded",
  "schema_validation": "pass",
  "wrapper_exit_status": 0,
  "invocation_count": 1,
  "result_count": 1,
  "retry_count": 0,
  "follow_on_query_count": 0,
  "raw_output_retained": false,
  "external_action_executed": true,
  "mutation_performed": false
}
```

Residual risk: `unknown_sensitive_key_count` is `1`. This is a bounded count only. No key name, value, identifier, prefix, suffix, length or hash is known or persisted, and no remediation is inferred or authorized.

SIRP-12 remains partial and unaccepted. Observe is complete; delivery remains unstarted and `OPS-ALERT-DELIVERY-GO` remains not Go pending an exact channel/body/timeout/expiry/no-retry/readback packet. `deploy-live-go` remains independent and not Go.

## Delivery-readiness decision

Run: `ABC-SEC120-20260728-SIRP12-DELIVERY-READINESS-DECISION`

Status: `BLOCKED_MISSING_REDACTED_READINESS_CONTRACT`

The current broad continuation “go” authorizes this repository-only planning follow-up. It is not action-specific send authority, creates no live-Go entry, and does not change `OPS-ALERT-DELIVERY-GO` from `open/not_go`.

Deep contract review found:

- Security-incident notification and delivery remain dry-run/no-send contracts.
- A generic Telegram delivery bridge exists, but it can dispatch only when an opaque target is configured, the reply gate is enabled, and the target passes the server-side allow check.
- That generic bridge is not bound to a SIRP incident/action version, single-use grant, expiry, immutable attempt record, or independent durable redacted correlation readback. Readiness booleans alone would therefore remain insufficient for a safe SIRP send.
- The current agent-safe Debian projection attests `TELEGRAM_BOT_TOKEN` presence only. It does not attest opaque `target_configured` or `TELEGRAM_AGENT_REPLY_ENABLED`.
- Token presence is therefore insufficient evidence for delivery readiness.

The smallest safe frontier is a separate repo-only implementation slice for a repository-owned fixed allowlisted readiness projection containing only:

- `telegram_token_present`: boolean
- `opaque_target_configured`: boolean
- `agent_reply_enabled`: boolean
- `send_ready`: boolean derived only from those fixed prerequisites
- `raw_target_visible`: `false`
- `secret_values_visible`: `false`

That projection must never disclose a target value or identifier, token value or derived metadata, raw environment, provider response, or send result. This decision record does not authorize modifying the product or probe implementation.

The same repo-only frontier must add a SIRP-bound delivery adapter that requires one incident/action version, one single-use grant with explicit expiry, one immutable no-retry attempt record, independent durable redacted status/correlation readback, and fail-closed handling for target, body, action-version, grant or policy drift.

After that contract is implemented, locally tested, and later read back under separate authority, an exact single-send packet would still need one Telegram channel target class, one redacted body reference, one attempt timeout, run-end expiry, no-retry recovery, and independent redacted delivery status/correlation readback.

No network, probe, provider, send, deploy, runtime database, or host action occurred in this decision record.

Root independently reported 95 focused delivery and notification tests passing. That evidence validates the current dry-run and generic delivery contracts; it does not establish live-send readiness or action-specific authority.

## SIRP-12A provisional implementation — not accepted

Run: `ABC-SEC121-20260728-SIRP12A`

Status: `WAITING_ON_USER_CONTRACT_BLOCKER`

The fixed probe now projects an exact nested boolean-only Telegram readiness object for opaque target configuration, agent reply enablement, derived send readiness, and explicit non-visibility flags. All supported server-side target routes are recognized without exposing target data, and readiness keys no longer inflate the bounded unknown-sensitive-key count.

The delivery module preserves dry-run behavior and adds an injected-only SIRP-bound adapter over the existing durable incident/action store. Exact incident, action version, scope, policy, redacted body reference, channel, grant expiry, readiness and timeout are bound; approval consumption is atomic; one immutable no-retry attempt is persisted; strict redacted transport results and independent durable terminal readback are enforced. Default composition cannot send.

Bob/Terra reported 56 focused tests, 70 adjacent attack/store/executor tests, and compilation passing. Deep Sol review required one bounded fix cycle, then independently reproduced 56 focused passes and compilation success. The host's shared pytest temp directory denied access on two preliminary Sol invocations; the final workspace-owned temp run passed.

Residual: a synchronous injected transport cannot be canceled while it is executing. If its measured elapsed time exceeds the bound, the attempt is durably failed and is never acknowledged or retried.

Final root review rejected acceptance. After an injected transport returns, timeout or durable receipt-write failure may mean an external effect occurred, but the adapter reports `delivery_performed: false`; the honest state is unknown and must remain no-retry. The request also accepts caller-supplied readiness booleans rather than a typed trusted repository projection. Production transport composition and the approved opaque target-class binding are not yet constrained strongly enough to make those inputs authoritative.

The stop rule is invoked pending an owner decision on that bounded contract change. No network, runtime probe, provider call, send, deploy, host change, runtime database operation, credential/authentication change, stage, commit, or push occurred. `OPS-ALERT-DELIVERY-GO` remains `open/not_go`.

## SIRP-12A R2 repo-only contract acceptance

Run: `ABC-SEC122-20260728-SIRP12A-R2`

Status: `ACCEPTED_REPO_ONLY_LIVE_DELIVERY_STILL_NOT_GO`

The user resumed the exact blocker. Trusted readiness issuance now matches the complete strict repository-owned v1 probe acceptance boundary: exact fields, schema and status; safe non-IP container with running true; bounded and relationally consistent counts; exact credential-presence key/type set; exact nested readiness derivation; and all visibility flags false. Missing, unknown, malformed, incomplete and forged status-ok projections fail with content-free errors.

The previously corrected safety contracts remain: post-invocation ambiguity is durable `unknown` with `delivery_performed: null` and no retry; only marker-gated sealed test transport exists; an arbitrary callable cannot dispatch; the opaque approved target class is durably bound; and elapsed timeout plus exact independent terminal readback are enforced.

Worker verification: 54 focused, 24 final delivery, 70 adjacent, compilation passed. Independent Sol verification: 53 focused before the final IP-only adversarial addition, then 24 delivery and 70 adjacent passed; compilation passed. Deep Sol required one exact IP-container rejection fix cycle.

This is local contract completion only. Production transport/route composition, live runtime readiness/readback, provider behavior, an actual send, deployment, host/runtime database behavior and temporal closure are not implemented or verified. No external or Git action occurred. `OPS-ALERT-DELIVERY-GO` remains `open/not_go`.

## OPS-ALERT B/C repo-only acceptance and D gate stop

Run: `ABC-SEC123-20260728-OPS-ALERT-CLOSURE`

Status: `B_C_ACCEPTED_D_PACKET_READY_WAITING_DEPLOY_LIVE_GO`

B adds one fixed redacted operator-notification smoke body with stable opaque body and target-class references plus a module-owned sealed Telegram production issuer. It remains uncomposed by default, resolves the target only server-side through the existing precedence and allowlist, uses the existing fixed-timeout sender, accepts no request target/body/provider fields, and projects only an opaque receipt. Strict acknowledgement requires one valid provider message ID; the ID is incorporated only into the receipt hash and never exposed. Malformed, failed, timed-out or persistence-ambiguous outcomes remain durable unknown with no retry.

C adds an owner/admin-scoped server route that accepts only `expected_version` with incident/action identity in the path. It requires the exact durable approved action, canonical server-derived delivery fields, trusted readiness and a sealed transport from server dependencies. Runtime app state accepts only the sealed production issuer; marked test transports are confined to the explicit fake-integration seam. The default application has no issuer/readiness composition and cannot send.

B passed 47 focused and 70 adjacent tests; C passed 56 focused and 70 adjacent tests. Both passed compilation and each required one deep Sol fix cycle. The reviewed B/C artifact digest is `ff6fc357f609d988e162eb717385ca38b1e251597f937e4a8467e4deba2c6e17`.

D repo-only parity and the exact deployment packet are ready. The run stops at `deploy-live-go`, which remains not Go. The packet must bind the reviewed digest plus execution-time revision/diff, one exact target, prior artifact, bounded timeout, run-end expiry, revocation, rollback to the prior disabled state and independent redacted health/readback. Deployment would not authorize E.

`OPS-ALERT-DELIVERY-GO` remains `open/not_go`. E still requires a fresh exact one-send packet and independent durable readback plus separate human confirmation. No probe, provider call, send, deploy, host/runtime database action, credential/authentication change, stage, commit or push occurred.

## OPS-ALERT D0 predeploy read-only observation packet

Run: `ABC-SEC123-20260728-OPS-ALERT-CLOSURE`

Status: `WAITING_PATH_SCOPED_PUBLISH_AND_EXACT_GO`; no observation was
authorized, invoked or received. This is a planning/evidence record only. It
does not change `deploy-live-go` or `OPS-ALERT-DELIVERY-GO` from `not_go`.

The future single-use packet is defined in
`docs/plans/security-incident-response-activation-packet.md` under “D0
predeploy read-only observation packet”. It binds the fixed alias
`odysseus-homeserver`, fixed repository path `/opt/odysseus`, at most one
invocation, a 30-second-or-less outer timeout, fixed redacted JSON schema,
revision/branch/worktree/upstream/service/container/API-version parity and
source-safe rollback readiness only. The exact schema also fixes
`raw_environment_visible=false` and `secret_values_visible=false`; any other
value fails closed. It requires this exact later user phrase:

```text
GO ABC-SEC123 D0 PREDEPLOY READ-ONLY OBSERVATION ONCE <=30S EXPIRES RUN_END
```

No phrase was supplied in this run. A future phrase is insufficient until the
reviewed wrapper is published and the packet revision, branch, timeout and
expiry fields are complete.

The only permitted future command is:

```text
ssh -F ops/homeserver/ssh_config odysseus-homeserver 'cd /opt/odysseus && exec python3 ops/homeserver/redacted_predeploy_observation.py'
```

Repository inspection initially found that
`ops/homeserver/redacted_predeploy_observation.py` did not exist. D0A has now
implemented it with adversarial offline tests and deep Sol review.
The legacy `ops/homeserver/check-backup-health.sh` is excluded because it
emits raw listings, and `ops/homeserver/run-backup-gate-evidence.sh` is
excluded because it mutates state. Neither can substitute for the missing
fixed wrapper. A separately authorized path-scoped commit and push remain
required before a later D0 user decision; neither is authorized by this record.

No environment/secret metadata, raw stdout/stderr, journal, provider response,
backup listing, snapshot ID, host output, SSH result or error text is retained
in this evidence note. If a future wrapper cannot safely emit a concrete
validated rollback snapshot ID, the output may record only
`backup_ready=false`, `rollback_snapshot_available=false`, and
`rollback_snapshot_id=null`, and it must invoke no backup command. The current
`/api/version` eight-hex `commit` is compared only with the prefix of the
independently required full 40-hex repository revision; only the boolean match
is emitted. The required next step is the separate
`GO OPS-ALERT-D0-BACKUP-SNAPSHOT-IDENTITY-READONLY-OBSERVATION as specified`
packet described in the D0 contract; D0 cannot infer or reuse that authority.
