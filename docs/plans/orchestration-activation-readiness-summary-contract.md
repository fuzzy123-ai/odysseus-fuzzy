# Orchestration Activation Readiness Summary Contract

Stand: 2026-06-17

Status: **AUTO11A Docs-Contract fuer kompakte AUTO Activation Readiness Summary**

Quellen:

- `docs/plans/orchestration-runtime-readiness-contract.md`
- `docs/plans/orchestration-operator-activation-contract.md`
- `docs/plans/orchestration-activation-summary-renderer-contract.md`

Dieser Contract definiert eine kompakte Summary fuer Operator, Charlie, Dashboard und spaetere Automation-Lenses. Die Summary beschreibt nur Zustand, Luecken und naechste sichere Schritte. Sie fuehrt keine Aktivierung aus und darf nicht als stilles Live-Go interpretiert werden.

## Ziel

Odysseus braucht eine kleine, stabile Activation Readiness Summary, die schnell beantwortet:

- in welchem Modus die AUTO-Orchestration aktuell steht
- ob Live-Dispatch ueberhaupt erlaubt ist
- wie viele offene Luecken noch bestehen
- welche Gruende gerade blockieren
- welche naechste sichere Aktion wirklich erlaubt ist

## Leitregel

Summary ist Orientierung, nicht Aktivierung.

Das bedeutet:

- die Summary darf keine Live-Aktion ausloesen
- die Summary darf keine Operator-Freigabe ersetzen
- die Summary darf `ready` nicht implizit behaupten, wenn noch Operator- oder Blocking-Bedingungen offen sind

## Pflichtfelder

Die spaetere Summary soll mindestens diese Felder enthalten:

- `mode`
- `live_dispatch_allowed`
- `open_gap_count`
- `blocking_reasons`
- `allowed_actions`
- `next_safe_action`
- `operator_required`
- `status_label`

## Bedeutung der Pflichtfelder

### `mode`

Spiegelt den aktuell bewerteten Aktivierungsmodus wider.

Erwartete Werte orientieren sich an der Aktivierungslogik:

- `disabled`
- `read_only`
- `prepare_dispatch`
- `dispatch_requires_confirm`
- `live_dispatch_limited`

### `live_dispatch_allowed`

Boolescher Kurzindikator, ob unter den aktuell freigegebenen Bedingungen ueberhaupt ein Live-Dispatch erlaubt waere.

Wichtig:

- `true` bedeutet nicht unbegrenzte Freigabe
- `true` darf nur innerhalb bestaetigter Operator-Grenzen gesetzt werden

### `open_gap_count`

Anzahl offener Readiness- oder Aktivierungsluecken, die noch sichtbar relevant sind.

Beispiele:

- fehlender Live-Thread-Hook
- fehlende Operator-Freigabe
- unklare Hotfile-Locks
- rote Tests

### `blocking_reasons`

Kurze, maschinenlesbare oder UI-taugliche Gruende, warum keine hoehere Aktivierungsstufe erlaubt ist.

Beispiele:

- `ambiguous_thread`
- `operator_confirmation_missing`
- `red_tests`
- `foreign_staged_files`
- `hotfile_overlap`
- `unknown_scope`

### `allowed_actions`

Kompakte Liste der Aktionen, die unter dem aktuellen Zustand sicher zulaessig sind.

Typische Werte:

- `read_status`
- `assess_gaps`
- `prepare_dispatch_plan`
- `request_confirmation`
- `downgrade_activation`
- `disable_runtime`

### `next_safe_action`

Eine einzige, klare naechste sichere Aktion.

Beispiele:

- `request operator confirmation`
- `resolve ambiguous thread mapping`
- `keep runtime in read_only mode`

Die Summary soll hier nie mehrere konkurrierende Handlungsaufforderungen gleichzeitig ausgeben.

### `operator_required`

Boolescher Indikator, ob eine bewusste Charlie- oder Operator-Entscheidung noch noetig ist.

Wenn `operator_required` wahr ist:

- kein stilles `ready`
- kein stilles Live-Go

### `status_label`

