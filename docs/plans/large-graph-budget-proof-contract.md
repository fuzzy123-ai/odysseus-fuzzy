# Large Graph Budget Proof Contract

Stand: 2026-06-17

Status: **RGM1A Docs-Contract fuer das Gate `large_graph_budget_proof`**

Quellen:

- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/graph-memory-release-evidence-map-contract.md`
- `docs/plans/progressive-graph-api-contract.md`

Dieser Contract definiert die Release-Sprache fuer einen 100.000+-Graph-Budget-Proof. Der Slice beschreibt keinen echten Vollgraph-Load, keinen UI-Vollrender, keinen globalen Graph-Rebuild, keine Postgres-Live-Migration und keine neue GraphDB. Er friert nur ein, wie grosse Graph-Mengen synthetisch oder fixture-basiert nachgewiesen werden duerfen, waehrend Ausgaben klein, budgetiert, cursor- oder aggregate-basiert und ehrlich als `partial` oder `clipped` markiert bleiben.

## Purpose

`RGM1A` ist die Release-Erklaerung fuer grosse Graph-Mengen ohne Full Dump.

Der Contract soll beantworten:

- was ein 100.000+-Proof im Release wirklich bedeutet
- welche Evidence fuer grosse Graph-Mengen zulaessig und nuetzlich ist
- welche Ausgaben trotz grossem Input bewusst klein bleiben muessen
- welche Aktionen fuer diesen Proof strikt verboten sind
- wie Alice, Bob und Charlie den Proof bounded und reviewbar halten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen Full Graph Dump
- keinen UI-Rendervollgraph
- keinen globalen Graph-Rebuild
- keine Postgres-Live-Migration
- keine neue GraphDB
- keine Qdrant-, Kuzu- oder adRAP-Pflicht
- keine Plugin-, Telegram-, Netzwerk- oder Host-Aktionen

## Definition Von 100.000+ Proof

Die Section `definition_of_100k_plus_proof` soll die zentrale Release-Leitplanke festhalten.

Ein 100.000+-Proof bedeutet:

- grosse Graph-Mengen werden ueber Zaehler, Generator oder Fixture nachgewiesen
- die Eingabegroesse darf 100.000 oder mehr Knoten/Kanten repräsentieren
- der Proof belegt, dass Outputs trotzdem klein und budgetiert bleiben
- der Proof zeigt keine Vollmenge in UI, Chat oder API

Ein 100.000+-Proof bedeutet nicht:

- dass 100.000 Knoten an UI oder Chat ausgegeben werden
- dass ein ganzer Graph live geladen oder gerendert wird
- dass ein globaler Rebuild oder Accelerator noetig wird

## Erlaubte Evidence

Die Section `allowed_evidence` soll die zulaessigen und produktiv hilfreichen Nachweise benennen.

Pflicht-Evidence:

- `node_count`
- `edge_count`
- `requested_count`
- `returned_count`
- `clipped_count`
- `budget`
- `cursor`
- `aggregate_summary`
- `reason`
- `next_action`

Wichtig:

- `node_count` und `edge_count` beschreiben die grosse Eingabemenge
- `returned_count` beschreibt nur die kleine sichtbare Antwort
- `clipped_count` macht sichtbar, was bewusst nicht voll ausgespielt wurde
- `aggregate_summary` darf groeßere Lage erklaeren, ohne Vollmengen zu dumpen

## Output-Grenzen

Die Section `output_boundaries` soll erklaeren, wie trotz grosser Inputs nur kleine Antworten releasefaehig bleiben.

Pflichtregeln:

- Outputs bleiben budgetiert und klein
- Cursor oder Aggregate tragen die Fortsetzung
- `partial` oder `clipped` werden ehrlich markiert
- `reason` und `next_action` erklaeren die Begrenzung
- UI, Chat und API sehen nur kleine Ausschnitte oder Zusammenfassungen

Wichtig:

- ein grosser Input rechtfertigt keinen grossen Payload
- der Proof misst Skalierungsdisziplin, nicht Render-Masse

## Verbotene Aktionen

Die Section `forbidden_actions` muss die harten Grenzen fuer diesen Proof nennen.

Mindestens:

- Full Graph Dump
- UI-Rendervollgraph
- Qdrant
- Kuzu
- adRAP
- Postgres-Live-Migration
- Graph-Rebuild
- Plugin-Scope
- Telegram-Aktionen
- Netzwerkaktionen
- Host-Kommandos

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- ein Large-Graph-Proof bleibt read-only, synthetisch oder fixture-basiert

## Release Gates

Die Section `release_gates` soll die minimalen Gates fuer einen ehrlichen 100.000+-Proof benennen.

Pflicht-Gates:

- `large_graph_input_recorded`
- `output_budget_enforced`
- `clipping_explained`
- `cursor_or_aggregate_available`
- `no_full_payload_dump`
- `accelerator_not_required`

### `large_graph_input_recorded`

Der Proof muss sichtbar machen, dass der Input 100.000 oder mehr Knoten/Kanten repraesentiert.

### `output_budget_enforced`

Der Proof ist nur releasefaehig, wenn `returned_count`, Budget und Ausgabegroesse klein und kontrolliert bleiben.

### `clipping_explained`

Wenn Ergebnisse begrenzt werden, muessen `partial` oder `clipped` mit `reason` klar erklaert sein.

### `cursor_or_aggregate_available`

Es muss eine saubere Folgeform geben:

- Cursor fuer Fortsetzung
- oder Aggregate fuer Uebersicht statt Vollmenge

### `no_full_payload_dump`

Der Proof ist nur releasefaehig, wenn keine Vollpayload in UI, Chat oder API entsteht.

### `accelerator_not_required`

Der Proof ist nur releasefaehig, wenn keine Accelerator-Pflicht fuer den Release behauptet wird.

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer diesen Proof festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Klartext: grosse Datenmenge ja, Full Dump nein
- Release-Ehrlichkeit fuer `partial`, `clipped`, Cursor und Aggregate

### Bob

Bob verantwortet:

- einen isolierten Large-Graph-Proof als read-only Modell oder Test
- Generator-, Zaehler- oder Fixture-basierte Eingabegroessen
- kleine, budgetierte Output-Summaries statt Vollpayloads

Wichtig:

- Bob darf keinen echten Vollgraph an UI, Chat oder API ausgeben
- Bob darf keine Accelerator-, Rebuild- oder Migration-Arbeit in diesen Slice ziehen

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testgrenzen, Laufzeitannahmen und Speicherdisziplin
- Stop-Entscheidung bei Full Dump, Scope-Drift oder versteckter Runtime-Eskalation

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in falsche Large-Graph-Claims verhindern.

Mindestens:

- wenn ein Test oder Modell einen Vollgraph als Payload ausgeben will: stoppen
- wenn `returned_count` oder Budget nicht sauber klein bleiben: `blocked`
- wenn `partial` oder `clipped` ohne `reason` erklaert werden: stoppen
- wenn weder Cursor noch Aggregate als Fortsetzungsform vorhanden sind: `needs_budget_review`
- wenn Qdrant, Kuzu, adRAP, Rebuild oder Migration in den Slice geschoben werden: stoppen
- wenn Plugin-, Telegram-, Netzwerk- oder Host-Scope auftaucht: stoppen

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isolierter read-only Large-Graph-Budget-Proof
- synthetische oder fixture-basierte 100.000+-Inputs
- kleine, budgetierte Resultat-Summaries
- Statusableitung fuer `budget_proof_ready`, `needs_budget_review`, `blocked`, `deferred`

Nicht erlaubt:

- Full Graph Dump
- UI-Rendervollgraph
- Graph-Rebuild
- Postgres-Live-Migration
- Qdrant, Kuzu oder adRAP als Pflicht
- Plugin-, Telegram-, Netzwerk- oder Host-Aktionen

Pflicht-Gate-ID:

- `large_graph_budget_proof`

Pflicht-Statuswerte:

- `budget_proof_ready`
- `needs_budget_review`
- `blocked`
- `deferred`

## Example Safe Large Graph Reading

Zulaessig:

- `node_count = 100000+`
- `returned_count = small bounded subgraph`
- `clipped = true`
- `reason = output budget enforced`
- `next_action = continue via cursor or use aggregate`
- `status = budget_proof_ready`

Nicht zulaessig:

- `render_full_graph = true`
- `api_payload = all nodes and edges`
- `graph_rebuild_needed_for_release = true`
- `accelerator_required = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer einen 100.000+-Graph-Budget-Proof. Er zeigt, wie grosse Graph-Mengen releasefaehig nur ueber Zaehler, Fixtures, Budgets, Cursor, Aggregate und ehrliches Clipping belegt werden, ohne Full Dump, Rebuild, Migration oder Plugin-Scope.
