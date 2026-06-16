# Agent State Isolation UX Contract

Stand: 2026-06-16

Status: **AS1A Produkt-/UX-Vertrag fuer `0.11.x Agent State & Architecture Hygiene`**

Quelle: `docs/plans/unified-odysseus-roadmap.md` definiert `0.11.x` als naechsten Foundation-Schnitt fuer Zustandstrennung, Context Capsules, Tool Truth und Workspace-Grenzen. Dieser Vertrag ist Alices isolierter Vorlauf fuer `AS1-agent-state-model`, damit Bob parallel ein kleines Backend-Modell mit klaren Feld- und Sichtbarkeitsregeln bauen kann, ohne sofort die Runtime umzubauen.

## Ziel

Odysseus soll Agenten nicht mehr nur als Persona-Text behandeln, sondern als explizite, nachvollziehbare Arbeitsinstanzen mit eigener Identitaet, eigenem Scope und klarer Sichtbarkeit.

Dieser Slice definiert:

- die Begriffe, die der Nutzer und das System spaeter konsistent verwenden sollen
- welche Zustandsdaten user-facing, agent-facing oder nur auditierbar sind
- welche Mindestfelder Bob im ersten Backend-Modell validieren soll
- welche Risiken `0.11.x` zuerst verhindert, bevor spaetere Orchestration-UI entsteht

## Kernbegriffe

### `agent_id`

Die konkrete Identitaet einer laufenden oder gespeicherten Agent-Instanz.

- Beispiel: `alice`, `bob`, `charlie`, `reviewer`, spaeter auch mehrere Instanzen derselben Rolle
- Zweck: technische und fachliche Trennung von Runs, Handoffs, Artefakten und Audit-Spuren
- Regel: `agent_id` ist die kleinste eindeutige Agent-Identitaet, nicht nur ein Anzeigename

### `role_id`

Die fachliche Rolle, in der ein Agent handelt.

- Beispiel: `master`, `implementation`, `review`, `user_delegate`
- Zweck: beschreibt Verhaltenserwartung, nicht die individuelle Instanz
- Regel: mehrere `agent_id`s duerfen dieselbe `role_id` tragen; Rollen sind semantische Klassen, keine Lauf-IDs

### `project_id`

Die Zugehoerigkeit eines Agent-Runs zu einem Projekt- oder Arbeitskontext.

- Beispiel: `odysseus-fork`, spaeter weitere getrennte Codebasen oder Missionen
- Zweck: verhindert, dass Handoffs, Memory, Evidence oder Workspaces projektuebergreifend vermischt werden
- Regel: ohne `project_id` gibt es keinen belastbaren Scope fuer Persistenz, Audit oder Memory-Zuordnung

### `memory_scope`

Die fachliche Grenze dafuer, auf welchen Memory-Kontext ein Agent zugreifen oder in welchen Memory-Kontext er schreiben darf.

- Beispiel: `project`, `thread`, `agent`, `audit_only`, spaeter granularere Namespaces
- Zweck: verhindert Context Bleeding zwischen Master, Alice, Bob, Reviewer und verschiedenen Projekten
- Regel: `memory_scope` ist eine Berechtigungs- und Herkunftsgrenze, kein UI-Label fuer "mehr Kontext"

### `workspace_scope`

Die Grenze dafuer, in welchem Dateisystem- oder Repository-Bereich ein Agent lesen oder schreiben darf.

- Beispiel: ein bestimmter Worktree, Repository-Unterordner oder eine explizit erlaubte Ziel-Codebase
- Zweck: trennt Schreibrechte fuer fremde Projekte, Odysseus-Systemdateien und unterschiedliche Agent-Korridore
- Regel: `workspace_scope` beschreibt den erlaubten Pfadraum, nicht bloss das aktuelle Arbeitsverzeichnis

### `run_id`

Die eindeutige Identitaet eines konkreten Agent-Laufs.

- Beispiel: ein einzelner Alice-Durchlauf fuer `AS1A`
- Zweck: verbindet Status, Evidence, Commit, Handoff, Tool-Resultate und Audit-Daten zu einer reproduzierbaren Einheit
- Regel: `run_id` ist kurzlebiger als `agent_id`; mehrere Runs koennen derselben Agent-Instanz gehoeren

