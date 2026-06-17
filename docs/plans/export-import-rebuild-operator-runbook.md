# Export Import Rebuild Operator Runbook

Stand: 2026-06-17

Status: **REL41A operator-taugliches Runbook fuer den offenen Export/Import/Rebuild Proof**

Quellen:

- `docs/plans/1.0-evidence-release-checklist.md`
- `docs/plans/1.0-manual-release-evidence-runbook.md`

Dieses Runbook beschreibt den noch offenen manuellen Export/Import/Rebuild Proof fuer `1.0.0`. Es ist bewusst keine automatische Testanweisung und ersetzt keine echte beobachtete Release-Evidence. Ziel ist, mit einem kleinen kontrollierten Test-Vault sicher zu belegen, dass Export, Import und Rebuild nachvollziehbar funktionieren, ohne produktive Nutzerartefakte zu beruehren, menschliche Quellen still zu ueberschreiben oder einen Unit-Test mit echter manueller Evidence zu verwechseln.

## Ziel

Der Export/Import/Rebuild Proof ist manuelle Release-Evidence.

Er ist:

- kein Unit-Test
- kein automatischer Go-Generator
- kein Freibrief fuer echte Nutzerdaten
- keine Einladung, Derived-Daten in menschliche Quellen zurueckzuschreiben

Ein externes `1.0.0` bleibt `No-Go`, bis dieser Gate-Lauf wirklich beobachtet und sauber dokumentiert wurde.

## No-Go-Hinweis

Dieses Runbook erzeugt selbst kein `Go`.

Ein `Go` entsteht erst, wenn:

- der Lauf gegen einen kleinen kontrollierten Test-Vault wirklich durchgefuehrt wurde
- Datum, Commit, Ergebnis, Blocker und Evidence festgehalten wurden
- keine Stop-Regel ausgelöst wurde

## Voraussetzungen

Vor dem Lauf muss mindestens folgendes klar sein:

- kleiner kontrollierter Test-Vault vorhanden
- keine produktiven Nutzerartefakte im Scope
- Backup- oder Rollback-Pfad ist vorbereitet
- authentifizierte Session ist verfuegbar
- Ziel-Commit auf `fuzzy/dev` ist bekannt
- Zielumgebung fuer Import und Rebuild ist klar benannt

## Sicherheitsregeln

Immer einhalten:

- keine produktiven Nutzerdaten verwenden
- keine stillen Source-Writes akzeptieren
- keine Derived-Daten als menschliche Quelle zurueckschreiben
- keine unklare Zielumgebung verwenden
- keine manuellen Freigaben aus Tests oder rohen Statusrouten ableiten

## Test-Vault vorbereiten

Der Test-Vault soll klein, kontrolliert und leicht sichtpruefbar sein.

## Minimaler Inhalt

Empfohlen:

- 2 bis 5 kleine Markdown-Dateien
- eindeutige Titel
- wenige Tags
- 1 bis 2 einfache Links zwischen Dateien
- klare, kleine Metadaten

Nicht verwenden:

- echte persoenliche Notizen
- geheime Daten
- grosse Attachments
- unklare Altartefakte

## Vorher-Snapshot-Notiz

Vor dem Lauf kurz dokumentieren:

- Dateinamen
- erwartete Tags
- erwartete Links
- erwartete Frontmatter- oder Metadatenfelder
- erwartete Anzahl an Quelldateien

Empfohlene Kurznotiz:

`Vorher-Snapshot: 3 Quelldateien, 2 Tags, 2 interne Links, keine Binaerdateien, keine Derived-Ausgabedateien.`

## Schrittfolge fuer Export

## 1. Ziel und Scope pruefen

Vor Export bestaetigen:

- Test-Vault ist wirklich Testmaterial
- Exportziel ist klar und kontrolliert
- keine produktiven Dateipfade sind eingebunden

## 2. Export ausfuehren

Route oder Kommando nur als Operator-Anweisung nutzen.

Nicht in diesem Runbook ausfuehren, aber spaeter kontrolliert pruefen:

- Export-Route oder Export-Befehl starten
- Ergebnis-Artefakt identifizieren

## 3. Export dokumentieren

Zu notieren:

- ob ein Manifest, Archiv oder vergleichbares Ergebnis erzeugt wurde
- Dateiname oder Artefaktname
- grobe Counts
- offensichtliche Warnungen

Nicht zu notieren:

- komplette sensible Dateiinhalte
- ganze Export-Payloads

## Export-Go/No-Go

`Go`, wenn:

- Export gegen Test-Vault erfolgreich und nachvollziehbar abgeschlossen wurde
- Artefakt und Counts plausibel sind

`Partial`, wenn:

- Export prinzipiell laeuft, aber Artefakt oder Counts nicht vollstaendig sichtpruefbar sind

`No-Go`, wenn:

- produktiver Pfad im Spiel ist
- unklare Artefakte entstehen
- Export offensichtliche Datenabweichungen zeigt

## Schrittfolge fuer Import

## 1. Kontrollierte Zielumgebung bestaetigen

Vor Import klar festhalten:

- Ziel ist nicht produktiv
- Ziel ist leer oder bewusst vorbereitet
- keine echten Quellen werden ueberschrieben

## 2. Dry-Run oder Preview nutzen, falls verfuegbar

Wenn Preview oder Dry-Run existiert:

- zuerst Preview nutzen
- keine Schreibaktion akzeptieren, bevor Ziel und Inhalt plausibel sind

