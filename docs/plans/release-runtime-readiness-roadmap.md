# Final 1.0 Release Runtime Readiness Roadmap

Stand: 2026-06-17

Status: **finale Abschlussroadmap bis zum externen `1.0.0` Release**

Diese Roadmap ist die letzte operative 1.0-Route. Sie ersetzt die vorherige Slice-Liste nicht historisch, sondern friert den erreichten Stand ein und konzentriert die verbleibende Arbeit auf die echten Release-Gates.

## Aktueller Stand

Odysseus ist intern **release-candidate-ready**. Die automatisierten Gates sind gruen, Graph Memory und Telegram sind bis zu sicheren Release-Grenzen vorbereitet, und das Plugin-Modul bleibt bewusst eingefroren.

| Bereich | Stand | Evidence |
| --- | --- | --- |
| Automatisierter Release-Gate-Lauf | gruen | `235 passed, 44 warnings` |
| Graph Memory / RAPTOR Evidence Map | abgeschlossen | RGM0: Alice `b841bd8a`, Bob `86e19316`; Test `tests/test_graph_memory_release_evidence_map.py` -> `5 passed, 1 warning` |
| 100.000+-Large-Graph-Budget-Proof | abgeschlossen | RGM1: Alice `8fbb5634`, Bob `0b4b5039`; Test `tests/test_large_graph_budget_proof.py` -> `5 passed, 1 warning` |
| Progressive Graph API Release Gate | abgeschlossen | RGM2: Alice/Charlie `5a1b0b2c`, Bob `2d8ff6bc`; Test `tests/test_progressive_graph_api_release_gate.py` -> `5 passed, 1 warning` |
| Graph Maintenance Review Gate | abgeschlossen | RGM3: Alice/Charlie `000aad1f`, Bob `82bb71b5`; Test `tests/test_graph_maintenance_review_gate.py` -> `5 passed, 1 warning` |
| Telegram Release Boundary | abgeschlossen | TLG0: Alice `c312e7f2`, Bob `b23f0d08`; Test `tests/test_telegram_release_boundary.py` -> `5 passed, 1 warning` |
| Telegram Offline Smoke Plan | abgeschlossen | TLG1: Alice `bc0af9ad`, Bob/Charlie `09009f8e`; Test `tests/test_telegram_offline_smoke_plan.py` -> `5 passed, 1 warning` |
| Telegram `getMe` Live-Smoke | teilweise belegt | Bot-Identitaet wurde manuell mit lokalem Token geprueft; kein Send-Smoke, solange `TELEGRAM_CHAT_ID` und explizites Go fehlen |
| Plugin-Modul | eingefroren | keine Plugin-Imports, kein `setup()`, keine Runtime-Aktivierung vor 1.0 |

## Harte Grenzen Bis 1.0

- Kein Plugin-Modul-Code anfassen.
- Keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime-Aktivierung.
- Kein Qdrant, Kuzu, UMAP, GMM oder adRAP als Release-Pflicht.
- Kein RAPTOR-Fullbuild, kein globaler Graph-Rebuild, keine Postgres-Live-Migration.
- Kein Full Graph Dump oder UI-Vollrendering fuer 100.000+ Graphen.
- Keine Telegram-Tokens, Chat-IDs oder Provider-Secrets in Repo, Tests, Logs, Automation-Prompts oder Handoffs.
- Keine echten Provider-, RAG-, Export-, Import-, Rebuild-, Telegram-, Netzwerk- oder Host-Aktionen ohne explizites Nutzer-Go.
- Kein externes `1.0.0`-Go ohne die zwei manuellen Pflicht-Gates: Provider-/Fallback-Antwortlauf und Test-Vault Export/Import/Rebuild.

## Was Releasefaehig Heisst

Releasefaehig bedeutet fuer diese letzte Phase:

```text
bounded, measured, reviewable, evidence-backed
```

Odysseus muss nicht jede vorbereitete Foundation live schalten. Odysseus muss beweisen, dass die 1.0-Funktionen begrenzt, nachvollziehbar, testbar und ehrlich dokumentiert sind.

## Verbleibende Pflicht-Gates

### FINAL1: Provider-/Fallback-Antwortlauf

Ziel: Das erste harte externe `1.0.0`-Gate belegen.

Referenz:

- `docs/plans/provider-fallback-answer-run-contract.md`

| Rolle | Auftrag | Scope |
| --- | --- | --- |
| Alice | `FINAL1A-provider-fallback-answer-run-contract`: Operator-Runbook fuer echten Antwortlauf, ready Query-Index, erwartete Antwort, Fallback-Verhalten, Redaction und No-Go-Sprache schreiben. | `docs/plans/provider-fallback-answer-run-contract.md`, optional kurzer Link in dieser Roadmap oder `docs/plans/1.0-evidence-release-checklist.md` |
| Bob | `FINAL1B-provider-fallback-answer-run-validator`: read-only Evidence-Validator bauen, der Provider-/Fallback-Evidence prueft, aber keinen Provider aufruft und keine Secrets annimmt. | `src/provider_fallback_answer_run.py`, `tests/test_provider_fallback_answer_run.py` |
| Charlie | Scope pruefen, fokussierten Test ausfuehren, Worktree sauber halten, Push koordinieren und echte Evidence nur mit Nutzer-Go bewerten. | Test: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_provider_fallback_answer_run.py` |

Exit:

- Provider-/Fallback-Verhalten ist real belegt oder ehrlich als No-Go/Partial markiert.
- Evidence enthaelt keine Tokens, Keys, Chat-IDs oder Rohantworten mit Secrets.
- Unit-/Validator-Schicht startet keinen Netzwerk- oder Providerlauf.

### FINAL2: Test-Vault Export/Import/Rebuild

Ziel: Das zweite harte externe `1.0.0`-Gate belegen.

Referenz:

- `docs/plans/test-vault-export-import-rebuild-contract.md`

| Rolle | Auftrag | Scope |
| --- | --- | --- |
| Alice | `FINAL2A-test-vault-export-import-rebuild-contract`: kleines Test-Vault-Runbook, Datenverlust-Warnungen, erwartete Artefakte und Go/No-Go-Sprache finalisieren. | `docs/plans/test-vault-export-import-rebuild-contract.md`, optional Link in Release-Checkliste |
| Bob | `FINAL2B-test-vault-export-import-rebuild-validator`: read-only Evidence-Validator fuer Export/Import/Rebuild-Resultate bauen. | `src/test_vault_export_import_rebuild.py`, `tests/test_test_vault_export_import_rebuild.py` |
| Charlie | Nur Nutzer-Evidence oder explizit freigegebene lokale Runs bewerten, fokussierte Tests ausfuehren und Release-Log minimal aktualisieren. | Test: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_test_vault_export_import_rebuild.py` |

