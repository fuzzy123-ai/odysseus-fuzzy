# Security Incident Response Runbook

Stand: 2026-07-03

Status: SIR-6 operator runbook contract

Dieses Runbook beschreibt den defensiven Incident-Response-Ablauf fuer Odysseus. Es ist bewusst prepare-only: keine Live-Remediation, keine Host-Kommandos, keine Token, keine Chat-IDs und keine privaten Inhalte.

## Ziel

Odysseus soll Sicherheitsereignisse strukturiert behandeln:

- redigierte Evidenz sammeln
- Incident-Level bestimmen
- Debug-Bundle vorbereiten
- eine redigierte Operator-Notification als No-send vorbereiten
- nur erlaubte Read-only-Diagnostik automatisch ausfuehren
- jede Remediation als Gate behandeln

## Incident-Level

| Level | Name | Bedeutung | Auto erlaubt | Operator |
| - | - | - | - | - |
| 0 | normal | Keine relevante Anomalie | Monitoring | keine Nachricht |
| 1 | watch | Niedrige Sicherheit, kleiner Spike | Kandidat, Dedupe, Korrelation | Digest moeglich |
| 2 | alert | Wiederholte Fehlversuche, Probe, sicherheitsrelevanter Ausfall | Incident, Debug-Bundle, MCP Read-only | sofort informieren |
| 3 | contain | Aktiver Missbrauch oder Stoerung | Empfehlung vorbereiten | approve/deny erforderlich |
| 4 | lockdown | Secret-Leak, Admin-Missbrauch, Exfiltration-Verdacht | Incident Mode, lokale Analyse, Risky Tools blocken | urgent |
| 5 | recovery | Rueckkehr in vertrauenswuerdigen Zustand | Recovery-Plan, Health-Check | Review |

## Standardablauf

1. Redigiertes Ereignis oder Summary erfassen.
2. Incident mit `raw_content_visible=false` erzeugen.
3. `security_recent_anomalies` oder passende Klassifikation ausfuehren.
4. `security_policy_readiness` pruefen, falls Policy-Zustand unklar ist.
5. `security_recommend_next_action` nutzen, um Policy und Operator-Notification vorzubereiten.
6. Optional ein redigiertes Debug-Bundle vorbereiten.
7. Eine redigierte Operator-Notification als No-send vorbereiten; keine
   Zustellung und keine Remediation ausfuehren.
8. Approve/Deny-Entscheidung abwarten, wenn eine Gate-Aktion vorgeschlagen wird.
9. Nach Abschluss Incident auf Recovery oder Closed setzen.

## Automatisch erlaubt

- Read-only MCP-Diagnostik
- redigierte Debug-Bundles
- Incident-Kandidaten
- Alert-Dedupe
- lokale sensitive Analyse im DSGVO/Incident Mode
- redigierte Operator-Notification als No-send-Entscheidung vorbereiten

## Gate-pflichtig

- CrowdSec Ban/Unban
- Firewall- oder Reverse-Proxy-Regel
- Service-Restart
- Scheduler Pause/Retry
- RaptorGraph Maintenance Restart
- Nextcloud Import Retry
- Token Rotation
- Session Invalidation
- Cloudflare Tunnel Aenderung
- Deploy Rollback
- Log-Level Erhoehung mit Privacy-Risiko

## Niemals erlaubt

- Hackback
- Third-party Exploit
- Secret-Ausgabe
- Upload privater Evidenz an externe Modelle im DSGVO/Incident Mode
- destruktives Cleanup vor Evidenzsicherung
- arbitrary shell ueber MCP
- `expose_all` fuer MCP

## Evidence-Regeln

Zulaessig:

- Hashes
- redigierte Event-IDs
- redigierte Correlation-IDs
- Surface, Component, Event Type, Status, Severity
- Debug-Bundle-ID
- Action-ID

Nicht zulaessig:

- Tokens
- Chat-IDs
- Authorization Header
- Cookies
- private Dokumenttexte
- E-Mail-Volltexte
- Bild-Base64
- absolute Hostpfade
- rohe Provider-Ausgaben

## Operator-Entscheidung

