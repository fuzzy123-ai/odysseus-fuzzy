# KI-Projektplanung in einem Vault-Ordner

## Betroffener Punkt

- 4: Die KI soll ein Projekt planen, indem sie in einem Ordner nach Wahl die noetigen Obsidian-Dokumente mit korrekten Zusammenhaengen erstellt, um ein komplexeres Programm zu visualisieren.

## Ziel

Die KI soll aus einer Projektidee eine nachvollziehbare Vault-Struktur erzeugen: Dokumente, Links, Tags und Graph-Beziehungen. Der Nutzer waehlt einen Zielordner, beschreibt das Projekt, prueft den Vorschlag und laesst dann die Dateien anlegen.

## Grundablauf

1. Nutzer waehlt Zielordner im Vault.
2. Nutzer beschreibt Projektziel, Umfang und optional Programmiersprache/Stack.
3. KI erstellt zuerst einen Plan, noch keine Dateien.
4. Plan zeigt Dokumentliste, Ordnerstruktur, Tags und Beziehungen.
5. Nutzer bestaetigt, bearbeitet oder verwirft.
6. KI legt Dokumente an.
7. Graph zeigt die erzeugte Struktur.

## Empfohlene Dokumenttypen

Fuer komplexere Softwareprojekte sollte die KI mindestens diese Dokumente vorschlagen koennen:

- Projektuebersicht.
- Anforderungen.
- Architektur.
- Moduluebersicht.
- Datenmodell.
- API/Interfaces.
- UI/UX-Flows, falls relevant.
- Risiken und offene Fragen.
- Implementierungsplan.
- Testplan.
- Entscheidungen/ADRs.
- Glossar.

Je nach Projekt:

- Deployment.
- Security.
- Performance.
- Migration.
- Betrieb/Monitoring.
- Roadmap.

## Beziehungsmodell

Die KI soll nicht nur Dateien erzeugen, sondern Beziehungen:

- `Projektuebersicht` verlinkt alle Hauptdokumente.
- `Architektur` verlinkt Module, Datenmodell und APIs.
- `Implementierungsplan` verlinkt Anforderungen, Module und Testplan.
- `Risiken` verlinkt betroffene Module.
- `Entscheidungen` verlinken Kontext und Konsequenzen.

Tags:

- Dateiname als impliziter Tag.
- Projektweiter Tag, z.B. `#mein-projekt`.
- Typ-Tags, z.B. `#architektur`, `#api`, `#test`, `#risiko`.
- Status-Tags, z.B. `#draft`, `#review`, `#final`, optional spaeter.

## KI-Ausgabeformat fuer spaetere Umsetzung

Die KI sollte intern strukturiert planen:

- Zielordner.
- Dateien mit Pfad.
- Inhaltsskizze pro Datei.
- Tags pro Datei.
- Links pro Datei.
- Begruendung fuer wichtige Beziehungen.
- Risiken/offene Fragen.

Wichtig: Der Nutzer sollte vor dem Schreiben eine Vorschau sehen. Direkter Schreibzugriff ohne Review ist fuer spaeter moeglich, aber nicht als Standard.

## Graph-Visualisierung

Nach dem Erzeugen sollte der Graph automatisch zeigen:

- Projektordner als Cluster.
- Dokumenttypen farblich oder per Icon unterscheidbar.
- Links zwischen Architektur, Modulen, Tests und Risiken.
- Gemeinsame Tags als Filter.
- Fokus auf Projektuebersicht als Einstieg.

## Akzeptanzkriterien

- Nutzer kann einen Zielordner auswaehlen.
- KI erzeugt zuerst einen bestaetigbaren Plan.
- Plan enthaelt mehrere sinnvolle Markdown-Dokumente.
- Dokumente haben Links und Tags.
- Dateien werden erst nach Bestaetigung angelegt.
- Graph zeigt die neue Projektstruktur ohne manuelles Nacharbeiten.
- Bestehende Dateien werden nicht still ueberschrieben.

## Risiken

- KI erzeugt zu viele Dateien.
- KI erzeugt generische Dokumente ohne echte Struktur.
- Links zeigen auf Dateien, die nicht existieren.
- Tags werden inkonsistent.
- Bestehende Projektstruktur wird vermischt oder ueberschrieben.

## Offene Entscheidungen

- Wie gross darf ein KI-generierter Plan maximal sein?
- Soll es Vorlagen fuer Projekttypen geben, z.B. Web-App, Python-Service, Game, Plugin?
- Soll die KI bestehende Dateien im Zielordner lesen, bevor sie plant?
- Darf die KI bestehende Dateien aktualisieren oder nur neue anlegen?
- Soll der Plan als eigenes `Projektplan.md` gespeichert werden?

