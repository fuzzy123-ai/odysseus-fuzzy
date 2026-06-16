# Odysseus Lens Rename Plan Contract

Stand: 2026-06-16

Status: **LENS6A UX-/Produktvertrag fuer `0.15.x Odysseus Lens Rename Plan`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/odysseus-lens-ui-memory-interaction.md`
- `docs/plans/lens-ui-ux-contract.md`
- `docs/plans/memory-read-write-tabs-contract.md`
- `docs/plans/tag-chip-system-contract.md`
- `docs/plans/document-intelligence-bar-contract.md`
- `docs/plans/review-audit-spark-redesign-contract.md`

Dieser Vertrag definiert die sichere Naming- und Migrationsstrategie fuer die Produktlinie `Odysseus Lens`. Der Slice fuehrt bewusst keine UI-, Backend-, Runtime-, Test- oder Dateiumbenennungen aus. Er friert nur die Produktentscheidung, die Aliasstrategie, die Migrationsstufen und die Gate-Kriterien ein, damit ein spaeterer `LENS6B`- oder Rename-Slice ohne API-Bruch geplant werden kann.

## Produktentscheidung

Die Produktentscheidung lautet:

- `Obsidian` bleibt Host-, Plugin- und Integrationskontext.
- `Odysseus Lens` wird die sichtbare Nutzeroberflaeche und der primaere Produktbegriff in der UI.

Das bedeutet:

- Nutzer sollen spaeter mit `Lens` als sichtbarer Arbeitsoberflaeche sprechen koennen.
- Interne Plugin-, Route- und Host-Begriffe duerfen weiter `obsidian` enthalten, solange die Nutzeroberflaeche klar auf `Odysseus Lens` zeigt.
- Die Umstellung ist zuerst eine Copy- und Navigationsentscheidung, nicht sofort eine technische Rename-Migration.

## Leitregel

Sichtbare Sprache darf sich schneller aendern als technische Identitaet.

Das bedeutet:

- UI-Labels, Docs und Onboarding-Texte duerfen zuerst auf `Odysseus Lens` gehen.
- interne IDs, CSS-Klassen, Handler, Routen und Plugin-Namen bleiben stabil, bis ein eigener Gate-Slice sie absichert.
- kein Nutzer darf durch die Umstellung denken, dass ein neues Plugin oder eine zweite App installiert werden muss.

## Sichtbare Labels vs stabile interne Begriffe

## Sichtbar spaeter aenderbar

Diese sichtbaren Begriffe duerfen spaeter auf `Odysseus Lens` oder `Lens` umgestellt werden:

- Titel der Oberflaeche
- sichtbare Bereichsnamen in Navigation und Panels
- Hilfe- und Empty-State-Texte
- Dokumentationssprache fuer die Produktoberflaeche
- Release- und Known-Limits-Texte mit Nutzerfokus

Beispiele:

- `Obsidian Sidebar` darf in Nutzertexten zu `Odysseus Lens` werden
- `Open Obsidian Panel` darf spaeter zu `Lens oeffnen` werden
- `Obsidian memory tools` darf spaeter als `Lens-Gedaechtnis` beschrieben werden

## Stabil zu haltende interne Begriffe

Diese Begriffe muessen fuer die erste Umstellungsphase stabil bleiben:

- interne JS-Handler- und Funktionsnamen
- CSS-Klassen und Datenattribute, solange kein eigener Refactor-Slice existiert
- Plugin-Ordner- und Dateinamen
- Route-, API- und Event-Namen
- Test-Selektoren, solange keine abgesicherte Testmigration erfolgt
- externe Integrationspunkte oder gespeicherte Einstellungen

Beispiele:

- `plugins/obsidian/...` bleibt Pfad
- `obsidian` in bestehenden Route- oder Handlernamen darf intern bestehen bleiben
- vorhandene Plugin- oder Host-Konfiguration bleibt stabil

## Aliasstrategie

Die Umstellung braucht explizite Aliase statt stiller Brueche.

## UI-Aliase

In der UI gilt spaeter:

- `Odysseus Lens` ist der primaere Produktname
- `Obsidian` wird nur dort sichtbar, wo der Host-Kontext erklaert werden muss

Empfohlene Regel:

- primaerer Titel: `Odysseus Lens`
- erklaerender Untertext oder Settings-Kontext: `Laeuft im Obsidian-Plugin`

## Docs-Aliase

In Doku und Runbooks gilt:

- neue produktbezogene Doku schreibt `Odysseus Lens`
- bestehende Host- oder Integrationshinweise duerfen `Obsidian-Plugin` weiter nennen
- bei Uebergangstexten ist `Odysseus Lens im Obsidian-Plugin` die sichere Bruecke

## Test-Aliase

Tests duerfen fuer eine Uebergangszeit weiter interne `obsidian`-Begriffe nutzen, wenn:

- sichtbare Nutzerlabels separat auf `Lens` geprueft werden
- keine false negatives nur wegen Copy-Aenderung entstehen
- ein spaeterer Rename-Slice explizit Testnamen, Selektoren und Textassertions uebernimmt

## Telemetrie- und Log-Aliase

Falls Logs, Debug-Ausgaben oder spaetere Telemetrie Begriffe sichtbar machen:

- nutzernahe Meldungen duerfen `Lens` zeigen
- technische Logs duerfen `obsidian` als stabilen Host-Bezeichner behalten
- keine gemischten Meldungen ohne Kontext, die wie zwei verschiedene Produkte wirken

## Migrationsstufen

Die Umstellung verlaeuft in drei Stufen.

## Stufe 1: Copy und Labels

Ziel:

- sichtbare Produktbegriffe in UI und Doku auf `Odysseus Lens` ausrichten

Erlaubt:

- Titel, Labels, Hilfetexte, Navigationstexte
- erklaerende Doku und Release-Texte

Nicht erlaubt:

- API-, Route- oder Dateinamen aendern
- interne IDs aufbrechen

## Stufe 2: Adapter und Aliase

Ziel:

- technische Altbegriffe absichern, waehrend sichtbare Sprache bereits auf `Lens` steht

Erlaubt:

- Alias- oder Mapping-Schicht in UI-Logik, falls noetig
- Tests auf neue sichtbare Sprache umstellen, ohne Integrationspunkte zu brechen
- dokumentierte Brueckentexte in Settings, Hilfe oder Migration-Hinweisen

Nicht erlaubt:

- stilles Entfernen bestehender `obsidian`-Bezeichner ohne Rueckwaertspfad

## Stufe 3: optionale spaetere harte Umbenennung

Ziel:

- nur mit eigenem Gate pruefen, ob interne Namen, Pfade oder APIs spaeter vereinheitlicht werden sollen

Nur erlaubt, wenn separat geschnitten:

- Dateiumbenennungen
- Handler- oder Klassen-Renames
- Plugin-/Route-/API-Renames
- Test-Suite-Migration

Diese Stufe ist optional und darf nur mit eigenem Go/No-Go-Gate starten.

## Risiken

## `main.js`

Risiken:

- sichtbare Labels und interne Handler sind heute voraussichtlich eng verschraenkt
- schneller Copy-Wechsel kann alte Titel, Dialoge oder Button-Texte uebersehen
- gemischte `Obsidian`- und `Lens`-Sprache kann wie zwei verschiedene Produkte wirken

## `style.css`

Risiken:

- alte Klassen oder Modifier-Namen koennen sichtbare Begriffe suggerieren
- CSS-gekoppelte Pseudo-Labels oder dekorative Titel koennen den Rename unvollstaendig machen
- spaetere visuelle Rename-Arbeit kann ohne klaren Scope zu unnötigem Layout-Churn fuehren

## `tests/test_obsidian_sidebar_static.py`

Risiken:

- Textassertions auf alte Nutzerbegriffe brechen sofort
- DOM- oder Snapshot-Annahmen koennen Host- und Produktnamen vermischen
- reine Copy-Aenderungen koennen faelschlich wie Runtime-Regressions wirken

## Plugin-Routes und Integrationspunkte

Risiken:

- externe Nutzer oder interne Tools verlassen sich auf bestehende Plugin- oder Route-Namen
- harte Rename-Operationen koennen Bookmarks, gespeicherte Einstellungen oder Dokumentation entkoppeln
- ein zu frueher API-Rename fuehrt zu vermeidbaren Bruechen ohne Nutzermehrwert

## Externe Nutzergewohnheiten

Risiken:

- bestehende Nutzer kennen das Feature als `Obsidian Plugin` oder `Obsidian Sidebar`
- ein abrupter Vollrename ohne Brueckentexte kann wie ein neues Produkt wirken
- Support, Screenshots und Runbooks koennen kurzfristig auseinanderlaufen

## Akzeptanzkriterien fuer spaeteres `LENS6B` oder einen Rename-Slice

`LENS6A` ist nur dann sauber abgeschlossen, wenn ein spaeterer Rename-Slice daraus ohne Grundsatzdebatte implementieren kann.

Mindestens klar sein muss:

- `Odysseus Lens` ist der sichtbare Produktname
- `Obsidian` bleibt der Host- und Plugin-Kontext
- sichtbare Copy darf sich vor technischen Bezeichnern aendern
- interne IDs, Routen, Pfade und APIs bleiben ohne eigenen Gate-Slice stabil
- Aliasregeln fuer UI, Docs, Tests und Logs sind beschrieben
- die Umstellung ist in Stufe 1, 2 und optionale 3 gegliedert
- ein spaeterer harter Rename braucht eigenes Go/No-Go und darf nicht implizit mit UI-Polish vermischt werden
- kein Nutzer muss wegen der Umbenennung neue Installations-, Plugin- oder Migrationsschritte erraten

## Nicht-Ziele

`LENS6A` fuehrt bewusst nicht aus:

- keine Runtime-Implementierung
- keine Pfad- oder Dateiumbenennung
- keine Plugin-Renames
- keine Route- oder API-Renames
- keine Tests
- keinen Frontend-Code
- keine Backend-Aenderungen
- keine Migration ohne Gate
- keinen Start von `LENS6B`

Der Vertrag beschreibt nur die sichere Naming- und Migrationsstrategie, mit der `Odysseus Lens` als sichtbare Produktoberflaeche eingefuehrt werden kann, waehrend `Obsidian` als Host- und Plugin-Kontext stabil bleibt.