Eine Operator-Notification muss enthalten:

- Incident-ID
- Level, Severity, Confidence, Status
- betroffene Surfaces
- Policy-Entscheidung
- Debug-Bundle-Referenz, falls vorhanden
- Action-IDs
- klare Approve/Deny-Anweisung

Die Notification darf keine Zielwerte wie Telegram Chat-ID oder Token enthalten. Zustellung ist serverseitige Konfiguration.

## Go / No-Go

Go fuer prepare-only Response:

- Incident ist strukturiert und redigiert
- Policy bewertet jede Action
- Notification ist redigiert
- `writes_performed=false`
- Gate-Actions sind nicht ausgefuehrt

No-Go:

- Raw Content sichtbar
- Secret-Marker in Evidenz oder Notification
- Remediation ohne Gate
- Host-Kommandos aus Odysseus-Core
- nicht erklaerter Zugriff auf Live-Systeme

## Activation packets and gate boundaries

The template in
`docs/plans/security-incident-response-activation-packet.md` is prepare-only
material for a later, single operator decision. It grants no Go. Each request
is single-use, action/policy-versioned, bound to an exact scope, and invalid
at its recorded expiry. A preparation record, related gate, receipt or
executor acknowledgement is not an authorization or effect proof.

- Read-only observe needs the independent gates
  `observability-live-smoke-go`, `debian-observability-live-go` and
  `log-retention-policy-go`; it never authorizes delivery or mutation. Debian
  readiness may reference only `ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe` and its fixed redacted JSON projection.
- One notification needs `OPS-ALERT-DELIVERY-GO`; a preview or delivery receipt
  does not authorize CrowdSec, sessions or deployment.
- CrowdSec needs all of `crowdsec-remediation-go`, `OPS-REMEDIATION-GO` and
  `mcp-remediation-tools-go` as separate later decisions.
- One non-operator test session needs
  `security-incident-session-invalidation-go` and
  `mcp-remediation-tools-go`. Credential, SSH and authentication-configuration
  changes remain separately gated.
- Temporal closure needs `security-incident-temporal-closure-go` after its own
  canary outcomes. `deploy-live-go` remains independent for any deploy or
  deploy rollback.

Every future packet must state target class, exact bounded scope, timeout,
single-use grant expiry, redacted evidence, rollback/recovery, independent
readback, abort conditions, later operator decision and post-action status.
Missing or ambiguous information is `blocked`; it must never be inferred.

## Recovery

Recovery beginnt erst, wenn:

- keine neuen korrelierten Alert-Events auftreten
- betroffene Services Health-ok oder bewusst unknown sind
- alle vorbereiteten Remediation-Actions approved, denied oder expired sind
- ein kurzer Post-Incident Summary ohne private Inhalte vorliegt

Recovery darf keine Beweise loeschen. Cleanup ist ein separater, gated Schritt.

## Transactional deploy stop gate

`docs/plans/security-incident-response-transactional-deploy-packet.md` is a
no-Go, `needs_live_observation` contract. Before an owner can bind any deploy
values, one separately authorized source-redacted Podman Compose capability
observation must establish the required service-scoped semantics. Repo-only
tests cannot prove target-host `--no-deps --no-build`, no dependency
recreate/pull, or rollback `--force-recreate` behavior. Do not infer a deploy
executor, command, target, revision, image, lock, timeout, rollback, or health
result from this runbook. SEC129 backup creation, restore, restic check, and
delivery remain separate gates; `deploy-live-go` remains unsatisfied.

The compatibility observation accepts only its complete fixed capability
schema: all parser/proven flags true and raw stdout, stderr, exception,
environment, source text, path, hostname, and secret visibility flags false.
A constant validated Compose version is allowed; raw version output is not.

If a future separately authorized transactional deploy reaches a runtime switch,
any later failure, timeout, or ambiguity requires exactly one bounded rollback
to the captured old image without data restore and independent old-revision
verification. A verified rollback is `rolled_back`; a rollback failure,
timeout, or ambiguity is `unknown` with no retry. Failure before switch leaves
the old runtime in place and performs no rollback.
