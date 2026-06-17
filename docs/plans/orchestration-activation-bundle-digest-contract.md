# Orchestration Activation Bundle Digest Contract

Stand: 2026-06-17

Status: **AUTO14A Docs-Contract fuer deterministischen Digest des AUTO Activation Bundle**

Quellen:

- `docs/plans/orchestration-activation-bundle-contract.md`
- `docs/plans/orchestration-activation-summary-renderer-contract.md`

Dieser Contract definiert, wie fuer das AUTO Activation Bundle ein stabiler Digest gebildet wird. Der Digest dient nur Change Detection, Traceability, Snapshot-History und Diff. Er fuehrt keine Aktivierung aus und darf nicht als Live-Signal interpretiert werden.

## Ziel

Odysseus braucht einen deterministischen Bundle-Digest, damit gleiche Inhalte auch wirklich gleich aussehen und nur inhaltliche Aenderungen einen neuen Fingerabdruck erzeugen.

Der Digest soll:

- gleiches Bundle stabil wiedererkennen
- No-change Detection erlauben
- History und Handoff erleichtern
- UI-Cache oder spaetere Snapshot-Vergleiche unterstuetzen

## Leitregel

Digest misst Bundle-Inhalt, nicht Anzeige oder Laufzeitrauschen.

Das bedeutet:

- Hashquelle ist die kanonische Bundle-Dict-Struktur
- Anzeige-Markdown ist nicht die Wahrheitsquelle fuer den Digest
- volatile Felder duerfen den stabilen Digest nicht unnoetig veraendern

## Digest-Grundregel

Der stabile Digest soll ueber kanonisches JSON der Bundle-Dict-Struktur gebildet werden.

Das bedeutet:

- zuerst Bundle in eine stabile Dict-Form bringen
- daraus kanonisches JSON erzeugen
- ueber dieses kanonische JSON den Digest berechnen

## Kanonisches JSON

Das kanonische JSON soll:

- stabile Feldnamen verwenden
- stabil serialisierbar sein
- stabile Reihenfolge fuer Felder und Listen nutzen, soweit die Bundle-Struktur das vorgibt
- nur den inhaltlichen Zustand des Bundles repraesentieren

Nicht Teil der Kanonisierung:

- rohe Logs
- Trace-Dumps
- zufaellige Reihenfolgen
- Provider- oder Laufzeitrauschen

## Hashquelle

Die Wahrheitsquelle fuer den stabilen Digest ist die Bundle-Dict-Struktur.

Mindestens inhaltlich relevant:

- `readiness_report`
- `activation_plan`
- `summary`
- `json_snapshot`
- `markdown_snapshot`
- `label`, falls als inhaltlich bedeutend genutzt

## Markdown-Regel

Markdown ist Anzeige, nicht eigene Hashquelle.

Das bedeutet:

- Markdown soll nicht separat als eigenstaendige Digest-Quelle gehasht werden
- der Digest wird nicht aus gerendertem Markdown allein gebildet
- Markdown bleibt Teil des Bundles nur als Anzeigesnapshot

Wenn Markdown in der Bundle-Dict-Struktur enthalten ist, zaehlt es nur als Bundle-Feld innerhalb des kanonischen JSON, nicht als konkurrierende zweite Wahrheitsquelle.

## Umgang mit `generated_at`

`generated_at` soll bewusst aus dem stabilen Digest ausgeschlossen werden.

Grund:

- gleiche Inhalte sollen nicht nur wegen eines neuen Zeitstempels anders wirken

Das bedeutet:

- derselbe inhaltliche Bundle-Zustand behaelt denselben stabilen Digest
- Zeitstempel allein erzeugt keinen neuen Inhalts-Digest

## Optionaler `audit_digest`

Optional darf spaeter ein separater `audit_digest` definiert werden, der `generated_at` oder aehnliche Audit-Metadaten bewusst mit einbezieht.

Wichtig:

- `audit_digest` ist nicht der stabile Inhalts-Digest
- `audit_digest` dient eher Nachvollziehbarkeit oder Ablaufdokumentation

## No-Go-Regeln

Der Digest darf nicht auf instabilen oder sensiblen Quellen beruhen.

Nicht in die stabile Hashquelle aufnehmen:

- Secrets
- Logs
- Live-Daten
- Thread-Verlaeufe
- Scheduler-Trace
- Test-Runner-Rohdaten
- volatile Zeit- oder Prozessdaten

## Use Cases

Der Digest soll mindestens diese sicheren Use Cases unterstuetzen:

- No-change Detection
- History
- Handoff
- UI cache

## Bedeutung der Use Cases

### No-change Detection

Wenn der stabile Digest gleich bleibt, kann ein spaeterer Helfer konservativ annehmen:

- keine inhaltliche Bundle-Aenderung

### History

Snapshot-History kann spaeter anhand des Digests erkennen:

- wann sich wirklich Inhalt geaendert hat
- wann nur Metadaten oder Zeitstempel anders waren

### Handoff

Charlie oder Operator kann einen Handoff mit einem kompakten Fingerabdruck referenzieren, statt das ganze Bundle erneut auszubreiten.

### UI cache

Spaetere UI kann denselben Digest nutzen, um unveraenderte Bundle-Zustaende nicht unnoetig neu zu behandeln.

## Determinismus-Regeln

Der stabile Digest soll fuer identische Bundle-Inhalte immer identisch sein.

Dafuer gelten mindestens diese Regeln:

- kanonisches JSON statt freier Serialisierung
- keine volatile Metadaten in der stabilen Hashquelle
- keine zweite konkurrierende Hashbasis aus Markdown
- keine zufaellige Sortierung

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf kleine Helper bauen fuer:

- canonical-json
- sha256 ueber das kanonische JSON

Wichtig:

- keine IO
- keine Thread-Hooks
- keine Git-Hooks
- keine Test-Hooks
- keine Scheduler-Hooks

## Beispiel fuer spaetere sichere Trennung

Zulaessig:

- `stable_digest` ueber kanonisches JSON ohne `generated_at`
- optional `audit_digest` mit `generated_at`

Nicht zulaessig:

- Digest direkt ueber Markdown-Dateitext als einzige Quelle
- Digest ueber Bundle plus rohe Logs
- Digest ueber Live-Thread- oder Scheduler-Daten

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Live-Aktivierung
- keine Persistenz- oder History-Implementierung
- keine API- oder UI-Implementierung
- keine IO-Operationen

Er legt nur fest, wie der Digest des AUTO Activation Bundle spaeter stabil, deterministisch und no-go-sicher gebildet werden soll.
