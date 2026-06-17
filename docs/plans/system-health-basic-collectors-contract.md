# System Health Basic Collectors Contract

Stand: 2026-06-17

Status: **SHC2A Docs-Contract fuer System Health Checker Basic Debian Collectors**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-checker-plugin.md`
- `docs/plans/system-health-rule-engine-alert-contract.md`
- `docs/plans/system-health-advanced-debian-collectors-contract.md`

Dieser Contract definiert die sichere Snapshot- und Collector-Semantik fuer die Basic Debian Collectors des System Health Checkers. Der Slice beschreibt nur, welche Daten spaeter fuer CPU, RAM, Load, Uptime und Disk Space erwartet werden und wie Unknown-/Unsupported-Zustaende sichtbar bleiben. Es werden keine Host-Kommandos ausgefuehrt und keine Runtime- oder Agent-Dateien angefasst.

## Ziel

Odysseus braucht eine kleine, robuste Grundmenge an Debian-Basis-Collectoren, die Homeserver-Zustand spaeter in bereinigte Snapshots uebersetzen koennen.

Diese Basic Collectors sollen:

- die ersten wichtigsten Host-Signale stabil liefern
- Unknown- und Unsupported-Zustaende konservativ behandeln
- Setup-Hinweise sichtbar machen, ohne den Core an Host-Reads zu koppeln

## Leitregel

Basic Collectors gehoeren spaeter nur in den Host-Agenten, nicht in den Odysseus-Core.

Das bedeutet:

- Odysseus-Core fuehrt keine `/proc`-Reads aus
- Odysseus-Core fuehrt keine `df`-, `lsblk`- oder `psutil`-Abfragen aus
- Collector-Daten kommen nur aus einem Host-Agent-Snapshot

## Collector IDs

Der Basic-Scope soll mindestens diese Collector IDs enthalten:

- `cpu`
- `memory`
- `load`
- `uptime`
- `disk`

## Input-Quellen

Die spaeteren Datenquellen sind ausschliesslich Verantwortung des Host-Agenten.

Zulaessige Host-Agent-Quellen spaeter:

- `/proc`
- `psutil`
- `df`
- `lsblk`

Wichtig:

- diese Quellen duerfen nicht direkt aus dem Odysseus-Core gelesen werden
- sie sind nur konzeptionelle Herkunft fuer spaetere Host-Agent-Collector

## Basic Collector Reading

Fuer jeden Basic Collector soll spaeter mindestens ein kleines Reading-Objekt oder eine Reading-Struktur denkbar sein, die in `CollectorStatus` oder `HealthSnapshot` aggregiert werden kann.

Empfohlene Grundfelder fuer ein spaeteres `BasicCollectorReading`:

- `collector_id`
- `status`
- `observed_value`
- `unit` optional
- `setup_hint` optional
- `source_hint` optional

## `observed_value`

`observed_value` soll der bereinigte, kompakte Messwert oder Messwertblock sein.

Wichtig:

- keine rohen Host-Dumps
- keine kompletten CLI-Ausgaben
- keine Debug- oder Trace-Logs

## `setup_hint`

`setup_hint` ist die menschenlesbare Hilfe, wenn ein Collector keine verlaesslichen Werte liefern kann oder zusaetzliche Host-Vorbereitung spaeter noetig ist.

Beispiele:

- `install python3-psutil on host agent`
- `verify disk mount visibility in host agent`
- `collector data unavailable from /proc`

## CPU Collector

Collector ID:

- `cpu`

Ziel:

- CPU-Auslastung oder vergleichbarer Lastindikator als kompakte Snapshot-Info

Erwartete spaetere Herkunft nur im Host-Agent:

- `/proc/stat`
- `psutil`

Beispiel fuer `observed_value`:

- `{ "usage_percent": 23.5 }`

Beispiel fuer `setup_hint`:

- `enable psutil fallback in host agent if /proc parsing is unavailable`

## Memory Collector

Collector ID:

- `memory`

Ziel:

- belegter, freier oder verfuegbarer RAM als kompakte Snapshot-Info

Erwartete spaetere Herkunft nur im Host-Agent:

- `/proc/meminfo`
- `psutil`

Beispiel fuer `observed_value`:

- `{ "used_percent": 61.2, "available_mb": 6240 }`

Beispiel fuer `setup_hint`:

- `verify host agent can read /proc/meminfo`

## Load Collector

Collector ID:

- `load`

Ziel:

- Systemlast in kompakter Form fuer Kurzbewertung

Erwartete spaetere Herkunft nur im Host-Agent:

- `/proc/loadavg`
- `psutil` falls sinnvoll

Beispiel fuer `observed_value`:

- `{ "load_1": 0.42, "load_5": 0.37, "load_15": 0.31 }`

Beispiel fuer `setup_hint`:

- `collector requires loadavg support from host agent environment`

## Uptime Collector

Collector ID:

- `uptime`

Ziel:

- Betriebsdauer oder letzter Boot-Kontext in kompakter Form

Erwartete spaetere Herkunft nur im Host-Agent:

- `/proc/uptime`
- `psutil`

Beispiel fuer `observed_value`:

- `{ "uptime_seconds": 864000 }`

Beispiel fuer `setup_hint`:

- `verify uptime collector can read host uptime source`

## Disk Collector

Collector ID:

- `disk`

Ziel:

- freie/belegte Kapazitaet fuer relevante Volumes oder Dateisysteme

Erwartete spaetere Herkunft nur im Host-Agent:

- `df`
- `lsblk`
- `psutil.disk_usage`

Beispiel fuer `observed_value`:

- `{ "mount": "/", "used_percent": 71.8, "free_gb": 132.4 }`

Beispiel fuer `setup_hint`:

- `verify host agent disk visibility for target mountpoints`

## Unknown- und Unsupported-Semantik

Basic Collectors muessen konservativ bleiben.

### `unknown`

`unknown` bedeutet:

- Collector haette prinzipiell Daten liefern sollen
- aktuell liegen aber keine verlaesslichen Daten vor

Beispiele:

- Host-Agent offline
- `/proc`-Read fehlgeschlagen
- unvollstaendige Disk-Daten

### `unsupported`

`unsupported` bedeutet:

- diese Collector-Art ist in der aktuellen Umgebung bewusst nicht verfuegbar

Bei den Basic Debian Collectors sollte `unsupported` selten sein, ist aber fuer abweichende Umgebungen trotzdem erlaubt.

## Aggregation in `HealthSnapshot`

Die Basic Collectors sollen spaeter nur in den bestehenden `HealthSnapshot` aggregiert werden.

Das bedeutet:

- keine separate Wahrheit neben `HealthSnapshot`
- keine direkte UI-Sonderlogik als Ersatz fuer Collector-Zustaende
- jeder Basic Collector liefert zuerst ein sauberes Reading oder `CollectorStatus`

## Threshold-Sprache

Schwellenwerte sind in diesem Slice nur vorlaeufige Empfehlung.

Das bedeutet:

- keine finale Rule Engine
- keine Alert-Ausloesung in diesem Slice
- keine harten globalen Grenzwerte behaupten

Zulaessige Sprache:

- `high memory usage may later map to warn or critical`
- `disk usage above common thresholds may later trigger alerts`

Nicht zulaessig:

- feste Produktions-Regel als abgeschlossen behaupten

## Beispielhafte Collector-Ausgaben

Zulaessige kompakte Beispiele:

- `cpu.status = ok`, `observed_value = { "usage_percent": 23.5 }`
- `memory.status = warn`, `observed_value = { "used_percent": 88.0, "available_mb": 1540 }`
- `load.status = unknown`, `setup_hint = "collector data unavailable from /proc/loadavg"`
- `uptime.status = ok`, `observed_value = { "uptime_seconds": 864000 }`
- `disk.status = warn`, `observed_value = { "mount": "/", "used_percent": 91.3 }`

## Sicherheits- und Robustheitsregeln

Diese Basic Collectors duerfen spaeter:

- keine Host-Kommandos aus dem Core ausfuehren
- keine rohen CLI-Ausgaben nach Odysseus durchreichen
- keine Secrets oder Tokens enthalten
- bei fehlenden Daten auf `unknown` zurueckfallen

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur kleine, reine Modelle bauen fuer:

- Dataclasses
- Builder
- Aggregation in `HealthSnapshot`

Wichtig:

- keine IO
- keine Host-Kommandos
- keine Container-Kommandos
- Tests nur mit Mock-Daten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Host-Agent-Implementierung
- keine CLI- oder `/proc`-Ausfuehrung im Core
- keine Rule Engine
- keine Alert-Logik
- keine UI-Implementierung

Er legt nur fest, wie die Basic Debian Collectors spaeter konservative, kompakte und offline-sichere Snapshot-Daten fuer CPU, RAM, Load, Uptime und Disk liefern sollen.
