# Evidence-Bound Summary Worker Contract

Stand: 2026-06-16

Status: **LM4A Produkt-/Safety-/Charlie-Vertrag fuer `0.14.x Evidence-Bound Summary Worker`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/maintenance-worker-contract.md`
- `docs/plans/derived-cluster-run-contract.md`
- `docs/plans/kmeans-clustering-proof-runbook.md`

Dieser Vertrag definiert die erlaubte Rolle eines kleinen Evidence-Bound Summary Workers in `0.14.x`. `LM4A` baut bewusst keine echte Summary-Generierung, keinen Runtime-Worker und keinen LLM-Call. Der Slice friert nur ein, wie kleine lokale Modelle spaeter kurze Zusammenfassungen fuer vorbereitete Chunks, Cluster oder Scopes erzeugen duerfen: bounded, evidence-bound, reviewbar und ohne Truth-Writes.

## Ziel

Odysseus soll kleine lokale Modelle spaeter fuer kurze, vorbereitete Summaries nutzen koennen, ohne unbegrenzten Kontext, unbelegte Behauptungen oder stille Wahrheitsupdates zuzulassen.

Der Summary-Worker-Vertrag soll:

- Quellen-, Chunk- und Evidence-Bindung verpflichtend machen
- harte Budgets fuer `max_chunks` und `max_tokens` festschreiben
- Review- und Fallback-Pfade sichtbar machen
- Truth-nahe Risiken ueber `needs_review` und `review_item_ref` abfangen
- Bob ein kleines, validierbares Summary-Worker-Modell vorbereiten

## Leitregel

Eine Summary aus diesem Worker ist Derived Data, nicht Wahrheit.

Das bedeutet:

- die Summary ist nur eine verdichtete Darstellung vorbereiteter Inputs
- sie darf keine neuen Fakten erzeugen
- sie darf keine Truth-Writes ausloesen
- Unsicherheit fuehrt zu Review oder Fallback
- Drift oder schwache Evidence machen die Summary pruefpflichtig

## Begriffe

### `summary_task_id`

Stabile Kennung eines einzelnen Summary-Tasks.

### `summary_ref`

Referenz auf die erzeugte oder vorbereitete Summary als Derived Output.

### `source_refs`

Referenzen auf die Quellen, aus denen die Summary abgeleitet werden darf.

### `chunk_refs`

Referenzen auf die konkreten Chunks, die den Summary-Scope bilden.

### `evidence_refs`

Referenzen auf die kleinste Belegmenge, die jede Summary nachvollziehbar macht.

### `max_chunks`

Die harte Obergrenze der Chunks, die in einem Summary-Task verarbeitet werden duerfen.

### `max_tokens`

Die harte Obergrenze fuer den Text- oder Prompt-Scope des Summary-Tasks.

### `model_profile_ref`

Referenz auf das kleine Modellprofil, das fuer den Summary-Task vorgesehen ist.

### `summary_scope_ref`

Referenz auf den klar begrenzten Scope der Summary, zum Beispiel ein Cluster, ein kleines Themenpaket oder ein vorbereiteter Review-Scope.

### `prompt_template_ref`

Referenz auf das verwendete Prompt- oder Template-Profil fuer die Summary-Aufgabe.

### `citation_policy`

Die lesbare Regel, wie Quellen, Chunks oder Evidence in der Summary sichtbar oder rueckverfolgbar bleiben muessen.

### `confidence`

Die lesbare Sicherheit der Summary innerhalb ihres kleinen, vorbereiteten Scopes.

### `uncertainty_reason`

Die kleinste lesbare Begruendung, warum die Summary unsicher, lueckenhaft oder review-pflichtig ist.

### `needs_review`

Marker, dass die Summary nicht automatisch weiterverwendet werden darf.

### `fallback_model_ref`

Referenz auf ein groesseres Reviewer- oder Retry-Modell fuer Grenzfaelle.

### `review_item_ref`

Referenz auf das Review Item, wenn die Summary menschlich oder durch ein groesseres Modell geprueft werden muss.

### `drift_check_ref`

Referenz auf einen Drift- oder Konsistenzcheck, der spaeter prueft, ob die Summary noch zu ihrer Evidence-Lage passt.

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer Halluzinations-, Drift-, Budget- oder Review-Risiken.

## Nutzer-Sicht

Nutzer sollen verstehen, was ein kleines Modell zusammenfassen darf und was nicht.

Es darf:

- kleine vorbereitete Chunk- oder Cluster-Pakete verdichten
- kurze Derived Summaries erzeugen
- Evidence-gebundene Review-Vorlagen vorbereiten

Es darf nicht:

- globales Memory zusammenfassen
- den ganzen Graph erklaeren
- neue Fakten erfinden
- Wahrheit still aendern

Warum das hilfreich ist:

- kleine, begrenzte Pakete werden schneller lesbar
- Cluster oder Scopes koennen spaeter besser reviewt werden
- kleine Modelle helfen bei Verdichtung, ohne das System zu dominieren

Warum das keine neue Wahrheit ist:

- die Summary haengt an vorbereiteten `source_refs`, `chunk_refs` und `evidence_refs`
- sie ist Derived Data
- sie kann veralten, driften oder Review brauchen

## Charlie-Sicht

Charlie braucht eine strengere und maschinenlesbare Sicht auf den Summary-Worker.

Charlie soll erkennen koennen:

- ist der Scope klein und bounded
- sind Quellen, Chunks und Evidence sauber gebunden
- gibt es eine Review- oder Fallback-Route
- bleibt die Summary Derived statt Truth
- wird Drift oder schwache Confidence sichtbar

Charlie braucht mindestens:

- `summary_task_id`
- `summary_ref`
- `source_refs`
- `chunk_refs`
- `evidence_refs`
- `max_chunks`
- `max_tokens`
- `model_profile_ref`
- `summary_scope_ref`
- `prompt_template_ref`
- `citation_policy`
- `confidence`
- `uncertainty_reason`
- `needs_review`
- `fallback_model_ref`
- `review_item_ref`
- `drift_check_ref`
- `risk_evidence_ref`

Charlie darf Bob das Modell als bereit melden lassen, wenn:

- `source_refs`, `chunk_refs` und `evidence_refs` nicht fehlen
- `max_chunks` und `max_tokens` klar bounded sind
- `citation_policy` Evidence-Bindung erzwingt
- Unsicherheit in `needs_review` oder `fallback_model_ref` ueberfuehrt werden kann
- `review_item_ref` fuer problematische Faelle modellierbar bleibt
- Drift ueber `drift_check_ref` adressierbar ist

Charlie muss stoppen, wenn:

- Quellen, Chunks oder Evidence fehlen
- Budgets unbounded bleiben
- Summary-Sprache nach Truth-Write klingt
- kein Review- oder Fallback-Pfad vorhanden ist
- Halluzinationsrisiko ohne Gate akzeptiert wird
- ein globaler Memory- oder Graph-Dump impliziert wird
- das Modellprofil das kleine Worker-Ziel sprengt

## Regeln

### Summaries sind Derived Data

Eine Summary aus diesem Worker ist eine abgeleitete, reviewbare Verdichtung, nicht Wahrheit.

### Jede Behauptung braucht Belegbezug

Jede Summary muss auf vorbereiteten `source_refs`, `chunk_refs` und `evidence_refs` beruhen.

### Niedrige Confidence fuehrt zu Review oder Fallback

Wenn `confidence` niedrig ist oder `uncertainty_reason` relevant wird, darf die Summary nicht still weiterlaufen.

### Keine neuen Fakten

Der Summary-Worker darf keine neuen Fakten erzeugen, sondern nur bereits vorhandene, belegte Inputs verdichten.

### Keine Truth-Writes

Die Summary darf keinen Truth-Write ausloesen und keine kanonische Memory- oder Graph-Wahrheit aendern.

### Budgets sind hart

`max_chunks` und `max_tokens` sind verpflichtend bounded.

### Keine globalen Dumps

Es gibt:

- keinen globalen Memory-Dump
- keinen globalen Graph-Dump
- keine stille Zusammenfassung grosser Vollmengen

### Kein Silent Overwrite

Alte Summaries duerfen nicht still ueberschrieben werden. Unsichere oder driftende Faelle muessen ueber Review, Drift-Check oder neuen Derived Output laufen.

### Review Item bei Risiko

Ein `review_item_ref` ist noetig, wenn:

- Evidence fehlt oder schwach ist
- Drift-Risiko sichtbar ist
- das Modellprofil unsicher oder grenzwertig ist

## Citation- und Drift-Sprache

`citation_policy` muss spaeter klar machen:

- wie Quellenbindung sichtbar bleibt
- wie Chunk- oder Scope-Bezug nachverfolgbar bleibt
- wie eine Summary ohne Beleg nicht als verlaesslich gelten kann

`drift_check_ref` muss spaeter adressieren:

- ob eine Summary noch zu ihrer Evidence-Lage passt
- ob neue Inputs oder Cluster-Aenderungen Review noetig machen

## Stop-Regeln

`LM4A` oder spaetere Summary-Worker-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- fehlende `source_refs`
- fehlende `chunk_refs`
- fehlende `evidence_refs`
- unbounded `max_chunks`
- unbounded `max_tokens`
- Truth-Write-Sprache
- fehlender Review- oder Fallback-Pfad
- Halluzinationsrisiko ohne Gate
- globaler Memory- oder Graph-Dump
- `model_profile_ref` ueber dem kleinen Worker-Budget

## Nicht-Ziele

`LM4A` fuehrt bewusst nicht aus:

- keine Implementierung
- kein UI
- keine Datenbank
- keinen echten LLM-Call
- keinen Summary-Runtime-Worker

Der Slice friert nur die bounded, evidence-bound und reviewbare Summary-Sprache ein.

## Handoff an Bob

Bobs spaeteres Summary-Worker-Modell soll mindestens diese Felder abbilden oder validieren:

- `summary_task_id`
- `summary_ref`
- `source_refs`
- `chunk_refs`
- `evidence_refs`
- `max_chunks`
- `max_tokens`
- `model_profile_ref`
- `summary_scope_ref`
- `prompt_template_ref`
- `citation_policy`
- `confidence`
- `uncertainty_reason`
- `needs_review`
- `fallback_model_ref`
- `review_item_ref`
- `drift_check_ref`
- `risk_evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- `source_refs`, `chunk_refs` und `evidence_refs` duerfen nicht fehlen
- `max_chunks` und `max_tokens` duerfen nicht offen oder implizit unbounded sein
- `citation_policy` muss referenzierbar und nicht nur Freitext-Optional sein
- `confidence` und `uncertainty_reason` muessen `needs_review` ableitbar machen
- `fallback_model_ref` und `review_item_ref` muessen fuer Grenzfaelle modellierbar sein
- `summary_ref` darf keine Wahrheit und keinen stillen Overwrite implizieren
- das Modell darf keinen globalen Scope oder Truth-Write-Pfad enthalten

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `summary_status`
- `returned_chunk_count`
- `evidence_count`
- `can_retry`
- `review_priority`
- `scope_label`

## Akzeptanz fuer diesen Vertrag

`LM4A-evidence-bound-summary-worker-contract` ist erfuellt, wenn:

- die Begriffe `summary_task_id`, `summary_ref`, `source_refs`, `chunk_refs`, `evidence_refs`, `max_chunks`, `max_tokens`, `model_profile_ref`, `summary_scope_ref`, `prompt_template_ref`, `citation_policy`, `confidence`, `uncertainty_reason`, `needs_review`, `fallback_model_ref`, `review_item_ref`, `drift_check_ref`, `risk_evidence_ref` klar definiert sind
- Nutzer-Sicht klar macht, was ein kleines Modell zusammenfassen darf und was nicht
- Charlie-Sicht klar macht, wann Bob das Modell als bereit melden darf und wann gestoppt werden muss
- Regeln Derived Data, Belegpflicht, Review/Fallback, keine neuen Fakten, keine Truth-Writes, bounded Budgets und kein Silent Overwrite sauber priorisieren
- Stop-Regeln fehlende Refs, unbounded Budgets, Halluzinationsrisiken, globale Dumps und uebergrosse Modellprofile blockieren
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Summary-Worker-Modell bekommt
