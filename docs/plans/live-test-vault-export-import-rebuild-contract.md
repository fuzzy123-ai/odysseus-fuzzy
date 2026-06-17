# Live Test Vault Export Import Rebuild Contract

Stand: 2026-06-17

Status: **LIVE2A Docs-Contract fuer das offene manuelle Gate `test_vault_export_import_rebuild`**

Quellen:

- `docs/plans/live-release-evidence-closeout-contract.md`
- `docs/plans/export-import-rebuild-operator-runbook.md`
- `docs/plans/1.0-evidence-release-checklist.md`
- `docs/plans/1.0-manual-release-evidence-runbook.md`

Dieser Contract definiert die sichere Operator-Sprache und Evidence-Anforderung fuer den spaeteren manuellen Test-Vault-Export/Import/Rebuild-Lauf. Das Gate bleibt rein vorbereitend: Es startet keinen echten Export, keinen Import, keinen Rebuild, keine Nextcloud-Integration, keinen Host-Befehl und keinen Runtime-Hook. Ziel ist nur, das offene externe Gate `test_vault_export_import_rebuild` sauber zu beschreiben, damit ein spaeterer manueller Lauf redigierte und nachvollziehbare Evidence liefern kann.

## Purpose

`LIVE2A` ist die Vertrags- und Runbook-Schicht fuer das zweite offene externe `1.0`-Gate.

Der Contract soll beantworten:

- was genau zum manuellen Gate `test_vault_export_import_rebuild` gehoert
- welche Operator-Inputs vor dem spaeteren Lauf vorliegen muessen
- welche Sicherheitsregeln fuer einen kleinen Test-Vault gelten
- wie Export, Import/Restore und Rebuild spaeter beobachtet werden muessen
- wie redigierte Evidence ohne private Inhalte, Pfade oder Rohlogs erfasst wird

## Leitregel

`LIVE2A` ist Vorbereitung und Contract, kein echter Export/Import/Rebuild und kein externes Release-Go.

Das bedeutet:

- kein echter Export- oder Importlauf
- kein echter Rebuild- oder Reindex-Lauf
- keine Nextcloud-, Host-, Telegram- oder Netzwerkaktion
- kein Runtime-Hook
- kein automatisches oder implizites `Go`

## Manual Gate Scope

Die Section `manual_gate_scope` soll den Umfang des spaeteren manuellen Gates klar abgrenzen.

Pflicht-Gate-ID:

- `test_vault_export_import_rebuild`

Zum Scope des spaeteren echten Operator-Laufs gehoert:

- kleiner kontrollierter Test-Vault
- Export-Artefakt beobachten
- Import oder Restore in isoliertem Ziel beobachten
- Rebuild- oder Index-Verifikation beobachten
- Redaction- und Safety-Review erfassen

Nicht zum Scope gehoert:

- Import-/Export-Code aendern
- Nextcloud aktivieren
- produktive Vaults pruefen
- Host- oder Netzwerk-Runtime aktivieren

## Required Operator Inputs

Die Section `required_operator_inputs` soll beschreiben, was vor dem spaeteren manuellen Lauf bekannt sein muss.

Mindestens:

- Branch und Commit
- kleiner kontrollierter Test-Vault
- klare Vorher-Snapshot-Notiz
- isolierte Zielumgebung fuer Import/Restore
- Backup- oder Rollback-Pfad
- Redaktionsregeln fuer spaetere Evidence-Erfassung

Wichtig:

- fehlende Inputs fuehren spaeter zu `needs_operator_input` statt zu improvisierten Live-Aktionen

## Test Vault Safety Rules

Die Section `test_vault_safety_rules` muss die Sicherheitsgrenzen fuer den spaeteren Test-Vault-Lauf hart setzen.

Mindestens:

- nur kleiner kontrollierter Test-Vault
- keine produktiven Nutzerartefakte
- keine privaten Vault-Inhalte
- keine stillen Source-Writes akzeptieren
- keine Derived-Daten als menschliche Quelle zurueckschreiben
- keine unklare Zielumgebung verwenden

Wichtig:

- sobald produktive oder private Daten im Scope auftauchen, stoppt der spaetere manuelle Lauf

## Export Capture Rules

Die Section `export_capture_rules` soll beschreiben, was der spaetere echte Export-Lauf beobachten und dokumentieren muss.

Mindestens:

- Export-Ziel ist kontrolliert
- Export-Artefakt oder Manifest ist identifizierbar
- grobe Counts oder Plausibilitaet sind notierbar
- keine sensiblen Inhalte werden kopiert

Zulaessig spaeter:

- Artefaktname
- grobe Counts
- kompakte Warnhinweise

Nicht zulaessig spaeter:

- private Dateiinhalte
- komplette Export-Payloads
- rohe Logs

## Import Restore Rules

Die Section `import_restore_rules` soll beschreiben, was der spaetere Import- oder Restore-Lauf sicher einhalten muss.

Mindestens:

