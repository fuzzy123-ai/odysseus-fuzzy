# KI-Steuerbarkeit fuer alle Plugin-Features

## Grundsatz

Alles, was ein Mensch im Obsidian-Plugin tun kann, muss die KI ebenfalls tun koennen. Das ist keine optionale Automationsschicht, sondern ein Kernprinzip des Plugins.

Die KI soll dabei nicht heimlich agieren. Sie braucht klare Aktionen, nachvollziehbare Ergebnisse, Bestaetigungen fuer riskante Schritte und Rueckmeldungen, wenn etwas nicht moeglich ist.

## Ziel

Das Plugin braucht eine interne Steuerflaeche, ueber die UI und KI dieselben Faehigkeiten nutzen. Dadurch entsteht keine doppelte Logik: Ein Feature wird einmal sauber modelliert und kann dann per UI, Tastatur, Command Palette oder KI ausgefuehrt werden.

## Stand nach Plugin-System-Migration

Die Steuerflaeche laeuft ab jetzt ueber den nativen Plugin-Vertrag:

- UI und API liegen im Plugin unter `/api/plugins/obsidian/...`.
- Der Plugin-Manager liest den UI-Einstieg aus `PLUGIN["ui"]["open"]`.
- KI-Aktionen werden als Plugin-Tools ueber `ctx.register_tool(...)` modelliert.
- Der alte Frontend-Loader `/api/plugins/loader.js` ist keine Zielarchitektur mehr.

Bereits abgedeckte KI-Aktionen:

- `obsidian_list_notes`
- `obsidian_tree`
- `obsidian_read_note`
- `obsidian_write_note`
- `obsidian_search_notes`
- `obsidian_list_tags`
- `obsidian_graph`
- `obsidian_list_relationships`
- `obsidian_add_relationship`
- `obsidian_delete_relationship`
- `obsidian_create_folder`
- `obsidian_rename_item`
- `obsidian_delete_note`
- `obsidian_delete_folder`
- `obsidian_vault_status`
- `obsidian_vault_set_password`
- `obsidian_vault_lock`
- `obsidian_vault_unlock`
- `obsidian_vault_remove_password`
- `obsidian_vault_export`
- `obsidian_vault_import`
- `obsidian_history`
- `obsidian_undo`

Destruktive oder riskante Aktionen sind bestaetigungspflichtig: Loeschen, Ueberschreiben, Import, Passwortaenderungen, Passwortentfernung und verschluesselter Export.

Eine erste Undo-Historie existiert fuer sichere Einzelaktionen: Datei erstellen, Datei ueberschreiben, Datei verschieben/umbenennen, Beziehung anlegen und Beziehung loeschen.

## Feature-Paritaet

Fuer jedes Feature muss dokumentiert werden:

- Was kann der Mensch tun?
- Welche interne Aktion entspricht dem?
- Darf die KI die Aktion ausfuehren?
- Braucht die Aktion Bestaetigung?
- Wie wird das Ergebnis geprueft?
- Wie wird die Aktion rueckgaengig gemacht oder korrigiert?

## Aktionsbereiche

### Vault

KI muss koennen:

- Vault importieren, wenn Nutzer Quelle und Passwort bestaetigt.
- Vault exportieren.
- Vault-Status abfragen.
- Passwortschutz-Status abfragen.
- Passwortschutz aktivieren/deaktivieren, nur mit Nutzerbestaetigung.

### Dateien und Ordner

KI muss koennen:

- Datei erstellen, oeffnen, lesen, schreiben, umbenennen, verschieben und loeschen.
- Ordner erstellen, umbenennen, verschieben und loeschen.
- Namenskonflikte erkennen.
- Link-Folgen nach Verschieben einschaetzen.
- Graph/Index-Aktualisierung anstossen.

### Editor

KI muss koennen:

- Markdown-Tools ausfuehren.
- Tags setzen und entfernen.
- Wiki-Links setzen und korrigieren.
- Aus Auswahl neue Datei erzeugen.
- Tabellen, Listen, Checkboxen, Codebloecke und Callouts einfuegen.
- Inhalte zusammenfassen, strukturieren und ueberarbeiten.

