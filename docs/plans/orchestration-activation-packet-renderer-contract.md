# Orchestration Activation Packet Renderer Contract

Stand: 2026-06-17

Status: **AUTO19A Docs-Contract fuer Operator Activation Packet Markdown/JSON Renderer**

Quellen:

- `docs/plans/orchestration-operator-activation-packet-contract.md`
- `docs/plans/orchestration-activation-audit-trail-contract.md`
- `docs/plans/orchestration-activation-handoff-checklist-contract.md`

Dieser Contract definiert die Ausgabeform fuer ein spaeteres Operator Activation Packet in Markdown und JSON. Die Renderer bleiben reine Funktionen ueber bereits vorbereitete Packet-Daten. Der Slice fuehrt bewusst keine IO, keine Persistenz, keine Thread-Sends, keine Git-/Test-Ausfuehrung und keine Runtime-Aktivierung aus.

## Ziel

Odysseus braucht zwei sichere Ausgabeformen fuer das Operator Activation Packet:

- Markdown fuer menschliches Review
- JSON fuer stabile maschinenlesbare Weiterverarbeitung

Diese Ausgabeformen sollen:

- denselben konservativen Entscheidungsstand tragen
- keine Runtime- oder Log-Lecks erzeugen
- keine Aktivierung oder Folgeaktion ausfuehren

## Leitregel

Rendering ist pure function, nicht Ausfuehrung.

Das bedeutet:

- keine IO
- keine Persistenz
- keine Thread-Sends
- keine Git-/Test-Ausfuehrung
- keine Scheduler-Aktivierung

## Renderer Outputs

Die spaeteren Renderer sollen mindestens diese zwei Outputs kennen:

- `markdown`
- `json_dict`

## `markdown`

Der Markdown-Renderer liefert eine kompakte, operator-taugliche Review-Darstellung.

Wichtig:

- lesbar
- konservativ
- keine rohen Logs
- keine Vollprompts

## `json_dict`

Der JSON-Renderer liefert eine stabile, maschinenlesbare Dict-Struktur.

Wichtig:

- deterministisch
- keine Secrets
- keine rohen Logs
- keine komplette Prompt-Historie

## Markdown Sections

Der spaetere Markdown-Renderer soll mindestens diese Bereiche enthalten:

- `Summary`
- `Decision`
- `Gate Status`
- `Handoff Checklist`
- `Audit Events`
- `Evidence`
- `Blocked Runtime Actions`
- `Operator Next Step`

## Bedeutung der Markdown Sections

### `Summary`

Kurze Gesamtzusammenfassung des Packets.

Typische Inhalte:

- was angefragt wurde
- welche Hauptlage besteht
- worauf der Operator schauen soll

### `Decision`

Zeigt den konservativen Decision State des Packets.

### `Gate Status`

Verdichtet Gate-Zustaende und macht Blocker oder Review-Bedarf sofort sichtbar.

### `Handoff Checklist`

Zeigt die relevanten Checklist-Items mit ihren Statuswerten.

### `Audit Events`

Listet nur die relevanten Audit-Ereignisse als kompakte Zusammenfassung.

### `Evidence`

Zeigt nur Evidence-Referenzen oder kurze Hinweise, keine Rohdaten.

### `Blocked Runtime Actions`

Macht explizit sichtbar, welche Runtime-Aktionen weiterhin nicht erlaubt sind.

### `Operator Next Step`

Zeigt genau die naechste sichere Handlung fuer den Operator.

Wichtig:

- keine implizite Automatisierung

## Decision Copy

Die Renderer muessen konservative Decision-Copy fuer mindestens diese Zustaende abbilden:

- `ready_for_review`
- `blocked`
- `approved_pending_runtime_gate`
- `cancelled`
- `deferred`

## Bedeutung der Decision Copy

### `ready_for_review`

Das Packet ist reviewbar, aber nicht automatisch aktiv.

### `blocked`

Mindestens eine relevante Sperre verhindert den naechsten Schritt.

### `approved_pending_runtime_gate`

Es gibt eine dokumentierte Vorfreigabe, aber die Runtime-Gates bleiben noch geschlossen.

### `cancelled`

Die Aktivierungsabsicht wurde verworfen.

### `deferred`

Die Aktivierungsabsicht wurde vertagt oder wartet auf spaetere Bedingungen.

## JSON-Anforderungen

`json_dict` muss spaeter:

- stabil
- maschinenlesbar
- deterministisch

bleiben.

Nicht enthalten:

- komplette Prompts
- Tokens
- rohe Logs
- komplette Thread-Historien
- Runtime-Dumps

Zulaessig:

- kompakte Decision-Werte
- Gate-Zustaende
- Evidence-Referenzen
- Audit-Zusammenfassungen

## No-Secrets Regel

Weder Markdown noch JSON duerfen enthalten:

- Secrets
- Tokens
- API-Schluessel
- rohe Credential-Hinweise

Wenn ein Feld potentiell sensibel waere, darf es nur als kurze Referenz oder bereinigter Hinweis erscheinen.

## Rendering als pure function

Der spaetere Renderer soll nur aus einem vorhandenen `OperatorActivationPacket` rendern.

Das bedeutet:

- keine Dateizugriffe
- keine Datenbankzugriffe
- keine Netzwerkaufrufe
- keine Runtime-Checks
- keine Git-/Test-Kommandos

## Conservative Rendering Logic

Die Kurzlogik lautet:

- der Renderer spiegelt nur den vorhandenen Decision State
- Blocker werden sichtbar, nicht beschoenigt
- `approved_pending_runtime_gate` bleibt sichtbar gesperrt fuer Runtime
- `ready_for_review` ist kein Live-Go

## Markdown-Stil

Der Markdown-Output soll:

- kurz
- scanbar
- operator-tauglich

sein.

Er soll nicht:

- ausschweifen
- rohe Logs kopieren
- mehrere konkurrierende Next Steps gleichzeitig ausgeben

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Renderer-Funktionen bauen fuer:

- `OperatorActivationPacket -> Markdown/String`
- `OperatorActivationPacket -> dict`

Wichtig:

- keine IO
- kein Netzwerk
- keine Runtime-Hooks
- keine Thread-Sends
- keine Git-/Test-Ausfuehrung

## Beispiel fuer spaeteren sicheren Markdown-Output

Zulaessig:

- `Summary: activation review for AUTO18 scope`
- `Decision: blocked`
- `Gate Status: fail due to foreign staged files`
- `Operator Next Step: clear staged file conflict before review`

Nicht zulaessig:

- kompletter Audit-Dump
- kompletter Prompt-Text
- `send thread now`
- `run tests now`

## Beispiel fuer spaeteren sicheren JSON-Output

Zulaessig:

- `decision_state`
- `summary`
- `gate_status`
- `handoff_checklist`
- `audit_events`
- `evidence_refs`
- `blocked_runtime_actions`
- `operator_next_step`

Nicht zulaessig:

- `full_prompt_text`
- `secret_token`
- `raw_log_blob`
- `dispatch_now`

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Persistenz
- keine Runtime-Aktivierung
- keine Thread-Sends
- keine Git-/Test-Runner
- keine Live-Checks beim Rendern

Er legt nur fest, wie ein spaeteres Operator Activation Packet konservativ, lesbar und maschinenlesbar nach Markdown und JSON gerendert werden soll.
