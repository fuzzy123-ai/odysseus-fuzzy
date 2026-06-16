# Workspace Sandbox v2 UX Runbook Contract

Stand: 2026-06-16

Status: **AS5A UX-/Runbook-Vertrag fuer `0.11.x Workspace Sandbox v2`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/context-capsules-contract.md`
- `docs/plans/dynamic-tool-loading-contract.md`

Dieser Vertrag baut auf Agent Identity, Context Capsules, Tool Result Truth und Dynamic Tool Loading auf. `AS5A` definiert, wie Workspace Sandbox v2 aus Nutzer-, Agent- und Charlie-Sicht funktionieren soll, damit Agenten fremde Codebases oder mehrere Projekte bearbeiten koennen, ohne Odysseus-Systemdateien, falsche Projektpfade oder parallele Agent-Arbeit zu treffen.

## Ziel

Workspace Sandbox v2 soll Schreibzugriffe auf den richtigen Arbeitsraum begrenzen und fuer Nutzer nachvollziehbar machen.

Der Vertrag soll sicherstellen:

- Agenten wissen, in welchem Projekt- und Pfadraum sie arbeiten duerfen
- Systemdateien, fremde Projekte und blockierte Bereiche bleiben geschuetzt
- Charlie kann vor, waehrend und nach einem Slice sauber pruefen, ob der Scope noch stimmt
- parallele Agenten beruehren sich nicht stillschweigend in denselben Dateien

## Nutzerbegriffe

### `workspace root`

Der oberste Pfadraum, in dem ein Agent fuer einen Auftrag ueberhaupt operieren darf.

- Beispiel: ein konkreter Worktree oder ein abgegrenztes Projektverzeichnis
- Zweck: groesste erlaubte Arbeitsgrenze

### `project root`

Der fachliche Wurzelpfad des aktuell bearbeiteten Projekts innerhalb oder gleich dem Workspace.

- Beispiel: ein geklonter Fremd-Repo-Ordner innerhalb eines groesseren Agent-Workspaces
- Zweck: verhindert, dass ein Agent versehentlich im falschen Projektzweig arbeitet

### `system root`

Der geschuetzte Pfadraum, der fuer das aktuelle Agentenziel nicht bearbeitet werden darf.

- Beispiel: Odysseus-Systemdateien oder Steuerdateien ausserhalb des delegierten Projekts
- Zweck: trennt Arbeitsprojekt von Agent-/Host-Infrastruktur

### `writable roots`

Explizite Pfade, in denen der Agent schreiben darf.

- Zweck: macht Schreibrechte klein und pruefbar
- Regel: ohne `writable roots` bleibt ein Agent standardmaessig im Lesemodus oder stoppt

### `blocked roots`

Explizite Pfade, in denen der Agent weder direkt noch indirekt schreiben darf.

- Zweck: harte Sicherheits- und Scope-Grenze
- Regel: `blocked roots` schlagen Bequemlichkeit und Naehe zum Ziel

### `agent-owned files`

Dateien, die fuer die Laufzeit dieses Slices einem Agenten eindeutig gehoeren.

- Zweck: verhindert paralleles Editieren derselben Datei
- Regel: Ownership ist slice- und laufbezogen, nicht fuer immer

### `hot files`

Dateien mit hoher Kollisions- oder Sicherheitswahrscheinlichkeit.

- Beispiele: zentrale Runtime-Dateien, gemeinsam genutzte Tests, Haupt-README, Roadmaps, Frontend-Hotfiles
- Zweck: erzwingt mehr Vorsicht, expliziten Handoff oder Stop

## UX-Regeln: wann darf ein Agent schreiben?

### Schreiben erlaubt

Ein Agent darf schreiben, wenn:

- der Auftrag eine write-faehige Capsule hat
- die Datei innerhalb der `writable roots` liegt
- die Datei nicht in `blocked roots` liegt
- die Datei nicht aktuell von einem anderen Agenten owned ist
- kein Hot-File-Konflikt ohne Handoff besteht

### Nur lesen

Ein Agent bleibt im Lesemodus, wenn:

- der Auftrag nur Analyse, Review oder Vertragsarbeit verlangt
- kein klarer Schreibpfad freigegeben wurde
- die noetige Datei ausserhalb des Schreibscopes liegt
- parallele Ownership ungeklaert ist

### Stoppen statt raten

Ein Agent muss stoppen oder Handoff melden, wenn:

- unklar ist, welches Projekt oder welcher Root-Pfad gemeint ist
- eine benoetigte Datei in `blocked roots` liegt
- eine Datei gleichzeitig fremd owned oder Hot File ist
- der Worktree vor Commit fremde oder unerwartete Aenderungen zeigt
- die Capsule implizit in ein anderes Projekt oder in Systemdateien driftet

## Sichtbarkeit

### Vor Start

Nutzer und Charlie sollen vor Slice-Start sehen koennen:

- welcher `workspace root` aktiv ist
- welcher `project root` bearbeitet werden soll
- welche `writable roots` gelten
- welche `blocked roots` gelten
- ob der Auftrag read-only oder write-faehig ist
- welche Hot Files fuer diesen Slice relevant sind

### Waerend des Runs

Nutzer und Charlie sollen waehrend des Slices sehen koennen:

- ob der Agent aktuell nur liest oder bereits schreibt
- welche Dateien bereits owned oder geaendert sind
- ob ein Hot-File- oder Scope-Blocker aufgetreten ist
- ob ein Handoff oder eine Freigabe noetig wird

### Bei Blocker

Im Blockerfall soll sichtbar sein:

- welcher Root, welche Datei oder welche Ownership den Stopp ausgeloest hat
- ob es ein Scope-, Ownership-, Hot-File-, Dirty-Worktree- oder Freigabeproblem ist
- ob Charlie neu schneiden, warten oder explizit uebergeben muss

## Regeln fuer parallele Agents

### File Ownership

- Eine aktiv bearbeitete Datei gehoert genau einem Agenten pro Slice.
- Ohne expliziten Handoff wird dieselbe Datei nicht parallel editiert.

### Hot-File Locks

- Hot Files brauchen einen extra Vorsichtsmodus.
- Wenn ein Hot File benoetigt wird und bereits fremd belegt ist, stoppt der Agent.

### Handoff

- Ownership-Wechsel braucht eine klare Handoff-Notiz.
- Ein "ist wahrscheinlich frei" ersetzt keinen Handoff.

### No destructive commands

- Keine destruktiven Git- oder Dateisystem-Kommandos ohne explizite Freigabe.
- Scope-Klarheit legitimiert nicht automatisch riskante Operationen.

### Commit-Hygiene

- Vor jedem Commit wird `git status --short` geprueft.
- Wenn fremde oder unerwartete Dateien sichtbar oder gestaged sind, wird nicht committed, sondern Handoff gemeldet.

## Runbook fuer Charlie

### Preflight vor Auftrag

Charlie soll vor Slice-Start mindestens pruefen:

1. Welcher `workspace root` und `project root` gelten.
2. Ob der Auftrag read-only oder write-faehig ist.
3. Welche `writable roots` und `blocked roots` aktiv sind.
4. Welche Hot Files oder laufenden Agent-Owner bereits existieren.
5. Ob der Worktree vor dem Auftrag sauber genug fuer einen neuen Slice ist.

### Waerend des Slices

Charlie soll waehrend der Arbeit ueberwachen:

1. Ob neue unerwartete Dateien oder fremde Edits auftauchen.
2. Ob ein Agent still in neue Roots driftet.
3. Ob Hot-File-Kollisionen entstehen.
4. Ob ein Handoff explizit noetig geworden ist.

### Vor Commit

Charlie oder der Agent soll vor jedem Commit mindestens pruefen:

1. `git status --short`
2. Gehoeren alle sichtbaren oder gestagten Dateien zum Slice?
3. Ist keine fremde Backend-, UI- oder Testdatei versehentlich mit im Scope?
4. Ist der Commit fokussiert genug fuer den vereinbarten Korridor?

Wenn eine dieser Fragen mit nein beantwortet wird:

- nicht committen
- Blocker oder Handoff melden

### Nach Commit

Charlie soll nach dem Commit pruefen:

1. Ob Commit-Inhalt und Slice wirklich zusammenpassen.
2. Ob der Worktree wieder sauber oder erklaert dirty ist.
3. Ob der Handoff die geaenderten Dateien, Evidence und Blocker korrekt spiegelt.
4. Ob der naechste Slice eindeutig oder ein neuer Zuschnitt noetig ist.

## Regeln gegen falsche Projektpfade

- `project root` darf nicht still aus einem Dateinamen erraten werden, wenn der Auftrag ihn explizit liefern kann.
- Ein Agent darf nicht automatisch von einem Projekt in ein benachbartes Projekt wechseln.
- Relative Pfade werden gegen den freigegebenen Projekt- und Workspace-Raum bewertet, nicht gegen spontane Shell-Kontexte allein.
- Wenn ein Auftrag mehrere Projekte beruehrt, braucht er einen ausdruecklichen Multi-Root-Hinweis statt stiller Scope-Erweiterung.

## Regeln gegen Systemtreffer

- `system root` bleibt fuer projektfremde oder hostkritische Dateien blockiert.
- Agenten sollen Systempfade nicht als "praktisch erreichbar" betrachten, nur weil das Dateisystem sie zeigt.
- Sicherheits- und Steuerdateien ausserhalb des delegierten Arbeitsraums bleiben tabu, solange kein expliziter Handoff sie freigibt.

## Handoff an Bob

Bobs erstes Backend-Modell fuer `AS5-workspace-sandbox-v2` soll mindestens diese Policy-Felder validieren:

- `workspace_root`
- `project_root`
- `system_root`
- `writable_roots`
- `blocked_roots`
- `agent_owned_files`
- `hot_files`
- `write_mode`

Empfohlene minimale Struktur:

- `workspace_root`: oberste erlaubte Arbeitsgrenze
- `project_root`: fachliches Zielprojekt
- `system_root`: geschuetzter Host-/Systemraum
- `writable_roots`: erlaubte Schreibpfade
- `blocked_roots`: verbotene Pfade
- `agent_owned_files`: aktuell belegte Dateien
- `hot_files`: besonders vorsichtige Dateien
- `write_mode`: `read_only` oder `write_enabled`

Minimum-Regeln fuer das Modell:

- `workspace_root` und `project_root` muessen vorhanden und nachvollziehbar verbunden sein
- `project_root` darf nicht ausserhalb von `workspace_root` liegen, wenn keine Multi-Root-Freigabe existiert
- `blocked_roots` muessen gegen `writable_roots` gewinnen
- `agent_owned_files` duerfen nicht still ignoriert werden
- `write_mode=write_enabled` braucht mindestens einen gueltigen `writable_root`
- Hot Files muessen gesondert markierbar sein

Sinnvolle, aber fuer den kleinsten Start nicht zwingende Zusatzfelder:

- `ownership_source`
- `lock_reason`
- `preflight_status`
- `dirty_worktree_policy`
- `approval_required`
- `audit_refs`

## Nicht-Ziele in diesem Slice

Dieser Vertrag fuehrt bewusst noch nicht aus:

- keine echte Sandbox-Runtime
- kein Docker- oder Container-Konzept
- kein OS-Permission-Umbau
- keine vollstaendige Lock-Engine
- keine Live-Dateisystem-Isolation
- keine automatisierte Merge- oder Rebase-Policy

`AS5A` friert nur UX-, Runbook- und Policy-Begriffe fuer Workspace Sandbox v2 ein.

## Risiken, die `AS5` explizit adressiert

### Falscher Projektpfad

Ein Agent arbeitet im falschen Repo oder im falschen Unterprojekt.

### Systemtreffer

Ein Agent beruehrt versehentlich Odysseus-Systemdateien oder hostkritische Bereiche.

### Fremdarbeit getroffen

Ein Agent editiert Dateien, die gerade einem anderen Agenten gehoeren.

### Hot-File-Kollision

Ein besonders riskantes oder gemeinsames File wird ohne Handoff parallel beruehrt.

### Unscope-sauberer Commit

Ein Commit enthaelt versehentlich fremde oder unerwartete Dateien.

## Akzeptanz fuer diesen Vertrag

`AS5A-workspace-sandbox-ux-runbook` ist erfuellt, wenn:

- die Nutzerbegriffe fuer Roots, Ownership und Hot Files klar definiert sind
- Regeln fuer lesen, schreiben und stoppen explizit sind
- Sichtbarkeit vor Start, waehrend des Runs und bei Blockern benannt ist
- parallele Agent-Regeln fuer Ownership, Handoff und keine destruktiven Kommandos festliegen
- Charlie ein klares Preflight-/Commit-/Post-Commit-Runbook bekommt
- Bob einen kleinen, klaren Mindest-Handoff fuer sein Policy-Modell bekommt
- Nicht-Ziele verhindern, dass `AS5A` schon Runtime-, Container- oder OS-Arbeit wird

## Handoff an Bob

Bitte das erste Backend-Modell fuer `AS5-workspace-sandbox-v2` klein halten:

- validiere zuerst Roots, Ownership, Hot Files und Write-Mode
- fuehre noch keine echte Runtime-Isolation oder Container-Logik ein
- behandle `blocked_roots` als harte Grenze gegen `writable_roots`
- mach `agent_owned_files` und Hot Files explizit pruefbar statt implizit
- lass Charlie spaeter `git status --short`-Checks und Dirty-Worktree-Policies daran andocken, statt schon jetzt eine grosse Engine zu bauen
