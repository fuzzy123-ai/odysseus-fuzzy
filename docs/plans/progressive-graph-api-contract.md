# Progressive Graph API Contract

Stand: 2026-06-16

Status: **MS6A Produkt-/UX-/Charlie-Vertrag fuer `0.13.x Progressive Graph API`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/memory-scale-foundation-roadmap.md`
- `docs/plans/query-budget-ux-contract.md`
- `docs/plans/memory-diagnostics-lens-contract.md`

Dieser Vertrag definiert die sichtbare Sprache fuer eine Progressive Graph API. `MS6A` baut bewusst noch keine echte API, keine UI und keine Graph-Runtime. Der Slice friert nur ein, wie grosse Graphen spaeter ueber budgetierte Ausschnitte, Aggregate, Cursor und ehrliche Clipping-Hinweise geliefert werden sollen.

## Ziel

Odysseus darf bei grossen Graphen niemals alle Nodes oder Edges an UI, Agenten oder Folgepfade dumpen. Stattdessen soll die Produktflaeche kleine, relevante und fortsetzbare Ausschnitte zeigen.

Die Progressive Graph API soll:

- nur budgetierte Subgraphs und Aggregate liefern
- Clipping, Partial Results und Fortsetzungen ehrlich sichtbar machen
- Nutzer nie mit Full Dumps oder riesigen Payloads belasten
- Charlie klar zeigen, wann ein Graph-Result fuer Folgearbeit reicht und wann nicht
- Bob ein kleines, validierbares Progressive-Graph-Modell vorbereiten

## Was ist eine Progressive Graph API?

Eine Progressive Graph API ist eine serverseitig budgetierte Vertragsflaeche fuer Graph-Ausschnitte.

Sie beschreibt nicht "den ganzen Graph", sondern:

- welche kleine Sicht angefragt wurde
- wie viele Nodes und Edges maximal geliefert werden durften
- ob das Result vollstaendig, partial oder clipped ist
- ob Aggregate oder Cursors den naechsten Schritt tragen

Eine Progressive Graph API ist:

- kleiner als ein vollstaendiger Graph-Explorer
- strenger als freie "load more"-Prosa
- kompatibel mit Query Budgets und Diagnostics Lenses

## Begriffe

### `graph_query_id`

Stabile Kennung einer konkreten Graph-Abfrage oder Graph-Lens.

### `graph_ref`

Referenz auf den fachlichen Graph- oder Graph-Snapshot-Kontext, gegen den die Abfrage gelesen wurde.

### `viewport_ref`

Referenz auf den aktuellen sichtbaren Ausschnitt oder Lens-Kontext.

### `node_ref`

Referenz auf einen einzelnen Graph-Knoten innerhalb des sichtbaren Ausschnitts.

### `edge_ref`

Referenz auf eine einzelne Graph-Kante innerhalb des sichtbaren Ausschnitts.

### `aggregate_ref`

Referenz auf eine zusammengefasste Sicht wie Cluster, Community, Bucket oder Summary-Gruppe.

### `limit`

Maximale Anzahl von Rueckgabeobjekten in einer Liste oder API-Antwort.

### `cursor`

Fortsetzungsmarke fuer die naechste sichtbare Teilmenge.

### `max_nodes`

Maximale Anzahl von Knoten, die ein Result liefern darf.

### `max_edges`

Maximale Anzahl von Kanten, die ein Result liefern darf.

### `depth`

Maximale Nachbarschafts- oder Traversal-Tiefe.

### `max_hops`

Maximale Anzahl von Hops fuer Pfad- oder Verbindungsabfragen.

### `aggregate_level`

Die Sichttiefe, auf der Daten eher aggregiert als als Einzelknoten dargestellt werden.

### `node_count`

Die Anzahl sichtbarer oder angefragter Knoten im Result-Kontext.

### `edge_count`

Die Anzahl sichtbarer oder angefragter Kanten im Result-Kontext.

### `partial`

Marker, dass das Result nutzbar, aber nicht vollstaendig ist.

### `clipped`

Marker, dass das Result bewusst an Budget- oder Payload-Grenzen gekappt wurde.

### `next_cursor`

Fortsetzungsreferenz fuer den naechsten Ausschnitt.

### `reason`

Die kleinste lesbare Begruendung fuer partial, clipped, blocked oder failed.

### `next_action`

Die kleinste konkrete Folgeaktion fuer Nutzer, Agent oder Charlie.

- Beispiel: "mit Cursor fortsetzen", "Aggregationsebene nutzen", "Tiefe senken", "Dispatch stoppen"

### `evidence_ref`

Kurze Referenz auf den wichtigsten Beleg fuer die aktuelle Graph-Lage.

## API-Muster

Die Progressive Graph API soll mindestens diese Muster spaeter beschreiben koennen:

### `graph/overview`

Eine kleine Uebersicht fuer einen Graph-Kontext.

Sie soll:

- Counts, Top-Bereiche oder Aggregate zeigen
- nie alle Nodes/Edges ausliefern
- bei grossen Datenmengen zuerst abstrahieren

### `graph/neighborhood`

Eine lokale Nachbarschaft um einen Knoten oder Fokuspunkt.

Sie soll:

- `node_ref`, `depth`, `max_nodes` und `max_edges` respektieren
- kleine, erklaerbare Nachbarschaften liefern
- Clipping sichtbar markieren

### `graph/path`

Eine budgetierte Pfadsicht zwischen zwei Knoten oder Entitaeten.

Sie soll:

- `max_hops` respektieren
- nicht alle moeglichen Verbindungen auflisten
- lieber wenige erklaerbare Pfade als unbounded Suchraeume liefern

### `graph/community`

Eine Cluster-, Community- oder Themen-Sicht.

Sie soll:

- mit `aggregate_level` arbeiten
- Aggregate statt Vollmengen liefern
- bei Bedarf in kleinere Ausschnitte ueberfuehren

### `graph/query-subgraph`

Eine query-nahe Subgraph-Sicht fuer eine bereits laufende Query oder Lens.

Sie soll:

- Query-Kontext mit Graph-Kontext verbinden
- `max_nodes`, `max_edges` und Cursor respektieren
- nie die komplette query-relevante Graph-Menge dumpen

## Nutzer-Sicht

Nutzer sollen bei riesigen Graphen nie den Eindruck bekommen, das Produkt sei kaputt, nur weil es nicht alles zeigt.

Die Nutzerflaeche soll stattdessen zeigen:

- einen kleinen Subgraph
- Aggregate oder Cluster
- einen sichtbaren Sampling- oder Clipping-Hinweis
- eine klare Folgeaktion
- niemals einen Full Dump

### Nutzer-Grundsaetze

- kleine Ausschnitte sind Produktverhalten, kein Defekt
- `partial` ist nutzbar, wenn es ehrlich markiert bleibt
- `clipped` ist kein stiller Datenverlust
- Aggregate sind bei grossen Graphen oft die erste, richtige Sicht
- leere oder kleine Resultate duerfen erklaert sein, statt Vollstaendigkeit zu behaupten

Der Nutzer braucht nicht:

- rohe Node-/Edge-Dumps
- komplette Graph-Historien
- technische DB- oder Traversal-Details

## Charlie-Sicht

Charlie braucht eine strengere Sicht als normale Nutzertexte.

Charlie soll erkennen koennen:

- reicht der gelieferte Subgraph fuer den naechsten Slice
- muss ueber `next_cursor` oder kleinere Budgets fortgesetzt werden
- ist ein Result nur clipped oder bereits fachlich unbrauchbar
- droht ein unbounded oder payloadlastiger Folgepfad

Charlie braucht pro Graph-Lage mindestens:

- `graph_query_id`
- `graph_ref`
- `viewport_ref`
- `limit`
- `cursor`
- `max_nodes`
- `max_edges`
- `depth`
- `max_hops`
- `aggregate_level`
- `node_count`
- `edge_count`
- `partial`
- `clipped`
- `next_cursor`
- `reason`
- `next_action`
- `evidence_ref`

Charlie darf weiter dispatchen, wenn:

- der gelieferte Ausschnitt fachlich reicht
- Clipping klar markiert und kontrolliert bleibt
- eine Fortsetzung ueber `next_cursor` oder kleinere Suchrichtung moeglich ist
- keine unbounded Payload-Lage entsteht

Charlie muss stoppen, wenn:

- Folgearbeit wesentlich auf unsichtbaren Graph-Teilen spekulieren wuerde
- kein Cursor oder keine alternative Folgeaktion vorhanden ist, obwohl mehr Graph noetig ist
- `clipped` oder `partial` eine zentrale Wahrheitsfrage verdecken
- Graph-Payload, Node- oder Edge-Mengen erkennbar in Richtung Full Dump kippen

## Budget-Regeln

### Keine unbounded Node- oder Edge-Listen

Keine Graph-Antwort darf still alle Knoten oder Kanten liefern.

### Cursor fuer Fortsetzung

Wenn mehr Daten fachlich relevant bleiben, braucht der Pfad eine kontrollierte Fortsetzung ueber `cursor` oder `next_cursor`.

### Graph-Budgets sind Pflicht

`max_nodes`, `max_edges`, `depth` und bei Pfaden `max_hops` muessen produktiv lesbar sein, nicht nur implizit im Backend existieren.

### Aggregate vor Vollmenge

Bei grossen Datenmengen soll die erste Antwort eher ueber `aggregate_ref` und `aggregate_level` arbeiten als ueber Einzelobjekt-Dumps.

### Keine Payload-Dumps

Auch wenn ein Backend mehr liefern koennte, darf die Progressive Graph API keine riesigen Payloads an UI oder Agenten auskippen.

### Clipping ist ehrlich user-facing

Wenn ein Result gekappt wurde, muss das sichtbar bleiben:

- nicht als versteckte Verkuerzung
- nicht als falsche Vollstaendigkeit
- nicht ohne Folgeaktion

## Regeln fuer Partial und Clipping

### `partial`

- darf fuer Nutzer und Agenten brauchbar sein
- braucht eine lesbare Begruendung
- darf nicht so tun, als sei der ganze Graph abgedeckt

### `clipped`

- braucht sichtbaren Hinweis auf die Kappung
- braucht nach Moeglichkeit `next_cursor` oder eine alternative Folgeaktion
- darf nicht als Backend-Detail versteckt werden

### Aggregate und Clipping

Wenn Einzelknoten zu gross werden, soll die API eher ueber Aggregate erklaeren als ueber immer groessere Vollmengen eskalieren.

## Diagnostics-Bezug

Die Progressive Graph API soll spaeter gut mit Diagnostics und Query Budgets zusammenspielen.

Das bedeutet:

- Node- und Edge-Counts muessen budgetierbar bleiben
- Clipping muss mit Diagnostics sichtbar werden
- Graph-Payloads muessen als eigene Budgetlage lesbar sein
- `evidence_ref` soll auf Graph-, Query- oder Snapshot-Belege zeigen koennen

## Nicht-Ziele

`MS6A` baut bewusst noch nicht:

- keine echte Graph-API
- keine UI-Implementierung
- keine Datenbank- oder Postgres-Integration
- keine Kuzu-, Qdrant-, UMAP- oder GMM-Arbeit
- keinen Graph-Layout-Algorithmus
- keine Traversal-Runtime

Der Slice friert nur die sichtbare Produkt- und Budgetsprache fuer spaetere Graph-Ausschnitte ein.

## Handoff an Bob

Bobs spaeteres Progressive-Graph-Modell soll mindestens diese Felder abbilden oder validieren:

- `graph_query_id`
- `graph_ref`
- `viewport_ref`
- `node_ref`
- `edge_ref`
- `aggregate_ref`
- `limit`
- `cursor`
- `max_nodes`
- `max_edges`
- `depth`
- `max_hops`
- `aggregate_level`
- `node_count`
- `edge_count`
- `partial`
- `clipped`
- `next_cursor`
- `reason`
- `next_action`
- `evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- keine Graph-Lage darf ohne Node-, Edge- oder Cursor-Grenze modelliert sein
- `partial` und `clipped` muessen explizit lesbar sein
- `next_cursor` darf nur gesetzt sein, wenn echte Fortsetzung moeglich ist
- `aggregate_level` muss sichtbar machen koennen, ob Aggregation statt Einzelobjekten geliefert wird
- `node_count` und `edge_count` duerfen nicht still Full-Dump-Groessen verschleiern
- `reason` und `next_action` muessen Folgearbeit steuerbar machen
- das Modell darf keinen stillen `load_all`- oder Full-Dump-Default erlauben

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `status`
- `summary`
- `payload_bytes`
- `returned_nodes`
- `returned_edges`
- `can_continue`

## Akzeptanz fuer diesen Vertrag

`MS6A-progressive-graph-api-contract` ist erfuellt, wenn:

- die Begriffe `graph_query_id`, `graph_ref`, `viewport_ref`, `node_ref`, `edge_ref`, `aggregate_ref`, `limit`, `cursor`, `max_nodes`, `max_edges`, `depth`, `max_hops`, `aggregate_level`, `node_count`, `edge_count`, `partial`, `clipped`, `next_cursor`, `reason`, `next_action`, `evidence_ref` klar definiert sind
- die API-Muster `graph/overview`, `graph/neighborhood`, `graph/path`, `graph/community`, `graph/query-subgraph` beschrieben sind
- Nutzer-Sicht Subgraph, Aggregate, Clipping-Hinweise und Folgeaktionen statt Full Dumps erklaert
- Charlie-Sicht klar macht, wann weiter dispatcht werden darf und wann gestoppt werden muss
- Budget-Regeln unbounded Node-/Edge-Listen, Payload-Dumps und unehrliches Clipping ausschliessen
- Nicht-Ziele echte API-, UI-, DB- oder Accelerator-Arbeit aus dem Slice heraushalten
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Progressive-Graph-Modell bekommt
