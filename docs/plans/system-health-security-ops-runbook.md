# System Health Security and Ops Runbook

Stand: 2026-06-17

Status: **SHC9A Security-/Ops-Runbook-Contract fuer den System Health Checker**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-basic-collectors-contract.md`
- `docs/plans/system-health-advanced-debian-collectors-contract.md`
- `docs/plans/system-health-rule-engine-alert-contract.md`
- `docs/plans/system-health-telegram-pull-status-contract.md`
- `docs/plans/system-health-auto-alerting-contract.md`
- `docs/plans/system-health-container-runtime-adapter-contract.md`
- `docs/plans/system-health-dashboard-contract.md`
- `docs/plans/system-health-plugin-foundation-index.md`

Dieses Runbook beschreibt den sicheren Betriebsrahmen fuer den spaeteren System Health Checker auf einem Debian-Homeserver. Es bleibt bewusst contract-only: keine echte Host-Agent-Ausfuehrung, keine Telegram-Tokens, keine Netzwerkaktionen und keine Runtime-Integration in diesem Slice.

## Zielbild

Odysseus bleibt vom Host getrennt:

- der Host-Agent sammelt
- Odysseus konsumiert nur bereinigte Snapshots
- Security-Grenzen bleiben sichtbar
- Unknown- und Unsupported-Zustaende werden ruhig statt fragil behandelt

## Install- und Betriebsnarrativ

Der spaetere sichere Betriebsfluss lautet:

1. Ein kleiner Debian Host-Agent sammelt Health-Daten auf dem Host.
2. Der Host-Agent normalisiert diese Daten in bereinigte Snapshots.
3. Odysseus liest nur die Snapshot-Semantik, nicht die Host-Kommandos selbst.
4. UI, Pull-Status und spaetere Alert-Entscheidungen arbeiten nur gegen diese Snapshots.

Wichtig:

- der Host-Agent ist die einzige Schicht mit spaeterem Host-Zugriff
- Odysseus-Core bleibt read-only gegen den Health-Zustand

## Sicherheitsgrenzen

Die harten Security Boundaries fuer diesen Track sind:

- kein Host-Zugriff aus dem Odysseus-Core
- keine Docker- oder Podman-Socket-Pflicht
- Tokens nie loggen

## Keine Host-Kommandos aus Odysseus

Odysseus-Core und Container duerfen nicht:

- `sensors`
- `smartctl`
- apt simulation
- `/proc`-Reads
- Runtime-CLI-Aufrufe

selbst ausfuehren.

Diese Verantwortung bleibt spaeter ausschliesslich beim Host-Agenten.

## Keine Socket-Pflicht

Der Track bleibt:

- Podman-first
- Docker-compatible

aber ohne Pflicht, einen Docker- oder Podman-Socket in Odysseus zu mounten.

Das bedeutet:

- kein `/var/run/docker.sock` als Default
- kein Podman-Socket-Zwang
- keine Core-Abhaengigkeit von Host-Runtime-Sockets

## Token-Sicherheit

Telegram- oder andere Tokens duerfen nicht:

- im Repo landen
- in Logs auftauchen
- in UI oder Snapshots auftauchen
- in Contracts als Klartextwert auftauchen

## Minimalrechte

Der spaetere Host-Agent soll nur minimale, gezielte Rechte erhalten.

Grundregel:

- so wenig Rechte wie moeglich
- niemals breite Root-Rechte fuer den Odysseus-Core

## Rechte fuer Advanced Collectors

Wenn spaeter benoetigt:

- `smartctl`
- `sensors`
- apt simulation

dann nur Host-Agent-seitig und nur mit minimalen Rechten.

Wenn diese Rechte oder Tools fehlen, gilt:

- `unknown` oder `unsupported`
- `setup_hint`
- nie Crash

## Unknown und Unsupported als Sicherheitsventil

Wenn eine Quelle fehlt, ein Paket nicht installiert ist oder Rechte nicht ausreichen, muss das System konservativ bleiben:

- `unknown` statt falschem `ok`
- `unsupported` statt stiller Behauptung
- `setup_hint` statt Stacktrace

Das Runbook verlangt ausdruecklich:

- keine Umgehung ueber mehr Rechte
- keine ad-hoc Root-Abkuerzung

## Go/No-Go Checkliste fuer MVP

Go ist spaeter erst denkbar, wenn mindestens diese Punkte erfuellt sind:

- HealthSnapshot-Interface ist stabil
- Basic Collectors sind mockbar und Unknown-sicher modelliert
- Advanced Collectors sind fixture-basiert beschrieben und rechteneutral fallbacksicher
- Rule Engine trennt warn/critical/unknown konservativ
- Telegram Pull bleibt allowlist-basiert und tokenfrei im Modell
- Auto-Alerting bleibt Decision-only und cooldown-/dedupe-sicher
- Container Runtime bleibt Podman-first und ohne Socket-Pflicht
- Odysseus-Core fuehrt keine Host-Kommandos aus

## No-Go Checkliste fuer MVP

No-Go bleibt bestehen, wenn einer dieser Punkte zutrifft:

- Host-Zugriff wird in den Odysseus-Core gezogen
- Docker- oder Podman-Socket wird als Pflicht vorgeschlagen
- Tokens tauchen in Repo, Logs oder Responses auf
- Root-Rechte werden pauschal statt minimal geplant
- Unknown/Unsupported fuehrt zu Crash oder irrefuehrendem `ok`
- Auto-Alerting tut so, als wuerde es schon Telegram-Push ausliefern

## Known Limits

Die Foundation dieses Tracks behauptet bewusst nicht mehr als vorbereitet ist.

Bekannte Grenzen:

- keine Reparatur
- keine vollstaendige SMART-Abdeckung
- keine echte Push-Ausfuehrung in der Foundation

## Keine Reparatur

Der System Health Checker:

- beobachtet
- bewertet
- empfiehlt

aber repariert nichts automatisch.

## Keine vollstaendige SMART-Abdeckung

SMART/NVMe bleibt spaeter:

- hardwareabhaengig
- rechteabhaengig
- host-agent-abhaengig

Fehlende SMART-Sicht bleibt ein sauberer Datenzustand, keine verdeckte Zusage.

## Keine echte Push-Ausfuehrung in der Foundation

Telegram Pull, Auto-Alerting und Queue-Semantik sind vorbereitet, aber:

- keine echte Push-Auslieferung
- kein Token-Handling
- kein Polling/Webhook-Lauf

## Podman-first Betriebsregel

Podman-first bedeutet im Betrieb:

- rootless Podman soll positiv mitgedacht werden
- Docker bleibt kompatibler Fallback
- keine Docker-only Architektur
- keine Socket-Kurzschluesse in Odysseus

## Systemd- und Service-Haltung

Fuer den spaeteren Host-Agenten gilt als Betriebsnarrativ:

- eigener Dienst
- eigene minimale Rechte
- klare Restart-Strategie
- keine Secrets in Logs
- lokale, kontrollierte Bereitstellung der Snapshot-Daten

Dieses Runbook liefert bewusst keine fertige Service-Datei als ausfuehrbares Rezept.

## Betriebsregeln fuer Alerts und Telegram

Selbst wenn spaeter Pull oder Push folgen, bleiben diese Regeln bestehen:

- Allowlist zuerst
- keine Tokens im Repo
- keine Secrets in Responses
- keine Panik-Sprache
- Recovery nur bei echter Entwarnung

## Operator-Sicht

Operatoren sollen spaeter schnell verstehen koennen:

- was der Host-Agent tut
- was Odysseus bewusst nicht tut
- welche Rechte minimal noetig sind
- warum `unknown` oder `unsupported` kein Fehler im Design, sondern eine Sicherheitsgrenze sein kann

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf hoechstens ein kleines Readiness- oder Checklist-Modell fuer den Runbook-Status bauen.

Wichtig:

- keine IO
- keine Host-Kommandos
- keine Netzwerkaktionen
- keine Token-Integration
- keine Runtime-Ausfuehrung

## Beispiel fuer spaeteren sicheren Readiness-Status

Zulaessig:

- `host_agent_boundary: ok`
- `socket_free_core: ok`
- `advanced_collectors_rights: unknown`
- `telegram_push_runtime: not_enabled`
- `mvp_go_no_go: no_go`

Nicht zulaessig:

- `run host checks now`
- `install packages automatically`
- `start telegram bot`
- `mount docker socket`

## Abschlussregel

Der Track bleibt nur dann sicher, wenn Odysseus bei fehlendem Host-Agent, fehlenden Rechten oder fehlenden Abhaengigkeiten ruhig bleibt und den Zustand verstaendlich als `unknown`, `unsupported` oder `no_go` darstellt, statt versteckte Host-Ausfuehrung oder ueberzogene Gesundheitsversprechen einzufuehren.
