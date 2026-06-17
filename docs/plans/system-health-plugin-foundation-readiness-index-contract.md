# System Health Plugin Foundation Readiness Index Contract

Stand: 2026-06-17

Status: **SHC14A Docs-Contract fuer einen finalen System Health Plugin Foundation Readiness Index**

Quellen:

- `docs/plans/system-health-plugin-operator-review-packet-contract.md`
- `docs/plans/system-health-plugin-readiness-score-contract.md`
- `docs/plans/system-health-plugin-audit-index-contract.md`
- `docs/plans/system-health-plugin-foundation-index.md`
- `docs/plans/system-health-security-ops-runbook.md`

Dieser Contract definiert einen knappen Foundation-Readiness-Index fuer die System Health Plugin Foundation. Der Index zeigt Operatoren, welche Plugin-Fundamente fertig vorbereitet sind, welche manuellen Reviews noch offen bleiben und welche Runtime-Faehigkeiten bewusst nicht aktiviert sind. Der Slice bleibt rein Foundation/Review: keine echte Host-Agent-, Telegram-, Netzwerk- oder Container-Runtime-Aktivierung.

## Ziel

Der System Health Checker braucht nach SHC11-SHC13 eine kurze Abschluss-Sicht, die nicht ausfuehrbar, aber schnell lesbar ist.

Der Foundation-Readiness-Index soll beantworten:

- welche Foundation-Artefakte vorhanden sind
- welche Readiness-Evidence und Referenzen vorliegen
- welche manuellen Review-Gates offen sind
- welche Runtime-Faehigkeiten weiterhin bewusst deaktiviert bleiben
- welche bekannten Grenzen fortgelten
- welche Folge-Slices noch sicher erlaubt bleiben

## Leitregel

Foundation ready bedeutet nicht deployed, nicht host-agent-running, nicht runtime-enabled.

Das bedeutet:

- ein gruener Foundation-Status ist nur Review-Reife
- kein Host-Agent darf daraus als laufend abgeleitet werden
- keine Telegram-, Netzwerk- oder Socket-Aktivierung darf impliziert werden

## Harte Architekturgrenzen

Die folgenden Grenzen bleiben fest:

- keine Host-Kommandos aus Odysseus-Core
- keine Telegram-Tokens
- keine Netzwerk-/Webhook-Aktivierung
- keine Podman- oder Docker-Socket-Pflicht

Wichtig:

- Podman-first, Docker-compatible bleibt die Leitplanke
- rootless Podman bleibt positiv mitgedacht
- Docker bleibt nur kompatibler Fallback

## Foundation Artifacts

Die Section `foundation_artifacts` soll die vorbereiteten SHC-Artefakte kompakt listen.

Mindestens:

- Plugin Foundation Index
- Plugin Audit Index
- Plugin Readiness Score
- Operator Review Packet
- Security and Ops Runbook
- Dashboard Contract
- Container Runtime Adapter Contract
- Rule Engine/Alert Contract

Wichtig:

- diese Liste beschreibt vorbereitete Foundation-Artefakte
- sie ist kein Beleg fuer laufende Runtime oder Delivery

## Readiness Evidence

Die Section `readiness_evidence` soll die relevanten Referenzen fuer Review aufnehmen.

Zulaessig:

- Testdatei- oder Testgruppen-Referenzen
- Nachtest-Hinweise aus frueheren SHC-Slices
- Verweise auf Runbooks, Ops-Readiness oder Dashboard-Contracts
- Boundary- und Audit-Referenzen

Nicht zulaessig:

- erfundene frische Testergebnisse
- neue Testausfuehrungen durch den Index
- rohe Logs oder komplette Testausgaben

Wichtig:

- Evidence wird referenziert, nicht neu behauptet

## Manual Review Gates

Die Section `manual_review_gates` soll die offenen menschlichen Review-Schritte kompakt zeigen.

Mindestens:

- Audit Index pruefen
- Readiness Score gegen harte Grenzen lesen
- Operator Review Packet in Reihenfolge abarbeiten
- Deployment-Voraussetzungen als Gate-Vorbedingungen bestaetigen
- blocked runtime actions bewusst offen lassen

Wichtig:

- diese Gates sind Review-only
- sie starten keinen Host-Agenten
- sie fuehren keine Netzwerk- oder Telegram-Aktionen aus

## Runtime Still Disabled

