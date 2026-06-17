# Graph Memory Release Evidence Map Contract

Stand: 2026-06-17

Status: **RGM0A Docs-Contract fuer das Gate `graph_memory_release_evidence_map`**

Quellen:

- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/large-graph-budget-proof-contract.md`
- `docs/plans/progressive-graph-api-contract.md`
- `docs/plans/query-budget-ux-contract.md`
- `docs/plans/derived-cluster-run-contract.md`
- `docs/plans/evidence-bound-summary-worker-contract.md`
- `docs/plans/graph-maintenance-worker-contract.md`
- `docs/plans/small-model-evaluation-gates-contract.md`
- `docs/plans/fallback-routing-contract.md`

Dieser Contract definiert eine release-taugliche Evidence-Map fuer RAPTOR-/Graph-Memory. Er beschreibt nicht den Bau eines RAPTOR-Fullbuilds, keinen globalen Graph-Rebuild, keine Postgres-Live-Migration und keine neue GraphDB. Der Slice friert nur ein, wie bestehende bounded und review-first Foundations als ehrliche Release-Evidence gelesen werden sollen: vorbereitet, budgetiert, provenance-gebunden, reviewbar und mit klaren Known Limits.

## Purpose

`RGM0A` ist die klare Release-Erzaehlung fuer Graph Memory und RAPTOR-nahe Maintenance.

Der Contract soll beantworten:

- was Graph Memory im Release wirklich bedeutet
- welche vorhandenen Evidence-Bausteine schon tragfaehig sind
- wie Truth und Derived Data streng getrennt bleiben
- welche Gates fuer einen bounded Release-Zustand vorhanden sein muessen
- welche Known Limits ehrlich offen bleiben muessen

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen RAPTOR-Fullbuild
- keinen globalen Graph-Rebuild
- keine Postgres-Live-Migration
- keine neue GraphDB
- keine Accelerator-Pflicht durch Qdrant, Kuzu, UMAP, GMM oder adRAP
- keine automatische Wahrheitsschreibung durch Worker oder Modelle

## Was Graph Memory Im Release Bedeutet

Die Section `graph_memory_release_meaning` soll den Release-Rahmen klar begrenzen.

Fuer den Release bedeutet Graph Memory:

- Graph-Daten werden bounded und budgetiert gelesen
- Maintenance bleibt review-first
- Cluster, Summaries und Kandidaten bleiben Derived Data
- Entity- und Edge-Vorschlaege bleiben Review-Objekte
- kleine Modelle arbeiten nur auf vorbereiteten, kleinen Paketen
- Clipping, Partial Results, Confidence und Provenance werden sichtbar statt versteckt

Fuer den Release bedeutet Graph Memory nicht:

- dass der gesamte Graph vollstaendig geladen wird
- dass RAPTOR global live aufgebaut wird
- dass Derived Maintenance ungeprueft Wahrheit schreibt

## Vorhandene Evidence-Bausteine

Die Section `existing_evidence_building_blocks` soll die vorhandenen Foundations als lesbare Release-Bausteine ordnen.

Pflicht-Bausteine:

- `progressive_graph_api`
- `query_budgets`
- `derived_cluster_runs`
- `evidence_bound_summaries`
- `graph_maintenance_worker`
- `small_model_evaluation_gates`
- `fallback_routing`

### Progressive Graph API

Release-Relevanz:

- grosse Graphen werden als kleine Subgraphs, Aggregate und Cursors beschrieben
- `partial` und `clipped` bleiben ehrliche Produktzustande
- Full Dumps gelten nicht als releasefaehig

### Query Budgets

Release-Relevanz:

- keine unbounded Query-, Memory-, Graph- oder UI-Pfade
- `limit`, `cursor`, `time_budget_ms`, `token_budget`, `max_nodes`, `max_edges`, `depth` bleiben Pflicht
- erschoepfte oder gekappte Resultate werden sichtbar statt still abgeschnitten

### Derived Cluster Runs

Release-Relevanz:

- Cluster bleiben rebuildbare Derived Data
- Snapshot-, Scope- und Algorithmusbezug bleiben explizit
- Cluster ersetzen keine Truth-Daten

### Evidence-Bound Summaries

Release-Relevanz:

- Summaries bleiben evidence-bound, bounded und reviewbar
- keine neuen Fakten, keine Truth-Writes
- Unsicherheit fuehrt zu Review oder Fallback

### Graph Maintenance Worker

Release-Relevanz:

- Entity- und Edge-Kandidaten bleiben Candidates
- Provenance, Dedupe, Confidence und Review sind Pflicht
- `truth_write_allowed = false` bleibt feste Grenze

### Small Model Evaluation Gates

Release-Relevanz:

- kleine Modelle duerfen nur bei gueltiger Struktur, ausreichender Evidence und niedrigem Risiko helfen
- JSON, Citation-, Drift- und Halluzinations-Gates bleiben sichtbar
- Gate-Fehler fuehren zu Review oder Fallback, nicht zu stiller Fortsetzung

### Fallback Routing

Release-Relevanz:

- Default bleibt das kleine Maintenance-Modell im bounded Scope
- Fallback bleibt Ausnahme mit Gate-Grund
- Retry, Backoff und Budgetgrenzen bleiben sichtbar

## Truth Vs Derived Data

Die Section `truth_vs_derived_data` muss die zentrale Release-Leitplanke festhalten.

Pflichtaussagen:

- Cluster, Summaries, Entity-Kandidaten und Edge-Kandidaten bleiben Derived Data
- Entity- und Edge-Kandidaten bleiben Review-Objekte
- automatische Wahrheitsschreibung bleibt deaktiviert
- Review Queue bleibt der einzige Weg in Richtung spaetere Uebernahme
- Derived Outputs duerfen Wahrheit nicht still ersetzen

Wichtig:

- Graph Memory ist im Release hilfreich und vorbereitet
- Graph Memory ist im Release nicht autonom wahrheitsschreibend

## Release Gates

Die Section `release_gates` soll die minimalen Graph-Memory-Gates fuer einen ehrlichen Release-Status benennen.

Pflicht-Gates:

- `budgets_present`
- `provenance_present`
- `review_required`
- `truth_write_disabled`
- `unbounded_fullbuild_disabled`
- `accelerator_optional_post_release`

### `budgets_present`

Nur releasefaehig, wenn Query-, Graph- und Worker-Pfade klar begrenzt sind.

### `provenance_present`

Nur releasefaehig, wenn Cluster, Summaries, Entity- und Edge-Kandidaten auf Scope, Snapshot, Source, Chunk oder Evidence zurueckfuehrbar bleiben.

### `review_required`

Nur releasefaehig, wenn riskante oder unklare Derived Outputs in Review muenden statt still uebernommen zu werden.

### `truth_write_disabled`

Nur releasefaehig, wenn automatische Wahrheitsschreibung fuer Graph-Maintenance deaktiviert bleibt.

### `unbounded_fullbuild_disabled`

Nur releasefaehig, wenn kein unbounded RAPTOR-Fullbuild und kein globaler Graph-Rebuild als Live-Default angenommen wird.

### `accelerator_optional_post_release`

Nur releasefaehig, wenn Qdrant, Kuzu, UMAP, GMM oder adRAP klar optional und post-release bleiben.

## Known Limits Fuer Externes Release

Die Section `known_limits_for_external_release` soll die ehrlichen Grenzen sichtbar halten.

Mindestens:

- kein RAPTOR-Fullbuild live
- kein globaler Graph-Rebuild live
- keine neue GraphDB als Release-Pflicht
- kein automatischer Truth-Write fuer Entity-/Edge-Kandidaten
- keine Accelerator-Pflicht
- grosse Graphen werden nur ueber budgetierte Ausschnitte und Aggregate getragen
- kleine Maintenance-Modelle arbeiten bounded und review-first, nicht als globale Denkzentrale

Wichtig:

- Known Limits sind Teil der Release-Ehrlichkeit
- sie sind keine spaet still zu entfernenden Fussnoten

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Release-Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Release-Erklaerung fuer bounded Graph Memory
- Truth-vs-Derived-Klarheit
- Known-Limits-Formulierungen

### Bob

Bob verantwortet:

- eine read-only Evidence-Map ueber bestehende Graph-Memory-Modelle und Tests
- Aggregation der vorhandenen bounded/review-first Signale
- keine neuen Runtime- oder Storage-Pfade

Wichtig:

- Bob darf keinen Fullbuild, keinen globalen Rebuild und keine Wahrheitsschreibung aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- fokussierte Tests und Evidence-Abgleich
- Known-Limits-Abgleich gegen Release-Checkliste
- Stop-Entscheidung bei Fullbuild-, Truth-Write- oder Accelerator-Scope-Drift

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in falsche Release-Claims verhindern.

Mindestens:

- wenn Graph-Kandidaten als automatische Wahrheit beschrieben werden: stoppen
- wenn Budgets, Provenance oder Review-Pfade fehlen: `blocked`
- wenn ein Fullbuild oder globaler Rebuild als Live-Default behauptet wird: stoppen
- wenn Postgres-Live-Migration oder neue GraphDB in den Slice geschoben werden: stoppen
- wenn Qdrant, Kuzu, UMAP, GMM oder adRAP als Release-Pflicht dargestellt werden: stoppen
- wenn Derived Outputs Truth ersetzen sollen: stoppen

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- eine isolierte read-only Graph-Memory-Release-Evidence-Map
- Aggregation der vorhandenen Evidence-Bausteine
- Statusableitung fuer `evidence_map_ready`, `needs_release_review`, `blocked`, `deferred`
- Tests mit bestehenden read-only Modellen, Fixtures und Contract-Signalen

Nicht erlaubt:

- RAPTOR-Fullbuild
- globaler Graph-Rebuild
- Truth-Writes
- Postgres-Live-Migration
- neue GraphDB
- Accelerator-Pflicht

Pflicht-Gate-ID:

- `graph_memory_release_evidence_map`

Pflicht-Statuswerte:

- `evidence_map_ready`
- `needs_release_review`
- `blocked`
- `deferred`

## Example Safe Release Reading

Zulaessig:

- `graph_memory_release_meaning = bounded, review-first, evidence-backed`
- `truth_vs_derived = candidates stay review objects`
- `release_gates = budgets present, provenance present, truth writes disabled`
- `status = needs_release_review`

Nicht zulaessig:

- `full_raptor_build_live = true`
- `global_graph_rebuild_enabled = true`
- `candidate_truth_write_allowed = true`
- `accelerators_required_for_release = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer Graph Memory und RAPTOR-nahe Maintenance. Er macht vorbereitete bounded Foundations lesbar, ohne daraus einen Fullbuild, eine neue GraphDB, eine Live-Migration oder automatische Wahrheitsschreibung zu behaupten.