- Zielumgebung ist klar isoliert und nicht-produktiv
- Dry-Run oder Preview wird genutzt, falls verfuegbar
- keine echten Quellen werden ueberschrieben
- unklare Zielzustande fuehren zu Stop statt zu Spekulation

Wichtig:

- Import/Restore bleibt spaeter manuell
- dieser Contract fuehrt keinen Import aus

## Rebuild Verification Rules

Die Section `rebuild_verification_rules` soll die spaeteren Beobachtungen fuer Rebuild und Index-Verifikation festlegen.

Mindestens:

- Source und Derived bleiben klar getrennt
- Count vor und nach dem Lauf ist nachvollziehbar
- kleine Stichproben bleiben moeglich
- keine stillen Source-Writes
- keine unerwarteten Diff-Signale ohne Dokumentation

Wichtig:

- Rebuild darf Derived-Daten neu erzeugen
- Rebuild darf menschliche Quellen nicht still ueberschreiben

## Evidence Capture Rules

Die Section `evidence_capture_rules` soll definieren, wie spaetere manuelle Evidence sicher erfasst wird.

Operator muss spaeter manuell belegen:

- kleiner Test-Vault
- Export-Artefakt
- Import/Restore in isoliertem Ziel
- Rebuild/Index-Verifikation
- Redaction/Safety Review

Zulaessig:

- Datum
- Commit
- kompakte Statusfelder
- grobe Counts
- kurze Go/Partial/No-Go-Notiz

Nicht zulaessig:

- private Vault-Inhalte
- private Pfade
- komplette Dateiinhalte
- rohe Logs

## Redaction Rules

Die Section `redaction_rules` muss die Redaktionsgrenzen hart setzen.

Nie erfassen oder kopieren:

- Secrets
- private Vault-Inhalte
- private Pfade
- rohe Logs
- komplette Export- oder Import-Payloads

Nur redigiert oder kompakt erlaubt:

- Test-Vault-Groesse oder Count
- Artefaktname
- Export-/Import-/Rebuild-Status
- kurze Warnungen oder Stop-Gruende

Wichtig:

- redigierte Evidence ist Pflicht
- private Daten oder Rohlogs stoppen den spaeteren manuellen Lauf

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Operator-Sprache
- Runbook- und Gate-Texte
- Go-/No-Go- und Redaktionsregeln

### Bob

Bob verantwortet:

- isolierte read-only Plan- oder Checker-Modelle
- Status `ready_for_manual_operator_run` oder `needs_operator_input`
- keine Export-/Import-/Rebuild-, Nextcloud-, Host- oder Netzwerk-Aktivierung

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei unklaren Gates oder riskanter Scope-Verschiebung

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in einen unkontrollierten Live-Lauf verhindern.

Mindestens:

- wenn kein kleiner kontrollierter Test-Vault vorliegt: stoppen
- wenn produktive oder private Inhalte im Scope sind: stoppen
- wenn Export-, Import- oder Restore-Ziel unklar ist: stoppen
- wenn Secrets, private Pfade oder rohe Logs auftauchen: stoppen
- wenn ein Slice echte Provider-, RAG-, Netzwerk-, Telegram-, Export-/Import-/Rebuild-, Nextcloud- oder Host-Aktionen verlangt: stoppen
- wenn Source und Derived nicht sauber trennbar bleiben: spaeter `needs_operator_input`, nicht `Go`

## Handoff To Live Closeout

Die Section `handoff_to_live_closeout` soll beschreiben, wie der spaetere manuelle Lauf in den LIVE-Closeout zurueckmeldet.

Mindestens:

- Gate-ID `test_vault_export_import_rebuild`
- Ergebnis `Go`, `Partial` oder `No-Go`
- redigierte Evidence-Referenz
- Blocker oder offene Operator-Fragen
- Hinweis, ob das erste offene Gate `provider_fallback_answer_run` noch weiterhin externes `1.0` blockiert

Wichtig:

- auch ein erfolgreiches Test-Vault-Gate allein erzeugt kein externes `1.0`-Go
- erst beide manuellen Gates zusammen duerfen den Live-Closeout veraendern

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Plan- oder Checker-Modell bauen, das den Test-Vault-Beweis ausschliesslich als `ready_for_manual_operator_run` oder `needs_operator_input` beschreibt.

Zulaessige Inputs:

- dokumentierte Gate-Statuswerte
- Runbook- und Contract-Artefakte
- read-only Readiness- oder Closeout-Snapshots

Wichtig:

- niemals Export/Import/Rebuild starten
- keine Nextcloud-, Host- oder Netzwerk-Aktivierung
- keine Provider-, Telegram- oder Runtime-Hooks

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Export-, Import- oder Rebuild-Lauf
- keine Nextcloud-, Host- oder Runtime-Aktivierung
- kein externes `1.0`-Go
- keine erfundene manuelle Evidence
- keine privaten Inhalte, Pfade oder Rohlogs

Er legt nur fest, wie das zweite offene externe Gate `test_vault_export_import_rebuild` sprachlich, prozessual und redaktionell sicher vorbereitet wird.
