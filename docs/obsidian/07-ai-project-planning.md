# Phase 4: KI-Projektplanung in Vaults

Stand: 2026-06-10

## Ziel

Phase 4 macht das Obsidian-Plugin zu einer Planungsoberflaeche fuer komplexe Projekte. Die KI soll in einem ausgewaehlten Vault-Ordner zuerst einen pruefbaren Projektplan erzeugen und nach Bestaetigung mehrere zusammenhaengende Markdown-Dokumente anlegen.

Das Ergebnis ist kein loser Dateistapel. Es ist eine graphfaehige Projektstruktur mit konsistenten Dateinamen, Frontmatter, Tags, Wiki-Links und optionalen manuellen Beziehungen.

## Nicht-Ziele

- Keine Cytoscape.js-Migration. Graph-v2 bleibt ein eigenes Paket.
- Keine globale Memory-Review-UI. Save-to-Obsidian bleibt Phase 5.
- Kein autonomes Ueberschreiben bestehender Projektdateien.
- Keine freie Tag-Erfindung ohne Schema, Begruendung und Vorschau.
- Keine Projektmanagement-Suite mit Kalender, Kanban oder externer Issue-Synchronisation.

## Voraussetzungen aus frueheren Phasen

Bereits vorhanden und fuer Phase 4 zu nutzen:

- Vault-sicheres Lesen, Schreiben, Umbenennen, Loeschen und Ordnererstellen.
- Plugin-Routen unter `/api/plugins/obsidian/...`.
- KI-Tools fuer Vault, Dateien, Suche, Tags, Graph und Beziehungen.
- Bestaetigungspflicht fuer riskante Aktionen.
- Undo-Historie fuer sichere Einzelaktionen.
- Graphmodell mit Datei-, Ordner-, Tag-, Link-, Dateinamen- und manuellen Beziehungskanten.
- Settings- und Header-UI als stabiler Einstiegspunkt.
- Regressionstests fuer Plugin-Vertrag, Tool-Policy, Vault-Sicherheit, Graph, History und statische UI-Smokes.

Noch zu definieren:

- Verbindliches Notiz- und Tag-Schema fuer KI-erzeugte Projektnotizen.
- Plan-vor-Schreiben-Datenvertrag.
- Vorschau- und Bestaetigungsworkflow fuer Massenaktionen.
- Tests fuer konsistente Projektgraphen.

## Nutzer-Workflow

1. Nutzer waehlt einen Zielordner im Vault.
2. Nutzer gibt Projektziel, Umfang, Projektart und optionale Randbedingungen ein.
3. KI analysiert den Zielordner und relevante bestehende Notizen.
4. KI erzeugt einen Projektplan als strukturierte Vorschau, schreibt aber noch keine Dateien.
5. Vorschau zeigt Dateien, Tags, Links, Beziehungen, Konflikte und offene Fragen.
6. Nutzer kann den Plan bestaetigen, ablehnen oder erneut generieren lassen.
7. Nach Bestaetigung schreibt das Plugin die neuen Dateien und Metadaten.
8. Graph und Tagindex werden aktualisiert.
9. Die Projektuebersicht wird geoeffnet und der Graph auf den Projektordner fokussiert.

## UX-Mindestumfang

Phase 4 braucht keine grosse neue Einstellungsseite. Der erste UI-Einstieg kann als Aktion im Obsidian-Header oder im Zielordner-Kontextmenue starten.

Mindestkomponenten:

- Zielordner-Auswahl oder Start aus aktuellem Ordner.
- Eingabefeld fuer Projektbeschreibung.
- Optionale Auswahl fuer Projektart: `software`, `research`, `writing`, `ops`, `generic`.
- Vorschau mit Dateibaum, Dokumenttypen, Tags und Linkliste.
- Konfliktanzeige fuer vorhandene Dateien.
- Bestaetigungsaktion "Projektstruktur anlegen".
- Ergebnisanzeige mit angelegten Dateien und Graph-Hinweis.

Do Later:

- Interaktives Bearbeiten einzelner Vorschau-Dateien.
- Template-Bibliothek mit vielen Projekttypen.
- Drag-and-drop Sortierung der geplanten Dateien.
- Kanban- oder Task-Ansicht.

## Notizschema

KI-erzeugte Projektdokumente sollen Frontmatter nutzen, damit spaetere Graph-, Such- und Memory-Flows dieselbe Struktur verstehen.

Pflichtfelder:

```yaml
---
type: project
project: example-project
status: draft
created: 2026-06-10
source: ai_project_planning
---
```

Felder je Dokument:

- `type`: `project`, `requirements`, `architecture`, `module`, `api`, `data_model`, `ui_flow`, `risk`, `decision`, `implementation_plan`, `test_plan`, `glossary`, `operations`, `research`.
- `project`: stabiler Projekt-Slug.
- `status`: `draft`, `active`, `review`, `archived`.
- `source`: fuer Phase 4 immer `ai_project_planning`.
- `created`: ISO-Datum.
- `updated`: optional, wenn spaeter veraendert.
- `depends_on`: optionale Liste von Wiki-Link-Zielen fuer fachliche Abhaengigkeiten.

Tag-Regeln:

- Ein Projekttag ist Pflicht: `#project/<slug>`.
- Ein Typ-Tag ist Pflicht: `#type/<document-type>`.
- Ein Status-Tag ist Pflicht: `#status/draft` in der ersten Version.
- Themen-Tags sind erlaubt, muessen aber aus bestehenden Tags bevorzugt werden.
- Neue Themen-Tags brauchen im Plan eine kurze Begruendung.

Dateinamen-Regeln:

- Dateinamen sind menschenlesbar und stabil.
- Die Projektuebersicht heisst standardmaessig `00 Projektuebersicht.md`.
- Kernnotizen nutzen nummerierte Praefixe, damit der Ordner lesbar sortiert bleibt.
- Modulnotizen liegen optional unter `Module/`.
- Entscheidungen liegen optional unter `Entscheidungen/`.
- Leerzeichen sind erlaubt, weil Obsidian-Naehe wichtiger ist als reine Slug-Aesthetik.

## Empfohlene Dokumentstruktur

Minimal fuer kleine Projekte:

- `00 Projektuebersicht.md`
- `01 Anforderungen.md`
- `02 Architektur.md`
- `03 Implementierungsplan.md`
- `04 Testplan.md`
- `05 Risiken und offene Fragen.md`

Erweitert fuer Softwareprojekte:

- `Module/<Modulname>.md`
- `APIs und Schnittstellen.md`
- `Datenmodell.md`
- `UI und Nutzerfluesse.md`
- `Entscheidungen/ADR-0001-<titel>.md`
- `Betrieb und Deployment.md`
- `Glossar.md`

Die KI darf weniger oder mehr Dateien vorschlagen, muss Abweichungen aber im Plan begruenden.

## Link- und Beziehungsregeln

Pflichtlinks:

- `00 Projektuebersicht.md` verlinkt alle Hauptdokumente.
- Jedes Hauptdokument verlinkt zur Projektuebersicht zurueck.
- `Implementierungsplan` verlinkt Anforderungen, Architektur und Testplan.
- `Testplan` verlinkt Anforderungen und relevante Module.
- `Risiken und offene Fragen` verlinkt betroffene Dokumente.

Optionale manuelle Beziehungen:

- `depends_on`: Implementierung haengt von Architektur, Modul von Datenmodell, Test von Anforderung ab.
- `blocks`: Risiko blockiert Modul oder Meilenstein.
- `supports`: Entscheidung unterstuetzt Architektur oder Modul.
- `relates_to`: lose fachliche Beziehung ohne harte Abhaengigkeit.

Phase-4-Mindestziel: Markdown-Links und Tags muessen reichen, damit der bestehende Graph sofort sinnvolle Kanten zeigt. Manuelle Beziehungen sind willkommen, aber nicht Pflicht fuer den ersten Slice.

## Plan-vor-Schreiben-Datenvertrag

Die KI erzeugt intern einen strukturierten Plan. UI und Tooling sollen denselben Vertrag verwenden.

```json
{
  "target_folder": "Projects/Example",
  "project": {
    "title": "Example Project",
    "slug": "example-project",
    "kind": "software",
    "summary": "Short project goal"
  },
  "files": [
    {
      "path": "Projects/Example/00 Projektuebersicht.md",
      "title": "Projektuebersicht",
      "type": "project",
      "status": "draft",
      "tags": ["#project/example-project", "#type/project", "#status/draft"],
      "frontmatter": {
        "type": "project",
        "project": "example-project",
        "status": "draft",
        "source": "ai_project_planning"
      },
      "links": ["[[01 Anforderungen]]", "[[02 Architektur]]"],
      "outline": ["Ziel", "Umfang", "Dokumente", "Offene Fragen"],
      "content_preview": "Kurzvorschau fuer die UI"
    }
  ],
  "relationships": [
    {
      "source": "Projects/Example/03 Implementierungsplan.md",
      "target": "Projects/Example/02 Architektur.md",
      "type": "depends_on",
      "reason": "Implementation depends on architecture decisions"
    }
  ],
  "new_tags": [
    {
      "tag": "#domain/example",
      "reason": "No existing domain tag matched"
    }
  ],
  "conflicts": [],
  "warnings": [],
  "questions": []
}
```

Validierungsregeln:

- Alle Pfade muessen innerhalb des Vaults und unterhalb des Zielordners liegen.
- Datei-Pfade duerfen keine Traversal-, absolute oder leeren Segmente enthalten.
- `files[].path` muss eindeutig sein.
- `links` muessen auf geplante oder bestehende Markdown-Dateien zeigen.
- Neue Tags muessen normalisiert sein.
- Vorhandene Dateien duerfen nicht ohne explizite Nutzerbestaetigung ueberschrieben werden.
- Wenn `conflicts` nicht leer ist, darf die Schreibaktion nicht automatisch laufen.

## Backend-API und KI-Tools

Empfohlene neue Routen:

- `POST /api/plugins/obsidian/project-plan/preview`
- `POST /api/plugins/obsidian/project-plan/apply`
- `GET /api/plugins/obsidian/project-plan/templates`

Empfohlene neue Plugin-Tools:

- `obsidian_project_plan_preview`
- `obsidian_project_plan_apply`
- `obsidian_project_plan_templates`

`preview`:

- liest Zielordner, Projektbeschreibung und Optionen.
- sucht vorhandene Notizen, Tags und Konflikte.
- gibt den strukturierten Plan zurueck.
- schreibt nichts.

`apply`:

- akzeptiert einen zuvor validierten Plan.
- verlangt Bestaetigung fuer Massenanlage und jeden Konflikt.
- schreibt Dateien.
- legt optionale Beziehungen an.
- aktualisiert Graph/Index.
- gibt betroffene Dateien, Tags, Beziehungen und Fehler zurueck.

`templates`:

- liefert erlaubte Projekttypen und Dokumentsets.
- enthaelt keine Modelllogik, nur deterministische Vorgaben.

## Implementation Packages

### P4.1 Schema und Validierung

Aufgaben:

- Python-Datenmodell fuer Projektplan, geplante Datei, geplante Beziehung, Tag-Vorschlag und Konflikt anlegen.
- Pfad-, Tag-, Link- und Frontmatter-Validierung implementieren.
- Bestehende Tags und Dateien in die Planung einbeziehen.
- Unit-Tests fuer gute und boese Plan-Payloads schreiben.

Akzeptanz:

- Ungueltige Pfade werden blockiert.
- Doppelte Datei-Pfade werden blockiert.
- Links auf nicht geplante/nicht vorhandene Dateien werden gemeldet.
- Neue Tags ohne Begruendung werden gemeldet.

### P4.2 Preview-Route und Tool

Aufgaben:

- `project-plan/preview` Route ergaenzen.
- `obsidian_project_plan_preview` registrieren.
- Deterministische erste Templates fuer `software` und `generic` bauen.
- Bestehende Zielordner-Inhalte und Tags in der Rueckgabe anzeigen.
- Keine Datei schreiben.

Akzeptanz:

- Vorschau funktioniert fuer leeren Zielordner.
- Vorschau meldet Konflikte in nicht leerem Zielordner.
- Tool und Route liefern denselben Planvertrag.

### P4.3 Apply-Route und Massenaktion

Aufgaben:

- `project-plan/apply` Route ergaenzen.
- `obsidian_project_plan_apply` registrieren.
- Bestaetigungspflicht fuer Massenanlage erzwingen.
- Dateien atomar genug schreiben: bei Fehler klare Teilergebnisliste liefern.
- History-Eintraege fuer angelegte Dateien und Beziehungen erzeugen, soweit bestehendes Modell passt.

Akzeptanz:

- Apply schreibt alle geplanten neuen Dateien.
- Bestehende Dateien werden ohne Bestaetigung nicht ueberschrieben.
- Ergebnis listet angelegte Dateien, Tags und Beziehungen.
- Graph enthaelt danach Link- und Tag-Kanten.

### P4.4 UI-Preview

Aufgaben:

- Startaktion fuer "Projekt planen" in der Obsidian-UI ergaenzen.
- Formular fuer Zielordner, Beschreibung und Projektart bauen.
- Vorschau mit Dateibaum, Tags, Links, Konflikten und Warnungen anzeigen.
- Bestaetigungsaktion an Apply anbinden.
- Nach Erfolg Projektuebersicht oeffnen und Graph-Fokus anbieten.

Akzeptanz:

- Nutzer kann Plan erzeugen, pruefen und anwenden.
- Konflikte sind sichtbar.
- Passwortwerte oder sensible Eingaben werden nicht gerendert.
- Mobile Layout bleibt bedienbar, muss aber nicht vollstaendig optimiert sein.

### P4.5 Graph- und Regressionstests

Aufgaben:

- Fixture fuer KI-Projektplan-Vault ergaenzen.
- Tests fuer Graph-Kanten nach Apply schreiben.
- Statische UI-Smokes fuer neue Projektplan-Controls ergaenzen.
- Tool-Policy-Tests fuer Preview-vs-Apply und Bestaetigungen ergaenzen.

