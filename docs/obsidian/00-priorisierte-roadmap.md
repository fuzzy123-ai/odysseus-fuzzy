# Odysseus Obsidian Plugin: Priorisierte Roadmap

Stand: 2026-06-09

Dieses Dokument ordnet die geplanten Features so, dass wir sie nacheinander sauber abarbeiten koennen. Die Reihenfolge ist bewusst nicht identisch mit der Ideensammlung: Erst kommt das Datenmodell, dann die Grundbedienung, dann Visualisierung. KI-Steuerbarkeit ist kein spaeteres Zusatzfeature, sondern gilt fuer jedes einzelne Feature von Anfang an.

## Update nach Plugin-System-Migration

Die Plugin-System-Migration ist als technisches Fundament erledigt. Das Obsidian-Plugin wird jetzt als Drop-in-Plugin unter `plugins/obsidian/plugin.py` geladen, nutzt Routen unter `/api/plugins/obsidian/...` und besitzt einen UI-Einstieg ueber `/api/plugins/obsidian/app`.

Dadurch aendert sich die Roadmap nicht in ihrem Zielbild, aber in der Abarbeitung:

- P0 enthaelt jetzt zusaetzlich die Stabilisierung des Plugin-Vertrags: Manifest, Route-Namespace, UI-Einstieg und KI-Tools muessen bei jedem Feature erhalten bleiben.
- KI-Steuerbarkeit wird ueber Plugin-Tools und Plugin-Routen geplant, nicht mehr ueber den alten Minimal-Loader.
- Der alte `/api/plugins/loader.js`-Ansatz ist kein Ziel mehr.
- Erste KI-Paritaet fuer Datei-/Ordner-Kernaktionen ist vorhanden: Listen, Baum, Lesen, Schreiben, Suchen, Ordner erstellen, Umbenennen, Datei loeschen, leeren Ordner loeschen.
- Destruktive Aktionen bleiben in der weiteren Roadmap bestaetigungspflichtig; die aktuelle KI-Ordnerloeschung ist bewusst auf leere Ordner begrenzt.

## Zielbild

Das Plugin soll kein reines Notizwerkzeug sein. Es soll ein lokaler Wissensraum werden, in dem Markdown-Dateien, Ordner, Tags, automatische Beziehungen und KI-generierte Projektplaene gemeinsam als lesbare Dokumente und als interaktiver Graph funktionieren.

Wichtig ist dabei:

- Markdown bleibt das zentrale, portable Dateiformat.
- Vaults bleiben importierbar/exportierbar und optional schuetzbar.
- Obsidian ist eine aktive Gedaechtnisquelle fuer Odysseus, aber nicht der Ort fuer die gesamte Odysseus-Systemlogik.
- Tags und Dateinamen bilden eine einfache, stabile Beziehungsschicht.
- Der Graph soll Zusammenhaenge erklaeren, nicht nur huebsch aussehen.
- Die Bedienung soll sich nah am originalen Obsidian anfuehlen: schnell, direkt, markdownzentriert, mit dynamischem Graph und vertrauter Vault-Navigation.
- Neue KI-erzeugte Notizen muessen nach einem nachvollziehbaren Notiz- und Tag-Schema entstehen, damit Graph-Verbindungen nicht zufaellig oder tag-chaotisch werden.
- KI darf alles tun, was ein Mensch im Plugin tun kann, muss aber nachvollziehbar, bestaetigbar und ruecknehmbar bleiben.
- Sicherheit steht vor Bedienkomfort: Pfadschutz, Passwortschutz, Rechtepruefung, Importhygiene und KI-Bestaetigungen sind Release-Blocker.

## Querschnittsregel: Mensch-KI-Paritaet

Jedes Feature braucht zwei Bedienwege:

- Menschlicher Bedienweg ueber UI, Tastatur, Maus oder Touch.
- KI-Bedienweg ueber klare interne Aktionen, Werkzeuge oder APIs.