Exit:

- Kleiner Test-Vault ist exportiert, importiert und rebuild-geprueft.
- Keine stillen Source-Writes, kein Datenverlust, keine unklaren Artefakte.
- Gate ist `go`, `partial` oder `no_go` mit konkreter Begruendung.

### FINAL3: Release Decision Bundle

Ziel: Aus allen Gates eine klare externe `1.0.0` Go/No-Go-Entscheidung bauen.

Referenz:

- `docs/plans/1.0-release-decision-bundle.md`

| Rolle | Auftrag | Scope |
| --- | --- | --- |
| Alice | `FINAL3A-release-decision-language`: Klartext fuer Nutzer: was ist drin, was nicht, welche Risiken bleiben, welche Gates sind belegt. | `docs/plans/1.0-release-decision-bundle.md` |
| Bob | `FINAL3B-release-decision-bundle-model`: read-only Bundle aus Graph, Telegram, Provider, Test-Vault, Plugin-Freeze und Known Limits aggregieren. | `src/release_decision_bundle.py`, `tests/test_release_decision_bundle.py` |
| Charlie | Gesamtsicht, fokussierte Tests, Push und finale Empfehlung: `go`, `partial` oder `no_go`. | Test: `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_release_decision_bundle.py` |

Exit:

- Release-Entscheidung ist nachvollziehbar.
- Offene Grenzen werden nicht als fertige Features verkauft.
- Externes `1.0.0` wird nur empfohlen, wenn FINAL1 und FINAL2 belegt sind.

## Optionales Nicht-Blockierendes Gate

### TLG2: Telegram Send-Smoke

Telegram ist fuer 1.0 vorbereitet, aber ein echter Send-Smoke blockiert den Release nicht, solange Telegram ehrlich als vorbereitet und nicht voll live dokumentiert bleibt.

Dieses Gate darf nur starten, wenn:

- ein neu rotierter Token lokal ausserhalb des Repos liegt,
- `TELEGRAM_CHAT_ID` lokal ausserhalb des Repos gesetzt ist,
- der Nutzer explizit `go` fuer einen Telegram-Send gibt,
- die Evidence redacted bleibt.

Ohne diese Bedingungen bleibt Telegram bei: **offline vorbereitet, `getMe` manuell geprueft, Send-Smoke pending**.

## Empfohlene Reihenfolge

1. `FINAL1-provider-fallback-answer-run`
2. `FINAL2-test-vault-export-import-rebuild`
3. optional `TLG2-telegram-send-smoke`
4. `FINAL3-release-decision-bundle`

## Alice/Bob/Charlie Arbeitsmodell

| Rolle | Verantwortung |
| --- | --- |
| Alice | Operator-/Nutzertexte, Runbooks, Go/No-Go-Sprache, Docs-only |
| Bob | read-only Validatoren, Modelle und Tests; keine echten Runtime-, Netzwerk-, Provider-, Export-/Import-, Graph-Rebuild-, Telegram-, Host- oder Plugin-Aktionen |
| Charlie | Scope, Tests, Worktree, Push, Stop-Entscheidung, Automation und finale Evidence-Bewertung |

## Stop-Regeln

Sofort stoppen und Nutzer fragen, wenn:

- ein Slice Plugin-Code importieren oder ausfuehren will,
- ein Slice echte Secrets in Repo, Tests, Logs, Automation-Prompts oder Handoffs schreiben will,
- ein Test versucht, einen 100.000+-Graphen als Vollpayload auszugeben,
- ein Modell Graph-Kandidaten als Wahrheit schreibt,
- Provider-, Export-/Import-, Rebuild-, Telegram-Live- oder Host-Aktionen ohne Nutzerfreigabe starten sollen,
- Hotfile-Konflikte oder fremde staged Files auftauchen,
- rote Tests keinen klaren fokussierten Fix haben.

## 1.0 Definition Von Fertig

`1.0.0` ist releasefaehig, wenn:

- FINAL1 Provider-/Fallback-Antwortlauf belegt ist,
- FINAL2 Test-Vault Export/Import/Rebuild belegt ist,
- Graph Memory bounded, review-first und evidence-backed bleibt,
- 100.000+-Graphen budgetiert statt voll gerendert werden,
- Telegram tokenfrei/offline vorbereitet ist und Live nur manuell bleibt,
- Plugin-Modul eingefroren bleibt,
- Known Limits klar dokumentiert sind,
- Charlie einen sauberen Worktree, gruene fokussierte Tests, finalen Push und eine ehrliche Go/No-Go-Empfehlung meldet.
