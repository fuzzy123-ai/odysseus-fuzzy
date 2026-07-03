# Agent Profile UX Contract

Stand: 2026-06-17

Status: backend contract consumed; APF profile model, registry, team card and read-only API payload implemented

Dieses Dokument definiert die Nutzer- und Operator-Sprache fuer Agent-Profile.
Es fuehrt kein neues Agentensystem ein, sondern verbindet vorhandene
Fundamente: `CrewMember`, Presets, `AgentIdentity`, `ContextCapsule`,
`ToolCatalog` und `delegate`.

## Zweck

Agent-Profile sollen dafuer sorgen, dass Main Agent, UI und spaetere
Automatisierung dieselbe Sprache fuer Rollen, Staerken, Grenzen, Overrides,
Timer und Parent/Child-Sichtbarkeit verwenden.

Vor `1.0` soll ein Nutzer oder Operator verstehen:

- wer standardmaessig orchestriert
- wann Alice, Bob oder Charlie direkt angesprochen werden
- welche Staerken und Grenzen ein Agent sichtbar hat
- welche Overrides erlaubt sind
- wie sichtbare Subagents und Hidden Worker dargestellt werden
- was die Timer-Uhr bedeutet und was nicht

## Nicht-Ziele

Dieser Slice deckt bewusst nicht ab:

- kein neues Agenten- oder Thread-System
- keine Live-Runtime-Aktivierung
- keinen echten Scheduler-Start
- keine echten Thread-Sends
- keine Netzwerk-, Provider-, Telegram-, Plugin- oder UI-Hotfile-Arbeit
- keine Behauptung, dass APF2 oder APF3 bereits implementiert sind

## Grundmodell

Ein Agent Profile ist ein verbindender UX-Layer ueber bestehende Fundamente.

Die Rollen der Fundamente bleiben:

- `CrewMember`: persistierte Persona-, Modell- und Session-nahe Daten
- Presets/Templates: Rollen- und Prompt-Vorlagen
- `AgentIdentity`: technische Agent-, Run-, Memory- und Workspace-Identitaet
- `ContextCapsule`: konkreter Arbeitsscope
- `ToolCatalog`: Tool-Sichtbarkeit und Safety-Filter
- `delegate`: Hidden-Worker-Ausfuehrung als begrenzter Arbeitsschritt

Agent Profile duerfen diese Schichten nicht ersetzen oder verdoppeln.

## Main Agent Als Standard-Orchestrator

Standardverhalten:

- Der Main Agent orchestriert standardmaessig.
- Nutzer muessen nicht mit `/agent ...` arbeiten, um normale Teamarbeit zu
  bekommen.
- Der Main Agent darf Subagents und Hidden Worker als Hilfsstruktur nutzen,
  solange die Safety-Grenzen eingehalten werden.

Gezielte Nutzeransprache:

- Nutzer duerfen Alice, Bob oder Charlie mit einfachen Befehlen direkt
  ansprechen.
- Diese Befehle sollen nach Profil-Sprache lesbar bleiben, zum Beispiel:
  `Alice, schreib das Runbook.`
  `Bob, pruef die Tests.`
  `Charlie, koordinier die naechsten Slices.`

Nicht versprechen:

- keine freie unbegrenzte Agentenfabrik
- keine implizite Live-Automation
- keine unsichtbaren Side-Effects ausser klar beschriebenen Hidden Work-Steps

## Bestandteile Eines Agent Profiles

Jedes Agent Profile soll aus Nutzer- und Operator-Sicht mindestens diese
Bereiche tragen:

- Preset oder Rollenanker
- Staerken
- Grenzen
- sichtbare Tools oder Tool-Gruppen
- Timer-Policy
- Hidden-Worker-Policy
- Parent-/Reports-to-Beziehung
- Overrides
- nicht ueberschreibbare Safety Rules

### Preset

Das Preset beschreibt die normale Rolle des Agenten, zum Beispiel
`docs-runbook`, `code-audit` oder `coordinator-release`.

Das Preset ist die Ausgangslage, nicht die gesamte Wahrheit ueber den Agenten.

### Staerken

Staerken sollen fuer Nutzer kurz und scanbar sichtbar sein.

Beispiele fuer Form:

- `Docs`
- `Tests`
- `Release`
- `Operator UX`

Staerken sind Routing-Hilfen, keine harten Garantien.

### Grenzen

Grenzen beschreiben, wofuer der Agent nicht automatisch genutzt werden soll.

Beispiele fuer Form:

- `Keine Backend-Hotfiles ohne Handoff`
- `Kein Live-Scheduler`
- `Kein Secret-Handling`

