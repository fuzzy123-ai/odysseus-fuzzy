# Release Runtime Readiness Roadmap

Stand: 2026-06-17

Status: **aktive Roadmap fuer die letzten Schritte bis zum externen Release**

Diese Roadmap konkretisiert die Punkte, die nach dem internen Release-Candidate-Stand noch bis zum externen Release geklaert werden muessen:

- RAPTOR-/Graph-Memory releasefaehig absichern
- grosse Graphen mit 100.000+ Elementen sicher handhaben
- Telegram bis zu einem sicheren Release-Gate bringen
- die zwei offenen manuellen Release-Gates belegen
- Plugin-Modul vorerst unangetastet lassen

## Leitentscheidung

Bis zum Release wird nicht versucht, alle vorbereiteten Foundations live zu schalten. Releasefaehig bedeutet hier:

```text
bounded, measured, reviewable, evidence-backed
```

Odysseus soll fuer grosse Memory- und Graph-Daten nicht "alles laden", sondern beweisen, dass relevante Ausschnitte, Budgets, Cursor, Clipping-Hinweise und Evidence sauber funktionieren. Telegram soll token- und allowlist-sicher vorbereitet werden. Das Plugin-Modul bleibt unveraendert, bis die Open-Source-Plugin-Landschaft reifer ist oder ein konkreter Operator-Review eine Aktivierung rechtfertigt.

## Harte Release-Grenzen

Diese Grenzen gelten fuer alle Slices:

- kein Plugin-Modul-Code anfassen
- keine Plugin-Imports, kein `setup()`, keine Plugin-Runtime-Aktivierung
- kein Qdrant, Kuzu, UMAP, GMM oder adRAP als Release-Pflicht
- kein unbounded Full-Graph-Load
- kein Telegram-Token im Repo, in Logs oder in Test-Fixtures
- keine Telegram-Sends ohne explizites manuelles Operator-Gate
- keine Host-Kommandos aus Odysseus-Core
- kein externes `1.0.0`-Go ohne Provider-Proof und Test-Vault-Rebuild-Evidence

## Release-Zielbild

### RAPTOR-/Graph-Memory

Fuer den Release gilt RAPTOR/Graph Memory als releasefaehig, wenn:

- Cluster, Summaries und Graph-Maintenance weiter als Derived Data behandelt werden
- kleine Maintenance-Modelle nur vorbereitete, begrenzte Aufgaben bekommen
- Entity- und Edge-Vorschlaege nie automatisch Wahrheit schreiben
- jeder Graph-Maintenance-Output Provenance, Confidence, Dedupe und Review-Status traegt
- Fallback- und Drift-Gates dokumentiert und getestet sind
- Known Limits klar sagen, dass kein RAPTOR-Fullbuild und kein globaler Graph-Rebuild live aktiviert ist

### 100.000+ Graphen

Fuer den Release bedeutet 100.000+-Faehigkeit nicht, dass die UI 100.000 Knoten rendert. Es bedeutet:

- synthetische oder Fixture-basierte Large-Graph-Evidence mit mindestens 100.000 Knoten/Kanten kann erzeugt oder referenziert werden
- die Progressive Graph API liefert nur budgetierte Subgraphs und Aggregate
- `max_nodes`, `max_edges`, `depth`, `max_hops`, `limit`, Cursor und Payload-Budgets sind Pflicht
- bei Ueberschreitung gibt es `partial` oder `clipped` mit `reason` und `next_action`
- die UI-/API-Grenze verhindert Full Dumps
- Performance-/Payload-Evidence ist dokumentiert

### Telegram

Telegram gilt fuer den Release als sicher vorbereitet, wenn:

- `/status` und `/alerts` tokenfrei parse-, authorize- und renderbar sind
- Allowlist-Verhalten getestet ist
- Redaction-Regeln fuer Status- und Alert-Texte dokumentiert sind
- ein Dry-Run mit Fixture-Snapshots funktioniert
- ein optionaler manueller Live-Smoke nur mit Operator-Token ausserhalb des Repos beschrieben ist
- ohne Token-Freigabe Telegram als vorbereitet, aber nicht live markiert bleibt

### Plugin-Modul

Das Plugin-Modul bleibt bis auf Weiteres eingefroren:

- keine neuen Plugin-Loader-Features
- keine Runtime-Aktivierung
- keine neuen Plugin-Imports
- keine Setup-/Discovery-Ausfuehrung
- nur bestehende read-only Manifest-, Audit-, Capability- und Review-Modelle duerfen als Evidence referenziert werden

## Release-Slices

### RGM0: Graph-Memory Evidence Map

Ziel: Den aktuellen Graph-/RAPTOR-/Maintenance-Stand in eine klare Release-Evidence-Sicht bringen.

| Rolle | Auftrag |
| --- | --- |
| Alice | Nutzerverstaendliche Beschreibung schreiben: Was ist Graph Memory, was ist RAPTOR-Maintenance, was ist bewusst noch kein Live-Fullbuild? |
| Bob | Bestehende Modelle und Tests fuer Graph Maintenance, Derived Cluster, Summary Worker, Evaluation Gates und Fallback Routing zu einer read-only Evidence Map aggregieren. |
| Charlie | Scope pruefen, fokussierte Tests laufen lassen, Known-Limits-Abgleich gegen Release-Checkliste machen. |

