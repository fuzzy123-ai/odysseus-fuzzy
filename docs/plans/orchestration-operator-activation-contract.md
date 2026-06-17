# Orchestration Operator Activation Contract

Stand: 2026-06-17

Status: **AUTO10A Docs-Contract fuer operator-kontrollierte Aktivierung der AUTO-Orchestration**

Quellen:

- `docs/plans/orchestration-runtime-readiness-contract.md`
- `docs/plans/automated-agent-handoff-orchestration-mvp.md`
- `docs/plans/orchestration-activation-readiness-summary-contract.md`

Dieser Contract beschreibt, wie Charlie oder ein Operator spaeter echte AUTO-Orchestration stufenweise freigeben darf, ohne versehentlich Live-Aktionen auszufuehren. Der Slice bleibt bewusst docs-only und fuehrt keine Runtime-, Thread-, Git-, Test-, Scheduler-, Provider- oder Netzwerkaktionen aus.

## Ziel

Odysseus braucht vor echter Live-Orchestration einen klaren operator-kontrollierten Aktivierungsplan.

Dieser Plan soll festlegen:

- welche Aktivierungsstufe gerade gilt
- welche Aktionen auf dieser Stufe erlaubt oder gesperrt sind
- welche Gates vor einer hoeheren Stufe gruen sein muessen
- wann sofort gestoppt oder auf eine sichere Stufe zurueckgeschaltet werden muss

## Leitregel

Aktivierung ist eine bewusste Freigabe, keine implizite Nebenwirkung.

Das bedeutet:

- dry-run Readiness allein startet keine Live-Orchestration
- hoehere Stufen duerfen nur durch Charlie oder Operator freigegeben werden
- unklare Lage fuehrt zu `read_only` oder `disabled`, nicht zu stiller Eskalation

## Aktivierungsstufen

Die spaetere AUTO-Orchestration soll mindestens diese Aktivierungsstufen kennen:

- `disabled`
- `read_only`
- `prepare_dispatch`
- `dispatch_requires_confirm`
- `live_dispatch_limited`

## Bedeutung der Aktivierungsstufen

### `disabled`

AUTO-Orchestration ist operativ abgeschaltet.

Erlaubt:

- Dokumentation lesen
- Readiness-Berichte lesen
- bekannte Gaps anzeigen

Nicht erlaubt:

- Dispatch vorbereiten
- Threads lesen oder senden
- Git-/Test-Aktionen anstossen

### `read_only`

Die Runtime darf nur Zustand lesen oder bereits vorhandene Snapshots auswerten.

Erlaubt:

- Registry- und Dashboard-Status lesen
- Readiness-Gaps zusammenfassen
- naechste sichere Aktion als Empfehlung ausgeben

Nicht erlaubt:

- echte Dispatches vorbereiten
- echte Thread-Sends
- Git- oder Test-Kommandos

### `prepare_dispatch`

Die Runtime darf einen moeglichen Dispatch vorbereiten, aber noch nichts live ausloesen.

Erlaubt:

- moegliche Ziel-Threads bestimmen
- Handoffs und Pflichtfelder validieren
- erlaubte Aktionen als Plan anzeigen

Nicht erlaubt:

- Live-Send
- Scheduler-Start
- echte Git-/Test-Ausfuehrung

### `dispatch_requires_confirm`

Die Runtime darf einen konkreten Dispatch-Vorschlag erzeugen, aber ein Mensch muss jede Live-Aktion bestaetigen.

Erlaubt:

- konkreten Dispatch-Plan anzeigen
- Ziel-Thread, Slice und Scope sichtbar machen
- Stop-Gates vor dem Send noch einmal pruefen

Nicht erlaubt:

- stiller Send ohne Bestaetigung
- automatischer Retry ohne erneute Freigabe

### `live_dispatch_limited`

Die Runtime darf begrenzte Live-Dispatches ausfuehren, aber nur innerhalb klarer Operator-Grenzen.

Erlaubt:

- freigegebene Live-Sends innerhalb des bestaetigten Scopes
- sichere Rueckkehr zu niedrigerer Stufe
- begrenzte Heartbeat-Fortsetzung innerhalb derselben Freigabe

Nicht erlaubt:

- unbegrenzte Agenten-Orchestration
- destruktive Git-Aktionen
- stilles Ueberspringen roter Tests
- Dispatch in unklare Threads oder Hotfiles

## Freigabe-Gates

Vor einer hoeheren Aktivierungsstufe muessen mindestens diese Gates klar bewertet sein:

- eindeutige Thread Registry
- sauberer Worktree
- gruene Tests
- keine fremden staged files
- klare Hotfile-Locks
- Nutzer-/Operator-Freigabe fuer Live-Sends

## Bedeutung der Freigabe-Gates

### Eindeutige Thread Registry

Es muss klar sein:

- welcher Thread zu welchem AgentRun und Slice gehoert
- welcher Thread aktiv, idle, blocked oder ambiguous ist

Ohne diese Eindeutigkeit bleibt die Aktivierung auf:

- `read_only`

### Sauberer Worktree

Vor Live-Dispatch muss klar sein:

