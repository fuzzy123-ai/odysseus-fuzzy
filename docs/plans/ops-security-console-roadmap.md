# Ops Security Console Roadmap

Status: repo-only complete under Standard ABC; live gates deferred

ABC mode: Standard ABC

## Goal

Unify System Health, Observability, Diagnostics and Security Ops into one
read-only-first ops model with incident timeline, alert routing, redacted
diagnostics and confirmation-gated remediation.

## Current Evidence

- System Health modules include basic/advanced collectors, rule engine,
  auto alerting, container runtime, dashboard summary, Telegram pull and plugin
  readiness artifacts.
- Observability modules include metrics, clients, alert routing and diagnostic
  bridge.
- Security modules include incident model, anomaly classifier, notifications,
  response policy and remediation actions.
- `plugins/system_health_checker/plugin.py` explicitly avoids host commands
  from Odysseus core.
- OPS1 now provides `docs/plans/ops-security-console-contract.md`, mapping
  System Health, Observability diagnostics, alert routing, security incidents,
  response policy and remediation planning into one read-only-first timeline,
  status, redaction and gate vocabulary.
- OPS2 now provides `src/ops_timeline.py`, a side-effect-free
  `odysseus.ops_timeline.v1` timeline packet and
  `odysseus.ops_timeline.event.v1` event model that sorts read-only ops
  events, hashes sensitive evidence/correlation references, rejects raw
  summaries and requires explicit gates for containment/lockdown events.
- OPS3 now provides `src/ops_timeline_adapters.py`, mapping existing System
  Health dashboard summaries, Observability diagnostic packets, alert routes,
  Security Incident payloads, Security Response Policy decisions and
  prepare-only Remediation plans into canonical timeline events without live
  queries, host commands or writes.
- OPS4 now provides `src/ops_console_snapshot.py` and
  `routes/ops_console_routes.py`, exposing the admin-gated
  `GET /api/ops-console/snapshot` route as a read-only snapshot over the
  canonical timeline, source states, operator gates and security/remediation
  readiness packets.
- OPS5 now provides `src/ops_tabletop_packet.py`, a synthetic
  `odysseus.ops_tabletop_packet.v1` fixture packet over the Ops Console
  snapshot that records expected operator steps, assertions and required gates
  without live checks, host commands, writes or remediation execution.
- OPS6 now provides `docs/plans/ops-security-console-live-runbook.md`, a
  docs-only live-ops preparation runbook with exact Go/Partial/Deferred/No-Go/
  Blocked language, gate-specific required inputs, stop rules and handoff
  cards. It does not grant live permission or execute any smoke.
- OPS7 now provides `docs/plans/ops-security-console-integration-review.md`,
  mapping the OPS1-OPS6 artifacts to the focused integration suite and
  documenting the remaining live gates as deferred.
- Current rework need: these systems should share timeline, action, evidence
  and redaction semantics.

## Mode

Standard ABC. Repo-only for models and route contracts. Host metrics, Loki,
Grafana, CrowdSec, remediation, service restart or lockdown require live Go.

## Non-goals

- Do not run host commands from Odysseus core.
- Do not enable remediation automatically.
- Do not query live Loki/Grafana/CrowdSec without explicit operator Go.
- Do not expose raw logs, private paths, IPs beyond policy, tokens or chat ids.

## What Must Be Done

- Define one ops event and incident timeline model.
- Connect system health alerts, observability findings and security incidents.
- Make remediation action lifecycle explicit: proposed, prepared, approved,
  denied, executed, expired, blocked.
- Add redacted diagnostic packet references instead of raw logs.
- Add route contract for ops console snapshot.
- Keep host-agent boundary as the only host data path.
- Add tabletop test fixtures.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| OPS1 surface inventory | safe_offline | Alice | roadmap and ops console contract | Done: `docs/plans/ops-security-console-contract.md` |
| OPS2 timeline model | repo_only | Bob | `src/ops_timeline.py`, tests | Done: `tests/test_ops_timeline.py` |
| OPS3 incident/action adapters | repo_only | Bob | observability/security/system health adapters | Done: `tests/test_ops_timeline_adapters.py` |
| OPS4 route snapshot | repo_only | Bob | route module and tests | Done: `tests/test_ops_console_snapshot.py` |
| OPS5 tabletop packet | repo_only | Bob | tabletop fixture/model | Done: `tests/test_ops_tabletop_packet.py` |
| OPS6 live ops runbook | needs_live_go | Alice | docs only | Done: `docs/plans/ops-security-console-live-runbook.md` |
| OPS7 integration | repo_only | Charlie | tests/docs | Done: `docs/plans/ops-security-console-integration-review.md` |

## Execution Progress

2026-07-06:
- OPS1 surface inventory done as a docs-only safe_offline slice.
  `docs/plans/ops-security-console-contract.md` maps System Health dashboard
  summaries, the System Health plugin host boundary, Observability diagnostic
  packets, alert-route dry-runs, Security Incident payloads, Security Response
  Policy decisions and prepare-only remediation plans into a canonical signal
  -> triage -> evidence -> decision -> action-plan -> operator-gate -> handoff
  timeline with redaction rules and explicit live gates.