Grenzen muessen kurz, sichtbar und operator-tauglich sein.

### Tools

Das Profil darf bevorzugte Tools oder Tool-Gruppen zeigen, aber keine Safety
oder Tool-Policy umgehen.

UI-Sprache:

- "bevorzugte Tools"
- "sichtbare Tools"
- "Tools durch Policy begrenzt"

Nicht sagen:

- "alle Tools erlaubt"
- "Override macht alles moeglich"

### Timer-Policy

Die Timer-Policy beschreibt, ob ein Agent nur manuell, watch-artig oder mit
spaeterer Automation arbeitet.

Wichtig:

- Eine Timer-Policy ist nicht gleichbedeutend mit aktiver Runtime-Automation.
- Vor APF3 und spaeteren Aktivierungs-Slices ist Timer-Sprache nur Profil- und
  Anzeigevertrag.

### Hidden-Worker-Policy

Die Hidden-Worker-Policy beschreibt, ob ein Agent interne Arbeitsschritte ueber
`delegate` nutzen darf.

Wichtig:

- Hidden Worker sind keine neuen sichtbaren Agenten in der Chatliste.
- Hidden Worker sind begrenzte Work-Steps unter dem Parent.

### Parent / Reports-to

Jeder sichtbare Subagent soll klar unter einem Parent-Chat oder Parent-Agenten
verortet sein.

Das Profil muss ausdruecken:

- wer uebergeordnet ist
- wohin Reports oder Handoffs gehoeren
- ob der Agent sichtbar oder hidden ist

### Overrides

Overrides duerfen gezielte, auditierbare Abweichungen erlauben, zum Beispiel:

- anderes Modell
- andere sichtbare Tools
- anderer Timer-Modus
- anderes Preset fuer die laufende Arbeit

Aber:

- Overrides duerfen Safety Rules nicht aushebeln.

## Safety Rules Gegen Overrides

Safety Rules stehen ueber Profil-Overrides.

Das bedeutet:

- ein Override darf keine verbotenen Tools freischalten
- ein Override darf keine Runtime-Aktivierung implizieren
- ein Override darf keine Hidden-Worker-Grenze in autonome Agentenaktivitaet
  verwandeln
- ein Override darf keine Parent/Child-Sichtbarkeit faelschen

Operator-Sprache:

- "overrideable preferences"
- "non-overrideable safety rules"

Nicht sagen:

- "override can force execution anyway"
- "preset override bypasses policy"

## Sichtbarkeit: Main, Visible Subagent, Hidden Worker

### Main Agent

Der Main Agent ist der Standard-Orchestrator des Chats.

Er ist sichtbar und bildet die oberste Team-Sicht fuer den Nutzer.

### Visible Subagent

Ein sichtbarer Subagent:

- erscheint unter seinem Parent-Chat
- ist als Untereinheit des Parent lesbar
- zeigt eigene Staerken-Chips
- zeigt eigene Grenzen
- kann eigene Reports/Handoffs haben, aber nicht losgeloest vom Parent

### Hidden Worker

Ein Hidden Worker:

- erscheint nicht als eigener Agent in der Chatliste
- erscheint nur als interner Work-Step im Verlauf
- dient kleinen, fokussierten Arbeitspaketen
- darf nicht wie ein normaler sichtbarer Agent verkauft werden

Operator-Sprache:

- visible subagent = sichtbarer Unteragent unter Parent
- hidden worker = interner Arbeitsschritt

Nicht sagen:

- hidden worker = eigener Chat in der Seitenleiste
- hidden worker = vollwertiger autonomer Agent

## Staerken In Der UI

Staerken sollen in der UI zweistufig sichtbar sein:

1. kurze Chips fuer schnelle Orientierung
2. Detail-Overlay fuer mehr Kontext

### Chips

Chips muessen:

- kurz sein
- scanbar sein
- nicht zu viel versprechen

Beispiele:

- `Docs`
- `Code`
- `Tests`
- `Release`
- `Coordination`

### Detail-Overlay

Das Detail-Overlay darf zeigen:

- Preset
- best for
- avoid for
- sichtbare Tools oder Tool-Gruppen
- Timer-Policy
- Hidden-Worker-Policy
- Parent/Reports-to
- aktive Overrides

Nicht versprechen:

- keine Runtime-Ausfuehrung nur wegen eines Overlays
- keine Safety-Freigabe nur wegen eines Profil-Labels

## Timer-Uhr Und Overlay

Die Timer-Uhr ist eine Sichtbarkeits- und Kontrollmetapher fuer aktive
Automation oder Watch-Zustaende.

### Basisverhalten

