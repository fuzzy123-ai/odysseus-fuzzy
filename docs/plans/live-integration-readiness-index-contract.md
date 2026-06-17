# Live Integration Readiness Index Contract

Stand: 2026-06-17

Status: **LIVE12A Docs-Contract fuer das Gate `live_integration_readiness_index_plan`**

Quellen:

- `docs/plans/live-release-evidence-closeout-contract.md`
- `docs/plans/live-provider-proof-run-contract.md`
- `docs/plans/live-test-vault-export-import-rebuild-contract.md`
- `docs/plans/live-orchestration-runtime-bridge-contract.md`
- `docs/plans/live-quality-gate-command-runner-contract.md`
- `docs/plans/live-plugin-loader-safe-mode-contract.md`
- `docs/plans/live-system-health-host-agent-mvp-contract.md`
- `docs/plans/live-system-health-local-api-consumer-contract.md`
- `docs/plans/live-telegram-status-dry-run-contract.md`
- `docs/plans/live-plugin-manifest-discovery-dry-run-contract.md`
- `docs/plans/live-plugin-capability-preview-index-contract.md`
- `docs/plans/live-plugin-operator-review-packet-contract.md`

Dieser Contract definiert einen statischen, operatorfreundlichen Readiness-Index ueber die Live-Integration-Slices `LIVE0` bis `LIVE11`. Der Index fasst vorbereitete Vertrags- und Dry-Run-Bausteine zusammen, ohne Runtime zu aktivieren, ohne Provider-, Export-, Plugin-, Telegram- oder Host-Aktionen auszufuehren und ohne externes `1.0.0` freizugeben. Er dient nur als Readiness-Uebersicht fuer manuelle Review und spaetere Operator-Entscheidungen.

## Purpose

`LIVE12A` ist der zusammenfassende Abschluss-Slice fuer die bisherige Live-Integration-Vorbereitung.

Der Contract soll beantworten:

- welche Live-Slices `LIVE0` bis `LIVE11` bereits als sichere Contracts vorliegen
- welche Readiness-Dimensionen dadurch abgedeckt sind
- welche harten No-Go-Gates externes `1.0.0` weiter blockieren
- welche Aktionen trotz guter Foundation weiterhin verboten bleiben
- wie Alice, Bob und Charlie den Readiness-Index read-only halten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Runtime-Aktivierung
- keine Provider- oder Fallback-Laeufe
- keinen echten Export, Import oder Rebuild
- keinen Plugin-Import
- kein `setup()`
- keine Telegram-, Netzwerk- oder Host-Aktion
- kein externes `1.0.0`-Go

## Covered Live Slices

Die Section `covered_live_slices` soll alle vorbereiteten Slices `LIVE0` bis `LIVE11` lesbar referenzieren.

Mindestens:

- `LIVE0` Release Evidence Closeout
- `LIVE1` Provider Proof Run Contract
- `LIVE2` Test Vault Export/Import/Rebuild Contract
- `LIVE3` Orchestration Runtime Bridge Dry-Run
- `LIVE4` Quality Gate Command Runner Dry-Run
- `LIVE5` Plugin Loader Safe Mode
- `LIVE6` System Health Host Agent MVP Plan
- `LIVE7` System Health Local API Consumer Plan
- `LIVE8` Telegram Status Dry-Run
- `LIVE9` Plugin Manifest Discovery Dry-Run
- `LIVE10` Plugin Capability Preview Index
- `LIVE11` Plugin Operator Review Packet

Wichtig:

- der Index beschreibt Vertrags- und Plan-Reife
- er beschreibt nicht, dass diese Slices bereits live aktiviert sind

## Readiness Dimensions

Die Section `readiness_dimensions` soll die inhaltlichen Bereiche des Index strukturieren.

Pflicht-Dimensionen:

- `release_evidence`
- `provider_proof_plan`
- `test_vault_rebuild_plan`
- `runtime_bridge_dry_run`
- `quality_gate_runner_dry_run`
- `plugin_safe_mode`
- `host_agent_plan`
- `local_api_consumer`
- `telegram_dry_run`
- `manifest_discovery`
- `capability_preview`
- `operator_review_packet`

Jede Dimension darf nur als statischer Contract- oder Dry-Run-Status beschrieben werden.

Wichtig:

- `integration_readiness_ready` bedeutet nur, dass die Readiness-Uebersicht lesbar und konsistent ist
- es bedeutet nicht, dass Provider, Export oder Plugins live genutzt werden duerfen

## Hard External 1.0.0 No-Go Gates

Die Section `hard_external_1_0_0_no_go_gates` muss die weiterhin offenen manuellen Gates klar markieren.

Pflicht-Gates:

- `provider_fallback_answer_run`
- `test_vault_export_import_rebuild`

Bedeutung:

