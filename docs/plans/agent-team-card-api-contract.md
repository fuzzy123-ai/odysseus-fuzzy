# Agent Team Card API Contract

Stand: 2026-06-17

Status: backend contract consumed; read-only Agent-Team-Card API surface implemented

Dieses Dokument definiert die read-only Surface fuer Agent-Team-Informationen.
Es baut auf `AgentProfile`, `AgentTeamCard` und `AgentProfileRegistry` auf und
erfindet kein neues Agentensystem.

## Zweck

Die Agent-Team-Card-Surface soll Main Agent und spaetere UI mit denselben
kompakten Team-Informationen versorgen:

- Rolle
- Staerken
- Grenzen
- Parent-/Reports-to-Sicht
- Sichtbarkeit
- Timer-Hinweis
- Safety Summary

Vor `1.0` geht es nur um eine leak-freie, read-only Surface, nicht um
Agentenstarts oder Automation.

## Nicht-Ziele

Dieser Slice deckt bewusst nicht ab:

- kein neues Agentensystem
- keine Agentenstarts
- keine Hidden-Worker-Ausfuehrung
- keine Scheduler-Aktivierung
- keine Thread-Sends
- keine Netzwerk-, Provider-, Telegram-, Plugin- oder UI-Hotfile-Arbeit
- keine Behauptung, dass die Surface Agenten starten, Scheduler schreibt oder
  Hidden Worker ausfuehrt

## Beziehung Zu Bestehenden Fundamenten

Die Team-Card-Surface ist eine abgeleitete Sicht auf bestehende Fundamente:

- `AgentProfile` liefert die semantische Agentbeschreibung
- `AgentTeamCard` liefert die kompakte Teamdarstellung
- `AgentProfileRegistry` liefert die aufgeloesten Rollen und Defaults
- `src/agent_team_card_api.py` erzeugt die read-only Payload fuer Main Agent
  und spaetere UI.
- `routes/agent_team_routes.py` stellt die admin-gated Read-Route bereit.
- Fokussierte Verifikation am 2026-07-03 fuer Profilmodell, Registry,
  Teamkarte, API-Payload und Route:
  `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_agent_team_routes.py tests\test_agent_team_card_api.py tests\test_agent_profile.py tests\test_agent_team_card.py tests\test_agent_profile_registry.py -q`
  returned `27 passed, 2 warnings`.

Wichtig:

- Die Surface ist nicht die Primaerwahrheit.
- Sie ist ein konsumierbares, kompaktes Read-Only-Abbild.
- Main Agent und spaetere UI sollen dieselbe Sicht verwenden, statt mehrere
  leicht unterschiedliche Darstellungen zu bauen.

## Read-Only Surface Boundary

Die Team-Card-Surface ist strikt read-only.

Das bedeutet:

- sie startet keine Agenten
- sie startet keine Hidden Worker
- sie startet keinen Scheduler
- sie sendet keine Threads
- sie fuehrt keine Tools aus
- sie erzeugt keine neuen Agent-Profil-Overrides

Operator-Sprache:

- "read-only team card surface"
- "compact context surface"
- "display and orchestration aid, not runtime control"

Nicht sagen:

- "call this to start Alice"
- "this endpoint dispatches subagents"
- "team card activates timers"

## Sichtbare Daten

Die Surface darf nur die Informationen enthalten, die fuer Nutzer, Main Agent
und spaetere UI sinnvoll und sicher sichtbar sind.

### Pro Agent sichtbar

Zulaessige Felder:

- `agent_id`
- `display_name`
- Rolle oder Preset-Hinweis
- Staerken-Chips
- `best_for`
- `avoid_for`
- Parent oder `reports_to`
- Sichtbarkeit
- Timer-Hinweis
- Safety Summary

### Erklaerung der Felder

#### `agent_id`

Stabile technische Agent-ID fuer Mapping und Anzeige.

#### `display_name`

Sichtbarer Name wie `Alice`, `Bob` oder `Charlie`.

#### Rolle oder Preset-Hinweis

Kurzer lesbarer Hinweis auf den Standardcharakter des Agenten, zum Beispiel
`docs-runbook` oder `code-audit`.