- keine unerklaerten lokalen Aenderungen im kritischen Scope
- keine unklaren Konflikte mit parallel laufenden Slices

### Gruene Tests

Rote Tests duerfen nicht ignoriert werden, wenn der naechste Schritt reale Orchestration betrifft.

Wenn Tests unklar oder rot sind:

- keine hoehere Aktivierungsstufe

### Keine fremden staged files

Live-Aktivierung darf nie auf einer Git-Lage aufbauen, in der fremde staged Dateien auf Mitnahme warten.

Wenn fremde staged Dateien sichtbar sind:

- Stop
- keine Dispatch-Freigabe

### Klare Hotfile-Locks

Hotfiles muessen Charlie-seitig eindeutig gesperrt oder freigegeben sein.

Wenn ein Slice in denselben Hotfiles arbeitet:

- keine Live-Dispatch-Eskalation

### Nutzer-/Operator-Freigabe fuer Live-Sends

Echte Sends duerfen nur stattfinden, wenn die Aktivierung sichtbar und bewusst freigegeben wurde.

Ohne diese Freigabe bleibt der Zustand:

- `dispatch_requires_confirm`

## Erlaubte Aktionen pro Stufe

Die spaetere Runtime oder ein Aktivierungsplan soll Aktionen mindestens in diese Gruppen einteilen:

- `read_status`
- `assess_gaps`
- `prepare_dispatch_plan`
- `request_confirmation`
- `send_live_dispatch`
- `downgrade_activation`
- `disable_runtime`

Die sichere Grundlogik lautet:

- `disabled`: nur `read_status`, `assess_gaps`
- `read_only`: nur `read_status`, `assess_gaps`
- `prepare_dispatch`: zusaetzlich `prepare_dispatch_plan`
- `dispatch_requires_confirm`: zusaetzlich `request_confirmation`
- `live_dispatch_limited`: erst dann `send_live_dispatch`, aber nur innerhalb bestaetigter Grenzen

## Stop-Regeln

Die Aktivierung muss sofort stoppen oder heruntergestuft werden bei:

- `ambiguous thread`
- fehlendem Handoff-Feld
- rotem Test
- Git-Konflikt
- Hotfile-Overlap
- unbekanntem Scope

Zusatzregeln:

- Stop bei fremden staged files
- Stop bei unklarer Operator-Freigabe
- Stop bei Scheduler-/Dispatch-Folge, die ausserhalb des bestaetigten Scopes liegt

## Rollback und Deaktivierung

Rueckkehr auf eine niedrigere Stufe muss immer moeglich sein.

Mindestens vorgesehen:

- Rueckkehr von `live_dispatch_limited` zu `dispatch_requires_confirm`
- Rueckkehr von `dispatch_requires_confirm` zu `prepare_dispatch`
- Rueckkehr auf `read_only`
- vollstaendiges Abschalten auf `disabled`

Wichtig:

- keine destruktiven Git-Aktionen
- kein automatisches Aufraeumen durch harte Resets
- keine stillen Folge-Dispatches nach Deaktivierung

## Sichere Gesamtlogik

Die Kurzlogik fuer spaetere Operator-Freigabe lautet:

- wenn Readiness nicht mindestens nachvollziehbar ist -> `disabled` oder `read_only`
- wenn Dispatch technisch vorbereitet, aber nicht menschlich freigegeben ist -> `prepare_dispatch` oder `dispatch_requires_confirm`
- wenn Live-Send erlaubt ist, aber nur eng begrenzt -> `live_dispatch_limited`
- wenn irgendein kritischer Stop aktiv wird -> sofort auf `read_only` oder `disabled`

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf ein kleines Modell bauen, das aus:

- Runtime-Readiness-Report
- Operator-Policy
- Gate-Status

einen `ActivationPlan` ableitet.

Dieser `ActivationPlan` soll nur sichtbar machen:

- aktuelle Aktivierungsstufe
- erlaubte Aktionen
- gesperrte Aktionen
- Gruende fuer Stop, Freigabe oder Herabstufung
- naechste sichere Aktion

Wichtig:

- keine Actions ausfuehren
- keine Threads senden
- keine Git-Kommandos ausfuehren
- keine Tests ausfuehren
- keine echte Aktivierung selbst entscheiden

## Beispiel fuer spaetere sichere Ausgabe

Zulaessige Ausgabe:

- `activation_stage: read_only`
- `allowed_actions: [read_status, assess_gaps]`
- `blocked_actions: [send_live_dispatch]`
- `next_safe_action: require operator confirmation for dispatch`

Nicht zulaessig:

- Live-Send ausloesen
- Testlauf starten
- Worktree bereinigen
- Hotfile-Konflikte selbst aufloesen

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine echte Runtime-Aktivierung
- keine Thread- oder Scheduler-Implementierung
- keine Git- oder Test-Hooks
- keine Provider-/RAG-/Export-/Import-Aktivierung
- keine destruktiven Rollback-Kommandos

Er legt nur fest, wie spaetere AUTO-Orchestration stufenweise, operator-kontrolliert und sicher freigegeben oder wieder deaktiviert werden darf.
