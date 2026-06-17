# Agent Profile Foundation Roadmap

Stand: 2026-06-17

Status: **APF0 gestartet; Ziel ist ein wartbarer Agent-Profile-Layer fuer Main-Agent, UI und spaetere Automatisierung**

## Goal

Odysseus soll Agenten wie Alice, Bob, Charlie und spaetere Subagents ueber ein gemeinsames Profilmodell beschreiben koennen, sodass Main Agent, UI, Toolauswahl, Timer und Handoffs dieselbe Quelle fuer Rollen, Staerken, Grenzen und Overrides nutzen.

## Current Evidence

- `core.database.CrewMember` speichert bereits persistierte Persona-/Assistant-Daten wie Name, Personality/Systemprompt, Modell, Memory Scope, Enabled Tools und Session-Link.
- `src/agent_identity.py` normalisiert Agent-, Rollen-, Memory-, Workspace- und Run-Identitaet.
- `src/context_capsule.py` beschreibt begrenzte Arbeitskapseln mit Ziel, erlaubten Dateien, Tests, Stop-Regeln und Handoff-Format.
- `src/tool_catalog.py` kann Tool-Sichtbarkeit ueber Rolle, Scope, Risiko und Approval bestimmen.
- `src/preset_manager.py` stellt Presets, User Templates und Group Presets bereit.
- `src/delegate_tool.py` unterstuetzt Hidden-Worker-Delegation als fokussierten Tool-Schritt.
- `static/js/assistant.js` hat bereits Assistant Settings fuer Name, Persona, Modell, Tools und Check-ins.

## Non-goals

- Kein neues Agentensystem neben `CrewMember`, Presets, `AgentIdentity`, Context Capsules und Tool Catalog.
- Keine freie autonome Agentenfabrik.
- Keine Live-Runtime-Aktivierung, kein echter Scheduler-Start und keine echten Thread-Sends aus Odysseus-Runtime.
- Keine Plugin-, Telegram-, Nextcloud-, Obsidian-, Graph-, RAPTOR- oder Provider-Arbeit in diesem Track.
- Kein grosses UI-Redesign im ersten Slice.

## Foundation Principles

- `AgentProfile` ist ein verbindender Layer, nicht die neue Primaerwahrheit fuer alles.
- Persistierte Persona-/Session-Daten bleiben bei `CrewMember`.
- Rollen-/Prompt-Vorlagen bleiben bei Presets/Templates.
- Sichere technische Identitaet bleibt `AgentIdentity`.
- Konkreter Arbeitsscope bleibt `ContextCapsule`.
- Tool-Sichtbarkeit bleibt `ToolCatalog`.
- Der Main Agent bekommt eine kompakte Teamkarte statt roher Datenbank- oder UI-Strukturen.
- UI darf Profile anzeigen und Overrides setzen, aber Safety Policy gewinnt immer gegen Overrides.

## Proposed AgentProfile Fields

- `agent_id`: stabile technische Agent-ID.
- `display_name`: sichtbarer Name, z. B. `Alice`.
- `role_preset_id`: Standardrolle, z. B. `docs-runbook`.
- `persona_preset_id`: optionaler Preset-/Template-Verweis fuer Systemprompt-Persoenlichkeit.
- `strengths`: kurze Chips fuer Anzeige und Routing, z. B. `docs`, `tests`, `release`.
- `best_for`: knappe Nutzersprache, wofuer der Agent besonders geeignet ist.
- `avoid_for`: knappe Grenzen, wofuer der Agent nicht automatisch genutzt werden soll.
- `default_tools`: bevorzugte Tools oder Tool-Capabilities.
- `allowed_actions`: z. B. `docs_edit`, `test_run`, `commit_prepare`, `handoff`.
- `safety_rules`: nicht ueberschreibbare Stop- und Boundary-Regeln.
- `timer_policy`: optionaler Timer-/Watch-Modus fuer sichtbare Agenten.
- `hidden_worker_policy`: ob und wie der Agent interne Hidden Worker nutzen darf.
- `reports_to`: Parent-Chat oder Parent-Agent.
- `visibility`: `main`, `visible_subagent`, `hidden_worker`.
- `overrides`: explizite, auditierbare Abweichungen vom Preset.

## Main-Agent Team Card

Der Main Agent soll fuer aktive Chats eine kompakte Teamkarte erhalten:

```text
Team:
- Alice: docs-runbook; strong at operator text, runbooks, Go/No-Go language; docs-only by default.
- Bob: code-test-audit; strong at implementation, tests, legacy checks; scoped files only.
- Charlie: coordinator-release; strong at scope, git, integration, automation, release gates.

Rules:
- Main agent orchestrates by default.
- User can override with simple commands.
- Visible subagents report to their parent chat.
- Hidden workers appear only as work steps and cannot commit, push, persist secrets or bypass safety.
- Overrides can change model/tools/timer, but safety rules win.
```

## UI Shape

- Chatliste zeigt sichtbare Subagents unter dem Parent-Chat.
- Jeder sichtbare Agent zeigt wenige Staerken-Chips, z. B. `Docs`, `Tests`, `Release`.
- Eine Uhr am Agenten zeigt aktive Timer oder Watches.
- Klick auf Agent oeffnet Profil-Overlay mit Rolle, Staerken, Grenzen, Tools, Overrides, Timer und Parent.
- Hidden Worker erscheinen nicht in der Chatliste, sondern als collapsible Work-Step im Chatverlauf.
- `/team` zeigt die Teamkarte.
- `/alice strengths`, `/bob preset code-audit`, `/charlie timer 1h` bleiben einfache Befehle.

## Slices

### APF0-roadmap

Charlie erstellt diese Roadmap, prueft vorhandene Fundamente und startet den ABC-Track.

### APF1-agent-profile-ux-contract

Alice beschreibt Nutzer- und Operator-Vertrag fuer Agent-Profile, Staerkenanzeige, Overrides, Parent/Child-Sichtbarkeit, Timer-Uhr und Hidden-Worker-Darstellung.

Erlaubter Scope:

- `docs/plans/agent-profile-ux-contract.md`
- optional kurzer Link in `docs/plans/agent-profile-foundation-roadmap.md`

Primaerer Alice-Contract:
- `docs/plans/agent-profile-ux-contract.md`

### APF2-agent-profile-model

Bob baut ein kleines runtime-agnostisches Profilmodell plus Tests. Kein DB-Write, keine UI, keine echten Agent-Sends.

Erlaubter Scope:

- `src/agent_profile.py`
- `tests/test_agent_profile.py`

### APF3-main-agent-team-card

Bob oder Charlie baut aus AgentProfile-Eingaben eine kompakte Teamkarte fuer den Main-Agent-Systemkontext. Keine LLM-Calls, keine Runtime-Aktivierung.

Voraussichtlicher Scope:

- `src/agent_team_card.py`
- `tests/test_agent_team_card.py`

### APF4-profile-registry-plan

Alice/Charlie definieren, wie Profile spaeter aus `CrewMember`, Presets, `AgentIdentity`, Tool Catalog und Overrides zusammengesetzt werden. Erst Plan/Contract, noch keine Migration.

### APF5-ui-readonly-profile-surface

Optionaler spaeterer UI-Slice: read-only Profilanzeige in Chatliste/Overlay. Nur wenn APF1-APF3 stabil sind.

## Verification

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_profile.py`
- Spaeter: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_team_card.py`
- Keine Live-Agenten-Sends, keine Scheduler-Aktivierung und keine Netzwerkaktionen als Teil der Verifikation.

## Go / Partial / No-Go

- `Go`: APF1 und APF2 sind dokumentiert/getestet, Main-Agent-Kontext kann aus Profilen sicher abgeleitet werden, Safety-Regeln sind nicht per Override aushebelbar.
- `Partial`: Profile sind modelliert, aber Teamkarte oder UI-Anzeige fehlt noch.
- `No-Go`: Profile duplizieren bestehende Primaerwahrheiten, Overrides koennen Safety-Regeln umgehen, oder Hidden Worker werden sichtbar/aktiviert ohne Boundary.
- `Deferred`: UI-Overlay, echte Timer-Editierung und dynamische Hidden-Worker-Ausfuehrung bleiben nach APF3 eigene Tracks.

## Stop Rules

- Stop bei fremden staged files oder Hotfile-Konflikt.
- Stop bei Secrets, Token, Chat-IDs oder privaten Provider-Ausgaben.
- Stop bei Plugin-, Telegram-, Nextcloud-, Obsidian-, Graph-, RAPTOR- oder Provider-Scope.
- Stop bei echter Runtime-Aktivierung, Scheduler-Start, Thread-Send oder Netzwerkaktion.
- Stop bei destruktiven Git-Kommandos oder nicht offensichtlicher Architekturentscheidung.
