# System Health Plugin Foundation Index

Stand: 2026-06-17

Status: **SHC10A finaler Plugin-Foundation-Index fuer den System Health Checker**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-basic-collectors-contract.md`
- `docs/plans/system-health-advanced-debian-collectors-contract.md`
- `docs/plans/system-health-rule-engine-alert-contract.md`
- `docs/plans/system-health-telegram-pull-status-contract.md`
- `docs/plans/system-health-auto-alerting-contract.md`
- `docs/plans/system-health-container-runtime-adapter-contract.md`
- `docs/plans/system-health-dashboard-contract.md`
- `docs/plans/system-health-security-ops-runbook.md`
- `docs/plans/system-health-plugin-audit-index-contract.md`

Dieser Index fasst die Foundation des System Health Checker Plugin-Tracks zusammen. Er dient als nachvollziehbarer Bundle-Plan fuer den spaeteren Weiterbau des Plugins, ohne echte Host-Agent-Ausfuehrung, Telegram-Delivery, Netzwerkaktionen oder UI-Hotfile-Arbeit zu behaupten.

## Ziel

Der System Health Checker braucht einen klaren Foundation-Index, damit spaetere Folgearbeit nicht auf verstreuten Contracts basiert.

Dieser Index soll:

- alle modellierten Foundation-Bausteine an einem Ort auflisten
- die Grenzen zwischen Foundation und spaeterer Runtime klar halten
- das Plugin als eigenen Track statt als versteckte Nebensache sichtbar machen

## Foundation-Bausteine

Die System Health Checker Foundation umfasst aktuell diese vorbereiteten Bausteine:

- `SHC1` Health Agent Interface
- `SHC2` Basic Debian Collectors
- `SHC3` Rule Engine und Alert-Modell
- `SHC4` Telegram Pull Status
- `SHC5` Auto-Alerting Decision Layer
- `SHC6` Container Runtime Adapter
- `SHC7` Advanced Debian Collectors
- `SHC8` Odysseus Health Dashboard Contract
- `SHC9` Security and Ops Runbook

## Was bereits modelliert oder contracted ist

### `SHC1` Health Agent Interface

Bereits modelliert:

- `HealthSnapshot`
- `CollectorStatus`
- `AlertSummary`
- `RuntimeStatus`
- Unknown-/Unsupported-Semantik

### `SHC2` Basic Debian Collectors

Bereits modelliert:

- `cpu`
- `memory`
- `load`
- `uptime`
- `disk`

Mit:

- `observed_value`
- `setup_hint`
- Host-Agent-only Quellen

### `SHC3` Rule Engine und Alerts

Bereits modelliert:

- Severity-Sprache
- Dedupe
- Cooldown
- Recovery/Cleared
- konservative Ursache- und Handlungstexte

### `SHC4` Telegram Pull Status

Bereits modelliert:

- Pull-Kommandos `/status`, `/alerts`, `/disk`, `/updates`, `/containers`
- Allowlist-Regel
- `blocked_unauthorized`
- `unsupported_command`
- ruhige read-only Response-Copy

### `SHC5` Auto-Alerting Decision Layer

Bereits modelliert:

- `send`
- `suppress_cooldown`
- `suppress_duplicate`
- `send_recovery`
- `no_action`

Mit:

- Decision-only Semantik
- keine Auslieferung

### `SHC6` Container Runtime Adapter

Bereits modelliert:

- `PodmanAdapter`
- `DockerAdapter`
- `NoContainerRuntimeAdapter`
- Runtime-Typen `podman`, `docker`, `both`, `none`, `unknown`
- rootless Podman ohne Socket-Pflicht

### `SHC7` Advanced Debian Collectors

Bereits modelliert:

- `temperature`
- `smart`
- `updates`
- `reboot_required`

Mit:

- Fixture-only Parser-Semantik
- `unsupported` bei fehlender Dependency
- `unknown` bei fehlenden Rechten

### `SHC8` Dashboard Contract

Bereits modelliert:

- Dashboard liest nur bereinigte Snapshots/Readiness Reports
- Sections `overview`, `active_alerts`, `collectors`, `containers`, `readiness`, `last_updated`
- UI-Zustaende `agent_offline`, `no_data`, `ok`, `warn`, `critical`, `partial_unknown`, `setup_required`

### `SHC9` Security and Ops Runbook

Bereits modelliert:

- Install- und Betriebsnarrativ
- Minimalrechte
- Podman-first ohne Socket-Pflicht
- Go/No-Go
- Known Limits

## Was bewusst NICHT umgesetzt ist

Die Foundation ist bewusst nicht gleich Runtime.

Noch nicht umgesetzt:

- Host-Agent Runtime
- echte Telegram-Auslieferung
- UI-Hotfiles
- Host-Kommandos
- Tokens
- Netzwerk

## Bedeutung der Nicht-Umsetzungen

### Host-Agent Runtime

Es gibt noch keinen echten, laufenden Host-Agenten in diesem Foundation-Slice.

### Echte Telegram-Auslieferung

Pull- und Push-Semantik sind modelliert, aber:

- keine Bot-Ausfuehrung
- keine Long-Polling-/Webhook-Integration
- keine Token-Nutzung

### UI-Hotfiles

Es gibt keinen konkreten Obsidian-/Lens- oder Plugin-Frontend-Umsetzungs-Slice in dieser Foundation.

### Host-Kommandos

Keine `sensors`, `smartctl`, apt-, `/proc`- oder Runtime-CLI-Ausfuehrung im Odysseus-Core.

### Tokens

Keine Token-Speicherung, kein Token-Handling, kein Token-Logging.

### Netzwerk

Keine echte HTTP-, Bot- oder Push-Auslieferung.

## Plugin-Grenzen

Der Plugin-Track bleibt absichtlich modular.

Die Grenzen lauten:

- Collector getrennt von Rule Engine
- Rule Engine getrennt von Alert-Auslieferung
- Alert-Auslieferung getrennt von Presentation
- Presentation getrennt von Host-Zugriff

## Schichtenmodell

Die Foundation kann als vier saubere Ebenen gelesen werden:

- `collector`
- `rule`
- `alert`
- `presentation`

### `collector`

Bereinigt Host-Agent-Daten zu `CollectorStatus` und Snapshot-Signalen.

### `rule`

Bewertet Signale zu Severity, Dedupe, Cooldown und Recovery.

### `alert`

Modelliert Pull-Responses und Push-Entscheidungen, aber keine echte Auslieferung.

### `presentation`

Modelliert Dashboard-/Plugin-Sicht, aber keinen konkreten UI-Code.

## MVP-Go/No-Go

Die sichere Kurzform lautet:

- Foundation `ready`
- Runtime `not ready`

## Foundation Ready

Die Foundation ist bereit, wenn:

- die Semantik der Bausteine klar ist
- Unknown/Unsupported robust modelliert ist
- Security-Grenzen dokumentiert sind
- die Schichten sauber getrennt sind

## Runtime Not Ready

Die Runtime bleibt ausdruecklich nicht bereit, solange fehlt:

- echter Host-Agent
- echte Telegram-Auslieferung
- echte Netzwerk-/Token-Integration
- konkrete UI-Implementierung

## Naechste sichere Folgearbeit

Die naechsten sicheren Folgen bleiben separat gegated:

- Host-Agent Implementation Gate
- UI Gate
- Telegram Delivery Gate

## Host-Agent Implementation Gate

Naechster Runtime-nahe Schritt erst separat:

- Host-Agent als eigener Follow-up-Track
- keine Core-Vermischung

## UI Gate

Dashboard-/Plugin-UI erst als eigener Slice:

- read-only Snapshot-Bindung
- keine Hotfile-Abkuerzung

## Telegram Delivery Gate

Push oder echter Bot-Betrieb erst separat:

- Token-Handling
- Allowlist-Pruefung
- Delivery-/Cooldown-Runtime

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf hoechstens ein Bundle-Readiness-Modell aus vorhandenen Reports und Summaries bauen.

Wichtig:

- keine Runtime
- keine IO
- keine Host-Kommandos
- keine Netzwerkaktionen
- keine Token-Integration

## Beispiel fuer spaeteren sicheren Foundation-Status

Zulaessig:

- `foundation_status: ready`
- `runtime_status: not_ready`
- `host_agent_gate: pending`
- `telegram_delivery_gate: pending`
- `ui_gate: pending`

Nicht zulaessig:

- `host_agent_running: true` ohne echte Runtime
- `telegram_push_enabled: true` ohne Delivery-Gate
- `dashboard_live: true` ohne UI-Implementierung

## Abschlussregel

Der System Health Checker Track ist mit dieser Foundation nachvollziehbar vorbereitet, aber nicht produktiv freigegeben. Alles, was echte Host-Ausfuehrung, Telegram-Delivery, Netzwerk oder UI-Hotfiles beruehrt, bleibt ein eigener Folge-Gate statt still in die Foundation hineinzugleiten.
