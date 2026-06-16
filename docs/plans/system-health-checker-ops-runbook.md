# System Health Checker Ops Runbook

Stand: 2026-06-16

Status: **SHC9 Ops-/Security-Runbook**

Quellen:

- `docs/plans/system-health-checker-plugin.md`
- `plugins/system_health_checker/`

Dieses Runbook beschreibt den sicheren Betrieb des spaeteren System Health
Checker Host-Agent. Es ist bewusst keine Root- oder Docker-Socket-Abkuerzung.

## Zielbild

Odysseus bleibt ein ruhiger Hausmeister fuer den Homeserver:

- still, wenn alles gesund ist
- fruehe Warnung bei Risiken
- klare Ursache und naechste Handlung
- keine geheimen Tokens in Logs
- kein privilegierter Hostzugriff aus dem Odysseus-Container

## Komponenten

### Debian Host-Agent

Geplanter Dienst:

```text
odysseus-health-agent.service
```

Aufgaben:

- Host-Metriken sammeln
- optionale externe CLIs kontrolliert aufrufen
- Health Snapshot lokal bereitstellen
- Telegram Pull/Push nur nach separatem Token-/Allowlist-Gate

### Odysseus Plugin

Aktueller Scope:

```text
plugins/system_health_checker/
```

Aufgaben:

- schema-stabile Health Snapshots darstellen
- Offline-/Unknown-Zustand erklaeren
- keine Host-Kommandos ausfuehren
- keine Docker-/Podman-Sockets mounten

## Minimaler Startpfad

1. Plugin in Odysseus aktivieren.
2. Plugin-Seite oeffnen.
3. Offline Snapshot sehen.
4. Host-Agent separat installieren.
5. Host-Agent liefert Snapshot an lokale API.
6. Odysseus liest nur bereinigte Snapshot-Daten.

## Debian-Pakete

MVP:

- Python 3 auf dem Host
- optional `python3-psutil`
- Podman oder Docker nur fuer Container-Status

Spaeter:

- `lm-sensors` fuer Temperaturen
- `smartmontools` fuer SMART/NVMe
- `python3-apt` oder kontrollierte apt Simulation

## Berechtigungen

Grundregel:

```text
so wenig Host-Rechte wie moeglich, niemals breite Root-Rechte fuer Odysseus
```

Erlaubte Richtung:

- Host-Agent bekommt gezielte Rechte
- Odysseus-Container liest nur Snapshot
- SMART oder sensors Rechte werden einzeln dokumentiert

Nicht erlaubt:

- `/var/run/docker.sock` in Odysseus mounten
- Podman/Docker Socket als Standard voraussetzen
- `sudo` aus Odysseus heraus
- OS-Kommandos aus Lens/UI
- Bot Tokens in Repo, Logs oder UI

## Systemd-Grundregeln

Der spaetere Dienst soll:

- als eigener User laufen
- Restart Policy besitzen
- Logs ohne Secrets schreiben
- lokal erreichbar sein
- keine externe API oeffnen, solange kein Auth-/TLS-Konzept existiert

Beispiel nur als Form, nicht als finaler Service:

```ini
[Service]
User=odysseus-health
ExecStart=/opt/odysseus-health-agent/venv/bin/python -m odysseus_health_agent
Restart=on-failure
```

## Telegram-Regeln

Default:

- Long Polling
- Allowlist fuer User IDs
- kein Token-Logging
- kein Webhook-Zwang

Noch nicht implementiert:

- echter Bot Runner
- Token Storage
- Push Send
- Secure Telegram Flow fuer sensible Daten

## Tests vor echtem Host-Agent

Aktueller Plugin-Test:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_system_health_checker_plugin.py tests\test_system_health_checker_collectors.py tests\test_system_health_checker_advanced_collectors.py tests\test_system_health_checker_rule_engine.py tests\test_system_health_checker_telegram_adapter.py tests\test_system_health_checker_alert_dispatcher.py tests\test_system_health_checker_runtime_adapter.py
```

Erwarteter Stand nach SHC8:

```text
53 passed, 1 warning
```

## Go/No-Go

Go fuer echten Host-Agent, wenn:

- Plugin-Snapshot und Offline UI gruen sind
- Collectors nur normalisierte Inputs erwarten
- Runtime Adapter keine Sockets voraussetzt
- Telegram Adapter tokenfrei und allowlist-faehig ist
- Alert Dispatcher nur Plaene erzeugt

No-Go, wenn:

- ein Slice Host-Kommandos aus dem Odysseus-Container ausfuehren will
- ein Token in Git, Logs oder UI auftaucht
- Alerts ohne Cooldown senden koennen
- Docker-only oder Socket-only als Standard entsteht
- Collector, Rule Engine und UI in einem grossen Hotfile-Slice vermischt werden

## Naechste sichere Slices

### `SHC10-host-agent-package-plan`

Nur Doku/Scaffold fuer den externen Debian Host-Agent. Kein echter Service-Start.

### `SHC11-host-agent-basic-api`

Kleiner lokaler Agent-Prototyp mit statischem Snapshot. Keine Host-Kommandos.

### `SHC12-host-agent-proc-collectors`

Erste echte Host-Reads aus `/proc`, aber nur im Host-Agent, nicht in Odysseus.

### `SHC13-telegram-runner`

Erst nach Token-Storage- und Allowlist-Plan. Kein Token im Repo.

## Abschlussregel

Dieser Track ist nur dann produktionsnah, wenn Odysseus auch bei fehlendem
Host-Agent ruhig bleibt und den Zustand verstaendlich als `unknown/offline`
darstellt.