#### Staerken-Chips

Kurze, scanbare Orientierung, zum Beispiel:

- `Docs`
- `Tests`
- `Release`
- `Coordination`

#### `best_for`

Knappe Nutzer-/Operator-Sprache, wofuer der Agent besonders geeignet ist.

#### `avoid_for`

Knappe Sprache, wofuer der Agent nicht automatisch genutzt werden soll.

#### Parent oder `reports_to`

Zeigt, unter welchem Parent-Chat oder Parent-Agent ein sichtbarer Subagent
steht oder wem er berichtet.

#### Sichtbarkeit

Zulaessige Bedeutungen:

- `main`
- `visible_subagent`
- `hidden_worker`

Wichtig:

- `hidden_worker` darf auf dieser Surface nur als Klassifikation oder
  Reduktion erscheinen, nicht als eigene aktive Chat-Entitaet.

#### Timer-Hinweis

Kurzer Hinweis, ob ein Agent einen Timer-, Watch- oder geplanten Modus hat.

Wichtig:

- Der Timer-Hinweis ist keine Aussage, dass Live-Runtime oder Scheduler schon
  aktiv laufen.

#### Safety Summary

Kurze, leak-freie Zusammenfassung von Grenzen oder nicht ueberschreibbaren
Safety Rules.

Beispiele fuer Form:

- `docs-only by default`
- `no live scheduler`
- `policy-limited tools`

## Niemals Sichtbar

Die Surface darf folgende Daten nie sichtbar machen:

- Secrets
- Tokens
- Chat-IDs
- private Memory-Inhalte
- rohe Provider-Daten
- rohe Runtime-Daten
- Thread-IDs aus internem Live-Kontext
- komplette Tool-Payloads
- Logs oder Tracebacks mit sensitiven Inhalten

Auch nicht sichtbar machen:

- versteckte implizite Agentenstarts
- aktive Dispatch-Kommandos
- Scheduler-Steuerkommandos

Operator-Sprache:

- "safe for display"
- "no secret-bearing runtime fields"

## Main Agent Als Konsument

Der Main Agent nutzt die Teamkarte als kompakten Kontext.

Das bedeutet:

- Der Main Agent sieht eine kleine, verdichtete Teamsicht.
- Die Teamkarte hilft bei Routing, Sprache, Rollen und Safety.
- Die Teamkarte ersetzt nicht `AgentProfileRegistry`, `AgentProfile` oder
  `ContextCapsule`.

Nicht sagen:

- "the team card is the source of truth"
- "the team card decides runtime execution alone"

## Spaetere UI-Verwendung

Die gleiche Surface darf spaeter mehrere kleine UI-Pfade speisen:

- `/team`
- Chat-Overlay
- Agent-Details
- kompakte Team-Zusammenfassung im Main-Agent-Kontext

Wichtig:

- Dieser Slice beschreibt nur die konsumierbare Surface.
- Er verspricht kein grosses UI-Refactor.
- Er verspricht keine Chatlisten-Neuordnung.

## Parent / Child / Hidden Worker Darstellung

### Main Agent

Der Main Agent steht als sichtbarer Orchestrator ueber dem Team-Kontext.

### Visible Subagent

Ein sichtbarer Subagent darf auf der Surface:

- eigene Rolle
- Staerken
- Grenzen
- Parent- oder Reports-to-Hinweis

zeigen.

### Hidden Worker

Ein Hidden Worker darf auf der Surface:

- nur als reduzierte Arbeitsklassifikation auftauchen
- nicht wie ein normaler Chat-Agent beworben werden
- nicht als implizit aktiver Runtime-Prozess dargestellt werden

Wenn Hidden Worker in der Teamkarte ueberhaupt vorkommen, dann nur als
kontrollierte, kompakte Information fuer Parent-Kontext oder Audit-Summary.

## Timer-Hinweis Auf Der Surface

Die Surface darf einen kompakten Timer-Hinweis tragen.

Zulaessige Bedeutung:

- geplant
- watch-only
- aktiver Hinweis im Profilkontext

Nicht zulaessige Bedeutung:

