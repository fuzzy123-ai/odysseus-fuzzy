# Progressive Graph API Release Gate Contract

Stand: 2026-06-17

Status: **RGM2A Docs-Contract fuer das Gate `progressive_graph_api_release_gate`**

Quellen:

- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/large-graph-budget-proof-contract.md`
- `docs/plans/progressive-graph-api-contract.md`

Dieser Contract definiert das Release-Gate fuer die Progressive Graph API vor externem `1.0.0`. Er beschreibt keinen echten Graph-Runtime-Start, kein UI-Vollrendering, keinen Full-Payload-Dump und keine Aktivierung von Netzwerk-, Host-, Plugin- oder Accelerator-Pfaden. Der Slice friert nur ein, wie grosse Graphen releasefaehig ausschliesslich ueber budgetierte, progressive Antworten sichtbar werden duerfen: kleine Subgraphs, Aggregate, Cursor, `partial`, `clipped`, `reason` und `next_action`.

## Purpose

`RGM2A` ist die operator-taugliche Release-Grenze fuer progressive Graph-Antworten.

Der Contract soll beantworten:

- wann ein Graph-Endpoint oder Graph-Result vor externem `1.0.0` ueberhaupt als releasefaehig gelten darf
- welche Evidence eine Progressive Graph API zwingend tragen muss
- wie `max_nodes`, `max_edges`, `partial`, `clipped`, Cursor und Aggregate zusammen releasefaehig bleiben
- welche Blocker sofort zum Stop fuehren
- wie Alice, Bob und Charlie die Gate-Sprache read-only und release-ehrlich halten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine echte Runtime-Aktivierung
- keinen Full Graph Dump
- kein UI-Vollrendering fuer 100.000+ Graphen
- keine Qdrant-, Kuzu- oder adRAP-Pflicht
- keine Postgres-Live-Migration
- keinen globalen Graph-Rebuild
- keine Plugin-Arbeit, keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime
- keine Provider-, Telegram-, Netzwerk- oder Host-Aktionen

## Release-Bedeutung Der Progressive Graph API

Die Section `progressive_graph_api_release_meaning` soll die zentrale Leitplanke festhalten.

Releasefaehig bedeutet:

- grosse Graphen werden nur ueber kleine, budgetierte Antworten freigegeben
- `max_nodes` und `max_edges` bleiben harte Ausgabegates
- `partial` und `clipped` bleiben ehrliche Zustandsmarker
- Cursor, `next_action` oder `aggregate_view` erklaeren die Fortsetzung
- kein Graph-Result darf Vollstaendigkeit behaupten, wenn Clipping oder Begrenzung aktiv war

Nicht releasefaehig ist:

- Vollpayload statt progressiver Antwort
- UI-Vollrendering statt Ausschnitt oder Aggregat
- verstecktes Clipping ohne Erklaerung

## Pflicht-Evidence

Die Section `required_evidence` soll die minimalen Belege fuer das Release-Gate festziehen.

Pflicht-Evidence:

- `graph_budget_required`
- `max_nodes_enforced`
- `max_edges_enforced`
- `clipped_status_explained`
- `partial_status_explained`
- `cursor_or_next_action_present`
- `aggregate_view_supported`
- `full_payload_dump_disabled`
- `api_runtime_activation_disabled`

### `graph_budget_required`

Jede Graph-Antwort braucht einen expliziten Budgetrahmen statt implizitem "load more all".

### `max_nodes_enforced`

Die Antwort muss zeigen, dass Knotenanzahl sichtbar begrenzt bleibt.

### `max_edges_enforced`

Die Antwort muss zeigen, dass Kantenanzahl sichtbar begrenzt bleibt.

### `clipped_status_explained`

Wenn Clipping vorliegt, muessen `reason` und Folgeverhalten lesbar bleiben.

### `partial_status_explained`

Wenn das Result nur teilweise ist, muss dies user-facing und operator-tauglich erklaert sein.

### `cursor_or_next_action_present`

Wenn mehr Graph fachlich relevant bleibt, braucht die Antwort Cursor oder eine klare Folgeaktion.

### `aggregate_view_supported`

Bei grossen Mengen muss eine Aggregat-Sicht moeglich sein, statt in Vollmengen zu kippen.

### `full_payload_dump_disabled`

Die Release-Grenze ist nur bestanden, wenn UI, Chat und API keine Vollpayload eines grossen Graphen ausgeben.

### `api_runtime_activation_disabled`

Dieser Gate-Slice bleibt read-only Contract- und Release-Sprache, keine Runtime-Aktivierung.

## Operator-Taugliche Gate-Regeln

Die Section `operator_facing_gate_rules` soll die Freigabeentscheidung auf kurze, pruefbare Regeln verdichten.

Pflichtregeln:

- Antworten muessen klein, budgetiert und progressiv bleiben
- `partial` und `clipped` duerfen nicht verschwiegen werden
- Cursor, Aggregate oder `next_action` muessen den naechsten Schritt tragen
- ein grosser Input legitimiert niemals einen grossen Payload
- 100.000+-Eingaben duerfen nur ueber Ausschnitte und Aggregate sichtbar werden

## Verbotene Aktionen

Die Section `forbidden_actions` muss die harten Release-Blocker nennen.

Mindestens:

- Full Graph Dump
- UI-Full-Render
- echte Runtime-Aktivierung
- Netzwerkaktionen
- Host-Aktionen
- Qdrant
- Kuzu
- adRAP
- Research-Accelerator-Scope
- Plugin-Scope
- echte Tokens oder Secrets

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- Plugin-Modul bleibt eingefroren und wird in diesem Gate nicht beruehrt

## Statussprache

Pflicht-Gate-ID:

- `progressive_graph_api_release_gate`

Pflicht-Statuswerte:

- `progressive_graph_gate_ready`
- `needs_gate_review`
- `blocked`
- `deferred`

### `progressive_graph_gate_ready`

Die Progressive Graph API kann als releasefaehige Budget-Grenze beschrieben werden, ohne Vollpayload, ohne unehrliches Clipping und ohne Runtime-Aktivierung.

### `needs_gate_review`

Mindestens eine Budget-, Clipping-, Cursor- oder Aggregat-Frage braucht noch manuelle Review, bevor ein Release-Claim sauber ist.

### `blocked`

Mindestens ein harter Verstoß liegt vor, zum Beispiel Vollpayload, UI-Vollrender oder Accelerator-/Runtime-Scope-Drift.

### `deferred`

Die Gate-Entscheidung ist bewusst verschoben und bleibt ausserhalb dieses Slices offen.

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer dieses Release-Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Release-Ehrlichkeit fuer `partial`, `clipped`, Cursor und Aggregate
- Klartext: grosser Graph ja, Vollpayload nein

### Bob

Bob verantwortet:

- ein isoliertes read-only Release-Gate-Modell oder Summary ueber Progressive Graph API Signale
- Validierung der Pflicht-Evidence und Gate-Statuswerte
- keine Runtime-, Netzwerk-, Host-, Plugin- oder Accelerator-Aktivierung

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- fokussierte Tests gegen unbounded Payloads und unehrliches Clipping
- Stop-Entscheidung bei Full Dump, UI-Vollrender oder Scope-Drift

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in falsche Release-Claims verhindern.

Mindestens:

- wenn ein Graph-Result als Vollpayload ausgegeben wird: stoppen
- wenn UI einen 100.000+-Graphen voll rendern soll: stoppen
- wenn `max_nodes` oder `max_edges` nicht klar enforced sind: `blocked`
- wenn `partial` oder `clipped` ohne Erklaerung erscheinen: `needs_gate_review`
- wenn weder Cursor, `next_action` noch Aggregat-Sicht vorhanden ist: `needs_gate_review`
- wenn Runtime-, Netzwerk-, Host-, Plugin- oder Accelerator-Scope in diesen Slice gezogen wird: stoppen
- wenn Secrets oder Tokens sichtbar werden: stoppen

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isoliertes read-only Progressive-Graph-Release-Gate-Modell
- Validierung der Pflicht-Evidence
- Statusableitung fuer `progressive_graph_gate_ready`, `needs_gate_review`, `blocked`, `deferred`
- Tests mit bestehenden Fixtures, Large-Graph-Proof-Signalen und Progressiv-Graph-Contracts

Nicht erlaubt:

- Full Graph Dump
- UI-Vollrendering
- Runtime-Aktivierung
- Netzwerk-, Host-, Plugin- oder Accelerator-Aktionen

## Example Safe Gate Reading

Zulaessig:

- `max_nodes_enforced = true`
- `max_edges_enforced = true`
- `partial_status_explained = true`
- `clipped_status_explained = true`
- `cursor_or_next_action_present = true`
- `aggregate_view_supported = true`
- `full_payload_dump_disabled = true`
- `status = progressive_graph_gate_ready`

Nicht zulaessig:

- `return_all_nodes = true`
- `render_full_graph_ui = true`
- `runtime_enabled = true`
- `accelerator_required_for_release = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer das Progressive Graph API Gate. Er macht grosse Graphen als budgetierte, progressive und ehrlich erklaerte Antworten releasefaehig lesbar, ohne Runtime-Aktivierung, Vollpayload, UI-Vollrendering oder Plugin-Scope.
