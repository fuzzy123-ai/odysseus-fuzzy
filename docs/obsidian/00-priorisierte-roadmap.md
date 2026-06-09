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
- Tags und Dateinamen bilden eine einfache, stabile Beziehungsschicht.
- Der Graph soll Zusammenhaenge erklaeren, nicht nur huebsch aussehen.
- Die Bedienung soll sich nah am originalen Obsidian anfuehlen: schnell, direkt, markdownzentriert, mit dynamischem Graph und vertrauter Vault-Navigation.
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

## Prioritaeten

### P0: Fundament und Sicherheit

1. Vault-Import/-Export und Passwortschutz planen und absichern.
2. Einheitliches Datenmodell fuer Vault, Ordner, Datei, Tag, Link und Graph-Kante definieren.
3. Regeln fuer automatische Tags und automatische Verbindungen festlegen.
4. Sicherheits- und Regressionstests fuer Pfade, Archive, Passwoerter, Rechte und KI-Aktionen definieren.
5. Plugin-Vertrag stabil halten: `PLUGIN`-Manifest, `/api/plugins/obsidian/...`-Routen, `/app`-UI-Einstieg und KI-Tool-Registrierung.

Warum zuerst: Fast alle spaeteren Features haengen davon ab. Wenn Tags, Dateinamen, Vault-Schutz und Graph-Kanten spaeter umgebaut werden muessen, wird jede UI-Funktion instabil.

Planungsdokumente:

- [01-vault-import-export-security.md](01-vault-import-export-security.md)
- [02-tags-highlighting-autolinks.md](02-tags-highlighting-autolinks.md)
- [03-graph-visual-model.md](03-graph-visual-model.md)
- [09-test-und-sicherheitsplan.md](09-test-und-sicherheitsplan.md)

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

Warum P4: Dieses Feature ist stark, aber risikoreich. Es braucht die vorherigen Bausteine, sonst erzeugt die KI nur lose Dateien statt einer verstaendlichen Projektstruktur.

Planungsdokument:

- [07-ai-project-planning.md](07-ai-project-planning.md)

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
10. KI-Steuerflaeche fuer alle Features pruefen und fehlende Tools nachziehen.
11. KI-Projektplanung als zusammenhaengendes Feature bauen.

## Noch offene Grundsatzfragen

- Soll das Plugin echte Dateien im Vault direkt bearbeiten oder eine interne Datenbank als Zwischenmodell fuehren?
- Sollen verschluesselte Vaults im laufenden Zustand voll entschluesselt auf Platte liegen, nur im Speicher, oder als temporaerer Arbeitsordner?
- Sollen Tags global pro Vault oder pro gesamter Odysseus-Installation gleich gefaerbt werden?
- Soll eine automatische Dateinamen-Verbindung nur bei exaktem Match entstehen oder auch bei Alias/Plural/Slug-Varianten?
- Soll die KI Dateien direkt anlegen duerfen oder immer erst einen Plan zur Bestaetigung zeigen?
- Welche Aktionen sind fuer die KI nur nach expliziter Nutzerbestaetigung erlaubt?
- Welche Tests muessen als Release-Blocker gelten und welche duerfen spaeter nachgezogen werden?
