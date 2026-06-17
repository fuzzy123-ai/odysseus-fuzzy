# System Health Dashboard Contract

Stand: 2026-06-17

Status: **SHC8A Docs-Contract fuer ein spaeteres Odysseus Health Dashboard / Plugin UI**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-rule-engine-alert-contract.md`
- `docs/plans/system-health-container-runtime-adapter-contract.md`
- `docs/plans/system-health-security-ops-runbook.md`

Dieser Contract definiert die semantische Grundlage fuer ein spaeteres Odysseus Health Dashboard oder Plugin UI. Der Slice bleibt bewusst docs-only: kein UI-Code, keine Obsidian-/Lens-Implementierung, keine Host-Kommandos, keine Token, keine Netzwerkaktionen. Das Dashboard liest spaeter nur bereinigte Snapshots und Readiness-Reports.

## Ziel

Odysseus braucht eine ruhige, verstaendliche Oberflaeche fuer Homeserver-Health.

Diese Oberflaeche soll:

- nur bereinigte Health-Snapshots lesen
- Unknown-, Offline- und Setup-Lagen verstaendlich machen
- aktive Alerts und naechste Handlungen kompakt zeigen
- keine Auto-Reparatur oder versteckten Host-Zugriff versprechen

## Leitregel

Das Dashboard liest nur bereinigte Snapshots und Readiness-Reports, nie direkte Host-Kommandos.

Das bedeutet:

- keine Host-CLI im Dashboard
- keine Runtime-Sockets im Dashboard
- keine direkten Token- oder Netzwerkthemen im UI-Modell
- keine Sonderabkuerzung an `HealthSnapshot` vorbei

## Datenquellen

Das Dashboard soll spaeter nur mit bereinigten, read-only Daten arbeiten:

- `HealthSnapshot`
- `CollectorStatus`
- `AlertSummary`
- `RuntimeStatus`
- `OpsReadinessReport` oder aequivalenter Readiness-Status

Wichtig:

- keine direkten Host-Kommandos
- kein Nachladen roher Host-Daten
- keine eigenstaendige Runtime-Erkennung im UI

## UI-Zustaende

Das Dashboard soll mindestens diese klaren UI-Zustaende kennen:

- `agent_offline`
- `no_data`
- `ok`
- `warn`
- `critical`
- `partial_unknown`
- `setup_required`

## Bedeutung der UI-Zustaende

### `agent_offline`

Der Host-Agent ist nicht erreichbar oder liefert aktuell keinen nutzbaren Snapshot.

Wichtig:

- ruhig erklaeren
- kein Crash
- keine Fehlbehauptung ueber Host-Gesundheit

### `no_data`

Es sind aktuell keine passenden Snapshot-Daten vorhanden.

Beispiele:

- Collectorbereich nicht geliefert
- Snapshot leer
- Feature noch nicht vorbereitet

### `ok`

Die vorliegenden Snapshot-Daten zeigen keine aktive Warn- oder Kritisch-Lage.

### `warn`

Die Snapshot-Daten zeigen mindestens eine relevante Warnlage.

### `critical`

Die Snapshot-Daten zeigen mindestens eine kritische Lage mit klarer Handlungsempfehlung.

### `partial_unknown`

Ein Teil der Daten ist verstaendlich vorhanden, aber andere Bereiche sind `unknown` oder unvollstaendig.

Wichtig:

- kein falsches Gesamt-`ok`

### `setup_required`

Die Datenlage zeigt, dass Abhaengigkeiten, Rechte oder Agent-Konfiguration fehlen.

Beispiele:

- `smartctl` fehlt
- `lm-sensors` fehlt
- Runtime-Sicht nicht vorbereitet

## Dashboard-Sections

Das Dashboard soll spaeter mindestens diese Bereiche kennen:

- `overview`
- `active_alerts`
- `collectors`
- `containers`
- `readiness`
- `last_updated`

## Bedeutung der Sections

### `overview`

Kurze Gesamtampel plus Hauptursache und naechste Handlung.

Typische Inhalte:

- Gesamtstatus
- wichtigste Ursache
- eine naechste sichere Aktion

### `active_alerts`

Zeigt aktive Warn- oder Kritisch-Zustaende kompakt an.

Wichtig:

- keine Alert-Flut
- keine rohen Log-Listen

### `collectors`

Zeigt den Zustand der einzelnen Collector-Bereiche.

Typische Inhalte:

- CPU
- Memory
- Disk
- Load
- Uptime
- Temperature
- SMART
- Updates
- Reboot Required

### `containers`

Zeigt die bereinigte Container-Runtime-Lage.

Typische Inhalte:

- Runtime-Typ
- `container_count`
- `unhealthy_count`
- `setup_hint` falls relevant

### `readiness`

Zeigt den spaeteren Betriebs- oder Ops-Readiness-Zustand.

Typische Inhalte:

- Host-Agent-Grenze eingehalten
- Socket-freie Core-Architektur
- Setup-Hinweise oder bekannte No-Go-Punkte

### `last_updated`

Zeigt den Zeitpunkt des letzten bekannten Snapshot-Standes.

Wichtig:

- Metadatum
- kein automatischer Frischebeweis fuer alle Teilbereiche

## Offline- und Unknown-Semantik

Offline oder Unknown muessen spaeter verstaendlich und ruhig dargestellt werden.

Die Grundregel lautet:

- kein Crash
- kein falsches `ok`
- keine versteckte Leere

Stattdessen:

- klarer Status
- kurze Ursache
- naechste sichere Handlung

## Copy-Regeln

Das Dashboard soll die Stimme eines ruhigen Hausmeisters behalten.

Mindestens enthalten:

- Ursache
- naechste Handlung

Nicht enthalten:

- Panik-Sprache
- Reparaturbehauptung
- irrefuehrende Vollgesundheit bei `unknown`

Empfohlene Form:

- `Status: warn`
- `Cause: disk usage on / is high`
- `Next action: review disk usage on host`

## Keine Auto-Reparatur-Sprache

Das Dashboard darf nie behaupten:

- der Host sei repariert
- ein Alert sei von selbst behoben worden
- Odysseus habe Host-Aktionen ausgefuehrt

Das Dashboard:

- beobachtet
- erklaert
- empfiehlt

## API- und Snapshot-Semantik

Ein spaeteres Dashboard-Modell soll nur gegen bereits bereinigte, stabile Objekte gebaut werden.

Wichtig:

- keine Sonderlogik gegen Host-Agent-Details
- keine CLI-Parser im UI-Modell
- keine Netzwerkanfragen in diesem Slice

## Keine UI-Hotfiles

Dieser Contract beschreibt ausdruecklich:

- keine Obsidian-/Lens-Hotfiles
- keine konkrete Plugin-Seite
- keine CSS- oder Frontend-Implementierung

Er liefert nur die semantische Grundlage fuer spaetere UI-Arbeit.

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf hoechstens ein isoliertes Dashboard Summary Model aus:

- `HealthSnapshot`
- `OpsReadinessReport`

bauen.

Wichtig:

- keine IO
- keine Host-Kommandos
- keine Netzwerkausfuehrung
- keine UI-Implementierung

## Beispiel fuer spaeteren sicheren Dashboard-Status

Zulaessig:

- `overview.status = warn`
- `active_alerts = 2`
- `containers.runtime = podman`
- `readiness.status = no_go`
- `last_updated = 2026-06-17T09:30:00Z`

Oder:

- `overview.status = partial_unknown`
- `cause = temperature collector unavailable`
- `next_action = verify host agent sensor setup`

Nicht zulaessig:

- `run smartctl now`
- `mount docker socket`
- `restart unhealthy container`
- `system repaired automatically`

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine konkrete Obsidian-/Lens-UI
- keinen Frontend-Code
- keine Host-Agent-Implementierung
- keine Netzwerk- oder Token-Integration
- keine Auto-Reparatur

Er legt nur fest, wie ein spaeteres Odysseus Health Dashboard ruhig, read-only und robust gegen bereinigte Health-Snapshots und Readiness-Reports modelliert werden soll.
