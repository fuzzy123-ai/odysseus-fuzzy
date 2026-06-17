# Test Vault Export Import Rebuild Contract

Stand: 2026-06-17

Status: **FINAL2A Docs-Contract fuer das Gate `test_vault_export_import_rebuild`**

Quellen:

- `docs/plans/release-runtime-readiness-roadmap.md`
- `docs/plans/1.0-evidence-release-checklist.md`
- `docs/plans/export-import-rebuild-operator-runbook.md`
- `docs/plans/live-test-vault-export-import-rebuild-contract.md`

Dieser Contract definiert den finalen Release-Vertrag fuer den Test-Vault Export/Import/Rebuild vor externem `1.0.0`. Er beschreibt keine echten Export-, Import- oder Rebuild-Aktionen, keine Netzwerknutzung und keine Runtime-Aktivierung. Der Slice friert nur ein, wie Operator-Evidence fuer einen kleinen, bewusst nicht-produktiven Test-Vault redigiert, nachvollziehbar und sicher dokumentiert werden muss, ohne Datenverlust, ohne stille Source-Writes und ohne Secrets.

## Purpose

`FINAL2A` ist die letzte operator-taugliche Release-Grenze fuer den Test-Vault Export/Import/Rebuild.

Der Contract soll beantworten:

- wann das manuelle Gate `test_vault_export_import_rebuild` ueberhaupt als vorbereitet gelten darf
- welche Pflicht-Evidence fuer Test-Vault-Scope, Export-Artefakt, Import-Ziel und Rebuild-Ergebnis vorliegen muss
- wie Datenverlust- und Source-Write-Warnungen release-ehrlich beschrieben werden
- welche Go/Partial/No-Go-Kriterien fuer das externe `1.0.0` gelten
- wie Alice, Bob und Charlie diese Evidence ohne Live-Runtime-Code und ohne Secrets vorbereiten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Export-Lauf
- keinen echten Import-Lauf
- keinen echten Rebuild-Lauf
- keine Provider-, RAG-, Telegram-, Netzwerk- oder Host-Aktion
- keine Plugin-Arbeit, keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime
- kein externes `1.0.0`-Go allein durch diesen Slice

## Gate Scope

Die Section `gate_scope` soll den Umfang des spaeteren manuellen Gates klar begrenzen.

Pflicht-Gate-ID:

- `test_vault_export_import_rebuild`

Zum Scope des spaeteren echten manuellen Laufs gehoert:

- ein kleiner, bewusst nicht-produktiver Test-Vault
- ein beobachtetes Export-Artefakt
- ein isoliertes Import-Ziel
- ein beobachtetes Rebuild-Ergebnis
- ein redigierter Datenverlust- und Source-Write-Check
- eine dokumentierte Operator-Bestaetigung

Nicht zum Scope gehoert:

- produktive Vaults
- unklare Zielumgebungen
- echte Runtime- oder Netzwerk-Aktivierung
- Plugin- oder Nextcloud-Arbeit

## Pflicht-Evidence

Die Section `required_evidence` soll die minimalen Belege fuer dieses Release-Gate festziehen.

Pflicht-Evidence:

- `test_vault_scope_recorded`
- `export_artifact_recorded`
- `import_target_recorded`
- `rebuild_result_recorded`
- `source_write_disabled`
- `data_loss_check_recorded`
- `rollback_plan_recorded`
- `operator_confirmation_recorded`

### `test_vault_scope_recorded`

Der Test-Vault muss als klein, kontrolliert und nicht-produktiv dokumentiert sein.

### `export_artifact_recorded`

Das beobachtete Export-Artefakt oder Manifest muss als redigierte Metainformation festgehalten werden.

### `import_target_recorded`

Das Import-Ziel muss als isolierte, nicht-produktive Zielumgebung dokumentiert sein.

### `rebuild_result_recorded`

Das Rebuild- oder Reindex-Ergebnis muss als redigierte Beobachtung dokumentiert sein.

### `source_write_disabled`

Es muss explizit festgehalten werden, dass menschliche Quellen nicht still ueberschrieben wurden.

### `data_loss_check_recorded`

Ein Datenverlust-Check muss dokumentiert sein, auch wenn das Ergebnis `kein Verlust beobachtet` lautet.

### `rollback_plan_recorded`

Es muss eine klare Ruecknahme- oder Wiederherstellungsanweisung fuer den manuellen Lauf geben.

### `operator_confirmation_recorded`

Ein echter manueller Lauf braucht bewusste Operator-Bestaetigung und darf nicht implizit als erfolgt gelten.

## Datenverlust- Und Source-Write-Warnungen

Die Section `data_loss_and_source_write_warnings` muss die Sicherheitsgrenze hart setzen.

Pflichtregeln:

- produktive Vaults sind tabu
- menschliche Quellen duerfen nicht still beschrieben oder ersetzt werden
- Derived-Daten duerfen nicht als Source zurueckgeschrieben werden
- ein unklarer Diff oder ein ungeklaerter Write ist Stop-Signal
- Datenverlust ist immer `No-Go`

Wichtig:

- Source-Write-Disziplin ist Pflicht, nicht optional
- dieser Contract darf keinen `wird schon passen`-Pfad offenlassen

## Redaction Rules

Die Section `redaction_rules` muss die Daten- und Pfadgrenzen hart setzen.

Nie erfassen oder kopieren:

- Secrets
- Keys
- Tokens
- Chat-IDs
- private Pfade
- komplette Vault-Inhalte
- komplette Export-/Import-Payloads
- rohe Logs

Nur redigiert oder kompakt erlaubt:

