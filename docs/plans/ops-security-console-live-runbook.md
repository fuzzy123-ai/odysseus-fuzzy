# Ops Security Console Live Runbook

Date: 2026-07-06

Status: OPS6 docs-only preparation under Standard ABC

## Purpose

This runbook defines how an operator may later approve one bounded live Ops
Security Console smoke. It is not approval by itself. It does not run host
commands, query live observability systems, send alerts, execute remediation,
restart services, mutate firewall/CrowdSec state, deploy, back up or restore.

## Required Preflight Evidence

Before any live smoke can be considered, the operator must review:

- `GET /api/ops-console/snapshot` output from the current app instance.
- A synthetic `odysseus.ops_tabletop_packet.v1` tabletop packet.
- The exact gate id being requested.
- The exact target class: host-agent snapshot, observability query, alert
  delivery dry-run promotion, or remediation prepare/execute handoff.
- The exact rollback or stop path for any mutation-capable action.
- Confirmation that no token, chat id, private path, raw log, private document,
  private email body or provider output will be persisted in repo artifacts.

## Gate Language

Use these words exactly:

- Go: run only the named smoke, with the named target, bounded timeout, and
  stated rollback/stop path.
- Partial: collect only read-only metadata; do not execute delivery or
  remediation.
- Deferred: keep the prepared packet and revisit later.
- No-Go: do not run the smoke; record the reason.
- Blocked: proceeding would require secrets, raw private content, missing
  rollback, unclear target, destructive action, or an unbounded command.

## Gate: OPS-HOST-AGENT-LIVE

Class: needs_live_go

Blocks: live host metrics, Podman metrics, SMART/update/reboot checks.

Decision needed:

- Approve one exact host-agent endpoint or fixture source.
- Approve one timeout.
- Approve one redaction policy for returned fields.
- Confirm that Odysseus core will not execute host commands.

Safe preparation done:

- OPS1 contract defines the host-agent boundary.
- OPS2 timeline model stores only redacted events.
- OPS4 snapshot route is read-only.
- OPS5 tabletop packet proves operator-gate behavior with synthetic data.

Risk if bypassed:

- Host path, service name, command output or secret-bearing metadata could be
  exposed or over-trusted.

Allowed result:

- One redacted snapshot reference may be recorded.
- No raw host paths or raw command output may be persisted.

## Gate: OPS-OBSERVABILITY-LIVE-QUERY

Class: needs_live_go

Blocks: Loki, Grafana, Prometheus or similar live queries.

Decision needed:

- Approve one exact query target.
- Approve one time window.
- Approve one maximum result count.
- Confirm whether the result is metadata-only or includes log snippets.

Safe preparation done:

- Observability diagnostic packets and alert routes already support redacted
  findings and dry-run alert routing.
- OPS timeline and snapshot contracts can reference redacted evidence without
  raw logs.

Risk if bypassed:

- Raw logs, IPs, user identifiers, private paths or provider output could leak
  into diagnostics.

Allowed result:

- A redacted diagnostic packet and evidence reference.
- No raw log lines in docs, tests, commits or handoff.

## Gate: OPS-ALERT-DELIVERY-GO

Class: needs_live_go

Blocks: Telegram, email, ntfy or external alert delivery.

Decision needed:

- Approve one channel.
- Approve one redacted message body.
- Confirm the server-side target is configured without exposing target values.
- Confirm delivery is necessary rather than dry-run notification preview.

Safe preparation done:

- Alert routing prepares dry-run notification decisions by default.
- User notification contracts hide token and chat-target values.

Risk if bypassed:

- A notification could be sent to the wrong target or include private incident
  detail.

Allowed result:

- Delivery status, reason and redacted correlation id.
- No token, chat id, recipient, private target or raw message source.

## Delivery activation-packet binding

`OPS-ALERT-DELIVERY-GO` remains open and this runbook grants no Go. A later
operator decision may cover only one send: one channel target class, one
server-side-ready opaque target, one redacted body, one attempt timeout and
one single-use grant expiry. The packet must include redacted preflight
evidence, a no-retry recovery path, independent redacted delivery-status or
correlation readback, stop rules for target/body drift, private content,
timeout, retry expansion or withdrawal, and a final status. It is independent
from observe, remediation, session and deployment results.

