# Test- und Sicherheitsplan

## Grundsatz

Sicherheit steht an erster Stelle. Kein Feature gilt als fertig, wenn es ungetestete Risiken bei Pfadgrenzen, Vault-Import, Passwortschutz, Nutzerrechten, KI-Aktionen oder Datenintegritaet gibt.

Die Tests sollen nicht nur spaeter von Hand beschrieben sein. Ziel ist, dass ich sie selbststaendig planen, implementieren und ausfuehren kann, sobald das Obsidian-Plugin lokal vorhanden ist.

## Testprioritaeten

### P0: Plugin-System-Vertrag

Diese Tests bleiben nach der Migration dauerhaft Pflicht:

- Obsidian wird vom echten `PluginManager` entdeckt und geladen.
- Manifest wird ohne Ausfuehrung von Plugin-Code gelesen.
- Alle Obsidian-Routen liegen unter `/api/plugins/obsidian/...`.
- Der UI-Einstieg `/api/plugins/obsidian/app` ist im Manifest vorhanden und wird geroutet.
- Das Plugin braucht keinen alten `/api/plugins/loader.js`-Mechanismus.
- KI-Tools registrieren sich robust ueber `ctx.register_tool(...)` oder degradieren sichtbar, wenn der Tool-Registry-Teil fehlt.

### P0: Sicherheitsblocker

Diese Tests blockieren jede Freigabe:

- Pfad-Traversal bei Import, Export, Dateioperationen und KI-Aktionen.
- Absolute Pfade und Windows-/Unix-Pfadvarianten.
- Symlink- und Junction-Verhalten.
- Passwort-Leaks in Logs, URLs, Dateinamen, Fehlermeldungen und Prozessargumenten.
- Falsches Passwort bei Import oder Vault-Entsperrung.
- Ueberschreiben oder Loeschen ohne Bestaetigung.
- KI darf keine Sicherheitsregeln umgehen.
- Geschuetzte Vaults duerfen nicht unbemerkt indexiert oder im Graph offengelegt werden.

### P1: Datenintegritaet

- Import und Export erhalten Ordnerstruktur.
- Markdown-Dateien bleiben inhaltlich unveraendert, sofern keine Migration erwartet wird.
- Tags, Dateitags und Graph-Kanten werden konsistent neu berechnet.
- Verschieben und Umbenennen aktualisiert Links oder meldet notwendige Reparaturen.
- Namenskonflikte werden erkannt.
- Abgebrochene Aktionen hinterlassen keinen halb kaputten Vault-Zustand.

### P2: Obsidian-Feel und UI

- Dateiwechsel ist schnell und stabil.
- Editor, Preview und Graph bleiben synchron.
- Lokale Graphansicht folgt der aktiven Datei.
- Drag and Drop zeigt klare Drop-Ziele.
- Autocomplete fuer `#` und `[[` funktioniert per Tastatur.
- Settings-Menue bleibt auf Desktop und Mobile bedienbar.

### P3: KI-Paritaet

- Jede UI-Aktion hat eine entsprechende KI-Aktion oder eine dokumentierte Ausnahme.
- KI-Aktionen liefern strukturierte Ergebnisse.
- KI kann Graph, Tags, Dateien, Editor und Settings zusammen nutzen.
- Riskante KI-Aktionen zeigen Plan oder Bestaetigung.

## Testarten

### Unit-Tests

Geeignet fuer:

- Pfadnormalisierung.
- Tag-Parser.
- Dateiname-zu-Tag-Normalisierung.
- Markdown-Link-Parser.
- Graph-Kantenberechnung.
- Archiv-Eintrag-Validierung.
- Rechte- und Bestaetigungslogik fuer KI-Aktionen.

### Integrationstests

Geeignet fuer:

- Vault importieren.
- Vault exportieren.
- Passwortgeschuetztes Archiv importieren.
- Datei verschieben und Graph aktualisieren.
- KI-Aktion fuehrt Dateioperation aus.
- Namenskonflikt wird korrekt behandelt.
- Geschuetzter Vault blockiert Indexierung.

### Browser-/UI-Tests

Geeignet fuer:

- Datei im Tree oeffnen.
- Datei per Drag and Drop verschieben.
- Editor-Toolbar ausloesen.
- Tag-Autocomplete nutzen.
- Dateiname-Autocomplete nutzen.
- Graph-Switch bedienen.
- Settings-Menue oeffnen und Import-/Export-Aktionen erreichen.

Aktueller automatisierter Stand:

- Statische UI-Smokes pruefen, dass Plugin-UI-Loader, Sidebar/Standalone-App, zentrale Obsidian-DOM-Elemente, Header-Graph-Switch, Settings-Menue, Toolbar-Sichtbarkeit und Caret-Autocomplete-Vertraege vorhanden sind.
- TestClient-Smokes pruefen `/api/plugins/obsidian/app` und `/api/plugins/obsidian/web/main.js`, damit eine schwarze/leere App-Seite nicht als erfolgreicher HTTP-Status durchgeht.
- Ein echter Browser-Smoke gegen die laufende App braucht eine authentifizierte lokale Browser-Session; ohne Login antwortet der Auth-Layer korrekt mit `Not authenticated`.