- Thread-Send ist gestartet
- Scheduler laeuft sicher live
- naechster Lauf wurde wirklich persistiert oder dispatcht

Der Timer-Hinweis ist eine sichtbare Zusammenfassung, keine Steueraktion.

## Safety Summary

Jede Agent-Karte soll eine kurze Safety Summary tragen.

Die Safety Summary darf:

- kurz sein
- routing- und operator-tauglich sein
- auf nicht ueberschreibbare Regeln hinweisen

Sie darf nicht:

- geheime Regeln offenlegen, die sensitive Daten enthalten
- echte Runtime-Policies mit secret-bearing Details dumpen

## Shape Der Surface

Ohne Implementierung festzunageln, soll die Surface semantisch mindestens diese
Bereiche abbilden:

- `team`
- `rules`
- `audit`
- optional kompakter Prompt-/Kontexttext fuer den Main Agent

### `team`

Liste der sichtbaren Agenten oder reduzierten Teammitglieder.

### `rules`

Gemeinsame Teamregeln, zum Beispiel:

- Main Agent orchestrates by default
- safety rules win over overrides
- hidden workers are internal work-steps only

### `audit`

Kompakte Read-Only-Hinweise, dass die Surface keine Runtime aktiviert und keine
sensitiven Daten enthalten darf.

### Optionaler Prompt-/Kontexttext

Ein spaeterer kompakter Text fuer den Main Agent darf aus derselben Surface
abgeleitet werden, bleibt aber ebenfalls read-only.

## Go / Partial / No-Go Fuer ATC1-ATC3

### Go

Go ist angemessen, wenn:

- ATC1 die sichtbaren und verbotenen Felder sauber festzieht
- ATC2 ein leak-freies read-only Payload-Modell liefert
- ATC3 eine read-only Route oder Surface anbietet, ohne Runtime-Aktionen
- Main Agent und spaetere UI dieselbe kompakte Teamsicht nutzen koennen

### Partial

Partial ist angemessen, wenn:

- Contract und Payload-Modell stehen
- Route oder UI-Hook noch bewusst deferred sind
- die Read-Only- und Leak-Free-Grenzen schon klar dokumentiert sind

### No-Go

No-Go ist angemessen, wenn:

- Secrets, Tokens, Chat-IDs oder Memory-Inhalte sichtbar werden
- die Surface Runtime-Aktionen startet oder impliziert
- die Surface bestehende Primaerwahrheiten verdoppelt oder ersetzt
- Hidden Worker als normale sichtbare Live-Agenten dargestellt werden

## Nutzer- Und Operator-Sprache

Bevorzugte kurze Sprache:

- read-only team card
- compact team context
- strengths chips
- parent visibility
- hidden worker reduced view
- timer hint
- safety summary

Zu vermeiden:

- "agent launcher"
- "runtime control API"
- "starts background workers"
- "full internal state dump"

## Rollen

### Alice

Alice definiert die Nutzer- und Operator-Sprache fuer sichtbare Felder,
verbotene Felder, Read-Only-Grenzen und die Beziehung zu Main Agent und UI.

### Bob

Bob baut spaeter das kleine Payload-Modell, leak-frei und runtime-agnostisch.

### Charlie

Charlie kontrolliert Scope, Tests, Route-Integration und stoppt jede
Verschiebung in Runtime-Aktionen, Scheduler, Thread-Sends oder Scope-Drift.

## Stop-Regeln

Sofort stoppen, wenn:

- die Surface Secrets, Tokens, Chat-IDs oder Memory-Inhalte sichtbar machen soll
- Runtime-, Scheduler- oder Thread-Send-Aktionen erforderlich werden
- die Teamkarte als neue Primaerwahrheit verkauft wird
- Scope in Code-, Route-, UI-, Test-, Plugin-, Telegram- oder Netzwerk-Arbeit
  abgleitet

## Abschluss

Die Agent-Team-Card-Surface soll vor `1.0` eine kleine, sichere und
read-only Teamsicht liefern: genug fuer Main Agent, `/team`, Chat-Overlay oder
spaetere Agent-Details, aber ohne Secrets, ohne Runtime-Steuerung und ohne ein
neues Agentensystem zu erfinden.