- Test-Vault-Groesse oder Count
- Artefaktname
- Import-Ziel als harmlose Metainformation
- Rebuild-Status
- kurze Go/Partial/No-Go-Notiz

Wichtig:

- redigierte Evidence ist Pflicht
- private Inhalte oder Rohpayloads stoppen das Gate

## Go Partial No-Go Kriterien

Die Section `go_partial_no_go_criteria` soll die finale Release-Sprache fuer dieses Gate festlegen.

### `Go`

Nur wenn:

- ein kleiner, nicht-produktiver Test-Vault klar dokumentiert ist
- Export-Artefakt beobachtet und redigiert festgehalten wurde
- Import-Ziel isoliert und nicht-produktiv ist
- Rebuild-Ergebnis nachvollziehbar dokumentiert ist
- `source_write_disabled` und `data_loss_check_recorded` positiv vorliegen
- Rollback und Operator-Bestaetigung dokumentiert sind
- keine Secret-, Scope- oder Logging-Regel verletzt wurde

### `Partial`

Wenn:

- einzelne Teilpfade nur teilweise belegt oder nur als Preview dokumentiert werden konnten
- kein Datenverlust beobachtet wurde, aber Import-/Rebuild-Lage noch nicht voll bestaetigt ist
- das Ergebnis ehrliche, aber noch nicht vollstaendige Release-Evidence liefert

### `No-Go`

Wenn:

- ein produktiver Vault oder ein unklarer Scope verwendet wurde
- Datenverlust beobachtet wurde
- Source-Writes nicht ausgeschlossen werden koennen
- Export-Artefakt, Import-Ziel oder Rebuild-Ergebnis fehlen
- der Lauf ohne ausdrueckliches Go oder ausserhalb der Boundary gestartet wurde

## Verbotene Aktionen Und Blocker

Die Section `forbidden_actions_and_blockers` muss die harten Release-Blocker nennen.

Mindestens:

- `production_vault_used`
- `source_write_enabled`
- `data_loss_detected`
- `missing_export_artifact`
- `missing_import_target`
- `missing_rebuild_result`
- `rebuild_run_without_go`
- `plugin_scope_touched`
- `unsafe_evidence_logging_enabled`

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- Plugin-Modul bleibt eingefroren und wird in diesem Gate nicht beruehrt

## Statussprache

Pflicht-Statuswerte:

- `test_vault_rebuild_ready`
- `needs_test_vault_evidence`
- `blocked`
- `deferred`

### `test_vault_rebuild_ready`

Der Gate-Lauf ist sauber vorbereitet: Test-Vault-Scope, Export-Artefakt, Import-Ziel, Rebuild-Ergebnis, Source-Write-Disziplin, Datenverlust-Check, Rollback und Operator-Bestaetigung sind klar beschrieben.

### `needs_test_vault_evidence`

Mindestens ein Pflichtbeleg zu Scope, Artefakt, Import-Ziel, Rebuild, Source-Write-Disziplin, Datenverlust-Check oder Rollback fehlt noch oder ist zu unscharf.

### `blocked`

Mindestens ein harter Verstoss liegt vor, zum Beispiel produktiver Vault, Datenverlust, Source-Write, unsafe Logging oder Lauf ohne manuelles Go.

### `deferred`

Die Gate-Entscheidung ist bewusst verschoben und bleibt ausserhalb dieses Slices offen.

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer dieses Release-Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Datenverlust-, Source-Write- und Go-/Partial-/No-Go-Texte
- Klartext: Test-Vault ja, Produktiv-Vault nein

### Bob

Bob verantwortet:

- ein isoliertes read-only Validator- oder Summary-Modell fuer das Gate
- Validierung der Pflicht-Evidence und Gate-Statuswerte
- keine Export-, Import-, Rebuild-, Netzwerk- oder Plugin-Aktivierung

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- fokussierte Tests gegen fehlende Pflicht-Evidence, unsafe Logging und Scope-Verstoss
- Stop-Entscheidung bei Produktivdaten-, Datenverlust- oder Source-Write-Risiko

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isoliertes read-only Evidence-Validator- oder Summary-Modell
- Validierung der Pflicht-Evidence
- Statusableitung fuer `test_vault_rebuild_ready`, `needs_test_vault_evidence`, `blocked`, `deferred`
- Tests mit rein redigierten, nicht-produktiven und netzwerkfreien Fixtures

Nicht erlaubt:

- echte Export-, Import- oder Rebuild-Aktionen
- Provider-, Telegram-, Netzwerk- oder Host-Aktionen
- Plugin- oder Nextcloud-Aktivierung
- Persistenz von Rohpayloads, privaten Inhalten oder Secrets

## Example Safe Gate Reading

Zulaessig:

- `test_vault_scope_recorded = true`
- `export_artifact_recorded = true`
- `import_target_recorded = true`
- `rebuild_result_recorded = true`
- `source_write_disabled = true`
- `data_loss_check_recorded = true`
- `rollback_plan_recorded = true`
- `operator_confirmation_recorded = true`
- `status = test_vault_rebuild_ready`

Nicht zulaessig:

- `production_vault_used = true`
- `source_write_enabled = true`
- `data_loss_detected = true`
- `missing_export_artifact = true`
- `plugin_scope_touched = true`

## Abschluss

Dieser Slice liefert nur die sichere Release-Sprache fuer den Test-Vault Export/Import/Rebuild. Er macht kleinen, nicht-produktiven Scope, redigierte Artefakt-Evidence, Source-Write-Disziplin, Datenverlust-Checks und ehrliche Go/Partial/No-Go-Kriterien releasefaehig lesbar, ohne echte Rebuild-Aktionen, ohne Secrets und ohne Plugin-Scope.
