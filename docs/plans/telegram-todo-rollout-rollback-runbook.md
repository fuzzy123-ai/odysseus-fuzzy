# Telegram Todo Rollout, Rollback und Live-Gates

Status: repo-only vorbereitet, alle Live-Aktionen blockiert.

Dieses Runbook beschreibt ausschließlich den Operator-Review. Der Renderer
`scripts/build_telegram_todo_rollout_packet.py` gibt JSON auf stdout aus und
besitzt keinen Executor. Jedes Paket bleibt `mode=plan_only`,
`execution_supported=false` und `execution_state=blocked`, selbst wenn alle
Voraussetzungs-Refs vorhanden sind.

## Paket vorbereiten

Vor einem Review werden ein exakter 40-stelliger Build-Commit, ein davon
verschiedener exakter Rollback-Commit und ein contentfreier Environment-Ref
festgehalten. Abgekürzte Commits, Hostnamen, URLs, Pfade, Tokens, Chat-IDs und
private Todo-Inhalte werden nicht akzeptiert. Evidence wird ausschließlich als
versionierter contentfreier Ref übergeben.

Der Renderer darf lokal zur Planprüfung aufgerufen werden. Seine Ausgabe ist
kein Live-Go, keine Deployment-Anweisung und keine Behauptung über einen
erfolgten Smoke. Der endgültige Build-Commit muss nach Abschluss und Review des
Repository-Slices neu ermittelt und in ein frisches Paket eingesetzt werden.

## Vier unabhängige Aktionen

Kein Gate impliziert ein anderes. Jede Aktion benötigt ihren eigenen exakten
GO-Satz mit der im Paket enthaltenen `release_id`; eine Freigabe darf nicht
wiederverwendet oder auf ein anderes Environment übertragen werden.

### TTD-LIVE-DEPLOY

Benötigt fokussierte und integrierte Test-Evidence, Healthcheck-Vertrag und
Pre-Update-Backup. Nach einem separat autorisierten Operator-Deploy müssen der
exakte Commit und die redigierte Readiness zurückgelesen werden. Bei Commit-
Drift, fehlendem Backup oder degradiertem Healthcheck wird abgebrochen.

Der Code-Rollback wählt ausschließlich den geprüften Rollback-Commit und
wiederholt Health/Readiness. Er führt keinen Daten-Rollback aus.

### TTD-LIVE-DATA-REPAIR

Benötigt Deploy-Readback, Todo-Drift-Preview, eigenständiges Datenbackup und
operator-geprüften Repair-Scope. Nur die im Preview enthaltenen Todo-Refs dürfen
nach einem separaten Go geändert werden. Notes- und Digest-Postconditions müssen
danach maschinenlesbar verifiziert sein.

Der Daten-Rollback restauriert nur das geprüfte Backup über einen ebenfalls
operator-kontrollierten Pfad. Er ändert keinen Code und darf keine unbeteiligten
Listen, Items oder Owner berühren.

### TTD-LIVE-TELEGRAM-SMOKE

Benötigt Deploy-Readback, redigierten Testkanal-Ref, Todo-Readback- und
Digest-Schedule-Vertrag. Der Scope ist genau ein synthetisches Todo: anlegen,
kanonische Aufnahme prüfen, erledigen, Digest-Ausschluss prüfen und höchstens an
den ausdrücklich geprüften Testkanal senden. Fehlende Receipts oder ein anderer
Kanal brechen den Smoke ab.

Cleanup darf nur das synthetische Smoke-Todo im selben genehmigten Scope
entfernen. Weitere Sends oder Änderungen an vorhandenen Daten sind verboten.

### TTD-LIVE-ROLLOVER-SMOKE

Benötigt Deploy-Readback, einen redigierten internen Rollover-Scope,
Archive-/Privacy-Vertrag und Todo-Readback-Vertrag. Nach einem separaten Go ist
genau ein kontrollierter Rollover erlaubt. Alte Session, neue Bindung,
Archive-after-bind, Single-use Continuity und kanonischer Todo-Readback werden
geprüft. Telegram-Send und Session-Delete bleiben verboten.

Recovery darf nur die vorherige lesbare Bindung operator-geprüft wiederbinden.
Beide Histories bleiben erhalten; es findet kein Todo- oder Daten-Rollback statt.

## Evidenz und Stop-Regeln

Alle Ergebnisse bleiben contentfrei: Commits, versionierte Hash-Refs, Status,
Counts und boolesche Gates. Keine Tokens, privaten Texte, direkten Chat-IDs,
Hostziele oder Rohlogs werden in Paket, Roadmap oder Handoff übernommen.

Sofort stoppen bei Commit-/Environment-Drift, fehlendem oder stale Backup,
abweichendem Testkanal, fehlender Postcondition, parallelem Rollover, aktivem
Turn, Delete-/Migration-Pfad, unklarer Recovery oder irgendeiner Aktion ohne
ihren eigenen action-specific Live-Go.
