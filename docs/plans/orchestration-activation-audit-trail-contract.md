# Orchestration Activation Audit Trail Contract

Stand: 2026-06-17

Status: **AUTO16A Docs-Contract fuer einen operator-sicheren Orchestration Activation Audit Trail**

Quellen:

- `docs/plans/orchestration-operator-activation-contract.md`
- `docs/plans/orchestration-activation-bundle-contract.md`
- `docs/plans/orchestration-activation-readiness-summary-contract.md`
- `docs/plans/orchestration-activation-handoff-checklist-contract.md`

Dieser Contract definiert einen nachvollziehbaren Audit Trail fuer spaetere Aktivierungsentscheidungen in der AUTO-Orchestration. Er beschreibt nur das Modell fuer Ereignisse und ihren Zusammenhang. Der Slice fuehrt bewusst keine echte Persistenz, keine Runtime-Hooks, keine Thread-Sends, keine Scheduler-Aktivierung und keine Git-/Test-Ausfuehrung aus.

## Ziel

Odysseus braucht einen klaren, operator-sicheren Audit Trail fuer Aktivierungsentscheidungen.

Der Audit Trail soll spaeter nachvollziehbar machen:

- was aktiviert werden sollte oder nicht
- warum eine Freigabe, Sperre oder Verschiebung entstand
- wer die Entscheidung getroffen hat
- welche Gates, Stop-Regeln und Evidence-Referenzen beteiligt waren

## Leitregel

Audit Trail dokumentiert Entscheidungen, fuehrt sie aber nicht aus.

Das bedeutet:

- kein Live-Send
- kein Scheduler-Start
- keine Git-/Test-Ausfuehrung
- keine implizite Aktivierung nur durch einen Audit-Eintrag

## Audit Events

Der spaetere Audit Trail soll mindestens diese Ereignistypen kennen:

- `activation_requested`
- `preflight_checked`
- `gate_passed`
- `gate_blocked`
- `operator_approved`
- `activation_deferred`
- `activation_cancelled`

## Bedeutung der Audit Events

### `activation_requested`

Es wurde eine Aktivierungspruefung oder Aktivierungsabsicht angemeldet.

### `preflight_checked`

Der vorbereitende Check gegen Gaps, Gates oder Stop-Regeln wurde dokumentiert.

### `gate_passed`

Ein relevantes Gate wurde als bestanden protokolliert.

Wichtig:

- kein Global-Go allein aus einem einzelnen Gate

### `gate_blocked`

Ein relevantes Gate oder eine Stop-Regel blockiert die naechste Aktivierungsstufe.

### `operator_approved`

Ein Operator oder Charlie hat eine explizite Freigabe fuer den naechsten erlaubten Schritt dokumentiert.

Wichtig:

- nur Freigabe-Ereignis
- keine Ausfuehrung

### `activation_deferred`

Eine Aktivierung oder Eskalation wurde bewusst vertagt.

### `activation_cancelled`

Eine Aktivierungsabsicht wurde verworfen oder abgebrochen.

## Pflichtfelder

Jedes Audit-Ereignis soll mindestens diese Pflichtfelder enthalten:

- `event_id`
- `run_id`
- `slice_id`
- `actor`
- `timestamp`
- `decision`
- `reason`
- `evidence_refs`
- `changed_files`
- `test_refs`

## Bedeutung der Pflichtfelder

### `event_id`

Stabile Kennung des Audit-Ereignisses.

### `run_id`

Kennung des betroffenen Aktivierungs- oder Orchestration-Laufs.

### `slice_id`

Zuordnung zu dem Slice oder Gate-Kontext, in dem die Entscheidung relevant ist.

### `actor`

Wer die Entscheidung oder Pruefung dokumentiert hat.

Beispiele:

- `operator`
- `charlie`
- `system_readiness_helper`

### `timestamp`

Zeitpunkt des dokumentierten Ereignisses.