Die Uhr zeigt:

- dass ein Agent einen aktiven Timer-, Watch- oder geplanten Modus hat

Sie zeigt nicht automatisch:

- dass bereits Live-Runtime laeuft
- dass Scheduler oder Threads aktiv gesendet werden

### Overlay-Inhalt

Das Overlay zur Uhr soll spaeter mindestens zeigen:

- Intervall
- naechster Lauf
- Modus
- ob die Anzeige nur geplant, watch-only oder spaeter aktivierbar ist

### Aenderung Fuer Den Naechsten Intervall

Der Nutzer oder Operator soll den Timer fuer den naechsten Intervall aendern
koennen.

Wichtig:

- Diese Sprache beschreibt den Vertrag, nicht die bereits existierende
  Runtime-Implementierung.
- "Aendern fuer den naechsten Intervall" heisst nicht, dass sofort ein echter
  Scheduler-Write oder Live-Dispatch geschieht.

## Einfache Befehle Statt `/agent`

Die Nutzerinteraktion soll einfache Sprache bevorzugen.

Erwuenscht:

- `Alice, mach das Runbook.`
- `Bob, pruef die Tests.`
- `Charlie, sag mir den naechsten Slice.`
- `Zeig mir Alices Staerken.`
- `Setz Bob auf das Code-Audit-Preset.`

Das Profilmodell muss deshalb:

- direkte Ansprache verstehen koennen
- zugleich den Main Agent als Orchestrator-Default erhalten

## Go / Partial / No-Go Fuer APF1-APF3

### Go

Go ist angemessen, wenn:

- APF1 die Nutzer- und Operator-Sprache sauber definiert
- APF2 ein kleines runtime-agnostisches Profilmodell liefert
- APF3 eine kompakte Main-Agent-Teamkarte auf dem Profilmodell aufbauen kann
- Safety Rules nicht per Override aushebelbar sind
- sichtbare und hidden Rollen nicht vermischt werden

### Partial

Partial ist angemessen, wenn:

- APF1 und APF2 klar stehen
- Teamkarte, Timer-Overlay oder UI-Sichtbarkeit noch nicht voll umgesetzt sind
- die Grenzen ehrlich dokumentiert bleiben

### No-Go

No-Go ist angemessen, wenn:

- Agent Profile bestehende Primaerwahrheiten duplizieren oder ersetzen wollen
- Overrides Safety Rules umgehen koennen
- Hidden Worker wie sichtbare autonome Agenten dargestellt werden
- Timer-Sprache eine Live-Runtime behauptet, die noch nicht existiert

## Nutzer- Und Operator-Sprache

Bevorzugte kurze Sprache:

- Main Agent orchestrates by default
- direct command to Alice, Bob, or Charlie
- strengths chips
- profile overlay
- visible subagent under parent
- hidden worker as internal work-step
- timer/watch indicator
- overrides are limited by safety rules

Zu vermeiden:

- "any agent can do anything"
- "timer means live automation is already running"
- "hidden workers are separate chats"
- "override bypasses policy"

## Rollen

### Alice

Alice definiert die Nutzer- und Operator-Sprache fuer Profile, Staerken,
Grenzen, Sichtbarkeit und Timer-Bedeutung.

### Bob

Bob baut spaeter das kleine Profilmodell und die Teamkarte, ohne daraus ein
neues Agentensystem zu machen.

### Charlie

Charlie kontrolliert Scope, Tests, Worktree, Integration und stoppt jede
Verschiebung in Live-Runtime, Scheduler, Thread-Sends oder Scope-Drift.

## Stop-Regeln

Sofort stoppen, wenn:

- Profil-Sprache ein neues Agentensystem erfindet
- Overrides Safety Rules oder Policy aushebeln sollen
- Hidden Worker als eigene sichtbare Chat-Agenten verkauft werden
- Timer-/Uhr-Sprache Live-Scheduler, Thread-Sends oder Runtime impliziert
- Scope in Code-, UI-, Test-, Plugin-, Telegram- oder Netzwerk-Arbeit abgleitet

## Abschluss

Agent Profile sollen vor `1.0` einen gemeinsamen UX- und Operator-Vertrag fuer
Main Agent, sichtbare Subagents und Hidden Worker liefern. Der Main Agent
orchestriert standardmaessig, Nutzer duerfen Alice/Bob/Charlie direkt und
einfach ansprechen, Staerken werden als Chips plus Overlay sichtbar, Overrides
bleiben unter Safety Rules, und die Timer-Uhr bleibt ein kontrollierter
Sichtbarkeitsvertrag statt eine unbelegte Live-Automation.
