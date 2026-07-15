# Offene Roadmaps: Prioritaeten und ABC-Masterprompt

Stand: 2026-07-15

Status: Ausfuehrungsreihenfolge festgelegt; keine Implementierung durch dieses Dokument

## Ziel

Dieses Dokument legt die Reihenfolge fuer die noch offenen Roadmap-Arbeiten fest
und enthaelt einen kopierfertigen `/abc`-Masterprompt. Es trennt vier Dinge, die
nicht vermischt werden duerfen:

1. lokale, noch nicht integrierte Roadmap-Entwuerfe;
2. repo-only Implementierung;
3. unabhaengige Regressionen;
4. Design-, Live- und Produktions-Gates.

## Verbindliche Ausgangslage

- Der primaere Checkout `C:\Users\nkatz\odysseus` ist stark verschmutzt und
  enthaelt viele fremde, nicht committete Aenderungen. Er darf nicht bereinigt,
  resettet, rebased oder als Integrationsworktree benutzt werden.
- Der zuletzt verifizierte, saubere Deployment-Stand lag auf `fuzzy/dev` bei
  `7e10d0d0bf1140e3fac6a4c6db49fc58739ce9f8`. Vor neuer Arbeit muss der aktuelle
  Remote-Stand erneut gelesen werden; der Hash ist keine dauerhafte Annahme.
- `docs/plans/universal-inbox-abc-roadmap.md` und
  `docs/plans/universal-inbox-document-workbench-handoff.md` enthalten lokal die
  noch nicht integrierte Fortsetzung UIX-ABC13 bis UIX-ABC24.
- UIX-ABC13 ist offen. Die Formulierung "Completed slice: UIX-ABC13" im Handoff
  ist nur eine Vorlage fuer die spaetere Abschlusskarte und kein Ist-Status.
- Im registrierten Open-Work-DAG ist TAX0 aktuell der einzige
  abhaengigkeitsbereite repo-only Slice. TAX1 haengt von TAX0 ab; TUA0 haengt
  von TAX1 ab.
- Der lokale PMCP9-Eintrag ist veraltet. PMCP9 ist im integrierten Stand bereits
  abgeschlossen und darf nicht erneut implementiert werden.
- Ein leerer Safe-Queue-Audit bedeutet nur, dass in der jeweils geprueften
  JSON-Queue kein sicherer Slice offen ist. Er beweist nicht, dass alle lokalen
  Markdown-Roadmaps oder alle Produkt-Roadmaps abgeschlossen sind.

## Prioritaetenliste

### P0 - Roadmap-Wahrheit und sauberer Integrationsstand

Diese Stufe ist kurz und zwingend. Sie liefert noch keine Produktfunktion.

1. Aktuelles `fuzzy/dev` in einem separaten sauberen Worktree auschecken.
2. Die lokalen UIX-Dokumente per Drei-Wege-Vergleich gegen `fuzzy/dev`
   pruefen. Nur die UIX-ABC12-bis-24-Hunks uebernehmen; fremde oder unklare
   Hunks werden nicht geraten.
3. UIX-ABC13 bis UIX-ABC24 als benannte Queue in die kanonische
   Master-Roadmap einhaengen. UIX-ABC13 erhaelt den Status `open`.
4. TAX0=`open`, TAX1=`pending`, TUA0=`pending` bestaetigen.
5. Den veralteten lokalen PMCP9-Status gegen den integrierten Abschluss
   normalisieren, ohne PMCP9 erneut zu oeffnen.
6. Eine kleine Docs-only-Integration committen. Erst danach darf Quellcode
   fuer UIX oder TAX/TUA beansprucht werden.

Abschlusskriterium: Ein sauberer Worktree enthaelt genau eine kanonische
Prioritaets- und Abhaengigkeitswahrheit; der primaere schmutzige Checkout wurde
nicht veraendert oder bereinigt.

### P1 - Universal Inbox Document Workbench, UIX-ABC13 bis UIX-ABC24

UIX ist aufgrund der ausdruecklichen Nutzerpriorisierung vor TAX/TUA
einzuordnen. Die Ausfuehrung bleibt abhaengigkeitsgetrieben:

1. UIX-ABC13: autoritativer, rein serverseitiger Capability-Vertrag.
2. UIX-ABC14 und UIX-ABC15: nach UIX-ABC13; nur bei disjunkten Pfaden duerfen
   sie getrennt bearbeitet werden. In dieser Ausfuehrung bleiben sie seriell.