Exit:

- Release-Evidence benennt klar, dass RAPTOR/GraphRAG-Maintenance vorbereitet und begrenzt ist
- keine globale Wahrheitsschreibung durch Modelle
- keine neue Storage-Migration

### RGM1: 100k Large-Graph Budget Proof

Ziel: Beweisen, dass Odysseus grosse Graphen nicht voll laedt, sondern budgetiert behandelt.

| Rolle | Auftrag |
| --- | --- |
| Alice | Operator-Text fuer 100.000+-Graphen schreiben: "grosse Datenmenge ja, Full Dump nein". |
| Bob | Synthetischen Large-Graph-Proof als isoliertes Modell/Test bauen: 100.000+ Knoten/Kanten als Zaehler/Fixture/Generator, aber API-Output bleibt klein, clipped und cursor-basiert. |
| Charlie | Laufzeit, Speicherannahmen und Testgrenzen pruefen; sicherstellen, dass kein echter Vollgraph im Test in UI oder Chat gedumpt wird. |

Exit:

- Test belegt budgetierte Antwort bei grossem Input
- `partial`/`clipped` wird mit Grund und naechster Aktion ausgegeben
- keine Qdrant-/Kuzu-Abhaengigkeit

### RGM2: Progressive Graph API Release Gate

Ziel: Die Progressive-Graph-API als Release-Grenze festziehen.

| Rolle | Auftrag |
| --- | --- |
| Alice | UX-/API-Sprache fuer Subgraph, Aggregate, Clipping und "show more" finalisieren. |
| Bob | Bestehende `progressive_graph_api`-Modelle um Release-Gate-Summary oder Markdown/JSON-Renderer ergaenzen, falls noetig. |
| Charlie | Tests gegen unbounded Payloads, fehlende Budgets und unehrliches Clipping pruefen. |

Exit:

- kein Graph-Endpoint darf ohne Budget als releasefaehig gelten
- Release-Report kann Graph-Readiness kurz zusammenfassen

### RGM3: Graph Maintenance Review Queue Gate

Ziel: Sicherstellen, dass Entity-/Edge-Kandidaten Review-Objekte bleiben.

| Rolle | Auftrag |
| --- | --- |
| Alice | Review-Queue-Sprache fuer Graph-Kandidaten und Confidence formulieren. |
| Bob | Tests fuer Kandidaten, Dedupe, Provenance, Confidence, Drift und blockierte Truth-Write-Claims ergaenzen. |
| Charlie | Stop-Gate pruefen: kein Kandidat wird automatisch Wahrheit. |

Exit:

- Graph-Maintenance bleibt review-first
- Truth-Write-Claims blockieren

### TLG0: Telegram Release Boundary

Ziel: Telegram als sicheren, begrenzten Release-Pfad definieren.

Referenz:

- `docs/plans/telegram-release-boundary-contract.md`

| Rolle | Auftrag |
| --- | --- |
| Alice | Nutzer- und Operator-Text: Telegram ist optional, tokenfrei vorbereitet, Live-Nutzung braucht manuelle Freigabe. |
| Bob | Bestehende Telegram-Adapter- und Dry-Run-Modelle gegen Release-Gates abgleichen. |
| Charlie | Token-/Log-/Allowlist-Regeln gegen Release-Checkliste pruefen. |

Exit:

- Telegram kann nicht versehentlich live starten
- Token-Regeln sind eindeutig

### TLG1: Telegram Offline End-to-End Smoke

Ziel: Ohne Netzwerk pruefen, dass Status- und Alert-Texte sauber entstehen.

| Rolle | Auftrag |
| --- | --- |
| Alice | Erwartete Beispielantworten fuer `/status` und `/alerts` definieren. |
| Bob | Offline-Smoke mit Fixture-Health-Snapshot, Allowlist und Redaction bauen. |
| Charlie | Fokussierten Telegram-Test ausfuehren und Evidence dokumentieren. |

Exit:

- `/status` und `/alerts` funktionieren offline
- nicht allowlisted Nutzer werden blockiert
- keine Tokens, keine Chat-IDs, kein Netzwerk

### TLG2: Optionaler manueller Telegram Live-Smoke

Ziel: Nur falls der Nutzer explizit Token und Freigabe gibt, ein minimaler echter Smoke.

| Rolle | Auftrag |
| --- | --- |
| Alice | Manuelles Runbook mit Redaction- und Abbruchregeln schreiben. |
| Bob | Keinen Live-Sender bauen, solange keine Freigabe vorliegt; nur Evidence-Validator vorbereiten. |
| Charlie | Bei Freigabe einen manuellen, redacted Evidence-Eintrag pruefen; ohne Freigabe bleibt Gate `manual_pending`. |

Exit:

- mit Freigabe: ein redacted Smoke ist dokumentiert
- ohne Freigabe: Telegram bleibt vorbereitet, aber nicht live

