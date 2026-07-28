# Telegram Security Incident Runbook

Stand: 2026-07-03

Status: SIR-6 Telegram operator notification contract

Dieses Runbook beschreibt, wie Odysseus Sicherheitsvorfaelle ueber Telegram vorbereitet und kommuniziert. Es speichert keine Chat-IDs, Tokens oder privaten Inhalte im Repo.

## Ziel

Telegram soll fuer den Operator ein Kontrollkanal sein:

- Incident kurz sehen
- Action-IDs erkennen
- approve/deny geben
- Status spaeter abfragen

Telegram ist nicht der Ort fuer private Evidenz, Rohlogs oder lange Debug-Ausgaben.

## Notification-Inhalt

Eine Incident-Nachricht darf enthalten:

- Incident-ID
- Level und Level-Name
- Severity
- Confidence
- Status
- betroffene Surface
- Policy-Entscheidung
- Debug-Bundle-ID
- Action-IDs
- Approve/Deny-Hinweis

Eine Incident-Nachricht darf nicht enthalten:

- Chat-ID
- Bot-Token
- Authorization Header
- private Dokumenttexte
- E-Mail-Volltexte
- Bilddaten
- rohe Tool-Ausgaben
- absolute Hostpfade

## Beispielstruktur

```text
Security incident
ID inc-... | Level 3 contain | high | confidence 0.860 | open
Surface: telegram, odysseus_api
Policy: gated_action (...); operator gate: yes
Debug bundle: dbg-... (12 events)
Actions:
- act-...: redacted_debug_bundle [auto-safe]
- act-...: service_restart [confirm]
Approve/deny: /incident approve <action_id> or /incident deny <action_id>
```

## Approve/Deny

Approve/Deny darf nur ueber Action-ID laufen:

- `/incident approve <action_id>`
- `/incident deny <action_id>`

Die Bedeutung der Action-ID muss serverseitig aus dem Incident/Action-Store kommen. Telegram darf keine privaten Aktionsdetails als Argument erzwingen.

## DSGVO und Incident Mode

Wenn DSGVO oder Incident Mode aktiv ist:

- keine sensiblen Inhalte an externe API-Modelle
- lokale Analyse bevorzugen
- Telegram nur fuer redigierte Summary nutzen
- bei Unsicherheit blocken und Operator informieren

Voice-Nachrichten gelten nicht automatisch als DSGVO-pflichtig. Der Inhalt kann aber nach Klassifikation sensibel werden.

## Failure Handling

Wenn Telegram nicht senden kann:

- kein Retry-Sturm
- Notification-Decision speichern oder redigiert loggen
- Status als blocked/failed mit Grund setzen
- keine Tokens oder Chat-Ziele loggen

Wenn eine Action fehlt:

- nicht raten
- Hinweis geben: Action-ID unbekannt oder expired
- keine Live-Aktion ausfuehren

## Single delivery activation

A prepared Telegram notification is a no-send artifact. Delivery remains
`not_run` without a later action-specific operator decision under
`OPS-ALERT-DELIVERY-GO`. That gate is independent of observe, CrowdSec,
session, MCP-tool, deploy and temporal-closure gates; no action ID or preview
satisfies it.

The one delivery packet in
`docs/plans/security-incident-response-activation-packet.md` must state a
channel target class with server-side target readiness but no disclosed target,
exactly one redacted body/send, exact attempt timeout, single-use grant expiry,
redacted preflight evidence, no-retry recovery, independent redacted
status/correlation readback, abort conditions and later operator decision.
Abort on target/body drift, private content, timeout, retry expansion or
withdrawal. Record only the redacted final status; a delivery receipt never
authorizes or proves a remediation.

## No-Go

No-Go gilt bei:

- Versand von Rohlogs
- Persistenz von Chat-IDs im Repo
- Tokens in Fehlertexten
- Remediation durch freie Textbeschreibung
- Execute ohne Action-ID und Gate
- private Evidenz in Telegram

## Akzeptanz

Der Telegram-Security-Flow gilt als vorbereitet, wenn:

- Incident Notification redigiert ist
- approve/deny nur Action-ID nutzt
- Dispatch serverseitig konfiguriert bleibt
- fehlende Live-Konfiguration sauber blocked meldet
- keine privaten Inhalte in Tests, Docs oder Logs landen