3. UIX-ABC16 nach UIX-ABC13/14 und dem Content-Read-Vertrag.
4. UIX-ABC18 nach UIX-ABC14; vorerst nur isoliertes Read Model ohne Layout.
5. UIX-ABC17 nach UIX-ABC16 und dem Working-Copy-Vertrag.
6. UIX-ABC19 erst nach UIX-ABC18, expliziter Design-Akzeptanz und
   V3-Hotfile-Handoff. Fuer diesen Slice ist `impeccable` zu verwenden.
7. UIX-ABC20 nach UIX-ABC16/19.
8. UIX-ABC21 nach UIX-ABC17/19/20.
9. UIX-ABC22 nach UIX-ABC13/14/19.
10. UIX-ABC23 nach UIX-ABC17/21 und dem Browser-Export-Vertrag.
11. UIX-ABC24 als gebuendelter Integrations-, Security-, Accessibility- und
    Release-Gate-Slice.

Gate-Regel: Fehlt fuer UIX-ABC13 die in der lokalen Roadmap verlangte
Produktsemantik-Bestaetigung, wird genau ein Entscheidungspaket persistiert.
Ohne Antwort wird nicht gewartet, sondern P2/TAX0 bearbeitet. Das UI-Gate fuer
UIX-ABC19 darf daraus nicht abgeleitet werden.

### P2 - Tool Taxonomy und Privacy-Safe Tool Analytics

1. TAX0 Deterministic Inventory.
2. TAX1 Descriptor-v2 Contract.
3. TUA0 Source-Overlap Baseline.
4. Danach nur die in den jeweiligen Detail-Roadmaps naechsten
   abhaengigkeitsbereiten repo-only Slices.

Feature-Aktivierung, Capture und Backfill bleiben bis zu ihrem jeweils
expliziten finalen Gate deaktiviert. TAX/TUA duerfen UIX-Hotfiles nicht
beanspruchen.

### P3 - Separate Regression-Queue

Die offene Regression-Queue wird erst nach einem definierten UIX- oder
TAX/TUA-Integrationspunkt bearbeitet. Jeder Fehler erhaelt einen eigenen Claim,
Scope, fokussierten Test und Commit. Ein unabhaengiger Testfehler wird nie
automatisch Teil des gerade aktiven Roadmap-Slices.

Die zwei `blocked_environment`-Eintraege bleiben geparkt, bis die fehlende
Umgebung nachweislich vorhanden ist. Die elf `queued`-Eintraege werden nach
Sicherheitswirkung, Nutzerwirkung und Abhaengigkeiten geordnet; gleiche
Prioritaet wird durch den kleinsten isolierten Scope entschieden.

### P4 - Bereits implementierte, aber live-gated Abschluesse

Diese Arbeiten werden nur mit aktionsspezifischem GO ausgefuehrt:

- Planning: PDE-08 echter Save/Undo sowie PMCP externe und echte
  Apply/Delete-Smokes;
- Temporal Light: TLR-10 echter 12-Stunden-Soak, TLR-11
  Produktionsaktivierung und `AGENT-12H-LIVE-GO`;
- Version 1.0 und produktiver UI-Root-Cutover;
- produktive Observability-, Security-, Nextcloud-, Provider-, Memory- und
  GitHub-Aktionen.

Ein frueheres GO fuer eine andere Aktion oder Umgebung wird nicht als
Universalvollmacht interpretiert.

### P5 - Naechste strategische Repo-Roadmaps

Nach P1 bis P3 oder nach ausdruecklicher Repriorisierung:

1. OWM-13 Gemma3 Maintenance Runtime Isolation;
2. OWM-14 GraphRAG/RAPTOR Observability;
3. OWM-15 Unified Source Index Core;
4. OWM-21 USI Data Lifecycle, OWM-19 USI Runtime und OWM-20 USI Domain
   Adapter gemaess ihren Parent-Barrieren;
5. OWM-16 Codebase Memory Engine;
6. OWM-17 Code Lineage;
7. OWM-18 Lens Code Graph.

Jede dieser Roadmaps braucht vor Start ein eigenes Goal oder eine explizite
Repriorisierung. Ihre Aktivierungs- und Live-Gates bleiben bestehen.