Wichtig:

- Metadatum fuer Nachvollziehbarkeit
- keine Aktivierungslogik allein aus Zeitstempel

### `decision`

Kompakte Verdichtung der Entscheidung.

Beispiele:

- `approved`
- `blocked`
- `deferred`
- `cancelled`
- `observed`

### `reason`

Kurze Begruendung der Entscheidung.

Beispiele:

- `operator confirmation missing`
- `foreign staged files detected`
- `all required preflight gates documented`

### `evidence_refs`

Referenzen auf zugehoerige Evidence-Quellen oder Snapshots.

Beispiele:

- Bundle-Snapshot
- Readiness-Report
- Summary-Output

### `changed_files`

Kontextfeld fuer relevante Dateimengen oder Scope-Hinweise.

Wichtig:

- als Referenz
- nicht als implizite Git-Aktion

### `test_refs`

Referenzen auf Testnachweise oder Teststatus.

Wichtig:

- nur Verweis oder Zusammenfassung
- kein Ausfuehren von Tests in diesem Modell

## Keine Secrets und keine Vollprompts

Der Audit Trail darf keine sensiblen Inhalte als Volltext mitschreiben.

Nicht enthalten:

- Secrets
- Tokens
- vollstaendige Prompts
- rohe Logs
- komplette Thread-Historien

Zulaessig:

- kurze Gruende
- Evidence-Referenzen
- knappe Test- oder Scope-Hinweise

## Append-only Modell

Der Audit Trail ist in diesem Slice ausdruecklich als append-only Modell gedacht.

Das bedeutet:

- neue Ereignisse werden konzeptionell hinzugefuegt
- bestehende Ereignisse werden nicht still ueberschrieben
- keine echte Persistenz-Implementierung in diesem Slice

## Beziehung zu Gates und Stop-Regeln

Der Audit Trail soll spaeter sichtbar machen koennen:

- welche Gates geprueft wurden
- welche Stop-Regeln gegriffen haben
- warum eine Eskalation nicht erlaubt war

Die Kurzlogik lautet:

- `gate_passed` dokumentiert nur den bestandenen Teilaspekt
- `gate_blocked` dokumentiert die wirksame Sperre
- `operator_approved` dokumentiert eine Freigabeentscheidung, aber keine Ausfuehrung

## Operator-Sicht

Ein Operator soll spaeter aus dem Audit Trail lesen koennen:

- was beantragt wurde
- was geprueft wurde
- was blockiert oder freigegeben wurde
- auf welche Evidence sich das stuetzt

Ohne:

- Runtime-Interna erraten zu muessen
- ganze Prompts lesen zu muessen

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Modelle bauen fuer:

- `AuditEvent`
- `AuditTrail`

Wichtig:

- keine IO
- keine Threads
- keine Git-Ausfuehrung
- keine Test-Ausfuehrung
- keine Scheduler- oder Runtime-Hooks

## Beispiel fuer spaetere sichere Audit-Eintraege

Zulaessig:

- `activation_requested` mit Grund `prepare limited dispatch review`
- `preflight_checked` mit Evidence-Referenz auf Activation Bundle
- `gate_blocked` mit Grund `foreign staged files detected`
- `operator_approved` mit Grund `limited dispatch approved after clean worktree`
- `activation_deferred` mit Grund `tests pending`

Nicht zulaessig:

- kompletter Thread-Dump
- kompletter Prompt-Text
- Secret oder Token im Reason-Feld
- `send dispatch now` als ausgefuehrte Aktion

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Persistenz-Implementierung
- keine Runtime-Integration
- keine Thread-Sends
- keine Scheduler-Ausfuehrung
- keine Git-/Test-Runner

Er legt nur fest, wie ein spaeterer append-only Audit Trail fuer Aktivierungsentscheidungen konservativ, nachvollziehbar und operator-sicher modelliert werden soll.