## Rollenvertrag

### Master / Charlie

- schneidet Slices zu
- erkennt Konflikte, Hot Files und Blocker
- sieht Fortschritt, Handoffs, Commits, Teststatus und offene Risiken ueber Agenten hinweg
- darf Sichtbarkeit aggregieren, ohne automatisch Zugriff auf jeden fremden Schreibscope zu bekommen

### Alice

- owned Produktvertrag, UX, Nutzertexte, Runbooks und Release-Evidence
- sieht nur den Kontext, den ihr Slice, Handoff und Scope benoetigen
- darf nicht stillschweigend globale Backend-Kontexte oder fremde Workspaces mitschleppen

### Bob

- owned Backend, Runtime, Tests, Stores und APIs
- braucht klare Scope-Daten fuer Validierung, Persistenz und Isolation
- darf Alices Doku-/UI-Kontext nicht implizit als eigenen Arbeitskontext erben

### Reviewer

- prueft Artefakte, Testresultate, Evidence und Risiken gegen den deklarierten Slice
- braucht Lesesicht auf Commit, Test, Handoff und Audit-Daten
- braucht nicht automatisch denselben Schreib- oder Memory-Scope wie Alice oder Bob

### User

- bleibt oberste Quelle fuer Auftrag, Freigabe und Zielwechsel
- soll Rollen, Fortschritt, Blocker und den aktiven Scope verstehen koennen
- soll nicht mit internen technischen IDs ueberladen werden, wenn Anzeigenamen und verdichtete Statusfelder reichen

## Sichtbarkeitsvertrag

Agent State ist nicht gleich Agent UI. Einige Felder sind fuer Nutzer sichtbar, andere fuer Agenten oder nur fuer Audit/Evidence.

### Nutzer sichtbar

Diese Informationen muessen spaeter in Status, Handoff oder Dashboard lesbar sein:

- Anzeigename oder lesbare Agent-Bezeichnung
- Rolle des Agents
- aktiver Slice
- `run_id` in lesbarer oder verkuerzter Form
- Status wie `running`, `blocked`, `done`, `failed`, `handoff`
- projektbezogene Einordnung
- grobe Workspace-Einordnung, falls fuer Risiko oder Vertrauen relevant
- Tests, Commit, Evidence und Blocker in verdichteter Form

### Agent sichtbar

Diese Informationen darf ein Agent fuer korrektes Arbeiten sehen:

- eigener `agent_id`
- eigene `role_id`
- eigener `project_id`
- eigener `memory_scope`
- eigener `workspace_scope`
- eigene `run_id`
- explizite Handoffs, erlaubte Dateien, verbotene Dateien, Blocker und Folge-Slice

Ein Agent soll nicht automatisch sehen:

- den kompletten privaten Arbeitskontext anderer Agents
- fremde unfreigegebene Workspaces
- Memory anderer Projekte oder anderer Nutzer
- rohe Audit- oder Sicherheitsdaten, die fuer den eigenen Slice nicht noetig sind

### Nur Audit / Evidence

Diese Informationen duerfen existieren, muessen aber nicht direkt user-facing oder agent-facing sein:

- interne Normalisierung von IDs
- maschinenlesbare Scope-Validierungsresultate
- detaillierte Zugriffspfade und Lock-Entscheidungen
- unverkuerzte Tool-Resultat-Metadaten
- historische Scope-Aenderungen ueber mehrere Runs

Regel:

- Audit-Daten dienen Nachvollziehbarkeit und Sicherheitsbeweis.
- Audit-Daten sind kein Vorwand, mehr user-facing Komplexitaet in `AS1A` zu bauen.

## UX-Grundsaetze fuer Agent State

- Rollen muessen fuer Nutzer semantisch verstaendlich sein, nicht nur technisch korrekt.
- Ein Agent soll als Arbeitsinstanz erkennbar sein, nicht als loses Chat-Gespraech.
- Scope muss als Schutz und Klarheit erscheinen, nicht als buerokratische Last.
- Wenn ein Agent blockiert ist, muss sichtbar sein, ob der Blocker aus Datei-Overlap, fehlendem Handoff, fehlender Freigabe oder Scope-Konflikt kommt.
- `claimed done` und `verified done` bleiben getrennt; Agent State ist Teil der Evidence, nicht nur eine Komfortanzeige.