Die Section `runtime_still_disabled` muss die bewusst nicht aktivierten Faehigkeiten sichtbar halten.

Mindestens:

- keine Host-Kommandos aus Odysseus-Core
- keine Telegram-Delivery
- keine Webhook- oder Polling-Aktivierung
- keine echte Netzwerk-Runtime
- keine Docker- oder Podman-Socket-Pflicht
- keine automatische Reparatur

Wichtig:

- diese Liste ist Sicherheitsgrenze
- `foundation_ready` darf sie nie ueberschreiben

## Known Limits

Die Section `known_limits` soll die weiterhin geltenden Foundation-Grenzen benennen.

Mindestens:

- kein Host-Agent in Betrieb
- keine Push-/Delivery-Runtime
- keine UI-Hotfiles
- keine Deployment-Freigabe
- keine Token-Nutzung

Wichtig:

- Grenzen bleiben sichtbar statt implizit zu verschwinden

## Next Allowed Slices

Die Section `next_allowed_slices` soll nur sichere Folge-Gates oder Folge-Slices benennen.

Typische Inhalte:

- Host-Agent Implementation Gate
- Telegram Delivery Gate
- Dashboard UI Gate
- Snapshot Transport Gate
- spaetere Deployment-Readiness-Checks

Wichtig:

- nur benennen, nicht aktivieren
- keine implizite Runtime-Freigabe

## Status Values

Der spaetere Foundation-Readiness-Index soll mindestens diese Statuswerte kennen:

- `foundation_ready`
- `review_required`
- `blocked`
- `deferred`

## Bedeutung der Status Values

### `foundation_ready`

Die Foundation-Artefakte sind ausreichend dokumentiert und konsistent fuer manuelle Review-Schritte.

Wichtig:

- nicht deployed
- nicht host-agent-running
- nicht runtime-enabled

### `review_required`

Ein Operator oder Charlie muss noch aktiv lesen, vergleichen oder offene Fragen aufloesen.

### `blocked`

Mindestens eine harte Sicherheits- oder Architekturgrenze ist verletzt oder unklar.

### `deferred`

Ein Folge-Gate oder eine spaetere Runtime-Freigabe ist bewusst vertagt.

## No-Secrets und No-Raw-Logs

Der Foundation-Readiness-Index darf nicht enthalten:

- Secrets
- echte Telegram-Tokens
- rohe Logs
- komplette Host-Ausgaben
- komplette Netzwerk- oder Webhook-Payloads

Zulaessig sind:

- kompakte Statuswerte
- kurze Boundary- und No-Go-Hinweise
- kurze Test- und Runbook-Referenzen
- kurze Gate- oder Defer-Hinweise

## Beispiel fuer spaeteren sicheren Foundation-Readiness-Index

Zulaessig:

- `foundation_artifacts = audit index, readiness score, operator review packet`
- `readiness_evidence = see assigned SHC test refs and runbooks`
- `manual_review_gates = audit -> score -> review packet`
- `runtime_still_disabled = host commands, telegram delivery, network runtime`
- `known_limits = no host agent, no deployment, no tokens`
- `next_allowed_slices = [host_agent_gate, dashboard_ui_gate]`

Nicht zulaessig:

- `host_agent_running = true`
- `deployed = true`
- `runtime_enabled = true`
- kompletter Host- oder Testlogdump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein reines Foundation-Readiness-Index- oder Summary-Modell ueber vorhandene SHC-Artefakte bauen.

Zulaessige Inputs:

- `SystemHealthPluginOperatorReviewPacket`
- `SystemHealthPluginReadinessScore`
- `SystemHealthPluginAuditIndex`
- `SystemHealthPluginFoundationIndex`
- `SystemHealthSecurityOpsRunbook`

Wichtig:

- keine IO
- kein Netzwerk
- keine Host-Kommandos
- keine Telegram-Tokens
- keine Webhook- oder Polling-Ausfuehrung

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine echte Host-Agent-Implementierung
- keine Telegram-Delivery
- keine Netzwerk- oder Webhook-Aktivierung
- keine Container-Socket-Nutzung im Core
- keine UI-Hotfiles
- keine erfundenen Testergebnisse

Er legt nur fest, wie ein spaeterer finaler Foundation-Readiness-Index die vorhandenen SHC-Artefakte, ihre Review-Referenzen und die weiterhin bewusst deaktivierten Runtime-Grenzen konservativ zusammenfassen soll.
