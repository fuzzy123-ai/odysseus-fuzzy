# Agent Automation Foundation Roadmap

Stand: 2026-06-17

Status: **AAF0 gestartet; Ziel ist ein sicheres Timer-/Watch-Fundament fuer Agents, Main Agent und spaetere UI**

## Goal

Odysseus soll Agent-Automationen als klare, validierbare Spezifikation beschreiben koennen, damit Main Agent und spaetere UI Timer, Watches und einmalige Termine anzeigen und vorbereiten koennen, ohne sofort Scheduler-Live-Aktionen auszufuehren.

## Current Evidence

- `docs/plans/agent-profile-ux-contract.md` beschreibt die Timer-Uhr als Sichtbarkeits- und Kontrollmetapher fuer Timer-, Watch- oder geplante Modi.
- `docs/plans/agent-team-card-api-contract.md` erlaubt einen Timer-Hinweis auf der read-only Team-Card-Surface, aber keine Runtime-Steuerung.
- `src/task_scheduler.py` hat bestehende Scheduler-Fundamente wie `compute_next_run()` fuer `once`, `daily`, `weekly`, `monthly` und `cron`.
- `routes/task_routes.py` existiert fuer echte Task-Scheduler-Verwaltung, bleibt in diesem Track aber unangetastet.
- `src/agent_profile.py` enthaelt `timer_policy` als Profilfeld.

## Non-goals

- Keine echte Scheduler-Aktivierung.
- Keine neuen ScheduledTask-DB-Writes.
- Keine echten Thread-Sends, Agentenstarts, Hidden-Worker-Ausfuehrung oder Provider-Aktionen.
- Keine UI-Implementierung, kein Overlay-Frontend und keine Chatlisten-Aenderung in den ersten Slices.
- Keine Plugin-, Telegram-, Nextcloud-, Obsidian-, Graph-, RAPTOR- oder Provider-Arbeit.
- Keine Secrets, Token, Chat-IDs oder privaten Runtime-Daten in Docs, Tests, Prompts oder Payloads.

## Foundation Principles

- Eine Agent-Automation-Spec ist ein Vorschlag oder sichtbarer Zustand, nicht automatisch ein aktiver Scheduler-Job.
- Die KI darf aus Nutzertext eine Spezifikation vorbereiten, aber Live-Persistenz oder Dispatch bleibt operator-gated.
- Timer-Uhr und Overlay zeigen Status, naechsten Lauf und Aenderungswunsch, ohne ungefragt zu schreiben.
- Safety Rules und Parent/Reports-to aus Agent-Profilen bleiben hoeherwertig als Automation-Overrides.

## Supported Schedule Shapes

Vor `1.0` soll das Modell diese Formen ausdruecken koennen:

- `interval`: alle N Minuten, Stunden oder Tage.
- `once`: einmaliges fixes Datum oder Datum/Uhrzeit.
- `recurring`: wiederkehrender Termin mit vorhandenen Scheduler-Namen wie daily, weekly, monthly oder cron.
- `watch`: kein harter Timer, sondern ein beobachtender Modus, der nur Status/Handoff vorbereitet.
- `manual`: keine aktive Automation, aber sichtbarer manueller Agent.

## Slices

### AAF0-roadmap

Charlie erstellt diese Roadmap, prueft bestehende Scheduler-/Agent-Fundamente und startet den ABC-Track.

### AAF1-agent-automation-ux-contract

Alice beschreibt den Nutzer-/Operator-Vertrag fuer Timer-Uhr, Overlay, einfache Befehle und Live-Gates.

Erlaubter Scope:

- `docs/plans/agent-automation-ux-contract.md`
- optional kurzer Link in `docs/plans/agent-automation-foundation-roadmap.md`

Primaerer Alice-Contract:
- `docs/plans/agent-automation-ux-contract.md`

Anforderungen:

- Beschreibe einfache Befehle wie `Charlie, stell einen Watch alle 30 Minuten`, ohne `/agent`-Pflicht.
- Beschreibe Overlay-Felder: Modus, Intervall, Einheit, naechster Lauf, Zeitzone, Status, Parent-Agent, letzter Handoff, Aenderung fuer naechsten Intervall.
- Beschreibe Go/Partial/No-Go fuer Vorbereitung, Anzeige und echte Aktivierung.
- Stelle klar: keine Live-Automation ohne ausdrueckliches Nutzer-Go.

### AAF2-agent-automation-spec-model

Bob baut ein runtime-agnostisches Spec-/Payload-Modell plus Tests.

Erlaubter Scope:

- `src/agent_automation_spec.py`
- `tests/test_agent_automation_spec.py`

Anforderungen:

- Modelliert `AgentAutomationSpec`, `AgentAutomationMode`, `AgentAutomationStatus`, `AgentAutomationUnit`, optional `AgentAutomationOverlay`.
- Unterstuetzt Minuten, Stunden, Tage, fixes Datum und wiederkehrende Scheduler-Formen.
- Normalisiert und validiert Eingaben streng, ohne Scheduler zu starten.
- Liefert eine JSON-kompatible Overlay-Payload fuer UI/Main Agent.
- Redigiert offensichtliche Secret-/Chat-ID-Muster in user-facing Textfeldern.
- Tests pruefen gueltige Modi, ungueltige Intervalle, fixed dates, recurring Formen, watch/manual und leak-freie Payload.

### AAF3-agent-team-card-timer-hint

Charlie oder Bob integriert nach AAF2 einen kleinen read-only Anschluss an die Team-Card-Payload, falls sauber lokalisierbar.

Voraussichtlicher Scope:

- `src/agent_team_card_api.py`
- `tests/test_agent_team_card_api.py`

Anforderungen:

- Team-Card-Agenten duerfen einen neutralen Timer-Hinweis aus einer vorbereiteten Spec anzeigen.
- Keine Scheduler-Writes, keine Route-Mutation, keine Runtime-Aktion.
- Falls der Anschluss nicht klein bleibt, wird AAF3 deferred.

### AAF4-agent-automation-admin-route

Optionaler P1/P2-Slice: read-only Preview-Route fuer Automation-Specs. Nur falls AAF2/AAF3 stabil sind und der Nutzer API-Arbeit priorisiert.

## Verification

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_automation_spec.py`
- Nach AAF3: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_automation_spec.py tests\test_agent_team_card_api.py tests\test_agent_profile.py tests\test_agent_team_card.py tests\test_agent_profile_registry.py`

## Go / Partial / No-Go

- `Go`: Timer-/Watch-Spec ist modelliert, validiert, leak-frei und als read-only Overlay-Payload nutzbar.
- `Partial`: UX-Vertrag und Spec-Modell stehen, Team-Card-Anschluss oder Route ist bewusst deferred.
- `No-Go`: Modell startet Scheduler, schreibt Tasks, sendet Threads, leakt Secrets oder impliziert Live-Automation ohne Nutzer-Go.
- `Deferred`: echtes Erstellen/Aendern von Automationen, UI-Overlay-Implementierung und Chatlisten-Uhr bleiben eigene Tracks.

## Stop Rules

- Stop bei fremden staged files oder Hotfile-Konflikt.
- Stop bei Secrets, Token, Chat-IDs oder privaten Provider-/Runtime-Daten.
- Stop bei Scheduler-Start, ScheduledTask-DB-Write, Thread-Send, Agentenstart oder Netzwerkaktion.
- Stop bei Plugin-, Telegram-, Nextcloud-, Obsidian-, Graph-, RAPTOR- oder Provider-Scope.
- Stop bei destruktiven Git-Kommandos oder nicht offensichtlicher Architekturentscheidung.