## Nicht-Ziele in diesem Slice

Dieser Vertrag fuehrt bewusst noch nicht aus:

- keine Big-Bang-Migration der existierenden Runtime
- keine Memory-Migration oder globale Neustrukturierung alter Daten
- keine Orchestration-UI oder grosses Dashboard
- keine vollstaendige Capsule-Payload fuer `AS2`
- keine Workspace-Lock-Implementierung fuer `AS5`
- keine `src`/`services`-Refactor-Arbeit fuer `AS6`

`AS1A` friert nur die Begriffe, Sichtbarkeit und Mindestfelder ein, damit Bob ein kleines, belastbares Backend-Modell bauen kann.

## Mindest-Handoff an Bob

Bobs erstes Backend-Modell fuer `AS1-agent-state-model` soll mindestens diese Felder validieren:

- `agent_id`
- `role_id`
- `project_id`
- `memory_scope`
- `workspace_scope`
- `run_id`
- `status`

Minimum-Regeln fuer das Modell:

- alle IDs muessen vorhanden und nicht leer sein
- `agent_id` und `run_id` muessen innerhalb ihres Kontexts eindeutig behandelbar sein
- `role_id` muss aus einer kontrollierten Rollenmenge stammen oder bewusst als erweiterbar markiert sein
- `project_id` darf nicht implizit aus einem Dateipfad geraten werden, wenn der Auftrag es explizit liefern kann
- `memory_scope` und `workspace_scope` duerfen nicht fehlen, wenn Schreib- oder Handoff-Arbeit stattfindet
- `status` muss zwischen laufend, blockiert, fertig und fehlgeschlagen unterscheiden koennen

Sinnvolle, aber fuer den kleinsten Start nicht zwingende Zusatzfelder:

- `parent_run_id`
- `owner_user_id`
- `started_at`
- `completed_at`
- `handoff_target`
- `evidence_refs`

## Risiken, die `AS1` explizit adressiert

### Context Bleeding

Ein Agent erbt zu viel globalen Kontext und arbeitet dadurch mit falschen Annahmen, fremden Artefakten oder ueberbreitem Prompt-Kontext.

### Falsche Owner-Zuordnung

Artefakte, Handoffs, Commits oder Entscheidungen landen beim falschen Agent oder in der falschen Rolle.

### Memory-Scope-Leak

Memory aus einem anderen Projekt, Thread oder Agent-Lauf wird sichtbar oder wiederverwendet, obwohl er fachlich nicht dazugehoert.

### Workspace-Verwechslung

Ein Agent arbeitet im falschen Repository, im falschen Worktree oder mit zu breitem Schreibraum.

### Audit-Luecke

Ein Run wirkt abgeschlossen, aber Commit, Evidence, Teststand oder Scope sind spaeter nicht reproduzierbar zuordenbar.

## Akzeptanz fuer diesen Vertrag

`AS1A-agent-state-ux-contract` ist erfuellt, wenn:

- die Kernbegriffe fuer Agent State klar und nicht widerspruechlich definiert sind
- Rollen fuer Master/Charlie, Alice, Bob, Reviewer und User erklaert sind
- Sichtbarkeit zwischen Nutzer, Agent und Audit getrennt ist
- Nicht-Ziele den Slice klein halten
- Bob einen klaren Mindest-Handoff fuer sein erstes Backend-Modell bekommt
- die Roadmap-Absicht aus `0.11.x` nachvollziehbar in einen umsetzbaren Produktvertrag uebersetzt ist

## Handoff an Bob

Bitte das erste Backend-Modell fuer `AS1-agent-state-model` so klein wie moeglich halten:

- validiere zuerst nur Identitaet, Rolle, Projekt und Scope
- baue noch keine grosse Runtime-Migration darum herum
- trenne technische IDs von spaeteren UI-Anzeigenamen
- behandle fehlenden `memory_scope` oder `workspace_scope` als echten Modellfehler fuer schreibende oder handoff-faehige Runs
- halte das Modell so, dass `AS2-context-capsules` spaeter darauf aufsetzen kann, statt es wieder zu ersetzen
