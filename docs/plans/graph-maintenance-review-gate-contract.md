# Graph Maintenance Review Gate Contract

Stand: 2026-06-17

Status: **RGM3A Docs-Contract fuer das Gate `graph_maintenance_review_gate`**

Quellen:

- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/progressive-graph-api-release-gate-contract.md`
- `docs/plans/graph-maintenance-worker-contract.md`

Dieser Contract definiert das Release-Gate fuer Graph-Maintenance vor externem `1.0.0`. Er beschreibt keinen globalen Graph-Rebuild, keinen RAPTOR-Fullbuild, keine Postgres-Live-Migration und keine Qdrant-, Kuzu- oder adRAP-Integration. Der Slice friert nur ein, wie Graph-Memory- und RAPTOR-nahe Maintenance releasefaehig review-first bleibt: abgeleitete Entity-, Edge- und Cluster-Kandidaten werden recorded, bounded, provenance-faehig und reviewbar gehalten, aber nicht automatisch als Wahrheit geschrieben.

## Purpose

`RGM3A` ist die operator-taugliche Release-Grenze fuer review-first Graph-Maintenance.

Der Contract soll beantworten:

- wann Graph-Maintenance vor externem `1.0.0` ueberhaupt als releasefaehig beschrieben werden darf
- welche Evidence fuer review-first Maintenance zwingend sichtbar bleiben muss
- wie Truth-Write-Verbote, bounded Batches und Rollback-/Operator-Schritte zusammen release-ehrlich bleiben
- welche Blocker sofort zum Stop fuehren
- wie Alice, Bob und Charlie die Gate-Sprache strikt read-only und review-first halten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen globalen Graph-Rebuild
- keinen RAPTOR-Fullbuild
- keine Postgres-Live-Migration
- keine Qdrant-, Kuzu- oder adRAP-Integration
- keine Plugin-Arbeit, keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime
- keine Provider-, Export-/Import-/Rebuild-, Telegram-, Netzwerk- oder Host-Aktionen
- keine automatische Wahrheitsschreibung

## Release-Bedeutung Von Graph-Maintenance

Die Section `graph_maintenance_release_meaning` soll die zentrale Leitplanke festhalten.

Releasefaehig bedeutet:

- Maintenance-Jobs bleiben bounded und nachvollziehbar
- Entity-, Edge- und Cluster-Kandidaten bleiben Derived oder Review-Objekte
- Provenance, Candidate-Count und Operator-Folgeaktion werden recorded
- Truth-Writes bleiben deaktiviert
- Review bleibt Pflicht statt spaeterer Randnotiz

Nicht releasefaehig ist:

- automatische Uebernahme in Wahrheit
- globaler oder unbounded Maintenance-Scope
- versteckte Runtime-Eskalation in Rebuild, Migration oder Accelerator-Pfade

## Pflicht-Evidence

Die Section `required_evidence` soll die minimalen Belege fuer dieses Release-Gate festziehen.

Pflicht-Evidence:

- `maintenance_job_recorded`
- `candidate_count_recorded`
- `provenance_recorded`
- `review_required`
- `truth_write_disabled`
- `bounded_batch_enforced`
- `rollback_plan_recorded`
- `operator_next_action_recorded`

### `maintenance_job_recorded`

Jeder Graph-Maintenance-Lauf muss als klarer Job oder Batch lesbar bleiben, nicht als unsichtbarer Dauerprozess.

### `candidate_count_recorded`

Die Zahl der abgeleiteten Entity-, Edge- oder Cluster-Kandidaten muss sichtbar sein.

### `provenance_recorded`

Sources, Chunks, Evidence oder Snapshot-Bezug duerfen nicht verlorengehen.

### `review_required`

Der Release-Gate-Status ist nur sauber, wenn Kandidaten explizit review-first bleiben.

### `truth_write_disabled`

Automatische Wahrheitsschreibung muss fuer diesen Gate-Pfad klar deaktiviert sein.

### `bounded_batch_enforced`

Maintenance darf nur in begrenzten Batches laufen, nicht global oder unbounded.

### `rollback_plan_recorded`

Es muss eine lesbare Rollback- oder Ruecknahme-Idee fuer abgeleitete Outputs geben.

### `operator_next_action_recorded`

Der naechste menschliche Schritt muss lesbar bleiben, statt implizit in Auto-Fortsetzung zu kippen.

## Operator-Taugliche Gate-Regeln

Die Section `operator_facing_gate_rules` soll die Freigabeentscheidung auf kurze, pruefbare Regeln verdichten.

Pflichtregeln:

- Maintenance bleibt review-first
- Kandidaten bleiben abgeleitet und nicht-kanonisch
- Truth-Writes bleiben deaktiviert
- Batches bleiben klein und begrenzt
- Operator-Folgeaktion oder Rollback-Idee bleiben lesbar
- kein Release-Claim darf globale oder autonome Graph-Pflege behaupten

## Verbotene Aktionen

Die Section `forbidden_actions` muss die harten Release-Blocker nennen.

Mindestens:

- `truth_write_enabled`
- `unbounded_maintenance_enabled`
- `graph_rebuild_enabled`
- `raptor_fullbuild_enabled`
- `postgres_runtime_migration_enabled`
- `qdrant_enabled`
- `kuzu_enabled`
- `research_accelerator_enabled`
- `plugin_scope_touched`
- `network_enabled`
- `unsafe_evidence_logging_enabled`

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- Plugin-Modul bleibt eingefroren und wird in diesem Gate nicht beruehrt

## Statussprache

Pflicht-Gate-ID:

- `graph_maintenance_review_gate`

Pflicht-Statuswerte:

- `graph_review_gate_ready`
- `needs_review_gate_evidence`
- `blocked`
- `deferred`

### `graph_review_gate_ready`

Graph-Maintenance kann als bounded, review-first und nicht-wahrheitsschreibend releasefaehig beschrieben werden.

### `needs_review_gate_evidence`

Mindestens eine Pflicht-Evidence zu Review, Provenance, Boundaries, Rollback oder Operator-Folgeaktion braucht noch Nachschaerfung.

### `blocked`

Mindestens ein harter Verstoss liegt vor, zum Beispiel Truth-Write, unbounded Maintenance, Rebuild-Scope oder unsafe Logging.

### `deferred`

Die Gate-Entscheidung ist bewusst verschoben und bleibt ausserhalb dieses Slices offen.

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer dieses Release-Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Release-Ehrlichkeit fuer review-first Maintenance
- Klartext: Kandidaten ja, automatische Wahrheit nein

### Bob

Bob verantwortet:

- ein isoliertes read-only Review-Gate-Modell oder Summary ueber Graph-Maintenance-Signale
- Validierung der Pflicht-Evidence und Gate-Statuswerte
- keine Wahrheitsschreibung, keine Migration, keine Accelerator-Aktivierung

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- fokussierte Tests gegen Truth-Write-, Unbounded- und Rebuild-Scope
- Stop-Entscheidung bei Scope-Drift oder unsafe Logging

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in falsche Release-Claims verhindern.

Mindestens:

- wenn Truth-Writes aktivierbar oder implizit werden: stoppen
- wenn Maintenance global oder unbounded beschrieben wird: stoppen
- wenn Graph-Rebuild oder RAPTOR-Fullbuild als Live-Default auftauchen: stoppen
- wenn Postgres-Runtime-Migration oder Accelerator-Scope in diesen Slice gezogen wird: stoppen
- wenn Plugin-Scope, Netzwerk oder unsafe Evidence-Logging auftauchen: stoppen
- wenn Candidate-Count, Provenance, Review-Pflicht oder Rollback-Idee fehlen: `needs_review_gate_evidence`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isoliertes read-only Graph-Maintenance-Review-Gate-Modell
- Validierung der Pflicht-Evidence
- Statusableitung fuer `graph_review_gate_ready`, `needs_review_gate_evidence`, `blocked`, `deferred`
- Tests mit bestehenden Graph-Maintenance-, Review- und Release-Contracts

Nicht erlaubt:

- Truth-Writes
- unbounded Maintenance
- Graph-Rebuild
- RAPTOR-Fullbuild
- Postgres-Runtime-Migration
- Qdrant-, Kuzu- oder adRAP-Aktivierung
- Plugin-, Netzwerk- oder Host-Aktionen

## Example Safe Gate Reading

Zulaessig:

- `maintenance_job_recorded = true`
- `candidate_count_recorded = true`
- `provenance_recorded = true`
- `review_required = true`
- `truth_write_disabled = true`
- `bounded_batch_enforced = true`
- `rollback_plan_recorded = true`
- `operator_next_action_recorded = true`
- `status = graph_review_gate_ready`

Nicht zulaessig:

- `truth_write_enabled = true`
- `graph_rebuild_enabled = true`
- `raptor_fullbuild_enabled = true`
- `postgres_runtime_migration_enabled = true`
- `plugin_scope_touched = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer das Graph-Maintenance-Review-Gate. Er macht review-first, bounded und provenance-gebundene Maintenance releasefaehig lesbar, ohne automatische Wahrheitsschreibung, Rebuild-Default, Migration oder Plugin-Scope.
