# Fallback Routing Contract

Stand: 2026-06-16

Status: **LM7A Produkt-/Safety-/Charlie-Vertrag fuer `0.14.x Fallback Routing`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/maintenance-worker-contract.md`
- `docs/plans/small-model-evaluation-gates-contract.md`
- `docs/plans/graph-maintenance-worker-contract.md`

Dieser Vertrag definiert die Produkt- und Safety-Sprache dafuer, wann Odysseus beim kleinen Maintenance-Modell bleibt, wann Retry oder Backoff genutzt wird, wann ein groesseres Fallback-Modell als Reviewer oder Retry einspringt und wann menschliches Review noetig ist. `LM7A` baut bewusst keine Runtime-Integration, keinen echten Modellaufruf, kein UI und keine Kostenmessung. Der Slice friert nur die Routing-Regeln fuer bounded Maintenance-Arbeit ein: Gate-Grund, Retry-Grenzen, Budgetgrenzen, sichtbare Begruendung und kein stiller Truth- oder Kosten-Drift.

## Ziel

Odysseus soll kleine lokale Maintenance-Modelle bevorzugt nutzen, aber nur so lange, wie ihre Gates, Budgets und Risiken tragfaehig bleiben.

Der Fallback-Routing-Vertrag soll:

- das konfigurierte Maintenance-Modell als Default festschreiben
- Fallback nur mit klarer Gate-Begruendung aus `LM6` erlauben
- Retry und Backoff endlich, budgetiert und sichtbar machen
- Review sauber von Retry und Fallback unterscheiden
- Truth-Writes auch auf Routing-Ebene ausschliessen
- Charlie eine klare Freigabe- oder Stop-Logik geben
- Bob ein kleines, validierbares Routing-Modell vorbereiten

## Leitregel

Fallback ist Ausnahme mit Begruendung, nicht stiller Ersatz fuer das kleine Modell.

Das bedeutet:

- Default ist das konfigurierte Maintenance-Modell
- Fallback braucht einen expliziten Gate-Grund
- Retry ist endlich und budgetiert
- Review bleibt der sichere Zielpfad fuer riskante oder unklare Faelle
- Routing entscheidet nur ueber Bearbeitung, Retry, Fallback oder Review, nie ueber Wahrheit

## Begriffe

### `routing_decision_id`

Stabile Kennung einer einzelnen Routing-Entscheidung fuer einen bounded Maintenance-Task.

### `maintenance_model_ref`

Referenz auf das kleine, konfigurierte Maintenance- oder Default-Modell, das fuer den Task zuerst vorgesehen ist.

### `fallback_model_ref`

Referenz auf das groessere Reviewer- oder Retry-Modell, das nur bei begruendetem Bedarf aus dem Gate-Pfad eingesetzt werden darf.

### `task_type`

Der konkrete Maintenance-Task-Typ, fuer den die Routing-Entscheidung gilt.

### `go_no_go_status`

Der explizite Freigabestatus des Routing-Pfads fuer diesen Task.

Erlaubte Werte mindestens:

- `draft`
- `review`
- `go`
- `no_go`
- `blocked`
- `superseded`

### `retry_policy_ref`

Referenz auf die Regel, wann derselbe Maintenance-Pfad noch einmal versucht werden darf.

### `backoff_policy_ref`

Referenz auf die Regel, wie Wiederholungen zeitlich oder operativ gedrosselt werden, damit kein stiller Schleifen- oder Kostenpfad entsteht.

### `cost_budget_ref`

Referenz auf das lesbare Kosten- oder Verbrauchsbudget, das den Routing-Pfad begrenzt.

### `max_retries`

Die harte Obergrenze fuer Wiederholungen innerhalb dieses Routing-Pfads.

### `estimated_cost`

Die kompakte Schaetzung fuer den Kosten- oder Ressourcenverbrauch des geplanten Pfads.

### `latency_budget_ms`

Die harte Obergrenze fuer die tolerierte Laufzeit des Routing-Pfads.

### `token_budget`

Die harte Obergrenze fuer Text-, Prompt- oder Kontextverbrauch des Routing-Pfads.

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer Gate-Grund, Routing-Risiko, Kosten- oder Retry-Entscheidung.

### `review_item_ref`

Referenz auf das Review Item, wenn der Task nicht sicher ueber kleines Modell, Retry oder Fallback weiterlaufen darf.

### `failure_reason`

Die kleinste lesbare Begruendung, warum der bisherige Pfad nicht ausreicht oder gestoppt werden muss.

### `next_action`

Die explizite Folgeaktion fuer den Task.

Mindestens modellierbar:

- `stay_on_maintenance_model`
- `retry_maintenance_model`
- `route_to_fallback_model`
- `route_to_review`
- `stop`

## Nutzer-Sicht

Nutzer sollen verstehen, was automatisch passiert und was nicht.

Automatischer Retry passiert, wenn:

- der Task weiter bounded bleibt
- der Fehler oder die Abweichung retry-faehig ist
- `max_retries` nicht erreicht ist
- `backoff_policy_ref` und Budgets den Wiederholungsversuch erlauben

Ein groesseres Modell wird gefragt, wenn:

- ein expliziter Gate-Grund aus `LM6` vorliegt
- das kleine Modell fuer diesen Task nicht genug Evidence, Struktur oder Sicherheit liefert
- ein begrenzter Reviewer- oder Retry-Pfad sinnvoller ist als blinder Wiederholungsversuch

Etwas landet im Review, wenn:

- Risiko, Drift oder Unsicherheit trotz Retry/Fallback zu hoch bleiben
- Budgetgrenzen erreicht sind
- keine saubere maschinelle Folgeaktion mehr verantwortbar ist

Wichtig aus Nutzersicht:

- das groessere Modell ist nicht still der wahre Default
- Retry ist nicht endlos
- Routing trifft keine Wahrheitsentscheidung

## Charlie-Sicht

Charlie braucht eine strengere und maschinenlesbare Sicht auf das Fallback Routing.

Charlie soll erkennen koennen:

- bleibt `maintenance_model_ref` der Default
- gibt es fuer Fallback einen klaren Gate-Grund aus `LM6`
- sind Retry und Backoff endlich und budgetiert
- bleiben Kosten-, Latenz- und Token-Grenzen sichtbar
- gibt es fuer riskante Faelle einen eindeutigen Review-Pfad
- bleibt Routing frei von Truth-Write-Sprache

Charlie braucht mindestens:

- `routing_decision_id`
- `maintenance_model_ref`
- `fallback_model_ref`
- `task_type`
- `go_no_go_status`
- `retry_policy_ref`
- `backoff_policy_ref`
- `cost_budget_ref`
- `max_retries`
- `estimated_cost`
- `latency_budget_ms`
- `token_budget`
- `risk_evidence_ref`
- `review_item_ref`
- `failure_reason`
- `next_action`

Charlie darf Bob das Routing-Modell als bereit melden lassen, wenn:

- `maintenance_model_ref` explizit der Default bleibt
- `fallback_model_ref` nur mit Gate-Grund, Budget und `risk_evidence_ref` beschrieben ist
- `retry_policy_ref`, `backoff_policy_ref` und `max_retries` Pflicht bleiben
- `estimated_cost`, `latency_budget_ms` und `token_budget` den Pfad begrenzen
- `review_item_ref` fuer unklare oder riskante Faelle modellierbar ist
- `next_action` klar zwischen Retry, Fallback, Review und Stop unterscheidet

Charlie muss stoppen, wenn:

- kein Gate-Grund fuer Fallback existiert
- Fallback implizit oder explizit zum Default wird
- Retry unbounded oder ohne Backoff bleibt
- Kosten-, Latenz- oder Token-Budgets fehlen
- `risk_evidence_ref` fehlt
- keine Review-Route existiert
- Truth-Write-Sprache auftaucht
- globale, Research- oder Accelerator-Claims in das Routing geschmuggelt werden

## Regeln

### Default bleibt das Maintenance-Modell

`maintenance_model_ref` ist der normale Startpfad. Routing darf diesen Default nicht still auf das groessere Modell verschieben.

### Fallback braucht Gate-Grund

`fallback_model_ref` darf nur genutzt werden, wenn ein klarer Gate-Grund aus `LM6` vorliegt, zum Beispiel:

- struktureller Schema-Fehler
- zu schwache Evidence- oder Citation-Abdeckung
- zu hohe Unsicherheit
- zu hoher `drift_score`
- zu hohes `hallucination_risk`

### Fallback braucht Begruendung und Budget

Fallback ohne `risk_evidence_ref`, `cost_budget_ref`, `estimated_cost`, `latency_budget_ms` oder `token_budget` ist nicht erlaubt.

### Retry ist endlich

`max_retries` muss endlich und klein bleiben. Es darf keinen unendlichen Wiederholungsmodus geben.

### Retry braucht Backoff

`retry_policy_ref` und `backoff_policy_ref` muessen zusammen verhindern:

- enge Schleifen
- still steigende Kosten
- wachsende Latenz ohne sichtbare Begruendung
- impliziten Token-Drift

### Kein stiller Kosten-, Token- oder Latenz-Drift

`estimated_cost`, `latency_budget_ms` und `token_budget` muessen fuer jeden Routing-Pfad sichtbar begrenzt bleiben.

### Review bleibt eigenstaendiger Zielpfad

Wenn Retry oder Fallback nicht mehr sauber begruendbar sind, muss `review_item_ref` der lesbare Sicherheitsausgang sein.

### Routing schreibt keine Wahrheit

Routing entscheidet nur ueber:

- beim kleinen Modell bleiben
- Retry
- Fallback
- Review
- Stop

Es entscheidet nicht ueber:

- Truth-Writes
- kanonische Graph- oder Memory-Aenderungen
- globale inhaltliche Wahrheitsannahmen

## Retry-, Fallback- und Review-Sprache

### Wann `retry_maintenance_model` sinnvoll ist

Der Retry-Pfad ist geeignet, wenn:

- der Fehler transient oder lokal wirkt
- der Task bounded bleibt
- Budgets noch frei sind
- `max_retries` nicht erreicht ist
- kein staerkeres Risiko gegen erneuten Versuch spricht

### Wann `route_to_fallback_model` sinnvoll ist

Der Fallback-Pfad ist geeignet, wenn:

- ein klarer Gate-Grund gegen das kleine Modell vorliegt
- ein groesseres Modell als Reviewer oder Retry helfen kann
- Budgets dies noch erlauben
- Review nicht schon der sicherere Ausgang ist

### Wann `route_to_review` sinnvoll ist

Der Review-Pfad ist geeignet, wenn:

- Risiko oder Unsicherheit zu hoch bleiben
- Retry oder Fallback das Problem nicht sauber begrenzen
- Evidence widerspruechlich ist
- Kosten oder Latenz nicht mehr verantwortbar sind

## Stop-Regeln

`LM7A` oder spaetere Fallback-Routing-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- fehlender Gate-Grund fuer Fallback
- `fallback_model_ref` wird zum Default
- unbounded `max_retries`
- fehlende `retry_policy_ref`
- fehlende `backoff_policy_ref`
- fehlende `cost_budget_ref`
- fehlende `estimated_cost`
- fehlende `latency_budget_ms`
- fehlende `token_budget`
- fehlende `risk_evidence_ref`
- fehlende `review_item_ref` fuer riskante oder unklare Faelle
- Truth-Write-Sprache
- globale, Research- oder Accelerator-Claims

## Nicht-Ziele

`LM7A` fuehrt bewusst nicht aus:

- keine Implementierung
- kein UI
- keinen Runtime Router
- keinen echten Modellaufruf
- keine Kostenmessung
- keine Datenbank

Der Slice friert nur die Routing-Sprache fuer Retry, Backoff, Fallback und Review ein.

## Handoff an Bob

Bobs spaeteres Fallback-Routing-Modell soll mindestens diese Felder abbilden oder validieren:

- `routing_decision_id`
- `maintenance_model_ref`
- `fallback_model_ref`
- `task_type`
- `go_no_go_status`
- `retry_policy_ref`
- `backoff_policy_ref`
- `cost_budget_ref`
- `max_retries`
- `estimated_cost`
- `latency_budget_ms`
- `token_budget`
- `risk_evidence_ref`
- `review_item_ref`
- `failure_reason`
- `next_action`

Minimum-Regeln fuer Bobs Modell:

- `maintenance_model_ref` muss explizit als Default-Pfad modellierbar sein
- `fallback_model_ref` darf nicht ohne Gate-Grund und `risk_evidence_ref` routebar sein
- `task_type` muss Pflichtfeld bleiben
- `max_retries` muss endlich und validierbar sein
- `retry_policy_ref` und `backoff_policy_ref` duerfen nicht fehlen
- `cost_budget_ref`, `estimated_cost`, `latency_budget_ms` und `token_budget` duerfen nicht offen oder implizit unbounded sein
- `review_item_ref` muss fuer Stop- oder Risiko-Faelle modellierbar bleiben
- `next_action` muss mindestens zwischen kleinem Modell, Retry, Fallback, Review und Stop unterscheiden
- das Modell darf keine Truth-Write-Freigabe und keinen impliziten Fallback-Default enthalten

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `retry_count`
- `budget_remaining_ref`
- `route_reason_code`
- `cooldown_until`
- `review_priority`
- `task_scope_ref`

## Akzeptanz fuer diesen Vertrag

`LM7A-fallback-routing-contract` ist erfuellt, wenn:

- die Begriffe `routing_decision_id`, `maintenance_model_ref`, `fallback_model_ref`, `task_type`, `go_no_go_status`, `retry_policy_ref`, `backoff_policy_ref`, `cost_budget_ref`, `max_retries`, `estimated_cost`, `latency_budget_ms`, `token_budget`, `risk_evidence_ref`, `review_item_ref`, `failure_reason`, `next_action` klar definiert sind
- Nutzer-Sicht erklaert, wann Retry, Fallback oder Review passiert
- Charlie-Sicht klar macht, wann Bob das Routing-Modell als bereit melden darf und wann gestoppt werden muss
- Regeln Maintenance-Default, Gate-Grund, Budgetpflicht, endliche Retries, Backoff, kein stiller Drift und kein Truth-Write sauber priorisieren
- Stop-Regeln fehlenden Gate-Grund, Fallback-Default, unbounded Retry, fehlende Budgets, fehlende Risk Evidence, fehlende Review-Route und Research-Claims blockieren
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Fallback-Routing-Modell bekommt
