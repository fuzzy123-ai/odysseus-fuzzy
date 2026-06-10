# Memory Review und Save-to-Obsidian

## Betroffener Punkt

- P5: Odysseus soll Memory-Kandidaten, Chat-Erkenntnisse und Projektentscheidungen reviewbar machen und bei Bedarf als sauber verlinkte Obsidian-Notizen speichern.

## Ziel

Memory Review soll kein reiner "Speichern"-Dialog sein. Wenn eine Erkenntnis nach Obsidian uebernommen wird, muessen Notiz erstellen, bestehende Notizen verknuepfen und passende Tags setzen ein gemeinsamer Workflow sein.

Das verhindert lose Markdown-Dateien und sorgt dafuer, dass der Graph sofort nutzbare Beziehungen zeigt.

## Architekturgrenze

Obsidian ist eine Gedaechtnisquelle und ein Wissensraum, aber nicht der gesamte Odysseus-Core.

Odysseus Core verantwortet:

- Memory-Kandidaten aus Chat, Dokumenten, Dateien, Mail, Kalender und Agentenlaeufen.
- Entscheidung, ob etwas als kurze Erinnerung, Obsidian-Notiz, Aufgabe oder Projektkontext vorgeschlagen wird.
- Quellenbindung, Vertrauensstatus, Review-Status und globale Kontextauswahl.
- Ranking relevanter bestehender Notizen und Tags ueber mehrere Quellen hinweg.

Obsidian-Plugin verantwortet:

- Vault-sicheres Lesen und Schreiben.
- Markdown-Format, Frontmatter, Tags, Wiki-Links und Dateipfade.
- Graph-, Tag- und Link-Modell fuer den Vault.
- UI/API fuer Notizvorschau, Speichern, Taggen und Verknuepfen.

## Grundablauf

1. Odysseus erkennt einen Memory-Kandidaten oder eine laengere Erkenntnis.
2. Review UI zeigt Quelle, Vorschlag, Risiko und moegliche Speicherorte.
3. Nutzer waehlt eine Aktion:
   - Nur als Memory speichern.
   - Als Obsidian-Notiz speichern.
   - An bestehende Obsidian-Notiz anhaengen.
   - Mit bestehender Notiz verlinken.
   - Verwerfen.
4. Bei Obsidian-Aktionen sucht Odysseus passende bestehende Notizen, Tags und Projektordner.
5. KI erzeugt eine Vorschau mit Datei, Titel, Frontmatter, Tags, Links und kurzem Inhalt.
6. Nutzer bestaetigt oder bearbeitet.
7. Plugin schreibt die Datei oder Aenderung.
8. Graph und Tagindex werden aktualisiert.

## Notiz- und Tag-Schema

Damit die KI nicht wahllos Tags erzeugt, braucht sie ein klares Schema. Dieses Schema sollte als Skill oder Konfigurationsdokument abrufbar sein und bei allen KI-Notizaktionen in den Kontext kommen.

Minimaler Schema-Vorschlag:

- `type`: Art der Notiz, z.B. `project`, `decision`, `person`, `resource`, `meeting`, `idea`, `reference`, `daily`, `memory`.
- `status`: Bearbeitungszustand, z.B. `draft`, `active`, `review`, `archived`.
- `project`: Projekt-Slug, falls die Notiz zu einem Projekt gehoert.
- `source`: Herkunft, z.B. `chat`, `document`, `mail`, `manual`, `agent`.
- `created`: Datum der Notizerstellung.
- `updated`: Datum der letzten KI- oder Nutzer-Aenderung.

Tags sollten in Ebenen gedacht werden:

- Projekttags: `#odysseus`, `#nextcloud-sync`, `#personal-brain`.
- Typ-Tags: `#project`, `#decision`, `#resource`, `#memory`.
- Status-Tags: `#draft`, `#active`, `#review`, `#archived`.
- Themen-Tags: nur wenn sie bereits existieren oder klar begruendet sind.

Regel: Bestehende Tags haben Vorrang vor neuen Tags. Neue Tags brauchen eine Begruendung und muessen normalisiert werden.

## Link-Regeln

