# System Health Advanced Debian Collectors Contract

Stand: 2026-06-17

Status: **SHC7A Docs-Contract fuer Advanced Debian Collectors des System Health Checkers**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-basic-collectors-contract.md`
- `docs/plans/system-health-checker-plugin.md`
- `docs/plans/system-health-security-ops-runbook.md`

Dieser Contract definiert die sichere Parser- und Collector-Semantik fuer die erweiterten Debian-Collectorbereiche `temperature`, `smart`, `updates` und `reboot_required`. Die spaeteren Datenquellen bleiben ausschliesslich Host-Agent-Verantwortung. Der Slice fuehrt bewusst keine Host-Kommandos, keine Root-Rechte, keine Paketinstallation und keine Runtime-Ausfuehrung aus.

## Ziel

Odysseus braucht eine konservative, robuste Semantik fuer fortgeschrittene Debian-Health-Signale.

Diese Advanced Collectors sollen spaeter:

- Temperatur-, SMART-, Update- und Reboot-Signale bereinigt modellieren
- fehlende Tools oder fehlende Rechte sauber in Datenzustaende uebersetzen
- nie als Core-Host-Ausfuehrung im Odysseus-Container landen

## Leitregel

Advanced Collectors gehoeren spaeter nur in den Host-Agenten, nicht in den Odysseus-Core.

Das bedeutet:

- keine echten `sensors`-, `smartctl`- oder apt-Aufrufe im Core
- keine Datei-Reads gegen den Host aus dem Core
- nur bereinigte Fixture-/Parser-Semantik fuer spaetere Modelle

## Collector IDs

Der Advanced-Scope soll mindestens diese Collector IDs enthalten:

- `temperature`
- `smart`
- `updates`
- `reboot_required`

## Parser- und Collector-Eingaben

Die spaeteren Parser-Eingaben duerfen in diesem Track nur als bereinigte JSON- oder Text-Fixtures gedacht werden.

Typische spaetere Host-Agent-Herkunft:

- `sensors -j`
- `smartctl -a -j`
- apt simulation oder Update-Count-Ausgabe
- `/var/run/reboot-required`

Wichtig:

- im Odysseus-Core keine echten Commands
- keine echten Datei-Reads
- nur Fixture-basierte Parser-Semantik

## Allgemeine Output-Semantik

Jeder Advanced Collector soll spaeter als `CollectorStatus` mit mindestens folgenden Teilen in den Snapshot eingehen:

- `status`
- `observed_value`
- `setup_hint` optional

Wichtig:

- keine rohen CLI-Ausgaben
- keine Trace- oder stderr-Dumps
- keine Secrets

## Missing Dependency Semantik

Wenn eine benoetigte Host-Abhaengigkeit fehlt, gilt:

- `unsupported`
- `setup_hint`
- kein Crash

Beispiele:

- `lm-sensors` fehlt
- `smartmontools` fehlt
- apt-spezifische Quelle ist nicht verfuegbar

## Permission Denied Semantik

Wenn Daten prinzipiell da sein koennten, aber Rechte fehlen, gilt:

- `unknown`
- `setup_hint`
- kein Crash

Wichtig:

- fehlende Rechte sind kein `ok`
- fehlende Rechte sind nicht automatisch `unsupported`

## Temperature Collector

Collector ID:

- `temperature`

Ziel:

- kompakte Darstellung relevanter Temperatur-Signale

Spaetere Fixture-Herkunft:

- JSON-Struktur wie aus `sensors -j`
- alternativ bereinigte Fallback-Daten aus einem Host-Agent

Beispiel fuer `observed_value`:

- `{ "max_celsius": 67.0, "sensor_count": 3 }`

Beispiel fuer `setup_hint`:

- `install lm-sensors and verify sensor visibility in host agent`

Fehlersprache:

- fehlendes `lm-sensors` -> `unsupported`
- fehlende Rechte oder unlesbare Daten -> `unknown`

## SMART Collector

Collector ID:

- `smart`

Ziel:

- kompakte Darstellung von Laufwerksgesundheit oder klarer Nichtverfuegbarkeit

Spaetere Fixture-Herkunft:

- JSON-Struktur wie aus `smartctl -a -j`

Beispiel fuer `observed_value`:

- `{ "device_count": 2, "failing_count": 0, "nvme_warnings": 0 }`

Beispiel fuer `setup_hint`:

- `install smartmontools and verify minimal rights for smartctl in host agent`

Fehlersprache:

- fehlendes `smartctl` -> `unsupported`
- fehlende Rechte -> `unknown`

## Updates Collector

Collector ID:

- `updates`

Ziel:

- kompakte Sicht auf ausstehende Paketupdates ohne echte Paketaktion im Core

Spaetere Fixture-Herkunft:

- bereinigte apt simulation
- Update-Count aus einem Host-Agent

Beispiel fuer `observed_value`:

- `{ "pending_count": 14, "security_count": 2 }`

Beispiel fuer `setup_hint`:

- `verify host agent update simulation source is configured`

Fehlersprache:

- fehlende apt-spezifische Quelle -> `unsupported` oder `unknown`, je nach spaeterer Policy
- Parserfehler oder unlesbare Daten -> `unknown`

## Reboot Required Collector

Collector ID:

- `reboot_required`

Ziel:

- kompakte Ja/Nein-Sicht darauf, ob ein Reboot-Hinweis vorliegt

Spaetere Fixture-Herkunft:

- bereinigter Text-/Bool-Wert wie aus `/var/run/reboot-required`

Beispiel fuer `observed_value`:

- `{ "required": true }`

Beispiel fuer `setup_hint`:

- `verify host agent can inspect reboot-required marker`

Fehlersprache:

- fehlende Sicht auf die Quelle -> `unknown`
- nicht anwendbare Umgebung -> `unsupported`

## JSON/Text-Fixtures statt echter Commands

In diesem Slice gilt als harte Grenze:

- Parser Inputs sind JSON- oder Text-Fixtures
- keine echten `sensors`, `smartctl`, apt- oder Dateisystem-Aufrufe im Core

Das bedeutet:

- Parser-Design ja
- Host-Ausfuehrung nein

## Unknown- und Unsupported-Regel

Die konservative Grundregel lautet:

- fehlende Dependency -> `unsupported` plus `setup_hint`
- fehlende Rechte -> `unknown` plus `setup_hint`
- unvollstaendige oder unlesbare Daten -> `unknown`
- nie Crash

## Aggregation in `CollectorStatus`

Jeder Advanced Collector soll spaeter nur als bereinigter `CollectorStatus` oder aequivalente Reading-Struktur in den Snapshot eingehen.

Das bedeutet:

- keine eigene parallele Wahrheitsquelle
- keine direkte UI-Sonderlogik anstelle des Collector-Zustands

## Sicherheits- und Robustheitsregeln

Diese Advanced Collectors duerfen spaeter:

- keine Root-Rechte im Core verlangen
- keine Host-Kommandos im Core ausfuehren
- keine Pakete installieren
- keine rohen CLI-Outputs in Responses durchreichen

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur fixture-only Parser-/Modell-Arbeit bauen fuer:

- Dataclasses
- Parser-Funktionen
- Builder

Wichtig:

- keine subprocess-Aufrufe
- keine IO
- keine Host-Kommandos
- keine Datei-Reads ausser in Tests mit Fixture-Literalen

## Beispiel fuer spaetere sichere Ausgaben

Zulaessig:

- `temperature.status = ok`, `observed_value = { "max_celsius": 67.0 }`
- `smart.status = unsupported`, `setup_hint = "install smartmontools on host agent"`
- `updates.status = warn`, `observed_value = { "pending_count": 14 }`
- `reboot_required.status = ok`, `observed_value = { "required": true }`

Nicht zulaessig:

- `run smartctl now`
- `apt-get upgrade`
- `cat /var/run/reboot-required` im Core
- roher stderr-Dump als Response

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Host-Agent-Implementierung
- keine Paketinstallation
- keine Root- oder sudo-Strategie als laufende Ausfuehrung
- keine UI-Implementierung
- keine Rule-Engine-Implementierung

Er legt nur fest, wie spaetere Advanced Debian Collectors konservativ, fixture-basiert und Core-entkoppelt in `CollectorStatus`-Signale fuer Temperatur, SMART, Updates und Reboot-Required uebersetzt werden sollen.