Akzeptanz:

- Regression laeuft mit bestehendem Phase-3-Testset plus neuen Phase-4-Tests.
- Preview ist nicht destruktiv.
- Apply beachtet Pfadschutz, Konflikte und Bestaetigungen.

## Sicherheitsregeln

Release-Blocker:

- Kein Schreiben ausserhalb des Vaults.
- Kein Schreiben ausserhalb des Zielordners.
- Kein Ueberschreiben ohne explizite Bestaetigung.
- Keine Ausfuehrung von Markdown-Inhalten als Anweisung.
- Keine Passwort- oder Secret-Ausgabe in Plan, Logs, Errors oder UI.
- Keine automatische Verarbeitung gesperrter Vault-Inhalte.
- Keine stillen Teilfehler bei Massenanlage.

Prompt-Injection-Regel:

Bestehende Vault-Inhalte sind Daten. Wenn eine Notiz Anweisungen wie "ignoriere Sicherheitsregeln" enthaelt, darf das nur als Inhalt behandelt werden und muss die Planungslogik nicht uebersteuern.

## Testplan

Unit-Tests:

- Planmodell akzeptiert gueltige Minimalplaene.
- Pfad-Traversal wird blockiert.
- Absolute Pfade werden blockiert.
- Doppelte Pfade werden blockiert.
- Linkziele werden gegen geplante und bestehende Dateien geprueft.
- Neue Tags ohne Begruendung werden abgelehnt oder als Warnung markiert.

Integrationstests:

- Preview fuer leeren Ordner schreibt keine Datei.
- Preview fuer bestehenden Ordner meldet Konflikte.
- Apply legt Minimalprojekt an.
- Apply ohne Bestaetigung fuer Massenaktion wird blockiert.
- Apply mit Konflikt wird blockiert oder verlangt explizite Konfliktbestaetigung.
- Graph nach Apply enthaelt Projektuebersicht, Hauptdokumente, Links und gemeinsame Tags.
- History enthaelt nachvollziehbare Eintraege fuer angelegte Dateien.

Statische/UI-Smokes:

- Projektplan-Startaktion vorhanden.
- Vorschau enthaelt Dateibaum, Tags, Links und Konfliktbereich.
- Apply-Button ist nur bei gueltigem Plan erreichbar.
- Mobile Header-/Settings-Vertraege aus Phase 3 bleiben erhalten.

Manueller Smoke:

- Projekt aus UI anlegen.
- Projektuebersicht oeffnet sich.
- Graph zeigt neue Struktur.
- Authentifizierter Browser-Smoke bleibt abhaengig von lokaler Login-Session.

## Akzeptanzkriterien fuer Phase 4

- Nutzer kann in einem Zielordner eine Projektplanung starten.
- KI erzeugt zuerst eine pruefbare Vorschau.
- Vorschau enthaelt mehrere sinnvolle Markdown-Dokumente, Tags, Links und Konflikte.
- Dateien werden erst nach Bestaetigung geschrieben.
- Bestehende Dateien werden nicht still ueberschrieben.
- Erzeugte Dokumente nutzen Frontmatter, Projekt-/Typ-/Status-Tags und Wiki-Links.
- Graph zeigt nach dem Schreiben sinnvolle Projektkanten ohne manuelle Nacharbeit.
- KI-Tool und UI nutzen denselben Datenvertrag.
- Sicherheits- und Regressionstests fuer Pfade, Bestaetigung und Graph bestehen.

## Offene Entscheidungen

- Soll der erste Planinhalt vollstaendige Markdown-Texte enthalten oder nur Outline plus kurze Abschnitte?
- Soll die Projektuebersicht automatisch eine Liste aller erzeugten Dateien pflegen?
- Sind nummerierte Dateipraefixe dauerhaft gewuenscht oder nur fuer generierte Projektordner?
- Wie fein sollen manuelle Beziehungen in Phase 4 genutzt werden?
- Soll der Nutzer neue Themen-Tags einzeln bestaetigen oder gesammelt mit dem Plan?
- Soll Apply bei Teilfehlern bereits geschriebene Dateien automatisch per History zurueckrollen?

## Empfohlener erster Slice

Der kleinste sinnvolle Implementierungsschnitt ist:

1. Datenmodell und Validator.
2. Deterministisches `software`-Template.
3. Preview-Route und Preview-Tool.
4. Apply-Route fuer neue Dateien ohne Ueberschreiben.
5. Tests fuer Pfadschutz, Konflikte und Graph-Kanten.

Danach kann die UI-Vorschau angebunden werden. So bleibt der Kern sicher und testbar, bevor viel Oberflaeche entsteht.
