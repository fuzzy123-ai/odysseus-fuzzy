# Ops Security Console Integration Review

Date: 2026-07-06

Status: OPS7 integration review under Standard ABC

## Scope

This review covers the repo-only and safe-offline Ops Security Console work:

- OPS1 surface inventory and shared vocabulary.
- OPS2 canonical timeline/event model.
- OPS3 adapters from existing Health, Observability and Security packets.
- OPS4 read-only Ops Console snapshot route.
- OPS5 synthetic tabletop packet.
- OPS6 live-ops runbook preparation.

It does not approve live host-agent calls, live Loki/Grafana/Prometheus
queries, alert delivery, CrowdSec/firewall/service changes, deploys, backups,
restores or remediation execution.

## Integration Map

| Area | Artifact | Evidence |
| --- | --- | --- |
| Surface vocabulary | `docs/plans/ops-security-console-contract.md` | Defines timeline stages, statuses, redaction rules and live gates |
| Timeline model | `src/ops_timeline.py` | `tests/test_ops_timeline.py` covers sorting, statuses, gate requirements and redaction |
| Source adapters | `src/ops_timeline_adapters.py` | `tests/test_ops_timeline_adapters.py` maps System Health, Observability, Security and Remediation packets |
| Snapshot contract | `src/ops_console_snapshot.py` | `tests/test_ops_console_snapshot.py` covers read-only default and gated security sources |
| Admin route | `routes/ops_console_routes.py` plus `app.py` | `tests/test_ops_console_snapshot.py` covers admin gate and route output |
| Tabletop fixture | `src/ops_tabletop_packet.py` | `tests/test_ops_tabletop_packet.py` covers synthetic packet validation and no live actions |
| Live handoff | `docs/plans/ops-security-console-live-runbook.md` | Docs-only check covers gate language and stop rules |

## Redaction And Safety Guarantees

The integrated OPS path keeps these flags false in model or route tests:

- `raw_content_visible`
- `raw_logs_visible`
- `host_paths_visible`
- `tokens_visible`
- `chat_targets_visible`
- `live_queries_performed`
- `host_commands_performed`
- `writes_performed`
- `remediation_performed`
- `live_actions_performed`

The tests also reject or hash sensitive evidence references such as private
host paths, IP-like identifiers, email-like identifiers, token markers, chat id
markers, raw log markers and raw output markers.

## Route Contract

`GET /api/ops-console/snapshot` is admin-gated through the existing
`require_admin` helper. The route calls a snapshot builder and returns the
read-only contract. It does not connect to live host, observability, alerting
or remediation systems.

Expected high-level response shape:

```text
schema: odysseus.ops_console.snapshot.v1
status: normal | watch | alert | contain | lockdown | recovery | blocked | denied
timeline: odysseus.ops_timeline.v1
source_states: system_health, diagnostics, alert_routes, security_policy, remediation
counts: timeline_events, required_gates, alert_routes, diagnostic_findings
operator_gates: tuple of gate ids
security_policy_readiness: prepare/readiness metadata
remediation_readiness: prepare-only metadata
```

## Focused Integration Suite

Use this suite for OPS7 and future regression checks:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_ops_tabletop_packet.py tests\test_ops_console_snapshot.py tests\test_ops_timeline.py tests\test_ops_timeline_adapters.py tests\test_system_health_rule_engine.py tests\test_system_health_dashboard_summary.py tests\test_observability_diagnostics_bridge.py tests\test_observability_alert_routing.py tests\test_security_incident_model.py tests\test_security_response_policy.py tests\test_security_remediation_actions.py -q
```

Expected result for OPS7: all tests pass, with only the known SQLAlchemy
deprecation warning.

## Remaining Gates

Gate: `OPS-HOST-AGENT-LIVE`
Class: needs_live_go
State after OPS7: deferred
Reason: live host metrics and host-agent runtime snapshots need an exact
operator-approved endpoint, timeout and redaction policy.

Gate: `OPS-OBSERVABILITY-LIVE-QUERY`
Class: needs_live_go
State after OPS7: deferred
Reason: live log/metrics queries need an exact target, time window and maximum
result count.

Gate: `OPS-ALERT-DELIVERY-GO`
Class: needs_live_go
State after OPS7: deferred
Reason: alert delivery needs a named channel, redacted message body and
server-side target confirmation.

Gate: `OPS-REMEDIATION-GO`
Class: needs_live_go
State after OPS7: deferred
Reason: mutation-capable actions need one action id, bounded scope, rollback or
expiry path and explicit operator Go.

## OPS7 Conclusion

The repo-only Ops Security Console path is integrated: shared vocabulary,
timeline, adapters, snapshot route, tabletop packet and live runbook exist and
are covered by a focused test suite. The roadmap is complete for safe offline
and repo-only preparation. Live host, observability, alert delivery and
remediation work remains deferred behind explicit operator gates.
