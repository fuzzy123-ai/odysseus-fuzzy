# System Health Rule Engine Alert Contract

Stand: 2026-06-17

Status: **SHC3A Docs-Contract fuer System Health Checker Rule Engine und Alert-Modell**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-basic-collectors-contract.md`
- `docs/plans/system-health-checker-plugin.md`

Dieser Contract definiert, wie uebergebene `HealthSnapshot`-, `CollectorStatus`- und bestehende `AlertSummary`-Daten spaeter in Alerts, Wiederholungsunterdrueckung und Recovery-Ereignisse uebersetzt werden. Der Slice fuehrt bewusst keine Host-Kommandos, keine Telegram-Aktionen, keine Netzwerkausfuehrung und keine Auto-Reparatur aus.

## Ziel

Odysseus braucht eine kleine, konservative Rule Engine fuer den System Health Checker.

Diese Rule Engine soll:

- auffaellige Snapshot-Zustaende bewerten
- Severity konservativ halten
- Alert-Spam durch Cooldown und Dedupe vermeiden
- Recovery oder Cleared-Zustaende sichtbar machen

## Leitregel

Die Rule Engine bewertet uebergebene Snapshots, aber fuehrt nichts aus.

Das bedeutet:

- keine Host-Kommandos
- keine Telegram-Sends
- keine Auto-Reparatur
- keine neuen Metriken einsammeln

## Rule Inputs

Die Rule Engine soll mindestens mit diesen Inputs arbeiten:

- `HealthSnapshot`
- `CollectorStatus`
- bestehende `AlertSummary` oder bekannte Alert-Lage aus frueherer Bewertung

## Bedeutung der Inputs

### `HealthSnapshot`

Das Gesamtartefakt, aus dem Collector-Zustaende und Gesamtlage gelesen werden.

### `CollectorStatus`

Die konkrete Quelle fuer CPU-, Memory-, Disk-, Load-, Uptime-, Updates-, Temperature-, SMART- oder Container-Signale.

### bestehende `AlertSummary`

Vorherige oder aktuell bekannte Alert-Zustaende dienen spaeter fuer:

- Dedupe
- Cooldown
- Recovery

## Rule Outputs

Die Rule Engine soll als Ergebnis mindestens liefern:

- `AlertSummary`
- Recovery- oder Cleared-Semantik

## `AlertSummary` als Output

Die Rule Engine nutzt das bestehende `AlertSummary`-Format aus SHC1 weiter.

Mindestens relevant:

- `severity`
- `title`
- `cause`
- `next_action`
- `dedupe_key`
- `cooldown_hint`

## Recovery- und Cleared-Semantik

Zusatz zur eigentlichen Alert-Erzeugung:

- ein vorher aktiver Alert kann spaeter als `recovered` oder `cleared` markiert werden
- ein Recovery-Ereignis soll verstaendlich und konservativ sein

Wichtig:

- Recovery ist nur eine Statusaenderung
- Recovery bedeutet keine automatische Reparatur

## Severity-Sprache

Die Severity muss konservativ mit den bestehenden Snapshot-Zustaenden umgehen.

Relevante Werte bleiben:

- `ok`
- `warn`
- `critical`
- `unknown`
- `unsupported`

## Bedeutung der Severity-Sprache

### `ok`

Der bewertete Zustand liefert keinen aktuellen Alert.

### `warn`

Auffaellige, aber nicht kritisch eskalierte Lage.

### `critical`

Kritische Lage mit dringender Handlungsempfehlung.

### `unknown`

Keine verlaessliche Aussage moeglich.

Wichtig:

- `unknown` darf nicht zu still `ok` heruntergerechnet werden

### `unsupported`

Diese Signalquelle ist bewusst nicht verfuegbar.

Wichtig:

- `unsupported` ist kein Fehlerbericht ueber einen kaputten Collector

## Threshold-Semantik

Thresholds sind in diesem Slice nur Regeldefinitionen, keine Ausfuehrung.

Die Rule Engine darf spaeter Schwellen ausdruecken wie:

- hohe CPU-Auslastung
- wenig freier RAM
- hohe Disk-Belegung
- auffaellige Load-Werte

Wichtig:

- keine Produktionsschwellen als unumstoessliche Wahrheit behaupten
- Thresholds bleiben konfigurierte oder definierte Regelwerte, nicht Host-Ausfuehrung

## Cooldown- und Dedupe-Semantik

Alert-Spam muss spaeter vermieden werden.

Dafuer braucht das Modell mindestens:

- `dedupe_key`
- `cooldown_hint`
- `repeat_suppression`
- `recovery event`

## `dedupe_key`

Der `dedupe_key` identifiziert denselben Alert-Typ fuer denselben betroffenen Bereich stabil wieder.

Beispiele:

- `disk:/`
- `memory:available`
- `health_agent:offline`

## `cooldown_hint`

`cooldown_hint` ist die menschen- oder maschinenlesbare Angabe, dass derselbe Alert nicht bei jedem Snapshot neu eskalieren soll.

Wichtig:

- nur Modell-/Policy-Hinweis
- keine echte Timer- oder Sendelogik in diesem Slice

## `repeat_suppression`

Die Rule Engine soll spaeter unterdruecken koennen, dass identische aktive Alerts dauernd neu als frisch gemeldet werden.

Das bedeutet:

- gleicher `dedupe_key` plus aktive Lage -> nicht automatisch neuer Alert-Spam

## `recovery event`

Wenn ein vorher aktiver Alert nicht mehr zutrifft, darf spaeter ein Recovery-Ereignis erzeugt werden.

Beispiele:

- Disk-Auslastung wieder unter Warnschwelle
- Health-Agent wieder erreichbar

Wichtig:

- Recovery ist eine beobachtete Entspannung
- keine Behauptung, dass Odysseus selbst etwas repariert hat

## Copy-Regeln

Alert-Copy soll ruhig, konkret und handlungsorientiert bleiben.

Mindestens enthalten:

- Ursache
- naechste Handlung

Nicht enthalten:

- Panik-Sprache
- Reparaturbehauptung
- unsichere Ursache als Tatsache

Empfohlene Form:

- `cause: disk usage on / is above warning threshold`
- `next_action: review disk usage on host`

Nicht zulaessig:

- `system fixed`
- `panic now`
- `host repaired automatically`

## Konservative Alert-Logik

Die Kurzlogik lautet:

- `critical` nur bei klarer kritischer Lage
- `warn` bei plausibler Auffaelligkeit
- `unknown` bei unklarer oder fehlender Datenlage
- `unsupported` bleibt sichtbar und wird nicht zu Fake-Warnung uminterpretiert

## Recovery-Logik

Recovery oder Cleared soll nur dann behauptet werden, wenn:

- derselbe Alert zuvor aktiv war
- derselbe `dedupe_key` jetzt nicht mehr in warn/critical steht

Keine Recovery-Behauptung bei:

- unvollstaendigen Vergleichsdaten
- unbekanntem Zwischenzustand ohne klare Entwarnung

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf reine Modelle bauen fuer:

- `RuleDefinition`
- `RuleEvaluation`
- `AlertDecision`

Wichtig:

- keine IO
- keine Telegram-Aktionen
- keine Host-Kommandos
- keine Netzwerkausfuehrung
- Tests nur mit Mock-Snapshots

## Beispiel fuer spaetere sichere Bewertung

Zulaessig:

- `memory` mit `used_percent = 88` -> `warn`
- `disk:/` mit `used_percent = 95` -> `critical`
- `health agent offline` -> `unknown` oder `critical`, je nach spaeterer Policy
- vorher aktiver `disk:/` Alert, jetzt wieder normal -> `recovery event`

Nicht zulaessig:

- Telegram-Nachricht direkt senden
- Host bereinigen
- Schwellen ueberschreiten und jeden Snapshot als neuen frischen Alert behandeln

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Rule-Engine-Implementierung
- keine Telegram-Sendelogik
- keine Host-Agent-Ausfuehrung
- keine Auto-Reparatur
- keine UI-Implementierung

Er legt nur fest, wie Health-Snapshots spaeter konservativ in Alerts, Cooldown/Dedupe-Entscheidungen und Recovery/Cleared-Zustaende uebersetzt werden sollen.
