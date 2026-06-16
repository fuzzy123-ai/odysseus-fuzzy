# Maintenance Worker Contract

Stand: 2026-06-16

Status: **LM1A Produkt-/Sicherheits-/Charlie-Vertrag fuer `0.14.x Lightweight Memory Maintenance`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/memory-store-interface-contract.md`
- `docs/plans/memory-diagnostics-lens-contract.md`
- `docs/plans/query-budget-ux-contract.md`
- `docs/plans/postgres-pgvector-migration-contract.md`
- `docs/plans/import-export-migration-proof-runbook.md`
- `docs/plans/progressive-graph-api-contract.md`
- `docs/plans/ops-homeserver-runbook-contract.md`

Dieser Vertrag definiert die erlaubte Rolle kleiner lokaler Maintenance-Modelle in `0.14.x`. `LM1A` baut bewusst keinen echten LLM-Call, keinen RAPTOR-Fullbuild, keinen Graph-Rebuild und keine Runtime-Integration. Der Slice friert nur ein, welche Aufgaben ein kleiner Worker unter harten Budgets uebernehmen darf, welche Evidence-Pflicht gilt und wann Review, Fallback oder Stop greifen muessen.

## Ziel

Odysseus soll kleine lokale Modelle unter 2 GB RAM spaeter als bounded Maintenance-Worker nutzen koennen, ohne ihnen globale Wahrheit, grosse Kontexte oder stille Schreibrechte zu geben.

Der Maintenance-Worker-Vertrag soll:

- erlaubte Worker-Aufgaben eng definieren
- harte Budgets fuer Speicher, Tokens, Chunks und Quellen festschreiben
- Derived Data, Review Items und Truth Store strikt trennen
- Charlie eine klare Freigabe- oder Stop-Logik geben
- Bob ein kleines, validierbares Task- und Worker-Modell vorbereiten

## Leitregel

Das kleine Modell ist Worker, nicht Denkzentrale.

Das bedeutet:

- das Modell bekommt nur kleine, vorbereitete Arbeitspakete
- es entscheidet nie globale Wahrheit
- es schreibt nie direkt in den Truth Store
- seine Outputs bleiben Derived Data oder Review Items
- Unsicherheit fuehrt zu Review oder Fallback, nicht zu stillen Writes

## Begriffe

### `maintenance_worker_profile`

Die kompakte Beschreibung des kleinen Maintenance-Modells als bounded Worker.

Mindestens enthalten:

- lokales kleines Modell
- unter 2 GB RAM Betriebsziel
- eng begrenzte Maintenance-Aufgaben
- keine globale Kontextaufnahme

### `task_ref`

Referenz auf ein einzelnes Maintenance-Arbeitspaket.

### `task_type`

Die konkrete Klasse der erlaubten Worker-Aufgabe.

In `LM1A` sind mindestens diese Typen Pflicht:

- `cluster_labeling`
- `evidence_summary`
- `entity_edge_candidate`
- `dedupe_candidate`
- `drift_check`
- `review_preparation`

### `model_profile_ref`

Referenz auf das eingesetzte kleine Modellprofil.

### `memory_budget_mb`

Die harte Arbeitsspeichergrenze fuer den Worker-Pfad.

### `token_budget`

Das maximale Text- oder Prompt-Budget fuer einen Task-Lauf.

### `chunk_budget`

Die maximale Anzahl oder Groesse von Chunks, die in einem Task-Paket verarbeitet werden duerfen.

### `source_refs`

Referenzen auf die Quellen, Chunks oder vorbereiteten Einheiten, aus denen der Worker arbeiten darf.

### `evidence_refs`

Referenzen auf die kleinste Evidence-Menge, die den Worker-Output nachvollziehbar macht.

### `derived_output_ref`

Referenz auf einen Derived Output wie Label, Summary-Entwurf, Kandidatenliste oder Drift-Markierung.

### `review_item_ref`

Referenz auf ein Review Item, wenn ein Output vor weiterer Verwendung pruefpflichtig ist.

### `fallback_model_ref`

Referenz auf ein groesseres Reviewer- oder Retry-Modell fuer Grenzfaelle.

### `confidence`

Die lesbare Sicherheit des Worker-Ergebnisses innerhalb des kleinen Task-Scope.

### `uncertainty_reason`

Die kleinste lesbare Begruendung, warum der Worker unsicher ist oder Review braucht.

### `needs_review`

Marker, dass der Output nicht automatisch weiterlaufen darf.

### `go_no_go_status`

Der explizite Freigabestatus fuer Worker-Readiness oder Task-Fortsetzung.

Erlaubte Werte mindestens:

- `draft`
- `review`
- `go`
- `no_go`
- `blocked`
- `superseded`

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer Halluzinations-, Budget- oder Review-Risiken.

## Erlaubte Task-Typen

### `cluster_labeling`

Der Worker darf kleine, vorbereitete Cluster-Beschreibungen oder Label-Vorschlaege erzeugen.

Regeln:

- Clustering selbst laeuft nicht im LLM
- der Worker sieht nur kleine vorbereitete Cluster-Pakete
- Ergebnisse bleiben Derived Labels oder Review-Kandidaten

### `evidence_summary`

Der Worker darf kleine evidence-bound Summary-Entwuerfe ueber vorbereitete Quellen oder Chunks erzeugen.

Regeln:

- jede Summary braucht `source_refs` und `evidence_refs`
- fehlende oder schwache Evidence fuehrt zu `needs_review`

### `entity_edge_candidate`

Der Worker darf Kandidaten fuer Entitaets- oder Kantenpflege vorschlagen.

Regeln:

- keine direkten Truth-Writes
- nur Kandidaten oder Review Items
- Unsicherheit muss lesbar bleiben

### `dedupe_candidate`

Der Worker darf Vorschlaege fuer Dublettenpruefung machen.

Regeln:

- keine automatische globale Zusammenfuehrung
- nur Review-faehige Kandidaten

### `drift_check`

Der Worker darf kleine, vorbereitete Drift-Indikatoren oder Summary-Abweichungen markieren.

Regeln:

- Drift ist Hinweis, nicht automatisch Wahrheit
- bei Unsicherheit geht der Fall in Review oder Fallback

### `review_preparation`

Der Worker darf Review-Pakete vorbereiten, strukturieren oder knapp erklaeren.

Regeln:

- Review-Vorbereitung aendert keine Wahrheit
- sie erleichtert menschliche oder groessere Modellpruefung

## Nutzer-Sicht

Nutzer sollen verstehen, was es bedeutet, wenn ein kleines lokales Modell "Memory pflegt".

Das bedeutet nicht:

- das Modell denkt fuer das ganze System
- das Modell entscheidet globale Wahrheit
- das Modell schreibt still in den kanonischen Memory- oder Graph-Bestand

Das bedeutet:

- das Modell bearbeitet kleine vorbereitete Maintenance-Pakete
- es erzeugt Derived Outputs oder Review-Vorlagen
- es muss Quellen oder Evidence binden
- Unsicherheit fuehrt sichtbar zu Review oder Fallback

### Was automatisch passieren darf

- kleine evidence-bound Label- oder Summary-Entwuerfe
- kleine Kandidatenlisten fuer Dedupe oder Entity-Edge-Pflege
- Drift-Markierungen im engen Scope
- Review-Vorbereitung fuer nachgelagerte Gates

### Was in Review muss

- alles mit niedriger `confidence`
- alles mit lesbarem `uncertainty_reason`
- alles, was spaeter Einfluss auf Truth-nahe Ableitungen haette
- alles, was Evidence nicht klar bindet

## Charlie-Sicht

Charlie braucht eine strengere und maschinenlesbare Sicht als normale Produktprosa.

Charlie soll erkennen koennen:

- ist das kleine Modell wirklich nur Worker
- sind Budgets fuer RAM, Tokens, Chunks und Quellen hart genug
- existiert fuer Unsicherheit eine Review- oder Fallback-Route
- bleiben Outputs Derived Data oder Review Items
- gibt es Halluzinationsschutz statt stiller Wahrheitsspruenge

Charlie braucht mindestens:

- `maintenance_worker_profile`
- `task_ref`
- `task_type`
- `model_profile_ref`
- `memory_budget_mb`
- `token_budget`
- `chunk_budget`
- `source_refs`
- `evidence_refs`
- `derived_output_ref`
- `review_item_ref`
- `fallback_model_ref`
- `confidence`
- `uncertainty_reason`
- `needs_review`
- `go_no_go_status`
- `risk_evidence_ref`

Charlie darf Bob oder den naechsten Slice weiterlaufen lassen, wenn:

- der Worker-Task klar bounded bleibt
- Outputs nie direkte Truth-Writes sind
- Evidence-Refs nicht fehlen
- Unsicherheit sichtbar wird
- `needs_review` und `fallback_model_ref` fuer Grenzfaelle vorhanden sind
- das Modellprofil das kleine Worker-Ziel nicht sprengt

Charlie muss stoppen, wenn:

- der Worker als globale Denk- oder Truth-Instanz beschrieben wird
- Budgets unbounded oder unklar sind
- Evidence fehlt
- keine Review- oder Fallback-Route existiert
- Halluzinationsrisiko ohne Gate akzeptiert wird
- Accelerator- oder Research-Arbeit in den Worker-Slice geschmuggelt wird

## Sicherheits- und Budget-Regeln

### Kleines Modell bleibt Worker

Das Modell darf nie den gesamten Memory-, Graph- oder Cluster-Zustand bekommen.

### Keine globalen Dumps

Es gibt:

- keine globalen Memory-Dumps
- keine globalen Graph-Dumps
- keine globalen Cluster-Dumps

### Keine Truth-Store-Writes durch das Modell

Worker-Outputs duerfen nicht direkt als Wahrheit persisitiert werden.

### Outputs sind Derived Data oder Review Items

Wenn ein Output weitergenutzt wird, dann nur als:

- `derived_output_ref`
- `review_item_ref`

### Evidence ist Pflicht

Jede Summary, jeder Edge-Kandidat, jede Korrektur und jeder Dedupe-Vorschlag braucht `source_refs` oder `evidence_refs`.

### Unsicherheit fuehrt zu Review oder Fallback

Ein unsicherer Worker darf nicht still "best effort" in Wahrheit umgedeutet werden.

### Fallback ist Reviewer, nicht Default

`fallback_model_ref` ist fuer Grenzfaelle, Retry oder Review gedacht, nicht als stiller Standard fuer alle Tasks.

### Clustering bleibt algorithmisch

Clustering laeuft in Engine, Jobs oder Algorithmen, nicht im LLM.

## Stop-Regeln

`LM1A` oder spaetere Worker-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- unbounded `memory_budget_mb`, `token_budget` oder `chunk_budget`
- fehlende `evidence_refs`
- direkte oder implizite Truth-Write-Claims
- fehlende Review-Route
- `model_profile_ref` ueber dem kleinen Worker-Budget
- implizite Accelerator- oder Research-Arbeit
- keine Fallback-Regel fuer unsichere Faelle
- Halluzinationsrisiko ohne Gate

## Derived-vs-Truth-Regel

Die wichtigste Trennung aus `0.13.x` bleibt erhalten:

- Truth Store bleibt ausserhalb des Worker-Modells
- Worker erzeugt nur vorbereitete, pruefbare Zwischenschichten
- Derived Outputs muessen rebuildbar oder verwerfbar bleiben

## Evidence-Paket

Ein spaeteres Worker-Evidence-Buendel sollte mindestens enthalten:

- `maintenance_worker_profile`
- `task_ref`
- `task_type`
- `model_profile_ref`
- `memory_budget_mb`
- `token_budget`
- `chunk_budget`
- `source_refs`
- `evidence_refs`
- `derived_output_ref`
- `review_item_ref`
- `fallback_model_ref`
- `confidence`
- `uncertainty_reason`
- `needs_review`
- `go_no_go_status`
- `risk_evidence_ref`

Empfohlene Zusatzbelege:

- kurze Budget-Zusammenfassung pro Task
- bekannte Halluzinationsrisiken
- Review-Notizen fuer Grenzfaelle
- Hinweis, ob Fallback genutzt, geplant oder bewusst nicht noetig war

## Nicht-Ziele

`LM1A` fuehrt bewusst nicht aus:

- keine echte Modell-Ausfuehrung
- keine Cluster-Implementierung
- keine Summary-Worker-Implementierung
- kein K-Means
- kein UMAP oder GMM
- keine UI
- keine Runtime-Integration

Der Slice friert nur die bounded Worker-, Evidence- und Sicherheits-Sprache ein.

## Handoff an Bob

Bobs spaeteres Maintenance-Worker-Modell soll mindestens diese Felder abbilden oder validieren:

- `maintenance_worker_profile`
- `task_ref`
- `task_type`
- `model_profile_ref`
- `memory_budget_mb`
- `token_budget`
- `chunk_budget`
- `source_refs`
- `evidence_refs`
- `derived_output_ref`
- `review_item_ref`
- `fallback_model_ref`
- `confidence`
- `uncertainty_reason`
- `needs_review`
- `go_no_go_status`
- `risk_evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- `task_type` muss aus der erlaubten Maintenance-Menge stammen
- `memory_budget_mb`, `token_budget` und `chunk_budget` duerfen nicht offen oder implizit unbounded sein
- `source_refs` und `evidence_refs` duerfen bei evidence-bound Tasks nicht fehlen
- `needs_review` muss aus Unsicherheit oder schwacher Evidence ableitbar sein
- `derived_output_ref` und `review_item_ref` muessen Truth-Writes klar ersetzen
- `fallback_model_ref` darf nicht implizit fuer alle Tasks Pflicht sein, muss aber fuer unsichere Faelle modellierbar bleiben
- das Modell darf keine globale Dump- oder Write-Faehigkeit implizieren

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `task_status`
- `summary_scope`
- `returned_chunk_count`
- `evidence_count`
- `can_retry`
- `review_priority`

