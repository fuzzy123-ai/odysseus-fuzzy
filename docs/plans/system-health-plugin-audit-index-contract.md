# System Health Plugin Audit Index Contract

Stand: 2026-06-17

Status: **SHC11A Docs-Contract fuer einen System Health Plugin Audit Index**

Quellen:

- `docs/plans/system-health-plugin-foundation-index.md`
- `docs/plans/system-health-security-ops-runbook.md`
- `docs/plans/system-health-dashboard-contract.md`
- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-rule-engine-alert-contract.md`

Dieser Contract definiert einen auditierbaren Index fuer die System Health Plugin Foundation. Er zeigt Operatoren, welche Plugin-Fundamente vorhanden sind, welche Host-, Telegram-, Netzwerk- und Runtime-Aktionen bewusst nicht im Odysseus-Core laufen, welche Tests und Runbooks relevant bleiben und was vor einer echten Host-Agent-Integration noch reviewed werden muss. Der Slice fuehrt keine Host-Kommandos aus, aktiviert keine Tokens und startet keine Netzwerk- oder Webhook-Integrationen.

## Ziel

Der System Health Checker braucht nach SHC8-SHC10 einen kompakten Audit-Einstieg, der Foundation und Runtime-Grenzen sauber trennt.

Der Audit-Index soll beantworten:

- welche Plugin-Foundation-Artefakte bereits vorliegen
- welche Host-Agent-Grenzen und Core-Grenzen gelten
- welche Runtime-Aktionen weiterhin No-Go bleiben
- welche Tests, Nachtests und Runbooks fuer Review relevant sind
- welche Deployment-Voraussetzungen vor einer spaeteren Host-Agent-Integration gelten
- welche Folge-Slices nach der Foundation noch offen bleiben

## Leitregel

Keine Host-Kommandos aus Odysseus, keine Telegram-Tokens, keine Netzwerk-/Webhook-Aktivierung, keine Podman/Docker-Socket-Pflicht.

Das bedeutet:

- Odysseus-Core bleibt ohne direkten Host-Zugriff
- Token- oder Socket-Abkuerzungen sind kein erlaubter Foundation-Pfad
- Audit-Readiness ist nicht gleich Host-Agent- oder Delivery-Readiness

## Architekturleitplanke

Podman-first, Docker-compatible bleibt die feste Architekturleitplanke.

Wichtig:

- rootless Podman bleibt positiv mitgedacht
- Docker darf kompatibler Fallback sein
- weder Podman- noch Docker-Socket werden im Odysseus-Core vorausgesetzt

## Plugin Foundation Artifacts

Die Section `plugin_foundation_artifacts` soll die vorbereiteten SHC-Bausteine sammeln.

Mindestens:

- Health Agent Interface
- Basic Debian Collectors
- Rule Engine und Alert-Modell
- Telegram Pull Status Contract
- Auto-Alerting Contract
- Container Runtime Adapter Contract
- Advanced Debian Collectors
- Health Dashboard Contract
- Security and Ops Runbook
- Plugin Foundation Index

Wichtig:

- diese Liste zeigt Foundation-Artefakte
- sie ist kein Beleg fuer laufende Runtime, Host-Agent oder Telegram-Delivery

## Host Agent Boundaries

Die Section `host_agent_boundaries` muss die klare Arbeitsteilung zeigen.

Mindestens:

- Host-Agent sammelt und normalisiert
- Odysseus-Core konsumiert nur bereinigte Snapshots
- Host-Kommandos bleiben ausserhalb des Core
- CLI-, Socket-, Sensor- und SMART-Details bleiben Host-Agent-Verantwortung

Wichtig:

- `/proc`, `sensors`, `smartctl`, apt-Simulation und Runtime-CLI-Aufrufe gehoeren nicht in den Odysseus-Core
- Unknown- oder Unsupported-Zustaende sind zulaessige Datenzustande, kein Anlass fuer Core-Workarounds

## No Go Runtime Actions

Die Section `no_go_runtime_actions` soll die bewusst weiter gesperrten Runtime-Aktionen benennen.

Mindestens:

- keine Host-Kommandos aus Odysseus-Core oder Odysseus-Container
- keine Telegram-Tokens im Repo, in Logs oder in Snapshot-Daten
- keine Webhook-, Polling- oder Push-Aktivierung
- keine Docker- oder Podman-Socket-Pflicht
- keine automatische Reparatur
- keine echten Netzwerkaufrufe aus diesem Foundation-Track

Wichtig:

- diese Liste ist Sicherheitsgrenze
- Audit-Index und Foundation-Readiness duerfen sie nicht abschwaechen

## Required Review Tests

Die Section `required_review_tests` soll relevante Tests, Nachtests und Runbooks referenzieren.

Zulaessig:

- Testdatei- oder Testgruppen-Referenzen
- Nachtest-Hinweise aus frueheren SHC-Slices
- Runbook-Referenzen fuer Ops-Readiness
- Dashboard- oder Rule-Model-Referenzen

Nicht zulaessig:

- erfundene frische Testergebnisse
- neue Testausfuehrungen aus diesem Audit-Index
- komplette Testlogs oder rohe Host-Ausgaben

Wichtig:

- Tests duerfen referenziert werden
- unbekannte oder noch nicht erneut gepruefte Ergebnisse bleiben offen statt beschoenigt

## Operator Audit Checklist

Die Section `operator_audit_checklist` soll eine konservative Reihenfolge fuer Review geben.

Mindestens:

- Plugin Foundation Index lesen
- Security/Ops Runbook lesen
- Dashboard-Contract und HealthSnapshot-Grenzen sichten
- Container Runtime Adapter und Podman-first-Grenzen bestaetigen
- Telegram Pull und Auto-Alerting als Decision-only lesen
- relevante Test- und Nachtest-Referenzen pruefen
- Deployment-Voraussetzungen und offene Folge-Gates notieren

Wichtig:

- die Checklist ist read-only
- sie startet keinen Host-Agenten
- sie fuehrt keine Host-, Netzwerk- oder Telegram-Aktionen aus

## Deployment Prerequisites

Die Section `deployment_prerequisites` soll nur die Voraussetzungen fuer eine spaetere echte Integration beschreiben.

Mindestens:

- separater Host-Agent statt Core-Host-Zugriff
- minimale Rechte fuer spaetere Advanced Collectors
- klare Token-Hygiene
- klare Snapshot-Schnittstelle
- Podman-first/Docker-compatible Betriebsrahmen
- Unknown-/Unsupported-Verhalten statt Crash

Wichtig:

- diese Voraussetzungen sind keine Aktivierung
- sie sind nur Gate-Vorbedingungen fuer spaetere Runtime-Arbeit

## Followup Slices

Die Section `followup_slices` soll nur sichere Folge-Gates oder Folge-Slices benennen.

Typische Inhalte:

- Host-Agent Implementation Gate
- Telegram Delivery Gate
- Dashboard UI Gate
- Snapshot Transport Gate
- spaetere Deployment-Readiness-Checks

Wichtig:

- nur benennen, nicht aktivieren
- keine implizite Host- oder Netzwerkfreigabe

## No-Secrets und No-Raw-Logs

Der Audit-Index darf nicht enthalten:

- Secrets
- echte Tokens
- rohe Logs
- komplette Host-Ausgaben
- komplette Telegram-Responses

Zulaessig sind:

- kompakte Statuswerte
- kurze Test- und Runbook-Referenzen
- kurze Boundary- und No-Go-Listen

## Beispiel fuer spaeteren sicheren Audit-Index

Zulaessig:

- `plugin_foundation_artifacts = dashboard contract, ops runbook, plugin foundation index`
- `host_agent_boundaries = host agent collects, core reads sanitized snapshots`
- `no_go_runtime_actions = host commands, telegram push, socket mount requirement`
- `required_review_tests = see assigned SHC test refs and runbooks`
- `deployment_prerequisites = separate host agent, minimal rights, token hygiene`
- `followup_slices = [host_agent_gate, telegram_delivery_gate, dashboard_ui_gate]`

Nicht zulaessig:

- `host_agent_running = true`
- `telegram_push_enabled = true`
- `mount docker socket now`
- kompletter Testlog- oder Hostdump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein reines Audit-Index- oder Summary-Modell ueber vorhandene SHC-Foundation-Artefakte bauen.

Zulaessige Inputs:

- `SystemHealthPluginFoundationIndex`
- `SystemHealthSecurityOpsRunbook`
- `HealthSnapshot`- und Dashboard-Statussichten
- Rule-, Alert- und Runtime-Adapter-Statussichten

Wichtig:

- keine IO
- kein Netzwerk
- keine Host-Kommandos
- keine Telegram-Tokens
- keine Webhook- oder Polling-Ausfuehrung

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine echte Host-Agent-Implementierung
- keine Host-Kommandos
- keine Telegram-Delivery
- keine Netzwerk- oder Webhook-Aktivierung
- keine Socket-Mount-Pflicht
- keine UI-Hotfiles
- keine erfundenen Testergebnisse

Er legt nur fest, wie ein spaeterer operatorfreundlicher Audit-Index die System Health Plugin Foundation, ihre Sicherheitsgrenzen, die relevanten Review-Referenzen und die weiterhin blockierten Runtime-Aktionen konservativ zusammenfassen soll.