Kompakte, UI-geeignete Verdichtung des Gesamtzustands.

## Status-Labels

Die Summary soll mindestens diese Labels kennen:

- `disabled`
- `read_only`
- `prepare_only`
- `confirm_required`
- `live_limited_ready`
- `blocked`

## Bedeutung der Status-Labels

### `disabled`

AUTO-Orchestration ist abgeschaltet oder bewusst nicht freigegeben.

### `read_only`

Nur Lesen und Gap-Bewertung sind erlaubt. Keine Live-Aktivierung.

### `prepare_only`

Dispatch oder Aktivierung duerfen vorbereitet, aber noch nicht live ausgefuehrt werden.

### `confirm_required`

Technische Vorbereitung ist weit genug, aber eine bewusste Freigabe fehlt noch.

### `live_limited_ready`

Eine eng begrenzte Live-Freigabe waere unter bestaetigten Operator-Regeln denkbar.

Wichtig:

- nicht gleichbedeutend mit global `ready`
- nur innerhalb bestaetigter Grenzen

### `blocked`

Mindestens ein kritischer Stop oder Blocker verhindert die naechste Eskalationsstufe.

## Copy-Regeln

Die Summary muss konservative Sprache benutzen.

Mindestens diese Regeln gelten:

- kein `ready` behaupten, wenn `operator_required` wahr ist
- kein `ready` behaupten, wenn `blocking_reasons` nicht leer ist
- kein `live_limited_ready` behaupten, wenn `live_dispatch_allowed` falsch ist
- kein beruhigendes Gruen signalisieren, wenn offene Gaps fuer Live-Hooks bestehen

Empfohlene Kurzlogik:

- bei Blockern -> `blocked`
- bei offener Operator-Freigabe -> `confirm_required`
- bei nur lesender Lage -> `read_only`
- bei vorbereiteter, aber nicht freigegebener Dispatch-Lage -> `prepare_only`

## Sichere Verdichtungslogik

Die spaetere Summary soll aus den vorhandenen AUTO-Bausteinen nur verdichten, nicht neu erfinden.

Mindestens ableiten aus:

- Runtime-Readiness-Report
- ActivationPlan
- bekannten Gate- und Stop-Zustaenden

Die Kurzlogik lautet:

- wenn kritische Blocker vorliegen -> `status_label = blocked`
- wenn Operator-Freigabe fehlt -> `status_label = confirm_required`
- wenn nur Lesemodus erlaubt ist -> `status_label = read_only`
- wenn Vorbereitungen erlaubt, aber kein Live-Send erlaubt ist -> `status_label = prepare_only`
- wenn eng begrenzter Live-Dispatch explizit erlaubt ist -> `status_label = live_limited_ready`

## Beispiel fuer spaetere sichere Summary

Zulaessiges Beispiel:

- `mode: dispatch_requires_confirm`
- `live_dispatch_allowed: false`
- `open_gap_count: 2`
- `blocking_reasons: [operator_confirmation_missing]`
- `allowed_actions: [read_status, assess_gaps, request_confirmation]`
- `next_safe_action: request operator confirmation`
- `operator_required: true`
- `status_label: confirm_required`

Nicht zulaessig:

- `status_label: ready` trotz offener Operator-Freigabe
- `next_safe_action: send live dispatch now`
- `live_dispatch_allowed: true` trotz Blocking-Gruenden

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf ein kleines Modell bauen, das aus:

- `RuntimeReadinessReport`
- `OrchestrationActivationPlan`

eine stabile Summary verdichtet.

Dieses Modell soll:

- keine Live-Aktionen ausfuehren
- keine Aktivierung selbst entscheiden
- keine Threads senden
- keine Git- oder Test-Kommandos starten
- nur Status, Luecken, erlaubte Aktionen und naechste sichere Aktion ausgeben

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine echte Runtime-Aktivierung
- keinen Dashboard-Code
- keine Scheduler-, Thread-, Git- oder Test-Hooks
- keine automatische Operator-Bypass-Logik

Die Summary ist ein kompaktes Entscheidungs- und Anzeigeformat, aber kein Aktivierungsmechanismus.