## Akzeptanz fuer diesen Vertrag

`LM1A-maintenance-worker-contract` ist erfuellt, wenn:

- die Begriffe `maintenance_worker_profile`, `task_ref`, `task_type`, `model_profile_ref`, `memory_budget_mb`, `token_budget`, `chunk_budget`, `source_refs`, `evidence_refs`, `derived_output_ref`, `review_item_ref`, `fallback_model_ref`, `confidence`, `uncertainty_reason`, `needs_review`, `go_no_go_status`, `risk_evidence_ref` klar definiert sind
- die Task-Typen `cluster_labeling`, `evidence_summary`, `entity_edge_candidate`, `dedupe_candidate`, `drift_check`, `review_preparation` beschrieben sind
- Nutzer-Sicht klar macht, was automatisch passieren darf und was in Review muss
- Charlie-Sicht klar macht, wann weitergelaufen werden darf und wann gestoppt werden muss
- Regeln Worker-Charakter, Evidence-Pflicht, keine Truth-Writes, Unsicherheits-Gates und algorithmisches Clustering sauber priorisieren
- Stop-Regeln unbounded Budgets, fehlende Evidence, Truth-Write-Claims, fehlende Review- oder Fallback-Wege und Halluzinationsrisiken blockieren
- Nicht-Ziele echte Modell-, Cluster-, Research- oder UI-Arbeit aus dem Slice heraushalten
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Maintenance-Worker-Modell bekommt
