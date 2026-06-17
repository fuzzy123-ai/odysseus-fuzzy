# System Health Agent Interface Contract

Stand: 2026-06-17

Status: **SHC1A Docs-Contract fuer das System Health Checker Plugin / Health-Agent Interface**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/system-health-checker-plugin.md`
- `docs/plans/system-health-checker-ops-runbook.md`
- `docs/plans/system-health-basic-collectors-contract.md`

Dieser Contract definiert das stabile Snapshot- und API-Interface zwischen Odysseus und einem kleinen Debian Host-Agent fuer System Health. Der Host-Agent sammelt Metriken auf dem Host und liefert bereinigte Snapshots. Odysseus konsumiert nur diese Snapshots. Der Slice fuehrt bewusst keine Host-Kommandos, keine Container-Socket-Zugriffe, keine Telegram-Token-Nutzung und keine Runtime-Ausfuehrung aus.

## Ziel

Odysseus braucht ein klares, robustes Interface fuer Homeserver-Health, ohne Host-Kommandos aus dem Core oder Container auszufuehren.

Dieses Interface soll:

- Health-Snapshots stabil typisieren
- Collector-Zustaende klar und konservativ machen
- Alerts und naechste Aktionen sichtbar machen
- Offline-, Unknown- und Unsupported-Zustaende robust behandeln

## Leitregel

Odysseus liest Health-Snapshots, aber fuehrt keine Host-Kommandos aus.

Das bedeutet:

- Core und Container sammeln keine Host-Metriken selbst
- ein Debian Host-Agent bleibt fuer Host-Reads zustaendig
- Odysseus konsumiert nur bereinigte Snapshot-Daten
- fehlende Daten fuehren zu `unknown` oder `unsupported`, nicht zu Crash oder Erraten

## Hauptobjekte

Das Interface soll mindestens diese Hauptobjekte kennen:

- `HealthSnapshot`
- `CollectorStatus`
- `AlertSummary`
- `RuntimeStatus`

## `HealthSnapshot`

`HealthSnapshot` ist das read-only Gesamtartefakt fuer den aktuellen Gesundheitszustand.

Mindestens enthalten:

- `snapshot_version`
- `generated_at`
- `host_label` optional
- `overall_status`
- `collectors`
- `alerts`
- `runtime_status`

## Bedeutung der `HealthSnapshot`-Felder

### `snapshot_version`

Version des Snapshot-Schemas.

Zweck:

- Schema-Aenderungen explizit machen
- Builder und Validatoren spaeter kompatibel halten

### `generated_at`

Zeitpunkt, wann der Host-Agent den Snapshot erzeugt hat.

Wichtig:

- Metadatum
- kein Beweis fuer Frische einzelner Collector-Werte

### `host_label` optional

Optionaler, menschenlesbarer Name des Hosts.

Beispiele:

- `homeserver`
- `mini-pc`
- `debian-main`

### `overall_status`

Konservative Gesamtampel fuer den Snapshot.

Erwartete Werte:

- `ok`
- `warn`
- `critical`
- `unknown`

`overall_status` darf nie ein einzelnes unbekanntes Detail als `ok` schoenrechnen.

### `collectors`

Map oder Liste der Collector-Zustaende fuer die bekannten Bereiche.

### `alerts`

Liste kompakter `AlertSummary`-Eintraege.

### `runtime_status`

Zusatzsicht auf Laufzeit-/Agent-Zustand des Health-Systems selbst.

## `CollectorStatus`

Fuer mindestens diese Collector-Bereiche braucht das Interface einen `CollectorStatus`:

- `cpu`
- `memory`
- `disk`
- `load`
- `uptime`
- `updates`
- `temperature`
- `smart`
- `containers`

## Pflichtstatus fuer `CollectorStatus`

Jeder `CollectorStatus` soll mindestens einen Zustand aus dieser Menge liefern:

- `ok`
- `warn`
- `critical`
- `unknown`
- `unsupported`

## Bedeutung der Collector-Zustaende

### `ok`

Collector hat gueltige Daten geliefert und sieht keinen aktuellen Warn- oder Kritisch-Fall.

### `warn`

Collector hat gueltige Daten, aber ein Warnschwellenwert oder auffaellige Lage ist erreicht.

### `critical`

Collector hat gueltige Daten und meldet eine kritische Lage.

### `unknown`

Collector konnte aktuell keinen verlaesslichen Zustand liefern.

Beispiele:

- Host-Agent offline
- Collector-Read fehlgeschlagen
- Daten unvollstaendig

### `unsupported`

Collector ist in dieser Umgebung bewusst nicht verfuegbar.

Beispiele:

- keine SMART-Unterstuetzung
- keine Temperaturquelle
- keine Container-Runtime eingebunden

## Erwartete `CollectorStatus`-Semantik

Ein spaeteres Modell oder Builder soll Collector-Zustaende konservativ behandeln:

- `unknown` ist kein `ok`
- `unsupported` ist keine Fehlbehauptung
- fehlende Collector-Daten duerfen UI und Plugin nicht crashen

## `AlertSummary`

`AlertSummary` ist die kompakte, UI- und Telegram-taugliche Verdichtung einer konkreten Auffaelligkeit.

Mindestens enthalten:

- `severity`
- `title`
- `cause`
- `next_action`
- `dedupe_key`
- `cooldown_hint`

## Bedeutung der `AlertSummary`-Felder

### `severity`

Schweregrad des Alerts.

Empfohlene Werte:

- `warn`
- `critical`
- `info`

### `title`

Kurzer, menschenlesbarer Titel.

Beispiele:

- `Disk space low`
- `Health agent offline`
- `Updates pending`

### `cause`

Kurze Ursache oder Ausloeserbeschreibung.

### `next_action`

Klare naechste Handlungsempfehlung fuer Operator.

Beispiele:

- `check disk usage on host`
- `verify host agent service`
- `review pending package updates`

### `dedupe_key`

Stabiler Schluessel fuer spaeteres Dedupe oder Cooldown-Verhalten.

Wichtig:

- dient nicht der Anzeige allein
- soll identische Alerts wiedererkennbar machen

### `cooldown_hint`

Hinweis fuer spaetere Alert-Drosselung oder Wiederholungslogik.

Wichtig:

- nur Hinweis
- keine echte Dispatch- oder Sendelogik in diesem Slice

## `RuntimeStatus`

`RuntimeStatus` beschreibt den Zustand des Health-Agent-Interfaces selbst.

Mindestens ausdruecklich modellieren:

- Agent erreichbar oder offline
- Snapshot plausibel oder unbekannt
- Collector-Teilmengen verfuegbar, unknown oder unsupported

Empfohlene konservative Zustaende:

- `ok`
- `degraded`
- `offline`
- `unknown`

## Agent offline / unknown Semantik

Agent offline oder Collector unknown duerfen nie zu Crash oder leerer Stille fuehren.

Stattdessen muss das System spaeter verstaendlich anzeigen koennen:

- Agent offline
- Collector unknown
- Collector unsupported
- letzter Snapshot unklar oder unvollstaendig

Die Grundregel lautet:

- lieber klar `unknown` als falsches `ok`

## Container- und Runtime-Grenze

Dieser Contract setzt harte Grenzen:

- keine direkten Host-Kommandos aus Odysseus
- keine Docker- oder Podman-Socket-Pflicht fuer den Core
- keine Telegram-Token im Snapshot-Interface

Podman-first, Docker-compatible bedeutet hier nur:

- der spaetere Host-Agent oder Runtime-Adapter kann beide Welten abbilden
- das Snapshot-Interface bleibt davon entkoppelt

## Sicherheits- und Robustheitsregeln

Das Interface soll:

- keine Secrets transportieren
- keine rohen Host-Command-Ausgaben voraussetzen
- keine Socket- oder Root-Abkuerzung im Core verlangen
- bei fehlenden Daten robust bleiben

Nicht Teil des Interfaces:

- echte Host-CLI-Aufrufe
- Container-Socket-Zwang
- Telegram-Bot-Geheimnisse
- Log- oder Trace-Dumps aus dem Host

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur kleine, reine Modelle bauen fuer:

- Dataclasses
- Validatoren
- Builder

Wichtig:

- keine IO
- keine Host-Kommandos
- keine Container-CLI-Aufrufe
- keine Socket-Pflicht
- Tests nur mit Mock-Daten

## Beispiel fuer spaeteren sicheren Snapshot

Zulaessige Struktur:

- `snapshot_version`
- `generated_at`
- `host_label`
- `overall_status`
- `collectors`
- `alerts`
- `runtime_status`

Zulaessige Collector-Werte:

- `cpu.status = ok`
- `temperature.status = unknown`
- `smart.status = unsupported`

Nicht zulaessig:

- `run_podman_now`
- `shell_command`
- `docker_socket_path`
- `telegram_token`

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Host-Agent-Implementierung
- keine Host-Kommandos
- keine Runtime-Adapter-Implementierung
- keine Telegram-Sendelogik
- keine UI-Implementierung

Er legt nur fest, wie Odysseus spaeter einen bereinigten, robusten und offline-sicheren Health-Snapshot von einem externen Host-Agent lesen soll.
