# CrowdSec Remediation Runbook

Stand: 2026-07-03

Status: SIR-6 gated remediation contract

Dieses Runbook beschreibt, wie Odysseus CrowdSec-Aktionen vorbereiten darf. Es aktiviert keine Live-Aktion und ersetzt keine Operator-Freigabe.

## Ziel

Odysseus darf CrowdSec als defensive Quelle und spaetere Remediation-Schicht nutzen, aber nur mit klarer Trennung:

- lesen und zusammenfassen: erlaubt, wenn redigiert
- Ban/Unban vorbereiten: erlaubt als Plan
- Ban/Unban ausfuehren: nur mit explizitem Gate

## Read-only Diagnose

Zulaessige Read-only-Daten:

- Anzahl relevanter Alerts
- Szenario- oder Parser-Klasse
- Severity
- Zeitfenster
- betroffene Surface
- Hash/Ref eines Quellereignisses

Nicht zulaessig:

- rohe IPs in user-facing Text ohne explizite Policy
- rohe Request-Header
- Tokens
- private Pfade
- komplette Logs

## Temporärer Ban

Ein `crowdsec_temp_block` darf nur vorbereitet werden, wenn:

- Incident-Level mindestens `contain` ist
- Confidence mindestens Policy-Schwelle erreicht
- Evidenz redigiert ist
- Ziel als Hash/Ref oder policy-erlaubter Identifier vorliegt
- Dauer begrenzt ist
- Operator Gate offen sichtbar ist

Ausfuehrung ist No-Go ohne:

- `crowdsec-remediation-go`
- konkrete Action-ID
- konkrete Dauer
- Scope
- Rollback-/Unban-Plan

## Unban

Ein `crowdsec_unblock` darf nur vorbereitet werden, wenn:

- die urspruengliche Ban-Action bekannt ist
- der Grund fuer Unban genannt ist
- der Operator das Risiko sehen kann
- keine aktive Kompromittierungsanzeige mehr offen ist

## Ban-Plan Format

Ein sicherer Plan enthaelt:

- Action-ID
- Incident-ID
- Typ: `crowdsec_temp_block` oder `crowdsec_unblock`
- Policy Gate
- Confidence
- Dauer oder Ablaufzeit
- Risiko
- erwarteter Impact
- Rollback-Hinweis
- `writes_performed=false`

## No-Go

No-Go gilt bei:

- breiten Firewall-Regeln
- unlimitierter Dauer
- fehlender Evidenz
- rohen privaten Daten in Summary
- Action ohne Operator-Gate
- automatischem Execute aus MCP
- Ban wegen unsicherer oder niedriger Confidence

## Operator-Kommandos

Operator-Kommandos duerfen nur Action-IDs referenzieren:

- approve: Action-ID bestaetigen
- deny: Action-ID ablehnen
- expire: alte Action nicht mehr anbieten

Kommandos duerfen keine Tokens, Chat-IDs, IPs oder privaten Pfade enthalten.

## Single-action packet before a later decision

`crowdsec-remediation-go`, `OPS-REMEDIATION-GO` and
`mcp-remediation-tools-go` are three independent gates. This runbook does not
satisfy any of them. A later Go must decide all applicable gates for exactly
one action/policy version and opaque scope. No prepared plan, other gate or
receipt is reusable, or a permission for sessions, delivery, deployment or
other remediation.

The packet in
`docs/plans/security-incident-response-activation-packet.md` must include one
target class (`crowdsec_temp_block` or `crowdsec_unblock`), one action ID,
opaque scope reference, exact TTL/unban or expiry route, action/readback
timeout, single-use grant expiry, redacted preflight evidence, false-positive
and lockout assessment, rollback/recovery owner, independent redacted effect
and unban/expiry readback, stop conditions, later explicit operator decision
and a final status. It aborts on broad or unlimited scope, missing TTL,
lockout risk, uncertain evidence, missing readback, timeout, replay, scope
drift or operator withdrawal.

Only references and redacted facts may reach the handoff card. An executor
acknowledgement never replaces independent readback.

## Recovery

Nach einer genehmigten CrowdSec-Aktion muss ein Recovery-Plan vorbereitet werden:

- pruefen, ob Alerts sinken
- Service Health pruefen
- False-Positive-Risiko bewerten
- Unban-Ablauf dokumentieren
- Post-Incident Summary ohne private Inhalte erzeugen

## Mindesttests fuer spaetere Implementierung

- niedriges Level blockt Ban
- niedrige Confidence blockt Ban
- fehlendes Gate blockt Execute
- Action-ID ist Pflicht
- Dauer ist begrenzt
- No-Go bei Raw Content
- Execute liefert ohne Live-Go immer blocked
