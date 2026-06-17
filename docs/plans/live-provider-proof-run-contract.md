# Live Provider Proof Run Contract

Stand: 2026-06-17

Status: **LIVE1A Docs-Contract fuer das offene manuelle Gate `provider_fallback_answer_run`**

Quellen:

- `docs/plans/live-release-evidence-closeout-contract.md`
- `docs/plans/provider-proof-operator-runbook.md`
- `docs/plans/1.0-evidence-release-checklist.md`
- `docs/plans/1.0-manual-release-evidence-runbook.md`

Dieser Contract definiert die sichere Operator-Sprache und Evidence-Anforderung fuer den spaeteren manuellen Provider-/Fallback-Antwortlauf. Das Gate bleibt rein vorbereitend: Es startet keinen Provider, keinen RAG-Pfad, kein Netzwerk und keinen Runtime-Hook. Ziel ist nur, das offene externe Gate `provider_fallback_answer_run` sauber zu beschreiben, damit ein spaeterer manueller Lauf redigierte und nachvollziehbare Evidence liefern kann.

## Purpose

`LIVE1A` ist die Vertrags- und Runbook-Schicht fuer das erste offene externe `1.0`-Gate.

Der Contract soll beantworten:

- was genau zum manuellen Gate `provider_fallback_answer_run` gehoert
- welche Operator-Inputs vor dem spaeteren Lauf vorliegen muessen
- wie der Query-Index-Precheck beschrieben wird
- welche Modell- und Fallback-Beobachtungen spaeter festgehalten werden muessen
- wie redigierte Evidence ohne Secrets, Tokens oder Rohantworten erfasst wird

## Leitregel

`LIVE1A` ist Vorbereitung und Contract, kein Providerlauf und kein externes Release-Go.

Das bedeutet:

- kein echter Provider- oder DeepSeek-Aufruf
- kein echter Query- oder RAG-Lauf
- keine Netzwerkaktivierung
- keine Runtime-Hooks
- kein automatisches oder implizites `Go`

## Manual Gate Scope

Die Section `manual_gate_scope` soll den Umfang des spaeteren manuellen Gates klar abgrenzen.

Pflicht-Gate-ID:

- `provider_fallback_answer_run`

Zum Scope des spaeteren echten Operator-Laufs gehoert:

- Query-Index-Readiness pruefen
- Default-Modell-Antwort beobachten
- Fallback-Modell-Antwort beobachten oder sauber als nicht verfuegbar markieren
- lokales/DeepSeek-Szenario beobachten oder sauber als nicht verfuegbar markieren
- nur redigierte Evidence erfassen

Nicht zum Scope gehoert:

- Provider umkonfigurieren als Feature-Arbeit
- RAG-/Router-Code aendern
- Netzwerk-, Host- oder Telegram-Aktionen ausserhalb des spaeteren manuellen Provider-Laufs

## Required Operator Inputs

Die Section `required_operator_inputs` soll beschreiben, was vor dem spaeteren manuellen Lauf bekannt sein muss.

Mindestens:

- Branch und Commit
- authentifizierte Session
- harmlose Testfrage
- dokumentierter Zielpfad fuer Default-, Fallback- und lokales/DeepSeek-Szenario
- Query-Index-Status oder klares `not ready`
- klare Redaktionsregeln fuer die spaetere Evidence-Erfassung

Wichtig:

- fehlende Inputs fuehren spaeter zu `needs_operator_input` statt zu improvisierten Live-Checks

## Ready Query Index Precheck

Die Section `ready_query_index_precheck` soll klar machen, wie der spaetere Lauf den Query-Index-Zustand vorab beschreiben muss.

Zulaessige Ergebnisse:

- `ready`
- `not_ready`
- `unclear`

Regel:

- nur `ready` erlaubt einen spaeteren echten Answer-Run ohne Vorbehalt
- `not_ready` oder `unclear` blockiert ein externes `Go` und fuehrt zu manueller Dokumentation statt zu stiller Eskalation

Wichtig:

- dieser Contract fuehrt den Precheck nicht aus
- er beschreibt nur die spaetere Pflichtbeobachtung

## Model Answer Matrix

Die Section `model_answer_matrix` soll die spaeter erwarteten Beobachtungsfelder fuer alle Teilpfade definieren.

Mindestens:

- Default-Modell-Antwort
- Fallback-Modell-Antwort
- lokales oder DeepSeek-Szenario

Zu erfassende Felder spaeter:

- `answer_mode`
- `selected_model`
- `selected_endpoint_id`
- `selected_role`
- `model_capability_warnings`

Wichtig:

- die Matrix ist Beobachtungsstruktur
- sie ist kein aktiver Providerlauf

## Fallback Expectations