Wenn ein Mensch eine Datei verschieben, einen Tag setzen, einen Graph-Filter aendern, ein Vault importieren, eine Graph-Beziehung erstellen oder ein Editor-Tool ausloesen kann, muss die KI dieselbe Aktion ebenfalls ausfuehren koennen. Ausnahmen muessen ausdruecklich begruendet werden, z.B. Passwort-Eingabe oder destruktive Aktionen ohne Nutzerbestaetigung.

Planungsdokument:

- [08-ai-control-surface.md](08-ai-control-surface.md)

## Querschnittsregel: Tests und Sicherheitsgate

Jedes Feature braucht vor Umsetzung einen Testplan und nach Umsetzung ausfuehrbare Tests. Ich kann diese Tests spaeter selbststaendig planen, schreiben und ausfuehren, solange das Plugin lokal vorhanden ist.

Sicherheitsrelevante Tests haben Vorrang vor UI-Polish. Ein Feature gilt nicht als fertig, wenn es Pfadgrenzen, Nutzerrechte, Passwortschutz, Importvalidierung, KI-Bestaetigungen oder Datenintegritaet ungeprueft laesst.

Planungsdokument:

- [09-test-und-sicherheitsplan.md](09-test-und-sicherheitsplan.md)

## Querschnittsregel: Obsidian als kuratiertes Gedaechtnis

Odysseus soll Obsidian als Kontext- und Langzeitgedaechtnis nutzen koennen, ohne dass das Obsidian-Plugin zur Ablage fuer alle Systemfunktionen wird.

Darum gilt:

- Das Obsidian-Plugin besitzt Vault, Markdown, Tags, Links, Graph und Notizbearbeitung.
- Odysseus Core besitzt globales Memory, Retrieval, Quellenranking, Backup, Datei-/Medienlogik und Sync.
- Eine neue Obsidian-Notiz aus Memory Review oder Chat darf nicht isoliert entstehen: Speichern, Taggen und Verknuepfen mit bestehenden Notizen ist ein gemeinsamer Schritt.
- Die KI muss vor dem Erzeugen einer Notiz bestehende relevante Tags, Notizen, Projektordner und Graph-Nachbarn beruecksichtigen.
- Tags duerfen nicht frei erfunden werden, wenn ein passendes bestehendes Tag oder ein definierter Schematag existiert.
- Neue Tags sind erlaubt, muessen aber begruendet, normalisiert und optional bestaetigbar sein.
- Fuer KI-generierte Notizen braucht es ein klares Skill-/Schema-Dokument, das Format, Frontmatter, Tag-Regeln, Link-Regeln und Projektordner beschreibt.

Ziel: Obsidian wird als lesbares, portables Gedaechtnis gepflegt; Odysseus entscheidet ueber globalen Kontext und Memory-Qualitaet.

## Prioritaeten

### P0: Fundament und Sicherheit

1. Vault-Import/-Export und Passwortschutz planen und absichern.
2. Einheitliches Datenmodell fuer Vault, Ordner, Datei, Tag, Link und Graph-Kante definieren.
3. Regeln fuer automatische Tags und automatische Verbindungen festlegen.
4. Sicherheits- und Regressionstests fuer Pfade, Archive, Passwoerter, Rechte und KI-Aktionen definieren.
5. Plugin-Vertrag stabil halten: `PLUGIN`-Manifest, `/api/plugins/obsidian/...`-Routen, `/app`-UI-Einstieg und KI-Tool-Registrierung.
6. Notiz- und Tag-Schema fuer KI-erzeugte Obsidian-Notizen definieren: erlaubte Tag-Typen, Wiederverwendung bestehender Tags, neue Tags mit Begruendung, Pflichtlinks und Frontmatter-Konventionen.

Warum zuerst: Fast alle spaeteren Features haengen davon ab. Wenn Tags, Dateinamen, Vault-Schutz und Graph-Kanten spaeter umgebaut werden muessen, wird jede UI-Funktion instabil.

Planungsdokumente:

