# UI: Graph-Switch, Settings-Zahnrad und kleines Settings-Menue

## Betroffene Punkte

- 5: Graph-Switch nach oben neben Minimieren verschieben und Settings-Zahnrad dazwischen.
- 6: Settings-Menue hat Button zum Import und Export von Vaults.
- 6b: Passwortschutz muss fuer die Vault einstellbar sein.

## Ziel

Die wichtigsten Plugin-Steuerungen sollen an einer erwartbaren Stelle sitzen: oben im Plugin-Fenster. Der Graph-Switch, das Settings-Zahnrad und Minimieren bilden eine kleine Kontrollgruppe.

## Aktueller Stand

Umgesetzt sind:

- Header-Kontrollgruppe rechts oben mit Graph-Switch, Settings-Zahnrad, Minimieren und Schliessen.
- Graph-Switch ist ein Toggle zwischen Editor- und Graph-Modus.
- Settings ist ein Popover-Menue.
- Das Menue enthaelt Vault importieren, Vault exportieren, Passwort setzen, Passwort entfernen und Graph-Reset.
- Import, Passwortschutz und Passwortentfernung laufen ueber bestaetigte Aktionen.
- Menue schliesst per Escape und Klick ausserhalb.
- Mobile bleibt der Graph-Switch bedienbar: die Textlabels werden kompakt ausgeblendet, der Toggle selbst bleibt sichtbar.
- Settings-Menue, Graph-Kontrollen und Toolbar sind durch statische UI-Smoke-Tests gegen unbeabsichtigtes Entfernen abgesichert.

## Zielanordnung

Empfohlene Reihenfolge rechts oben:

1. Graph-Switch.
2. Settings-Zahnrad.
3. Minimieren.

Der Graph-Switch soll direkt sichtbar sein, weil er zwischen Schreib- und Visualisierungsmodus wechselt. Das Settings-Zahnrad soll keine grosse Einstellungsseite erzwingen, sondern ein kleines Menue fuer Vault-nahe Aktionen oeffnen.

## Settings-Menue: erste Version

Inhalte:

- Vault importieren.
- Vault exportieren.
- Passwortschutz aktivieren/deaktivieren.
- Passwort aendern, falls aktiv.
- Tag-Farben zuruecksetzen oder spaeter verwalten.
- Graph-Ansicht zuruecksetzen.

Nicht in die erste Version:

- Vollstaendige globale App-Einstellungen.
- Theme-Editor.
- Modell-/KI-Provider-Auswahl.

## UX-Regeln

- Import/Export sind Aktionen, keine Toggles.
- Passwortschutz ist klar als Vault-bezogen markiert.
- Gefaehrliche Aktionen brauchen Bestaetigung.
- Fehlermeldungen muessen knapp und konkret sein.
- Menue muss per Escape und Klick ausserhalb schliessen.
- Bedienung per Tastatur muss moeglich sein.

## Akzeptanzkriterien

- Graph-Switch sitzt oben neben den Fensterkontrollen.
- Settings-Zahnrad oeffnet ein kleines Menue.
- Menue enthaelt Import und Export.
- Passwortschutz-Aktion ist sichtbar, aber zeigt keine Passwoerter.
- Menue ueberlappt keine wichtigen Inhalte unlesbar.
- Mobile Darstellung bleibt bedienbar.

## Offene Entscheidungen

- Soll Import/Export direkt starten oder zuerst einen Wizard oeffnen?
- Wo werden Vault-spezifische Settings gespeichert?

## Geloeste Entscheidungen

- Graph-Switch ist ein Toggle.
- Settings-Menue ist ein Popover.
- Vault-nahe Metadaten wie Beziehungen und History liegen vault-lokal unter `.obsidian`.
- Import/Export bleiben in Phase 3 direkte bestaetigte Aktionen; ein Wizard ist ein spaeterer UX-Ausbau, falls Import-Konflikte oder grosse Vaults mehr Vorschau brauchen.

## Do Later

- Tag-Farbverwaltung als eigenes UI.
- Vollstaendige globale Plugin-Einstellungsseite.