Eine neue Obsidian-Notiz muss direkt mit bestehendem Kontext verbunden werden.

Die KI soll vor dem Schreiben pruefen:

- Gibt es eine bestehende Projektuebersicht?
- Gibt es passende Personen-, Projekt-, Ressourcen- oder Entscheidungsnotizen?
- Gibt es Tags, die bereits dieselbe Bedeutung tragen?
- Gibt es eine Notiz, an die diese Erkenntnis besser angehaengt wird als eine neue Datei zu erzeugen?

Mindestens eine der folgenden Beziehungen sollte entstehen:

- Wiki-Link zu einer bestehenden Notiz.
- Gemeinsames Projekt- oder Typ-Tag.
- Backlink aus einer Projektuebersicht oder Indexnotiz.
- Expliziter Abschnitt "Verknuepfte Notizen".

Wenn keine sinnvolle Beziehung gefunden wird, muss die KI das offen sagen und eine Inbox-/Review-Notiz vorschlagen statt so zu tun, als sei der Kontext klar.

## KI-Skill fuer Obsidian-Notizen

Es sollte einen eigenen Skill oder ein klar versioniertes Schema geben, z.B. `odysseus-obsidian-note-schema`.

Der Skill beschreibt:

- Wann eine Information nach Obsidian gehoert.
- Wann eine kurze Memory reicht.
- Welche Ordner und Dateinamen verwendet werden.
- Welche Frontmatter-Felder Pflicht sind.
- Welche Tag-Ebenen erlaubt sind.
- Wie bestehende Tags wiederverwendet werden.
- Wie Links und Rueckverweise gesetzt werden.
- Wann der Nutzer bestaetigen muss.

Dieser Skill darf nicht nur fuer Projektplanung gelten, sondern auch fuer Memory Review, Chat-Zusammenfassungen, Datei-Inbox und spaetere Nextcloud-/Galerie-Workflows.

## Bestaetigungsregeln

Ohne Bestaetigung moeglich, wenn der Nutzer Schreibrechte grundsaetzlich erlaubt hat:

- Neue Review-Vorschau erzeugen.
- Bestehende passende Tags und Notizen vorschlagen.
- Graph-Zusammenhang erklaeren.

Mit Bestaetigung:

- Neue Obsidian-Notiz schreiben.
- Bestehende Notiz veraendern.
- Neue Tags dauerhaft einfuehren, wenn sie nicht aus Dateiname, Projekt oder Schema ableitbar sind.
- Projektuebersicht oder Indexnotiz automatisch aktualisieren.

## Akzeptanzkriterien

- Eine Memory-Review-Entscheidung kann eine Obsidian-Notiz erzeugen.
- Speichern und Verknuepfen sind ein gemeinsamer Workflow.
- Neue Notizen enthalten passende Tags und Links.
- Bestehende Tags werden bevorzugt.
- Neue Tags sind normalisiert und begruendet.
- Die Vorschau zeigt betroffene Dateien, Tags und Links vor dem Schreiben.
- Nach dem Schreiben zeigt der Graph sinnvolle Beziehungen.
- Odysseus kann spaeter die Quelle der Notiz nennen.

## Risiken

- Ohne Schema erzeugt die KI Tag-Wildwuchs.
- Zu viele automatisch erzeugte Notizen machen den Vault unbrauchbar.
- Falsche Links erzeugen scheinbare Zusammenhaenge.
- Memory und Obsidian koennen divergieren, wenn keine Quellenbindung existiert.
- Ein Plugin-Monolith entsteht, wenn Core-Memory-Entscheidungen in das Obsidian-Plugin wandern.

## Offene Entscheidungen

- Wo liegt das erste Schema: als Skill, als Vault-Datei, als Plugin-Konfiguration oder im Core?
- Welche Tags sind globale Systemtags und welche sind vault-spezifisch?
- Soll jede KI-erzeugte Notiz Frontmatter bekommen?
- Soll eine Projektuebersicht automatisch Backlinks auf neue Notizen erhalten?
- Welche Review-Aktionen duerfen Agenten spaeter selbststaendig ausfuehren?
