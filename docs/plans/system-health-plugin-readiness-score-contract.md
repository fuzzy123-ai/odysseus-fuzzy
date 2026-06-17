# System Health Plugin Readiness Score Contract

Stand: 2026-06-17

Status: **SHC12A Docs-Contract fuer einen System Health Plugin Readiness Score / Go-No-Go Summary**

Quellen:

- `docs/plans/system-health-plugin-audit-index-contract.md`
- `docs/plans/system-health-plugin-foundation-index.md`
- `docs/plans/system-health-security-ops-runbook.md`
- `docs/plans/system-health-dashboard-contract.md`
- `docs/plans/system-health-container-runtime-adapter-contract.md`
- `docs/plans/system-health-plugin-operator-review-packet-contract.md`

Dieser Contract definiert einen operatorfreundlichen Readiness Score und eine Go-No-Go-Zusammenfassung fuer die System Health Plugin Foundation. Der Score verdichtet Foundation-Vollstaendigkeit, Host-Grenzen, Audit-Abdeckung, Operator-Dokumentation, Runtime-No-Go-Integritaet und Deployment-Voraussetzungen zu einer konservativen Review-Ampel. Der Slice aktiviert keinen Host-Agenten, keine Telegram-Delivery, keine Netzwerk- oder Webhook-Runtime und keine Container-Socket-Integration.

## Ziel

Der System Health Checker braucht nach SHC11 eine klare Review-Ampel, damit Operatoren nicht aus vielen einzelnen Contracts erraten muessen, wie weit die Foundation vorbereitet ist.

Der Readiness Score soll beantworten:

- wie vollstaendig die SHC-Foundation dokumentiert und modelliert ist
- ob die Host-Grenzen und Core-Grenzen sicher eingehalten werden
- wie gut Audit-, Test- und Runbook-Abdeckung sichtbar sind
- ob die Runtime-No-Go-Grenzen sauber intakt bleiben
- welche Deployment-Voraussetzungen vor einer spaeteren Host-Agent-Integration noch geprueft werden muessen

## Leitregel

Readiness Score bedeutet manuelle Review-Reife, nicht Host-Agent- oder Delivery-Aktivierung.

Das bedeutet:

- eine gruene oder fast gruene Foundation-Ampel ist kein Go fuer Host-Kommandos
- der Score darf keine Telegram-, Netzwerk- oder Socket-Aktivierung implizieren
- deaktivierte Runtime-Aktionen muessen trotz guter Foundation-Sicht explizit sichtbar bleiben

## Architekturgrenzen

Die folgenden Grenzen bleiben fest:

- keine Host-Kommandos aus Odysseus-Core
- keine Telegram-Tokens im Repo, in Logs oder in Modellartefakten
- keine Netzwerk-/Webhook-Aktivierung
- keine Podman- oder Docker-Socket-Pflicht

Wichtig:

- Podman-first, Docker-compatible bleibt die feste Architekturleitplanke
- rootless Podman bleibt positiv mitgedacht
- Docker bleibt nur kompatibler Fallback

## Score Dimensions

Der spaetere Readiness Score soll mindestens diese Dimensionen kennen:

- `foundation_completeness`
- `host_boundary_safety`
- `audit_coverage`
- `operator_docs`
- `runtime_no_go_integrity`
- `deployment_prerequisites`

## Bedeutung der Score Dimensions

### `foundation_completeness`

Bewertet, ob die benoetigten SHC-Foundation-Artefakte vorhanden und konsistent beschrieben sind.

Typische Inputs:

- Plugin Foundation Index
- Audit Index
- Dashboard Contract
- Security/Ops Runbook

### `host_boundary_safety`

Bewertet, ob die Trennung zwischen Host-Agent und Odysseus-Core sauber bleibt.

Typische Inputs:

- keine Host-Kommandos im Core
- keine Socket-Pflicht
- Host-Agent-only Collectors
- Unknown/Unsupported statt Crash

### `audit_coverage`

Bewertet, ob Tests, Nachtests, Runbooks und Audit-Referenzen ausreichend auffindbar dokumentiert sind.

Wichtig:

- nur referenzierte Abdeckung
- keine erfundenen frischen Ergebnisse

### `operator_docs`

Bewertet, ob Operatoren die noetigen Contracts, Runbooks und Checklisten als lesbare Review-Kette vorfinden.

### `runtime_no_go_integrity`

Bewertet, ob die bewusst blockierten Runtime-Aktionen klar und widerspruchsfrei als No-Go markiert bleiben.

### `deployment_prerequisites`

Bewertet, ob die Voraussetzungen fuer eine spaetere echte Host-Agent-Integration als Gate-Vorbedingungen sichtbar sind.

