# Manual Release Evidence Artifact Contract

Stand: 2026-06-17

Status: **REL44A Docs-Contract fuer ein manuelles Release-Evidence-Artefakt**

Quellen:

- `docs/plans/manual-release-evidence-operator-index.md`
- `docs/plans/provider-proof-operator-runbook.md`
- `docs/plans/export-import-rebuild-operator-runbook.md`

Dieser Contract definiert ein kleines, reproduzierbares Artefakt fuer die letzten offenen manuellen `1.0`-Gates. Das Artefakt dient nur als Status- und Gap-Snapshot fuer Charlie oder einen Operator. Es ersetzt keine echte beobachtete Evidence und erzeugt kein externes `1.0`-Go.

## Zweck

Das Artefakt soll morgens oder vor einem manuellen Lauf schnell zeigen:

- welche manuellen `1.0`-Gates noch offen sind
- welche Operator-Runbooks relevant sind
- ob der Status aktuell `pending`, `partial` oder `blocked` ist

Es ist gedacht als:

- reproduzierbarer Snapshot
- read-only Uebergabehilfe
- Orientierung fuer Operator oder Charlie

Es ist nicht gedacht als:

- Beweis, dass ein manueller Gate-Lauf schon stattgefunden hat
- Release-Freigabe
- Ersatz fuer das echte Evidence-Log

## Bestandteile

Das Artefakt soll spaeter mindestens diese Teile tragen:

- `markdown_summary`
- `json_report`
- `sha256_digest`
- optional `generated_at`
- optional `operator_context`

## Bedeutungen

### `markdown_summary`

Kurz lesbare menschenfreundliche Zusammenfassung fuer Handoff, Morgenbrief oder Operator-Check.

### `json_report`

Kanonischer maschinenlesbarer Snapshot ueber:

- offene manuelle Gates
- Status
- Blocker
- Referenzen auf die Runbooks

### `sha256_digest`

Stabiler Digest ueber den kanonischen JSON-Report.

### `generated_at`

Optionaler Zeitstempel, wann das Artefakt erzeugt wurde.

### `operator_context`

Optionaler Kontextblock, z. B. Branch, Commit oder Laufzweck.

## Digest-Regel

Der Digest wird ueber kanonisches JSON gebildet, nicht ueber Markdown.

Das bedeutet:

- `sha256_digest` haengt nicht von Zeilenumbruechen oder Markdown-Formatierung ab
- menschenlesbare Texte duerfen sich veraendern, ohne die JSON-Quelle aus dem Blick zu verlieren
- maschinenlesbare Stabilitaet hat Vorrang vor Text-Layout

## No-Go-Regel

Das Artefakt ist nur ein Status- oder Gap-Snapshot.

Es ist kein Go-Beweis.

Das bedeutet:

- ein vorhandenes Artefakt beweist nicht, dass Provider Proof erledigt ist
- ein vorhandenes Artefakt beweist nicht, dass Export/Import/Rebuild erledigt ist
- ein vorhandener Digest beweist nur Konsistenz eines Snapshots, nicht die Wahrheit eines echten manuellen Laufs

Solange echte manuelle Evidence fehlt, bleibt:

- `No-Go fuer externes 1.0 Release`

## Ablage- und Copy-Regeln

In dieses Artefakt gehoeren nicht:

- Secrets
- API Keys
- Bearer Tokens
- komplette Providerantworten
- sensible Snippets
- komplette Vault-Inhalte

Erlaubt sind nur:

- Status
- Gate-Namen
- Blocker
- kompakte Runbook-Referenzen
- harmlose Metadaten wie Branch, Commit oder Zeitstempel

## Sicherheitsgrenzen

Die sichere Kurzform lautet:

- keine Secrets
- keine sensiblen Providerantworten
- keine sensiblen Test-Vault-Inhalte
- keine menschlichen Quellen im Artefakt

Wenn diese Grenze nicht sicher eingehalten werden kann:

- Artefakt nicht erzeugen
- oder nur als bewusst unvollstaendig/blockiert markieren

## Beziehung zum echten Evidence-Log

Das Artefakt ersetzt nicht:

- `docs/plans/1.0-manual-release-evidence-log.md`

Das echte Evidence-Log bleibt der Ort fuer:

- beobachtete manuelle Gates
- Datum
- Ergebnis
- Blocker
- echte Evidence-Links

Das Artefakt bleibt:

- vorbereitender Snapshot
- Operator-Orientierung
- Gap-Bericht

## Erwartete Struktur des JSON-Reports

Der JSON-Report soll spaeter mindestens ausdruecken koennen:

- welche manuellen Gates offen sind
- ob sie `pending`, `partial` oder `blocked` sind
- welche Detail-Runbooks relevant sind
- ob ein externer `1.0`-Go-Status weiterhin blockiert bleibt

## Erwartete Struktur der Markdown-Zusammenfassung

Die Markdown-Zusammenfassung soll spaeter knapp zeigen:

- offene Gates
- naechste Operator-Schritte
- klare No-Go-Notiz
- Verweise auf Provider- und Export/Import/Rebuild-Runbooks

## Akzeptanz fuer spaeteren Bob-Slice

Der spaetere Bundle-Helper darf:

- vorhandene Renderer verwenden
- vorhandenen Digest verwenden
- daraus ein kleines Dataclass- oder Dict-Modell bauen

Erwartung:

- kein neues Freigabelogik-System
- keine doppelte Wahrheit neben Evidence-Log und Runbooks
- nur ein kleiner, stabiler Read-only-Bundle-Layer

## Akzeptanzkriterien

Dieser Contract ist nur dann sauber abgeschlossen, wenn ein spaeterer Bob-Slice daraus ein kleines Artefaktmodell bauen kann, ohne neue Produktdebatte.

Mindestens klar sein muss:

- das Artefakt ist ein Snapshot, kein Go-Beweis
- `markdown_summary`, `json_report` und `sha256_digest` sind Pflichtteile
- `generated_at` und `operator_context` sind optionale Metadaten
- der Digest basiert auf kanonischem JSON, nicht auf Markdown
- keine Secrets oder sensiblen Snippets duerfen im Artefakt landen
- das echte Evidence-Log bleibt der einzige Ort fuer echte beobachtete manuelle Evidence

## Nicht-Ziele

Dieser Contract fuehrt bewusst nicht aus:

- keine Code-Implementierung
- keine Tests
- keine Runtime-Aenderung
- keine Release-Freigabe
- keine echte manuelle Evidence-Erfassung

Der Contract beschreibt nur, wie ein manuelles Release-Evidence-Artefakt als sicherer, reproduzierbarer Gap- und Status-Snapshot aussehen soll.
