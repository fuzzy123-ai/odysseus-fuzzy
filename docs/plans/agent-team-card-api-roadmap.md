# Agent Team Card API Roadmap

Stand: 2026-06-17

Status: **ATC0-ATC3 abgeschlossen; read-only Team-Card-Surface ist fuer Main Agent und spaetere UI bereit**

## Goal

Odysseus soll die aktive Agenten-Teamkarte als sichere, read-only API-Surface bereitstellen, damit Main Agent und spaetere UI dieselben Profil-, Staerken-, Parent- und Hidden-Worker-Informationen nutzen koennen.

## Current Evidence

- `src/agent_profile.py` modelliert Agentenprofile, Sichtbarkeit, Overrides und Safety Rules.
- `src/agent_team_card.py` rendert eine kompakte Main-Agent-Teamkarte und Audit Summary.
- `src/agent_profile_registry.py` loest Default-Rollen wie Alice, Bob und Charlie aus Presets, CrewMember-aehnlichen Inputs und Overrides auf.
- `tests/test_agent_profile.py`, `tests/test_agent_team_card.py` und `tests/test_agent_profile_registry.py` sind gruen.
- `docs/plans/agent-profile-ux-contract.md` beschreibt die erwartete Anzeige von Staerken, Parent/Child-Sichtbarkeit, Overrides und Timer-Hinweisen.
- `docs/plans/agent-team-card-api-contract.md` beschreibt den read-only Operator-/Nutzervertrag fuer die Team-Card-Surface.
- `src/agent_team_card_api.py` baut eine leak-freie Payload aus der Default-Registry.
- `routes/agent_team_routes.py` stellt `GET /api/agents/team-card` admin-gated und read-only bereit.

## Non-goals

- Kein neues Agentensystem neben CrewMember, Presets, AgentProfile, TeamCard und Registry.
- Keine echten Agentenstarts, Thread-Sends, Scheduler-Aktivierung oder Hidden-Worker-Ausfuehrung.
- Keine Plugin-, Telegram-, Nextcloud-, Obsidian-, Graph-, RAPTOR- oder Provider-Arbeit.
- Kein grosses UI-Redesign und keine Chatlisten-Reorganisation in diesem Track.
- Keine Secrets, Token, Chat-IDs oder privaten Runtime-Daten in API, Docs, Tests oder Logs.

## Stop Rules

- Stop bei fremden staged files oder Hotfile-Konflikt.
- Stop bei Secrets, Token, Chat-IDs oder privaten Provider-Ausgaben.
- Stop bei Runtime-Aktivierung, Scheduler-Start, Thread-Send, Netzwerkaktion oder Live-Agentenaktion.
- Stop bei Plugin-, Telegram-, Nextcloud-, Obsidian-, Graph-, RAPTOR- oder Provider-Scope.
- Stop bei destruktiven Git-Kommandos oder nicht offensichtlicher Architekturentscheidung.

## Slices

### ATC0-roadmap

Charlie erstellt diese Roadmap, prueft bestehende Profil-/Team-Fundamente und startet den ABC-Track.

### ATC1-agent-team-card-api-contract

Alice beschreibt den Nutzer-/Operator-Vertrag fuer die read-only Team-Card-Surface.

Erlaubter Scope:

- `docs/plans/agent-team-card-api-contract.md`
- optional kurzer Link in `docs/plans/agent-team-card-api-roadmap.md`

Primaerer Alice-Contract:
- `docs/plans/agent-team-card-api-contract.md`

Anforderungen:

- Erklaere, welche Daten sichtbar werden duerfen: Agent-ID, Name, Rolle, Staerken-Chips, Best-for, Avoid-for, Parent, Sichtbarkeit, Timer-Hinweis, Safety Summary.
- Erklaere, welche Daten nicht sichtbar werden duerfen: Secrets, Tokens, Chat-IDs, private Memory-Inhalte, rohe Runtime-/Provider-Daten.
- Beschreibe, dass die Surface read-only ist und keine Agenten startet.
- Beschreibe die Beziehung zu spaeterer UI: Teamkarte kann in Chat-Overlay, `/team` oder Agent-Details genutzt werden.

### ATC2-agent-team-card-api-model

Bob baut ein kleines runtime-agnostisches Payload-Modell plus Tests.

Erlaubter Scope:

- `src/agent_team_card_api.py`
- `tests/test_agent_team_card_api.py`

Anforderungen:

- Nutze `build_default_agent_profile_registry()` als Quelle fuer Default-Teamdaten.
- Gib ein leak-freies Dict fuer API/UI zurueck: `team`, `rules`, `audit`, `prompt_text`.
- Begrenze Textlaengen und Agentenanzahl ueber bestehende TeamCard/AgentProfile-Regeln.
- Keine DB-Reads, keine Runtime-Aktivierung, keine Thread-Sends, keine Scheduler-Aktion.
- Tests pruefen Default-Team, Hidden-Worker-Reduktion, Parent-Informationen und dass keine Secret-Muster durchgereicht werden.

### ATC3-agent-team-card-admin-route

Charlie oder Bob integriert nach ATC2 eine kleine read-only Route, falls ATC2 stabil ist.

Voraussichtlicher Scope:

- `routes/agent_team_routes.py`
- `tests/test_agent_team_routes.py`
- optional minimale Router-Registrierung, nur falls klar lokalisierbar.

Anforderungen:

- `GET /api/agents/team-card` oder vergleichbarer vorhandener Prefix.
- Read-only und admin-gated, falls die bestehenden Route-Konventionen das nahelegen.
- Keine Agentenstarts, keine Writes, keine Secrets, keine Chat-IDs.
- Tests pruefen Gate, Payload-Shape und leak-freie Ausgabe.

### ATC4-ui-readonly-hook

Status: **Deferred**. Optionaler P2-Slice: nur ein sehr kleiner UI-Link oder Read-only Fetch-Hook, falls Route und Payload stabil sind und der Nutzer UI-Arbeit explizit priorisiert.

## Verification

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_profile.py tests\test_agent_team_card.py tests\test_agent_profile_registry.py`
- Nach ATC2: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_team_card_api.py`
- Nach ATC3: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_team_card_api.py tests\test_agent_team_routes.py`
- Abschlusslauf: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_team_routes.py tests\test_agent_team_card_api.py tests\test_agent_profile.py tests\test_agent_team_card.py tests\test_agent_profile_registry.py` -> `25 passed, 1 warning`

## Go / Partial / No-Go

- `Go`: Team-Card-Payload ist read-only, leak-frei, getestet und fuer Main Agent/UI konsumierbar.
- `Partial`: Modell/Payload ist fertig, Route oder UI-Hook ist bewusst deferred.
- `No-Go`: Payload leakt Secrets/IDs/Memory-Inhalte, startet Runtime-Aktionen oder dupliziert bestehende Primaerwahrheiten.
- `Deferred`: echte Chatlisten-Unterordnung, Timer-Overlay-Editierung und dynamische Hidden-Worker-Ausfuehrung bleiben eigene Tracks.
