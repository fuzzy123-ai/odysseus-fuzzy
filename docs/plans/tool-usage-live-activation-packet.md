# Tool Usage Analytics Live Activation Packet

Status: repository-ready, live-gated
Gate: `TUA-LIVE-ACTIVATION`
`activation_authorized=false`

This packet records the requirements for a later bounded activation. It does
not enable capture, open a production store, run a real historical backfill,
export telemetry, restart a service, deploy, or authorize any live action.

## Version And Environment Template

- Analytics version: `<version>`
- Target environment: `<environment>`
- Capture default: `off`
- Activation control: `<feature-switch-or-version>`
- Rollback target: `<disabled-feature-switch-or-prior-version>`
- Observation window: `<bounded-window>`

Every placeholder must be replaced in the later action-specific GO. Repository
acceptance alone cannot infer an environment, switch, version, or rollback
target.

## Retention And Admin Scope

- Event retention: 90 days
- Aggregate retention: 400 days
- Admin read scope: `<role>`
- Raw-event drilldown: unavailable
- External telemetry export: disabled
- Deferred tools: unchanged and default-off

Retention applies only after a separately authorized persistent writer exists.
Rollback stops new writes but does not automatically delete previously accepted
content-free aggregates. Any later deletion is a separate scoped operation.

## Optional Legacy Backfill

- Historical backfill default: `no`
- Repository evidence: fixed synthetic source, immutable dry-run, count-only
  rejection of unsafe rows, no apply mode
- Later allowed choice: `no` or one explicit bounded period
- Real local inventory read: forbidden until the GO states the period
- Backfill writer: unavailable until the same activation boundary is approved

Omitting the backfill choice means `no`.

## Pre-Activation Checks

1. TAX10 analytics identity and alias contract remains stable.
2. TUA1 through TUA12 commits and focused evidence are present.
3. Privacy, Incognito, dedupe, retention, performance, failure-isolation,
   Admin aggregate and low-cardinality metrics tests are green.
4. The synthetic rollout command passes with default-off, Incognito no-write,
   and rollback assertions.
5. The chosen environment, Admin role, observation window and rollback control
   are concrete and redacted.
6. E-mail, calendar, contacts and all other deferred tools remain disabled.

Required repository command:

```powershell
venv\Scripts\python.exe scripts\verify_tool_usage_rollout.py --mode synthetic --assert-default-off --assert-incognito-no-write --assert-rollback
```

## Monitoring And Budgets

During a later bounded observation window monitor only aggregate technical
signals:

- coverage and incomplete-invocation rate;
- duplicate rejection, unknown identity and writer failure counts;
- privacy rejection and Incognito write counts;
- bounded family/source/surface/status cardinality;
- writer p95 below 5 ms without a simulated storage stall;
- Admin aggregate availability and low-cardinality metrics projection.

Do not persist raw arguments, results, prompts, exception text, direct owner or
session identifiers, private locations, or external provider payloads.

## Abort Criteria

Abort the later activation and disable new capture when any of these occurs:

- any raw content or direct identifier can reach persistence or output;
- Incognito produces a persistent event or aggregate;
- duplicate, unknown-identity or writer-failure counts exceed the approved
  bounded budget;
- event/source/status cardinality becomes unbounded;
- writer p95 reaches or exceeds 5 ms without an approved documented exception;
- tool execution changes because a writer, store, aggregator or exporter fails;
- Admin scope, retention or rollback state differs from the approved packet.

No automatic destructive action is authorized by an abort.

## Rollback

1. Disable the exact capture control named in the GO.
2. Verify a synthetic tool call still returns the unchanged result with capture
   disabled.
3. Verify new usage-event writes stop, including all Incognito paths.
4. Preserve accepted content-free aggregates for read-only inspection unless a
   separate deletion decision is approved.
5. Keep real backfill and external export disabled.
6. Record only aggregate technical evidence and the selected prior version or
   disabled control.

Rollback must not run a destructive database operation, delete unrelated data,
enable a deferred tool, or imply deployment authority.

## Dormant GO Template

This is the only later activation phrase. It is dormant until every placeholder
is concrete and the user sends it explicitly:

```text
GO TUA-LIVE: Aktiviere metadata-only Tool Analytics <Version> in <Umgebung>
mit 90 Tagen Event- und 400 Tagen Aggregat-Retention, Admin-Scope <Rolle>,
historischem Backfill <nein/Zeitraum> und Rollback ueber <Feature-Schalter/Version>.
```

The phrase grants only the named target and controls. It does not grant a
provider write, external export, deferred-tool activation, unrelated deployment,
data deletion, force-push, or unbounded soak.
