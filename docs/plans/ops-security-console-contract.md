# Ops Security Console Contract

Date: 2026-07-06

Status: OPS1 docs-only safe_offline

## Goal

Define one read-only-first console vocabulary for System Health, Observability,
Diagnostics and Security Ops without executing host commands, live
observability queries or remediation actions.

## Scope

This contract is documentation only. It does not query live Loki, Grafana,
Prometheus, CrowdSec, host agents or Podman. It does not restart services,
change firewall rules, pause schedulers, rotate tokens, roll back deploys,
send alerts or expose raw logs.

## Existing Surfaces

| Surface | Existing evidence | Current role |
| --- | --- | --- |
| System health dashboard | `src/system_health_dashboard_summary.py` | Summarizes collector, alert and readiness sections into redacted dashboard states. |
| System health plugin | `plugins/system_health_checker/plugin.py` | Plugin boundary that must avoid host commands from core. |
| Observability diagnostic bridge | `src/observability_diagnostics_bridge.py` | Maps operator questions to redacted diagnostic packets from metrics, summaries and alert routes. |
| Observability alert routing | `src/observability_alert_routing.py` | Prepares dry-run alert routes and notification decisions without delivery. |
| Security incident model | `src/security_incident_model.py` | Builds redacted incident and recommended-action payloads. |
| Security response policy | `src/security_response_policy.py` | Classifies actions as observe, diagnose, recommend, gated, blocked or denied without execution. |
| Security remediation planner | `src/security_remediation_actions.py` | Prepares bounded remediation plans while keeping writes and execution false. |

## Canonical Timeline Model

| Stage | Meaning | Safe payload rule |
| --- | --- | --- |
| signal | Health, metric, alert, diagnostic or incident candidate is observed. | Use metric names, dedupe keys, status tokens and hashed refs, not raw logs. |
| triage | Severity, confidence, affected surfaces and current state are normalized. | No raw IPs, emails, host paths, tokens, chat IDs or private content. |
| evidence | Redacted diagnostic packet or evidence ref is linked. | Store refs and counts only; raw content remains invisible. |
| decision | Policy chooses observe, diagnose, recommend, gated_action, blocked or denied. | `allowed_to_execute` stays false in console snapshots. |
| action_plan | Remediation or notification is prepared. | Prepare-only; dry-run true; writes false; operator gate explicit. |
| operator_gate | Human approval is required for host, external, remediation or live alert action. | Exact action, rollback/hold and stop rules required. |
| handoff | Operator gets status, evidence refs, blocked gates and next safe action. | Handoff is redacted and quotes no private output. |

## Canonical Status Tokens

| Status | Meaning |
| --- | --- |
| normal | No attention required from current evidence. |
| watch | Low-risk anomaly or stale signal needs observation. |
| alert | Operator attention is recommended. |
| contain | Containment recommendation exists but execution is gated. |
| lockdown | High-risk state; live remediation still requires explicit Go. |
| recovery | Follow-up/recovery evidence is needed. |
| blocked | Required evidence, live gate or safe boundary is missing. |
| denied | Requested action is outside allowed defensive scope. |

## Redaction Rules

- Raw logs, raw prompts, raw outputs, provider responses and private documents
  are never included in console payloads.
- Host paths, emails, IPs, tokens, API keys, cookies, chat IDs and passwords are
  rejected or hashed by surface-specific models.
- Diagnostic packets should carry evidence refs, metric names, counts, dedupe
  keys and sanitized summaries.
- Public payloads must include booleans such as `raw_content_visible=False`,
  `writes_performed=False`, `delivery_performed=False` or
  `allowed_to_execute=False` where applicable.

## Gate Queue

Gate: `OPS-HOST-AGENT-LIVE`
Class: needs_live_go
Blocks: live host metrics, Podman metrics, SMART/update/reboot checks
Safe preparation: offline snapshot contracts and fixture packets.
Risk if bypassed: host path leakage or unsafe host command execution.

Gate: `OPS-OBSERVABILITY-LIVE-QUERY`
Class: needs_live_go
Blocks: live Loki, Grafana, Prometheus or CrowdSec queries from Odysseus core
Safe preparation: diagnostic packets from already-redacted snapshots and
fixtures.
Risk if bypassed: raw logs, private identifiers or provider output leak into
the app.

Gate: `OPS-REMEDIATION-GO`
Class: needs_live_go
Blocks: CrowdSec, firewall, service restart, scheduler mutation, token
rotation, Cloudflare tunnel change, deploy rollback or lockdown action
Safe preparation: proposed/prepared remediation plans with
`allowed_to_execute=False`.
Risk if bypassed: service disruption, lockout or destructive response.

Gate: `OPS-ALERT-DELIVERY-GO`
Class: needs_live_go
Blocks: live Telegram or external notification dispatch
Safe preparation: dry-run notification decisions and alert route previews.
Risk if bypassed: noisy or misdirected alerts with private context.

## Compatibility Rules

- Keep the System Health plugin as the boundary for host-facing data.
- Keep Observability alert routing dry-run unless alert delivery is explicitly
  approved.
- Keep Security remediation prepare-only until an exact operator gate is Go.
- Do not merge raw diagnostic logs into incident or timeline payloads.
- Route snapshots may be added additively, but must not imply host-agent or
  remediation live readiness.

## OPS1 Done Definition

- Existing health, observability, diagnostic and security surfaces are mapped.
- Canonical timeline stages, statuses, redaction rules and gates are defined.
- Later OPS2-OPS5 implementation can add models, adapters, routes and tabletop
  fixtures without guessing live-action boundaries.
- No host, observability, alert-delivery or remediation action is performed.
