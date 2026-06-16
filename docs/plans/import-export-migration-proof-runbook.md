# Import/Export Migration Proof Runbook

Stand: 2026-06-16

Status: **MS5A Nutzer-/Ops-/Charlie-Runbook fuer `0.13.x Import/Export Migration Proof`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/memory-scale-foundation-roadmap.md`
- `docs/plans/memory-store-interface-contract.md`
- `docs/plans/memory-diagnostics-lens-contract.md`
- `docs/plans/query-budget-ux-contract.md`
- `docs/plans/postgres-pgvector-migration-contract.md`

Dieses Runbook definiert den wiederholbaren Proof-Pfad fuer eine spaetere Migration vom bestehenden Memory Store in das geplante Postgres-plus-pgvector-Zielmodell. `MS5A` beschreibt nur Proof, Vergleich, Evidence und Rollback-Sprache. Es fuehrt keine echte Migration aus, schaltet keine Runtime um und startet keine neue Datenbank.

## Ziel

Odysseus soll eine zukuenftige Migration nicht per Bauchgefuehl freigeben, sondern ueber einen kontrollierten Proof-Pfad:

- Export aus dem bestehenden Store
- Import in ein isoliertes Postgres-Zielmodell
- Read-only-Vergleich ohne Runtime-Cutover
- explizites Go/No-Go
- klarer Rollback-Pfad

Das Runbook soll:

- Nutzer und Ops sehen lassen, warum ein Proof belastbar ist
- Charlie eine klare Stop- und Freigabelogik geben
- Bob ein kleines, validierbares Import/Export-Proof-Modell vorbereiten
- Dual-Write und versteckte Wahrheitsverschiebung verhindern

## Begriffe

### `export_run_id`

Stabile Kennung fuer einen konkreten Exportlauf aus dem bestehenden Store.

### `import_run_id`

Stabile Kennung fuer einen konkreten Importlauf in das isolierte Postgres-Zielmodell.

### `source_manifest_ref`

Referenz auf den Export- oder Quell-Manifest-Snapshot, der beschreibt, was aus dem bestehenden Store gelesen wurde.

### `target_manifest_ref`

Referenz auf den Ziel-Manifest-Snapshot, der beschreibt, was im Zielmodell angekommen ist.

### `backup_ref`

Referenz auf die Sicherung, die vor dem Proof fuer die aktuelle Wahrheit erstellt oder verifiziert wurde.

### `restore_ref`

Referenz auf die dokumentierte Wiederherstellungsprozedur fuer den gesicherten Ausgangszustand.

### `count_comparison`

Vergleich der relevanten Zaehler zwischen Quelle und Ziel.

Mindestens erwartet:

- Quellen
- Chunks
- Embeddings
- Entitaeten
- Relationen
- Provenance-Eintraege

### `sample_comparison`

Gezielter Vergleich kleiner, nachvollziehbarer Stichproben zwischen Quell- und Zielzustand.

### `read_only_compare`

Vergleichsphase, in der das Zielmodell geprueft wird, ohne Runtime-Schreib- oder Leseverkehr auf das neue Ziel umzuschalten.

### `rollback_plan`

Lesbarer Plan, wie der Proof oder eine spaetere Migration sauber abgebrochen und auf die geschuetzte Wahrheit zurueckgefuehrt wird.

### `go_no_go_status`

Expliziter Freigabestatus fuer den Proof oder den naechsten Migrationsschritt.

Erlaubte Werte:

- `draft`
- `review`
- `go`
- `no_go`
- `rolled_back`
- `superseded`

### `proof_evidence_ref`

Kurze Referenz auf das Evidence-Buendel, das den Proof nachvollziehbar macht.

## Leitregel

Der Import/Export-Proof ist ein Vergleichspfad, keine Produktivumschaltung.

Das bedeutet:

- kein Dual-Write
- keine zweite gleichberechtigte Wahrheit
- keine Live-Runtime auf dem Zielmodell
- kein "wir migrieren mal schnell und schauen dann"

Die Wahrheit bleibt bis zu einem spaeter separat freigegebenen Cutover beim bestehenden Produktionspfad. Das Zielmodell ist im Proof nur Vergleichs- und Readiness-Oberflaeche.

## Proof-Ablauf

### 1. Preflight und Schutz der Ausgangswahrheit

Vor jedem Proof muss klar sein:

- welcher Ausgangsstand geprueft wird
- welche `backup_ref` dazu gehoert
- welche `restore_ref` im Notfall gilt
- welcher Worktree- und Evidence-Stand zum Proof gehoert

Wenn Backup oder Restore unklar sind, darf der Proof nicht starten.

### 2. Export aus dem bestehenden Store

Der Proof beginnt mit einem kontrollierten Export aus dem bestehenden Store.

Der Export muss mindestens liefern:

- `export_run_id`
- `source_manifest_ref`
- erkennbare Zaehler fuer Quellen, Chunks, Embeddings, Entitaeten, Relationen und Provenance
- lesbare Provenance des Exportzeitpunkts

Der Export soll nicht beweisen, dass das Ziel schon korrekt ist. Er schafft nur die belegte Quellgrundlage fuer den spaeteren Vergleich.

### 3. Import in das isolierte Postgres-Zielmodell

Der Import ueberfuehrt den Export in das geplante Zielmodell.

Der Import muss mindestens liefern:

- `import_run_id`
- `target_manifest_ref`
- Zielzaehler fuer dieselben Kernobjekte wie im Export
- lesbaren Bezug zum Ziel-Schema oder dessen Version

Wichtig:

- der Import ist isoliert
- er aendert nicht den aktiven Runtime-Pfad
- er erzeugt keinen dauerhaften Dual-Write-Zustand

### 4. Read-only-Vergleich

Nach Export und Import folgt `read_only_compare`.

Diese Phase beantwortet:

- stimmen die relevanten Counts ueberein oder sind Abweichungen erklaert
- stimmen Stichproben fachlich ueberein
- ist Provenance nachvollziehbar
- ist das Zielmodell nur Vergleichsoberflaeche und noch nicht Produktivwahrheit

`read_only_compare` darf:

- Counts vergleichen
- Stichproben vergleichen
- Schema- und Manifest-Bezuege pruefen
- Evidence fuer Charlie vorbereiten

`read_only_compare` darf nicht:

- einen Runtime-Cutover implizieren
- fehlende Mismatches sprachlich weichzeichnen
- das Ziel als bereits freigegeben verkaufen

### 5. Go/No-Go-Entscheidung

Nach dem Vergleich wird ein explizites `go_no_go_status` gesetzt.

`go` bedeutet in diesem Slice nur:

- der Proof ist fuer den naechsten isolierten Modell- oder Validierungsschritt tragfaehig

`go` bedeutet nicht:

- Produktivumschaltung
- Dual-Write-Freigabe
- Runtime-Migration abgeschlossen

### 6. Rollback-Bereitschaft

Auch wenn `MS5A` keine echte Migration ausfuehrt, muss `rollback_plan` vorhanden bleiben.

Der Rollback-Teil des Runbooks beantwortet:

- wie der Proof verworfen wird
- welche Evidence als ungueltig markiert wird
- wie der geschuetzte Ausgangszustand ueber `restore_ref` weiter die Wahrheit bleibt

## Nutzer- und Ops-Sicht

Nutzer und Ops muessen dem Proof nur dann vertrauen, wenn sie die Beweiskette knapp und klar sehen koennen.

Sichtbar sein muessen mindestens:

- welcher Ausgangsstand exportiert wurde
- welcher Zielstand importiert wurde
- welche `backup_ref` und `restore_ref` gelten
- wie `count_comparison` aussieht
- welche `sample_comparison` geprueft wurde
- ob `read_only_compare` sauber vom Runtime-Cutover getrennt blieb
- welches `go_no_go_status` entschieden wurde
- welche `proof_evidence_ref` die Belege zusammenfasst

Nicht sichtbar sein muessen:

- rohe DB-Dumps
- lange Tool-Logs
- unstrukturierte Debug-Serien

## Charlie-Sicht

Charlie braucht eine strengere Sicht als normale Nutzertexte.

Charlie darf Bob erst in einen isolierten Vergleichsmodell-Spike weiterlassen, wenn:

- `backup_ref` vorhanden und reviewbar ist
- `restore_ref` vorhanden und reviewbar ist
- `source_manifest_ref` und `target_manifest_ref` beide vorhanden sind
- `count_comparison` vollstaendig oder sauber erklaert ist
- `sample_comparison` fuer den gewaehlten Scope nachvollziehbar ist
- `read_only_compare` klar keinen Runtime-Cutover behauptet
- `proof_evidence_ref` die Belege kompakt zusammenhaelt
- `go_no_go_status` explizit auf `go` oder bewusst auf `review` steht

Charlie muss stoppen, wenn:

- Backup oder Restore fehlen
- Counts abweichen und keine belastbare Erklaerung existiert
- Stichproben fachlich nicht uebereinstimmen
- Provenance unklar bleibt
- der Worktree dirty ist
- Runtime-Cutover behauptet oder still vorausgesetzt wird

## Stop-Regeln

Der Proof ist `no_go` oder muss sofort gestoppt werden, wenn mindestens einer dieser Faelle eintritt:

- fehlender `backup_ref`
- fehlender `restore_ref`
- `count_comparison` mit Mismatch ohne lesbare Erklaerung
- `sample_comparison` mit fachlichem Widerspruch
- unklare oder gebrochene Provenance
- dirty Worktree waehrend des Proof- oder Review-Schritts
- Sprache, die Runtime-Umschaltung oder aktive Zielwahrheit behauptet

`warning`-artige Lagen sind in diesem Runbook nicht genug, wenn sie die Wahrheitsfrage beruehren. Bei Wahrheits-, Restore- oder Vergleichsunklarheit ist die Entscheidung `no_go`.

## Evidence-Paket

Ein spaeteres Proof-Evidence-Buendel sollte mindestens enthalten:

- `export_run_id`
- `import_run_id`
- `source_manifest_ref`
- `target_manifest_ref`
- `backup_ref`
- `restore_ref`
- `count_comparison`
- `sample_comparison`
- `read_only_compare`-Status
- `rollback_plan`
- `go_no_go_status`
- `proof_evidence_ref`

Empfohlene Zusatzbelege:

- Schema- oder Versionsreferenz des Zielmodells
- Index- oder Rebuild-Hinweis, falls fuer Embeddings relevant
- kurze Notiz zu bewusst tolerierten und erklaerten Differenzen

## Nicht-Ziele

`MS5A` fuehrt bewusst nicht aus:

- keine echte Migration
- keine Runtime-Umschaltung
- kein dauerhaftes Dual-Write
- kein Start einer neuen Datenbank
- keine Docker- oder Compose-Arbeit
- keine Import-/Export-Codeimplementierung

Das Runbook friert nur den Proof-Pfad und die Evidence-Sprache ein.

## Handoff an Bob

Bobs spaeteres Import/Export-Proof-Modell soll mindestens diese Felder abbilden oder validieren:

- `export_run_id`
- `import_run_id`
- `source_manifest_ref`
- `target_manifest_ref`
- `backup_ref`
- `restore_ref`
- `count_comparison`
- `sample_comparison`
- `read_only_compare`
- `rollback_plan`
- `go_no_go_status`
- `proof_evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- Export und Import brauchen jeweils eine stabile Run-Identitaet
- Quell- und Zielmanifest muessen getrennt und referenzierbar bleiben
- `count_comparison` darf nicht nur Freitext sein
- `sample_comparison` muss sichtbar machen, ob Stichproben bestanden oder widerspruechlich sind
- `read_only_compare` muss explizit von einem Cutover getrennt sein
- `go_no_go_status` darf keine implizite Erfolgssprache verwenden
- fehlende Backup-, Restore- oder Provenance-Belege muessen blockierend validierbar sein

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `schema_version`
- `provenance_status`
- `count_delta_summary`
- `sample_scope`
- `review_notes`

## Akzeptanz fuer dieses Runbook

`MS5A-import-export-migration-proof-runbook` ist erfuellt, wenn:

- die Begriffe `export_run_id`, `import_run_id`, `source_manifest_ref`, `target_manifest_ref`, `backup_ref`, `restore_ref`, `count_comparison`, `sample_comparison`, `read_only_compare`, `rollback_plan`, `go_no_go_status`, `proof_evidence_ref` klar definiert sind
- der Ablauf Export, Import, Read-only-Vergleich und Go/No-Go ohne Dual-Write beschrieben ist
- Nutzer-/Ops-Sicht zeigt, welche Belege Vertrauen schaffen
- Charlie-Sicht klar macht, wann Bob weiterarbeiten darf und wann gestoppt werden muss
- Stop-Regeln fehlende Sicherung, Mismatch, Provenance-Probleme, dirty Worktree und Runtime-Cutover-Behauptungen blockieren
- Nicht-Ziele echte Migration, Runtime-Switch und neue DB-Starts aus dem Slice heraushalten
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Proof-Modell bekommt