### REL-GATE1: Provider-/Fallback-Antwortlauf

Ziel: Das erste harte externe `1.0.0`-Gate belegen.

| Rolle | Auftrag |
| --- | --- |
| Alice | Operator-Checkliste fuer Modell, Fallback, erwartete Antwort und Known Limits. |
| Bob | Read-only Evidence-Validator fuer Provider-Proof-Ausgabe, keine Secrets. |
| Charlie | Nur mit Nutzerfreigabe echten Lauf oder Nutzer-Evidence bewerten und Release-Log aktualisieren. |

Exit:

- Provider-/Fallback-Verhalten ist real belegt oder ehrlich als No-Go/Partial markiert

### REL-GATE2: Test-Vault Export/Import/Rebuild

Ziel: Das zweite harte externe `1.0.0`-Gate belegen.

| Rolle | Auftrag |
| --- | --- |
| Alice | Test-Vault-Runbook und Datenverlust-Warnungen finalisieren. |
| Bob | Evidence-Validator fuer Export/Import/Rebuild-Resultate, keine echten Source-Writes. |
| Charlie | Manuelle Evidence pruefen, fokussierte Tests ausfuehren, Release-Log aktualisieren. |

Exit:

- kleiner Test-Vault ist exportiert, importiert und rebuild-geprueft
- kein Datenverlust, keine stillen Source-Writes

### REL-FINAL: Release Decision Bundle

Ziel: Aus allen Gates eine klare Go/No-Go-Entscheidung fuer den Nutzer bauen.

| Rolle | Auftrag |
| --- | --- |
| Alice | Release-Erklaerung in Klartext: was ist drin, was nicht, welche Risiken bleiben. |
| Bob | Readiness-Bundle aggregiert Graph, Telegram, Provider, Rebuild und Known Limits. |
| Charlie | Gesamttests, Worktree, Push, finale Go/No-Go-Empfehlung. |

Exit:

- Release-Entscheidung ist nachvollziehbar
- offene Grenzen sind nicht als Features verkauft
- Plugin-Modul bleibt unangetastet

## Empfohlene Reihenfolge

1. `RGM0-graph-memory-evidence-map`
2. `RGM1-large-graph-budget-proof`
3. `RGM2-progressive-graph-api-release-gate`
4. `RGM3-graph-maintenance-review-queue-gate`
5. `TLG0-telegram-release-boundary`
6. `TLG1-telegram-offline-e2e-smoke`
7. `REL-GATE1-provider-fallback-answer-run`
8. `REL-GATE2-test-vault-export-import-rebuild`
9. `TLG2-optional-manual-telegram-live-smoke`
10. `REL-FINAL-release-decision-bundle`

`TLG2` ist optional. Wenn kein echter Telegram-Token freigegeben wird, blockiert das nicht automatisch `1.0.0`, solange Telegram ehrlich als vorbereitet, aber nicht live dokumentiert ist.

## Fortschrittsmodell

| Bereich | Release-Pflicht | Aktueller Zielstatus |
| --- | --- | --- |
| Graph-Memory Evidence | ja | muss als Release-Evidence gebuendelt werden |
| 100.000+-Graph-Budget-Proof | ja | synthetischer/budgetierter Proof reicht, kein Full Runtime Load |
| Progressive Graph API Gate | ja | bestehende Modelle als Release-Gate absichern |
| Graph Maintenance Review Gate | ja | Kandidaten bleiben Review-first |
| Telegram Offline Smoke | ja, wenn Telegram als vorbereitet genannt wird | tokenfrei/offline |
| Telegram Live Smoke | optional | nur mit Nutzerfreigabe |
| Provider Proof | ja | manuelles Gate offen |
| Test-Vault Rebuild | ja | manuelles Gate offen |
| Plugin-Modul | nein | eingefroren |
| Qdrant/Kuzu/adRAP | nein | post-release |

## Stop-Regeln

Sofort stoppen und Nutzer fragen, wenn:

- ein Slice Plugin-Code importieren oder ausfuehren will
- ein Slice echte Telegram-Tokens oder Chat-IDs ins Repo schreiben will
- ein Test versucht, einen 100.000+-Graphen als Vollpayload auszugeben
- ein Modell Graph-Kandidaten als Wahrheit schreibt
- Provider-, Export-/Import-, Rebuild- oder Telegram-Live-Aktionen ohne Nutzerfreigabe starten sollen
- Hotfile-Konflikte oder fremde staged Files auftauchen

## Definition von Releasefaehig

Der Release ist erreicht, wenn:

- Graph Memory bounded, review-first und evidence-backed ist
- 100.000+-Graphen als budgetierte Ausschnitte bewiesen sind
- Telegram offline sicher funktioniert und live nur optional/manuell bleibt
- Provider-Proof und Test-Vault-Rebuild manuell belegt sind
- Plugin-Modul eingefroren bleibt
- Known Limits offen dokumentiert sind
- Charlie einen sauberen Worktree, gruene fokussierte Tests und finalen Push meldet