## Decision States

Der spaetere Score oder die spaetere Ampel soll mindestens diese Decision States kennen:

- `ready_for_manual_review`
- `blocked`
- `review_required`
- `deferred`

## Bedeutung der Decision States

### `ready_for_manual_review`

Die Foundation ist konsistent genug vorbereitet, dass ein Operator einen manuellen Review-Schritt starten kann.

Wichtig:

- kein Host-Agent-Go
- keine Delivery-Freigabe
- kein Start von Host-, Netzwerk- oder Telegram-Runtime

### `blocked`

Mindestens eine harte Sicherheits- oder Architekturgrenze ist verletzt oder unklar.

### `review_required`

Die Foundation ist vorbereitet, aber Operator- oder Charlie-Pruefung ist noch explizit noetig.

### `deferred`

Die weitere Bewertung oder Aktivierung ist bewusst vertagt und bleibt ausserhalb dieses Foundation-Slices.

## Go/No-Go Summary

Die `go_no_go_summary` soll die manuelle Prueflogik fuer Operatoren knapp zusammenfassen.

Vor einer spaeteren Host-Agent-Integration muss ein Operator mindestens pruefen:

- Foundation-Artefakte sind vorhanden und konsistent
- Host-Grenzen bleiben intakt
- Audit-Index und Runbooks sind lesbar und ausreichend
- Runtime-No-Go-Liste ist klar und nicht weichgespuelt
- Deployment-Voraussetzungen sind benannt
- offene Folge-Gates sind noch getrennt von der Foundation

No-Go bleibt bestehen, wenn:

- Host-Kommandos in den Core rutschen
- Telegram-Tokens oder Netzwerkaktivierung still mitgedacht werden
- Socket-Mount als Pflicht auftaucht
- Tests als frisch gruen behauptet werden, ohne echte neue Evidence
- Security/Ops-Runbook oder Audit-Index Luecken offenlassen

## Test- und Runbook-Referenzen

Tests und Runbooks duerfen im Score referenziert werden, aber nur konservativ.

Zulaessig:

- Testdatei- oder Testgruppen-Referenzen
- Nachtest-Hinweise aus frueheren SHC-Slices
- Verweise auf Runbooks, Ops-Checklisten oder Dashboard-Contracts

Nicht zulaessig:

- erfundene frische Ergebnisse
- neue Testausfuehrung durch den Score
- rohe Logs oder komplette Testausgaben

## Deployment Prerequisites

Die Deployment-Voraussetzungen duerfen nur als Gate-Vorbedingungen beschrieben werden.

Mindestens:

- separater Host-Agent
- minimale Rechte fuer spaetere Collectors
- saubere Token-Hygiene
- klare Snapshot-Schnittstelle
- Podman-first/Docker-compatible Betriebsrahmen
- Unknown/Unsupported statt Crash

Wichtig:

- diese Voraussetzungen sind kein Deployment-Start
- sie sind nur Review-Inputs fuer spaetere Folge-Slices

## No-Secrets und No-Raw-Logs

Der Readiness Score darf nicht enthalten:

- Secrets
- echte Telegram-Tokens
- rohe Logs
- komplette Host-Ausgaben
- komplette Netzwerk- oder Webhook-Payloads

Zulaessig sind:

- kompakte Score- oder Decision-Labels
- kurze Boundary-Hinweise
- kurze Test- und Runbook-Referenzen
- kurze Blocker- oder Defer-Hinweise

## Beispiel fuer spaeteren sicheren Readiness Score

Zulaessig:

- `foundation_completeness = ready_for_manual_review`
- `host_boundary_safety = review_required`
- `audit_coverage = review_required`
- `runtime_no_go_integrity = ready_for_manual_review`
- `deployment_prerequisites = deferred`
- `go_no_go_summary = manual review possible, runtime still blocked`

Nicht zulaessig:

- `host_agent_ready_to_run = true`
- `telegram_push_can_start = true`
- `mount_socket_now`
- kompletter Host- oder Testlogdump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein reines Readiness-Score- oder Go-No-Go-Modell ueber vorhandene SHC-Foundation-Artefakte bauen.

Zulaessige Inputs:

- `SystemHealthPluginAuditIndex`
- `SystemHealthPluginFoundationIndex`
- `SystemHealthSecurityOpsRunbook`
- Dashboard-, Runtime-Adapter- und Boundary-Statussichten

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

Er legt nur fest, wie ein spaeterer operatorfreundlicher Readiness Score und eine Go-No-Go-Zusammenfassung die vorhandenen System-Health-Foundation-Artefakte, ihre Sicherheitsgrenzen und ihre Review-Voraussetzungen konservativ verdichten sollen.
