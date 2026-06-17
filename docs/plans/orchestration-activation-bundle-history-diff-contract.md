# Orchestration Activation Bundle History Diff Contract

Stand: 2026-06-17

Status: **AUTO15A Docs-Contract fuer History/Diff von AUTO Activation Bundles**

Quellen:

- `docs/plans/orchestration-activation-bundle-contract.md`
- `docs/plans/orchestration-activation-bundle-digest-contract.md`
- `docs/plans/orchestration-activation-readiness-summary-contract.md`

Dieser Contract definiert, wie zwei AUTO Activation Bundle Snapshots sicher verglichen werden. History und Diff dienen nur Orientierung, Change Detection und Handoff-Evidence. Sie fuehren keine Aktivierung aus und erzeugen keine neue operative Wahrheit.

## Ziel

Odysseus braucht einen kleinen, konservativen Vergleich zwischen einem vorherigen und einem aktuellen Bundle-Snapshot.

Der Vergleich soll beantworten:

- hat sich inhaltlich etwas geaendert
- hat sich der Status sichtbar geaendert
- welche Blocker sind neu oder entfallen
- hat sich die naechste sichere Aktion veraendert

## Leitregel

Diff beschreibt Unterschiede, nicht Fortschritt per Behauptung.

Das bedeutet:

- Diff darf keine Aktivierung ausloesen
- Diff darf keine fehlenden Snapshots erraten
- Diff darf keine Verbesserung behaupten, wenn nur Metadaten oder unklare Signale vorliegen

## Eingaben

Ein spaeteres Diff-Modell soll mindestens diese Eingaben kennen:

- `previous_bundle_digest`
- `current_bundle_digest`
- `previous_summary`
- `current_summary`
- `previous_status`
- `current_status`
- `previous_label` optional
- `current_label` optional
- `previous_generated_at` optional
- `current_generated_at` optional

## Bedeutung der Eingaben

### `previous_bundle_digest`

Stabiler Digest des vorherigen Bundle-Zustands.

### `current_bundle_digest`

Stabiler Digest des aktuellen Bundle-Zustands.

### `previous_summary`

Vorherige kompakte Activation Readiness Summary.

### `current_summary`

Aktuelle kompakte Activation Readiness Summary.

### `previous_status`

Vorherige Statusverdichtung, zum Beispiel aus Summary oder Bundle-Lens.

### `current_status`

Aktuelle Statusverdichtung, zum Beispiel aus Summary oder Bundle-Lens.

### Optionale Labels und Zeitstempel

`previous_label`, `current_label`, `previous_generated_at`, `current_generated_at` sind nur Metadaten.

Wichtig:

- sie duerfen Kontext geben
- sie ersetzen keine Inhaltsaussage

## Output-Felder

Ein spaeteres Diff-Ergebnis soll mindestens diese Felder enthalten:

- `changed`
- `digest_changed`
- `status_changed`
- `new_blockers`
- `resolved_blockers`
- `next_safe_action_changed`
- `notes`

## Bedeutung der Output-Felder

### `changed`

Gesamtindikator, ob ein relevanter Unterschied festgestellt wurde.

### `digest_changed`

Zeigt, ob sich der stabile Bundle-Digest geaendert hat.

Wenn `false`, soll das Diff konservativ bleiben:

- wahrscheinlich kein inhaltlicher Bundle-Unterschied

### `status_changed`

Zeigt, ob sich der sichtbare Status zwischen vorher und aktuell geaendert hat.

Beispiele:

- `read_only` -> `confirm_required`
- `blocked` -> `read_only`

### `new_blockers`

Blockierende Gruende, die aktuell neu hinzugekommen sind.

### `resolved_blockers`

Blockierende Gruende, die im aktuellen Zustand nicht mehr vorhanden sind.

### `next_safe_action_changed`

Boolescher Indikator, ob sich die eine naechste sichere Aktion geaendert hat.

### `notes`

Kurze, konservative Einordnung fuer Menschen oder spaetere UI.

Beispiele:

- `digest unchanged`
- `status changed from read_only to confirm_required`
- `new blocker: operator_confirmation_missing`

## Vergleichsregel

Das Diff-Modell vergleicht nur uebergebene Daten.

Das bedeutet:

- keine echten Snapshots aus Dateien lesen
- keine History-Dateien oeffnen
- keine Bundle-Artefakte selbst nachladen
- keine externen Hooks, keine IO

## Sichere Vergleichslogik

Die konservative Kurzlogik lautet:

- wenn `previous_bundle_digest` und `current_bundle_digest` gleich sind -> `digest_changed = false`
- wenn Digests unterschiedlich sind -> `digest_changed = true`
- wenn sichtbare Statuswerte unterschiedlich sind -> `status_changed = true`
- neue Blocker entstehen nur aus konkretem Unterschied zwischen vorherigen und aktuellen Blocking-Signalen
- geloeste Blocker entstehen nur aus konkretem Wegfall vorheriger Blocking-Signale

## Umgang mit unbekannt

Wenn Eingaben fehlen oder unklar sind, bleibt das Ergebnis konservativ.

Das bedeutet:

- unbekannt bleibt `unknown` in der Copy oder den Notizen
- kein behaupteter Fortschritt
- keine implizite Verbesserung

## Copy-Regeln

Die Diff-Copy muss vorsichtig bleiben.

Mindestens diese Regeln gelten:

- kein `improved` behaupten ohne konkret `resolved_blockers`
- kein `better` oder `fixed` behaupten, wenn nur Digest oder Zeitstempel anders sind
- unbekannt bleibt `unknown`
- reine Metadatenbewegung ist keine Fortschrittsaussage

Empfohlene Sprache:

- `unchanged`
- `status changed`
- `new blockers detected`
- `resolved blockers detected`
- `unknown due to incomplete comparison input`

## Pseudo-Evidence vermeiden

History/Diff darf keine neue Evidence erfinden.

Nicht behaupten:

- Bundle sei verifiziert
- Aktivierung sei naeher, wenn dafuer keine konkreten resolved blockers vorliegen
- Operator-Freigabe sei erfolgt, wenn sie nur indirekt vermutet wird

## Use Cases

Das Diff soll mindestens diese sicheren Use Cases unterstuetzen:

- Handoff-Vergleich
- Snapshot-History
- UI-Change-Lens
- No-change Detection

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf einen kleinen Dataclass- oder Builder-Typ fuer `ActivationBundleDiff` bauen.

Wichtig:

- keine IO
- keine Thread-Hooks
- keine Git-Hooks
- keine Test-Hooks
- keine Scheduler-Hooks

Der Builder soll nur:

- uebergebene Digests vergleichen
- uebergebene Status-/Summary-Daten verdichten
- konservative Diff-Felder ausgeben

## Beispiel fuer spaeteren sicheren Diff

Zulaessig:

- `changed: true`
- `digest_changed: true`
- `status_changed: true`
- `new_blockers: [operator_confirmation_missing]`
- `resolved_blockers: []`
- `next_safe_action_changed: true`
- `notes: status changed from read_only to confirm_required`

Nicht zulaessig:

- `improved: true` ohne konkrete resolved blockers
- Dateisystem lesen, um fehlende Vergleichsdaten zu erraten
- Live-Status aus Runtime-Hooks nachladen

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Persistenz- oder History-Engine
- keine Datei- oder Datenbankzugriffe
- keine Live-Aktivierung
- keine UI- oder API-Implementierung

Er legt nur fest, wie zwei AUTO Activation Bundle Snapshots spaeter konservativ, deterministisch und no-go-sicher verglichen werden sollen.