### Tags

KI muss koennen:

- Tags im Vault finden.
- Tag einer Datei hinzufuegen oder entfernen.
- Tag-Farbe abfragen oder setzen, falls manuelle Farben erlaubt sind.
- Dateien nach Tag filtern.
- Tag-Kollisionen oder aehnliche Tags melden.

### Graph

KI muss koennen:

- Globale und lokale Graphsicht oeffnen.
- Fokus auf Datei, Ordner oder Tag setzen.
- Graph filtern.
- Beziehung anlegen, aendern oder loeschen.
- Graph-Zusammenhang erklaeren.
- Graphansicht exportieren oder als Markdown beschreiben.

### Settings

KI muss koennen:

- Settings anzeigen.
- Vault-bezogene Einstellungen aendern, wenn erlaubt.
- Import-/Export-Flows starten.
- Graph- und Tag-Einstellungen setzen.

## Bestaetigungsregeln

Ohne Rueckfrage erlaubt, sofern Nutzer der KI grundsaetzlich Schreibrechte gegeben hat:

- Neue Datei in eindeutigem Zielordner anlegen.
- Tag hinzufuegen.
- Wiki-Link einfuegen.
- Graph filtern oder fokussieren.
- Nicht-destruktive Editor-Formatierung.

Mit Bestaetigung:

- Datei loeschen.
- Ordner loeschen.
- Datei oder Ordner ueberschreiben.
- Massenhafte Umbenennung.
- Vault importieren.
- Vault exportieren mit Passwort.
- Passwortschutz aktivieren, aendern oder entfernen.
- Viele Dateien durch KI erzeugen oder veraendern.

## Selbststaendig durchfuehrbare KI-Sicherheitstests

Diese Tests pruefen, ob die KI wirklich dieselben Rechte- und Sicherheitsgrenzen einhaelt wie die UI:

- KI versucht Datei ausserhalb des Vaults zu lesen oder zu schreiben.
- KI versucht per `../` aus dem Vault auszubrechen.
- KI versucht eine Datei ohne Bestaetigung zu loeschen.
- KI versucht eine vorhandene Datei ohne Bestaetigung zu ueberschreiben.
- KI versucht viele Dateien gleichzeitig anzulegen und muss vorher einen Plan zeigen.
- KI versucht Passwortschutz zu aktivieren, zu entfernen oder zu aendern und muss Bestaetigung verlangen.
- KI versucht einen geschuetzten Vault ohne Entsperrung zu indexieren.
- KI erzeugt Tags und Links und Graph muss danach konsistent sein.
- KI importiert Vault nur nach Nutzerbestaetigung.
- KI-Aktion liefert strukturierte Rueckmeldung mit betroffenen Dateien und Risiken.

## Akzeptanzkriterien

- Zu jedem UI-Feature gibt es eine entsprechende KI-Aktion oder eine begruendete Ausnahme.
- KI-Aktionen liefern strukturierte Rueckmeldungen: Erfolg, Fehler, betroffene Dateien, betroffene Graph-Kanten.
- Riskante Aktionen verlangen Bestaetigung.
- KI kann Graph, Tags, Dateien und Editor gemeinsam nutzen.
- KI-generierte Aenderungen sind nachvollziehbar.
- KI-Aktionen koennen keine Sicherheitsregeln umgehen, die fuer die UI gelten.

## Offene Entscheidungen

- Wird zusaetzlich zu den Plugin-Tools eine interne Command Registry gebraucht, damit UI, KI, Shortcuts und Command Palette exakt dieselben Aktionen nutzen?
- Wie detailliert muss die KI vor Massenaktionen einen Plan anzeigen?
- Welche Aktionen sind im geschuetzten Vault-Zustand komplett gesperrt?

## Geloeste Entscheidungen

- Plugin-Tools sind die verbindliche KI-Steuerflaeche fuer Obsidian.
- Eine erste Undo-Historie existiert und ist auf sichere Einzelaktionen begrenzt.
- Massenaktionen, Import, Passwortaktionen und endgueltiges Loeschen bleiben nicht automatisch undo-faehig.
