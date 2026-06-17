# Orchestration Activation Bundle Contract

Stand: 2026-06-17

Status: **AUTO13A Docs-Contract fuer AUTO Activation Bundle / Entrypoint**

Quellen:

- `docs/plans/orchestration-runtime-readiness-contract.md`
- `docs/plans/orchestration-operator-activation-contract.md`
- `docs/plans/orchestration-activation-readiness-summary-contract.md`
- `docs/plans/orchestration-activation-summary-renderer-contract.md`
- `docs/plans/orchestration-activation-bundle-digest-contract.md`
- `docs/plans/orchestration-activation-audit-trail-contract.md`

Dieser Contract definiert einen stabilen Einstiegspunkt fuer Operator, Automation und spaetere UI: ein Current Activation Bundle, das mehrere bereits vorbereitete AUTO-Artefakte zu einem read-only Snapshot zusammenfasst. Das Bundle fuehrt nichts aus, aktiviert nichts und darf nicht als Dispatch-Mechanik missverstanden werden.

## Ziel

Odysseus braucht einen einzigen, klaren Einstiegspunkt fuer den aktuellen AUTO-Aktivierungszustand.

Dieser Einstiegspunkt soll:

- Readiness Report, Activation Plan und Summary gebuendelt sichtbar machen
- die vorhandenen JSON- und Markdown-Snapshots in ein gemeinsames Artefakt setzen
- fuer Operator, Handoff und spaetere UI denselben Status liefern

## Leitregel

Bundle ist Statusartefakt, nicht Aktivierung.

Das bedeutet:

- kein Live-Dispatch
- keine Aktivierung
- keine Thread-, Git-, Test- oder Scheduler-Hooks
- keine verdeckte Seiteneffekte beim Bauen oder Lesen des Bundles

## Bundle-Felder

Ein spaeteres Current Activation Bundle soll mindestens diese Felder enthalten:

- `readiness_report`
- `activation_plan`
- `summary`
- `json_snapshot`
- `markdown_snapshot`
- `generated_at` optional
- `label` optional

## Bedeutung der Bundle-Felder

### `readiness_report`

Enthaelt den aktuellen Runtime-Readiness-Blick.

Zweck:

- aktuelle Gaps und Statusarten sichtbar machen
- technische Vorbereitungsgrenze zwischen dry-run und live konservativ abbilden

### `activation_plan`

Enthaelt den operator-kontrollierten Aktivierungsplan mit erlaubten und gesperrten Aktionen.

Zweck:

- aktuelle Aktivierungsstufe sichtbar machen
- Operator-Gates und Stop-Regeln mitfuehren

### `summary`

Enthaelt die kompakte Activation Readiness Summary.

Zweck:

- schnelle Lesbarkeit fuer Operator, Charlie und spaetere UI
- eine einzige `next_safe_action`
- konservatives `status_label`

### `json_snapshot`

Enthaelt die deterministische JSON-Darstellung der Summary.

Zweck:

- Snapshot-Tests
- spaetere UI- oder Automationsaufnahme
- diff-freundliche Weiterverarbeitung

### `markdown_snapshot`

Enthaelt die lesbare Markdown-Darstellung der Summary.

Zweck:

- Handoff
- Morgenstatus
- Operator-Notizen und Artefakte

### `generated_at` optional

Optionaler Zeitstempel, wann das Bundle erzeugt wurde.

Wichtig:

- nur Metadatum
- kein Live-Signal

### `label` optional

Optionaler Name fuer den aktuellen Snapshot.

Beispiele:

- `current`
- `morning-check`
- `handoff-preview`

## No-Go-Regel

Das Bundle ist immer nur ein Statusartefakt.

Es ist ausdruecklich:

- keine Live-Aktivierung
- kein Dispatch
- kein Scheduler-Start
- kein Git- oder Test-Runner

Auch wenn ein Bundle `live_limited_ready`-Informationen enthaelt, bleibt das Bundle selbst read-only.

## Empfohlene Gesamtlogik

Ein Bundle soll die vorhandenen AUTO-Bausteine nur zusammenfassen, nicht neu bewerten.

Die Kurzlogik lautet:

- `readiness_report` liefert Vorbereitungs- und Gap-Sicht
- `activation_plan` liefert erlaubte und gesperrte Aktionen
- `summary` liefert den kompakten Gesamteindruck
- `json_snapshot` und `markdown_snapshot` liefern stabile Renderformen fuer dieselbe Lage

## Use Cases

Das Bundle soll mindestens diese sicheren Use Cases unterstuetzen:

- Morgenstatus
- Dashboard-Snapshot
- Handoff
- spaeter Diff/History

## Bedeutung der Use Cases

### Morgenstatus

Charlie oder Operator sieht auf einen Blick:

- welche Gaps offen sind
- ob Operator-Freigabe fehlt
- was die naechste sichere Aktion ist

### Dashboard-Snapshot

Spaetere UI oder interne Statusseiten koennen einen kompakten Status lesen, ohne eigene Logik neu zu erfinden.

### Handoff

Ein Handoff kann dasselbe Bundle referenzieren, statt mehrere Einzeldokumente auseinanderzuziehen.

### Diff/History spaeter

Die Bundle-Form eignet sich spaeter fuer Verlauf oder Vergleich, ohne dass dafuer jetzt echte History-Mechanik gebaut werden muss.

## Stabilitaetsregeln

Das Bundle soll:

- dieselben Kernfelder bei gleicher Eingabe konsistent halten
- nur read-only Daten enthalten
- keine Secrets oder Logs mittransportieren
- keine kompletten Thread- oder Runtime-Dumps aufnehmen

Nicht enthalten:

- Live-Thread-Inhalte
- Scheduler-Trace
- Git-Details mit Seiteneffekt
- Test-Runner-Ausgaben als rohe Logs

## Operator- und UI-Sicht

Operator und spaetere UI sollen aus dem Bundle sofort lesen koennen:

- aktueller Aktivierungszustand
- konservativer Status
- erlaubte Aktionen
- klare Blocker
- naechste sichere Aktion

Sie sollen aus dem Bundle nicht direkt tun koennen:

- Aktivierung ausloesen
- Dispatch senden
- Gates umgehen

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf einen kleinen Builder fuer das aktuelle Default-Bundle bauen.

Mindestens erlaubt:

- vorhandene Modelle zu einem `current`-Bundle zusammenfassen
- `to_dict()` fuer das Bundle bereitstellen

Wichtig:

- ohne IO
- ohne Thread-Hooks
- ohne Git-Hooks
- ohne Test-Hooks
- ohne Scheduler-Hooks

## Beispiel fuer spaeteren sicheren Inhalt

Zulaessige Struktur:

- `readiness_report`
- `activation_plan`
- `summary`
- `json_snapshot`
- `markdown_snapshot`
- `generated_at`
- `label`

Nicht zulaessig:

- `dispatch_now`
- `run_scheduler`
- `execute_tests`
- `send_thread_message`

## Nicht-Ziele

Dieser Contract definiert bewusst nicht:

- keine Live-Orchestration
- keinen API- oder Dashboard-Code
- keine Persistenz- oder History-Implementierung
- keine IO-Operationen
- keine Thread-, Git-, Test- oder Scheduler-Ausfuehrung

Er legt nur fest, wie vorhandene AUTO-Aktivierungsartefakte spaeter in einem stabilen, read-only Current Activation Bundle gebuendelt werden sollen.