- ohne echten manuellen Provider-/Fallback-Antwortlauf bleibt externes `1.0.0` blockiert
- ohne echten manuellen Export/Import/Rebuild-Proof mit kleinem Test-Vault bleibt externes `1.0.0` blockiert

Wichtig:

- kein Readiness-Index darf diese Gates implizit auf `go` setzen
- `needs_manual_evidence` bleibt korrekt, solange die beiden Gates nicht belegt sind

## Forbidden Actions

Die Section `forbidden_actions` muss die fuer den Index hart verbotenen Aktionen nennen.

Mindestens:

- Runtime-Enablement
- Netzwerkzugriff
- Token- oder Secret-Nutzung
- Host-Kommandos
- Plugin-Importe
- `setup()`
- Auto-Approval

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- auch gut aussehende Readiness-Daten erlauben keinen Live-Schritt ohne separates manuelles Gate

## Operator Friendly Index Structure

Die Section `operator_friendly_index_structure` soll den minimalen Aufbau des spaeteren Index beschreiben.

Pflicht-Sections:

- `summary`
- `covered_live_slices`
- `readiness_dimensions`
- `manual_evidence_gates`
- `forbidden_actions`
- `blocked_or_deferred_reasons`
- `operator_next_steps`
- `known_limits`

Pflicht-Inhalte:

- klarer Unterschied zwischen interner Contract-Reife und externem Release-Go
- kompakte Hinweise auf die zwei offenen manuellen Evidence-Gates
- kurze Stop-Hinweise statt Rohdaten

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer den Index festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Readiness-Dimensionen und Known-Limits-Texte
- Go-/No-Go-Formulierungen

### Bob

Bob verantwortet:

- ein isoliertes read-only Readiness-Index-Modell
- die Aggregation von LIVE0-LIVE11 Statussignalen
- Statusableitung fuer die Index-Sprache

Wichtig:

- Bob darf keine Runtime-, Provider-, Export-, Plugin-, Telegram-, Host- oder Netzwerk-Aktion ausloesen
- Bob darf nur statische oder read-only Signale verdichten

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei Scope-Bruch, fremden staged Files oder implizitem Live-Go

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in ein falsches Live-Go verhindern.

Mindestens:

- wenn `provider_fallback_answer_run` unbelegt bleibt: `needs_manual_evidence` oder `blocked`, kein `go`
- wenn `test_vault_export_import_rebuild` unbelegt bleibt: `needs_manual_evidence` oder `blocked`, kein `go`
- wenn Runtime-Enablement, Netzwerk, Tokens, Host-Kommandos, Plugin-Importe oder Auto-Approval auftauchen: stoppen
- wenn Secrets, private Pfade oder rohe Logs sichtbar werden: stoppen
- wenn der Unterschied zwischen Contract-Reife und externer Release-Reife verwischt wird: stoppen

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- ein isoliertes read-only Readiness-Index-Modell
- Aggregation der Statussignale aus `LIVE0` bis `LIVE11`
- Statusableitung fuer `integration_readiness_ready`, `needs_manual_evidence`, `blocked`, `deferred`
- Tests mit mockten oder Fixture-basierten Live-Statusdaten

Nicht erlaubt:

- Provider- oder Fallback-Laeufe
- Export, Import oder Rebuild
- Plugin-Importe oder `setup()`
- Telegram-, Host- oder Netzwerkaktionen
- Runtime-Enablement oder Auto-Approval

Pflicht-Gate-ID:

- `live_integration_readiness_index_plan`

Pflicht-Statuswerte:

- `integration_readiness_ready`
- `needs_manual_evidence`
- `blocked`
- `deferred`

## Example Safe Readiness Summary

Zulaessig:

- `covered_live_slices = LIVE0-LIVE11`
- `manual_evidence_gates = provider_fallback_answer_run, test_vault_export_import_rebuild`
- `status = needs_manual_evidence`
- `operator_next_steps = manual provider proof and test vault proof`

Nicht zulaessig:

- `external_release_go = true`
- `provider_gate_passed_implicitly = true`
- `plugin_runtime_may_enable_now = true`
- `host_actions_allowed = true`

## Akzeptanz Fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Readiness-Index-Modell bauen, das die vorhandenen Live-Contracts und Dry-Run-Bausteine zusammenfasst und die offenen manuellen Gates ehrlich sichtbar laesst.

Wichtig:

- keine IO ausser read-only Artefakt- oder Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine Plugin-Imports
- kein Runtime-Enablement

## Abschluss

Dieser Slice liefert nur die sichere Plan- und Vertragssprache fuer einen zusammenfassenden Live-Integration-Readiness-Index. Er ist kein Runtime Enablement, kein Provider- oder Export-Lauf, keine Plugin-Aktivierung und kein externes `1.0.0`-Go.
