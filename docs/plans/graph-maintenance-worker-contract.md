# Graph Maintenance Worker Contract

Stand: 2026-06-16

Status: **LM5A Produkt-/Safety-/Charlie-Vertrag fuer `0.14.x Graph Maintenance Worker`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/maintenance-worker-contract.md`
- `docs/plans/evidence-bound-summary-worker-contract.md`
- `docs/plans/derived-cluster-run-contract.md`

Dieser Vertrag definiert die erlaubte Rolle eines kleinen Graph Maintenance Workers in `0.14.x`. `LM5A` baut bewusst keinen echten LLM-Call, keinen Graph-Write-Pfad, keinen Entity Resolver und keine Runtime-Integration. Der Slice friert nur ein, wie kleine lokale Modelle spaeter Entity- und Edge-Kandidaten vorbereiten duerfen: bounded, evidence-bound, provenance-faehig, dedupe-sicher, reviewbar und ohne Truth-Writes.

## Ziel

Odysseus soll kleine lokale Modelle spaeter fuer Graph-Maintenance nutzen koennen, ohne halluzinierte Kanten, stille Duplikate oder unbelegte Struktur-Aenderungen in den Wahrheitsbestand zu lassen.

Der Graph-Maintenance-Vertrag soll:

- Entity- und Edge-Vorschlaege strikt als Candidates oder Derived Data festschreiben
- Quellen-, Chunk-, Evidence- und Provenance-Bindung verpflichtend machen
- `confidence`, `needs_review`, Dedupe und Merge-Regeln sichtbar machen
- direkte Truth-Writes durch `truth_write_allowed = false` ausschliessen
- Charlie eine klare Freigabe- oder Stop-Logik geben
- Bob ein kleines, validierbares Graph-Maintenance-Modell vorbereiten

## Leitregel

Ein kleines Modell darf Graph-Wartung vorbereiten, aber nie Graph-Wahrheit schreiben.

Das bedeutet:

- neue Entities oder Kanten sind nur Kandidaten
- jede Behauptung braucht Quellen, Chunks, Evidence und Provenance
- Unsicherheit fuehrt zu Review oder Fallback
- Review Queue ist der einzige Weg zur spaeteren Uebernahme
- `truth_write_allowed` bleibt in diesem Slice immer `false`

## Begriffe

### `graph_maintenance_task_id`

Stabile Kennung eines einzelnen Graph-Maintenance-Tasks.

### `entity_candidate_ref`

Referenz auf einen vorgeschlagenen Entity-Kandidaten als Derived Output oder Review-faehiges Arbeitsobjekt.

### `edge_candidate_ref`

Referenz auf einen vorgeschlagenen Edge-Kandidaten als Derived Output oder Review-faehiges Arbeitsobjekt.

### `source_refs`

Referenzen auf die Quellen, aus denen Entity- oder Edge-Kandidaten abgeleitet werden duerfen.

### `chunk_refs`

Referenzen auf die konkreten Chunks, die den kleinen, bounded Task-Scope bilden.

### `evidence_refs`

Referenzen auf die kleinste Belegmenge, die einen Entity- oder Edge-Vorschlag nachvollziehbar macht.

### `provenance_ref`

Referenz auf die Rueckverfolgbarkeit des Vorschlags: aus welchem Scope, welcher Quelle, welchem Chunk-Paket oder welchem vorbereiteten Worker-Task der Kandidat stammt.

### `dedupe_key`

Die kanonische Vergleichs- oder Match-Referenz, mit der spaeter verhindert wird, dass identische oder nahezu identische Candidates still doppelt angelegt werden.

### `confidence`

Die lesbare Sicherheit des Kandidaten innerhalb seines kleinen, vorbereiteten Scopes.

### `uncertainty_reason`

Die kleinste lesbare Begruendung, warum ein Kandidat unsicher, lueckenhaft oder review-pflichtig ist.

### `needs_review`

Marker, dass ein Entity- oder Edge-Kandidat nicht automatisch uebernommen werden darf.

### `review_item_ref`

Referenz auf das Review Item, ueber das ein Kandidat spaeter geprueft, bestaetigt, verworfen oder eskaliert werden kann.

### `merge_policy_ref`

Referenz auf die Regel, wie spaetere Uebernahme, Zusammenfuehrung oder Ablehnung ablaufen soll, ohne stille Overwrites oder verdeckte Duplikate zu erzeugen.

### `truth_write_allowed`

Expliziter Marker, ob der Slice oder Kandidat direkte Truth-Writes ausloesen darf.

Fuer `LM5A` gilt verpflichtend:

- `truth_write_allowed = false`

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer Halluzinations-, Duplikat-, Provenance-, Budget- oder Review-Risiken.

### `fallback_model_ref`

Referenz auf ein groesseres Reviewer- oder Retry-Modell fuer Grenzfaelle mit niedriger `confidence`, hoher Ambiguitaet oder konflikthafter Evidence.

### `drift_check_ref`

Referenz auf einen spaeteren Drift- oder Konsistenzcheck, der Kandidaten gegen veraenderte Evidence, bestehende Derived-Schichten oder geaenderte Review-Lagen absichert.

## Nutzer-Sicht

Nutzer sollen verstehen, was es bedeutet, wenn ein kleines Modell neue Entities oder Kanten "vorschlaegt".

Das bedeutet nicht:

- dass die Kante schon wahr ist
- dass die Entity schon kanonisch angelegt wurde
- dass das Modell den Graph autonom erweitert hat

Das bedeutet:

- ein kleiner Worker hat in einem vorbereiteten, bounded Scope einen Kandidaten gefunden
- der Vorschlag ist an `source_refs`, `chunk_refs`, `evidence_refs` und `provenance_ref` gebunden
- der Vorschlag kann Review brauchen
- niedrige `confidence` oder Ambiguitaet fuehrt nicht zu stiller Uebernahme

Warum das hilfreich ist:

- kleine Modelle koennen vorbereitete Graph-Pflegearbeit vorsortieren
- Review-Pakete werden kompakter und besser erklaerbar
- der Graph kann spaeter verbessert werden, ohne unkontrollierte Wahrheitsupdates zu riskieren

Warum das nur Kandidaten sind:

- Entity- und Edge-Vorschlaege sind Derived Data
- sie haengen von kleinem Scope, Evidence und Modellgrenzen ab
- sie koennen falsch, redundant oder unvollstaendig sein

## Charlie-Sicht

Charlie braucht eine strengere und maschinenlesbare Sicht auf den Graph-Maintenance-Worker.

Charlie soll erkennen koennen:

- bleibt der Worker im Candidate- statt Truth-Modus
- sind Evidence und Provenance vollstaendig
- gibt es Dedupe- und Merge-Regeln gegen stille Doppelungen
- fuehren Unsicherheit oder Konflikte sauber zu Review oder Fallback
- bleibt der Scope bounded und ohne globale Nachbarschafts- oder Graph-Dumps

Charlie braucht mindestens:

- `graph_maintenance_task_id`
- `entity_candidate_ref`
- `edge_candidate_ref`
- `source_refs`
- `chunk_refs`
- `evidence_refs`
- `provenance_ref`
- `dedupe_key`
- `confidence`
- `uncertainty_reason`
- `needs_review`
- `review_item_ref`
- `merge_policy_ref`
- `truth_write_allowed`
- `risk_evidence_ref`
- `fallback_model_ref`
- `drift_check_ref`

Charlie darf Bob das Modell als bereit melden lassen, wenn:

- `truth_write_allowed` explizit `false` bleibt
- `source_refs`, `chunk_refs`, `evidence_refs` und `provenance_ref` nicht fehlen
- `dedupe_key` und `merge_policy_ref` Pflicht bleiben
- niedrige `confidence` oder `uncertainty_reason` sauber auf `needs_review` oder `fallback_model_ref` fuehren
- `review_item_ref` fuer problematische Faelle modellierbar ist
- kein unbounded Graph-Scope oder globale Nachbarschaft impliziert wird

Charlie muss stoppen, wenn:

- Evidence oder Provenance fehlen
- die Sprache nach direktem Graph- oder Truth-Write klingt
- keine Dedupe- oder Merge-Regel vorhanden ist
- Review- oder Fallback-Pfad fehlt
- unbegrenzter Graph-Scope, globale Dumps oder unbounded Nachbarschaften impliziert werden
- halluzinierte Kanten ohne Beleg toleriert werden
- das Modellprofil das kleine Worker-Ziel sprengt

## Regeln

### Entity- und Edge-Vorschlaege sind Candidates

`entity_candidate_ref` und `edge_candidate_ref` beschreiben nur Derived Candidates oder Review-Objekte, nie bereits akzeptierte Wahrheit.

### Jede Kante braucht Beleg und Provenance

Ein Edge-Kandidat ist nur dann ueberhaupt sinnvoll, wenn `source_refs`, `chunk_refs`, `evidence_refs` und `provenance_ref` vorhanden sind.

### `truth_write_allowed` bleibt `false`

Dieser Slice erlaubt keinen direkten Graph-Write, keinen kanonischen Merge und keinen stillen Truth-Update-Pfad.

### Niedrige Confidence fuehrt zu Review oder Fallback

Wenn `confidence` niedrig ist oder `uncertainty_reason` relevant wird, darf ein Kandidat nicht still weiterlaufen.

### Dedupe und Merge-Regel sind Pflicht

`dedupe_key` und `merge_policy_ref` muessen spaeter verhindern:

- doppelte Entity-Kandidaten
- doppelte Edge-Kandidaten
- stille Overwrites bestehender Derived- oder Review-Objekte
- unerklaerte Zusammenfuehrungen widerspruechlicher Vorschlaege

### Review Queue ist der einzige Uebernahmepfad

Ein Kandidat darf spaeter nur ueber `review_item_ref` und eine explizite Review-Entscheidung in Richtung Uebernahme weiterlaufen.

### Keine globalen Graph-Dumps

Es gibt:

- keinen globalen Graph-Dump
- keine unbounded Nachbarschaftsabfragen
- keine stille Traversierung grosser Graph-Bereiche als Worker-Default

### Drift und Konflikte bleiben sichtbar

`drift_check_ref` soll spaeter adressieren, ob ein Kandidat noch zu bestehender Evidence, spaeteren Derived-Schichten oder veraenderten Graph-Lagen passt.

## Dedupe- und Merge-Sprache

`dedupe_key` soll spaeter mindestens helfen zu beantworten:

- bezieht sich der Kandidat auf eine bereits bekannte Entity oder Kante
- gibt es semantisch aehnliche Vorschlaege aus anderen Worker-Laeufen
- droht doppelte Anlage oder widerspruechliche Pflege

`merge_policy_ref` soll spaeter klar machen:

- wann ein Kandidat nur reviewt wird
- wann Kandidaten zusammengefuehrt werden duerfen
- wann ein Konflikt offen bleiben muss
- dass nie still ueberschrieben wird

## Review- und Fallback-Sprache

`needs_review` soll mindestens dann gesetzt werden, wenn:

- `confidence` niedrig ist
- `uncertainty_reason` nicht leer oder relevant ist
- Evidence knapp, indirekt oder konflikthaft ist
- der Dedupe-Fall unklar bleibt
- die Kante wie eine Halluzination wirken koennte

`fallback_model_ref` ist fuer Grenzfaelle gedacht:

- komplexe Ambiguitaet
- widerspruechliche Evidence
- schwierige Entity-Abgrenzung
- unklare Edge-Semantik

Fallback ist Reviewer oder Retry, nicht Standardpfad fuer jeden Kandidaten.

## Stop-Regeln

`LM5A` oder spaetere Graph-Maintenance-Worker-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- fehlende `source_refs`
- fehlende `chunk_refs`
- fehlende `evidence_refs`
- fehlende `provenance_ref`
- Truth-Write-Sprache oder `truth_write_allowed != false`
- fehlende `dedupe_key`
- fehlende `merge_policy_ref`
- unbounded Graph-Scope oder globale Nachbarschaft
- fehlender Review- oder Fallback-Pfad
- halluzinierte Edge ohne Beleg
- Modellprofil ueber kleinem Worker-Budget

## Nicht-Ziele

`LM5A` fuehrt bewusst nicht aus:

- keine Implementierung
- kein UI
- keine Datenbank
- keinen echten Graph-Write
- keinen Entity Resolver
- keinen Runtime-Worker

Der Slice friert nur die bounded, evidence-bound und reviewbare Kandidaten-Sprache fuer Graph-Maintenance ein.

## Handoff an Bob

Bobs spaeteres Graph-Maintenance-Modell soll mindestens diese Felder abbilden oder validieren:

- `graph_maintenance_task_id`
- `entity_candidate_ref`
- `edge_candidate_ref`
- `source_refs`
- `chunk_refs`
- `evidence_refs`
- `provenance_ref`
- `dedupe_key`
- `confidence`
- `uncertainty_reason`
- `needs_review`
- `review_item_ref`
- `merge_policy_ref`
- `truth_write_allowed`
- `risk_evidence_ref`
- `fallback_model_ref`
- `drift_check_ref`

Minimum-Regeln fuer Bobs Modell:

- `truth_write_allowed` muss fuer diesen Pfad explizit `false` validierbar sein
- `source_refs`, `chunk_refs`, `evidence_refs` und `provenance_ref` duerfen nicht fehlen
- `dedupe_key` und `merge_policy_ref` duerfen nicht optional in einen stillen Default kippen
- `confidence` und `uncertainty_reason` muessen `needs_review` ableitbar machen
- `review_item_ref` muss fuer Kandidaten mit Risiko modellierbar sein
- `fallback_model_ref` muss fuer Grenzfaelle modellierbar bleiben, ohne zum Default zu werden
- das Modell darf keinen globalen Graph-Scope, keinen Full-Dump und keinen Truth-Write-Pfad implizieren

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `candidate_status`
- `evidence_count`
- `conflict_count`
- `review_priority`
- `candidate_scope_ref`
- `can_retry`

## Akzeptanz fuer diesen Vertrag

`LM5A-graph-maintenance-worker-contract` ist erfuellt, wenn:

- die Begriffe `graph_maintenance_task_id`, `entity_candidate_ref`, `edge_candidate_ref`, `source_refs`, `chunk_refs`, `evidence_refs`, `provenance_ref`, `dedupe_key`, `confidence`, `uncertainty_reason`, `needs_review`, `review_item_ref`, `merge_policy_ref`, `truth_write_allowed`, `risk_evidence_ref`, `fallback_model_ref`, `drift_check_ref` klar definiert sind
- Nutzer-Sicht erklaert, warum neue Entities oder Kanten nur Kandidaten sind
- Charlie-Sicht klar macht, wann Bob das Modell als bereit melden darf und wann gestoppt werden muss
- Regeln Candidate-only, Evidence- und Provenance-Pflicht, `truth_write_allowed = false`, Dedupe, Merge, Review Queue und kein globaler Graph-Dump sauber priorisieren
- Stop-Regeln fehlende Evidence/Provenance, Truth-Write-Sprache, fehlende Dedupe-/Merge-Regeln, unbounded Graph-Scope, fehlende Review-/Fallback-Wege und halluzinierte Kanten blockieren
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Graph-Maintenance-Modell bekommt