Die Section `fallback_expectations` soll klar machen, was der spaetere Lauf als nachvollziehbares Fallback-Verhalten dokumentieren muss.

Mindestens:

- `fallback_reason` ist vorhanden oder explizit leer erklaert
- Fallback-Verhalten passt zur beobachteten Provider-Lage
- Fallback wird real beobachtet oder sauber als bewusst nicht verfuegbar markiert
- lokales/DeepSeek-Szenario wird real beobachtet oder sauber als nicht verfuegbar markiert

Wichtig:

- unerwarteter Provider oder unerklärliches Fallback fuehrt spaeter zu `No-Go`
- dieser Contract selbst erzeugt keine technische Diagnose

## Evidence Capture Rules

Die Section `evidence_capture_rules` soll definieren, wie spaetere manuelle Evidence sicher erfasst wird.

Operator muss spaeter manuell belegen:

- ready Query-Index oder sauber dokumentiertes `not_ready`
- Default-Modell-Antwort
- Fallback-Modell-Antwort
- lokale/DeepSeek-Verfuegbarkeit oder begruendete Nichtverfuegbarkeit
- redigierte Evidence fuer jeden Teilpfad

Zulaessig:

- Datum
- Commit
- harmlose Testfrage
- kompakte Statusfelder
- kurze Go/Partial/No-Go-Notiz

Nicht zulaessig:

- komplette Providerantworten
- komplette Quellenlisten
- rohe Logs

## Redaction Rules

Die Section `redaction_rules` muss die Redaktionsgrenzen hart setzen.

Nie erfassen oder kopieren:

- Secrets
- Tokens
- private Pfade
- komplette Providerantworten
- sensible Prompts
- rohe Logs

Nur redigiert oder kompakt erlaubt:

- `selected_model`
- `selected_endpoint_id`
- `selected_role`
- `answer_mode`
- `fallback_reason`
- `model_capability_warnings`

Wichtig:

- redigierte Evidence ist Pflicht
- offene Geheimnisse oder Rohantworten stoppen den spaeteren manuellen Lauf

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Operator-Sprache
- Runbook- und Gate-Texte
- Go-/No-Go- und Redaktionsregeln

### Bob

Bob verantwortet:

- isolierte read-only Plan- oder Checker-Modelle
- Status `ready_for_manual_operator_run` oder `needs_operator_input`
- keine Provider-, Netzwerk- oder RAG-Aktivierung

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei unklaren Gates oder riskanter Scope-Verschiebung

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in einen unkontrollierten Live-Lauf verhindern.

Mindestens:

- wenn Query-Index nicht `ready` oder sauber `not_ready` dokumentierbar ist: stoppen
- wenn Secrets, Tokens, private Pfade oder rohe Logs auftauchen: stoppen
- wenn ein Slice echte Provider-, RAG-, Netzwerk-, Telegram-, Export-/Import- oder Host-Aktionen verlangt: stoppen
- wenn kompletter Antworttext oder sensible Prompt-Daten in Evidence landen wuerden: stoppen
- wenn Fallback oder lokale/DeepSeek-Verfuegbarkeit nicht erklaerbar ist: spaeter `needs_operator_input`, nicht `Go`

## Handoff To Live Closeout

Die Section `handoff_to_live_closeout` soll beschreiben, wie der spaetere manuelle Lauf in den LIVE-Closeout zurueckmeldet.

Mindestens:

- Gate-ID `provider_fallback_answer_run`
- Ergebnis `Go`, `Partial` oder `No-Go`
- redigierte Evidence-Referenz
- Blocker oder offene Operator-Fragen
- Hinweis, ob das zweite offene Gate `test_vault_export_import_rebuild` noch weiterhin externes `1.0` blockiert

Wichtig:

- auch ein erfolgreiches Provider-Gate allein erzeugt kein externes `1.0`-Go
- erst beide manuellen Gates zusammen duerfen den Live-Closeout veraendern

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Plan- oder Checker-Modell bauen, das einen Provider-Proof ausschliesslich als `ready_for_manual_operator_run` oder `needs_operator_input` beschreibt.

Zulaessige Inputs:

- dokumentierte Gate-Statuswerte
- Runbook- und Contract-Artefakte
- read-only Readiness- oder Closeout-Snapshots

Wichtig:

- niemals Provider oder Netzwerk starten
- keine RAG- oder Router-Aktivierung
- keine Export-/Import-, Host- oder Telegram-Aktionen

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Providerlauf
- keine RAG-, Netzwerk- oder Runtime-Aktivierung
- kein externes `1.0`-Go
- keine erfundene manuelle Evidence
- keine Token-, Secret- oder Rohantwort-Erfassung

Er legt nur fest, wie das erste offene externe Gate `provider_fallback_answer_run` sprachlich, prozessual und redaktionell sicher vorbereitet wird.