- [01-vault-import-export-security.md](01-vault-import-export-security.md)
- [02-tags-highlighting-autolinks.md](02-tags-highlighting-autolinks.md)
- [03-graph-visual-model.md](03-graph-visual-model.md)
- [09-test-und-sicherheitsplan.md](09-test-und-sicherheitsplan.md)
- [11-memory-review-save-to-obsidian.md](11-memory-review-save-to-obsidian.md)

### P1: Taegliche Bedienung

1. Drag and Drop fuer Ordner und Dateien.
2. Klare visuelle Trennung von Ordnern, Unterordnern und Markdown-Dokumenten.
3. Editor-Tools fuer Markdown-Dateien.
4. Autocomplete fuer Dateinamen und Tags pruefen.
5. Originalnahes Obsidian-Gefuehl herstellen: schnelle Vault-Navigation, direkte Markdown-Bearbeitung, vertraute Link-/Tag-Flows, kaum Reibung.
6. Jede Bedienaktion als KI-steuerbare Aktion modellieren.

Warum danach: Diese Funktionen machen das Plugin im Alltag brauchbar. Sie sollten auf dem P0-Datenmodell aufbauen, nicht parallel daran vorbei entstehen.

Planungsdokumente:

- [04-file-tree-drag-drop-hierarchy.md](04-file-tree-drag-drop-hierarchy.md)
- [05-editor-tools-autocomplete.md](05-editor-tools-autocomplete.md)

### P2: Graph als Verstaendniswerkzeug

1. Graph-Regeln erweitern: Dateiname in anderem Dokument erzeugt automatisch Verbindung.
2. Tags im Graph sichtbar machen.
3. Ordner/Unterordner als eigene visuelle Ebenen darstellen.
4. Graph nicht nur als Netzwerk, sondern als Erklaerflaeche fuer komplexe Zusammenhaenge ausbauen.
5. Dynamische Graphsicht wie im originalen Obsidian planen: Graph reagiert automatisch auf Dateiwechsel, Link-Aenderungen, Tags, Filter und Fokus.
6. KI kann Graphansichten oeffnen, filtern, fokussieren, erklaeren, exportieren und Beziehungen anlegen.

Warum P2: Der Graph braucht stabile Dateien, Tags und Links. Danach kann er aus einer einfachen Ansicht zu einer Analyse- und Planungsoberflaeche werden.

Planungsdokument:

- [03-graph-visual-model.md](03-graph-visual-model.md)
- [08-ai-control-surface.md](08-ai-control-surface.md)

### P3: UI-Polish und Einstellungen

1. Graph-Switch nach oben neben Minimieren verschieben.
2. Settings-Zahnrad zwischen Graph-Switch und Minimieren platzieren.
3. Kleines Settings-Menue bauen.
4. Import-/Export-Buttons dort anbieten.
5. Vault-Passwortschutz dort verwalten.

Warum P3: Die UI-Aenderung ist sichtbar, aber fachlich kleiner als Datenmodell, Security und Editor. Sie sollte die vorherigen Funktionen nur verfuegbar machen, nicht definieren.

Planungsdokument:

- [06-ui-settings-menu.md](06-ui-settings-menu.md)

### P4: KI-Projektplanung in Vaults

1. KI soll in einem ausgewaehlten Ordner ein Projekt planen.
2. Sie soll mehrere passende Obsidian-Dokumente erzeugen.
3. Dokumente sollen korrekt verlinkt, getaggt und graphfaehig sein.
4. Komplexe Programme sollen dadurch als Struktur visualisierbar werden.
5. Projektplanung muss das definierte Notiz- und Tag-Schema nutzen, damit Projektordner, Status-Tags, Typ-Tags und Backlinks konsistent bleiben.

Warum P4: Dieses Feature ist stark, aber risikoreich. Es braucht die vorherigen Bausteine, sonst erzeugt die KI nur lose Dateien statt einer verstaendlichen Projektstruktur.

Planungsdokument:

- [07-ai-project-planning.md](07-ai-project-planning.md)

