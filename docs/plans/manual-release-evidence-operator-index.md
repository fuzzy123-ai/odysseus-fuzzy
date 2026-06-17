# Manual Release Evidence Operator Index

Stand: 2026-06-17

Status: **REL42A kompakter Operator-Index fuer die letzten externen 1.0-Gates**

Quellen:

- `docs/plans/provider-proof-operator-runbook.md`
- `docs/plans/export-import-rebuild-operator-runbook.md`
- `docs/plans/1.0-manual-release-evidence-runbook.md`

Dieser Index fasst die letzten offenen manuellen `1.0.0`-Evidence-Schritte fuer Charlie oder einen Operator zusammen. Er ersetzt nicht die Detail-Runbooks und behauptet keine erledigte Evidence. Ziel ist nur, die Reihenfolge, Stop-Punkte und Eintragsorte fuer die beiden verbleibenden externen Gates kompakt sichtbar zu machen.

Artefakt-Hilfen fuer Morning-Status und Handoff:

- Contract: `docs/plans/manual-release-evidence-artifact-contract.md`
- Handoff-Template: `docs/plans/manual-release-evidence-artifact-handoff-template.md`

Wichtig:

- Diese Artefakt-Hilfen sind Status- und Gap-Werkzeuge.
- Sie ersetzen keine echte beobachtete manuelle Evidence und erzeugen kein externes `1.0`-Go.

## Ziel

Dieser Operator-Index ist der kurze Einstieg fuer die letzten externen `1.0`-Gates:

1. Provider Proof
2. Export / Import / Rebuild Proof

Er ist:

- kein Evidence-Log
- kein Go-Report
- kein Ersatz fuer echte beobachtete Durchlaeufe

## Offene externe Gates

Aktuell bleiben fuer externes `1.0.0` offen:

- Provider Proof
- Export / Import / Rebuild Proof

Solange eines dieser Gates nur `Partial` oder `No-Go` ist, bleibt der Gesamtstatus:

- `No-Go fuer externes 1.0 Release`

## Reihenfolge

Die empfohlene Reihenfolge lautet:

## 1. Provider Proof

Nur mit harmloser und sicherer Testfrage arbeiten.

Ziel:

- Default-Modell nachvollziehbar pruefen
- Fallback nachvollziehbar pruefen oder ehrlich als nicht verfuegbar markieren
- lokales oder DeepSeek-Szenario pruefen oder ehrlich als nicht verfuegbar markieren

Detail-Runbook:

- `docs/plans/provider-proof-operator-runbook.md`

## 2. Export / Import / Rebuild Proof

Nur mit kleinem kontrollierten Test-Vault arbeiten.

Ziel:

- keinen produktiven Vault beruehren
- Export beobachten
- Import nur in klarer Nicht-Produktivumgebung pruefen
- Rebuild gegen Source-vs-Derived-Trennung beobachten

Detail-Runbook:

- `docs/plans/export-import-rebuild-operator-runbook.md`

## Gemeinsame Sicherheitsregeln

Vor beiden Gates gelten dieselben Grundregeln:

- keine Secrets loggen
- keine sensiblen Quellen verwenden
- keine produktiven Nutzerartefakte beruehren
- keine stillen Source-Writes akzeptieren
- keine Derived-Daten als menschliche Quellen behandeln
- keine Unit-Tests als echte manuelle Evidence verkaufen

Wenn eine dieser Regeln verletzt wird:

- Lauf stoppen
- kein `Go` behaupten
- Blocker dokumentieren

## Entscheidungslogik

Externe `1.0`-Freigabe ist nur dann `Go`, wenn:

- Provider Proof echte Evidence hat
- Export / Import / Rebuild Proof echte Evidence hat
- beide Gates jeweils `Go` sind

Wenn eines der beiden Gates:

- `Partial` ist
- `No-Go` ist
- oder gar nicht beobachtet wurde

dann bleibt der Gesamtstatus:

- `No-Go fuer externes 1.0 Release`

## Tagesstart-Checkliste fuer Charlie oder Operator

Diesen Block zu Beginn des manuellen Laufs verwenden:

```text
REL42 Operator Start Checklist
Datum:
Tester:
Branch: fuzzy/dev
Commit:

Vor Provider Proof:
- laufender Server bestaetigt: ja | nein
- authentifizierte Session verfuegbar: ja | nein
- harmlose Testfrage vorbereitet: ja | nein
- keine Secrets im Sichtfeld: ja | nein
- Query-Index ready oder bewusst markiert: ja | nein

Vor Export / Import / Rebuild:
- kleiner Test-Vault bestaetigt: ja | nein
- keine produktiven Nutzerartefakte im Scope: ja | nein
- Zielumgebung fuer Import klar nicht-produktiv: ja | nein
- Backup-/Rollback-Pfad klar: ja | nein
- Source-vs-Derived-Trennung verstanden: ja | nein
```

## Handoff-Block fuer morgens

Diesen Block verwenden, bevor die eigentlichen Gates angefasst werden:

```text
REL42 Morning Handoff
Datum:
Tester:
Branch: fuzzy/dev
Commit:
Naechster Gate-Schritt: Provider Proof | Export / Import / Rebuild Proof

Vor dem Lauf pruefen:
- keine Secrets sichtbar
- keine sensiblen Quellen fuer Provider Proof
- kein produktiver Vault fuer Export / Import / Rebuild
- Zielumgebung klar
- Ergebnis wird nur im echten Evidence-Log dokumentiert

Ergebnis eintragen in:
- docs/plans/1.0-manual-release-evidence-log.md

Sofort stoppen wenn:
- Secrets sichtbar werden
- Query-Index unklar und nicht sauber markierbar ist
- produktiver Vault oder unklare Zielumgebung betroffen ist
- unbekannte Writes oder unerwartete Diffs auftauchen
- Fallback oder Providerpfad nicht erklaerbar ist
```

## Wo das Ergebnis dokumentiert wird

Die echte beobachtete Evidence gehoert spaeter nur in:

- `docs/plans/1.0-manual-release-evidence-log.md`

Dieser Index selbst bleibt:

- read-only Orientierung
- kein Ersatz fuer das Evidence-Log

## Nicht-Ziele

Dieser Index fuehrt bewusst nicht aus:

- keine echten Provider-Aktionen
- keine echten Export-/Import-/Rebuild-Aktionen
- keine Release-Freigabe
- keine Evidence-Markierung als erledigt

Der Index beschreibt nur die kompakte Reihenfolge, Sicherheitsregeln und Eintragslogik fuer die letzten externen manuellen `1.0`-Gates.
