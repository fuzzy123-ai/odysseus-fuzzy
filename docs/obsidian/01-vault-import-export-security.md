# Vault Import, Export und Passwortschutz

## Betroffene Punkte

- 6: Settings-Menue hat Import und Export von Vaults.
- 6a: Passwortgeschuetzte Vaults muessen importierbar sein.
- 6b: Passwortschutz muss fuer die Vault einstellbar sein.

## Ziel

Vaults sollen sicher, nachvollziehbar und portabel importiert und exportiert werden koennen. Passwortschutz darf keine kosmetische UI-Funktion sein, sondern muss klaeren, welche Daten wann verschluesselt sind und wie ein Nutzer wieder an seine Daten kommt.

## Funktionsumfang

### Import

Der Import soll mindestens unterstuetzen:

- Einen normalen Ordner mit Markdown-Dateien.
- Ein ZIP-Archiv eines Vaults.
- Ein passwortgeschuetztes Vault-Archiv.
- Bestehende Ordnerstruktur inklusive Unterordnern.
- Assets wie Bilder, PDFs oder Anhange, sofern das Plugin diese bereits akzeptiert.

Beim Import sollte das Plugin einen Vorab-Check zeigen:

- Anzahl erkannter Markdown-Dateien.
- Anzahl erkannter Assets.
- Potenzielle Namenskonflikte.
- Ob ein Passwort benoetigt wird.
- Ob Dateien ausserhalb des erwarteten Vault-Roots referenziert werden.

### Export

Der Export soll mindestens bieten:

- Export als Ordnerstruktur oder ZIP.
- Optionaler Export mit Passwortschutz.
- Auswahl: gesamter Vault, einzelner Ordner, aktuelle Datei plus verlinkte Dateien.
- Manifest-Datei mit Metadaten wie Vault-Name, Export-Zeitpunkt, Plugin-Version und aktivem Tag-/Graph-Modell.

### Passwortschutz

Es gibt drei moegliche Schutzstufen:

1. Nur Export-Archiv ist verschluesselt.
2. Vault liegt lokal verschluesselt und wird beim Oeffnen entsperrt.
3. Einzelne Dateien oder Ordner koennen separat geschuetzt werden.

Empfehlung fuer die erste Version: Stufe 1 und Stufe 2 planen, aber zuerst Stufe 1 implementieren. Stufe 2 braucht deutlich mehr Sicherheits- und UX-Entscheidungen.

## UX-Plan

Im Settings-Menue:

- Button: Vault importieren.
- Button: Vault exportieren.
- Toggle oder Aktion: Passwortschutz fuer diesen Vault aktivieren.
- Aktion: Passwort aendern.
- Aktion: Passwortschutz entfernen.

Wichtig: Kein Passwort in Logs, URLs, Dateinamen, Kommandozeilenargumenten oder Fehlermeldungen anzeigen.

## Sicherheitsanforderungen

- Passwoerter nie im Klartext speichern.
- Export-Passwort nur im Import-/Export-Fluss im Speicher halten.
- Import muss Pfad-Traversal verhindern, z.B. Archiv-Eintraege mit `../`.
- Symlinks und absolute Pfade muessen explizit behandelt oder abgelehnt werden.
- Fehler bei falschem Passwort muessen klar sein, aber keine Details ueber interne Struktur leaken.
- Bei verschluesselten Vaults muss klar sein, ob Suchindex, Tags oder Graph-Metadaten ebenfalls geschuetzt sind.

## Selbststaendig durchfuehrbare Sicherheitstests

Diese Tests kann ich spaeter lokal planen, schreiben und ausfuehren, sobald das Plugin vorhanden ist:

- ZIP mit `../escape.md` importieren und pruefen, dass keine Datei ausserhalb des Vaults geschrieben wird.
- ZIP mit absolutem Pfad importieren und Ablehnung pruefen.
- ZIP mit Windows-Pfadvarianten wie `C:\temp\escape.md` und gemischten Separatoren pruefen.
- ZIP mit Symlink-Eintraegen pruefen, sofern das Format sie enthaelt.
- ZIP-Bombe oder extrem grosses Archiv simulieren und Groessen-/Dateianzahl-Limits pruefen.
- Passwortgeschuetztes Archiv mit richtigem Passwort importieren.
- Passwortgeschuetztes Archiv mit falschem Passwort importieren und sauberen Fehler pruefen.
- Logs, Fehlermeldungen und Prozessargumente auf Passwort-Leaks pruefen.
- Export mit Passwort erzeugen und pruefen, dass Rohinhalte nicht im Archiv ohne Passwort lesbar sind.
- Import mit Namenskonflikt pruefen, dass nichts still ueberschrieben wird.
- Import in gesperrten oder nicht entsperrten Vault pruefen.
- Suchindex, Tagindex und Graph-Metadaten pruefen, ob sie geschuetzte Inhalte offenlegen.

## Akzeptanzkriterien

- Ein normaler Vault kann importiert und danach unveraendert exportiert werden.
- Ein passwortgeschuetzter Export kann mit richtigem Passwort importiert werden.
- Ein falsches Passwort bricht sauber ab.
- Importierte Ordnerstruktur bleibt erhalten.
- Namenskonflikte werden nicht still ueberschrieben.
- Passwortschutz ist in den Settings sichtbar und verstaendlich.
- Alle sicherheitskritischen Import-/Export-Tests bestehen.
- Kein Passwort erscheint in Logs, URLs, Dateinamen, Prozessargumenten oder Fehlermeldungen.

## Risiken

- Halbfertige Verschluesselung kann falsche Sicherheit vermitteln.
- Automatische Indizes koennen geschuetzte Inhalte indirekt offenlegen.
- Passwortverlust kann Daten unrettbar machen, falls echte lokale Verschluesselung genutzt wird.
- Import grosser Vaults kann UI blockieren, wenn kein Hintergrundjob genutzt wird.

## Offene Entscheidungen

- Welches Archivformat ist Ziel fuer passwortgeschuetzten Export?
- Soll ein verschluesselter Vault ohne Passwort im Graph ueberhaupt sichtbar sein?
- Werden Tags und Graph-Kanten in einer Manifest-Datei gespeichert oder aus Markdown neu berechnet?
- Brauchen wir Recovery-Hinweise oder bewusst keine Recovery-Funktion?
