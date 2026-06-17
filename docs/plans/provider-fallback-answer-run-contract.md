# Provider Fallback Answer Run Contract

Stand: 2026-06-17

Status: **FINAL1A Docs-Contract fuer das Gate `provider_fallback_answer_run`**

Quellen:

- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/1.0-evidence-release-checklist.md`
- `docs/plans/provider-proof-operator-runbook.md`
- `docs/plans/live-provider-proof-run-contract.md`

Dieser Contract definiert den finalen Release-Vertrag fuer den Provider-/Fallback-Antwortlauf vor externem `1.0.0`. Er beschreibt keine echten Provider-Aufrufe, keine RAG-Ausfuehrung, keine Netzwerknutzung und keine Runtime-Aktivierung. Der Slice friert nur ein, wie Operator-Evidence fuer einen echten Antwortlauf mit ready Query-Index, Default-/Fallback-Modellpfad und ehrlicher Go/Partial/No-Go-Sprache redigiert, nachvollziehbar und secret-frei dokumentiert werden muss.

## Purpose

`FINAL1A` ist die letzte operator-taugliche Release-Grenze fuer den echten Provider-/Fallback-Antwortlauf.

Der Contract soll beantworten:

- wann das manuelle Gate `provider_fallback_answer_run` ueberhaupt als vorbereitet gelten darf
- welche Pflicht-Evidence fuer ready Query-Index, Default-Modell, Fallback-Modell und redigierten Antwortlauf vorliegen muss
- wie Redaction fuer Prompts, Antworten und Providernamen sauber bleibt
- welche Go/Partial/No-Go-Kriterien fuer das externe `1.0.0` gelten
- wie Alice, Bob und Charlie diese Evidence ohne Secrets und ohne Live-Runtime-Code vorbereiten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Provider-Run
- keine RAG-Ausfuehrung
- keine Netzwerknutzung
- keine Telegram-, Export-/Import-/Rebuild- oder Host-Aktion
- keine Plugin-Arbeit, keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime
- kein externes `1.0.0`-Go allein durch diesen Slice

## Gate Scope

Die Section `gate_scope` soll den Umfang des spaeteren manuellen Gates klar begrenzen.

Pflicht-Gate-ID:

- `provider_fallback_answer_run`

Zum Scope des spaeteren echten manuellen Laufs gehoert:

- ready Query-Index vorab bestaetigen
- Default-Modellpfad beobachten
- Fallback-Modellpfad beobachten oder sauber als nicht verfuegbar markieren
- eine harmlose, redigierbare Testfrage dokumentieren
- Antwort- und Fallback-Verhalten als redigierte Evidence festhalten
- Known Limits gegen das beobachtete Verhalten reviewen

Nicht zum Scope gehoert:

- Provider-Code aendern
- Router- oder RAG-Architektur umbauen
- Secrets im Repo, in Docs, in Tests oder in Handoffs festhalten

## Pflicht-Evidence

Die Section `required_evidence` soll die minimalen Belege fuer dieses Release-Gate festziehen.

Pflicht-Evidence:

- `ready_query_index_recorded`
- `default_model_recorded`
- `fallback_model_recorded`
- `answer_prompt_recorded`
- `answer_result_recorded_redacted`
- `fallback_behavior_explained`
- `known_limits_reviewed`
- `operator_confirmation_recorded`

### `ready_query_index_recorded`

Der Query-Index muss fuer den spaeteren Lauf als `ready` oder klar nicht-ready dokumentiert sein. Fuer ein ehrliches `Go` ist `ready` Pflicht.

### `default_model_recorded`

Der beobachtete Default-Modellpfad muss als redigierte, harmlose Metainformation festgehalten werden.

### `fallback_model_recorded`

Der beobachtete Fallback-Modellpfad muss als redigierte Metainformation festgehalten werden oder sauber als nicht verfuegbar markiert bleiben.

### `answer_prompt_recorded`

Die verwendete Testfrage muss dokumentiert sein, aber so harmlos und redigiert, dass keine sensiblen Inhalte erscheinen.

### `answer_result_recorded_redacted`

Das Ergebnis des Antwortlaufs muss dokumentiert sein, aber nur in redigierter Kurzform, nicht als Rohantwort.

### `fallback_behavior_explained`

Das beobachtete Fallback-Verhalten muss mit `fallback_reason` oder gleichwertiger erklaerter Beobachtung nachvollziehbar sein.

### `known_limits_reviewed`

Der Lauf muss gegen bekannte Grenzen gelesen werden, damit kein Teil-Erfolg still als Voll-Go verkauft wird.

### `operator_confirmation_recorded`

Ein echter manueller Lauf braucht bewusste Operator-Bestaetigung und darf nicht versehentlich gestartet oder als gegeben angenommen werden.

## Redaction Rules

Die Section `redaction_rules` muss die Geheimnis- und Payload-Grenzen hart setzen.

Nie erfassen oder kopieren:

- Provider-Secrets
- API-Keys
- Tokens
- Chat-IDs
- private Pfade
- komplette Prompts mit sensitiven Daten
- komplette Providerantworten
- rohe Logs

Nur redigiert oder kompakt erlaubt:

- harmlose Testfrage
- `selected_model`
- `selected_endpoint_id`
- `selected_role`
- `answer_mode`
- `fallback_reason`
- `model_capability_warnings`
- kurze Go/Partial/No-Go-Notiz

Wichtig:

- Providernamen duerfen nur so weit erscheinen, wie sie fuer Debug-/Evidence-Zwecke noetig und secret-frei sind
- Rohpayloads bleiben verboten, auch wenn der Lauf erfolgreich war

## Go Partial No-Go Kriterien

Die Section `go_partial_no_go_criteria` soll die finale Release-Sprache fuer dieses Gate festlegen.

### `Go`

Nur wenn:

- Query-Index als `ready` dokumentiert ist
- Default-Modellpfad real beobachtet wurde
- Fallback-Verhalten real beobachtet oder sauber begruendet nicht verfuegbar ist
- redigierte Antwort-Evidence vorliegt
- Known Limits mit dem Beobachtungsstand abgeglichen wurden
- keine Secret-, Logging- oder Scope-Regel verletzt wurde

### `Partial`

Wenn:

- ein Teilpfad sauber und ehrlich belegt ist, aber Fallback oder lokaler Pfad nicht voll bestaetigt werden konnten
- Query-Index- oder Providerlage zwar dokumentiert, aber nicht voll release-tauglich ist
- das Ergebnis brauchbare, aber nicht vollstaendige Release-Evidence liefert

### `No-Go`

Wenn:

- Query-Index nicht `ready` ist
- Fallback-Verhalten unklar bleibt
- Secrets, Rohpayloads oder unsafe Logs auftauchen
- der Lauf ohne ausdrueckliches Go oder ausserhalb der Boundary gestartet wurde
- Beobachtungen dem dokumentierten Known-Limits-Rahmen widersprechen

## Verbotene Aktionen Und Blocker

Die Section `forbidden_actions_and_blockers` muss die harten Release-Blocker nennen.

Mindestens:

- `provider_secret_persisted`
- `provider_secret_logged`
- `raw_provider_payload_persisted`
- `missing_ready_query_index`
- `fallback_behavior_unknown`
- `network_run_without_go`
- `plugin_scope_touched`
- `unsafe_evidence_logging_enabled`

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- Plugin-Modul bleibt eingefroren und wird in diesem Gate nicht beruehrt

## Statussprache

Pflicht-Statuswerte:

- `provider_answer_run_ready`
- `needs_provider_evidence`
- `blocked`
- `deferred`

### `provider_answer_run_ready`

Der Gate-Lauf ist sauber vorbereitet: Query-Index-Readiness, redigierte Prompt-/Antwortfelder, Default-/Fallback-Metadaten und Operator-Bestaetigung sind klar beschrieben.

### `needs_provider_evidence`

Mindestens ein Pflichtbeleg zu Query-Index, Fallback, Antwortresultat, Known Limits oder Operator-Bestaetigung fehlt noch oder ist zu unscharf.

### `blocked`

Mindestens ein harter Verstoss liegt vor, zum Beispiel Secret-Persistenz, unsichere Logs, fehlender ready Query-Index oder Netzwerk-Run ohne Freigabe.

### `deferred`

Die Gate-Entscheidung ist bewusst verschoben und bleibt ausserhalb dieses Slices offen.

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer dieses Release-Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Redaction-, Go-/Partial-/No-Go- und Known-Limits-Texte
- Klartext: echte Evidence ja, Rohsecrets und Rohpayloads nein

### Bob

Bob verantwortet:

- ein isoliertes read-only Validator- oder Summary-Modell fuer das Gate
- Validierung der Pflicht-Evidence und Gate-Statuswerte
- keine Provider-, Netzwerk-, RAG- oder Plugin-Aktivierung

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- fokussierte Tests gegen fehlende Pflicht-Evidence und unsafe Logging
- Stop-Entscheidung bei Secret-, Scope- oder Network-Go-Verstoss

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isoliertes read-only Evidence-Validator- oder Summary-Modell
- Validierung der Pflicht-Evidence
- Statusableitung fuer `provider_answer_run_ready`, `needs_provider_evidence`, `blocked`, `deferred`
- Tests mit rein redigierten, secret-freien und netzwerkfreien Fixtures

Nicht erlaubt:

- echte Provider-Aufrufe
- RAG-Ausfuehrung
- Netzwerknutzung
- Plugin-, Host- oder Telegram-Aktivierung
- Persistenz von Rohpayloads oder Secrets

## Example Safe Gate Reading

Zulaessig:

- `ready_query_index_recorded = true`
- `default_model_recorded = true`
- `fallback_model_recorded = true`
- `answer_prompt_recorded = true`
- `answer_result_recorded_redacted = true`
- `fallback_behavior_explained = true`
- `known_limits_reviewed = true`
- `operator_confirmation_recorded = true`
- `status = provider_answer_run_ready`

Nicht zulaessig:

- `provider_secret_persisted = true`
- `provider_secret_logged = true`
- `raw_provider_payload_persisted = true`
- `network_run_without_go = true`
- `plugin_scope_touched = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer den Provider-/Fallback-Antwortlauf. Er macht ready Query-Index, Default-/Fallback-Pfad, redigierte Antwort-Evidence und ehrliche Go/Partial/No-Go-Kriterien releasefaehig lesbar, ohne echte Provider-Aktionen, ohne Secrets und ohne Plugin-Scope.
