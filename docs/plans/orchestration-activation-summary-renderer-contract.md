# Orchestration Activation Summary Renderer Contract

Stand: 2026-06-17

Status: **AUTO12A Docs-Contract fuer Markdown/JSON-Renderer der AUTO Activation Readiness Summary**

Quellen:

- `docs/plans/orchestration-activation-readiness-summary-contract.md`
- `docs/plans/orchestration-operator-activation-contract.md`

Dieser Contract definiert die stabile Ausgabeform fuer Markdown- und JSON-Renderer der Activation Readiness Summary. Beide Renderer dienen nur Anzeige, Handoff und spaeteren UI-/Automation-Snapshots. Sie fuehren keine Live-Aktivierung, keine Runtime-Hooks und keine Folgeaktionen aus.

## Ziel

Odysseus braucht zwei kleine, deterministische Ausgabeformen fuer die Activation Readiness Summary:

- Markdown fuer Operator, Charlie, Handoff und Artefakte
- JSON fuer Automationshelfer, spaetere UI und validierbare Snapshot-Tests

## Leitregel

Renderer zeigen Zustand, sie aktivieren nichts.

Das bedeutet:

- Renderer fuehren keine Commands aus
- Renderer senden keine Threads
- Renderer starten keine Scheduler
- Renderer duerfen Status nicht beschoenigen oder hochstufen

## Markdown-Ausgabe

Der Markdown-Renderer soll kompakt, lesbar und no-go-sicher bleiben.

Mindestens enthalten:

- `Status Label`
- `Mode`
- `Live Dispatch erlaubt`
- `Offene Gaps`
- `Blockierende Gruende`
- `Erlaubte Actions`
- `Next Safe Action`
- `Operator Required`

## Bedeutung der Markdown-Felder

### `Status Label`

Zeigt genau das `status_label` der Summary an.

Beispiele:

- `blocked`
- `read_only`
- `confirm_required`
- `live_limited_ready`

### `Mode`

Zeigt den zugrunde liegenden Aktivierungsmodus.

Beispiele:

- `disabled`
- `read_only`
- `prepare_dispatch`
- `dispatch_requires_confirm`
- `live_dispatch_limited`

### `Live Dispatch erlaubt`

Klare Ja/Nein-Ausgabe fuer `live_dispatch_allowed`.

Wichtig:

- `Ja` ist nur zulaessig, wenn die Summary das wirklich so ausweist
- `Ja` bedeutet keine globale Vollfreigabe

### `Offene Gaps`

Zeigt `open_gap_count` als kompakte Anzahl.

Optional ergaenzbar durch kurze Einordnung wie:

- `2 offene Gaps`
- `0 offene Gaps`

### `Blockierende Gruende`

Listet `blocking_reasons` sichtbar und knapp auf.

Wenn keine Blocker vorhanden sind, soll das konservativ bleiben, zum Beispiel:

- `keine expliziten Blocker gemeldet`

Nicht schreiben:

- `alles frei`

### `Erlaubte Actions`

Listet `allowed_actions` in stabiler Reihenfolge.

Nur sichtbare Erlaubnis, keine Ausfuehrung.

### `Next Safe Action`

Zeigt genau eine naechste sichere Aktion.

Keine Mehrfachaufrufe, kein impliziter Automatismus.

### `Operator Required`

Klare Ja/Nein-Ausgabe fuer `operator_required`.

Wenn `Ja`, muss die Markdown-Copy sichtbar machen:

- keine automatische Aktivierung

## Empfohlene Markdown-Struktur

Ein spaeterer Renderer soll eine kurze, wiederholbare Struktur ausgeben, zum Beispiel:

```md
# AUTO Activation Readiness Summary

- Status Label: confirm_required
- Mode: dispatch_requires_confirm
- Live Dispatch erlaubt: nein
- Offene Gaps: 2
- Blockierende Gruende: operator_confirmation_missing
- Erlaubte Actions: read_status, assess_gaps, request_confirmation
- Next Safe Action: request operator confirmation
- Operator Required: ja
```

Wichtig:

- kompakt halten
- keine Logs
- keine Thread- oder Runtime-Details aufblasen

## JSON-Ausgabe

Der JSON-Renderer soll deterministisch, validierbar und snapshot-tauglich sein.

Mindestens enthalten:

- `status_label`
- `mode`
- `live_dispatch_allowed`
- `open_gap_count`
- `blocking_reasons`
- `allowed_actions`
- `next_safe_action`
- `operator_required`

## JSON-Regeln

Die JSON-Ausgabe muss:

- deterministisch sortiert oder stabil serialisierbar sein
- nur Summary-Daten enthalten
- keine Live-Daten einbetten
- keine Secrets enthalten
- keine Logs enthalten

Nicht enthalten:

- komplette Thread-Historien
- Provider- oder Token-Daten
- Runtime-Trace-Logs
- Scheduler-Dumps

## Determinismus und Validierbarkeit

Der spaetere JSON-Renderer soll fuer identische Eingabe identische strukturierte Ausgabe liefern.

Das bedeutet:

- stabile Feldnamen
- stabile Feldreihenfolge oder kanonische Serialisierung
- stabile Listenreihenfolge fuer `blocking_reasons` und `allowed_actions`, sofern die Summary das vorgibt

## Copy-Regeln

Renderer-Copy muss konservativ bleiben.

Mindestens diese Regeln gelten:

- `live_limited_ready` nur anzeigen, wenn die Summary das wirklich sagt
- sonst klare Block-, Read-only- oder Prepare-Sprache
- kein `ready` als freies Synonym benutzen
- kein `go` oder `activated` schreiben, wenn nur eine Summary vorliegt

Empfohlene Kurzlogik:

- bei `blocked` -> klare Stop-Sprache
- bei `read_only` -> klare Beobachtungs-/Lesesprache
- bei `prepare_only` -> klare Vorbereitungssprache
- bei `confirm_required` -> klare Freigabesprache
- bei `live_limited_ready` -> klare begrenzte Freigabesprache, niemals Vollfreigabe

## No-Go-Sicherheit

Beide Renderer muessen no-go-sicher bleiben.

Das bedeutet:

- keine implizite Live-Freigabe
- keine Formulierung, die Operator-Freigabe ersetzt
- keine Gruenfaerbung trotz offener Blocking-Gruende
- keine automatische Empfehlung zu `send_live_dispatch`

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf kleine Renderer-Funktionen bauen, die:

- eine Activation Readiness Summary nach Markdown rendern
- dieselbe Summary nach JSON rendern
- snapshotartige Tests nutzen

Wichtig:

- keine Command-Ausfuehrung
- keine Thread-Ausfuehrung
- keine Scheduler-Ausfuehrung
- keine Git- oder Test-Hooks

## Snapshot-Test-Erwartung

Spaetere Tests sollen nur pruefen:

- stabile Felder
- konservative Copy
- deterministische JSON-Ausgabe
- korrekte Ja/Nein-Darstellung fuer `live_dispatch_allowed` und `operator_required`

Nicht pruefen:

- echte Runtime-Aktivierung
- echte Thread-Hooks
- echte Scheduler-Aktionen

## Nicht-Ziele

Dieser Contract definiert bewusst nicht:

- keine Live-Orchestration
- keinen Dashboard- oder HTTP-Code
- keine Runtime- oder Thread-Hooks
- keine Logs, Secrets oder Trace-Dumps

Er legt nur fest, wie die Activation Readiness Summary spaeter sicher und deterministisch nach Markdown und JSON ausgegeben werden soll.