### P5: Memory Review und Save-to-Obsidian

1. Odysseus soll vorgeschlagene Erinnerungen, Chat-Erkenntnisse und Projektentscheidungen in einer Memory-Review-Oberflaeche anzeigen.
2. Der Nutzer kann entscheiden: nur als Memory speichern, als Obsidian-Notiz speichern, mit bestehender Notiz verknuepfen oder verwerfen.
3. "Als Obsidian-Notiz speichern" und "mit bestehenden Notizen verknuepfen" ist ein gemeinsamer Workflow, kein spaeterer Nachbearbeitungsschritt.
4. Beim Erstellen einer neuen Notiz muss die KI passende bestehende Tags und Notizen vorschlagen, Pflichtlinks setzen und nur begruendete neue Tags erzeugen.
5. Der erzeugte Graph muss direkt nach dem Speichern sinnvolle Kanten zeigen: Wiki-Links, gemeinsame Tags, Dateinamen-Referenzen und optional Projekt-/Typ-Beziehungen.
6. Odysseus Core entscheidet, welche Memory-Kandidaten reviewt werden; das Obsidian-Plugin fuehrt Vault-spezifisches Schreiben, Taggen und Linken aus.

Warum P5: Ein starkes Gedaechtnis entsteht nicht durch ungefiltertes Speichern. Es braucht Review, Quellen, ein Tag-Schema und kontrollierte Verknuepfung, damit Obsidian langfristig nutzbarer Kontext bleibt.

Planungsdokument:

- [11-memory-review-save-to-obsidian.md](11-memory-review-save-to-obsidian.md)

## Empfohlene Abarbeitung

1. Plugin-Vertrag als Regression-Gate behalten: echter `PluginManager` muss Obsidian laden.
2. P0-Datenmodell reviewen und offene Entscheidungen treffen.
3. Sicherheitsgate und Testmatrix fuer das erste Fachpaket festlegen.
4. Vault-Import/-Export und Passwortschutz als erstes Fach-Implementierungspaket planen.
5. Tag-Erkennung, Dateiname-als-Standardtag und Highlighting als zweites Paket planen.
6. Automatische Graph-Kanten definieren und testen.
7. File Tree mit Drag and Drop und visueller Hierarchie verbessern.
8. Markdown-Editor-Tools und Autocomplete bauen.
9. Settings-Menue anbinden.
10. Notiz- und Tag-Schema fuer KI-erzeugte Obsidian-Notizen festlegen.
11. KI-Steuerflaeche fuer alle Features pruefen und fehlende Tools nachziehen.
12. KI-Projektplanung als zusammenhaengendes Feature bauen.
13. Memory Review mit Save-to-Obsidian und direkter Verknuepfung planen.

## Noch offene Grundsatzfragen

- Soll das Plugin echte Dateien im Vault direkt bearbeiten oder eine interne Datenbank als Zwischenmodell fuehren?
- Sollen verschluesselte Vaults im laufenden Zustand voll entschluesselt auf Platte liegen, nur im Speicher, oder als temporaerer Arbeitsordner?
- Sollen Tags global pro Vault oder pro gesamter Odysseus-Installation gleich gefaerbt werden?
- Soll eine automatische Dateinamen-Verbindung nur bei exaktem Match entstehen oder auch bei Alias/Plural/Slug-Varianten?
- Soll die KI Dateien direkt anlegen duerfen oder immer erst einen Plan zur Bestaetigung zeigen?
- Nach welchem Schema duerfen KI und Memory Review neue Obsidian-Notizen, Tags und Links erzeugen?
- Wann soll eine Information nur in Odysseus Memory, nur in Obsidian oder in beiden Systemen landen?
- Welche bestehenden Tags muessen bevorzugt werden, bevor ein neues Tag angelegt werden darf?
- Welche Aktionen sind fuer die KI nur nach expliziter Nutzerbestaetigung erlaubt?
- Welche Tests muessen als Release-Blocker gelten und welche duerfen spaeter nachgezogen werden?