- OPS1 verification passed: docs-only scoped whitespace/diff checks.
- OPS2 timeline model done as a repo_only slice. `src/ops_timeline.py`
  defines the canonical read-only ops timeline/event model for signal, triage,
  evidence, decision, action-plan, operator-gate and handoff stages across
  System Health, Observability, Diagnostics, Security and Remediation surfaces.
  It keeps raw content/logs/host paths/live actions/writes disabled, hashes
  sensitive refs and rejects unsafe summaries.
- OPS2 verification passed: compile plus `tests/test_ops_timeline.py` with 5
  tests passed and the known SQLAlchemy deprecation warning.
- OPS3 incident/action adapters done as a repo_only slice.
  `src/ops_timeline_adapters.py` composes already-redacted System Health,
  Observability, Security Policy and Remediation source packets into canonical
  Ops Timeline events, preserves operator-gate requirements for containment and
  remediation actions, hashes sensitive legacy evidence references and keeps
  writes/live actions/host commands disabled.
- OPS3 verification passed: compile plus `tests/test_ops_timeline.py` and
  `tests/test_ops_timeline_adapters.py` with 10 tests passed and the known
  SQLAlchemy deprecation warning.
- OPS4 route snapshot done as a repo_only slice. `src/ops_console_snapshot.py`
  builds a conservative read-only Ops Console snapshot from canonical timeline,
  source states, gate counts, Security Response Policy readiness and
  prepare-only Remediation readiness, while keeping raw content/logs/host
  paths/tokens/chat targets/live queries/host commands/writes/remediation
  disabled. `routes/ops_console_routes.py` exposes
  `GET /api/ops-console/snapshot` behind the existing admin gate and `app.py`
  registers the route.
- OPS4 verification passed: compile plus `tests/test_ops_console_snapshot.py`
  with 5 tests passed and the known SQLAlchemy deprecation warning.
- OPS5 tabletop packet done as a repo_only slice. `src/ops_tabletop_packet.py`
  builds and validates deterministic synthetic tabletop packets from
  Security Incident, Response Policy, prepare-only Remediation and Ops Console
  snapshot contracts. The packet records expected operator steps, assertions,
  policy/remediation decisions and required live gates while keeping raw
  content/logs/host paths/tokens/chat targets/live actions/host commands/
  writes/remediation disabled.
- OPS5 verification passed: compile plus `tests/test_ops_tabletop_packet.py`
  with 5 tests passed and the known SQLAlchemy deprecation warning.
- OPS6 live ops runbook done as a docs-only preparation slice.
  `docs/plans/ops-security-console-live-runbook.md` names
  `OPS-HOST-AGENT-LIVE`, `OPS-OBSERVABILITY-LIVE-QUERY`,
  `OPS-ALERT-DELIVERY-GO` and `OPS-REMEDIATION-GO`, defines required operator
  decisions, safe preparation evidence, risk if bypassed, allowed redacted
  results, stop rules and future live/No-Go handoff cards. No live action was
  approved or performed.
- OPS6 verification passed: docs-only scoped whitespace/diff checks.
- OPS7 integration done as a repo_only tests/docs slice.
  `docs/plans/ops-security-console-integration-review.md` maps surface
  vocabulary, timeline model, adapters, snapshot route, tabletop packet and
  live runbook to the focused OPS suite, records redaction and no-live-action
  guarantees, and keeps host-agent, observability live query, alert delivery
  and remediation work deferred behind explicit operator gates.
- OPS7 verification passed: focused OPS integration suite across tabletop,
  snapshot, timeline, adapters, System Health, Observability and Security
  contracts with 63 tests passed and the known SQLAlchemy deprecation warning,
  plus docs-only whitespace/diff checks.

## Gate Queue

Gate: `OPS-HOST-AGENT-LIVE`
Class: needs_live_go
Blocks: live host metrics, Podman metrics, SMART/update/reboot checks
Decision needed: approve host-agent endpoint and bounded snapshot
Safe preparation done: offline snapshot contract
Risk if bypassed: host path/secret leakage or unsafe host command execution
Next safe slice: fixture tests

Gate: `OPS-REMEDIATION-GO`
Class: needs_live_go
Blocks: CrowdSec, firewall, service restart, rollback or lockdown action
Decision needed: approve one exact action with rollback
Safe preparation done: proposed/prepared action packet
Risk if bypassed: service disruption or lockout
Next safe slice: tabletop dry-run

## Paths

Alice path:
- define operator language and live ops runbook
- write incident/tabletop expectations

Bob path:
- implement timeline and adapter models
- build read-only snapshot route

Charlie path:
- enforce no host commands from core
- run health/observability/security tests

## Verification

- `pytest tests/test_system_health_rule_engine.py`
- `pytest tests/test_system_health_dashboard_summary.py`
- `pytest tests/test_observability_diagnostics_bridge.py`
- `pytest tests/test_observability_alert_routing.py`
- `pytest tests/test_security_incident_model.py`
- `pytest tests/test_security_response_policy.py`
- `pytest tests/test_security_remediation_actions.py`
- `git diff --check`

## Go Language

- Go: ops snapshot combines health, observability and security in read-only
  redacted form.
- Partial: snapshot exists but live host/observability sources are deferred.
- Deferred: remediation and host metrics wait for operator Go.
- No-Go: core executes host commands or remediation without explicit approval.