## Gate: OPS-REMEDIATION-GO

Class: needs_live_go

Blocks: CrowdSec, firewall, reverse proxy, service restart, scheduler action,
rollback, lockdown or deploy-related remediation.

Decision needed:

- Approve one exact action id from the remediation plan.
- Approve the bounded scope.
- Approve the rollback or expiry path.
- Confirm confidence threshold and incident mode.
- Confirm the operator understands this is mutation-capable.

Safe preparation done:

- Security Response Policy classifies gated actions without execution.
- Remediation plans are prepare-only by default.
- OPS5 tabletop packets prove approved gates still do not execute inside the
  repo contract.

Risk if bypassed:

- Service disruption, lockout, firewall mistakes, incident evidence loss or
  unreviewed mutation.

Allowed result:

- A handoff card describing action id, decision, status, rollback and evidence.
- Actual execution only in a separate explicitly approved live run.

Activation-packet boundary:

- `OPS-REMEDIATION-GO` does not substitute for `crowdsec-remediation-go` or
  `mcp-remediation-tools-go` for a CrowdSec action. The single action packet
  needs opaque scope, TTL, timeout, grant expiry, false-positive/lockout
  review, rollback/expiry, independent redacted readback and abort rules.
- It never authorizes session invalidation. That requires the separate
  `security-incident-session-invalidation-go` plus
  `mcp-remediation-tools-go` packet for one non-operator test session, with
  recovery and independent redacted session-state readback.
- It never authorizes deployment or deploy rollback. `deploy-live-go` remains
  an independent later decision with its own target, rollback and readback.

## Activation packet boundaries

`docs/plans/security-incident-response-activation-packet.md` grants no Go and
binds any future request to one target class, exact bounded scope, timeout,
single-use grant expiry, redacted evidence, recovery, independent readback,
abort conditions, later operator decision and final status.

Read-only observe needs the distinct canonical gates
`observability-live-smoke-go`, `debian-observability-live-go` and
`log-retention-policy-go`. Its one source/query, time window/result limit and
timeout are read-only; it cannot send alerts or mutate state. Debian readiness
may reference only `ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe` and its fixed redacted JSON projection.

Temporal closure is separately gated by
`security-incident-temporal-closure-go`: one approved start/end observation
window and named canary evidence references, with expiry/retry/readback/audit
evidence, independent review and stop on any evidence gap or new action
family. It grants no lockdown, delivery, remediation, session action or
deployment.

## Stop Rules

Stop immediately if any of these appears:

- A secret, token, chat id, password, cookie, private path, raw log, private
  document text, private email body or provider output would be copied into
  docs, tests, commits, prompts or handoff.
- The target host, channel, query, action id, timeout or rollback is ambiguous.
- A command would be destructive or unbounded.
- The operator asks for broad remediation rather than one named action.
- A live run would require deploy, backup, restore, firewall or service changes
  that are not explicitly named.
- The current repo worktree has unrelated staged files.

## Live Handoff Card

Use this shape after a future approved live smoke:

```text
Gate:
Decision:
Target class:
Bounded target:
Timeout:
Rollback/stop path:
Live action performed: yes | no
Mutation performed: yes | no
Redacted evidence ref:
Snapshot/timeline event ref:
Result:
Residual risk:
Next safe action:
```

## No-Go Handoff Card

Use this shape when a future live smoke is denied or blocked:

```text
Gate:
Status: no-go | blocked | deferred
Reason:
Safe preparation already available:
Risk if bypassed:
Next safe repo-only slice:
```

## OPS6 Done Definition

- This runbook exists as a repo-only preparation artifact.
- It names all current OPS live gates and their required operator decisions.
- It separates preparation from live permission.
- It includes stop rules and handoff cards.
- It does not include secrets, private paths, raw logs, chat ids or host
  commands.