Wenn keine Preview verfuegbar ist:

- Risiko explizit dokumentieren
- bei Unsicherheit stoppen

## 3. Import kontrolliert pruefen

Nur gegen die kontrollierte Zielumgebung.

Zu erfassen:

- ob Import startet
- ob Zielumgebung plausibel bleibt
- ob Quelldateien oder Counts erkennbar sind
- ob unerwartete Ueberschreibsignale auftauchen

## Import-Go/No-Go

`Go`, wenn:

- Import in klarer Nicht-Produktivumgebung nachvollziehbar funktioniert
- keine echten Quellen ueberschrieben werden

`Partial`, wenn:

- Preview plausibel ist, echter Import aber bewusst noch nicht ausgefuehrt wurde

`No-Go`, wenn:

- Zielumgebung unklar ist
- Route ohne ausreichende Kontrolle schreibt
- produktive oder menschliche Quellen betroffen sein koennten

## Schrittfolge fuer Rebuild-Proof

## 1. Rebuild-Ziel klar trennen

Vor Rebuild festhalten:

- welche Daten menschliche Quellen sind
- welche Daten Derived-Daten sind
- was sich veraendern darf und was nicht

Regel:

- Rebuild darf Derived-Daten neu erzeugen
- Rebuild darf menschliche Quellen nicht still umschreiben

## 2. Rebuild oder Proof-Route ausfuehren

Nur als Operator-Anweisung fuer spaeteren echten Lauf:

- Rebuild-Proof oder Reindex-Route starten
- Ergebnisdaten beobachten

## 3. Counts und Samples dokumentieren

Zu erfassen:

- Count vor und nach dem Lauf
- kleine Stichproben fuer Links, Tags oder Derived-Ergebnisse
- offensichtliche Abweichungen

Nicht zu erfassen:

- komplette Vault-Inhalte
- sensible Volltexte

## Rebuild-Go/No-Go

`Go`, wenn:

- Quellen erhalten bleiben
- Derived-Daten nachvollziehbar rebuildbar sind
- keine stillen Source-Writes auftreten

`Partial`, wenn:

- Rebuild technisch laeuft, aber Count- oder Sample-Sicht noch lueckenhaft ist

`No-Go`, wenn:

- menschliche Quellen veraendert werden
- Derived und Source nicht sauber trennbar bleiben
- unerwartete Diffs oder Datenverlust sichtbar werden

## Ergebnis-Matrix

## Go

`Go` fuer den Export/Import/Rebuild Proof nur, wenn:

- Test-Vault kontrolliert und klein war
- Export nachvollziehbar war
- Import in klarer Zielumgebung nachvollziehbar war
- Rebuild Sources und Derived sauber getrennt liess
- keine Stop-Regel ausgelöst wurde

## Partial

`Partial`, wenn:

- Teilpfade bewusst nur als Preview oder unter eingeschraenkten Bedingungen gelaufen sind
- keine falsche Vollfreigabe behauptet wird

## No-Go

`No-Go`, wenn:

- produktive Pfade beruehrt wurden oder beruehrt werden koennten
- Datenverlust sichtbar wird
- stille Source-Writes auftreten
- Zielumgebung oder Auth-Lage unklar ist

## Copy/Paste Evidence-Block

Diesen Block fuer `docs/plans/1.0-manual-release-evidence-log.md` verwenden:

```text
Gate: Export / Import / Rebuild Proof
Datum:
Tester:
Branch: fuzzy/dev
Commit:
Umgebung:
Test-Vault: klein | kontrolliert | nicht-produktiv
Ergebnis: Go | Partial | No-Go
Blocker:
Evidence-Link:
Kurznotiz:
- Vorher-Snapshot notiert: ja | nein
- Export ausgefuehrt: ja | nein
- Export-Artefakt/Manifest plausibel: ja | nein
- Import ausgefuehrt oder Preview genutzt: ja | nein
- Zielumgebung klar nicht-produktiv: ja | nein
- Rebuild-Proof ausgefuehrt: ja | nein
- Source und Derived klar getrennt: ja | nein
- stille Source-Writes beobachtet: ja | nein
- Datenverlust beobachtet: ja | nein
- unerwartete Diffs beobachtet: ja | nein
```

## Stop-Regeln

Sofort stoppen und nicht als `Go` dokumentieren, wenn:

- produktives Vault im Spiel ist
- unbekannte Writes auftreten
- Datenverlust sichtbar wird
- Zielumgebung unklar ist
- Auth fehlt
- Route ohne Preview oder ausreichende Kontrolle schreibt
- unerwartete Diff-Signale auftauchen

## Nachbereitung

Nach jedem Teilpfad:

- nur kompakte Beobachtungen notieren
- keine sensiblen Inhalte in Screenshots oder Logs kopieren
- temporäre Testartefakte klar vom Rest trennen
- geaenderte Testumgebungen in sicheren Ausgangszustand bringen

## Nicht-Ziele

Dieses Runbook fuehrt bewusst nicht aus:

- keine echten Export-/Import-/Rebuild-Aktionen in diesem Slice
- keine Runtime-Aenderung
- keine Import-/Export-Codeanpassung
- keine Test-Aenderung
- keinen Eintrag ins echte Evidence-Log ohne echten beobachteten Lauf

Das Runbook beschreibt nur, wie der offene manuelle Export/Import/Rebuild Proof sicher und nachvollziehbar belegt werden soll.