### P6 - Deferierte oder externe Lanes

Version-1.0-Release, autonome Coding-Aktivierung, AI-GUI-Breitbetrieb,
MCP-Service-Aktivierung, Nextcloud-Pilot, produktive Observability/Security,
Memory-Reindex, GitHub-Rollout und Calendar bleiben zurueckgestellt, bis ihre
konkreten Vorbedingungen und Freigaben vorliegen.

## Kopierfertiger Masterprompt

```text
/abc: Bearbeite die offenen Odysseus-Roadmaps nach der verbindlichen
Prioritaets- und Abhaengigkeitsreihenfolge in
`docs/plans/open-roadmaps-priority-and-masterprompt-2026-07-15.md`.

Erstelle zu Beginn ein Goal mit dem Ziel, P0 abzuschliessen und danach alle
abhaengigkeitsbereiten repo-only Slices aus P1 und P2 seriell bis zum naechsten
echten Design-, Live-, externen oder Environment-Gate fertigzustellen. Nutze
`/abc` als einzigen Roadmap-Einstiegspunkt. ABC entscheidet anhand des Slices,
ob weitere Skills gebraucht werden. Fuer UIX-ABC19 und weitere echte
Frontend-Gestaltung ist `impeccable` Pflicht. Verwende das angeforderte
5.6-Sol-Max-Profil fuer normale Implementierung, sofern es in der aktuellen
Oberflaeche verfuegbar ist; Ultra nur fuer Architektur-, Security- und finale
Integrationspruefungen. Behaupte keinen Modellwechsel, den du nicht verifizieren
kannst.

AUSFUEHRUNGSMODUS
- Arbeite als `/root`, seriell, mit genau einem aktiven Slice und einem
  Pfad-Claim gleichzeitig.
- Keine Subagenten, kein Multi-Worker und keine parallelen Mutationen.
- Persistiere Runstate, Claim, erlaubte Pfade, Baseline-Hashes, Tests,
  Commit und naechsten Frontier so, dass ein 12- bis 24-Stunden-Lauf ohne
  Chatgedaechtnis fortgesetzt werden kann.
- Sende bei langer Arbeit mindestens alle 60 Minuten einen knappen Status und
  persistiere nach jedem Slice/Commit einen Heartbeat.
- Schliesse ein Slice nicht wegen Zeit- oder Tokenverbrauch ab, sondern nur
  mit der definierten Evidence.

WORKTREE- UND GIT-REGELN
- Der primaere Checkout `C:\Users\nkatz\odysseus` ist fremd verschmutzt. Keine
  Bereinigung, kein Reset, kein Rebase, kein Checkout fremder Dateien und
  keine pauschale Staging-Aktion.
- Lies zuerst `git status --short --branch`, aktuelle Claims und den aktuellen
  Remote-Stand von `fuzzy/dev`.
- Erzeuge oder verwende fuer die Arbeit einen separaten sauberen Worktree auf
  Basis des aktuellen `fuzzy/dev`. Ein alter dokumentierter Commit ist nur
  Evidence, keine aktuelle Basis.
- Vor jeder Mutation: kein aktiver Same-Checkout-Owner, Claim mit minimalen
  erlaubten Pfaden persistiert und Baseline-Fingerprints erfasst.
- Bewahre alle fremden Hunks bytegenau. Wenn ein benoetigter Pfad fremde oder
  nicht zuordenbare Aenderungen enthaelt, stoppe nur diesen Slice und wechsle
  zum naechsten sicheren Frontier.
- Pro abgeschlossenen Slice genau einen kleinen, inhaltlich geschlossenen
  Commit nach erfolgreichen fokussierten Tests erstellen. Nie `git add -A`.
- Push, Merge und Deployment nur, wenn die aktuelle Autorisierung genau diese
  Aktion und das Ziel umfasst. Nie Force-Push.

P0: ROADMAP-WAHRHEIT
1. Reconcile die lokalen Dateien
   `docs/plans/universal-inbox-abc-roadmap.md` und
   `docs/plans/universal-inbox-document-workbench-handoff.md` per Drei-Wege-
   Vergleich gegen den aktuellen `fuzzy/dev`-Stand.
2. Uebernimm nur die identifizierten UIX-ABC12-bis-24-Hunks. Rate keine
   unklaren oder fremden Hunks.
3. Haenge UIX-ABC13 bis UIX-ABC24 in genau eine kanonische Open-Work-Queue ein.
   Setze UIX-ABC13 auf `open`; alle Nachfolger bleiben
   abhaengigkeitsblockiert.
4. Bestaetige TAX0=`open`, TAX1=`pending`, TUA0=`pending`.
5. Normalisiere den veralteten lokalen PMCP9-Status gegen den bereits
   integrierten Abschluss. Implementiere PMCP9 nicht erneut.
6. Validiere Roadmap-Schema/DAG und `git diff --check`; erstelle einen
   Docs-only-Commit. Keine Produktdateien in diesem Commit.

P1: UIX
- Nutzerprioritaet ist UIX-ABC13 bis UIX-ABC24.
- Beginne ausschliesslich mit UIX-ABC13 und den Pfaden
  `src/universal_inbox_workbench.py` sowie
  `tests/test_universal_inbox_workbench.py`. Eine Lizenzdatei ist nur erlaubt,
  wenn nachweislich fremder MIT-Code adaptiert wird.
- UIX-ABC13 ist ein reiner serverseitiger Capability-/Action-State-Vertrag.
  Keine Datei-, Netzwerk-, Datenbank-, Provider- oder UI-Zugriffe.
- Verwende die Deliverables, Verbote und Tests der kanonischen UIX-Roadmap
  wortgetreu. Das Original bleibt unveraendert; Browser-Erkennung bleibt
  advisory; Live-Schreibvorgaenge bleiben gated.
- Die lokale Roadmap enthaelt eine Unklarheit: Das Gate-Modell blockiert durch
  Design-Akzeptanz nur UI-Arbeit, UIX-ABC13 nennt aber eine
  Produktsemantik-Bestaetigung als Abhaengigkeit. Loese diese Unklarheit nicht
  still. Persistiere ein einmaliges Entscheidungspaket mit der exakten
  Semantik. Wenn keine Bestaetigung vorliegt, beanspruche UIX-ABC13 nicht und
  wechsle ohne Leerlauf zu P2/TAX0.
- Nach bestaetigtem UIX-ABC13 folge exakt diesem DAG:
  `13 -> 14 -> 16 -> 17`, `13 -> 15`, `14 -> 18`,
  `18 + Design + Hotfile-Handoff -> 19`,
  `16 + 19 -> 20`, `17 + 19 + 20 -> 21`,
  `13 + 14 + 19 -> 22`, `17 + 21 + Browser-Export -> 23`,
  danach `24`.
- Trotz disjunkter Aeste in diesem Auftrag seriell arbeiten.
- `app.py`, V3-Hotfiles, Datenbank-/Migrationsdateien und bestehende Document-
  Hotfiles nur nach frischem, pfadgenauem Handoff beanspruchen.
- UIX-ABC19 braucht die explizite UIX-Design-Akzeptanz und `impeccable`. Eine
  Planning- oder Agent-Screen-UX-Akzeptanz ersetzt dieses Gate nicht.
- Nextcloud-/Provider-/Memory-Live-Writes bleiben verboten, bis ihr jeweiliges
  Gate explizit erteilt wurde.

P2: TAX/TUA
- Wenn UIX-ABC13 am Produktsemantik-Gate wartet oder wenn der vollstaendige
  UIX-Integrationspunkt erreicht ist, bearbeite TAX0.
- Danach TAX1, danach TUA0, jeweils nur wenn die direkte Abhaengigkeit mit
  Evidence abgeschlossen ist.
- Fahre anschliessend nur mit dem naechsten abhaengigkeitsbereiten repo-only
  Slice der Detail-Roadmaps fort.
- Feature-Aktivierung, Capture und Backfill bleiben deaktiviert.

REGRESSIONEN
- Fuehre Gesamtsuiten nur an den in den Roadmaps definierten
  Integrationspunkten aus: UIX-ABC24, TAX/TUA-Integrationsbarrieren und finaler
  Release-Check.
- Ein unabhaengiger Fehler kommt mit Reproduktion und Scope in die separate
  Regression-Queue. Er erweitert nie automatisch den aktiven Slice.
- Bearbeite die Regression-Queue erst an einem solchen Integrationspunkt oder
  nach expliziter Repriorisierung. `blocked_environment` bleibt geparkt.

VERIFIKATION UND COMMIT-EVIDENCE
- Pro Slice zuerst die fokussierten Tests aus der Detail-Roadmap ausfuehren.
- Danach `git diff --check` nur fuer die erlaubten Pfade.
- Vor dem Commit staged Diff und staged Pfade gegen den Claim pruefen.
- Commit-Nachricht nennt die Slice-ID.
- Nach dem Commit Status, Commit-Hash, Tests, Redaction-/No-Mutation-Evidence,
  offene Gates und naechsten sicheren Slice persistieren.
- Nie private Dokumentinhalte, absolute Uploadpfade, Tokens, Chat-IDs oder
  Secrets in Tests, Logs, Evidence oder Handoffs schreiben.

GATE- UND STOP-LOGIK
- Ordinaere repo-only Koordinationsgates darfst du nach erfolgreicher
  Ownership-, Claim- und Scope-Pruefung selbst konsumieren.
- Stoppe den betroffenen Slice bei: Designentscheidung, Live-/Produktions-
  Aktion, externer Mutation, irreversibler/destruktiver Aktion, Secret-Bedarf,
  unklarer Produktsemantik, fremdem Hotfile ohne Handoff oder fehlender
  Umgebung.
- Persistiere dann ein einziges Gate-Paket mit: Gate-ID, blockiertem Slice,
  bereits erledigter Evidence, exakter benoetigter Entscheidung, sicheren
  Antwortoptionen und naechstem disjunkten Frontier.
- Wenn ein anderer benannter repo-only Frontier sicher und abhaengigkeitsbereit
  ist, arbeite dort weiter. Wenn keiner existiert, setze den Runstate auf
  `waiting_on_user`; erfinde keine neue Arbeit.
- Keine echten Planning-Writes, kein Temporal-Produktivstart, kein echter
  12-Stunden-Soak, kein Provider-/Nextcloud-/Memory-Write und kein Deployment
  ohne aktionsspezifisches GO.

PRIORITAET NACH P1/P2
1. Separate Regression-Queue.
2. OWM-13 Gemma3 Maintenance Runtime Isolation.
3. OWM-14 GraphRAG/RAPTOR Observability.
4. OWM-15/21/19/20 Unified Source Index gemaess Parent-Barrieren.
5. OWM-16 Codebase Memory, dann OWM-17 Code Lineage, dann OWM-18 Lens Code
   Graph.
6. Design-, Live-, externe und deferierte Lanes nur nach ihrem exakten GO.

ABSCHLUSSFORMAT JEDES SLICES
- Roadmap und Slice-ID
- Status: completed, gated oder blocked_environment
- Claim und geaenderte Pfade
- fokussierte Tests mit exaktem Ergebnis
- Commit-Hash oder begruendetes `not_committed`
- erhaltene fremde Hunks und Sicherheitsinvarianten
- offene Gates
- naechster abhaengigkeitsbereiter Slice

Markiere das Goal nur dann als `complete`, wenn alle im Goal benannten
P0/P1/P2-Arbeiten tatsaechlich abgeschlossen sind und keine erforderliche
Arbeit verbleibt. Wenn kein sicherer Frontier mehr existiert, persistiere
`waiting_on_user` im Runstate und lasse das Goal offen. Ein Gate ist kein
erfolgreicher Abschluss der blockierten Roadmap.
```

## Einmalige UIX-Entscheidung, falls noch nicht erteilt

Die folgende Formulierung schliesst nur die Produktsemantik fuer UIX-ABC13 und
die spaetere visuelle Richtung fuer UIX-ABC19. Sie erteilt weder einen
Hotfile-Handoff noch eine Live-Schreibfreigabe:

```text
UIX-WORKBENCH-DESIGN-ACCEPTANCE: Bestaetigt sind der Harbor-One-V3-
Dokumentarbeitsbereich, Dokument-Fokus, P0 Markdown/Text/PDF/DOCX,
Original-unveraendert, Bearbeitung nur in versionierter Arbeitskopie, Routing
nur als erklaerbarer Dry Run und Export zunaechst nur als lokaler
Browser-Download. Provider-, Nextcloud- und Memory-Schreibvorgaenge bleiben
gesperrt. Hotfiles brauchen weiterhin einen separaten pfadgenauen Handoff.
```
