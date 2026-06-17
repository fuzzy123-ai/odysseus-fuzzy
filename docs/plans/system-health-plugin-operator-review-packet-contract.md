# System Health Plugin Operator Review Packet Contract

Stand: 2026-06-17

Status: **SHC13A Docs-Contract fuer ein System Health Plugin Operator Review Packet**

Quellen:

- `docs/plans/system-health-plugin-audit-index-contract.md`
- `docs/plans/system-health-plugin-readiness-score-contract.md`
- `docs/plans/system-health-plugin-foundation-index.md`
- `docs/plans/system-health-security-ops-runbook.md`
- `docs/plans/system-health-dashboard-contract.md`

Dieser Contract definiert ein operatorfreundliches Review Packet fuer die System Health Plugin Foundation. Das Packet bringt Audit Index, Readiness Score, Dashboard-/Security-/Ops-Foundation und die harten Runtime-No-Go-Grenzen in eine konservative manuelle Review-Reihenfolge. Es ist eine Entscheidungsgrundlage fuer Menschen, kein Deployment und kein Runtime Enablement.

## Packet Purpose

Das Operator Review Packet soll den spaeteren Review-Einstieg vereinfachen.

Es beantwortet:

- welche SHC-Foundation-Artefakte zusammen gelesen werden muessen
- in welcher Reihenfolge ein Operator diese lesen soll
- welche Go-/No-Go-Fragen vor einer Host-Agent-Integration offen bleiben
- welche Runtime-Aktionen weiterhin blockiert bleiben
- welche Inputs fuer einen spaeteren manuellen Signoff benoetigt werden

## Leitregel

Review Packet bedeutet manuelle Entscheidungsgrundlage, nicht Deployment oder Runtime Enablement.

Das bedeutet:

- kein Host-Agent-Start
- keine Telegram-Delivery
- keine Netzwerk- oder Webhook-Aktivierung
- keine Podman- oder Docker-Socket-Pflicht
- keine Host-Kommandos aus Odysseus-Core

## Included Artifacts

Die Section `included_artifacts` soll die relevanten SHC-Foundation-Artefakte sammeln.

Mindestens:

- Plugin Foundation Index
- Plugin Audit Index
- Plugin Readiness Score
- Security and Ops Runbook
- Dashboard Contract
- Container Runtime Adapter Contract
- Rule Engine/Alert Contract

Wichtig:

- diese Liste beschreibt Review-Artefakte
- sie ist kein Beleg fuer laufende Runtime oder Delivery

## Review Order

Die Section `review_order` soll eine klare manuelle Lesereihenfolge vorgeben.

Empfohlene Reihenfolge:

- Plugin Foundation Index lesen
- Plugin Audit Index lesen
- Readiness Score lesen
- Security/Ops Runbook lesen
- Dashboard- und Boundary-Contracts sichten
- relevante Test- und Runbook-Referenzen pruefen
- blocked runtime actions bestaetigen

Wichtig:

- die Reihenfolge startet nichts
- sie ist nur fuer menschliche Orientierung gedacht

## Go No Go Questions

Die Section `go_no_go_questions` soll die entscheidenden Review-Fragen sammeln.

Mindestens:

- Sind die Foundation-Artefakte vollstaendig und konsistent beschrieben?
- Bleibt der Host-Agent klar ausserhalb des Odysseus-Core?
- Sind keine Telegram-Tokens, keine Netzwerkaktivierung und keine Socket-Pflicht impliziert?
- Sind Audit-Index und Readiness Score widerspruchsfrei?
- Sind Deployment-Voraussetzungen nur als Gate-Vorbedingungen beschrieben?
- Sind Tests und Runbooks nur referenziert und nicht als frisch gelaufen erfunden?

No-Go bleibt bestehen, wenn:

- Host-Kommandos in den Core rutschen
- Telegram-/Netzwerk-Aktivierung still mitgedacht wird
- Socket-Mount zur Pflicht wird
- Test- oder Runbook-Evidence erfunden wird
- harte Runtime-No-Go-Grenzen weichgespuelt werden

## Blocked Runtime Actions