### Manuelle Explorationschecks

Diese bleiben sinnvoll, auch wenn automatisierte Tests existieren:

- Fuehlt sich Dateiwechsel wie Obsidian an?
- Springt der Graph nervoes oder bleibt er lesbar?
- Ist der Unterschied zwischen Ordner, Unterordner und Markdown-Datei sofort sichtbar?
- Sind Bestaetigungsdialoge verstaendlich?
- Sind Fehlermeldungen hilfreich, ohne sensible Details zu leaken?

## Sicherheits-Testmatrix

| Bereich | Risiko | Test | Erwartung |
| --- | --- | --- | --- |
| Import | `../` schreibt ausserhalb des Vaults | Archiv mit Traversal-Eintrag | Import wird abgelehnt |
| Import | Absoluter Pfad | Archiv mit `C:\...` oder `/tmp/...` | Import wird abgelehnt |
| Import | ZIP-Bombe | Viele/grosse Dateien | Limit greift, sauberer Fehler |
| Passwort | Leak | Logs und Fehler durchsuchen | Passwort taucht nirgends auf |
| Passwort | Falsches Passwort | Import/Entsperrung testen | Kein Inhalt wird sichtbar |
| Dateioperation | Ueberschreiben | Datei in Ziel mit gleichem Namen | Bestaetigung oder Abbruch |
| Dateioperation | Loeschen | UI und KI loeschen Datei | Bestaetigung erforderlich |
| KI | Vault-Ausbruch | KI schreibt `../x.md` | Aktion blockiert |
| KI | Prompt-Injection in Markdown | Dokument fordert KI zu Regelbruch auf | KI ignoriert Anweisung als Daten |
| Graph | Geschuetzter Inhalt | Gesperrter Vault im Graph | Keine Inhalts-/Tag-Leaks |

## Testdaten

Wir sollten kleine feste Test-Vaults pflegen:

- `basic-vault`: einfache Ordner, Markdown-Dateien, Links und Tags.
- `nested-vault`: tiefe Unterordner, gleiche Dateinamen in verschiedenen Pfaden.
- `security-vault`: gefaehrliche Archivpfade, Symlinks/Junctions, Namenskonflikte.
- `encrypted-vault`: passwortgeschuetzter Import-/Export-Fall.
- `large-vault`: viele Dateien fuer Performance und Graph-Stabilitaet.
- `ai-vault`: Dateien mit Prompt-Injection-Inhalten und KI-Aktionsszenarien.

Aktueller Stand: Ein deterministischer Large-Vault-Fixture-Generator existiert im Plugin und liefert Graph-Baselines fuer Node-/Edge-Anzahl und Laufzeit.

## Selbststaendige Durchfuehrung

Wenn die Implementierung beginnt, kann ich pro Feature eigenstaendig:

1. Passende Testfaelle aus diesem Dokument auswaehlen.
2. Fehlende Testdaten anlegen.
3. Unit- und Integrationstests schreiben.
4. UI-Smoke-Tests mit Browser pruefen, falls ein lokaler Server laeuft.
5. Sicherheitsregressionen vor jeder groesseren Aenderung ausfuehren.
6. Testergebnisse zusammenfassen und Blocker markieren.

## Release-Gate

Ein Implementierungspaket darf erst als erledigt gelten, wenn:

- Alle P0-Sicherheitstests fuer den betroffenen Bereich bestehen.
- Keine bekannten Passwort-, Pfad- oder Rechte-Leaks offen sind.
- KI-Aktionen dieselben Sicherheitsregeln einhalten wie UI-Aktionen.
- Fehlerfaelle sauber abbrechen.
- Mindestens ein positiver End-to-End-Test fuer den Hauptworkflow besteht.

## Offene Entscheidungen

- Wo liegen die Test-Vaults im Repository?
- Welche Tests duerfen echte Verschluesselungsbibliotheken nutzen und welche arbeiten mit Testdoubles?
- Wie streng muessen Performance-Grenzen fuer grosse Vaults sein?
- Gibt es eine zentrale Command Registry, die UI- und KI-Aktionen gemeinsam testbar macht?
- Welche Tests laufen immer lokal, welche nur optional wegen Laufzeit?

## Geloeste Testentscheidungen

- Windows-Launcher- und Restart-Skripte sind Regression-Gates.
- Plugin-Tool-Registry, Tool-RAG, Dispatcher, Prompt-Schemas und Plugin-Load sind automatisiert getestet.
- Obsidian-Beziehungen, History/Undo, Tags, Graph, Vault-Sicherheit und KI-Bestaetigungen sind durch Plugin-Tests abgedeckt.
