# Ops Homeserver Runbook Contract

Stand: 2026-06-16

Status: **MS7A Nutzer-/Ops-/Charlie-Vertrag fuer `0.13.x Homeserver Fitness`**

Quellen:

- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/memory-scale-foundation-roadmap.md`
- `docs/plans/memory-store-interface-contract.md`
- `docs/plans/memory-diagnostics-lens-contract.md`
- `docs/plans/query-budget-ux-contract.md`
- `docs/plans/postgres-pgvector-migration-contract.md`
- `docs/plans/import-export-migration-proof-runbook.md`
- `docs/plans/progressive-graph-api-contract.md`

Dieser Vertrag definiert die sichtbare Betriebs- und Readiness-Sprache fuer einen MiniPC- oder Homeserver-Betrieb der Memory Scale Foundation. `MS7A` baut bewusst keine echte Infrastruktur, startet keine Datenbank und schreibt kein Docker Compose. Der Slice friert nur ein, welche Betriebsannahmen, Budgets, Restore-Nachweise und Risiko-Gates spaeter sichtbar und pruefbar sein muessen.

## Ziel

Odysseus soll auf einem kleinen Homeserver nicht nur theoretisch laufen, sondern kontrolliert, budgetiert und wiederherstellbar betrieben werden koennen.

Der Homeserver-Vertrag soll:

- Nutzer und Ops sehen lassen, ob ein MiniPC mit 16 GB RAM und 2x500 GB SSD verantwortbar betrieben werden kann
- Charlie eine klare Abschluss- oder Stop-Logik fuer `0.13.x` geben
- Backup, Restore, Speichergrenzen und Job-Drosselung wichtiger machen als Peak-Performance
- Bob ein kleines, validierbares Homeserver-Readiness-Modell vorbereiten

## Leitregel

Homeserver-Fitness ist zuerst eine Restore- und Risk-Disziplin, nicht ein Performance-Marketing-Versprechen.

Das bedeutet:

- Restore-Faehigkeit ist wichtiger als theoretische Spitzenwerte
- Jobs muessen drosselbar bleiben
- Rebuilds duerfen nicht unbounded anlaufen
- Postgres bleibt die geplante Wahrheit
- Accelerator wie Qdrant oder Kuzu bleiben optional und gehoeren nicht in diesen Slice

## Begriffe

### `homeserver_profile`

Die kompakte Beschreibung des geplanten Betriebsprofils.

Fuer diesen Vertrag mindestens:

- MiniPC oder Homeserver
- 16 GB RAM
- 2x500 GB SSD
- begrenzte CPU- und I/O-Reserven

### `service_ref`

Referenz auf einen einzelnen Betriebsdienst oder eine einzelne Service-Einheit im geplanten Setup.

### `postgres_ref`

Referenz auf den geplanten Postgres-Dienst als kuenftige Wahrheitsquelle.

### `data_volume_ref`

Referenz auf das Daten-Volume oder die Datenablage fuer persistente Postgres- und Memory-Daten.

### `backup_ref`

Referenz auf die Sicherung, die den geplanten Wahrheitsstand schuetzt.

### `restore_ref`

Referenz auf die dokumentierte Restore-Prozedur fuer Daten und Betriebsbereitschaft.

### `resource_budget`

Die lesbare Grenze fuer CPU, RAM, Speicher oder I/O, innerhalb derer Betrieb, Jobs und Wartung bleiben muessen.

### `job_concurrency`

Die maximal zulaessige Parallelitaet fuer ingest-, rebuild-, index- oder review-nahe Jobs.

### `maintenance_window`

Das geplante Zeitfenster fuer Wartung, Backup, Restore-Drills oder schwere Rebuild-Arbeit.

### `vacuum_policy`

Die lesbare Regel fuer Vacuum- oder vergleichbare Speicherpflege des kuenftigen Postgres-Systems.

### `index_maintenance_policy`

Die lesbare Regel fuer Index-Pflege, Rebuild-Naehe und Wartungsaufwand.

### `storage_pressure`

Der sichtbare Zustand, wenn Datenvolumen, Backups oder Indexe zu nahe an die verfuegbaren Speichergrenzen ruecken.

### `restore_drill_status`

Der Status, ob ein Restore-Drill geplant, geprueft, fehlgeschlagen oder unbekannt ist.

Erlaubte Werte mindestens:

- `draft`
- `planned`
- `verified`
- `failed`
- `unknown`

### `go_no_go_status`

Der explizite Freigabestatus fuer Homeserver-Readiness.

Erlaubte Werte mindestens:

- `draft`
- `review`
- `go`
- `no_go`
- `blocked`
- `superseded`

### `risk_evidence_ref`

Kurze Referenz auf das wichtigste Evidence-Buendel fuer Risiko-, Restore- oder Resource-Readiness.

## Nutzer- und Ops-Sicht

Nutzer und Ops muessen nicht jede Infrastrukturentscheidung im Detail sehen. Sie muessen aber erkennen koennen, ob der geplante Betrieb auf einem kleinen System verantwortbar ist.

Sichtbar sein muessen mindestens:

- welches `homeserver_profile` angenommen wird
- welcher `postgres_ref` als geplante Wahrheit gilt
- welche `data_volume_ref` fuer Daten und Backups relevant ist
- welche `resource_budget`-Grenzen gelten
- ob `job_concurrency` begrenzt und drosselbar ist
- ob `maintenance_window` definiert ist
- ob `backup_ref` und `restore_ref` konkret vorhanden sind
- ob `restore_drill_status` ueberhaupt belastbar ist
- ob `storage_pressure` kontrolliert, warnend oder kritisch ist
- welches `go_no_go_status` daraus folgt

Der Nutzer oder Operator soll auf einen Blick verstehen:

- laeuft das System in einem kleinen Ressourcenprofil oder nur unter Wunschannahmen
- ist Restore realistisch vorbereitet
- koennen Jobs und Rebuilds gebremst werden
- droht die Speicherkapazitaet unkontrolliert zu wachsen

## Was man fuer einen 16-GB- und 2x500-GB-Server sehen muss

Damit ein MiniPC mit 16 GB RAM und 2x500 GB SSD als tragfaehig gilt, muessen spaeter mindestens diese Punkte lesbar sein:

- CPU- und RAM-Budgets fuer Datenbank, Jobs und Nebenlasten sind benannt
- Daten- und Backup-Ablagen sind getrennt oder bewusst dokumentiert
- Jobs haben eine kleine, kontrollierte `job_concurrency`
- Rebuilds sind nicht implizit unbounded
- `storage_pressure` kann frueh erkannt werden
- `maintenance_window` fuer Backup, Vacuum und Index-Pflege ist geplant
- `restore_drill_status` ist mindestens geplant und spaeter pruefbar
- `backup_ref` und `restore_ref` sind nicht nur Platzhalter

## Charlie-Sicht

Charlie braucht eine strengere Readiness-Sicht als normale Betriebsprosa.

Charlie soll erkennen koennen:

- ist das geplante Setup auf kleinem System betrieblich diszipliniert
- gibt es einen realen Backup- und Restore-Pfad
- sind Jobs, Rebuilds und Wartung begrenzt
- ist Postgres weiterhin die geplante Wahrheit
- wurde kein stiller Accelerator-Pfad in die Foundation geschmuggelt

Charlie braucht mindestens:

- `homeserver_profile`
- `service_ref`
- `postgres_ref`
- `data_volume_ref`
- `backup_ref`
- `restore_ref`
- `resource_budget`
- `job_concurrency`
- `maintenance_window`
- `vacuum_policy`
- `index_maintenance_policy`
- `storage_pressure`
- `restore_drill_status`
- `go_no_go_status`
- `risk_evidence_ref`

Charlie darf `MS7` als inhaltlich abschliessbar betrachten, wenn:

- Backup und Restore lesbar definiert sind
- `restore_drill_status` mindestens geplant und nicht `unknown` ist
- `job_concurrency` begrenzt und drosselbar beschrieben ist
- `resource_budget` fuer CPU, RAM und Speicher nicht implizit bleibt
- `maintenance_window`, `vacuum_policy` und `index_maintenance_policy` vorhanden sind
- `storage_pressure` als Risiko-Lage sichtbar gemacht wird
- Postgres als geplante Wahrheit bleibt
- keine implizite Qdrant-, Kuzu- oder andere Accelerator-Pflicht eingefuehrt wird

Charlie muss stoppen, wenn:

- Backup oder Restore unklar bleiben
- kein Restore-Drill vorgesehen ist
- Volumes oder Datenablagen nicht nachvollziehbar sind
- Jobs unbounded oder praktisch ungedrosselt wirken
- Speicher- oder RAM-Grenzen fehlen
- Wartung und Pflege nur implizit oder ad hoc gedacht sind
- Accelerator als versteckte Voraussetzung auftauchen

## Betriebsregeln

### Restore vor Peak-Performance

Ein Homeserver gilt nicht als bereit, nur weil ein schneller Pfad denkbar ist. Er gilt nur dann als verantwortbar, wenn Restore und Recovery plausibel vorbereitet sind.

### Jobs muessen drosselbar sein

`job_concurrency` darf nicht offen oder aggressiv hoch bleiben.

Regel:

- jede schwere Ingest-, Rebuild-, Embedding- oder Graph-Arbeit muss begrenzt oder pausierbar sein

### Keine unbudgetierten Rebuilds

Full Rebuilds duerfen auf einem kleinen Homeserver nie als stiller Normalpfad gedacht sein.

### Postgres bleibt geplante Wahrheit

Auch im Ops-Runbook bleibt Postgres die geplante Wahrheitsquelle. Kein Teil dieses Vertrags macht daraus einen Multi-DB-Zwang.

### Accelerator bleiben optional

Qdrant, Kuzu oder UMAP/GMM gehoeren nicht zur Homeserver-Grundlage in `MS7A`.

## Wartung und Pflege

### `maintenance_window`

Wartung braucht ein klares Fenster, statt zufaellig neben dem Normalbetrieb stattzufinden.

### `vacuum_policy`

Es muss eine lesbare Regel geben, wann Speicherpflege anfaellt und wie sie in das kleine Ressourcenprofil passt.

### `index_maintenance_policy`

Index-Pflege darf nicht als unsichtbarer Dauerzustand gedacht sein. Sie braucht Grenzen, Timing und einen Bezug zu `resource_budget`.

## Speicher- und Risikolage

`storage_pressure` muss sichtbar machen:

- ob Datenvolumen zu schnell waechst
- ob Backups noch in das geplante Speicherbudget passen
- ob Indexe oder Rebuilds den Server in kritische Naehe bringen

Eine Homeserver-Lage ist nicht gesund, wenn Daten, Backups und Indexe nur mit implizitem Glueck in die SSD-Grenzen passen.

## Stop-Regeln

`MS7A` oder eine spaetere Homeserver-Readiness ist `no_go` oder `blocked`, wenn mindestens einer dieser Faelle eintritt:

- fehlender `backup_ref`
- fehlender `restore_ref`
- kein `restore_drill_status` oder Status `unknown`
- unklare `data_volume_ref`
- unbounded oder praktisch unlimitierte `job_concurrency`
- kein lesbares `resource_budget`
- keine definierte `maintenance_window`
- fehlende `vacuum_policy`
- fehlende `index_maintenance_policy`
- implizite Qdrant- oder Kuzu-Einfuehrung als Voraussetzung

## Evidence-Paket

Ein spaeteres Homeserver-Evidence-Buendel sollte mindestens enthalten:

- `homeserver_profile`
- `postgres_ref`
- `data_volume_ref`
- `backup_ref`
- `restore_ref`
- `resource_budget`
- `job_concurrency`
- `maintenance_window`
- `vacuum_policy`
- `index_maintenance_policy`
- `storage_pressure`
- `restore_drill_status`
- `go_no_go_status`
- `risk_evidence_ref`

Empfohlene Zusatzbelege:

- kurze CPU- und RAM-Annahmen
- Daten-vs-Backup-Aufteilung
- Rebuild- oder Job-Drosselungsnotizen
- bekannte Risiken, die bewusst akzeptiert oder vertagt werden

## Nicht-Ziele

`MS7A` fuehrt bewusst nicht aus:

- keine echte Infrastruktur
- keine laufende Datenbank
- kein Docker Compose
- keine Runtime-Umschaltung
- keine echte Postgres-Installation
- keine Accelerator-Arbeit

Der Slice friert nur die Betriebs-, Risk- und Restore-Sprache fuer Homeserver-Fitness ein.

## Handoff an Bob

Bobs spaeteres Homeserver-Readiness-Modell soll mindestens diese Felder abbilden oder validieren:

- `homeserver_profile`
- `service_ref`
- `postgres_ref`
- `data_volume_ref`
- `backup_ref`
- `restore_ref`
- `resource_budget`
- `job_concurrency`
- `maintenance_window`
- `vacuum_policy`
- `index_maintenance_policy`
- `storage_pressure`
- `restore_drill_status`
- `go_no_go_status`
- `risk_evidence_ref`

Minimum-Regeln fuer Bobs Modell:

- `postgres_ref` muss als geplante Wahrheit lesbar bleiben
- `data_volume_ref` darf nicht unklar oder implizit sein
- `resource_budget` muss CPU-, RAM- oder Speichergrenzen referenzierbar machen
- `job_concurrency` darf keine unbounded oder offene Parallelitaet erlauben
- `restore_drill_status` darf nicht fehlen
- `go_no_go_status` darf kein stilles "wird schon gehen" enthalten
- Accelerator duerfen nicht als Pflicht des Basisprofils modelliert sein

Sinnvolle, aber fuer den kleinsten Start optionale Zusatzfelder:

- `cpu_budget`
- `ram_budget_gb`
- `storage_budget_gb`
- `backup_retention_hint`
- `throttle_policy`
- `known_limit_summary`

## Akzeptanz fuer diesen Vertrag

`MS7A-ops-homeserver-runbook-contract` ist erfuellt, wenn:

- die Begriffe `homeserver_profile`, `service_ref`, `postgres_ref`, `data_volume_ref`, `backup_ref`, `restore_ref`, `resource_budget`, `job_concurrency`, `maintenance_window`, `vacuum_policy`, `index_maintenance_policy`, `storage_pressure`, `restore_drill_status`, `go_no_go_status`, `risk_evidence_ref` klar definiert sind
- Nutzer-/Ops-Sicht zeigt, was fuer einen 16-GB-RAM- und 2x500-GB-SSD-Server sichtbar sein muss
- Charlie-Sicht klar macht, wann `MS7` abgeschlossen werden darf und wann gestoppt werden muss
- Regeln Restore-Faehigkeit, Drosselung, unbudgetierte Rebuilds, Postgres-Wahrheit und optionale Accelerator sauber priorisieren
- Stop-Regeln fehlende Sicherung, Restore-Drill, Volume-Klarheit, Budget-Grenzen und versteckte Accelerator-Einfuehrungen blockieren
- Nicht-Ziele echte Infrastruktur-, DB- oder Accelerator-Arbeit aus dem Slice heraushalten
- Bob einen kleinen, konkreten Validierungs-Handoff fuer sein Homeserver-Readiness-Modell bekommt