Die Section `blocked_runtime_actions` muss die weiterhin blockierten Aktionen kompakt zeigen.

Mindestens:

- keine Host-Kommandos aus Odysseus-Core
- keine Telegram-Tokens
- keine Webhook- oder Polling-Aktivierung
- keine echte Netzwerk-Runtime
- keine Docker- oder Podman-Socket-Pflicht
- keine automatische Reparatur

Wichtig:

- diese Liste ist Sicherheitsgrenze
- ein gutes Review Packet darf sie nicht relativieren

## Operator Signoff Inputs

Die Section `operator_signoff_inputs` soll die Inputs fuer einen spaeteren manuellen Review- oder Signoff-Schritt benennen.

Mindestens:

- Audit Index
- Readiness Score
- Security/Ops Runbook
- Deployment-Prerequisite-Liste
- relevante Test- und Nachtest-Referenzen
- bekannte Folge-Gates

Wichtig:

- das Packet liefert Inputs, keinen Signoff selbst
- es gibt keine automatische Freigabe

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
- keine implizite Runtime-Freigabe

## Decision States

Das spaetere Review Packet soll mindestens diese Decision States kennen:

- `review_ready`
- `blocked`
- `needs_operator_input`
- `deferred`

## Bedeutung der Decision States

### `review_ready`

Die Foundation-Artefakte sind konsistent genug vorbereitet, dass ein menschlicher Review-Schritt sinnvoll starten kann.

Wichtig:

- kein Deployment-Go
- kein Host-Agent-Go
- kein Delivery-Go

### `blocked`

Mindestens eine harte Sicherheits- oder Architekturgrenze ist verletzt oder unklar.

### `needs_operator_input`

Ein Operator muss bewusst entscheiden, interpretieren oder fehlende Evidenz bewerten.

### `deferred`

Die weitere Bewertung oder Aktivierung ist bewusst vertagt und bleibt ausserhalb dieses Foundation-Slices.

## Test- und Runbook-Referenzen

Tests und Runbooks duerfen im Packet referenziert werden, aber nur konservativ.

Zulaessig:

- Testdatei- oder Testgruppen-Referenzen
- Nachtest-Hinweise aus frueheren SHC-Slices
- Verweise auf Runbooks, Ops-Readiness oder Dashboard-Contracts

Nicht zulaessig:

- erfundene frische Ergebnisse
- neue Testausfuehrungen aus dem Packet
- rohe Logs oder komplette Testausgaben

## No-Secrets und No-Raw-Logs

Das Review Packet darf nicht enthalten:

- Secrets
- echte Telegram-Tokens
- rohe Logs
- komplette Host-Ausgaben
- komplette Netzwerk- oder Webhook-Payloads

Zulaessig sind:

- kompakte Decision-Labels
- kurze Boundary- und No-Go-Hinweise
- kurze Test- und Runbook-Referenzen
- kurze offene Fragen

## Beispiel fuer spaeteres sicheres Review Packet

Zulaessig:

- `packet_purpose = manual review before any host-agent gate`
- `included_artifacts = audit index, readiness score, ops runbook`
- `review_order = foundation -> audit -> score -> runbook`
- `go_no_go_questions = boundaries intact, no tokens, no socket requirement`
- `blocked_runtime_actions = host commands, telegram delivery, network runtime`
- `operator_signoff_inputs = see referenced contracts and tests`

Nicht zulaessig:

- `deploy_now = true`
- `start host agent`
- `enable telegram push`
- kompletter Host- oder Testlogdump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein reines Review-Packet- oder Summary-Modell ueber vorhandene SHC-Foundation-Artefakte bauen.

Zulaessige Inputs:

- `SystemHealthPluginAuditIndex`
- `SystemHealthPluginReadinessScore`
- `SystemHealthPluginFoundationIndex`
- `SystemHealthSecurityOpsRunbook`
- Dashboard- oder Boundary-Statussichten

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

Er legt nur fest, wie ein spaeteres operatorfreundliches Review Packet die vorhandenen SHC-Foundation-Artefakte, Readiness-Signale und harten Runtime-No-Go-Grenzen in eine konservative manuelle Entscheidungsgrundlage ueberfuehren soll.
